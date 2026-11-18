"""The surface a downstream tool actually calls: weeks, SQL, JSON.

These exist because a consumer had to work around each of them -- hand-building predicates
because `sql()` could not quote a column, and assuming Monday weeks because the parser had
no way to be told otherwise.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from date_wrangler import (
    Basis,
    DateMatch,
    DateRange,
    Grain,
    Mod,
    SqlDialect,
    WranglerConfig,
    parse_one,
)
from date_wrangler.calendars import week_range

TODAY = date(2026, 10, 15)  # a Thursday
US = WranglerConfig(week_starts_on=6)  # Sunday


def rng(text, cfg=None):
    m = parse_one(text, today=TODAY, config=cfg or WranglerConfig())
    assert m is not None, f"{text!r} found no date"
    return m.range


# ---------------------------------------------------------------------------
# week_starts_on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,monday,sunday",
    [
        ("this week",
         (date(2026, 10, 12), date(2026, 10, 19)), (date(2026, 10, 11), date(2026, 10, 18))),
        ("last week",
         (date(2026, 10, 5), date(2026, 10, 12)), (date(2026, 10, 4), date(2026, 10, 11))),
        ("next week",
         (date(2026, 10, 19), date(2026, 10, 26)), (date(2026, 10, 18), date(2026, 10, 25))),
    ],
)
def test_weeks_follow_the_configured_first_day(text, monday, sunday):
    """In a US workspace "last week" is Sunday to Saturday. Monday weeks are a day out at
    both ends, which is a whole day of data on each side of a weekly report."""
    assert (rng(text).start, rng(text).end) == monday
    assert (rng(text, US).start, rng(text, US).end) == sunday


def test_multi_week_spans_follow_it_too():
    assert rng("last 2 weeks").start == date(2026, 9, 28)
    assert rng("last 2 weeks", US).start == date(2026, 9, 27)


def test_this_weekday_follows_it():
    """"this Tuesday" is the Tuesday of the current week, so which week that is matters.
    On Sunday 18 October a Sunday week has just begun, and its Tuesday is the 20th; a
    Monday week is ending, and its Tuesday was the 13th. On Thursday the 15th both weeks
    hold the 13th.

    REGRESSION: this test once pinned the Sunday week's "this Tuesday" to Monday the 12th.
    The day was counted from the week's first day as if that were always a Monday."""
    sunday = date(2026, 10, 18)
    assert parse_one("this Tuesday", today=sunday).range.start == date(2026, 10, 13)
    assert parse_one("this Tuesday", today=sunday, config=US).range.start == date(2026, 10, 20)
    assert rng("this Tuesday").start == date(2026, 10, 13)
    assert rng("this Tuesday", US).start == date(2026, 10, 13)


_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@pytest.mark.parametrize("start", range(7))
@pytest.mark.parametrize("phrase", ["this {}", "{}"])
def test_a_weekday_named_is_that_weekday_whatever_day_the_week_starts(start, phrase):
    cfg = WranglerConfig(week_starts_on=start)
    week = week_range(TODAY, start)
    assert week.start is not None and week.end is not None
    for index, name in enumerate(_DAY_NAMES):
        r = rng(phrase.format(name), cfg)
        assert r.start is not None and r.start.weekday() == index, (name, start)
        assert week.start <= r.start < week.end, (name, start)


def test_the_weekend_deliberately_does_not_follow_it():
    """A weekend is the Saturday-Sunday pair. On a Sunday-start calendar that pair
    straddles the week boundary, so following the setting would split it."""
    assert (rng("this weekend").start, rng("this weekend").end) == (
        date(2026, 10, 17),
        date(2026, 10, 19),
    )
    assert rng("this weekend", US) == rng("this weekend")


def test_week_starts_on_is_validated_on_construction():
    with pytest.raises(ValueError, match="week_starts_on"):
        WranglerConfig(week_starts_on=7)
    with pytest.raises(ValueError, match="week_starts_on"):
        WranglerConfig(week_starts_on=-1)


