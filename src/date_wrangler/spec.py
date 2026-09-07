"""What a rule recognised, before any dates are computed.

Resolution happens later, in :mod:`.resolve`, once ``today`` and the config are known.
Keeping them apart is what lets a range agree on a year and a basis across both endpoints
before either is resolved.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto

from .types import Anchor, Basis, Grain, Mod

__all__ = ["Kind", "Part", "Spec"]


class Part(Enum):
    """A slice of a named period: "first half of March", "late 2024".

    Thirds for early/mid/late, halves for first/second half. Both are conventions rather
    than facts, so they are named explicitly here instead of being buried in resolve().
    """

    EARLY = auto()        # first third
    MID = auto()          # middle third
    LATE = auto()         # last third
    FIRST_HALF = auto()
    SECOND_HALF = auto()


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
    #: Whole years to shift the resolved period by: "Q1 last year" is -1. Kept apart from
    #: ``year`` because a fiscal quarter's year is a *label*, not a number to subtract from.
    year_offset: int | None = None
    #: A slice of the period rather than all of it: "first half of March".
    part: Part | None = None
    #: A single day inside the period: "1st of next month".
    day_of_period: int | None = None
    #: None => take it from configuration. Only relative periods can roll.
    anchor: Anchor | None = None

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
