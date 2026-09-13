"""Operations on a resolved range: splitting it into buckets and shifting it by periods.

Parsing hands back one range. What a report does next is almost always one of two things --
group it by a finer grain, or compare it against the same period earlier -- and both are
date arithmetic that is easy to get subtly wrong by hand. These tests pin the edges that
the hand-rolled version gets wrong: short months, leap days, fiscal boundaries and the ends
of the supported calendar.
"""

from __future__ import annotations

from datetime import MAXYEAR, date, timedelta
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from date_wrangler import (
    Anchor,
    Basis,
    DateRange,
    DateRangeOverflow,
    Grain,
    Mod,
    WranglerConfig,
    parse_one,
)

TODAY = date(2025, 9, 4)
#: A bare "Q1 2024" is fiscal by default. Where a test is about the arithmetic rather than
#: the calendar, saying so outright keeps the expected dates readable.
CALENDAR = WranglerConfig(bare_period_basis=Basis.CALENDAR)


def rng(text, cfg=None):
    m = parse_one(text, today=TODAY, config=cfg or WranglerConfig())
    assert m is not None, f"{text!r} found no date"
    return m.range


def spans(buckets):
    return [(b.start, b.end) for b in buckets]


# ---------------------------------------------------------------------------
# split: the parsed phrase becomes a GROUP BY axis
# ---------------------------------------------------------------------------


def test_split_a_parsed_phrase_into_buckets():
    assert spans(rng("last 3 months").split(Grain.MONTH)) == [
        (date(2025, 6, 1), date(2025, 7, 1)),
        (date(2025, 7, 1), date(2025, 8, 1)),
        (date(2025, 8, 1), date(2025, 9, 1)),
    ]


def test_split_a_fiscal_year_into_its_own_quarters():
    """The grid starts at the range's own start, which for a fiscal year is the fiscal
    boundary -- so this needs no fiscal calendar passed in to come out right."""
    assert spans(rng("FY25").split(Grain.QUARTER)) == [
        (date(2024, 4, 1), date(2024, 7, 1)),
        (date(2024, 7, 1), date(2024, 10, 1)),
        (date(2024, 10, 1), date(2025, 1, 1)),
        (date(2025, 1, 1), date(2025, 4, 1)),
    ]


def test_split_a_month_into_days():
    days = rng("March 2024").split(Grain.DAY)
    assert len(days) == 31
    assert days[0].start == date(2024, 3, 1)
    assert days[-1].end == date(2024, 4, 1)
    assert all(b.days == 1 for b in days)


def test_split_a_quarter_into_weeks():
    """A quarter is not a whole number of weeks, so the buckets run from its own start and
    the last one is short."""
    q = rng("Q1 2024", CALENDAR)
    weeks = q.split(Grain.WEEK)
    assert weeks[0].start == q.start == date(2024, 1, 1)
    assert all(b.days == 7 for b in weeks[:-1])
    assert weeks[-1].end == q.end == date(2024, 4, 1)


@pytest.mark.parametrize(
    "grain,expected",
    [
        (Grain.MONTH, 12),
        (Grain.QUARTER, 4),
        (Grain.HALF, 2),
        (Grain.YEAR, 1),
    ],
)
def test_a_year_holds_a_whole_number_of_every_coarse_grain(grain, expected):
    assert len(rng("2024").split(grain)) == expected


def test_buckets_tile_the_range_exactly():
    """Half-open is what makes this work: each bucket's end is the next one's start, so
    they partition the range with no gap and no double-counted day."""
    r = rng("2024")
    buckets = r.split(Grain.MONTH)
    assert buckets[0].start == r.start
    assert buckets[-1].end == r.end
    for earlier, later in pairwise(buckets):
        assert earlier.end == later.start


def test_buckets_generate_usable_sql():
    first = rng("last 3 months").split(Grain.MONTH)[0]
    assert first.sql("order_date") == (
        "order_date >= '2025-06-01' AND order_date < '2025-07-01'"
    )