# ---------------------------------------------------------------------------
# SqlDialect
# ---------------------------------------------------------------------------

Q2 = DateRange(date(2025, 4, 1), date(2025, 7, 1), Grain.QUARTER)


@pytest.mark.parametrize(
    "dialect,expected",
    [
        (SqlDialect.plain(), "order date >= '2025-04-01' AND order date < '2025-07-01'"),
        (
            SqlDialect.postgres(),
            "\"order date\" >= DATE '2025-04-01' AND \"order date\" < DATE '2025-07-01'",
        ),
        (
            SqlDialect.tsql(),
            "[order date] >= CAST('2025-04-01' AS DATE) AND "
            "[order date] < CAST('2025-07-01' AS DATE)",
        ),
        (
            SqlDialect.mysql(),
            "`order date` >= '2025-04-01' AND `order date` < '2025-07-01'",
        ),
        (
            SqlDialect.bigquery(),
            "`order date` >= DATE '2025-04-01' AND `order date` < DATE '2025-07-01'",
        ),
        (
            SqlDialect.redshift(),
            "\"order date\" >= DATE '2025-04-01' AND \"order date\" < DATE '2025-07-01'",
        ),
        (
            SqlDialect.databricks(),
            "`order date` >= DATE '2025-04-01' AND `order date` < DATE '2025-07-01'",
        ),
        (
            SqlDialect.fabric(),
            "[order date] >= CAST('2025-04-01' AS DATE) AND "
            "[order date] < CAST('2025-07-01' AS DATE)",
        ),
        (
            SqlDialect.hana(),
            "\"order date\" >= CAST('2025-04-01' AS DATE) AND "
            "\"order date\" < CAST('2025-07-01' AS DATE)",
        ),
    ],
)
def test_sql_dialects(dialect, expected):
    assert Q2.sql("order date", dialect=dialect) == expected


PRESETS = [
    SqlDialect.ansi, SqlDialect.postgres, SqlDialect.redshift, SqlDialect.oracle,
    SqlDialect.snowflake, SqlDialect.hana, SqlDialect.tsql, SqlDialect.fabric,
    SqlDialect.mysql, SqlDialect.bigquery, SqlDialect.databricks, SqlDialect.sqlite,
]


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.__name__)
def test_quoting_switched_off_keeps_the_presets_date(preset):
    """Supported, and relied on: a caller whose condition holds a placeholder the model
    fills in later takes a preset for its date and switches quoting off, because a quoted
    "<column>" is no longer a placeholder. Nothing may be quoted, whatever the name."""
    import dataclasses

    dialect = preset()
    off = dataclasses.replace(dialect, quote_open="", quote_close="")
    lit = dialect.date_literal
    assert Q2.sql("<column>", dialect=off) == (
        f"<column> >= {lit(date(2025, 4, 1))} AND <column> < {lit(date(2025, 7, 1))}"
    )
    for name in ("date", "order date", "m.date", 'a"b', "x]y"):
        assert off.identifier(name) == name


def test_one_quote_character_alone_is_refused():
    import dataclasses

    with pytest.raises(ValueError, match="both quote characters"):
        dataclasses.replace(SqlDialect.oracle(), quote_open="")


def test_fabric_is_tsql():
    """A Fabric Warehouse and its SQL analytics endpoint speak T-SQL; the name is for
    finding it."""
    assert SqlDialect.fabric() == SqlDialect.tsql()


