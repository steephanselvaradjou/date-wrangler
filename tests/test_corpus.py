"""The author's original acceptance corpus.

These 77 phrases were the hand-written test list for the module this library replaces.
They are the closest thing to real user input the project has, so they are kept as a
regression suite rather than left in git history.

Three defects surfaced here that the generated and property suites had both missed,
because none of them invents idiomatic business phrasing:

* "6 month before" silently returned the *sixth month of the fiscal year*. The direction
  words lived in two places -- the scanning pattern and the parse function -- and "before"
  was in neither, so the phrase fell through to the fiscal-month rule.
* "5 years after" did not parse at all, for the same reason.
* "Q4 2024 to Q1" and "from H2 to H1 2025" refused to join. Unifying the year across a
  range set it *before* the wrap-around retry ran, and the retry declined to act on a spec
  that already had a year.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import parse

TODAY = date(2025, 9, 4)

#: Phrases that must yield at least one date.
RECOGNISED = [
    'sales for Q1 2024',
    'performance in 2nd qtr of 2024',
    'a report on qtr3 24',
    "show me data for 4 quarter '23",
    'first half 2024 results',
    'a summary of H2 2025',
    'from H2 2024 to H1 2024',
    'revenue for Jan 2024',
    "expenses in september of '23",
    'the entire year 2024',
    'a report for year 25',
    'What was the total for CY2024?',
    'Please summarize FY2024.',
    'How did we do in fiscal year 25?',
    'summarize financial year 2025',
    '1st quarter of FY24',
    '4th qtr FY25',
    'H1 FY2024',
    'H2 FY2024',
    'sales from 1q 2024 to 3q 2024',
    'Jan 24 - mar 24',
    'December 2023 to February 2024',
    'from H2 2023 to H1 2024',
    'Q4 CY23 to Q1 FY25',
    'Nov to Jan',
    'Q4 2024 to Q1',
    'from H2 to H1 2025',
    'sales for the last 4 quarters',
    'show me the past 6 months',
    'a summary of the previous 2 years',
    'the next 3 quarters',
    'performance in the following year',
    'last quarter',
    'previous year',
    'next month',
    '5 years after',
    '6 month before',
    '2 qtr ago',
    'last 5 cy',
    'past 2 fy',
    'next 3 FY',
    'last FY',
    'next CY',
    'this month',
    'this year',
    'this quarter',
    'contribution for third q of 2024',
    'contribution for the third quarter of 2024',
    'contribution for second month of 2024',
    'last qtr',
    'sales for the current year',
    'a report for current year 2025',
    'q1   Fy24   TO   h1   cY25',
    "Let's compare last 3 months with performance in Q1 FY24.",
    'H3 CY23 is not a valid period.',
    'sales in calendar year 2024',
    'Fy 2024-25',
    'Fy 2024-2025',
    'FY24/25',
    'Fy 24-25',
    'compare q1 sales of fy 24 with q4 sales of fy 24',
    'compare sales of q2 fy 24 with q4 sales of fy 24',
    'date from 10.05.2024 to 15.06.2024',
    'data between 2024-05-10 and 2024-06-15',
    'Sales of YTD',
    'performance in the last YTD period',
    'YTD september 2024',
    'jan-mar 2024',
    'jan 24-mar 2025',
    'jan 24',
    'jan to mar',
    'feb to apr 2025',
    'period from March 2025 to March 2025',
]

#: Phrases that contain numbers but no date, and must yield nothing.
NOT_A_DATE = [
    'What about the 5th quarter of 2024?',
    'Order number 2024 is pending',
    'A 2-day trip to see the 2024 eclipse',
    'Sales increased by 25 percent',
]


@pytest.mark.parametrize("phrase", RECOGNISED)
def test_every_corpus_phrase_is_recognised(phrase):
    assert parse(phrase, today=TODAY), f"{phrase!r} produced no match"


@pytest.mark.parametrize("phrase", NOT_A_DATE)
def test_numbers_that_are_not_dates_are_left_alone(phrase):
    assert parse(phrase, today=TODAY) == []


@pytest.mark.parametrize(
    "phrase,start,end",
    [
        # Relative counts: "before"/"after" must not be read as an ordinal month index.
        ("6 month before", date(2025, 3, 1), date(2025, 4, 1)),
        ("5 years after", date(2030, 4, 1), date(2031, 4, 1)),
        ("2 qtr ago", date(2025, 1, 1), date(2025, 4, 1)),
        # Ranges where only one endpoint carries a year.
        ("Q4 2024 to Q1", date(2024, 1, 1), date(2024, 7, 1)),
        ("from H2 to H1 2025", date(2023, 10, 1), date(2024, 10, 1)),
        ("Q1 to Q2 2024", date(2023, 4, 1), date(2023, 10, 1)),
        ("Nov to Jan", date(2025, 11, 1), date(2026, 2, 1)),
        # Fiscal-year spellings the author actually uses.
        ("Fy 2024-25", date(2024, 4, 1), date(2025, 4, 1)),
        ("FY24/25", date(2024, 4, 1), date(2025, 4, 1)),
        ("sales in calendar year 2024", date(2024, 1, 1), date(2025, 1, 1)),
        # Numeric and ISO ranges.
        ("date from 10.05.2024 to 15.06.2024", date(2024, 5, 10), date(2024, 6, 16)),
        ("data between 2024-05-10 and 2024-06-15", date(2024, 5, 10), date(2024, 6, 16)),
        # Year-to-date.
        ("performance in the last YTD period", date(2024, 4, 1), date(2024, 9, 5)),
        ("YTD september 2024", date(2024, 4, 1), date(2024, 10, 1)),
    ],
)
def test_corpus_phrases_resolve_exactly(phrase, start, end):
    found = parse(phrase, today=TODAY)
    assert found, f"{phrase!r} produced no match"
    assert (found[0].range.start, found[0].range.end) == (start, end)


def test_an_invalid_period_is_ignored_without_losing_the_valid_one():
    """"H3 CY23" -- there is no third half, but the year is still readable."""
    found = parse("H3 CY23 is not a valid period.", today=TODAY)
    assert len(found) == 1
    assert found[0].range.start == date(2023, 1, 1)
