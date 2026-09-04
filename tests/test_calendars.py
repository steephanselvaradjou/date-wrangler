"""Tests for period arithmetic.

Several cases here are regression pins for specific defects in the predecessor module;
those are marked so nobody "simplifies" them back.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler.calendars import (
    DateRangeOverflow,
    add_months,
    current_fiscal_year,
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
from date_wrangler.config import FiscalCalendar, YearLabel
from date_wrangler.types import Basis, Grain

APR = FiscalCalendar(4, YearLabel.END_YEAR)
JAN = FiscalCalendar(1, YearLabel.END_YEAR)
OCT = FiscalCalendar(10, YearLabel.END_YEAR)
JUL = FiscalCalendar(7, YearLabel.END_YEAR)
US_CORP = FiscalCalendar(10, YearLabel.START_YEAR)


# ---------------------------------------------------------------------------
# add_months
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start,n,expected",
    [
        (date(2024, 1, 31), 1, date(2024, 2, 29)),  # leap-year clamp
        (date(2023, 1, 31), 1, date(2023, 2, 28)),  # non-leap clamp
        (date(2024, 1, 1), 12, date(2025, 1, 1)),
        (date(2024, 1, 1), -1, date(2023, 12, 1)),
        (date(2024, 6, 15), 0, date(2024, 6, 15)),
        (date(2024, 12, 1), 1, date(2025, 1, 1)),  # year rollover
        (date(2024, 1, 1), -13, date(2022, 12, 1)),
    ],
)
def test_add_months(start, n, expected):
    assert add_months(start, n) == expected


def test_add_months_rejects_overflow_instead_of_crashing():
    """REGRESSION: '5000 years ago' used to raise a bare ValueError from date()."""
    with pytest.raises(DateRangeOverflow):
        add_months(date(2025, 1, 1), -12 * 5000)
    with pytest.raises(DateRangeOverflow):
        add_months(date(2025, 1, 1), 12 * 9000)


# ---------------------------------------------------------------------------
# Fiscal year <-> calendar year
# ---------------------------------------------------------------------------


def test_fiscal_year_start_april_end_year_labelling():
    # The pandas Q-MAR convention: FY2024 ends in March 2024.
    assert fiscal_year_start(2024, APR) == date(2023, 4, 1)


def test_fiscal_year_start_january_is_not_off_by_one():
    """REGRESSION: a January fiscal start put FY2024 in calendar 2023.

    With a January start the fiscal year *is* the calendar year, so the two bases must
    span the same dates. They still carry different ``basis`` labels, which is intended:
    the range records which calendar the caller asked about, not just where it landed.
    """
    assert fiscal_year_start(2024, JAN) == date(2024, 1, 1)
    fiscal = year_range(2024, JAN, Basis.FISCAL)
    calendar_ = year_range(2024, JAN, Basis.CALENDAR)
    assert (fiscal.start, fiscal.end) == (calendar_.start, calendar_.end)
    assert (fiscal.start, fiscal.end) == (date(2024, 1, 1), date(2025, 1, 1))


def test_fiscal_year_start_honours_start_year_labelling():
    assert fiscal_year_start(2024, US_CORP) == date(2024, 10, 1)
    assert fiscal_year_start(2024, OCT) == date(2023, 10, 1)


@pytest.mark.parametrize(
    "day,cal,expected",
    [
        (date(2025, 3, 31), APR, 2025),
        (date(2025, 4, 1), APR, 2026),
        (date(2025, 9, 4), APR, 2026),
        (date(2026, 1, 15), APR, 2026),
        (date(2025, 9, 4), JAN, 2025),
        (date(2025, 9, 4), OCT, 2025),
        (date(2025, 10, 1), OCT, 2026),
        (date(2025, 10, 1), US_CORP, 2025),
    ],
)
def test_fiscal_year_of(day, cal, expected):
    assert fiscal_year_of(day, cal) == expected


def test_fiscal_year_round_trips():
    for label in range(1990, 2050):
        for cal in (APR, JAN, OCT, JUL, US_CORP):
            assert fiscal_year_of(fiscal_year_start(label, cal), cal) == label


# ---------------------------------------------------------------------------
# The bare-period default: the nine-months-of-the-year bug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("today", [date(2025, 9, 4), date(2026, 1, 15), date(2025, 4, 1)])
def test_bare_quarter_resolves_against_the_current_fiscal_year(today):
    """REGRESSION: a bare Q1 defaulted to today.year read as a fiscal label, so from
    April to December it resolved to the fiscal year that had already ended."""
    fy = current_fiscal_year(today, APR)
    assert fy == 2026
    q1 = quarter_range(fy, 1, APR, Basis.FISCAL)
    assert q1.start == date(2025, 4, 1)
    assert q1.end == date(2025, 7, 1)


# ---------------------------------------------------------------------------
# Period constructors
# ---------------------------------------------------------------------------


def test_quarter_range_fiscal_counts_from_fiscal_start():
    assert quarter_range(2026, 1, APR, Basis.FISCAL).start == date(2025, 4, 1)
    assert quarter_range(2026, 4, APR, Basis.FISCAL).start == date(2026, 1, 1)


def test_quarter_range_calendar_ignores_fiscal_calendar():
    for cal in (APR, JAN, OCT):
        assert quarter_range(2024, 1, cal, Basis.CALENDAR).start == date(2024, 1, 1)
        assert quarter_range(2024, 4, cal, Basis.CALENDAR).end == date(2025, 1, 1)


def test_half_range():
    assert half_range(2026, 1, APR, Basis.FISCAL).start == date(2025, 4, 1)
    assert half_range(2026, 2, APR, Basis.FISCAL).start == date(2025, 10, 1)
    assert half_range(2024, 2, APR, Basis.CALENDAR).start == date(2024, 7, 1)


def test_fiscal_month_index_is_relative_to_the_fiscal_year():
    """REGRESSION: 'twelfth month' resolved to December rather than the 12th fiscal month."""
    assert fiscal_month_range(2026, 1, APR).start == date(2025, 4, 1)
    assert fiscal_month_range(2026, 12, APR).start == date(2026, 3, 1)


def test_month_range_handles_leap_february():
    assert month_range(2024, 2).end == date(2024, 3, 1)
    assert month_range(2024, 2).end_inclusive == date(2024, 2, 29)
    assert month_range(2023, 2).end_inclusive == date(2023, 2, 28)


@pytest.mark.parametrize("bad", [0, 13, -1])
def test_month_range_rejects_bad_months(bad):
    with pytest.raises(ValueError):
        month_range(2024, bad)


def test_week_range():
    # 2025-09-04 is a Thursday.
    w = week_range(date(2025, 9, 4))
    assert w.start == date(2025, 9, 1)  # Monday
    assert w.end == date(2025, 9, 8)
    assert w.grain is Grain.WEEK
    sun = week_range(date(2025, 9, 4), week_starts_on=6)
    assert sun.start == date(2025, 8, 31)


def test_day_range_is_one_day_wide():
    d = day_range(date(2024, 2, 29))
    assert d.start == date(2024, 2, 29)
    assert d.end == date(2024, 3, 1)
    assert d.days == 1


# ---------------------------------------------------------------------------
# Structural properties -- these are what make half-open ranges worth having
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cal", [APR, JAN, OCT, JUL])
def test_consecutive_quarters_tile_without_gap_or_overlap(cal):
    for label in (2024, 2025):
        for q in (1, 2, 3):
            assert (
                quarter_range(label, q, cal, Basis.FISCAL).end
                == quarter_range(label, q + 1, cal, Basis.FISCAL).start
            )
        assert (
            quarter_range(label, 4, cal, Basis.FISCAL).end
            == quarter_range(label + 1, 1, cal, Basis.FISCAL).start
        )


@pytest.mark.parametrize("cal", [APR, JAN, OCT, JUL])
def test_four_quarters_exactly_cover_the_year(cal):
    y = year_range(2025, cal, Basis.FISCAL)
    assert quarter_range(2025, 1, cal, Basis.FISCAL).start == y.start
    assert quarter_range(2025, 4, cal, Basis.FISCAL).end == y.end


@pytest.mark.parametrize("cal", [APR, JAN, OCT, JUL])
def test_twelve_fiscal_months_exactly_cover_the_year(cal):
    y = year_range(2025, cal, Basis.FISCAL)
    assert fiscal_month_range(2025, 1, cal).start == y.start
    assert fiscal_month_range(2025, 12, cal).end == y.end


def test_all_periods_are_half_open_and_never_inverted():
    cases = [
        year_range(2025, APR, Basis.FISCAL),
        quarter_range(2025, 3, APR, Basis.FISCAL),
        half_range(2025, 2, APR, Basis.CALENDAR),
        month_range(2024, 2),
        week_range(date(2025, 9, 4)),
        day_range(date(2025, 9, 4)),
    ]
    for r in cases:
        assert r.start is not None and r.end is not None
        assert r.start < r.end
        assert r.end_inclusive == r.end - __import__("datetime").timedelta(days=1)
        assert r.start in r
        assert r.end not in r  # the defining property of a half-open range
