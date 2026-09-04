"""Command line entry point: ``date-wrangler "q1 fy25"``.

Exists mainly so that "what does this library think that phrase means?" is answerable in
one line, without writing a script. The predecessor had no such affordance, which made
every ambiguity a debugging session.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__
from .config import DateOrder, FiscalCalendar, ParserConfig, YearLabel
from .format import format_iso, format_range
from .parser import diagnose
from .types import Basis


def _build_config(args: argparse.Namespace) -> ParserConfig:
    return ParserConfig(
        fiscal=FiscalCalendar(
            start_month=args.fiscal_start,
            label_by=YearLabel(args.label_by),
        ),
        bare_period_basis=Basis(args.basis),
        date_order=DateOrder(args.date_order),
        strictness=args.strictness,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="date-wrangler",
        description="Parse dates, ranges and periods out of natural language.",
    )
    p.add_argument("text", nargs="+", help="text to parse")
    p.add_argument("--today", metavar="YYYY-MM-DD", help="resolve relative dates as of this day")
    p.add_argument("--tz", metavar="ZONE",
                   help="take today's date in this timezone, e.g. Asia/Kolkata")
    p.add_argument("--fiscal-start", type=int, default=4, metavar="M",
                   help="fiscal year start month (default 4)")
    p.add_argument("--label-by", choices=[y.value for y in YearLabel], default="end_year")
    p.add_argument("--basis", choices=[b.value for b in Basis], default="fiscal",
                   help="what a bare Q1/H1 means (default fiscal)")
    p.add_argument("--date-order", choices=[d.value for d in DateOrder], default="DMY")
    p.add_argument("--strictness", choices=["strict", "balanced", "greedy"], default="balanced")
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

    if args.json:
        print(json.dumps(
            {
                "text": text,
                "matches": [
                    {
                        "text": m.text,
                        "span": list(m.span),
                        "start": m.range.start.isoformat() if m.range.start else None,
                        "end": m.range.end.isoformat() if m.range.end else None,
                        "grain": m.range.grain.value,
                        "basis": m.range.basis.value,
                        "mod": m.range.mod.value if m.range.mod else None,
                        "iso": format_iso(m.range),
                        "confidence": m.confidence,
                    }
                    for m in matches
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
