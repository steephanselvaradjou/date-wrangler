"""Property-based tests.

The example-based suites say the right things happen for inputs we thought of. These say
nothing *wrong* happens for inputs we did not -- which is the half that found the real bug:
"Q2 to Q1" resolved to the zero-width range ``[2025-07-01, 2025-07-01)``, because the
wrap-around check tested for "ends before it starts" and adjacency slipped through.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from date_wrangler import (
    Basis,
    DateOrder,
    FiscalCalendar,
    WranglerConfig,
    YearLabel,
    diagnose,
    format_iso,
    format_range,
    make_formatter,
    parse,
    substitute,
)
from date_wrangler.calendars import (
    fiscal_month_range,
    fiscal_year_of,
    fiscal_year_start,
    quarter_range,
    year_range,
)

SETTINGS = settings(
    max_examples=300,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

# Fragments real date text is built from, so generated strings look like something a
# person might type rather than uniform noise.
FRAGMENTS = [
    "Q1", "Q2", "q3", "Q4", "h1", "H2", "fy24", "FY2025", "cy2024", "2024", "'24",
    "march", "jan", "december", "sept", "to", "and", "-", "–", "—", "vs", "versus",
    "since", "before", "after", "as of", "up to", "until", "onwards", "through",
    "last", "next", "this", "past", "3", "12", "months", "quarter", "year", "week",
    "day", "ago", "ytd", "mtd", "qtd", "ttm", "L3M", "of", "the", "in", "for",
    ",", ".", "(", ")", "/", "monday", "friday", "ending", "ended", "today",
    "yesterday", "1st", "2nd", "twelfth", "quarter ending", "15", "31", "2024-03-15",
]

texts = st.one_of(
    st.text(max_size=80),
    st.lists(st.sampled_from(FRAGMENTS), min_size=1, max_size=12).map(" ".join),
)

configs = st.builds(
    WranglerConfig,
    fiscal=st.builds(
        FiscalCalendar,
        start_month=st.integers(min_value=1, max_value=12),
        label_by=st.sampled_from(list(YearLabel)),
    ),
    bare_period_basis=st.sampled_from(list(Basis)),
    date_order=st.sampled_from(list(DateOrder)),
    two_digit_pivot=st.integers(min_value=0, max_value=99),
    strictness=st.sampled_from(["strict", "balanced", "greedy"]),
)

todays = st.dates(min_value=date(1970, 1, 1), max_value=date(2100, 12, 31))


# ---------------------------------------------------------------------------
# Wrangler invariants
# ---------------------------------------------------------------------------


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_parse_never_raises(text, cfg, today):
    assert isinstance(parse(text, today=today, config=cfg), list)


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_spans_index_the_input_and_do_not_overlap(text, cfg, today):
    previous_end = 0
    for m in parse(text, today=today, config=cfg):
        lo, hi = m.span
        assert 0 <= lo <= hi <= len(text)
        assert text[lo:hi] == m.text
        assert lo >= previous_end, "matches must be disjoint and in order"
        previous_end = hi


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_every_range_is_usable(text, cfg, today):
    """No inverted range, no zero-width range, and never unbounded on both ends."""
    for m in parse(text, today=today, config=cfg):
        r = m.range
        assert not (r.start is None and r.end is None)
        if r.start is not None and r.end is not None:
            assert r.start < r.end, f"{r} is empty or inverted"
            assert r.days is not None and r.days > 0


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_half_open_contract_holds(text, cfg, today):
    for m in parse(text, today=today, config=cfg):
        r = m.range
        if r.start is not None:
            assert r.start in r
            assert r.start - timedelta(days=1) not in r
        if r.end is not None:
            assert r.end not in r, "the end of a half-open range is outside it"
            assert r.end - timedelta(days=1) in r


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_formatters_never_raise_and_never_return_empty(text, cfg, today):
    for m in parse(text, today=today, config=cfg):
        for render in (format_range, format_iso, make_formatter()):
            out = render(m.range)
            assert isinstance(out, str) and out


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_diagnostic_spans_are_in_bounds(text, cfg, today):
    _, diags = diagnose(text, today=today, config=cfg)
    for d in diags:
        lo, hi = d.span
        assert 0 <= lo <= hi <= len(text)


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_substitute_reaches_a_fixed_point(text, cfg, today):
    """Repeated substitution must settle, and must not grow without bound.

    One-pass idempotence does *not* hold in general, and cannot: substitution is textual,
    so the inserted phrase can fuse with a neighbouring token that was never part of a
    date. "15 Q1" becomes "15 April 2025 to June 2025", and re-reading that, "15 April
    2025" is a perfectly good date. Hypothesis found this via "1ST 1Q".

    What must hold is that the process terminates. The predecessor failed exactly here --
    its output began "period from ...", which it matched inside its own result, so every
    pass prepended another "period" forever.
    """
    seen = []
    current = text
    for _ in range(12):
        nxt = substitute(current, today=today, config=cfg)
        if nxt == current:
            break
        assert nxt not in seen, "substitution is cycling instead of converging"
        seen.append(nxt)
        current = nxt
    else:
        raise AssertionError(f"no fixed point after 12 passes: {text!r}")


def test_substitution_can_fuse_with_an_adjacent_number():
    """The known, documented limit of `substitute`, pinned so it stays known.

    Use `parse()` and render the ranges yourself when exactness matters.
    """
    today = date(2025, 9, 4)
    once = substitute("sales 15 Q1", today=today)
    assert once == "sales 15 April 2025 to June 2025"
    # "15 April 2025" now reads as a day, so a second pass differs -- but then settles.
    twice = substitute(once, today=today)
    assert twice == "sales April 2025 to June 2025"
    assert substitute(twice, today=today) == twice


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_parsing_is_deterministic(text, cfg, today):
    first = parse(text, today=today, config=cfg)
    second = parse(text, today=today, config=cfg)
    assert [(m.span, m.range) for m in first] == [(m.span, m.range) for m in second]


@given(text=texts, cfg=configs, today=todays)
@SETTINGS
def test_strict_never_finds_more_than_greedy(text, cfg, today):
    """Strictness only ever removes matches; it must not change what is found."""
    strict = parse(text, today=today, config=cfg.with_(strictness="strict"))
    balanced = parse(text, today=today, config=cfg.with_(strictness="balanced"))
    greedy = parse(text, today=today, config=cfg.with_(strictness="greedy"))
    assert len(strict) <= len(greedy)
    assert len(balanced) <= len(greedy)


# ---------------------------------------------------------------------------
# Calendar arithmetic invariants
# ---------------------------------------------------------------------------

calendars = st.builds(
    FiscalCalendar,
    start_month=st.integers(min_value=1, max_value=12),
    label_by=st.sampled_from(list(YearLabel)),
)
labels = st.integers(min_value=1900, max_value=2200)


@given(cal=calendars, label=labels)
@SETTINGS
def test_fiscal_year_round_trips(cal, label):
    assert fiscal_year_of(fiscal_year_start(label, cal), cal) == label


@given(cal=calendars, day=st.dates(min_value=date(1900, 1, 1), max_value=date(2200, 1, 1)))
@SETTINGS
def test_a_day_belongs_to_exactly_one_fiscal_year(cal, day):
    fy = fiscal_year_of(day, cal)
    assert day in year_range(fy, cal, Basis.FISCAL)
    assert day not in year_range(fy - 1, cal, Basis.FISCAL)
    assert day not in year_range(fy + 1, cal, Basis.FISCAL)


@given(cal=calendars, label=labels)
@SETTINGS
def test_quarters_tile_the_fiscal_year_exactly(cal, label):
    year = year_range(label, cal, Basis.FISCAL)
    quarters = [quarter_range(label, q, cal, Basis.FISCAL) for q in (1, 2, 3, 4)]
    assert quarters[0].start == year.start
    assert quarters[3].end == year.end
    for earlier, later in zip(quarters, quarters[1:], strict=False):
        assert earlier.end == later.start, "quarters must tile without gap or overlap"


@given(cal=calendars, label=labels)
@SETTINGS
def test_fiscal_months_tile_the_fiscal_year_exactly(cal, label):
    year = year_range(label, cal, Basis.FISCAL)
    months = [fiscal_month_range(label, i, cal) for i in range(1, 13)]
    assert months[0].start == year.start
    assert months[11].end == year.end
    for earlier, later in zip(months, months[1:], strict=False):
        assert earlier.end == later.start


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Q1 FY25", "January 2024", "15 January 2024", "Q1 to Q3 FY25", "since April 2024",
        "up to March 2024", "before 2024", "as of March 2024", "last 3 months",
        "2024-03-15", "this week", "last Monday", "TTM", "quarter ending June 2024",
    ],
)
@given(cfg=configs)
@SETTINGS
def test_rendered_ranges_reparse_to_themselves(text, cfg):
    """format_range must produce something the wrangler reads back identically.

    Without it, `substitute` corrupts text on a second pass -- the predecessor's output
    began "period from ...", which it then matched inside its own result.
    """
    today = date(2025, 9, 4)
    found = parse(text, today=today, config=cfg)
    if not found:
        return
    original = found[0].range
    again = parse(format_range(original), today=today, config=cfg)
    assert again, f"{format_range(original)!r} did not re-parse"
    assert (again[0].range.start, again[0].range.end) == (original.start, original.end)
