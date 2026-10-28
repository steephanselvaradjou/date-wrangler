"""Fold typographic variants to ASCII, keeping a map back to the original string.

Editors rewrite ``-`` as an en dash and ``'24`` with a curly apostrophe; spreadsheets paste
non-breaking spaces. None of that should decide whether a date parses.

Matches report spans into the caller's string, so every produced character records where it
came from. Normalisation is per-character rather than whole-string NFKC: that keeps the
offset map exact, at the cost of not composing accents, which no date needs.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .vocab import MONTHS, UNIT_WORDS, alt

__all__ = ["Normalized", "normalize"]

#: Characters folded to an ASCII equivalent before matching. NFKC handles fullwidth forms
#: and most spaces on its own; these are the ones it deliberately leaves alone because they
#: are semantically distinct in general text but interchangeable in a date.
_FOLD = {
    "‐": "-",  # hyphen
    "‑": "-",  # non-breaking hyphen
    "‒": "-",  # figure dash
    "–": "-",  # en dash        "Q1 – Q2"
    "—": "-",  # em dash        "Q1 — Q2"
    "―": "-",  # horizontal bar
    "−": "-",  # minus sign
    "‘": "'",  # left single quote   "q1 ‘24"
    "’": "'",  # right single quote  "q1 ’24"
    "‛": "'",
    " ": " ",  # non-breaking space
    " ": " ",  # narrow no-break space
    " ": " ",  # figure space
    # Zero-width characters map to nothing at all; they carry no meaning here and would
    # otherwise break word boundaries in the middle of a month name.
    "​": "",
    "‌": "",
    "‍": "",
    "﻿": "",
}

#: The full stop of an abbreviated month before a number: "Sept. 2013", "Mar. 15",
#: "FEB.2018". It becomes a space -- the same length, so every offset still holds -- and
#: only before a number, so a month that ends a sentence keeps its full stop. Or before
#: what joins it to the next date -- a comma, "&", a spaced dash, or a word in lower case,
#: as no sentence opens: "Mar. - Apr. 2024", "15 of Mar. to 20 of Apr.". REGRESSION: the
#: full stop parted them, and the first was this year's or lost. A dash touching it only
#: before the next month or a day of it -- "Mar.-Apr.", "15 of Mar.-20 of Apr." -- since
#: "Mar.-2024" is March 2024, and spaced it would be a span.
_ABBREV_DOT = re.compile(
    r"\b(jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec)\."
    r"(?=\s*,?\s*'?\d|\s*[,&]|\s+-|\s+(?-i:to|till|until|through|thru|and|or|vs|versus)\b"
    rf"|-(?:\d{{1,2}}(?:st|nd|rd|th)?[\s-]*(?:of\s+)?)?{alt(MONTHS)}\b)",
    re.IGNORECASE,
)


def _undot(text: str) -> str:
    return _ABBREV_DOT.sub(lambda m: m.group(1) + " ", text) if "." in text else text


#: A count written against its unit -- "3months", "5days", "2wks" -- or joined to it by a
#: hyphen, as an adjective: "12-month", "3-day". Read as if spaced, so every rule that
#: counts reads them ("last 3months", "3mos ago", "trailing 12-month") without each one
#: spelling out the joint. Not "half": "2half" is nobody's, and "2 half" would be H2.
_COUNT_UNIT = re.compile(
    r"\b(\d{1,4})(-?)(?=(?:"
    + "|".join(sorted(
        [w for w in UNIT_WORDS if w not in ("half", "halves")]
        + ["hours", "hour", "hrs", "hr", "minutes", "minute", "mins", "min",
           "seconds", "second", "secs", "sec"],
        key=len, reverse=True,
    ))
    + r")\b)",
    re.IGNORECASE,
)


def _space_counts(
    text: str, starts: list[int], ends: list[int]
) -> tuple[str, list[int], list[int]]:
    """``text`` with a space between each count and its unit, and the offset map to match.
    A hyphen becomes the space and keeps its place; a space put in where there was none
    stands for no character of the original."""
    pieces: list[str] = []
    new_starts: list[int] = []
    new_ends: list[int] = []
    pos = 0
    for m in _COUNT_UNIT.finditer(text):
        cut = m.end(1)
        pieces.append(text[pos:cut])
        new_starts += starts[pos:cut]
        new_ends += ends[pos:cut]
        pieces.append(" ")
        if m.group(2):
            new_starts.append(starts[cut])
            new_ends.append(ends[cut])
            pos = cut + 1
        else:
            new_starts.append(starts[cut])
            new_ends.append(starts[cut])
            pos = cut
    pieces.append(text[pos:])
    new_starts += starts[pos:]
    new_ends += ends[pos:]
    return "".join(pieces), new_starts, new_ends


@dataclass(frozen=True, slots=True)
class Normalized:
    """Cleaned text plus the index map back to what the caller supplied.

    ``_starts`` is ``None`` for text that needed no rewriting, which is the overwhelming
    majority of real input. Spans then map through unchanged and no per-character tables
    are built at all.
    """

    text: str
    original: str
    _starts: tuple[int, ...] | None
    _ends: tuple[int, ...] | None

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """Translate a span in :attr:`text` to the equivalent span in :attr:`original`."""
        if self._starts is None or self._ends is None:
            return (start, end)
        if not self._starts:
            return (0, 0)
        start = max(0, min(start, len(self._starts)))
        end = max(start, min(end, len(self._starts)))
        if start >= len(self._starts):
            return (len(self.original), len(self.original))
        lo = self._starts[start]
        hi = self._ends[end - 1] if end > start else lo
        return (lo, hi)

    def slice_original(self, start: int, end: int) -> str:
        """The original text underlying a span in :attr:`text`."""
        lo, hi = self.to_original(start, end)
        return self.original[lo:hi]


def normalize(text: str) -> Normalized:
    """Fold typographic variants to ASCII, keeping an offset map to ``text``.

    Pure ASCII input skips the per-character walk. Every key in :data:`_FOLD` is non-ASCII
    and NFKC is a no-op on ASCII, so there is provably nothing to fold -- and skipping the
    walk is worth roughly half the cost of a short parse. Only a month's full stop may
    change, and that keeps the length, so no map is needed -- unless a count touches its
    unit, which is rare, and then a map is built for that text alone.
    """
    if text.isascii():
        body = _undot(text)
        if _COUNT_UNIT.search(body) is None:
            return Normalized(text=body, original=text, _starts=None, _ends=None)
        body, starts, ends = _space_counts(
            body, list(range(len(text))), list(range(1, len(text) + 1))
        )
        return Normalized(text=body, original=text, _starts=tuple(starts), _ends=tuple(ends))

    out: list[str] = []
    starts = []
    ends = []
    for i, ch in enumerate(text):
        folded = _FOLD.get(ch)
        if folded is None:
            folded = unicodedata.normalize("NFKC", ch)
            folded = "".join(_FOLD.get(c, c) for c in folded)
        for c in folded:
            out.append(c)
            starts.append(i)
            ends.append(i + 1)
    body, starts, ends = _space_counts(_undot("".join(out)), starts, ends)
    return Normalized(text=body, original=text, _starts=tuple(starts), _ends=tuple(ends))
