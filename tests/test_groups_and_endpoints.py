"""Which dates were named together, and what a span was made from.

A question that compares two periods and one that merely mentions two dates look alike as
a flat list of matches. "Q1 and Q3", "2019 versus 2020" are named together; the two dates in
"diagnosed on 1994/2/19 ... concentration on 1993/11/12" are not. A caller checking that
an answer covers every period asked about needs to tell them apart, so each match says
which group it is in and how it is joined to the one before it.

A span is one range, but a change question -- "the decrease from 2012 to 2013" -- is about
its two ends, so a span also says which two periods it was made from.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import DateMatch, DateRange, Grain, parse

TODAY = date(2026, 10, 16)


def groups(text):
    return [(m.text, m.group, m.joined_by) for m in parse(text, today=TODAY)]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Q1 and Q3", [("Q1", 0, None), ("Q3", 0, "list")]),
        ("Q1, Q2 and Q3", [("Q1", 0, None), ("Q2", 0, "list"), ("Q3", 0, "list")]),
        ("2019 versus 2020", [("2019", 0, None), ("2020", 0, "compare")]),
        ("Q1 FY25 vs Q1 FY26", [("Q1 FY25", 0, None), ("Q1 FY26", 0, "compare")]),
        ("compare Q1 2024 to Q1 2025", [("Q1 2024", 0, None), ("Q1 2025", 0, "compare")]),
        # REGRESSION: "with" after "compare" linked nothing, so the two quarters were
        # unrelated, and two bare years, with no other cue, were not read at all.
        (
            "compare claims paid in Q1 FY25 with Q1 FY26",
            [("Q1 FY25", 0, None), ("Q1 FY26", 0, "compare")],
        ),
        ("comparing 2023 with 2024", [("2023", 0, None), ("2024", 0, "compare")]),
        ("compare 2023 to 2024", [("2023", 0, None), ("2024", 0, "compare")]),
        (
            "revenue in Q1 and Q3 2024 vs 2023",
            [("Q1", 0, None), ("Q3 2024", 0, "list"), ("2023", 0, "compare")],
        ),
    ],
)
def test_dates_named_together_share_a_group(text, expected):
    assert groups(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "patients diagnosed on 1994/2/19 whose concentration on 1993/11/12 was high",
        "crimes committed in 1995 by people with accounts opened in 1997",
        "sales in March. Returns in June.",
        # "with" compares only after "compare": here it is an ordinary preposition.
        "sales in Q1 with returns in Q2",
    ],
)
def test_unrelated_dates_are_each_in_a_group_of_their_own(text):
    """REGRESSION (for callers): nothing told these apart from "1995 vs 1997", so a check
    that every compared period is answered insisted on both."""
    found = groups(text)
    assert len(found) == 2
    assert [g for _, g, _ in found] == [0, 1]
    assert all(j is None for *_, j in found)


def test_a_span_is_one_match_in_its_group():
    found = parse("Jan to Mar and Jul to Sep 2024", today=TODAY)
    assert [(m.text, m.group, m.joined_by) for m in found] == [
        ("Jan to Mar", 0, None),
        ("Jul to Sep 2024", 0, "list"),
    ]


def ends(text):
    (m,) = parse(text, today=TODAY)
    assert m.endpoints is not None, text
    return [(e.start, e.end, e.grain) for e in m.endpoints]


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "decrease rate from 2012 to 2013",
            [(date(2012, 1, 1), date(2013, 1, 1), Grain.YEAR),
             (date(2013, 1, 1), date(2014, 1, 1), Grain.YEAR)],
        ),
        (
            "From November to December 1981",
            [(date(1981, 11, 1), date(1981, 12, 1), Grain.MONTH),
             (date(1981, 12, 1), date(1982, 1, 1), Grain.MONTH)],
        ),
        (
            "between Q1 and Q3 2024",
            [(date(2024, 1, 1), date(2024, 4, 1), Grain.QUARTER),
             (date(2024, 7, 1), date(2024, 10, 1), Grain.QUARTER)],
        ),
        (
            "Nov 2023 to Feb 2024",
            [(date(2023, 11, 1), date(2023, 12, 1), Grain.MONTH),
             (date(2024, 2, 1), date(2024, 3, 1), Grain.MONTH)],
        ),
        (
            "1-15 March 2024",
            [(date(2024, 3, 1), date(2024, 3, 2), Grain.DAY),
             (date(2024, 3, 15), date(2024, 3, 16), Grain.DAY)],
        ),
        (
            "between 1 and 15 March 2024",
            [(date(2024, 3, 1), date(2024, 3, 2), Grain.DAY),
             (date(2024, 3, 15), date(2024, 3, 16), Grain.DAY)],
        ),
    ],
)
def test_a_span_says_which_two_periods_it_runs_between(text, expected):
    """REGRESSION (for callers): "from 2012 to 2013" came back as 2012-01-01 to 2013-12-31
    and nothing else, so a change question could not find its two years."""
    assert ends(text) == expected


def test_the_span_runs_from_the_start_of_one_end_to_the_end_of_the_other():
    (m,) = parse("from March to June 2024", today=TODAY)
    first, last = m.endpoints
    assert (m.range.start, m.range.end) == (first.start, last.end)


@pytest.mark.parametrize("text", ["Q1 2024", "since 2012", "the year to March 2024", "YTD"])
def test_anything_that_is_not_a_span_has_no_endpoints(text):
    assert all(m.endpoints is None for m in parse(text, today=TODAY))


def test_groups_and_endpoints_survive_a_round_trip_through_a_dict():
    for m in parse("from 2012 to 2013 vs 2015", today=TODAY):
        assert DateMatch.from_dict(m.to_dict()) == m


def test_a_dict_from_before_groups_still_loads():
    old = {
        "text": "Q1",
        "span": [0, 2],
        "confidence": 1.0,
        "range": DateRange(date(2024, 1, 1), date(2024, 4, 1), Grain.QUARTER).to_dict(),
    }
    m = DateMatch.from_dict(old)
    assert (m.group, m.joined_by, m.endpoints) == (0, None, None)
