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
#: Fiscal quarters, calendar years -- the split year_basis exists for.
CALENDAR_YEARS = WranglerConfig(year_basis=Basis.CALENDAR)


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
    for cfg, year_start, year_end in (
        (CALENDAR_YEARS, date(2024, 1, 1), date(2025, 1, 1)),
        (WranglerConfig(), date(2023, 4, 1), date(2024, 4, 1)),
    ):
        early, mid, late = rng("early 2024", cfg), rng("mid 2024", cfg), rng("late 2024", cfg)
        assert early[1] == mid[0]
        assert mid[1] == late[0]
        assert early[0] == year_start and late[1] == year_end


@pytest.mark.parametrize(
    "text,calendar,fiscal",
    [
        ("early 2024", (date(2024, 1, 1), date(2024, 5, 1)), (date(2023, 4, 1), date(2023, 8, 1))),
        ("late 2024", (date(2024, 9, 1), date(2025, 1, 1)), (date(2023, 12, 1), date(2024, 4, 1))),
        ("first half of 2024", (date(2024, 1, 1), date(2024, 7, 1)),
         (date(2023, 4, 1), date(2023, 10, 1))),
    ],
)
def test_a_part_of_a_numbered_year_follows_year_basis(text, calendar, fiscal):
    """A year with a part taken out of it is read the way year_basis reads years.

    0.3.0 forced it to the calendar year -- "early 2024" starting in 2023 was treated as
    a bug -- which left "the second half of 2024" on the calendar and "the last quarter of
    2024" on the fiscal setting, a year apart. Both now follow year_basis, so with it set
    to calendar "early 2024" is January to April, and with the default fiscal setting it
    is the first third of FY2024.
    """
    assert rng(text, CALENDAR_YEARS) == calendar
    assert rng(text) == fiscal


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
    # "March 2024 to date" was the example here until 0.7.1 learned to read it. A future
    # period still has nothing "to date", so this one stays a partial read.
    matches, diags = diagnose("next month to date", today=TODAY)
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


# ---------------------------------------------------------------------------
# Times of day
#
# A date carries a day; a clock carries a moment inside it. The library answers with the
# day either way, which is fine -- but it was doing so at full confidence with nothing
# said, which is the same silent-partial-read this file exists to stop.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,dropped",
    [
        ("yesterday at 2pm", "at 2pm"),
        ("yesterday at 14:30", "at 14:30"),
        ("last Monday at 9am", "at 9am"),
        ("due by 5pm on Friday", "by 5pm on"),
        ("the meeting at 3pm on Tuesday", "at 3pm on"),
        ("call at noon on Friday", "at noon on"),
        ("between 2pm and 4pm yesterday", "2pm and 4pm"),
    ],
)
def test_a_time_of_day_beside_a_date_is_reported_not_ignored(text, dropped):
    matches, diags = diagnose(text, today=TODAY)
    assert matches, f"{text!r} found no date at all"
    assert matches[0].confidence <= 0.5, f"{text!r} answered with a whole day, confidently"
    assert any(dropped in d.reason for d in diags), [d.reason for d in diags]


@pytest.mark.parametrize(
    "text",
    [
        "2024-03-15T14:30:00Z",
        "2024-03-15T14:30:00+05:30",
        "[2024-03-15 14:30:00] INFO started",
        "Mar 15 14:30:00 host sshd[1234]: accepted",
        "Last-Modified: Wed, 15 Mar 2024 14:30:00 GMT",
        "2024-03-15 14:30",
    ],
)
def test_a_timestamp_is_a_format_not_an_unread_qualifier(text):
    """A clock sitting straight against a date is part of a timestamp, and the day is the
    documented answer -- nothing was overlooked, so nothing is flagged. Only a preposition
    turns a time into a qualifier. Without this line every ISO instant in a log file would
    come back at half confidence."""
    matches, _ = diagnose(text, today=TODAY)
    assert matches and matches[0].confidence == 1.0


def test_a_bare_two_digit_year_is_not_read_as_a_clock():
    """"15 Mar 24" ends in a number that a looser clock pattern would claim as 24:00."""
    m = parse_one("15 Mar 24", today=TODAY)
    assert m is not None and m.confidence == 1.0
    assert m.range.start == date(2024, 3, 15)


