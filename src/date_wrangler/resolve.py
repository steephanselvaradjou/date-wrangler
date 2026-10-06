"""Turn a :class:`~date_wrangler.spec.Spec` into a concrete :class:`DateRange`.

Where "what did they say" becomes "which days". Three distinctions carry most of the
weight: AGO names one period while RELATIVE names a span, a fiscal period with no stated
year belongs to the current *fiscal* year rather than ``today.year``, and "last YTD"
shifts the window back a year instead of widening to the whole prior year.
"""

from __future__ import annotations

from datetime import date, timedelta

from .calendars import (
    add_business_days,
    add_months,
    day_range,
    fiscal_month_range,
    fiscal_year_of,
    fiscal_year_start,
    half_range,
    is_business_day,
    iso_week_range,
    month_range,
    quarter_range,
    week_range,
    year_range,
)
from .config import WranglerConfig
from .spec import Kind, Part, Spec
from .types import Anchor, Basis, DateRange, Grain, Mod
from .vocab import WEEKDAY_DISPLAY

__all__ = ["resolve", "default_year_for", "UnresolvableSpec"]


class UnresolvableSpec(ValueError):
    """A spec that cannot be turned into dates, e.g. a quarter index of 7."""


# ---------------------------------------------------------------------------
# Period grids -- where the current day sits on each unit's boundaries
# ---------------------------------------------------------------------------


def _month_start(today: date) -> date:
    return date(today.year, today.month, 1)


