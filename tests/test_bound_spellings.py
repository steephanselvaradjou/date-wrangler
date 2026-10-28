"""An open-ended period or a bound reads the same however it is written.

Since, after, before and until, deadlines, a count from a date, and "as of" -- each written
every way people write them, against a month, a day, a year, a fiscal quarter, a relative
period, an ISO day and a numeric month, and held to the plain form's start, end and
modifier. 2.3.0 read about seven in ten. Among the rest, "not later than March 2024" was
*after* March, its "not" unread; "on and after 15 March" began on the 16th; "3 days earlier
than 15 March 2024" and "two weeks later than 1 April 2024" counted from today; and "w.e.f.
March 2024", "til March 2024", ">= 2024-03-15", "pre March 2024", "beyond 2012", "as from
Q1 FY25" and "1 April 2024 + 2 weeks" were each the period alone, the bound dropped.

Left alone on purpose: "post March" without its hyphen, which is a verb as often; "forward"
and "on" without "from" ("let's march forward"); and "a year from March", which may be the
twelve months starting in March, so it stays flagged.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from date_wrangler import Mod, diagnose, parse

TODAY = date(2026, 10, 16)

PERIODS = ["March 2024", "15 March 2024", "2012", "Q1 FY25", "last month", "2024-03-15", "2024-03"]
DAYS = {"15 March 2024", "2024-03-15"}
#: "the end of" a single day is not something anyone writes.
NOT_FOR_DAYS = ("the end of", "the start of")

#: plain form: (spellings for any period, spellings for a day only)
FAMILIES: dict[str, tuple[list[str], list[str]]] = {
    "since {p}": (
        [
            "from {p} onwards", "from {p} onward", "{p} onwards", "{p} onward",
            "{p} and later", "{p} or later", "{p} and after", "{p} or after",
            "{p} and beyond", "starting {p}", "starting from {p}", "starting in {p}",
            "beginning {p}", "beginning from {p}", "commencing {p}", "in or after {p}",
            "not before {p}", "no earlier than {p}", "from and including {p}",
            "ever since {p}", "Since {p}", "SINCE {p}", "from {p} on", "from {p} forward",
            "from {p} forwards", "as from {p}", "with effect from {p}", "w.e.f. {p}",
            "wef {p}", "w.e.f {p}", "{p} onwards.", "since {p} onwards",
            "anytime since {p}", "starting with {p}",
        ],
        ["on or after {p}", "from {p}", ">= {p}", "≥ {p}", "on and after {p}"],
    ),
    "after {p}": (
        [
            "later than {p}", "post-{p}", "subsequent to {p}", "beyond {p}",
            "anytime after {p}", "sometime after {p}", "After {p}", "only after {p}",
            "after the end of {p}",
        ],
        ["> {p}"],
    ),
    "before {p}": (
        [
            "prior to {p}", "earlier than {p}", "pre {p}", "pre-{p}", "ahead of {p}",
            "preceding {p}", "Before {p}", "anytime before {p}", "sometime before {p}",
            "only before {p}", "before the start of {p}", "older than {p}",
        ],
        ["< {p}"],
    ),
    "until {p}": (
        [
            "till {p}", "til {p}", "untill {p}", "up to {p}", "upto {p}", "up till {p}",
            "up until {p}", "through {p}", "thru {p}", "by {p}", "no later than {p}",
            "not later than {p}", "in or before {p}", "not after {p}", "{p} and earlier",
            "{p} or earlier", "{p} and before", "{p} or before", "{p} and prior",
            "up to and including {p}", "until and including {p}", "Until {p}",
            "till the end of {p}", "through the end of {p}", "by the end of {p}",
            "until the end of {p}", "up to the end of {p}", "uptil {p}",
        ],
        ["on or before {p}", "<= {p}", "≤ {p}", "on and before {p}"],
    ),
}  # fmt: skip

#: Counted from a date, and "as of": plain form -> spellings.
SPELLINGS: dict[str, list[str]] = {
    "3 days before 15 March 2024": [
        "3 days prior to 15 March 2024", "three days before 15 March 2024",
        "3 days earlier than 15 March 2024", "3 days ahead of 15 March 2024",
        "15 March 2024 minus 3 days", "3 days before March 15, 2024",
        "3 days before 2024-03-15", "3 days before 15/03/2024",
        "3 business days before 15 March 2024",
    ],
    "two weeks after 1 April 2024": [
        "2 weeks after 1 April 2024", "two weeks from 1 April 2024", "14 days after 1 April 2024",
        "1 April 2024 plus two weeks", "1 April 2024 + 2 weeks",
        "two weeks later than 1 April 2024", "two weeks following 1 April 2024",
        "a fortnight after 1 April 2024", "2 wks after 1 April 2024",
        "two weeks past 1 April 2024",
    ],
    "a year after March 2024": [
        "one year after March 2024", "1 year after March 2024", "12 months after March 2024",
        "a year later than March 2024", "March 2024 plus a year", "1 yr after March 2024",
    ],
    "as of 31 March 2024": [
        "as on 31 March 2024", "as at 31 March 2024", "As at 31 March 2024",
        "as of 31/03/2024", "as at 31.03.2024", "as on 31-Mar-2024", "as of March 31, 2024",
        "As of 31 March 2024", "as of 2024-03-31", "as at 31st March 2024",
        "as on 31st March, 2024", "position as at 31 March 2024",
        "as at the 31st of March 2024",
    ],
}  # fmt: skip


def _cases() -> Iterator[tuple[str, str]]:
    for plain, (any_period, day_only) in FAMILIES.items():
        for p in PERIODS:
            for spelling in any_period + (day_only if p in DAYS else []):
                if p in DAYS and any(w in spelling for w in NOT_FOR_DAYS):
                    continue
                yield spelling.format(p=p), plain.format(p=p)
    for plain, spellings in SPELLINGS.items():
        for spelling in spellings:
            yield spelling, plain


def _read(text: str) -> tuple[object, object, object] | None:
    found = parse(text, today=TODAY)
    if len(found) != 1:
        return None
    r = found[0].range
    return (r.start, r.end, r.mod)


@pytest.mark.parametrize(("text", "plain"), list(_cases()))
def test_a_spelling_reads_as_its_plain_form(text: str, plain: str) -> None:
    want = _read(plain)
    assert want is not None, f"the plain form {plain!r} is not read"
    assert _read(text) == want


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        # "forward" and "on" only after "from": a march is not a month.
        ("let's march forward", []),
        ("2012 forward", []),
        ("revenue for Q1 on the west coast", [("2026-01-01", "2026-04-01", None)]),
        # "post" without its hyphen is a verb as often.
        ("post March 2024 figures", [("2024-03-01", "2024-04-01", None)]),
        # A span is still a span, not a date minus a count.
        ("1 April 2024 - 15 April 2024", [("2024-04-01", "2024-04-16", None)]),
        # "the beginning of" is still the first part.
        ("the beginning of March 2024", [("2024-03-01", "2024-03-11", None)]),
        ("by the end of 2024-03", [("None", "2024-04-01", Mod.UNTIL)]),
    ],
)
def test_what_stays_as_it_was(text: str, dates: list[tuple[str, str, object]]) -> None:
    found = parse(text, today=TODAY)
    assert [(str(m.range.start), str(m.range.end), m.range.mod) for m in found] == dates


@pytest.mark.parametrize("text", ["a year from March", "two weeks from Friday"])
def test_counted_on_from_a_period_is_flagged_not_guessed(text: str) -> None:
    """Twelve months starting in March, or March next year? Left flagged as unread."""
    found, diags = diagnose(text, today=TODAY)
    assert all(m.confidence <= 0.5 for m in found)
    assert any("from" in d.reason for d in diags)


def test_business_days_step_over_the_weekend() -> None:
    (found,) = parse("3 business days before 18 March 2024", today=TODAY)  # a Monday
    assert found.range.start == date(2024, 3, 13)
