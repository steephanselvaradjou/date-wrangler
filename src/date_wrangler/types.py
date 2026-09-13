"""Core value types.

:class:`DateRange` is half-open: ``end`` is the first day *outside* the range. That way
periods tile exactly (Q1.end == Q2.start), generated SQL is safe against TIMESTAMP columns,
and ``end=None`` has an obvious meaning. Use :attr:`DateRange.end_inclusive` for display.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import Enum

from .arith import DateRangeOverflow, add_months

__all__ = ["Grain", "Basis", "Anchor", "Mod", "DateRange", "DateMatch"]


class Grain(str, Enum):
    """Resolution the period was expressed at. Decides the GROUP BY bucket."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    HALF = "half"
    YEAR = "year"


#: Grains from finest to coarsest. The ordering is a fact about the enum, so it lives here
#: rather than with any one caller: a range joining two resolutions takes the coarser, and
#: a range intersecting two takes the finer.
_GRAIN_ORDER: tuple[Grain, ...] = (
    Grain.DAY, Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.HALF, Grain.YEAR,
)
_GRAIN_RANK: dict[Grain, int] = {g: i for i, g in enumerate(_GRAIN_ORDER)}


def grain_rank(g: Grain) -> int:
    """Position from finest (``DAY`` = 0) to coarsest (``YEAR``)."""
    return _GRAIN_RANK[g]


class Basis(str, Enum):
    """Which calendar a period was measured against."""

    FISCAL = "fiscal"
    CALENDAR = "calendar"


class Anchor(str, Enum):
    """Where a *relative* period's edges fall.

    ANCHORED snaps to whole calendar units: asked on 4 September, "last month" is
    1-31 August. ROLLING measures back from today instead: 8 August - 7 September.

    Anchored is the default because it is what reporting means, and because only whole
    units are comparable -- rolling months are 28 to 31 days long, so month-on-month
    stops being a like-for-like. Rolling is what you want for "the last 30 days of
    activity", where the cutoff is genuinely now.

    Orthogonal to :class:`Basis`, which picks the *calendar*, and to absolute vs relative,
    which is whether a period was named outright ("March 2024") or computed from today.
    """

    ANCHORED = "anchored"
    ROLLING = "rolling"


class Mod(str, Enum):
    """Open-ended and point-in-time qualifiers.

    UNTIL includes the named period, BEFORE excludes it: "until March" ends April 1st,
    "before March" ends March 1st.
    """

    SINCE = "since"
    UNTIL = "until"
    BEFORE = "before"
    AFTER = "after"
    AS_OF = "as_of"


#: How many months one unit of a grain spans. Week and day are not whole months.
_MONTHS_PER_GRAIN = {
    Grain.YEAR: 12,
    Grain.HALF: 6,
    Grain.QUARTER: 3,
    Grain.MONTH: 1,
}
_DAYS_PER_GRAIN = {
    Grain.WEEK: 7,
    Grain.DAY: 1,
}


def _advance(day: date, grain: Grain, periods: int) -> date:
    """``day`` moved by ``periods`` whole units of ``grain``.

    Month-based grains go through :func:`add_months`, so a quarter moves by three months
    rather than by an assumed 90 days -- which is the arithmetic callers get wrong.
    """
    months = _MONTHS_PER_GRAIN.get(grain)
    if months is not None:
        return add_months(day, months * periods)
    days = _DAYS_PER_GRAIN.get(grain)
    if days is None:
        raise ValueError(f"not a Grain: {grain!r}")
    try:
        return day + timedelta(days=days * periods)
    except (OverflowError, ValueError) as exc:
        raise DateRangeOverflow(
            f"{day.isoformat()} {periods:+} {grain.value}(s) falls outside the supported "
            f"year range"
        ) from exc


# An unbounded start is -infinity and an unbounded end is +infinity, so which of the four
# helpers to reach for depends on whether you are narrowing or widening. Spelling them out
# separately beats one clever function, because getting the infinity backwards turns
# "since March" into "nothing at all" without raising anything.


