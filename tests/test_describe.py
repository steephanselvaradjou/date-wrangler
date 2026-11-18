"""describe(config): how dates are read, in plain words for a model's prompt.

The words are only worth having if they are true, so every example in them -- a phrase in
quotes and what it "is" -- is checked against what the library reads that phrase as, under
the same configuration on the same day, for many configurations and days.
"""

from __future__ import annotations

import itertools
import re
from datetime import date

import pytest

from date_wrangler import (
    Anchor,
    Basis,
    DateOrder,
    FiscalCalendar,
    MonthNumber,
    WranglerConfig,
    YearLabel,
    describe,
    format_range,
    parse_one,
)

TODAY = date(2026, 11, 17)

#: A quoted phrase and its reading, which ends where the sentence or clause does.
EXAMPLE = re.compile(r'"([^"]+)" is (.+?)(?=, as "|, and "| and "|\. |\.$)')

FINANCE = WranglerConfig(fiscal=FiscalCalendar(4), bare_period_basis=Basis.FISCAL,
                         year_basis=Basis.FISCAL, of_year_basis=Basis.CALENDAR)


def configs():
    yield FINANCE
    yield WranglerConfig()
    for start, label, basis, order, anchor, number in itertools.product(
        (1, 2, 4, 7, 10, 12), YearLabel, (Basis.CALENDAR, Basis.FISCAL), DateOrder,
        Anchor, MonthNumber,
    ):
        yield WranglerConfig(fiscal=FiscalCalendar(start, label), year_basis=basis,
                             date_order=order, anchor=anchor, month_number=number)
    for week, pivot in ((6, 29), (5, 0), (0, 99)):
        yield WranglerConfig(week_starts_on=week, two_digit_pivot=pivot, weekend=(4, 5),
                             holidays=frozenset({date(2026, 12, 25)}))


DAYS = [TODAY, date(2026, 1, 1), date(2026, 3, 31), date(2026, 12, 31), date(2024, 2, 29)]


@pytest.mark.parametrize("day", DAYS)
def test_every_example_is_what_the_library_reads(day):
    for cfg in configs():
        text = describe(cfg, today=day)
        pairs = EXAMPLE.findall(text)
        assert len(pairs) >= 10, (cfg, text)
        for phrase, reading in pairs:
            m = parse_one(phrase, today=day, config=cfg)
            assert m is not None and m.text == phrase, (phrase, cfg)
            assert format_range(m.range) == reading, (phrase, reading, cfg, day)
        assert '""' not in text and ": ." not in text and "  " not in text


def test_the_finance_workspace():
    text = describe(FINANCE, today=TODAY)
    assert text.startswith("A year here is the fiscal year, April to March, named by the year "
                           'it ends in: "2024" is April 2023 to March 2024, as "FY2024" is.')
    assert 'A half or quarter "of" a year is the calendar one: "H1 of 2025" is January 2025 ' \
           "to June 2025." in text
    assert '"January" is January 2027' in text  # in the fiscal year in progress
    assert '"since December" is December 2025 onwards' in text
    assert 'All-number dates are day first: "08/04/2026" is 8 April 2026.' in text
    assert '"the 2nd week of January 2026" is 8 January 2026 to 14 January 2026' in text
    assert '"last 3 months" is August 2026 to October 2026' in text


def test_a_calendar_workspace_says_what_a_fiscal_year_is():
    text = describe(today=TODAY)
    assert text.startswith('A year here is the calendar year: "2024" is January 2024 to '
                           "December 2024")
    assert 'A fiscal year runs April to March and is named by the year it ends in: "FY2024" ' \
           "is April 2023 to March 2024." in text
    assert '"of" a year' not in text  # said only when it differs from year_basis


def test_the_settings_each_change_the_words():
    us = WranglerConfig(fiscal=FiscalCalendar(10, YearLabel.START_YEAR),
                        date_order=DateOrder.MDY, week_starts_on=6, anchor=Anchor.ROLLING,
                        month_number=MonthNumber.YEAR, two_digit_pivot=29, weekend=(4, 5))
    text = describe(us, today=TODAY)
    assert "named by the year it starts in" in text
    assert '"08/04/2026" is 4 August 2026' in text
    assert "Weeks start on Sunday" in text
    assert '"Last 3 months" counts back from today' in text
    assert '"Jan 24" is January 2024' in text
    assert "'29 is 20xx" in text
    assert "Business days skip Friday and Saturday." in text


@pytest.mark.parametrize(
    "cfg",
    [WranglerConfig(strictness="strict"), WranglerConfig(strictness="strict",
                                                          year_basis=Basis.FISCAL)],
)
@pytest.mark.parametrize("day", [TODAY, date(9999, 12, 1), date(1, 1, 1), date(9999, 12, 31)])
def test_an_example_the_library_does_not_read_is_left_out_whole(cfg, day):
    """REGRESSION: under strictness="strict" a bare month is not read, and the sentence
    was written around the gap: 'the calendar year: , and'. Near either end of the
    calendar the next month did not exist and it raised."""
    text = describe(cfg, today=day)
    assert '""' not in text and ": ." not in text and ", ." not in text and "  " not in text
    assert ": ," not in text and " and ." not in text and ": and" not in text
    for phrase, reading in EXAMPLE.findall(text):
        m = parse_one(phrase, today=day, config=cfg)
        assert m is not None and format_range(m.range) == reading


def test_it_defaults_to_today_and_the_library_defaults():
    assert describe() == describe(WranglerConfig(), today=date.today())
