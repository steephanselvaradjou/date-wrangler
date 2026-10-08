# date-wrangler

Wrangles messy human dates into clean ranges: parses `last quarter`, `since March`,
`15 Jan 2024`.

Whatever someone types — an absolute date, a relative expression, an open-ended range, a
fiscal period — it comes back as one type: a half-open `DateRange` that is safe to hand
straight to a query.

<!-- No version number anywhere in this file, on purpose: it is baked into the wheel and
     rendered on PyPI, where the released version is already in the page header. Repeating
     it is one more thing to remember at release time, and it went stale once already.
     tests/test_packaging.py enforces this. -->
> **Status: early development.** The API may still change before 1.0.

## Why another date library

Most date libraries answer "what instant is this?". `date-wrangler` answers **"what range is
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

**Why `<` and not the `<=` everyone writes.** SQL reads a bare `'2024-02-29'` as
`'2024-02-29 00:00:00'` — the first *instant* of the day, not the day. So on a `TIMESTAMP`
or `DATETIME` column, `<=` keeps only rows stamped exactly midnight:

| predicate, `ts` is a TIMESTAMP | rows found |
|---|---|
| `ts BETWEEN '2025-04-01' AND '2025-06-30'` | 1 |
| `ts >= '2025-04-01' AND ts <= '2025-06-30'` | 1 |
| `ts >= '2025-04-01' AND ts <  '2025-07-01'` | **4** |

The two are identical on a `DATE` column, where every value *is* midnight. The library
can't see which it is, and `<` is right for both. When you know it's a `DATE`:

```python
r.sql("order_date", inclusive=True)   # ... AND order_date <= '2025-06-30'
```

**Quoting and literals per engine.** The default quotes nothing and writes a bare string,
which most engines accept. Two things break that: a column named `order`, `date` or with a
space in it, and Oracle, which converts a bare `'2026-07-01'` using the session's
`NLS_DATE_FORMAT` and so raises or reads a different date.

```python
from date_wrangler import SqlDialect

r.sql("m.claim_date", dialect=SqlDialect.oracle())
# m.claim_date >= DATE '2025-04-01' AND m.claim_date < DATE '2025-07-01'
r.sql("order date", dialect=SqlDialect.tsql())
# [order date] >= CAST('2025-04-01' AS DATE) AND [order date] < CAST('2025-07-01' AS DATE)
r.sql("m.date", dialect=SqlDialect.oracle())
# m."DATE" >= DATE '2025-04-01' AND m."DATE" < DATE '2025-07-01'
```

**Identifiers are quoted only where they have to be**, and that is a correctness rule, not
a style one. Oracle, Snowflake, HANA and DB2 store an unquoted name in upper case, so a
column created as `claim_date` is held as `CLAIM_DATE` and the quoted `"claim_date"` is an
invalid identifier. Bare `claim_date` is right on every engine, so a plain name is left
alone. When quoting *is* needed:

- a **reserved word** that is otherwise plain was created unquoted, so it is folded to the
  case the engine stored it in — `"DATE"` on Oracle, `"date"` on Postgres;
- a name with a **space or punctuation** must have been created quoted, so it keeps exactly
  the case you gave;
- a **qualified name** is quoted part by part — `m.claim_date` is a table and a column, not
  one column with a dot in it.

Presets: `.plain()` (the default), `.ansi()`, `.postgres()`, `.oracle()`, `.snowflake()`,
`.tsql()`, `.mysql()`, `.bigquery()`, `.sqlite()`. They are shorthand for a few independent
choices — quote characters, literal style, which case the engine `folds` unquoted names to,
and `quoting="needed"` or `"always"` — so an engine with no preset is still expressible.

**Printing never shows the exclusive end.** `str(r)` reports the last day *inside* the
range, because `[2025-09-04, 2025-09-05)` reads as two days however correct the bracket is:

```python
print(parse_one("today").range)          # 2025-09-04 (1 day)
print(parse_one("last quarter").range)   # 2025-04-01 .. 2025-06-30 (91 days)
print(parse_one("since March").range)    # 2025-03-01 onwards
```

`repr(r)` still shows the stored fields, exclusive `end` and all.

### Anchored or rolling

`last month` has two defensible readings, and which one you get should not be an accident.
Asked on 4 September:

```python
parse("last month")                                       # 2025-08-01 .. 2025-09-01
parse("last month", config=WranglerConfig(anchor=Anchor.ROLLING))
                                                          # 2025-08-04 .. 2025-09-04
```

**Anchored** (the default) snaps to whole calendar units. **Rolling** measures back from
today. Anchored is the default because only whole units are comparable — rolling months are
28 to 31 days long, so month-on-month stops being like-for-like — and because a
part-finished current period should not be mixed in with completed ones.

The wording wins over the setting where it is explicit: `rolling 3 months` always rolls,
and `TTM` / `trailing 12 months` never does, because in reporting that means the last
twelve *completed* months. Day-grain phrases like `last 30 days` are the same either way —
a day is its own unit, so there is nothing to snap to.

`r.anchor` is on the range, so callers can tell which they got.

### Which day a week starts on

Monday by default, which is ISO and most of the world. A US workspace wants Sunday, and the
difference is a whole day of data at each end of every weekly report:

```python
parse("last week")                                            # Mon 5 – Sun 11 Oct
parse("last week", config=WranglerConfig(week_starts_on=6))   # Sun 4 – Sat 10 Oct
```

It applies to `this/last/next week`, multi-week spans, and `this Tuesday` — which means
"the one in the current week". It deliberately does **not** apply to `this weekend`: a
weekend is the Saturday–Sunday pair, and on a Sunday-start calendar that pair straddles the
week boundary, so following the setting would split it.

Nor to **numbered weeks**. `week 42`, `2026-W42`, `CW42` and `KW 42` are ISO 8601 weeks,
which are Monday to Sunday by definition — renumbering them from Sunday would make one label
mean different days under different settings. The ISO year is not the calendar year
either: `2026-W01` begins on 29 December 2025, and some years have a week 53 and others do
not. Asking for one that does not exist is refused with a diagnostic, not rounded into the
next year. A bare `W42` is treated like a bare month name and needs a cue (`in W42`),
because on its own it is just as likely a part number.

### Business days

```python
parse("5 business days ago")                 # one day, weekends skipped
parse("last business day of the month")      # the close date, not the 31st if that's a Sunday
parse("first working day of next month")
parse("last 10 business days")               # a window holding exactly ten working days
```

Weekend and holidays are configuration, never guessed:

```python
WranglerConfig(
    weekend=(4, 5),                                   # Friday–Saturday
    holidays=frozenset({date(2026, 12, 25), ...}),    # yours, from wherever you keep them
)
```

Which days are holidays depends on a country, a region, an industry and sometimes one
company's handbook. A built-in list would be wrong somewhere and confident everywhere, so
the default counts weekends only and you supply the rest.

A business-day **window** is contiguous and keeps any weekend inside it, because a date
filter has to be one range. Both ends land on a working day, though — asked on a Monday,
`last business day` is Friday, not Friday to Sunday. `r.business_days(weekend=..., holidays=...)`
says how many of a range's days are working ones; it takes the calendar as arguments for the
same reason `split()` does, since a range does not carry configuration.

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

### Grouping and comparing

Parsing gives you one range. A report needs one row per bucket, and a number to compare it
against. Both are date arithmetic that is easy to get quietly wrong by hand, so the range
does them:

```python
today = date(2025, 9, 4)
r = parse_one("last 3 months", today=today).range

for bucket in r.split(Grain.MONTH):
    print(bucket, bucket.sql("order_date"))
# 2025-06-01 .. 2025-06-30 (30 days)  order_date >= '2025-06-01' AND order_date < '2025-07-01'
# 2025-07-01 .. 2025-07-31 (31 days)  ...
# 2025-08-01 .. 2025-08-31 (31 days)  ...

q = parse_one("Q1 FY25", today=today).range   # 2024-04-01 .. 2024-07-01
q.shift(-1)                                   # previous quarter: 2024-01-01 .. 2024-04-01
q.shift(-1, Grain.YEAR)                       # same quarter last year: 2023-04-01 .. 2023-07-01
```

`shift` moves by whole units of the range's own `grain`, both ends together, so the length
survives. That is the part hand-rolled arithmetic loses: `- timedelta(days=90)` drifts
because quarters are 90 to 92 days, `- timedelta(days=365)` breaks across a leap year, and
`start.replace(year=...)` raises on 29 February. Basis comes along too — a fiscal Q1 starts
on a fiscal boundary already, so one step back lands on fiscal Q4 of the year before.

