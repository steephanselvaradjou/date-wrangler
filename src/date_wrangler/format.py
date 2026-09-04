"""Rendering ranges back to text.

Formatting is a separate, replaceable function rather than something baked into the
parser, which is what makes the output format configurable at all. It also keeps the two
halves honest: :func:`~date_wrangler.parser.parse` can express an unbounded range because
nothing in it is obliged to turn one into a sentence.

Month names come from a table in :mod:`.vocab`, never from ``strftime('%B')``. That
directive honours ``LC_TIME``, so the predecessor's output changed from "June" to "Juni"
if anything anywhere in the host process had called ``setlocale`` -- a library's output
should not depend on unrelated global state.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from .types import DateRange, Grain, Mod
from .vocab import MONTH_DISPLAY

__all__ = ["format_range", "format_iso", "format_day", "format_month", "make_formatter"]


def format_day(d: date) -> str:
    return f"{d.day} {MONTH_DISPLAY[d.month - 1]} {d.year}"


def format_month(d: date) -> str:
    return f"{MONTH_DISPLAY[d.month - 1]} {d.year}"


def format_iso(r: DateRange) -> str:
    """ISO 8601 interval notation, with ``..`` for an unbounded side."""
    lo = r.start.isoformat() if r.start else ".."
    hi = r.end.isoformat() if r.end else ".."
    return f"{lo}/{hi}"


def _point(d: date, grain: Grain) -> str:
    """Render a boundary at the resolution the period was asked at."""
    if grain in (Grain.DAY, Grain.WEEK):
        return format_day(d)
    return format_month(d)


def format_range(r: DateRange) -> str:
    """A readable phrase for ``r``, at the grain it was expressed at.

    Deliberately does not begin with the word "period". The predecessor emitted
    "period from X to Y", which collided with the word in its own input: "the P&L for the
    period April 2024 - March 2025" came back reading "for the period period from ...".
    """
    if r.start is None and r.end is None:
        return "any date"

    if r.start is None:
        assert r.end is not None
        edge = r.end if r.mod is Mod.BEFORE else r.end_inclusive
        assert edge is not None
        word = "before" if r.mod is Mod.BEFORE else "up to"
        return f"{word} {_point(edge, r.grain)}"

    if r.end is None:
        return f"{_point(r.start, r.grain)} onwards"

    if r.is_empty:
        return "an empty period"

    if r.mod is Mod.AS_OF:
        return f"as of {format_day(r.start)}"

    last = r.end_inclusive
    assert last is not None

    if r.grain is Grain.DAY and r.days == 1:
        return format_day(r.start)
    if r.grain in (Grain.DAY, Grain.WEEK):
        return f"{format_day(r.start)} to {format_day(last)}"
    if (r.start.year, r.start.month) == (last.year, last.month):
        return format_month(r.start)
    # No leading "from". The phrase usually lands in a slot that already has a preposition
    # -- "sales report of <range>" -- and "of from April to June" reads as a typo.
    return f"{format_month(r.start)} to {format_month(last)}"


def make_formatter(
    *,
    date_format: str = "%Y-%m-%d",
    closed: str = "{start} to {end}",
    single: str = "{start}",
    since: str = "{start} onwards",
    until: str = "up to {end}",
    before: str = "before {end}",
    after: str = "{start} onwards",
    as_of: str = "as of {start}",
    unbounded: str = "any date",
    inclusive_end: bool = True,
) -> Callable[[DateRange], str]:
    """Build a formatter from format strings, for callers who do not want to write one.

    ``date_format`` is a ``strftime`` pattern applied to each bound; the templates are
    ``str.format`` patterns taking ``{start}`` and ``{end}``. One is chosen per range
    according to which bounds exist and which modifier applies.

    ``inclusive_end`` decides which day ``{end}`` names. It defaults to True because an
    end-user-facing string should say the last day *inside* the period -- "to 2024-03-31",
    not "to 2024-04-01". Set it False when the output feeds a system that wants the
    exclusive bound.

    Note that ``%B`` and ``%A`` in ``date_format`` are locale-sensitive, unlike the
    built-in :func:`format_range`. That is your choice to make here, but it does mean the
    output can change with the host process's locale::

        fmt = make_formatter(date_format="%d/%m/%Y", closed="{start} - {end}")
        substitute("sales in Q1 FY25", formatter=fmt)
        # 'sales in 01/04/2024 - 30/06/2024'
    """

    def render(r: DateRange) -> str:
        def fmt(d: date | None) -> str:
            return d.strftime(date_format) if d is not None else ""

        end_day = r.end_inclusive if inclusive_end else r.end
        start, end = fmt(r.start), fmt(end_day)

        if r.start is None and r.end is None:
            return unbounded
        if r.mod is Mod.AS_OF:
            return as_of.format(start=start, end=end)
        if r.end is None:
            template = after if r.mod is Mod.AFTER else since
            return template.format(start=start, end=end)
        if r.start is None:
            # BEFORE excludes the named period, so it reports the exclusive bound even
            # when inclusive_end is set -- "before March" ends where March begins.
            edge = fmt(r.end) if r.mod is Mod.BEFORE else end
            template = before if r.mod is Mod.BEFORE else until
            return template.format(start=start, end=edge)
        if r.grain is not Grain.DAY and (r.start.year, r.start.month) == (
            end_day.year,  # type: ignore[union-attr]
            end_day.month,  # type: ignore[union-attr]
        ):
            return single.format(start=start, end=end)
        if r.grain is Grain.DAY and r.days == 1:
            return single.format(start=start, end=end)
        return closed.format(start=start, end=end)

    return render
