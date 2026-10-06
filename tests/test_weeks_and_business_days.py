"""Numbered weeks and working days -- the two calendars a reporting date is often written in.

Both are new parsing domains, so both come with the same thing every new recogniser here
gets: a hunt for ordinary text it would now claim wrongly. "W42" is as often a part number
as a week, and "a working day" is a phrase, not a date.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from date_wrangler import DateRange, Grain, WranglerConfig, diagnose, parse, parse_one
from date_wrangler.calendars import (
    add_business_days,
    count_business_days,
    is_business_day,
    iso_week_range,
)

THU = date(2026, 10, 15)  # ISO 2026-W42, a Thursday
MON = date(2026, 10, 19)  # a weekend sits directly behind it


def rng(text, today=THU, cfg=None):
    m = parse_one(text, today=today, config=cfg or WranglerConfig())
    assert m is not None, f"{text!r} found no date"
    return m.range


def span(text, today=THU, cfg=None):
    r = rng(text, today, cfg)
    return r.start, r.end


# ---------------------------------------------------------------------------
# ISO weeks
# ---------------------------------------------------------------------------

W42 = (date(2026, 10, 12), date(2026, 10, 19))


@pytest.mark.parametrize(
    "text",
    [
        "2026-W42", "2026W42", "week 42", "wk 42", "CW42", "KW 42", "calendar week 42",
        "week 42 of 2026", "week 42, 2026", "in W42",
    ],
)
def test_the_ways_people_write_a_week_number(text):
    assert span(text) == W42


def test_week_42_comma_2026_is_one_match_not_two():
    """A spreadsheet heading. The comma once split it into a week and a stray year."""
    assert len(parse("week 42, 2026", today=THU)) == 1


def test_a_week_number_is_always_monday_to_sunday():
    """ISO defines a numbered week; renumbering from Sunday would make one label mean
    different days under different configurations."""
    assert span("week 42", cfg=WranglerConfig(week_starts_on=6)) == W42


def test_week_one_can_start_in_the_previous_calendar_year():
    assert span("week 1 2026") == (date(2025, 12, 29), date(2026, 1, 5))


def test_a_bare_week_takes_the_iso_year_not_the_calendar_year():
    """On 1 January 2027 the current week is still week 53 of *2026*."""
    assert span("week 53", today=date(2027, 1, 1)) == (date(2026, 12, 28), date(2027, 1, 4))


def test_a_week_that_does_not_exist_is_refused_not_rounded():
    matches, diags = diagnose("week 53 of 2021", today=THU)
    assert matches == []
    assert any("no week 53" in d.reason for d in diags)


@pytest.mark.parametrize("text", ["week 0", "week 54", "week 99", "2026-W00", "W999"])
def test_out_of_range_week_numbers_are_not_weeks(text):
    assert parse(text, today=THU) == []


@pytest.mark.parametrize("text", ["part W42", "room W12", "bus W8", "model W42 spec", "w42"])
def test_a_bare_w_number_needs_a_cue(text):
    """Weak like a bare month: an uppercase W and two digits is as likely a part number,
    and lower case "w42" is never read at all."""
    assert parse(text, today=THU) == []


def test_iso_week_range_directly():
    assert iso_week_range(2026, 42) == DateRange(*W42, Grain.WEEK)
    with pytest.raises(ValueError):
        iso_week_range(2026, 0)


# ---------------------------------------------------------------------------
# Business days
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,day",
    [
        ("5 business days ago", date(2026, 10, 8)),
        ("in 3 business days", date(2026, 10, 20)),
        ("3 working days from now", date(2026, 10, 20)),
        ("next business day", date(2026, 10, 16)),
        ("last business day of the month", date(2026, 10, 30)),
        ("first working day of next month", date(2026, 11, 2)),  # 1 Nov is a Sunday
        ("last business day of last month", date(2026, 9, 30)),
    ],
)
def test_a_single_business_day(text, day):
    assert span(text) == (day, day + timedelta(days=1))


def test_first_working_day_of_a_relative_period():
    """REGRESSION: one flag meant both "count working days" and "land on a working day",
    and a relative target like "next month" is itself counted -- so "first working day of
    next month" came back as the next working day, 16 October."""
    assert rng("first working day of next month").start == date(2026, 11, 2)