`split` tiles the range exactly: each bucket's `end` is the next one's `start`, no gap and
no day counted twice. The grid starts at `start` rather than at a calendar boundary, so a
fiscal year splits into its own fiscal quarters without being told which calendar it is on,
and a range that begins mid-unit never widens outwards to cover days you did not ask for.
Only the final bucket can be short, and `bucket.days` says by how much.

### Combining ranges

Ranges compose like sets, with an unbounded end treated as an infinity throughout — which
is the only reading that lets `since March` take part at all:

```python
q1 & since_feb          # intersection — the general form of clamp()
q1.overlaps(q3)         # False; half-open means Q1 and Q2 are adjacent, not overlapping
feb in q1               # containment; a bare date still works too
q1 | q2                 # union of two ranges that meet
q1.difference(feb)      # [Jan, Feb) and [Mar, Apr) — a bite out of the middle
```

Intersecting disjoint ranges gives an **empty range, not `None`**, so a chain keeps working;
check `result.is_empty`. Results take the finer grain — an intersection is never longer than
the shorter side — and drop `mod`, which described the range it came from.

`difference` returns a **list**, because subtracting from the middle leaves two pieces.

`|` **raises** when the two do not meet. A union across a gap is two ranges, not one, and
returning the hull silently is exactly how `Q1 and Q3` comes to include Q2. Call
`q1.hull(q3)` when the widening is what you actually want.

## Configuration

Config is passed per call and never read from a module global, so one process can serve
tenants on different fiscal calendars.

```python
from date_wrangler import (
    Anchor, DateOrder, FiscalCalendar, MonthNumber, WranglerConfig, YearLabel,
)

WranglerConfig(
    fiscal=FiscalCalendar.us_federal(),   # October start
    anchor=Anchor.ANCHORED,               # what "last month" means
    date_order=DateOrder.MDY,             # how to read 03/04/2024
    month_number=MonthNumber.YEAR,        # what "jan 24" means
    week_starts_on=6,                     # 0=Monday (default), 6=Sunday for the US
    weekend=(5, 6),                       # non-working days, for "business days"
    holidays=frozenset(),                 # yours to supply; never guessed
    bare_period_basis=Basis.FISCAL,       # what a bare "Q1" means
    year_basis=Basis.CALENDAR,            # what "this year" and YTD mean
    two_digit_pivot=68,                   # "99" -> 1999, not 2099
    strictness="balanced",
)
```

Presets: `FiscalCalendar.india()`, `.uk()`, `.australia()`, `.us_federal()`, `.calendar()`.

Fiscal years follow the pandas `Q-MAR` convention by default — labelled by the year they
**end**, so with an April start FY2024 runs Apr 2023 – Mar 2024 and Apr–Jun is Q1. Set
`label_by=YearLabel.START_YEAR` for the US corporate convention.

**Which basis a phrase is read on** comes down to what it says, never to which rule
happened to match it:

| phrase | basis |
|---|---|
| `2013`, `in 2013`, `the year 2013`, `end of the year 2013` | calendar — a year on its own is a calendar year |
| `FY2013`, `fiscal year 2013`, `this fiscal year`, `last FY` | fiscal — it says so |
| `CY2013`, `this calendar year`, `next calendar quarter` | calendar — it says so |
| `this year`, `last year`, `YTD`, `this year to date` | `year_basis` — nothing was said |
| `Q1`, `H1`, `last quarter`, `QTD` | `bare_period_basis` — nothing was said |
| `Q1 of 2013`, `Q1 of the year 2013` | `of_year_basis` — a year labelling a quarter is genuinely ambiguous |

So an explicit `fiscal` or `calendar` always beats the configured default, and the word
"year" on its own never makes anything fiscal.

`year_basis` exists for the team whose "Q1" is the fiscal quarter but whose "this year" is
January to December. It defaults to `None`, which follows `bare_period_basis`, so the two
only come apart when you ask. A value from a form or select works as-is — `"calendar"` and
`"fiscal"` are accepted in any case — and anything else fails on construction, by name.

