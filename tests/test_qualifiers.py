"""Period qualifiers and the anchored/rolling distinction.

Every case in the first class was silently wrong before: the qualifier was scanned as its
own match, or dropped entirely, and the caller got a confident range for a period nobody
asked about. "first half of March" resolving to April-October was the worst of them.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import (
    Anchor,
    Basis,
    Grain,
    WranglerConfig,
    diagnose,
    parse,
    parse_one,
)

TODAY = date(2025, 9, 4)  # a Thursday, in fiscal Q2 of FY2026 on an April start
ROLLING = WranglerConfig(anchor=Anchor.ROLLING)


def rng(text, cfg=None):
    m = parse_one(text, today=TODAY, config=cfg or WranglerConfig())
    assert m is not None, f"{text!r} did not match"
    return m.range.start, m.range.end


# ---------------------------------------------------------------------------
# Relative years: "Q1 last year"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("Q1 last year", date(2024, 4, 1), date(2024, 7, 1)),
        ("Q1 of last year", date(2024, 4, 1), date(2024, 7, 1)),
        ("Q3 next year", date(2026, 10, 1), date(2027, 1, 1)),
        ("Q1 this year", date(2025, 4, 1), date(2025, 7, 1)),
        ("March last year", date(2024, 3, 1), date(2024, 4, 1)),
        ("H1 last year", date(2024, 4, 1), date(2024, 10, 1)),
    ],
)
def test_relative_year_qualifier(text, start, end):
    """REGRESSION: "Q1 last year" scanned as two matches and Q1 kept the current year."""
    assert rng(text) == (start, end)


def test_relative_year_is_one_match():
    found = parse("Q1 last year", today=TODAY)
    assert len(found) == 1
    assert found[0].text == "Q1 last year"


# ---------------------------------------------------------------------------
# Parts of a period: "first half of March", "late 2024"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end",
    [
        # Days, because half a month is not a whole number of months.
        ("first half of March", date(2025, 3, 1), date(2025, 3, 16)),
        ("second half of June", date(2025, 6, 16), date(2025, 7, 1)),
        ("beginning of March", date(2025, 3, 1), date(2025, 3, 11)),
        ("start of March", date(2025, 3, 1), date(2025, 3, 11)),
        ("mid March", date(2025, 3, 11), date(2025, 3, 21)),
        ("end of March", date(2025, 3, 21), date(2025, 4, 1)),
        # Months, because a year divides evenly into halves and thirds.
        ("early 2024", date(2024, 1, 1), date(2024, 5, 1)),
        ("late 2024", date(2024, 9, 1), date(2025, 1, 1)),
        ("first half of 2024", date(2024, 1, 1), date(2024, 7, 1)),
    ],
)
def test_part_of_period(text, start, end):
    """REGRESSION: "first half of March" read "first half" as fiscal H1 and dropped the
    month, answering April-October -- six months out, and plausible enough to survive."""
    assert rng(text) == (start, end)


def test_parts_tile_the_period_exactly():
    """No day may fall between the pieces, and none may be counted twice."""
    first = rng("first half of March")
    second = rng("second half of June")
    assert first[0] == date(2025, 3, 1) and first[1] == date(2025, 3, 16)
    assert second[1] == date(2025, 7, 1)
    early, mid, late = rng("early 2024"), rng("mid 2024"), rng("late 2024")
    assert early[1] == mid[0]
    assert mid[1] == late[0]
    assert early[0] == date(2024, 1, 1) and late[1] == date(2025, 1, 1)


def test_bare_year_part_is_a_calendar_year():
    """"early 2024" must not quietly mean the fiscal year and start in 2023."""
    assert rng("early 2024")[0].year == 2024


# ---------------------------------------------------------------------------
# A day inside a period: "1st of next month"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,day",
    [
        ("1st of next month", date(2025, 10, 1)),
        ("15th of next month", date(2025, 10, 15)),
        ("15th of March", date(2025, 3, 15)),
        ("3rd of last month", date(2025, 8, 3)),
    ],
)
def test_nth_of_period(text, day):
    """REGRESSION: "1st of next month" dropped the ordinal and returned all 31 days."""
    assert rng(text) == (day, day + (date(2025, 1, 2) - date(2025, 1, 1)))


def test_day_outside_the_period_does_not_resolve():
    assert parse("31st of February", today=TODAY) == []


# ---------------------------------------------------------------------------
# "same quarter last year"
# ---------------------------------------------------------------------------


def test_same_period_last_year():
    """REGRESSION: returned the whole of last year, dropping "same quarter"."""
    assert rng("same quarter last year") == (date(2024, 7, 1), date(2024, 10, 1))
    assert rng("same month last year") == (date(2024, 9, 1), date(2024, 10, 1))
    assert rng("this time last year") == (date(2024, 9, 4), date(2024, 9, 5))


# ---------------------------------------------------------------------------
# Anchored vs rolling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,anchored,rolling",
    [
        ("last month", (date(2025, 8, 1), date(2025, 9, 1)),
                       (date(2025, 8, 4), date(2025, 9, 4))),
        ("last 3 months", (date(2025, 6, 1), date(2025, 9, 1)),
                          (date(2025, 6, 4), date(2025, 9, 4))),
        ("last 2 quarters", (date(2025, 1, 1), date(2025, 7, 1)),
                            (date(2025, 3, 4), date(2025, 9, 4))),
    ],
)
def test_anchor_setting(text, anchored, rolling):
    assert rng(text) == anchored
    assert rng(text, ROLLING) == rolling


def test_anchored_is_the_default():
    assert parse_one("last month", today=TODAY).range.anchor is Anchor.ANCHORED


def test_rolling_wording_overrides_the_default():
    """"rolling 3 months" means rolling whatever the config says."""
    r = parse_one("rolling 3 months", today=TODAY).range
    assert (r.start, r.end) == (date(2025, 6, 4), date(2025, 9, 4))
    assert r.anchor is Anchor.ROLLING


def test_trailing_twelve_months_stays_anchored():
    """TTM means the last twelve *completed* months; rolling it would defeat the point."""
    for text in ("TTM", "LTM", "trailing 12 months", "T12M"):
        assert rng(text) == (date(2024, 9, 1), date(2025, 9, 1)), text
        assert rng(text, ROLLING) == (date(2024, 9, 1), date(2025, 9, 1)), text


def test_day_grain_is_unaffected():
    """A day is its own unit, so anchored and rolling coincide -- which is why
    "last 30 days" has always rolled, whatever the setting said."""
    assert rng("last 30 days") == rng("last 30 days", ROLLING)
    assert rng("last 30 days") == (date(2025, 8, 5), date(2025, 9, 4))


def test_absolute_periods_ignore_the_anchor():
    for text in ("March 2024", "Q1 FY25", "2024-03-15"):
        assert rng(text) == rng(text, ROLLING), text


def test_rolling_months_land_on_the_same_day_of_month():
    r = parse_one("last 6 months", today=date(2025, 3, 31), config=ROLLING).range
    assert r.start == date(2024, 9, 30)  # September has no 31st; clamped, not rolled over
    assert r.end == date(2025, 3, 31)


# ---------------------------------------------------------------------------
# The safety net: qualifiers the rules do not cover
# ---------------------------------------------------------------------------


def test_unread_qualifier_lowers_confidence_and_explains():
    matches, diags = diagnose("March 2024 to date", today=TODAY)
    assert matches and matches[0].confidence <= 0.5
    assert any("to date" in d.reason for d in diags)


def test_clean_matches_keep_full_confidence():
    for text in ("sales in March", "Q1 FY25", "last month", "first half of March"):
        matches, diags = diagnose(text, today=TODAY)
        assert matches, text
        assert matches[0].confidence == 1.0, text
        assert not [d for d in diags if d.rule == "partial"], text


# ---------------------------------------------------------------------------
# Vocabulary added alongside the qualifier work
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end",
    [
        # TODAY is a Thursday, so "this weekend" is the one ahead of it.
        ("weekend", date(2025, 9, 6), date(2025, 9, 8)),
        ("this weekend", date(2025, 9, 6), date(2025, 9, 8)),
        ("next weekend", date(2025, 9, 13), date(2025, 9, 15)),
        ("last weekend", date(2025, 8, 30), date(2025, 9, 1)),
    ],
)
def test_weekend(text, start, end):
    assert rng(text) == (start, end)


def test_weekend_is_two_days():
    for text in ("weekend", "next weekend", "last weekend"):
        start, end = rng(text)
        assert (end - start).days == 2, text
        assert start.weekday() == 5, text  # Saturday


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("month end", date(2025, 9, 21), date(2025, 10, 1)),
        ("end of the month", date(2025, 9, 21), date(2025, 10, 1)),
        ("EOM", date(2025, 9, 21), date(2025, 10, 1)),
        ("month start", date(2025, 9, 1), date(2025, 9, 11)),
    ],
)
def test_period_edges(text, start, end):
    assert rng(text) == (start, end)


@pytest.mark.parametrize(
    "text,start,end",
    [
        ("in 3 days", date(2025, 9, 7), date(2025, 9, 8)),
        ("in 2 weeks", date(2025, 9, 15), date(2025, 9, 22)),
        ("2 weeks from now", date(2025, 9, 15), date(2025, 9, 22)),
        ("3 months from today", date(2025, 12, 1), date(2026, 1, 1)),
        ("in a month", date(2025, 10, 1), date(2025, 11, 1)),
    ],
)
def test_future_relative(text, start, end):
    """REGRESSION: "3 months from today" matched only "today" and answered one day."""
    assert rng(text) == (start, end)


def test_article_counts_as_one():
    assert rng("a month ago") == rng("1 month ago")
    assert rng("a week ago") == rng("one week ago")


def test_fortnight_is_two_weeks():
    a, b = rng("last fortnight")
    assert (b - a).days == 14
    assert rng("next fortnight") == (date(2025, 9, 8), date(2025, 9, 22))


@pytest.mark.parametrize(
    "text,day",
    [
        ("day before yesterday", date(2025, 9, 2)),
        ("the day before yesterday", date(2025, 9, 2)),
        ("day after tomorrow", date(2025, 9, 6)),
    ],
)
def test_day_idioms_are_single_days(text, day):
    """REGRESSION: read as "before yesterday", these became unbounded ranges reaching
    back to the beginning of time -- a range that matches almost every row."""
    start, end = rng(text)
    assert (start, end) == (day, day + (date(2025, 1, 2) - date(2025, 1, 1)))


@pytest.mark.parametrize("text", ["mid-March", "mid-2024", "early-2024"])
def test_hyphenated_parts(text):
    """An editor writing "mid-March" means the same as "mid March"."""
    assert parse(text, today=TODAY) != []


def test_a_year_from_a_month_is_flagged_not_guessed():
    """Not resolved, but not silently answered with the bare month either."""
    matches, diags = diagnose("a year from March", today=TODAY)
    assert matches and matches[0].confidence <= 0.5
    assert any("a year from" in d.reason for d in diags)


# ---------------------------------------------------------------------------
# Shape of the results
# ---------------------------------------------------------------------------


def test_part_and_day_report_a_sensible_grain():
    """A slice is not the grain it came out of -- grain drives the GROUP BY bucket, and a
    third of a year is four months, so grouping it by year would collapse it to one row."""
    assert parse_one("first half of March", today=TODAY).range.grain is Grain.DAY
    assert parse_one("15th of March", today=TODAY).range.grain is Grain.DAY
    assert parse_one("early 2024", today=TODAY).range.grain is Grain.MONTH


def test_relative_year_keeps_its_basis():
    assert parse_one("Q1 last year", today=TODAY).range.basis is Basis.FISCAL
    assert parse_one("Q1 CY2024", today=TODAY).range.basis is Basis.CALENDAR


def test_qualified_periods_survive_a_round_trip_through_parse():
    """Nothing here may produce an inverted or empty range."""
    texts = [
        "Q1 last year", "first half of March", "late 2024", "1st of next month",
        "same quarter last year", "rolling 3 months", "mid March", "end of March",
    ]
    for text in texts:
        for cfg in (WranglerConfig(), ROLLING):
            m = parse_one(text, today=TODAY, config=cfg)
            assert m is not None, (text, cfg.anchor)
            assert m.range.start is not None and m.range.end is not None
            assert m.range.start < m.range.end, (text, m.range)
