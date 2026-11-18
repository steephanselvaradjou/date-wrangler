"""Ordinals listed or run before one unit: "the 2nd and 3rd weeks of January".

REGRESSION: only the last ordinal was read -- "3rd weeks of January" -- confidently, with
nothing to say the 2nd had been dropped; "the 2nd and 3rd Mondays of March" was all of
March. Each ordinal is now read exactly as its own phrase would be, and joins its
neighbours as any two dates do: a list after "and", "&" or a comma, a span after "to" or
inside "between".
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from date_wrangler import Basis, WranglerConfig, parse, parse_one

TODAY = date(2026, 11, 17)


def readings(text, cfg=None):
    return [
        (m.text, m.range.start, m.range.end_inclusive, m.joined_by)
        for m in parse(text, today=TODAY, config=cfg or WranglerConfig())
    ]


def alone(phrase, cfg=None):
    m = parse_one(phrase, today=TODAY, config=cfg or WranglerConfig())
    assert m is not None and m.text == phrase, phrase
    return m.range.start, m.range.end_inclusive


#: Each list, and the singular phrase every ordinal in it must read as.
LISTS = [
    ("the 2nd and 3rd weeks of January", ["the 2nd week of January", "the 3rd week of January"]),
    ("the 2nd and 3rd week of January", ["the 2nd week of January", "the 3rd week of January"]),
    ("2nd and 3rd weeks of January 2024",
     ["the 2nd week of January 2024", "the 3rd week of January 2024"]),
    ("the 2nd, 3rd and 4th weeks of January",
     ["the 2nd week of January", "the 3rd week of January", "the 4th week of January"]),
    ("the 2nd & 3rd weeks of January", ["the 2nd week of January", "the 3rd week of January"]),
    ("the first and last weeks of March", ["the first week of March", "the last week of March"]),
    ("the 2nd and the 3rd week of January",
     ["the 2nd week of January", "the 3rd week of January"]),
    ("the 2nd week and the 3rd week of January",
     ["the 2nd week of January", "the 3rd week of January"]),
    ("the first and second quarters of 2024",
     ["the first quarter of 2024", "the second quarter of 2024"]),
    ("the first, second and third quarters of FY25",
     ["the first quarter of FY25", "the second quarter of FY25", "the third quarter of FY25"]),
    ("the first and second halves of 2024", ["the first half of 2024", "the second half of 2024"]),
    ("the 1st and 3rd months of FY25", ["the 1st month of FY25", "the 3rd month of FY25"]),
    ("the 2nd and 3rd Mondays of March", ["the 2nd Monday of March", "the 3rd Monday of March"]),
    ("the 22nd and 23rd days of March", ["the 22nd day of March", "the 23rd day of March"]),
    ("the 1st and 2nd weeks of next month",
     ["the 1st week of next month", "the 2nd week of next month"]),
    ("the 2nd week and the 3rd month of 2024", ["the 2nd week of 2024", "the 3rd month of 2024"]),
    ("the first half and the second quarter of 2024",
     ["the first half of 2024", "the second quarter of 2024"]),
]


@pytest.mark.parametrize("cfg", [WranglerConfig(), WranglerConfig(year_basis=Basis.FISCAL)],
                         ids=["calendar", "fiscal"])
@pytest.mark.parametrize("text,each", LISTS)
def test_each_ordinal_is_read_as_its_own_phrase(text, each, cfg):
    found = readings(text, cfg)
    assert [(s, e) for _, s, e, _ in found] == [alone(p, cfg) for p in each], found
    assert [j for *_, j in found] == [None] + ["list"] * (len(each) - 1)


def test_the_first_ordinal_is_read_where_it_is_written():
    assert [t for t, *_ in readings("sales in the 2nd and 3rd weeks of January")] == [
        "the 2nd", "3rd weeks of January"
    ]
    assert [t for t, *_ in readings("the 2nd week and the 3rd week of January")] == [
        "the 2nd week", "the 3rd week of January"
    ]


@pytest.mark.parametrize(
    "text,first,last",
    [
        ("the 2nd to 4th weeks of January", date(2026, 1, 8), date(2026, 1, 28)),
        ("between the 2nd and 3rd weeks of January", date(2026, 1, 8), date(2026, 1, 21)),
        ("the first through third quarters of 2024", date(2024, 1, 1), date(2024, 9, 30)),
    ],
)
def test_a_run_of_ordinals_is_one_span(text, first, last):
    (m,) = parse(text, today=TODAY)
    assert (m.range.start, m.range.end_inclusive) == (first, last)
    assert m.text == text


def test_a_list_keeps_the_year_and_basis_of_its_last_ordinal():
    found = parse("the first and second quarters of FY25", today=TODAY)
    assert [m.range.basis for m in found] == [Basis.FISCAL, Basis.FISCAL]
    assert all(m.confidence == 1.0 for m in found)  # the year is written


@pytest.mark.parametrize(
    "text,expected",
    [
        ("won the first and second weeks of training", []),
        ("the 1st and 15th of March", [("the 1st", date(2026, 3, 1)), ("15th of March",
                                                                         date(2026, 3, 15))]),
        ("1st and 15th March", [("1st", date(2026, 3, 1)), ("15th March", date(2026, 3, 15))]),
        ("the 3rd week of January", [("the 3rd week of January", date(2026, 1, 15))]),
    ],
)
def test_what_is_not_such_a_list_is_unchanged(text, expected):
    assert [(t, s) for t, s, _, _ in readings(text)] == expected


def test_nothing_is_dropped_without_a_word():
    """Every ordinal before the unit is read, or the phrase says why: none goes missing
    silently, as the 2nd of "the 2nd and 3rd weeks" did."""
    for text, each in LISTS:
        found = parse(text, today=TODAY)
        ordinals = re.findall(r"\b(?:\d+(?:st|nd|rd|th)|first|second|third|last)\b", text)
        assert len(found) == len(ordinals) == len(each), (text, found)
