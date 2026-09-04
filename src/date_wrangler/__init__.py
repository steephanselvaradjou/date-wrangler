"""date-wrangler: wrangles messy human dates into clean ranges.

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
from .config import (
    DEFAULT_CONFIG,
    DateOrder,
    FiscalCalendar,
    MonthNumber,
    WranglerConfig,
    YearLabel,
)
from .format import format_iso, format_range, make_formatter
from .resolve import UnresolvableSpec
from .types import Basis, DateMatch, DateRange, Grain, Mod
from .wrangler import Diagnostic, diagnose, parse, parse_one, substitute

__version__ = "0.1.0"

__all__ = [
    # Values
    "DateRange",
    "DateMatch",
    "Grain",
    "Basis",
    "Mod",
    # Configuration
    "WranglerConfig",
    "FiscalCalendar",
    "DateOrder",
    "MonthNumber",
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
