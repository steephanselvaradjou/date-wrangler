"""Boundaries that keep their day, and quarter labels that keep their year and basis.

Every case here once returned a confident answer that was wrong in a way no reader of the
output could spot: a boundary day lost, a meaning reversed, a year or a stated basis
silently replaced by a default. They came out of a sweep for exactly that shape of bug.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import Basis, FiscalCalendar, WranglerConfig, YearLabel, parse, parse_one

TODAY = date(2026, 10, 8)  # Thursday; FY27 Q3 on an April start
FISCAL = WranglerConfig(bare_period_basis=Basis.FISCAL)
CALENDAR = WranglerConfig()


def bounds(text, cfg=FISCAL):
    m = parse_one(text, today=TODAY, config=cfg)
    assert m is not None, text
    return m.range.start, m.range.end


# ---------------------------------------------------------------------------
# Inclusive and negated bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("on or after 1 April", date(2026, 4, 1), None),
        ("not before 1 April", date(2026, 4, 1), None),
        ("no earlier than 1 April", date(2026, 4, 1), None),
        ("on or before 31 March", None, date(2026, 4, 1)),
        ("not after 31 March", None, date(2026, 4, 1)),
        ("up to and including 31 March", None, date(2026, 4, 1)),
    ],
)
def test_inclusive_and_negated_bounds_keep_the_boundary_day(text, start, end):
    """REGRESSION: the plain prefix claimed the last word. "on or after 1 April" began on
    the 2nd, "on or before 31 March" ended on the 30th, and "not before 1 April" -- read
    as "before 1 April" -- meant the opposite of what it said."""
    assert bounds(text) == (start, end)


def test_plain_before_and_after_are_still_strict():
    assert bounds("after 1 April") == (date(2026, 4, 2), None)
    assert bounds("before 31 March") == (None, date(2026, 3, 31))


@pytest.mark.parametrize(
    "text,end",
    [
        ("by 31 March", date(2026, 4, 1)),
        ("through 31 March", date(2026, 4, 1)),
        ("by March", date(2026, 4, 1)),
        ("before the end of March", date(2026, 4, 1)),
        ("by the end of the quarter", date(2027, 1, 1)),
        ("by end of year", date(2027, 4, 1)),
    ],
)
def test_a_deadline_runs_up_to_and_including_its_day(text, end):
    """"by 31 March" was that one day. "before the end of March" read "end of" as the
    last third of March and stopped on the 20th."""
    assert bounds(text) == (None, end)


def test_no_later_than_a_weekday():
    m = parse_one("no later than Friday", today=TODAY)
    assert m is not None
    assert (m.range.start, m.range.end_inclusive) == (None, date(2026, 10, 9))


def test_late_still_means_the_last_third():
    """Only "end of" became a deadline. "late March" is still Mar 21-31."""
    assert bounds("before late March") == (None, date(2026, 3, 21))


@pytest.mark.parametrize("text,start", [
    ("from 1 April", date(2026, 4, 1)),
    ("from today", TODAY),
])
def test_from_a_single_day_starts_an_open_range(text, start):
    assert bounds(text) == (start, None)


@pytest.mark.parametrize("text,start,end", [
    ("the figures from Q1", date(2026, 4, 1), date(2026, 7, 1)),
    ("revenue from March", date(2026, 3, 1), date(2026, 4, 1)),
])
def test_from_a_period_names_a_source_not_a_start(text, start, end):
    """"the figures from Q1" are Q1's figures. Only a single day after "from" opens a range."""
    assert bounds(text) == (start, end)


def test_ranges_are_untouched_by_the_new_prefixes():
    assert bounds("from 1 April to 30 June") == (date(2026, 4, 1), date(2026, 7, 1))
    assert bounds("Jan through Mar") == (date(2026, 1, 1), date(2026, 4, 1))


# ---------------------------------------------------------------------------
# Quarter and half labels
# ---------------------------------------------------------------------------

Q3_FY24 = (date(2023, 10, 1), date(2024, 1, 1))   # fiscal default, label 2024
Q3_CY24 = (date(2024, 7, 1), date(2024, 10, 1))   # calendar default