# ---- split: the awkward shapes -------------------------------------------


def test_only_the_last_bucket_is_ever_short():
    """"The last 45 days" does not divide into months. The remainder lands at the end,
    where `days` makes it obvious, rather than being rounded away."""
    r = DateRange(date(2025, 7, 21), date(2025, 9, 4), Grain.DAY)
    buckets = r.split(Grain.MONTH)
    assert spans(buckets) == [
        (date(2025, 7, 21), date(2025, 8, 21)),
        (date(2025, 8, 21), date(2025, 9, 4)),
    ]
    assert buckets[-1].days == 14


def test_the_grid_never_reaches_outside_the_range():
    """Snapping outwards to calendar month starts would make the first bucket begin on
    1 July -- ten days the caller never asked about."""
    r = DateRange(date(2025, 7, 21), date(2025, 9, 4), Grain.DAY)
    buckets = r.split(Grain.MONTH)
    assert buckets[0].start == r.start
    assert buckets[-1].end == r.end


def test_month_end_boundaries_do_not_drift():
    """REGRESSION-SHAPED: stepping the cursor a month at a time clamps 31 Jan to 28 Feb
    and then never recovers, so every later bucket is three days early. Each boundary is
    measured from the range start instead."""
    r = DateRange(date(2025, 1, 31), date(2025, 6, 30), Grain.DAY)
    assert [b.start for b in r.split(Grain.MONTH)] == [
        date(2025, 1, 31),
        date(2025, 2, 28),
        date(2025, 3, 31),
        date(2025, 4, 30),
        date(2025, 5, 31),
    ]


def test_a_range_shorter_than_one_bucket_is_one_short_bucket():
    day = rng("yesterday")
    buckets = day.split(Grain.MONTH)
    assert len(buckets) == 1
    assert (buckets[0].start, buckets[0].end) == (day.start, day.end)


def test_splitting_by_a_coarser_grain_truncates_rather_than_widens():
    buckets = rng("March 2024").split(Grain.YEAR)
    assert spans(buckets) == [(date(2024, 3, 1), date(2024, 4, 1))]


def test_an_empty_range_splits_into_nothing():
    empty = DateRange(date(2025, 1, 1), date(2025, 1, 1), Grain.DAY)
    assert empty.is_empty
    assert empty.split(Grain.DAY) == []


def test_splitting_an_unbounded_range_raises():
    since = rng("since March")
    assert since.end is None
    with pytest.raises(ValueError, match="clamp"):
        since.split(Grain.MONTH)


def test_clamping_first_makes_an_unbounded_range_splittable():
    since = rng("since March").clamp(hi=date(2025, 6, 1))
    assert len(since.split(Grain.MONTH)) == 3


def test_split_near_the_end_of_the_calendar_stops_at_the_range_end():
    """The next boundary would be year 10000. That is not an error here -- the bucket is
    simply truncated, the same as any other short final bucket."""
    r = DateRange(date(MAXYEAR, 1, 1), date(MAXYEAR, 12, 31), Grain.YEAR)
    assert spans(r.split(Grain.YEAR)) == [(date(MAXYEAR, 1, 1), date(MAXYEAR, 12, 31))]


def test_split_rejects_a_non_grain():
    """Grain is a str enum, so "month" is accepted as the real thing. Anything else is not."""
    assert rng("2024").split("month") == rng("2024").split(Grain.MONTH)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not a Grain"):
        rng("2024").split("fortnight")  # type: ignore[arg-type]


# ---- split: what the buckets carry ---------------------------------------


def test_buckets_take_the_target_grain_and_keep_the_basis():
    buckets = rng("FY25").split(Grain.QUARTER)
    assert all(b.grain is Grain.QUARTER for b in buckets)
    assert all(b.basis is Basis.FISCAL for b in buckets)


