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
were cut to one end, "M3" before a year was read as a month, an FY before a comma took the
next sentence's week. Those are held here too, at their readings from before.

A tax year is the fiscal year, and "FY24 M3" FY24's third month: both were read with a
word dropped -- "tax", "M3".
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
    """A quarter holds thirteen weeks and the start of a fourteenth, not a month's five.
    REGRESSION: "the 10th week of Q1" was refused, and "week 10 of Q1" read as ISO week 10
    beside Q1."""
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
        # ...and a calendar year's end with its year is that calendar year, as "year end
        # 2024" is 2024. "CY end 2024" was the end of 2024, "CY" dropped.
        ("calendar year end 2024", [("calendar year end 2024", "2024-01-01", "2024-12-31")]),
        ("calendar year end December 2024",
         [("calendar year end December 2024", "2024-01-01", "2024-12-31")]),
        ("CY end 2024", [("CY end 2024", "2024-01-01", "2024-12-31")]),
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
    "short, long",
    [
        ("FY24 M3", "the 3rd month of FY24"),
        ("FY24 M03", "the 3rd month of FY24"),
        ("FY24-M3", "the 3rd month of FY24"),
        ("FY24M3", "the 3rd month of FY24"),
        ("FY24 M1", "the 1st month of FY24"),
        ("FY24 M12", "the 12th month of FY24"),
        ("FY2024-25 M3", "the 3rd month of FY2024-25"),
        ("FY24 M3-M5", "months 3-5 of FY24"),
        ("FY24 M3-5", "months 3-5 of FY24"),
        ("FY24 M3 to M5", "months 3-5 of FY24"),
    ],
)
def test_a_month_numbered_after_a_fiscal_year_is_that_years_month(short, long):
    """"FY24 M3", as reports head a column, under every fiscal calendar. REGRESSION: it was
    FY24, M3 left over."""
    for cfg in CALENDARS:
        got, want = days(short, cfg), days(long, cfg)
        assert want is not None, (long, cfg.fiscal)
        assert got == want, (short, cfg.fiscal)


def test_an_m_number_is_read_a_little_below_certain():
    """"FY24 M3 growth" may be the money supply: read, but not as surely as "month 3"."""
    for text in ["FY24 M3", "FY24 M3-M5"]:
        (m,) = parse(text, today=TODAY)
        assert m.confidence == 0.8, text
    (m,) = parse("FY24 month 3", today=TODAY)
    assert m.confidence == 1.0


def test_m_numbers_listed_are_each_a_month_of_the_year():
    assert readings("FY24 M3 and M5") == [
        ("FY24 M3", "2023-06-01", "2023-06-30"),
        ("M5", "2023-08-01", "2023-08-31"),
    ]


@pytest.mark.parametrize(
    "text",
    ["M3 FY24", "500 m3 FY24", "BMW M3 FY24", "M1 M2 M3 FY24", "FY24 M25 widening",
     "FY24 M13", "FY24 M0", "FY24 M3-M15"],
)
def test_an_m_number_before_a_fiscal_year_or_past_twelve_is_not_a_month(text):
    """Before the year, "M3" is a car or cubic metres as often as a month; past twelve,
    a road. The fiscal year is read, and no month -- nor month 3 with "-M15" left over."""
    assert [r[1:] for r in readings(text)] == [("2023-04-01", "2024-03-31")], text


