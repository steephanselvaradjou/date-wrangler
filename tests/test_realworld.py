"""Dates as they appear outside a BI dashboard.

The rest of the suite grew out of reporting phrases -- quarters, fiscal years, YTD -- which
is a narrow slice of how dates actually get written. These cases come from contracts,
clinical notes, news copy, travel bookings, HR records, chat, log lines and data-entry
forms, and they are paired with the thing that matters more in general text: the numbers
that look like dates and are not.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import DateOrder, WranglerConfig, parse, parse_one

TODAY = date(2025, 9, 4)  # a Thursday


def first(text, cfg=None):
    m = parse_one(text, today=TODAY, config=cfg or WranglerConfig())
    assert m is not None, f"{text!r} found no date"
    return m.range.start, m.range.end


# ---------------------------------------------------------------------------
# Written formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,day",
    [
        ("15/03/2024", date(2024, 3, 15)),
        ("2024-03-15", date(2024, 3, 15)),
        ("2024/03/15", date(2024, 3, 15)),
        ("15.03.2024", date(2024, 3, 15)),
        ("15 March 2024", date(2024, 3, 15)),
        ("March 15, 2024", date(2024, 3, 15)),
        ("15th March 2024", date(2024, 3, 15)),
        ("15-Mar-2024", date(2024, 3, 15)),
        ("15-Mar-24", date(2024, 3, 15)),
        ("15 Mar 24", date(2024, 3, 15)),
        ("1 Jan 2026", date(2026, 1, 1)),
    ],
)
def test_written_date_formats(text, day):
    assert first(text) == (day, day + (date(2025, 1, 2) - date(2025, 1, 1)))


def test_dashed_month_is_a_month():
    """"Mar-24" is a spreadsheet column heading; the hyphen settles the jan-24 ambiguity."""
    assert first("Mar-24") == (date(2024, 3, 1), date(2024, 4, 1))
    assert first("Mar-2024") == (date(2024, 3, 1), date(2024, 4, 1))


@pytest.mark.parametrize(
    "text",
    [
        "2024-03-15T14:30:00Z",
        "2024-03-15T14:30:00+05:30",
        "[2024-03-15 14:30:00] INFO started",
        "expires=2025-12-31",
    ],
)
def test_iso_timestamps_in_log_lines(text):
    """REGRESSION: the trailing \\b meant an ISO instant ending in "T" never matched, so
    every timestamp in a log file was invisible."""
    assert parse(text, today=TODAY) != []


def test_syslog_timestamp():
    """"Mar 15 14:30:00" -- the month_day_year guard refuses a following number."""
    assert first("Mar 15 14:30:00 host sshd[1234]: accepted") == (
        date(2025, 3, 15), date(2025, 3, 16)
    )


def test_rfc_2822_header():
    assert first("Last-Modified: Wed, 15 Mar 2024 14:30:00 GMT") == (
        date(2024, 3, 15), date(2024, 3, 16)
    )


def test_date_order_config_applies_to_ambiguous_numeric():
    mdy = WranglerConfig(date_order=DateOrder.MDY)
    assert first("03/04/2024") == (date(2024, 4, 3), date(2024, 4, 4))
    assert first("03/04/2024", mdy) == (date(2024, 3, 4), date(2024, 3, 5))


# ---------------------------------------------------------------------------
# Domains other than finance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("This Agreement is effective from 1 January 2024.", date(2024, 1, 1)),
        ("The term expires on 31 December 2026.", date(2026, 12, 31)),
        ("Notice must be given no later than 30 September.", date(2025, 9, 30)),
        ("Patient DOB: 12/03/1985", date(1985, 3, 12)),
        ("Last dose administered on 2024-03-15.", date(2024, 3, 15)),
        ("Joined the company on 3 March 2020.", date(2020, 3, 3)),
        ("Departing 03/11/2025 returning 17/11/2025", date(2025, 11, 3)),
        ("My birthday is on 5 May.", date(2025, 5, 5)),
        ("School holidays start 20 July.", date(2025, 7, 20)),
    ],
)
def test_dates_in_prose(text, expected):
    assert first(text)[0] == expected


def test_two_dates_in_one_sentence_stay_separate():
    found = parse("Admitted 14 Feb, discharged 19 Feb.", today=TODAY)
    assert len(found) == 2
    assert found[0].range.start == date(2025, 2, 14)
    assert found[1].range.start == date(2025, 2, 19)


def test_booking_range():
    assert first("Your stay from Friday to Sunday") == (date(2025, 9, 5), date(2025, 9, 8))


@pytest.mark.parametrize(
    "text,start",
    [
        ("Can we meet Thursday?", date(2025, 9, 4)),
        ("See you on the 15th", date(2025, 9, 15)),
        ("Rent is due on the 1st.", date(2025, 9, 1)),
        ("I'll send it EOD", date(2025, 9, 4)),
        ("Due COB Friday", date(2025, 9, 5)),
        ("moved to tomorrow morning", date(2025, 9, 5)),
    ],
)
def test_everyday_and_chat(text, start):
    """Conversational phrasing needed its own cues -- "meet", "due", "on the" -- none of
    which appear in a reporting vocabulary."""
    assert first(text)[0] == start


def test_decades():
    assert first("Popular throughout the 1990s.") == (date(1990, 1, 1), date(2000, 1, 1))
    assert first("The war ended in 1945.") == (date(1945, 1, 1), date(1946, 1, 1))


def test_duration_from_now():
    """"Probation ends after 6 months" is a date; "after March" is a modifier."""
    assert first("Probation ends after 6 months.") == (date(2026, 3, 1), date(2026, 4, 1))
    assert first("in 3 days") == (date(2025, 9, 7), date(2025, 9, 8))


# ---------------------------------------------------------------------------
# Numbers that are not dates. In general text this matters more than recall.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Ordinals that are positions, not days. A following noun is what tells them apart.
        "he lives on the 3rd floor",
        "we finished in the 2nd round",
        "the 1st item on the list",
        "turn left at the 2nd exit",
        "on the 5th attempt",
        "ranked 21st out of 30",
        "Built in the 19th century.",
        # "cob" in lower case is corn, not close of business.
        "corn on the cob",
        "a cob of sweetcorn",
        # Version numbers, identifiers, money, measurements.
        "upgrade to version 2024.1",
        "running v1.2.3",
        "Python 3.12",
        "revenue of $2,024",
        "order #2024",
        "invoice 20240315",
        "call 555-2024",
        "Suite 1900",
        "weighs 1985 kg",
        "a 1500 metre run",
        "scored 15-03",
        "pages 12-24",
        "a 15-year mortgage",
        "clause 3-14",
        "IP 10.0.2.24",
        "$1,500 per month",
        # Durations with no anchor.
        "bake for 30 minutes",
        "a 90 minute meeting",
        "runs for 2 hours",
        # Month and weekday words used as ordinary language.
        "the march on Washington",
        "you may proceed",
        "an august institution",
        "sales by June Patel",
    ],
)
def test_not_dates(text):
    assert parse(text, today=TODAY) == []


def test_compact_iso_is_not_read():
    """"20240315" is deliberately unsupported: it is indistinguishable from an invoice
    number, and reading it would make "invoice 20240315" a date."""
    assert parse("20240315", today=TODAY) == []
    assert parse("invoice 20240315", today=TODAY) == []


# ---------------------------------------------------------------------------
# Robustness on messy real input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "no dates here at all",
        "!!!???",
        "\x00﻿",
        "a" * 5000,
        "2024-13-45",          # impossible date
        "31 February 2024",    # impossible day
        "Q9 FY99",             # impossible quarter
        "1/1/1",
        "-" * 200,
        "2024-03-15" * 100,    # many matches
    ],
)
def test_messy_input_never_raises(text):
    assert isinstance(parse(text, today=TODAY), list)


def test_impossible_dates_are_not_invented():
    assert parse("31 February 2024", today=TODAY) == []
    assert parse("2024-13-45", today=TODAY) == []
