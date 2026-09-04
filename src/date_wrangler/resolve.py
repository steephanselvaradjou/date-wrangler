"""Turn a :class:`~date_wrangler.spec.Spec` into a concrete :class:`DateRange`.

Where "what did they say" becomes "which days". Three distinctions carry most of the
weight: AGO names one period while RELATIVE names a span, a fiscal period with no stated
year belongs to the current *fiscal* year rather than ``today.year``, and "last YTD"
shifts the window back a year instead of widening to the whole prior year.
"""

from __future__ import annotations

from datetime import date, timedelta

from .calendars import (
    add_months,
    day_range,
    fiscal_month_range,
    fiscal_year_of,
    fiscal_year_start,
    half_range,
    month_range,
    quarter_range,
    week_range,
    year_range,
)
from .config import WranglerConfig
from .spec import Kind, Spec
from .types import Basis, DateRange, Grain, Mod

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
        start = week_range(today).start
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
    except (ValueError, OverflowError) as exc:
        if isinstance(exc, UnresolvableSpec):
            raise
        raise UnresolvableSpec(str(exc)) from exc
    return _apply_mod(base, spec.mod, today)


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
        return _resolve_relative(spec, today, cfg, basis)

    if spec.kind is Kind.AGO:
        return _resolve_ago(spec, today, cfg, basis)

    if spec.kind is Kind.TO_DATE:
        return _resolve_to_date(spec, today, cfg, basis)

    if spec.kind is Kind.WEEKDAY:
        return _resolve_weekday(spec, today)

    if spec.kind is Kind.PERIOD_ENDING:
        return _resolve_period_ending(spec, today, cfg, basis)

    raise UnresolvableSpec(f"unhandled spec kind {spec.kind}")


def _resolve_weekday(spec: Spec, today: date) -> DateRange:
    """"last Monday", "next Friday", "this Tuesday".

    Past and future are strict -- on a Thursday, "last Thursday" is a week ago. "This
    Tuesday" is the one in the current week, either side of today.
    """
    if spec.index is None:
        raise UnresolvableSpec("a weekday spec needs a weekday")
    if spec.direction == 0:
        week_start = week_range(today).start
        assert week_start is not None
        return day_range(week_start + timedelta(days=spec.index))
    delta = (spec.index - today.weekday()) % 7
    if spec.direction < 0:
        back = delta - 7 if delta else -7
        return day_range(today + timedelta(days=back))
    return day_range(today + timedelta(days=delta or 7))


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


def _resolve_relative(spec: Spec, today: date, cfg: WranglerConfig, basis: Basis) -> DateRange:
    """"last 3 months" -- ``count`` whole units, excluding the current one.

    Asked in September that is June, July and August; including a part-finished September
    would mix a complete period with an incomplete one.
    """
    unit = spec.unit or Grain.MONTH
    n = spec.count if spec.count is not None else 1
    if n < 1:
        raise UnresolvableSpec(f"a period count must be at least 1, got {n}")
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
