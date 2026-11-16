"""A fiscal week, a month counted into a year, and the fiscal year's end, written the short
way round -- "fiscal week 3", "month 3 of FY24", "fiscal year end", "FYE 2024".

Each was misread with no sign of it: "fiscal week 3" and "week 3 of FY25" were ISO week 3
of a calendar year, "month 3 of FY24" its 3rd *day*, "fiscal year end" the calendar year's
end and "FYE 2024" the calendar year 2024. Each is now read exactly as the longer phrase
the library already read right -- "the 3rd week of FY25", "the 3rd month of FY24", "the
end of the fiscal year", the fiscal year ended 2024 -- so no new convention comes in with
the fix: the equivalence tests hold that under every fiscal calendar.
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


def days(text: str, cfg: WranglerConfig | None = None) -> tuple[str, str] | None:
    """First and last day (inclusive) of the one date in ``text``, or None if not one."""
    found = parse(text, today=TODAY, config=cfg or WranglerConfig())
    if len(found) != 1 or found[0].span != (0, len(text)):
        return None
    r = found[0].range
    last = r.end - timedelta(days=1) if r.end else None
    return (r.start.isoformat() if r.start else "..", last.isoformat() if last else "..")


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
        ("week 3 of FY25", "2024-04-15", "2024-04-21"),
        ("week 3 FY25", "2024-04-15", "2024-04-21"),
        ("FY25 week 3", "2024-04-15", "2024-04-21"),
        ("week 3 of the fiscal year", "2026-04-15", "2026-04-21"),
        ("week 3 of last FY", "2025-04-15", "2025-04-21"),
        ("fiscal week 20 FY25", "2024-08-12", "2024-08-18"),
        ("the 20th week of FY25", "2024-08-12", "2024-08-18"),
        ("fiscal week 53 FY25", "2025-03-31", "2025-03-31"),  # cut at the year's end
        # A month counted into a year.
        ("month 3 of FY24", "2023-06-01", "2023-06-30"),
        ("month 3 FY24", "2023-06-01", "2023-06-30"),
        ("FY24 month 3", "2023-06-01", "2023-06-30"),
        ("FY24 M3", "2023-06-01", "2023-06-30"),
        ("M3 FY24", "2023-06-01", "2023-06-30"),
        ("month 12 of FY24", "2024-03-01", "2024-03-31"),
        ("month 3 of the fiscal year", "2026-06-01", "2026-06-30"),
        ("fiscal month 3", "2026-06-01", "2026-06-30"),
        ("month 3 of 2024", "2024-03-01", "2024-03-31"),
        ("month 2 of Q1 FY25", "2024-05-01", "2024-05-31"),
        # The fiscal year's ends.
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
        # With a year: the fiscal year that ends in it.
        ("FYE 2024", "2023-04-01", "2024-03-31"),
        ("fiscal year end 2024", "2023-04-01", "2024-03-31"),
        ("fiscal year ended 2024", "2023-04-01", "2024-03-31"),
        ("2024 fiscal year end", "2023-04-01", "2024-03-31"),
        ("FYE March 2024", "2023-04-01", "2024-03-31"),
        # Weeks of a month, written week first, or "for".
        ("week 3 of jan", "2026-01-15", "2026-01-21"),
        ("week 3 of January 2025", "2025-01-15", "2025-01-21"),
        ("3rd week for jan", "2026-01-15", "2026-01-21"),
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
        ("month 3 of FY24", "the 3rd month of FY24"),
        ("FY24 M3", "the 3rd month of FY24"),
        ("fiscal month 3", "the 3rd month of the fiscal year"),
        ("month 3 of the fiscal year", "the 3rd month of the fiscal year"),
        ("fiscal year end", "the end of the fiscal year"),
        ("FYE", "the end of the fiscal year"),
        ("fiscal year start", "the start of the fiscal year"),
        ("by fiscal year end", "by the end of the fiscal year"),
        ("week 3 of jan", "the 3rd week of jan"),
        ("3rd week for jan", "3rd week of jan"),
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
    assert days("FYE 2024", cfg) == want
    assert days("fiscal year end 2024", cfg) == want


def test_a_year_left_unwritten_is_a_guess_as_week_3_is():
    for text in ["fiscal week 3", "fiscal month 3", "week 3"]:
        (m,) = parse(text, today=TODAY)
        assert m.confidence == YEAR_GUESSED, text
    for text in ["week 3 of the fiscal year", "fiscal week 3 FY25", "month 3 of FY24"]:
        (m,) = parse(text, today=TODAY)
        assert m.confidence == 1.0, text


def test_fiscal_weeks_and_months_are_on_the_fiscal_basis():
    for text in ["fiscal week 3", "week 3 of FY25", "month 3 of FY24", "fiscal year end"]:
        (m,) = parse(text, today=TODAY)
        assert m.range.basis.value == "fiscal", text


@pytest.mark.parametrize(
    "text, says",
    [
        ("month 13 of FY24", "twelve months"),
        ("month 0 of FY24", "twelve months"),
    ],
)
def test_a_month_no_year_has_is_refused_with_the_reason(text, says):
    found, diags = diagnose(text, today=TODAY)
    assert found == [] and any(says in d.reason for d in diags)


@pytest.mark.parametrize(
    "text",
    ["fiscal week 54 FY25", "the 54th week of FY25", "fiscal month 3 of CY2024",
     "the 6th week of jan"],
)
def test_what_is_not_there_is_not_answered(text):
    assert parse(text, today=TODAY) == []


@pytest.mark.parametrize(
    "text, first, last",
    [
        # A week number with nothing fiscal about it stays an ISO week -- under a fiscal
        # year_basis too, as the tool and README say.
        ("week 3", "2026-01-12", "2026-01-18"),
        ("week 3 of 2025", "2025-01-13", "2025-01-19"),
        ("W3", "2026-01-12", "2026-01-18"),
        ("calendar week 3 2025", "2025-01-13", "2025-01-19"),
        # "year end" alone is the calendar's, and "FY24 end" names FY24 by its end, as
        # "Q1-end 2024" names Q1.
        ("year end", "2026-09-01", "2026-12-31"),
        ("FY24 end", "2023-04-01", "2024-03-31"),
        ("year end 2024", "2024-01-01", "2024-12-31"),
        # The day of a month is not touched.
        ("3rd week of jan", "2026-01-15", "2026-01-21"),
        ("day 3 of FY24", "2023-04-03", "2023-04-03"),
    ],
)
def test_neighbours_keep_their_readings(text, first, last):
    found = parse(text, today=TODAY)
    assert found, text
    r = found[0].range
    assert (r.start.isoformat(), (r.end - timedelta(days=1)).isoformat()) == (first, last)


def test_week_numbers_stay_iso_on_a_fiscal_year_basis():
    fiscal = WranglerConfig(year_basis="fiscal")
    assert days("week 3", fiscal) == days("week 3")
    assert days("week 3 of 2025", fiscal) == days("week 3 of 2025")


@pytest.mark.parametrize("text", ["BMW M3", "500 m3", "M3", "the M3 motorway", "month 3"])
def test_m3_and_month_3_alone_are_not_dates(text):
    assert parse(text, today=TODAY) == []


def test_fiscal_weeks_run_together_as_weeks_do():
    """"fiscal weeks 3-5": both ends fiscal weeks, joined as "weeks 11-12" are."""
    assert days("fiscal weeks 3-5 FY25") == ("2024-04-15", "2024-05-05")
    assert days("weeks 3-5 of FY25") == ("2024-04-15", "2024-05-05")
