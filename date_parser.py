# backend/app/core/date_parser.py
import contextvars
import re
from contextlib import contextmanager
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from typing import Optional, Tuple, TypedDict, Literal

from app.core.config import settings

# What day it is, for the question being processed.
#
# Every relative phrase here — "this month", "last quarter", "YTD" — is measured from today, and
# today used to be `datetime.now()`: the SERVER's date, which is UTC on the box. An Indian tenant
# asking on the 1st at 04:00 IST was answered for the previous month, silently and plausibly.
#
# A context variable rather than a `today` argument threaded through fifteen private helpers: the
# entry point sets it once for the call, every helper reads it, and nothing in between has to
# carry a parameter it does not use. Set through master_date_preprocessor, which is the only way
# in — see anchored_to.
_TODAY: contextvars.ContextVar = contextvars.ContextVar("date_parser_today", default=None)


@contextmanager
def anchored_to(day: Optional[date]):
    """Resolve relative dates as though today were `day`. None restores the machine's own date."""
    token = _TODAY.set(day)
    try:
        yield
    finally:
        _TODAY.reset(token)


def _now() -> date:
    """Today, for whoever is asking. A `date`, not a datetime — nothing here needs the time, and
    the hour is exactly the part that was wrong."""
    return _TODAY.get() or datetime.now().date()

# ---------------- Constants and Type Definitions ----------------
class DateTokenInfo(TypedDict, total=False):
    type: Literal['quarter', 'half', 'month', 'fy', 'cy', 'year', 'relative', 'current_year', 'this_period', 'ytd', 'unknown']
    raw: str; year: Optional[int]; fiscal: bool; quarter: int; half: str; month: int
    start: date; end: date; num: int; unit: str; direction: int; fiscal_unit: bool
    is_ordinal: bool

FISCAL_YEAR_START_MONTH = settings.FISCAL_YEAR_START_MONTH  # configurable; India defaults to April
MONTH_MAP = {
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3, 'april': 4, 'apr': 4, 'may': 5,
    'june': 6, 'jun': 6, 'july': 7, 'jul': 7, 'august': 8, 'aug': 8, 'september': 9, 'sept': 9, 'sep': 9,
    'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12
}
SORTED_MONTH_KEYS = sorted(MONTH_MAP.keys(), key=len, reverse=True)
NUMBER_WORDS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelfth': 12}
ORDINAL_WORDS = {
    'first': 1, '1st': 1, 'second': 2, '2nd': 2, 'third': 3, '3rd': 3, 'fourth': 4, '4th': 4, 'fifth': 5, '5th': 5,
    'sixth': 6, '6th': 6, 'seventh': 7, '7th': 7, 'eighth': 8, '8th': 8, 'ninth': 9, '9th': 9, 'tenth': 10, '10th': 10,
    'eleventh': 11, '11th': 11, 'twelfth': 12, '12th': 12
}
FILLER_KEYWORDS = r'(?:sales|revenue|profit|contribution|performance|data|report)'


# ---------------- Helpers ----------------
def _to_human_readable_range(s: date, e: date) -> str:
    return f"period from {s.strftime('%B %Y')} to {e.strftime('%B %Y')}"

def _get_inclusive_end_date(start: date, delta: relativedelta) -> date:
    return (start + delta) - relativedelta(days=1)

def _normalize_year(y_str: Optional[str]) -> int:
    if not y_str: return _now().year
    year_int = int(re.sub(r'\D', '', y_str))
    return 2000 + year_int if year_int < 100 else year_int

def _normalize_number(num_str: str) -> int:
    low = num_str.lower().strip()
    if low in NUMBER_WORDS: return NUMBER_WORDS[low]
    return int(low)

def _normalize_ordinal_to_int(text: str) -> Optional[int]:
    low = text.lower()
    for word, num in ORDINAL_WORDS.items():
        if re.search(rf'\b{word}\b', low): return num
    m = re.search(r'\b(\d{1,2})\b', low)
    if m: return int(m.group(1))
    return None