@pytest.mark.parametrize(
    "tax, fiscal",
    [
        ("tax year 2024", "fiscal year 2024"),
        ("tax year 2024-25", "FY2024-25"),
        ("tax year 24", "FY24"),
        ("the tax year 2024", "the fiscal year 2024"),
        ("tax-year 2024", "fiscal year 2024"),
        ("2024-25 tax year", "2024-25 financial year"),
        ("this tax year", "this fiscal year"),
        ("last tax year", "last fiscal year"),
        ("last 3 tax years", "last 3 fiscal years"),
        ("tax year start", "fiscal year start"),
        ("tax year start 2024", "fiscal year start 2024"),
        ("tax year end", "fiscal year end"),
        ("tax year end 2024", "fiscal year end 2024"),
        ("tax year end 24", "fiscal year end 24"),
        ("2024 tax year end", "2024 fiscal year end"),
        ("tax year ending 2024", "fiscal year ending 2024"),
        ("the end of the tax year", "the end of the fiscal year"),
        ("the end of last tax year", "the end of last fiscal year"),
        ("Q1 tax year 2024", "Q1 fiscal year 2024"),
        ("Q1 of the tax year", "Q1 of the fiscal year"),
        ("Q1 last tax year", "Q1 last fiscal year"),
        ("June of the tax year", "June of the fiscal year"),
        ("tax year 2025 June", "fiscal year 2025 June"),
        ("week 3 of the tax year", "week 3 of the fiscal year"),
        ("tax week 3", "fiscal week 3"),
        ("tax month 3", "fiscal month 3"),
        ("the 3rd month of the tax year", "the 3rd month of the fiscal year"),
        ("tax year to date", "fiscal year to date"),
        ("so far this tax year", "so far this fiscal year"),
        ("tax year 2024 to date", "fiscal year 2024 to date"),
    ],
)
def test_a_tax_year_is_the_fiscal_year(tax, fiscal):
    """On the configured calendar, as an assessment year is the one after it -- India's tax
    year since April 2026 is its financial year. REGRESSION: "tax" was dropped, and these
    were the calendar year's, or nothing."""
    for cfg in CALENDARS:
        got, want = days(tax, cfg), days(fiscal, cfg)
        assert want is not None, (fiscal, cfg.fiscal)
        assert got == want, (tax, cfg.fiscal)


def test_a_tax_year_is_the_year_before_the_assessment_year_named_alike():
    """AY 2025-26 assesses the income of tax year 2024-25."""
    for cfg in CALENDARS:
        assert days("AY 2025-26", cfg) == days("tax year 2024-25", cfg), cfg.fiscal
        assert days("tax year 2025-26", cfg) == days("FY2025-26", cfg), cfg.fiscal


@pytest.mark.parametrize(
    "text, want",
    [
        # "tax" makes a fiscal year only of a year: a tax day, a tax quarter and the tax
        # paid so far are not fiscal periods, and stay as they were.
        ("tax day", []),
        ("tax quarter", []),
        ("this tax quarter", []),
        ("sales tax 2024", []),
        ("tax ytd", [("ytd", "2026-01-01", "2026-11-16")]),
        ("we paid tax last year", [("last year", "2025-01-01", "2025-12-31")]),
        # A tax year that ends on a day given ends there, whatever the calendar.
        ("the tax year ending 5 April 2024",
         [("the tax year ending 5 April 2024", "2023-04-06", "2024-04-05")]),
    ],
)
def test_tax_beside_anything_but_a_year_changes_nothing(text, want):
    assert readings(text) == want


@pytest.mark.parametrize(
    "hyphened, spaced",
    [("fiscal-year 2024", "fiscal year 2024"), ("financial-year 2024", "financial year 2024"),
     ("calendar-year 2024", "calendar year 2024"), ("tax-year end", "tax year end")],
)
def test_a_hyphened_year_word_keeps_its_basis(hyphened, spaced):
    """REGRESSION: "fiscal-year 2024" was the calendar year 2024, "fiscal" dropped."""
    for cfg in CALENDARS:
        assert days(hyphened, cfg) == days(spaced, cfg), (hyphened, cfg.fiscal)


@pytest.mark.parametrize(
    "text, long",
    [
        ("FY25 weeks 3-5 of Q2", "weeks 3-5 of Q2 FY25"),
        ("FY25 week 3 to week 5 of Q2", "weeks 3-5 of Q2 FY25"),
        ("FY2024-25 weeks 3-5 of Q2", "weeks 3-5 of Q2 FY2024-25"),
        ("FY2024-25 week 3 of Q2", "week 3 of Q2 FY2024-25"),
        ("FY2024-25 month 3 of Q2", "month 3 of Q2 FY2024-25"),
    ],
)
def test_weeks_after_a_fiscal_year_counted_in_a_period_are_the_periods(text, long):
    """Read whole, in the period named. REGRESSION: "FY25 weeks 3-5 of Q2" was taken one
    number short -- week 3 of FY25 to the 5th day of Q2 -- and "FY2024-25 week 3 of Q2" was
    FY2024 with "-25" left over."""
    for cfg in CALENDARS:
        assert [r[1:] for r in readings(text, cfg)] == [r[1:] for r in readings(long, cfg)], (
            text, cfg.fiscal,
        )


