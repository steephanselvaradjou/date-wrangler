"""The recognisers: text in, :class:`Spec` out.

Each rule is a scanning pattern plus a function that re-reads the matched fragment. The
patterns hold no capturing groups, so they concatenate into one alternation without group
numbers colliding; each parse function then runs its own small regex over the dozen or so
characters that matched.

Order matters. Python alternation is leftmost-first, not longest-match, so specific rules
must come before general ones -- month-day-year before bare month, or "January 15, 2024"
is claimed by the month rule and the day is left behind.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from .calendars import fiscal_year_of
from .config import MonthNumber, WranglerConfig
from .spec import Kind, Part, Spec
from .types import Anchor, Basis, Grain, Mod
from .vocab import (
    CARDINALS,
    DECADE_WORDS,
    FUTURE_WORDS,
    MONTH_NAMES,
    MONTHS,
    ORDINALS,
    PAST_WORDS,
    UNIT_SCALE,
    UNIT_WORDS,
    WEEKDAY_NAMES,
    WEEKDAYS,
    alt,
    find_month,
    ordinal_to_int,
    word_to_int,
)

__all__ = ["Rule", "RULES", "parse_year_token"]

# ---------------------------------------------------------------------------
# Shared fragments
# ---------------------------------------------------------------------------

_MONTH = alt(MONTH_NAMES)
_ORD = rf"(?:{alt(ORDINALS)}|\d{{1,2}}(?:st|nd|rd|th)?)"
_NUM = rf"(?:\d{{1,4}}|{alt(CARDINALS)})"
#: A count where "a couple of" may stand for one: "a couple of weeks ago", "the last couple
#: of months", "in a couple of days". Only in those rules -- see vocab.COUPLE.
_COUNT = rf"(?:{_NUM}|(?:a\s+)?couple\s+of)"
_UNIT = alt(UNIT_WORDS)
_PAST = alt(PAST_WORDS)
_FUTURE = alt(FUTURE_WORDS)
_DIRWORD = rf"(?:{_PAST}|{_FUTURE})"
# ``f\.?y\.?`` also covers "F.Y." and "FY."; the separator allows "fy-24" and "FY 24".
#: A tax year is the fiscal year on the configured calendar, as an assessment year is the
#: one after it: "tax year 2024-25" is FY2024-25. REGRESSION: "tax" was dropped, and "tax
#: year 2024", "tax year start 2024" and "this tax year" were read on the calendar year.
_TAX_YEAR = r"tax[\s-]+year"
#: Hyphened too, as an adjective is: "fiscal-year 2024". REGRESSION: that was the calendar
#: year 2024, "fiscal" dropped.
_FY_WORD = rf"(?:f\.?y\.?|(?:financial|fiscal)[\s-]+year|{_TAX_YEAR})"
#: "fiscal 2024", as US filings write it: the word alone, straight before its year.
_FY_BARE = r"(?:fiscal|financial)(?=\s+'?\d)"
_CY_WORD = r"(?:c\.?y\.?|calendar[\s-]+year)"
_YEAR_WORD = rf"(?:{_FY_WORD}|{_FY_BARE}|{_CY_WORD}|year|yr)"
#: A basis said outright in front of a unit: "this fiscal year", "last calendar quarter".
#: "tax" only in front of a year: "this tax year" is the fiscal year, but a tax day or a tax
#: quarter is not a fiscal one.
_BASIS_WORD = r"(?:fiscal|financial|calendar|tax(?=[\s-]+(?:years?|yrs?)\b))"


def _basis_of(word: str | None) -> Basis | None:
    """The basis a "fiscal"/"financial"/"calendar"/"tax" word names, or None when absent."""
    if word is None:
        return None
    return Basis.CALENDAR if word.lower().startswith("c") else Basis.FISCAL
_SEP = r"[\s-]*"

#: A fiscal year, which may be written with both its calendar years: "FY24", "FY2024-25",
#: "FY23/24". REGRESSION: the second year was not read after a quarter, so "Q1 FY2024-25"
#: stopped at FY2024 and came back a year early. Unspaced, and not before a month, so
#: "FY24 - 15 March" stays a range.
_FY_YEAR = (
    rf"(?:{_FY_WORD}|{_FY_BARE}){_SEP}'?\d{{2,4}}"
    rf"(?:[-/]'?\d{{2,4}}(?![\d/])(?![\s,]+(?:of\s+)?{_MONTH}\b))?"
)

#: A bare two-digit number is never a year -- that is how a day-of-month becomes one.
_MARKED_YEAR = rf"(?:{_FY_YEAR}|{_CY_WORD}{_SEP}'?\d{{2,4}})"

#: A split year with no FY in front: "Q3 2024-25". year_basis decides what it is -- see
#: parse_year_token. Not before a day or a month, so "15 Mar 2024-15 Apr 2024" stays a range.
_SPLIT_YEAR = (
    rf"(?:19|20)\d{{2}}[-/]\d{{2}}(?![\d/-])"
    rf"(?![\s,]+(?:\d{{1,2}}(?:st|nd|rd|th)?\s+)?(?:of\s+)?{_MONTH}\b)"
)

#: A year where nothing else can be meant -- after a month, quarter or half. Any four
#: digits, since "January 2100" is unambiguous. Restricting this to 19xx/20xx meant we
#: could not read back our own output, and substitute() grew the text on every pass.
_YEAR = (
    rf"(?:{_FY_YEAR}|(?:{_CY_WORD}|year){_SEP}'?\d{{2,4}}|'\d{{2}}|{_SPLIT_YEAR}|\d{{4}})"
)

#: A year or a run of two -- "2015", "2015-16", "2015/2016" -- as a season is named by.
_YEAR_RUN = r"(?:19|20)\d{2}(?:\s*[-/]\s*(?:19|20)?\d{2})?"
#: Words that make the years before or after them a name: "the 2015-16 season".
_SEASON_WORD = r"(?:season|academic\s+year|school\s+year|session)"
#: Up to two words naming whose season it is -- "the 2015-16 NBA season", "the 2015-16 flu
#: season" -- but not a word that ties the years to something else: "2015-16 sales by
#: season" is two years of sales. REGRESSION: the league's season was two calendar years.
_SEASON_OWNER = (
    r"(?:(?!(?:by|per|and|or|of|in|for|the|a|an|to|vs|versus|with|each|every|this|that"
    r"|than|from|over|across|on|at|since|until|till|through|between|last|next)\b)[a-z]+\s+)"
    r"{0,2}"
)

#: A year standing alone. Here the century is all that separates a date from a
#: quantity, so "5000" stays a number.
_BARE_YEAR = r"(?:19|20|21)\d{2}"
#: "last year", "next year", "this year" -- a year named by offset rather than by number.
#: Without this "Q1 last year" scans as two separate matches and the Q1 keeps the current
#: year, which is silently wrong rather than merely unrecognised.
#:
#: And a fiscal or calendar one: "Q1 last FY", "June of this fiscal year", "H2 of the
#: financial year". REGRESSION: each was the calendar period with the fiscal year a second,
#: unrelated date, and "June of this fiscal year" lost June altogether.
_FC_Y = r"[fc]\.?y\.?(?![\w'])(?!\s*-?\s*'?\d)"  # "FY", "CY" with no number after
_REL_YEAR = (
    rf"(?:(?:the\s+)?(?:{_DIRWORD}|this|current)\s+(?:(?:{_BASIS_WORD}\s+)?year|{_FC_Y})"
    rf"|the\s+(?:{_BASIS_WORD}\s+year|{_FC_Y}))"
)

#: "the" before a year that is written: "the year 2024", "the financial year 2024-25",
#: "the calendar year 2024". REGRESSION: only "the year" was taken, and "Q1 of the financial
#: year 2024-25" was this fiscal year's Q1 beside two calendar years.
_THE_YEAR = rf"the\s+(?=year\b|{_MARKED_YEAR})"

#: A marked year may abut ("Q1FY24"); an unmarked one may not, or "Q12024" splits. An
#: apostrophe may stand in for the space: "may'18", and "June' 2018" as statements write it.
_YEAR_SUFFIX = (
    rf"(?:\s*{_MARKED_YEAR}|\s+(?:of\s+)?(?:{_THE_YEAR})?{_YEAR}"
    rf"|'\s*\d{{4}}\b|'\d{{2}}(?!\d)|\s+(?:of\s+)?{_REL_YEAR})?"
)

_QWORD = r"(?:quarters?|qtrs?\.?|q)"
_HWORD = r"(?:halves|half|h)"

#: Words placing a count relative to now: "3 months ago", "5 years after". Defined once
#: because the scanning pattern and the parse function have to agree.
_AGO_WORDS = r"ago|back|earlier|prior|before|later|hence|after|ahead|out"
_AGO_FUTURE = frozenset({"later", "hence", "after", "ahead", "out"})


#: Patterns a parse function runs on every match it is handed. Built once here rather than
#: interpolated per call: the f-string is rebuilt and rehashed for the module cache each
#: time, and these are the innermost loop of scanning.
_WEEKDAY_RE = re.compile(rf"\b({alt(WEEKDAY_NAMES)})\b")
_PAST_RE = re.compile(rf"\s*{_PAST}\b")
_FUTURE_RE = re.compile(rf"\s*{_FUTURE}\b")
_YEAR_TAIL_RE = re.compile(rf"\s*(?:of\s+)?'?\s*({_YEAR})\s*$", re.IGNORECASE)
#: The same, read back: the direction word, and the basis the year states.
_REL_YEAR_TAIL_RE = re.compile(
    rf"\s*(?:of\s+)?(?:(?:the\s+)?(?P<dir>{_DIRWORD}|this|current)\s+"
    rf"(?:(?P<basis>{_BASIS_WORD})\s+)?year"
    rf"|(?:(?:the\s+)?(?P<dir2>{_DIRWORD}|this|current)|the)\s+"
    rf"(?:(?P<basis2>{_BASIS_WORD})\s+year|(?P<fc>[fc])\.?y\.?))\s*$",
    re.IGNORECASE,
)
_OF_RE = re.compile(r"\bof\b", re.IGNORECASE)


#: Several dates read out of one fragment, each with its own span inside it: the days of
#: "1st and 15th March" share the month, but each is a date of its own.
Parts = list[tuple[Spec, int, int]]


@dataclass(frozen=True, slots=True)
class Rule:
    """A named recogniser.

    A rule with no pattern is read from tokens (:mod:`.tokens`) rather than by a regular
    expression. It keeps its place in :data:`RULES`, which is still the order that decides
    between two dates starting at the same place.
    """

    name: str
    pattern: str | None
    parse: Callable[[str, WranglerConfig], Spec | Parts | None] | None


def _from_tokens(name: str) -> Rule:
    return Rule(name, None, None)


# ---------------------------------------------------------------------------
# Year handling
# ---------------------------------------------------------------------------

_YEAR_DIGITS = re.compile(r"'?(\d{2,4})")


def _pivot_year(digits: str, cfg: WranglerConfig) -> int:
    """Expand a written year, applying the two-digit pivot."""
    value = int(digits)
    if len(digits.lstrip("0")) <= 2 and value < 100:
        return 2000 + value if value <= cfg.two_digit_pivot else 1900 + value
    return value


def _stated_basis(token: str) -> Basis | None:
    """The basis a year fragment declares -- "FY24", "CY2024", "fiscal year" -- if any."""
    low = token.lower()
    # No trailing \b -- "cy2024" has no boundary between marker and digits, and
    # requiring one silently drops the CY the writer went out of their way to give.
    if re.search(rf"\bc\.?y\.?(?={_SEP}'?\d)|\bcalendar[\s-]+year\b", low):
        return Basis.CALENDAR
    if re.search(
        rf"\bf\.?y\.?(?={_SEP}'?\d)|\b(?:fiscal|financial)(?:[\s-]+year\b|(?=\s+'?\d))"
        rf"|\b{_TAX_YEAR}\b",
        low,
    ):
        return Basis.FISCAL
    return None


_SPAN_DIGITS = re.compile(r"\d\s*[-/]\s*'?\d")
#: "2024-25" with nothing in front of it.
_PLAIN_SPLIT = re.compile(r"(?<![\w'])(?:19|20)\d{2}[-/]\d{2}(?![\d/-])")


def parse_year_token(token: str, cfg: WranglerConfig) -> tuple[int | None, Basis | None]:
    """Read a year fragment, returning the year and any basis it declared.

    A fiscal year written with both its calendar years -- "FY2024-25" -- is the label the
    calendar gives it, which is not the first number when years are named by their end.

    Without the FY, "2024-25" is what year_basis says, as any other year is: the fiscal
    year 2024-25 on a fiscal basis, and on a calendar one two years -- of which a quarter
    or a month is not one period, so it is refused with what to write instead.
    """
    basis = _stated_basis(token)
    split = basis is None and _PLAIN_SPLIT.fullmatch(token.strip())
    if split and cfg.effective_year_basis is not Basis.FISCAL:
        raise ValueError(
            f"{token.strip()!r} is two calendar years with year_basis='calendar', so a period "
            f"of it is not one period: write FY{token.strip()} for the fiscal year, or set "
            "year_basis='fiscal'"
        )
    if split or (basis is Basis.FISCAL and _SPAN_DIGITS.search(token)):
        label = _fy_label_of_span(token, cfg)
        if label is None:
            raise ValueError(_not_one_fiscal_year(token))
        return label, Basis.FISCAL
    m = _YEAR_DIGITS.search(token.lower())
    return (_pivot_year(m.group(1), cfg) if m else None), basis


def _month_year_basis(text: str) -> Basis | None:
    """The basis of the year written after a month or a day.

    A plain year leaves a month on the calendar: "June 2025" is June 2025. FY makes it a
    label -- "June FY25" is the June inside FY25 -- and so does a split year read as a
    fiscal year: on a fiscal basis "June 2024-25" is June 2024.
    """
    return _stated_basis(text) or (Basis.FISCAL if _PLAIN_SPLIT.search(text) else None)


def _not_one_fiscal_year(text: str) -> str:
    return (
        f"{text.strip()!r} is not one fiscal year: the second year has to follow the first, "
        "as in FY2024-25; for several years write FY22 to FY24"
    )


def _fy_label_of_span(text: str, cfg: WranglerConfig) -> int | None:
    """The fiscal year "2024-25" names: the one that starts in 2024.

    Which number labels it depends on the convention -- 2025 when years are named by their
    end, 2024 when by their start -- so take the first year and ask the calendar.

    None unless the second year is the one after the first. "FY24 - 2023" or "FY24 - 15
    March" is not one fiscal year, and reading it as one was confidently wrong.
    """
    m = re.search(r"'?(\d{2,4})\s*[-/]\s*'?(\d{2,4})", text)
    if not m:
        return None
    first, second = _pivot_year(m.group(1), cfg), _pivot_year(m.group(2), cfg)
    if (first + 1) % 100 != second % 100:
        return None
    return fiscal_year_of(date(first, cfg.fiscal.start_month, 1), cfg.fiscal)


def _year_from_suffix(text: str, cfg: WranglerConfig) -> tuple[int | None, Basis | None]:
    """Pull a trailing year off a period phrase, if one is there."""
    m = _YEAR_TAIL_RE.search(text)
    if not m:
        return None, None
    year, basis = parse_year_token(m.group(1), cfg)
    if basis is None and _OF_RE.search(text):
        basis = cfg.effective_of_year_basis
    return year, basis


def _relative_year_of_suffix(text: str) -> tuple[int, Basis | None] | None:
    """"Q1 last year" -> (-1, None), "Q1 last FY" -> (-1, FISCAL), "Q3 of the fiscal
    year" -> (0, FISCAL). None when no relative year is named."""
    m = _REL_YEAR_TAIL_RE.search(text)
    if not m:
        return None
    word = (m.group("dir") or m.group("dir2") or "this").lower()
    offset = 0 if word in ("this", "current") else _direction_of(word)
    said = m.group("basis") or m.group("basis2") or m.group("fc")
    return offset, _basis_of(said)


def _year_offset_from_suffix(text: str) -> int | None:
    """"Q1 last year" -> -1. None when no relative year is named."""
    rel = _relative_year_of_suffix(text)
    return None if rel is None else rel[0]


def _period_suffix(text: str, cfg: WranglerConfig) -> tuple[int | None, Basis | None, int | None]:
    """The year, basis and year-offset trailing a period phrase. At most one year form. A
    relative year keeps the basis it states: "Q1 last FY" is a fiscal quarter."""
    year, basis = _year_from_suffix(text, cfg)
    if year is not None:
        return year, basis, None
    rel = _relative_year_of_suffix(text)
    if rel is None:
        return None, basis, None
    return None, rel[1] or basis, rel[0]


def _unit_of(word: str) -> Grain | None:
    name = UNIT_WORDS.get(word.lower().rstrip("."))
    return Grain[name] if name else None


def _scale_of(word: str) -> int:
    """How many of the mapped grain the word means: a fortnight is two weeks."""
    return UNIT_SCALE.get(word.lower().rstrip("."), 1)


def _direction_of(word: str) -> int:
    return 1 if word.lower() in FUTURE_WORDS else -1


# ---------------------------------------------------------------------------
# Parse functions
# ---------------------------------------------------------------------------


def _p_iso(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not m:
        return None
    return Spec(Kind.ABS_DAY, year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))


#: A month name that is not part of a longer word, though digits may touch it: the
#: "june" of "13june2018". find_month() wants a word boundary, which a digit does not give.
_MONTH_IN = re.compile(rf"(?<![a-z])({_MONTH})(?![a-z])", re.IGNORECASE)


def _month_in(text: str) -> int | None:
    m = _MONTH_IN.search(text)
    return None if m is None else MONTHS[m.group(1).lower()]


#: "the 1990s", "the 1990's", "the '90s", "the 90s", "the nineties".
_DECADE = rf"(?:(?:1[89]|20)\d0'?s|'?\d0s|{alt(DECADE_WORDS)})"


def _p_decade(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the 1990s" -- ten years, starting at the zero.

    Two digits name a century only by convention: the '30s to the '90s are the 1900s, the
    '00s to the '20s this century. Without the apostrophe "the 90s" may be a temperature,
    so it reads at 0.8, like a bare year.
    """
    # Case-insensitive: the scanner lowered the text to find this, but the fragment here
    # is the original, and a heading in capitals -- "THE 1990S" -- has a capital S.
    low = text.lower()
    m = re.search(r"\b((?:1[89]|20)\d)0'?s\b", low)
    if m:
        return Spec(Kind.DECADE, year=int(m.group(1)) * 10)
    m = re.search(r"('?)(\d)0s\b", low)
    if m:
        tens = int(m.group(2))
        year = (2000 if tens <= 2 else 1900) + tens * 10
        return Spec(Kind.DECADE, year=year, confidence=1.0 if m.group(1) else 0.8)
    for word, year in DECADE_WORDS.items():
        if re.search(rf"\b{word}\b", low):
            return Spec(Kind.DECADE, year=year)
    return None


def _p_ordinal_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the 15th", "on the 1st" -- a day of the current month.

    Weak on purpose: a bare ordinal is far more often a list position than a date, so this
    only survives where a cue vouches for it.
    """
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)\b", text)
    if not m:
        return None
    day = int(m.group(1))
    if not 1 <= day <= 31:
        return None
    return Spec(Kind.THIS_PERIOD, unit=Grain.MONTH, day_of_period=day, confidence=0.8)


