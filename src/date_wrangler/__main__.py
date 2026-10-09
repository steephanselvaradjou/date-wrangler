"""Command line entry point: ``date-wrangler "q1 fy25"``.

So that "what does this think that phrase means?" is answerable without writing a script.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__
from .config import DateOrder, FiscalCalendar, WranglerConfig, YearLabel
from .format import format_iso, format_range
from .types import Anchor, Basis
from .wrangler import diagnose


def _build_config(args: argparse.Namespace) -> WranglerConfig:
    return WranglerConfig(
        fiscal=FiscalCalendar(
            start_month=args.fiscal_start,
            label_by=YearLabel(args.label_by),
        ),
        bare_period_basis=Basis(args.basis),
        year_basis=Basis(args.year_basis) if args.year_basis else None,
        anchor=Anchor(args.anchor),
        date_order=DateOrder(args.date_order),
        week_starts_on=args.week_starts_on,
        strictness=args.strictness,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="date-wrangler",
        description="Wrangles messy human dates into clean ranges.",
    )
    p.add_argument("text", nargs="+", help="text to parse")
    p.add_argument("--today", metavar="YYYY-MM-DD", help="resolve relative dates as of this day")
    p.add_argument("--tz", metavar="ZONE",
                   help="take today's date in this timezone, e.g. Asia/Kolkata")
    p.add_argument("--fiscal-start", type=int, default=4, metavar="M",
                   help="fiscal year start month (default 4)")
    p.add_argument("--label-by", choices=[y.value for y in YearLabel], default="end_year")
    p.add_argument("--basis", choices=[b.value for b in Basis], default="calendar",
                   help="default for --year-basis (default calendar)")
    p.add_argument("--year-basis", choices=[b.value for b in Basis], default=None,
                   help="how every year and period that does not say fiscal or calendar is "
                        "read (default: follow --basis)")
    p.add_argument("--anchor", choices=[a.value for a in Anchor], default="anchored",
                   help="what 'last month' means (default anchored)")
    p.add_argument("--date-order", choices=[d.value for d in DateOrder], default="DMY")
    p.add_argument("--strictness", choices=["strict", "balanced", "greedy"], default="balanced")
    p.add_argument("--week-starts-on", type=int, default=0, metavar="D",
                   help="0=Monday (default) through 6=Sunday")
    p.add_argument("--min-confidence", type=float, default=0.0, metavar="C",
                   help="drop matches below this, e.g. 0.9 to exclude flagged reads")
    p.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    p.add_argument("--version", action="version", version=f"date-wrangler {__version__}")
    args = p.parse_args(argv)

    text = " ".join(args.text)
    today = None
    if args.today:
        try:
            today = datetime.strptime(args.today, "%Y-%m-%d").date()
        except ValueError:
            print(f"error: --today must be YYYY-MM-DD, got {args.today!r}", file=sys.stderr)
            return 2
    tz = None
    if args.tz:
        try:
            tz = ZoneInfo(args.tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            print(f"error: unknown timezone {args.tz!r}: {exc}", file=sys.stderr)
            return 2
    try:
        cfg = _build_config(args)
    except (ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    matches, diags = diagnose(text, today=today, tz=tz, config=cfg)
    # Filtering here rather than inside diagnose keeps the diagnostics: a caller asking
    # for confident matches only still wants to be told what was dropped and why.
    matches = [m for m in matches if m.confidence >= args.min_confidence]

    if args.json:
        print(json.dumps(
            {
                "text": text,
                "matches": [
                    {**m.to_dict(), "iso": format_iso(m.range)} for m in matches
                ],
                "diagnostics": [
                    {"text": d.text, "span": list(d.span), "rule": d.rule, "reason": d.reason}
                    for d in diags
                ],
            },
            indent=2,
        ))
        return 0

    if not matches and not diags:
        print("no dates found")
        return 1
    for m in matches:
        r = m.range
        print(f"{m.text!r}")
        print(f"   {format_range(r)}")
        print(f"   {format_iso(r)}   grain={r.grain.value} basis={r.basis.value}"
              f"{' mod=' + r.mod.value if r.mod else ''} confidence={m.confidence:g}")
        print(f"   SQL: {r.sql('d')}")
    for d in diags:
        print(f"?? {d.text!r} ({d.rule}): {d.reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
