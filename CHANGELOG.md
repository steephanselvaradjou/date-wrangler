# Changelog

Every release, newest first. From 1.0.0 the public API follows semantic versioning: a
change that breaks existing callers comes only with a new major version. Entries marked
**changes answers** return different dates or confidence for text that parsed before.

The full notes for each release, with examples, are on the
[releases page](https://github.com/steephanselvaradjou/date-wrangler/releases).

## Unreleased

**Changes answers.**

### Fixed
- `not later than March 2024` was *after* March, the "not" unread; it is up to the end
  of March. `on and after 15 March` began on the 16th, and `on and before` ended on the
  14th; both keep the day.
- These were the period alone, the bound dropped: `w.e.f.`, `wef`, `with effect from`,
  `as from`, `til`, `untill`, `uptil`, `until and including`, `pre March 2024`, `ahead
  of`, `preceding`, `older than`, `subsequent to`, `beyond`, `from 2012 forward`, `from
  Q1 on`, and as a query writes them, `>=`, `<=`, `>`, `<`, `≥`, `≤`.
- `3 days earlier than 15 March 2024`, `3 days ahead of 15 March 2024`, `two weeks later
  than 1 April 2024` and `3 business days before 15 March 2024` counted from today, with
  the date beside them a second answer; `two weeks from 1 April 2024`, `1 April 2024 + 2
  weeks` and `15 March 2024 minus 3 days` were the date alone. Each is the date moved by
  the count, business days stepping over weekends and holidays. `a year from March` is
  still flagged: it may be the twelve months starting in March.
- `beginning March 2024` was 1–10 March; it is March 2024 onwards, as a notice says
  "beginning 1 April". `the beginning of March` is still the start of March.
- `by the end of 2024-03` was refused as two calendar years, and `end of 2024-03-15` was
  the end of 2024; a month in numbers can now have a part taken out of it.
- `a month from the 15th of March` was flagged as partly read; it is 15 April.
- A day's two-digit year was dropped whenever the mark changed between its parts: `15
  Mar-24`, `15-March 24`, `15/March.24`, `15 MAR24` and `15 of March 24` were 15 March of
  this year. `15 of March24` was the 24th of this March; `15thMarch 2024` and
  `March/15/2024` were nothing, `March/15th, 2024` three dates, `15Mar. 2024` this year's
  15 March. Each is 15 March 2024. Left alone on purpose: two digits after a comma (`on
  15 Mar, 24 orders shipped`) and `Mar.15.24`, a decimal as often once the month's full
  stop is read.
- A quarter, half or month run together with its year, or written out before one: `Qtr
  3-24` was this year's Q3; `2024QTR3`, `FY24Qtr3`, `FY-24Q3`, `FY-24H1`, `2024March`,
  `MarchFY24`, `FY24March` and `CY2024Mar` were nothing; `FY2024-25QTR3` was FY2024, and
  `FY2024-25March` a span from FY2024 to the 25th of March. Each is its period. A
  lower-case digit-first `3q` or `1h` is still a quarter or half only with a year straight
  after it -- `1h` is an hour as often. `tests/test_label_years.py` adds them; 32 of its
  cases failed in 2.3.0.
- `tests/test_day_spellings.py` now holds 2,620 spellings of a day, its month and year,
  and 288 left alone on purpose; 823 of its cases failed in 2.3.0.
- `tests/test_bound_spellings.py`: 627 spellings of open-ended periods, bounds, counts
  from a date and "as of"; 166 of its cases failed in 2.3.0. Left alone on purpose:
  `post March` without its hyphen, which is a verb as often, and `forward` or `on`
  without a `from` before the date.

## 2.3.0 — 2026-10-23

Two periods joined as the words between them say, whatever the words: comparisons, lists
and spans with every connector people write, and a period named as another moved back.
The API is unchanged. **Changes answers.**

### Fixed
- `compare claims paid in Q1 FY25 with Q1 FY26` gave two unrelated quarters: "with" after
  "compare" joined nothing, where "vs", "and" and "to" made one comparison. It is one
  comparison now, so `group` and `joined_by` say so. On its own "with" still joins
  nothing: `sales in Q1 with returns in Q2` is two unrelated quarters.
- For the same reason, a relative period after "compare … with" counted from today:
  `compare Q1 2024 with the same quarter last year` was Q4 2025, and `compare March 2024
  with the previous month` September 2026. They are Q1 2023 and February 2024.
- `compare 2023 with 2024`, `compare 2023 to 2024` and `comparing 2023 vs 2024` found
  nothing: a bare year needs a word that marks it as a date, and "compare" was not one.
  It is now, with `comparing`, `comparison`, `benchmark` and `contrast`.
- These gave two unrelated periods, and a bare year after them was not read at all; each
  is a comparison now: `v/s`, `v.s.`, `as against`, `as compared to`, `in comparison
  with`, `compared against`, `vis-a-vis`, `how does A compare to B`, `A stacks up against
  B`, `more in A than B`, and the measure written again on either side — `claims in Q1
  FY25 vs claims in Q1 FY26`, `Q1 FY25 claims vs Q1 FY26 claims`.
- Likewise lists: `as well as`, `along with`, `together with`, `plus`, `+`.
- `the difference between Q1 and Q3`, and the variance, gap or delta between them, ran
  from Q1 to Q3, holding Q2, which nobody named. It compares the two quarters. `between
  Q1 and Q3` alone is still the span, and `growth from 2012 to 2013` still a span with
  its two ends.
- `March 2024 vs a year earlier` was all of 2025, `Q1 2024 vs the same period last year`
  all of last year, `compare March 2024 to the previous month` September 2026, and `vs
  LY`/`vs PY` were not read. Each is the period it is compared with, moved back: March
  2023, Q1 2023, February 2024, and `YTD vs LY` last year's YTD. `the same period last
  year` with nothing beside it is declined with a reason.
- `compare 2024 and prior year` was "up to 2024" and 2023; it is 2024 and 2023. `2012 and
  prior years` is still up to 2012.
- In `revenue in March Vs June`, March was taken for a person ("Vs" for a surname).
- `tests/test_comparison_spellings.py`: 1,081 phrases joining two periods with every
  connector; 467 of them failed in 2.2.0.

## 2.2.0 — 2026-10-21

Dates with month names read from their words and numbers, not one pattern per spelling,
and each family of phrase measured against its plain form -- thousands of spellings that
were read wrongly or not at all. The API is unchanged. **Changes answers.**

### Added
- Read for the first time: a quarter, half or month and the year beside it — `Q3 in 2024`,
  `Q3/FY24`, `FY 2024 Qtr 3`, `2024 - third quarter`, `1HFY24`, `1HCY 2024`,
  `1H fiscal 2024`, `June in 2024`, `Mar/2024`, `Mar. FY24`, `2024 March` — and a month
  that touches its numbers: `15 March2024`, `2024Mar15`, `MAR15, 2024`, `15th/March/24`.
  A spaced dash after the label is still a span, as before: `June - FY25`, `Q3 - 2024`.
- `tests/test_label_years.py`: every quarter, half and month label, each way of joining
  it to its year, every way of writing the year, in both orders.
- Relative phrases spelt short, as in chat and reports: a count against its unit (`last
  3months`, `3mos ago`, `trailing 12-month`), short units (`mth`, `mths`, `mnths`),
  `prev`, `Weds`, `yday`, `y'day`, `tmrw`, `tmr`, `day before yday`, `wknd`, `biz days`,
  `workdays`, `business-days`, `a couple of weeks ago`, `same month of last year`,
  `same quarter LY`, and the week before its weekday: `last week Fri`, `last week's
  Friday`, `Fri. last week`. A single letter for a unit (`3m`, `3d`, `last q`) stays
  unread: it is minutes or millions as often as months.
- `tests/test_relative_spellings.py`: 927 spellings of relative phrases, each held to its
  plain form.
- To-date phrases however they are written: `year-to-date`, `Y-T-D`, `so far this year`,
  `this quarter so far`, `WTD` and `week to date`, `FYTD`, `fiscal YTD`, `FY to date`,
  `CYTD`, `LYTD`, `PYTD`, `LMTD`, `YTD last year`, `MTD last month`, `last year's MTD`,
  `YTD for 2024`, `2024-YTD`, `YTD through March 2024`, `March 2024 YTD`, `YTD Mar-24`;
  and the ends people write after a start date: `till present`, `up till now`, `to-date`,
  `uptill date`, `till dt`, `onwards till date`, `through to today`, `as of now`.
  `tests/test_to_date_spellings.py` holds 382 of them.
- Parts and edges of a period written other ways: `end-of-month`, `E.O.M.`, `end of 1Q
  2024`, `end of the first quarter of 2024`, `the back half of 2024`, `the early part of
  2024`, `last workday of the month`, `the 3rd Thurs. of Nov.`, `first wk of April`,
  `last 3 mths of 2024`, `15th of the month`, `the 1st of the next month`, `1st to the
  15th of March`, `wc 5 Oct`, `week commencing Monday 5 October`, and reporting shorthand
  `QE June 2024`, `YE March 2024`, `FYE March 2024`, `12M to March 2024`, `6M to June
  2024`, `the 12-month period ended March 2024`. `tests/test_part_spellings.py` holds 165.
- Numbers written other ways: `03-2024`, `2024wk11`, `week no. 11 2024`, `W11/2024`,
  `yr 2024`, `throughout 2024`, and runs of weeks — `weeks 11-12 2024`, `week 11 to 12`.
  Left alone on purpose: a run of digits (`20240315`, as likely an order number), parts
  joined by underscores (`sales_2024` is a table), a month with a dot (`2024.03` is as
  likely an amount) and `2024M03` (as likely square metres). `tests/test_number_spellings.py`
  holds 104 spellings, and 16 numbers that must stay numbers.
- Declined with a reason, where before there was nothing: `the past few weeks`, `in recent
  months`, `in the next few days`, `a few weeks from now`, `not long ago`, `a while back`,
  `some time back`, `summer-2024`, `the 2015-16 flu season`. `tests/test_declined_spellings.py`
  holds 77 phrases that must stay unanswered.

### Fixed
- `2024 week 11` was week 11 of this year; `week number 11 of 2024` was 11 January;
  `W11-2024` and `week 11-2024` ran from the week to the end of 2024, and `2024-wk11` from
  the start of 2024 to the week; `week 11-12` was week 11 alone. Each is its week, or its
  run of weeks, in 2024.
- `every first Monday of the month` was this month's first Monday, and `the summer months
  of 2024` the whole of 2024; both are declined, as `every Monday` and `summer 2024` are.
  `every Monday in March` is still March.
- `AY 2015-16`, `SY 2015-16`, `the 2015-16 NBA season`, `the 2015-16 Premier League
  season`, `the 2015-16 campaign` and `the 2015-16 term` were two calendar years; they are
  labels, as `the 2015-16 season` is. `2015-16 sales by season` is still the years.
- `2024-Mar` and `2024 - March` were January to March 2024, and `FY24 - June` the start
  of FY24 to June; they are the month, as `FY24 - Q3` is the quarter.
- `2024-3Q` was March 2024, and `2024-1H` January 2024.
- `3qtrs ago` was the third quarter of this year; `last week Fri` was all of last week;
  `same month of last year` was the whole of last year.
- `fiscal year to date`, `fiscal YTD` and `FY YTD` were the *calendar* year to date: the
  fiscal year was dropped. They start at the fiscal year's first day.
- `from 01/06/2018 till date`, and any `from` or `since` a day with an end written after
  it, was open-ended -- the end dropped -- where `from 2012 to date` ended today. It ends
  today.
- **Changes answers:** `last MTD` and `last QTD` went back a year; they are the same days
  of the previous month and quarter, as LMTD means. `last YTD` is unchanged, and `last
  year's MTD` is the month so far a year back.
- `2024-YTD` ran from 2024 to today; it is 2024's year to date, as `2024 YTD` is.
  `this year so far` was the whole year, future included.
- `last day of month`, `last business day of month` and `last Friday of month` -- no
  "the" -- were yesterday and last week's Friday; `first-half of March 2024` was January
  to June 2024; `2024 year end` was the end of this year; `start of Mar-24` was this
  year's March; `Q1-end 2024` was the whole of 2024 (it is Q1 2024, as `year-end 2024`
  is the year).
