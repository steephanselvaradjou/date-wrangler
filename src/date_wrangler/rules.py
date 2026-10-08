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

from .config import MonthNumber, WranglerConfig
from .spec import Kind, Part, Spec
from .types import Anchor, Basis, Grain
from .vocab import (
    CARDINALS,
    FUTURE_WORDS,
    MONTH_NAMES,
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
_CY_WORD = r"(?:c\.?y\.?|calendar\s+year)"
_YEAR_WORD = rf"(?:{_FY_WORD}|{_CY_WORD}|year)"
#: A basis said outright in front of a unit: "this fiscal year", "last calendar quarter".
_BASIS_WORD = r"(?:fiscal|financial|calendar)"


def _basis_of(word: str | None) -> Basis | None:
    """The basis a "fiscal"/"financial"/"calendar" word names, or None when absent."""
    if word is None:
        return None
    return Basis.CALENDAR if word.lower().startswith("c") else Basis.FISCAL
_SEP = r"[\s-]*"

#: A bare two-digit number is never a year -- that is how a day-of-month becomes one.
_MARKED_YEAR = rf"(?:{_FY_WORD}|{_CY_WORD}){_SEP}'?\d{{2,4}}"

#: A year where nothing else can be meant -- after a month, quarter or half. Any four
#: digits, since "January 2100" is unambiguous. Restricting this to 19xx/20xx meant we
#: could not read back our own output, and substitute() grew the text on every pass.
_YEAR = rf"(?:{_YEAR_WORD}{_SEP}'?\d{{2,4}}|'\d{{2}}|\d{{4}})"

#: A year standing alone. Here the century is all that separates a date from a
#: quantity, so "5000" stays a number.
_BARE_YEAR = r"(?:19|20|21)\d{2}"
#: "last year", "next year", "this year" -- a year named by offset rather than by number.
#: Without this "Q1 last year" scans as two separate matches and the Q1 keeps the current
#: year, which is silently wrong rather than merely unrecognised.
_REL_YEAR = rf"(?:{_DIRWORD}|this|current)\s+year"

#: A marked year may abut ("Q1FY24"); an unmarked one may not, or "Q12024" splits.
_YEAR_SUFFIX = (
    rf"(?:\s*{_MARKED_YEAR}|\s+(?:of\s+)?(?:the\s+(?=year\b))?{_YEAR}"
    rf"|\s+(?:of\s+)?{_REL_YEAR})?"
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
_YEAR_TAIL_RE = re.compile(rf"\s*(?:of\s+)?({_YEAR})\s*$", re.IGNORECASE)
_REL_YEAR_TAIL_RE = re.compile(
    rf"\s*(?:of\s+)?({_DIRWORD}|this|current)\s+year\s*$", re.IGNORECASE
)
_OF_RE = re.compile(r"\bof\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Rule:
    """A named recogniser."""

    name: str
    pattern: str
    parse: Callable[[str, WranglerConfig], Spec | None]


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


def parse_year_token(token: str, cfg: WranglerConfig) -> tuple[int | None, Basis | None]:
    """Read a year fragment, returning the year and any basis it declared."""
    low = token.lower()
    basis: Basis | None = None
    # No trailing \b -- "cy2024" has no boundary between marker and digits, and
    # requiring one silently drops the CY the writer went out of their way to give.
    if re.search(rf"\bc\.?y\.?(?={_SEP}'?\d)|\bcalendar\s+year\b", low):
        basis = Basis.CALENDAR
    elif re.search(rf"\bf\.?y\.?(?={_SEP}'?\d)|\b(?:fiscal|financial)\s+year\b", low):
        basis = Basis.FISCAL
    m = _YEAR_DIGITS.search(low)
    return (_pivot_year(m.group(1), cfg) if m else None), basis


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


def _p_dashed_day(text: str, cfg: WranglerConfig) -> Spec | None:
    """"15-Mar-2024", "15-Mar-24" -- what spreadsheets and SQL clients emit."""
    month = find_month(text)
    if month is None:
        return None
    m = re.match(r"\s*(\d{1,2})\s*-", text)
    if not m:
        return None
    year_m = re.search(r"-\s*'?(\d{2,4})\s*$", text)
    year = _pivot_year(year_m.group(1), cfg) if year_m else None
    return Spec(Kind.ABS_DAY, year=year, month=month, day=int(m.group(1)))


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


def _p_decade(text: str, cfg: WranglerConfig) -> Spec | None:
    """"the 1990s" -- ten years, starting at the zero."""
    # Case-insensitive: the scanner lowered the text to find this, but the fragment here
    # is the original, and a heading in capitals -- "THE 1990S" -- has a capital S.
    m = re.search(r"\b((?:1[89]|20)\d)0s\b", text, re.IGNORECASE)
    if not m:
        return None
    return Spec(Kind.DECADE, year=int(m.group(1)) * 10)


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

    A component above 12 can only be a day, so unambiguous input reads right regardless.
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
    return Spec(Kind.ABS_DAY, year=year, month=month, day=day, confidence=0.9)


def _p_day_month_year(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(r"\s*(\d{1,2})(?:st|nd|rd|th)?\s+", text, re.IGNORECASE)
    if not m:
        return None
    month = find_month(text)
    if month is None:
        return None
    year, _ = _year_from_suffix(text, cfg)
    if year is None:
        # "15 Mar 24". A bare two-digit number is normally too ambiguous to be a year, but
        # here the day slot is already filled, so nothing else is left for it to be.
        short = re.search(r"\s+(\d{2})\s*$", text)
        if short:
            year = _pivot_year(short.group(1), cfg)
    return Spec(Kind.ABS_DAY, year=year, month=month, day=int(m.group(1)))


def _p_month_day_year(text: str, cfg: WranglerConfig) -> Spec | None:
    """"January 15, 2024", "march 3" -- and the ambiguous "jan 24".

    Only a bare two-digit number with no year beside it is actually in doubt. Everything
    else settles itself: an ordinal suffix or a single digit is a day, an explicit year
    means the number beside it is a day, and anything above 31 can only be a year.
    """
    month = find_month(text)
    if month is None:
        return None
    m = re.search(rf"{_MONTH}\s+(\d{{1,2}})(st|nd|rd|th)?", text, re.IGNORECASE)
    if not m:
        return None
    digits, suffix = m.group(1), m.group(2)
    year, _ = _year_from_suffix(text, cfg)
    if year is not None or suffix or len(digits) == 1:
        return Spec(Kind.ABS_DAY, year=year, month=month, day=int(digits))
    value = int(digits)
    if value > 31 or cfg.month_number is MonthNumber.YEAR:
        return Spec(Kind.ABS_MONTH, month=month, year=_pivot_year(digits, cfg))
    return Spec(Kind.ABS_DAY, month=month, day=value)


def _p_fy_range(text: str, cfg: WranglerConfig) -> Spec | None:
    """One fiscal year written with both its calendar years: "FY2024-25"."""
    m = re.search(r"'?(\d{2,4})\s*[-/]\s*'?(\d{2,4})", text)
    if not m:
        return None
    return Spec(Kind.ABS_YEAR, year=_pivot_year(m.group(2), cfg), basis=Basis.FISCAL)


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
        rf"\s*(?:the\s+)?(?:({_NUM})\s+)?(half[\s-]?years?|{_UNIT})\s+"
        rf"(end(?:ing|ed|s)?|to)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    word = m.group(2).lower()
    unit = Grain.HALF if word.startswith("half") else _unit_of(m.group(2))
    count = word_to_int(m.group(1)) if m.group(1) else 1
    if unit is None or count is None or count < 1:
        return None
    # "3 days to March" counts down to something; it is not a period that ends in March.
    if m.group(3).lower() == "to" and unit in (Grain.DAY, Grain.WEEK):
        return None
    count *= _scale_of(m.group(2))
    tail = m.group(4)
    # An end given as a day -- "30 June 2024", "June 30, 2024" -- rather than a month.
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
    elif _FUTURE_RE.match(low):
        direction = 1
    else:
        direction = 0
    confidence = 1.0 if direction or low != m.group(1) else 0.8
    return Spec(Kind.WEEKDAY, index=index, direction=direction, confidence=confidence)


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
        rf"\s*(?P<qh>{_QH})['-](?P<yr>\d{{2}}(?:\d{{2}})?)\s*",                 # Q3'24 Q3-2024
        rf"\s*(?P<yr>(?:19|20)\d{{2}})\s*-?\s*(?P<qh>{_QH})\s*",                # 2024-Q3 2024Q3
        rf"\s*(?P<basis>{_FY_WORD}|{_CY_WORD})\s*'?(?P<yr>\d{{2,4}})\s*-?\s*(?P<qh>{_QH})\s*",
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
    if groups.get("yr"):
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
    month = find_month(text)
    if month is None:
        return None
    year, _, offset = _period_suffix(text, cfg)
    return Spec(Kind.ABS_MONTH, year=year, month=month, year_offset=offset)


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
    rf"|h\s*[12](?!\d){_YEAR_SUFFIX}|{_YEAR}|{_DIRWORD}\s+(?:{_BASIS_WORD}\s+)?{_UNIT}"
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
    month = find_month(tail)
    if month is not None:
        year, _, offset = _period_suffix(tail, cfg)
        return Spec(Kind.ABS_MONTH, month=month, year=year, year_offset=offset)
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


def _p_day_span(text: str, cfg: WranglerConfig) -> Spec | None:
    """"1-15 March", "1st to 15th March", "March 1-15", "March 1 to 15, 2024".

    The run used to collapse to one of its ends: "1-15 March" was the 15th alone, and
    "March 1-15" the 1st, each at full confidence, because the other number had no rule.
    """
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


#: The units a run can be measured in, "the first 3 months of", "the last week of".
_SUB_UNIT = r"(?:days?|weeks?|months?|quarters?|qtrs?)"
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
    return Spec(Kind.AGO, count=count * _scale_of(m.group(2)), unit=unit, direction=1)


# ---------------------------------------------------------------------------
# The table. Order is priority: specific before general.
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    # No trailing \b: an ISO timestamp runs the date straight into the time with a "T",
    # and a word boundary between "5" and "T" does not exist -- so every ISO 8601 instant
    # in a log line was being skipped.
    Rule("iso", r"\b\d{4}-\d{1,2}-\d{1,2}(?!\d)", _p_iso),
    Rule("dashed_day", rf"\b\d{{1,2}}-{_MONTH}-'?\d{{2,4}}\b", _p_dashed_day),
    Rule("fy_range", rf"\b{_FY_WORD}\s*'?\d{{2,4}}\s*[-/]\s*'?\d{{2,4}}\b", _p_fy_range),
    # Ahead of the year, quarter and half rules, which start at the same place and would
    # each take one piece: "FY24" out of "FY24 Q3", "2024" out of "2024-Q3", "Q3" out of
    # "Q3'24". (?!\w) rather than \b, because "Q3'24" ends on a digit after a quote.
    Rule(
        "labelled_period",
        rf"\b(?:{_BASIS_TAG}\s+{_QH}{_YEAR_SUFFIX}"
        rf"|{_QH}\s+(?:fiscal|financial|calendar)\b"
        rf"|{_QH}\s+(?:fy|cy)\b(?!\s*'?\d)"
        rf"|{_QH}['-]\d{{2}}(?:\d{{2}})?(?!\d)"
        rf"|(?:19|20)\d{{2}}\s*-?\s*{_QH}"
        rf"|(?:{_FY_WORD}|{_CY_WORD})\s*'?\d{{2,4}}\s*-?\s*{_QH}"
        rf"|(?:[1-4]\s*q|[12]\s*h)\s*'?\d{{2,4}})(?!\w)",
        _p_labelled_period,
    ),
    Rule("numeric", r"\b\d{1,4}[/.]\d{1,2}[/.]\d{1,4}\b", _p_numeric),
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
    # After to_date, so "year to date" and "last year to date" keep their YTD reading, and
    # ahead of every plain period rule, which would otherwise claim "this year" or "Q3"
    # from the same position and leave "to date" behind.
    Rule("period_to_date", rf"\b{_TD_TARGET}{_TO_DATE}\b", _p_period_to_date),
    Rule(
        "period_ending",
        rf"\b(?:the\s+)?(?:{_NUM}\s+)?(?:half[\s-]?years?|{_UNIT})\s+(?:end(?:ing|ed|s)?|to)\s+"
        rf"(?:\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{4}})?"
        rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?!\d)(?:,?\s+\d{{4}})?"
        rf"|{_MONTH}{_YEAR_SUFFIX}|{_YEAR})\b",
        _p_period_ending,
    ),
    Rule("trailing_months", r"\b(?:ttm|ltm|[tl]\d{1,2}m)\b", _p_trailing_months),
    # Before "half" and "quarter": "first half of March" opens with something the half
    # rule will happily claim as fiscal H1, throwing the month away.
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
    # as the dash and leave "W42" behind.
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
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?{_DAY_JOIN}\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?"
        rf"{_MONTH}{_YEAR_SUFFIX}"
        rf"|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?{_DAY_JOIN}\d{{1,2}}(?:st|nd|rd|th)?"
        # "March 1 to 15 April" runs across two months; leave that to the range merge.
        rf"(?!\d)(?!\s+(?:of\s+)?{_MONTH})(?:,?\s*\d{{4}})?\b",
        _p_day_span,
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
        rf"\b(?:the\s+)?(?:{_NTH_STRICT}\s+(?:{_NUM}\s+)?(?:days?|weeks?|months?)"
        rf"|(?:last|final)\s+(?:quarter|qtr)){_NTH_OF}{_NTH_TARGET}\b",
        _p_sub_period,
    ),
    Rule("nth_of", rf"\b(?:the\s+)?{_ORD}\s+of\s+{_PART_TARGET}\b", _p_nth_of),
    Rule(
        "day_month_year",
        # The bare two-digit year is only allowed here, and only when no colon follows,
        # so "15 Mar 14:30:00" stays a syslog time rather than becoming the year 2014.
        # It has to be tried first: _YEAR_SUFFIX is optional, so it matches empty and
        # would win the alternation before the two-digit form is ever considered.
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{2}}\b(?!\s*:)|{_YEAR_SUFFIX})",
        _p_day_month_year,
    ),
    # Syslog: "Mar 15 14:30:00". The month_day_year guard below refuses a following
    # number, so without this the timestamp on every syslog line is invisible.
    Rule("syslog", rf"\b{_MONTH}\s+\d{{1,2}}(?=\s+\d{{1,2}}:\d{{2}})", _p_month_day_year),
    Rule(
        "month_day_year",
        rf"\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?!\s*\d)(?:\s*,?\s+{_YEAR})?\b",
        _p_month_day_year,
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
    Rule(
        "day_keyword",
        r"\b(?:(?:the\s+)?day\s+(?:before\s+yesterday|after\s+tomorrow)"
        r"|today|yesterday|tomorrow)\b",
        _p_day_keyword,
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
    Rule("dashed_month", rf"\b{_MONTH}-'?\d{{2,4}}\b", _p_dashed_month),
    Rule("decade", r"\b(?:the\s+)?(?:1[89]|20)\d0s\b", _p_decade),
    Rule("cob", r"\b(?:eod|cob|eob)(?:\s+(?:on\s+)?" + alt(WEEKDAY_NAMES) + r")?\b",
         _p_close_of_business),
    # "the" only before "year": "March of the year 2013". Without it the month had no cue,
    # was dropped by strictness, and the answer was the whole year at full confidence.
    Rule("month_year", rf"\b{_MONTH}\s+(?:of\s+)?(?:the\s+(?=year\b))?{_YEAR}\b", _p_month),
    # "March last year" -- without this the month has no cue, is dropped by strictness,
    # and the answer becomes the whole of last year.
    Rule("month_rel_year", rf"\b{_MONTH}\s+(?:of\s+)?{_REL_YEAR}\b", _p_month),
    Rule("year", rf"\b{_YEAR_WORD}{_SEP}'?\d{{2,4}}\b", _p_year),
    Rule("month", rf"\b{_MONTH}\b", _p_month),
    Rule("weekday", rf"\b{alt(WEEKDAY_NAMES)}\b", _p_weekday),
    # Weak, like a bare month: "W42" is as likely a part number as a week, so in balanced
    # mode it needs a cue. The explicit forms above never do.
    Rule("week_bare", rf"\bw\s*\d{{1,2}}\b{_WEEK_YEAR_SUFFIX}", _p_week_bare),
    Rule("bare_year", rf"\b{_BARE_YEAR}\b", _p_bare_year),
    # Last, and starting at the digit rather than at "the": the scanner takes the leftmost
    # match, so swallowing the article would beat the quarter rule to "the 5th quarter"
    # whatever the order here says. The cue check allows the article instead.
    # ...and only at the end of a clause. "on the 15th" is a date; "on the 3rd floor",
    # "the 2nd round", "for the 3rd time" are not, and a following noun is what tells them
    # apart. "the 15th of March" is the nth_of rule's job, not this one's.
    Rule("ordinal_day", r"\b\d{1,2}(?:st|nd|rd|th)\b(?!\s*\w)", _p_ordinal_day),
)
