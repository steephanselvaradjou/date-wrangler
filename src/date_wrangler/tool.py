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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from .types import Anchor, Basis, DateMatch, DateRange
from .wrangler import YEAR_GUESSED, Diagnostic, diagnose, parse

__all__ = ["NAME", "DESCRIPTION", "INPUT_SCHEMA", "ToolInputError", "definition", "call",
           "resolve_dates"]

NAME = "resolve_dates"

DESCRIPTION = (
    "Resolve the dates written in a piece of text to exact first and last days. Call this "
    "whenever an answer depends on what dates a phrase means -- before filtering data, "
    "writing a SQL date condition, comparing periods or stating a date range -- instead of "
    "working the dates out yourself, and above all for relative or fiscal phrases: 'last "
    "quarter', 'Q3 FY24', 'YTD', 'since March', 'H1 2025 vs H1 2024', 'w.e.f. 1 April "
    "2024', 'the last month of the fiscal year'. Pass the text as the user wrote it: every "
    "date in it is found, in order. Pass today as the user's date when you know it, and "
    "the fiscal-year settings when the organisation's are known. Each match has first_day "
    "and last_day (both inclusive; null for an open end), reads_as (a readable form), "
    "confidence (below 1 means part of it is a guess, and notes say which part) and "
    "relative_to_today. 'assumptions' lists each setting you left out that changed an "
    "answer, with what the date would be otherwise: tell the user, or ask and call again "
    "with that setting. 'diagnostics' lists words that looked like dates but could not be "
    "read, and why."
)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

#: Plain JSON Schema, inside what strict tool use accepts: enums rather than numeric
#: bounds, ``format`` for the date, ``additionalProperties`` false. Mutating it changes
#: what :func:`call` accepts; :func:`definition` hands out copies.
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
                           "read: '2024', 'Q1 2024', 'last quarter', 'YTD'. Default "
                           "calendar. 'FY24' and 'CY24' always read as written.",
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
        "week_starts_on": {
            "type": "string",
            "enum": list(_WEEKDAYS),
            "description": "The day a week starts, for 'last week' and week numbers. "
                           "Default monday.",
        },
        "anchor": {
            "type": "string",
            "enum": [a.value for a in Anchor],
            "description": "Where 'last month' and 'last 3 quarters' fall. anchored "
                           "(default): whole calendar periods before the current one. "
                           "rolling: counted back from today.",
        },
    },
    "required": ["text"],
    "additionalProperties": False,
}


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
    left out, and an enum value matches whatever its case.
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
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Setting:
    """One setting a caller may leave out, and how to tell whether leaving it out mattered."""

    name: str
    values: tuple[Any, ...]
    default: Any
    #: The values tried in its place when it was left out. Each that reads a match as other
    #: dates makes the default an assumption worth reporting.
    others: tuple[Any, ...]
    #: Whether to report what the dates would be under the other value. Not for the fiscal
    #: start month: any other month moves every fiscal date, and naming one would read as
    #: a suggestion.
    show_otherwise: bool
    #: What a match must have for the setting to reach it at all, so the text is not read
    #: again for nothing: "fiscal" (a fiscal period), "relative" (a date that counts from
    #: today), "digit", or "numeric" (an all-number date: 03/04/2024, 3-4-24). None for
    #: settings that can reach any match.
    needs: str | None
    note: str


_DEFAULT_FISCAL_START = DEFAULT_CONFIG.fiscal.start_month

