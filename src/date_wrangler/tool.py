"""The library as one tool an AI agent can call: a schema, a function, and one answer.

A model is poor at date arithmetic and good at knowing when it needs some. So it gets a
single tool, ``resolve_dates``, and everything it needs to judge the answer comes back
with it: each date's first and last day, which settings were assumed rather than given
*and whether they changed the answer*, and why a fragment was not read. All of that is
worked out here, beside the parser. The layers on top -- the definition handed to a
model, the MCP server (``date-wrangler-mcp``), the skill, the CLI's ``--json`` -- pass
arguments in and this answer out, and hold no date logic of their own.

    >>> from date_wrangler import tool
    >>> tool.definition()["name"]
    'resolve_dates'
    >>> answer = tool.call({"text": "Q3 FY24", "today": "2026-11-16"})
    >>> answer["matches"][0]["first_day"], answer["matches"][0]["last_day"]
    ('2023-10-01', '2023-12-31')
"""

from __future__ import annotations

import calendar
import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from . import __version__
from .config import (
    DEFAULT_CONFIG,
    DateOrder,
    FiscalCalendar,
    MonthNumber,
    WranglerConfig,
    YearLabel,
)
from .format import format_iso, format_range
from .normalize import normalize
from .types import Anchor, Basis, DateMatch, DateRange, Grain, Mod
from .wrangler import YEAR_GUESSED, Diagnostic, diagnose, parse

__all__ = ["NAME", "DESCRIPTION", "INPUT_SCHEMA", "OUTPUT_SCHEMA", "ToolInputError",
           "definition", "call", "resolve_dates"]

NAME = "resolve_dates"

DESCRIPTION = (
    "Resolve the dates written in a piece of text to exact first and last days. Call this "
    "whenever an answer depends on what dates a phrase means -- before filtering data, "
    "writing a SQL date condition, comparing periods or stating a date range -- instead of "
    "working the dates out yourself, and above all for relative, fiscal or business-day "
    "phrases: 'last quarter', 'Q3 FY24', 'YTD', 'since March', 'H1 2025 vs H1 2024', "
    "'w.e.f. 1 April 2024', 'the last month of the fiscal year', '5 business days ago'. "
    "Pass the text as the user wrote it: every date in it is found, in order. Pass today "
    "as the user's date when you know it, and the organisation's fiscal year, weekend and "
    "holidays when they are known. Each match has first_day and last_day (both inclusive; "
    "null for an open end), reads_as (a readable form), confidence (below 1 means part of "
    "it is uncertain; notes give the reason where one is recorded) and relative_to_today. "
    "'assumptions' lists "
    "each setting you left out that changed an answer, with what the date would be "
    "otherwise where there is one other reading to give: tell the user, or ask and call "
    "again with that setting. 'diagnostics' lists words that looked like dates but could "
    "not be read, and why."
)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

