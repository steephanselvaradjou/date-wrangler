"""Months that share a year written once, at the end, are of that year -- and so is each of
two spans compared or listed side by side.

"Jan-Mar 24" is January to March 2024, as "Jan-Mar 2024" and "Jan-Mar'24" are. On its own
"Mar 24" is the 24th, as month_number says, but beside a period with no day of its own a
span from a whole month to the 24th of another is nobody's: the two digits are the year
the span is written with. In 2.4.0 "Jan-Mar 24" ran from January to 24 March of this year,
and "Mar/24" was nothing, though "Mar-24" and "Q3/24" were read.

A span that states a year keeps it beside another that states its own. In 2.4.0 "Jan-Mar
2024 vs Jan-Mar 2025" and "Q1-Q3 2024 vs Q1-Q3 2025" gave the first span's start the 2025,
and the span fell apart. After "compare" a dash or "through" was taken for the comparison:
"compare Jan-Mar 2024 with Jan-Mar 2025" was four dates.

Left alone on purpose, and held to it: "Mar 24" on its own; a partner with a day of its
own -- "Jan 15 - Mar 24", "15 Jan - Mar 24" -- where the 24 is a day too; a month written
first, "Mar 24 to June"; and the month's last day, "Jan-Mar 31", which ends the span where
the month does.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from itertools import product

import pytest

from date_wrangler import MonthNumber, WranglerConfig, parse

TODAY = date(2026, 10, 16)

STYLES = [("Jan", "Mar"), ("January", "March"), ("JAN", "MAR"), ("jan", "mar"), ("Jan.", "Mar.")]
JOINS = ["-", "–", " - ", " to ", " through ", " thru ", " till ", " until "]
#: The year after the last month, every way it is written.
YEARS = [" 24", "'24", " '24", "-24", "/24", " 2024", ", 2024", "-2024"]
Q1_2024 = (date(2024, 1, 1), date(2024, 4, 1))


def _doubled(text: str) -> bool:
    """A month's own full stop with another mark straight after it -- "Mar.-24": nobody's."""
    return any(f".{m}" in text for m in "-/'–") or ".," in text.replace("., ", "")


def _spans() -> Iterator[tuple[str, list[tuple[date, date]]]]:
    for (a, b), join, year in product(STYLES, JOINS, YEARS):
        if _doubled(f"{a}{join}{b}{year}"):
            continue
        yield f"{a}{join}{b}{year}", [Q1_2024]
        if join == " to ":
            yield f"from {a} to {b}{year}", [Q1_2024]
            yield f"between {a} and {b}{year}", [Q1_2024]
    # Across the year's end: November 2023 to February 2024.
    for join, year in product(["-", " to "], [" 24", "'24", " 2024"]):
        yield f"Nov{join}Feb{year}", [(date(2023, 11, 1), date(2024, 3, 1))]
    # A quarter or a year before the month, which has no day either.
    for year in (" 24", " 2024"):
        yield f"Q1 to Mar{year}", [Q1_2024]
        yield f"Q1-Mar{year}", [Q1_2024]


def _months(*months: int, year: int = 2024) -> list[tuple[date, date]]:
    return [(date(year, m, 1), date(year + m // 12, m % 12 + 1, 1)) for m in months]


def _lists() -> Iterator[tuple[str, list[tuple[date, date]]]]:
    for year in (" 24", " 2024", "'24"):
        yield f"Jan, Feb and Mar{year}", _months(1, 2, 3)
        yield f"Jan and Mar{year}", _months(1, 3)
        yield f"Jan & Mar{year}", _months(1, 3)
        yield f"Jan, Feb, Mar{year}", _months(1, 2, 3)
        yield f"Jan vs Mar{year}", _months(1, 3)


Q1_2025 = (date(2025, 1, 1), date(2025, 4, 1))


def _side_by_side() -> Iterator[tuple[str, list[tuple[date, date]]]]:
    """Two spans, each with its year: compared, listed, or after "compare"."""
    sides = [
        ("Jan-Mar 24", "Jan-Mar 25", Q1_2024, Q1_2025),
        ("Jan-Mar 2024", "Jan-Mar 2025", Q1_2024, Q1_2025),
        ("Jan to Mar 2024", "Jan to Mar 2025", Q1_2024, Q1_2025),
        ("Jan through Mar 2024", "Jan through Mar 2025", Q1_2024, Q1_2025),
        ("Q1-Q3 2024", "Q1-Q3 2025", (date(2024, 1, 1), date(2024, 10, 1)),
         (date(2025, 1, 1), date(2025, 10, 1))),
        ("Dec-Feb 2024", "Dec-Feb 2025", (date(2023, 12, 1), date(2024, 3, 1)),
         (date(2024, 12, 1), date(2025, 3, 1))),
    ]  # fmt: skip
    for a, b, wa, wb in sides:
        for frame in ("{a} vs {b}", "{a} compared with {b}", "{a} against {b}", "{a} and {b}",
                      "compare {a} with {b}", "compare {a} to {b}", "compare {a} and {b}",
                      "comparing {a} with {b}"):  # fmt: skip
            if " to " in a and " to " in frame:
                continue  # "compare Jan to Mar 2024 to Jan to Mar 2025": three "to"s, any two
            yield frame.format(a=a, b=b), [wa, wb]
    # A span with no year of its own takes the one written beside it.
    yield "Jan-Mar vs Apr-Jun 2024", [Q1_2024, (date(2024, 4, 1), date(2024, 7, 1))]
    yield "Jan to Mar and Jul to Sep 2024", [Q1_2024, (date(2024, 7, 1), date(2024, 10, 1))]
    yield "compare Jan to Mar 2024 with Apr to Jun 2024", [
        Q1_2024, (date(2024, 4, 1), date(2024, 7, 1))
    ]


CASES = dict([*_spans(), *_lists(), *_side_by_side()])


@pytest.mark.parametrize("text", list(CASES))
def test_months_take_the_year_written_at_the_end(text: str) -> None:
    assert [(m.start, m.end) for m in parse(text, today=TODAY)] == CASES[text]


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        # On its own, the 24th, as month_number says.
        ("Mar 24", [(date(2026, 3, 24), date(2026, 3, 25))]),
        # Beside a day, a day.
        ("Jan 15 - Mar 24", [(date(2026, 1, 15), date(2026, 3, 25))]),
        ("15 Jan - Mar 24", [(date(2026, 1, 15), date(2026, 3, 25))]),
        ("Mar 24 - Jun 24", [(date(2026, 3, 24), date(2026, 6, 25))]),
        # A month written first: the year comes at the end, if anywhere.
        ("Mar 24 to June", [(date(2026, 3, 24), date(2026, 7, 1))]),
        # The month's last day ends the span where the month does.
        ("Jan-Mar 31", [(date(2026, 1, 1), date(2026, 4, 1))]),
        ("Jan-Feb 28", [(date(2026, 1, 1), date(2026, 3, 1))]),
        # An ordinal or a single digit is always a day.
        ("Jan-Mar 24th", [(date(2026, 1, 1), date(2026, 3, 25))]),
        ("Jan-Mar 4", [(date(2026, 1, 1), date(2026, 3, 5))]),
    ],
)
def test_what_stays_as_it_was(text: str, dates: list[tuple[date, date]]) -> None:
    assert [(m.start, m.end) for m in parse(text, today=TODAY)] == dates


def test_month_number_year_reads_every_one_as_a_year() -> None:
    cfg = WranglerConfig(month_number=MonthNumber.YEAR)
    assert [(m.start, m.end) for m in parse("Mar 24", today=TODAY, config=cfg)] == [
        (date(2024, 3, 1), date(2024, 4, 1))
    ]
    assert [(m.start, m.end) for m in parse("Jan-Mar 24", today=TODAY, config=cfg)] == [Q1_2024]
