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
from .spec import Kind, Spec
from .types import Basis, Grain
from .vocab import (
    CARDINALS,
    FUTURE_WORDS,
    MONTH_NAMES,
    ORDINALS,
    PAST_WORDS,
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
#: A marked year may abut ("Q1FY24"); an unmarked one may not, or "Q12024" splits.
_YEAR_SUFFIX = rf"(?:\s*{_MARKED_YEAR}|\s+(?:of\s+)?{_YEAR})?"

_QWORD = r"(?:quarters?|qtrs?\.?|q)"
_HWORD = r"(?:halves|half|h)"

#: Words placing a count relative to now: "3 months ago", "5 years after". Defined once
#: because the scanning pattern and the parse function have to agree.
_AGO_WORDS = r"ago|back|earlier|prior|before|later|hence|after|ahead|out"
_AGO_FUTURE = frozenset({"later", "hence", "after", "ahead", "out"})


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
    m = re.search(rf"\s*(?:of\s+)?({_YEAR})\s*$", text, re.IGNORECASE)
    if not m:
        return None, None
    year, basis = parse_year_token(m.group(1), cfg)
    if basis is None and re.search(r"\bof\b", text, re.IGNORECASE):
        basis = cfg.effective_of_year_basis
    return year, basis


def _unit_of(word: str) -> Grain | None:
    name = UNIT_WORDS.get(word.lower().rstrip("."))
    return Grain[name] if name else None


def _direction_of(word: str) -> int:
    return 1 if word.lower() in FUTURE_WORDS else -1


# ---------------------------------------------------------------------------
# Parse functions
# ---------------------------------------------------------------------------


def _p_iso(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text.strip())
    if not m:
        return None
    return Spec(Kind.ABS_DAY, year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))


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
    return Spec(Kind.RELATIVE, count=count, unit=Grain.MONTH, direction=-1)


def _p_period_ending(text: str, cfg: WranglerConfig) -> Spec | None:
    """"quarter ending June 2024", "year ended March 2024"."""
    m = re.match(rf"\s*(?:the\s+)?({_UNIT})\s+end", text, re.IGNORECASE)
    if not m:
        return None
    unit = _unit_of(m.group(1))
    if unit is None:
        return None
    month = find_month(text)
    year, basis = _year_from_suffix(text, cfg)
    if month is None and year is None:
        return None
    return Spec(Kind.PERIOD_ENDING, unit=unit, month=month, year=year, basis=basis)


def _p_weekday(text: str, cfg: WranglerConfig) -> Spec | None:
    low = text.strip().lower()
    m = re.search(rf"\b({alt(WEEKDAY_NAMES)})\b", low)
    if not m:
        return None
    index = WEEKDAYS[m.group(1)]
    if re.match(rf"\s*{_PAST}\b", low):
        direction = -1
    elif re.match(rf"\s*{_FUTURE}\b", low):
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


def _p_quarter(text: str, cfg: WranglerConfig) -> Spec | None:
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
    year, basis = _year_from_suffix(text, cfg)
    return Spec(Kind.ABS_QUARTER, year=year, index=index, basis=basis)


def _p_half(text: str, cfg: WranglerConfig) -> Spec | None:
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
    year, basis = _year_from_suffix(text, cfg)
    return Spec(Kind.ABS_HALF, year=year, index=index, basis=basis)


def _p_month(text: str, cfg: WranglerConfig) -> Spec | None:
    month = find_month(text)
    if month is None:
        return None
    year, _ = _year_from_suffix(text, cfg)
    return Spec(Kind.ABS_MONTH, year=year, month=month)


def _p_year(text: str, cfg: WranglerConfig) -> Spec | None:
    year, basis = parse_year_token(text, cfg)
    if year is None:
        return None
    return Spec(Kind.ABS_YEAR, year=year, basis=basis)


def _p_bare_year(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.fullmatch(rf"\s*({_BARE_YEAR})\s*", text)
    if not m:
        return None
    # A year on its own is a calendar year. Only a quarter or half labelled with one
    # inherits the configured basis, because that case is genuinely ambiguous.
    return Spec(Kind.ABS_YEAR, year=int(m.group(1)), basis=Basis.CALENDAR, confidence=0.8)


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
    return Spec(Kind.AGO, count=count, unit=unit, direction=direction)


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


def _p_relative(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(rf"\s*({_DIRWORD})\s+(?:({_NUM})\s+)?({_UNIT})\b", text, re.IGNORECASE)
    if not m:
        return None
    count = word_to_int(m.group(2)) if m.group(2) else 1
    unit = _unit_of(m.group(3))
    if count is None or unit is None:
        return None
    return Spec(Kind.RELATIVE, count=count, unit=unit, direction=_direction_of(m.group(1)))


def _p_this(text: str, cfg: WranglerConfig) -> Spec | None:
    m = re.match(rf"\s*(?:this|current|present)\s+({_UNIT})\b", text, re.IGNORECASE)
    if not m:
        return None
    unit = _unit_of(m.group(1))
    if unit is None:
        return None
    return Spec(Kind.THIS_PERIOD, unit=unit)


def _p_day_keyword(text: str, cfg: WranglerConfig) -> Spec | None:
    low = text.strip().lower()
    offset = {"today": 0, "yesterday": -1, "tomorrow": 1}.get(low)
    if offset is None:
        return None
    return Spec(Kind.DAY_KEYWORD, direction=offset)


# ---------------------------------------------------------------------------
# The table. Order is priority: specific before general.
# ---------------------------------------------------------------------------

RULES: tuple[Rule, ...] = (
    Rule("iso", r"\b\d{4}-\d{1,2}-\d{1,2}\b", _p_iso),
    Rule("fy_range", rf"\b{_FY_WORD}\s*'?\d{{2,4}}\s*[-/]\s*'?\d{{2,4}}\b", _p_fy_range),
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
    Rule(
        "period_ending",
        rf"\b(?:the\s+)?{_UNIT}\s+end(?:ing|ed|s)?\s+(?:{_MONTH}{_YEAR_SUFFIX}|{_YEAR})\b",
        _p_period_ending,
    ),
    Rule("trailing_months", r"\b(?:ttm|ltm|[tl]\d{1,2}m)\b", _p_trailing_months),
    Rule(
        "day_month_year",
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}{_YEAR_SUFFIX}\b",
        _p_day_month_year,
    ),
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
    Rule("relative", rf"\b{_DIRWORD}\s+(?:{_NUM}\s+)?{_UNIT}\b", _p_relative),
    Rule("this_period", rf"\b(?:this|current|present)\s+{_UNIT}\b", _p_this),
    Rule("day_keyword", r"\b(?:today|yesterday|tomorrow)\b", _p_day_keyword),
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
    Rule("month_year", rf"\b{_MONTH}\s+(?:of\s+)?{_YEAR}\b", _p_month),
    Rule("year", rf"\b{_YEAR_WORD}{_SEP}'?\d{{2,4}}\b", _p_year),
    Rule("month", rf"\b{_MONTH}\b", _p_month),
    Rule("weekday", rf"\b{alt(WEEKDAY_NAMES)}\b", _p_weekday),
    Rule("bare_year", rf"\b{_BARE_YEAR}\b", _p_bare_year),
)
