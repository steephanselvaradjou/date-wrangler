"""Every common date joined to every other with every joiner: nothing dropped, nothing merged.

The snapshot pins answers; this pins properties of them, over far more combinations than
anyone would write out. It exists because the same kind of miss kept turning up one
phrase at a time: "1st and 15th March" lost the 1st, "Q1 and Q3" held Q2, "2023 - H2 2024"
lost 2024. Each was a join nobody had listed.

For each phrase:

- neither side may vanish: each is read as a date, or a diagnostic says why not;
- a list joiner ("and", "&", a comma, "vs") never merges both sides into one range, and
  the two sides come back in one group;
- a span joiner ("to", "-", "between ... and") gives one range across both, or a
  diagnostic saying why it could not, and the span's endpoints are its two ends;
- a comma is part of one date where it is part of how dates are written -- "June, 2013",
  "Q1, 2024", "Friday, 15 March" -- and reads exactly as the date without it.

The comma rule was missing, and the sweep insisted on the opposite: "June, 2013" had to
be two periods, which is how "in June, 2013" came back as June 2013 and all of 2013.
"""

from __future__ import annotations

from datetime import date

from date_wrangler import WranglerConfig, diagnose, parse

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

#: A comma between these is part of one date: "June, 2013", "Q1, 2024", "March 15, 2024".
NO_YEAR = ["Q1", "Q3", "H1", "March", "June", "15 March", "March 15", "1st March"]
YEARS = ["2023", "2024", "FY24", "FY25", "CY2024"]
#: ...and so is one after a weekday, before the date it names: "Friday, 15 March".
WEEKDAYS = ["Monday", "Friday"]
DAYS = ["15 March", "March 15", "15th March 2024", "2024-03-15", "03/04/2024", "1st March"]


def _comma_is_one_date(a: str, joiner: str, b: str) -> bool:
    return joiner == ", " and ((a in NO_YEAR and b in YEARS) or (a in WEEKDAYS and b in DAYS))


def _cases():
    pairs = [(a, b) for a in FORMS for b in FORMS if a != b]
    pairs += [(a, b) for a in BARE_DAYS for b in DAYS_WITH_MONTH]
    for ctx in CONTEXTS:
        pre = ctx.index("{}")
        for a, b in pairs:
            for joiner, kind in [(j, "list") for j in LIST_JOINERS] + [
                (j, "span") for j in SPAN_JOINERS
            ]:
                if _comma_is_one_date(a, joiner, b):
                    kind = "one"
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


def _reading(text: str) -> list[tuple[date | None, date | None]]:
    return [(m.range.start, m.range.end) for m in parse(text, today=TODAY)]


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
        if kind == "list" and not across:
            first = [m for m in matches if _overlaps(*a, [m.span])]
            second = [m for m in matches if _overlaps(*b, [m.span])]
            if first and second and first[0].group != second[-1].group:
                found.append(f"named together but not grouped: {phrase!r}")
        both_read = _overlaps(*a, spans) and _overlaps(*b, spans)
        if kind == "span" and not across and not diags and both_read:
            found.append(f"split a span without saying why: {phrase!r}")
        if kind == "span" and across:
            # "FY24 - Q3" is a label, Q3 of FY24, and has no ends; "to" always makes a span.
            m = across[0]
            ends = m.endpoints
            if (ends is None and " to " in phrase) or (
                ends is not None and (ends[0].start, ends[1].end) != (m.range.start, m.range.end)
            ):
                found.append(f"a span whose endpoints are not its ends: {phrase!r}")
        if kind == "one":
            words = phrase[a[0] : b[1]]
            # The date the comma belongs to, written without it.
            alone = words.replace(", ", " ") if words.split(", ")[0] in NO_YEAR else words.split(
                ", "
            )[1]
            if len(across) != 1 or [(across[0].range.start, across[0].range.end)] != _reading(
                alone
            ):
                found.append(f"a comma split one date, or read it differently: {phrase!r}")
    return found


def test_no_join_drops_merges_or_splits_unexplained():
    problems = _problems()
    assert not problems, f"{len(problems)} problems, e.g.:\n" + "\n".join(problems[:30])


LISTED = ["Q1", "Q3", "H1", "March", "June"]
COMPARED_YEARS = ["2023", "2024", "FY24", "FY25"]


def test_a_year_stays_on_its_own_side_of_a_comparison():
    """"Q1 and Q3 2024 vs 2023": the 2024 is Q1's too, not the 2023 across the "vs".
    REGRESSION: the last year written anywhere in the run was given to every period."""
    problems = []
    for a in LISTED:
        for b in LISTED:
            if a == b:
                continue
            for y1 in COMPARED_YEARS:
                for y2 in COMPARED_YEARS:
                    if y1 == y2:
                        continue
                    for joiner in (" and ", ", ", " & "):
                        for versus in (" vs ", " versus ", " compared to "):
                            phrase = f"{a}{joiner}{b} {y1}{versus}{y2}"
                            got = _reading(phrase)
                            if not got or got[0] != _reading(f"{a} {y1}")[0]:
                                problems.append(phrase)
    assert not problems, f"{len(problems)} problems, e.g.:\n" + "\n".join(problems[:30])