@pytest.mark.parametrize(
    "text", ["Q3 2024", "Q3'24", "Q3-24", "Q3-2024", "2024-Q3", "2024Q3", "2024 Q3",
             "3Q24", "3Q 2024", "3Q'24"],
)
def test_every_spelling_of_q3_2024_agrees(text):
    """REGRESSION: an attached year was dropped -- "Q3'24" meant Q3 of *this* year -- and
    a dash read as a range, so "2024-Q3" ran from January 2024 to the end of Q3. Each
    spelling now means what "Q3 2024" does, under either default."""
    assert bounds(text, FISCAL) == Q3_FY24
    assert bounds(text, CALENDAR) == Q3_CY24


@pytest.mark.parametrize("text", ["FY24 Q3", "FY24-Q3"])
def test_a_fiscal_year_before_the_quarter_is_fiscal_whatever_the_default(text):
    assert bounds(text, CALENDAR) == Q3_FY24


@pytest.mark.parametrize("text", ["fiscal Q3", "FY Q3", "Q3 fiscal", "Q3 FY"])
def test_a_stated_fiscal_basis_beats_a_calendar_default(text):
    """REGRESSION: the word was dropped, so under a calendar default "fiscal Q3" became
    the calendar quarter -- the one thing the writer had said it was not."""
    assert bounds(text, CALENDAR) == (date(2026, 10, 1), date(2027, 1, 1))


@pytest.mark.parametrize("text", ["calendar Q3", "CY Q3"])
def test_a_stated_calendar_basis_beats_a_fiscal_default(text):
    assert bounds(text, FISCAL) == (date(2026, 7, 1), date(2026, 10, 1))


@pytest.mark.parametrize("text,expected", [
    ("H1'25", (date(2024, 4, 1), date(2024, 10, 1))),
    ("1H24", (date(2023, 4, 1), date(2023, 10, 1))),
    ("2H2024", (date(2023, 10, 1), date(2024, 4, 1))),
    ("fiscal H1", (date(2026, 4, 1), date(2026, 10, 1))),
])
def test_half_spellings(text, expected):
    assert bounds(text) == expected


@pytest.mark.parametrize(
    "text",
    ["a 1h 30m meeting", "a 2h drive", "took 1h24 minutes", "2h 15m", "the 4q engine",
     "gear 1h20", "version 2024-q3x", "order 3q24x"],
)
def test_hours_and_part_numbers_are_not_halves_or_quarters(text):
    """REGRESSION: "a 2h drive" was H2. A digit before the letter is the finance shorthand
    only when the letter is a capital or a year follows it."""
    assert parse(text, today=TODAY) == []


def test_lower_case_shorthand_with_a_year_still_reads():
    assert parse("sales from 1q 2024 to 3q 2024", today=TODAY) != []
    assert bounds("1h 2024") == (date(2023, 4, 1), date(2023, 10, 1))


# ---------------------------------------------------------------------------
# A month labelled with a fiscal year
# ---------------------------------------------------------------------------

JUNE_2024 = (date(2024, 6, 1), date(2024, 7, 1))


@pytest.mark.parametrize(
    "text",
    ["Jun FY25", "June FY25", "June of FY25", "June FY2025", "Jun-FY25", "FY25 June",
     "FY25 Jun", "June FY 2024-25", "FY2024-25 June"],
)
def test_a_month_of_a_fiscal_year_is_the_one_inside_it(text):
    """REGRESSION: FY25 beside a month was read as the calendar year 2025, so with an April
    year "Jun FY25" was June 2025 -- outside FY25 altogether. "Jun-FY25" was a span from
    June to the end of FY25, and "FY25 June" the whole fiscal year with the month dropped."""
    for cfg in (FISCAL, CALENDAR):
        assert bounds(text, cfg) == JUNE_2024


@pytest.mark.parametrize(
    "fiscal,text,start",
    [
        (FiscalCalendar(4, YearLabel.START_YEAR), "Jun FY25", date(2025, 6, 1)),
        (FiscalCalendar(4, YearLabel.START_YEAR), "Mar FY25", date(2026, 3, 1)),
        (FiscalCalendar.australia(), "Jul FY25", date(2024, 7, 1)),
        (FiscalCalendar.australia(), "Jun FY25", date(2025, 6, 1)),
        (FiscalCalendar.us_federal(), "Oct FY25", date(2024, 10, 1)),
        (FiscalCalendar.calendar(), "Jun FY25", date(2025, 6, 1)),
    ],
)
def test_the_month_is_found_on_the_configured_fiscal_calendar(fiscal, text, start):
    assert bounds(text, WranglerConfig(fiscal=fiscal))[0] == start


