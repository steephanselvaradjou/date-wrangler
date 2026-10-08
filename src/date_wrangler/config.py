"""Everything the wrangler treats as policy rather than fact.

Passed per call, never read from a module global, so one process can serve tenants on
different fiscal calendars. Frozen and validated on construction, so a bad fiscal month
names the field it came from instead of failing later inside ``date()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum
from typing import Any

from .types import Anchor, Basis

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

    #: The default for ``year_basis`` when that is not set. Fiscal, so out of the box every
    #: year and period that does not say otherwise is read on the fiscal calendar. Kept
    #: under its old name so configurations written for earlier versions mean the same.
    bare_period_basis: Basis = Basis.FISCAL

    #: An optional override for a period "of" a numbered year -- "Q1 of 2024", "the second
    #: half of 2024", "early 2024". None, the default, inherits ``year_basis``, which is
    #: almost always what you want; set it only to read those one way and the rest another.
    of_year_basis: Basis | None = None  # None => inherit

    #: **The** basis setting. Every phrase that does not say fiscal or calendar is read on
    #: it: "2024", "the year 2024", "Q4 2024", "Q3'24", "Q1", "H1", "last quarter", "QTD",
    #: "this year", "YTD", "the second half of 2024", "the last quarter of 2024".
    #:
    #: Only a phrase that names its basis is exempt. "FY2024", "FY24 Q3", "fiscal Q3" and
    #: "this fiscal year" are fiscal; "CY2024", "CY Q3" and "this calendar year" are
    #: calendar -- whatever this is set to. None inherits ``bare_period_basis``, which is
    #: fiscal; for general prose rather than finance, "calendar" is usually what you want.
    year_basis: Basis | None = None  # None => inherit

    #: Where a relative period's edges fall when the phrasing does not say. See
    #: :class:`~date_wrangler.types.Anchor`. Phrasings that are explicit about it -- "last
    #: 30 days", "rolling 4 weeks" -- override this.
    anchor: Anchor = Anchor.ANCHORED

    #: Reading of all-numeric dates. See :class:`DateOrder`.
    date_order: DateOrder = DateOrder.DMY

    #: What "jan 24" means. See :class:`MonthNumber`.
    month_number: MonthNumber = MonthNumber.DAY

    #: Two-digit years at or below this map to 20xx, above it to 19xx. POSIX default,
    #: so "99" is 1999.
    two_digit_pivot: int = 68

    #: Which day a week begins on, 0=Monday through 6=Sunday. Monday is the ISO default
    #: and most of the world; set 6 for a US workspace, where "last week" means Sunday to
    #: Saturday and the Monday reading is a day out at both ends.
    week_starts_on: int = 0

    #: Non-working weekdays for business-day phrases, 0=Monday. Saturday and Sunday by
    #: default; (4, 5) for a Friday-Saturday weekend.
    weekend: tuple[int, ...] = (5, 6)

    #: Dates that are not working days, for "5 business days ago" and friends. Supplied,
    #: never guessed: which days are holidays depends on a country, a region, an industry
    #: and sometimes a single company, so a built-in list would be wrong somewhere and
    #: confident everywhere. Empty by default, which counts weekends only.
    holidays: frozenset[date] = frozenset()

    #: How eagerly to claim bare month names in running prose. "strict" requires a year
    #: or an explicit period marker; "greedy" matches any month name anywhere.
    strictness: str = "balanced"

    def __post_init__(self) -> None:
        if not isinstance(self.fiscal, FiscalCalendar):
            raise TypeError("WranglerConfig.fiscal must be a FiscalCalendar")
        if not isinstance(self.date_order, DateOrder):
            raise TypeError("WranglerConfig.date_order must be a DateOrder")
        if not isinstance(self.anchor, Anchor):
            raise TypeError("WranglerConfig.anchor must be an Anchor")
        for name in ("bare_period_basis", "of_year_basis", "year_basis"):
            value = getattr(self, name)
            if value is None or isinstance(value, Basis):
                continue
            # A setting like this usually arrives from a form or a select as the string
            # "calendar" or "fiscal". Accept exactly those; anything else fails here, by
            # name, instead of on the first request that happens to mention a year.
            try:
                object.__setattr__(self, name, Basis(str(value).strip().lower()))
            except ValueError:
                raise ValueError(
                    f"WranglerConfig.{name} must be 'calendar' or 'fiscal', got {value!r}"
                ) from None
        if self.bare_period_basis is None:
            raise ValueError("WranglerConfig.bare_period_basis cannot be None")
        if not isinstance(self.month_number, MonthNumber):
            raise TypeError("WranglerConfig.month_number must be a MonthNumber")
        if not isinstance(self.week_starts_on, int) or not 0 <= self.week_starts_on <= 6:
            raise ValueError(
                "WranglerConfig.week_starts_on must be 0-6 (0=Monday), got "
                f"{self.week_starts_on!r}"
            )
        if not all(isinstance(d, int) and 0 <= d <= 6 for d in self.weekend):
            raise ValueError(f"WranglerConfig.weekend must be weekdays 0-6, got {self.weekend!r}")
        if len(set(self.weekend)) >= 7:
            raise ValueError("WranglerConfig.weekend must leave at least one working day")
        if not isinstance(self.holidays, frozenset):
            # A list or set would make the config unhashable, or mutable after the fact.
            object.__setattr__(self, "holidays", frozenset(self.holidays))
        if not all(isinstance(d, date) for d in self.holidays):
            raise TypeError("WranglerConfig.holidays must hold datetime.date values")
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
        return self.of_year_basis if self.of_year_basis is not None else self.effective_year_basis

    @property
    def effective_year_basis(self) -> Basis:
        return self.year_basis if self.year_basis is not None else self.bare_period_basis

    def with_(self, **changes: Any) -> WranglerConfig:
        """A copy with fields replaced, validated the same way."""
        return replace(self, **changes)


DEFAULT_CONFIG = WranglerConfig()