def test_buckets_carry_the_anchor_but_drop_the_mod():
    """A bucket is bounded at both ends by construction, so an open-ended qualifier on the
    range it came from no longer describes it."""
    r = rng("since March").clamp(hi=date(2025, 6, 1))
    assert r.mod is Mod.SINCE
    buckets = r.split(Grain.MONTH)
    assert all(b.mod is None for b in buckets)

    rolling = rng("last month", WranglerConfig(anchor=Anchor.ROLLING))
    assert all(b.anchor is Anchor.ROLLING for b in rolling.split(Grain.WEEK))


# ---------------------------------------------------------------------------
# shift: period over period
# ---------------------------------------------------------------------------


def test_shift_defaults_to_the_ranges_own_grain():
    q = rng("Q1 FY25")
    assert (q.start, q.end) == (date(2024, 4, 1), date(2024, 7, 1))
    assert (q.shift(-1).start, q.shift(-1).end) == (date(2024, 1, 1), date(2024, 4, 1))
    assert q.shift(1).start == date(2024, 7, 1)
    assert q.shift(0) == q


def test_shift_by_a_year_is_the_comparison_everyone_wants():
    q = rng("Q1 FY25")
    assert (q.shift(-1, Grain.YEAR).start, q.shift(-1, Grain.YEAR).end) == (
        date(2023, 4, 1),
        date(2023, 7, 1),
    )


def test_a_shifted_fiscal_quarter_lands_on_a_fiscal_boundary():
    """Q1 of a March-ending fiscal year is Apr-Jun. One step back is fiscal Q4 of the year
    before -- Jan-Mar -- not three calendar months of some other quarter."""
    assert rng("Q1 FY25").shift(-1) == rng("Q4 FY24")


def test_shift_agrees_with_the_phrase_that_says_the_same_thing():
    assert rng("this quarter").shift(-1, Grain.YEAR) == rng("same quarter last year")


@pytest.mark.parametrize(
    "text,grain,periods,start,end",
    [
        ("March 2024", Grain.MONTH, -1, date(2024, 2, 1), date(2024, 3, 1)),
        ("March 2024", Grain.MONTH, 1, date(2024, 4, 1), date(2024, 5, 1)),
        ("2024", Grain.YEAR, -1, date(2023, 1, 1), date(2024, 1, 1)),
        ("Q1 2024", Grain.QUARTER, -1, date(2023, 10, 1), date(2024, 1, 1)),
        ("H1 2024", Grain.HALF, -1, date(2023, 7, 1), date(2024, 1, 1)),
        ("15 March 2024", Grain.DAY, -1, date(2024, 3, 14), date(2024, 3, 15)),
        ("15 March 2024", Grain.WEEK, -1, date(2024, 3, 8), date(2024, 3, 9)),
    ],
)
def test_shift_at_every_grain(text, grain, periods, start, end):
    shifted = rng(text, CALENDAR).shift(periods, grain)
    assert (shifted.start, shifted.end) == (start, end)


def test_shift_keeps_the_length_where_hand_arithmetic_loses_it():
    """A quarter is 90 to 92 days, so `- timedelta(days=90)` drifts a little further out
    of alignment on every step."""
    for label in ("Q1 2024", "Q2 2024", "Q3 2024", "Q4 2024"):
        q = rng(label)
        assert q.shift(-4, Grain.QUARTER) == rng(label.replace("2024", "2023"))


def test_shift_across_a_leap_year():
    """`- timedelta(days=365)` from 1 March 2025 lands on 1 March 2024 only in a common
    year; 2024 has a 29 February, so the naive version is a day out."""
    feb = rng("February 2024")
    assert (feb.start, feb.end) == (date(2024, 2, 1), date(2024, 3, 1))
    assert (feb.shift(1, Grain.YEAR).start, feb.shift(1, Grain.YEAR).end) == (
        date(2025, 2, 1),
        date(2025, 3, 1),
    )
    assert feb.days == 29
    assert feb.shift(1, Grain.YEAR).days == 28


