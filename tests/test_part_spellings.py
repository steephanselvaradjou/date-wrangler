"""A part or edge of a period reads the same however it is written.

Each spelling is held to its plain form -- start, end and modifier. In 2.1.0 about four
in five read as their plain form; among the rest, "last day of month" was yesterday,
"first-half of March 2024" was January to June, "2024 year end" was the end of this year,
"start of Mar-24" this year's March, and "Q1-end 2024" the whole of 2024.

Left alone on purpose: "later in 2024" and "earlier in 2024" (earlier or later than
what?), "by month end" (until it), "WE 9 Oct" ("we"), and shapes nobody writes twice.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import parse

TODAY = date(2026, 10, 16)

SPELLINGS: dict[str, list[str]] = {
    "end of Q1 2024": [
        "the end of Q1 2024",
        "end Q1 2024",
        "end-Q1 2024",
        "End of Q1 2024",
        "end of 1Q 2024",
        "end of Q1, 2024",
        "end of the first quarter of 2024",
        "end of first quarter 2024",
        "close of Q1 2024",
        "towards the end of Q1 2024",
        "end of Qtr 1 2024",
    ],
    "beginning of March 2024": [
        "start of March 2024",
        "the start of March 2024",
        "beginning March 2024",
        "beginning of Mar 2024",
        "start of Mar-24",
        "begin of March 2024",
        "starting of March 2024",
    ],
    "mid 2024": ["mid-2024", "middle of 2024", "the middle of 2024", "mid-year 2024"],
    "late 2024": [
        "late in 2024",
        "late-2024",
        "the latter part of 2024",
        "end of 2024",
        "end-2024",
    ],
    "early 2024": [
        "early in 2024",
        "early-2024",
        "the early part of 2024",
        "beginning of 2024",
        "start of 2024",
        "begining of 2024",
    ],
    "2024": ["year-end 2024", "year end 2024", "2024 year end", "2024 year-end"],
    "Q1 2024": ["Q1-end 2024"],
    "first half of March 2024": [
        "the first half of March 2024",
        "1st half of March 2024",
        "first half March 2024",
        "first-half of March 2024",
    ],
    "second half of 2024": [
        "2nd half of 2024",
        "latter half of 2024",
        "the back half of 2024",
        "the last half of 2024",
        "2H 2024",
    ],
    "end of the month": [
        "end of month",
        "end of the mth",
        "month end",
        "month-end",
        "EOM",
        "E.O.M.",
        "end-of-month",
        "end of this month",
        "mth end",
    ],
    "end of the quarter": ["end of qtr", "quarter-end", "qtr end", "EOQ", "end-of-quarter"],
    "end of the year": ["end of year", "year-end", "yr end", "EOY", "end-of-year"],
    "start of next month": [
        "beginning of next month",
        "the start of next month",
        "beginning of next mth",
        "early next month",
    ],
    "the last day of the month": [
        "last day of the month",
        "last day of month",
        "final day of the month",
        "last day of the mth",
        "last date of the month",
    ],
    "the first day of next month": [
        "first day of next month",
        "1st day of next month",
        "first day of the next month",
        "first date of next month",
    ],
    "the last business day of the month": [
        "last business day of the month",
        "last working day of the month",
        "last biz day of the month",
        "last workday of the month",
        "last business day of month",
        "the last bus. day of the month",
    ],
    "the first business day of next month": [
        "first working day of next month",
        "first workday of next month",
        "first biz day of next month",
    ],
    "the first Monday of March 2024": [
        "first Monday of March 2024",
        "1st Monday of March 2024",
        "first Monday in March 2024",
        "first Mon of March 2024",
        "1st Mon of Mar 2024",
        "first monday of march 2024",
    ],
    "the third Thursday of November": [
        "3rd Thursday of November",
        "3rd Thu of Nov",
        "the 3rd Thurs. of Nov.",
    ],
    "the last Friday of the month": [
        "last Friday of the month",
        "final Friday of the month",
        "last Fri of the month",
        "last Friday in the month",
        "last Friday of month",
    ],
    "the first week of April 2024": [
        "first week of April 2024",
        "1st week of April 2024",
        "first wk of April 2024",
        "1st wk of Apr 2024",
        "first week in April 2024",
    ],
    "the last week of March 2024": ["final week of March 2024", "last wk of March 2024"],
    "the first 2 weeks of March 2024": [
        "first two weeks of March 2024",
        "first 2 wks of March 2024",
        "the first fortnight of March 2024",
    ],
    "the last 3 months of 2024": [
        "last three months of 2024",
        "last 3 mths of 2024",
        "the final 3 months of 2024",
    ],
    "the last month of the quarter": [
        "final month of the quarter",
        "last month of the qtr",
        "last mth of the quarter",
    ],
    "the 15th of March 2024": [
        "15th of March 2024",
        "the fifteenth of March 2024",
        "15 of March 2024",
    ],
    "the 1st of next month": [
        "1st of next month",
        "the first of next month",
        "1st of the next month",
        "the 1st of the next month",
    ],
    "the 15th of this month": [
        "15th of this month",
        "the 15th of the month",
        "15th of the month",
        "the 15th of the current month",
    ],
    "1-15 March 2024": [
        "1st-15th March 2024",
        "1 to 15 March 2024",
        "1st to the 15th of March 2024",
        "1st thru 15th March 2024",
        "March 1-15, 2024",
        "Mar 1-15 2024",
        "between the 1st and 15th of March 2024",
        "1-15th March 2024",
    ],
    "the week of 5 October 2026": [
        "week of 5 October 2026",
        "wk of 5 Oct 2026",
        "w/c 5 October 2026",
        "week commencing 5 October 2026",
        "wc 5 Oct 2026",
        "w.c. 5 Oct 2026",
        "WC 5 Oct 2026",
        "week commencing Monday 5 October 2026",
    ],
    "the quarter ending June 2024": [
        "quarter ended June 2024",
        "qtr ending June 2024",
        "QE June 2024",
        "Q/E June 2024",
        "3 months ended June 2024",
        "quarter ended 30 June 2024",
    ],
    "the year ended March 2024": [
        "year ending March 2024",
        "the year to March 2024",
        "12 months ended March 2024",
        "YE March 2024",
        "FYE March 2024",
        "12M to March 2024",
        "the 12-month period ended March 2024",
    ],
    "the six months to June 2024": [
        "6 months to June 2024",
        "half-year to June 2024",
        "6M to June 2024",
    ],
    "the week ending 9 October 2026": [
        "week ended 9 October 2026",
        "w/e 9 October 2026",
        "W/E 9 Oct 2026",
        "wk ending 9 Oct 2026",
    ],
}


def _read(text: str) -> tuple[object, object, object] | None:
    found = parse(text, today=TODAY)
    if len(found) != 1 or text[found[0].span[1] :].strip(" .") != "":
        return None
    r = found[0].range
    return (r.start, r.end, r.mod)


@pytest.mark.parametrize(
    ("text", "plain"), [(v, plain) for plain, vs in SPELLINGS.items() for v in vs]
)
def test_a_spelling_reads_as_its_plain_form(text: str, plain: str) -> None:
    want = _read(plain)
    assert want is not None, f"the plain form {plain!r} is not read"
    assert _read(text) == want


@pytest.mark.parametrize(
    ("text", "count"),
    [("starting March 2024", 1), ("the first Monday in months", 0), ("WE 9 Oct 2026", 1)],
)
def test_what_stays_as_it_was(text: str, count: int) -> None:
    assert len(parse(text, today=TODAY)) == count
