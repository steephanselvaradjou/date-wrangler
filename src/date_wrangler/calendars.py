"""Period arithmetic for every grain, on both the calendar and fiscal bases.

A fiscal year *label* and a calendar year are different kinds of number and are never
interchangeable here -- conflating them is how a bare "Q1" ends up in the wrong year.

Everything returned is half-open, so nothing in this module subtracts a day.
"""

from __future__ import annotations

import calendar as _calendar
from datetime import MAXYEAR, MINYEAR, date, timedelta

from .config import FiscalCalendar, YearLabel
from .types import Basis, DateRange, Grain

__all__ = [
    "add_months",
    "fiscal_year_start",
    "fiscal_year_of",
    "current_fiscal_year",
    "year_range",
    "half_range",
    "quarter_range",
    "month_range",
    "fiscal_month_range",
    "week_range",
    "day_range",
    "DateRangeOverflow",
]


class DateRangeOverflow(ValueError):
    """Raised when a period falls outside the range ``datetime.date`` can represent."""


def _check_year(year: int, what: str) -> int:
    """Guard a year before it reaches ``date()``, so a typo cannot crash a request."""
    if not MINYEAR <= year <= MAXYEAR:
        raise DateRangeOverflow(
            f"{what} resolves to year {year}, outside the supported range "
            f"{MINYEAR}-{MAXYEAR}"
        )
    return year


def add_months(d: date, n: int) -> date:
    """``d`` shifted by ``n`` months, clamping to the end of a short month."""
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    _check_year(year, f"{d.isoformat()} + {n} months")
    day = min(d.day, _calendar.monthrange(year, month)[1])
    return date(year, month, day)


# ---------------------------------------------------------------------------
# Fiscal year <-> calendar year
# ---------------------------------------------------------------------------


def _label_offset(cal: FiscalCalendar) -> int:
    """How far the fiscal label sits ahead of the year the fiscal year starts in.

    Zero for a January start -- it begins and ends in the same calendar year, so both
    conventions agree -- and for START_YEAR labelling. One otherwise.
    """
    if cal.is_calendar_aligned or cal.label_by is YearLabel.START_YEAR:
        return 0
    return 1


def fiscal_year_start(label: int, cal: FiscalCalendar) -> date:
    """The first day of the fiscal year named ``label``."""
    start_year = _check_year(label - _label_offset(cal), f"fiscal year {label}")
    return date(start_year, cal.start_month, 1)


def fiscal_year_of(day: date, cal: FiscalCalendar) -> int:
    """The label of the fiscal year containing ``day``."""
    start_year = day.year if day.month >= cal.start_month else day.year - 1
    return start_year + _label_offset(cal)


def current_fiscal_year(today: date, cal: FiscalCalendar) -> int:
    """The label of the fiscal year in progress on ``today``."""
    return fiscal_year_of(today, cal)


# ---------------------------------------------------------------------------
# Period constructors
# ---------------------------------------------------------------------------


def _span(start: date, months: int, grain: Grain, basis: Basis) -> DateRange:
    return DateRange(start=start, end=add_months(start, months), grain=grain, basis=basis)


def year_range(label: int, cal: FiscalCalendar, basis: Basis) -> DateRange:
    """A full year, fiscal or calendar."""
    if basis is Basis.FISCAL:
        return _span(fiscal_year_start(label, cal), 12, Grain.YEAR, basis)
    _check_year(label, f"year {label}")
    return _span(date(label, 1, 1), 12, Grain.YEAR, basis)


def half_range(label: int, half: int, cal: FiscalCalendar, basis: Basis) -> DateRange:
    """H1 or H2 of the given year."""
    if half not in (1, 2):
        raise ValueError(f"half must be 1 or 2, got {half}")
    base = year_range(label, cal, basis).start
    assert base is not None
    return _span(add_months(base, 6 * (half - 1)), 6, Grain.HALF, basis)


def quarter_range(label: int, quarter: int, cal: FiscalCalendar, basis: Basis) -> DateRange:
    """Q1-Q4 of the given year, counted from the start of the fiscal year.

    With an April start Q1 is Apr-Jun, matching pandas' ``freq='Q-MAR'``.
    """
    if not 1 <= quarter <= 4:
        raise ValueError(f"quarter must be 1-4, got {quarter}")
    base = year_range(label, cal, basis).start
    assert base is not None
    return _span(add_months(base, 3 * (quarter - 1)), 3, Grain.QUARTER, basis)


def month_range(year: int, month: int) -> DateRange:
    """A single calendar month. Months are calendar facts; there is no fiscal variant."""
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12, got {month}")
    _check_year(year, f"{year}-{month:02d}")
    return _span(date(year, month, 1), 1, Grain.MONTH, Basis.CALENDAR)


def fiscal_month_range(label: int, index: int, cal: FiscalCalendar) -> DateRange:
    """The ``index``-th month of a fiscal year, 1-based.

    With an April start the 12th month of FY2026 is March 2026, not December.
    """
    if not 1 <= index <= 12:
        raise ValueError(f"fiscal month index must be 1-12, got {index}")
    return _span(
        add_months(fiscal_year_start(label, cal), index - 1), 1, Grain.MONTH, Basis.FISCAL
    )


def week_range(day: date, week_starts_on: int = 0) -> DateRange:
    """The week containing ``day``. ``week_starts_on`` is 0=Monday through 6=Sunday."""
    if not 0 <= week_starts_on <= 6:
        raise ValueError(f"week_starts_on must be 0-6, got {week_starts_on}")
    start = day - timedelta(days=(day.weekday() - week_starts_on) % 7)
    return DateRange(start, start + timedelta(days=7), Grain.WEEK, Basis.CALENDAR)


def day_range(day: date) -> DateRange:
    """A single day as a half-open range."""
    return DateRange(day, day + timedelta(days=1), Grain.DAY, Basis.CALENDAR)