def _p_close_of_business(text: str, cfg: WranglerConfig) -> Spec | None:
    """"EOD", "COB Friday" -- a deadline landing on a whole day.

    A weekday after it settles the matter. On its own the acronym has to be capitalised,
    because "cob" in lower case is far more often corn than close of business.
    """
    stripped = text.strip()
    m = _WEEKDAY_RE.search(stripped.lower())
    if m is not None:
        return Spec(Kind.WEEKDAY, index=WEEKDAYS[m.group(1)], direction=0)
    acronym = re.match(r"[A-Za-z]+", stripped)
    if acronym is None or not acronym.group(0).isupper():
        return None
    return Spec(Kind.DAY_KEYWORD, direction=0)


def _p_numeric(text: str, cfg: WranglerConfig) -> Spec | None:
    """An all-numeric date, read per :attr:`WranglerConfig.date_order`.

    A component above 12 can only be a day, so unambiguous input reads right regardless --
    and is certain: only when either number could be the month is the order a guess.
    """
    m = re.fullmatch(r"(\d{1,4})[/.-](\d{1,2})[/.-](\d{1,4})", text.strip())
    if not m:
        return None
    a, b, c = (int(g) for g in m.groups())
    order = cfg.date_order.value
    if order == "YMD" or len(m.group(1)) == 4:
        year, month, day = a, b, c
    elif order == "MDY":
        month, day, year = a, b, c
    else:
        day, month, year = a, b, c
    if month > 12 and day <= 12:  # unambiguously the other way round
        month, day = day, month
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    year = _pivot_year(str(year), cfg) if year < 100 else year
    either_way = len(m.group(1)) != 4 and day <= 12 and day != month
    return Spec(
        Kind.ABS_DAY, year=year, month=month, day=day, confidence=0.9 if either_way else 1.0
    )


