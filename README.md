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

Python 3.10+. No runtime dependencies, except `tzdata` on Windows — which ships no system
timezone database, so the standard library needs it for the optional `tz=` argument.

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
from date_wrangler import WranglerConfig, FiscalCalendar, DateOrder, MonthNumber, YearLabel

WranglerConfig(
    fiscal=FiscalCalendar.us_federal(),   # October start
    date_order=DateOrder.MDY,             # how to read 03/04/2024
    month_number=MonthNumber.YEAR,        # what "jan 24" means
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

### `jan 24` — day or year?

Genuinely ambiguous, and it depends who is writing. Prose means the 24th; a finance sheet
listing `jan 24, feb 24, mar 24` means the year. `month_number` decides:

```python
parse("jan 24")                                    # 24 January 2025   (default)
parse("jan 24", config=WranglerConfig(month_number=MonthNumber.YEAR))
                                                   # January 2024
```

The setting only decides that ambiguous middle. Everything else settles itself:

| written | reads as | why |
|---|---|---|
| `march 3` | 3 March | a single digit is never a year |
| `jan 24th` | 24 January | ordinal suffix |
| `jan '24` | January 2024 | apostrophe |
| `jan 2024` | January 2024 | four digits |
| `jan 87` | January 1987 | above 31, so it cannot be a day |
| `january 15, 2024` | 15 January 2024 | the year is already stated |

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
| Fiscal periods | `Q1 FY25`, `Q1FY24`, `H1 FY25`, `1H 2024`, `FY2024-25`, `fy-24`, `F.Y. 2024` |
| Calendar periods | `CY2024`, `Q1 of 2024`, `January 2024`, `2024` |
| Fiscal month index | `third month of FY24`, `twelfth month` |
| Relative | `last 3 months`, `next 2 quarters`, `3 months ago`, `this week`, `yesterday` |
| Weekdays | `last Monday`, `next Friday`, `this Tuesday` |
| To-date | `YTD`, `MTD`, `QTD`, `last YTD` (the same window a year earlier) |
| Reporting shorthand | `TTM`, `LTM`, `T12M`, `L3M`, `trailing 12 months`, `rolling 3 months` |
| Period-ending | `quarter ending June 2024`, `year ended March 2024` |
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
WranglerConfig(strictness="greedy")       # match any month name anywhere
WranglerConfig(strictness="strict")       # require a year or explicit period marker
```

### Rewriting text

```python
substitute("sales report of Q1", today=today)
# 'sales report of from April 2025 to June 2025'
```

Only the matched phrase is replaced.

**One limit worth knowing.** Substitution is textual, so an inserted phrase can fuse with a
neighbouring token that was never part of a date:

```python
substitute("sales 15 Q1")   # 'sales 15 April 2025 to June 2025'
```

Read that back and `15 April 2025` is a perfectly good date, so a second pass gives a
different answer. Repeated substitution always *converges* — it never grows without bound,
which is the failure that matters — but it is not idempotent in one pass when a bare number
abuts a date expression. When exactness matters, use `parse()` and render the ranges
yourself; `substitute` is a convenience.

## Output format

Formatting is a separate, replaceable function, so the output format and structure are
entirely yours. Three levels, in increasing order of control:

**1. Don't format at all.** The dates are already objects — `m.range.start`, `m.range.end`,
`m.range.grain`. Most callers never need a string.

**2. `make_formatter()`** — build one from format strings:

```python
from date_wrangler import make_formatter

make_formatter()(r)                                          # '2024-04-01 to 2024-06-30'
make_formatter(date_format="%d/%m/%Y", closed="{start} - {end}")(r)
                                                             # '01/04/2024 - 30/06/2024'
make_formatter(closed="BETWEEN '{start}' AND '{end}'")(r)
                                                # "BETWEEN '2024-04-01' AND '2024-06-30'"
make_formatter(closed="[{start}, {end}]", inclusive_end=False)(r)
                                                             # '[2024-04-01, 2024-07-01]'
```

`date_format` is a `strftime` pattern; the templates are `str.format` patterns taking
`{start}` and `{end}`. Separate templates exist for each shape — `closed`, `single`,
`since`, `until`, `before`, `after`, `as_of`, `unbounded`.

`inclusive_end` decides which day `{end}` names. It defaults to `True`, so a human-facing
string says the last day *inside* the period (`2024-06-30`); set it `False` to emit the
exclusive bound (`2024-07-01`) for a machine.

**3. Any callable.** A formatter is just `DateRange -> str`:

```python
substitute(text, formatter=lambda r: f"<{r.start}..{r.end})")
```

Built-ins: `format_range` (prose, locale-independent) and `format_iso` (`2024-04-01/2024-07-01`).

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

MIT © 2026 Steephan Selvaradjou — see [LICENSE](LICENSE).