_SETTINGS = (
    _Setting(
        "fiscal_start_month", tuple(range(1, 13)), _DEFAULT_FISCAL_START,
        (1 if _DEFAULT_FISCAL_START != 1 else 4,), False, "fiscal",
        f"The fiscal year is taken to start in {calendar.month_name[_DEFAULT_FISCAL_START]}. "
        "Pass fiscal_start_month if the organisation's starts in another month.",
    ),
    _Setting(
        "fiscal_year_label", tuple(y.value for y in YearLabel),
        DEFAULT_CONFIG.fiscal.label_by.value, (YearLabel.START_YEAR.value,), True, "fiscal",
        "A fiscal year is taken to be named by the calendar year it ends in. Pass "
        "fiscal_year_label 'start_year' if it is named by the year it starts in.",
    ),
    _Setting(
        "year_basis", (Basis.CALENDAR.value, Basis.FISCAL.value),
        DEFAULT_CONFIG.effective_year_basis.value, (Basis.FISCAL.value,), True, None,
        "A year or period that does not say fiscal or calendar is read on the calendar "
        "year. Pass year_basis 'fiscal' if it means the fiscal year.",
    ),
    _Setting(
        "date_order", tuple(d.value for d in DateOrder), DEFAULT_CONFIG.date_order.value,
        (DateOrder.MDY.value, DateOrder.YMD.value), True, "numeric",
        "All-number dates are read day first. Pass date_order 'MDY' if they are written "
        "month first, as in the US.",
    ),
    _Setting(
        "month_number", tuple(n.value for n in MonthNumber), DEFAULT_CONFIG.month_number.value,
        (MonthNumber.YEAR.value,), True, "digit",
        "Two digits after a month name are read as a day: 'jan 24' is 24 January. Pass "
        "month_number 'year' if they are a year.",
    ),
    _Setting(
        "week_starts_on", _WEEKDAYS, _WEEKDAYS[DEFAULT_CONFIG.week_starts_on],
        ("sunday",), True, None,
        "Weeks are taken to start on Monday. Pass week_starts_on if they start on another "
        "day.",
    ),
    _Setting(
        "anchor", tuple(a.value for a in Anchor), DEFAULT_CONFIG.anchor.value,
        (Anchor.ROLLING.value,), True, "relative",
        "'Last month' and the like are read as whole calendar periods before the current "
        "one. Pass anchor 'rolling' to count back from today instead.",
    ),
)

#: How far today is moved to see which dates count from it. Over a year, so that a week,
#: a month, a quarter and a year all change, and a year with no year written lands on
#: another one.
_LATER = timedelta(days=400)


def _setting_value(setting: _Setting, raw: object) -> Any:
    """``raw`` as one of ``setting.values``, or a ToolInputError saying what is allowed."""
    if isinstance(raw, Enum):
        raw = raw.value
    numbered = setting.name in ("fiscal_start_month", "week_starts_on")
    if numbered and isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw)
    if isinstance(raw, int) and not isinstance(raw, bool):
        if setting.name == "fiscal_start_month" and 1 <= raw <= 12:
            return raw
        if setting.name == "week_starts_on" and 0 <= raw <= 6:
            return _WEEKDAYS[raw]
    elif isinstance(raw, str):
        wanted = raw.strip().lower()
        for value in setting.values:
            if str(value).lower() == wanted:
                return value
    if setting.name == "fiscal_start_month":
        raise ToolInputError(f"fiscal_start_month must be a month number, 1-12, got {raw!r}")
    allowed = ", ".join(repr(v) for v in setting.values)
    raise ToolInputError(f"{setting.name} must be one of {allowed}, got {raw!r}")


def _zone(raw: object) -> tzinfo | None:
    if raw is None or isinstance(raw, tzinfo):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return ZoneInfo(raw.strip())
        except (ZoneInfoNotFoundError, ValueError):
            pass
    raise ToolInputError(
        f"timezone must be an IANA name such as 'Asia/Kolkata', got {raw!r}"
    )


def _today(raw: object, zone: tzinfo | None) -> date:
    if raw is None:
        return datetime.now(zone).date() if zone is not None else date.today()
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw.strip())
        except ValueError:
            pass
    raise ToolInputError(f"today must be a date written YYYY-MM-DD, got {raw!r}")


