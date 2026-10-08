"""Runs of days, periods named by their end, and the Nth unit of a period.

Each phrase here came back confidently as one piece of itself -- the 15th instead of the
1st to the 15th, June instead of six months, all of April instead of its first week.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import parse, parse_one

TODAY = date(2026, 10, 8)  # FY27 Q3 on an April start


def bounds(text):
    m = parse_one(text, today=TODAY)
    assert m is not None, text
    assert m.confidence == 1.0, (text, m.confidence)
    return m.range.start, m.range.end


# ---------------------------------------------------------------------------
# Runs of days inside a month
# ---------------------------------------------------------------------------

MAR_1_15 = (date(2026, 3, 1), date(2026, 3, 16))


@pytest.mark.parametrize(
    "text", ["1-15 March", "1st to 15th March", "March 1-15", "1st-15th of March", "1 to 15 March"],
)
def test_a_run_of_days_is_read_whole(text):
    """REGRESSION: "1-15 March" was the 15th alone and "March 1-15" the 1st."""
    assert bounds(text) == MAR_1_15


def test_a_run_of_days_with_a_year():
    assert bounds("March 1-15, 2024") == (date(2024, 3, 1), date(2024, 3, 16))
    assert bounds("1 to 15 March 2024") == (date(2024, 3, 1), date(2024, 3, 16))


def test_a_run_across_two_months_is_still_a_range():
    assert bounds("March 1 to 15 April") == (date(2026, 3, 1), date(2026, 4, 16))


def test_impossible_runs_are_refused():
    assert parse("1-31 February", today=TODAY) == []
    assert parse("15-1 March", today=TODAY) == []


def test_a_score_is_not_a_run_of_days():
    found = parse("scored 3-1 in March", today=TODAY)
    assert [(m.range.start, m.range.end) for m in found] == [(date(2026, 3, 1), date(2026, 4, 1))]


# ---------------------------------------------------------------------------
# Periods named by where they end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("the year to March 2024", date(2023, 4, 1), date(2024, 4, 1)),
        ("six months to June 2024", date(2024, 1, 1), date(2024, 7, 1)),
        ("the six months to 30 June 2024", date(2024, 1, 1), date(2024, 7, 1)),
        ("the 12 months to March 2024", date(2023, 4, 1), date(2024, 4, 1)),
        ("12 months ending March 2024", date(2023, 4, 1), date(2024, 4, 1)),
        ("nine months ended 30 September 2024", date(2024, 1, 1), date(2024, 10, 1)),
        ("the half year to 30 September 2024", date(2024, 4, 1), date(2024, 10, 1)),
        ("half-year ended 30 Sep 2024", date(2024, 4, 1), date(2024, 10, 1)),
        ("7 days ending 15 March", date(2026, 3, 9), date(2026, 3, 16)),
        ("quarter ending June 2024", date(2024, 4, 1), date(2024, 7, 1)),
        ("year ended March 2024", date(2023, 4, 1), date(2024, 4, 1)),
    ],
)
def test_a_period_named_by_its_end(text, start, end):
    """REGRESSION: "to" was not read and the count was dropped, so "six months to June
    2024" was June alone and "12 months ending March 2024" was one month."""
    assert bounds(text) == (start, end)


def test_with_no_year_the_end_is_the_last_one_that_has_happened():
    assert bounds("three months to 31 December") == (date(2025, 10, 1), date(2026, 1, 1))


@pytest.mark.parametrize("text", ["3 days to March", "2 weeks to June", "six months to go"])
def test_a_countdown_is_not_a_period(text):
    assert parse(text, today=TODAY) == []


# ---------------------------------------------------------------------------
# The Nth unit of a period
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("the first week of April", date(2026, 4, 1), date(2026, 4, 8)),
        ("first week of next month", date(2026, 11, 1), date(2026, 11, 8)),
        ("the last week of March", date(2026, 3, 25), date(2026, 4, 1)),
        ("the last 10 days of March", date(2026, 3, 22), date(2026, 4, 1)),
        ("the first 2 weeks of next month", date(2026, 11, 1), date(2026, 11, 15)),
        ("the first 3 months of 2024", date(2024, 1, 1), date(2024, 4, 1)),
        ("the last 6 months of the year", date(2026, 10, 1), date(2027, 4, 1)),
        ("last 3 months of the year", date(2027, 1, 1), date(2027, 4, 1)),
        ("the last month of the quarter", date(2026, 12, 1), date(2027, 1, 1)),
        ("the third month of the quarter", date(2026, 12, 1), date(2027, 1, 1)),
        ("the first month of FY27", date(2026, 4, 1), date(2026, 5, 1)),
    ],
)
def test_the_nth_unit_of_a_period(text, start, end):
    """REGRESSION: "the first week of April" was all of April, and "the third month of the
    quarter" the third month of the fiscal year."""
    assert bounds(text) == (start, end)


def test_a_last_week_is_counted_back_from_the_end_not_aligned_to_a_weekday():
    """Its final seven days, whatever day the week is set to begin on."""
    assert bounds("the last week of March") == (date(2026, 3, 25), date(2026, 4, 1))


def test_a_fifth_week_is_cut_at_the_end_of_the_month_or_refused():
    assert bounds("the fifth week of March") == (date(2026, 3, 29), date(2026, 4, 1))
    assert parse("the fifth week of February", today=TODAY) == []


@pytest.mark.parametrize(
    "text", ["the last quarter of 2024", "the fourth quarter of 2024", "Q4 of 2024"],
)
def test_the_last_quarter_of_a_year_is_its_fourth_quarter(text):
    """REGRESSION: "the last quarter of 2024" was "last quarter" -- counted back from today
    -- plus a stray 2024. It now means what "the fourth quarter of 2024" does, and all
    three spellings follow of_year_basis together."""
    assert bounds(text) == (date(2024, 1, 1), date(2024, 4, 1))


@pytest.mark.parametrize("text,start,end", [
    ("the first quarter of 2024", date(2023, 4, 1), date(2023, 7, 1)),
    ("the third quarter", date(2026, 10, 1), date(2027, 1, 1)),
    ("the last quarter of the year", date(2027, 1, 1), date(2027, 4, 1)),
    ("the final quarter of FY27", date(2027, 1, 1), date(2027, 4, 1)),
])
def test_ordinal_quarters_still_belong_to_the_quarter_rule(text, start, end):
    """A first version of the new rule claimed every "Nth quarter of" and resolved only
    "last", so "the first quarter of 2024" matched and returned nothing."""
    assert bounds(text) == (start, end)


def test_the_last_half_of_a_year_is_its_second_half():
    assert bounds("the last half of 2024") == bounds("the second half of 2024")


@pytest.mark.parametrize(
    "text",
    ["the first week of training", "the second week in a row", "the first 3 months",
     "12 months of hard work", "the final week"],
)
def test_ordinals_before_something_that_is_not_a_period(text):
    assert parse(text, today=TODAY) == []
