"""A period of a fiscal year named from this one -- "Q1 last FY", "June of this fiscal
year", "the last month of the fiscal year", "same quarter last FY" -- is that period.

TODAY is 16 October 2026: on an April year, FY27 runs April 2026 to March 2027 and this is
its third quarter. Each period is written with every way the fiscal year is named from
this one, and held to the dates on the fiscal calendar.

In 2.4.0 most of these were the calendar period with the fiscal year a second, unrelated
date: "Q1 last FY" was January to March 2026 and FY26 apart, "Q4 of this fiscal year"
October to December 2026, "the last quarter of this fiscal year" the same. "June of this
fiscal year", "June last FY" and "June previous year" lost June altogether. "the last
month of the fiscal year" was last month; "the end of the fiscal year" nothing; "the last
day of the fiscal year" yesterday; "same quarter last FY" all of FY26.

The calendar forms stay on the calendar: "Q1 last year" is January to March 2025, "same
half last year" July to December 2025.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from itertools import product

import pytest

from date_wrangler import parse

TODAY = date(2026, 10, 16)

#: The fiscal year, named from this one, and how far from it.
YEARS: dict[str, int] = {
    "this FY": 0, "this fiscal year": 0, "this financial year": 0, "current FY": 0,
    "the current fiscal year": 0, "the fiscal year": 0, "the financial year": 0, "the FY": 0,
    "last FY": -1, "last fiscal year": -1, "the last fiscal year": -1, "previous FY": -1,
    "the previous fiscal year": -1, "prior FY": -1, "the prior financial year": -1,
    "next FY": 1, "next fiscal year": 1, "the following FY": 1, "the coming fiscal year": 1,
}  # fmt: skip


def _month(fy_offset: int, months_in: int) -> date:
    """The first day of the month ``months_in`` months into the fiscal year."""
    m = 4 + months_in
    return date(2026 + fy_offset + (m - 1) // 12, (m - 1) % 12 + 1, 1)


def _span(fy_offset: int, first: int, months: int) -> tuple[date, date]:
    return _month(fy_offset, first), _month(fy_offset, first + months)


def _cases() -> Iterator[tuple[str, tuple[date, date]]]:
    for (year, off), join in product(YEARS.items(), [" ", " of "]):
        bare = not year.startswith("the ")
        if join == " " and not bare:
            continue  # "Q1 the fiscal year" is nobody's
        for q in range(1, 5):
            yield f"Q{q}{join}{year}", _span(off, 3 * (q - 1), 3)
        for h in (1, 2):
            yield f"H{h}{join}{year}", _span(off, 6 * (h - 1), 6)
        yield f"June{join}{year}", _span(off, 2, 1)  # June: the third month of the year
        yield f"March{join}{year}", _span(off, 11, 1)  # March: its last
        if join == " of ":
            yield f"the first quarter of {year}", _span(off, 0, 3)
            yield f"the third quarter of {year}", _span(off, 6, 3)
            yield f"the last quarter of {year}", _span(off, 9, 3)
            yield f"the first half of {year}", _span(off, 0, 6)
            yield f"the second half of {year}", _span(off, 6, 6)
            yield f"the first month of {year}", _span(off, 0, 1)
            yield f"the last month of {year}", _span(off, 11, 1)
            first = _month(off, 0)
            yield f"the first day of {year}", (first, date(first.year, first.month, 2))
            last = _month(off, 12)
            yield f"the last day of {year}", (date(last.year, 3, 31), last)


#: Today's period on the fiscal calendar, a year back.
SAME = {
    "quarter": (date(2025, 10, 1), date(2026, 1, 1)),
    "half": (date(2025, 10, 1), date(2026, 4, 1)),
    "month": (date(2025, 10, 1), date(2025, 11, 1)),
}
SAME_BACK = ["last FY", "previous FY", "of last fiscal year", "of the previous financial year",
             "of the prior FY"]  # fmt: skip


def _same() -> Iterator[tuple[str, tuple[date, date]]]:
    for (unit, want), back in product(SAME.items(), SAME_BACK):
        yield f"same {unit} {back}", want
        yield f"the same {unit} {back}", want
    for unit, want in SAME.items():
        yield f"the same fiscal {unit} last year", want


CASES = dict([*_cases(), *_same()])


@pytest.mark.parametrize("text", list(CASES))
def test_a_period_of_a_fiscal_year_named_from_this_one(text: str) -> None:
    assert [(m.start, m.end) for m in parse(text, today=TODAY)] == [CASES[text]]


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        # On the calendar, as before.
        ("Q1 last year", (date(2025, 1, 1), date(2025, 4, 1))),
        ("Q1 of the previous year", (date(2025, 1, 1), date(2025, 4, 1))),
        ("June previous year", (date(2025, 6, 1), date(2025, 7, 1))),
        ("same half last year", (date(2025, 7, 1), date(2026, 1, 1))),
        ("Q1 of this calendar year", (date(2026, 1, 1), date(2026, 4, 1))),
        # A fiscal year with its number is its own, not this one moved.
        ("Q1 FY27", (date(2026, 4, 1), date(2026, 7, 1))),
        ("June FY25", (date(2024, 6, 1), date(2024, 7, 1))),
        ("the last quarter of FY27", (date(2027, 1, 1), date(2027, 4, 1))),
    ],
)
def test_what_stays_as_it_was(text: str, dates: tuple[date, date]) -> None:
    assert [(m.start, m.end) for m in parse(text, today=TODAY)] == [dates]
