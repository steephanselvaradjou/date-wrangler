"""date-wrangler: parse dates, ranges and periods out of natural language.

Everything resolves to one type -- a half-open :class:`DateRange`, either end optionally
unbounded. A single day, a week, a calendar month, a fiscal quarter and "since March" are
all the same shape, which is what lets them share an API.

    >>> from datetime import date
    >>> from date_wrangler import parse
    >>> m = parse("revenue for Q1 FY25", today=date(2025, 9, 4))[0]
    >>> m.range.start, m.range.end
    (datetime.date(2024, 4, 1), datetime.date(2024, 7, 1))
"""

from .calendars import DateRangeOverflow
from .config import DEFAULT_CONFIG, DateOrder, FiscalCalendar, ParserConfig, YearLabel
from .format import format_iso, format_range, make_formatter
from .parser import Diagnostic, diagnose, parse, parse_one, substitute
from .resolve import UnresolvableSpec
from .types import Basis, DateMatch, DateRange, Grain, Mod

__version__ = "0.1.0.dev0"

__all__ = [
    # Values
    "DateRange",
    "DateMatch",
    "Grain",
    "Basis",
    "Mod",
    # Configuration
    "ParserConfig",
    "FiscalCalendar",
    "DateOrder",
    "YearLabel",
    "DEFAULT_CONFIG",
    # Parsing
    "parse",
    "parse_one",
    "diagnose",
    "substitute",
    "Diagnostic",
    # Formatting
    "format_range",
    "format_iso",
    "make_formatter",
    # Errors
    "UnresolvableSpec",
    "DateRangeOverflow",
    "__version__",
]
