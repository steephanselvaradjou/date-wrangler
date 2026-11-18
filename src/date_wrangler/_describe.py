"""A configuration in plain words, for a model's prompt.

Told nothing, a model reads "2024" as the calendar year and "08/04/2026" the US way. These
words tell it how this library reads dates under one configuration, and every example in
them is the library's own reading, parsed when the words are written -- so they cannot
drift from the answers the library gives.
"""

from __future__ import annotations

import calendar
from datetime import date

from .config import DEFAULT_CONFIG, DateOrder, MonthNumber, WranglerConfig, YearLabel
from .format import format_range
from .types import Anchor, Basis, DateRange
from .wrangler import parse_one

__all__ = ["describe"]

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def describe(config: WranglerConfig | None = None, *, today: date | None = None) -> str:
    """How dates are read under ``config``, in plain words for a model's prompt.

    One paragraph: what a year is and how a fiscal year is named, a period "of" a year,
    a month with no year, all-number dates, two digits after a month and two-digit years,
    weeks, "last 3 months" and "last 30 days", and business days. Each example is quoted
    with the dates this library reads it as, on ``today`` (default: the date where this
    runs) -- the same words a model would pass the tool, and the same answer it would get.

        >>> from date_wrangler import FiscalCalendar, WranglerConfig, Basis
        >>> cfg = WranglerConfig(fiscal=FiscalCalendar(4), year_basis=Basis.FISCAL)
        >>> describe(cfg, today=date(2026, 11, 17)).split(": ")[0]
        'A year here is the fiscal year, April to March, named by the year it ends in'
    """
    cfg = DEFAULT_CONFIG if config is None else config
    day = date.today() if today is None else today
    said = _Examples(cfg, day)
    parts = [
        _years(said, cfg),
        _of_a_year(said, cfg),
        _months(said, cfg, day),
        _numbers(said, cfg, day),
        _weeks(said, cfg),
        _relative(said, cfg, day),
        _business(cfg),
    ]
    return " ".join(p for p in parts if p)


class _Examples:
    """Phrases read under one configuration on one day, quoted with their reading."""

    def __init__(self, cfg: WranglerConfig, day: date) -> None:
        self._cfg, self._day = cfg, day

    def range(self, text: str) -> DateRange | None:
        m = parse_one(text, today=self._day, config=self._cfg)
        return None if m is None or m.text != text else m.range

    def __call__(self, text: str) -> str:
        """``"text" is <its reading>``, or "" when the library does not read it whole."""
        r = self.range(text)
        return "" if r is None else f'"{text}" is {format_range(r)}'


def _month_span(start_month: int) -> str:
    end = 12 if start_month == 1 else start_month - 1
    return f"{calendar.month_name[start_month]} to {calendar.month_name[end]}"


def _said(text: str, *examples: str) -> str:
    """``text`` as a sentence, with whichever ``examples`` the library read after a colon.
    An example it did not read -- a bare month under strictness="strict" -- is left out
    rather than leaving a gap in the sentence."""
    shown = [e for e in examples if e]
    return text + (": " + " and ".join(shown) if shown else "") + "."


def _years(said: _Examples, cfg: WranglerConfig) -> str:
    fiscal = cfg.fiscal
    named = "ends" if fiscal.label_by is YearLabel.END_YEAR else "starts"
    fy = said("FY2024")
    if cfg.effective_year_basis is Basis.FISCAL:
        if fiscal.start_month == 1:
            return (_said("A year here is the fiscal year, which is the calendar year",
                          said("2024"))
                    + ' "This year", "Q1" and "YTD" are periods of it.')
        bare = said("2024")
        same = f"{bare}, as {fy.split(' is ')[0]} is" if bare and fy else bare or fy
        return (
            _said(f"A year here is the fiscal year, {_month_span(fiscal.start_month)}, named "
                  f"by the year it {named} in", same)
            + _said(' So "this year", "Q1" and "YTD" are fiscal periods unless the question '
                    "says calendar", said("CY2024"))
        )
    fiscal_part = "" if fiscal.start_month == 1 else " " + _said(
        f"A fiscal year runs {_month_span(fiscal.start_month)} and is named by the year it "
        f"{named} in", fy)
    return (
        _said("A year here is the calendar year", said("2024"))
        + ' "This year", "Q1" and "YTD" are calendar periods unless the question says fiscal.'
        + fiscal_part
    )


