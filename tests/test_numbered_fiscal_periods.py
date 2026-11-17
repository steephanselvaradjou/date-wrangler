"""A fiscal week, a month counted into a year, and the fiscal year's end, written the short
way round -- "fiscal week 3", "month 3 of FY24", "fiscal year end", "FYE 2024".

Each was misread with no sign of it: "fiscal week 3" and "week 3 of FY25" were ISO week 3
of a calendar year, "month 3 of FY24" its 3rd *day*, "fiscal year end" the calendar year's
end and "FYE 2024" the calendar year 2024. Each is now read exactly as the longer phrase
the library already read right -- "the 3rd week of FY25", "the 3rd month of FY24", "the
end of the fiscal year", the fiscal year ended 2024 -- so no new convention comes in with
the fix: the equivalence tests hold that under every fiscal calendar.

A first version of the fix took more than it should: the phrases a basis word now reached
-- "fiscal quarter end June 2024", "calendar year end 2024" -- lost their readings, runs
were cut to one end, "M3" beside a year was read as a month, an FY before a comma took the
next sentence's week. Those are held here too, at their readings from before.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from date_wrangler import YEAR_GUESSED, WranglerConfig, diagnose, parse
from date_wrangler.config import FiscalCalendar, YearLabel

TODAY = date(2026, 11, 16)  # FY27 with an April year

#: Every fiscal calendar: each start month, each way of naming the year.
CALENDARS = [WranglerConfig(fiscal=FiscalCalendar(m, label)) for m in range(1, 13)
             for label in YearLabel]


def readings(text: str, cfg: WranglerConfig | None = None) -> list[tuple[str, str, str]]:
    """Each date in ``text``: its words, first day and last day (inclusive)."""
    out = []
    for m in parse(text, today=TODAY, config=cfg or WranglerConfig()):
        r = m.range
        last = r.end - timedelta(days=1) if r.end else None
        out.append((m.text, r.start.isoformat() if r.start else "..",
                    last.isoformat() if last else ".."))
    return out


def days(text: str, cfg: WranglerConfig | None = None) -> tuple[str, str] | None:
    """First and last day of the one date ``text`` is, all of it; None if not that."""
    found = readings(text, cfg)
    if len(found) != 1 or found[0][0] != text:
        return None
    return found[0][1], found[0][2]


@pytest.mark.parametrize(
    "text, first, last",
    [
        # Fiscal weeks: seven-day blocks from the fiscal year's first day.
        ("fiscal week 3", "2026-04-15", "2026-04-21"),
        ("fiscal wk 3", "2026-04-15", "2026-04-21"),
        ("FY week 3", "2026-04-15", "2026-04-21"),
        ("fiscal week 3 FY25", "2024-04-15", "2024-04-21"),
        ("fiscal week 3 2025", "2024-04-15", "2024-04-21"),
        ("fiscal week 3 of 2025", "2024-04-15", "2024-04-21"),
        ("fiscal week 3 in FY25", "2024-04-15", "2024-04-21"),
        ("week 3 of FY25", "2024-04-15", "2024-04-21"),
        ("week 3 in FY25", "2024-04-15", "2024-04-21"),
        ("week 3 FY25", "2024-04-15", "2024-04-21"),
        ("FY25 week 3", "2024-04-15", "2024-04-21"),
        ("FY2024-25 week 3", "2024-04-15", "2024-04-21"),
        ("FY24/25 week 3", "2024-04-15", "2024-04-21"),
        ("week 3 of the fiscal year", "2026-04-15", "2026-04-21"),
        ("week 3 of FY", "2026-04-15", "2026-04-21"),
        ("week 3 of last FY", "2025-04-15", "2025-04-21"),
        ("fiscal week 20 FY25", "2024-08-12", "2024-08-18"),
        ("the 20th week of FY25", "2024-08-12", "2024-08-18"),
        ("fiscal week 53 FY25", "2025-03-31", "2025-03-31"),  # cut at the year's end
        # ...and of a period inside one: that period's weeks.
        ("fiscal week 3 of Q1 FY25", "2024-04-15", "2024-04-21"),
        ("week 3 of January", "2026-01-15", "2026-01-21"),
        ("week 3 of January 2025", "2025-01-15", "2025-01-21"),
        ("week 3 of next month", "2026-12-15", "2026-12-21"),
        # Runs, read whole.
        ("fiscal weeks 3-5 FY25", "2024-04-15", "2024-05-05"),
        ("FY25 week 3-5", "2024-04-15", "2024-05-05"),
        ("FY25 weeks 3-5", "2024-04-15", "2024-05-05"),
        ("weeks 3-5 of FY25", "2024-04-15", "2024-05-05"),
        ("week 3 to week 5 of FY25", "2024-04-15", "2024-05-05"),
        ("fiscal week 3 to week 5", "2026-04-15", "2026-05-05"),
        ("weeks 3-5 of January", "2026-01-15", "2026-01-31"),
        # A month counted into a year.
        ("month 3 of FY24", "2023-06-01", "2023-06-30"),
        ("month 3 FY24", "2023-06-01", "2023-06-30"),
        ("FY24 month 3", "2023-06-01", "2023-06-30"),
        ("month 12 of FY24", "2024-03-01", "2024-03-31"),
        ("month 3 of the fiscal year", "2026-06-01", "2026-06-30"),
        ("fiscal month 3", "2026-06-01", "2026-06-30"),
        ("month 3 of 2024", "2024-03-01", "2024-03-31"),
        ("month 2 of Q1 FY25", "2024-05-01", "2024-05-31"),
        ("months 3-5 of FY24", "2023-06-01", "2023-08-31"),
        ("month 3 to month 5 of FY24", "2023-06-01", "2023-08-31"),
        # The fiscal year's edges.
        ("fiscal year end", "2026-12-01", "2027-03-31"),
        ("fiscal year-end", "2026-12-01", "2027-03-31"),
        ("the fiscal year end", "2026-12-01", "2027-03-31"),
        ("financial year end", "2026-12-01", "2027-03-31"),
        ("FY end", "2026-12-01", "2027-03-31"),
        ("FYE", "2026-12-01", "2027-03-31"),
        ("F.Y.E", "2026-12-01", "2027-03-31"),
        ("fiscal year start", "2026-04-01", "2026-07-31"),
        ("fiscal quarter end", "2026-12-01", "2026-12-31"),
        ("until fiscal year end", "..", "2027-03-31"),
        # With a year: the fiscal year that ends in it, however the year is written.
        ("FYE 2024", "2023-04-01", "2024-03-31"),
        ("FYE 24", "2023-04-01", "2024-03-31"),
        ("FYE '24", "2023-04-01", "2024-03-31"),
        ("F.Y.E. 2024", "2023-04-01", "2024-03-31"),
        ("2024 FYE", "2023-04-01", "2024-03-31"),
        ("FY end 24", "2023-04-01", "2024-03-31"),
        ("fiscal year end 2024", "2023-04-01", "2024-03-31"),
        ("fiscal year end 24", "2023-04-01", "2024-03-31"),
        ("fiscal year ended 2024", "2023-04-01", "2024-03-31"),
        ("2024 fiscal year end", "2023-04-01", "2024-03-31"),
        ("FYE March 2024", "2023-04-01", "2024-03-31"),
        # A year's start with its year.
        ("year start 2024", "2024-01-01", "2024-04-30"),
        ("fiscal year start 2024", "2023-04-01", "2023-07-31"),
    ],
)
def test_each_reads_as_written(text, first, last):
    assert days(text) == (first, last)


@pytest.mark.parametrize(
    "short, long",
    [
        ("fiscal week 3", "the 3rd week of the fiscal year"),
        ("week 3 of FY25", "the 3rd week of FY25"),
        ("FY25 week 3", "the 3rd week of FY25"),
        ("fiscal week 3 FY25", "the 3rd week of FY25"),
        ("fiscal week 40 FY25", "the 40th week of FY25"),
        ("week 3 of last FY", "the 3rd week of last FY"),
        ("week 3 of FY", "the 3rd week of FY"),
        ("month 3 of FY24", "the 3rd month of FY24"),
        ("FY24 month 3", "the 3rd month of FY24"),
        ("fiscal month 3", "the 3rd month of the fiscal year"),
        ("month 3 of the fiscal year", "the 3rd month of the fiscal year"),
        ("fiscal year end", "the end of the fiscal year"),
        ("FYE", "the end of the fiscal year"),
        ("fiscal year start", "the start of the fiscal year"),
        ("fiscal year start FY24", "the start of FY24"),
        ("by fiscal year end", "by the end of the fiscal year"),
        ("week 3 of jan", "the 3rd week of jan"),
        ("week 3 of Q1 FY25", "the 3rd week of Q1 FY25"),
    ],
)
def test_each_reads_as_the_phrase_the_library_already_read_right(short, long):
    """Under every fiscal calendar, so the short form brings no convention of its own."""
    for cfg in CALENDARS:
        got, want = days(short, cfg), days(long, cfg)
        assert want is not None, (long, cfg.fiscal)
        assert got == want, (short, cfg.fiscal)


@pytest.mark.parametrize("cfg", CALENDARS, ids=lambda c: str(c.fiscal))
def test_fye_with_a_year_is_the_fiscal_year_that_ends_in_it(cfg):
    """Whichever label that year carries: with a start-year label the year ending in March
    2024 is FY2023, and "FYE 2024" is still it."""
    start = cfg.fiscal.start_month
    first = date(2024 if start == 1 else 2023, start, 1)
    end_month = cfg.fiscal.end_month
    following = date(2024 + (end_month == 12), end_month % 12 + 1, 1)
    want = (first.isoformat(), (following - timedelta(days=1)).isoformat())
    for text in ["FYE 2024", "fiscal year end 2024", "FYE 24", "2024 FYE"]:
        assert days(text, cfg) == want, text


def test_fye_with_a_fiscal_label_is_that_fiscal_year_whatever_its_label():
    """REGRESSION: with an October year named by its start, "FYE FY24" came back as the
    year the number 24 ends in -- FY23."""
    us = WranglerConfig(fiscal=FiscalCalendar(10, YearLabel.START_YEAR))
    for text in ["FYE FY24", "fiscal year end FY24", "FY end FY24", "FYE FY2024-25"]:
        assert days(text, us) == days("FY24", us) == ("2024-10-01", "2025-09-30"), text


def test_lists_are_each_a_date_in_the_same_period():
    assert readings("weeks 3 and 5 of FY25") == [
        ("weeks 3", "2024-04-15", "2024-04-21"),
        ("5 of FY25", "2024-04-29", "2024-05-05"),
    ]
    assert readings("week 3 and week 5 of FY25") == [
        ("week 3", "2024-04-15", "2024-04-21"),
        ("week 5 of FY25", "2024-04-29", "2024-05-05"),
    ]
    assert [r[1] for r in readings("weeks 3, 4 and 5 of FY25")] == [
        "2024-04-15", "2024-04-22", "2024-04-29",
    ]


def test_a_list_needs_the_unit_plural_or_said_again():
    """"week 3 and 5 people" is one week."""
    assert readings("week 3 and 5 people") == [("week 3", "2026-01-12", "2026-01-18")]


def test_a_year_left_unwritten_is_a_guess_as_week_3_is():
    for text in ["fiscal week 3", "fiscal month 3", "week 3"]:
        (m,) = parse(text, today=TODAY)
        assert m.confidence == YEAR_GUESSED, text
    for text in ["week 3 of the fiscal year", "fiscal week 3 FY25", "month 3 of FY24"]:
        (m,) = parse(text, today=TODAY)
        assert m.confidence == 1.0, text


@pytest.mark.parametrize(
    "text, says",
    [
        ("month 13 of FY24", "twelve months"),
        ("month 0 of FY24", "twelve months"),
        ("week 10 of March", "no week 10"),
        ("month 4 of Q1 FY25", "no month 4"),
        ("fiscal week 3 of CY2025", "not a calendar one"),
        ("fiscal month 3 of CY2024", "not a calendar one"),
    ],
)
def test_what_a_period_does_not_have_is_refused_with_the_reason(text, says):
    found, diags = diagnose(text, today=TODAY)
    assert found == [] and any(says in d.reason for d in diags), (found, diags)


@pytest.mark.parametrize(
    "text", ["fiscal week 54 FY25", "the 54th week of FY25", "the 6th week of jan",
             "week 14 of Q1 2025", "the 6th week of 2025"]
)
def test_what_is_not_there_is_not_answered(text):
    """Q1 2025 has 90 days, so its 14th seven-day week would start after it; a calendar
    year's numbered weeks are ISO weeks, and "the 6th week of 2025" stays refused."""
    assert parse(text, today=TODAY) == []


