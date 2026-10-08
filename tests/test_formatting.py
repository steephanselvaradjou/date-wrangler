"""Rendering a range back to text without claiming more than it covers.

The parse layer learned to be careful about what it had actually read; the output layer
had not. Each of these rendered a period as something larger than it was, with nothing to
show it had been rounded.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import format_range, make_formatter, parse_one

TODAY = date(2026, 10, 15)  # mid-month, mid-quarter, so to-date periods are partial


def rng(text):
    m = parse_one(text, today=TODAY)
    assert m is not None, f"{text!r} found no date"
    return m.range


# ---------------------------------------------------------------------------
# format_range: a partial period is not the month it sits in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,shown",
    [
        ("MTD", "1 October 2026 to 15 October 2026"),
        ("QTD", "1 October 2026 to 15 October 2026"),
        ("YTD", "1 April 2026 to 15 October 2026"),
    ],
)
def test_a_to_date_period_names_days_not_months(text, shown):
    """"MTD" on the 15th is 15 days. Rendering it as "October 2026" claims the other
    sixteen, and nothing in the output says it was rounded."""
    assert format_range(rng(text)) == shown


@pytest.mark.parametrize(
    "text,shown",
    [
        ("last month", "September 2026"),
        ("August 2026", "August 2026"),
        ("last quarter", "July 2026 to September 2026"),
        ("FY27", "April 2026 to March 2027"),
    ],
)
def test_whole_month_periods_still_name_months(text, shown):
    assert format_range(rng(text)) == shown


def test_the_test_is_month_alignment_not_length():
    """Half-open makes it exact: a range covering whole months ends on the 1st."""
    from date_wrangler import DateRange, Grain

    aligned = DateRange(date(2026, 4, 1), date(2026, 7, 1), Grain.QUARTER)
    ragged = DateRange(date(2026, 4, 1), date(2026, 6, 30), Grain.QUARTER)
    assert format_range(aligned) == "April 2026 to June 2026"
    assert format_range(ragged) == "1 April 2026 to 29 June 2026"


# ---------------------------------------------------------------------------
# make_formatter: {start} alone is only honest when {end} renders the same
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,shown",
    [
        ("yesterday", "14 October 2026"),
        ("last week", "05 October 2026 to 11 October 2026"),
        ("last fortnight", "28 September 2026 to 11 October 2026"),
        ("last month", "01 September 2026 to 30 September 2026"),
        ("last quarter", "01 July 2026 to 30 September 2026"),
    ],
)
def test_a_day_precision_format_never_collapses_a_multi_day_range(text, shown):
    """REGRESSION: any range landing inside one calendar month used the `single` template,
    so a week printed as its Monday and a month as its 1st -- and `substitute` put that
    into the text, making a reader query one day instead of seven."""
    assert make_formatter(date_format="%d %B %Y")(rng(text)) == shown


@pytest.mark.parametrize(
    "text,shown",
    [
        ("last month", "September 2026"),
        ("last quarter", "July 2026 to September 2026"),
        ("FY27", "April 2026 to March 2027"),
    ],
)
def test_a_month_precision_format_keeps_months_for_whole_months(text, shown):
    """Whole-month ranges are exactly what "%B %Y" can describe, so it is used as asked."""
    assert make_formatter(date_format="%B %Y")(rng(text)) == shown


@pytest.mark.parametrize(
    "text,shown",
    [
        ("yesterday", "2026-10-14"),
        ("last week", "2026-10-05 to 2026-10-11"),
        ("MTD", "2026-10-01 to 2026-10-15"),
        ("YTD", "2026-04-01 to 2026-10-15"),
    ],
)
def test_a_coarse_format_falls_back_to_days_rather_than_overstate(text, shown):
    """REGRESSION: with "%B %Y", YTD rendered as "April 2026 to October 2026" -- which reads
    as all of October, sixteen days the range does not cover -- and a single week as
    "October 2026". The format cannot show a day and the range does not sit on whole
    months, so naming months would claim more than was parsed."""
    assert make_formatter(date_format="%B %Y")(rng(text)) == shown


def test_day_format_none_renders_exactly_what_was_asked():
    """The escape hatch, for bucket labels where the overstatement is intended."""
    assert make_formatter(date_format="%B %Y", day_format=None)(rng("YTD")) == (
        "April 2026 to October 2026"
    )


def test_the_default_format_is_unaffected_by_the_fallback():
    """"%Y-%m-%d" already shows days, so nothing changes unless the format is coarse."""
    fmt = make_formatter()
    assert fmt(rng("YTD")) == "2026-04-01 to 2026-10-15"
    assert fmt(rng("last month")) == "2026-09-01 to 2026-09-30"


def test_a_one_day_range_at_any_grain_prints_one_day():
    """REGRESSION: "MTD" asked on the 1st is one day at MONTH grain, and printed as
    "1 October 2026 to 1 October 2026" -- a range with one end."""
    first = parse_one("MTD", today=date(2026, 10, 1))
    assert first is not None and first.range.days == 1
    assert format_range(first.range) == "1 October 2026"


def test_single_is_used_exactly_when_the_two_ends_render_alike():
    marker = make_formatter(date_format="%B %Y", single="ONE {start}", closed="{start}/{end}")
    assert marker(rng("last month")).startswith("ONE ")
    assert "/" in marker(rng("last quarter"))


def test_custom_templates_still_apply():
    fmt = make_formatter(date_format="%d/%m/%Y", closed="{start} - {end}")
    assert fmt(rng("last quarter")) == "01/07/2026 - 30/09/2026"


# ---------------------------------------------------------------------------
# Open-ended ranges and the day fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,shown",
    [
        ("since March", "from March 2026 onwards"),
        ("after FY24", "from April 2024 onwards"),
        ("before 2024", "before January 2024"),
        ("up to March 2026", "up to March 2026"),
    ],
)
def test_an_open_ended_range_on_a_month_boundary_keeps_months(text, shown):
    """REGRESSION: 0.7.0's day fallback required both ends to be on a month boundary, and
    an unbounded end has none -- so "since March", already exact, became "from 01 March
    2026 onwards". Only the ends that exist can overstate anything."""
    fmt = make_formatter(date_format="%B %Y", since="from {start} onwards",
                         after="from {start} onwards")
    assert fmt(rng(text)) == shown


def test_an_open_ended_range_off_a_boundary_still_falls_back():
    fmt = make_formatter(date_format="%B %Y", since="from {start} onwards")
    assert fmt(rng("since 15 March")) == "from 2026-03-15 onwards"
