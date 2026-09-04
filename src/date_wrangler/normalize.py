"""Fold typographic variants to ASCII, keeping a map back to the original string.

Editors rewrite ``-`` as an en dash and ``'24`` with a curly apostrophe; spreadsheets paste
non-breaking spaces. None of that should decide whether a date parses.

Matches report spans into the caller's string, so every produced character records where it
came from. Normalisation is per-character rather than whole-string NFKC: that keeps the
offset map exact, at the cost of not composing accents, which no date needs.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

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

    Pure ASCII input is returned untouched. Every key in :data:`_FOLD` is non-ASCII and
    NFKC is a no-op on ASCII, so there is provably nothing to do -- and skipping the
    per-character walk is worth roughly half the cost of a short parse.
    """
    if text.isascii():
        return Normalized(text=text, original=text, _starts=None, _ends=None)

    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for i, ch in enumerate(text):
        folded = _FOLD.get(ch)
        if folded is None:
            folded = unicodedata.normalize("NFKC", ch)
            folded = "".join(_FOLD.get(c, c) for c in folded)
        for c in folded:
            out.append(c)
            starts.append(i)
            ends.append(i + 1)
    return Normalized(
        text="".join(out),
        original=text,
        _starts=tuple(starts),
        _ends=tuple(ends),
    )
