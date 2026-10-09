"""Every common date joined to every other with every joiner: nothing dropped, nothing merged.

The snapshot pins answers; this pins properties of them, over far more combinations than
anyone would write out. It exists because the same kind of miss kept turning up one
phrase at a time: "1st and 15th March" lost the 1st, "Q1 and Q3" held Q2, "2023 - H2 2024"
lost 2024. Each was a join nobody had listed.

For each phrase:

- neither side may vanish: each is read as a date, or a diagnostic says why not;
- a list joiner ("and", "&", a comma, "vs") never merges both sides into one range;
- a span joiner ("to", "-", "between ... and") gives one range across both, or a
  diagnostic saying why it could not.
"""

from __future__ import annotations

from datetime import date

from date_wrangler import WranglerConfig, diagnose

TODAY = date(2026, 10, 9)

FORMS = [
    "Q1", "Q3", "Q3 2024", "FY24 Q3", "H1", "H2 2024", "FY24", "FY25", "CY2024",
    "March", "June", "March 2024", "June 2025", "Jun FY25",
    "15 March", "March 15", "15th March 2024", "2024-03-15", "03/04/2024", "1st March",
    "2023", "2024",
    "last week", "this month", "last month", "yesterday", "today", "last 7 days", "YTD",
    "last quarter", "next year",
    "Monday", "Friday",
]
#: A bare day only joins a day that brings the month: "1st and 15th March".
BARE_DAYS = ["1st", "1", "5th"]
DAYS_WITH_MONTH = ["15th March", "15 March", "15th March 2024", "20 June"]

LIST_JOINERS = [" and ", " & ", ", ", ", and ", " vs "]
SPAN_JOINERS = [" to ", " - "]
CONTEXTS = ["{}", "sales for {}"]


def _cases():
    pairs = [(a, b) for a in FORMS for b in FORMS if a != b]
    pairs += [(a, b) for a in BARE_DAYS for b in DAYS_WITH_MONTH]
    for ctx in CONTEXTS:
        pre = ctx.index("{}")
        for a, b in pairs:
            for joiner, kind in [(j, "list") for j in LIST_JOINERS] + [
                (j, "span") for j in SPAN_JOINERS
            ]:
                if joiner == ", " and a[-1].isdigit() and (b[0].isdigit() or b[:2] in ("FY", "CY")):
                    continue  # "March 15, 2024" is one date, not a list
                b_at = pre + len(a) + len(joiner)
                yield ctx.format(a + joiner + b), (pre, pre + len(a)), (b_at, b_at + len(b)), kind
            b_at = pre + len("between ") + len(a) + len(" and ")
            yield (
                ctx.format(f"between {a} and {b}"),
                (pre + len("between "), pre + len("between ") + len(a)),
                (b_at, b_at + len(b)),
                "span",
            )


def _overlaps(lo, hi, spans):
    return any(a < hi and lo < b for a, b in spans)


def _problems():
    cfg = WranglerConfig()
    found = []
    for phrase, a, b, kind in _cases():
        matches, diags = diagnose(phrase, today=TODAY, config=cfg)
        spans = [m.span for m in matches]
        explained = [d.span for d in diags]
        for side, (lo, hi) in (("first", a), ("second", b)):
            if not _overlaps(lo, hi, spans) and not _overlaps(lo, hi, explained):
                found.append(f"dropped the {side} date: {phrase!r}")
        across = [m for m in matches if m.span[0] <= a[0] and m.span[1] >= b[1]]
        if kind == "list" and across:
            found.append(f"merged a list into one range: {phrase!r}")
        both_read = _overlaps(*a, spans) and _overlaps(*b, spans)
        if kind == "span" and not across and not diags and both_read:
            found.append(f"split a span without saying why: {phrase!r}")
    return found


def test_no_join_drops_merges_or_splits_unexplained():
    problems = _problems()
    assert not problems, f"{len(problems)} problems, e.g.:\n" + "\n".join(problems[:30])
