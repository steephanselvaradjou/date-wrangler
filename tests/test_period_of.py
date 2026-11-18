"""period_of(day, grain, config): the whole period that holds a day.

Public so a report's axis -- the first day of every week, month, quarter or year from one
date to another -- needs nothing from the private calendars module. It is the rule the
parser reads "the week of 5 October" and "the quarter of June" by, and the tests below
hold the two to each other under every fiscal calendar, both bases and every week start.
"""

from __future__ import annotations

import calendar
import itertools
from datetime import date, datetime, timedelta

import pytest

import date_wrangler
from date_wrangler import (
    Basis,
    DateRange,
    DateRangeOverflow,
    FiscalCalendar,
    Grain,
    WranglerConfig,
    YearLabel,
    parse_one,
    period_of,
)

DAYS = [
    date(2026, 11, 18), date(2026, 1, 1), date(2025, 12, 31), date(2026, 3, 31),
    date(2026, 4, 1), date(2024, 2, 29), date(2026, 6, 30), date(2026, 7, 1),
    date(2026, 9, 30), date(2026, 10, 1), date(2027, 2, 28),
]
UNITS = ["week", "month", "quarter", "half", "year"]


def configs():
    for start, label, basis in itertools.product(range(1, 13), YearLabel, Basis):
        yield WranglerConfig(fiscal=FiscalCalendar(start, label), year_basis=basis)
    for week in range(7):
        yield WranglerConfig(week_starts_on=week)


def spoken(d: date) -> str:
    return f"{d.day} {calendar.month_name[d.month]} {d.year}"


def test_it_is_what_the_parser_reads_as_the_period_of_a_day():
    for cfg in configs():
        for d, unit in itertools.product(DAYS, UNITS):
            m = parse_one(f"the {unit} of {spoken(d)}", today=date(2026, 11, 18), config=cfg)
            assert m is not None
            got = period_of(d, unit, cfg)
            assert (got.start, got.end, got.grain, got.basis) == (
                m.range.start, m.range.end, m.range.grain, m.range.basis
            ), (unit, d, cfg)


@pytest.mark.parametrize("d", DAYS)
@pytest.mark.parametrize("grain", list(Grain))
def test_the_period_holds_the_day_and_is_one_whole_unit(d, grain):
    for cfg in configs():
        r = period_of(d, grain, cfg)
        assert r.start <= d < r.end
        assert r.grain is grain
        assert r.split(grain) == [r]  # exactly one bucket of its own grain


def test_basis_can_be_chosen_for_one_axis():
    fiscal = WranglerConfig(year_basis="fiscal")  # April years
    d = date(2026, 11, 18)
    assert str(period_of(d, "half", fiscal)) == "2026-10-01 .. 2027-03-31 (182 days)"
    assert str(period_of(d, "half", fiscal, basis="calendar")) == (
        "2026-07-01 .. 2026-12-31 (184 days)"
    )
    assert period_of(d, Grain.YEAR, WranglerConfig(), basis=Basis.FISCAL).start == date(2026, 4, 1)
    # A month and a week are the same on either basis.
    assert period_of(d, "month", fiscal).start == period_of(d, "month").start
    assert period_of(d, "week", fiscal).start == period_of(d, "week").start


def test_a_week_starts_on_the_configured_day():
    d = date(2026, 11, 18)  # a Wednesday
    assert [period_of(d, "week", WranglerConfig(week_starts_on=w)).start.weekday()
            for w in range(7)] == list(range(7))


@pytest.mark.parametrize("grain", ["week", "month", "quarter", "half", "year"])
@pytest.mark.parametrize("cfg", [WranglerConfig(), WranglerConfig(year_basis="fiscal",
                                                                  week_starts_on=6),
                                 WranglerConfig(fiscal=FiscalCalendar(7, YearLabel.START_YEAR),
                                                year_basis="fiscal")],
                         ids=["calendar", "fiscal-april-sunday", "fiscal-july-start-year"])
def test_a_trend_axis_is_every_whole_period_from_one_day_to_another(grain, cfg):
    start, end = date(2024, 2, 29), date(2026, 11, 18)
    first, last = period_of(start, grain, cfg), period_of(end, grain, cfg)
    buckets = DateRange(first.start, last.end, Grain(grain)).split(Grain(grain))
    assert buckets[0].start <= start and end < buckets[-1].end
    for b in buckets:
        # Each bucket is a whole period, the one that holds its own first day.
        p = period_of(b.start, grain, cfg)
        assert (b.start, b.end) == (p.start, p.end)
    assert all(a.end == b.start for a, b in zip(buckets, buckets[1:], strict=False))


def test_grain_and_basis_are_taken_as_written():
    d = date(2026, 11, 18)
    assert period_of(d, " Quarter ") == period_of(d, Grain.QUARTER)
    assert period_of(d, "year", basis="FISCAL") == period_of(d, "year", basis=Basis.FISCAL)
    with pytest.raises(ValueError, match="grain must be one of day, week, month"):
        period_of(d, "fortnight")
    with pytest.raises(ValueError, match="basis must be one of"):
        period_of(d, "year", basis="lunar")


def test_a_datetime_is_its_day_and_anything_else_is_refused():
    assert period_of(datetime(2026, 11, 18, 23, 59), "day").start == date(2026, 11, 18)
    with pytest.raises(TypeError, match="day must be a date"):
        period_of("2026-11-18", "day")  # type: ignore[arg-type]


def test_the_ends_of_the_calendar():
    assert period_of(date.min, "year").start == date(1, 1, 1)
    assert period_of(date.max - timedelta(days=40), "month").end is not None
    with pytest.raises(DateRangeOverflow):
        period_of(date.max, "year")  # its end, 1 January 10000, cannot be written


def test_it_is_public():
    assert "period_of" in date_wrangler.__all__
