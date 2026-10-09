"""Joined periods, and the leftovers that must lower confidence rather than vanish.

A list ("Q1 and Q2") and a span ("Q1 to Q2") are different things. Merging both the same
way -- start of the first to end of the last -- cut lists short whenever a later period sat
inside an earlier one, at full confidence.
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


@pytest.mark.parametrize(
    "text,cfg,start,end",
    [
        ("this year and Q1", None, date(2026, 4, 1), date(2027, 4, 1)),
        ("this year and Q1", JAN, date(2026, 1, 1), date(2027, 1, 1)),
        ("Q1 and January", JAN, date(2026, 1, 1), date(2026, 4, 1)),
        ("Q1 and Q2", None, date(2026, 4, 1), date(2026, 10, 1)),
    ],
)
def test_a_list_is_the_union_of_its_periods(text, cfg, start, end):
    """REGRESSION: "this year and Q1" came back as Q1 alone -- the list was merged like a
    span, from the start of the first period to the end of the last, which cut it short
    wherever a later period sat inside an earlier one."""
    m, _ = one(text, cfg)
    assert (m.range.start, m.range.end) == (start, end)
    assert m.confidence == 1.0


@pytest.mark.parametrize(
    "text", ["Q1 and Q3", "Q3 and Q1", "March and June", "Q1 and Q3 and last month"]
)
def test_a_list_with_a_gap_is_joined_but_flagged(text):
    m, diags = one(text)
    assert m.confidence <= 0.5
    assert any("in between" in d.reason for d in diags)


def test_a_list_has_no_order_so_it_does_not_wrap():
    """"Q3 and Q1" is two quarters of one year, not Q3 then the Q1 after it."""
    assert one("Q3 and Q1")[0].range == one("Q1 and Q3")[0].range


def test_a_year_written_on_one_item_reaches_the_others():
    m, _ = one("Q1 and Q2 2024", JAN)
    assert (m.range.start, m.range.end) == (date(2024, 1, 1), date(2024, 7, 1))


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
