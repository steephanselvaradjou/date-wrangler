"""Everything the wrangler treats as policy rather than fact.

Passed per call, never read from a module global, so one process can serve tenants on
different fiscal calendars. Frozen and validated on construction, so a bad fiscal month
names the field it came from instead of failing later inside ``date()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .types import Basis

__all__ = ["DateOrder", "MonthNumber", "YearLabel", "FiscalCalendar", "WranglerConfig"]


class DateOrder(Enum):
    """How to read an all-numeric date like ``03/04/2024``.

    3 April to most of the world, 4 March in the US, so this has to be your choice.
    Unambiguous forms -- ISO, or any component above 12 -- are read right regardless.
    """

    DMY = "DMY"
    MDY = "MDY"
    YMD = "YMD"


class MonthNumber(Enum):
    """What a bare two-digit number after a month name means: ``jan 24``.

    Genuinely ambiguous, and which way it falls depends on the writing. Prose means the
    day; a finance sheet listing "jan 24, feb 24, mar 24" means the year.

    The setting only decides the ambiguous middle. A single digit is always a day (nobody
    writes a year as ``3``), an ordinal suffix is always a day (``jan 24th``), and an
    apostrophe, four digits, or anything above 31 is always a year.
    """

    DAY = "day"
    YEAR = "year"


class YearLabel(Enum):
    """Which calendar year names a fiscal year.

    END_YEAR (India/UK/Australia, and pandas): with an April start FY2024 is Apr 2023 -
    Mar 2024. START_YEAR (common in US corporate reporting): FY2024 begins in 2024.
    """

    END_YEAR = "end_year"
    START_YEAR = "start_year"


@dataclass(frozen=True, slots=True)
class FiscalCalendar:
    """The fiscal year's shape: when it starts, and which year names it."""

    start_month: int = 4
    label_by: YearLabel = YearLabel.END_YEAR

    def __post_init__(self) -> None:
        if not isinstance(self.start_month, int) or isinstance(self.start_month, bool):
            raise TypeError(
                f"FiscalCalendar.start_month must be an int, got {type(self.start_month).__name__}"
            )
        if not 1 <= self.start_month <= 12:
            raise ValueError(
                f"FiscalCalendar.start_month must be 1-12, got {self.start_month}"
            )
        if not isinstance(self.label_by, YearLabel):
            raise TypeError("FiscalCalendar.label_by must be a YearLabel")

    @property
    def is_calendar_aligned(self) -> bool:
        """True when the fiscal year is the calendar year (January start)."""
        return self.start_month == 1

    @property
    def end_month(self) -> int:
        return 12 if self.start_month == 1 else self.start_month - 1

    # Presets, because "which month does the US federal year start" is the kind of
    # thing people look up once and misremember afterwards.

    @staticmethod
    def india() -> FiscalCalendar:
        return FiscalCalendar(4, YearLabel.END_YEAR)

    @staticmethod
    def uk() -> FiscalCalendar:
        return FiscalCalendar(4, YearLabel.END_YEAR)

    @staticmethod
    def australia() -> FiscalCalendar:
        return FiscalCalendar(7, YearLabel.END_YEAR)

    @staticmethod
    def us_federal() -> FiscalCalendar:
        return FiscalCalendar(10, YearLabel.END_YEAR)

    @staticmethod
    def calendar() -> FiscalCalendar:
        return FiscalCalendar(1, YearLabel.END_YEAR)

    def __str__(self) -> str:
        import calendar as _cal

        return f"FY starts {_cal.month_abbr[self.start_month]}, labelled by {self.label_by.value}"


@dataclass(frozen=True, slots=True)
class WranglerConfig:
    """Everything the wrangler treats as policy."""

    fiscal: FiscalCalendar = field(default_factory=FiscalCalendar)

    #: What a bare "Q1"/"H1" means when no year and no fy/cy marker is present.
    bare_period_basis: Basis = Basis.FISCAL

    #: What "Q1 of 2024" means. None inherits ``bare_period_basis``, so that phrasing and
    #: "Q1 2024" agree -- they should never land a year apart.
    of_year_basis: Basis | None = None  # None => inherit

    #: Reading of all-numeric dates. See :class:`DateOrder`.
    date_order: DateOrder = DateOrder.DMY

    #: What "jan 24" means. See :class:`MonthNumber`.
    month_number: MonthNumber = MonthNumber.DAY

    #: Two-digit years at or below this map to 20xx, above it to 19xx. POSIX default,
    #: so "99" is 1999.
    two_digit_pivot: int = 68

    #: How eagerly to claim bare month names in running prose. "strict" requires a year
    #: or an explicit period marker; "greedy" matches any month name anywhere.
    strictness: str = "balanced"

    def __post_init__(self) -> None:
        if not isinstance(self.fiscal, FiscalCalendar):
            raise TypeError("WranglerConfig.fiscal must be a FiscalCalendar")
        if not isinstance(self.date_order, DateOrder):
            raise TypeError("WranglerConfig.date_order must be a DateOrder")
        if not isinstance(self.month_number, MonthNumber):
            raise TypeError("WranglerConfig.month_number must be a MonthNumber")
        if not 0 <= self.two_digit_pivot <= 99:
            raise ValueError(
                f"WranglerConfig.two_digit_pivot must be 0-99, got {self.two_digit_pivot}"
            )
        if self.strictness not in ("strict", "balanced", "greedy"):
            raise ValueError(
                f"WranglerConfig.strictness must be strict/balanced/greedy, got {self.strictness!r}"
            )

    @property
    def effective_of_year_basis(self) -> Basis:
        return self.of_year_basis if self.of_year_basis is not None else self.bare_period_basis

    def with_(self, **changes: Any) -> WranglerConfig:
        """A copy with fields replaced, validated the same way."""
        return replace(self, **changes)


DEFAULT_CONFIG = WranglerConfig()
