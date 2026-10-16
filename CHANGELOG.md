# Changelog

Every release, newest first. From 1.0.0 the public API follows semantic versioning: a
change that breaks existing callers comes only with a new major version. Entries marked
**changes answers** return different dates or confidence for text that parsed before.

The full notes for each release, with examples, are on the
[releases page](https://github.com/steephanselvaradjou/date-wrangler/releases).

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
