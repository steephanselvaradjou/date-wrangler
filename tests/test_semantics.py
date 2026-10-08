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


# ---------------------------------------------------------------------------
# Which basis a year is read on
#
# A year on its own is a calendar year; only a quarter or half *labelled* with one follows
# the configured basis, because that case is genuinely ambiguous. Each case here once came
# down to which rule happened to match the phrase rather than to what it said.
# ---------------------------------------------------------------------------

OCT8 = date(2026, 10, 8)
CAL_2013 = (date(2013, 1, 1), date(2014, 1, 1))


def _span(text, cfg):
    m = parse_one(text, today=OCT8, config=cfg)
    assert m is not None, text
    return m.range.start, m.range.end


YEAR_2013 = ["2013", "in 2013", "the year 2013", "year 2013", "in the year 2013", "for year 2013"]


@pytest.mark.parametrize("start_month", [1, 4, 7, 10])
def test_every_way_of_naming_a_year_agrees(start_month):
    """REGRESSION: "the year 2013" went through the rule that also reads "FY2013" and came
    back fiscal while "in 2013" came back calendar. However it is written, a year with no
    FY or CY on it is now one thing -- whatever year_basis says it is."""
    for year_basis in (Basis.CALENDAR, Basis.FISCAL):
        cfg = WranglerConfig(fiscal=FiscalCalendar(start_month=start_month), year_basis=year_basis)
        assert len({_span(text, cfg) for text in YEAR_2013}) == 1


@pytest.mark.parametrize("text", YEAR_2013)
def test_a_year_with_no_marker_follows_year_basis(text):
    assert _span(text, WranglerConfig(year_basis=Basis.CALENDAR)) == CAL_2013
    assert _span(text, WranglerConfig(year_basis=Basis.FISCAL)) == (
        date(2012, 4, 1), date(2013, 4, 1)
    )


def test_the_default_reads_a_bare_year_as_fiscal():
    """The default basis is fiscal, and with nothing written on it a year is read on it --
    so with the default April fiscal year, "2013" is FY2013. General prose, where a year
    almost always means January to December, wants year_basis="calendar"."""
    assert _span("in 2013", WranglerConfig()) == (date(2012, 4, 1), date(2013, 4, 1))


@pytest.mark.parametrize(
    "text,calendar,fiscal",
    [
        ("end of the year 2013", (date(2013, 9, 1), date(2014, 1, 1)),
         (date(2012, 12, 1), date(2013, 4, 1))),
        ("first half of the year 2013", (date(2013, 1, 1), date(2013, 7, 1)),
         (date(2012, 4, 1), date(2012, 10, 1))),
        ("early year 2013", (date(2013, 1, 1), date(2013, 5, 1)),
         (date(2012, 4, 1), date(2012, 8, 1))),
    ],
)
def test_a_part_of_a_numbered_year_follows_year_basis(text, calendar, fiscal):
    """The year 2013 on its own is always the calendar year; a part taken out of it is
    read the way year_basis reads years, the same as "the last quarter of 2013" is."""
    assert _span(text, WranglerConfig(year_basis=Basis.CALENDAR)) == calendar
    assert _span(text, WranglerConfig(year_basis=Basis.FISCAL)) == fiscal


@pytest.mark.parametrize("year_basis", [Basis.CALENDAR, Basis.FISCAL])
def test_halves_and_quarters_of_a_numbered_year_agree(year_basis):
    """The disagreement this exists to prevent: "the second half of 2024" was forced to the
    calendar year while "the last quarter of 2024" followed the fiscal setting."""
    cfg = WranglerConfig(year_basis=year_basis)
    second_half = _span("the second half of 2024", cfg)
    last_quarter = _span("the last quarter of 2024", cfg)
    assert second_half[1] == last_quarter[1]  # both end where that year ends
    assert _span("the last 6 months of 2024", cfg) == second_half
    assert last_quarter == _span("the fourth quarter of 2024", cfg) == _span("Q4 of 2024", cfg)