def _of_a_year(said: _Examples, cfg: WranglerConfig) -> str:
    if cfg.effective_of_year_basis is cfg.effective_year_basis:
        return ""
    which = "calendar" if cfg.effective_of_year_basis is Basis.CALENDAR else "fiscal"
    return _said(f'A half or quarter "of" a year is the {which} one', said("H1 of 2025"))


def _months(said: _Examples, cfg: WranglerConfig, day: date) -> str:
    fiscal = cfg.effective_year_basis is Basis.FISCAL and cfg.fiscal.start_month != 1
    year = "fiscal year" if fiscal else "year"
    shown = [e for e in (said("January"), said("December")) if e]
    out = f"A month with no year is in the {year} in progress"
    out += f": on {_day(day)}, {' and '.join(shown)}." if shown else "."
    # A bound that looks back, shown on next month while it is still in the year in
    # progress -- where looking back is what moves it.
    this_year = said.range("this year")
    if day.month == 12 and day.year == date.max.year:
        return out
    after = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    if this_year is not None and this_year.end is not None and after < this_year.end:
        since = said(f"since {calendar.month_name[after.month]}")
        if since:
            out += f' "Since" looks back to the latest one begun: {since}.'
    return out


def _numbers(said: _Examples, cfg: WranglerConfig, day: date) -> str:
    y = day.year
    order = {
        DateOrder.DMY: ("day first", f"08/04/{y}"),
        DateOrder.MDY: ("month first", f"08/04/{y}"),
        DateOrder.YMD: ("year first", f"{y}/08/04"),
    }[cfg.date_order]
    out = _said(f"All-number dates are {order[0]}", said(order[1]))
    which = "a day" if cfg.month_number is MonthNumber.DAY else "a year"
    out += " " + _said(f"Two digits after a month name are {which}", said("Jan 24"))
    pivot = cfg.two_digit_pivot
    if pivot < 99:
        low, high = f"Jan '{pivot:02d}", f"Jan '{pivot + 1:02d}"
        out += " " + _said(f"A two-digit year up to '{pivot:02d} is 20xx and after it 19xx",
                           said(low), said(high))
    return out


def _weeks(said: _Examples, cfg: WranglerConfig) -> str:
    return (
        _said(f"Weeks start on {_WEEKDAYS[cfg.week_starts_on]}", said("this week"))
        + ' Week numbers ("W12") are ISO weeks. '
        + _said("A week of a month or a fiscal year counts seven days from its first day",
                said("the 2nd week of January 2026"))
    )


def _relative(said: _Examples, cfg: WranglerConfig, day: date) -> str:
    if cfg.anchor is Anchor.ANCHORED:
        three = _said('"Last 3 months" and "last quarter" are whole periods before the '
                      "current one", said("last 3 months"))
    else:
        three = _said('"Last 3 months" counts back from today', said("last 3 months"))
    return three + " " + _said("Days count back from today", said("last 30 days"))


def _business(cfg: WranglerConfig) -> str:
    days = sorted(cfg.weekend)
    weekend = " and ".join(_WEEKDAYS[d] for d in days) if days else "no weekday"
    holidays = len(cfg.holidays)
    extra = "" if not holidays else f", and {holidays} holiday{'s' if holidays > 1 else ''}"
    return f"Business days skip {weekend}{extra}."


def _day(d: date) -> str:
    return f"{_WEEKDAYS[d.weekday()]} {d.day} {calendar.month_name[d.month]} {d.year}"
