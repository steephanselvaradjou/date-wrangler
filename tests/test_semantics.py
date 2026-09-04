"""Semantic tests: is the answer *right*, not merely well-formed.

The property suite proves nothing nonsensical comes out. These sweep whole years, day by
day, across every fiscal calendar, and check the meaning -- that a day falls in the fiscal
year it claims, that "last quarter" ends exactly where "this quarter" begins, and that a
year-to-date window really is comparable with the one a year earlier.

The pandas comparison is the strongest single check here: `Q-MAR` and friends encode the
same anchored-quarter convention this library uses, so agreeing with them across all twelve
anchor months is independent confirmation the fiscal arithmetic is right.
"""

from __future__ import annotations

import calendar as pycalendar
from datetime import date, timedelta

import pytest

from date_wrangler import Basis, FiscalCalendar, WranglerConfig, YearLabel, parse_one
from date_wrangler.calendars import (
    fiscal_year_of,
    month_range,
    quarter_range,
    year_range,
)

ALL_START_MONTHS = list(range(1, 13))
PROBE_DAYS = [
    date(2024, 2, 29),   # leap day
    date(2025, 1, 1),
    date(2025, 3, 31),   # last day of an April-start fiscal year
    date(2025, 4, 1),    # first day of one
    date(2025, 9, 4),
    date(2025, 12, 31),
    date(2024, 12, 31),
    date(2023, 6, 15),
]


def _range(text: str, today: date, cfg: WranglerConfig | None = None):
    match = parse_one(text, today=today, config=cfg or WranglerConfig())
    assert match is not None, f"{text!r} did not parse"
    return match.range


# ---------------------------------------------------------------------------
# Fiscal-year membership
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("start_month", ALL_START_MONTHS)
def test_every_day_belongs_to_exactly_one_fiscal_year(start_month):
    cal = FiscalCalendar(start_month)
    day = date(2023, 1, 1)
    while day <= date(2026, 12, 31):
        fy = fiscal_year_of(day, cal)
        assert day in year_range(fy, cal, Basis.FISCAL)
        assert day not in year_range(fy - 1, cal, Basis.FISCAL)
        assert day not in year_range(fy + 1, cal, Basis.FISCAL)
        day += timedelta(days=11)


@pytest.mark.parametrize("start_month", ALL_START_MONTHS)
def test_this_year_is_the_fiscal_year_in_progress(start_month):
    cal = FiscalCalendar(start_month)
    cfg = WranglerConfig(fiscal=cal)
    day = date(2024, 1, 1)
    while day <= date(2026, 12, 31):
        want = year_range(fiscal_year_of(day, cal), cal, Basis.FISCAL)
        got = _range("this year", day, cfg)
        assert (got.start, got.end) == (want.start, want.end), f"on {day}"
        day += timedelta(days=13)


@pytest.mark.parametrize("start_month", [1, 2, 4, 7, 10, 12])
def test_bare_quarters_always_use_the_current_fiscal_year(start_month):
    """The defect this pins cost the predecessor a wrong year for nine months of twelve."""
    cal = FiscalCalendar(start_month)
    cfg = WranglerConfig(fiscal=cal)
    day = date(2024, 1, 1)
    while day <= date(2026, 12, 31):
        fy = fiscal_year_of(day, cal)
        for q in (1, 2, 3, 4):
            want = quarter_range(fy, q, cal, Basis.FISCAL)
            got = _range(f"Q{q}", day, cfg)
            assert (got.start, got.end) == (want.start, want.end), f"Q{q} on {day}"
        day += timedelta(days=29)


# ---------------------------------------------------------------------------
# Relative periods
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("today", PROBE_DAYS)
@pytest.mark.parametrize("unit", ["month", "quarter", "year", "week"])
def test_last_this_and_next_are_contiguous(today, unit):
    last = _range(f"last {unit}", today)
    current = _range(f"this {unit}", today)
    nxt = _range(f"next {unit}", today)
    assert last.end == current.start, "a gap between last and this"
    assert current.end == nxt.start, "a gap between this and next"
    assert today in current


@pytest.mark.parametrize("today", PROBE_DAYS)
@pytest.mark.parametrize("n", [1, 2, 3, 6, 12, 24])
def test_last_n_months_abuts_the_current_month_and_spans_n(today, n):
    r = _range(f"last {n} months", today)
    assert r.end == date(today.year, today.month, 1)
    spanned = (r.end.year - r.start.year) * 12 + (r.end.month - r.start.month)
    assert spanned == n


@pytest.mark.parametrize("today", PROBE_DAYS)
@pytest.mark.parametrize("n", [1, 3, 12])
def test_n_months_ago_is_one_month_n_back(today, n):
    """"3 months ago" names a single month. The predecessor returned three."""
    r = _range(f"{n} months ago", today)
    spanned = (r.end.year - r.start.year) * 12 + (r.end.month - r.start.month)
    assert spanned == 1
    current = date(today.year, today.month, 1)
    back = (current.year * 12 + current.month) - (r.start.year * 12 + r.start.month)
    assert back == n