```python
cfg = WranglerConfig(year_basis="calendar")       # April fiscal year, calendar "this year"
parse("this year", config=cfg)                     # January 2026 to December 2026
parse("Q1", config=cfg)                            # April 2026 to June 2026 — still fiscal
parse("FY2013", config=cfg)                        # April 2012 to March 2013 — says so
```

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
| Calendar periods | `CY2024`, `Q1 of 2024`, `January 2024`, `2024`, `the year 2024` |
| Fiscal month index | `third month of FY24`, `twelfth month` |
| Relative | `last 3 months`, `next 2 quarters`, `3 months ago`, `this week`, `yesterday` |
| Stated basis | `this fiscal year`, `this FY`, `last 2 fiscal quarters`, `next calendar year` |
| Weekdays | `last Monday`, `next Friday`, `this Tuesday` |
| To-date | `YTD`, `MTD`, `QTD`, `last YTD` (the same window a year earlier) |
| Period to date | `this year to date`, `Q3 to date`, `October to date`, `March 2024 to date` |
| Reporting shorthand | `TTM`, `LTM`, `T12M`, `L3M`, `trailing 12 months`, `rolling 3 months` |
| Period-ending | `quarter ending June 2024`, `year ended March 2024` |
| Absolute | `2024-03-15`, `15 January 2024`, `January 15, 2024`, `03/04/2024` |
| Ranges | `Q1 to Q2`, `Jan–Mar`, `from April to September 2024`, `Nov to Feb` (wraps) |
| Open-ended | `since March`, `from Q1 onwards`, `up to March 2024`, `before 2024`, `after FY24` |
| Point-in-time | `as of 31 March 2024` |
| Relative year | `Q1 last year`, `March last year`, `H2 next year` |
| Part of a period | `first half of March`, `early 2024`, `mid March`, `end of Q1` |
| Day in a period | `1st of next month`, `15th of March`, `the last day of the month` |
| Nth weekday | `first Monday of March`, `last Friday of the month`, `3rd Thursday of November` |
| Week numbers | `week 42`, `2026-W42`, `CW42`, `KW 42`, `week 42 of 2026` |
| Business days | `5 business days ago`, `next business day`, `last business day of the month` |
| Same period back | `same quarter last year`, `this time last year` |
| Weekend | `this weekend`, `next weekend`, `last weekend` |
| Period edges | `month end`, `EOM`, `EOQ`, `end of the quarter`, `year start` |
| Ahead | `in 3 days`, `2 weeks from now`, `3 months from today` |
| Fortnights | `a fortnight ago`, `last fortnight`, `next fortnight` |
| Day idioms | `day before yesterday`, `day after tomorrow` |
| Written formats | `15-Mar-2024`, `15 Mar 24`, `Mar-24`, `15.03.2024`, `2024/03/15` |
| Timestamps | `2024-03-15T14:30:00Z`, `Mar 15 14:30:00`, `Wed, 15 Mar 2024 14:30:00 GMT` |
| Chat and email | `EOD`, `COB Friday`, `on the 15th`, `meet Thursday` |
| Decades | `the 1990s` |

Connectors include `to`, `through`, `thru`, `until`, `till`, `upto`, `and`, and hyphen, en
dash or em dash — the last three matter because editors rewrite `-` as `–` on sight.

**`<period> to date` runs from the start of the period up to and including today.** For
the current period that is the period so far — `this year to date` is the same window as
`YTD`. For one that has already ended it carries on to today, because that is what "to
date" says: `March 2024 to date` is everything since 1 March 2024, not March alone. With no
year written, a period that would start in the future means the last one instead, so
`December to date` asked in October starts last December. An explicit future period —
`FY28 to date` — has nothing to date yet and is refused with a diagnostic.

### What it deliberately does not read

Recall is not the only thing that matters. In ordinary prose a wrong date is worse than no
date, so these are left alone on purpose:

| not read | why |
|---|---|
| `20240315` | indistinguishable from `invoice 20240315` |
| `the 3rd floor`, `2nd round` | an ordinal followed by a noun is a position, not a day |
| `cob` in lower case | corn, not close of business — `COB` is read |
| `19th century` | centuries are unsupported, and guessing a day would be worse |
| `Christmas`, `Diwali` | holidays need a locale and a calendar of their own |
| `every Monday` | recurrence is a different shape from a range |
| `within 30 days of the Effective Date` | a duration with no anchor to measure from |
| `bake for 30 minutes` | durations and times of day are out of scope |

Version numbers, prices, phone numbers, invoice IDs, scores and measurements are all left
alone too. See [tests/test_realworld.py](tests/test_realworld.py) for the full corpus.

