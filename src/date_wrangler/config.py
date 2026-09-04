"""Parser configuration.

Everything the parser treats as policy rather than fact lives here, and it is passed per
call. Nothing is read from a module global: two tenants on different fiscal calendars have
to be servable from one process, which the predecessor could not do.

Config objects are frozen and validated on construction, so a bad fiscal month is a
``ValueError`` naming the field at startup, not a ``ValueError: month must be in 1..12``
from deep inside ``date()`` on the first request that happens to mention a quarter.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .types import Basis

__all__ = ["DateOrder", "YearLabel", "FiscalCalendar", "ParserConfig"]


class DateOrder(Enum):
    """How to read an all-numeric date like ``03/04/2024``.

    There is no correct default -- 3 April to most of the world, 4 March in the US -- so
    the parser refuses to guess silently and this must be a deliberate choice. Unambiguous
    forms (ISO ``2024-03-04``, or any form with a day above 12) are read correctly under
    every setting.
    """

    DMY = "DMY"
    MDY = "MDY"
    YMD = "YMD"


class YearLabel(Enum):
    """Which calendar year names a fiscal year.

    ``END_YEAR`` is the Indian/UK/Australian convention and matches pandas' anchored
    quarters: with an April start, FY2024 runs Apr 2023 - Mar 2024, and Apr-Jun is Q1.
    ``START_YEAR`` is common in US corporate reporting, where FY2024 begins in 2024.
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

    # -- presets ---------------------------------------------------------------
    # Named rather than left to the caller to derive, because "which month does the
    # US federal year start" is exactly the kind of thing people get wrong once.

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
class ParserConfig:
    """Everything the parser treats as policy."""

    fiscal: FiscalCalendar = field(default_factory=FiscalCalendar)

    #: What a bare "Q1"/"H1" means when no year and no fy/cy marker is present.
    bare_period_basis: Basis = Basis.FISCAL

    #: What "Q1 of 2024" means. INHERIT keeps it identical to ``bare_period_basis`` so
    #: that "Q1 2024" and "Q1 of 2024" agree -- the predecessor resolved those a year
    #: apart, which was its single most surprising behaviour.
    of_year_basis: Basis | None = None  # None => inherit

    #: Reading of all-numeric dates. See :class:`DateOrder`.
    date_order: DateOrder = DateOrder.DMY

    #: Two-digit years at or below this map to 20xx, above it to 19xx. The default is
    #: the POSIX pivot, so "99" is 1999 rather than 2099.
    two_digit_pivot: int = 68

    #: How eagerly to claim bare month names in running prose. "strict" requires a year
    #: or an explicit period marker; "greedy" matches any month name anywhere.
    strictness: str = "balanced"

    def __post_init__(self) -> None:
        if not isinstance(self.fiscal, FiscalCalendar):
            raise TypeError("ParserConfig.fiscal must be a FiscalCalendar")
        if not isinstance(self.date_order, DateOrder):
            raise TypeError("ParserConfig.date_order must be a DateOrder")
        if not 0 <= self.two_digit_pivot <= 99:
            raise ValueError(
                f"ParserConfig.two_digit_pivot must be 0-99, got {self.two_digit_pivot}"
            )
        if self.strictness not in ("strict", "balanced", "greedy"):
            raise ValueError(
                f"ParserConfig.strictness must be strict/balanced/greedy, got {self.strictness!r}"
            )

    @property
    def effective_of_year_basis(self) -> Basis:
        return self.of_year_basis if self.of_year_basis is not None else self.bare_period_basis

    def with_(self, **changes: Any) -> ParserConfig:
        """A copy with fields replaced, validated the same way."""
        return replace(self, **changes)


DEFAULT_CONFIG = ParserConfig()