# ---------------------------------------------------------------------------
# To-date windows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("today", PROBE_DAYS)
@pytest.mark.parametrize("start_month", [1, 4, 7, 10])
def test_ytd_and_last_ytd_are_comparable(today, start_month):
    cfg = WranglerConfig(fiscal=FiscalCalendar(start_month))
    now = _range("ytd", today, cfg)
    prior = _range("last ytd", today, cfg)
    assert now.end == today + timedelta(days=1), "year-to-date must include today"
    assert abs((now.days or 0) - (prior.days or 0)) <= 2, "windows differ by more than a leap day"
    assert prior.end <= now.start, "the prior window must not extend into the current one"


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,months",
    [
        ("January 2024", [(2024, 1)]),
        ("Q1 FY25", [(2024, 4), (2024, 5), (2024, 6)]),
        ("H1 FY25", [(2024, m) for m in (4, 5, 6, 7, 8, 9)]),
        ("CY2024", [(2024, m) for m in range(1, 13)]),
        ("quarter ending June 2024", [(2024, 4), (2024, 5), (2024, 6)]),
    ],
)
def test_a_period_covers_exactly_its_own_months(text, months):
    r = _range(text, date(2025, 9, 4))
    covered: list[tuple[int, int]] = []
    day = r.start
    assert day is not None and r.end is not None
    while day < r.end:
        if (day.year, day.month) not in covered:
            covered.append((day.year, day.month))
        day += timedelta(days=1)
    assert covered == months


# ---------------------------------------------------------------------------
# Leap years and impossible dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("year", [1900, 2000, 2023, 2024, 2100, 2400])
def test_february_length_follows_the_gregorian_rule(year):
    feb = month_range(year, 2)
    expected = 29 if pycalendar.isleap(year) else 28
    assert feb.days == expected
    assert feb.end_inclusive is not None and feb.end_inclusive.day == expected


def test_leap_day_parses_and_impossible_days_do_not():
    today = date(2025, 9, 4)
    assert _range("29 February 2024", today).start == date(2024, 2, 29)
    assert parse_one("30 February 2024", today=today) is None
    assert parse_one("31 June 2024", today=today) is None


# ---------------------------------------------------------------------------
# Differential: pandas anchored quarters
# ---------------------------------------------------------------------------

_ANCHOR = {
    1: "DEC", 2: "JAN", 3: "FEB", 4: "MAR", 5: "APR", 6: "MAY",
    7: "JUN", 8: "JUL", 9: "AUG", 10: "SEP", 11: "OCT", 12: "NOV",
}


@pytest.mark.parametrize("start_month", ALL_START_MONTHS)
def test_fiscal_quarters_agree_with_pandas(start_month):
    """pandas encodes the same convention, so agreement is independent confirmation."""
    pd = pytest.importorskip("pandas")
    cal = FiscalCalendar(start_month, YearLabel.END_YEAR)
    for year in (2023, 2024, 2025):
        for q in (1, 2, 3, 4):
            ours = quarter_range(year, q, cal, Basis.FISCAL)
            theirs = pd.Period(f"{year}Q{q}", freq=f"Q-{_ANCHOR[start_month]}")
            assert ours.start == theirs.start_time.date()
            assert ours.end_inclusive == theirs.end_time.date()


# ---------------------------------------------------------------------------
# Complex sentences
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence,expected_matches",
    [
        ("Compare Q1 FY25 revenue against Q1 FY24 for the north region", 2),
        ("Show me MTD, QTD and YTD numbers side by side", 3),
        ("Pull the P&L from April 2024 to March 2025 and compare with FY24", 2),
        ("What did we book between 1 Jan 2024 and 31 Mar 2024?", 1),
        ("Trend monthly sales for the trailing twelve months", 1),
        ("Sales in Q1, Q2 and Q3 of FY25", 3),
        ("Anything invoiced since 15 March 2024 but before 1 June 2024?", 2),
        ("Revenue for the quarter ending June 2024 versus the quarter ending June 2023", 2),
        ("Headcount as of 31 March 2024", 1),
        ("Give me last month, this month and next month", 3),
    ],
)
def test_complex_sentences_find_the_right_number_of_periods(sentence, expected_matches):
    from date_wrangler import parse

    assert len(parse(sentence, today=date(2025, 9, 4))) == expected_matches


def test_a_year_stated_once_reaches_every_period_in_a_list():
    from date_wrangler import parse

    found = parse("Sales in Q1, Q2 and Q3 of FY25", today=date(2025, 9, 4))
    assert [m.range.start for m in found] == [
        date(2024, 4, 1), date(2024, 7, 1), date(2024, 10, 1),
    ]


# ---------------------------------------------------------------------------
# The library must be able to read back its own output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("year", [1850, 1900, 1999, 2000, 2024, 2099, 2100, 2150, 2199])
@pytest.mark.parametrize("text", ["{y}-03-15", "15 March {y}", "March {y}", "Q1 {y}"])
def test_dates_far_from_today_round_trip(year, text):
    r"""REGRESSION: the year pattern was ``(?:19|20)\d{2}``, so from 2100 onwards a year
    was not recognised at all. "1 January 2100" matched only "1 January", `substitute`
    re-emitted the year, and the orphan accumulated on every pass -- unbounded growth,
    the precise defect this library was written to remove."""
    from date_wrangler import format_range, parse, substitute

    today = date(2025, 9, 4)
    phrase = text.format(y=year)
    found = parse(phrase, today=today)
    assert found, f"{phrase!r} did not parse"
    assert found[0].span == (0, len(phrase)), (
        f"{phrase!r} matched only {found[0].text!r}; the year was left outside the span"
    )
    once = substitute(phrase, today=today)
    assert substitute(once, today=today) == once, f"{phrase!r} does not converge"
    reparsed = parse(format_range(found[0].range), today=today)
    assert reparsed and reparsed[0].range.start == found[0].range.start


@pytest.mark.parametrize("text", ["5000", "3000 employees", "top 5000 customers", "9999"])
def test_implausible_bare_numbers_are_not_years(text):
    """A bare four-digit number only reads as a year in a plausible century. Beside a
    month or quarter any four digits are fine, because nothing else can be meant there."""
    from date_wrangler import parse

    assert parse(text, today=date(2025, 9, 4)) == []