def _config(chosen: Mapping[str, Any], strictness: str) -> WranglerConfig:
    return WranglerConfig(
        fiscal=FiscalCalendar(chosen["fiscal_start_month"], YearLabel(chosen["fiscal_year_label"])),
        year_basis=Basis(chosen["year_basis"]),
        date_order=DateOrder(chosen["date_order"]),
        month_number=MonthNumber(chosen["month_number"]),
        week_starts_on=_WEEKDAYS.index(chosen["week_starts_on"]),
        anchor=Anchor(chosen["anchor"]),
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
    week_starts_on: int | str | None = None,
    anchor: Anchor | str | None = None,
    strictness: str = "balanced",
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    """Every date in ``text``, resolved, with what an agent needs to judge each one.

    A setting left as None is *assumed*: the library default is used, and the text is read
    again under the setting's other values. If that reads a match as other dates, the
    default changed the answer, and ``assumptions`` says so with what it would be
    otherwise. A default that changed nothing is not reported, so an empty list means no
    setting the caller left out made a difference. ``strictness`` and ``min_confidence``
    are for Python and the CLI; they are not in the tool's schema.

    Returns a JSON-ready dict -- ``text``, ``today``, ``settings``, ``matches``,
    ``assumptions``, ``diagnostics``, ``version`` -- with each match carrying its
    ``first_day`` and ``last_day`` (inclusive), ``reads_as``, ``confidence``, ``notes``
    and ``relative_to_today`` alongside everything :meth:`DateMatch.to_dict` gives.
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
        "week_starts_on": week_starts_on,
        "anchor": anchor,
    }
    given = {s.name: _setting_value(s, raw[s.name]) for s in _SETTINGS if raw[s.name] is not None}
    chosen = {s.name: given.get(s.name, s.default) for s in _SETTINGS}
    cfg = _config(chosen, strictness)

    found, diags = diagnose(text, today=day, config=cfg)
    found = [m for m in found if m.confidence >= min_confidence]
    later = _readings(text, day + _LATER, cfg) if found else {}
    relative = [_moved(m, later) is not None for m in found]

    assumptions: list[dict[str, Any]] = []
    if today is None and any(relative):
        assumptions.append({
            "setting": "today",
            "assumed": day.isoformat(),
            "note": f"Relative dates count from today, taken as {day.isoformat()} from the "
                    "clock where this runs. Pass today as the user's date to be sure.",
            "matches": [_where(m) for m, moves in zip(found, relative, strict=True) if moves],
        })
    for setting in _SETTINGS:
        if setting.name in given or not _could_change(setting, found, relative):
            continue
        changed = _changed_by(setting, text, day, chosen, strictness, found)
        if changed:
            assumptions.append({
                "setting": setting.name,
                "assumed": setting.default,
                "note": setting.note,
                "matches": changed,
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


#: An all-number date has digits either side of a dash, slash or dot. Digits apart by
#: spaces alone are never read as a date ("03 04 2024" is not one), so a match without
#: this cannot depend on the date order.
_NUMERIC_DATE = re.compile(r"\d\s*[-/.]\s*\d")


def _could_change(setting: _Setting, found: list[DateMatch], relative: list[bool]) -> bool:
    if setting.needs is None:
        return bool(found)
    if setting.needs == "fiscal":
        return any(m.range.basis is Basis.FISCAL for m in found)
    if setting.needs == "relative":
        return any(relative)
    if setting.needs == "numeric":
        return any(_NUMERIC_DATE.search(m.text) for m in found)
    return any(ch.isdigit() for m in found for ch in m.text)


def _readings(text: str, day: date, cfg: WranglerConfig) -> dict[tuple[int, int], DateRange]:
    return {m.span: m.range for m in parse(text, today=day, config=cfg)}


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


def _changed_by(
    setting: _Setting,
    text: str,
    day: date,
    chosen: Mapping[str, Any],
    strictness: str,
    found: list[DateMatch],
) -> list[dict[str, Any]]:
    """The matches whose dates depend on ``setting``, each with its other readings."""
    otherwise: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for value in setting.others:
        other = _readings(text, day, _config({**chosen, setting.name: value}, strictness))
        for m in found:
            o = _moved(m, other)
            if o is not None:
                otherwise.setdefault(m.span, []).append({"value": value, **_days(o)})
    out = []
    for m in found:
        if m.span in otherwise:
            entry = _where(m)
            if setting.show_otherwise:
                entry["otherwise"] = otherwise[m.span]
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
