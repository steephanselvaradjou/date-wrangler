"""A quarter, half or month and its year, joined, are one period.

The scanner reads the label and the year apart -- "Q3", "2024" -- and a later step joins
them. Each rule used to list its own joins, and most were never listed: "Q3 in 2024" was
two periods, "June/2024" two, "2024-Mar" January to March, "2024-3Q" March. So this is a
grid, not a list: every label, each way of joining it, every way of writing the year, in
both orders.

One join is a span, not a label: a spaced dash after the label, the way "June - FY25" is
written for June to the end of FY25. Before the label it is a heading: "FY24 - Q3".
"""

from __future__ import annotations

from datetime import date
from itertools import product

import pytest

from date_wrangler import WranglerConfig, parse

TODAY = date(2026, 10, 16)
CFG = WranglerConfig()

QUARTERS = ["Q3", "Qtr 3", "QTR3", "Quarter 3", "3Q", "third quarter", "3rd qtr"]
HALVES = ["H1", "1H", "Half 1", "first half", "1st half"]
MONTHS = ["June", "Jun", "JUN", "june"]

#: Year as written -> what Q3, H1 and June of it are.
_Q3_2024 = (date(2024, 7, 1), date(2024, 10, 1))
_H1_2024 = (date(2024, 1, 1), date(2024, 7, 1))
_JUNE_2024 = (date(2024, 6, 1), date(2024, 7, 1))
_FY24 = ((date(2023, 10, 1), date(2024, 1, 1)), (date(2023, 4, 1), date(2023, 10, 1)),
         (date(2023, 6, 1), date(2023, 7, 1)))
YEARS = {
    "2024": (_Q3_2024, _H1_2024, _JUNE_2024),
    "CY2024": (_Q3_2024, _H1_2024, _JUNE_2024),
    "FY24": _FY24,
    "FY 2024": _FY24,
    "FY2024-25": ((date(2024, 10, 1), date(2025, 1, 1)), (date(2024, 4, 1), date(2024, 10, 1)),
                  (date(2024, 6, 1), date(2024, 7, 1))),
}
#: Only after the label: these read oddly in front of it.
YEARS_AFTER = {"fiscal 2024": _FY24, "calendar year 2024": YEARS["2024"]}
LABEL_YEAR = [" ", "-", "/", ", ", " of ", " in "]
YEAR_LABEL = [" ", "-", " - ", "/"]


def _which(label: str) -> int:
    return 0 if label in QUARTERS else 1 if label in HALVES else 2


def _wordy(label: str) -> bool:
    return " " in label and not label[-1].isdigit()


def _cases() -> list[tuple[str, tuple[date, date]]]:
    cases = []
    for label in QUARTERS + HALVES + MONTHS:
        for (year, want), join in product({**YEARS, **YEARS_AFTER}.items(), LABEL_YEAR):
            if _wordy(label) and join in ("-", "/"):
                continue
            cases.append((f"{label}{join}{year}", want[_which(label)]))
        for (year, want), join in product(YEARS.items(), YEAR_LABEL):
            if _wordy(label) and join in ("-", "/"):
                continue
            cases.append((f"{year}{join}{label}", want[_which(label)]))
    return cases


@pytest.mark.parametrize(("text", "want"), _cases())
def test_a_label_and_its_year_are_one_period(text: str, want: tuple[date, date]) -> None:
    matches = parse(text, today=TODAY, config=CFG)
    got = [(m.text, m.start, m.end) for m in matches]
    assert got == [(text, *want)]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # "to" makes a span; so does a spaced dash after the label; so does a label with a
        # year of its own.
        ("Q3 to 2024", [(date(2024, 7, 1), date(2025, 1, 1))]),
        ("Q3 - 2024", [(date(2024, 7, 1), date(2025, 1, 1))]),
        ("June - FY25", [(date(2024, 6, 1), date(2025, 4, 1))]),
        ("2023 - H2 2024", [(date(2023, 1, 1), date(2025, 1, 1))]),
        ("2023 - March 2024", [(date(2023, 1, 1), date(2024, 4, 1))]),
        ("Q1 2023 - 2024", [(date(2023, 1, 1), date(2025, 1, 1))]),
        # A comma after the year may list it: left as two.
        ("2023, Q3", [(date(2023, 1, 1), date(2024, 1, 1)), (date(2023, 7, 1), date(2023, 10, 1))]),
        # A full stop after a month's full name ends the sentence.
        ("in March. 2024 was good", [(date(2026, 3, 1), date(2026, 4, 1))]),
        # On a calendar basis "2024-25" is two years, so no one quarter of it.
        ("Q3/2024-25", None),
        # The verb, not the month.
        ("2024 may see growth", []),
    ],
)
def test_what_stays_apart(text: str, want: list[tuple[date, date]] | None) -> None:
    matches = parse(text, today=TODAY, config=CFG)
    if want is None:
        assert not any(m.text == text for m in matches)
        return
    assert [(m.start, m.end) for m in matches] == want
