"""Word lists and the small conversions that go with them.

Two things here are deliberate corrections of the predecessor.

Cardinals and ordinals are separate maps. The old module had a single ``NUMBER_WORDS``
containing ``'twelfth': 12`` -- an ordinal filed among the cardinals -- and no ``'twelve'``
at all, so "twelve months ago" did not parse while "twelfth months ago" would have.

Month lookup is anchored, never substring. The old ``_normalize_month`` asked
``if month_name in text``, which makes ``'may' in 'maybe'`` true and ``'mar' in 'market'``
true. That was only ever held back by word boundaries in a regex several layers away.
:func:`find_month` matches whole words or nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

__all__ = [
    "MONTHS",
    "MONTH_NAMES",
    "WEEKDAYS",
    "WEEKDAY_NAMES",
    "MONTH_DISPLAY",
    "CARDINALS",
    "ORDINALS",
    "UNIT_WORDS",
    "PAST_WORDS",
    "FUTURE_WORDS",
    "CONNECTORS",
    "alt",
    "find_month",
    "word_to_int",
    "ordinal_to_int",
]

MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

#: Longest first, so an alternation prefers "september" over "sep".
MONTH_NAMES: tuple[str, ...] = tuple(sorted(MONTHS, key=len, reverse=True))

#: Month names for output. Hardcoded rather than ``strftime('%B')`` because that honours
#: LC_TIME, so an unrelated ``setlocale`` elsewhere in the host process would change this
#: library's output from "June" to "Juni".
MONTH_DISPLAY: tuple[str, ...] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

CARDINALS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

ORDINALS: dict[str, int] = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
    "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7,
    "eighth": 8, "8th": 8,
    "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
    "eleventh": 11, "11th": 11,
    "twelfth": 12, "12th": 12,
}

#: Weekday names, mapped to ``date.weekday()`` numbering (Monday is 0).
WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

#: Longest first, for the same reason as :data:`MONTH_NAMES`.
WEEKDAY_NAMES: tuple[str, ...] = tuple(sorted(WEEKDAYS, key=len, reverse=True))

#: Words that name a period length, mapped to the Grain value name.
UNIT_WORDS: dict[str, str] = {
    "day": "DAY", "days": "DAY",
    "week": "WEEK", "weeks": "WEEK", "wk": "WEEK", "wks": "WEEK",
    "month": "MONTH", "months": "MONTH", "mo": "MONTH", "mos": "MONTH",
    "quarter": "QUARTER", "quarters": "QUARTER", "qtr": "QUARTER", "qtrs": "QUARTER",
    "half": "HALF", "halves": "HALF",
    "year": "YEAR", "years": "YEAR", "yr": "YEAR", "yrs": "YEAR",
}

PAST_WORDS: frozenset[str] = frozenset(
    {"last", "past", "previous", "prior", "preceding", "trailing", "rolling"}
)
FUTURE_WORDS: frozenset[str] = frozenset({"next", "coming", "following", "upcoming", "forthcoming"})

#: Words that join two periods into one range. The predecessor supported only
#: ``to``/``and``/``-``; everything else silently produced two separate matches that read
#: as garbage. Dashes arrive here already folded to ASCII by :mod:`.normalize`.
CONNECTORS: tuple[str, ...] = (
    "through", "thru", "until", "till", "upto", "up to", "to", "and", "-",
)


def alt(words: Iterable[str]) -> str:
    """A non-capturing regex alternation over ``words``, longest first.

    Sorting by length matters: an alternation is leftmost-first, so without it ``sep``
    would match inside ``september`` and leave ``tember`` behind.
    """
    items = sorted(words, key=len, reverse=True)
    return "(?:" + "|".join(re.escape(w) for w in items) + ")"


_MONTH_RE = re.compile(rf"\b{alt(MONTH_NAMES)}\b", re.IGNORECASE)


def find_month(text: str) -> int | None:
    """The month named in ``text``, matched as a whole word. None if there is none."""
    m = _MONTH_RE.search(text)
    return MONTHS[m.group(0).lower()] if m else None


def word_to_int(text: str) -> int | None:
    """A cardinal count: ``"three"`` or ``"3"``."""
    low = text.strip().lower()
    if low in CARDINALS:
        return CARDINALS[low]
    if low.isdigit():
        return int(low)
    return None


def ordinal_to_int(text: str) -> int | None:
    """An ordinal position: ``"third"``, ``"3rd"`` or a bare ``"3"``."""
    low = text.strip().lower()
    if low in ORDINALS:
        return ORDINALS[low]
    stripped = re.sub(r"(?<=\d)(?:st|nd|rd|th)$", "", low)
    if stripped.isdigit():
        return int(stripped)
    return None