def test_each_month_of_a_fiscal_span_lands_in_its_own_calendar_year():
    """The label is kept until resolution, so October and March of FY25 are a year apart."""
    assert bounds("Oct to Mar FY25") == (date(2024, 10, 1), date(2025, 4, 1))
    assert bounds("Apr-Jun FY25") == (date(2024, 4, 1), date(2024, 7, 1))
    found = parse("Mar, Apr and May FY25", today=TODAY)
    assert [m.range.start for m in found] == [date(2025, 3, 1), date(2024, 4, 1), date(2024, 5, 1)]


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("15 June FY25", date(2024, 6, 15), date(2024, 6, 16)),
        ("June 15 FY25", date(2024, 6, 15), date(2024, 6, 16)),
        ("the first week of June FY25", date(2024, 6, 1), date(2024, 6, 8)),
        ("the first half of June FY25", date(2024, 6, 1), date(2024, 6, 16)),
        ("end of June FY25", date(2024, 6, 21), date(2024, 7, 1)),
    ],
)
def test_days_and_parts_of_a_fiscal_month_follow_it(text, start, end):
    assert bounds(text, CALENDAR) == (start, end)


@pytest.mark.parametrize("text", ["June 2025", "June of 2025", "Jun-25", "June CY25"])
def test_a_month_with_a_plain_year_is_that_calendar_month_whatever_year_basis_says(text):
    """Nobody means June 2024 by "June 2025". Only FY makes the year beside a month a label."""
    for cfg in (FISCAL, CALENDAR, WranglerConfig(year_basis=Basis.FISCAL)):
        assert bounds(text, cfg) == (date(2025, 6, 1), date(2025, 7, 1))


def test_may_after_a_fiscal_year_is_a_verb():
    assert [m.text for m in parse("FY25 may see growth", today=TODAY)] == ["FY25"]


@pytest.mark.parametrize("fiscal", [FiscalCalendar(4), FiscalCalendar(4, YearLabel.START_YEAR)])
@pytest.mark.parametrize("text", ["FY2024-25", "FY24/25", "FY 2024-2025"])
def test_a_fiscal_year_written_with_both_years_starts_in_the_first(fiscal, text):
    """REGRESSION: the second year was always taken as the label, so where years are named
    by their start, "FY2024-25" came back as the year from April 2025."""
    assert bounds(text, WranglerConfig(fiscal=fiscal)) == (date(2024, 4, 1), date(2025, 4, 1))


def test_a_spaced_dash_before_a_fiscal_year_is_still_a_range():
    """"Jun-FY25" is a label; "June - FY25" is how a range is written."""
    assert bounds("June - FY25") == (date(2024, 6, 1), date(2025, 4, 1))
    assert bounds("FY24 - 15 March 2024") == (date(2023, 4, 1), date(2024, 3, 16))


def test_a_month_with_its_own_year_keeps_it_when_joined_to_a_fiscal_year():
    """Joining shares one basis between the ends, but "March 2024" is a calendar fact: it
    must not become the March of fiscal 2024, which is March 2025 where years are named by
    their start."""
    cfg = WranglerConfig(fiscal=FiscalCalendar(4, YearLabel.START_YEAR))
    assert bounds("March 2024 to FY24", cfg) == (date(2024, 3, 1), date(2025, 4, 1))
    assert bounds("15 March 2024 to FY24", cfg) == (date(2024, 3, 15), date(2025, 4, 1))


@pytest.mark.parametrize("text", ["FY24 - 2023", "FY2023-2025", "FY24/26"])
def test_two_years_that_are_not_one_fiscal_year_are_not_read_as_one(text):
    """REGRESSION: any two numbers made a fiscal year -- "FY24 - 2023" was FY2023."""
    from date_wrangler import diagnose

    matches, diags = diagnose(text, today=TODAY)
    assert matches == []
    assert diags