def test_weeks_listed_after_a_fiscal_year_are_each_the_periods():
    assert [r[1:] for r in readings("FY25 weeks 3 and 5 of Q2")] == [
        ("2024-07-15", "2024-07-21"), ("2024-07-29", "2024-08-04"),
    ]


@pytest.mark.parametrize(
    "with_the, without",
    [
        ("Q1 of the financial year 2024-25", "Q1 of financial year 2024-25"),
        ("the first quarter of the financial year 2024-25", "the first quarter of FY2024-25"),
        ("H1 of the financial year 2024-25", "H1 of financial year 2024-25"),
        ("Q1 of the fiscal year 2024", "Q1 of fiscal year 2024"),
        ("Q1 of the tax year 2024-25", "Q1 of FY2024-25"),
        ("the last month of the fiscal year 2024", "the last month of FY2024"),
        ("the last quarter of the fiscal year 2024", "the last quarter of FY2024"),
        ("Q1 of the calendar year 2024", "Q1 of calendar year 2024"),
        ("the last month of the year 2024", "the last month of 2024"),
        ("the first day of the year 2024", "the first day of 2024"),
    ],
)
def test_the_before_a_written_year_keeps_the_year(with_the, without):
    """REGRESSION: "Q1 of the financial year 2024-25" was this fiscal year's Q1 beside the
    calendar years 2024 to 2025 -- "the" was taken only before "year" itself."""
    for cfg in CALENDARS:
        got, want = days(with_the, cfg), days(without, cfg)
        assert want is not None, (without, cfg.fiscal)
        assert got == want, (with_the, cfg.fiscal)


@pytest.mark.parametrize(
    "text, want",
    [
        ("We are in week 2 of the marathon training plan.", ["week 2"]),
        ("Week 3 of the monthly close is the busiest.", ["Week 3"]),
        ("In week 1 of the quarterly planning cycle", ["week 1"]),
        ("week 3 in decline", ["week 3"]),
        ("week 2 of Mayhem", ["week 2"]),
        ("week 4 of the H1B process", ["week 4"]),
        ("week 3 of the 2020s", ["week 3", "the 2020s"]),
    ],
)
def test_a_counted_week_takes_no_word_that_only_starts_like_a_period(text, want):
    """REGRESSION: "week 2 of the marathon" was a week of March, "week 3 of the monthly
    close" a week of this month, "week 2 of Mayhem" one of May."""
    assert [r[0] for r in readings(text)] == want


@pytest.mark.parametrize(
    "text", ["In month 3, 2000 customers churned.", "Month 12 5000 users",
             "By month 6, 1500 orders had shipped."],
)
def test_a_number_after_month_n_is_its_year_only_when_marked_as_one(text):
    """REGRESSION: any four digits were, and the first was March 2000."""
    assert parse(text, today=TODAY) == []


@pytest.mark.parametrize(
    "text, long",
    [
        ("last FYE", "the end of last fiscal year"),
        ("previous FYE", "the end of last fiscal year"),
        ("next FYE", "the end of next fiscal year"),
        ("last fiscal year end", "the end of last fiscal year"),
        ("last month end", "the end of last month"),
        ("next quarter end", "the end of next quarter"),
        ("this month end", "the end of this month"),
        ("FYE-2024", "FYE 2024"),
        ("FYE: 2024", "FYE 2024"),
        ("FY24 FYE", "FY24"),
        ("FY24 year end", "FY24"),
        ("FY2024-25 FYE", "FY2024-25"),
    ],
)
def test_a_periods_edge_keeps_the_words_around_it(text, long):
    """REGRESSION: "since last FYE" was this fiscal year's end, months ahead, "FYE-2024"
    this fiscal year's end beside 2024, and "FY24 FYE" FY24 beside this fiscal year's end;
    "last month end" was all of last month."""
    for cfg in CALENDARS:
        got, want = days(text, cfg), days(long, cfg)
        assert want is not None, (long, cfg.fiscal)
        assert got == want, (text, cfg.fiscal)