### Telling "nothing there" from "couldn't read it"

```python
matches, diags = diagnose("5000 years ago", today=today)
# matches == []
# diags == [Diagnostic(text='5000 years ago', ..., reason='...outside the supported range')]
```

`parse()` never raises on user input.

`substitute()` is the one function that turns a flagged guess into a confident sentence, so
it can decline:

```python
substitute("revenue Q1 and Q3")                      # 'revenue April 2026 to December 2026'
substitute("revenue Q1 and Q3", min_confidence=0.9)  # 'revenue Q1 and Q3'
```

Raise `min_confidence` whenever the output will be read as fact — by a person or a model.
The default is 0.0, so nothing changes unless you ask.

A match whose neighbouring words change the period but could not be read comes back with
**confidence 0.5** and an explanation, rather than a confident answer to a question nobody
asked:

```python
matches, diags = diagnose("two years from March 2024", today=today)
matches[0].confidence   # 0.5
diags[0].reason         # "read 'March 2024' but not 'two years from', which changes the period"
```

Filter on `confidence` when a wrong range is worse than no range.

Two common cases land here rather than in the rules, because the range that comes back is
still the most useful one available — it just isn't the whole answer:

```python
parse("yesterday at 2pm")   # the whole day, confidence 0.5
# "read 'yesterday' but not 'at 2pm', which changes the period"

parse("Q1 and Q3")          # Apr-Dec, confidence 0.5 — Q2 is in there too
# "read 'Q1 and Q3' as one span, which also covers 2025-07-01 to 2025-10-01 in between"
```

Times of day are out of scope, so a date beside a clock resolves to the day. That is worth
saying out loud rather than answering a question about 2pm with 24 hours. **Only a
preposition counts** — `at`, `by`, `around`. A clock sitting straight against a date is part
of a timestamp (`2024-03-15T14:30:00Z`, `Mar 15 14:30:00`), where the day *is* the intended
answer, so log lines stay at full confidence.

`and` joins two periods into one span, which is right for `Q1 and Q2` and for
`between March and June` — adjacent periods are how people write a range. Only a **gap**
between them makes the hull a guess, and an explicit `between` or `from … to` settles it
either way.

### Precision on running prose

Bare month names are the dominant false positive for this kind of library — `strictness`
controls how eagerly they are claimed:

```python
parse("the march on Washington")        # [] — no cue, so "march" is a noun
parse("sales in March")                 # matched — "in" is a cue
parse("the March figures")              # matched — the cue can follow, too
WranglerConfig(strictness="greedy")       # match any month name anywhere
WranglerConfig(strictness="strict")       # require a year or explicit period marker
```

### June, May and April are also people

Four month names are common given names, so a cue alone is not enough — `sales by June
Patel` has a perfectly good cue in `by`. Four signals override it, each sufficient on its
own:

```python
parse("sales by June Patel")     # [] — a capitalised surname follows
parse("for Dr. May Chen")        # [] — a title precedes
parse("in June's laptop")        # [] — possessive over a noun no month owns
parse("for April said otherwise")  # [] — a verb only people perform
```

The possessive test turns on the noun, so `March's figures` and `June's revenue` stay
dates. Copulas are deliberately excluded from the verb list — `sales in June was strong`
has to keep working — and `June Quarter` is not read as a surname.

This is shape, not meaning: there is no name list and no grammar model, so a genuinely
ambiguous sentence like `June saw record sales` still reads as the month. The aim is to
stop the confident errors, not to resolve English. `strictness="greedy"` bypasses all of
it; `strict` needs an explicit year or period marker and never guesses.

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

**A coarse `date_format` never overstates a range.** With `date_format="%B %Y"`, a YTD
asked on 15 October would read `April 2026 to October 2026` — which says all of October,
sixteen days it does not cover. When the format cannot show a day *and* the range does not
sit on whole months, both ends fall back to `day_format` (`"%Y-%m-%d"` by default):

```python
fmt = make_formatter(date_format="%B %Y")
fmt(last_quarter)   # 'July 2026 to September 2026'   — whole months, as asked
fmt(ytd)            # '2026-04-01 to 2026-10-15'      — partial, so days
make_formatter(date_format="%B %Y", day_format=None)(ytd)
                    # 'April 2026 to October 2026'    — you asked for exactly this
```

The default `date_format` already shows days, so nothing changes unless yours is coarse.

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