def test_a_window_keeps_the_weekends_inside_it():
    """A date filter has to be contiguous. `business_days` says how many count."""
    r = rng("last 10 business days")
    assert (r.start, r.end) == (date(2026, 10, 1), date(2026, 10, 15))
    assert r.days == 14
    assert r.business_days() == 10


def test_a_window_never_ends_on_a_weekend():
    """Asked on a Monday, "last business day" is Friday -- not Friday to Sunday, which is
    what ending the window at today would give."""
    assert span("last business day", today=MON) == (date(2026, 10, 16), date(2026, 10, 17))
    assert span("last 3 business days", today=MON) == (date(2026, 10, 14), date(2026, 10, 17))


def test_holidays_and_another_weekend_are_honoured():
    gulf = WranglerConfig(weekend=(4, 5), holidays=frozenset({date(2026, 10, 14)}))
    assert rng("next business day", cfg=gulf).start == date(2026, 10, 18)  # Sunday
    assert span("last 3 business days", cfg=gulf) == (date(2026, 10, 11), date(2026, 10, 14))


def test_business_days_on_a_range():
    october = rng("October 2026")
    assert october.business_days() == 22
    assert october.business_days(holidays={date(2026, 10, 2), date(2026, 10, 12)}) == 20
    assert rng("since March").business_days() is None


@pytest.mark.parametrize(
    "text",
    [
        "a working day", "working days are long", "business days matter",
        "the trading day ended", "a hard day at work", "the working day is 8 hours",
        "it takes 3 business days to clear", "5 working days notice period",
    ],
)
def test_business_day_phrases_that_are_not_dates(text):
    assert parse(text, today=THU) == []


# ---- the arithmetic underneath --------------------------------------------


def test_add_business_days_steps_over_weekends():
    assert add_business_days(THU, 1) == date(2026, 10, 16)
    assert add_business_days(THU, 2) == date(2026, 10, 19)
    assert add_business_days(MON, -1) == date(2026, 10, 16)


def test_zero_business_days_snaps_forward_off_a_weekend():
    assert add_business_days(date(2026, 10, 17), 0) == MON
    assert add_business_days(THU, 0) == THU


def test_counting_agrees_with_walking_day_by_day():
    """The counter works a week at a time, so check it against the obvious slow one --
    across odd weekends and holidays, including holidays that fall on a weekend."""
    rnd = random.Random(7)
    for _ in range(1500):
        start = date(2020, 1, 1) + timedelta(days=rnd.randint(0, 3000))
        end = start + timedelta(days=rnd.randint(0, 400))
        weekend = tuple(rnd.sample(range(7), rnd.randint(0, 3)))
        holidays = [start + timedelta(days=rnd.randint(-5, 420)) for _ in range(rnd.randint(0, 6))]
        slow = sum(
            is_business_day(start + timedelta(days=i), weekend, holidays)
            for i in range((end - start).days)
        )
        assert count_business_days(start, end, weekend, holidays) == slow


def test_config_rejects_a_week_with_no_working_days():
    with pytest.raises(ValueError, match="at least one working day"):
        WranglerConfig(weekend=tuple(range(7)))
    with pytest.raises(ValueError, match="0-6"):
        WranglerConfig(weekend=(7,))
    with pytest.raises(TypeError, match="datetime.date"):
        WranglerConfig(holidays=frozenset({"2026-10-12"}))  # type: ignore[arg-type]


def test_holidays_given_as_a_list_still_make_a_hashable_config():
    cfg = WranglerConfig(holidays=[date(2026, 10, 12)])  # type: ignore[arg-type]
    assert isinstance(cfg.holidays, frozenset)
    assert hash(cfg) is not None