#: Plain JSON Schema, inside what strict tool use accepts: enums rather than numeric
#: bounds, ``format`` for dates, no array-length rules, ``additionalProperties`` false.
#: Mutating it changes what :func:`call` accepts; :func:`definition` hands out copies.
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "The text holding the dates, exactly as written. A whole "
                           "sentence or paragraph is fine; every date in it is found.",
        },
        "today": {
            "type": "string",
            "format": "date",
            "description": "The user's current date, YYYY-MM-DD. 'Last month', 'YTD' and "
                           "a month with no year count from it. Leave out to use the date "
                           "where the tool runs.",
        },
        "timezone": {
            "type": "string",
            "description": "IANA time zone to take today's date in when today is left "
                           "out, e.g. 'Asia/Kolkata' or 'America/New_York'.",
        },
        "fiscal_start_month": {
            "type": "integer",
            "enum": list(range(1, 13)),
            "description": "The month the fiscal year starts, 1-12: 4 for India, the UK "
                           "and Japan, 7 for Australia, 10 for the US federal government. "
                           "Default 4.",
        },
        "fiscal_year_label": {
            "type": "string",
            "enum": [y.value for y in YearLabel],
            "description": "Which calendar year names a fiscal year. end_year (default): "
                           "with an April start, FY2024 is April 2023 to March 2024. "
                           "start_year: FY2024 begins in 2024.",
        },
        "year_basis": {
            "type": "string",
            "enum": [b.value for b in (Basis.CALENDAR, Basis.FISCAL)],
            "description": "How a year or period that does not say fiscal or calendar is "
                           "read: '2024', 'Q1 2024', 'last quarter', 'YTD', 'year end'. "
                           "Default calendar. 'FY24', 'CY24', 'fiscal week 3' and 'fiscal "
                           "year end' always read as written. Week numbers ('week 3', "
                           "'W12') are ISO weeks on either basis.",
        },
        "date_order": {
            "type": "string",
            "enum": [d.value for d in DateOrder],
            "description": "How an all-number date like 03/04/2024 is read. DMY (default) "
                           "is 3 April; MDY, the US order, is 4 March.",
        },
        "month_number": {
            "type": "string",
            "enum": [n.value for n in MonthNumber],
            "description": "What two digits after a month name mean: 'jan 24'. day "
                           "(default) is 24 January; year is January 2024.",
        },
        "two_digit_pivot": {
            "type": "integer",
            "description": "Where a two-digit year changes century, 0-99: years at or below "
                           "it are 20xx, above it 19xx. With 68 (default), '24 is 2024 and "
                           "'85 is 1985.",
        },
        "week_starts_on": {
            "type": "string",
            "enum": list(_WEEKDAYS),
            "description": "The day a week starts, for 'last week', 'this week' and 'this "
                           "Friday'. Default monday; sunday in the US, saturday in parts of "
                           "the Middle East. Week numbers ('W12') are ISO weeks either way, "
                           "and a fiscal week ('fiscal week 3') or 'the 3rd week of January' "
                           "is seven days counted from the period's first day.",
        },
        "anchor": {
            "type": "string",
            "enum": [a.value for a in Anchor],
            "description": "Where 'last month' and 'last 3 quarters' fall. anchored "
                           "(default): whole calendar periods before the current one. "
                           "rolling: counted back from today.",
        },
        "weekend": {
            "type": "array",
            "items": {"type": "string", "enum": list(_WEEKDAYS)},
            "description": "The days that are not working days, for '5 business days ago' "
                           "and 'the last working day of the month'. Default saturday and "
                           "sunday; friday and saturday in much of the Middle East.",
        },
        "holidays": {
            "type": "array",
            "items": {"type": "string", "format": "date"},
            "description": "Dates that are not working days, YYYY-MM-DD, for the same "
                           "phrases. None by default: business days skip weekends only "
                           "unless these are given.",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}

#: The settings of a call, after defaults: every key of :data:`INPUT_SCHEMA` but ``text``
#: and ``today``, which have keys of their own in the answer.
_SETTING_NAMES = tuple(k for k in INPUT_SCHEMA["properties"] if k not in ("text", "today"))


class ToolInputError(ValueError):
    """Arguments the tool cannot use. The message is written for the model that sent them:
    hand it back as the tool's error result, and the model can correct the call."""


def definition() -> dict[str, Any]:
    """The tool as most model APIs take it: ``name``, ``description``, ``input_schema``.

    Claude's Messages API takes this dict as it is. MCP calls the schema ``inputSchema``
    and OpenAI-style APIs ``parameters``: pass :data:`INPUT_SCHEMA` there. A fresh copy
    each time, so adding ``strict`` or ``cache_control`` to one leaves the next alone.
    """
    return {"name": NAME, "description": DESCRIPTION, "input_schema": copy.deepcopy(INPUT_SCHEMA)}