def _normalize_quarter(q_str: str) -> Optional[int]:
    low = q_str.lower().strip()
    # Don't claim a token that actually names a different period — otherwise the ordinal/"last"
    # fallbacks below grab "first half" as Q1 or "last month" as Q4. Let those fall through to
    # _normalize_half / _normalize_month instead.
    if 'half' in low or 'month' in low or re.search(r'\bh\s*[12]\b', low):
        return None
    if 'last' in low: return 4
    m = re.search(r'q(?:tr)?\s*(\d)', low)
    if m and 1 <= int(m.group(1)) <= 4:
        return int(m.group(1))
    num = _normalize_ordinal_to_int(low)
    if num and 1 <= num <= 4:
        return num
    return None

def _normalize_half(h_str: str) -> Optional[str]:
    low = h_str.lower().strip()
    if 'half' not in low and not re.search(r'\bh\s*[12]\b', low):
        return None
    if 'last' in low: return 'h2'
    if re.search(r'\b(1|first|1st|h\s*1)\b', low): return 'h1'
    if re.search(r'\b(2|second|2nd|h\s*2)\b', low): return 'h2'
    return None

def _normalize_month(m_str: str) -> Optional[Tuple[int, bool]]:
    low = m_str.lower().strip()
    if 'month' in low:
        if 'last' in low: return (12, True)
        num = _normalize_ordinal_to_int(low)
        if num and 1 <= num <= 12: return (num, True)
    for month_name in SORTED_MONTH_KEYS:
        if month_name in low:
            return (MONTH_MAP[month_name], False)
    if 'last' in low:
        return (12, False)
    return None

def _get_fiscal_start_date(year: int) -> date:
    return date(year - 1, FISCAL_YEAR_START_MONTH, 1)

def _get_current_fiscal_year() -> int:
    now = _now()
    return now.year + 1 if now.month >= FISCAL_YEAR_START_MONTH else now.year

def _get_month_dates(year: int, month: int) -> Tuple[date, date]:
    start = date(year, month, 1)
    end = _get_inclusive_end_date(start, relativedelta(months=1))
    return start, end

def _get_quarter_dates(year: int, quarter: int) -> Tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    end = _get_inclusive_end_date(start, relativedelta(months=3))
    return start, end