- `15th-Mar-2024` was 15 October of this year and March 2024; `15 March2024` was
  15 March of this year.
- A day whose marks change between its parts: `15-Mar 2024`, `15/Mar 2024` and
  `15.Mar.2024` were March 2024, the day lost; `Mar-15 2024` was March 2015, and
  `2024-Mar-15` 2024 and March 2015; `15 Mar-2024` and `Mar 15-2024` ran to the end of
  2024; `2024/Mar/15` was nothing. Each is 15 March 2024, and `15-Mar` alone is 15 March.
  `Mar-15` alone is still March 2015, and `15 Mar - 2024`, spaced, still a span.
- `March 15 to 03/04/2024` was April 2024, and `Jun FY25 - 15 March` was nothing.
- `1H FY-24` was the first half of this fiscal year.
- `1Q and since 1Q` gave two overlapping matches.
- A bare month in the middle of a span — `Q1 to March to Q3` — was dropped in strict
  mode, and in balanced mode without a cue, which cut the span in two.
- `the 2015–2016 season`, `season 2015/16`, `academic year 2015-16` and `the 2015-16
  school year` were read as the two calendar years they span. A season is a name, with
  months of its own, so it is now kept as a label: no match, and `diagnose` says why.
  `2015–2016` on its own still reads as the years.

