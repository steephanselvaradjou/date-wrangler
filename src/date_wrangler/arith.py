"""Month and business-day arithmetic, underneath everything else.

This sits below :mod:`date_wrangler.types` so that :class:`~date_wrangler.types.DateRange`
can shift, split and count working days without importing the calendar layer that is
built on top of it. :mod:`date_wrangler.calendars` re-exports every name here, which is
where the rest of the package still imports them from.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import MAXYEAR, MINYEAR, date, timedelta

__all__ = [
    "add_months",
    "DateRangeOverflow",
    "DEFAULT_WEEKEND",
    "is_business_day",
    "add_business_days",
    "count_business_days",
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


# ---------------------------------------------------------------------------
# Business days
# ---------------------------------------------------------------------------

#: Saturday and Sunday, by ``date.weekday()`` numbering.
DEFAULT_WEEKEND: tuple[int, ...] = (5, 6)


def is_business_day(
    day: date,
    weekend: Sequence[int] = DEFAULT_WEEKEND,
    holidays: Collection[date] = (),
) -> bool:
    """Whether ``day`` is a working day on the calendar you describe.

    Holidays are supplied, never guessed: which days are holidays depends on a country, a
    region, an industry and sometimes a single company's handbook, and a library that
    guessed would be wrong somewhere and confident everywhere.
    """
    return day.weekday() not in weekend and day not in holidays


def add_business_days(
    day: date,
    n: int,
    weekend: Sequence[int] = DEFAULT_WEEKEND,
    holidays: Collection[date] = (),
) -> date:
    """``day`` moved ``n`` working days, landing on one.

    ``n == 0`` snaps forward to the next working day if ``day`` is not one, which is what
    "the next business day" means when asked on a Saturday.
    """
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    current = day
    if remaining == 0:
        while not is_business_day(current, weekend, holidays):
            current += timedelta(days=1)
            _guard(current)
        return current
    while remaining:
        current += timedelta(days=step)
        _guard(current)
        if is_business_day(current, weekend, holidays):
            remaining -= 1
    return current


def _guard(day: date) -> None:
    """A weekend list covering all seven days would otherwise walk off the calendar."""
    if day.year <= MINYEAR or day.year >= MAXYEAR:
        raise DateRangeOverflow(
            "business-day arithmetic ran off the supported calendar; check that `weekend` "
            "leaves at least one working day"
        )


def count_business_days(
    start: date,
    end: date,
    weekend: Sequence[int] = DEFAULT_WEEKEND,
    holidays: Collection[date] = (),
) -> int:
    """Working days in the half-open range ``[start, end)``.

    Counted a whole week at a time rather than a day at a time, so a decade costs the same
    as a fortnight; only the ragged ends and the holidays are walked.
    """
    if end <= start:
        return 0
    span = (end - start).days
    working_per_week = 7 - len({d % 7 for d in weekend})
    whole_weeks, leftover = divmod(span, 7)
    total = whole_weeks * working_per_week
    for offset in range(leftover):
        if (start + timedelta(days=whole_weeks * 7 + offset)).weekday() not in weekend:
            total += 1
    for holiday in set(holidays):
        if start <= holiday < end and holiday.weekday() not in weekend:
            total -= 1
    return total