@pytest.mark.parametrize("year_basis", [Basis.CALENDAR, Basis.FISCAL])
def test_a_year_contains_every_part_of_itself(year_basis):
    """The consequence of one rule: "2024" and every slice of it are on the same calendar,
    so the slices sit inside the year. When a bare year was forced to the calendar and its
    halves were not, "the second half of 2024" could fall outside "2024" altogether."""
    cfg = WranglerConfig(year_basis=year_basis)
    year = _span("2024", cfg)
    for part in ("the second half of 2024", "the last quarter of 2024", "Q4 2024",
                 "early 2024", "the first 3 months of 2024"):
        start, end = _span(part, cfg)
        assert year[0] <= start and end <= year[1], (part, year_basis)


def test_march_of_the_year_2013_keeps_its_month():
    """REGRESSION: "the" broke the month-year rule, the bare month was dropped by
    strictness, and the answer was the whole year at full confidence."""
    assert _span("March of the year 2013", WranglerConfig()) == (date(2013, 3, 1), date(2013, 4, 1))
    assert _span("April of the year 2013", WranglerConfig()) == (date(2013, 4, 1), date(2013, 5, 1))


def test_an_explicit_fiscal_year_is_still_fiscal():
    april = WranglerConfig()
    assert _span("FY2013", april) == (date(2012, 4, 1), date(2013, 4, 1))
    assert _span("fiscal year 2013", april) == (date(2012, 4, 1), date(2013, 4, 1))


def test_a_quarter_labelled_with_a_year_follows_of_year_basis_however_it_is_written():
    """The one genuinely ambiguous case keeps its setting -- and the three spellings agree."""
    for basis, start in ((Basis.FISCAL, date(2012, 4, 1)), (Basis.CALENDAR, date(2013, 1, 1))):
        cfg = WranglerConfig(of_year_basis=basis)
        for text in ("Q1 of 2013", "Q1 of year 2013", "Q1 of the year 2013"):
            assert _span(text, cfg)[0] == start, (text, basis)


# ---------------------------------------------------------------------------
# A basis said outright: "this fiscal year", "last calendar quarter"
# ---------------------------------------------------------------------------

FY27 = (date(2026, 4, 1), date(2027, 4, 1))


@pytest.mark.parametrize("bare", [Basis.FISCAL, Basis.CALENDAR])
@pytest.mark.parametrize(
    "text,expected",
    [
        ("sales this fiscal year", FY27),
        ("this financial year", FY27),
        ("current fiscal year", FY27),
        ("this FY", FY27),
        ("current FY", FY27),
        ("this calendar year", (date(2026, 1, 1), date(2027, 1, 1))),
        ("this CY", (date(2026, 1, 1), date(2027, 1, 1))),
        ("this fiscal quarter", (date(2026, 10, 1), date(2027, 1, 1))),
        ("last fiscal quarter", (date(2026, 7, 1), date(2026, 10, 1))),
        ("last 2 fiscal years", (date(2024, 4, 1), date(2026, 4, 1))),
        ("this fiscal year to date", (date(2026, 4, 1), date(2026, 10, 9))),
        ("end of this fiscal year", (date(2026, 12, 1), date(2027, 4, 1))),
    ],
)
def test_a_stated_basis_is_read_and_beats_the_default(text, expected, bare):
    """REGRESSION: the basis word was only read after "last"/"next", so "this fiscal year"
    -- the commonest of them -- matched nothing, and substitute left it unchanged."""
    assert _span(text, WranglerConfig(bare_period_basis=bare)) == expected


def test_this_year_with_no_word_still_follows_the_default():
    assert _span("this year", WranglerConfig(bare_period_basis=Basis.FISCAL)) == FY27
    assert _span("this year", WranglerConfig(bare_period_basis=Basis.CALENDAR)) == (
        date(2026, 1, 1), date(2027, 1, 1)
    )


@pytest.mark.parametrize(
    "text",
    ["this fiscal policy", "the current fiscal discipline", "next calendar invite",
     "this financial advisor", "last fiscal stimulus"],
)
def test_a_basis_word_before_something_that_is_not_a_period(text):
    from date_wrangler import parse

    assert parse(text, today=OCT8) == []


# ---------------------------------------------------------------------------
# year_basis: "this year" can be the calendar year while "Q1" stays fiscal
# ---------------------------------------------------------------------------

CAL_2026 = (date(2026, 1, 1), date(2027, 1, 1))
SPLIT = WranglerConfig(year_basis=Basis.CALENDAR)  # fiscal quarters, calendar years


