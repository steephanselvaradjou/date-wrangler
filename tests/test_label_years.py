"""A quarter or half and its year, however they are joined, are one period.

The scanner reads the label and the year apart -- "Q3", "2024" -- and a later step joins
them. Each rule used to list its own joins, and most were never listed: "Q3 in 2024" was
two periods, "Q3 - 2024" a span from this year's Q3 to the whole of 2024, "2024-3Q" March.
So this is a grid, not a list: every label, each way of joining, every way of writing the
year, in both orders.
"""

from __future__ import annotations

from datetime import date
from itertools import product

import pytest

from date_wrangler import WranglerConfig, parse

TODAY = date(2026, 10, 16)
CFG = WranglerConfig()

#: Label -> (index, kind); "wordy" labels need a space either side.
QUARTERS = ["Q3", "Qtr 3", "QTR3", "Quarter 3", "3Q", "third quarter", "3rd qtr"]
HALVES = ["H1", "1H", "Half 1", "first half", "1st half"]

#: Year as written -> the year it names, for a quarter (Q3) and a half (H1).
YEARS = {
    "2024": ((date(2024, 7, 1), date(2024, 10, 1)), (date(2024, 1, 1), date(2024, 7, 1))),
    "CY2024": ((date(2024, 7, 1), date(2024, 10, 1)), (date(2024, 1, 1), date(2024, 7, 1))),
    "FY24": ((date(2023, 10, 1), date(2024, 1, 1)), (date(2023, 4, 1), date(2023, 10, 1))),
    "FY 2024": ((date(2023, 10, 1), date(2024, 1, 1)), (date(2023, 4, 1), date(2023, 10, 1))),
    "FY2024-25": ((date(2024, 10, 1), date(2025, 1, 1)), (date(2024, 4, 1), date(2024, 10, 1))),
}
#: Only after the label: these read oddly in front of it.
YEARS_AFTER = {
    "fiscal 2024": YEARS["FY24"],
    "calendar year 2024": YEARS["2024"],
}
LABEL_YEAR = [" ", "-", " - ", "/", ", ", " of ", " in "]
YEAR_LABEL = [" ", "-", " - ", "/"]


def _wordy(label: str) -> bool:
    return " " in label and not label[-1].isdigit()


def _cases() -> list[tuple[str, tuple[date, date]]]:
    cases = []
    for label in QUARTERS + HALVES:
        which = 0 if label in QUARTERS else 1
        for (year, want), join in product({**YEARS, **YEARS_AFTER}.items(), LABEL_YEAR):
            if _wordy(label) and join in ("-", "/"):
                continue
            cases.append((f"{label}{join}{year}", want[which]))
        for (year, want), join in product(YEARS.items(), YEAR_LABEL):
            if _wordy(label) and join in ("-", "/"):
                continue
            cases.append((f"{year}{join}{label}", want[which]))
    return cases


@pytest.mark.parametrize(("text", "want"), _cases())
def test_a_label_and_its_year_are_one_period(text: str, want: tuple[date, date]) -> None:
    matches = parse(text, today=TODAY, config=CFG)
    got = [(m.text, m.start, m.end) for m in matches]
    assert got == [(text, *want)]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # "to" makes a span, as does a label with a year of its own.
        ("Q3 to 2024", [(date(2024, 7, 1), date(2025, 1, 1))]),
        ("2023 - H2 2024", [(date(2023, 1, 1), date(2025, 1, 1))]),
        ("Q1 2023 - 2024", [(date(2023, 1, 1), date(2025, 1, 1))]),
        # A comma after the year may list it: left as two.
        ("2023, Q3", [(date(2023, 1, 1), date(2024, 1, 1)), (date(2023, 7, 1), date(2023, 10, 1))]),
        # On a calendar basis "2024-25" is two years, so no one quarter of it.
        ("Q3 - 2024-25", None),
    ],
)
def test_what_stays_apart(text: str, want: list[tuple[date, date]] | None) -> None:
    matches = parse(text, today=TODAY, config=CFG)
    if want is None:
        assert not any(m.text == text for m in matches)
        return
    assert [(m.start, m.end) for m in matches] == want
