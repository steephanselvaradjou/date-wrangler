"""15 March 2024, written every way people write it, is 15 March 2024.

A grid rather than a list, for the same reason as test_label_years.py: each rule used to
list its own spellings, and the combinations nobody listed were read wrong -- the day lost
("15-Mar 2024" was March), the 15 taken for a year ("Mar-15 2024" was March 2015), or a
span made of a mark ("15 Mar-2024" ran to the end of the year).
"""

from __future__ import annotations

from datetime import date
from itertools import product

import pytest

from date_wrangler import parse

TODAY = date(2026, 10, 16)
WANT = (date(2024, 3, 15), date(2024, 3, 16))

DAYS = ["15", "15th"]
MONTHS = ["March", "Mar", "MAR", "mar"]


def _cases() -> list[str]:
    out: set[str] = set()
    for d, m in product(DAYS, MONTHS):
        for mark in ("-", "/", "."):
            out.add(f"{d}{mark}{m}{mark}2024")  # one mark throughout
            for tail in (" 2024", ", 2024"):
                out.add(f"{d}{mark}{m}{tail}")  # the year after a space or a comma
        for mid, tail in product((" ", ""), (" 2024", ", 2024", "-2024", "/2024", "2024")):
            if mid == "" and d.endswith("th"):
                continue  # "15thMarch 2024" is nobody's
            out.add(f"{d}{mid}{m}{tail}")
        out.add(f"{d} {m} '24")
        out.add(f"{d}-{m}-24")
        out.add(f"{d} of {m} 2024")
        out.add(f"{d} of {m}, 2024")
        # month first
        for tail in (" 2024", ", 2024", "-2024", "/2024"):
            out.add(f"{m} {d}{tail}")
        out.add(f"{m}-{d}-2024")
        if d == "15":
            out.add(f"{m}-{d} 2024")
            out.add(f"{m}-{d}, 2024")
        # year first
        for mark in (" ", "-", "/", "."):
            out.add(f"2024{mark}{m}{mark}{d}")
    return sorted(out)


@pytest.mark.parametrize("text", _cases())
def test_every_spelling_is_the_day(text: str) -> None:
    got = [(m.text, m.start, m.end) for m in parse(text, today=TODAY)]
    assert got == [(text, *WANT)]
