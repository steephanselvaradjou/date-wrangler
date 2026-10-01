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
    """"this Tuesday" is the one in the current week, so which week that is matters."""
    assert rng("this Tuesday").start == date(2026, 10, 13)
    assert rng("this Tuesday", US).start == date(2026, 10, 12)


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
    ],
)
def test_sql_dialects(dialect, expected):
    assert Q2.sql("order date", dialect=dialect) == expected


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
        "[d] >= CAST('2025-04-01' AS DATE) AND [d] <= CAST('2025-06-30' AS DATE)"
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
