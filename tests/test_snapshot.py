"""Every answer the library gives, pinned, so none can change without being seen.

Behaviour tests check the cases someone thought of. This checks everything else: several
thousand phrases, under three configurations and on two days, each with its full answer.
When a change alters any of them -- on purpose or not -- this fails and lists them.

To accept a change, look at what moved and then rewrite the snapshot:

    python tests/test_snapshot.py --update

The rewritten file is part of the commit, so every changed answer shows in the diff, one
line each, for whoever reviews it. A change that moves nothing shows nothing.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from date_wrangler import DateMatch, DateOrder, FiscalCalendar, WranglerConfig, YearLabel, diagnose

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "answers.txt"

CONFIGS = {
    "default": WranglerConfig(),
    "fiscal": WranglerConfig(year_basis="fiscal"),
    # A US company: October year named by its start, month-first dates, Sunday weeks.
    "us": WranglerConfig(
        fiscal=FiscalCalendar(10, YearLabel.START_YEAR),
        date_order=DateOrder.MDY,
        week_starts_on=6,
    ),
}
DAYS = [date(2026, 10, 8), date(2026, 3, 31)]  # mid-quarter Thursday; an April year's end

PHRASES = [
    # absolute
    "2024-03-15", "15 March 2024", "March 15, 2024", "15/03/2024", "03/04/2024",
    "15-Mar-2024", "Mar-24", "March 2024", "March", "2024", "the year 2013", "FY25", "FY2025",
    "FY2024-25", "CY2024", "fiscal year 2025", "Q1", "Q3", "Q1 FY25", "Q1 2024", "Q1 of 2024",
    "Q1 of the year 2024", "H1", "H2 FY25", "the 1990s", "week 42", "2026-W42", "CW42",
    "Jun FY25", "FY25 June", "Jun-FY25", "June FY 2024-25",
    # relative
    "today", "yesterday", "tomorrow", "this week", "last week", "next week", "this month",
    "last month", "next month", "this quarter", "last quarter", "next quarter", "this year",
    "last year", "next year", "this half", "last 3 months", "last 30 days", "next 2 weeks",
    "past 6 months", "last 2 quarters", "next 3 years", "3 months ago", "2 years ago",
    "a fortnight ago", "in 3 days", "2 weeks from now", "last fortnight",
    "this fiscal year", "this calendar year", "this FY", "last fiscal quarter",
    "last 2 fiscal years", "next calendar quarter", "current fiscal year",
    # to date
    "YTD", "MTD", "QTD", "year to date", "this year to date", "this month to date",
    "this quarter to date", "Q3 to date", "FY27 to date", "last YTD", "March 2024 to date",
    # weekdays and weekends
    "last Monday", "next Friday", "this Tuesday", "this weekend", "next weekend",
    # parts, edges, nth
    "first half of March", "early 2024", "mid March", "end of Q1", "end of the year",
    "beginning of next month", "end of next month", "start of FY27",
    "1st of next month", "15th of March", "last day of the month", "the last day of March",
    "first day of next quarter", "first Monday of March", "last Friday of the month",
    "third Thursday of November", "end of the year 2013", "first half of the year 2013",
    # business days
    "5 business days ago", "next business day", "last 10 business days", "in 3 business days",
    "last business day of the month", "first working day of next month",
    # relative years, trailing, period ending
    "Q1 last year", "March last year", "same quarter last year", "this time last year",
    "H2 next year", "TTM", "LTM", "trailing 12 months", "rolling 3 months", "L3M",
    "quarter ending June 2024", "year ended March 2024",
    # open-ended
    "since March", "since March 2024", "before 2024", "after FY24", "until March 2024",
    "from Q1 onwards", "up to March 2024", "as of 31 March 2024", "March 2024 onwards",
    "on or after 1 April", "on or before 31 March", "no later than Friday",
    # idioms
    "day before yesterday", "day after tomorrow", "EOD", "EOM", "COB Friday",
    # realistic phrasings, written without the library in mind
    "the last quarter of 2024", "the last month of the year", "the last week of March",
    "the first week of April", "the last month of the quarter", "the first quarter",
    "the fourth quarter of 2024", "the second half of last year", "the last half of 2024",
    "first week of next month", "last week of the year", "the final quarter",
    "the quarter before last", "the month before last", "the year before last",
    "the week before last", "two quarters ago", "since the start of the year",
    "since the beginning of the month", "from the start of Q2", "until the end of the month",
    "by the end of the quarter", "by end of year", "before the end of March",
    "start of this month", "end of last quarter", "late last year", "early next year",
    "mid-2024", "mid next year", "fiscal Q3", "Q3 fiscal", "FY Q3", "Q3 FY", "3Q24", "Q3'24",
    "Q3-24", "2024Q3", "2024-Q3", "1H24", "H1'25", "Q3 2024", "the third quarter",
    "second quarter of 2025", "the 2020s", "the '90s", "the nineties", "Jan '24",
    "January '24", "Jan 2024 - Mar 2024", "Jan-Mar 2024", "January to March 2024",
    "Q1-Q2 2024", "between Monday and Wednesday", "Monday through Friday", "Mon-Fri",
    "from 1 April to 30 June", "1-15 March", "1st to 15th March", "March 1-15",
    "the year to March 2024", "six months to June 2024", "the 12 months to March 2024",
    "12 months ending March 2024", "the half year to 30 September 2024",
    "as at 31 March 2024", "the quarter to June", "within the next 30 days",
    "in the next 2 weeks", "over the next quarter", "last 12 months", "past 12 months",
    "trailing twelve months", "recently", "a few days ago", "the other day",
    "in the coming months", "some time ago", "last summer", "this morning", "tonight",
    "tomorrow morning", "next Monday week", "a week on Monday", "Monday next",
    "every Monday", "fortnightly", "weekdays",
    # lists
    "Q1, Q2 and Q3", "Jan, Feb, Mar 2024", "Sales in Q1, Q2 and Q3 of FY25",
    "1st and 15th March", "1 and 15 March", "March 1 and 15", "1, 5 and 9 March 2024",
    "March 1 and 15, 2024", "1st vs 15th March", "between 1 and 15 March",
    "Q1 and Q3", "Q1 and Q3 2024", "Jan to Mar and Jul to Sep 2024", "the figures from Q1 and Q3",
    "FY24 - Q3", "FY24 - Q3 results", "2023 - H2 2024", "FY24, March",
    # "the" and what goes before it
    "in 2013", "sales for the year 2013", "sales for the last quarter", "the March figures",
    "the 15 March meeting", "since the last quarter", "before the last week",
    "after the first quarter", "up to the previous month", "since the year 2013",
    "from the 15th of March", "from 15th of March", "from the last day of March",
    "from the first Monday of March", "two weeks from the 15th", "a month from the 15th of March",
    # the period that holds a date
    "the week of 5 October", "the week of October 5", "week of 2026-10-07", "the week of the 5th",
    "week commencing 5 October", "w/c 7 Oct", "week beginning 7 October 2026",
    "the quarter of June", "the quarter of June FY25", "the year of 15 March 2024",
    "the month of March", "the week of last Monday", "the quarter of last month",
    "compare Q1 2024 to Q1 2025", "Q1 2024 vs Q1 2025", "last week vs this week",
    # sentences
    "How much did we book between April and September 2024?",
    "Give me last month, this month and next month",
    "revenue for Q1 FY25 against last quarter, and the trend since April 2024",
    "Headcount as of 31 March 2024", "the march on Washington", "sales by June Patel",
    "Order number 2024 is pending", "a 2h drive", "Sales increased by 25 percent",
]

#: Joins: a handful of dates people do join, with every joiner, under the default only.
ENDPOINTS = [
    "Q1", "Q3", "H1", "FY24", "FY25", "March", "June", "March 2024", "15 March",
    "15 March 2024", "2023", "2024", "last month", "this month", "last quarter",
    "yesterday", "Monday", "Friday", "last week", "this year",
]
JOINERS = [" and ", " & ", ", ", " to ", " - ", " through ", " until ", " vs "]


def _answer(matches: list[DateMatch]) -> str:
    if not matches:
        return "(none)"
    parts = []
    for m in matches:
        r = m.range
        start = r.start.isoformat() if r.start else "..."
        end = r.end_inclusive.isoformat() if r.end_inclusive else "..."
        mod = f" {r.mod.value}" if r.mod is not None else ""
        parts.append(
            f"{m.text!r} {start}..{end} {r.grain.value} {r.basis.value}{mod} c={m.confidence:g}"
        )
    return " ; ".join(parts)


def _line(label: str, day: date, phrase: str, cfg: WranglerConfig) -> str:
    """The answer, then -- only when there are any -- the reasons given for what was not read.

    A phrase with no match and no reason looks exactly like one that holds no date, so the
    reasons are part of the answer: losing one, or gaining one, shows in the diff too.
    """
    matches, diags = diagnose(phrase, today=day, config=cfg)
    reasons = f" !! {' ; '.join(d.reason for d in diags)}" if diags else ""
    return f"{label} {day} | {phrase} | {_answer(matches)}{reasons}"


def answers() -> list[str]:
    """One line per phrase, configuration and day, in a fixed order."""
    lines = [
        _line(name, day, phrase, cfg)
        for name, cfg in CONFIGS.items()
        for day in DAYS
        for phrase in PHRASES
    ]
    for a in ENDPOINTS:
        for b in ENDPOINTS:
            if a == b:
                continue
            for phrase in [a + joiner + b for joiner in JOINERS] + [f"between {a} and {b}"]:
                lines.append(_line("join", DAYS[0], phrase, CONFIGS["default"]))
    return lines


def test_every_answer_matches_the_snapshot():
    assert SNAPSHOT.is_file(), "no snapshot yet: run python tests/test_snapshot.py --update"
    want = SNAPSHOT.read_text(encoding="utf-8").splitlines()
    got = answers()
    key = lambda line: line.rsplit(" | ", 1)[0]  # noqa: E731
    was = {key(line): line for line in want}
    now = {key(line): line for line in got}
    changed = [
        f"  {k}\n    was {was[k].rsplit(' | ', 1)[1]}\n    now {now[k].rsplit(' | ', 1)[1]}"
        for k in now
        if k in was and was[k] != now[k]
    ]
    added = sorted(set(now) - set(was))
    removed = sorted(set(was) - set(now))
    assert not (changed or added or removed), (
        f"{len(changed)} answers changed, {len(added)} phrases added, {len(removed)} removed.\n"
        + "\n".join(changed[:40])
        + ("\n  ..." if len(changed) > 40 else "")
        + "\nIf every change is intended: python tests/test_snapshot.py --update"
    )


if __name__ == "__main__":
    if "--update" not in sys.argv:
        sys.exit("usage: python tests/test_snapshot.py --update")
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text("\n".join(answers()) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {SNAPSHOT}")
