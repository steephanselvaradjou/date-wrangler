# date-wrangler

Parse dates, ranges and periods out of natural language.

Absolute dates, relative expressions, open-ended ranges and fiscal periods all resolve to
one type — a half-open `DateRange` that is safe to hand straight to a query.

> **Status: early development (0.1.0.dev0).** The API may still change before 1.0.
> Not yet published to PyPI.

## Why another date library

Most date parsers answer "what instant is this?". `date-wrangler` answers **"what range is
this?"** — which is the question you actually have when a person types `last quarter`,
`since March`, or `Q1 FY25` into a search box.

Everything is a range: a single day is a `DAY`-grain range, a month is a `MONTH`-grain
range, a fiscal quarter is a `QUARTER`-grain range on the fiscal basis. That one decision
is what lets days, weeks, quarters, fiscal years and open-ended intervals share an API
instead of each needing their own.

## Install

```bash
pip install date-wrangler
```

No runtime dependencies. Python 3.10+.

## The core model

```python
from datetime import date
from date_wrangler import FiscalCalendar, Basis
from date_wrangler.calendars import quarter_range, month_range, current_fiscal_year

cal = FiscalCalendar.india()                  # fiscal year starts in April
fy = current_fiscal_year(date(2025, 9, 4), cal)   # -> 2026

q1 = quarter_range(fy, 1, cal, Basis.FISCAL)
q1.start          # date(2025, 4, 1)
q1.end            # date(2025, 7, 1)   <- exclusive
q1.end_inclusive  # date(2025, 6, 30)  <- for display
q1.grain          # Grain.QUARTER
q1.sql("order_date")
# "order_date >= '2025-04-01' AND order_date < '2025-07-01'"
```

### Ranges are half-open

`end` is the first day *outside* the range, never the last day inside it. This is the
Duckling convention and it exists to prevent a specific, common data bug:

```python
feb = month_range(2024, 2)
feb.end            # date(2024, 3, 1)
feb.end_inclusive  # date(2024, 2, 29)
```

`WHERE ts BETWEEN '2024-02-01' AND '2024-02-29'` silently drops every row after midnight
on the 29th when `ts` is a timestamp. `ts < '2024-03-01'` does not. Half-open ranges also
tile exactly — `Q1.end == Q2.start` — so they compose without off-by-one errors.

### Open-ended ranges

Either bound may be `None`, which is what makes `since March` and `before 2024`
expressible at all:

```python
r.start, r.end     # date(2024, 3, 1), None
r.is_bounded       # False
r.sql("d")         # "d >= '2024-03-01'"
```

`end=None` means **unbounded** — never a silent "up to today". Deciding between those is
the caller's business, so you opt in explicitly:

```python
r.clamp(hi=date.today() + timedelta(days=1))
```

## Configuration

Config is passed per call and never read from a module global, so one process can serve
tenants on different fiscal calendars.

```python
from date_wrangler import ParserConfig, FiscalCalendar, DateOrder, YearLabel

ParserConfig(
    fiscal=FiscalCalendar.us_federal(),   # October start
    date_order=DateOrder.MDY,             # how to read 03/04/2024
    bare_period_basis=Basis.FISCAL,       # what a bare "Q1" means
    two_digit_pivot=68,                   # "99" -> 1999, not 2099
    strictness="balanced",
)
```

Presets: `FiscalCalendar.india()`, `.uk()`, `.australia()`, `.us_federal()`, `.calendar()`.

Fiscal years follow the pandas `Q-MAR` convention by default — labelled by the year they
**end**, so with an April start FY2024 runs Apr 2023 – Mar 2024 and Apr–Jun is Q1. Set
`label_by=YearLabel.START_YEAR` for the US corporate convention.

Invalid configuration fails on construction with a message naming the field, not later
from inside `date()` on the first request that mentions a quarter.

## Parsing text

```python
from date_wrangler import parse

for m in parse("revenue for Q1 FY25 vs Q1 FY24", today=date(2025, 9, 4)):
    print(m.text, m.span, m.range.start, m.range.end)
# Q1 FY25 (12, 19) 2024-04-01 2024-07-01
# Q1 FY24 (23, 30) 2023-04-01 2023-07-01
```

`parse()` returns `DateMatch` objects carrying the resolved range, the matched text and its
span in the string you passed — so you can highlight or rewrite without searching again.

A comparison stays **two** matches. Merging `compare Q1 2024 to Q1 2025` into one fifteen
month span is the kind of error that survives review because the number still looks
plausible.

### What it understands

| | |
|---|---|
| Fiscal periods | `Q1 FY25`, `H2 of last fiscal year`, `FY2024-25`, `third month of FY24` |
| Calendar periods | `CY2024`, `Q1 of 2024`, `January 2024`, `2024` |
| Relative | `last 3 months`, `next 2 quarters`, `3 months ago`, `this week`, `yesterday` |
| To-date | `YTD`, `MTD`, `QTD`, `last YTD` (the same window a year earlier) |
| Absolute | `2024-03-15`, `15 January 2024`, `January 15, 2024`, `03/04/2024` |
| Ranges | `Q1 to Q2`, `Jan–Mar`, `from April to September 2024`, `Nov to Feb` (wraps) |
| Open-ended | `since March`, `from Q1 onwards`, `up to March 2024`, `before 2024`, `after FY24` |
| Point-in-time | `as of 31 March 2024` |

Connectors include `to`, `through`, `thru`, `until`, `till`, `upto`, `and`, and hyphen, en
dash or em dash — the last three matter because editors rewrite `-` as `–` on sight.

### Telling "nothing there" from "couldn't read it"

```python
matches, diags = diagnose("5000 years ago", today=today)
# matches == []
# diags == [Diagnostic(text='5000 years ago', ..., reason='...outside the supported range')]
```

`parse()` never raises on user input.

### Precision on running prose

Bare month names are the dominant false positive for this kind of library — `strictness`
controls how eagerly they are claimed:

```python
parse("the march on Washington")        # [] — no cue, so "march" is a noun
parse("sales in March")                 # matched — "in" is a cue
ParserConfig(strictness="greedy")       # match any month name anywhere
ParserConfig(strictness="strict")       # require a year or explicit period marker
```

### Rewriting text

```python
substitute("sales report of Q1", today=today)
# 'sales report of from April 2025 to June 2025'
```

Only the matched phrase is replaced, and the output is a fixed point — running it again
changes nothing.

## Command line

```console
$ date-wrangler --today 2025-09-04 "revenue since Q1 FY25"
'since Q1 FY25'
   from April 2024 onwards
   2024-04-01/..   grain=quarter basis=fiscal mod=since confidence=1
   SQL: d >= '2024-04-01'
```

`--json` for machine-readable output; `--fiscal-start`, `--basis`, `--date-order` and
`--strictness` to try configurations.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT © 2026 Steephan Selvaraj — see [LICENSE](LICENSE).