def test_shift_off_a_leap_day_clamps_instead_of_raising():
    """`date(2024, 2, 29).replace(year=2023)` raises ValueError. This does not."""
    leap_day = DateRange(date(2024, 2, 29), date(2024, 3, 1), Grain.DAY)
    back = leap_day.shift(-1, Grain.YEAR)
    assert (back.start, back.end) == (date(2023, 2, 28), date(2023, 3, 1))


def test_shift_leaves_an_unbounded_end_unbounded():
    since = rng("since March")
    moved = since.shift(-1)
    assert moved.start == date(2025, 2, 1)
    assert moved.end is None
    assert moved.mod is Mod.SINCE


def test_shift_preserves_grain_basis_and_anchor():
    rolling = rng("last month", WranglerConfig(anchor=Anchor.ROLLING))
    moved = rolling.shift(-1)
    assert moved.grain is rolling.grain
    assert moved.basis is rolling.basis
    assert moved.anchor is Anchor.ROLLING
    assert moved.days == rolling.days


def test_shift_past_the_supported_calendar_raises_rather_than_crashing():
    with pytest.raises(DateRangeOverflow):
        rng("2024").shift(-10_000, Grain.YEAR)
    with pytest.raises(DateRangeOverflow):
        rng("2024").shift(9_000, Grain.YEAR)
    with pytest.raises(DateRangeOverflow):
        DateRange(date(MAXYEAR, 12, 30), date(MAXYEAR, 12, 31), Grain.DAY).shift(10)


def test_shift_rejects_a_non_grain():
    with pytest.raises(ValueError, match="not a Grain"):
        rng("2024").shift(-1, "fortnight")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The two together, and properties that should hold for any range at all
# ---------------------------------------------------------------------------


def test_split_then_shift_gives_the_prior_year_axis():
    """The shape a year-on-year chart actually needs: one bucket per month, each paired
    with the same month a year earlier."""
    this_year = rng("2024").split(Grain.MONTH)
    pairs = [(b, b.shift(-1, Grain.YEAR)) for b in this_year]
    assert len(pairs) == 12
    assert (pairs[0][1].start, pairs[0][1].end) == (date(2023, 1, 1), date(2023, 2, 1))
    assert (pairs[-1][1].start, pairs[-1][1].end) == (date(2023, 12, 1), date(2024, 1, 1))
    for current, prior in pairs:
        assert prior.start.month == current.start.month
        assert prior.start.year == current.start.year - 1


BOUNDED = st.builds(
    lambda start, length, grain, basis: DateRange(
        start, start + timedelta(days=length), grain, basis
    ),
    start=st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 1, 1)),
    length=st.integers(min_value=0, max_value=800),
    grain=st.sampled_from(list(Grain)),
    basis=st.sampled_from(list(Basis)),
)
GRAINS = st.sampled_from(list(Grain))


@settings(max_examples=300, deadline=None)
@given(r=BOUNDED, grain=GRAINS)
def test_buckets_always_partition_the_range(r, grain):
    buckets = r.split(grain)
    if r.is_empty:
        assert buckets == []
        return
    assert buckets[0].start == r.start
    assert buckets[-1].end == r.end
    for earlier, later in pairwise(buckets):
        assert earlier.end == later.start
    assert sum(b.days for b in buckets) == r.days
    assert all(not b.is_empty for b in buckets)


@settings(max_examples=300, deadline=None)
@given(r=BOUNDED, grain=GRAINS, periods=st.integers(min_value=-40, max_value=40))
def test_shift_is_reversible_and_never_inverts(r, grain, periods):
    moved = r.shift(periods, grain)
    assert moved.end >= moved.start
    # Month arithmetic clamps, so a range starting after the 28th is not guaranteed to
    # come back to exactly where it began; everything else is.
    if r.start.day <= 28 and r.end.day <= 28:
        assert moved.shift(-periods, grain) == r