def _later(a: date | None, b: date | None) -> date | None:
    """The later of two starts, where None means -infinity."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _earlier(a: date | None, b: date | None) -> date | None:
    """The earlier of two ends, where None means +infinity."""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _earlier_start(a: date | None, b: date | None) -> date | None:
    """The earlier of two starts. Unbounded wins, since nothing precedes -infinity."""
    return None if a is None or b is None else min(a, b)


def _later_end(a: date | None, b: date | None) -> date | None:
    """The later of two ends. Unbounded wins, since nothing follows +infinity."""
    return None if a is None or b is None else max(a, b)


@dataclass(frozen=True, slots=True)
class DateRange:
    """A half-open date interval ``[start, end)``, either end optionally unbounded."""

    start: date | None
    end: date | None
    grain: Grain
    basis: Basis = Basis.CALENDAR
    mod: Mod | None = None
    #: Only meaningful for a relative period; an absolute one like "March 2024" is always
    #: ANCHORED, since it names its own edges.
    anchor: Anchor = Anchor.ANCHORED

    def __post_init__(self) -> None:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError(
                f"end {self.end} precedes start {self.start}; a DateRange must not be inverted"
            )

    # ---- bounds -------------------------------------------------------------

    @property
    def is_bounded(self) -> bool:
        """True when both ends are known, i.e. the range is safe to query directly."""
        return self.start is not None and self.end is not None

    @property
    def is_empty(self) -> bool:
        """True for a zero-width range. Half-open means start == end contains nothing."""
        return self.is_bounded and self.start == self.end

    @property
    def end_inclusive(self) -> date | None:
        """The last day *inside* the range, for display. None when unbounded above."""
        return None if self.end is None else self.end - timedelta(days=1)

    @property
    def days(self) -> int | None:
        """Number of days covered, or None when either end is unbounded."""
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).days

    # ---- operations ---------------------------------------------------------

    def __contains__(self, item: date | DateRange) -> bool:
        """``day in r``, and also ``smaller in bigger`` for a whole range."""
        if isinstance(item, DateRange):
            return self.covers(item)
        below = self.start is not None and item < self.start
        above = self.end is not None and item >= self.end
        return not (below or above)

    def clamp(self, lo: date | None = None, hi: date | None = None) -> DateRange:
        """Close off unbounded ends against a window you supply.

        We never guess whether "since March" means "up to today" or "for all time", so
        ``end`` stays None until you say. Existing bounds are tightened, never widened.
        """
        start, end = self.start, self.end
        if lo is not None:
            start = lo if start is None else max(start, lo)
        if hi is not None:
            end = hi if end is None else min(end, hi)
        if start is not None and end is not None and end < start:
            end = start
        return replace(self, start=start, end=end)

    # ---- algebra ------------------------------------------------------------
    #
    # An unbounded end is treated as an infinity throughout, which is the only reading that
    # makes "since March" compose. Results take the finer grain, because an intersection is
    # never longer than the shorter side, and a calendar-neutral basis and anchor where the
    # two disagree -- so `a & b` and `b & a` are the same range in every field.

    def _meta(self, other: DateRange) -> tuple[Grain, Basis, Anchor]:
        grain = min(self.grain, other.grain, key=grain_rank)
        basis = self.basis if self.basis is other.basis else Basis.CALENDAR
        anchor = self.anchor if self.anchor is other.anchor else Anchor.ANCHORED
        return grain, basis, anchor

    def overlaps(self, other: DateRange) -> bool:
        """Whether the two share at least one day. An empty range overlaps nothing."""
        if self.is_empty or other.is_empty:
            return False
        starts_before_other_ends = (
            self.start is None or other.end is None or self.start < other.end
        )
        other_starts_before_self_ends = (
            other.start is None or self.end is None or other.start < self.end
        )
        return starts_before_other_ends and other_starts_before_self_ends

    def covers(self, other: DateRange) -> bool:
        """Whether every day of ``other`` is also in this range."""
        if other.is_empty:
            return True
        after_start = self.start is None or (
            other.start is not None and other.start >= self.start
        )
        before_end = self.end is None or (other.end is not None and other.end <= self.end)
        return after_start and before_end

    def intersection(self, other: DateRange) -> DateRange:
        """The days in both. Disjoint ranges give an empty range, not an error.

            >>> q1 = DateRange(date(2024, 1, 1), date(2024, 4, 1), Grain.QUARTER)
            >>> since = DateRange(date(2024, 2, 1), None, Grain.MONTH, mod=Mod.SINCE)
            >>> str(q1 & since)
            '[2024-02-01, 2024-04-01)'

        This is the general form of :meth:`clamp`, which is intersection against a window
        you write out by hand. Use ``is_empty`` on the result rather than checking for
        ``None``, so a chain of intersections keeps working.
        """
        start = _later(self.start, other.start)
        end = _earlier(self.end, other.end)
        if start is not None and end is not None and end < start:
            end = start  # disjoint: an empty range, positioned where they failed to meet
        grain, basis, anchor = self._meta(other)
        return DateRange(start, end, grain, basis, None, anchor)

    def hull(self, other: DateRange) -> DateRange:
        """The smallest range covering both, gap included.

        Named rather than spelled ``|`` because for ranges that do not meet this is a
        deliberate widening: the hull of Q1 and Q3 quietly contains Q2. :meth:`union`
        refuses that case; this one is how you ask for it anyway.
        """
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        grain, basis, anchor = self._meta(other)
        return DateRange(
            _earlier_start(self.start, other.start),
            _later_end(self.end, other.end),
            grain,
            basis,
            None,
            anchor,
        )

    def union(self, other: DateRange) -> DateRange:
        """The two joined into one range. Raises if they neither overlap nor touch.

        A union that spans a gap is not a range, it is two of them, and quietly returning
        the hull is how "Q1 and Q3" comes to include Q2. Call :meth:`hull` if the widening
        is what you want, or keep the two ranges apart.
        """
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        if not (self.overlaps(other) or self._touches(other)):
            raise ValueError(
                f"{self} and {other} do not meet, so their union is not a single range; "
                f"use hull() to widen over the gap"
            )
        return self.hull(other)

    def _touches(self, other: DateRange) -> bool:
        """Whether the two are adjacent with no day between. Half-open makes this exact."""
        return (self.end is not None and self.end == other.start) or (
            other.end is not None and other.end == self.start
        )

    def difference(self, other: DateRange) -> list[DateRange]:
        """The days in this range but not in ``other``.

        Returns nothing when ``other`` swallows this range, one range when it bites off an
        end, and *two* when it takes a piece out of the middle -- which is why this is a
        list and not an operator.
        """
        if self.is_empty:
            return []
        if not self.overlaps(other):
            return [self]
        grain, basis, anchor = self._meta(other)
        pieces = []
        if other.start is not None and (self.start is None or self.start < other.start):
            pieces.append(DateRange(self.start, other.start, grain, basis, None, anchor))
        if other.end is not None and (self.end is None or other.end < self.end):
            pieces.append(DateRange(other.end, self.end, grain, basis, None, anchor))
        return pieces

    def __and__(self, other: DateRange) -> DateRange:
        return self.intersection(other)

    def __or__(self, other: DateRange) -> DateRange:
        return self.union(other)

    def shift(self, periods: int, grain: Grain | None = None) -> DateRange:
        """This range moved by ``periods`` whole units, for period-over-period comparison.

        The default unit is the range's own :attr:`grain`, so ``shift(-1)`` on a quarter is
        the previous quarter. Pass ``grain`` to step by something else: the comparison
        everyone actually wants is the same period a year earlier, which is
        ``shift(-1, Grain.YEAR)`` whatever the range happens to be.

            >>> q = DateRange(date(2024, 4, 1), date(2024, 7, 1), Grain.QUARTER, Basis.FISCAL)
            >>> q.shift(-1).start
            datetime.date(2024, 1, 1)
            >>> q.shift(-1, Grain.YEAR).start
            datetime.date(2023, 4, 1)

        Length is preserved, and both ends move together, so this stays correct where hand
        rolled arithmetic does not: ``- timedelta(days=90)`` drifts because quarters are
        90 to 92 days, ``- timedelta(days=365)`` breaks across a leap year, and
        ``replace(year=...)`` raises on 29 February.

        Basis is carried along rather than recomputed, which is what makes a fiscal shift
        right: a fiscal Q1 starts on a fiscal boundary already, so stepping back three
        months lands on fiscal Q4 of the year before. An unbounded end stays unbounded, so
        ``shift`` on "since March" gives "since February".
        """
        step = self.grain if grain is None else grain
        return replace(
            self,
            start=None if self.start is None else _advance(self.start, step, periods),
            end=None if self.end is None else _advance(self.end, step, periods),
        )

    def split(self, grain: Grain) -> list[DateRange]:
        """This range cut into consecutive buckets of ``grain``, for a GROUP BY axis.

        Parsing gives one range; a chart or a report needs one row per bucket.

            >>> r = DateRange(date(2025, 6, 1), date(2025, 9, 1), Grain.MONTH)
            >>> [str(b) for b in r.split(Grain.MONTH)]
            ['[2025-06-01, 2025-07-01)', '[2025-07-01, 2025-08-01)', '[2025-08-01, 2025-09-01)']

        The buckets tile exactly -- each one's ``end`` is the next one's ``start`` -- so
        they partition the range with no gap and no overlap, and ``sql()`` on each is a
        safe predicate for that bucket alone.

        **The grid starts at** :attr:`start`, not at a calendar boundary. For everything
        the parser produces at an aligned grain that is the same thing, because the range
        already begins on one: a fiscal year splits into its own fiscal quarters, a
        calendar month into calendar weeks-from-the-1st. It also means the answer never
        depends on a fiscal calendar or a week-start setting that this range does not
        carry. For a range that starts mid-unit -- a rolling period, or "the last 45 days"
        -- the buckets are measured from its start instead of snapping outwards, so the
        result never covers a day the range did not.

        Only the final bucket can be short, and :attr:`days` says by how much. A range
        shorter than one bucket comes back as a single truncated bucket; an empty range
        comes back empty. Raises if either end is unbounded -- clamp it first, since only
        you know what "since March" should stop at.
        """
        if self.start is None or self.end is None:
            raise ValueError("cannot split an unbounded DateRange; clamp() it first")
        buckets: list[DateRange] = []
        cursor, periods = self.start, 1
        while cursor < self.end:
            try:
                edge = _advance(self.start, grain, periods)
            except DateRangeOverflow:
                edge = self.end  # the last bucket runs off the end of the calendar
            if edge > self.end:
                edge = self.end
            buckets.append(
                DateRange(cursor, edge, grain, self.basis, None, self.anchor)
            )
            cursor, periods = edge, periods + 1
        return buckets

    def iter_days(self) -> Iterator[date]:
        """Every day in the range. Raises if either end is unbounded."""
        if self.start is None or self.end is None:
            raise ValueError("cannot iterate an unbounded DateRange; clamp() it first")
        day, stop = self.start, self.end
        while day < stop:
            yield day
            day += timedelta(days=1)

    def sql(self, column: str) -> str:
        """A SQL predicate for this range, covering all four bound states."""
        parts = []
        if self.start is not None:
            parts.append(f"{column} >= '{self.start.isoformat()}'")
        if self.end is not None:
            parts.append(f"{column} < '{self.end.isoformat()}'")
        return " AND ".join(parts) if parts else "TRUE"

    def __str__(self) -> str:
        lo = self.start.isoformat() if self.start else "-inf"
        hi = self.end.isoformat() if self.end else "+inf"
        return f"[{lo}, {hi})"


@dataclass(frozen=True, slots=True)
class DateMatch:
    """A resolved range plus where it came from. ``span`` indexes the original text."""

    range: DateRange
    text: str
    span: tuple[int, int]
    confidence: float = 1.0

    @property
    def start(self) -> date | None:
        return self.range.start

    @property
    def end(self) -> date | None:
        return self.range.end