# ---------------- Main Date Calculation Logic ----------------
def _resolve_dates_from_info(info: DateTokenInfo, ref_year: Optional[int] = None) -> Optional[Tuple[date, date]]:
    now = _now()
    year = info.get('year') if info.get('year') is not None else (ref_year or now.year)
    type = info.get('type')
    if type == 'ytd':
        if info.get('direction') == -1:
            target_fy = _get_current_fiscal_year() - 1; start_date = _get_fiscal_start_date(target_fy)
            return start_date, _get_inclusive_end_date(start_date, relativedelta(years=1))
        if 'month' in info and 'year' in info:
            end_date = _get_inclusive_end_date(date(info['year'], info['month'], 1), relativedelta(months=1))
            fy_of_end_date = end_date.year + 1 if end_date.month >= FISCAL_YEAR_START_MONTH else end_date.year
            start_date = _get_fiscal_start_date(fy_of_end_date)
            return start_date, end_date
        if 'year' in info:
            end_date = date(info['year'], 12, 31)
            fy_of_end_date = info['year'] + 1 if FISCAL_YEAR_START_MONTH > 1 else info['year']
            start_date = _get_fiscal_start_date(fy_of_end_date)
            return start_date, end_date
        else:
            end_date = now; start_date = _get_fiscal_start_date(_get_current_fiscal_year())
            return start_date, end_date
    if type in ('fy', 'cy', 'year'): return info['start'], info['end']
    if type == 'current_year':
        current_fy = _get_current_fiscal_year(); start = _get_fiscal_start_date(current_fy)
        return start, _get_inclusive_end_date(start, relativedelta(years=1))
    if type == 'this_period':
        unit = info['unit']
        if unit == 'month': return _get_month_dates(now.year, now.month)
        if unit == 'year':
            current_fy = _get_current_fiscal_year(); start = _get_fiscal_start_date(current_fy)
            return start, _get_inclusive_end_date(start, relativedelta(years=1))
        if unit == 'quarter': return _get_quarter_dates(now.year, (now.month - 1) // 3 + 1)
    if type == 'relative':
        n, unit, direction = info['num'], info['unit'], info['direction']
        if unit.startswith('y'):
            is_calendar_unit = info.get('fiscal_unit') is False
            if not is_calendar_unit:
                current_fy = _get_current_fiscal_year(); start_fy = current_fy - n if direction == -1 else current_fy + 1
                end_fy = current_fy - 1 if direction == -1 else current_fy + n; start_date = _get_fiscal_start_date(start_fy)
                return start_date, _get_inclusive_end_date(_get_fiscal_start_date(end_fy + 1), relativedelta(days=0))
            else:
                start_year = now.year - n if direction == -1 else now.year + 1; end_year = now.year - 1 if direction == -1 else now.year + n
                return date(start_year, 1, 1), date(end_year, 12, 31)
        current_quarter_start = date(now.year, (now.month - 1) // 3 * 3 + 1, 1); current_month_start = date(now.year, now.month, 1)
        if direction == -1:
            if unit.startswith('q'):
                end_date = current_quarter_start - relativedelta(days=1); start_date = current_quarter_start - relativedelta(months=n * 3)
                return start_date, end_date
            if unit.startswith('m'):
                end_date = current_month_start - relativedelta(days=1); start_date = current_month_start - relativedelta(months=n)
                return start_date, end_date
        else:
            if unit.startswith('q'):
                start_date = current_quarter_start + relativedelta(months=3); end_date = _get_inclusive_end_date(start_date, relativedelta(months=n * 3))
                return start_date, end_date
            if unit.startswith('m'):
                start_date = current_month_start + relativedelta(months=1); end_date = _get_inclusive_end_date(start_date, relativedelta(months=n))
                return start_date, end_date
    if type == 'quarter':
        q = info['quarter']
        start_date = _get_fiscal_start_date(year) + relativedelta(months=3 * (q - 1)) if info.get('fiscal') else _get_quarter_dates(year, q)[0]
        return start_date, _get_inclusive_end_date(start_date, relativedelta(months=3))
    if type == 'half':
        is_h1 = (info['half'] == 'h1'); fiscal_start = _get_fiscal_start_date(year)
        start_date = (fiscal_start if is_h1 else fiscal_start + relativedelta(months=6)) if info.get('fiscal') else (date(year, 1, 1) if is_h1 else date(year, 7, 1))
        return start_date, _get_inclusive_end_date(start_date, relativedelta(months=6))
    if type == 'month':
        m = info['month']
        start_date = (_get_fiscal_start_date(year) + relativedelta(months=m - 1)) if info.get('fiscal') and info.get('is_ordinal') else date(year, m, 1)
        return start_date, _get_inclusive_end_date(start_date, relativedelta(months=1))
    return None

def _get_year_and_fiscal_flag(text_token: str) -> Tuple[Optional[int], bool]:
    year_str_match = re.search(r'((?:\'|20)?\d{2,4})', text_token); year_str = year_str_match.group(1) if year_str_match else None
    year = _normalize_year(year_str) if year_str else None
    is_calendar = any(k in text_token.lower() for k in ['cy', 'calendar year']); is_fiscal = not is_calendar
    return year, is_fiscal

def _parse_token_to_info(token: str) -> DateTokenInfo:
    t, low = token.strip(), token.lower()
    ytd_keyword_pattern = r'\b(ytd|year-?to-?date)\b'
    if re.search(ytd_keyword_pattern, low):
        if re.search(r'\b(last|previous)\b', low): return {'type': 'ytd', 'direction': -1}
        non_ytd_part = re.sub(ytd_keyword_pattern + r'\s*', '', low, count=1).strip()
        if non_ytd_part and (_normalize_month(non_ytd_part)):
            month_num, _ = _normalize_month(non_ytd_part); year, _ = _get_year_and_fiscal_flag(non_ytd_part)
            year = year or _now().year; return {'type': 'ytd', 'month': month_num, 'year': year}
        if non_ytd_part and re.fullmatch(r'(?:(?:fy|cy)?\s*(?:\'|20)?\d{2,4})', non_ytd_part):
            year, _ = _get_year_and_fiscal_flag(non_ytd_part); return {'type': 'ytd', 'year': year}
        return {'type': 'ytd'}
    
    # "period of year": handle fiscal vs calendar
    m_period_context = re.match(rf'^(?P<period_str>.*?)\s+(?:{FILLER_KEYWORDS}\s+)?of\s+(?P<year_str>.*)$', low)
    if m_period_context:
        period_str, year_str = m_period_context.group('period_str'), m_period_context.group('year_str')
        year, _ = _get_year_and_fiscal_flag(year_str)
        # "of [year]" implies calendar unless 'fy' is explicit.
        fiscal = any(k in year_str.lower() for k in ['fy', 'fiscal', 'financial'])

        if q_num := _normalize_quarter(period_str): return {'type': 'quarter', 'quarter': q_num, 'year': year, 'fiscal': fiscal}
        if half_key := _normalize_half(period_str): return {'type': 'half', 'half': half_key, 'year': year, 'fiscal': fiscal}
        if month_info := _normalize_month(period_str):
            month_num, is_ordinal = month_info
            return {'type': 'month', 'month': month_num, 'year': year, 'fiscal': fiscal, 'is_ordinal': is_ordinal}

    m_filler_of_period = re.match(rf'^{FILLER_KEYWORDS}\s+of\s+(?P<period_str>.*)$', low)
    if m_filler_of_period: return _parse_token_to_info(m_filler_of_period.group('period_str'))
    m_period_with_filler = re.match(rf'^(?P<period_str>.*?)\s+{FILLER_KEYWORDS}$', low)
    if m_period_with_filler: return _parse_token_to_info(m_period_with_filler.group('period_str'))
    fy_range_pattern = r'^(?:fy|financial year|fiscal year)\s*(?:\'|20)?(?P<y1>\d{2,4})\s?[/-]\s?(?:\'|20)?(?P<y2>\d{2,4})$'
    if m_fy_range := re.fullmatch(fy_range_pattern, low):
        end_year_str = m_fy_range.group('y2'); fiscal_year = _normalize_year(end_year_str)
        start = _get_fiscal_start_date(fiscal_year); end = _get_inclusive_end_date(start, relativedelta(years=1))
        return {'type': 'fy', 'year': fiscal_year, 'fiscal': True, 'start': start, 'end': end}
    year_keywords = r'fy|cy|financial year|fiscal year|calendar year|year'
    m_current_year = re.fullmatch(rf'current year(?:\s+(?:\'|20)?\d{{2,4}})?', low)
    if m_current_year:
        if re.search(r'\d', low):
            year, fiscal = _get_year_and_fiscal_flag(t)
            if fiscal:
                start = _get_fiscal_start_date(year); end = _get_inclusive_end_date(start, relativedelta(years=1))
                return {'type': 'fy', 'year': year, 'fiscal': True, 'start': start, 'end': end}
            else:
                start = date(year, 1, 1); end = date(year, 12, 31)
                return {'type': 'cy', 'year': year, 'fiscal': False, 'start': start, 'end': end}
        return {'type': 'current_year'}
    m_unambiguous_year = re.fullmatch(rf'(?:{year_keywords})\s*(?:\'|20)?\d{{2,4}}', low)
    if m_unambiguous_year:
        year, fiscal = _get_year_and_fiscal_flag(t)
        if fiscal:
            start = _get_fiscal_start_date(year); end = _get_inclusive_end_date(start, relativedelta(years=1))
            return {'type': 'fy', 'year': year, 'fiscal': True, 'start': start, 'end': end}
        else:
            start = date(year, 1, 1); end = date(year, 12, 31)
            return {'type': 'cy', 'year': year, 'fiscal': False, 'start': start, 'end': end}
    if m_this := re.fullmatch(r'this\s+(month|quarter|year)', low): return {'type': 'this_period', 'unit': m_this.group(1)}
    num_pattern_core = r'\d+|' + '|'.join(NUMBER_WORDS.keys())
    m_rel_ago = re.match(rf'^({num_pattern_core})\s+(qtr|quarter|month|year|yr)s?\s+(ago|before|after)$', low)
    if m_rel_ago:
        num_str, unit, direction_word = m_rel_ago.groups(); return {'type': 'relative', 'num': _normalize_number(num_str), 'unit': unit, 'direction': 1 if direction_word == 'after' else -1}
    m_rel_fy_cy = re.match(rf'^(last|past|previous|next)\s+({num_pattern_core})\s+(fy|cy)$', low)
    if m_rel_fy_cy:
        direction_word, num_str, period_type = m_rel_fy_cy.groups()
        return {'type': 'relative', 'num': _normalize_number(num_str), 'unit': 'year', 'direction': 1 if direction_word == 'next' else -1, 'fiscal_unit': (period_type == 'fy')}
    if m_rel_fy_cy_single := re.fullmatch(r'(next|last|previous)\s+(fy|cy)', low):
        direction_word, period_type = m_rel_fy_cy_single.groups(); direction = 1 if direction_word == 'next' else -1
        if period_type == 'fy':
            target_year = _get_current_fiscal_year() + direction; start = _get_fiscal_start_date(target_year); return {'type': 'fy', 'year': target_year, 'fiscal': True, 'start': start, 'end': _get_inclusive_end_date(start, relativedelta(years=1))}
        else:
            target_year = _now().year + direction; return {'type': 'cy', 'year': target_year, 'fiscal': False, 'start': date(target_year, 1, 1), 'end': date(target_year, 12, 31)}
    units_pattern = r'(?:qtr|quarter|month|year|yr)s?'
    m_rel_general = re.match(rf'^(last|past|previous|next|following|coming)\s+({num_pattern_core})?\s*({units_pattern})$', low)
    if m_rel_general:
        direction_word, num_str, unit = m_rel_general.groups(); num = _normalize_number(num_str) if num_str else 1
        return {'type': 'relative', 'num': num, 'unit': unit, 'direction': -1 if direction_word in ['last', 'past', 'previous'] else 1}
    if half_key := _normalize_half(low):
        year, fiscal = _get_year_and_fiscal_flag(t); return {'type': 'half', 'half': half_key, 'year': year, 'fiscal': fiscal}
    if q_num := _normalize_quarter(low):
        if 'month' not in low:
            year, fiscal = _get_year_and_fiscal_flag(t); return {'type': 'quarter', 'quarter': q_num, 'year': year, 'fiscal': fiscal}
    if month_info := _normalize_month(low):
        month_num, is_ordinal = month_info; year, fiscal = _get_year_and_fiscal_flag(t)
        token_info: DateTokenInfo = {'type': 'month', 'month': month_num, 'year': year, 'fiscal': fiscal}
        if is_ordinal: token_info['is_ordinal'] = True
        return token_info
    
    return {'type': 'unknown', 'raw': token}

# ---------------- Master Preprocessor and Regex ----------------
ORDINAL_PATTERN = r'(?:' + '|'.join(ORDINAL_WORDS.keys()) + r'|\d+|last)'
PAT_UNAMBIGUOUS_YEAR = r'\b(?:fy|cy|financial year|fiscal year|calendar year|year)\s*(?:\'|20)?\d{2,4}\b'
ANY_YEAR_SPEC = f'(?:{PAT_UNAMBIGUOUS_YEAR}|(?:(?<=\\s)|(?<=^))(?:\'|20)?\\d{{2,4}}\\b)'

CORE_MONTH_PATTERN = r'(?:' + '|'.join(SORTED_MONTH_KEYS) + r')'
CORE_ORDINAL_MONTH_PATTERN = rf'(?:the\s+)?{ORDINAL_PATTERN}(?:st|nd|rd|th)?\s+month'
WORD_PART_QH = r'(?:qtr|quarter|q|half|h)'; NUM_PART_QH = rf'(?:{ORDINAL_PATTERN})(?:st|nd|rd|th)?'
CORE_QH_PATTERN = rf'(?:{NUM_PART_QH}\s*{WORD_PART_QH}|{WORD_PART_QH}\s*{NUM_PART_QH})'

PAT_MONTH_WITH_YEAR = rf'\b(?:{CORE_MONTH_PATTERN}|{CORE_ORDINAL_MONTH_PATTERN})\s+{ANY_YEAR_SPEC}\b'
PAT_QH_WITH_YEAR = rf'\b{CORE_QH_PATTERN}\s+{ANY_YEAR_SPEC}\b'
PAT_MONTH_ALONE = rf'\b(?:{CORE_MONTH_PATTERN}|{CORE_ORDINAL_MONTH_PATTERN})\b'
PAT_QH_ALONE = rf'\b{CORE_QH_PATTERN}\b'

PAT_FY_RANGE = r'\b(?:fy|financial year|fiscal year)\s*(?:\'|20)?\d{2,4}\s?[/-]\s?(?:\'|20)?\d{2,4}\b'
CORE_PERIOD_PATTERN = rf'(?:{CORE_QH_PATTERN}|{CORE_MONTH_PATTERN}|{CORE_ORDINAL_MONTH_PATTERN})'
YEAR_SUFFIX_OPTIONAL = r'(?:\s+(?:of\s+)?(?:(?:fy|cy)?\s*(?:\'|20)?\d{2,4}))?'

# "of [year]" pattern uses ANY_YEAR_SPEC for robustness.
PAT_PERIOD_CONTEXT_OF_YEAR = rf'\b{CORE_PERIOD_PATTERN}(\s+{FILLER_KEYWORDS})?\s+of\s+{ANY_YEAR_SPEC}\b'
PAT_FILLER_OF_PERIOD = rf'\b{FILLER_KEYWORDS}\s+of\s+(?:{CORE_PERIOD_PATTERN}){YEAR_SUFFIX_OPTIONAL}\b'
PAT_PERIOD_WITH_FILLER = rf'\b{CORE_PERIOD_PATTERN}\s+{FILLER_KEYWORDS}\b'

YTD_KEYWORD = r'(?:ytd|year-?to-?date)'
PAT_YTD_LAST = rf'\b(last|previous)\s+{YTD_KEYWORD}\b'
SAFE_CORE_MONTH_PATTERN = rf'{CORE_MONTH_PATTERN}(?!\s+\d{{1,2}}(?:st|nd|rd|th)?\b)'
PAT_YTD_WITH_DATE = rf'\b{YTD_KEYWORD}\s+{SAFE_CORE_MONTH_PATTERN}{YEAR_SUFFIX_OPTIONAL}\b'
PAT_YTD_WITH_YEAR = rf'\b{YTD_KEYWORD}\s+{ANY_YEAR_SPEC}\b'
PAT_YTD_SIMPLE = rf'\b{YTD_KEYWORD}\b'

PAT_CURRENT_YEAR_WITH_YEAR = rf'\bcurrent year\s+{ANY_YEAR_SPEC}\b'; PAT_CURRENT_YEAR = r'\bcurrent year\b'
PAT_THIS_PERIOD = r'\bthis\s+(?:month|quarter|year)\b'; NUM_CORE_PATTERN = r'(?:\d+|' + '|'.join(NUMBER_WORDS.keys()) + r')'
PAT_RELATIVE_AGO = rf'\b{NUM_CORE_PATTERN}\s+(?:qtrs?|quarters?|months?|years?|yrs?)\s+(?:ago|before|after)\b'
PAT_RELATIVE_FY_CY_NUM = rf'\b(?:last|past|previous|next)\s+{NUM_CORE_PATTERN}\s+(?:fy|cy)\b'
PAT_RELATIVE_FY_CY_SINGLE = r'\b(?:next|last|previous)\s+(?:fy|cy)\b'
PAT_RELATIVE_GENERAL = rf'\b(?:last|past|previous|next|following|coming)\s+(?:{NUM_CORE_PATTERN}\s+)?(?:qtrs?|quarters?|months?|years?|yrs?)\b'

ANY_TOKEN_PATTERN = r'|'.join([
    PAT_PERIOD_CONTEXT_OF_YEAR, PAT_FILLER_OF_PERIOD, PAT_PERIOD_WITH_FILLER,
    PAT_YTD_LAST, PAT_YTD_WITH_DATE, PAT_YTD_WITH_YEAR, PAT_YTD_SIMPLE,
    PAT_FY_RANGE, PAT_CURRENT_YEAR_WITH_YEAR, 
    PAT_RELATIVE_AGO,
    PAT_MONTH_WITH_YEAR, PAT_QH_WITH_YEAR,
    PAT_UNAMBIGUOUS_YEAR, 
    PAT_MONTH_ALONE, PAT_QH_ALONE,
    PAT_RELATIVE_FY_CY_NUM, PAT_RELATIVE_FY_CY_SINGLE, PAT_RELATIVE_GENERAL, 
    PAT_THIS_PERIOD, PAT_CURRENT_YEAR
])
MASTER_PATTERN = re.compile(
    rf'\b(?:from\s+)?(?P<start_token_range>{ANY_TOKEN_PATTERN})\s*(?P<connector>to|and|-)\s*(?P<end_token_range>{ANY_TOKEN_PATTERN})\b'
    rf'|\b(?P<single_token>{ANY_TOKEN_PATTERN})\b', re.I
)

def _process_match(match: re.Match) -> Optional[str]:
    now = _now()
    groups = match.groupdict()
    if groups.get('single_token'):
        info = _parse_token_to_info(groups['single_token'])
        if info['type'] != 'unknown':
            if dates := _resolve_dates_from_info(info): return _to_human_readable_range(*dates)
    elif groups.get('start_token_range') and groups.get('end_token_range'):
        s_info = _parse_token_to_info(groups['start_token_range']); e_info = _parse_token_to_info(groups['end_token_range'])
        if s_info['type'] == 'unknown' or e_info['type'] == 'unknown': return None
        s_year_explicit = s_info.get('year'); e_year_explicit = e_info.get('year')
        default_year = now.year - 1 if (now.month < 3 and s_info.get('year') is None and 'q' in s_info.get('raw', '').lower()) else now.year
        final_s_year: int; final_e_year: int
        if s_year_explicit and e_year_explicit:
            final_s_year, final_e_year = s_year_explicit, e_year_explicit
        elif s_year_explicit:
            final_s_year, final_e_year = s_year_explicit, s_year_explicit
        elif e_year_explicit:
            final_s_year, final_e_year = e_year_explicit, e_year_explicit
        else:
            final_s_year, final_e_year = default_year, default_year
        s_dates = _resolve_dates_from_info(s_info, final_s_year); e_dates = _resolve_dates_from_info(e_info, final_e_year)
        if s_dates and e_dates:
            s_start, _ = s_dates; _, e_end = e_dates
            if e_end < s_start and e_year_explicit is None:
                if e_dates_next_year := _resolve_dates_from_info(e_info, final_e_year + 1):
                    e_end = e_dates_next_year[1]
            if e_end < s_start: return None
            return _to_human_readable_range(s_start, e_end)
    return None

def master_date_preprocessor(raw_text: str, today: Optional[date] = None) -> str:
    """Resolve relative date phrases in a question to explicit ranges.

    `today` is the asking project's current date — see project_time. Omitted, this falls back to
    the machine's date, which is what every caller did before and is only right when the server
    and the tenant share a timezone.
    """
    with anchored_to(today):
        replacements = []
        for match in MASTER_PATTERN.finditer(raw_text):
            if replacement_text := _process_match(match):
                replacements.append((match.start(), match.end(), replacement_text))
        processed_text = raw_text
        for start, end, text in reversed(replacements):
            processed_text = processed_text[:start] + text + processed_text[end:]
        return processed_text