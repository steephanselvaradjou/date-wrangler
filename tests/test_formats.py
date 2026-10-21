"""Every shape of date and range we know people write, each with the answer it must give.

The snapshot pins what the library *does*, so it can only catch a change. This pins what it
*should* do, from a list written without looking at the rules: standard formats, the way
people write dates in questions, and real requests (``additional_formats.txt``, read as is).
A shape that is wrong or missing fails here even if it has always been wrong.

Expected answers are written compactly:

    2024            the year            2024-03     the month        2024-03-15   the day
    A..B            from the start of A to the end of B;  "A.." onwards;  "..B" up to
    X ; Y           two matches, in order
    -               no match at all     !           no match, but a reason
    ~               not answered confidently: flagged at 0.5 or less, or declined with a reason

Phrases still answered wrongly are listed in KNOWN_GAPS and marked xfail(strict): fixing
one makes its test pass, which fails the run until it is taken off the list.
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path

import pytest

from date_wrangler import Basis, WranglerConfig, diagnose

CAL = WranglerConfig()
FY = WranglerConfig(year_basis=Basis.FISCAL)
TODAY = date(2026, 10, 16)  # a Friday
BANK_DAY = date(2018, 6, 13)  # the day the requests in additional_formats.txt were made

SECTIONS: list[tuple[str, date, WranglerConfig, list[tuple[str, str]]]] = [
    ("iso and machine formats", TODAY, CAL, [
        ("2024-03-15", "2024-03-15"),
        ("2024-3-5", "2024-03-05"),
        ("2024-03", "2024-03"),
        ("2024/03", "2024-03"),
        ("03/2024", "2024-03"),
        ("3/2024", "2024-03"),
        ("2024-W11", "2024-03-11..2024-03-17"),
        ("2024-W11-5", "2024-03-15"),
        ("2024-03-15T14:30", "2024-03-15"),
        ("2024-03-15 14:30:00", "2024-03-15"),
        ("2024-03-15T23:30:00-05:00", "2024-03-15"),
        ("2024.03.15", "2024-03-15"),
        ("15.03.2024", "2024-03-15"),
        ("15/03/24", "2024-03-15"),
        ("3/15/2024", "2024-03-15"),
        ("03-15-2024", "2024-03-15"),
        ("15-03-2024", "2024-03-15"),
        ("1/1/2010", "2010-01-01"),
        ("1998/1/1", "1998-01-01"),
        ("on 1993/11/12", "1993-11-12"),
        ("Fri, 15 Mar 2024 14:30:00 GMT", "2024-03-15"),
        ("DATE '2024-03-15'", "2024-03-15"),
        ("'2024-03-15'", "2024-03-15"),
        ("ticket 2024-03-15-001", "2024-03-15"),
    ]),
    ("written days", TODAY, CAL, [
        ("15 March 2024", "2024-03-15"),
        ("15th March 2024", "2024-03-15"),
        ("15th of March 2024", "2024-03-15"),
        ("the 15th of March, 2024", "2024-03-15"),
        ("March 15, 2024", "2024-03-15"),
        ("March 15th, 2024", "2024-03-15"),
        ("March 15 2024", "2024-03-15"),
        ("Mar. 15, 2024", "2024-03-15"),
        ("Sept. 15, 2024", "2024-09-15"),
        ("15-Mar-2024", "2024-03-15"),
        ("15 Mar '24", "2024-03-15"),
        ("15MAR2024", "2024-03-15"),
        ("15-MAR-24", "2024-03-15"),
        ("15/Mar/2024", "2024-03-15"),
        ("Mar-15-2024", "2024-03-15"),
        ("2024 March 15", "2024-03-15"),
        ("2024 Mar 15", "2024-03-15"),
        ("March the 15th, 2024", "2024-03-15"),
        ("the first of January 2024", "2024-01-01"),
        ("January first, 2024", "2024-01-01"),
        ("1st Jan 2024", "2024-01-01"),
        ("Jan 1st 2024", "2024-01-01"),
        ("1 Jan, 2024", "2024-01-01"),
        ("on 12 March, 2014", "2014-03-12"),
        ("on 30th August 2013", "2013-08-30"),
        ("Thursday 14th March 2024", "2024-03-14"),
        ("Sat 16/03/2024", "2024-03-16"),
        ("31st Dec 2023", "2023-12-31"),
        ("Dec 31st, 2023", "2023-12-31"),
        ("the second Tuesday in April 2024", "2024-04-09"),
        ("the 1st Monday in March 2024", "2024-03-04"),
        ("13june2018", "2018-06-13"),
        ("01may 2018", "2018-05-01"),
        ("18th may'18", "2018-05-18"),
        ("13th June' 2018", "2018-06-13"),
        # Read since the token reader: each was nothing, or wrong, before it.
        ("15th-Mar-2024", "2024-03-15"),
        ("15th/March/24", "2024-03-15"),
        ("15 March2024", "2024-03-15"),
        ("2024Mar15", "2024-03-15"),
        ("MAR15, 2024", "2024-03-15"),
        # Marks that change between the day, the month and the year. REGRESSION, each in
        # 2.1.0: the day was lost, or the 15 read as the year 2015, or a span came out.
        ("15-Mar 2024", "2024-03-15"),  # March 2024
        ("15/Mar 2024", "2024-03-15"),  # March 2024
        ("15.Mar.2024", "2024-03-15"),  # March 2024
        ("15-Mar, 2024", "2024-03-15"),  # March 2024
        ("15 Mar-2024", "2024-03-15"),  # 15 March to the end of 2024
        ("15 Mar/2024", "2024-03-15"),  # 15 March, and 2024
        ("Mar 15-2024", "2024-03-15"),  # 15 March to the end of 2024
        ("Mar 15/2024", "2024-03-15"),
        ("Mar-15 2024", "2024-03-15"),  # March 2015
        ("Mar-15, 2024", "2024-03-15"),  # March 2015, and 2024
        ("2024-Mar-15", "2024-03-15"),  # 2024, and March 2015
        ("2024/Mar/15", "2024-03-15"),  # nothing
        ("2024.Mar.15", "2024-03-15"),  # 15 March this year
        ("15-Mar", "2026-03-15"),  # March
        ("1-Mar to 15-Mar", "2026-03-01..2026-03-15"),
        # Still what they were: a dash alone settles "Mar-15" as a month, and a spaced dash
        # after the day is a span, as after any label.
        ("Mar-15", "2015-03"),
        ("15 Mar - 2024", "2024-03-15..2024-12-31"),
    ]),
    ("a weekday with its date", TODAY, CAL, [
        ("Friday, 15 March 2024", "2024-03-15"),
        ("Friday, March 15, 2024", "2024-03-15"),
        ("on Friday 15 March 2024", "2024-03-15"),
        ("Fri 15 Mar 2024", "2024-03-15"),
        ("Monday the 15th of April 2024", "2024-04-15"),
        ("last Friday, 15 March 2024", "2024-03-15"),
        ("Monday, 15 March 2024", "~"),  # 15 March 2024 was a Friday
    ]),
    ("months", TODAY, CAL, [
        ("March 2024", "2024-03"),
        ("Mar 2024", "2024-03"),
        ("Mar-2024", "2024-03"),
        ("Mar'24", "2024-03"),
        ("March of 2024", "2024-03"),
        ("in August of 2013", "2013-08"),
        ("during the month of August 2013", "2013-08"),
        ("the month of March 2024", "2024-03"),
        ("in the month of May 1998", "1998-05"),
        ("in June, 2013", "2013-06"),
        ("September, 2013", "2013-09"),
        ("products consumed in September, 2013.", "2013-09"),
        ("May, 2012", "2012-05"),
        ("Sept. 2013", "2013-09"),
        ("Sept., 2013", "2013-09"),
        ("Jan. 2024", "2024-01"),
        ("of month FEB.2018", "2018-02"),
        # A month and its year beside it are one period.
        ("June in 2024", "2024-06"),
        ("Mar/2024", "2024-03"),
        ("June/FY24", "2023-06"),
        ("Mar. FY24", "2024-03"),
        ("Mar., FY24", "2024-03"),
        ("2024 March", "2024-03"),
        ("2024-Mar", "2024-03"),  # was January to March 2024
        ("2024/March", "2024-03"),
        ("2024 - March", "2024-03"),  # the year first is a heading, as in "FY24 - Q3"
        ("FY24 - June", "2023-06"),
        ("15th of March/2024", "2024-03-15"),
        # Still apart: a spaced dash after the month is a span, and a full stop after a
        # month's full name ends a sentence. REGRESSION: "in March. 15 people came" was 15
        # March, in the token reader before release.
        ("June - 2024", "2024-06..2024-12"),
        ("in March. 2024 was good", "2026-03"),
        ("in March. 15 people came", "2026-03"),
    ]),
    ("years", TODAY, CAL, [
        ("in 2024", "2024"),
        ("the year 2024", "2024"),
        ("in the year 2012", "2012"),
        ("in year 2012", "2012"),
        ("during the year of 2012", "2012"),
        ("year of 2012", "2012"),
        ("In 1998, how many orders", "1998"),
        ("CY24", "2024"),
        ("calendar year 2024", "2024"),
        ("the 1998's figures", "1998"),
        ("in '24", "2024"),
        ("the year '24", "2024"),
    ]),
    ("fiscal years", TODAY, CAL, [
        ("FY24", "2023-04..2024-03"),
        ("FY 2024", "2023-04..2024-03"),
        ("FY2024-25", "2024-04..2025-03"),
        ("FY 24-25", "2024-04..2025-03"),
        ("FY23/24", "2023-04..2024-03"),
        ("FY 23/24", "2023-04..2024-03"),
        ("2023-24 financial year", "2023-04..2024-03"),
        ("financial year 2023-24", "2023-04..2024-03"),
        ("the fiscal year 2023-2024", "2023-04..2024-03"),
        ("fiscal 2024", "2023-04..2024-03"),
        ("fiscal year ending March 2024", "2023-04..2024-03"),
        ("the year ended 31 March 2024", "2023-04..2024-03"),
        ("FY ending June 30, 2024", "2023-07..2024-06"),
        ("FY22-24", "!"),  # not one fiscal year
        # With no FY, year_basis decides: on the calendar, two years. Fiscal is below.
        ("2024-25", "2024..2025"),
        ("sales 2024-25", "2024..2025"),
        ("2012-13", "2012..2013"),
        ("2012/13", "2012..2013"),
        ("2011-12", "2011-12"),  # also a month, in ISO; December 2011
        ("Q3 2024-25", "!"),  # a quarter of two calendar years is not one period
        ("June 2024-25", "!"),
        ("2024-30", "!"),  # not one fiscal year either way
    ]),
    ("split years on a fiscal basis", TODAY, FY, [
        ("2024-25", "2024-04..2025-03"),
        ("sales 2024-25", "2024-04..2025-03"),
        ("2012-13", "2012-04..2013-03"),
        ("2012/13", "2012-04..2013-03"),
        ("2011-12", "2011-04..2012-03"),
        ("Q3 2024-25", "2024-10..2024-12"),
        ("H1 2024-25", "2024-04..2024-09"),
        ("June 2024-25", "2024-06"),
        ("15 June 2024-25", "2024-06-15"),
        ("Q1 2024-25 vs Q1 2023-24", "2024-04..2024-06 ; 2023-04..2023-06"),
        ("2024-03", "2024-03"),  # not the year after, so a month on any basis
        ("FY2024-25", "2024-04..2025-03"),
        ("the 2015-16 season", "!"),  # a season is not a fiscal year either
    ]),
    ("quarters and halves", TODAY, CAL, [
        ("Q1 2024", "2024-01..2024-03"),
        ("Q1-2024", "2024-01..2024-03"),
        ("Q1/2024", "2024-01..2024-03"),
        ("Q1, 2024", "2024-01..2024-03"),
        ("2024 Q1", "2024-01..2024-03"),
        ("Q1 of 2024", "2024-01..2024-03"),
        ("1st quarter 2024", "2024-01..2024-03"),
        ("2nd quarter of 2024", "2024-04..2024-06"),
        ("quarter 2 2024", "2024-04..2024-06"),
        ("Qtr 2 2024", "2024-04..2024-06"),
        ("in the second quarter of 1998", "1998-04..1998-06"),
        ("Q1 FY24", "2023-04..2023-06"),
        ("Q1 of FY24", "2023-04..2023-06"),
        ("FY24 Q1", "2023-04..2023-06"),
        ("FY24-Q1", "2023-04..2023-06"),
        ("FY 2024 Q1", "2023-04..2023-06"),
        ("Q1 FY2024-25", "2024-04..2024-06"),
        ("Q3 FY 2024-25", "2024-10..2024-12"),
        ("FY2024-25 Q3", "2024-10..2024-12"),
        ("FY24-25 Q1", "2024-04..2024-06"),
        ("first quarter of fiscal 2024", "2023-04..2023-06"),
        ("H1 2024", "2024-01..2024-06"),
        ("H1, 2024", "2024-01..2024-06"),
        ("first half of 2024", "2024-01..2024-06"),
        ("in the 2nd half of 2012", "2012-07..2012-12"),
        ("H2 FY2024-25", "2024-10..2025-03"),
        ("9M 2024", "2024-01..2024-09"),
        ("9M FY24", "2023-04..2023-12"),
        ("the March quarter 2024", "2024-01..2024-03"),
        ("the quarter ended 31 March 2024", "2024-01..2024-03"),
        ("quarter ended 31/03/2024", "2024-01..2024-03"),
        ("the first 3 quarters of 2024", "2024-01..2024-09"),
        # A label and its year beside it are one period -- not two periods, nor a span from
        # this year's quarter to the whole year.
        ("Q3 in 2024", "2024-07..2024-09"),
        ("Q3/FY24", "2023-10..2023-12"),
        ("Qtr 3-2024", "2024-07..2024-09"),
        ("third quarter in FY 2024-25", "2024-10..2024-12"),
        ("FY 2024 Qtr 3", "2023-10..2023-12"),
        ("FY 2024-25 - Qtr 3", "2024-10..2024-12"),
        ("2024 third quarter", "2024-07..2024-09"),
        ("2024-3Q", "2024-07..2024-09"),  # REGRESSION: March 2024
        ("2024-1H", "2024-01..2024-06"),  # REGRESSION: January 2024
        ("2024/1H", "2024-01..2024-06"),
        ("1HFY24", "2023-04..2023-09"),
        ("1HCY 2024", "2024-01..2024-06"),
        ("1H FY-24", "2023-04..2023-09"),
        ("1H fiscal 2024", "2023-04..2023-09"),
        ("1H calendar year 2024", "2024-01..2024-06"),
        ("Q1 and Q3 in 2024", "2024-01..2024-03 ; 2024-07..2024-09"),
        # Still spans: "to" says so, so does a spaced dash after the label -- the way "June
        # - FY25" is written -- and so does a quarter with a year of its own.
        ("Q3 to 2024", "2024-07..2024-12"),
        ("Q3 - 2024", "2024-07..2024-12"),
        ("Q3 - FY24", "2023-10..2024-03"),
        ("2023 - H2 2024", "2023-01..2024-12"),
    ]),
    ("parts of a period", TODAY, CAL, [
        ("early 2024", "2024-01..2024-04"),
        ("mid-2024", "2024-05..2024-08"),
        ("end of March 2024", "2024-03-21..2024-03-31"),
        ("the end of March, 2024", "2024-03-21..2024-03-31"),
        ("first week of March 2024", "2024-03-01..2024-03-07"),
        ("first 10 days of March 2024", "2024-03-01..2024-03-10"),
        ("first fortnight of March 2024", "2024-03-01..2024-03-14"),
        ("the second half of March 2024", "2024-03-16..2024-03-31"),
        ("in the first six months of 2012", "2012-01..2012-06"),
        ("in the last three months of 2012", "2012-10..2012-12"),
        ("week ending 15 March 2024", "2024-03-09..2024-03-15"),
        ("week ending 15/03/2024", "2024-03-09..2024-03-15"),
        ("w/e 15/03/2024", "2024-03-09..2024-03-15"),
        ("the week of March 15, 2024", "2024-03-11..2024-03-17"),
        ("year end 2024", "2024"),
        ("year-end 2024", "2024"),
        ("the second half of the match", "~"),
    ]),
    ("spans of years", TODAY, CAL, [
        ("from 2012 to 2013", "2012..2013"),
        ("decrease rate from 2012 to 2013", "2012..2013"),
        ("2012-2013", "2012..2013"),
        ("2012 – 2013", "2012..2013"),
        ("2010 - 2015", "2010..2015"),
        ("2010—2015", "2010..2015"),
        ("between 2012 and 2013", "2012..2013"),
        ("from 2012 through 2013", "2012..2013"),
        ("2012 thru 2013", "2012..2013"),
        ("from 2012 until 2013", "2012..2013"),
        ("from the year 2010 to 2012", "2010..2012"),
        ("between the years 2010 and 2015", "2010..2015"),
        ("the years 2012 through 2014", "2012..2014"),
        ("years 2012-2014", "2012..2014"),
        ("in the years 1990 to 2000", "1990..2000"),
        ("for the years 2012 to 2014", "2012..2014"),
        ("for the period 2012-2014", "2012..2014"),
        ("during 1995-1997", "1995..1997"),
        ("in 2014-2015", "2014..2015"),
        ("from 1990s to 2000s", "1990..2009"),
        ("from FY22 to FY24", "2021-04..2024-03"),
        ("FY22-FY24", "2021-04..2024-03"),
    ]),
    ("spans of months and days", TODAY, CAL, [
        ("from March 2012 to May 2013", "2012-03..2013-05"),
        ("Dec 2023 - Jan 2024", "2023-12..2024-01"),
        ("January - March 2024", "2024-01..2024-03"),
        ("From November to December 1981", "1981-11..1981-12"),
        ("from Jan to Jun 2012", "2012-01..2012-06"),
        ("Jan - Mar '24", "2024-01..2024-03"),
        ("between Q1 2023 and Q2 2024", "2023-01..2024-06"),
        ("Q4 2023 - Q1 2024", "2023-10..2024-03"),
        ("15-20 March 2024", "2024-03-15..2024-03-20"),
        ("15 to 20 March 2024", "2024-03-15..2024-03-20"),
        ("15th-20th March 2024", "2024-03-15..2024-03-20"),
        ("March 15-20, 2024", "2024-03-15..2024-03-20"),
        ("15 March - 20 April 2024", "2024-03-15..2024-04-20"),
        ("March 15 to April 20, 2024", "2024-03-15..2024-04-20"),
        ("between Jan 1 and Mar 31, 2024", "2024-01-01..2024-03-31"),
        ("Jan 1 - Mar 31, 2024", "2024-01-01..2024-03-31"),
        ("January 1st through March 31st, 2024", "2024-01-01..2024-03-31"),
        ("1 Jan 2024 – 31 Mar 2024", "2024-01-01..2024-03-31"),
        ("between 1/1/2010 and 12/31/2010", "2010-01-01..2010-12-31"),
        ("from 1/1/2012 to 6/30/2012", "2012-01-01..2012-06-30"),
        ("2024-03-15 to 2024-03-20", "2024-03-15..2024-03-20"),
        ("2024-03-15/2024-03-20", "2024-03-15..2024-03-20"),
        ("2024-03-15 - 2024-03-20", "2024-03-15..2024-03-20"),
        ("from 2024-01-01 until 2024-03-31", "2024-01-01..2024-03-31"),
        ("15.03.2024 - 20.03.2024", "2024-03-15..2024-03-20"),
        ("01.01.2024–31.03.2024", "2024-01-01..2024-03-31"),
        ("1998/1/1 to 1998/12/31", "1998-01-01..1998-12-31"),
        ("the period from 1 April 2023 to 31 March 2024", "2023-04-01..2024-03-31"),
        ("between the 1st and 15th of March 2024", "2024-03-01..2024-03-15"),
        ("between March 1st and March 15th, 2024", "2024-03-01..2024-03-15"),
        ("from start of 2012 to end of 2013", "2012..2013"),
        ("June, 2013 to August, 2013", "2013-06..2013-08"),
        ("Jun FY25 - 15 March", "2024-06-01..2025-03-15"),
        ("Jun FY25 - 03/04/2024", "2024-06 ; 2024-04-03"),  # runs backwards: two, and why
        ("Jun FY25 - 2023", "2024-06 ; 2023"),
        ("March 15 to 03/04/2024", "2024-03-15..2024-04-03"),
        ("March 15, 2024-03-15", "2024-03-15 ; 2024-03-15"),
    ]),
    ("spans up to now", TODAY, CAL, [
        ("2012 to date", "2012-01-01..2026-10-16"),
        ("from 2012 to date", "2012-01-01..2026-10-16"),
        ("15 March 2024 to date", "2024-03-15..2026-10-16"),
        ("from 2012 till today", "2012-01-01..2026-10-16"),
        ("from 2012 to present", "2012-01-01..2026-10-16"),
        ("2012-present", "2012-01-01..2026-10-16"),
        ("from March 2024 to now", "2024-03-01..2026-10-16"),
        ("Mar 2024 to present", "2024-03-01..2026-10-16"),
        ("YTD 2024", "2024"),
        ("2024 YTD", "2024"),
        ("the report is up to date", "-"),
        ("we want to present the results", "-"),
        # Each was nothing, two dates, or a different period, in 2.1.0.
        ("from 01/06/2018 till date", "2018-06-01..2026-10-16"),  # open-ended: its end lost
        ("since 1 June 2018 till date", "2018-06-01..2026-10-16"),
        ("1 June 2018 up till now", "2018-06-01..2026-10-16"),
        ("1 June 2018 till present", "2018-06-01..2026-10-16"),
        ("1 June 2018 to-date", "2018-06-01..2026-10-16"),
        ("1 June 2018 onwards till date", "2018-06-01..2026-10-16"),
        ("1 June 2018 through to today", "2018-06-01..2026-10-16"),
        ("till present", "..2026-10-16"),
        ("as of now", "2026-10-16"),
        ("YTD", "2026-01-01..2026-10-16"),
        ("year-to-date", "2026-01-01..2026-10-16"),
        ("Y-T-D", "2026-01-01..2026-10-16"),
        ("so far this year", "2026-01-01..2026-10-16"),
        ("this quarter so far", "2026-10-01..2026-10-16"),  # was the whole quarter
        ("WTD", "2026-10-12..2026-10-16"),
        ("fiscal YTD", "2026-04-01..2026-10-16"),  # was the calendar year to date
        ("fiscal year to date", "2026-04-01..2026-10-16"),
        ("FYTD", "2026-04-01..2026-10-16"),
        ("FY to date", "2026-04-01..2026-10-16"),
        ("CYTD", "2026-01-01..2026-10-16"),
        ("LYTD", "2025-01-01..2025-10-16"),
        ("YTD last year", "2025-01-01..2025-10-16"),
        ("last MTD", "2026-09-01..2026-09-16"),  # the same days last month: was a year back
        ("MTD last month", "2026-09-01..2026-09-16"),
        ("last QTD", "2026-07-01..2026-07-16"),
        ("last year's MTD", "2025-10-01..2025-10-16"),
        ("2024-YTD", "2024"),  # was 2024 to today
        ("YTD for 2024", "2024"),
        ("YTD through March 2024", "2024-01..2024-03"),
        ("March 2024 YTD", "2024-01..2024-03"),
        ("YTD Mar-24", "2024-01..2024-03"),
        # Still two: a comma lists, and "to" spans.
        ("YTD, 2024", "2026-01-01..2026-10-16 ; 2024"),
    ]),
    ("open-ended", TODAY, CAL, [
        ("since 2012", "2012.."),
        ("after 2012", "2013.."),
        ("before 2012", "..2011"),
        ("prior to 2012", "..2011"),
        ("earlier than 2012", "..2011"),
        ("later than 2012", "2013.."),
        ("after the year 2012", "2013.."),
        ("post-2012", "2013.."),
        ("pre-2012", "..2011"),
        ("2012 onwards", "2012.."),
        ("2012 or later", "2012.."),
        ("2012 and later", "2012.."),
        ("2012 and after", "2012.."),
        ("2012 and earlier", "..2012"),
        ("in or after 2012", "2012.."),
        ("from the year 1990 onwards", "1990.."),
        ("starting 2012", "2012.."),
        ("starting from 2012", "2012.."),
        ("through 2012", "..2012"),
        ("by 2012", "..2012"),
        ("until 2012", "..2012"),
        ("up until 2012", "..2012"),
        ("no earlier than 2012", "2012.."),
        ("before 2000-01-01", "..1999-12-31"),
        ("after 2012-08-15", "2012-08-16.."),
        ("on or after 1 April 2024", "2024-04-01.."),
    ]),
    ("lists", TODAY, CAL, [
        ("2012, 2013 and 2014", "2012 ; 2013 ; 2014"),
        ("in 2012 or 2013", "2012 ; 2013"),
        ("March or April 2024", "2024-03 ; 2024-04"),
        ("Jan/Feb 2024", "2024-01 ; 2024-02"),
        ("Jan/Feb/Mar 2024", "2024-01 ; 2024-02 ; 2024-03"),
        ("March, April and May 2024", "2024-03 ; 2024-04 ; 2024-05"),
        ("in the years 2012 and 2013", "2012 ; 2013"),
        ("in 2012 and 2013 respectively", "2012 ; 2013"),
        ("in the 2000s and 2010s", "2000..2009 ; 2010..2019"),
        ("Q1 & Q2 2024", "2024-01..2024-03 ; 2024-04..2024-06"),
        ("in Q1'24 and Q2'24", "2024-01..2024-03 ; 2024-04..2024-06"),
        ("on the 15th and 16th of March 2024", "2024-03-15 ; 2024-03-16"),
        ("March 15th and 16th, 2024", "2024-03-15 ; 2024-03-16"),
        ("the first quarter of 2024 and the last quarter of 2023",
         "2024-01..2024-03 ; 2023-10..2023-12"),
        ("revenue in Q1 and Q3 2024", "2024-01..2024-03 ; 2024-07..2024-09"),
        ("revenue in Q1 and Q3 2024 vs 2023", "2024-01..2024-03 ; 2024-07..2024-09 ; 2023"),
        ("Q1 and Q3 2024 compared to Q1 and Q3 2023",
         "2024-01..2024-03 ; 2024-07..2024-09 ; 2023-01..2023-03 ; 2023-07..2023-09"),
        ("2015 to 2016, 2016 to 2017", "2015..2016 ; 2016..2017"),
    ]),
    ("comparisons", TODAY, CAL, [
        ("2023 vs. 2022", "2023 ; 2022"),
        ("2023 v 2022", "2023 ; 2022"),
        ("2023 v. 2022", "2023 ; 2022"),
        ("2023 over 2022", "2023 ; 2022"),
        ("1H 2024 vs 2H 2023", "2024-01..2024-06 ; 2023-07..2023-12"),
        ("Q3 2024 vs Q2", "2024-07..2024-09 ; 2024-04..2024-06"),
        ("Q1 2024 versus the same quarter last year", "2024-01..2024-03 ; 2023-01..2023-03"),
        ("March 2024 compared to the previous month", "2024-03 ; 2024-02"),
        ("2024 vs prior year", "2024 ; 2023"),
        ("2024 vs the year before", "2024 ; 2023"),
    ]),
    ("relative to today", TODAY, CAL, [
        ("last March", "2026-03"),
        ("this March", "2026-03"),
        ("next March", "2027-03"),
        ("March this year", "2026-03"),
        ("March next year", "2027-03"),
        ("last Q1", "2026-01..2026-03"),
        ("Q1 this year", "2026-01..2026-03"),
        ("previous year", "2025"),
        ("prior year", "2025"),
        ("the year before", "!"),  # before what? A date beside it says: "2024 vs the year before"
        ("the previous quarter", "2026-07..2026-09"),
        ("the following month", "2026-11"),
        ("the preceding month", "2026-09"),
        ("the prior month", "2026-09"),
        ("the prior quarter", "2026-07..2026-09"),
        ("the coming year", "2027"),
        ("the upcoming quarter", "2027-01..2027-03"),
        ("the present year", "2026"),
        ("last 5 years", "2021..2025"),
        ("over the past two years", "2024..2025"),
        ("the last 12 months", "2025-10..2026-09"),
        ("the past decade", "2016..2025"),
        ("in the last decade", "2016..2025"),
        ("a decade ago", "2016"),
        ("2 yrs ago", "2024"),
        ("3 mos ago", "2026-07"),
        ("6 wks ago", "2026-08-31..2026-09-06"),
        ("between 2 and 5 years ago", "~"),
        ("on the 31st of December last year", "2025-12-31"),
        ("2nd March last year", "2025-03-02"),
        ("Monday to Friday last week", "2026-10-05..2026-10-09"),
        ("Friday last week", "2026-10-09"),
        ("Monday next", "2026-10-19"),
        ("the final quarter", "2026-10..2026-12"),
        ("the quarter before last", "2026-04..2026-06"),
        ("the month before last", "2026-08"),
        ("the year before last", "2024"),
        ("the week before last", "2026-09-28..2026-10-04"),
        ("this morning", "~"),  # today, but only part of it
        ("tonight", "~"),
        ("recently", "!"),  # too vague to answer
        ("a few days ago", "!"),
        ("the other day", "!"),
        ("some time ago", "!"),
        ("every Monday", "!"),  # a recurrence, not a period
        # Each was nothing, or read in part, in 2.1.0.
        ("last 3months", "2026-07..2026-09"),  # a count against its unit
        ("3mos ago", "2026-07"),
        ("in 3days", "2026-10-19"),
        ("trailing 12-month revenue", "2025-10..2026-09"),
        ("3qtrs ago", "2026-01..2026-03"),  # REGRESSION: the third quarter of this year
        ("last 3 mths", "2026-07..2026-09"),
        ("last mth", "2026-09"),
        ("prev month", "2026-09"),
        ("last Weds", "2026-10-14"),
        ("yday", "2026-10-15"),
        ("y'day", "2026-10-15"),
        ("tmrw", "2026-10-17"),
        ("day before yday", "2026-10-14"),
        ("last week Fri", "2026-10-09"),  # the week came back, the day lost
        ("last week's Friday", "2026-10-09"),
        ("Fri. last week", "2026-10-09"),
        ("this week on Monday", "2026-10-12"),
        ("last 5 biz days", "2026-10-09..2026-10-15"),
        ("5 workdays ago", "2026-10-09"),
        ("next workday", "2026-10-19"),
        ("same month of last year", "2025-10"),  # last year, whole
        ("same month a year ago", "2025-10"),
        ("same quarter LY", "2025-10..2025-12"),
        ("this wknd", "2026-10-17..2026-10-18"),
        ("a couple of weeks ago", "2026-09-28..2026-10-04"),
        ("last couple of months", "2026-08..2026-09"),
        # Single letters stay unread: "3m" is minutes or millions as often as months.
        ("3m ago", "-"),
        ("last 3 m", "-"),
    ]),
    ("counted from another date", TODAY, CAL, [
        ("3 days before 15 March 2024", "2024-03-12"),
        ("two weeks after 1 April 2024", "2024-04-15"),
        ("a year after March 2024", "2025-03"),
        ("the day after 15 March 2024", "2024-03-16"),
        ("the day before 15 March 2024", "2024-03-14"),
        ("the month before March 2024", "2024-02"),
        ("the week before 15 March 2024", "~"),
        ("within 30 days of 15 March 2024", "~"),
    ]),
    ("decades and seasons", TODAY, CAL, [
        ("the 1990s", "1990..1999"),
        ("1990s", "1990..1999"),
        ("the 1990's", "1990..1999"),
        ("in the 90s", "1990..1999"),
        ("the 2010s", "2010..2019"),
        ("the eighties", "1980..1989"),
        ("the early 1990s", "1990..1993"),
        ("mid-1990s", "1994..1996"),
        ("the late 90s", "1997..1999"),
        ("the '90s", "1990..1999"),
        ("the nineties", "1990..1999"),
        ("summer 2024", "!"),  # which months depends on the hemisphere
        ("winter 2023/24", "!"),
        ("last summer", "!"),
        # A season, a school year: a name with months of its own, not the years it spans.
        ("the 2015–2016 season", "!"),
        ("in the 2015/16 season", "!"),
        ("goals scored in the 2015-16 season", "!"),
        ("season 2015/2016", "!"),
        ("the 2020 season", "!"),
        ("academic year 2015-16", "!"),
        ("the 2015-16 school year", "!"),
        ("2015–2016", "2015..2016"),  # with no such word, still the years
    ]),
    ("not dates", TODAY, CAL, [
        ("3/4 cup of flour", "-"),
        ("a 50/50 split", "-"),
        ("version 2.1.2024", "-"),
        ("release 1.5.3", "-"),
        ("server 10.0.0.1", "-"),
        ("rating 4.5/5", "-"),
        ("page 3/4", "-"),
        ("score 3-2", "-"),
        ("room 1015", "-"),
        ("3 to 5 years of experience", "-"),
        ("between 1 and 5 items", "-"),
        ("May I ask a question", "-"),
        ("let's march forward", "-"),
        ("the sun is out", "-"),
        ("I sat down", "-"),
        ("to mar the event", "-"),
        ("an august institution", "-"),
        ("Jan Smith said hello", "-"),
        ("Windows 2000 machines", "-"),
        ("COVID-19 cases", "-"),
        ("1990 employees", "-"),
        ("call 1800 123 456", "-"),
        ("dimensions 2024 x 1080", "-"),
        ("provide monthly statement", "-"),
        ("fortnightly", "-"),
        ("weekdays", "-"),
        ("the march on Washington", "-"),
        ("sales by June Patel", "-"),
        ("Order number 2024 is pending", "-"),
        ("Sales increased by 25 percent", "-"),
    ]),
]

#: additional_formats.txt, a line each: real requests for bank statements, read on the day
#: they were made. Keyed by the line with its spacing tidied; the test reads the file as is.
BANK = {
    "01st May 2018 to till date": "2018-05-01..2018-06-13",
    "01/06/18 till today": "2018-06-01..2018-06-13",
    "01/04/2018 to 31/05/2018": "2018-04-01..2018-05-31",
    "For the month of May": "2018-05",
    "01.05.2018 to till date": "2018-05-01..2018-06-13",
    "April 2015 to till date": "2015-04-01..2018-06-13",
    "1-4-2018-31-5-2018": "2018-04-01..2018-05-31",
    "13/06/2018": "2018-06-13",
    "01.06.2018 to 14.06.2018": "2018-06-01..2018-06-14",
    "6/13/2018": "2018-06-13",
    "13.06.2018": "2018-06-13",
    "01.01.17 to 31.05.18": "2017-01-01..2018-05-31",
    "01/06/2018 to till date": "2018-06-01..2018-06-13",
    "12.06.2018 & 13.06.2018": "2018-06-12 ; 2018-06-13",
    "01-04-2018 to 02-05-2018": "2018-04-01..2018-05-02",
    "May 2018": "2018-05",
    "13.06.18": "2018-06-13",
    "1/5/18 to 7/6/18": "2018-05-01..2018-06-07",
    "from the opening of the account till 13th June’ 2018": "..2018-06-13",
    "today's bank statement": "2018-06-13",
    "11.5.2018 to 14.6.2018": "2018-05-11..2018-06-14",
    "11-06-18": "2018-06-11",
    "from beginning to till date": "..2018-06-13",
    "13 june 2018 to 13 June 2018": "2018-06-13",
    "11 june 2018 to 11 June 2018": "2018-06-11",
    "01-05-2018 to till date": "2018-05-01..2018-06-13",
    "1 march 2018 to till date": "2018-03-01..2018-06-13",
    "1st dec 2017 to till date": "2017-12-01..2018-06-13",
    "01may 2018 to 13june2018": "2018-05-01..2018-06-13",
    "1st June 2017 till 31st May 2018": "2017-06-01..2018-05-31",
    "1-4-2018 TO 31/05/2018": "2018-04-01..2018-05-31",
    "01.04.2017 till dated": "2017-04-01..2018-06-13",
    "01-Apr-17 to 31-Mar-18": "2017-04-01..2018-03-31",
    "February & march 2018": "2018-02 ; 2018-03",
    "01.06.18 to till Date": "2018-06-01..2018-06-13",
    "1.05.18 to 31.05.2018": "2018-05-01..2018-05-31",
    "1.5.2018 to 13.6.2018": "2018-05-01..2018-06-13",
    "last six month": "2017-12..2018-05",
    "April 01, 2018 to till date": "2018-04-01..2018-06-13",
    "11 June 2018 to 12 June 2018": "2018-06-11..2018-06-12",
    "01-04-2018 to till date": "2018-04-01..2018-06-13",
    "1st march 2018 to as on date": "2018-03-01..2018-06-13",
    "01 MAY 2018 TO 31 MAY 2018": "2018-05-01..2018-05-31",
    "opening of the account till 12th June’ 2018": "..2018-06-12",
    "Jan-18 & march-18": "2018-01 ; 2018-03",
    "01/09/2017 to 30/09/2017": "2017-09-01..2017-09-30",
    "current financial year": "2018-04..2019-03",
    "2015 to 2016 , 2016 to 2017 , 2017 to 2018": "2015..2016 ; 2016..2017 ; 2017..2018",
    "provide monthly statement": "-",
    "as on date": "2018-06-13",
    "last 3 months": "2018-03..2018-05",
    "inception to till date": "..2018-06-13",
    "of month FEB.2018": "2018-02",
    "18th may'18 to 12th jun'18": "2018-05-18..2018-06-12",
    "8th June 2018 to 13th June 2018": "2018-06-08..2018-06-13",
    "10th June 2018-to till date": "2018-06-10..2018-06-13",
    "1ST JUNE 2018 TO TILL DATE": "2018-06-01..2018-06-13",
    "JUNE 1 2018 TO TILL DATE": "2018-06-01..2018-06-13",
    "for last 15 days": "2018-05-29..2018-06-12",
}
BANK_FILE = Path(__file__).resolve().parent / "additional_formats.txt"

#: Answered wrongly or not at all today. Each is a bug to fix, not an answer to keep.
KNOWN_GAPS: frozenset[str] = frozenset()


def _whole(token: str) -> tuple[date, date]:
    parts = [int(p) for p in token.split("-")]
    if len(parts) == 1:
        return date(parts[0], 1, 1), date(parts[0], 12, 31)
    if len(parts) == 2:
        y, m = parts
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    d = date(*parts)
    return d, d


def _expected(want: str) -> list[tuple[date | None, date | None]]:
    out = []
    for item in want.split(" ; "):
        if ".." in item:
            a, b = item.split("..")
            out.append((_whole(a)[0] if a else None, _whole(b)[1] if b else None))
        else:
            out.append(_whole(item))
    return out


def _tidy(line: str) -> str:
    return " ".join(line.split())


def check(phrase: str, want: str, today: date, cfg: WranglerConfig) -> None:
    matches, diags = diagnose(phrase, today=today, config=cfg)
    got = [(m.range.start, m.range.end_inclusive) for m in matches]
    seen = [(m.text, str(m.range), m.confidence) for m in matches], [d.reason for d in diags]
    if want == "-":
        assert not matches, seen
    elif want == "!":
        assert not matches and diags, seen
    elif want == "~":
        assert (not matches and diags) or any(m.confidence <= 0.5 for m in matches), seen
    else:
        assert got == _expected(want), seen


def _cases():
    for name, today, cfg, cases in SECTIONS:
        for phrase, want in cases:
            yield pytest.param(
                phrase, want, today, cfg, id=f"{name}: {phrase}",
                marks=[pytest.mark.xfail(strict=True)] if phrase in KNOWN_GAPS else [],
            )


@pytest.mark.parametrize("phrase,want,today,cfg", list(_cases()))
def test_each_shape_reads_as_it_should(phrase, want, today, cfg):
    check(phrase, want, today, cfg)


def _bank_lines() -> list[str]:
    return [line for line in BANK_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize(
    "line",
    [
        pytest.param(
            line, id=_tidy(line),
            marks=[pytest.mark.xfail(strict=True)] if _tidy(line) in KNOWN_GAPS else [],
        )
        for line in dict.fromkeys(_bank_lines())
    ],
)
def test_each_real_request_reads_as_it_should(line):
    """Read exactly as written, odd spacing and non-breaking spaces included."""
    check(line, BANK[_tidy(line)], BANK_DAY, CAL)


def test_every_line_of_the_request_file_has_an_answer():
    """A line added to additional_formats.txt must come with the answer it should give."""
    missing = [_tidy(line) for line in _bank_lines() if _tidy(line) not in BANK]
    assert not missing, missing


def test_no_phrase_is_listed_twice_with_different_answers():
    """Under one configuration; "2024-25" rightly has one answer per year_basis."""
    seen: dict[tuple[str, str], str] = {}
    for _, _, cfg, cases in SECTIONS:
        for phrase, want in cases:
            assert seen.setdefault((phrase, repr(cfg)), want) == want, phrase