def _quarter_start(today: date, cfg: WranglerConfig, basis: Basis) -> date:
    """Start of the quarter containing ``today``, on the relevant grid.

    The grids coincide only for Jan/Apr/Jul/Oct starts, so the basis matters.
    """
    if basis is Basis.FISCAL:
        fy_start = fiscal_year_start(fiscal_year_of(today, cfg.fiscal), cfg.fiscal)
        elapsed = (today.year - fy_start.year) * 12 + (today.month - fy_start.month)
        return add_months(fy_start, (elapsed // 3) * 3)
    return date(today.year, (today.month - 1) // 3 * 3 + 1, 1)


def _year_start(today: date, cfg: WranglerConfig, basis: Basis) -> date:
    if basis is Basis.FISCAL:
        return fiscal_year_start(fiscal_year_of(today, cfg.fiscal), cfg.fiscal)
    return date(today.year, 1, 1)


def _period_start(today: date, cfg: WranglerConfig, unit: Grain, basis: Basis) -> date:
    if unit is Grain.DAY:
        return today
    if unit is Grain.WEEK:
        start = week_range(today, cfg.week_starts_on).start
        assert start is not None
        return start
    if unit is Grain.MONTH:
        return _month_start(today)
    if unit is Grain.QUARTER:
        return _quarter_start(today, cfg, basis)
    if unit is Grain.HALF:
        year_start = _year_start(today, cfg, basis)
        elapsed = (today.year - year_start.year) * 12 + (today.month - year_start.month)
        return add_months(year_start, (elapsed // 6) * 6)
    return _year_start(today, cfg, basis)


def _shift(start: date, unit: Grain, n: int) -> date:
    """``start`` moved by ``n`` whole units."""
    if unit is Grain.DAY:
        return start + timedelta(days=n)
    if unit is Grain.WEEK:
        return start + timedelta(weeks=n)
    months = {Grain.MONTH: 1, Grain.QUARTER: 3, Grain.HALF: 6, Grain.YEAR: 12}[unit]
    return add_months(start, n * months)


# ---------------------------------------------------------------------------
# Basis and year defaults
# ---------------------------------------------------------------------------


def _basis_for(spec: Spec, cfg: WranglerConfig) -> Basis:
    if spec.basis is not None:
        return spec.basis
    return cfg.bare_period_basis


def _default_year(today: date, cfg: WranglerConfig, basis: Basis) -> int:
    """The year a bare period belongs to: the fiscal one in progress, not today.year."""
    if basis is Basis.FISCAL:
        return fiscal_year_of(today, cfg.fiscal)
    return today.year


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def default_year_for(spec: Spec, today: date, cfg: WranglerConfig) -> int | None:
    """The year :func:`resolve` would assume for ``spec``, or None if it needs none.

    A range uses this to retry an endpoint a year on. For a fiscal quarter the number is a
    fiscal label, so it cannot be read back off the resolved dates.
    """
    if spec.is_relative or spec.year is not None:
        return None
    if spec.kind is Kind.FISCAL_MONTH:
        return fiscal_year_of(today, cfg.fiscal)
    if spec.kind in (Kind.ABS_MONTH, Kind.ABS_DAY):
        return today.year
    return _default_year(today, cfg, _basis_for(spec, cfg))


def resolve(spec: Spec, today: date, cfg: WranglerConfig) -> DateRange:
    """Resolve ``spec`` against ``today``. Raises :class:`UnresolvableSpec` on bad input."""
    try:
        base = _resolve_core(spec, today, cfg)
        if spec.year_offset:
            base = _shift_years(base, spec.year_offset)
        if spec.through_today:
            # With no year written, "December to date" in October means the December just
            # gone, not the one coming -- the same choice "YTD March" already makes. An
            # explicit year is taken at its word, so "FY28 to date" is still refused.
            if base.start is not None and base.start > today and not spec.has_explicit_year:
                base = _shift_years(base, -1)
            base = _through_today(base, today)
        if spec.part is not None:
            base = _slice_part(base, spec.part)
        if spec.nth_weekday is not None:
            base = _pick_weekday(base, *spec.nth_weekday)
        if spec.day_of_period is not None:
            if spec.pick_business_day:
                base = _pick_business_day(base, spec.day_of_period, cfg)
            else:
                base = _pick_day(base, spec.day_of_period)
    except (ValueError, OverflowError) as exc:
        if isinstance(exc, UnresolvableSpec):
            raise
        raise UnresolvableSpec(str(exc)) from exc
    return _apply_mod(base, spec.mod, today)


def _through_today(r: DateRange, today: date) -> DateRange:
    """A period "to date": from its start up to and including today.

    For the current period that is the period so far -- "this year to date" is the same
    window as YTD. For one that has already ended it runs on to today, because that is
    what "to date" says: "revenue from March 2024 to date" is everything since March 2024,
    not March 2024 alone. One reading covers both, so there is no case to pick.

    A period that has not started yet has nothing to date, and is refused rather than
    turned into an empty or inverted range.
    """
    if r.start is None:
        raise UnresolvableSpec("a period with no start has nothing to count to date from")
    if r.start > today:
        raise UnresolvableSpec(
            f"the period starts on {r.start}, after today, so there is nothing to date yet"
        )
    return DateRange(r.start, today + timedelta(days=1), r.grain, r.basis, r.mod, r.anchor)


def _shift_years(r: DateRange, offset: int) -> DateRange:
    """The same period, ``offset`` whole years away. "Q1 last year"."""
    if r.start is None or r.end is None:
        raise UnresolvableSpec("cannot shift an unbounded range by a year")
    return DateRange(
        add_months(r.start, 12 * offset),
        add_months(r.end, 12 * offset),
        r.grain,
        r.basis,
        r.mod,
        r.anchor,
    )


def _whole_months(start: date, end: date) -> int | None:
    """How many whole months ``[start, end)`` spans, or None if it is not a whole number."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    return months if months > 0 and add_months(start, months) == end else None


def _slice_part(r: DateRange, part: Part) -> DateRange:
    """A half or a third of a resolved period.

    Divided by months where that comes out even, otherwise by days: "early 2024" should be
    January to April, not the first 122 days, while "first half of March" has to be days
    because half a month is not a month. Remainders go to the last piece, so the parts tile
    the period exactly rather than leaving a day unaccounted for.
    """
    if r.start is None or r.end is None:
        raise UnresolvableSpec("cannot take part of an unbounded range")
    span = (r.end - r.start).days
    if span < 2:
        raise UnresolvableSpec("period is too short to divide")
    pieces = 2 if part in (Part.FIRST_HALF, Part.SECOND_HALF) else 3
    months = _whole_months(r.start, r.end)

    if months is not None and months % pieces == 0:
        # A slice is no longer the grain it came out of: a third of a year is four months,
        # so MONTH is the bucket to group it by, not YEAR.
        step, cut, grain = months // pieces, add_months, Grain.MONTH
    else:
        step, cut, grain = span // pieces, lambda d, n: d + timedelta(days=n), Grain.DAY

    if part is Part.FIRST_HALF:
        return DateRange(r.start, cut(r.start, step), grain, r.basis)
    if part is Part.SECOND_HALF:
        return DateRange(cut(r.start, step), r.end, grain, r.basis)
    if part is Part.EARLY:
        return DateRange(r.start, cut(r.start, step), grain, r.basis)
    if part is Part.MID:
        return DateRange(cut(r.start, step), cut(r.start, 2 * step), grain, r.basis)
    return DateRange(cut(r.start, 2 * step), r.end, grain, r.basis)


def _pick_day(r: DateRange, day_of_period: int) -> DateRange:
    """One day inside a period. "1st of next month", "the last day of the month".

    Positive counts from the start, negative from the end, matching the way people index
    in both directions. There is no day zero, which is what the guard is for.
    """
    if r.start is None or r.end is None:
        raise UnresolvableSpec("cannot index into an unbounded range")
    if day_of_period == 0:
        raise UnresolvableSpec("there is no day 0 of a period; days count from 1")
    if day_of_period > 0:
        target = r.start + timedelta(days=day_of_period - 1)
    else:
        # end is exclusive, so end - 1 day is the last day inside the period.
        target = r.end + timedelta(days=day_of_period)
    if not r.start <= target < r.end:
        raise UnresolvableSpec(
            f"day {day_of_period} falls outside the period {r.start}..{r.end}"
        )
    return day_range(target)


def _pick_business_day(r: DateRange, index: int, cfg: WranglerConfig) -> DateRange:
    """The nth working day of a period. "the last business day of the month".

    Month-end close, payroll cut-off and settlement dates are all phrased this way, and
    the calendar last day is wrong for every one of them whenever it lands on a weekend.
    """
    if r.start is None or r.end is None:
        raise UnresolvableSpec("cannot index into an unbounded range")
    if index == 0:
        raise UnresolvableSpec("there is no 0th business day of a period")
    days = [
        r.start + timedelta(days=i)
        for i in range((r.end - r.start).days)
        if is_business_day(r.start + timedelta(days=i), cfg.weekend, cfg.holidays)
    ]
    if abs(index) > len(days):
        raise UnresolvableSpec(
            f"{r.start}..{r.end} has {len(days)} business days, not {abs(index)}"
        )
    return day_range(days[index - 1] if index > 0 else days[index])


def _pick_weekday(r: DateRange, index: int, weekday: int) -> DateRange:
    """The nth weekday of a period. "third Thursday of November", "last Friday".

    ``index`` counts forward from 1 and backward from -1, like :func:`_pick_day`. Counting
    from the correct end matters: a month holds four or five of any given weekday, so the
    last one is not the fourth.
    """
    if r.start is None or r.end is None:
        raise UnresolvableSpec("cannot index into an unbounded range")
    if index == 0:
        raise UnresolvableSpec("there is no 0th weekday of a period")
    if index > 0:
        first = r.start + timedelta(days=(weekday - r.start.weekday()) % 7)
        target = first + timedelta(days=7 * (index - 1))
    else:
        last_day = r.end - timedelta(days=1)
        last = last_day - timedelta(days=(last_day.weekday() - weekday) % 7)
        target = last - timedelta(days=7 * (-index - 1))
    if not r.start <= target < r.end:
        name = WEEKDAY_DISPLAY[weekday]
        raise UnresolvableSpec(
            f"there is no {_ordinal_word(index)} {name} in {r.start}..{r.end}"
        )
    return day_range(target)


def _ordinal_word(index: int) -> str:
    if index < 0:
        return "last" if index == -1 else f"{-index}th-from-last"
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(index, f"{index}th")


def _resolve_core(spec: Spec, today: date, cfg: WranglerConfig) -> DateRange:
    basis = _basis_for(spec, cfg)

    if spec.kind is Kind.ABS_DAY:
        if spec.month is None or spec.day is None:
            raise UnresolvableSpec("an absolute day needs a month and a day")
        # "meeting on March 3" states no year; assume the current calendar year.
        return day_range(date(spec.year if spec.year is not None else today.year,
                              spec.month, spec.day))

    if spec.kind is Kind.ABS_MONTH:
        if spec.month is None:
            raise UnresolvableSpec("a month spec needs a month")
        # Months are calendar facts; only the *year* they land in is in question.
        return month_range(spec.year if spec.year is not None else today.year, spec.month)

    if spec.kind is Kind.ABS_QUARTER:
        if spec.index is None:
            raise UnresolvableSpec("a quarter spec needs an index")
        year = spec.year if spec.year is not None else _default_year(today, cfg, basis)
        return quarter_range(year, spec.index, cfg.fiscal, basis)

    if spec.kind is Kind.ABS_HALF:
        if spec.index is None:
            raise UnresolvableSpec("a half spec needs an index")
        year = spec.year if spec.year is not None else _default_year(today, cfg, basis)
        return half_range(year, spec.index, cfg.fiscal, basis)

    if spec.kind is Kind.ABS_YEAR:
        year = spec.year if spec.year is not None else _default_year(today, cfg, basis)
        return year_range(year, cfg.fiscal, basis)

    if spec.kind is Kind.FISCAL_MONTH:
        if spec.index is None:
            raise UnresolvableSpec("a fiscal month spec needs an index")
        year = spec.year if spec.year is not None else fiscal_year_of(today, cfg.fiscal)
        return fiscal_month_range(year, spec.index, cfg.fiscal)

    if spec.kind is Kind.DAY_KEYWORD:
        return day_range(today + timedelta(days=spec.direction))

    if spec.kind is Kind.THIS_PERIOD:
        unit = spec.unit or Grain.MONTH
        start = _period_start(today, cfg, unit, basis)
        return DateRange(start, _shift(start, unit, 1), unit, basis)

    if spec.kind is Kind.RELATIVE:
        if spec.business:
            return _business_window(spec, today, cfg)
        return _resolve_relative(spec, today, cfg, basis)

    if spec.kind is Kind.AGO:
        if spec.business:
            return _business_day(spec, today, cfg)
        return _resolve_ago(spec, today, cfg, basis)

    if spec.kind is Kind.TO_DATE:
        return _resolve_to_date(spec, today, cfg, basis)

    if spec.kind is Kind.WEEKDAY:
        return _resolve_weekday(spec, today, cfg)

    if spec.kind is Kind.WEEKEND:
        return _resolve_weekend(spec, today)

    if spec.kind is Kind.ISO_WEEK:
        if spec.index is None:
            raise UnresolvableSpec("a week number spec needs a week")
        # The ISO year, not the calendar year: on 31 December 2026 the current week is
        # week 53 of 2026, but on 1 January 2027 it is still week 53 of *2026*.
        year = spec.year if spec.year is not None else today.isocalendar()[0]
        return iso_week_range(year, spec.index)

    if spec.kind is Kind.DECADE:
        if spec.year is None:
            raise UnresolvableSpec("a decade needs a year")
        return DateRange(
            date(spec.year, 1, 1), date(spec.year + 10, 1, 1), Grain.YEAR, Basis.CALENDAR
        )

    if spec.kind is Kind.PERIOD_ENDING:
        return _resolve_period_ending(spec, today, cfg, basis)

    raise UnresolvableSpec(f"unhandled spec kind {spec.kind}")


def _resolve_weekday(spec: Spec, today: date, cfg: WranglerConfig) -> DateRange:
    """"last Monday", "next Friday", "this Tuesday".

    Past and future are strict -- on a Thursday, "last Thursday" is a week ago. "This
    Tuesday" is the one in the current week, either side of today, so which week that is
    depends on ``week_starts_on``: on a Sunday-start calendar the Tuesday just gone is
    still "this Tuesday" a day later than it would be on a Monday-start one.
    """
    if spec.index is None:
        raise UnresolvableSpec("a weekday spec needs a weekday")
    if spec.direction == 0:
        week_start = week_range(today, cfg.week_starts_on).start
        assert week_start is not None
        return day_range(week_start + timedelta(days=spec.index))
    delta = (spec.index - today.weekday()) % 7
    if spec.direction < 0:
        back = delta - 7 if delta else -7
        return day_range(today + timedelta(days=back))
    return day_range(today + timedelta(days=delta or 7))


def _resolve_weekend(spec: Spec, today: date) -> DateRange:
    """Saturday and Sunday of the week meant.

    Deliberately measured from a Monday week whatever ``week_starts_on`` says. A weekend
    is the Saturday-Sunday pair, and on a Sunday-start calendar that pair straddles the
    week boundary -- so following the setting would split "this weekend" across two weeks
    and hand back a Sunday from one and a Saturday from the other. Anchoring on Monday
    means "this weekend" is the one ahead of a weekday, and the one you are standing in
    if it is already Saturday, on either calendar.
    """
    week_start = week_range(today).start
    assert week_start is not None
    saturday = week_start + timedelta(days=5 + 7 * spec.direction)
    return DateRange(saturday, saturday + timedelta(days=2), Grain.DAY, Basis.CALENDAR)


def _resolve_period_ending(
    spec: Spec, today: date, cfg: WranglerConfig, basis: Basis
) -> DateRange:
    """"quarter ending June 2024" -- a period pinned by its end, not its start."""
    unit = spec.unit or Grain.QUARTER
    if spec.month is not None:
        year = spec.year
        if year is None:
            year = today.year if date(today.year, spec.month, 1) <= today else today.year - 1
        end = month_range(year, spec.month).end
    elif spec.year is not None:
        end = year_range(spec.year, cfg.fiscal, basis).end
    else:
        raise UnresolvableSpec("a period-ending spec needs a month or a year to end at")
    assert end is not None
    return DateRange(_shift(end, unit, -1), end, unit, basis)


def _business_window(spec: Spec, today: date, cfg: WranglerConfig) -> DateRange:
    """"last 10 business days" -- the span holding that many working days.

    The range is contiguous and includes any weekend *inside* it, because that is what a
    date filter has to be; :meth:`DateRange.business_days` says how many of its days are
    working ones. Both ends sit on a working day, though. Asked on a Monday, "last
    business day" is Friday -- ending the window at today, the way "last 10 days" does,
    would make it Friday to Sunday.
    """
    n = spec.count if spec.count is not None else 1
    if n < 1:
        raise UnresolvableSpec(f"a business-day count must be at least 1, got {n}")
    if spec.direction < 0:
        start = add_business_days(today, -n, cfg.weekend, cfg.holidays)
        latest = add_business_days(today, -1, cfg.weekend, cfg.holidays)
        return DateRange(start, latest + timedelta(days=1), Grain.DAY, Basis.CALENDAR)
    first = add_business_days(today, 1, cfg.weekend, cfg.holidays)
    last = add_business_days(today, n, cfg.weekend, cfg.holidays)
    return DateRange(first, last + timedelta(days=1), Grain.DAY, Basis.CALENDAR)


def _business_day(spec: Spec, today: date, cfg: WranglerConfig) -> DateRange:
    """"5 business days ago", "in 3 working days", "next business day" -- one day."""
    n = spec.count if spec.count is not None else 1
    if n < 0:
        raise UnresolvableSpec(f"a business-day count cannot be negative, got {n}")
    return day_range(add_business_days(today, n * spec.direction, cfg.weekend, cfg.holidays))


def _resolve_relative(spec: Spec, today: date, cfg: WranglerConfig, basis: Basis) -> DateRange:
    """"last 3 months" -- ``count`` units back, either anchored or rolling.

    Anchored counts whole units and excludes the current one: asked in September that is
    June, July and August, because including a part-finished September would mix a complete
    period with an incomplete one. Rolling measures from today instead, so the same phrase
    is 4 June to 3 September.
    """
    unit = spec.unit or Grain.MONTH
    n = spec.count if spec.count is not None else 1
    if n < 1:
        raise UnresolvableSpec(f"a period count must be at least 1, got {n}")
    anchor = spec.anchor if spec.anchor is not None else cfg.anchor
    if anchor is Anchor.ROLLING:
        # A day is its own unit, so rolling and anchored coincide at DAY grain -- which is
        # why "last 30 days" has always rolled, whatever the setting says.
        if spec.direction < 0:
            return DateRange(_shift(today, unit, -n), today, unit, basis, None, anchor)
        return DateRange(today, _shift(today, unit, n), unit, basis, None, anchor)
    current = _period_start(today, cfg, unit, basis)
    if spec.direction < 0:
        return DateRange(_shift(current, unit, -n), current, unit, basis)
    start = _shift(current, unit, 1)
    return DateRange(start, _shift(start, unit, n), unit, basis)


def _resolve_ago(spec: Spec, today: date, cfg: WranglerConfig, basis: Basis) -> DateRange:
    """"3 months ago" -- the single period ``count`` units away, one unit wide."""
    unit = spec.unit or Grain.MONTH
    n = spec.count if spec.count is not None else 1
    if n < 0:
        raise UnresolvableSpec(f"a period count cannot be negative, got {n}")
    current = _period_start(today, cfg, unit, basis)
    start = _shift(current, unit, n * spec.direction)
    return DateRange(start, _shift(start, unit, 1), unit, basis)


def _resolve_to_date(spec: Spec, today: date, cfg: WranglerConfig, basis: Basis) -> DateRange:
    """YTD / MTD / QTD: the current period so far, ``today`` included.

    "Last YTD" shifts the whole window back a year, so both are the same length.
    """
    unit = spec.unit or Grain.YEAR

    if spec.month is not None:
        # With no year, the most recent March that has actually finished.
        year = spec.year
        if year is None:
            year = today.year if date(today.year, spec.month, 1) <= today else today.year - 1
        end = month_range(year, spec.month).end
        assert end is not None
        start = _period_start(end - timedelta(days=1), cfg, unit, basis)
        return DateRange(start, end, unit, basis)

    if spec.year is not None:
        end = year_range(spec.year, cfg.fiscal, basis).end
        assert end is not None
        start = _period_start(end - timedelta(days=1), cfg, unit, basis)
        return DateRange(start, end, unit, basis)

    start = _period_start(today, cfg, unit, basis)
    end = today + timedelta(days=1)
    if spec.direction < 0:
        start, end = _shift(start, Grain.YEAR, -1), _shift(end, Grain.YEAR, -1)
    return DateRange(start, end, unit, basis)


# ---------------------------------------------------------------------------
# Modifiers -- where ranges become open-ended
# ---------------------------------------------------------------------------


def _apply_mod(r: DateRange, mod: Mod | None, today: date) -> DateRange:
    """Reshape a closed range according to a modifier.

    An open end stays None rather than quietly becoming today -- whether "since March"
    means "up to now" or "for all time" is the caller's question, via ``clamp()``.
    """
    if mod is None:
        return r
    if mod is Mod.SINCE:
        return DateRange(r.start, None, r.grain, r.basis, mod)
    if mod is Mod.AFTER:
        return DateRange(r.end, None, r.grain, r.basis, mod)
    if mod is Mod.UNTIL:
        return DateRange(None, r.end, r.grain, r.basis, mod)
    if mod is Mod.BEFORE:
        return DateRange(None, r.start, r.grain, r.basis, mod)
    if mod is Mod.AS_OF:
        # A snapshot, not a span: the last day of whatever period was named.
        anchor = r.end_inclusive if r.end is not None else today
        assert anchor is not None
        snap = day_range(anchor)
        return DateRange(snap.start, snap.end, Grain.DAY, r.basis, mod)
    return r
