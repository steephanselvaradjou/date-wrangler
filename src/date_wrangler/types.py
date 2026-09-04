"""Core value types.

The central choice here is that :class:`DateRange` is **half-open**: ``end`` is the first
instant *outside* the range, never the last one inside it. Three things fall out of that.

Adjacent periods tile exactly -- Q1.end == Q2.start -- so ranges compose without the
off-by-one that inclusive ends invite. SQL generated from a range is correct against a
TIMESTAMP column, not just a DATE (``ts < '2024-03-01'`` keeps the whole of Feb 29th;
``ts BETWEEN ... AND '2024-02-29'`` silently drops everything after midnight). And an
absent bound has an obvious meaning: ``end=None`` is simply "no upper bound", which an
inclusive end cannot express coherently.

Callers who want the last day inside the range ask for :attr:`DateRange.end_inclusive`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import Enum

__all__ = ["Grain", "Basis", "Mod", "DateRange", "DateMatch"]


class Grain(str, Enum):
    """Resolution the period was expressed at.

    Kept alongside the dates because ``2024-01-01 .. 2024-04-01`` alone cannot tell a
    caller whether the user asked for a quarter or for three months, which is exactly
    what decides the ``GROUP BY`` bucket downstream.
    """

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    HALF = "half"
    YEAR = "year"


class Basis(str, Enum):
    """Which calendar a period was measured against."""

    FISCAL = "fiscal"
    CALENDAR = "calendar"


class Mod(str, Enum):
    """Open-ended and point-in-time qualifiers.

    ``UNTIL`` includes the named period, ``BEFORE`` excludes it -- "until March" ends
    April 1st, "before March" ends March 1st. Keeping the distinction on the range rather
    than folding it into the dates lets a caller render the phrase back, or refuse an
    unbounded query outright.
    """

    SINCE = "since"
    UNTIL = "until"
    BEFORE = "before"
    AFTER = "after"
    AS_OF = "as_of"


@dataclass(frozen=True, slots=True)
class DateRange:
    """A half-open date interval ``[start, end)``, either end optionally unbounded."""

    start: date | None
    end: date | None
    grain: Grain
    basis: Basis = Basis.CALENDAR
    mod: Mod | None = None

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

    def __contains__(self, day: date) -> bool:
        below = self.start is not None and day < self.start
        above = self.end is not None and day >= self.end
        return not (below or above)

    def clamp(self, lo: date | None = None, hi: date | None = None) -> DateRange:
        """Close off unbounded ends against a window the caller supplies.

        This is the intended way to answer "since March" -- the parser refuses to guess
        whether that means "up to today" or "for all time", so it leaves ``end`` as None
        and the caller opts in::

            r.clamp(hi=today + timedelta(days=1))   # through end of today

        Bounds already present are tightened, never widened.
        """
        start, end = self.start, self.end
        if lo is not None:
            start = lo if start is None else max(start, lo)
        if hi is not None:
            end = hi if end is None else min(end, hi)
        if start is not None and end is not None and end < start:
            end = start
        return replace(self, start=start, end=end)

    def iter_days(self) -> Iterator[date]:
        """Every day in the range. Raises if either end is unbounded."""
        if self.start is None or self.end is None:
            raise ValueError("cannot iterate an unbounded DateRange; clamp() it first")
        day, stop = self.start, self.end
        while day < stop:
            yield day
            day += timedelta(days=1)

    def sql(self, column: str) -> str:
        """A SQL predicate for this range.

        One code path covers all four bound states, and it is correct for DATE and
        TIMESTAMP columns alike because the upper bound is exclusive.
        """
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
    """A resolved range plus where it came from in the source text.

    ``span`` indexes the *original* text, so a caller can highlight or replace the phrase
    without re-searching. ``text`` is the phrase as written.
    """

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