### Changed
- Dates written with month names are read by splitting the text into words and numbers
  and naming each piece once (`tokens.py`), in place of eleven regular expressions that
  each listed their own spellings.
- Each rule's reading of a fragment is remembered, per configuration and within a fixed
  bound, so "Q1" or "FY24" is read once rather than in every text. Against 2.1.0, text
  that names a month parses 3–9% slower, text naming none 10–13% faster, and prose with
  no date at all about as fast.

## 2.1.0 — 2026-10-16

Which dates go together, what a span runs between, how sure the year is — and about 150
date shapes that were read wrongly or not at all. **Changes answers.**

### Added
- `DateMatch.group` and `DateMatch.joined_by` (`"list"`, `"compare"`, `"range"`): dates
  named together share a group; unrelated dates in one text each have their own.
- `DateMatch.endpoints`: a span's two ends, each as a period — `from 2012 to 2013` is 2012
  and 2013.
- `YEAR_GUESSED` (0.7): the confidence of a period whose year is not written (`September`,
  `Q1`, `15 March`). `substitute(min_confidence=0.75)` leaves such periods as typed.
- `2024-25` with no FY follows `year_basis`: the fiscal year 2024-25 on a fiscal basis, the
  years 2024 to 2025 on a calendar one.
- Read for the first time: `2024-03`, `03/2024`, `01-04-2018`, `13.06.18`, `March 15 2024`,
  `Sept. 2013`, `FEB.2018`, `15MAR2024`, `13june2018`, `may'18`, `last March`, `prior to
  2012`, `pre-2012`, `2012 and later`, `the years 2012 through 2014`, `2023 v 2022`, `the
  '90s`, `the nineties`, `the early 1990s`, `the month before last`, `2024-W11-5`,
  `2024-03-15/2024-03-20`, `01/06/2018 to till date`, `as on date`, `from 2012 to present`.
