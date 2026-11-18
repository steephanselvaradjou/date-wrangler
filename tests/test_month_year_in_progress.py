"""Which year a month with no year falls in.

A month follows year_basis, as a quarter does: on the calendar it is this calendar year's,
on a fiscal basis this fiscal year's. Before, a month always took the calendar year, so on
an April year in November "January" was January of this year while "Q4", which holds it,
was January to March of next.

Two things look back instead. "Since", which points to the past -- "since March" -- takes
the latest such month that has begun; "from", "after" and the rest may point ahead, and
keep the year in progress. And a span ending on a period counted from today -- "15 March
to last month" -- starts before it.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import Basis, FiscalCalendar, WranglerConfig, YearLabel, parse, parse_one

NOV = date(2026, 11, 17)  # FY2027 on an April year: April 2026 to March 2027
FISCAL = WranglerConfig(year_basis=Basis.FISCAL)
CALENDAR = WranglerConfig()


def days(text, cfg, today=NOV):
    m = parse_one(text, today=today, config=cfg)
    assert m is not None, text
    return m.range.start, m.range.end_inclusive


@pytest.mark.parametrize(
    "text,fiscal,calendar",
    [
        ("January", (date(2027, 1, 1), date(2027, 1, 31)), (date(2026, 1, 1), date(2026, 1, 31))),
        ("March", (date(2027, 3, 1), date(2027, 3, 31)), (date(2026, 3, 1), date(2026, 3, 31))),
        ("April", (date(2026, 4, 1), date(2026, 4, 30)), (date(2026, 4, 1), date(2026, 4, 30))),
        ("15 January", (date(2027, 1, 15),) * 2, (date(2026, 1, 15),) * 2),
        ("the 2nd week of January", (date(2027, 1, 8), date(2027, 1, 14)),
         (date(2026, 1, 8), date(2026, 1, 14))),
        ("first Monday of March", (date(2027, 3, 1),) * 2, (date(2026, 3, 2),) * 2),
        ("this March", (date(2027, 3, 1), date(2027, 3, 31)),
         (date(2026, 3, 1), date(2026, 3, 31))),
    ],
)
def test_a_month_with_no_year_is_in_the_year_in_progress(text, fiscal, calendar):
    assert days(text, FISCAL) == fiscal
    assert days(text, CALENDAR) == calendar


def test_a_month_and_the_quarter_that_holds_it_agree():
    """REGRESSION: on a fiscal basis "January" was a year before "Q4", which holds it."""
    january, q4 = days("January", FISCAL), days("Q4", FISCAL)
    assert q4[0] <= january[0] and january[1] <= q4[1]


@pytest.mark.parametrize(
    "fiscal", [FiscalCalendar(7), FiscalCalendar(10, YearLabel.START_YEAR), FiscalCalendar(2)],
)
def test_every_fiscal_calendar_puts_a_month_inside_its_year_in_progress(fiscal):
    cfg = WranglerConfig(fiscal=fiscal, year_basis=Basis.FISCAL)
    year = days("this year", cfg)
    for month in ("January", "March", "June", "September", "December"):
        start, end = days(month, cfg)
        assert year[0] <= start and end <= year[1], (month, fiscal)


def test_a_year_written_or_counted_is_taken_as_it_is():
    assert days("March 2026", FISCAL) == (date(2026, 3, 1), date(2026, 3, 31))
    assert days("last March", FISCAL) == (date(2026, 3, 1), date(2026, 3, 31))
    assert days("next March", FISCAL) == (date(2027, 3, 1), date(2027, 3, 31))
    # "last year" is FY2026 on this basis, and its March is March 2026.
    assert days("March last year", FISCAL) == (date(2026, 3, 1), date(2026, 3, 31))


@pytest.mark.parametrize(
    "text,start",
    [
        ("since March", date(2026, 3, 1)),
        ("Since March", date(2026, 3, 1)),
        ("ever since March", date(2026, 3, 1)),
        ("since 15 March", date(2026, 3, 15)),
        ("since mid March", date(2026, 3, 11)),
        ("since the first Monday of March", date(2026, 3, 2)),
    ],
)
@pytest.mark.parametrize("cfg", [FISCAL, CALENDAR], ids=["fiscal", "calendar"])
def test_since_takes_the_latest_month_that_has_begun(text, start, cfg):
    """"Since" points to the past. Asked in November on an April year, this year's March is
    still to come, and "since March" means the one that has passed; on the calendar the
    same holds in February."""
    today = NOV if cfg is FISCAL else date(2027, 2, 15)
    m = parse_one(text, today=today, config=cfg)
    assert m is not None and m.range.start == start and m.range.end is None


@pytest.mark.parametrize(
    "text,start",
    [
        ("from 1 March", date(2027, 3, 1)),
        ("the new price applies from 1 March", date(2027, 3, 1)),
        ("after 31 March", date(2027, 4, 1)),
        ("on or after 1 March", date(2027, 3, 1)),
        ("not before 1 March", date(2027, 3, 1)),
        ("no earlier than 1 March", date(2027, 3, 1)),
        ("starting March", date(2027, 3, 1)),
        ("March onwards", date(2027, 3, 1)),
        (">= 1 March", date(2027, 3, 1)),
    ],
)
@pytest.mark.parametrize("cfg", [FISCAL, CALENDAR], ids=["fiscal", "calendar"])
def test_any_other_start_keeps_the_year_in_progress(text, start, cfg):
    """Only "since" looks back. "From", "after", "on or after" and the rest may point
    ahead -- "the new price applies from 1 March", said in February -- so a month with no
    year stays in the year in progress, as it does with no bound at all."""
    today = NOV if cfg is FISCAL else date(2027, 2, 15)
    m = parse_one(text, today=today, config=cfg)
    assert m is not None and m.range.start == start and m.range.end is None


@pytest.mark.parametrize(
    "today,cfg,start",
    [
        (date(2028, 1, 15), CALENDAR, date(2024, 2, 29)),  # 2028's has not begun
        (date(2029, 3, 1), CALENDAR, date(2028, 2, 29)),   # 2029 has none
        (date(2027, 11, 17), FISCAL, date(2024, 2, 29)),   # FY2028's is 2028's, to come
        (date(2028, 3, 5), CALENDAR, date(2028, 2, 29)),
    ],
)
def test_a_bound_on_29_february_looks_back_to_a_year_that_has_one(today, cfg, start):
    """REGRESSION: the year in progress had no 29 February, or had not reached it, and
    the year before had none either -- so "since 29 February" found nothing."""
    m = parse_one("since 29 February", today=today, config=cfg)
    assert m is not None and m.range.start == start and m.range.end is None


@pytest.mark.parametrize("cfg", [FISCAL, CALENDAR], ids=["fiscal", "calendar"])
@pytest.mark.parametrize(
    "text,start",
    [
        ("December to last fiscal quarter", date(2025, 12, 1)),
        ("December to last fiscal year", date(2025, 12, 1)),
        ("15 December to fiscal YTD", date(2025, 12, 15)),
        ("December to last quarter", date(2025, 12, 1)),
    ],
)
def test_a_month_beside_a_fiscal_period_moves_back_one_year_not_two(text, start, cfg):
    """REGRESSION: the month took "fiscal" from its partner, was given a calendar year to
    move back from, and read it as a fiscal label: December 2024."""
    found = parse(text, today=NOV, config=cfg)
    assert len(found) == 1 and found[0].range.start == start


def test_a_fiscal_period_to_a_month_runs_forward_to_it():
    """Already wrong in 2.5.0: "fiscal Q3 to August" was refused as running backwards."""
    for cfg in (FISCAL, CALENDAR):
        (m,) = parse("fiscal Q3 to August", today=NOV, config=cfg)
        assert days("fiscal Q3", cfg)[0] == m.range.start < m.range.end
        assert m.range.end_inclusive == date(2027, 8, 31)


def test_a_bound_that_has_begun_stays_where_it_is():
    assert days("since November", FISCAL)[0] == date(2026, 11, 1)
    assert parse_one("since the 2nd week of March", today=date(2026, 3, 10),
                     config=CALENDAR).range.start == date(2026, 3, 8)


@pytest.mark.parametrize("text", ["by March", "until March", "before March"])
def test_a_bound_that_looks_ahead_keeps_the_year_in_progress(text):
    m = parse_one(text, today=NOV, config=FISCAL)
    assert m is not None and m.range.start is None
    assert date(2027, 3, 1) <= m.range.end <= date(2027, 4, 1)


@pytest.mark.parametrize(
    "text", ["15 March to last month", "between 15 March and today", "March to yesterday",
             "15 March - Friday", "June to last quarter"],
)
@pytest.mark.parametrize("cfg", [FISCAL, CALENDAR], ids=["fiscal", "calendar"])
def test_a_span_ending_on_a_period_counted_from_today_starts_before_it(text, cfg):
    """REGRESSION: an end counted from today has no year to move, and the search stopped
    there; with March in the fiscal year in progress, "15 March to last month" asked in
    November split into two dates. The start moves back instead."""
    today = NOV if cfg is FISCAL else date(2027, 2, 15)
    found = parse(text, today=today, config=cfg)
    assert len(found) == 1, found
    r = found[0].range
    # One span, begun already. Its end is wherever the period counted from today ends:
    # "last quarter" is behind, "Friday" alone this week's.
    assert r.start is not None and r.end is not None and r.start <= today and r.start < r.end
