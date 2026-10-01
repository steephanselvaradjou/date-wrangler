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
        ("yesterday", "October 2026"),
        ("last week", "October 2026"),
        ("last month", "September 2026"),
        ("last quarter", "July 2026 to September 2026"),
    ],
)
def test_a_month_precision_format_may_collapse(text, shown):
    """With "%B %Y" both ends of a within-month range render identically, so printing one
    of them loses nothing. That is the actual rule -- what the format can show -- rather
    than a guess from the grain."""
    assert make_formatter(date_format="%B %Y")(rng(text)) == shown


def test_single_is_used_exactly_when_the_two_ends_render_alike():
    marker = make_formatter(date_format="%B %Y", single="ONE {start}", closed="{start}/{end}")
    assert marker(rng("last month")).startswith("ONE ")
    assert "/" in marker(rng("last quarter"))


def test_custom_templates_still_apply():
    fmt = make_formatter(date_format="%d/%m/%Y", closed="{start} - {end}")
    assert fmt(rng("last quarter")) == "01/07/2026 - 30/09/2026"