def call(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Run the tool on arguments exactly as a model sent them.

    Checked before anything runs, because a model's arguments are not a programmer's: an
    unknown name or a month of 13 comes back as :class:`ToolInputError` naming what to
    change, never as a crash or a silently ignored setting. A JSON ``null`` counts as
    left out, and a value is matched whatever its case.
    """
    if not isinstance(arguments, Mapping):
        raise ToolInputError(
            f"the arguments must be an object, got {type(arguments).__name__}"
        )
    known = INPUT_SCHEMA["properties"]
    unknown = sorted(str(k) for k in arguments if k not in known)
    if unknown:
        raise ToolInputError(
            f"unknown argument{'s' if len(unknown) > 1 else ''} "
            f"{', '.join(map(repr, unknown))}; the arguments are {', '.join(known)}"
        )
    if arguments.get("text") is None:
        raise ToolInputError("text is required: the words that hold the dates")
    return resolve_dates(**arguments)


# ---------------------------------------------------------------------------
# Reading the arguments
# ---------------------------------------------------------------------------

_ISO_DAY = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def _whole(raw: object) -> object:
    """``raw`` as an int when it is one written another way: "7", 7.0. Else unchanged.

    ``isdecimal`` and not ``isdigit``: "²" is a digit to Python but no number to ``int``.
    """
    if isinstance(raw, str) and raw.strip().isdecimal():
        return int(raw)
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    return raw


def _iso_day(raw: object, name: str) -> date:
    """A date given as a date, or written year-month-day: "2026-11-16", or "2026-1-5".

    Read here rather than by ``date.fromisoformat``, which from Python 3.11 also takes
    "20261116" and "2026-W47-1" and on 3.10 does not: an argument that works on one Python
    and fails on another is worse than one form that works everywhere. The unpadded month
    and day are what the CLI's ``--today`` has always taken.
    """
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    written = _ISO_DAY.fullmatch(raw.strip()) if isinstance(raw, str) else None
    if written:
        try:
            return date(*(int(part) for part in written.groups()))
        except ValueError:
            raise ToolInputError(f"{name} {raw!r} is not a date in the calendar") from None
    raise ToolInputError(f"{name} must be a date written YYYY-MM-DD, got {raw!r}")


def _weekday(raw: object, name: str) -> str:
    """A day of the week: its name or first three letters, any case, or 0 (Monday)-6."""
    if isinstance(raw, Enum):
        raw = raw.value
    raw = _whole(raw)
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= 6:
        return _WEEKDAYS[raw]
    if isinstance(raw, str):
        wanted = raw.strip().lower()
        for day in _WEEKDAYS:
            if len(wanted) >= 3 and day.startswith(wanted):
                return day
    raise ToolInputError(
        f"{name} must be a day of the week, monday to sunday, got {raw!r}"
    )


def _listed(raw: object, name: str) -> list[object]:
    """A list argument, also taken as one comma-separated string ("sat,sun")."""
    if isinstance(raw, str):
        return [part for part in raw.split(",") if part.strip()]
    if isinstance(raw, (list, tuple, set, frozenset)):
        return list(raw)
    raise ToolInputError(f"{name} must be a list, got {type(raw).__name__}")


def _setting_value(name: str, raw: object) -> Any:
    """``raw`` as the setting's plain JSON value, or a ToolInputError saying what is allowed."""
    if name == "fiscal_start_month":
        month = _whole(raw.value if isinstance(raw, Enum) else raw)
        if isinstance(month, int) and not isinstance(month, bool) and 1 <= month <= 12:
            return month
        raise ToolInputError(f"fiscal_start_month must be a month number, 1-12, got {raw!r}")
    if name == "two_digit_pivot":
        pivot = _whole(raw)
        if isinstance(pivot, int) and not isinstance(pivot, bool) and 0 <= pivot <= 99:
            return pivot
        raise ToolInputError(f"two_digit_pivot must be a whole number, 0-99, got {raw!r}")
    if name == "week_starts_on":
        return _weekday(raw, name)
    if name == "weekend":
        resting = {_weekday(item, "each day of weekend") for item in _listed(raw, name)}
        if len(resting) == 7:
            raise ToolInputError("weekend must leave at least one working day")
        return [day for day in _WEEKDAYS if day in resting]
    if name == "holidays":
        dates = {_iso_day(item, "each holiday") for item in _listed(raw, name)}
        return [d.isoformat() for d in sorted(dates)]
    if isinstance(raw, Enum):
        raw = raw.value
    allowed = INPUT_SCHEMA["properties"][name]["enum"]
    if isinstance(raw, str):
        for value in allowed:
            if value.lower() == raw.strip().lower():
                return value
    raise ToolInputError(
        f"{name} must be one of {', '.join(map(repr, allowed))}, got {raw!r}"
    )


def _zone(raw: object) -> tzinfo | None:
    if raw is None or isinstance(raw, tzinfo):
        return raw
    if isinstance(raw, str) and raw.strip():
        key = raw.strip()
        try:
            return ZoneInfo(key)
        # OSError as well: on Windows a directory of the database, "America", is
        # PermissionError rather than ZoneInfoNotFoundError.
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
        near = [z for z in available_timezones() if z.lower() == key.lower()]
        if near:
            raise ToolInputError(f"timezone {raw!r} is not an IANA name; it is {near[0]!r}")
    raise ToolInputError(
        f"timezone must be an IANA name such as 'Asia/Kolkata', got {raw!r}"
    )


def _today(raw: object, zone: tzinfo | None) -> date:
    if raw is None:
        return datetime.now(zone).date() if zone is not None else date.today()
    return _iso_day(raw, "today")


# ---------------------------------------------------------------------------
# Settings, and how to tell whether leaving one out mattered
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Setting:
    """A setting tried at other values when the caller left it out."""

    name: str
    #: The values tried in its place. A match the other value reads as other days depends
    #: on the setting, and the default becomes an assumption worth reporting.
    others: tuple[Any, ...]
    #: Whether to give the dates under each other value. Not for the fiscal start month:
    #: eleven readings would bury the one fact that matters, that the answer depends on it.
    show_otherwise: bool
    #: Which matches the setting can reach; see :func:`_reach`. When none can, the text is
    #: not read again for it. None reaches every match.
    reach: str | None
    note: str
    #: Another setting whose values change what this one's other values read, tried
    #: together with them when the caller left that one out too -- for finding a
    #: dependence only, never for the readings shown -- and what to add to the note when
    #: only that finds it.
    together: tuple[str, tuple[Any, ...], str] | None = None


_DEFAULT_FISCAL_START = DEFAULT_CONFIG.fiscal.start_month

_DEFAULTS: dict[str, Any] = {
    "timezone": None,
    "fiscal_start_month": _DEFAULT_FISCAL_START,
    "fiscal_year_label": DEFAULT_CONFIG.fiscal.label_by.value,
    "year_basis": DEFAULT_CONFIG.effective_year_basis.value,
    "date_order": DEFAULT_CONFIG.date_order.value,
    "month_number": DEFAULT_CONFIG.month_number.value,
    "two_digit_pivot": DEFAULT_CONFIG.two_digit_pivot,
    "week_starts_on": _WEEKDAYS[DEFAULT_CONFIG.week_starts_on],
    "anchor": DEFAULT_CONFIG.anchor.value,
    "weekend": [_WEEKDAYS[d] for d in sorted(DEFAULT_CONFIG.weekend)],
    "holidays": sorted(d.isoformat() for d in DEFAULT_CONFIG.holidays),
}

_SETTINGS = (
    # The fiscal settings reach every match. Whether the fiscal calendar went into a date
    # cannot be read off the answer: "15-20 June FY25" is calendar days, on calendar basis,
    # in a year the fiscal calendar chose. Every other start month is tried, because one
    # can miss -- "October of this fiscal year" moves only for a November start.
    _Setting(
        "fiscal_start_month",
        tuple(m for m in range(1, 13) if m != _DEFAULT_FISCAL_START),
        False, None,
        f"The fiscal year is taken to start in {calendar.month_name[_DEFAULT_FISCAL_START]}. "
        "Pass fiscal_start_month if the organisation's starts in another month.",
    ),
    _Setting(
        "fiscal_year_label", (YearLabel.START_YEAR.value,), True, None,
        "A fiscal year is taken to be named by the calendar year it ends in. Pass "
        "fiscal_year_label 'start_year' if it is named by the year it starts in.",
    ),
    # Tried with the fiscal year starting in every month when that is left out too: "last
    # quarter" reads the same on a fiscal year starting in April as on the calendar, and
    # three months later on one starting in February.
    _Setting(
        "year_basis", (Basis.FISCAL.value,), True, None,
        "A year or period that does not say fiscal or calendar is read on the calendar "
        "year. Pass year_basis 'fiscal' if it means the fiscal year.",
        together=(
            "fiscal_start_month", tuple(range(1, 13)),
            " Read on the fiscal year, it would also depend on the month that year starts "
            "in: pass fiscal_start_month as well.",
        ),
    ),
    _Setting(
        "date_order", (DateOrder.MDY.value, DateOrder.YMD.value), True, "numeric",
        "All-number dates are read day first (DMY). Pass date_order 'MDY' if they are "
        "written month first, as in the US, or 'YMD' if year first.",
    ),
    _Setting(
        "month_number", (MonthNumber.YEAR.value,), True, "digit",
        "Two digits after a month name are read as a day: 'jan 24' is 24 January. Pass "
        "month_number 'year' if they are a year.",
    ),
    # The other convention in wide use, not the far ends of the range: Windows and Excel
    # read two-digit years as 1930-2029, where this library reads 1969-2068 (POSIX). The
    # two agree on '00-'29 and '69-'99, so only '30-'68 is reported -- where the century
    # is genuinely in doubt -- and "FY24" is not offered as 1924.
    _Setting(
        "two_digit_pivot",
        tuple(p for p in (29,) if p != DEFAULT_CONFIG.two_digit_pivot),
        True, "digit",
        f"Two-digit years are read as {1901 + DEFAULT_CONFIG.two_digit_pivot} to "
        f"{2000 + DEFAULT_CONFIG.two_digit_pivot}; Windows and Excel read them as 1930 to "
        "2029. Pass two_digit_pivot to choose, or write the year in full.",
    ),
    # The days a week starts on somewhere: Monday (ISO, most of the world), Sunday (the
    # US, Canada, Japan) and Saturday (parts of the Middle East). No country starts it on
    # another day. Both are tried, because "Saturday last week" moves only for the last.
    _Setting(
        "week_starts_on", ("sunday", "saturday"), True, None,
        "Weeks are taken to start on Monday. Pass week_starts_on if they start on another "
        "day.",
    ),
    _Setting(
        "anchor", (Anchor.ROLLING.value,), True, "relative",
        "'Last month' and the like are read as whole calendar periods before the current "
        "one. Pass anchor 'rolling' to count back from today instead.",
    ),
)

#: The weekend tried in place of Saturday and Sunday, for the reading under it: Friday
#: and Saturday is the other weekend much of the world keeps.
_OTHER_WEEKEND = ["friday", "saturday"]

_WEEKEND_NOTE = (
    "Business days are counted with Saturday and Sunday as the weekend. Pass weekend if "
    "the organisation's falls on other days."
)
_HOLIDAYS_NOTE = (
    "Business days are counted with no holidays. Pass holidays, the organisation's "
    "non-working dates, to skip them."
)

#: How far today is moved to see which dates count from it. Over a year, so that a week,
#: a month, a quarter and a year all change, and a year with no year written lands on
#: another one.
_LATER = timedelta(days=400)

#: An all-number date has digits either side of a dash, slash or dot -- once normalised,
#: as the parser reads it, which folds en dashes, minus signs and fullwidth slashes to
#: these. Digits apart by spaces alone are never read as a date ("03 04 2024" is not one),
#: so words without this cannot depend on the date order.
_NUMERIC_DATE = re.compile(r"\d\s*[-/.]\s*\d")


def _config(chosen: Mapping[str, Any], strictness: str) -> WranglerConfig:
    return WranglerConfig(
        fiscal=FiscalCalendar(chosen["fiscal_start_month"], YearLabel(chosen["fiscal_year_label"])),
        year_basis=Basis(chosen["year_basis"]),
        date_order=DateOrder(chosen["date_order"]),
        month_number=MonthNumber(chosen["month_number"]),
        two_digit_pivot=chosen["two_digit_pivot"],
        week_starts_on=_WEEKDAYS.index(chosen["week_starts_on"]),
        anchor=Anchor(chosen["anchor"]),
        weekend=tuple(_WEEKDAYS.index(d) for d in chosen["weekend"]),
        holidays=frozenset(date.fromisoformat(d) for d in chosen["holidays"]),
        strictness=strictness,
    )


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def resolve_dates(
    text: str,
    *,
    today: date | datetime | str | None = None,
    timezone: tzinfo | str | None = None,
    fiscal_start_month: int | str | None = None,
    fiscal_year_label: YearLabel | str | None = None,
    year_basis: Basis | str | None = None,
    date_order: DateOrder | str | None = None,
    month_number: MonthNumber | str | None = None,
    two_digit_pivot: int | str | None = None,
    week_starts_on: int | str | None = None,
    anchor: Anchor | str | None = None,
    weekend: list[str] | tuple[str, ...] | str | None = None,
    holidays: list[str | date] | tuple[str | date, ...] | str | None = None,
    strictness: str = "balanced",
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    """Every date in ``text``, resolved, with what an agent needs to judge each one.

    A setting left as None is *assumed*: the library default is used, and the text is read
    again with the setting at its other values. If that reads a match as other days, the
    default changed the answer, and ``assumptions`` says so, with what the dates would be
    otherwise. A default that changed nothing is not reported, so an empty list means no
    setting the caller left out made a difference. Business days are found by reading the
    text with a weekend no real calendar has, and today by reading it a year on.

    That makes a call several reads of the text where :func:`parse` is one -- milliseconds,
    nothing beside a model's turn, but :func:`parse` is the one for bulk work.
    ``strictness`` and ``min_confidence`` are for Python and the CLI, not the tool schema.

    Returns a JSON-ready dict matching :data:`OUTPUT_SCHEMA`, each match carrying
    ``first_day`` and ``last_day`` (inclusive), ``reads_as``, ``confidence``, ``notes`` and
    ``relative_to_today`` alongside everything :meth:`DateMatch.to_dict` gives.
    """
    if not isinstance(text, str):
        raise ToolInputError(f"text must be a string, got {type(text).__name__}")
    if strictness not in ("strict", "balanced", "greedy"):
        raise ToolInputError(
            f"strictness must be 'strict', 'balanced' or 'greedy', got {strictness!r}"
        )
    zone = _zone(timezone)
    day = _today(today, zone)
    raw = {
        "fiscal_start_month": fiscal_start_month,
        "fiscal_year_label": fiscal_year_label,
        "year_basis": year_basis,
        "date_order": date_order,
        "month_number": month_number,
        "two_digit_pivot": two_digit_pivot,
        "week_starts_on": week_starts_on,
        "anchor": anchor,
        "weekend": weekend,
        "holidays": holidays,
    }
    given = {name: _setting_value(name, value) for name, value in raw.items() if value is not None}
    chosen = {name: given.get(name, _DEFAULTS[name]) for name in raw}
    cfg = _config(chosen, strictness)

    found, diags = diagnose(text, today=day, config=cfg)
    found = [m for m in found if m.confidence >= min_confidence]
    relative = _relative(text, day, cfg, found)

    assumptions: list[dict[str, Any]] = []
    if today is None and any(relative):
        clock = "the clock where this runs" if zone is None else f"the clock in {zone}"
        assumptions.append({
            "setting": "today",
            "assumed": day.isoformat(),
            "note": f"Relative dates count from today, taken as {day.isoformat()} from "
                    f"{clock}. Pass today as the user's date to be sure.",
            "matches": [_where(m) for m, moves in zip(found, relative, strict=True) if moves],
        })
    read = _Reads(text, day, strictness)
    for setting in _SETTINGS:
        if setting.name in given:
            continue
        reached = _reach(setting.reach, found, relative)
        if not reached:
            continue
        changed, only_together = _changed_by(setting, read, chosen, found, reached, set(given))
        if changed:
            together_note = setting.together[2] if setting.together and only_together else ""
            assumptions.append({
                "setting": setting.name,
                "assumed": chosen[setting.name],
                "note": setting.note + together_note,
                "matches": changed,
            })
    if "weekend" not in given or "holidays" not in given:
        business = _business(read, chosen, found)
        if business and "weekend" not in given:
            assumptions.append({
                "setting": "weekend",
                "assumed": chosen["weekend"],
                "note": _WEEKEND_NOTE,
                "matches": _otherwise_for(business, "weekend", [_OTHER_WEEKEND], read, chosen),
            })
        if business and "holidays" not in given:
            assumptions.append({
                "setting": "holidays",
                "assumed": chosen["holidays"],
                "note": _HOLIDAYS_NOTE,
                "matches": [_where(m) for m in business],
            })

    return {
        "text": text,
        "today": day.isoformat(),
        "settings": {**chosen, "timezone": None if zone is None else str(zone)},
        "matches": [_match(m, diags, moves) for m, moves in zip(found, relative, strict=True)],
        "assumptions": assumptions,
        "diagnostics": [_diagnostic(d) for d in diags],
        "version": __version__,
    }


def _readings(text: str, day: date, cfg: WranglerConfig) -> dict[tuple[int, int], DateRange]:
    return {m.span: m.range for m in parse(text, today=day, config=cfg)}


class _Reads:
    """The text read under each set of settings one call tries, once each.

    Two questions can need the same reading: the start month is tried at every month on
    its own, and year_basis tries each month again as its baseline. Kept for the one call
    and dropped with it -- nothing is remembered between calls.
    """

    def __init__(self, text: str, day: date, strictness: str) -> None:
        self._text, self._day, self._strictness = text, day, strictness
        self._seen: dict[tuple[tuple[str, Any], ...], dict[tuple[int, int], DateRange]] = {}

    def __call__(self, chosen: Mapping[str, Any]) -> dict[tuple[int, int], DateRange]:
        key = tuple(
            (name, tuple(value) if isinstance(value, list) else value)
            for name, value in sorted(chosen.items())
        )
        if key not in self._seen:
            cfg = _config(chosen, self._strictness)
            self._seen[key] = _readings(self._text, self._day, cfg)
        return self._seen[key]


def _moved(m: DateMatch, other: Mapping[tuple[int, int], DateRange]) -> DateRange | None:
    """The other reading of ``m``'s words, when it falls on other days.

    Matched by span, so only the same words read another way count. Words that are no
    date at all under the other setting -- "13/04/2024" read month first -- have only the
    one reading, and a difference in grain or basis that lands on the same days is no
    difference to anyone filtering by date.
    """
    o = other.get(m.span)
    if o is None or (o.start, o.end) == (m.range.start, m.range.end):
        return None
    return o


def _relative(text: str, day: date, cfg: WranglerConfig, found: list[DateMatch]) -> list[bool]:
    """Which matches count from today: those the same words read otherwise a year on.

    Words that are no longer read as a date at all on the other day count too: the only
    thing that changed was today, so their reading depended on it.
    """
    if not found:
        return []
    try:
        later = day + _LATER
    except OverflowError:  # today near 9999-12-31: look a year back instead
        later = day - _LATER
    other = _readings(text, later, cfg)
    return [m.span not in other or _moved(m, other) is not None for m in found]


def _reach(reach: str | None, found: list[DateMatch], relative: list[bool]) -> list[DateMatch]:
    """The matches a setting can change.

    It only skips work: when it is empty the setting is not tried, and when it is not, the
    setting is tried against every match. Each rule is a fact about the parser, checked by
    a test against trying every setting on every match.

    - "relative": anchoring applies only to periods counted from today.
    - "numeric": the date order applies only to all-number dates.
    - "digit": "jan 24" means the day or the year only where there are digits.
    """
    if reach is None:
        return found
    if reach == "relative":
        return [m for m, moves in zip(found, relative, strict=True) if moves]
    if reach == "numeric":
        return [m for m in found if _NUMERIC_DATE.search(normalize(m.text).text)]
    if reach == "digit":
        return [m for m in found if any(ch.isdigit() for ch in m.text)]
    raise AssertionError(reach)


def _changed_by(
    setting: _Setting,
    read: _Reads,
    chosen: Mapping[str, Any],
    found: list[DateMatch],
    reached: list[DateMatch],
    given: set[str],
) -> tuple[list[dict[str, Any]], bool]:
    """The matches whose days depend on ``setting``, each with its other readings, and
    whether any of them was found only by trying ``setting.together``.

    Each other reading changes that one setting and keeps the rest as they were: the
    fiscal reading of "Q1 2024" is on the fiscal year as configured, April by default.
    Where the reading under that one change is the same -- "last quarter", whose fiscal
    quarters on an April year are the calendar's -- the setting named in ``together`` is
    tried with it too, if the caller left that out as well: on a February year "last
    quarter" is three other months. Such a match is listed with no other reading, since
    its reading depends on two settings, and the note says so.

    A setting whose readings are not shown stops as soon as every match it can reach is
    known to depend on it: "Q3 FY24" moves with the first other month tried, and nothing
    the remaining ten could show would change what is reported. Trying ``together`` stops
    the same way.
    """
    otherwise: dict[tuple[int, int], list[dict[str, Any]]] = {}
    pending = {m.span for m in reached}
    for value in setting.others:
        other = read({**chosen, setting.name: value})
        for m in found:
            o = _moved(m, other)
            if o is not None:
                otherwise.setdefault(m.span, []).append({"value": value, **_days(o)})
                pending.discard(m.span)
        if not setting.show_otherwise and not pending:
            break
    together: set[tuple[int, int]] = set()
    if setting.together is not None and setting.together[0] not in given and pending:
        name, values, _ = setting.together
        for other_value in values:
            if other_value == chosen[name]:
                continue  # tried above
            # Against this setting as chosen *at the same other value*, not against the
            # answer: "FY24 Q1" moves with the start month alone, and that is the start
            # month's to report, not this setting's. Read only once a difference shows.
            for value in setting.others:
                other = read({**chosen, setting.name: value, name: other_value})
                moved = [m for m in found if m.span in pending and _moved(m, other) is not None]
                base = read({**chosen, name: other_value}) if moved else {}
                for m in moved:
                    o, b = other[m.span], base.get(m.span)
                    if b is not None and (o.start, o.end) != (b.start, b.end):
                        together.add(m.span)
                        pending.discard(m.span)
            if not pending:
                break
    out = []
    for m in found:
        if m.span in otherwise or m.span in together:
            entry = _where(m)
            if setting.show_otherwise and m.span in otherwise:
                entry["otherwise"] = otherwise[m.span]
            out.append(entry)
    return out, bool(together)


def _business(
    read: _Reads, chosen: Mapping[str, Any], found: list[DateMatch]
) -> list[DateMatch]:
    """The matches counted in working days, found by changing the weekend to an odd one.

    Under a weekend of every day but one that the real calendar does not work, every date
    picked by counting or choosing working days lands on that one day -- where the real
    answer never can. So a business-day match always moves, and nothing else is touched by
    the weekend at all. With no weekend there is no such day, so two are tried: an answer
    may land on a Sunday or on a Wednesday, but not on both.
    """
    if not found:
        return []
    resting = chosen["weekend"][:1] or ["sunday", "wednesday"]
    hits: set[tuple[int, int]] = set()
    for only_working in resting:
        other = read({**chosen, "weekend": [d for d in _WEEKDAYS if d != only_working]})
        hits.update(m.span for m in found if m.span not in other or _moved(m, other))
    return [m for m in found if m.span in hits]


def _otherwise_for(
    matches: list[DateMatch],
    name: str,
    values: list[Any],
    read: _Reads,
    chosen: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """``matches``, each with its reading under each of ``values`` that falls on other days."""
    readings = [(value, read({**chosen, name: value})) for value in values]
    out = []
    for m in matches:
        entry = _where(m)
        others = [
            {"value": value, **_days(o)}
            for value, other in readings
            if (o := _moved(m, other)) is not None
        ]
        if others:
            entry["otherwise"] = others
        out.append(entry)
    return out


def _where(m: DateMatch) -> dict[str, Any]:
    return {"text": m.text, "span": list(m.span)}


def _days(r: DateRange) -> dict[str, Any]:
    last = r.end_inclusive
    return {
        "reads_as": format_range(r),
        "first_day": None if r.start is None else r.start.isoformat(),
        "last_day": None if last is None else last.isoformat(),
    }


def _match(m: DateMatch, diags: list[Diagnostic], relative: bool) -> dict[str, Any]:
    """A match for a reader that is a model: the inclusive days first, then the rest.

    ``range.end`` stays exclusive, as everywhere in this library, for code that queries
    with it; ``first_day`` and ``last_day`` are what a model should quote, because an
    exclusive end read as the last day is an off-by-one nobody notices.

    ``notes`` are the diagnostics about this match's own words, and the unwritten year.
    That is known from the confidence alone, which nothing but an unwritten year sets to
    YEAR_GUESSED; a doubt that lowers it further brings its own diagnostic, and the year
    still shows in ``relative_to_today``. The parser lowers confidence in a few other
    places without recording why -- an all-number date that reads either way (0.9), a bare
    year, "the 15th" or "'24" (0.8), "this morning" (0.5) -- so those have no note; where
    the doubt is a setting left to its default, ``assumptions`` gives the other reading.
    """
    lo, hi = m.span
    notes = [d.reason for d in diags if lo <= d.span[0] and d.span[1] <= hi]
    if m.confidence == YEAR_GUESSED:
        notes.append("The year is not written, so it is taken from today.")
    return {
        "text": m.text,
        "span": list(m.span),
        **_days(m.range),
        "confidence": m.confidence,
        "notes": notes,
        "relative_to_today": relative,
        **m.to_dict(),
        "iso": format_iso(m.range),
    }


def _diagnostic(d: Diagnostic) -> dict[str, Any]:
    return {"text": d.text, "span": list(d.span), "rule": d.rule, "reason": d.reason}


# ---------------------------------------------------------------------------
# The answer's shape
# ---------------------------------------------------------------------------

_DAY_OR_NULL = {"type": ["string", "null"], "format": "date"}
_SPAN = {"type": "array", "items": {"type": "integer"}}

_RANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "start": _DAY_OR_NULL,
        "end": _DAY_OR_NULL,
        "end_inclusive": _DAY_OR_NULL,
        "days": {"type": ["integer", "null"]},
        "grain": {"type": "string", "enum": [g.value for g in Grain]},
        "basis": {"type": "string", "enum": [b.value for b in Basis]},
        "mod": {"type": ["string", "null"], "enum": [*(m.value for m in Mod), None]},
        "anchor": {"type": "string", "enum": [a.value for a in Anchor]},
    },
    "required": ["start", "end", "end_inclusive", "days", "grain", "basis", "mod", "anchor"],
}

_READING_SCHEMA = {
    "type": "object",
    "properties": {
        "value": {},
        "reads_as": {"type": "string"},
        "first_day": _DAY_OR_NULL,
        "last_day": _DAY_OR_NULL,
    },
    "required": ["value", "reads_as", "first_day", "last_day"],
}

#: What :func:`resolve_dates` returns, as JSON Schema -- the MCP server declares it as the
#: tool's output schema. Open to keys added later; each key it names is always present.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "today": {"type": "string", "format": "date"},
        "settings": {
            "type": "object",
            "properties": {
                **{
                    name: copy.deepcopy(INPUT_SCHEMA["properties"][name])
                    for name in _SETTING_NAMES
                },
                "timezone": {"type": ["string", "null"]},
            },
            "required": list(_SETTING_NAMES),
        },
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "span": _SPAN,
                    "reads_as": {"type": "string"},
                    "first_day": _DAY_OR_NULL,
                    "last_day": _DAY_OR_NULL,
                    "confidence": {"type": "number"},
                    "notes": {"type": "array", "items": {"type": "string"}},
                    "relative_to_today": {"type": "boolean"},
                    "range": _RANGE_SCHEMA,
                    "group": {"type": "integer"},
                    "joined_by": {"type": ["string", "null"],
                                  "enum": ["list", "compare", "range", None]},
                    "endpoints": {"type": ["array", "null"], "items": _RANGE_SCHEMA},
                    "iso": {"type": "string"},
                },
                "required": ["text", "span", "reads_as", "first_day", "last_day", "confidence",
                             "notes", "relative_to_today", "range", "group", "joined_by",
                             "endpoints", "iso"],
            },
        },
        "assumptions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "setting": {"type": "string", "enum": ["today", *_SETTING_NAMES]},
                    "assumed": {},
                    "note": {"type": "string"},
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "span": _SPAN,
                                "otherwise": {"type": "array", "items": _READING_SCHEMA},
                            },
                            "required": ["text", "span"],
                        },
                    },
                },
                "required": ["setting", "assumed", "note", "matches"],
            },
        },
        "diagnostics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "span": _SPAN,
                    "rule": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["text", "span", "rule", "reason"],
            },
        },
        "version": {"type": "string"},
    },
    "required": ["text", "today", "settings", "matches", "assumptions", "diagnostics",
                 "version"],
}
