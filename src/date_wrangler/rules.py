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
    MONTH_DISPLAY,
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
_UNIT = alt(UNIT_WORDS)
_PAST = alt(PAST_WORDS)
_FUTURE = alt(FUTURE_WORDS)
_DIRWORD = rf"(?:{_PAST}|{_FUTURE})"
# ``f\.?y\.?`` also covers "F.Y." and "FY."; the separator allows "fy-24" and "FY 24".
_FY_WORD = r"(?:f\.?y\.?|financial\s+year|fiscal\s+year)"
#: "fiscal 2024", as US filings write it: the word alone, straight before its year.
_FY_BARE = r"(?:fiscal|financial)(?=\s+'?\d)"
_CY_WORD = r"(?:c\.?y\.?|calendar\s+year)"
_YEAR_WORD = rf"(?:{_FY_WORD}|{_FY_BARE}|{_CY_WORD}|year)"
#: A basis said outright in front of a unit: "this fiscal year", "last calendar quarter".
_BASIS_WORD = r"(?:fiscal|financial|calendar)"


def _basis_of(word: str | None) -> Basis | None:
    """The basis a "fiscal"/"financial"/"calendar" word names, or None when absent."""
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

#: A year where nothing else can be meant -- after a month, quarter or half. Any four
#: digits, since "January 2100" is unambiguous. Restricting this to 19xx/20xx meant we
#: could not read back our own output, and substitute() grew the text on every pass.
_YEAR = rf"(?:{_FY_YEAR}|(?:{_CY_WORD}|year){_SEP}'?\d{{2,4}}|'\d{{2}}|\d{{4}})"

#: A year standing alone. Here the century is all that separates a date from a
#: quantity, so "5000" stays a number.
_BARE_YEAR = r"(?:19|20|21)\d{2}"
#: "last year", "next year", "this year" -- a year named by offset rather than by number.
#: Without this "Q1 last year" scans as two separate matches and the Q1 keeps the current
#: year, which is silently wrong rather than merely unrecognised.
_REL_YEAR = rf"(?:{_DIRWORD}|this|current)\s+year"