@pytest.mark.parametrize(
    "text, want",
    [
        ("Balances since last FYE", [("since last FYE", "2025-12-01", "..")]),
        ("FYE of FY24", [("FYE of FY24", "2023-04-01", "2024-03-31")]),
        ("FYE in 2024", [("FYE in 2024", "2023-04-01", "2024-03-31")]),
        ("FYE 24 results", [("FYE 24", "2023-04-01", "2024-03-31")]),
        # Two digits a unit or a measure follows are a quantity, not a year.
        ("FYE 12 months revenue", [("FYE", "2026-12-01", "2027-03-31")]),
        ("Filing is due FYE 90 days", [("FYE", "2026-12-01", "2027-03-31")]),
        ("FY end 15 percent", [("FY end", "2026-12-01", "2027-03-31")]),
        ("fiscal year end 24 hours later", [("fiscal year end", "2026-12-01", "2027-03-31")]),
    ],
)
def test_fye_reads_no_number_that_is_not_its_year(text, want):
    """REGRESSION: "FYE 12 months" was FY2012, "FYE 90 days" the year ending 1990."""
    assert readings(text) == want


@pytest.mark.parametrize(
    "text", ["KW3 FY25", "CW 3 of FY25", "calendar week 3 of FY25", "CW3 FY25", "W3 FY25"],
)
def test_a_calendar_week_is_an_iso_week_whatever_year_follows(text):
    """KW and CW are ISO weeks by definition. REGRESSION: before an FY they were the fiscal
    year's weeks."""
    assert days(text) == ("2025-01-13", "2025-01-19")


@pytest.mark.parametrize(
    "text, first, last",
    [
        ("week 3 of the half year ending June 2024", "2024-01-15", "2024-01-21"),
        ("week 12 of FY ending March 2025", "2024-06-17", "2024-06-23"),
        ("the 12th week of FY ending March 2025", "2024-06-17", "2024-06-23"),
        ("week 12 of the year ending March 2025", "2024-06-17", "2024-06-23"),
        ("month 3 of the fiscal year ending March 2025", "2024-06-01", "2024-06-30"),
        ("the 3rd month of the fiscal year ending March 2025", "2024-06-01", "2024-06-30"),
        ("the first 3 months of the year ending June 2024", "2023-07-01", "2023-09-30"),
        ("the last day of the quarter ended June 2024", "2024-06-30", "2024-06-30"),
        ("the first Monday of the quarter ending June 2024", "2024-04-01", "2024-04-01"),
    ],
)
def test_a_period_named_by_its_end_is_counted_in_whole(text, first, last):
    """REGRESSION: the week took "the half" or "FY" -- this year's -- and lost the period
    ending June 2024; "month 3 of the fiscal year ending March 2025" was the 3rd *day* of
    this fiscal year; "the last day of the quarter ended June 2024" was 31 December."""
    assert days(text) == (first, last)


@pytest.mark.parametrize(
    "text, cfg, first",
    [
        ("fiscal year end of 2024", WranglerConfig(), "2023-04-01"),
        ("FYE in 2024", WranglerConfig(), "2023-04-01"),
        ("fiscal year end for 2024", WranglerConfig(), "2023-04-01"),
        ("FYE of FY24", WranglerConfig(), "2023-04-01"),
        # On an October year named by its start: the year ending in 2024, and FY24 itself.
        ("fiscal year end of 2024",
         WranglerConfig(fiscal=FiscalCalendar(10, YearLabel.START_YEAR), year_basis="fiscal"),
         "2023-10-01"),
        ("FYE of FY24",
         WranglerConfig(fiscal=FiscalCalendar(10, YearLabel.START_YEAR), year_basis="fiscal"),
         "2024-10-01"),
    ],
)
def test_a_fiscal_years_end_of_a_year_is_the_fiscal_year_ending_in_it(text, cfg, first):
    """REGRESSION: "fiscal year end of 2024" was the end of the calendar year 2024, and "FYE
    of FY24" this fiscal year's end beside FY24."""
    found = days(text, cfg)
    assert found is not None and found[0] == first, found


