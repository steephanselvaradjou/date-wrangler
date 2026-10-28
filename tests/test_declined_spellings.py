"""What is declined on purpose stays declined however it is written, and says why.

A recurrence, a vague time, a season, a season's label and an hour are not periods this
library answers. A reading of any of them answers a question nobody asked, so each
spelling here must come back with nothing -- and, where a rule exists for it, a reason.
In 2.1.0 seven of them were answered: "every first Monday of the month" as this month's
first Monday, "the summer months of 2024" as the whole year, and "SY 2015-16", "the
2015-16 NBA season", "the 2015-16 Premier League season", "the 2015-16 campaign" and "the
2015-16 term" each as two calendar years. And "a while back" and "some time back" never
reached the rule that names them.

"AY" is not here: it is the assessment year, the fiscal year after the one it assesses,
and is read -- see tests/test_formats.py. "academic year" is written out.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import diagnose, parse

TODAY = date(2026, 10, 16)

#: Declined with a reason, by the rule named.
DECLINED: dict[str, list[str]] = {
    "recurrence": [
        "every Monday",
        "every Mon",
        "every other Monday",
        "every week",
        "every 2 weeks",
        "every two weeks",
        "every fortnight",
        "every 3 months",
        "every quarter",
        "every weekday",
        "every first Monday of the month",
        "every last Friday",
    ],
    "vague": [
        "a few weeks ago",
        "few weeks ago",
        "a few weeks back",
        "several months back",
        "a number of weeks ago",
        "a while ago",
        "a while back",
        "a little while ago",
        "some time ago",
        "sometime ago",
        "some time back",
        "recently",
        "lately",
        "the other day",
        "not long ago",
        "a long time ago",
        "ages ago",
        "in recent weeks",
        "in recent months",
        "the past few weeks",
        "the last few weeks",
        "over the past few months",
        "the last several months",
        "in the next few days",
        "in a few weeks",
        "a few weeks from now",
    ],
    "season": [
        "summer 2024",
        "the summer of 2024",
        "summer '24",
        "summer-2024",
        "the summer months of 2024",
        "this summer",
        "last winter",
        "winter 2024/25",
        "fall 2024",
    ],
    "season_label": [
        "the 2015-16 season",
        "season 2015/16",
        "the 2015-2016 season",
        "the 2015-16 academic year",
        "school year 2015-16",
        "SY 2015-16",
        "the 2015-16 NBA season",
        "the 2015-16 Premier League season",
        "the 2015-16 flu season",
        "the 2015-16 campaign",
        "the 2015-16 term",
    ],
    "sub_day": [
        "in the next 3 hours",
        "last hour",
        "the past hour",
        "last 30 mins",
        "last 24 hours",
        "past 48 hours",
        "an hour ago",
        "2 hrs ago",
        "in 2 hours",
    ],
}

#: No reading and no rule for them: right, if quiet.
UNREAD = [
    "each Monday",
    "on Mondays",
    "once a week",
    "twice a month",
    "weeks ago",
    "summer, 2024",
    "SS24",
    "the 15/16 season",
    "within the hour",
]


@pytest.mark.parametrize(("text", "rule"), [(v, rule) for rule, vs in DECLINED.items() for v in vs])
def test_declined_with_a_reason(text: str, rule: str) -> None:
    found, diags = diagnose(text, today=TODAY)
    assert found == []
    assert [d.rule for d in diags] == [rule]


@pytest.mark.parametrize("text", UNREAD)
def test_unread(text: str) -> None:
    assert parse(text, today=TODAY) == []


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        # The years are what is asked about, by season: two of them.
        ("2015-16 sales by season", "2015-01-01..2017-01-01"),
        # The Mondays fall inside March, and the 2024 holds every month.
        ("every Monday in March 2024", "2024-03-01..2024-04-01"),
        ("sales for each month of 2024", "2024-01-01..2025-01-01"),
        ("the last few months of 2024", "2024-01-01..2025-01-01"),
        # A count is not vague.
        ("the last 3 weeks", "2026-09-21..2026-10-12"),
        ("a couple of weeks ago", "2026-09-28..2026-10-05"),
    ],
)
def test_what_stays_as_it_was(text: str, dates: str) -> None:
    found = parse(text, today=TODAY)
    assert [f"{m.range.start}..{m.range.end}" for m in found] == [dates]
