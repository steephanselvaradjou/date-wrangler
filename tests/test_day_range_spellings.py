"""Days that share a month or a year, written every way people write them, are those days.

A run inside one month -- "15-20 March 2024" -- a run across two that shares the year --
"15 March - 20 April 2024" -- one across the year's end, and a list, "15, 16 and 20 March
2024". Each part of the spelling is varied against the others: the joining word, the days,
what joins them to the month, the month's name, and the year however it follows.

The runs and lists were patterns of their own, and read the year with fewer spellings than
a single day. In 2.3.0 under half of these read as written. Among the rest, "15-20 Mar-24",
"15-20 MAR 24" and "15-20 March.2024" were this year's March; "15-20 June FY25" was June
2025, the FY dropped; "15-20March 2024" was the 20th alone; "2024 March 15-20" the 15th, and
"2024 March 15th-20th" a run to the 20th of this month; "between the 15th and the 20th of
March" the 20th. Across two months, "15 March 2024-20 April 2024" lost the first year;
"15-March-20-April-2024" and "15 of March-20 of April 2024" began in March 2020; "15 Mar. -
20 Apr. 2024" and "Mar.-Apr. 2024" lost the first month to its full stop. In a list, "15,
16 and 20 March-2024" ran the 20th to the end of 2024.

Left alone on purpose, and held to it: a two-digit year after a comma at the end -- "on 15
Mar, 24 orders shipped" is a count -- reads the days without the year, as for one day.
Before the next date it is a year, as no count can be: "15 Mar, 24 - 20 Apr, 24".
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from itertools import product

import pytest

from date_wrangler import parse

TODAY = date(2026, 10, 16)

#: The month's name every way it is written, and the same style in the months beside it.
STYLES = ["{}", "{:.3}", "{:.3}.", "{!u}", "{:.3!u}", "{!l}"]
SEPT = ["September", "Sept", "Sept.", "Sep", "SEP"]


def _name(month: str, style: str) -> str:
    upper, lower = "!u" in style, "!l" in style
    name = style.replace("!u", "").replace("!l", "").format(month)
    return name.upper() if upper else name.lower() if lower else name


DAYS = [("15", "20"), ("15th", "20th"), ("15", "20th"), ("15th", "20")]
JOINS = ["-", " - ", "–", " – ", " to ", " till ", " until ", " through ", " thru "]
LEADS = ["", "from ", "between "]
DAY_MONTH = [" ", " of ", ""]
#: A year after the month, with the year it is.
YEARS = [
    *((f"{mark}2024", 2024) for mark in [" ", ", ", ",", "-", "/", ".", ""]),
    *((f"{mark}24", 2024) for mark in [" ", "-", "/", ".", "", "'", " '"]),
    ("", 2026),
]


def _doubled(text: str) -> bool:
    """A month's own full stop with another mark straight after it: nobody's."""
    return any(f".{m}" in text for m in ".-/'") or ".," in text.replace("., ", "")


def _joined(lead: str, join: str) -> str | None:
    """The join as "between" has it: "and" for "to"."""
    if lead != "between ":
        return join
    return " and " if join == " to " else None


def _run(year: int, month: int) -> list[tuple[date, date]]:
    return [(date(year, month, 15), date(year, month, 21))]


def _in_one_month() -> Iterator[tuple[str, list[tuple[date, date]]]]:
    # The joining word, the days and a word in front, with the month as written plainly.
    for (d1, d2), join, lead in product(DAYS, [*JOINS, " to the "], LEADS):
        j = _joined(lead, join) if join != " to the " else " to the "
        if j is None or (join == " to the " and not d2[-1].isalpha()):
            continue
        for tail, year in (("March 2024", 2024), ("Mar-24", 2024), ("March", 2026)):
            yield f"{lead}{d1}{j}{d2} {tail}", _run(year, 3)
        yield f"{lead}the {d1}{j}{d2} of March 2024", _run(2024, 3)
    # What joins the days to the month, the month's name, and the year however it follows.
    for days, dm, style, (tail, year) in product(DAYS[:2], DAY_MONTH, STYLES, YEARS):
        text = f"{days[0]}-{days[1]}{dm}{_name('March', style)}{tail}"
        if not _doubled(text):
            yield text, _run(year, 3)
    for days, dm, name, (tail, year) in product(DAYS[:2], DAY_MONTH, SEPT, YEARS):
        text = f"{days[0]}-{days[1]}{dm}{name}{tail}"
        if not _doubled(text):
            yield text, _run(year, 9)
    # The month first.
    for style, (d1, d2), join, lead in product(STYLES, DAYS, JOINS, LEADS):
        j = _joined(lead, join)
        if j is None:
            continue
        name = _name("March", style)
        for tail, year in ((", 2024", 2024), (" 2024", 2024), (",2024", 2024), (" '24", 2024),
                           ("", 2026)):  # fmt: skip
            text = f"{lead}{name} {d1}{j}{d2}{tail}"
            if not _doubled(text):
                yield text, _run(year, 3)
    yield "March the 15th to the 20th, 2024", _run(2024, 3)
    # The year first.
    for style, (d1, d2), join in product(STYLES, DAYS, JOINS):
        yield f"2024 {_name('March', style)} {d1}{join}{d2}", _run(2024, 3)
    # A fiscal year: the month inside it. FY24 runs April 2023 to March 2024, so its March
    # is March 2024, and FY25's September is September 2024.
    for month, fys in ((3, ["FY24", "FY 24", "FY2023-24", "FY-24", "fiscal 2024"]),
                       (9, ["FY25", "FY 25", "FY2024-25", "FY-25", "fiscal 2025"])):  # fmt: skip
        name = "March" if month == 3 else "Sept"
        for fy, mark, join in product(fys, [" ", ", ", " of "], ["-", " to "]):
            yield f"15{join}20 {name}{mark}{fy}", _run(2024, month)
            if mark != " of ":
                yield f"{name} 15{join}20{mark}{fy}", _run(2024, month)


#: Across two months, and across a year's end: 15 March to 20 April 2024, and 15 December
#: 2023 to 20 January 2024.
ACROSS = [("March", "April", 2024, 2024), ("December", "January", 2023, 2024)]


def _across() -> Iterator[tuple[str, list[tuple[date, date]]]]:
    for m1, m2, y1, y2 in ACROSS:
        n1, n2 = (date(2000, 1, 1).replace(month=_MONTH[m]) for m in (m1, m2))
        want = [(date(y1, n1.month, 15), date(y2, n2.month, 21))]
        # The joining word and the word in front, the year written once.
        for (d1, d2), join, lead in product(DAYS, JOINS, LEADS):
            j = _joined(lead, join)
            if j is not None:
                yield f"{lead}{d1} {m1}{j}{d2} {m2} {y2}", want
                yield f"{lead}{m1} {d1}{j}{m2} {d2}, {y2}", want
        # The month's name, what joins the day to it, and the year -- once, or on both.
        for style, dm, join in product(STYLES, [" ", " of ", "-"], ["-", " - ", " to "]):
            a, b = _name(m1, style), _name(m2, style)
            for mark, digits in ((" ", 4), (", ", 4), ("-", 4), (" ", 2), ("-", 2), ("'", 2),
                                 (", ", 2)):  # fmt: skip
                if dm == "-" and mark != "-" or dm == " of " and mark in ("-", "'"):
                    continue
                ya, yb = str(y1)[-digits:], str(y2)[-digits:]
                for both in (False, True):
                    if mark == ", " and digits == 2 and not both:
                        continue  # at the end, a count: below
                    first = f"15{dm}{a}" + (f"{mark}{ya}" if both else "")
                    text = f"{first}{join}20{dm}{b}{mark}{yb}"
                    if not _doubled(text):
                        yield text, want
        for style, join in product(STYLES, ["-", " - ", " to "]):
            a, b = _name(m1, style), _name(m2, style)
            for first in (f"{a} 15", f"{a} 15, {y1}"):
                text = f"{first}{join}{b} 20, {y2}"
                if not _doubled(text):
                    yield text, want


_MONTH = {"March": 3, "April": 4, "December": 12, "January": 1}


def _days(year: int, month: int, *days: int) -> list[tuple[date, date]]:
    return [(date(year, month, d), date(year, month, d) + timedelta(days=1)) for d in days]


LIST_JOINS = [(", ", " and "), (", ", ", and "), (", ", " & "), (" & ", " & "), (",", " and ")]


def _lists() -> Iterator[tuple[str, list[tuple[date, date]]]]:
    for (s1, s2), ords, style in product(LIST_JOINS, (False, True), STYLES):
        d = ["15th", "16th", "20th"] if ords else ["15", "16", "20"]
        name = _name("March", style)
        for dm, (tail, year) in product((" ", " of "), YEARS):
            text = f"{d[0]}{s1}{d[1]}{s2}{d[2]}{dm}{name}{tail}"
            if not _doubled(text):
                yield text, _days(year, 3, 15, 16, 20)
        for tail, year in ((", 2024", 2024), (" 2024", 2024), ("", 2026)):
            yield f"{name} {d[0]}{s1}{d[1]}{s2}{d[2]}{tail}", _days(year, 3, 15, 16, 20)
    for fy, (s1, s2) in product(["FY25", "FY 25", "FY2024-25", "FY-25", "fiscal 2025"], LIST_JOINS):
        yield f"15{s1}16{s2}20 Sept {fy}", _days(2024, 9, 15, 16, 20)
        yield f"Sept 15{s1}16{s2}20 {fy}", _days(2024, 9, 15, 16, 20)


CASES = dict([*_in_one_month(), *_across(), *_lists()])


@pytest.mark.parametrize("text", list(CASES))
def test_every_spelling_is_those_days(text: str) -> None:
    got = [(m.start, m.end) for m in parse(text, today=TODAY)]
    assert got == CASES[text]


#: A two-digit year after a comma at the end is a count, as for one day: the days are read,
#: this year's, and the number left.
LEFT: dict[str, list[tuple[date, date]]] = {
    **{f"15-20 {_name('March', s)}, 24": _run(2026, 3) for s in STYLES},
    **{f"15th to the 20th of {_name('March', s)},24": _run(2026, 3) for s in STYLES},
    **{f"15, 16 and 20 {_name('March', s)}, 24": _days(2026, 3, 15, 16, 20) for s in STYLES},
    **{
        f"15 {_name('March', s)}{j}20 {_name('April', s)}, 24": [
            (date(2026, 3, 15), date(2026, 4, 21))
        ]
        for s in STYLES
        for j in ("-", " - ", " to ")
        if not _doubled(f"{_name('March', s)}{j}")
    },
}


@pytest.mark.parametrize("text", list(LEFT))
def test_a_year_left_alone_on_purpose_leaves_the_days(text: str) -> None:
    assert [(m.start, m.end) for m in parse(text, today=TODAY)] == LEFT[text]


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        # A run that ends before it starts is refused, not read as one of its ends.
        ("15-1 March 2024", []),
        ("March 15-1, 2024", []),
        # A number after a month and a comma is a count, not a listed day.
        ("on 15 Mar, 24 and 25 more", [("2026-03-15", "2026-03-16")]),
        # A bare last number is a count unless a year, a stop or the end follows.
        ("March 1 and 15 customers", [("2026-03-01", "2026-03-02")]),
        # Into another month, a range; and a date going on is a date of its own.
        ("March 15 to 20 April", [("2026-03-15", "2026-04-21")]),
        ("March 1 to 03/04/2024", [("2024-03-01", "2024-04-04")]),
        # A year and then a comma lists it.
        ("2024, March 15-20", [("2024-01-01", "2025-01-01"), ("2024-03-15", "2024-03-21")]),
        # A dash touching an abbreviated month's full stop joins its year; a full stop and a
        # capital end the sentence.
        ("Mar.-2024", [("2024-03-01", "2024-04-01")]),
        ("Mar.-Apr. 2024", [("2024-03-01", "2024-05-01")]),
        ("in Mar. To be fair, 2024 was odd", [("2026-03-01", "2026-04-01")]),
        ("Jan., Feb. and Mar. 2024", [("2024-01-01", "2024-02-01"), ("2024-02-01", "2024-03-01"),
                                      ("2024-03-01", "2024-04-01")]),  # fmt: skip
    ],
)
def test_what_stays_as_it_was(text: str, dates: list[tuple[str, str]]) -> None:
    assert [(str(m.start), str(m.end)) for m in parse(text, today=TODAY)] == dates