def _p_day_month_year(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(r"\s*(\d{1,2})(?:st|nd|rd|th)?\s*", text, re.IGNORECASE)
    if not m:
        return None
    month = _month_in(text)
    if month is None:
        return None
    # "2nd March last year": REGRESSION -- the "last year" was matched and then not read,
    # so the answer was this year's 2 March at full confidence.
    year, _, offset = _period_suffix(text, cfg)
    basis = _month_year_basis(text) if year is not None else None  # "15 June FY25", as a month
    if year is None and offset is None:
        # "15 Mar 24". A bare two-digit number is normally too ambiguous to be a year, but
        # here the day slot is already filled, so nothing else is left for it to be.
        short = re.search(r"\s+(\d{2})\s*$", text)
        if short:
            year = _pivot_year(short.group(1), cfg)
    return Spec(
        Kind.ABS_DAY, year=year, month=month, day=int(m.group(1)), basis=basis,
        year_offset=offset,
    )


def _p_month_day_year(text: str, cfg: WranglerConfig) -> Spec | None:
    """"January 15, 2024", "march 3" -- and the ambiguous "jan 24".

    Only a bare two-digit number with no year beside it is actually in doubt. Everything
    else settles itself: an ordinal suffix or a single digit is a day, an explicit year
    means the number beside it is a day, and anything above 31 can only be a year.
    """
    month = find_month(text)
    if month is None:
        return None
    m = re.search(
        rf"{_MONTH}\.?\s+(?:the\s+)?(\d{{1,2}})(st|nd|rd|th)?", text, re.IGNORECASE
    )
    if not m:
        return None
    digits, suffix = m.group(1), m.group(2)
    year, _, offset = _period_suffix(text, cfg)
    if year is not None or offset is not None or suffix or len(digits) == 1:
        basis = _month_year_basis(text) if year is not None else None
        return Spec(
            Kind.ABS_DAY, year=year, month=month, day=int(digits), basis=basis,
            year_offset=offset,
        )
    value = int(digits)
    if value > 31 or cfg.month_number is MonthNumber.YEAR:
        return Spec(Kind.ABS_MONTH, month=month, year=_pivot_year(digits, cfg))
    return Spec(Kind.ABS_DAY, month=month, day=value)


def _p_fy_range(text: str, cfg: WranglerConfig) -> Spec | None:
    """One fiscal year written with both its calendar years: "FY2024-25".

    REGRESSION: the second year was taken as the label whatever the convention, so where
    years are named by their start "FY2024-25" came back as the year from April 2025.
    """
    label = _fy_label_of_span(text, cfg)
    if label is None:
        raise ValueError(_not_one_fiscal_year(text))
    return Spec(Kind.ABS_YEAR, year=label, basis=Basis.FISCAL)


def _p_split_year(text: str, cfg: WranglerConfig) -> Spec | Parts | None:
    """"2024-25", "2012/13" -- with no FY in front, year_basis decides.

    In a finance team it is a fiscal year; to anyone else, two years. Nothing in the
    phrase says which, so it is read the way every other year is: by year_basis. On a
    fiscal basis it is the fiscal year 2024-25; on a calendar one, 2024 to 2025 -- the two
    years come back as a span, each end its own, exactly as "2024-2025" does.
    """
    m = re.fullmatch(r"\s*((?:19|20)\d{2})\s*[-/]\s*(\d{2}|\d{4})\s*", text)
    if not m:
        return None
    first, second = int(m.group(1)), int(m.group(2))
    if (first + 1) % 100 != second % 100:
        raise ValueError(
            f"{text.strip()!r} is not one fiscal year, whose second year follows the first; "
            "if it is a run of years, write it as one -- '2024 to 2030'"
        )
    if cfg.effective_year_basis is Basis.FISCAL:
        label = _fy_label_of_span(text, cfg)
        return None if label is None else Spec(Kind.ABS_YEAR, year=label, basis=Basis.FISCAL)
    return [
        (Spec(Kind.ABS_YEAR, year=first, confidence=0.8), m.start(1), m.end(1)),
        (Spec(Kind.ABS_YEAR, year=first + 1, confidence=0.8), m.start(2), m.end(2)),
    ]


def _p_numeric_month(text: str, cfg: WranglerConfig) -> Spec | None:
    """"2024-03", "2024/03", "03/2024", "03-2024" -- a month written as numbers.

    "2011-12" is also how a fiscal year is written. year_basis decides, as it does for
    "2024-25": on a fiscal basis it is the fiscal year 2011-12, on a calendar one December
    2011, the ISO reading.
    """
    t = text.strip()
    m = re.fullmatch(r"((?:19|20)\d{2})[-/](\d{1,2})", t) or re.fullmatch(
        r"(\d{1,2})[-/]((?:19|20)\d{2})", t
    )
    if not m:
        return None
    year_first = len(m.group(1)) == 4
    year, month = (int(m.group(1)), int(m.group(2))) if year_first else (
        int(m.group(2)), int(m.group(1))
    )
    if not 1 <= month <= 12:
        return None
    fiscal = cfg.effective_year_basis is Basis.FISCAL
    if fiscal and year_first and len(m.group(2)) == 2 and (year + 1) % 100 == month:
        label = _fy_label_of_span(t, cfg)
        if label is not None:
            return Spec(Kind.ABS_YEAR, year=label, basis=Basis.FISCAL)
    return Spec(Kind.ABS_MONTH, year=year, month=month)


def _p_iso_week_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"2024-W11-5" -- an ISO week date: the year, the week, the day of the week."""
    m = re.fullmatch(r"\s*((?:19|20)\d{2})-?w(\d{2})-?([1-7])\s*", text, re.IGNORECASE)
    if not m:
        return None
    d = date.fromisocalendar(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return Spec(Kind.ABS_DAY, year=d.year, month=d.month, day=d.day)


#: A basis said outright with a to-date: "fiscal YTD", "FY YTD", "calendar year to date",
#: "tax year to date" -- "tax" only before "year": "tax YTD" is the tax paid so far.
_TD_BASIS = r"(?:fiscal|financial|calendar|f\.?y\.?|c\.?y\.?|tax(?=[\s-]+year\b))"
#: The same window back: "last YTD", "prior year YTD", "last year's YTD", "LY YTD", "PY YTD".
_TD_PAST = rf"(?:{_PAST}(?:\s+(?:year|month|quarter|week)(?:'s)?)?|ly|py)"
#: The to-dates short -- "YTD", "Y-T-D", "Y.T.D.", "MTD", "QTD", "WTD", "LYTD", "PYTD",
#: "LMTD", "LQTD", "FYTD", "CYTD" -- and the units they are spelt out with.
_TD_SHORT = r"(?:[ymqw][.-]?t[.-]?d\b\.?|[lp]ytd|lmtd|lqtd|fytd|cytd)"
_TD_UNIT_WORD = r"(?:year|month|mth|quarter|qtr|week)"
_TD_CORE = (
    rf"(?:(?:{_TD_BASIS}[\s-]*)?(?:{_TD_SHORT}|{_TD_UNIT_WORD}[\s-]*(?:to|till|until)[\s-]*date)"
    rf"|{_TD_BASIS}[\s-]+to[\s-]+date"
    rf"|(?:(?:this|the)\s+)?(?:{_TD_BASIS}\s+)?{_TD_UNIT_WORD}\s+so\s+far"
    rf"|so\s+far\s+(?:this|the)\s+(?:{_TD_BASIS}\s+)?{_TD_UNIT_WORD})"
)
#: What may follow one: its year -- "YTD 2024", "YTD for 2024", "YTD-2024", "YTD'24" -- or
#: a unit back: "YTD last year", "MTD last month", "YTD LY". Not after a comma or a spaced
#: dash: "YTD, 2024" lists two periods and "YTD - 2024" spans them.
_TD_TAIL = (
    rf"(?:\s+(?:of|for|in)\s+{_YEAR}|-{_YEAR}(?![-/.]\d)|\s+{_YEAR}|'\d{{2}}(?!\d)"
    rf"|\s+(?:{_PAST}\s+{_TD_UNIT_WORD}|ly|py))"
)
#: The month a YTD runs to, and the words that lead to it: "YTD through March 2024". Not
#: "to": "YTD to March" is a span, both ends its own, as "to" always makes one.
_TD_UNTIL = r"(?:through|thru|till|until|up\s*to|upto|as\s+(?:of|at|on)|ending|ended)"
_TD_MONTH = rf"{_MONTH}(?:\s*,\s*\d{{4}}|-\d{{2}}(?:\d{{2}})?(?!\d)|{_YEAR_SUFFIX})"
_TD_YTD = r"(?:y[.-]?t[.-]?d|year[\s-]*to[\s-]*date)"

#: Which to-date a phrase names: the letter of its abbreviation, or the unit spelt out.
_TD_WHICH = re.compile(
    r"\b(?:[lp]?([ymqw])[.-]?t[.-]?d"
    r"|(year|month|mth|quarter|qtr|week)[\s-]*(?:(?:to|till|until)[\s-]*date|so\s+far)"
    r"|so\s+far\s+(?:this|the)\s+(?:(?:fiscal|financial|calendar|tax(?=\s+year))\s+)?"
    r"(year|month|mth|quarter|qtr|week))"
)
_TD_UNITS = {
    "y": Grain.YEAR, "year": Grain.YEAR, "m": Grain.MONTH, "month": Grain.MONTH,
    "mth": Grain.MONTH, "q": Grain.QUARTER, "quarter": Grain.QUARTER, "qtr": Grain.QUARTER,
    "w": Grain.WEEK, "week": Grain.WEEK,
}
_TD_YEAR_BACK = re.compile(rf"\b(?:{_PAST}\s+year|ly|py|[lp]ytd)\b")
_TD_UNIT_BACK = re.compile(rf"\b(?:{_PAST}|lmtd|lqtd)\b")
_TD_FISCAL = re.compile(
    rf"\b(?:fiscal|financial|fytd)\b|\bf\.?y\.?(?![\s'-]*\d)|\b{_TAX_YEAR}\b"
)
_TD_CALENDAR = re.compile(r"\b(?:calendar|cytd)\b|\bc\.?y\.?(?![\s'-]*\d)")
#: The year written with the month: "March 2024", "Mar-24", "March, 2024", "Mar'24".
_TD_MONTH_YEAR = re.compile(
    rf"(?:{_MONTH})(?:\s*[,']?\s*|-)(\d{{4}}|(?<=[-'])\d{{2}}(?!\d))", re.IGNORECASE
)


def _p_to_date(text: str, cfg: WranglerConfig) -> Spec | None:
    """YTD, MTD, QTD, WTD -- short, spelt out or "so far" -- with a basis, a year, a month
    to run to, or a step back.

    REGRESSION: "fiscal year to date" and "FY YTD" were the *calendar* year to date --
    the basis was dropped; "2024-YTD" ran from 2024 to today; "YTD last year" and
    "YTD through March 2024" were two dates each; "this year so far" was the whole year.
    """
    low = text.lower()
    which = _TD_WHICH.search(low)
    unit = Grain.YEAR if which is None else _TD_UNITS[next(g for g in which.groups() if g)]
    year_back = _TD_YEAR_BACK.search(low) is not None
    back = year_back or _TD_UNIT_BACK.search(low) is not None
    spec = Spec(Kind.TO_DATE, unit=unit, direction=-1 if back else 0)
    if year_back and unit is not Grain.YEAR:
        # "last year's MTD": the month so far, a year back -- not last month's.
        spec = spec.with_(direction=0, year_offset=-1)
    stated = (
        Basis.FISCAL if _TD_FISCAL.search(low)
        else Basis.CALENDAR if _TD_CALENDAR.search(low) else None
    )
    month = find_month(low)
    year, basis = _year_from_suffix(text, cfg)
    lead = re.match(rf"\s*((?:19|20)\d{{2}}|{_MARKED_YEAR})[\s-]+(?=y)", text, re.I)
    if year is None and lead is not None:
        # "2024 YTD": the year first, as a column heading puts it.
        year, basis = parse_year_token(lead.group(1), cfg)
    if year is None and month is not None:
        written = _TD_MONTH_YEAR.search(text)  # "March 2024 YTD", "YTD Mar-24"
        if written is not None:
            year = _pivot_year(written.group(1), cfg)
    basis = stated or basis
    if month is not None:
        # Year may still be unstated ("YTD March"); resolve() picks the most recent March.
        return spec.with_(month=month, year=year, basis=basis)
    if year is not None:
        return spec.with_(year=year, basis=basis)
    return spec if basis is None else spec.with_(basis=basis)


#: "to date", "-to-date", "to-date".
_TO_DATE = r"(?:\s+|\s*-\s*)to(?:\s+|\s*-\s*)date"

#: The periods "<period> to date" is written with. Deliberately not "last year" or a bare
#: "year": "last year to date" is last year's YTD -- the same window a year earlier -- and
#: the to_date rule already reads both that way.
_TD_TARGET = (
    rf"(?:{_MONTH}{_YEAR_SUFFIX}|{_QWORD}\s*[1-4](?!\d){_YEAR_SUFFIX}"
    rf"|h\s*[12](?!\d){_YEAR_SUFFIX}|{_YEAR}"
    rf"|(?:this|current|present)\s+(?:(?:{_BASIS_WORD}\s+)?{_UNIT}|{_FY_WORD}|{_CY_WORD}))"
)


def _p_period_to_date(text: str, cfg: WranglerConfig) -> Spec | None:
    """"this year to date", "Q3 to date", "March 2024 to date".

    Only the abbreviations and "year to date" were read before. Spelled out with "this",
    or with a named period, the period was read and "to date" was left over -- so the
    safety net flagged it at 0.5 and the answer was the whole period, future included.
    """
    m = re.match(rf"\s*(.+?){_TO_DATE}\b", text, re.IGNORECASE)
    if not m:
        return None
    target = _target_spec(m.group(1), cfg)
    if target is None:
        return None
    return target.with_(through_today=True)


def _p_trailing_months(text: str, cfg: WranglerConfig) -> Spec | None:
    """Reporting shorthand: TTM, LTM, T12M, L3M."""
    low = text.strip().lower()
    if low in ("ttm", "ltm"):
        count = 12
    else:
        m = re.fullmatch(r"[tl](\d{1,2})m", low)
        if not m:
            return None
        count = int(m.group(1))
    if not 1 <= count <= 60:
        return None
    # Always anchored: TTM is the last twelve *completed* months, which is the whole
    # reason the figure gets quoted. Rolling it would make it incomparable period to
    # period, so this ignores the configured default rather than following it.
    return Spec(
        Kind.RELATIVE, count=count, unit=Grain.MONTH, direction=-1, anchor=Anchor.ANCHORED
    )


def _p_period_ending(text: str, cfg: WranglerConfig) -> Spec | None:
    """"quarter ending June 2024", "the 12 months to March 2024", "six months ended 30 June".

    Annual-report phrasing names a period by where it stops, and two parts of it were
    lost. A count was dropped, so "12 months ending March 2024" came back as one month;
    and "to" was not read at all, so "six months to June 2024" and "the year to March
    2024" came back as the month alone.
    """
    # Reporting shorthand, read as its words: "QE June 2024" is the quarter ended June
    # 2024, "YE" the year ended and "FYE" the fiscal year ended, "12M to March 2024" twelve
    # months to it; "2024 year end" is "year end 2024", the year 2024. REGRESSION: that
    # last one was the end of this year, and the rest read only their month; and "FYE 2024"
    # was the calendar year 2024.
    # "FYE 24", "fiscal year end 24": two digits after a fiscal year's end are its year, as
    # "FYE '24" already was. REGRESSION: they were this fiscal year's end, the year dropped.
    two_digits = re.fullmatch(
        rf"\s*(?:the\s+)?(?:f\.?y\.?e\.?|{_FY_WORD}[\s-]*end(?:ing|ed|s)?)\s+(\d{{2}})\s*",
        text,
        re.IGNORECASE,
    )
    if two_digits:
        return _p_period_ending(
            f"fiscal year ended {_pivot_year(two_digits.group(1), cfg)}", cfg
        )
    # "FYE: 2024" and "FYE-2024" as a heading writes them, too.
    short = re.match(
        r"\s*(?:the\s+)?(q/?e|y/?e|f\.?y\.?e\.?)(?:\s*[:,-]\s*|\s+)(.+)$", text, re.IGNORECASE
    )
    if short:
        word = re.sub(r"[./]", "", short.group(1).lower())
        unit_word = {"fye": "fiscal year"}.get(word, "quarter" if word[0] == "q" else "year")
        return _p_period_ending(f"{unit_word} ended {short.group(2)}", cfg)
    months = re.match(r"\s*(?:the\s+)?(\d{1,2})\s*m\s+(to|ended|ending)\s+(.+)$", text, re.I)
    if months:
        n, word, rest = months.groups()
        return _p_period_ending(f"{n} months {word} {rest}", cfg)
    year_first = re.fullmatch(
        rf"\s*((?:19|20)\d{{2}}|{_FY_YEAR})\s+(?:((?:{_BASIS_WORD}\s+)?year|{_FY_WORD}|{_CY_WORD})"
        rf"[\s-]+(?:end(?:ing|ed)?|close)|(f\.?y\.?e\.?))\s*",
        text,
        re.I,
    )
    if year_first:
        # "2024 year end", "2024 fiscal year end", "2024 FYE": the year written first --
        # "year" as year_basis says, a calendar year the calendar's, anything fiscal the
        # fiscal year's. An FY label first is that fiscal year, as "FYE FY24" is: "FY24 FYE".
        # REGRESSION: "2024 CY end" was FY2024 on a fiscal basis, and "FY24 FYE" FY24 beside
        # this fiscal year's end.
        if not year_first.group(1)[0].isdigit():
            label, _ = parse_year_token(year_first.group(1), cfg)
            return None if label is None else Spec(Kind.ABS_YEAR, year=label, basis=Basis.FISCAL)
        said = year_first.group(2) or year_first.group(3)
        if re.match(r"(?i)(?:fiscal|financial|tax|f\.?y)", said):
            unit_word = "fiscal year"
        elif re.match(r"(?i)(?:calendar|c\.?y)", said):
            unit_word = "calendar year"
        else:
            unit_word = "year"
        return _p_period_ending(f"{unit_word} end {year_first.group(1)}", cfg)
    m = re.match(
        rf"\s*(?:the\s+)?(?:({_NUM})\s+)?(half[\s-]?years?|{_FY_WORD}|{_CY_WORD}|{_UNIT})"
        rf"(?:\s+period)?(?:\s+|-)(end(?:ing|ed|s)?|close|to)(?:\s*,\s*|\s+)(.+)$",
        text,
        re.IGNORECASE,
    )
    if m is None:
        # "w/e 15/03/2024": week ending, as timesheets and statements abbreviate it.
        m = re.match(r"\s*()(w/e|w\.e\.)()\s+(.+)$", text, re.IGNORECASE)
    if not m:
        return None
    word = m.group(2).lower()
    calendar = re.fullmatch(_CY_WORD, word) is not None
    if word.startswith("half"):
        unit: Grain | None = Grain.HALF
    elif word.startswith("w"):
        unit = Grain.WEEK
    elif re.fullmatch(_FY_WORD, word) or calendar:
        unit = Grain.YEAR  # "FY ending June 30, 2024": twelve months to then, whatever config
    else:
        unit = _unit_of(m.group(2))
    count = word_to_int(m.group(1)) if m.group(1) else 1
    if unit is None or count is None or count < 1:
        return None
    # "3 days to March" counts down to something; it is not a period that ends in March.
    if m.group(3).lower() == "to" and unit in (Grain.DAY, Grain.WEEK):
        return None
    count *= _scale_of(m.group(2))
    tail = m.group(4)
    # An end given as a day -- "30 June 2024", "June 30, 2024", "31/03/2024" -- rather
    # than a month.
    if re.fullmatch(r"\s*\d{4}-\d{1,2}-\d{1,2}\s*", tail):
        day = _p_iso(tail, cfg)
    elif re.fullmatch(r"\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\s*", tail):
        day = _p_numeric(tail, cfg)
    else:
        day = _p_day_month_year(tail, cfg)
    if day is None and re.match(rf"\s*{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?!\d)", tail, re.I):
        day = _p_month_day_year(tail, cfg)
    if day is not None and day.kind is Kind.ABS_DAY:
        return Spec(Kind.PERIOD_ENDING, unit=unit, count=count, month=day.month,
                    day=day.day, year=day.year)
    month = find_month(tail)
    year, basis = _year_from_suffix(tail, cfg)
    if month is None and year is None:
        return None
    if month is None and year is not None and calendar and count == 1:
        # "calendar year end 2024", "CY end 2024": the calendar year 2024, on any basis.
        # REGRESSION: "calendar" was dropped -- the fiscal year on a fiscal basis -- and "CY
        # end 2024" was the end of 2024, September to December.
        return Spec(Kind.ABS_YEAR, year=year, basis=Basis.CALENDAR)
    if month is None and year is not None and re.fullmatch(_FY_WORD, word):
        # The basis the year states, not one "of" brings from of_year_basis: "fiscal year
        # end of 2024" names no label.
        if _stated_basis(tail) is Basis.FISCAL:
            # "FYE FY24": a fiscal year named by its label is that fiscal year, whichever
            # year its label is taken from. REGRESSION: it was read as the year FY24's
            # number ends in, a year early with a start-year label.
            return Spec(Kind.ABS_YEAR, year=year, basis=Basis.FISCAL)
        # "fiscal year ended 2024", "FYE 2024": a year alone names the fiscal year that
        # ends in it -- April 2023 to March 2024 with an April year, whichever label that
        # year carries. A month or a day given says where it ends instead, whatever the
        # configuration: "FY ending June 30, 2024".
        end_month = cfg.fiscal.end_month
        if count == 1:
            label = fiscal_year_of(date(year, end_month, 1), cfg.fiscal)
            return Spec(Kind.ABS_YEAR, year=label, basis=Basis.FISCAL)
        return Spec(Kind.PERIOD_ENDING, unit=unit, count=count, month=end_month, year=year)
    if calendar:
        basis = Basis.CALENDAR  # "calendar year ending June 2024" says which kind it is
    return Spec(Kind.PERIOD_ENDING, unit=unit, count=count, month=month, year=year, basis=basis)


def _p_weekday(text: str, cfg: WranglerConfig) -> Spec | None:
    low = text.strip().lower()
    m = _WEEKDAY_RE.search(low)
    if not m:
        return None
    index = WEEKDAYS[m.group(1)]
    if _PAST_RE.match(low):
        direction = -1
    elif _FUTURE_RE.match(low) or low.endswith(" next"):  # "Monday next", as the British say
        direction = 1
    else:
        direction = 0
    confidence = 1.0 if direction or low != m.group(1) else 0.8
    return Spec(Kind.WEEKDAY, index=index, direction=direction, confidence=confidence)


_WEEK_DIRECTION = {
    "last": -1, "previous": -1, "prior": -1, "this": 0, "next": 1, "following": 1,
    "coming": 1,
}


def _p_weekday_of_week(text: str, cfg: WranglerConfig) -> Spec | None:
    """"Friday last week", "Monday to Friday last week", "Wednesday of next week".

    REGRESSION: only "last week" was read, so "Monday to Friday last week" came back as the
    whole seven days, at full confidence.
    """
    m = re.match(
        rf"\s*({_WEEKDAY_ALT})(?:\s*(?:-|to|through|thru|till|until)\s*({_WEEKDAY_ALT}))?"
        r"\s+(?:of\s+)?(?:the\s+)?(last|previous|prior|this|next|following|coming)\s+week",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    first = WEEKDAYS[m.group(1).lower()]
    last = WEEKDAYS[m.group(2).lower()] if m.group(2) else first
    direction = _WEEK_DIRECTION[m.group(3).lower()]
    week = (
        Spec(Kind.THIS_PERIOD, unit=Grain.WEEK)
        if direction == 0
        else Spec(Kind.RELATIVE, count=1, unit=Grain.WEEK, direction=direction,
                  anchor=Anchor.ANCHORED)
    )
    return week.with_(weekday_span=(first, last))


def _p_fiscal_month(text: str, cfg: WranglerConfig) -> Spec | None:
    """An index into a fiscal year, not a calendar month: "the third month of FY24"."""
    m = re.match(rf"\s*(?:the\s+)?({_ORD})\s+month", text, re.IGNORECASE)
    if not m:
        return None
    index = ordinal_to_int(m.group(1))
    if index is None or not 1 <= index <= 12:
        return None
    year, basis = _year_from_suffix(text, cfg)
    return Spec(Kind.FISCAL_MONTH, year=year, index=index, basis=basis or Basis.FISCAL)


#: "1h" and "2h" are hours far more often than halves -- "a 2h drive" read as H2 -- and
#: "4q" is rarely a quarter. A digit written *before* the letter is the finance shorthand
#: when the letter is a capital (1H, 3Q), the rule the bare COB acronym follows, or when a
#: year comes after it ("from 1q 2024 to 3q 2024"). "1h 30m" and "1h24" have neither. The
#: scanner lowers text before matching, so case is checked on the original fragment.
_DIGIT_FIRST = re.compile(r"\b[1-4]\s*([qQhH])(?![a-zA-Z])")
_YEAR_AFTER = re.compile(r"\s+(?:(?:'\d{2}|(?:19|20)\d{2})\b|[fFcC]\.?[yY])")


def _digit_first_is_capital(text: str) -> bool:
    m = _DIGIT_FIRST.search(text)
    if m is None or m.group(1).isupper():
        return True
    return _YEAR_AFTER.match(text, m.end()) is not None


def _p_quarter(text: str, cfg: WranglerConfig) -> Spec | None:
    if not _digit_first_is_capital(text):
        return None
    low = text.lower()
    index: int | None = None
    m = re.search(rf"{_QWORD}\s*([1-4])(?!\d)", low)
    if m:
        index = int(m.group(1))
    else:
        m2 = re.search(rf"({_ORD})\s*{_QWORD}", low)
        if m2:
            index = ordinal_to_int(m2.group(1))
    if index is None or not 1 <= index <= 4:
        return None
    year, basis, offset = _period_suffix(text, cfg)
    return Spec(Kind.ABS_QUARTER, year=year, index=index, basis=basis, year_offset=offset)


#: A quarter or half as a label: Q3, 3Q, H1, 1H, and written out -- "Qtr 3", "Qtr. 3",
#: "Quarter 3", "Half 1" -- so a year joins them as it joins Q3. REGRESSION: "Qtr 3-24" was
#: this year's Q3, "2024QTR3" and "FY24Qtr3" were nothing, and "FY2024-25QTR3" was FY2024.
_QH = r"(?:(?:quarter|qtrs?\.?|q)\s*[1-4]|[1-4]\s*q|(?:half|h)\s*[12]|[12]\s*h)"
#: An FY or CY touching the H of "1HFY24", "1HCY 2024", where no word boundary falls.
_H_FY = r"(?=[fc]\.?y\.?[\s-]*'?\d)"
#: A basis written beside one: "fiscal Q3", "Q3 FY", "calendar H1".
_BASIS_TAG = r"(?:fiscal|financial|calendar|fy|cy)"

#: The spellings that the quarter and half rules either split apart or never saw, each a
#: full match on the fragment so the label and the year cannot be confused -- "3Q24" read
#: left to right has a "Q2" in it.
_LABELLED_FORMS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        rf"\s*(?P<basis>{_BASIS_TAG})\s+(?P<qh>{_QH})(?P<tail>.*)",          # fiscal Q3 [2024]
        rf"\s*(?P<qh>{_QH})\s+(?P<basis>fiscal|financial|calendar|fy|cy)\s*",  # Q3 fiscal
        rf"\s*(?P<qh>{_QH})['/-](?P<yr>\d{{2}}(?:\d{{2}})?)\s*",               # Q3'24 Q3/2024
        rf"\s*(?P<yr>(?:19|20)\d{{2}})\s*-?\s*(?P<qh>{_QH})\s*",                # 2024-Q3 2024Q3
        rf"\s*(?P<basis>{_FY_WORD}|{_CY_WORD})[\s-]*'?(?P<yr>\d{{2,4}})"          # FY2024-25 Q3
        rf"(?P<span>[-/]'?\d{{2,4}})?\s*-?\s*(?P<qh>{_QH})\s*",
        r"\s*(?P<qh>[1-4]\s*q|[12]\s*h)\s*'?(?P<yr>\d{2,4})\s*",             # 3Q24 1H2024
    )
)


def _p_labelled_period(text: str, cfg: WranglerConfig) -> Spec | None:
    """"fiscal Q3", "Q3 FY", "Q3'24", "Q3-2024", "2024-Q3", "FY24 Q3", "3Q24", "1H24".

    Each of these used to lose part of itself, and confidently. A stated basis was dropped,
    so "fiscal Q3" followed the configured default and became a calendar quarter under a
    calendar one. An attached year was dropped, so "Q3'24" meant Q3 of *this* year. And
    a dash between year and quarter read as a range, so "2024-Q3" ran from January 2024
    to the end of Q3.

    The result is the same shape "Q3 2024" produces, so with no basis written it follows
    ``bare_period_basis`` exactly as that does.
    """
    if not _digit_first_is_capital(text):
        return None
    for form in _LABELLED_FORMS:
        m = form.fullmatch(text)
        if m:
            break
    else:
        return None
    groups = m.groupdict()
    qh = re.sub(r"\s+", "", groups["qh"]).lower()
    digit = int(re.search(r"\d", qh).group(0))  # type: ignore[union-attr]
    kind = Kind.ABS_QUARTER if "q" in qh else Kind.ABS_HALF
    if not (1 <= digit <= (4 if kind is Kind.ABS_QUARTER else 2)):
        return None

    basis: Basis | None = None
    word = (groups.get("basis") or "").strip().lower()
    if word:
        basis = Basis.CALENDAR if word.startswith("c") else Basis.FISCAL

    year: int | None = None
    if groups.get("span"):
        # "FY2024-25 Q3": a quarter of the fiscal year written with both its years.
        year = _fy_label_of_span(groups["yr"] + groups["span"], cfg)
        if year is None:
            raise ValueError(_not_one_fiscal_year(text))
        basis = Basis.FISCAL
    elif groups.get("yr"):
        year = _pivot_year(groups["yr"], cfg)
    elif groups.get("tail"):
        year, tail_basis = _year_from_suffix(groups["tail"], cfg)
        if groups["tail"].strip() and year is None:
            return None  # something follows that is not a year; leave it to other rules
        basis = basis or tail_basis
    return Spec(kind, year=year, index=digit, basis=basis)


def _p_half(text: str, cfg: WranglerConfig) -> Spec | None:
    if not _digit_first_is_capital(text):
        return None
    low = text.lower()
    index: int | None = None
    m = re.search(r"\bh\s*([12])(?!\d)", low) or re.search(r"\bhalf\s*([12])(?!\d)", low)
    if m:
        index = int(m.group(1))
    else:
        m2 = re.search(rf"\b([12])\s*h(?:\b|{_H_FY})", low)
        if m2:
            index = int(m2.group(1))
        else:
            m3 = re.search(rf"({_ORD})\s+{_HWORD}", low)
            if m3:
                index = ordinal_to_int(m3.group(1))
    if index is None or index not in (1, 2):
        return None
    year, basis, offset = _period_suffix(text, cfg)
    return Spec(Kind.ABS_HALF, year=year, index=index, basis=basis, year_offset=offset)


def _p_month(text: str, cfg: WranglerConfig) -> Spec | None:
    """"June", "June 2025", "June FY25", "Jun-FY25".

    A plain year beside a month is the calendar year the month falls in, whatever
    year_basis says -- nobody means June 2024 by "June 2025". Only FY makes the year a
    fiscal label, and then the month is the one inside that fiscal year: with an April
    year, "June FY25" is June 2024. The label is kept as written and looked up when
    resolved, so in "Oct to Mar FY25" each end lands in its own calendar year.
    """
    month = find_month(text)
    if month is None:
        return None
    year, rel_basis, offset = _period_suffix(text, cfg)
    # "June of this fiscal year": the June inside the fiscal year in progress.
    basis = _month_year_basis(text) if year is not None else rel_basis
    return Spec(Kind.ABS_MONTH, year=year, month=month, year_offset=offset, basis=basis)


_NEAREST = {"last": -1, "previous": -1, "this": 0, "next": 1, "coming": 1}


def _p_month_nearest(text: str, cfg: WranglerConfig) -> Spec | None:
    """"last March", "this March", "next March" -- the nearest one to today.

    REGRESSION: matched nothing at all, though "March last year" and "last Monday" did.
    """
    m = re.match(rf"\s*(last|this|next|previous|coming)\s+({_MONTH})", text, re.IGNORECASE)
    if not m:
        return None
    if m.group(2) == "may":
        return None  # "at last may we" -- a lower-case "may" is the verb
    return Spec(
        Kind.ABS_MONTH, month=MONTHS[m.group(2).lower()], nearest=_NEAREST[m.group(1).lower()]
    )


def _p_year(text: str, cfg: WranglerConfig) -> Spec | None:
    year, basis = parse_year_token(text, cfg)
    if year is None:
        return None
    # FY or CY sets the basis; with only the word "year" -- "the year 2013" -- it stays
    # unset and year_basis decides, the same as for a bare "2013".
    return Spec(Kind.ABS_YEAR, year=year, basis=basis)


#: The year a fiscal year's income is assessed in, as tax returns in India and Bangladesh
#: name it: always the year after. "AY 2025-26" is the fiscal year 2024-25.
_AY_WORD = r"(?:a\.?y\.?|assessment\s+year)"


def _p_assessment_year(text: str, cfg: WranglerConfig) -> Spec | None:
    """"AY 2025-26", "A.Y. 2025-26", "assessment year 2025-26", "AY26": the fiscal year
    before it, whose income it assesses, on the configured fiscal calendar.

    REGRESSION: "AY 2015-16" was declined as an academic year, which is written out --
    "academic year 2015-16" -- where AY names the tax year.
    """
    digits = re.sub(rf"^\s*{_AY_WORD}", "", text, flags=re.IGNORECASE)
    try:
        label, _ = parse_year_token(f"FY{digits.strip(' -')}", cfg)
    except ValueError:
        raise ValueError(_not_one_fiscal_year(text)) from None
    if label is None:
        return None
    return Spec(Kind.ABS_YEAR, year=label - 1, basis=Basis.FISCAL)


def _p_bare_year(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.fullmatch(rf"\s*({_BARE_YEAR})\s*", text)
    if not m:
        return None
    # Nothing says which kind of year, so year_basis decides -- "2024", "in 2024" and "the
    # year 2024" are read alike, and alike with "Q4 2024" and "the second half of 2024".
    return Spec(Kind.ABS_YEAR, year=int(m.group(1)), confidence=0.8)


def _p_ago(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(rf"\s*({_COUNT})\s+({_UNIT})\s+({_AGO_WORDS})", text, re.IGNORECASE)
    if not m:
        return None
    count = word_to_int(m.group(1))
    unit = _unit_of(m.group(2))
    if count is None or unit is None:
        return None
    # "before"/"after" belong here as well as in the modifier prefixes, or "6 month
    # before" falls through to the fiscal-month rule and answers with the sixth month.
    direction = 1 if m.group(3).lower() in _AGO_FUTURE else -1
    return Spec(
        Kind.AGO, count=count * _scale_of(m.group(2)), unit=unit, direction=direction
    )


def _p_relative_fy(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(rf"\s*({_DIRWORD})\s+(?:({_NUM})\s+)?({_FY_WORD}|{_CY_WORD})\b", text, re.I)
    if not m:
        return None
    count = word_to_int(m.group(2)) if m.group(2) else 1
    if count is None:
        return None
    basis = Basis.CALENDAR if re.match(r"c", m.group(3).strip(), re.I) else Basis.FISCAL
    return Spec(
        Kind.RELATIVE,
        count=count,
        unit=Grain.YEAR,
        direction=_direction_of(m.group(1)),
        basis=basis,
    )


def _period_basis(word: str | None, unit: Grain) -> Basis | None:
    """A stated basis, where it can change anything.

    Only years, halves and quarters have a fiscal form. "this fiscal month" is this month
    -- months, weeks and days are calendar facts -- so the word is accepted and ignored
    rather than stamping a fiscal label on a calendar month.
    """
    if unit in (Grain.YEAR, Grain.HALF, Grain.QUARTER):
        return _basis_of(word)
    return None


def _p_relative(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(
        rf"\s*({_DIRWORD})\s+(?:({_COUNT})\s+)?(?:({_BASIS_WORD})\s+)?({_UNIT})\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    # A plural with no number is vague, not one unit: "in the coming months" came back as
    # exactly one month at full confidence. Nothing is a better answer than a made-up one.
    if not m.group(2) and re.search(r"(?:s|halves)$", m.group(4), re.IGNORECASE):
        return None
    count = word_to_int(m.group(2)) if m.group(2) else 1
    unit = _unit_of(m.group(4))
    if count is None or unit is None:
        return None
    # "trailing 12 months" is TTM spelled out, and "rolling" says what it means; both name
    # the anchoring outright, so neither should follow the configured default.
    word = m.group(1).lower()
    anchor = {"trailing": Anchor.ANCHORED, "rolling": Anchor.ROLLING}.get(word)
    return Spec(
        Kind.RELATIVE,
        count=count * _scale_of(m.group(4)),
        unit=unit,
        direction=_direction_of(m.group(1)),
        anchor=anchor,
        basis=_period_basis(m.group(3), unit),
    )


def _p_this(text: str, cfg: WranglerConfig) -> Spec | None:
    """"this year", "this fiscal year", "current quarter", "this FY".

    The basis word used to be read only after "last"/"next" ("last fiscal year"), so
    "this fiscal year" -- the commonest of them -- matched nothing at all.
    """
    m = re.match(
        rf"\s*(?:this|current|present)\s+(?:({_BASIS_WORD})\s+)?({_UNIT})\b",
        text,
        re.IGNORECASE,
    )
    if m:
        unit = _unit_of(m.group(2))
        if unit is None:
            return None
        if _scale_of(m.group(2)) == 10:
            # A decade is ten years counted back or forward, but "this decade" is the 2020s,
            # which no unit of years can say.
            raise ValueError(
                f"{text.strip()!r} is not resolved: name the decade, as in 'the 2020s'"
            )
        return Spec(Kind.THIS_PERIOD, unit=unit, basis=_period_basis(m.group(1), unit))
    m = re.match(rf"\s*(?:this|current|present)\s+({_FY_WORD}|{_CY_WORD})\b", text, re.I)
    if m:
        calendar = m.group(1).strip().lower().startswith("c")
        return Spec(
            Kind.THIS_PERIOD,
            unit=Grain.YEAR,
            basis=Basis.CALENDAR if calendar else Basis.FISCAL,
        )
    return None


#: Words naming a slice of a period. "middle" before "mid" would never match, so the
#: longer spelling of each pair comes first.
_PART_WORDS: tuple[tuple[str, Part], ...] = (
    # REGRESSION: "first-half of March 2024" was H1 2024, January to June.
    (r"first[\s-]+half|1st[\s-]+half|front[\s-]+half", Part.FIRST_HALF),
    # "last half of 2024" was read as "last half" -- the previous half-year, counted back
    # from today -- plus a stray 2024. It means the second half, as "latter half" does, and
    # "the back half" in a finance review.
    (
        r"second[\s-]+half|2nd[\s-]+half|latter[\s-]+half|last[\s-]+half|final[\s-]+half"
        r"|back[\s-]+half",
        Part.SECOND_HALF,
    ),
    # "begin", "beginning", "starting", "begining" only with "of": "starting March" and
    # "beginning March 2024" are since March, as a notice says "beginning 1 April, fees
    # rise". REGRESSION: "beginning March 2024" was 1-10 March. The "... part" forms first:
    # the parse takes what follows the word as the period, so "early" alone would leave
    # "part of 2024" to be read as one.
    (
        r"(?:early|earlier|first)\s+part|start|early"
        r"|(?:begin|beginning|begining|starting)(?=[\s-]+of\b)",
        Part.EARLY,
    ),
    (r"middle\s+part|middle|mid", Part.MID),
    (r"(?:latter|later|last|final)\s+part|end|late|close", Part.LATE),
)
_PART_ALT = "|".join(p for p, _ in _PART_WORDS)

#: The period a part or an ordinal day can be taken from.
#: A month in numbers as the period a part is taken from: "by the end of 2024-03". And a
#: year there may not be the start of one: REGRESSION -- "the end of 2024-03" was refused
#: as two calendar years, and "end of 2024-03-15" was the end of 2024.
_NUMERIC_MONTH = (
    r"(?:(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])|(?:0?[1-9]|1[0-2])[-/](?:19|20)\d{2})(?![\d/.-])"
)
#: A month's year joined by a mark or touching it -- "March-24", "March/24", "Mar.24",
#: "March2024" -- or, two digits, after a space where the clause ends: "the 15th of
#: March 24". REGRESSION: "15 of March 24" was this year's 15 March, and "15 of March24"
#: the 24th of this March.
#:
#: Two digits with a month after them are the next date's day, not this one's year; and
#: after a space they are a year before the next date as at the end of the clause, and
#: after a comma too, where no count can be meant. REGRESSION: "15 of March-20 of April
#: 2024" was 15 March 2020, and "15 of March 24 to 20 of April 24" ran from this year's
#: March. The token reader's two_digit_year is the same.
_NEXT_DAY = (
    r"\s*(?:-|to|till|until|through|thru|and|&|or|vs\.?|versus)\s*(?:the\s+)?"
    rf"\d{{1,2}}(?:st|nd|rd|th)?[-/.\s]*(?:of\s+)?{_MONTH}\b"
)
_MONTH_JOINED_YEAR = (
    rf"\.?(?:[-/.]?(?:\d{{4}}|\d{{2}}(?![-/.\s]*(?:of\s+)?{_MONTH}\b))(?!\d)"
    rf"|\s+\d{{2}}(?=\s*(?:[,.;:!?)]|$)|{_NEXT_DAY})|\s*,\s*\d{{2}}(?={_NEXT_DAY}))"
)
#: The fiscal or calendar year with nothing to say which -- "the fiscal year", "the
#: financial quarter", "the FY" -- as a period something is taken from: the one in
#: progress, as "this fiscal year" is. REGRESSION: "the last month of the fiscal year" was
#: last month, and "the end of the fiscal year" nothing.
_THE_BASIS_UNIT = (
    rf"(?:{_BASIS_WORD}\s+{_UNIT}|{_FY_WORD}|{_CY_WORD})(?![\w'])(?!\s*-?\s*'?\d)"
)
_FULL_YEAR_WORD = {"f": "fiscal year", "c": "calendar year"}
#: The same, named from this one: "the fiscal year", "last FY", "the following FY".
_NAMED_BASIS_UNIT = (
    rf"(?:(?:the\s+)?(?:{_DIRWORD}|this|current)\s+|the\s+)?{_THE_BASIS_UNIT}"
)
_PART_TARGET = (
    rf"(?:{_MONTH}(?:{_MONTH_JOINED_YEAR}|{_YEAR_SUFFIX})"
    rf"|{_QWORD}\s*[1-4](?!\d){_YEAR_SUFFIX}|[1-4]\s*q(?![a-z]){_YEAR_SUFFIX}"
    rf"|(?:the\s+)?{_ORD}\s+(?:quarter|qtr){_YEAR_SUFFIX}"
    rf"|h\s*[12](?!\d){_YEAR_SUFFIX}|{_DECADE}|{_NUMERIC_MONTH}|{_YEAR}(?![-/.]\d)"
    rf"|(?:the\s+)?{_DIRWORD}\s+(?:{_BASIS_WORD}\s+)?{_UNIT}"
    rf"|(?:the\s+)?(?:this|current)\s+(?:{_BASIS_WORD}\s+)?{_UNIT}"
    rf"|{_NAMED_BASIS_UNIT}|(?:the\s+)?{_UNIT})"
)


def _part_of(text: str) -> Part | None:
    for pattern, part in _PART_WORDS:
        if re.match(rf"\s*(?:the\s+)?(?:{pattern})\b", text, re.IGNORECASE):
            return part
    return None


#: What separates a part from its period: "mid-March" and "mid March" are the same phrase,
#: and "end of the month" carries an article the period itself does not want.
_OF = r"(?:\s*-\s*|\s+)(?:(?:of|in)(?:\s*-\s*|\s+))?(?:the\s+)?"  # also "end-of-month"


def _target_spec(tail: str, cfg: WranglerConfig) -> Spec | None:
    """Read the period a part or ordinal is being taken out of."""
    tail = tail.strip()
    if not tail:
        return None
    if re.fullmatch(_ENDING_TARGET, tail, re.IGNORECASE):
        # "the quarter ended June 2024", "FY ending March 2025": the period-ending rule's
        # own reader, so "the last day of the quarter ended June 2024" is 30 June 2024.
        ending = _p_period_ending(tail, cfg)
        if ending is not None:
            return ending
    if re.fullmatch(_DECADE, tail, re.IGNORECASE):
        return _p_decade(tail, cfg)  # "the early 1990s", "mid-1990s"
    tail = re.sub(r"^the\s+", "", tail, flags=re.IGNORECASE)  # "the 1st of the next month"
    if re.fullmatch(_NUMERIC_MONTH, tail):
        return _p_numeric_month(tail, cfg)  # "the end of 2024-03"
    dashed = re.fullmatch(
        rf"({_MONTH})\.?(?:[-/.]?(\d{{4}}|\d{{2}})|\s*,?\s*(\d{{2}}))", tail, re.IGNORECASE
    )
    if dashed:
        # "start of Mar-24", "15 of March24", "the 15th of March 24": March 2024. And "15
        # of March, 24", which only reaches here before the next date. REGRESSION: the year
        # was lost, and it was this year's March.
        return Spec(
            Kind.ABS_MONTH, month=MONTHS[dashed.group(1).lower()],
            year=_pivot_year(dashed.group(2) or dashed.group(3), cfg),
        )
    if find_month(tail) is not None:
        # The month rule's own reader, so "the first week of June FY25" is June 2024 with
        # an April year, the same as "June FY25" is.
        return _p_month(tail, cfg)
    # Quarters and halves divide as neatly as months do -- "early Q1" is its first month.
    for reader in (_p_quarter, _p_half):
        spec = reader(tail, cfg)
        if spec is not None:
            return spec
    # The rules' own readers, so a target understands exactly what the rule does --
    # "end of this fiscal year" and "first day of next fiscal quarter" included. A
    # private copy of these regexes is how the basis word got missed here before.
    for reader in (_p_relative, _p_this):
        spec = reader(tail, cfg)
        if spec is not None:
            return spec
    named = re.fullmatch(
        rf"\s*(?:({_DIRWORD}|this|current)\s+)?({_THE_BASIS_UNIT})\s*", tail, re.IGNORECASE
    )
    if named:
        # "the fiscal year" is the one in progress; "last FY" the fiscal year before it.
        word, unit = (named.group(1) or "this").lower(), named.group(2)
        # Each way of naming the year as the readers below take it: "FY", "tax year" and
        # "fiscal-year" as "fiscal year", "CY" as "calendar year".
        if re.fullmatch(_FY_WORD, unit, re.IGNORECASE):
            unit = "fiscal year"
        elif re.fullmatch(_CY_WORD, unit, re.IGNORECASE):
            unit = "calendar year"
        reader = _p_this if word in ("this", "current") else _p_relative
        return reader(f"{word} {unit}", cfg)
    year, basis = _year_from_suffix(tail, cfg)
    if year is not None:
        # This is always a year with a part taken out of it -- "the second half of 2024",
        # "early 2024", "the first 3 months of 2024" -- because a year standing alone never
        # reaches here. Such a year follows of_year_basis, which inherits year_basis, so
        # "the second half of 2024" and "the last quarter of 2024" are read on the same
        # calendar: they used to disagree, one forced to the calendar year and the other
        # following the fiscal setting. A year that says what it is -- FY2024, CY2024 --
        # keeps the basis it states.
        if basis is None and re.fullmatch(
            rf"\s*(?:the\s+)?(?:year\s+)?{_BARE_YEAR}\s*", tail, re.IGNORECASE
        ):
            basis = cfg.effective_of_year_basis
        return Spec(Kind.ABS_YEAR, year=year, basis=basis)
    bare = re.fullmatch(rf"\s*({_UNIT})\s*", tail, re.IGNORECASE)
    if bare:
        unit = _unit_of(bare.group(1))
        if unit is not None:
            return Spec(Kind.THIS_PERIOD, unit=unit)
    return None


def _p_part_of(text: str, cfg: WranglerConfig) -> Spec | None:
    """"first half of March", "late 2024", "end of next month".

    Without this "first half of March" scanned as a fiscal H1 plus a stray March, and the
    H1 won -- so a phrase about two weeks in March resolved to six months in April.
    """
    part = _part_of(text)
    if part is None:
        return None
    m = re.match(rf"\s*(?:the\s+)?(?:{_PART_ALT}){_OF}(.+)$", text, re.IGNORECASE)
    if not m:
        return None
    target = _target_spec(m.group(1), cfg)
    if target is None:
        return None
    return target.with_(part=part)


_WEEKDAY_ALT = alt(WEEKDAY_NAMES)
#: An ordinal that may also count from the end: "third Thursday", "last Friday".
_NTH = rf"(?:{_ORD}|last|final)"
#: These two rules insist on the preposition, and a bare unit target has to carry "the".
#: Without that, "the first Monday in months" -- ordinary English, not a date at all --
#: reads as the first Monday of this month.
_NTH_OF = r"\s+(?:of|in)\s+"
#: Where a period named by its end stops: a day, a month or a year.
_ENDING_AT = (
    rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-](?:\d{{4}}|\d{{2}})"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{4}})?"
    rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?!\d)(?:,?\s+\d{{4}})?"
    rf"|{_MONTH}{_YEAR_SUFFIX}|{_YEAR})"
)
#: A period named by where it ends, as something is counted in or taken out of: "the half
#: year ending June 2024", "FY ending March 2025", "the quarter ended 30 June 2024".
#: REGRESSION: "week 3 of the half year ending June 2024" took "the half" -- this year's --
#: and "month 3 of the fiscal year ending March 2025" was the 3rd *day* of this one.
_ENDING_TARGET = (
    rf"(?:the\s+)?(?:half[\s-]?years?|{_FY_WORD}|{_CY_WORD}|{_UNIT})(?:\s+period)?"
    rf"(?:\s+|-)end(?:ing|ed)\s+{_ENDING_AT}"
)
_NTH_TARGET = (
    rf"(?:{_ENDING_TARGET}|{_MONTH}{_YEAR_SUFFIX}|{_QWORD}\s*[1-4](?!\d){_YEAR_SUFFIX}"
    rf"|h\s*[12](?!\d){_YEAR_SUFFIX}|{_NUMERIC_MONTH}|(?:{_THE_YEAR})?{_YEAR}(?![-/.]\d)"
    rf"|(?:the\s+)?(?:{_DIRWORD}|this|current)\s+(?:{_BASIS_WORD}\s+)?{_UNIT}|the\s+{_UNIT}"
    rf"|{_NAMED_BASIS_UNIT}"
    # A single unit needs no article: "last day of month". REGRESSION: it fell through to
    # "last day" -- yesterday. Plurals still do: "the first Monday in months" is not a date.
    r"|(?:month|mth|quarter|qtr|year|yr|week|wk)(?![a-z]))"
)
#: Not the head of a period named by where it ends: in "week 3 of the half year ending June
#: 2024" the period is not "the half", nor in "week 12 of FY ending March 2025" "FY".
#: REGRESSION: each was this year's half or fiscal year, and the period-ending phrase lost.
_NOT_ENDING = r"(?![\s-]+(?:(?:half[\s-]?)?years?[\s-]+|period\s+)?end(?:ing|ed|s)?\b)"


def _nth_index(word: str) -> int | None:
    """An ordinal as a signed index. "last" and "final" count back from the end."""
    if word.lower() in ("last", "final"):
        return -1
    return ordinal_to_int(word)


def _nth_target(tail: str, cfg: WranglerConfig) -> Spec | None:
    """The period an nth-of phrase indexes into. The article is ours, not the period's."""
    return _target_spec(re.sub(r"^\s*the\s+", "", tail, flags=re.IGNORECASE), cfg)


def _p_nth_weekday(text: str, cfg: WranglerConfig) -> Spec | None:
    """"third Thursday of November", "last Friday of the month".

    Before this the weekday and the period were separate matches and the period won, so
    "first Monday of March" answered with all 31 days of March -- confidently, since
    nothing was left over for the safety net to notice. "last Friday of the month" was
    worse: the weekday rule read it as the last Friday *before today* and dropped the
    month, giving a date three weeks out.
    """
    m = re.match(
        rf"\s*(?:the\s+)?({_NTH})\s+({_WEEKDAY_ALT})\.?{_NTH_OF}(.+)$", text, re.IGNORECASE
    )
    if not m:
        return None
    index = _nth_index(m.group(1))
    # A month holds at most five of any weekday, and a year fifty-three; beyond five this
    # is not a date phrase.
    if index is None or index == 0 or index > 5:
        return None
    target = _nth_target(m.group(3), cfg)
    if target is None:
        return None
    return target.with_(nth_weekday=(index, WEEKDAYS[m.group(2).lower()]))


def _p_edge_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the last day of the month", "first day of next quarter".

    "last day" on its own was read as "last 1 day" -- yesterday -- and the period it
    belonged to was discarded, so the answer was not a rounding of the right date but a
    different one entirely.
    """
    m = re.match(
        rf"\s*(?:the\s+)?(first|1st|last|final)\s+({_EDGE_BIZ})?(?:day|date){_NTH_OF}(.+)$",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    target = _nth_target(m.group(3), cfg)
    if target is None:
        return None
    # "the last business day of the month" is the close date, and the calendar last day is
    # wrong for it whenever that lands on a weekend.
    return target.with_(
        day_of_period=1 if m.group(1).lower() in ("first", "1st") else -1,
        pick_business_day=m.group(2) is not None,
    )


#: What people write in front of a week number. "cw"/"kw" are the German and Scandinavian
#: abbreviations, which turn up in any supply chain that touches Europe.
_WEEK_WORD = r"(?:calendar\s+week|cal\s+week|week|wk|cw|kw)"
#: The words that say a week is a calendar -- ISO -- week, whatever year follows: "KW3
#: FY25" is ISO week 3 of 2025, never the fiscal year's third. REGRESSION: it was.
_CALENDAR_WEEK = r"(?:calendar\s+week|cal\s+week|cw|kw)"
#: "week 42, 2026" is how a spreadsheet heading reads; the comma is not the
#: period's, so it is allowed here and stripped before the year is read. A tight dash or
#: slash joins the year as it does a quarter's: REGRESSION -- "W11-2024" and "week 11-2024"
#: ran from the week to the end of 2024.
_WEEK_YEAR_SUFFIX = rf"(?:[-/](?:19|20)\d{{2}}(?!\d)|(?:\s*,)?{_YEAR_SUFFIX})"
#: "week 11", "week #11", "week no. 11", "week number 11". REGRESSION: "week number 11 of
#: 2024" was the 11th of January.
_WEEK_NO = r"\s*\.?\s*(?:#|no\.?|num(?:ber)?\.?)?\s*"
#: "2024-W11", and the year in front of a week written out: "2024wk11", "2024 week 11".
#: REGRESSION: "2024 week 11" was week 11 of this year, and "2024-wk11" ran from the
#: start of 2024 to the week.
_YEAR_WEEK = r"((?:19|20)\d{2})\s*-?\s*(?:w|wk|week)\s*(\d{1,2})\b"


def _p_iso_week_compact(text: str, cfg: WranglerConfig) -> Spec | None:
    """"2026-W42", "2026W42" -- the ISO 8601 form, which carries its own year."""
    m = re.match(rf"\s*{_YEAR_WEEK}", text, re.IGNORECASE)
    if not m:
        return None
    week = int(m.group(2))
    if not 1 <= week <= 53:
        return None
    return Spec(Kind.ISO_WEEK, year=int(m.group(1)), index=week)


def _p_week_number(text: str, cfg: WranglerConfig) -> Spec | None:
    """"week 42", "wk 42", "CW42", "week 42 2026" -- and "week 3 FY25", a fiscal week.

    A week number is an ISO week, whatever year_basis says, unless the year after it is a
    fiscal one: then it is that year's third week, as "the third week of FY25" is. Weeks
    counted "of" or "in" something -- "week 3 of FY25", "week 3 of January" -- are the
    counted rule's, which comes first.
    """
    m = re.match(rf"\s*({_WEEK_WORD}){_WEEK_NO}(\d{{1,2}})\b(.*)$", text, re.IGNORECASE)
    if not m:
        return None
    week, tail = int(m.group(2)), m.group(3)
    if not 1 <= week <= 53:
        return None
    if _SAYS_FISCAL.search(tail) and not _says_calendar_week(m.group(1)):
        target = _fiscal_year_of_tail(tail, cfg)
        return None if target is None else target.with_(sub_period=(week, 1, Grain.WEEK))
    return _iso_week_of(tail, cfg).with_(index=week)


def _says_calendar_week(word: str) -> bool:
    return re.fullmatch(_CALENDAR_WEEK, word.strip(), re.IGNORECASE) is not None


def _iso_week_of(tail: str, cfg: WranglerConfig) -> Spec:
    """An ISO week in the year the words after its number name -- written, "week 3 2025";
    named from this one, "week 3 of next year"; or neither, this year -- with no number yet.
    REGRESSION: "week 3 of next year" and "week 3 last year" were this year's week 3."""
    year, _ = _year_from_suffix(tail.lstrip(" ,-/"), cfg)
    offset = None
    if year is None:
        rel = _relative_year_of_suffix(tail)
        offset = None if rel is None else rel[0]
    return Spec(Kind.ISO_WEEK, year=year, year_offset=offset)


#: A fiscal year said outright: "fiscal", "financial", "FY", "F.Y.", "tax year".
_SAYS_FISCAL = re.compile(
    rf"\b(?:fiscal|financial)\b|\bf\.?y\.?(?![a-z])|\b{_TAX_YEAR}\b", re.IGNORECASE
)
#: The words that make a numbered week or month the fiscal year's: "fiscal week 3", "FY
#: week 3", "tax week 3" -- a tax year's week, as payroll counts them.
_FISCAL_LEAD = r"(?:fiscal|financial|tax|f\.?y\.?)"
#: "fiscal week", "FY week", "financial wk", "tax week": a week counted in a fiscal year.
_FISCAL_WEEK_WORD = rf"{_FISCAL_LEAD}[\s-]*(?:week|wk)"


def _is_year(spec: Spec) -> bool:
    return spec.kind is Kind.ABS_YEAR or spec.unit is Grain.YEAR


def _fiscal_year_of_tail(tail: str, cfg: WranglerConfig) -> Spec | None:
    """The fiscal year a numbered week or month is counted in, from the words after it.

    "FY25" and, after "fiscal week", a plain "2025" are that fiscal year; "the fiscal
    year", "FY" and "last FY" the one in progress and the one before it; nothing at all
    the one in progress, its year unwritten -- so a guess, as "week 3" is. A calendar year
    is no fiscal year to count in, and gives None, as anything not a year does.

    Read off the words, not through year_basis: after "fiscal week" they have said which
    kind of year already.
    """
    rest = re.sub(r"^[\s,/-]*(?:(?:of|in)\s+)?", "", tail, flags=re.IGNORECASE).strip()
    if not rest:
        return Spec(Kind.ABS_YEAR, basis=Basis.FISCAL)
    if re.search(r"\b(?:calendar|c\.?y\.?)(?![a-z])", rest, re.IGNORECASE):
        return None
    target = _nth_target(rest, cfg)
    if target is None or not _is_year(target):
        return None
    return target.with_(basis=Basis.FISCAL)


#: Between the two numbers of a run of weeks: "week 11-12", "weeks 11 to 12".
_WEEK_JOIN = r"\s*(?:-|to|through|thru|till|until)\s*"
#: Between numbers listed: "3, 4 and 5", "3 & 5".
_LIST_JOIN = r"\s*(?:,\s*(?:and\s+|&\s*)?|and\s+|&\s*)"
#: Between the two ends of a run: "3-5", "3 to 5".
_SPAN_WORDS = re.compile(r"-|\b(?:to|through|thru|till|until)\b", re.IGNORECASE)


def _said(unit: str) -> str:
    """The unit said again before a later number, with a fiscal word or without: "week 5",
    "weeks 5", "fiscal week 5" -- "week 3 to fiscal week 5" is one run of fiscal weeks, as
    "fiscal week 3 to week 5" is. REGRESSION: it joined an ISO week to a fiscal one."""
    return rf"(?:{_FISCAL_LEAD}[\s-]*)?{unit}s?{_WEEK_NO}"


def _run(unit: str) -> str:
    """The numbers after a unit word: "3", "s 3-5", " 3 to week 5", "s 3, 4 and 5", " 3
    and week 5". A list needs the unit plural or said again -- "week 3 and 5 people" is
    one week -- and a run joins two."""
    one = rf"{_WEEK_NO}\d{{1,2}}\b"
    said = _said(unit)
    span = rf"{_WEEK_JOIN}(?:{said})?\d{{1,2}}\b"
    return (
        rf"(?:s{one}(?:{span}|(?:{_LIST_JOIN}(?:{said})?\d{{1,2}}\b)+)?"
        rf"|{one}(?:{span}|(?:{_LIST_JOIN}{said}\d{{1,2}}\b)+)?)"
    )


_WEEK_RUN = _run(r"(?:week|wk)")
_MONTH_RUN = _run(r"(?:month|mth)")
#: The numbers a fiscal year holds: weeks 1 to 53, months 1 to 12.
_WEEK_NUMBER = r"(?:0?[1-9]|[1-4]\d|5[0-3])"
_MONTH_NUMBER = r"(?:0?[1-9]|1[0-2])"


def _whole_run(unit: str, number: str) -> str:
    """The same numbers, where nothing may follow them: never a shorter run than is there.
    Taken one number short, "FY25 weeks 3-5 of Q2" was week 3 of FY25 to the 5th day of Q2,
    and "FY25 weeks 3 and 5 of Q2" week 3 and that day.

    And only numbers a year holds: past them the fiscal year is read alone, as "FY24 M13"
    is. REGRESSION: "FY24 month 13", an ERP's adjustment period, and "FY24 week 0" lost
    FY24 to a refusal."""
    one = rf"{_WEEK_NO}{number}\b"
    said = _said(unit)
    span, any_span = rf"{_WEEK_JOIN}(?:{said})?{number}\b", rf"{_WEEK_JOIN}(?:{said})?\d"
    bare, any_bare = rf"{_LIST_JOIN}(?:{said})?{number}\b", rf"{_LIST_JOIN}(?:{said})?\d"
    again, any_again = rf"{_LIST_JOIN}{said}{number}\b", rf"{_LIST_JOIN}{said}\d"
    return (
        rf"(?:s{one}(?:{span}|(?:{bare})+)?(?!{any_span}|{any_bare})"
        rf"|{one}(?:{span}|(?:{again})+)?(?!{any_span}|{any_again}))"
    )


#: Numbered weeks or months straight after a fiscal year -- "FY25 week 3", "FY24 months
#: 3-5" -- and counted in nothing else: "FY24 week 3 of Q2" is Q2's week, not FY24's.
_FY_WEEKS = (
    rf"\s*(?:(?:fiscal|financial|tax)\s+)?(?:week|wk)"
    rf"{_whole_run('(?:week|wk)', _WEEK_NUMBER)}(?!\s*(?:of|in)\b)"
)
_FY_MONTHS = (
    rf"\s*(?:(?:fiscal|financial|tax)\s+)?(?:month|mth)"
    rf"{_whole_run('(?:month|mth)', _MONTH_NUMBER)}(?!\s*(?:of|in)\b)"
)
#: A fiscal year's month by its number, as reports head a column: "FY24 M3", "FY24-M03",
#: runs "FY24 M3-M5" and "FY24 M3 to 5", lists "FY24 M3 and M5". Only after the fiscal
#: year and only 1 to 12: before one -- "BMW M3 FY24", "500 m3 FY24" -- or past twelve --
#: "FY24 M25 widening" -- it is a model, a measure or a road, and the fiscal year is read
#: alone. Nor cut short: "FY24 M3-M15" is not month 3 with "-M15" left over.
_M_NUMBER = r"(?:0?[1-9]|1[0-2])(?!\w)"
_M_RUN = (
    rf"m{_M_NUMBER}(?:{_WEEK_JOIN}m?{_M_NUMBER}|(?:{_LIST_JOIN}m{_M_NUMBER})+)?"
    rf"(?!{_WEEK_JOIN}m?\d|{_LIST_JOIN}m\d)"
)
#: After the fiscal year, as the month_counted rule takes it: not counted in something
#: else, since "FY24 M3 of Q2" is not FY24's.
_FY_M = rf"\s*-?\s*{_M_RUN}(?!\s*(?:of|in)\b)"
#: "M3" might be a money-supply measure -- "FY24 M3 growth" -- so it is read a little below
#: certain, as a bare "2024" is.
_M_CONFIDENCE = 0.8
#: What numbered weeks or months are counted in: "of FY25", "in January", "of the fiscal
#: year", "of next quarter".
_COUNTED_IN = r"\s+(?:of|in)\s+(?:the\s+)?"


def _weeks_in(target: Spec) -> int:
    """The most seven-day weeks, counted from its first day, a period can start: five in a
    month, fourteen in a quarter, 27 in a half, 53 in a year -- the last of each cut short
    where the period ends."""
    if _is_year(target):
        return 53
    if target.kind is Kind.ABS_QUARTER or target.unit is Grain.QUARTER:
        return 14
    if target.kind is Kind.ABS_HALF or target.unit is Grain.HALF:
        return 27
    return 5


def _count_limit(unit: Grain, target: Spec) -> tuple[int, str]:
    """How many of ``unit`` the period holds, and what to call it in a refusal. Weeks are
    seven days from its first day, as "the 3rd week of January" counts them."""
    if _is_year(target):
        holder, months = "a year", 12
    elif target.kind is Kind.ABS_QUARTER or target.unit is Grain.QUARTER:
        holder, months = "a quarter", 3
    elif target.kind is Kind.ABS_HALF or target.unit is Grain.HALF:
        holder, months = "a half", 6
    else:
        holder, months = "a month", 0
    return (_weeks_in(target) if unit is Grain.WEEK else months), holder


def _counted_in(
    tail: str, cfg: WranglerConfig, unit: Grain, fiscal: bool
) -> tuple[Spec | None, Spec | None] | None:
    """What numbered weeks or months are counted in: (period, None), or (None, an ISO week
    with no number yet) for ISO weeks; None when the words name nothing they can be counted
    in.

    "fiscal week 3 of January" and "week 3 of Q1" are the period's own weeks; "week 3 of
    FY25" and "fiscal week 3 2025" the fiscal year's. "week 3 of 2025", with nothing fiscal
    about it, is an ISO week -- whatever year_basis says, as week numbers always are.
    """
    rest = re.sub(r"^[\s,/-]*(?:(?:of|in)\s+)?", "", tail, flags=re.IGNORECASE).strip()
    period = _nth_target(rest, cfg) if rest else None
    # A year named by where it ends -- "the year ending March 2025" -- is no calendar year
    # with ISO weeks: it holds its own weeks and months, as a quarter does.
    if period is not None and (not _is_year(period) or period.kind is Kind.PERIOD_ENDING):
        return period, None
    if fiscal:
        target = _fiscal_year_of_tail(tail, cfg)
        return None if target is None else (target, None)
    if period is None:
        return None
    if unit is Grain.MONTH:
        return period, None
    if _SAYS_FISCAL.search(rest):
        target = _fiscal_year_of_tail(tail, cfg)
        return None if target is None else (target, None)
    return None, _iso_week_of(rest, cfg)


def _p_counted(text: str, cfg: WranglerConfig, unit: Grain) -> Spec | Parts | None:
    """Weeks or months numbered into a period: "fiscal week 3", "week 3 of FY25", "FY25
    weeks 3-5", "month 3 of FY24", "months 3-5 of FY24", "week 3 and week 5 of FY25".

    Each is read exactly as "the 3rd week of FY25" or "the 3rd month of FY24" already was
    -- a fiscal week is seven days counted from the fiscal year's first day -- and a run or
    a list is read whole, every number in the same period. REGRESSION: "fiscal week 3" was
    ISO week 3 of the calendar year, "week 3 of FY25" of calendar 2025, "month 3 of FY24"
    the 3rd *day* of FY24, and "week 3 of January" ISO week 3 beside all of January.

    "FY24 M3" is "month 3 of FY24" too, a little below certain: see _M_CONFIDENCE.
    REGRESSION: it was FY24, M3 left over.
    """
    # A calendar week -- "CW 3", "KW3", "calendar week 3" -- is an ISO week, and is read by
    # week_number whatever follows it.
    word = r"(?:week|wk)" if unit is Grain.WEEK else r"(?:month|mth)"
    m = re.match(
        rf"\s*(?:(?P<fy>{_FY_YEAR})\s*(?:(?:fiscal|financial|tax)\s+)?{word}"
        rf"|(?:the\s+)?(?P<fiscal>{_FISCAL_LEAD}[\s-]*)?{word})"
        rf"(?P<run>{_WEEK_RUN if unit is Grain.WEEK else _MONTH_RUN})(?P<tail>.*)$",
        text,
        re.IGNORECASE,
    )
    #: The unit said again before a later number, where that number's date starts.
    again_at = rf"{_said(word)}$"
    confidence = 1.0
    if m is None and unit is Grain.MONTH:
        m = re.fullmatch(rf"\s*(?P<fy>{_FY_YEAR})\s*-?\s*(?P<run>{_M_RUN})\s*", text, re.I)
        again_at, confidence = r"m$", _M_CONFIDENCE
    if not m:
        return None
    fy, tail = m.groupdict().get("fy"), m.groupdict().get("tail") or ""
    # Fiscal when a fiscal word leads, or stands at either end of a run: "week 3 to fiscal
    # week 5" as well as "fiscal week 3 to week 5".
    fiscal = m.groupdict().get("fiscal") is not None or re.search(
        rf"{_FISCAL_LEAD}[\s-]*{word}", m.group("run"), re.IGNORECASE
    ) is not None
    base = m.start("run")
    numbers = [
        (int(n.group()), base + n.start(), base + n.end())
        for n in re.finditer(r"\d{1,2}", m.group("run"))
    ]
    iso: Spec | None = None
    if fy:
        year, basis = parse_year_token(fy, cfg)
        if year is None:
            return None
        period: Spec | None = Spec(Kind.ABS_YEAR, year=year, basis=basis or Basis.FISCAL)
    else:
        counted = _counted_in(tail, cfg, unit, fiscal)
        if counted is None:
            if fiscal and re.search(
                r"\b(?:calendar|c\.?y\.?)(?![a-z])", tail, re.IGNORECASE
            ):
                raise ValueError(
                    f"a fiscal {unit.value} is counted in a fiscal year, not a calendar one"
                )
            return None
        period, iso = counted
    # A run has two ends, the first before the last.
    is_run = len(numbers) > 1 and _SPAN_WORDS.search(text[numbers[0][2] : numbers[1][1]])
    if is_run and len(numbers) != 2:
        return None
    if is_run and numbers[0][0] >= numbers[1][0]:
        raise ValueError(
            f"a run of {unit.value}s goes from the earlier to the later, and "
            f"{numbers[0][0]} is not before {numbers[1][0]}"
        )
    specs = []
    for n, _, _ in numbers:
        if period is None:
            if iso is None or not 1 <= n <= 53:
                return None
            specs.append(iso.with_(index=n))
            continue
        limit, holder = _count_limit(unit, period)
        if not 1 <= n <= limit:
            if unit is Grain.MONTH and holder == "a year":
                raise ValueError(f"a year has twelve months, and no month {n}")
            raise ValueError(f"{holder} has no {unit.value} {n} in it")
        specs.append(period.with_(sub_period=(n, 1, unit), confidence=confidence))
    if len(specs) == 1:
        return specs[0]
    # Each its own date, joined as "weeks 11-12" or "Q1 and Q3" are: the first from the
    # start of the words, the last to their end, the rest the number alone -- with its
    # unit when that is said again, so that only "to" or "and" lies between: "month 3 to
    # month 5" joins as "month 3" to "month 5 of FY24".
    starts = [0]
    for _, lo, _ in numbers[1:]:
        again = re.search(again_at, text[:lo], re.IGNORECASE)
        starts.append(again.start() if again else lo)
    last = len(specs) - 1
    end = len(text.rstrip())
    return [
        (spec, start, end if i == last else hi)
        for i, (spec, start, (_, _, hi)) in enumerate(zip(specs, starts, numbers, strict=True))
    ]


def _p_week_counted(text: str, cfg: WranglerConfig) -> Spec | Parts | None:
    return _p_counted(text, cfg, Grain.WEEK)


def _p_month_counted(text: str, cfg: WranglerConfig) -> Spec | Parts | None:
    return _p_counted(text, cfg, Grain.MONTH)


def _p_week_span(text: str, cfg: WranglerConfig) -> Parts | None:
    """"week 11-12", "weeks 11 to 12 2024": both ends, each its own, joined as a span.

    REGRESSION: "week 11-12" was week 11 alone, and "weeks 11-12" nothing.
    """
    m = re.match(
        rf"\s*({_WEEK_WORD})s?{_WEEK_NO}(\d{{1,2}}){_WEEK_JOIN}(\d{{1,2}})\b(.*)$",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    first, last = int(m.group(2)), int(m.group(3))
    if not 1 <= first < last <= 53:
        return None
    if _SAYS_FISCAL.search(m.group(4)) and not _says_calendar_week(m.group(1)):
        # "weeks 3-5 of FY25": fiscal weeks, as "week 3 of FY25" is one.
        target = _fiscal_year_of_tail(m.group(4), cfg)
        if target is None:
            return None
        return [
            (target.with_(sub_period=(first, 1, Grain.WEEK)), 0, m.end(2)),
            (target.with_(sub_period=(last, 1, Grain.WEEK)), m.start(3), len(text.rstrip())),
        ]
    iso = _iso_week_of(m.group(4), cfg)
    return [
        (iso.with_(index=first), 0, m.end(2)),
        (iso.with_(index=last), m.start(3), len(text.rstrip())),
    ]


def _p_week_bare(text: str, cfg: WranglerConfig) -> Spec | None:
    """"W42" with nothing to say it is a week.

    Weak on purpose: an uppercase W and two digits is just as likely to be a part number,
    a room or a bus route, so in balanced mode this needs a cue the way a bare month does.
    """
    # The scanner lowers ASCII text before matching, so the pattern cannot test case --
    # the fragment handed here keeps the original, which is where the check belongs.
    # Same arrangement as the bare COB acronym.
    if not text.lstrip().startswith("W"):
        return None
    m = re.match(r"\s*W\s*(\d{1,2})\b(.*)$", text)
    if not m:
        return None
    week = int(m.group(1))
    if not 1 <= week <= 53:
        return None
    return _iso_week_of(m.group(2), cfg).with_(index=week)


#: "business day", "working days", "trading day". Weekend and holidays come from config.
_BIZ_WORD = r"(?:business|working|trading|work|biz)"
#: "business days", "working days", "biz days", "business-days", "workdays", "bus. days".
_BIZ = rf"(?:{_BIZ_WORD}[\s-]+days?|workdays?|bus\.\s*days?)"
#: The business-day words before "day" in "the last business day of": "business day",
#: "working day", "workday", "biz day", "bus. day".
_EDGE_BIZ = rf"(?:{_BIZ_WORD}|bus\.)[\s-]*"


def _p_business_relative(text: str, cfg: WranglerConfig) -> Spec | None:
    """"last 10 business days", "next business day"."""
    m = re.match(rf"\s*({_DIRWORD})\s+(?:({_NUM})\s+)?{_BIZ}\b", text, re.IGNORECASE)
    if not m:
        return None
    count = word_to_int(m.group(2)) if m.group(2) else 1
    if count is None:
        return None
    return Spec(
        Kind.RELATIVE, count=count, unit=Grain.DAY,
        direction=_direction_of(m.group(1)), business=True,
    )


def _p_business_ago(text: str, cfg: WranglerConfig) -> Spec | None:
    """"5 business days ago", "3 working days from now", "in 2 business days"."""
    m = re.match(rf"\s*in\s+({_NUM})\s+{_BIZ}\b", text, re.IGNORECASE)
    if m:
        count, direction = word_to_int(m.group(1)), 1
    else:
        m = re.match(
            rf"\s*({_NUM})\s+{_BIZ}\s+(from\s+(?:now|today)|{_AGO_WORDS})\b",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        tail = m.group(2).lower()
        direction = 1 if tail.startswith("from") or tail in _AGO_FUTURE else -1
        count = word_to_int(m.group(1))
    if count is None:
        return None
    return Spec(Kind.AGO, count=count, unit=Grain.DAY, direction=direction, business=True)


#: Units finer than a day, and how many of them: "last 3 hours", "half an hour ago".
_SUB_DAY = r"(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)"
_SUB_DAY_COUNT = rf"(?:{_NUM}|an?|half\s+an?|a\s+few|few|several|(?:a\s+)?couple\s+of)"
#: What makes a number a quantity, not a year, when it follows: "12 months", "90 days", "24
#: hours", "15 percent", "15%".
_QUANTITY = (
    rf"\s*(?:%|(?:{_UNIT}|{_SUB_DAY}|percent|per\s*cent|pct|bps|basis\s+points?|times)\b)"
)
#: The direction a period's edge is named from: "last FYE", "next quarter end", "this month
#: end".
_EDGE_DIR = rf"(?:(?:the\s+)?(?:{_DIRWORD}|this|current)\s+)?"
#: A year's end after the year: "2024 year end", "2024 fiscal year close", "FY24 FYE".
_YEAR_END_AFTER = (
    rf"\s+(?:(?:(?:{_BASIS_WORD}\s+)?year|{_FY_WORD}|{_CY_WORD})[\s-]+(?:end(?:ing|ed)?|close)"
    rf"|f\.?y\.?e)\b"
)
#: Not straight after a basis word, whose own form reads the unit: "fiscal year end 6/30"
#: is not the calendar year's end with "fiscal" dropped. REGRESSION: it was.
_NO_BASIS_BEFORE = "".join(
    rf"(?<!{word}{sep})" for word in ("fiscal", "financial", "calendar", "tax") for sep in " -"
)


def _p_sub_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"last 3 hours", "in 30 minutes", "2 hours ago" -- read only to say why not.

    The library answers in whole days, so a period of hours is not resolved. It used to
    vanish without a word, which looks exactly like text that holds no date; reading it
    to decline gives the caller the reason instead.
    """
    raise ValueError(
        "shorter than a day: date-wrangler answers in whole days, not hours, minutes or "
        "seconds, so this is not resolved"
    )


#: A single day in the forms people write after "the week of".
_ONE_DAY = (
    r"(?:\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}(?:,?\s+\d{{4}})?"
    rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?!\d)(?:,?\s*\d{{4}})?"
    r"|\d{1,2}(?:st|nd|rd|th)(?!\s*\w))"  # "the week of the 5th": a day of this month
)
_ONE_MONTH = rf"{_MONTH}(?:\s+(?:of\s+)?(?:{_FY_WORD}{_SEP}'?\d{{2,4}}|\d{{4}}))?"
_WITHIN_HEAD = re.compile(
    r"\s*(?:the\s+)?(?:(?P<unit>week|wk|month|mth|quarter|half|year)\s+of"
    r"|(?P<from>(?:week|wk)\s+(?:commencing|beginning|starting)|w/c|w\.c\.?|wc))\s+(?:the\s+)?"
    rf"(?:{_WEEKDAY_ALT},?\s+)?",  # "week commencing Monday 5 October"
    re.IGNORECASE,
)


def _p_within(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the week of 5 October", "the quarter of June", "week commencing 5 October".

    REGRESSION: only the date was read, so "the week of 5 October" was that one day at
    full confidence. "of" names the period that holds the date; "commencing" (or
    "beginning", "starting", "w/c") the seven days from it.
    """
    head = _WITHIN_HEAD.match(text)
    if head is None:
        return None
    rest = text[head.end() :]
    # Pick the reader by shape: "June 2024" must not be read as 20 June.
    if re.match(r"\d{4}-", rest):
        spec = _p_iso(rest, cfg)
    elif re.match(r"\d{1,2}[/.]", rest):
        spec = _p_numeric(rest, cfg)
    elif re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)\s*", rest, re.IGNORECASE):
        spec = _p_ordinal_day(rest, cfg)
        spec = spec.with_(confidence=1.0) if spec is not None else None  # "week of" says so
    elif re.match(r"\d", rest):
        spec = _p_day_month_year(rest, cfg)
    elif re.match(rf"{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?!\d)", rest, re.IGNORECASE):
        spec = _p_month_day_year(rest, cfg)
    else:
        spec = _p_month(rest, cfg)
    if spec is None:
        return None
    if head.group("from"):
        one_day = spec.kind is Kind.ABS_DAY or spec.day_of_period is not None
        return spec.with_(week_from=True) if one_day else None
    return spec.with_(within=_unit_of(head.group("unit")))


#: The units a run can be measured in, "the first 3 months of", "the last week of".
_SUB_UNIT = r"(?:days?|weeks?|wks?|fortnights?|months?|mths?|mnths?|mos?|quarters?|qtrs?)"
#: An ordinal that says so: a word or a suffixed number. A bare "3" is a count, and "3
#: months of 2024" must not become "the third month of 2024".
_NTH_STRICT = rf"(?:{alt(ORDINALS)}|\d{{1,2}}(?:st|nd|rd|th)|last|final)"


def _p_sub_period(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the first week of April", "the last month of the year", "the last quarter of 2024",
    "the first 3 months of 2024".

    The ordinal used to be lost along with its unit. "the first week of April" came back
    as all of April, and "the last quarter of 2024" as two matches -- "last quarter",
    counted back from today, and 2024 -- the first of them confidently wrong.
    """
    m = re.match(
        rf"\s*(?:the\s+)?({_NTH_STRICT})\s+(?:({_NUM})\s+)?({_SUB_UNIT}){_NTH_OF}(.+)$",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    index = _nth_index(m.group(1))
    count = word_to_int(m.group(2)) if m.group(2) else 1
    word = m.group(3).lower()
    unit = Grain.QUARTER if word.startswith("q") else _unit_of(word)
    if index is None or index == 0 or count is None or count < 1 or unit is None:
        return None
    # A run of several units can only be counted from one end or the other.
    if m.group(2) and index not in (1, -1):
        return None
    target = _nth_target(m.group(4), cfg)
    if target is None:
        return None
    limit = {Grain.DAY: 31, Grain.WEEK: 5, Grain.MONTH: 12, Grain.QUARTER: 4}.get(unit, 0)
    if unit is Grain.WEEK and (
        target.basis is Basis.FISCAL or not _is_year(target) or target.kind is Kind.PERIOD_ENDING
    ):
        # Weeks in what holds them: "the 20th week of FY25" and "the 10th week of Q1" are
        # weeks, as "fiscal week 20 FY25" and "week 10 of Q1" are. REGRESSION: both were
        # refused, five being a month's most. A calendar year keeps five: its numbered
        # weeks are ISO weeks.
        limit = _weeks_in(target)
    if index > limit:
        return None
    # "the first fortnight of March" is its first fourteen days.
    count *= _scale_of(word)
    if unit is Grain.QUARTER and m.group(2):
        # "the first 3 quarters of 2024": REGRESSION -- read as "3 quarters of 2024" and
        # answered with Q3 alone.
        if not (target.kind is Kind.ABS_YEAR or target.unit is Grain.YEAR) or count > 4:
            return None
        return target.with_(sub_period=(index, count, Grain.QUARTER))
    if unit is Grain.QUARTER:
        # A quarter of a year is a quarter labelled with that year, so it is read exactly
        # as "the fourth quarter of 2024" is -- of_year_basis and all -- rather than by
        # slicing the calendar year. Only "last"/"final" reach here; "the third quarter of
        # 2024" is the quarter rule's, and the two must not disagree.
        if index != -1 or count != 1:
            return None
        if not (target.kind is Kind.ABS_YEAR or target.unit is Grain.YEAR):
            return None
        tail = re.sub(rf"^\s*{_THE_YEAR}", "", m.group(4), flags=re.IGNORECASE)
        return _p_quarter(f"fourth quarter of {tail}", cfg)
    return target.with_(sub_period=(index, count, unit))


def _p_nth_of(text: str, cfg: WranglerConfig) -> Spec | None:
    """"1st of next month", "15th of March" -- one day inside a named period."""
    m = re.match(rf"\s*(?:the\s+)?({_ORD})\s+(?:of\s+)?(.+)$", text, re.IGNORECASE)
    if not m:
        return None
    day = ordinal_to_int(m.group(1))
    if day is None or not 1 <= day <= 31:
        return None
    target = _target_spec(m.group(2), cfg)
    if target is None:
        return None
    return target.with_(day_of_period=day)


def _p_same_period(text: str, cfg: WranglerConfig) -> Spec | None:
    """"same quarter last year", "this time last year" -- today's period, a year back.

    "the same period last year" names no unit: it is the period it is compared with, a
    year back, and the wrangler fills that in. Here it is a THIS_PERIOD with no unit.
    """
    m = re.match(
        rf"\s*(?:the\s+)?same\s+(?:(?P<basis>{_BASIS_WORD})\s+)?(?P<unit>{_UNIT}|period)\s+"
        rf"{_SAME_YEAR_BACK}",
        text,
        re.IGNORECASE,
    )
    if m:
        offset = _direction_of(m.group("dir")) if m.group("dir") else -1  # "a year ago", "LY"
        if m.group("unit").lower() == "period":
            return Spec(Kind.THIS_PERIOD, year_offset=offset)
        unit = _unit_of(m.group("unit"))
        if unit is None:
            return None
        # "same quarter last FY" is today's fiscal quarter a year back. REGRESSION: it was
        # all of last FY, the "same quarter" unread.
        word = m.group("basis") or m.group("ybasis") or ("fiscal" if m.group("fy") else None)
        return Spec(
            Kind.THIS_PERIOD, unit=unit, year_offset=offset, basis=_period_basis(word, unit)
        )
    m2 = re.match(rf"\s*this\s+time\s+({_DIRWORD})\s+year\b", text, re.IGNORECASE)
    if not m2:
        return None
    return Spec(Kind.DAY_KEYWORD, direction=0, year_offset=_direction_of(m2.group(1)))


def _p_rolling(text: str, cfg: WranglerConfig) -> Spec | None:
    """"rolling 3 months" -- a window measured back from today, not to unit boundaries.

    Only the words that say so outright. "last 3 months" follows configuration, and
    "trailing 12 months"/TTM stays anchored: in reporting it means the last twelve
    *completed* months, which is the whole point of quoting it.
    """
    m = re.match(
        rf"\s*(?:the\s+)?(?:rolling|moving|sliding)\s+({_NUM})\s+({_UNIT})\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    count = word_to_int(m.group(1))
    unit = _unit_of(m.group(2))
    if count is None or unit is None:
        return None
    return Spec(
        Kind.RELATIVE, count=count, unit=unit, direction=-1, anchor=Anchor.ROLLING
    )


#: How the three days are written: in full, short, and misspelt as often as not.
_DAY_WORDS: dict[str, int] = {
    "today": 0, "tdy": 0,
    "yesterday": -1, "yday": -1, "y'day": -1, "yest": -1, "ystd": -1,
    "tomorrow": 1, "tmrw": 1, "tmr": 1, "2moro": 1, "2morrow": 1,
    "tomorow": 1, "tommorow": 1, "tommorrow": 1,
}
_YESTERDAY = alt(w for w, n in _DAY_WORDS.items() if n == -1)
_TOMORROW = alt(w for w, n in _DAY_WORDS.items() if n == 1)
#: The year back in "same month last year", "same quarter of last year", "same month a year
#: ago", "same month LY" -- and a fiscal one: "same quarter last FY", "same quarter of the
#: previous fiscal year". The direction word is captured; the others are a year back.
_SAME_YEAR_BACK = (
    rf"(?:of\s+)?(?:(?:the\s+)?(?P<dir>{_DIRWORD})\s+(?:(?P<ybasis>{_BASIS_WORD})\s+)?"
    rf"(?:year|(?P<fy>f\.?y))|a\s+year\s+ago|ly|py)\b"
)


def _p_day_keyword(text: str, cfg: WranglerConfig) -> Spec | None:
    low = re.sub(r"\s+", " ", text.strip().lower())
    # Idioms, not a modifier plus a day. Read as "before yesterday" they became unbounded
    # ranges reaching back to the beginning of time.
    m = re.fullmatch(r"(?:the )?day (before|after) (\S+)", low)
    if m:
        side = -1 if m.group(1) == "before" else 1
        if _DAY_WORDS.get(m.group(2)) != side:
            return None
        return Spec(Kind.DAY_KEYWORD, direction=2 * side)
    offset = _DAY_WORDS.get(low)
    if offset is None:
        return None
    return Spec(Kind.DAY_KEYWORD, direction=offset)


def _p_weekend(text: str, cfg: WranglerConfig) -> Spec | None:
    """"this weekend", "next weekend" -- Saturday and Sunday of the week meant."""
    m = re.match(rf"\s*(?:(this|{_DIRWORD})\s+)?(?:weekend|wknd)\b", text, re.IGNORECASE)
    if not m:
        return None
    word = (m.group(1) or "this").lower()
    direction = 0 if word == "this" else _direction_of(word)
    return Spec(Kind.WEEKEND, direction=direction)


#: "EOM"/"month end" and friends. The unit each names, which end of it, and the basis when
#: the word says one: "FYE" is the end of the fiscal year.
_EDGE_WORDS: dict[str, tuple[str, Part, Basis | None]] = {
    "eom": ("month", Part.LATE, None),
    "eoq": ("quarter", Part.LATE, None),
    "eoy": ("year", Part.LATE, None),
    "eow": ("week", Part.LATE, None),
    "fye": ("year", Part.LATE, Basis.FISCAL),
}


def _p_period_edge(text: str, cfg: WranglerConfig) -> Spec | None:
    """"month end", "EOY", "fiscal year end", "FYE" -- the edge of the current period.

    A basis said in front of the unit is kept: "fiscal year end" is the end of the fiscal
    year, exactly as "the end of the fiscal year" is. REGRESSION: it was read as "year
    end", the calendar year's, "fiscal" dropped -- and "fiscal year start" its start.

    A direction in front is kept too: "last FYE" is the end of last fiscal year, as "the end
    of last fiscal year" is, and "last month end" the end of last month. REGRESSION: "last"
    was dropped -- "since last FYE" was this fiscal year's end -- or "end" was.
    """
    directed = re.fullmatch(
        rf"\s*(?:the\s+)?({_DIRWORD}|this|current)\s+(.+?)\s*", text, re.IGNORECASE
    )
    if directed:
        inner = _p_period_edge(directed.group(2), cfg)
        word = directed.group(1).lower()
        if inner is None or inner.kind is not Kind.THIS_PERIOD or word in ("this", "current"):
            return inner
        return Spec(
            Kind.RELATIVE, count=1, unit=inner.unit, direction=_direction_of(word),
            basis=inner.basis, part=inner.part,
        )
    started = re.fullmatch(
        rf"\s*(?:the\s+)?(?P<basis>{_BASIS_WORD}[\s-]+)?(?P<unit>year|yr|{_FY_WORD}|{_CY_WORD})"
        r"[-\s](?:start|beginning)\s+(?:of\s+)?(?P<year>.+?)\s*",
        text,
        re.IGNORECASE,
    ) or re.fullmatch(
        rf"\s*(?P<year>(?:19|20)\d{{2}})\s+(?:the\s+)?(?P<basis>{_BASIS_WORD}[\s-]+)?"
        rf"(?P<unit>year|yr|{_FY_WORD}|{_CY_WORD})[-\s](?:start|beginning)\s*",
        text,
        re.IGNORECASE,
    )
    if started:
        # "year start 2024", "fiscal year start FY24", "2024 F.Y. start": the start of that
        # year. A year's end with a year beside it is period_ending's -- "year end 2024" is
        # the year 2024. REGRESSION: "year start 2024" was the start of this year, 2024
        # dropped, and "2024 F.Y. start" this fiscal year's start.
        target = _target_spec(started.group("year"), cfg)
        if target is None or not _is_year(target):
            return None
        said = started.group("basis") or started.group("unit")
        if re.match(r"(?i)(?:fiscal|financial|tax|f\.?y)", said):
            target = target.with_(basis=Basis.FISCAL)
        elif re.match(r"(?i)(?:calendar|c\.?y)", said):
            target = target.with_(basis=Basis.CALENDAR)
        return target.with_(part=Part.EARLY)
    low = re.sub(r"[\s.-]+", " ", text.strip().lower())
    edge = _EDGE_WORDS.get(low.replace(" ", ""))
    if edge is not None:
        unit_word, part, basis = edge
        unit = _unit_of(unit_word)
        return None if unit is None else Spec(Kind.THIS_PERIOD, unit=unit, part=part, basis=basis)
    # "FY end", "F.Y. end", "CY end": the year, its basis written short.
    low = re.sub(r"^(?:the )?([fc]) ?y\b", lambda f: _FULL_YEAR_WORD[f.group(1)], low)
    m = re.fullmatch(
        rf"(?:the\s+)?(?:({_BASIS_WORD})\s+)?({_UNIT})[- ](end|start|beginning|close)", low
    )
    if not m:
        return None
    unit = _unit_of(m.group(2))
    if unit is None:
        return None
    part = Part.LATE if m.group(3) in ("end", "close") else Part.EARLY
    return Spec(Kind.THIS_PERIOD, unit=unit, part=part, basis=_basis_of(m.group(1)))


def _p_in_future(text: str, cfg: WranglerConfig) -> Spec | None:
    """"in 3 days", "2 weeks from now", "3 months from today".

    One period that far ahead, matching "3 months ago" in the other direction. Without
    this "3 months from today" matched only "today" and answered with a single day.
    """
    # "after 6 months" is a duration from now; "after March" is the AFTER modifier and is
    # handled elsewhere. The number is what tells them apart.
    m = re.match(rf"\s*(?:in|after|within)\s+({_COUNT})\s+({_UNIT})\b", text, re.IGNORECASE)
    if m is None:
        m = re.match(
            rf"\s*({_COUNT})\s+({_UNIT})\s+from\s+(?:now|today)\b", text, re.IGNORECASE
        )
    if m is None:
        return None
    count = word_to_int(m.group(1))
    unit = _unit_of(m.group(2))
    if count is None or unit is None:
        return None
    count *= _scale_of(m.group(2))
    if re.match(r"\s*within\b", text, re.IGNORECASE):
        # "within 30 days" is the whole window, as "within the next 30 days" is. REGRESSION:
        # it was the one day thirty days out.
        return Spec(Kind.RELATIVE, count=count, unit=unit, direction=1)
    return Spec(Kind.AGO, count=count, unit=unit, direction=1)


def _p_months_of_year(text: str, cfg: WranglerConfig) -> Spec | None:
    """"9M 2024", "9M FY24", "9MFY24" -- the first nine months, as results are reported.

    A capital M only: "9m" is nine metres or minutes far more often than nine months.
    """
    m = re.match(r"\s*(\d{1,2})\s*M(.*)$", text)
    if not m:
        return None
    months = int(m.group(1))
    if not 1 <= months <= 12:
        return None
    tail = m.group(2).strip()
    year: int | None
    basis: Basis | None
    if re.fullmatch(r"\d{2}", tail):
        year, basis = _pivot_year(tail, cfg), None
    else:
        year, basis = _year_from_suffix(tail, cfg)
    if year is None:
        return None
    return Spec(Kind.ABS_YEAR, year=year, basis=basis, sub_period=(1, months, Grain.MONTH))


def _p_month_quarter(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the March quarter 2024", "the June quarter" -- a quarter named by its last month."""
    month = find_month(text)
    if month is None:
        return None
    tail = re.sub(r"^.*?\b(?:quarter|qtr)\b", "", text, flags=re.IGNORECASE)
    year, basis, offset = _period_suffix(tail, cfg)
    return Spec(
        Kind.PERIOD_ENDING, unit=Grain.QUARTER, count=1, month=month, year=year, basis=basis,
        year_offset=offset,
    )


def _p_final_quarter(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the final quarter" on its own: the last quarter of the year, Q4."""
    return Spec(Kind.ABS_QUARTER, index=4)


def _p_unit_before_last(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the month before last", "the year after next" -- two periods back or on.

    REGRESSION: matched nothing at all, and said nothing either.
    """
    m = re.match(rf"\s*(?:the\s+)?({_UNIT})\s+(before\s+last|after\s+next)", text, re.I)
    if not m:
        return None
    unit = _unit_of(m.group(1))
    if unit is None:
        return None
    direction = -1 if m.group(2).lower().startswith("before") else 1
    return Spec(Kind.AGO, count=2 * _scale_of(m.group(1)), unit=unit, direction=direction)


def _p_unit_before(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the year before", "the day after" -- one period back or on.

    On its own it counts from today; with a date after it -- "the day after 15 March" --
    it counts from that date (see the wrangler, which joins the two).
    """
    m = re.match(rf"\s*(?:the\s+)?({_UNIT})\s+(before|after)", text, re.I)
    if not m:
        return None
    unit = _unit_of(m.group(1))
    if unit is None:
        return None
    direction = -1 if m.group(2).lower() == "before" else 1
    return Spec(Kind.AGO, count=_scale_of(m.group(1)), unit=unit, direction=direction)


def _p_till_now(text: str, cfg: WranglerConfig) -> Spec | None:
    """"till date", "as on date", "to present" -- today, as the end of what came before.

    "01/06/2018 to till date" is how a statement is asked for, and the end was dropped:
    the answer was 1 June alone. The wrangler joins this to the date before it; standing
    alone, "till date" is everything up to today and "as on date" is today.
    """
    low = text.strip().lower()
    if low.startswith("as"):
        return Spec(Kind.DAY_KEYWORD, direction=0, mod=Mod.AS_OF)
    if re.match(r"(?:up\s*)?(?:till|until)\b|upto\b|up\s+to\b", low):
        return Spec(Kind.DAY_KEYWORD, direction=0, mod=Mod.UNTIL)
    return Spec(Kind.DAY_KEYWORD, direction=0)


def _p_decline(reason: str) -> Callable[[str, WranglerConfig], Spec | None]:
    """A rule read only to say why it gives no answer."""

    def parse(text: str, cfg: WranglerConfig) -> Spec | None:
        raise ValueError(reason.format(text=text.strip()))

    return parse


def _p_part_of_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"this morning", "tonight": today, but only part of it -- flagged, as "tomorrow
    morning" is."""
    return Spec(Kind.DAY_KEYWORD, direction=0, confidence=0.5)


def _p_quoted_year(text: str, cfg: WranglerConfig) -> Spec | None:
    """"in '24" -- the two digits after an apostrophe; the wrangler takes the apostrophe."""
    m = re.fullmatch(r"\s*(\d{2})\s*", text)
    if not m:
        return None
    return Spec(Kind.ABS_YEAR, year=_pivot_year(m.group(1), cfg), confidence=0.8)


# ---------------------------------------------------------------------------
# The table. Order is priority: specific before general.
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    # No trailing \b: an ISO timestamp runs the date straight into the time with a "T",
    # and a word boundary between "5" and "T" does not exist -- so every ISO 8601 instant
    # in a log line was being skipped.
    Rule("iso", r"\b\d{4}-\d{1,2}-\d{1,2}(?!\d)", _p_iso),
    # "the 2015-2016 season", "season 2015/16", "the 2015-16 academic year": a name, not
    # dates. A season runs over months of its own, so 2015 to 2016 overstates it by a year
    # and the calendar cannot say which months. REGRESSION: read as Jan 2015 to Dec 2016,
    # where the data held it as a label. Ahead of every rule that would take the years.
    # "the 2015-16 campaign" and "the 2015-16 term" as well, and "SY 2015-16" for a school
    # year. REGRESSION: each was two calendar years.
    # And a season's edges: "academic year start", "the school year end 2024", "2024
    # academic year end" -- the start of an academic year is no January. REGRESSION: they
    # were the calendar year's start or end.
    Rule(
        "season_label",
        rf"\b(?:the\s+)?{_YEAR_RUN}\s+{_SEASON_OWNER}(?:{_SEASON_WORD}|campaign|term)s?\b"
        rf"|\b(?:{_SEASON_WORD}|sy)\s+(?:of\s+)?{_YEAR_RUN}\b"
        rf"|\b(?:the\s+)?(?:{_YEAR_RUN}\s+)?(?:academic|school)\s+year[-\s]+"
        rf"(?:end|start|beginning|close)(?:\s+(?:of\s+)?{_YEAR_RUN})?\b",
        _p_decline(
            "{text!r} names a season, which runs over months of its own -- a sport's, a "
            "school's -- so it is read as a label, not as dates"
        ),
    ),
    Rule(
        "assessment_year",
        rf"\b{_AY_WORD}{_SEP}'?\d{{2,4}}(?:[-/]'?\d{{2,4}})?(?![\d/])",
        _p_assessment_year,
    ),
    # "2023-24 financial year": the fiscal year written before its name.
    Rule(
        "fy_range_after",
        rf"\b\d{{2,4}}\s*[-/]\s*'?\d{{2,4}}\s+(?:financial\s+year|fiscal\s+year|{_TAX_YEAR})\b",
        _p_fy_range,
    ),
    # A month in numbers -- "2024-03", "03/2024", "03-2024" -- and not the start of a longer
    # date, nor a quarter or half: REGRESSION -- "2024-3Q" was March 2024 and "2024-1H"
    # January. Not with a dot: "2024.03" and "3.2024" are amounts as often as months.
    Rule(
        "numeric_month",
        r"\b(?:(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])|(?:0?[1-9]|1[0-2])[-/](?:19|20)\d{2})"
        r"(?![\d/.-]|[qh](?![a-z]))",
        _p_numeric_month,
    ),
    # "2024-25" with nothing to say whether it is one fiscal year or two years: declined.
    # Not a year and then a day of the next date: REGRESSION -- in "15 of March, 2024-20
    # of April, 2024" the "2024-20" was refused as a split year, and the first year lost.
    Rule(
        "split_year",
        r"\b(?:19|20)\d{2}(?:[-/]\d{2}|/(?:19|20)\d{2})(?![\d/.-])"
        rf"(?!(?:st|nd|rd|th)?[\s,]*(?:of\s+)?{_MONTH}\b)",
        _p_split_year,
    ),
    # Days, months and years written with month names are read from tokens -- see
    # tokens.py -- and these entries hold their places in the order. "15-Mar-2024",
    # "15MAR2024", "13june2018".
    _from_tokens("dashed_day"),
    # A fiscal year and then a month inside it, ahead of fy_range and year, which would
    # each take the year and leave the month behind: "FY25 June", "FY2024-25 March".
    _from_tokens("fy_month"),
    # Not when a month follows: in "FY24 - 15 March 2024" the 15 is a day. Not when a
    # quarter does: "FY2024-25 Q3" is a quarter of that year, for labelled_period. Nor
    # numbered weeks or months the counted rules take with it -- "FY2024-25 week 3",
    # "FY2024-25 M3" -- and only those: "FY2024-25 week 3 of Q2" keeps its year here.
    # REGRESSION: any number after "week" turned this rule away, and that one was FY2024
    # with "-25" left over.
    Rule(
        "fy_range",
        rf"\b{_FY_WORD}\s*'?\d{{2,4}}\s*[-/]\s*'?\d{{2,4}}\b(?![\s,]+{_MONTH}\b)"
        rf"(?!\s*-?\s*{_QH}(?!\w))(?!{_FY_WEEKS}|{_FY_MONTHS}|{_FY_M}|{_YEAR_END_AFTER})",
        _p_fy_range,
    ),
    # "9M 2024", "9M FY24": the first months of a year, as results are reported.
    Rule(
        "months_of_year",
        rf"\b(?:1[0-2]|[1-9])\s*m\s*(?:{_MARKED_YEAR}|(?:19|20)\d{{2}}\b|'?\d{{2}}(?!\d))(?!\w)",
        _p_months_of_year,
    ),
    # Ahead of the year, quarter and half rules, which start at the same place and would
    # each take one piece: "FY24" out of "FY24 Q3", "2024" out of "2024-Q3", "Q3" out of
    # "Q3'24". (?!\w) rather than \b, because "Q3'24" ends on a digit after a quote.
    Rule(
        "labelled_period",
        rf"\b(?:{_BASIS_TAG}\s+{_QH}{_YEAR_SUFFIX}"
        rf"|{_QH}\s+(?:fiscal|financial|calendar)\b"
        rf"|{_QH}\s+(?:fy|cy)\b(?!\s*-?\s*'?\d)"  # not "1H FY-24", whose FY has its year
        rf"|{_QH}['/-]\d{{2}}(?:\d{{2}})?(?!\d)"
        # Not when the quarter has a year of its own: "FY24 - Q3" is a label, "2023 - H2
        # 2024" a range from 2023 to H2 2024.
        rf"|(?:19|20)\d{{2}}\s*-?\s*{_QH}(?!\s*'?\d)"
        rf"|(?:{_FY_WORD}|{_CY_WORD})[\s-]*'?\d{{2,4}}(?:[-/]'?\d{{2,4}})?\s*-?\s*{_QH}(?!\s*'?\d)"
        rf"|(?:[1-4]\s*q|[12]\s*h)\s*'?\d{{2,4}})(?!\w)",
        _p_labelled_period,
    ),
    # One separator throughout, and a year of two or four digits: "15/03/2024",
    # "13.06.18", "01-04-2018". Mixed or odd shapes -- "4.5/5", "1.5.3" -- are ratings
    # and version numbers, not dates.
    Rule(
        "numeric",
        r"\b(?:\d{4}/\d{1,2}/\d{1,2}|\d{4}\.\d{1,2}\.\d{1,2}"
        r"|\d{1,2}/\d{1,2}/(?:\d{4}|\d{2})|\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})"
        r"|\d{1,2}-\d{1,2}-(?:\d{4}|\d{2}))\b",
        _p_numeric,
    ),
    Rule(
        "ytd_period",
        rf"\b(?:(?:{_TD_PAST}\s+)?(?:{_TD_BASIS}[\s-]*)?{_TD_YTD}\s+(?:{_TD_UNTIL}\s+)?{_TD_MONTH}"
        rf"|{_TD_MONTH}\s+{_TD_YTD})\b",
        _p_to_date,
    ),
    Rule("to_date", rf"\b(?:{_TD_PAST}\s+)?{_TD_CORE}{_TD_TAIL}?\b", _p_to_date),
    Rule(
        "year_ytd",
        rf"\b(?:(?:19|20)\d{{2}}|{_MARKED_YEAR})(?:\s+|-){_TD_YTD}\b",
        _p_to_date,
    ),
    # After to_date, so "year to date" and "last year to date" keep their YTD reading, and
    # ahead of every plain period rule, which would otherwise claim "this year" or "Q3"
    # from the same position and leave "to date" behind.
    Rule("period_to_date", rf"\b{_TD_TARGET}{_TO_DATE}\b", _p_period_to_date),
    Rule(
        "period_ending",
        rf"\b(?:(?:(?:the\s+)?(?:{_NUM}\s+)?(?:half[\s-]?years?|{_FY_WORD}|{_CY_WORD}|{_UNIT})"
        rf"(?:\s+period)?(?:\s+|-)(?:end(?:ing|ed|s)?|close|to)|w/e|w\.e\.|q/?e|y/?e|f\.?y\.?e\.?"
        rf"|(?:the\s+)?\d{{1,2}}\s*m\s+(?:to|ended|ending))\s+{_ENDING_AT}"
        rf"|(?:(?:19|20)\d{{2}}|{_FY_YEAR}){_YEAR_END_AFTER}"
        rf"|f\.?y\.?e\.?\s*[:-]\s*(?:(?:19|20)\d{{2}}|'\d{{2}})(?![\d/.-])"
        # A fiscal year's end, then its year after a comma or "of": "fiscal year end, 2024",
        # "fiscal year end of 2024", "FYE in 2024", "FYE of FY24". REGRESSION: these were the
        # calendar year's end, or this fiscal year's, beside the year.
        rf"|(?:f\.?y\.?e\.?|(?:the\s+)?{_FY_WORD}[\s-]*(?:end(?:ing|ed|s)?|close))"
        rf"(?:\s*,\s*|\s+(?:of|in|for)\s+(?:the\s+)?)(?:(?:19|20)\d{{2}}(?![\d/.-])|{_FY_YEAR})"
        # Two digits after a fiscal year's end are its year -- unless a unit or a measure
        # follows them: "FYE 12 months", "FYE 90 days", "fiscal year end 24 hours later".
        rf"|(?:f\.?y\.?e\.?|(?:the\s+)?{_FY_WORD}[\s-]*end(?:ing|ed|s)?)\s+\d{{2}}(?![\d/.-])"
        rf"(?!{_QUANTITY}))\b",
        _p_period_ending,
    ),
    Rule("trailing_months", r"\b(?:ttm|ltm|[tl]\d{1,2}m)\b", _p_trailing_months),
    # Before "half" and "quarter": "first half of March" opens with something the half
    # rule will happily claim as fiscal H1, throwing the month away.
    # Hours, minutes and seconds: matched only to be declined with a reason.
    Rule(
        "sub_day",
        rf"\b(?:(?:within|in|over|during)\s+)?(?:the\s+)?"
        rf"{_DIRWORD}\s+(?:{_SUB_DAY_COUNT}\s+)?{_SUB_DAY}\b"
        rf"|\b{_SUB_DAY_COUNT}\s+{_SUB_DAY}\s+(?:ago|from\s+now|later|earlier|back)\b"
        rf"|\b(?:in|within)\s+{_SUB_DAY_COUNT}\s+{_SUB_DAY}\b",
        _p_sub_day,
    ),
    # The period that holds a date: ahead of period_edge, which reads "week beginning" as
    # the start of this week, and week_number, which starts at the same "week". A week or
    # month holds a day; a quarter, half or year may hold a month too.
    Rule(
        "within",
        rf"\b(?:the\s+)?(?:(?:week|wk|month|mth)\s+of\s+(?:the\s+)?{_ONE_DAY}"
        rf"|(?:quarter|half|year)\s+of\s+(?:the\s+)?(?:{_ONE_DAY}|{_ONE_MONTH})"
        rf"|(?:(?:week|wk)\s+(?:commencing|beginning|starting)|w/c|w\.c\.?|wc)\s+(?:the\s+)?"
        rf"(?:{_WEEKDAY_ALT},?\s+)?{_ONE_DAY})\b",
        _p_within,
    ),
    # Before "part_of": "month end" is an edge of the current period, and part_of would
    # read the bare unit as the whole of it. The forms that say a basis -- "fiscal year
    # end", "FYE", "CY end" -- take nothing that follows them as a year or a date: those are
    # period_ending's ("fiscal quarter end June 2024"), and part_of's ("CY end 2024"), as
    # they were before the basis was read here. REGRESSION: they were all this year's end.
    # A year's start may take its year: "year start 2024", "fiscal year start FY24".
    Rule(
        "period_edge",
        rf"\b(?:e\.?o\.?[mqyw]"
        rf"|(?:the\s+)?(?:{_BASIS_WORD}[\s-]+)?(?:year|yr|{_FY_WORD}|{_CY_WORD})"
        rf"[-\s](?:start|beginning)\s+(?:of\s+)?{_YEAR}(?![-/.]\d)"
        rf"|(?:19|20)\d{{2}}\s+(?:the\s+)?(?:{_BASIS_WORD}[\s-]+)?(?:year|yr|{_FY_WORD}|{_CY_WORD})"
        rf"[-\s](?:start|beginning)"
        # A direction word before the edge is its: "last FYE" is the end of last fiscal year,
        # and "this month end" of this month. Nothing that follows as a year or a date --
        # "FYE-2024", "FYE: 2024", "FYE of FY24", "FYE in 2024" -- is left behind.
        # REGRESSION: "since last FYE" was this fiscal year's end, in the future, and "FYE of
        # FY24" this fiscal year's end beside FY24.
        rf"|{_EDGE_DIR}(?:f\.?y\.?e\.?|(?:the\s+)?(?:{_BASIS_WORD}[\s-]+{_UNIT}|{_FY_WORD}"
        rf"|{_CY_WORD})[-\s](?:end|start|beginning|close))"
        # A number that is a quantity is not its year: "fiscal year end 24 hours later".
        rf"(?![\s,:-]*(?:(?:of|in|for)\s+)?(?:'?\d(?!\d*{_QUANTITY})|{_MONTH}\b|[fc]\.?y))"
        rf"|{_EDGE_DIR}(?:the\s+)?{_NO_BASIS_BEFORE}{_UNIT}[-\s](?:end|start|beginning|close))\b",
        _p_period_edge,
    ),
    Rule(
        "part_of",
        rf"\b(?:the\s+)?(?:{_PART_ALT}){_OF}{_PART_TARGET}\b",
        _p_part_of,
    ),
    Rule("weekend", rf"\b(?:(?:this|{_DIRWORD})\s+)?(?:weekend|wknd)\b", _p_weekend),
    Rule(
        "in_future",
        # Not a year with "year" after it, which no one counts in: in "in 2024 year end",
        # 2024 is the year. REGRESSION: it was 2,024 years from now, the year 4050.
        rf"\b(?:(?:in|after|within)\s+(?!(?:19|20|21)\d{{2}}\s+(?:year|yr)\b(?!s)){_COUNT}"
        rf"\s+{_UNIT}|{_COUNT}\s+{_UNIT}\s+from\s+(?:now|today))\b",
        _p_in_future,
    ),
    Rule(
        "same_period",
        rf"\b(?:the\s+)?(?:same\s+(?:{_BASIS_WORD}\s+)?(?:{_UNIT}|period)\s+(?:of\s+)?"
        rf"(?:(?:the\s+)?{_DIRWORD}\s+(?:(?:{_BASIS_WORD}\s+)?year|f\.?y)"
        rf"|a\s+year\s+ago|ly|py)|this\s+time\s+{_DIRWORD}\s+year)\b",
        _p_same_period,
    ),
    Rule(
        "rolling",
        rf"\b(?:the\s+)?(?:rolling|moving|sliding)\s+{_NUM}\s+{_UNIT}\b",
        _p_rolling,
    ),
    # Before "fiscal_month" and "day_month_year", both of which open with a number.
    # Before "iso", whose \d{4}-\d{1,2} opening would otherwise claim "2026-W42" as far
    # as the dash and leave "W42" behind. The week date first, or "2024-W11-5" is the week.
    Rule("iso_week_day", r"\b(?:19|20)\d{2}-?w\d{2}-?[1-7]\b", _p_iso_week_day),
    Rule("iso_week_compact", rf"\b{_YEAR_WEEK}", _p_iso_week_compact),
    # Ahead of week_number, which would take "week 11" and leave "-12".
    # Before week_span and week_number, which would take "week 3" and leave "fiscal"; and
    # before "year", which would take "FY25" and leave "week 3".
    # Weeks counted into a period -- "fiscal week 3", "FY25 weeks 3-5", "week 3 of FY25",
    # "week 3 of January" -- before week_span and week_number, which would take "week 3"
    # and leave "fiscal" or "of January", and before "year", which would take "FY25". The
    # fiscal-year-first form takes nothing after it: "FY24 week 3 of Q2" is not FY24's.
    Rule(
        "week_counted",
        rf"\b(?:(?:the\s+)?{_FISCAL_WEEK_WORD}{_WEEK_RUN}"
        rf"(?:{_COUNTED_IN}{_NTH_TARGET}\b{_NOT_ENDING}|{_WEEK_YEAR_SUFFIX})"
        rf"|{_FY_YEAR}{_FY_WEEKS}"
        # "week 3 to fiscal week 5": a run of fiscal weeks, the fiscal word at its end.
        rf"|(?:week|wk){_WEEK_NO}\d{{1,2}}\b{_WEEK_JOIN}{_FISCAL_WEEK_WORD}{_WEEK_NO}\d{{1,2}}\b"
        rf"(?:{_COUNTED_IN}{_NTH_TARGET}\b{_NOT_ENDING}|{_WEEK_YEAR_SUFFIX})"
        # "week 3 of January", "weeks 3 and 4 FY25". Not a calendar week, an ISO week.
        rf"|(?:week|wk){_WEEK_RUN}"
        rf"(?:{_COUNTED_IN}{_NTH_TARGET}\b{_NOT_ENDING}|\s+{_MARKED_YEAR}(?![-/.]\d)))\b",
        _p_week_counted,
    ),
    Rule(
        "week_span",
        rf"\b{_WEEK_WORD}s?{_WEEK_NO}\d{{1,2}}{_WEEK_JOIN}\d{{1,2}}\b{_WEEK_YEAR_SUFFIX}",
        _p_week_span,
    ),
    Rule(
        "week_number",
        rf"\b{_WEEK_WORD}{_WEEK_NO}\d{{1,2}}\b{_WEEK_YEAR_SUFFIX}",
        _p_week_number,
    ),
    # Days running inside one month -- "1-15 March", "March 1-15", "between 1 and 15 March"
    # -- and days sharing one, listed: "1st and 15th March", "March 1, 5 and 9". Read from
    # tokens, so the month and the year are read as a single day's are. Ahead of the rules
    # that would take one day out of them from the same place.
    _from_tokens("day_span"),
    _from_tokens("day_list"),
    # Both must precede "relative" and the weekday rules, which otherwise claim the
    # opening words from the same position and win the alternation: "last day" scans as
    # "last 1 day" and "last Friday" as the weekday on its own.
    Rule(
        "edge_day",
        rf"\b(?:the\s+)?(?:first|1st|last|final)\s+(?:{_EDGE_BIZ})?(?:day|date){_NTH_OF}"
        rf"{_NTH_TARGET}\b",
        _p_edge_day,
    ),
    # After edge_day, so "the last business day of the month" is a day inside the month
    # and not the window of one business day before today.
    Rule("business_relative", rf"\b{_DIRWORD}\s+(?:{_NUM}\s+)?{_BIZ}\b", _p_business_relative),
    Rule(
        "business_ago",
        rf"\b(?:in\s+{_NUM}\s+{_BIZ}|{_NUM}\s+{_BIZ}\s+(?:from\s+(?:now|today)|{_AGO_WORDS}))\b",
        _p_business_ago,
    ),
    Rule(
        "nth_weekday",
        rf"\b(?:the\s+)?{_NTH}\s+(?:{_WEEKDAY_ALT})\.?{_NTH_OF}{_NTH_TARGET}\b",
        _p_nth_weekday,
    ),
    # After edge_day, which keeps "the last day of", and ahead of relative, fiscal_month
    # and month -- "the last week of March" otherwise scans as "last week" and a stray
    # March, and "the third month of the quarter" as the third month of the fiscal year.
    # Quarters only as "last"/"final": an ordinal quarter belongs to the quarter rule, and
    # claiming it here as well left "the first quarter of 2024" matched by this rule and
    # resolved by none.
    # "month 3 of FY24": ahead of nth_of, which would read "3 of FY24" as a day, and of
    # "year", which would take "FY24" from "FY24 month 3". It needs a fiscal word or a
    # period after it: "month 3" alone says nothing about which year's month.
    Rule(
        "month_counted",
        rf"\b(?:(?:the\s+)?(?:fiscal|financial|tax)\s+(?:month|mth){_MONTH_RUN}"
        rf"(?:{_COUNTED_IN}{_NTH_TARGET}\b{_NOT_ENDING}|{_YEAR_SUFFIX})"
        rf"|{_FY_YEAR}(?:{_FY_MONTHS}|{_FY_M})"
        # A year after a plain "month 3" only when it is marked as one: "month 3 FY24".
        # REGRESSION: any four digits were, and "In month 3, 2000 customers churned" was
        # March 2000.
        rf"|(?:the\s+)?(?:month|mth){_MONTH_RUN}"
        rf"(?:{_COUNTED_IN}{_NTH_TARGET}\b{_NOT_ENDING}|(?:\s*,)?\s+{_MARKED_YEAR}(?![-/.]\d)))\b",
        _p_month_counted,
    ),
    Rule(
        "sub_period",
        rf"\b(?:the\s+)?(?:{_NTH_STRICT}\s+(?:{_NUM}\s+)?"
        rf"(?:days?|weeks?|wks?|fortnights?|months?|mths?|mnths?|mos?)"
        rf"|(?:first|1st|last|final)\s+{_NUM}\s+(?:quarters|qtrs)"
        rf"|(?:last|final)\s+(?:quarter|qtr)){_NTH_OF}{_NTH_TARGET}\b{_NOT_ENDING}",
        _p_sub_period,
    ),
    Rule("final_quarter", r"\bthe\s+final\s+(?:quarter|qtr)\b(?!\s+(?:of|in)\b)", _p_final_quarter),
    # Not a week's or a month's number: "month 3 of FY24" is never its 3rd day.
    Rule(
        "nth_of",
        r"\b" + "".join(rf"(?<!{w} )" for w in ("week", "weeks", "wk", "month", "months", "mth"))
        + rf"(?:the\s+)?{_ORD}\s+of\s+{_PART_TARGET}\b",
        _p_nth_of,
    ),
    # "15 March 2024", "01may 2018", "15 Mar 24"; "Mar 15 14:30:00" (syslog); "March 15
    # 2024", "March the 15th"; "2024 March 15"; "January first, 2024". After the rules that
    # take a day out of something larger: "1-15 March", "15th of March", "first Monday of".
    _from_tokens("day_month_year"),
    _from_tokens("syslog"),
    _from_tokens("month_day_year"),
    _from_tokens("year_month_day"),
    _from_tokens("month_ordinal_word"),
    # Before "fiscal_month": both open with a bare number, and "3 months ago" would
    # otherwise read as "the 3rd month".
    Rule("ago", rf"\b{_COUNT}\s+{_UNIT}\s+(?:{_AGO_WORDS})\b", _p_ago),
    # Singular "month" only: an ordinal position is "the third month", never "months".
    Rule("fiscal_month", rf"\b(?:the\s+)?{_ORD}\s+month\b{_YEAR_SUFFIX}", _p_fiscal_month),
    Rule(
        "relative_fy",
        rf"\b{_DIRWORD}\s+(?:{_NUM}\s+)?(?:{_FY_WORD}|{_CY_WORD})\b",
        _p_relative_fy,
    ),
    Rule(
        "relative", rf"\b{_DIRWORD}\s+(?:{_COUNT}\s+)?(?:{_BASIS_WORD}\s+)?{_UNIT}\b",
        _p_relative,
    ),
    Rule(
        "this_period",
        rf"\b(?:this|current|present)\s+(?:(?:{_BASIS_WORD}\s+)?{_UNIT}|{_FY_WORD}|{_CY_WORD})\b",
        _p_this,
    ),
    # "till date", "as on date": today, as statements are asked for. Before day_keyword.
    Rule(
        "till_now",
        r"\b(?:(?:up\s*)?(?:till|until)\s+(?:the\s+present|date|dated|dt|now|present)"
        r"|upto\s+(?:date|dated|dt|now)|up\s+to\s+now"
        r"|as\s+(?:on|of|at)\s+(?:date|dated|dt)|as\s+(?:of|at)\s+now)\b",
        _p_till_now,
    ),
    # "to present", "to now", "-present", "to date": today, but only as the end of the date
    # before it -- "we want to present the results" is not one.
    Rule(
        "to_now",
        r"\b(?:to|through|thru)\s+(?:the\s+)?(?:present|now)\b"
        r"(?!\s+(?:the|a|an|our|your|their|this|that|these|those|it|them|you|we|us)\b)"
        # "up to date" only here, as the end of a date: on its own it is an adjective.
        r"|\bto(?:\s+|\s*-\s*)date\b|\bup\s+to\s+date\b|\bto\s+this\s+day\b"
        r"|\b(?:(?<=-)|(?<=-\s))(?:present|now|date)\b",
        _p_till_now,
    ),
    Rule(
        "day_keyword",
        rf"\b(?:(?:the\s+)?day\s+(?:before\s+{_YESTERDAY}|after\s+{_TOMORROW})"
        rf"|{alt(_DAY_WORDS)})\b",
        _p_day_keyword,
    ),
    Rule(
        "unit_before_last",
        rf"\b(?:the\s+)?{_UNIT}\s+(?:before\s+last|after\s+next)\b",
        _p_unit_before_last,
    ),
    # After day_keyword, which keeps "the day before yesterday" whole.
    Rule(
        "unit_before",
        r"\b(?:the\s+)?(?:day|week|fortnight|month|quarter|half|year)\s+(?:before|after)\b"
        r"(?!\s+(?:last|next|yesterday|tomorrow)\b)",
        _p_unit_before,
    ),
    Rule("part_of_day", r"\b(?:this\s+(?:morning|afternoon|evening)|tonight)\b", _p_part_of_day),
    # Read only to say why there is no answer.
    # "the past few weeks", "in recent months", "a few weeks from now" as well: no answer
    # either way, but now a reason.
    Rule(
        "vague",
        r"\b(?:recently|lately|the\s+other\s+day|some\s*time\s+(?:ago|back)"
        r"|a\s+(?:little\s+)?while\s+(?:ago|back)|(?:not\s+)?long\s+ago|ages\s+ago"
        r"|a\s+long\s+time\s+ago"
        r"|(?:a\s+)?(?:few|several|some|many|number\s+of)\s+(?:days?|weeks?|months?|years?)"
        r"\s+(?:ago|back|from\s+now)"
        r"|(?:in\s+)?recent\s+(?:days|weeks|months|years)"
        r"|(?:(?:over|in|during)\s+)?(?:the\s+)?(?:past|last|previous|next|coming)"
        r"\s+(?:few|several|many)\s+(?:days|weeks|months|quarters|years)"
        r"|in\s+(?:a\s+)?(?:few|several)\s+(?:days|weeks|months|years))\b",
        _p_decline("{text!r} is too vague to answer: it names no period"),
    ),
    # "every first Monday of the month" as well. REGRESSION: it was this month's first
    # Monday.
    Rule(
        "recurrence",
        rf"\bevery\s+(?:other\s+)?(?:(?:{_NTH}\s+)?{_WEEKDAY_ALT}|day|week|fortnight|month"
        rf"|quarter|year|weekday|weekend|{_NUM}\s+{_UNIT})\b",
        _p_decline("{text!r} recurs; it is not one period, so it is not resolved"),
    ),
    # "the summer months of 2024" and "summer-2024" as well. REGRESSION: the first was the
    # whole of 2024.
    Rule(
        "season",
        r"\b(?:(?:last|this|next|past|coming|the)\s+)?(?:spring|summer|autumn|fall|winter)"
        r"(?:\s+months)?(?:\s+(?:of\s+)?|-)(?:(?:19|20)\d{2}(?:\s*/\s*\d{2,4})?|'\d{2})\b"
        r"|\b(?:last|this|next|past|coming)\s+(?:spring|summer|autumn|fall|winter)\b",
        _p_decline(
            "{text!r}: seasons fall in different months north and south of the equator, so "
            "this is not resolved; name the months"
        ),
    ),
    # Before weekday_rel and weekday, which take the weekday and leave the week behind.
    Rule(
        "weekday_of_week",
        rf"\b{_WEEKDAY_ALT}(?:\s*(?:-|to|through|thru|till|until)\s*{_WEEKDAY_ALT})?"
        r"\s+(?:of\s+)?(?:the\s+)?(?:last|previous|prior|this|next|following|coming)\s+week\b",
        _p_weekday_of_week,
    ),
    Rule("weekday_rel", rf"\b(?:{_DIRWORD}|this)\s+{alt(WEEKDAY_NAMES)}\b", _p_weekday),
    Rule(
        "quarter",
        # "Q1-end 2024" is Q1 2024, as "year-end 2024" is the year. REGRESSION: it was a
        # span from Q1 to the end of 2024.
        rf"\b(?:{_QWORD}\s*[1-4](?!\d)|{_ORD}\s*{_QWORD})(?:-end\b)?{_YEAR_SUFFIX}\b",
        _p_quarter,
    ),
    Rule(
        "half",
        # "1HFY24": the FY may touch the H, as it may the Q of "3QFY24".
        rf"\b(?:h\s*[12](?!\d)|half\s*[12](?!\d)|[12]\s*h(?:\b|{_H_FY})|{_ORD}\s+{_HWORD})"
        rf"{_YEAR_SUFFIX}\b",
        _p_half,
    ),
    _from_tokens("dashed_month_day"),  # "Mar-15-2024"
    _from_tokens("dashed_month"),  # "Mar-24": the hyphen settles "jan 24" as a month
    Rule(
        "decade",
        rf"\b(?:the\s+)?(?:(?:1[89]|20)\d0'?s|{alt(DECADE_WORDS)})\b|\bthe\s+'?\d0s\b",
        _p_decade,
    ),
    # "the March quarter 2024": the quarter that ends in March. Ahead of the month rules.
    Rule(
        "month_quarter",
        rf"\b(?:the\s+)?{_MONTH}\s+(?:quarter|qtr)\b{_YEAR_SUFFIX}",
        _p_month_quarter,
    ),
    Rule("cob", r"\b(?:eod|cob|eob)(?:\s+(?:on\s+)?" + alt(WEEKDAY_NAMES) + r")?\b",
         _p_close_of_business),
    # "March 2024", "June, 2013", "March of the year 2013", "Mar'24", "June' 2018", "June FY
    # 2024-25", "Jun-FY25", "March last year" -- where "June - FY25", spaced, is a range.
    # REGRESSION: a rule that took "Jun FY25 - 15" as one fiscal year failed on it and lost
    # both ends.
    _from_tokens("month_year"),
    # "last March", "this March", "next March": the nearest one, as "last Monday" is.
    Rule(
        "month_nearest",
        rf"\b(?:last|this|next|previous|coming)\s+{_MONTH}\b(?!\s*(?:\d|'))",
        _p_month_nearest,
    ),
    Rule("year", rf"\b{_YEAR_WORD}{_SEP}'?\d{{2,4}}\b", _p_year),
    _from_tokens("month"),
    # "Monday next" -- British for next Monday. Not "Monday next week", read above.
    Rule("weekday_next", rf"\b{alt(WEEKDAY_NAMES)}\s+next\b(?!\s+{_UNIT})", _p_weekday),
    Rule("weekday", rf"\b{alt(WEEKDAY_NAMES)}\b", _p_weekday),
    # Weak, like a bare month: "W42" is as likely a part number as a week, so in balanced
    # mode it needs a cue. The explicit forms above never do.
    Rule("week_bare", rf"\bw\s*\d{{1,2}}\b{_WEEK_YEAR_SUFFIX}", _p_week_bare),
    Rule("bare_year", rf"\b{_BARE_YEAR}\b", _p_bare_year),
    # "in '24": weak, like a bare year. The apostrophe is outside the match -- no word
    # boundary comes before it -- so the wrangler widens the span over it.
    Rule("quoted_year", r"\b(?<=[\s(]')\d{2}(?![\d'])\b", _p_quoted_year),
    # Last, and starting at the digit rather than at "the": the scanner takes the leftmost
    # match, so swallowing the article would beat the quarter rule to "the 5th quarter"
    # whatever the order here says. The cue check allows the article instead.
    # ...and only at the end of a clause. "on the 15th" is a date; "on the 3rd floor",
    # "the 2nd round", "for the 3rd time" are not, and a following noun is what tells them
    # apart. "the 15th of March" is the nth_of rule's job, not this one's.
    Rule("ordinal_day", r"\b\d{1,2}(?:st|nd|rd|th)\b(?!\s*\w)", _p_ordinal_day),
)