# ---------------------------------------------------------------------------
# "and" between two periods that do not meet
# ---------------------------------------------------------------------------


def test_and_joining_adjacent_periods_is_a_span_and_stays_confident():
    """This is why "and" is a connector at all: two touching periods are how people write
    a span, and the hull is exactly right."""
    m = parse_one("Q1 and Q2", today=TODAY)
    assert m is not None and m.confidence == 1.0
    assert (m.range.start, m.range.end) == (date(2025, 4, 1), date(2025, 10, 1))


@pytest.mark.parametrize(
    "text,gap",
    [
        ("Q1 and Q3", "2025-07-01 to 2025-10-01"),
        ("March and June", "2025-04-01 to 2025-06-01"),
        ("15 March and 17 March", "2025-03-16 to 2025-03-17"),
    ],
)
def test_and_joining_periods_with_a_gap_is_flagged(text, gap):
    """Read as one span, "Q1 and Q3" quietly returns Q2 as well -- a quarter of data nobody
    asked for, previously at full confidence. The range is unchanged; the claim about it
    is what was wrong."""
    matches, diags = diagnose(text, today=TODAY)
    assert matches and matches[0].confidence <= 0.5
    assert any(gap in d.reason for d in diags), [d.reason for d in diags]


@pytest.mark.parametrize("text", ["between March and June", "from March to June", "Q1 to Q3"])
def test_an_explicit_span_lead_in_settles_it(text):
    """"between X and Y" is a span by construction, so the gap is not a guess."""
    m = parse_one(text, today=TODAY)
    assert m is not None and m.confidence == 1.0


def test_a_comma_separated_list_still_comes_back_as_separate_periods():
    found = parse("Q1, Q2 and Q3", today=TODAY)
    assert len(found) == 3
    assert all(m.confidence == 1.0 for m in found)


# ---------------------------------------------------------------------------
# <period> to date
# ---------------------------------------------------------------------------

OCT6 = date(2026, 10, 6)  # FY27 Q3 on an April start


@pytest.mark.parametrize(
    "text,start",
    [
        ("this year to date", date(2026, 4, 1)),
        ("current year to date", date(2026, 4, 1)),
        ("this month to date", date(2026, 10, 1)),
        ("this quarter to date", date(2026, 10, 1)),
        ("Q3 to date", date(2026, 10, 1)),
        ("FY27 to date", date(2026, 4, 1)),
        ("October to date", date(2026, 10, 1)),
        ("March 2024 to date", date(2024, 3, 1)),
        ("March 2024-to-date", date(2024, 3, 1)),
    ],
)
def test_a_period_to_date_runs_from_its_start_through_today(text, start):
    """REGRESSION: only the abbreviations and "year to date" were read. "this year to
    date" read "this year", left "to date" over, and answered with the whole fiscal year
    at confidence 0.5 -- April 2026 to March 2027, five months of it in the future."""
    m = parse_one(text, today=OCT6)
    assert m is not None and m.confidence == 1.0, text
    assert (m.range.start, m.range.end) == (start, date(2026, 10, 7))


def test_this_year_to_date_is_the_same_window_as_ytd():
    assert parse_one("this year to date", today=OCT6).range == parse_one("YTD", today=OCT6).range


def test_last_year_to_date_keeps_its_ytd_reading():
    """"last year to date" is last year's YTD -- the same window a year earlier -- not the
    span from the start of last year to today. The to_date rule reads it first."""
    r = parse_one("last year to date", today=OCT6).range
    assert (r.start, r.end) == (date(2025, 4, 1), date(2025, 10, 7))


def test_an_undated_period_in_the_future_means_the_last_one():
    """The same choice "YTD March" makes: the most recent one, not the coming one."""
    assert parse_one("December to date", today=OCT6).range.start == date(2025, 12, 1)
    assert parse_one("Q4 to date", today=OCT6).range.start == date(2026, 1, 1)


@pytest.mark.parametrize("text", ["FY28 to date", "December 2026 to date"])
def test_an_explicitly_future_period_has_nothing_to_date(text):
    matches, diags = diagnose(text, today=OCT6)
    assert matches == []
    assert any("after today" in d.reason for d in diags)
