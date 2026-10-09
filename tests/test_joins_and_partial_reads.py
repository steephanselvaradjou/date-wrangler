"""Joined periods, and the leftovers that must lower confidence rather than vanish.

A list ("Q1 and Q3") and a span ("Q1 to Q3") are different things. A span is one range; a
list is its periods, each on its own. Merging a list into one range either cut it short --
"this year and Q1" was Q1 alone -- or took in days nobody named: "Q1 and Q3" held Q2.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import Basis, FiscalCalendar, WranglerConfig, diagnose, parse, parse_one

TODAY = date(2026, 10, 8)  # FY27 Q3 on an April start
FISCAL = WranglerConfig(bare_period_basis=Basis.FISCAL)
JAN = WranglerConfig(fiscal=FiscalCalendar.calendar())


def one(text, cfg=None):
    matches, diags = diagnose(text, today=TODAY, config=cfg or FISCAL)
    assert len(matches) == 1, (text, matches)
    return matches[0], diags


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def periods(text, cfg=None):
    """Each period found, as (text, start, end, confidence)."""
    return [
        (m.text, m.range.start, m.range.end, m.confidence)
        for m in parse(text, today=TODAY, config=cfg or FISCAL)
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Q1 and Q3", [("Q1", date(2026, 4, 1), date(2026, 7, 1)),
                       ("Q3", date(2026, 10, 1), date(2027, 1, 1))]),
        ("Q1 and Q2", [("Q1", date(2026, 4, 1), date(2026, 7, 1)),
                       ("Q2", date(2026, 7, 1), date(2026, 10, 1))]),
        ("March and June", [("March", date(2026, 3, 1), date(2026, 4, 1)),
                            ("June", date(2026, 6, 1), date(2026, 7, 1))]),
        ("this year and Q1", [("this year", date(2026, 4, 1), date(2027, 4, 1)),
                              ("Q1", date(2026, 4, 1), date(2026, 7, 1))]),
    ],
)
def test_and_lists_its_periods_each_on_its_own(text, expected):
    """REGRESSION: "and" merged its periods into one range. With a gap that took in days
    nobody named -- "Q1 and Q3" held Q2 -- and the best it could do was flag it; joined
    like a span it cut the list short instead, "this year and Q1" coming back as Q1. Each
    period now comes back as written, confidently, the same as in "Q1, Q2 and Q3"."""
    assert [(t, s, e) for t, s, e, _ in periods(text)] == expected
    assert all(c == 1.0 for *_, c in periods(text))


def test_a_list_has_no_order_so_it_does_not_wrap():
    """"Q3 and Q1" is two quarters of one year, not Q3 then the Q1 after it."""
    assert sorted(p[1:3] for p in periods("Q3 and Q1")) == sorted(
        p[1:3] for p in periods("Q1 and Q3")
    )


@pytest.mark.parametrize(
    "text,cfg,starts",
    [
        ("Q1 and Q2 2024", JAN, [date(2024, 1, 1), date(2024, 4, 1)]),
        ("Q1 and Q3 of FY25", FISCAL, [date(2024, 4, 1), date(2024, 10, 1)]),
        ("Jan to Mar and Jul to Sep 2024", JAN, [date(2024, 1, 1), date(2024, 7, 1)]),
    ],
)
def test_a_year_written_on_one_item_reaches_the_others(text, cfg, starts):
    """Spans inside the list included: "Jan to Mar" is in 2024 too."""
    assert [p[1] for p in periods(text, cfg)] == starts


def test_and_inside_a_list_keeps_its_spans():
    """Only "to" makes a span, so each span in a list of spans stays whole."""
    assert [p[0] for p in periods("Jan to Mar and Jul to Sep")] == ["Jan to Mar", "Jul to Sep"]


@pytest.mark.parametrize("text", ["from Q1 and Q3", "the figures from Q1 and Q3"])
def test_from_before_and_still_lists(text):
    """"from" opens a span only with "to"; before "and" it names where figures come from."""
    assert len(periods(text)) == 2


def test_a_comma_list_is_still_separate_matches():
    assert len(parse("Q1, Q2 and Q3", today=TODAY)) == 3


# ---------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------


def test_a_span_may_stop_partway_through_its_first_period():
    """"from the start of Q1 to 15 March" is a perfectly good question."""
    m, _ = one("Q1 to 15 March 2024", JAN)
    assert (m.range.start, m.range.end) == (date(2024, 1, 1), date(2024, 3, 16))
    assert m.confidence == 1.0


@pytest.mark.parametrize("text", ["Q1 to January", "between Q1 and January"])
def test_a_span_whose_end_lies_at_its_own_start_is_flagged(text):
    """Under a January year, "Q1 to January" is just January -- Q1 contributes nothing.
    Keep the literal range, but do not vouch for it."""
    m, diags = one(text, JAN)
    assert m.confidence <= 0.5
    assert any("adds nothing" in d.reason for d in diags)


@pytest.mark.parametrize(
    "text", ["Q1 to Q3", "Nov to Feb", "from March to June", "between March and June"]
)
def test_ordinary_spans_are_unchanged(text):
    assert one(text)[0].confidence == 1.0


# ---------------------------------------------------------------------------
# Leftovers that must be reported
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,dropped",
    [
        ("tomorrow morning", "morning"),
        ("yesterday afternoon", "afternoon"),
        ("next Monday week", "week"),
        ("a week on Monday", "a week on"),
        ("two weeks from Friday", "two weeks from"),
        ("3 days from Monday", "3 days from"),
    ],
)
def test_a_dropped_qualifier_lowers_confidence_and_says_what_it_was(text, dropped):
    m, diags = one(text)
    assert m.confidence <= 0.5
    assert any(dropped in d.reason for d in diags), [d.reason for d in diags]


def test_from_after_a_duration_is_not_a_start():
    """"two weeks from Friday" counts on from Friday. The "from <day>" reading added for
    "from 1 April" briefly turned it into "since Friday"."""
    m, _ = one("two weeks from Friday")
    assert m.range.end is not None


@pytest.mark.parametrize("text", ["in the coming months", "the past weeks", "next years"])
def test_a_plural_with_no_number_is_too_vague_to_answer(text):
    """REGRESSION: "in the coming months" was exactly one month, at full confidence."""
    assert parse(text, today=TODAY) == []


def test_a_counted_plural_still_reads():
    assert parse_one("last 3 months", today=TODAY) is not None


def test_a_decade_in_capitals():
    """REGRESSION: "THE 1990S" -- a heading in capitals -- matched nothing."""
    m, _ = one("THE 1990S")
    assert (m.range.start, m.range.end) == (date(1990, 1, 1), date(2000, 1, 1))


# ---------------------------------------------------------------------------
# Days sharing a month
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,days,year",
    [
        ("1st and 15th March", [1, 15], 2026),
        ("1st, 5th and 9th of March 2024", [1, 5, 9], 2024),
        ("March 1 and 15", [1, 15], 2026),
        ("March 1 and 15, 2024", [1, 15], 2024),
        ("March 1, 5 and 9.", [1, 5, 9], 2026),
        ("1st vs 15th March", [1, 15], 2026),
        ("1st and 15th March last year", [1, 15], 2025),
    ],
)
def test_days_sharing_a_month_each_come_back(text, days, year):
    """REGRESSION: only one day survived, at full confidence -- "1st and 15th March" was the
    15th, "March 1 and 15" the 1st -- because the other numbers had no month beside them."""
    found = parse(text, today=TODAY, config=JAN)
    assert [m.range.start for m in found] == [date(year, 3, d) for d in days]
    assert all(m.range.days == 1 and m.confidence == 1.0 for m in found)


def test_a_day_list_of_a_fiscal_month_follows_the_fiscal_year():
    found = parse("1 and 15 June FY25", today=TODAY, config=FISCAL)
    assert [m.range.start for m in found] == [date(2024, 6, 1), date(2024, 6, 15)]


@pytest.mark.parametrize("text", ["1 and 15 March", "top 3 and 15 March sales"])
def test_a_bare_first_number_is_probably_but_not_surely_a_day(text):
    """"the top 3 and 15 March" reads the same as "1 and 15 March". A suffix or a month in
    front would settle it; without one, the days come back at 0.8, like a bare year."""
    assert {m.confidence for m in parse(text, today=TODAY, config=JAN)} == {0.8}


@pytest.mark.parametrize(
    "text", ["March 1 and 15 customers", "March 1, 5 people attended", "March 1, 2024"]
)
def test_a_number_after_a_month_and_and_is_not_always_a_day(text):
    """Only before a year, a stop or the end does a bare number after "March 1 and" count."""
    assert len(parse(text, today=TODAY, config=JAN)) == 1


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("between 1 and 15 March", date(2026, 3, 1), date(2026, 3, 16)),
        ("between March 1 and 15", date(2026, 3, 1), date(2026, 3, 16)),
    ],
)
def test_between_two_days_of_a_month_is_a_run(text, start, end):
    m, _ = one(text, JAN)
    assert (m.range.start, m.range.end) == (start, end)


# ---------------------------------------------------------------------------
# A dash between a year and a quarter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("FY24 - Q3", date(2023, 10, 1), date(2024, 1, 1)),  # a label: Q3 of FY24
        ("FY24 - Q3 results", date(2023, 10, 1), date(2024, 1, 1)),
        ("2024 - Q3", date(2024, 7, 1), date(2024, 10, 1)),
        ("2023 - H2 2024", date(2023, 1, 1), date(2025, 1, 1)),  # a range: H2 has a year
    ],
)
def test_a_spaced_dash_is_a_label_unless_the_quarter_has_its_own_year(text, start, end):
    """REGRESSION: "2023 - H2 2024" was read as the label "2023-H2" and the 2024 was lost."""
    found = parse(text, today=TODAY, config=WranglerConfig())
    assert (found[0].range.start, found[0].range.end) == (start, end)


def test_a_comma_after_a_fiscal_year_lists_rather_than_labels():
    """"FY25 June" is June of FY25; "FY24, March" is FY24 and March."""
    assert [m.text for m in parse("FY24, March", today=TODAY)] == ["FY24", "March"]