@pytest.mark.parametrize(
    "text,expected",
    [
        ("this year", CAL_2026),
        ("last year", (date(2025, 1, 1), date(2026, 1, 1))),
        ("next 2 years", (date(2027, 1, 1), date(2029, 1, 1))),
        ("2 years ago", (date(2024, 1, 1), date(2025, 1, 1))),
        ("YTD", (date(2026, 1, 1), date(2026, 10, 9))),
        ("year to date", (date(2026, 1, 1), date(2026, 10, 9))),
        ("this year to date", (date(2026, 1, 1), date(2026, 10, 9))),
        ("last YTD", (date(2025, 1, 1), date(2025, 10, 9))),
    ],
)
def test_year_basis_decides_a_year_counted_from_today(text, expected):
    assert _span(text, SPLIT) == expected


@pytest.mark.parametrize(
    "text,calendar,fiscal",
    [
        ("Q1", (date(2026, 1, 1), date(2026, 4, 1)), (date(2026, 4, 1), date(2026, 7, 1))),
        ("H1", (date(2026, 1, 1), date(2026, 7, 1)), (date(2026, 4, 1), date(2026, 10, 1))),
        ("Q4 2024", (date(2024, 10, 1), date(2025, 1, 1)), (date(2024, 1, 1), date(2024, 4, 1))),
        ("Q3'24", (date(2024, 7, 1), date(2024, 10, 1)), (date(2023, 10, 1), date(2024, 1, 1))),
    ],
)
def test_quarters_and_halves_follow_year_basis_too(text, calendar, fiscal):
    """A quarter or half with nothing written on it -- year or no year -- is read the way
    year_basis reads everything else. There is one setting, not one per phrase."""
    assert _span(text, WranglerConfig(year_basis=Basis.CALENDAR)) == calendar
    assert _span(text, WranglerConfig(year_basis=Basis.FISCAL)) == fiscal


@pytest.mark.parametrize("text", ["last quarter", "this quarter", "QTD", "H1", "Q1 2024"])
def test_relative_quarters_carry_year_basis(text):
    """With an April year the quarter months coincide with calendar quarters, so the dates
    alone cannot show which calendar was used; the basis on the range can."""
    for year_basis in (Basis.CALENDAR, Basis.FISCAL):
        m = parse_one(text, today=OCT8, config=WranglerConfig(year_basis=year_basis))
        assert m is not None and m.range.basis is year_basis, (text, year_basis)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("FY2013", (date(2012, 4, 1), date(2013, 4, 1))),
        ("FY24 Q3", (date(2023, 10, 1), date(2024, 1, 1))),
        ("fiscal Q3", (date(2026, 10, 1), date(2027, 1, 1))),
        ("this fiscal year", FY27),
        ("CY2013", CAL_2013),
        ("CY Q3", (date(2026, 7, 1), date(2026, 10, 1))),
        ("this calendar year", CAL_2026),
    ],
)
@pytest.mark.parametrize("year_basis", [Basis.CALENDAR, Basis.FISCAL])
def test_year_basis_never_overrides_a_basis_the_phrase_states(text, expected, year_basis):
    """The only exemption from year_basis: a phrase that says which kind of year it means."""
    assert _span(text, WranglerConfig(year_basis=year_basis)) == expected


def test_year_basis_defaults_to_following_bare_period_basis():
    """None inherits, so nothing changes for anyone who has not asked for the split."""
    for bare in (Basis.FISCAL, Basis.CALENDAR):
        assert _span("this year", WranglerConfig(bare_period_basis=bare)) == _span(
            "this year", WranglerConfig(bare_period_basis=bare, year_basis=bare)
        )


@pytest.mark.parametrize("raw", ["calendar", "Calendar", " CALENDAR "])
def test_year_basis_accepts_the_string_a_form_sends(raw):
    assert WranglerConfig(year_basis=raw).year_basis is Basis.CALENDAR  # type: ignore[arg-type]


def test_an_unknown_basis_fails_on_construction_by_name():
    with pytest.raises(ValueError, match="year_basis must be 'calendar' or 'fiscal'"):
        WranglerConfig(year_basis="gregorian")  # type: ignore[arg-type]
