"""Two periods named together are compared, listed or spanned, whatever words join them.

Each pair of periods is joined by every connector people write, and must come back as both
periods, in one group, joined as the connector says, each with its own dates -- a span as
one match from the start of one to the end of the other. In 2.2.0 about four in seven read
that way. Among the rest, "claims in Q1 FY25 vs claims in Q1 FY26", "Q1 FY25 v/s Q1 FY26",
"Q1 FY25 as against Q1 FY26", "how does Q1 FY25 compare to Q1 FY26" and "Q1 FY25 as well as
Q1 FY26" gave two unrelated quarters, with a bare year after them not read at all; "the
difference between Q1 and Q3" ran from Q1 to Q3; "March 2024 vs a year earlier" was all of
2025; "Q1 2024 vs the same period last year" all of last year; "compare March 2024 to the
previous month" September 2026; and "vs LY" was not read.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from date_wrangler import diagnose, parse

TODAY = date(2026, 10, 16)

PAIRS = [
    ("Q1 FY25", "Q1 FY26"),
    ("2023", "2024"),
    ("March 2024", "March 2025"),
    ("March", "June"),
    ("H1 2024", "H2 2024"),
    ("15 March 2024", "15 April 2024"),
]
#: Read only with a word in front that marks them as dates.
BARE = {"2023", "March"}
COMPARE = [
    "vs", "vs.", "Vs", "VS", "v", "v.", "v/s", "v.s.", "versus", "against", "compared to",
    "compared with", "as compared to", "as compared with", "as against", "in comparison to",
    "in comparison with", "relative to", "over", "vis-a-vis", "compared against",
    "compare to", "compare with", "compares to", "compares with", "stack up against",
    "stacks up against",
]  # fmt: skip
LIST = ["and", ",", "or", "&", "and/or", "as well as", "along with", "plus", "together with", "+"]
RANGE = ["to", "-", "through", "thru", "until", "till", "up to", "upto", "–", "through to"]
#: After a word that says a comparison is coming, these compare too.
LEADS = ["compare", "comparing", "comparison of", "benchmark", "contrast"]
LED = ["and", "to", "with", "against", "vs", "versus"]
#: "the difference between A and B" compares A and B; "between A and B" alone is a span.
BETWEEN = ["difference between", "variance between", "gap between", "delta between"]
#: A period named from the one beside it.
RELATIVE = [
    ("Q1 2024", "the same quarter last year", (date(2023, 1, 1), date(2023, 4, 1))),
    ("March 2024", "the previous month", (date(2024, 2, 1), date(2024, 3, 1))),
    ("2024", "prior year", (date(2023, 1, 1), date(2024, 1, 1))),
    ("2024", "the year before", (date(2023, 1, 1), date(2024, 1, 1))),
    ("Q2 2024", "the prior quarter", (date(2024, 1, 1), date(2024, 4, 1))),
    ("March 2024", "a year earlier", (date(2023, 3, 1), date(2023, 4, 1))),
    ("Q1 2024", "the same period last year", (date(2023, 1, 1), date(2023, 4, 1))),
    ("Q1 2024", "LY", (date(2023, 1, 1), date(2023, 4, 1))),
]


def _cases() -> Iterator[tuple[str, str, str, str, tuple[date, date] | None]]:
    for a, b in PAIRS:
        for frame in ("revenue in {a} {c} {b}", "{a} {c} {b}"):
            for kind, connectors in (("compare", COMPARE), ("list", LIST), ("range", RANGE)):
                for c in connectors:
                    yield frame.format(a=a, b=b, c=c).replace(" ,", ","), a, b, kind, None
        for lead in LEADS:
            for c in LED:
                yield f"{lead} {a} {c} {b}", a, b, "compare", None
                yield f"{lead} revenue in {a} {c} {b}", a, b, "compare", None
        for lead in BETWEEN:
            yield f"{lead} {a} and {b}", a, b, "compare", None
        for lead in ("how does", "how did"):
            for c in ("compare to", "compare with", "stack up against"):
                yield f"{lead} revenue in {a} {c} {b}", a, b, "compare", None
        # The measure written again on either side. Not after a bare year or month with
        # nothing in front: "2023 claims" is as likely two thousand and twenty-three.
        yield f"claims in {a} vs claims in {b}", a, b, "compare", None
        if a not in BARE:
            yield f"{a} claims vs {b} claims", a, b, "compare", None
        yield f"compare claims in {a} with claims in {b}", a, b, "compare", None
    for a, b, b_range in RELATIVE:
        for c in ("vs", "compared to", "against", "versus", "v/s", "as against"):
            yield f"revenue in {a} {c} {b}", a, b, "compare", b_range
        for c in ("with", "to", "and"):
            yield f"compare {a} {c} {b}", a, b, "compare", b_range


def _alone(period: str) -> tuple[date | None, date | None]:
    (found,) = parse(f"revenue in {period}", today=TODAY)
    return found.range.start, found.range.end


@pytest.mark.parametrize(("text", "a", "b", "kind", "b_range"), list(_cases()))
def test_two_periods_are_joined_as_the_words_say(
    text: str, a: str, b: str, kind: str, b_range: tuple[date, date] | None
) -> None:
    found = parse(text, today=TODAY)
    first, second = _alone(a), b_range or _alone(b)
    if kind == "range":
        assert [(m.range.start, m.range.end) for m in found] == [(first[0], second[1])]
        return
    assert [(m.range.start, m.range.end) for m in found] == [first, second]
    assert [(m.group, m.joined_by) for m in found] == [(0, None), (0, kind)]


@pytest.mark.parametrize(
    ("text", "dates"),
    [
        # "with" and "over" join nothing on their own.
        ("sales in Q1 with returns in Q2", ["2026-01-01", "2026-04-01"]),
        ("sales in Q1 spread over three regions in Q2", ["2026-01-01", "2026-04-01"]),
        ("Q1 2024 with LY", ["2024-01-01"]),
        # "between" alone is a span, and so is a change "from" one period "to" another.
        ("sales between 2023 and 2024", ["2023-01-01"]),
        ("growth from 2012 to 2013", ["2012-01-01"]),
        # "a year earlier" with nothing beside it counts from today; "2012 and prior
        # years" is everything up to 2012.
        ("a year earlier", ["2025-01-01"]),
        ("2012 and prior years", ["None"]),
        # "PY" on its own is not a year.
        ("see script.py for details", []),
    ],
)
def test_what_stays_as_it_was(text: str, dates: list[str]) -> None:
    assert [str(m.range.start) for m in parse(text, today=TODAY)] == dates


def test_the_same_period_with_nothing_beside_it_says_why() -> None:
    found, diags = diagnose("the same period last year", today=TODAY)
    assert found == []
    assert [d.rule for d in diags] == ["same_period"]
    assert "compared with" in diags[0].reason