- A reason, where these matched nothing silently: seasons, `the year before` alone,
  `recently`, `every Monday`.
- `tests/test_formats.py`: about 450 phrases, each with the answer it must give.

### Fixed
- A comma or weekday inside one date gave two: `in June, 2013` was June 2013 and all of
  2013. A weekday that does not match its date is now flagged.
- A year crossed a "vs": `Q1 and Q3 2024 vs 2023` gave Q1 the 2023.
- `Q1 FY2024-25` was FY2024, a year early.
- These dropped their year or qualifier: `2nd March last year`, `2024 March 15`,
  `Q1/2024`, `year-end 2024`, `2024 YTD`, `9M FY24`, `the March quarter 2024`, `the first 3
  quarters of 2024`, `first fortnight of March`, `mid-1990s`, `Monday to Friday last week`.
- `in 2012 or 2013` and `Jan/Feb 2024` kept one item.
- `3 days before 15 March 2024` and `March 2024 compared to the previous month` counted
  from today.
- `within 30 days` was one day thirty days out; it is the window.
- `release 1.5.3` and `rating 4.5/5` were read as dates.

### Changed
- An all-numeric date that reads only one way (`13/06/2018`) is at 1.0, not 0.9.
- Text with no date parses as fast as before; date-heavy text about 16% slower.