def test_the_readme_table_is_what_each_preset_writes():
    """The README lists, per engine, how a quoted name and a date are written. Each row
    must be what the presets it names actually produce."""
    from pathlib import Path

    readme = Path(__file__).resolve().parent.parent / "README.md"
    if not readme.is_file():
        pytest.skip("README.md is not beside the tests")
    presets = {
        "ANSI": SqlDialect.ansi(), "Postgres": SqlDialect.postgres(),
        "Redshift": SqlDialect.redshift(), "Oracle": SqlDialect.oracle(),
        "Snowflake": SqlDialect.snowflake(), "SAP HANA": SqlDialect.hana(),
        "SQL Server": SqlDialect.tsql(), "Fabric (T-SQL)": SqlDialect.fabric(),
        "BigQuery": SqlDialect.bigquery(), "Databricks": SqlDialect.databricks(),
        "Spark SQL": SqlDialect.databricks(), "MySQL": SqlDialect.mysql(),
        "SQLite": SqlDialect.sqlite(),
    }
    lines = readme.read_text(encoding="utf-8").splitlines()
    first = lines.index("| Engine | Name | Date |") + 2  # past the header and its rule
    rows = []
    for line in lines[first:]:
        if not line.startswith("|"):
            break
        rows.append(line)
    named = set()
    for row in rows:
        engines, names, literal = (cell.strip() for cell in row.strip("|").split(" | "))
        code = [part.strip().strip("`").strip() for part in names.split(", ")]
        for engine in engines.split(", "):
            d = presets[engine]
            named.add(engine)
            assert code == [d.identifier("order date"), d.identifier("date")], row
            assert literal.strip("`") == d.date_literal(date(2025, 4, 1)), row
    assert named == set(presets), "an engine is missing from the README's table"


def test_the_default_is_exactly_what_it_always_was():
    assert Q2.sql("order_date") == (
        "order_date >= '2025-04-01' AND order_date < '2025-07-01'"
    )


def test_quoting_is_what_makes_an_awkward_column_name_work():
    """Not cosmetic: a column with a space is a syntax error unquoted, on every engine."""
    db = sqlite3.connect(":memory:")
    db.execute('CREATE TABLE t ("order date" DATE)')
    db.executemany(
        "INSERT INTO t VALUES (?)",
        [(d.isoformat(),) for d in
         DateRange(date(2025, 1, 1), date(2025, 12, 31), Grain.DAY).iter_days()],
    )
    quoted = Q2.sql("order date", dialect=SqlDialect.sqlite())
    assert db.execute(f"SELECT count(*) FROM t WHERE {quoted}").fetchone()[0] == 91
    with pytest.raises(sqlite3.OperationalError):
        db.execute(f"SELECT count(*) FROM t WHERE {Q2.sql('order date')}")


def test_a_closing_quote_inside_an_identifier_is_doubled():
    assert Q2.sql('a"b', dialect=SqlDialect.postgres()).startswith('"a""b" >=')
    assert Q2.sql("a]b", dialect=SqlDialect.tsql()).startswith("[a]]b] >=")


def test_dialect_composes_with_inclusive():
    assert Q2.sql("d", inclusive=True, dialect=SqlDialect.tsql()) == (
        "d >= CAST('2025-04-01' AS DATE) AND d <= CAST('2025-06-30' AS DATE)"
    )


def test_dialect_is_validated():
    with pytest.raises(ValueError, match="plain/date/cast"):
        SqlDialect(literal="iso")
    with pytest.raises(ValueError, match="both quote characters"):
        SqlDialect(quote_open='"')


# ---------------------------------------------------------------------------
# to_dict / from_dict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["last quarter", "today", "since March", "before 2024", "Q1 FY25", "MTD"]
)
def test_a_match_survives_a_json_round_trip(text):
    m = parse_one(text, today=TODAY)
    assert m is not None
    restored = DateMatch.from_dict(json.loads(json.dumps(m.to_dict())))
    assert restored == m


def test_the_dict_carries_both_ends_so_neither_side_recomputes():
    d = rng("last quarter").to_dict()
    assert d["end"] == "2026-10-01"  # exclusive, for machines
    assert d["end_inclusive"] == "2026-09-30"  # for people
    assert d["days"] == 92


