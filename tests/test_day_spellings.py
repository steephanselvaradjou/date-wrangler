"""A day with its month and year, written every way people write it, is that day.

A grid rather than a list, for the same reason as test_label_years.py: each rule used to
list its own spellings, and the combinations nobody listed were read wrong -- the day lost
("15-Mar 2024" was March), the 15 taken for a year ("Mar-15 2024" was March 2015), or a
span made of a mark ("15 Mar-2024" ran to the end of the year).

In 2.3.0 about seven in ten of these read as the day. Among the rest, a two-digit year was
dropped whenever the mark changed -- "15 Mar-24", "15-March 24", "15/March.24" were this
year's 15 March -- as it was after "of" ("15 of March 24"); "15 of March24" was the 24th
of this March; "15thMarch 2024" and "March/15/2024" were nothing; "March/15th, 2024" was
three dates.

Left alone on purpose, and held to it: a two-digit year after a comma -- "on 15 Mar, 24
orders shipped" is a count -- and an abbreviated month with full stops and a two-digit
year, "Mar.15.24", which is "Mar 15.24" once the month's own full stop is read: a decimal
as often. Each is the day without its year. Shapes nobody writes, a doubled mark like
"Mar..2024", are not here.
"""

from __future__ import annotations

from datetime import date
from itertools import product

import pytest

from date_wrangler import parse

TODAY = date(2026, 10, 16)
MONTHS = {
    3: ["March", "Mar", "MAR", "mar", "Mar.", "MARCH", "march"],
    9: ["September", "Sep", "Sept", "Sept.", "SEP", "Sep."],
}
FULL_NAMES = {"march", "september"}
DAYS = ["15", "15th"]
DAY_MONTH = [" ", "-", "/", ".", "", " of "]
MONTH_YEAR = [" ", ", ", ",", "-", "/", ".", "", "'", " '"]
MONTH_DAY = [" ", "-", "/", ".", ""]
DAY_YEAR = [" ", ", ", ",", "-", "/", "."]
YEAR_MONTH_DAY = [" ", "-", "/", ".", ""]


def _doubled(text: str) -> bool:
    """A month's own full stop with another mark straight after it: nobody's."""
    return any(f".{m}" in text for m in ".-/'") or ".," in text.replace("., ", "")


def _spellings() -> dict[str, tuple[int, bool]]:
    """Each spelling -> (its month, whether its year is read)."""
    out: dict[str, tuple[int, bool]] = {}
    for month, names in MONTHS.items():
        for day, s1, name, s2, year in product(DAYS, DAY_MONTH, names, MONTH_YEAR, ["2024", "24"]):
            if s2 in ("'", " '") and year != "24":
                continue
            text = f"{day}{s1}{name}{s2}{year}"
            if not _doubled(text):
                out[text] = (month, not (year == "24" and s2 in (", ", ",")))
        for name, s1, day, s2, year in product(names, MONTH_DAY, DAYS, DAY_YEAR, ["2024", "24"]):
            if year == "24" and not (s1 == s2 and s1 in "-/."):
                continue  # month first, a two-digit year only with one mark throughout
            text = f"{name}{s1}{day}{s2}{year}"
            if not _doubled(text):
                short = name.rstrip(".").lower() not in FULL_NAMES
                out[text] = (month, not (year == "24" and s1 == "." and short))
        for s, name, day in product(YEAR_MONTH_DAY, names, DAYS):
            text = f"2024{s}{name}{s}{day}"
            if not _doubled(text):
                out[text] = (month, True)
    return out


SPELLINGS = _spellings()


@pytest.mark.parametrize("text", [t for t, (_, read) in SPELLINGS.items() if read])
def test_every_spelling_is_the_day(text: str) -> None:
    month = SPELLINGS[text][0]
    got = [(m.start, m.end) for m in parse(text, today=TODAY)]
    assert got == [(date(2024, month, 15), date(2024, month, 16))]


@pytest.mark.parametrize("text", [t for t, (_, read) in SPELLINGS.items() if not read])
def test_a_year_left_alone_on_purpose_leaves_the_day(text: str) -> None:
    month = SPELLINGS[text][0]
    got = [(m.start, m.end) for m in parse(text, today=TODAY)]
    assert got == [(date(2026, month, 15), date(2026, month, 16))]


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        # A two-digit number that is not a year stays what it is.
        ("on 15 Mar, 24 orders shipped", [("2026-03-15", "2026-03-16")]),
        ("the 1st of March 24 people came", [("2026-03-01", "2026-03-02")]),
        ("15 Mar 24:00", [("2026-03-15", "2026-03-16")]),
        # A mark between two days, or a day and a month, is still a span.
        ("March 15-24", [("2026-03-15", "2026-03-25")]),
        ("15 Mar-24 Apr", [("2026-03-15", "2026-04-25")]),
        ("1 Mar-15 Mar", [("2026-03-01", "2026-03-16")]),
        # A full month name and a full stop ends a sentence.
        ("in March. 2024 was good", [("2026-03-01", "2026-04-01")]),
    ],
)
def test_what_stays_as_it_was(text: str, dates: list[tuple[str, str]]) -> None:
    assert [(str(m.start), str(m.end)) for m in parse(text, today=TODAY)] == dates
