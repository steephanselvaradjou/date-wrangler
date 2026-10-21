"""A date written in numbers reads the same however a person or a system writes it.

Each spelling is held to its plain form -- start, end and modifier -- with any time of day
or punctuation around it allowed. In 2.1.0 about nine in ten read as their plain form;
among the rest, "2024 week 11" was week 11 of this year, "week number 11 of 2024" the
11th of January, "W11-2024" a span from the week to the end of 2024, "2024-wk11" one
from the start of 2024 to the week, and "week 11-12" week 11 alone.

Left alone on purpose: a run of digits with nothing to set it apart ("20240315", as likely
an order number), parts joined by underscores ("sales_2024" is a table), a month with a
dot ("2024.03" is as likely an amount), statistics notation ("2024M03"), and ordinal weeks
past the fifth ("the 11th week": week 11, or the 11th seven days?).
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import parse

TODAY = date(2026, 10, 16)

SPELLINGS: dict[str, list[str]] = {
    "2024-03-15": [
        "2024-3-15",
        "2024/03/15",
        "2024/3/15",
        "2024.03.15",
        "2024.3.15",
        "2024‑03‑15",
        "2024–03–15",
        "2024-03-15T14:30",
        "2024-03-15T14:30:00Z",
        "2024-03-15T14:30:00.123Z",
        "2024-03-15T14:30:00.123456",
        "2024-03-15T14:30:00+05:30",
        "2024-03-15T14:30:00-05:00",
        "2024-03-15 14:30:00",
        "2024-03-15 00:00:00+00",
        "2024-03-15 14:30:00 UTC",
        "2024-03-15 2:30 PM",
        "'2024-03-15'",
        "DATE '2024-03-15'",
        "(2024-03-15)",
        "on 2024-03-15",
        "dt=2024-03-15",
        "date: 2024-03-15",
        "report-2024-03-15",
        "/2024/03/15/",
        "15/03/2024",
        "15.03.2024",
        "15-03-2024",
        "15/3/2024",
        "15/03/24",
        "15.03.24",
        "3/15/2024",
        "03-15-2024",
        "3.15.2024",
        "03/15/24",
        "15/03/2024 14:30",
        "15.03.2024 14:30",
        "15‑03‑2024",
    ],
    "2024-03": [
        "2024/03",
        "2024-3",
        "2024/3",
        "03/2024",
        "3/2024",
        "03-2024",
        "3-2024",
        "'2024-03'",
        "dt=2024-03",
        "in 2024-03",
        "2024‑03",
    ],
    "2024-W11": [
        "2024W11",
        "2024-w11",
        "2024 W11",
        "2024-wk11",
        "2024wk11",
        "2024 week 11",
        "W11 2024",
        "W11-2024",
        "W11/2024",
        "W11 '24",
        "week 11 2024",
        "week 11, 2024",
        "week 11 of 2024",
        "week 11-2024",
        "wk 11 2024",
        "wk. 11 2024",
        "week #11 2024",
        "week no. 11 2024",
        "week number 11 of 2024",
        "CW11 2024",
        "calendar week 11 2024",
    ],
    "2024-W11-5": ["2024W115", "2024-w11-5", "2024W11-5"],
    "week 11 to week 12 2024": [
        "weeks 11-12 2024",
        "week 11-12 2024",
        "weeks 11 to 12 2024",
        "wk 11-12 2024",
        "weeks 11-12 of 2024",
        "week 11-12, 2024",
        "W11-W12 2024",
    ],
    "2024": [
        "in 2024",
        "year 2024",
        "the year 2024",
        "Year: 2024",
        "yr 2024",
        "CY2024",
        "CY24",
        "calendar year 2024",
        "in '24",
        "in ’24",
        "year=2024",
        "in the year 2024",
        "during 2024",
        "throughout 2024",
        "for 2024",
    ],
    "2024-25": [
        "2024/25",
        "2024-2025",
        "2024/2025",
        "2024–25",
        "2024—2025",
        "2024 to 2025",
        "from 2024 to 2025",
        "between 2024 and 2025",
        "2024 thru 2025",
    ],
}

#: Numbers that are not dates, whatever digits they hold.
NOT_DATES = [
    "1710460800",
    "version 1.5.3",
    "rated 4.5/5",
    "3/5 stars",
    "1/2 cup",
    "24/7",
    "score 3-1",
    "pages 10-15",
    "call 555-1234",
    "ISBN 978-3-16-148410-0",
    "+1-202-555-0143",
    "we have 2024 customers",
    "order 20240315",
    "SKU 2024-0315",
    "ratio 16:9",
    "2024 units sold",
]


def _read(text: str) -> tuple[object, object, object] | None:
    found = parse(text, today=TODAY)
    if len(found) != 1:
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


@pytest.mark.parametrize("text", NOT_DATES)
def test_a_number_that_is_not_a_date_stays_a_number(text: str) -> None:
    assert parse(text, today=TODAY) == []


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("20240315", 0),
        ("sales_2024", 0),
        ("total 2024.03", 0),
        ("2024, week 11", 2),
        ("week 11 to week 12", 1),
        ("revenue (2024)", 1),
    ],
)
def test_what_stays_as_it_was(text: str, count: int) -> None:
    assert len(parse(text, today=TODAY)) == count