def test_unbounded_ends_survive_the_round_trip():
    since = rng("since March")
    assert since.to_dict()["end"] is None
    assert DateRange.from_dict(since.to_dict()) == since
    assert DateRange.from_dict(since.to_dict()).mod is Mod.SINCE


def test_from_dict_ignores_the_derived_keys():
    """`end_inclusive` and `days` are output only; feeding them back must not confuse it."""
    raw = {"start": "2025-04-01", "end": "2025-07-01", "grain": "quarter",
           "basis": "fiscal", "end_inclusive": "nonsense", "days": -1}
    assert DateRange.from_dict(raw) == DateRange(
        date(2025, 4, 1), date(2025, 7, 1), Grain.QUARTER, Basis.FISCAL
    )


# ---------------------------------------------------------------------------
# Identifiers: quote only where it is required
# ---------------------------------------------------------------------------

ALL_QUOTING = [
    SqlDialect.oracle(), SqlDialect.snowflake(), SqlDialect.postgres(),
    SqlDialect.tsql(), SqlDialect.mysql(), SqlDialect.bigquery(),
    SqlDialect.redshift(), SqlDialect.databricks(), SqlDialect.fabric(),
    SqlDialect.hana(), SqlDialect.ansi(), SqlDialect.sqlite(),
]


@pytest.mark.parametrize("dialect", ALL_QUOTING)
def test_a_plain_name_is_never_quoted(dialect):
    """REGRESSION: always quoting broke Oracle and Snowflake, which store an unquoted name
    in upper case -- so "claim_date" is an invalid identifier for a column created as
    claim_date. Bare is correct on every engine."""
    assert dialect.identifier("claim_date") == "claim_date"


@pytest.mark.parametrize(
    "dialect,column,expected",
    [
        (SqlDialect.postgres(), "m.claim_date", "m.claim_date"),
        (SqlDialect.oracle(), "m.date", 'm."DATE"'),
        (SqlDialect.tsql(), "order date.claim_date", "[order date].claim_date"),
        (SqlDialect.postgres(), "s.t.order date", 's.t."order date"'),
    ],
)
def test_a_qualified_name_is_quoted_part_by_part(dialect, column, expected):
    """REGRESSION: "m.claim_date" quoted whole is one column with a dot in its name, which
    looks right and refers to nothing."""
    assert dialect.identifier(column) == expected


@pytest.mark.parametrize(
    "dialect,expected",
    [
        (SqlDialect.oracle(), '"DATE"'),
        (SqlDialect.snowflake(), '"DATE"'),
        (SqlDialect.postgres(), '"date"'),
        (SqlDialect.tsql(), "[date]"),
        (SqlDialect.mysql(), "`date`"),
        (SqlDialect.redshift(), '"date"'),
        (SqlDialect.hana(), '"DATE"'),
        (SqlDialect.fabric(), "[date]"),
        (SqlDialect.databricks(), "`date`"),
    ],
)
def test_a_reserved_word_is_quoted_in_the_case_the_engine_stored_it(dialect, expected):
    """Quoted only because it is reserved, so it was created unquoted and stored folded:
    DATE on Oracle, date on Postgres."""
    assert dialect.identifier("date") == expected


def test_a_name_that_needs_quoting_keeps_its_case():
    """A space or punctuation means it was created quoted, so it is stored verbatim."""
    assert SqlDialect.oracle().identifier("Order Date") == '"Order Date"'


def test_always_quoting_is_still_available():
    always = SqlDialect('"', '"', "date", quoting="always")
    assert always.identifier("claim_date") == '"claim_date"'


def test_the_full_predicate_on_oracle():
    q = DateRange(date(2026, 4, 1), date(2026, 7, 1), Grain.QUARTER)
    assert q.sql("m.claim_date", dialect=SqlDialect.oracle()) == (
        "m.claim_date >= DATE '2026-04-01' AND m.claim_date < DATE '2026-07-01'"
    )
