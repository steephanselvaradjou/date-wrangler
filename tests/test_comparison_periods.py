"""The periods a date is compared with: the one before it, and the same one a year back.

DateRange.previous() and DateRange.year_earlier() exist so a caller never builds them from
shift() and split() itself -- the arithmetic that goes wrong at month ends, leap days and
weeks. A week is the hard case: one on the calendar's grid goes back 52 weeks, keeping its
weekdays, while one counted from a period's first day takes the same place a year earlier.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from date_wrangler import (
    Basis,
    DateMatch,
    DateRange,
    Grain,
    WranglerConfig,
    parse,
    parse_one,
    tool,
)

TODAY = date(2026, 11, 17)  # a Tuesday; FY2027 on an April year
FISCAL = WranglerConfig(year_basis=Basis.FISCAL)


def rng(text, cfg=FISCAL):
    m = parse_one(text, today=TODAY, config=cfg)
    assert m is not None, text
    return m.range


def days(r):
    return r.start, r.end_inclusive


# ---------------------------------------------------------------------------
# previous()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,before",
    [
        ("Q1 FY26", (date(2025, 1, 1), date(2025, 3, 31))),  # Q4 FY25
        ("last 30 days", (date(2026, 9, 18), date(2026, 10, 17))),
        ("last 3 months", (date(2026, 5, 1), date(2026, 7, 31))),
        ("last week", (date(2026, 11, 2), date(2026, 11, 8))),
        ("2024", (date(2022, 4, 1), date(2023, 3, 31))),  # FY2023
        ("today", (date(2026, 11, 16), date(2026, 11, 16))),
        ("week 3 of FY25", (date(2024, 4, 8), date(2024, 4, 14))),  # week 2
        # Cut short: the same stretch of the period before.
        ("MTD", (date(2026, 10, 1), date(2026, 10, 17))),
        ("QTD", (date(2026, 7, 1), date(2026, 8, 17))),
        ("YTD", (date(2025, 4, 1), date(2025, 11, 17))),
        ("WTD", (date(2026, 11, 9), date(2026, 11, 10))),
    ],
)
def test_the_period_before(text, before):
    assert days(rng(text).previous()) == before


def test_whole_units_end_where_the_period_starts():
    for text in ["Q1 FY26", "last 30 days", "last 3 months", "rolling 3 months", "last week",
                 "H2 2024", "2024", "weeks 3-5 of FY25", "last 2 quarters", "March 2025"]:
        r = rng(text)
        p = r.previous()
        assert p.end == r.start, text
        assert p.grain is r.grain and p.basis is r.basis


def test_a_month_to_date_gives_the_days_the_month_before_has():
    """30 March is past the end of February: all of February, not the 1st to the 27th."""
    mtd = parse_one("MTD", today=date(2026, 3, 30)).range
    assert days(mtd.previous()) == (date(2026, 2, 1), date(2026, 2, 28))


def test_anything_else_gives_as_many_days_just_before():
    r = rng("15 March to last month")
    p = r.previous()
    assert p.end == r.start and p.days == r.days and p.grain is Grain.DAY


def test_an_open_range_has_no_period_before_it():
    with pytest.raises(ValueError, match="open-ended"):
        rng("since March").previous()


# ---------------------------------------------------------------------------
# year_earlier()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,earlier",
    [
        # On the calendar's grid: 52 weeks back, the same weekdays.
        ("W12 2026", (date(2025, 3, 17), date(2025, 3, 23))),
        ("last week", (date(2025, 11, 10), date(2025, 11, 16))),
        ("last 2 weeks", (date(2025, 11, 3), date(2025, 11, 16))),
        ("WTD", (date(2025, 11, 17), date(2025, 11, 18))),
        ("week ending 6 March 2026", (date(2025, 3, 1), date(2025, 3, 7))),
        # Counted from a period's first day: the same place a year earlier.
        ("week 3 of FY25", (date(2023, 4, 15), date(2023, 4, 21))),
        ("the 2nd week of January 2024", (date(2023, 1, 8), date(2023, 1, 14))),
        ("weeks 3-5 of FY25", (date(2023, 4, 15), date(2023, 5, 5))),
        ("fiscal week 3", (date(2025, 4, 15), date(2025, 4, 21))),
        # Everything else: the same dates.
        ("rolling 4 weeks", (date(2025, 10, 20), date(2025, 11, 16))),
        ("Q1 FY26", (date(2024, 4, 1), date(2024, 6, 30))),
        ("MTD", (date(2025, 11, 1), date(2025, 11, 17))),
        ("last 30 days", (date(2025, 10, 18), date(2025, 11, 16))),
        ("February 2025", (date(2024, 2, 1), date(2024, 2, 29))),
        ("29 February 2024", (date(2023, 2, 28), date(2023, 2, 28))),
    ],
)
def test_the_same_period_a_year_earlier(text, earlier):
    assert days(rng(text).year_earlier()) == earlier


def test_a_week_on_the_grid_keeps_its_weekdays():
    for text in ["W12 2026", "last week", "this week", "the week of 3 March 2026"]:
        r, e = rng(text), rng(text).year_earlier()
        assert e.start.weekday() == r.start.weekday() and r.start - e.start == timedelta(weeks=52)


def test_the_two_kinds_of_week_part_a_year_earlier_though_they_look_alike():
    """Both Monday 8 to Sunday 14 January 2024: one is the 2nd week of January, the other
    the week of 8 January. A year earlier the first is 8-14 January 2023, the second the
    Monday-to-Sunday week 52 weeks back."""
    calendar = WranglerConfig()  # where both weeks are on the calendar basis
    counted = rng("the 2nd week of January 2024", calendar)
    grid = rng("the week of 8 January 2024", calendar)
    assert days(counted) == days(grid) and counted == grid  # equal: the flag is not compared
    assert days(counted.year_earlier()) == (date(2023, 1, 8), date(2023, 1, 14))
    assert days(grid.year_earlier()) == (date(2023, 1, 9), date(2023, 1, 15))


@pytest.mark.parametrize(
    "text,today,earlier",
    [
        # REGRESSION: the exclusive end, 29 February, went back to the 28th -- a day short.
        ("28 February 2028", date(2028, 3, 5), (date(2027, 2, 28), date(2027, 2, 28))),
        ("yesterday", date(2028, 2, 29), (date(2027, 2, 28), date(2027, 2, 28))),
        ("YTD", date(2028, 2, 28), (date(2027, 1, 1), date(2027, 2, 28))),
        ("MTD", date(2028, 2, 28), (date(2027, 2, 1), date(2027, 2, 28))),
        ("the last 7 days", date(2028, 2, 29), (date(2027, 2, 22), date(2027, 2, 28))),
        ("1-28 February 2028", date(2028, 3, 5), (date(2027, 2, 1), date(2027, 2, 28))),
        # A month's last day stays its month's last.
        ("February 2029", date(2029, 3, 5), (date(2028, 2, 1), date(2028, 2, 29))),
        ("1-28 February 2029", date(2029, 3, 5), (date(2028, 2, 1), date(2028, 2, 29))),
    ],
)
def test_a_year_earlier_keeps_every_day_around_a_leap_day(text, today, earlier):
    r = parse_one(text, today=today).range
    assert days(r.year_earlier()) == earlier


def test_no_range_comes_back_empty_a_year_earlier():
    for start in (date(2028, 1, 1) + timedelta(days=k) for k in range(70)):
        for length in (1, 2, 7, 28, 29, 31):
            r = DateRange(start, start + timedelta(days=length), Grain.DAY)
            assert r.year_earlier().days in (length - 1, length, length + 1), r


def test_an_empty_range_has_no_period_before_it():
    with pytest.raises(ValueError, match="empty"):
        DateRange(date(2026, 3, 1), date(2026, 3, 1), Grain.MONTH).previous()


def test_previous_counts_units_rather_than_building_them():
    """REGRESSION: it built split() just to count, and took seconds over centuries."""
    import time

    t = time.perf_counter()
    r = DateRange(date(1500, 1, 1), date(2500, 1, 1), Grain.DAY).previous()
    assert r.end == date(1500, 1, 1) and r.days == 365243
    m = DateRange(date(1500, 1, 15), date(2500, 3, 1), Grain.MONTH).previous()
    assert m.end == date(1500, 1, 15)
    assert time.perf_counter() - t < 0.5


@pytest.mark.parametrize("grain", [Grain.MONTH, Grain.QUARTER, Grain.HALF, Grain.YEAR,
                                   Grain.WEEK, Grain.DAY])
def test_the_unit_count_is_the_number_of_buckets_split_cuts(grain):
    from date_wrangler.types import _units

    for start in (date(2024, 1, 31), date(2024, 2, 29), date(2025, 3, 15), date(2025, 12, 1)):
        for length in (1, 27, 28, 29, 31, 59, 90, 92, 181, 366, 400, 800):
            r = DateRange(start, start + timedelta(days=length), grain)
            assert _units(r.start, r.end, grain) == len(r.split(grain)), (r, grain)


def test_an_open_range_has_no_year_earlier():
    with pytest.raises(ValueError, match="open-ended"):
        rng("since March").year_earlier()


# ---------------------------------------------------------------------------
# counted_in_period
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,counted",
    [
        ("week 3 of FY25", True), ("fiscal week 3", True), ("the 2nd week of January", True),
        ("week 10 of Q1 2025", True), ("the last week of March", True),
        ("weeks 3-5 of FY25", True), ("the first 2 months of FY25", True),
        ("W12 2026", False), ("week 12", False), ("last week", False), ("this week", False),
        ("the week of 3 March", False), ("rolling 4 weeks", False), ("Q1", False),
    ],
)
def test_which_ranges_are_counted_in_a_period(text, counted):
    assert rng(text).counted_in_period is counted


def test_the_flag_survives_json_only_where_it_is_set():
    counted, plain = rng("week 3 of FY25"), rng("W12 2026")
    assert counted.to_dict()["counted_in_period"] is True
    assert "counted_in_period" not in plain.to_dict()  # every other dict is as before
    back = DateRange.from_dict(json.loads(json.dumps(counted.to_dict())))
    assert back.counted_in_period and days(back.year_earlier()) == days(counted.year_earlier())
    m = parse("week 3 of FY25", today=TODAY, config=FISCAL)[0]
    assert DateMatch.from_dict(json.loads(json.dumps(m.to_dict()))).range.counted_in_period


def test_shift_keeps_the_flag():
    assert rng("week 3 of FY25").shift(-1).counted_in_period


# ---------------------------------------------------------------------------
# In the tool's answer
# ---------------------------------------------------------------------------


def test_comparisons_are_left_out_unless_asked_for():
    result = tool.resolve_dates("Q1 FY26", today=TODAY.isoformat())
    assert "comparisons" not in result["matches"][0]


def test_comparisons_sit_inside_each_match_under_their_own_names():
    result = tool.resolve_dates("Q1 FY26 vs last 30 days", today=TODAY.isoformat(),
                                comparisons=True)
    q1, last30 = result["matches"]
    assert q1["comparisons"]["previous"]["first_day"] == "2025-01-01"
    assert q1["comparisons"]["previous"]["last_day"] == "2025-03-31"
    assert q1["comparisons"]["year_earlier"]["first_day"] == "2024-04-01"
    assert last30["comparisons"]["previous"]["last_day"] == "2026-10-17"
    assert len(result["matches"]) == 2  # never extra matches


def test_an_open_range_compares_with_nothing():
    (m,) = tool.resolve_dates("since March", today=TODAY.isoformat(),
                              comparisons=True)["matches"]
    assert m["comparisons"] == {"previous": None, "year_earlier": None}


def test_call_passes_the_hosts_switch():
    result = tool.call({"text": "week 3 of FY25", "today": TODAY.isoformat()}, comparisons=True)
    assert result["matches"][0]["comparisons"]["year_earlier"]["first_day"] == "2023-04-15"
    assert result["matches"][0]["range"]["counted_in_period"] is True


def test_the_answer_with_comparisons_matches_the_output_schema():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(
        tool.OUTPUT_SCHEMA, format_checker=jsonschema.FormatChecker()
    )
    for text in ["Q1 FY26", "since March", "week 3 of FY25 and W12", "last 30 days", "nothing"]:
        validator.validate(tool.resolve_dates(text, today=TODAY.isoformat(), comparisons=True))