@pytest.mark.parametrize(
    "text, first",
    [
        ("fiscal week 3 to date", "2026-04-15"),
        ("week 3 of FY25 to date", "2024-04-15"),
        ("the 3rd week of FY25 to date", "2024-04-15"),
        ("month 3 of FY24 to date", "2023-06-01"),
        ("FY25 week 3 - present", "2024-04-15"),
        ("fiscal week 40 to date", "2025-12-30"),  # this year's week 40 is still to come
    ],
)
def test_to_date_after_a_numbered_week_runs_from_it_to_today(text, first):
    """REGRESSION: "to date" was put on the year before its week was taken out, and the
    week alone came back."""
    assert days(text) == (first, "2026-11-16")


def test_a_fiscal_word_at_either_end_of_a_run_makes_it_fiscal():
    """REGRESSION: "week 3 to fiscal week 5" joined ISO week 3 to fiscal week 5, and "fiscal
    week 3-week 5" was read as "3 weeks"."""
    assert days("week 3 to fiscal week 5") == days("fiscal week 3 to week 5") == (
        "2026-04-15", "2026-05-05",
    )
    assert days("fiscal week 3-week 5") == ("2026-04-15", "2026-05-05")
    assert days("FY24 month 3-month 4") == ("2023-06-01", "2023-07-31")
    assert parse("a 3-week sprint", today=TODAY) == []


@pytest.mark.parametrize("text", ["FY24 month 13", "FY24 week 0 baseline", "FY24 week 54"])
def test_a_number_the_fiscal_year_has_not_got_leaves_the_year_read(text):
    """Month 13 is an ERP's adjustment period. REGRESSION: the refusal took FY24 with it."""
    assert [r[1:] for r in readings(text)] == [("2023-04-01", "2024-03-31")]


@pytest.mark.parametrize(
    "text, first",
    [("week 3 of next year", "2027-01-18"), ("week 3 of last year", "2025-01-13"),
     ("week 3 next year", "2027-01-18"), ("W3 next year", "2027-01-18"),
     ("week 3 of this year", "2026-01-12")],
)
def test_a_week_number_in_a_year_named_from_this_one_is_that_years(text, first):
    """ISO week 3 of 2027, not this year's moved on a year. REGRESSION: it was this year's
    week 3, the year a guess."""
    (m,) = parse(text, today=TODAY)
    assert m.range.start.isoformat() == first and m.confidence == 1.0


@pytest.mark.parametrize(
    "text", ["2024 CY end", "2024 calendar year end", "calendar year end 2024", "CY end 2024"]
)
def test_a_calendar_years_end_is_the_calendar_year_on_a_fiscal_basis(text):
    """REGRESSION: "calendar" was dropped, and these were FY2024."""
    assert days(text, WranglerConfig(year_basis="fiscal")) == ("2024-01-01", "2024-12-31")


def test_weeks_listed_before_a_fiscal_year_are_its():
    assert readings("weeks 3 and 4 FY25") == [
        ("weeks 3", "2024-04-15", "2024-04-21"), ("4 FY25", "2024-04-22", "2024-04-28"),
    ]


def test_a_year_with_year_after_it_is_no_count_of_years():
    """REGRESSION: "in 2024 year end" was 2,024 years from now."""
    assert readings("Revenue in 2024 year end rose 5%.") == [
        ("2024 year end", "2024-01-01", "2024-12-31"),
    ]


@pytest.mark.parametrize("text", ["FY25 week 5-3", "week 5-3 of FY25", "months 5-3 of FY24",
                                  "FY24 M5-M3"])
def test_a_run_backwards_is_refused_with_the_reason(text):
    found, diags = diagnose(text, today=TODAY)
    assert found == [] and any("from the earlier to the later" in d.reason for d in diags)


@pytest.mark.parametrize("text", ["BMW M3", "500 m3", "M3", "the M3 motorway", "month 3",
                                  "the 3rd week for each team"])
def test_these_are_not_dates(text):
    assert parse(text, today=TODAY) == []