#: A marked year may abut ("Q1FY24"); an unmarked one may not, or "Q12024" splits. An
#: apostrophe may stand in for the space: "may'18", and "June' 2018" as statements write it.
_YEAR_SUFFIX = (
    rf"(?:\s*{_MARKED_YEAR}|\s+(?:of\s+)?(?:the\s+(?=year\b))?{_YEAR}"
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
_REL_YEAR_TAIL_RE = re.compile(
    rf"\s*(?:of\s+)?({_DIRWORD}|this|current)\s+year\s*$", re.IGNORECASE
)
_OF_RE = re.compile(r"\bof\b", re.IGNORECASE)


#: Several dates read out of one fragment, each with its own span inside it: the days of
#: "1st and 15th March" share the month, but each is a date of its own.
Parts = list[tuple[Spec, int, int]]


@dataclass(frozen=True, slots=True)
class Rule:
    """A named recogniser."""

    name: str
    pattern: str
    parse: Callable[[str, WranglerConfig], Spec | Parts | None]


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
    if re.search(rf"\bc\.?y\.?(?={_SEP}'?\d)|\bcalendar\s+year\b", low):
        return Basis.CALENDAR
    if re.search(
        rf"\bf\.?y\.?(?={_SEP}'?\d)|\b(?:fiscal|financial)(?:\s+year\b|(?=\s+'?\d))", low
    ):
        return Basis.FISCAL
    return None


_SPAN_DIGITS = re.compile(r"\d\s*[-/]\s*'?\d")


def parse_year_token(token: str, cfg: WranglerConfig) -> tuple[int | None, Basis | None]:
    """Read a year fragment, returning the year and any basis it declared.

    A fiscal year written with both its calendar years -- "FY2024-25" -- is the label the
    calendar gives it, which is not the first number when years are named by their end.
    """
    basis = _stated_basis(token)
    if basis is Basis.FISCAL and _SPAN_DIGITS.search(token):
        label = _fy_label_of_span(token, cfg)
        if label is None:
            raise ValueError(_not_one_fiscal_year(token))
        return label, basis
    m = _YEAR_DIGITS.search(token.lower())
    return (_pivot_year(m.group(1), cfg) if m else None), basis


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


def _year_offset_from_suffix(text: str) -> int | None:
    """"Q1 last year" -> -1. None when no relative year is named."""
    m = _REL_YEAR_TAIL_RE.search(text)
    if not m:
        return None
    word = m.group(1).lower()
    if word in ("this", "current"):
        return 0
    return _direction_of(word)


def _period_suffix(text: str, cfg: WranglerConfig) -> tuple[int | None, Basis | None, int | None]:
    """The year, basis and year-offset trailing a period phrase. At most one year form."""
    year, basis = _year_from_suffix(text, cfg)
    if year is not None:
        return year, basis, None
    return None, basis, _year_offset_from_suffix(text)


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


def _p_dashed_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"15-Mar-2024", "15/Mar/2024", "15MAR2024", "13june2018" -- what spreadsheets, SQL
    clients and bank systems emit."""
    month = _month_in(text)
    if month is None:
        return None
    m = re.match(r"\s*(\d{1,2})", text)
    if not m:
        return None
    year_m = re.search(r"'?(\d{2,4})\s*$", text)
    year = _pivot_year(year_m.group(1), cfg) if year_m else None
    return Spec(Kind.ABS_DAY, year=year, month=month, day=int(m.group(1)))


def _p_dashed_month_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"Mar-15-2024" -- month first, then the day and the year."""
    m = re.fullmatch(rf"\s*({_MONTH})-(\d{{1,2}})-(\d{{2}}|\d{{4}})\s*", text, re.IGNORECASE)
    if not m:
        return None
    return Spec(
        Kind.ABS_DAY, year=_pivot_year(m.group(3), cfg), month=MONTHS[m.group(1).lower()],
        day=int(m.group(2)),
    )


def _p_dashed_month(text: str, cfg: WranglerConfig) -> Spec | None:
    """"Mar-24", "Mar-2024".

    The hyphen settles the ``jan 24`` ambiguity on its own: a column of "Jan-24, Feb-24,
    Mar-24" is months, and a day would be written "15-Mar-24" with the month in the middle.
    """
    month = find_month(text)
    if month is None:
        return None
    m = re.search(r"-\s*'?(\d{2,4})\s*$", text)
    if not m:
        return None
    return Spec(Kind.ABS_MONTH, year=_pivot_year(m.group(1), cfg), month=month)


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
    basis = _stated_basis(text) if year is not None else None  # "15 June FY25", as a month
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
        basis = _stated_basis(text) if year is not None else None
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


def _p_split_year(text: str, cfg: WranglerConfig) -> Spec | None:
    """"2024-25", "2012/13" -- with no FY in front, nothing says which of two things it is.

    In a finance team it is a fiscal year; to anyone else, two years. Either guess is
    confidently wrong half the time, so it is declined with the two ways to say it.
    """
    m = re.fullmatch(r"\s*((?:19|20)\d{2})\s*[-/]\s*(\d{2}|\d{4})\s*", text)
    if not m:
        return None
    first, second = int(m.group(1)), int(m.group(2))
    if (first + 1) % 100 != second % 100:
        return None
    raise ValueError(
        f"{text.strip()!r} could be the fiscal year {first}-{(first + 1) % 100:02d} or the "
        f"years {first} to {first + 1}: write FY{first}-{(first + 1) % 100:02d} or "
        f"{first} to {first + 1}"
    )


def _p_numeric_month(text: str, cfg: WranglerConfig) -> Spec | None:
    """"2024-03", "2024/03", "03/2024" -- a month written as numbers.

    "2011-12" is also how a fiscal year is written, and the two cannot be told apart, so a
    month that is also the year after is declined rather than guessed.
    """
    t = text.strip()
    m = re.fullmatch(r"((?:19|20)\d{2})[-/](\d{1,2})", t) or re.fullmatch(
        r"(\d{1,2})/((?:19|20)\d{2})", t
    )
    if not m:
        return None
    year_first = len(m.group(1)) == 4
    year, month = (int(m.group(1)), int(m.group(2))) if year_first else (
        int(m.group(2)), int(m.group(1))
    )
    if not 1 <= month <= 12:
        return None
    if year_first and len(m.group(2)) == 2 and (year + 1) % 100 == month:
        raise ValueError(
            f"{t!r} could be {MONTH_DISPLAY[month - 1]} {year} or the fiscal year "
            f"{year}-{month:02d}: write {MONTH_DISPLAY[month - 1]} {year} or FY{t}"
        )
    return Spec(Kind.ABS_MONTH, year=year, month=month)


def _p_iso_week_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"2024-W11-5" -- an ISO week date: the year, the week, the day of the week."""
    m = re.fullmatch(r"\s*((?:19|20)\d{2})-?w(\d{2})-?([1-7])\s*", text, re.IGNORECASE)
    if not m:
        return None
    d = date.fromisocalendar(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return Spec(Kind.ABS_DAY, year=d.year, month=d.month, day=d.day)


def _p_to_date(text: str, cfg: WranglerConfig) -> Spec | None:
    low = text.lower()
    if re.search(r"\b(?:mtd|month\s*to\s*date)\b", low):
        unit = Grain.MONTH
    elif re.search(r"\b(?:qtd|quarter\s*to\s*date)\b", low):
        unit = Grain.QUARTER
    else:
        unit = Grain.YEAR
    direction = -1 if re.search(rf"\b{_PAST}\b", low) else 0
    spec = Spec(Kind.TO_DATE, unit=unit, direction=direction)
    month = find_month(low)
    year, basis = _year_from_suffix(text, cfg)
    lead = re.match(rf"\s*((?:19|20)\d{{2}}|{_MARKED_YEAR})\s+(?=ytd|year)", text, re.I)
    if year is None and lead is not None:
        # "2024 YTD": the year first, as a column heading puts it.
        year, basis = parse_year_token(lead.group(1), cfg)
    if month is not None:
        # Year may still be unstated ("YTD March"); resolve() picks the most recent March.
        return spec.with_(month=month, year=year, basis=basis)
    if year is not None:
        return spec.with_(year=year, basis=basis)
    return spec


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
    m = re.match(
        rf"\s*(?:the\s+)?(?:({_NUM})\s+)?(half[\s-]?years?|{_FY_WORD}|{_UNIT})(?:\s+|-)"
        rf"(end(?:ing|ed|s)?|to)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if m is None:
        # "w/e 15/03/2024": week ending, as timesheets and statements abbreviate it.
        m = re.match(r"\s*()(w/e|w\.e\.)()\s+(.+)$", text, re.IGNORECASE)
    if not m:
        return None
    word = m.group(2).lower()
    if word.startswith("half"):
        unit: Grain | None = Grain.HALF
    elif word.startswith("w"):
        unit = Grain.WEEK
    elif re.fullmatch(_FY_WORD, word):
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


#: A quarter or half as a label: Q3, 3Q, H1, 1H.
_QH = r"(?:q\s*[1-4]|[1-4]\s*q|h\s*[12]|[12]\s*h)"
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
        rf"\s*(?P<basis>{_FY_WORD}|{_CY_WORD})\s*'?(?P<yr>\d{{2,4}})"             # FY2024-25 Q3
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
        m2 = re.search(r"\b([12])\s*h\b", low)
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
    year, _, offset = _period_suffix(text, cfg)
    basis = _stated_basis(text) if year is not None else None
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


def _p_month_of_fy_span(text: str, cfg: WranglerConfig) -> Spec | None:
    """"June FY 2024-25", "FY2024-25 June" -- a month of a fiscal year written in full."""
    month = find_month(text)
    label = _fy_label_of_span(text, cfg)
    if month is None or label is None:
        return None
    return Spec(Kind.ABS_MONTH, year=label, month=month, basis=Basis.FISCAL)


def _p_fy_month(text: str, cfg: WranglerConfig) -> Spec | None:
    """"FY25 June", "FY2024-25 March" -- the fiscal year first, then the month inside it."""
    head = re.match(
        rf"\s*{_FY_WORD}{_SEP}'?\d{{2,4}}(\s*[-/]\s*'?\d{{2,4}})?", text, re.IGNORECASE
    )
    if head is None:
        return None
    if head.group(1):
        return _p_month_of_fy_span(text, cfg)
    year, basis = parse_year_token(head.group(0), cfg)
    month = find_month(text[head.end() :])
    if year is None or month is None:
        return None
    return Spec(Kind.ABS_MONTH, year=year, month=month, basis=basis)


def _p_year(text: str, cfg: WranglerConfig) -> Spec | None:
    year, basis = parse_year_token(text, cfg)
    if year is None:
        return None
    # FY or CY sets the basis; with only the word "year" -- "the year 2013" -- it stays
    # unset and year_basis decides, the same as for a bare "2013".
    return Spec(Kind.ABS_YEAR, year=year, basis=basis)


def _p_bare_year(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.fullmatch(rf"\s*({_BARE_YEAR})\s*", text)
    if not m:
        return None
    # Nothing says which kind of year, so year_basis decides -- "2024", "in 2024" and "the
    # year 2024" are read alike, and alike with "Q4 2024" and "the second half of 2024".
    return Spec(Kind.ABS_YEAR, year=int(m.group(1)), confidence=0.8)


def _p_ago(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(rf"\s*({_NUM})\s+({_UNIT})\s+({_AGO_WORDS})", text, re.IGNORECASE)
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
        rf"\s*({_DIRWORD})\s+(?:({_NUM})\s+)?(?:({_BASIS_WORD})\s+)?({_UNIT})\b",
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
    (r"first\s+half|1st\s+half", Part.FIRST_HALF),
    # "last half of 2024" was read as "last half" -- the previous half-year, counted back
    # from today -- plus a stray 2024. It means the second half, as "latter half" does.
    (r"second\s+half|2nd\s+half|latter\s+half|last\s+half|final\s+half", Part.SECOND_HALF),
    (r"beginning|start|early", Part.EARLY),
    (r"middle|mid", Part.MID),
    (r"end|late|close", Part.LATE),
)
_PART_ALT = "|".join(p for p, _ in _PART_WORDS)

#: The period a part or an ordinal day can be taken from.
_PART_TARGET = (
    rf"(?:{_MONTH}{_YEAR_SUFFIX}|{_QWORD}\s*[1-4](?!\d){_YEAR_SUFFIX}"
    rf"|h\s*[12](?!\d){_YEAR_SUFFIX}|{_DECADE}|{_YEAR}"
    rf"|{_DIRWORD}\s+(?:{_BASIS_WORD}\s+)?{_UNIT}"
    rf"|this\s+(?:{_BASIS_WORD}\s+)?{_UNIT}|{_UNIT})"
)


def _part_of(text: str) -> Part | None:
    for pattern, part in _PART_WORDS:
        if re.match(rf"\s*(?:the\s+)?(?:{pattern})\b", text, re.IGNORECASE):
            return part
    return None


#: What separates a part from its period: "mid-March" and "mid March" are the same phrase,
#: and "end of the month" carries an article the period itself does not want.
_OF = r"(?:\s*-\s*|\s+)(?:of\s+|in\s+)?(?:the\s+)?"


def _target_spec(tail: str, cfg: WranglerConfig) -> Spec | None:
    """Read the period a part or ordinal is being taken out of."""
    tail = tail.strip()
    if not tail:
        return None
    if re.fullmatch(_DECADE, tail, re.IGNORECASE):
        return _p_decade(tail, cfg)  # "the early 1990s", "mid-1990s"
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
_NTH_TARGET = (
    rf"(?:{_MONTH}{_YEAR_SUFFIX}|{_QWORD}\s*[1-4](?!\d){_YEAR_SUFFIX}"
    rf"|h\s*[12](?!\d){_YEAR_SUFFIX}|{_YEAR}"
    rf"|(?:the\s+)?(?:{_DIRWORD}|this|current)\s+(?:{_BASIS_WORD}\s+)?{_UNIT}|the\s+{_UNIT})"
)


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
        rf"\s*(?:the\s+)?({_NTH})\s+({_WEEKDAY_ALT}){_NTH_OF}(.+)$", text, re.IGNORECASE
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
        rf"\s*(?:the\s+)?(first|1st|last|final)\s+({_BIZ_WORD}\s+)?day{_NTH_OF}(.+)$",
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
#: "week 42, 2026" is how a spreadsheet heading reads; the comma is not the
#: period's, so it is allowed here and stripped before the year is read.
_WEEK_YEAR_SUFFIX = rf"(?:\s*,)?{_YEAR_SUFFIX}"


def _p_iso_week_compact(text: str, cfg: WranglerConfig) -> Spec | None:
    """"2026-W42", "2026W42" -- the ISO 8601 form, which carries its own year."""
    m = re.match(r"\s*((?:19|20)\d{2})\s*-?\s*w\s*(\d{1,2})\b", text, re.IGNORECASE)
    if not m:
        return None
    week = int(m.group(2))
    if not 1 <= week <= 53:
        return None
    return Spec(Kind.ISO_WEEK, year=int(m.group(1)), index=week)


def _p_week_number(text: str, cfg: WranglerConfig) -> Spec | None:
    """"week 42", "wk 42", "CW42", "week 42 of 2026"."""
    m = re.match(rf"\s*{_WEEK_WORD}\s*\.?\s*#?\s*(\d{{1,2}})\b(.*)$", text, re.IGNORECASE)
    if not m:
        return None
    week = int(m.group(1))
    if not 1 <= week <= 53:
        return None
    year, _ = _year_from_suffix(m.group(2).lstrip(" ,"), cfg)
    return Spec(Kind.ISO_WEEK, year=year, index=week)


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
    year, _ = _year_from_suffix(m.group(2).lstrip(" ,"), cfg)
    return Spec(Kind.ISO_WEEK, year=year, index=week)


#: "business day", "working days", "trading day". Weekend and holidays come from config.
_BIZ_WORD = r"(?:business|working|trading|work)"
_BIZ = rf"{_BIZ_WORD}\s+days?"


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


#: Between the two days of a run: "1-15", "1st to 15th", "1 through 15".
_DAY_JOIN = r"\s*(?:-|to|until|till|through|thru)\s*"
_DAY_NUM = r"\d{1,2}(?:st|nd|rd|th)?"


#: Units finer than a day, and how many of them: "last 3 hours", "half an hour ago".
_SUB_DAY = r"(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)"
_SUB_DAY_COUNT = rf"(?:{_NUM}|an?|half\s+an?|a\s+few|few|several|(?:a\s+)?couple\s+of)"


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
    r"\s*(?:the\s+)?(?:(?P<unit>week|month|quarter|half|year)\s+of"
    r"|(?P<from>week\s+(?:commencing|beginning|starting)|w/c))\s+(?:the\s+)?",
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
    return spec.with_(within=Grain[head.group("unit").upper()])


def _p_day_span(text: str, cfg: WranglerConfig) -> Spec | None:
    """"1-15 March", "1st to 15th March", "March 1-15", "March 1 to 15, 2024", and
    "between 1 and 15 March" -- the one place "and" joins two days into a run.

    The run used to collapse to one of its ends: "1-15 March" was the 15th alone, and
    "March 1-15" the 1st, each at full confidence, because the other number had no rule.
    """
    between = re.match(r"\s*between\s+", text, re.IGNORECASE)
    if between:
        text = re.sub(r"\s+and\s+", " - ", text[between.end() :], count=1, flags=re.IGNORECASE)
    m = re.match(
        rf"\s*(\d{{1,2}})(?:st|nd|rd|th)?{_DAY_JOIN}(\d{{1,2}})(?:st|nd|rd|th)?"
        rf"\s+(?:of\s+)?(.+)$",
        text,
        re.IGNORECASE,
    )
    if m:
        first, last, rest = int(m.group(1)), int(m.group(2)), m.group(3)
    else:
        m = re.match(
            rf"\s*({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?{_DAY_JOIN}(\d{{1,2}})"
            rf"(?:st|nd|rd|th)?(.*)$",
            text,
            re.IGNORECASE,
        )
        if not m:
            return None
        first, last, rest = int(m.group(2)), int(m.group(3)), f"{m.group(1)}{m.group(4)}"
    if not 1 <= first <= last <= 31:
        return None
    month = find_month(rest)
    if month is None:
        return None
    year, _, offset = _period_suffix(rest.replace(",", " "), cfg)
    return Spec(
        Kind.ABS_MONTH, month=month, year=year, year_offset=offset, day_span=(first, last)
    )


#: Between the days of a list: "1st, 5th and 9th", "1 & 15", "1st vs 15th".
_DAY_SEP = r"(?:\s*,\s*(?:and\s+|&\s*)?|\s+(?:and|vs\.?|versus)\s+|\s*&\s*)"
#: A day in a list: never the start of a longer number, so the 20 of "15, 2024" is not one.
_LIST_DAY = r"\d{1,2}(?:st|nd|rd|th)?(?!\d)"
_DAY_LIST = rf"{_LIST_DAY}(?:{_DAY_SEP}{_LIST_DAY})+"
#: "1st and 15th March 2024": the days, then the month and whatever year follows.
_DAY_LIST_FIRST = re.compile(rf"\s*(?P<days>{_DAY_LIST})\s+(?:of\s+)?(?P<rest>.+)$", re.I)
#: "March 1 and 15, 2024": the month, the days, then whatever year follows.
_MONTH_LIST_FIRST = re.compile(
    rf"\s*(?P<month>{_MONTH})\s+(?P<days>{_DAY_LIST})(?P<rest>.*)$", re.IGNORECASE
)
_DAY_TOKEN = re.compile(r"\d{1,2}(?:st|nd|rd|th)?", re.IGNORECASE)


def _p_day_list(text: str, cfg: WranglerConfig) -> Parts | None:
    """"1st and 15th March", "1, 5 and 9 March", "March 1 and 15, 2024" -- several days
    sharing one month, each a date of its own.

    REGRESSION: only one day survived, at full confidence. "1st and 15th March" was the
    15th, "March 1 and 15" the 1st: the other numbers had no month of their own to make
    them dates. Each day now comes back with its own span -- the month, and any year, go
    with the day they are written beside -- so a list reads like any other list.
    """
    month_first = _MONTH_LIST_FIRST.match(text)
    m = month_first or _DAY_LIST_FIRST.match(text)
    if m is None:
        return None
    rest = f"{m.group('month')} {m.group('rest')}" if month_first else m.group("rest")
    month = find_month(rest)
    if month is None:
        return None
    year, _, year_offset = _period_suffix(rest.replace(",", " "), cfg)
    basis = _stated_basis(rest) if year is not None else None
    days = list(_DAY_TOKEN.finditer(m.group("days")))
    values = [int(re.sub(r"\D", "", d.group(0))) for d in days]
    if len(days) < 2 or not all(1 <= v <= 31 for v in values):
        return None
    # A bare first number may be a count -- "the top 3 and 15 March" -- where a suffix
    # or a month in front of it settles the matter.
    sure = month_first is not None or days[0].group(0)[-1].isalpha()
    offset = m.start("days")
    parts: Parts = []
    for i, (d, value) in enumerate(zip(days, values, strict=True)):
        lo, hi = offset + d.start(), offset + d.end()
        if i == 0 and month_first:
            lo = m.start("month")  # "March 1": the month is written with the first day
        if i == len(days) - 1:
            hi = len(text.rstrip())  # "15th March 2024": and the year with the last
        spec = Spec(
            Kind.ABS_DAY,
            year=year,
            month=month,
            day=value,
            year_offset=year_offset,
            basis=basis,
            confidence=1.0 if sure else 0.8,
        )
        parts.append((spec, lo, hi))
    return parts


#: The units a run can be measured in, "the first 3 months of", "the last week of".
_SUB_UNIT = r"(?:days?|weeks?|fortnights?|months?|quarters?|qtrs?)"
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
    limit = {Grain.DAY: 31, Grain.WEEK: 5, Grain.MONTH: 12, Grain.QUARTER: 4}.get(unit, 0)
    if index > limit:
        return None
    target = _nth_target(m.group(4), cfg)
    if target is None:
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
        tail = re.sub(r"^\s*the\s+(?=year\b)", "", m.group(4), flags=re.IGNORECASE)
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
    """"same quarter last year", "this time last year" -- today's period, a year back."""
    m = re.match(
        rf"\s*(?:the\s+)?same\s+({_UNIT})\s+({_DIRWORD})\s+year\b", text, re.IGNORECASE
    )
    if m:
        unit = _unit_of(m.group(1))
        if unit is None:
            return None
        return Spec(Kind.THIS_PERIOD, unit=unit, year_offset=_direction_of(m.group(2)))
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


def _p_day_keyword(text: str, cfg: WranglerConfig) -> Spec | None:
    low = re.sub(r"\s+", " ", text.strip().lower())
    offset = {
        "today": 0,
        "yesterday": -1,
        "tomorrow": 1,
        # Idioms, not a modifier plus a day. Read as "before yesterday" they became
        # unbounded ranges reaching back to the beginning of time.
        "day before yesterday": -2,
        "the day before yesterday": -2,
        "day after tomorrow": 2,
        "the day after tomorrow": 2,
    }.get(low)
    if offset is None:
        return None
    return Spec(Kind.DAY_KEYWORD, direction=offset)


def _p_weekend(text: str, cfg: WranglerConfig) -> Spec | None:
    """"this weekend", "next weekend" -- Saturday and Sunday of the week meant."""
    m = re.match(rf"\s*(?:(this|{_DIRWORD})\s+)?weekend\b", text, re.IGNORECASE)
    if not m:
        return None
    word = (m.group(1) or "this").lower()
    direction = 0 if word == "this" else _direction_of(word)
    return Spec(Kind.WEEKEND, direction=direction)


#: "EOM"/"month end" and friends. The unit each names, and which end of it.
_EDGE_WORDS: dict[str, tuple[str, Part]] = {
    "eom": ("month", Part.LATE),
    "eoq": ("quarter", Part.LATE),
    "eoy": ("year", Part.LATE),
    "eow": ("week", Part.LATE),
}


def _p_period_edge(text: str, cfg: WranglerConfig) -> Spec | None:
    """"month end", "EOY", "start of the quarter" -- the edge of the current period."""
    low = re.sub(r"[\s.-]+", " ", text.strip().lower())
    edge = _EDGE_WORDS.get(low.replace(" ", ""))
    if edge is not None:
        unit_word, part = edge
        unit = _unit_of(unit_word)
        return None if unit is None else Spec(Kind.THIS_PERIOD, unit=unit, part=part)
    m = re.fullmatch(rf"(?:the\s+)?({_UNIT})[- ](end|start|beginning|close)", low)
    if not m:
        return None
    unit = _unit_of(m.group(1))
    if unit is None:
        return None
    part = Part.LATE if m.group(2) in ("end", "close") else Part.EARLY
    return Spec(Kind.THIS_PERIOD, unit=unit, part=part)


def _p_in_future(text: str, cfg: WranglerConfig) -> Spec | None:
    """"in 3 days", "2 weeks from now", "3 months from today".

    One period that far ahead, matching "3 months ago" in the other direction. Without
    this "3 months from today" matched only "today" and answered with a single day.
    """
    # "after 6 months" is a duration from now; "after March" is the AFTER modifier and is
    # handled elsewhere. The number is what tells them apart.
    m = re.match(rf"\s*(?:in|after|within)\s+({_NUM})\s+({_UNIT})\b", text, re.IGNORECASE)
    if m is None:
        m = re.match(
            rf"\s*({_NUM})\s+({_UNIT})\s+from\s+(?:now|today)\b", text, re.IGNORECASE
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


def _p_year_month_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"2024 March 15" -- the year first, as in a heading or a log."""
    m = re.fullmatch(
        rf"\s*((?:19|20)\d{{2}})\s+({_MONTH})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*", text, re.I
    )
    if not m:
        return None
    return Spec(
        Kind.ABS_DAY, year=int(m.group(1)), month=MONTHS[m.group(2).lower()],
        day=int(m.group(3)),
    )


def _p_month_ordinal_word(text: str, cfg: WranglerConfig) -> Spec | None:
    """"January first, 2024", "March the fifteenth" -- the day written as a word."""
    m = re.match(rf"\s*({_MONTH})\s+(?:the\s+)?({alt(ORDINALS)})", text, re.IGNORECASE)
    if not m:
        return None
    day = ordinal_to_int(m.group(2))
    if day is None:
        return None
    year, _, offset = _period_suffix(text[m.end() :].replace(",", " "), cfg)
    return Spec(
        Kind.ABS_DAY, year=year, month=MONTHS[m.group(1).lower()], day=day, year_offset=offset
    )


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
    if re.match(r"(?:till|until|upto)\b", low):
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
    # "2023-24 financial year": the fiscal year written before its name.
    Rule(
        "fy_range_after",
        r"\b\d{2,4}\s*[-/]\s*'?\d{2,4}\s+(?:financial|fiscal)\s+year\b",
        _p_fy_range,
    ),
    # A month in numbers -- "2024-03", "03/2024" -- and not the start of a longer date.
    Rule(
        "numeric_month",
        r"\b(?:(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])|(?:0?[1-9]|1[0-2])/(?:19|20)\d{2})"
        r"(?![\d/.-])",
        _p_numeric_month,
    ),
    # "2024-25" with nothing to say whether it is one fiscal year or two years: declined.
    Rule(
        "split_year",
        r"\b(?:19|20)\d{2}(?:[-/]\d{2}|/(?:19|20)\d{2})(?![\d/.-])",
        _p_split_year,
    ),
    Rule(
        "dashed_day",
        rf"\b\d{{1,2}}(?:-{_MONTH}-|/{_MONTH}/|\.{_MONTH}\.|{_MONTH})'?\d{{2,4}}\b",
        _p_dashed_day,
    ),
    # A fiscal year and then a month inside it, ahead of fy_range and year, which would
    # each take the year and leave the month behind. Not "may": in "FY25 may see growth"
    # it is a verb. "FY2024-25 June" only unspaced: "FY24 - 15 March" is a range.
    Rule(
        "fy_month",
        rf"\b{_FY_WORD}{_SEP}'?\d{{2,4}}(?:[-/]'?\d{{2,4}})?\s+(?!may\b){_MONTH}\b",
        _p_fy_month,
    ),
    # Not when a month follows: in "FY24 - 15 March 2024" the 15 is a day. Not when a
    # quarter does: "FY2024-25 Q3" is a quarter of that year, for labelled_period.
    Rule(
        "fy_range",
        rf"\b{_FY_WORD}\s*'?\d{{2,4}}\s*[-/]\s*'?\d{{2,4}}\b(?![\s,]+{_MONTH}\b)"
        rf"(?!\s*-?\s*{_QH}(?!\w))",
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
        rf"|{_QH}\s+(?:fy|cy)\b(?!\s*'?\d)"
        rf"|{_QH}['/-]\d{{2}}(?:\d{{2}})?(?!\d)"
        # Not when the quarter has a year of its own: "FY24 - Q3" is a label, "2023 - H2
        # 2024" a range from 2023 to H2 2024.
        rf"|(?:19|20)\d{{2}}\s*-?\s*{_QH}(?!\s*'?\d)"
        rf"|(?:{_FY_WORD}|{_CY_WORD})\s*'?\d{{2,4}}(?:[-/]'?\d{{2,4}})?\s*-?\s*{_QH}(?!\s*'?\d)"
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
        rf"\b(?:{_PAST}\s+)?(?:ytd|year\s*to\s*date)\s+{_MONTH}{_YEAR_SUFFIX}\b",
        _p_to_date,
    ),
    Rule(
        "to_date",
        rf"\b(?:{_PAST}\s+)?(?:ytd|mtd|qtd|year\s*to\s*date|month\s*to\s*date"
        rf"|quarter\s*to\s*date)(?:\s+(?:of\s+)?{_YEAR})?\b",
        _p_to_date,
    ),
    Rule(
        "year_ytd",
        rf"\b(?:(?:19|20)\d{{2}}|{_MARKED_YEAR})\s+(?:ytd|year\s*to\s*date)\b",
        _p_to_date,
    ),
    # After to_date, so "year to date" and "last year to date" keep their YTD reading, and
    # ahead of every plain period rule, which would otherwise claim "this year" or "Q3"
    # from the same position and leave "to date" behind.
    Rule("period_to_date", rf"\b{_TD_TARGET}{_TO_DATE}\b", _p_period_to_date),
    Rule(
        "period_ending",
        rf"\b(?:(?:the\s+)?(?:{_NUM}\s+)?(?:half[\s-]?years?|{_FY_WORD}|{_UNIT})(?:\s+|-)"
        rf"(?:end(?:ing|ed|s)?|to)|w/e|w\.e\.)\s+"
        rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-](?:\d{{4}}|\d{{2}})"
        rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{4}})?"
        rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?!\d)(?:,?\s+\d{{4}})?"
        rf"|{_MONTH}{_YEAR_SUFFIX}|{_YEAR})\b",
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
        rf"\b(?:the\s+)?(?:(?:week|month)\s+of\s+(?:the\s+)?{_ONE_DAY}"
        rf"|(?:quarter|half|year)\s+of\s+(?:the\s+)?(?:{_ONE_DAY}|{_ONE_MONTH})"
        rf"|(?:week\s+(?:commencing|beginning|starting)|w/c)\s+(?:the\s+)?{_ONE_DAY})\b",
        _p_within,
    ),
    # Before "part_of": "month end" is an edge of the current period, and part_of would
    # read the bare unit as the whole of it.
    Rule(
        "period_edge",
        rf"\b(?:eom|eoq|eoy|eow|(?:the\s+)?{_UNIT}[-\s](?:end|start|beginning|close))\b",
        _p_period_edge,
    ),
    Rule(
        "part_of",
        rf"\b(?:the\s+)?(?:{_PART_ALT}){_OF}{_PART_TARGET}\b",
        _p_part_of,
    ),
    Rule("weekend", rf"\b(?:(?:this|{_DIRWORD})\s+)?weekend\b", _p_weekend),
    Rule(
        "in_future",
        rf"\b(?:(?:in|after|within)\s+{_NUM}\s+{_UNIT}"
        rf"|{_NUM}\s+{_UNIT}\s+from\s+(?:now|today))\b",
        _p_in_future,
    ),
    Rule(
        "same_period",
        rf"\b(?:the\s+)?(?:same\s+{_UNIT}|this\s+time)\s+{_DIRWORD}\s+year\b",
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
    Rule("iso_week_compact", r"\b(?:19|20)\d{2}\s*-?\s*w\s*\d{1,2}\b", _p_iso_week_compact),
    Rule(
        "week_number",
        rf"\b{_WEEK_WORD}\s*\.?\s*#?\s*\d{{1,2}}\b{_WEEK_YEAR_SUFFIX}",
        _p_week_number,
    ),
    # Ahead of month_day_year, which takes "March 1" out of "March 1-15" from the same
    # place. The day-first form starts at the digit, so it is leftmost anyway.
    Rule(
        "day_span",
        rf"\b(?:between\s+{_DAY_NUM}\s+and\s+|{_DAY_NUM}{_DAY_JOIN})"
        rf"{_DAY_NUM}\s+(?:of\s+)?{_MONTH}{_YEAR_SUFFIX}"
        rf"|\b(?:between\s+{_MONTH}\s+{_DAY_NUM}\s+and\s+|{_MONTH}\s+{_DAY_NUM}{_DAY_JOIN})"
        rf"{_DAY_NUM}"
        # "March 1 to 15 April" runs across two months; leave that to the range merge.
        rf"(?!\d)(?!\s+(?:of\s+)?{_MONTH})(?:,?\s*\d{{4}})?\b",
        _p_day_span,
    ),
    # Days sharing a month, listed: "1st and 15th March", "March 1, 5 and 9". Each comes
    # back on its own. The month-first form takes a bare last number only before a year,
    # a stop or the end, so "March 1 and 15 customers" does not make 15 a day.
    Rule(
        "day_list",
        rf"\b{_DAY_LIST}\s+(?:of\s+)?{_MONTH}{_YEAR_SUFFIX}"
        rf"|\b{_MONTH}\s+{_LIST_DAY}(?:{_DAY_SEP}{_LIST_DAY})*{_DAY_SEP}\d{{1,2}}"
        rf"(?:(?:st|nd|rd|th)\b|(?=\s*(?:,?\s*\d{{4}}\b|[.,;:!?)]|$)))"
        rf"(?!\s+(?:of\s+)?{_MONTH})(?:,?\s*\d{{4}})?",
        _p_day_list,
    ),
    # Both must precede "relative" and the weekday rules, which otherwise claim the
    # opening words from the same position and win the alternation: "last day" scans as
    # "last 1 day" and "last Friday" as the weekday on its own.
    Rule(
        "edge_day",
        rf"\b(?:the\s+)?(?:first|1st|last|final)\s+(?:{_BIZ_WORD}\s+)?day{_NTH_OF}"
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
        rf"\b(?:the\s+)?{_NTH}\s+(?:{_WEEKDAY_ALT}){_NTH_OF}{_NTH_TARGET}\b",
        _p_nth_weekday,
    ),
    # After edge_day, which keeps "the last day of", and ahead of relative, fiscal_month
    # and month -- "the last week of March" otherwise scans as "last week" and a stray
    # March, and "the third month of the quarter" as the third month of the fiscal year.
    # Quarters only as "last"/"final": an ordinal quarter belongs to the quarter rule, and
    # claiming it here as well left "the first quarter of 2024" matched by this rule and
    # resolved by none.
    Rule(
        "sub_period",
        rf"\b(?:the\s+)?(?:{_NTH_STRICT}\s+(?:{_NUM}\s+)?(?:days?|weeks?|fortnights?|months?)"
        rf"|(?:first|1st|last|final)\s+{_NUM}\s+(?:quarters|qtrs)"
        rf"|(?:last|final)\s+(?:quarter|qtr)){_NTH_OF}{_NTH_TARGET}\b",
        _p_sub_period,
    ),
    Rule("final_quarter", r"\bthe\s+final\s+(?:quarter|qtr)\b(?!\s+(?:of|in)\b)", _p_final_quarter),
    Rule("nth_of", rf"\b(?:the\s+)?{_ORD}\s+of\s+{_PART_TARGET}\b", _p_nth_of),
    Rule(
        "day_month_year",
        # The bare two-digit year is only allowed here, and only when no colon follows,
        # so "15 Mar 14:30:00" stays a syslog time rather than becoming the year 2014.
        # It has to be tried first: _YEAR_SUFFIX is optional, so it matches empty and
        # would win the alternation before the two-digit form is ever considered.
        # The day may touch the month -- "01may 2018", as statements are asked for.
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s*{_MONTH}(?![a-z])"
        rf"(?:\s+\d{{2}}\b(?!\s*:)|{_YEAR_SUFFIX})",
        _p_day_month_year,
    ),
    # Syslog: "Mar 15 14:30:00". The month_day_year guard below refuses a following
    # number, so without this the timestamp on every syslog line is invisible.
    Rule("syslog", rf"\b{_MONTH}\s+\d{{1,2}}(?=\s+\d{{1,2}}:\d{{2}})", _p_month_day_year),
    # "March 15 2024" needs no comma, and "March the 15th" an article. REGRESSION: a
    # number after the day was refused outright, to keep syslog times out, so "JUNE 1 2018"
    # matched nothing; a four-digit year is not a time.
    Rule(
        "month_day_year",
        rf"\b{_MONTH}\s+(?:the\s+(?=\d{{1,2}}(?:st|nd|rd|th)))?\d{{1,2}}(?:st|nd|rd|th)?"
        # Not a year that a quarter follows: in "March 15, FY24 Q3" the FY24 is Q3's.
        rf"(?:\s*,?\s+{_YEAR}\b(?!\s*-?\s*{_QH}(?!\w))|\b(?!\s*\d))",
        _p_month_day_year,
    ),
    Rule(
        "year_month_day",
        rf"\b(?:19|20)\d{{2}}\s+{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?!\s*[\d:])",
        _p_year_month_day,
    ),
    # "January first, 2024": only before a year or the end of a clause, so "March first,
    # then June" and "the June first-quarter results" stay what they are.
    Rule(
        "month_ordinal_word",
        rf"\b{_MONTH}\s+(?:the\s+)?{alt(w for w in ORDINALS if not w[0].isdigit())}\b"
        rf"(?=\s*,?\s*(?:{_YEAR}\b|[.;:!?)]|$))(?:\s*,?\s*{_YEAR}\b)?",
        _p_month_ordinal_word,
    ),
    # Before "fiscal_month": both open with a bare number, and "3 months ago" would
    # otherwise read as "the 3rd month".
    Rule("ago", rf"\b{_NUM}\s+{_UNIT}\s+(?:{_AGO_WORDS})\b", _p_ago),
    # Singular "month" only: an ordinal position is "the third month", never "months".
    Rule("fiscal_month", rf"\b(?:the\s+)?{_ORD}\s+month\b{_YEAR_SUFFIX}", _p_fiscal_month),
    Rule(
        "relative_fy",
        rf"\b{_DIRWORD}\s+(?:{_NUM}\s+)?(?:{_FY_WORD}|{_CY_WORD})\b",
        _p_relative_fy,
    ),
    Rule(
        "relative", rf"\b{_DIRWORD}\s+(?:{_NUM}\s+)?(?:{_BASIS_WORD}\s+)?{_UNIT}\b", _p_relative
    ),
    Rule(
        "this_period",
        rf"\b(?:this|current|present)\s+(?:(?:{_BASIS_WORD}\s+)?{_UNIT}|{_FY_WORD}|{_CY_WORD})\b",
        _p_this,
    ),
    # "till date", "as on date": today, as statements are asked for. Before day_keyword.
    Rule(
        "till_now",
        r"\b(?:(?:till|until|upto)\s+(?:date|dated|now)|as\s+(?:on|of)\s+(?:date|dated))\b",
        _p_till_now,
    ),
    # "to present", "to now", "-present", "to date": today, but only as the end of the date
    # before it -- "we want to present the results" is not one.
    Rule(
        "to_now",
        r"\b(?:to|through|thru)\s+(?:the\s+)?(?:present|now)\b"
        r"(?!\s+(?:the|a|an|our|your|their|this|that|these|those|it|them|you|we|us)\b)"
        r"|\bto\s+date\b|\b(?:(?<=-)|(?<=-\s))(?:present|now|date)\b",
        _p_till_now,
    ),
    Rule(
        "day_keyword",
        r"\b(?:(?:the\s+)?day\s+(?:before\s+yesterday|after\s+tomorrow)"
        r"|today|yesterday|tomorrow)\b",
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
    Rule(
        "vague",
        r"\b(?:recently|lately|the\s+other\s+day|some\s+time\s+(?:ago|back)"
        r"|a\s+while\s+(?:ago|back)"
        r"|(?:a\s+)?(?:few|several|some|many)\s+(?:days?|weeks?|months?|years?)\s+(?:ago|back))\b",
        _p_decline("{text!r} is too vague to answer: it names no period"),
    ),
    Rule(
        "recurrence",
        rf"\bevery\s+(?:other\s+)?(?:{_WEEKDAY_ALT}|day|week|fortnight|month|quarter|year"
        rf"|weekday|weekend|{_NUM}\s+{_UNIT})\b",
        _p_decline("{text!r} recurs; it is not one period, so it is not resolved"),
    ),
    Rule(
        "season",
        r"\b(?:(?:last|this|next|past|coming|the)\s+)?(?:spring|summer|autumn|fall|winter)"
        r"\s+(?:of\s+)?(?:(?:19|20)\d{2}(?:\s*/\s*\d{2,4})?|'\d{2})\b"
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
        rf"\b(?:{_QWORD}\s*[1-4](?!\d)|{_ORD}\s*{_QWORD}){_YEAR_SUFFIX}\b",
        _p_quarter,
    ),
    Rule(
        "half",
        rf"\b(?:h\s*[12](?!\d)|half\s*[12](?!\d)|[12]\s*h\b|{_ORD}\s+{_HWORD}){_YEAR_SUFFIX}\b",
        _p_half,
    ),
    Rule("dashed_month_day", rf"\b{_MONTH}-\d{{1,2}}-(?:\d{{4}}|\d{{2}})\b", _p_dashed_month_day),
    Rule("dashed_month", rf"\b{_MONTH}-'?\d{{2,4}}\b", _p_dashed_month),
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
    # "the" only before "year": "March of the year 2013". Without it the month had no cue,
    # was dropped by strictness, and the answer was the whole year at full confidence.
    # A month of a fiscal year written in full, ahead of month_year, which would stop at
    # "June FY 2024" and leave the "-25" behind.
    Rule(
        "month_fy_span",
        rf"\b{_MONTH}\s+(?:of\s+)?{_FY_WORD}\s*'?\d{{2,4}}\s*[-/]\s*'?\d{{2,4}}\b",
        _p_month_of_fy_span,
    ),
    # A hyphen only before FY or CY, and unspaced: "Jun-FY25" is June of FY25, where
    # "Jun-25" (the dashed_month rule) is a calendar month and "June - FY25" a range.
    Rule(
        "month_year",
        rf"\b{_MONTH}(?:\s+(?:of\s+)?(?:the\s+(?=year\b))?{_YEAR}|-{_MARKED_YEAR})\b"
        rf"|\b{_MONTH}(?:'\s*\d{{4}}\b|'\d{{2}}(?!\d))",  # "Mar'24", "June' 2018"
        _p_month,
    ),
    # "last March", "this March", "next March": the nearest one, as "last Monday" is.
    Rule(
        "month_nearest",
        rf"\b(?:last|this|next|previous|coming)\s+{_MONTH}\b(?!\s*(?:\d|'))",
        _p_month_nearest,
    ),
    # "March last year" -- without this the month has no cue, is dropped by strictness,
    # and the answer becomes the whole of last year.
    Rule("month_rel_year", rf"\b{_MONTH}\s+(?:of\s+)?{_REL_YEAR}\b", _p_month),
    Rule("year", rf"\b{_YEAR_WORD}{_SEP}'?\d{{2,4}}\b", _p_year),
    Rule("month", rf"\b{_MONTH}\b", _p_month),
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
