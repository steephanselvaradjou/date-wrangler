---
name: date-wrangler
description: Resolves dates written in words to exact first and last days with the date-wrangler library - relative phrases ("last quarter", "YTD", "since March"), fiscal periods ("Q3 FY24", "the last month of the fiscal year"), comparisons ("H1 2025 vs H1 2024") and written forms ("w.e.f. 1 April 2024", "15-20 Mar-24"). Use whenever a task depends on what dates a phrase means - filtering data, writing SQL date conditions, building a report, comparing periods - instead of working the dates out by hand.
---

# Resolving dates with date-wrangler

Date arithmetic done by hand goes wrong quietly: a fiscal year that starts in April, a
fiscal year named by the year it ends in, "last week" starting on a Sunday, an exclusive
end read as the last day. Resolve dates with the `date-wrangler` command and quote what it
returns.

## Setup

```bash
date-wrangler --version || pip install date-wrangler
```

Python 3.10 or later, no dependencies. If the command is not on the PATH after installing,
`python -m date_wrangler` takes the same arguments.

## Resolve

```bash
date-wrangler --json --today 2026-11-16 "revenue for Q3 FY24 vs the same quarter last FY"
```

- Pass the user's words as written. Every date in the text is found, in order.
- Pass `--today` as the user's current date whenever you know it.
- Pass the organisation's settings when you know them:

| Flag | What it decides | Default |
|---|---|---|
| `--fiscal-start M` | the month the fiscal year starts, 1-12: 4 India, UK, Japan; 7 Australia; 10 US federal | 4 |
| `--label-by end_year\|start_year` | which year names a fiscal year: with end_year, FY2024 is April 2023 to March 2024 | end_year |
| `--year-basis calendar\|fiscal` | how "2024", "Q1 2024", "last quarter", "YTD" are read when they do not say | calendar |
| `--date-order DMY\|MDY\|YMD` | how 03/04/2024 is read: DMY is 3 April, MDY (US) is 4 March | DMY |
| `--month-number day\|year` | whether "jan 24" is 24 January or January 2024 | day |
| `--week-starts-on monday..sunday` | the first day of a week, for "last week" and week numbers | monday |
| `--anchor anchored\|rolling` | "last month" as the previous calendar month, or counted back from today | anchored |
| `--tz ZONE` | the IANA time zone to take today in, when `--today` is not given | - |

## Read the answer

- `matches`: one per date found. Quote `first_day` and `last_day` (both inclusive; `null`
  for an open end, as in "since March") or `reads_as`. `confidence` below 1 means part of
  the date is a guess, and `notes` says which part. `relative_to_today` is true for a date
  that counts from today. `range.end` is the exclusive end, for code.
- `assumptions`: each setting you did not pass that changed an answer. `note` says what was
  assumed; `otherwise` gives the dates under the other value.
- `diagnostics`: words that looked like a date but could not be read, and why.

## Rules

1. Never recompute a date the command returned. Quote it.
2. When `assumptions` is not empty, say what was assumed - "taking the fiscal year to start
   in April" - or ask the user and run again with the flag. Ask first when a query, a
   report or a change depends on it; for a quick answer, state the assumption.
3. When `confidence` is below 1, say which part is a guess, from `notes`.
4. In SQL or code, filter with `column >= range.start AND column < range.end`. The exclusive
   end is right for timestamps as well as dates; `BETWEEN first_day AND last_day` drops the
   last day's times. A `null` end means no bound on that side.
5. If words the user meant as a date are in `diagnostics` rather than `matches`, say they
   could not be read and why. Do not guess them.
6. For a result relative to today, say the date it is as of when the answer will be kept.

## From Python

The same answer, without the shell:

```python
from date_wrangler import tool

answer = tool.resolve_dates("Q3 FY24", today="2026-11-16", fiscal_start_month=4)
```