## 2.0.0 — 2026-10-09

"and" lists periods, and every answer the library gives is pinned. **Changes answers.**

- `Q1 and Q3` is two quarters, not one range holding Q2. Only `to`, `-`, `through` and
  `between … and` make one range; combine matches with `|` or `hull()`.
- A match's text may start with "the" (`the year 2013`), so `substitute` leaves no stray
  article.
- Fixed: `since the last quarter` is open-ended, `from 15th of March` runs onwards, `the
  week of 5 October` is the week, `1st and 15th March` is two days.
- `last 3 hours` and other periods shorter than a day give a reason.
- `tests/snapshots/answers.txt` pins the full answer to every recorded phrase, and a sweep
  joins 33 date forms with every joiner.

## 1.0.0 — 2026-10-09

One basis rule, calendar by default, and `substitute` that keeps your words. **Changes
answers.**

- What the phrase says, otherwise `year_basis` — which now defaults to calendar.
  `FY24`, `fiscal Q3` and `this fiscal year` stay fiscal under either setting.
- A month of a fiscal year (`Jun FY25`) is the June inside FY25.
- `substitute(keep_text=True)` keeps the phrase and puts its dates after it in brackets,
  and running it again adds nothing.
- `substitute(min_confidence=0.6)` by default: matches flagged at 0.5 stay as typed.
- Fixed: `FY2024-25` where years are named by their start; two unrelated numbers read as
  a fiscal year.

## 0.8.0 — 2026-10-08

Read the whole phrase, and keep the boundary day. **Changes answers.**

- `<period> to date` runs from the start of the period through today.
- `year_basis` setting, apart from `bare_period_basis`.
- Fixed, all confident and wrong before: `on or after 1 April` (from the 2nd), `not before
  1 April` (reversed), `by 31 March`, attached years (`Q3'24`, `2024-Q3`, `3Q24`), `fiscal
  Q3` under a calendar default, `the last quarter of 2024`, `the first week of April`, `1-15
  March`, `six months to June 2024`, `a 2h drive` (read as H2).

## 0.7.0 — 2026-10-06

Week numbers, business days, and SQL that runs on Oracle.

- `week 42`, `2026-W42`, `CW42`, `KW 42` — ISO weeks, Monday to Sunday.
- Business days: `5 business days ago`, `next business day`, `the last business day of the
  month`, with weekend and holidays from configuration; `DateRange.business_days()`.
- Fixed: `sql(dialect=)` quoted identifiers that Oracle and Snowflake fold to upper case;
  a qualified name was quoted whole; coarse formats named whole months for partial ones.

## 0.6.0 — 2026-10-02

Read the whole phrase, and report only what was read. **Changes answers.**

- The nth weekday or day of a period: `last day of the month`, `first Monday of March`,
  `third Thursday of November`.
- `week_starts_on`, `SqlDialect` for `sql()`, `to_dict()`/`from_dict()`.
- Formatting no longer claims whole months for partial ones; `substitute` takes
  `min_confidence`.

## 0.5.0 — 2026-10-02

Say what wasn't read, and let ranges combine.

- A time of day beside a date (`yesterday at 2pm`) is flagged at 0.5.
- `&`, `|`, `overlaps`, `in`, `difference()` and `hull()` on ranges.
- `str()` shows the last day inside a range; `sql(inclusive=True)`.

## 0.4.0 — 2026-09-13

Group and compare a parsed range: `split(grain)` for a GROUP BY axis, `shift(periods,
grain)` for period over period.

## 0.3.0 — 2026-09-10

Qualifiers are no longer silently dropped (`first half of March`, `Q1 last year`,
`same quarter last year`, `day before yesterday`), with a confidence-0.5 safety net for
the rest; anchored or rolling periods; ISO timestamps, syslog and other written formats;
the prefilter ten times faster on text with no date.

## 0.2.0 — 2026-09-04

Month names that are also people's names (`sales by June Patel`); a cue may follow the
month (`the March figures`).

## 0.1.0 — 2026-09-04

First release.