@pytest.mark.parametrize(
    "short, long, first",
    [
        ("week 10 of Q1 2025", "the 10th week of Q1 2025", "2025-03-05"),
        ("week 13 of Q1 2025", "the 13th week of Q1 2025", "2025-03-26"),
        ("week 20 of H1 2025", "the 20th week of H1 2025", "2025-05-14"),
    ],
)
def test_a_quarter_and_a_half_hold_their_weeks(short, long, first):
    """A quarter has thirteen weeks, not a month's five. REGRESSION: "the 10th week of Q1"
    was refused, and "week 10 of Q1" read as ISO week 10 beside Q1."""
    assert days(short) == days(long)
    assert days(short)[0] == first


@pytest.mark.parametrize(
    "text",
    ["academic year start", "academic year end 2024", "the school year end 2024",
     "2024 school year end", "school year start 2024"],
)
def test_an_academic_years_edges_are_declined_as_the_year_is(text):
    """An academic year starts in August or September, not January. REGRESSION: these were
    the calendar year's start or end."""
    found, diags = diagnose(text, today=TODAY)
    assert found == [] and any("names a season" in d.reason for d in diags)


@pytest.mark.parametrize(
    "text, want",
    [
        # A basis word in front of a period's end, then a date or a year: read as before
        # the basis was read in front of a bare end.
        ("fiscal quarter end June 2024", [("quarter end June 2024", "2024-04-01", "2024-06-30")]),
        ("calendar quarter end June 2024", [("quarter end June 2024", "2024-04-01", "2024-06-30")]),
        ("calendar year end 2024", [("year end 2024", "2024-01-01", "2024-12-31")]),
        ("calendar year end December 2024",
         [("year end December 2024", "2024-01-01", "2024-12-31")]),
        ("CY end 2024", [("end 2024", "2024-09-01", "2024-12-31")]),
        ("fiscal half end September 2024",
         [("half end September 2024", "2024-04-01", "2024-09-30")]),
        ("calendar month end March 2024", [("month end March 2024", "2024-03-01", "2024-03-31")]),
        ("fiscal quarter end 2024", [("quarter end 2024", "2024-10-01", "2024-12-31")]),
        ("2024 calendar year end", [("2024 calendar year end", "2024-01-01", "2024-12-31")]),
        # A plain week number stays an ISO week, also after an FY and a comma.
        ("In FY24, week 3 of 2024 was slow",
         [("FY24", "2023-04-01", "2024-03-31"), ("week 3 of 2024", "2024-01-15", "2024-01-21")]),
        ("week 3", [("week 3", "2026-01-12", "2026-01-18")]),
        ("week 3 of 2025", [("week 3 of 2025", "2025-01-13", "2025-01-19")]),
        ("W3", [("W3", "2026-01-12", "2026-01-18")]),
        ("calendar week 3 2025", [("calendar week 3 2025", "2025-01-13", "2025-01-19")]),
        ("weeks 42-43 2026", [("weeks 42-43 2026", "2026-10-12", "2026-10-25")]),
        ("week 42, 2026", [("week 42, 2026", "2026-10-12", "2026-10-18")]),
        # "year end" alone is the calendar's, and "FY24 end" names FY24 by its end, as
        # "Q1-end 2024" names Q1.
        ("year end", [("year end", "2026-09-01", "2026-12-31")]),
        ("FY24 end", [("FY24", "2023-04-01", "2024-03-31")]),
        ("year end 2024", [("year end 2024", "2024-01-01", "2024-12-31")]),
        ("3rd week of jan", [("3rd week of jan", "2026-01-15", "2026-01-21")]),
        ("day 3 of FY24", [("3 of FY24", "2023-04-03", "2023-04-03")]),
        # "for" is not read in place of "of": "the last day for 2024" was never a day of 2024.
        ("the last day for 2024", [("the last day", "2026-11-15", "2026-11-15"),
                                   ("2024", "2024-01-01", "2024-12-31")]),
    ],
)
def test_neighbours_keep_their_readings(text, want):
    assert readings(text) == want


def test_week_numbers_stay_iso_on_a_fiscal_year_basis():
    fiscal = WranglerConfig(year_basis="fiscal")
    assert days("week 3", fiscal) == days("week 3")
    assert days("week 3 of 2025", fiscal) == days("week 3 of 2025")


@pytest.mark.parametrize(
    "text",
    ["FY24 M3", "M3 FY24", "500 m3 FY24", "BMW M3 FY24", "M1 M2 M3 FY24", "FY24 M1 motorway",
     "FY24 M25 widening"],
)
def test_an_m_number_beside_a_fiscal_year_is_not_a_month(text):
    """"M3" is a car, a road, a money supply or cubic metres as often as a month, and a
    fiscal year beside it does not settle which: the fiscal year is read, and no month."""
    assert [r[1:] for r in readings(text)] == [("2023-04-01", "2024-03-31")], text


@pytest.mark.parametrize("text", ["BMW M3", "500 m3", "M3", "the M3 motorway", "month 3",
                                  "the 3rd week for each team"])
def test_these_are_not_dates(text):
    assert parse(text, today=TODAY) == []
