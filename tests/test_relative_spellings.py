"""A relative phrase reads the same however it is spelt.

Checked against its plain form, keeping the writer's own direction word, so this pins the
spelling, not the meaning: "past 3 mos" must be what "past 3 months" is. Each kind used to
fail somewhere -- a count against its unit ("last 3months"), a short unit ("last mth"), a
chat word ("yday"), the week before its weekday ("last week Fri") -- and "3qtrs ago" was
the third quarter of this year.

Single letters for a unit stay unread: "3m" is minutes or millions as often as months.
"""

from __future__ import annotations

from datetime import date
from itertools import product

import pytest

from date_wrangler import parse

TODAY = date(2026, 10, 16)  # a Friday

UNITS = {
    "days": ["days", "dys"],
    "weeks": ["weeks", "wks", "wk"],
    "months": ["months", "mos", "mo", "mths", "mnths"],
    "quarters": ["quarters", "qtrs", "qtr"],
    "years": ["years", "yrs", "yr"],
}
ONE = {
    "week": ["wk"],
    "month": ["mo", "mth"],
    "quarter": ["qtr"],
    "year": ["yr"],
}


def _cases() -> list[tuple[str, str]]:
    out: dict[str, str] = {}
    for d, (unit, spellings) in product(
        ["last", "past", "previous", "prior", "next", "coming", "following"], UNITS.items()
    ):
        plain = f"{d} 3 {unit}"
        for sp, lead in product(spellings, ["", "in the "]):
            out[f"{lead}{d} 3 {sp}"] = plain
            out[f"{lead}{d} 3{sp}"] = plain
            out[f"{lead}{d} three {sp}"] = plain
    for d, (unit, spellings) in product(
        ["last", "previous", "this", "current", "next"], ONE.items()
    ):
        for sp in spellings:
            out[f"{d} {sp}"] = f"{d} {unit}"
            out[f"the {d} {sp.upper()}"] = f"{d} {unit}"
    for unit, spellings in UNITS.items():
        for sp in spellings:
            out[f"3 {sp} ago"] = f"3 {unit} ago"
            out[f"3{sp} ago"] = f"3 {unit} ago"
            out[f"3 {sp} back"] = f"3 {unit} ago"
            out[f"in 3{sp}"] = f"in 3 {unit}"
            out[f"3 {sp} from now"] = f"in 3 {unit}"
    out["prev month"] = "previous month"
    out["prev. quarter"] = "previous quarter"
    out["a couple of weeks ago"] = "2 weeks ago"
    out["the past couple of months"] = "past 2 months"
    out["trailing 12-month"] = "trailing 12 months"
    for word, plain in {
        "yday": "yesterday", "y'day": "yesterday", "yest": "yesterday", "Yesterday": "yesterday",
        "tmrw": "tomorrow", "tmr": "tomorrow", "2moro": "tomorrow", "tomorow": "tomorrow",
        "tommorow": "tomorrow", "tdy": "today", "day before yday": "the day before yesterday",
        "the day after tmrw": "the day after tomorrow",
    }.items():
        out[word] = plain
    for (day, short), week in product(
        [("Monday", "Mon"), ("Wednesday", "Weds"), ("Friday", "Fri"), ("Thursday", "Thurs")],
        ["last week", "this week", "next week"],
    ):
        plain = f"{day} {week}"
        out[f"{week} {day}"] = plain
        out[f"{week} {short}"] = plain
        out[f"{week}'s {day}"] = plain
        out[f"{week} on {day}"] = plain
        out[f"{short}. {week}"] = plain
        out[f"{short}. of {week}"] = plain
    for d in ["last", "next", "this"]:
        out[f"{d} Weds"] = f"{d} Wednesday"
        out[f"{d} wknd"] = f"{d} weekend"
    for spelt in [
        "biz days", "work days", "workdays", "business-days", "bus. days", "working days"
    ]:
        out[f"last 5 {spelt}"] = "last 5 business days"
        out[f"5 {spelt} ago"] = "5 business days ago"
        out[f"in 5 {spelt}"] = "in 5 business days"
    out["next workday"] = "next business day"
    out["next biz day"] = "next business day"
    for unit in ["month", "quarter"]:
        for spelt in ["of last year", "a year ago", "LY", "prior year", "previous year"]:
            out[f"same {unit} {spelt}"] = f"same {unit} last year"
    return sorted(out.items())


def _read(text: str) -> tuple[date | None, date | None] | None:
    """One period, read to the end of the text: a leading "in the" is not part of it."""
    found = parse(text, today=TODAY)
    if len(found) != 1 or text[found[0].span[1] :].strip(" .") not in ("", "'s"):
        return None
    return (found[0].range.start, found[0].range.end)


@pytest.mark.parametrize(("text", "plain"), _cases())
def test_a_spelling_reads_as_its_plain_form(text: str, plain: str) -> None:
    want = _read(plain)
    assert want is not None, f"the plain form {plain!r} is not read"
    assert _read(text) == want


@pytest.mark.parametrize("text", ["3m ago", "last 3 m", "in 3 d", "last q", "5 BD ago", "LFY"])
def test_single_letters_and_bare_acronyms_stay_unread(text: str) -> None:
    assert parse(text, today=TODAY) == []
