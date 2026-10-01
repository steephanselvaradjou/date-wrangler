"""Rendering ranges back to text.

Formatting is a separate, replaceable function, which is what makes the output format
configurable -- and lets :func:`~date_wrangler.wrangler.parse` return an unbounded range
without having to phrase one.

Month names come from :mod:`.vocab`, never ``strftime('%B')``, which honours LC_TIME and
would let an unrelated ``setlocale`` elsewhere turn "June" into "Juni".
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


def _whole_months(r: DateRange) -> bool:
    """Whether the range covers whole calendar months, so naming months is honest.

    A to-date period does not. "MTD" on the 15th is half of October, and rendering it as
    "October 2026" claims the other half: a 15-day window described as a 31-day one, with
    nothing to show it was rounded. Half-open makes the test exact -- a range ending on a
    month boundary ends on the 1st.
    """
    return r.start is not None and r.end is not None and r.start.day == 1 == r.end.day


def format_range(r: DateRange) -> str:
    """A readable phrase for ``r``, at the grain it was expressed at."""
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
    if r.grain in (Grain.DAY, Grain.WEEK) or not _whole_months(r):
        return f"{format_day(r.start)} to {format_day(last)}"
    if (r.start.year, r.start.month) == (last.year, last.month):
        return format_month(r.start)
    # No leading "from": the phrase usually lands after a preposition already, and
    # "of from April to June" reads as a typo.
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
    """Build a formatter from format strings, so you need not write one.

    ``date_format`` is a strftime pattern for each bound; the templates are ``str.format``
    patterns taking ``{start}`` and ``{end}``, one per shape of range.

    ``inclusive_end`` decides which day ``{end}`` names -- the last day inside the period
    (the default, for people) or the exclusive bound (for machines).

    Unlike :func:`format_range`, ``%B`` and ``%A`` here are locale-sensitive::

        fmt = make_formatter(date_format="%d/%m/%Y", closed="{start} - {end}")
        substitute("sales in Q1 FY25", formatter=fmt)   # '01/04/2024 - 30/06/2024'
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
            # BEFORE excludes the named period, so it always reports the exclusive
            # bound: "before March" ends where March begins.
            edge = fmt(r.end) if r.mod is Mod.BEFORE else end
            template = before if r.mod is Mod.BEFORE else until
            return template.format(start=start, end=edge)
        # `single` prints {start} alone, so it is only honest when {end} would render the
        # same text. One day always qualifies. Otherwise it depends on what `date_format`
        # can actually show: "%B %Y" collapses any range inside one month to the same
        # string, while "%d %B %Y" does not -- which is how a week came back as its
        # Monday and a month as its 1st.
        if start == end:
            return single.format(start=start, end=end)
        return closed.format(start=start, end=end)

    return render
