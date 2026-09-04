"""What a rule recognised, before any dates are computed.

Resolution happens later, in :mod:`.resolve`, once ``today`` and the config are known.
Keeping them apart is what lets a range agree on a year and a basis across both endpoints
before either is resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto

from .types import Basis, Grain, Mod

__all__ = ["Kind", "Spec"]


class Kind(Enum):
    """What sort of thing a rule recognised."""

    ABS_DAY = auto()            # 2024-03-15, 15 Jan 2024
    ABS_MONTH = auto()          # March 2024, March
    ABS_QUARTER = auto()        # Q1 2024, first quarter
    ABS_HALF = auto()           # H1 FY25
    ABS_YEAR = auto()           # FY24, CY2024, 2024
    FISCAL_MONTH = auto()       # "the third month of FY24"
    RELATIVE = auto()           # last 3 months, next 2 quarters
    AGO = auto()                # "3 months ago" -- one period, not a span of three
    THIS_PERIOD = auto()        # this month, current quarter
    DAY_KEYWORD = auto()        # today, yesterday, tomorrow
    TO_DATE = auto()            # YTD, MTD, QTD
    WEEKDAY = auto()            # last Monday, next Friday
    PERIOD_ENDING = auto()      # "quarter ending June 2024" -- a period fixed by its end


@dataclass(frozen=True, slots=True)
class Spec:
    """A recognised period, not yet resolved to dates.

    ``None`` in ``year`` or ``basis`` means "not stated": a later stage fills it in from
    the other end of a range, or from config. That distinction is load bearing.
    """

    kind: Kind
    year: int | None = None
    month: int | None = None
    day: int | None = None
    index: int | None = None      # quarter 1-4, half 1-2, fiscal month 1-12
    count: int | None = None      # "3" in "last 3 months"
    unit: Grain | None = None
    direction: int = -1           # -1 past, +1 future
    basis: Basis | None = None    # None => take it from configuration
    mod: Mod | None = None
    confidence: float = 1.0

    @property
    def has_explicit_year(self) -> bool:
        return self.year is not None

    @property
    def is_relative(self) -> bool:
        """True when the period floats with ``today`` and so cannot take a year."""
        return self.kind in (
            Kind.RELATIVE,
            Kind.AGO,
            Kind.THIS_PERIOD,
            Kind.DAY_KEYWORD,
            Kind.TO_DATE,
            Kind.WEEKDAY,
        )

    def with_(self, **changes: object) -> Spec:
        return replace(self, **changes)  # type: ignore[arg-type]
