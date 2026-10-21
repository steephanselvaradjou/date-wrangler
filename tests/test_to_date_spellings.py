"""A to-date phrase reads the same however it is spelt.

Each spelling is held to its plain form -- start, end and modifier -- so this pins the
spelling, not the meaning. In 2.1.0 about half of these read as their plain form: the
hyphen of "year-to-date", "so far", "WTD" and "FYTD" were not read; "fiscal year to date"
was the *calendar* year to date; "from 01/06/2018 till date" lost its end; "YTD last year"
and "YTD through March 2024" were two dates each.

Left unread or apart on purpose: "till the date" and "as on this date", which usually go
on "... of joining"; "to date" alone, which is only ever an end; "YTD, 2024", which lists
two periods; and "YTD to March", which "to" makes a span.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import parse

TODAY = date(2026, 10, 16)

_GROUPS: dict[str, list[str]] = {
    "YTD": ["ytd", "Ytd", "Y-T-D", "Y.T.D.", "year to date", "Year-to-date", "year-to-date",
            "YEAR TO DATE", "year to-date", "year till date", "year until date", "so far this year",
            "this year so far", "this year to date", "the year so far", "the year to date",
            "year so far", "for the year to date"],
    "MTD": ["mtd", "M-T-D", "M.T.D.", "month to date", "month-to-date", "so far this month",
            "this month so far", "this month to date", "the month so far", "this mth to date"],
    "QTD": ["qtd", "Q-T-D", "quarter to date", "quarter-to-date", "so far this quarter",
            "this quarter so far", "this quarter to date", "the quarter so far"],
    "this week to date": ["WTD", "wtd", "W-T-D", "week to date", "week-to-date", "this week so far",
                          "so far this week", "the week so far"],
    "this fiscal year to date": ["FYTD", "fytd", "FY YTD", "FY-YTD", "fiscal YTD",
                                 "fiscal year to date", "fiscal year-to-date", "FY to date",
                                 "financial year to date", "this FY to date", "fiscal year so far",
                                 "so far this fiscal year", "the financial year till date"],
    "this calendar year to date": ["CYTD", "calendar YTD", "calendar year to date",
                                   "calendar year-to-date"],
    "last YTD": ["prior YTD", "previous YTD", "LYTD", "PYTD", "last year YTD", "YTD last year",
                 "last year's YTD", "prior year YTD", "last year to date", "prior year to date",
                 "LY YTD", "YTD LY", "PY YTD"],
    "last MTD": ["prior MTD", "previous MTD", "LMTD", "last month to date", "MTD last month",
                 "last month's MTD"],
    "last QTD": ["prior QTD", "previous QTD", "LQTD", "last quarter to date", "QTD last quarter"],
    "YTD 2024": ["2024 YTD", "YTD-2024", "2024-YTD", "YTD of 2024", "2024 year to date",
                 "year to date 2024", "YTD for 2024", "YTD in 2024", "2024 year-to-date",
                 "year-to-date 2024", "YTD'24"],
    "YTD FY24": ["FY24 YTD", "YTD-FY24", "FY24-YTD", "YTD of FY24", "YTD for FY24",
                 "FY24 year-to-date"],
    "YTD March 2024": ["YTD Mar 2024", "YTD Mar-24", "YTD Mar'24", "YTD March, 2024",
                       "YTD through March 2024", "YTD thru March 2024", "YTD till March 2024",
                       "YTD until March 2024", "YTD up to March 2024", "YTD upto March 2024",
                       "YTD as of March 2024", "YTD as at March 2024", "YTD as on March 2024",
                       "March 2024 YTD", "Mar-24 YTD", "year-to-date March 2024",
                       "YTD ending March 2024", "YTD ended March 2024"],
    "till date": ["until date", "till now", "until now", "till today", "until today", "up to now",
                  "up till now", "uptill now", "till dt", "TILL DATE", "upto now", "until present",
                  "till present"],
    "as on date": ["as of date", "as on today", "as of today", "as at today", "as of now",
                   "as on dt", "as on dated", "AS ON DATE", "As on date", "as at date",
                   "as at now"],
}
_ENDS = ["till date", "till today", "till now", "until date", "until now", "until today",
         "up to today", "upto today", "up till now", "up until now", "to date", "to present",
         "to the present", "- present", "through today", "thru today", "to till date",
         "to-date", "till dt", "till dated", "TILL DATE", "uptill date", "upto now",
         "until present", "until the present", "to now", "- till date", "till present",
         "upto date", "up to date", "to this day", "up to now", "through to today",
         "onwards till date"]
_STARTS = ["1 June 2018", "01/06/2018", "June 2018", "2018", "from 1 June 2018", "Q1 2018",
           "since 1 June 2018"]


def _cases() -> list[tuple[str, str]]:
    out = [(v, plain) for plain, spellings in _GROUPS.items() for v in spellings]
    for start in _STARTS:
        plain = f"{start.replace('since', 'from')} to today"
        out += [(f"{start} {end}", plain) for end in _ENDS]
    return out


def _read(text: str) -> tuple[object, object, object] | None:
    found = parse(text, today=TODAY)
    if len(found) != 1 or text[found[0].span[1] :].strip(" .") != "":
        return None
    r = found[0].range
    return (r.start, r.end, r.mod)


@pytest.mark.parametrize(("text", "plain"), _cases())
def test_a_spelling_reads_as_its_plain_form(text: str, plain: str) -> None:
    want = _read(plain)
    assert want is not None, f"the plain form {plain!r} is not read"
    assert _read(text) == want


@pytest.mark.parametrize(
    ("text", "count"),
    [("YTD, 2024", 2), ("the report is up to date", 0), ("as on the date of joining", 0)],
)
def test_what_stays_apart_or_unread(text: str, count: int) -> None:
    assert len(parse(text, today=TODAY)) == count
