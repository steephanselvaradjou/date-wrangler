"""Month arithmetic, underneath everything else.

This sits below :mod:`date_wrangler.types` so that :class:`~date_wrangler.types.DateRange`
can shift and split itself without importing the calendar layer that is built on top of it.
:mod:`date_wrangler.calendars` re-exports both names, which is where the rest of the
package still imports them from.
"""

from __future__ import annotations

from datetime import MAXYEAR, MINYEAR, date

__all__ = ["add_months", "DateRangeOverflow"]


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


_MONTH_LENGTHS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        return 29
    return _MONTH_LENGTHS[month - 1]


def add_months(d: date, n: int) -> date:
    """``d`` shifted by ``n`` months, clamping to the end of a short month.

    Hot enough to be worth the hand-rolled arithmetic: this is the innermost step of every
    relative period. ``calendar.monthrange`` builds a date of its own to get the weekday it
    is also asked for and we never want, and the overflow message costs an ``isoformat``
    on every call to describe a failure that almost never happens -- so both are deferred.
    """
    total = d.month - 1 + n
    year = d.year + total // 12
    month = total % 12 + 1
    if not MINYEAR <= year <= MAXYEAR:
        _check_year(year, f"{d.isoformat()} + {n} months")
    day = d.day if d.day <= 28 else min(d.day, _days_in_month(year, month))
    return date(year, month, day)
