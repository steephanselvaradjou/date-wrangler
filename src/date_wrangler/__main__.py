"""Command line entry point: ``date-wrangler "q1 fy25"``.

So that "what does this think that phrase means?" is answerable without writing a script.
Both outputs come from :func:`date_wrangler.tool.resolve_dates`, so ``--json`` is exactly
the answer an agent's tool call gets -- which is what lets a skill run this command.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import DateOrder, MonthNumber, YearLabel
from .tool import ToolInputError, resolve_dates
from .types import Anchor, Basis, DateRange


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="date-wrangler",
        description="Wrangles messy human dates into clean ranges.",
    )
    p.add_argument("text", nargs="+", help="text to parse")
    p.add_argument("--today", metavar="YYYY-MM-DD", help="resolve relative dates as of this day")
    p.add_argument("--tz", metavar="ZONE",
                   help="take today's date in this timezone, e.g. Asia/Kolkata")
    # Settings default to None rather than their values, so that a setting the caller left
    # out can be told from one they gave: only the first is reported as an assumption.
    p.add_argument("--fiscal-start", type=int, default=None, metavar="M",
                   help="fiscal year start month (default 4)")
    p.add_argument("--label-by", choices=[y.value for y in YearLabel], default=None,
                   help="which year names a fiscal year (default end_year)")
    p.add_argument("--basis", choices=[b.value for b in Basis], default=None,
                   help="default for --year-basis (default calendar)")
    p.add_argument("--year-basis", choices=[b.value for b in Basis], default=None,
                   help="how every year and period that does not say fiscal or calendar is "
                        "read (default: follow --basis)")
    p.add_argument("--anchor", choices=[a.value for a in Anchor], default=None,
                   help="what 'last month' means (default anchored)")
    p.add_argument("--date-order", choices=[d.value for d in DateOrder], default=None,
                   help="how 03/04/2024 is read (default DMY)")
    p.add_argument("--month-number", choices=[n.value for n in MonthNumber], default=None,
                   help="what two digits after a month name are: 'jan 24' (default day)")
    p.add_argument("--two-digit-pivot", default=None, metavar="N",
                   help="two-digit years at or below N are 20xx, above it 19xx (default 68)")
    p.add_argument("--strictness", choices=["strict", "balanced", "greedy"], default="balanced")
    p.add_argument("--week-starts-on", default=None, metavar="D",
                   help="0=Monday (default) through 6=Sunday, or the day's name")
    p.add_argument("--weekend", default=None, metavar="DAYS",
                   help="non-working days for business-day phrases, comma-separated, e.g. "
                        "fri,sat (default sat,sun)")
    p.add_argument("--holidays", default=None, metavar="DATES",
                   help="non-working dates, comma-separated YYYY-MM-DD (default none)")
    p.add_argument("--min-confidence", type=float, default=0.0, metavar="C",
                   help="drop matches below this, e.g. 0.9 to exclude flagged reads")
    p.add_argument("--json", action="store_true",
                   help="emit JSON instead of a table: the matches with their first and last "
                        "days, the settings assumed that changed an answer, and diagnostics")
    p.add_argument("--version", action="version", version=f"date-wrangler {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    text = " ".join(args.text)
    try:
        answer = resolve_dates(
            text,
            today=args.today,
            timezone=args.tz,
            fiscal_start_month=args.fiscal_start,
            fiscal_year_label=args.label_by,
            year_basis=args.year_basis or args.basis,
            date_order=args.date_order,
            month_number=args.month_number,
            two_digit_pivot=args.two_digit_pivot,
            week_starts_on=args.week_starts_on,
            anchor=args.anchor,
            weekend=args.weekend,
            holidays=args.holidays,
            strictness=args.strictness,
            # Filtering here rather than inside diagnose keeps the diagnostics: a caller
            # asking for confident matches only still wants to be told what was dropped.
            min_confidence=args.min_confidence,
        )
    except ToolInputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(answer, indent=2))
        return 0

    matches, diags = answer["matches"], answer["diagnostics"]
    if not matches and not diags:
        print("no dates found")
        return 1
    for m in matches:
        r = DateRange.from_dict(m["range"])
        print(f"{m['text']!r}")
        print(f"   {m['reads_as']}")
        print(f"   {m['iso']}   grain={r.grain.value} basis={r.basis.value}"
              f"{' mod=' + r.mod.value if r.mod else ''} confidence={m['confidence']:g}")
        print(f"   SQL: {r.sql('d')}")
    for d in diags:
        print(f"?? {d['text']!r} ({d['rule']}): {d['reason']}", file=sys.stderr)
    # Today is left out of the table: at a terminal it is plainly the clock, and every
    # relative phrase would repeat it.
    for a in answer["assumptions"]:
        if a["setting"] != "today":
            assumed = a["assumed"]
            if isinstance(assumed, list):  # weekend, holidays: as the flag takes them
                assumed = ",".join(assumed) or "none"
            print(f"assumed {a['setting']}={assumed}: {a['note']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
