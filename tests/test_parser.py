"""Parser tests.

``today`` is pinned to 2025-09-04 throughout. With an April fiscal start that day sits in
FY2026 (Apr 2025 - Mar 2026) and in the *second* fiscal quarter, which is what makes it a
useful anchor: a bare "Q1" resolving to April 2025 proves the current-fiscal-year default,
and "this quarter" resolving to July proves the quarter grid independently.

Cases marked REGRESSION reproduce a specific defect in the predecessor module.
"""

from __future__ import annotations

from datetime import date

import pytest

from date_wrangler import (
    Basis,
    DateOrder,
    FiscalCalendar,
    Grain,
    Mod,
    WranglerConfig,
    diagnose,
    parse,
    parse_one,
    substitute,
)

TODAY = date(2025, 9, 4)
CFG = WranglerConfig()  # April fiscal start, bare periods fiscal


def rng(text: str, cfg: WranglerConfig = CFG):
    """The single range parsed from ``text``, as a (start, end) tuple."""
    matches = parse(text, today=TODAY, config=cfg)
    assert len(matches) == 1, f"expected exactly one match in {text!r}, got {len(matches)}"
    r = matches[0].range
    return r.start, r.end


def ranges(text: str, cfg: WranglerConfig = CFG):
    return [(m.range.start, m.range.end) for m in parse(text, today=TODAY, config=cfg)]


# ---------------------------------------------------------------------------
# Fiscal periods
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Q1 FY25", (date(2024, 4, 1), date(2024, 7, 1))),
        ("Q1 2024", (date(2023, 4, 1), date(2023, 7, 1))),
        ("H1 FY25", (date(2024, 4, 1), date(2024, 10, 1))),
        ("FY24", (date(2023, 4, 1), date(2024, 4, 1))),
        ("FY2024-25", (date(2024, 4, 1), date(2025, 4, 1))),
        ("third month of fy24", (date(2023, 6, 1), date(2023, 7, 1))),
    ],
)
def test_fiscal_periods(text, expected):
    assert rng(text) == expected


def test_bare_quarter_uses_the_current_fiscal_year():
    """REGRESSION: a bare Q1 defaulted to today.year read as a fiscal label, so from
    April to December it named the fiscal year that had already ended."""
    assert rng("Q1") == (date(2025, 4, 1), date(2025, 7, 1))
    assert rng("H1") == (date(2025, 4, 1), date(2025, 10, 1))
    assert rng("twelfth month") == (date(2026, 3, 1), date(2026, 4, 1))


def test_cy_marker_forces_the_calendar_basis():
    """REGRESSION: the fy/cy test required a trailing word boundary, which "cy2024" has
    not, so every CY token silently fell through to the fiscal default."""
    assert rng("CY2024") == (date(2024, 1, 1), date(2025, 1, 1))
    assert parse("CY2024", today=TODAY)[0].range.basis is Basis.CALENDAR


def test_quarter_index_is_not_taken_from_the_year():
    """REGRESSION: ``q(?:tr)?\\s*(\\d)`` matched the "2" of "qtr 2024", so "1st qtr 2024"
    and "3rd qtr 2019" both resolved to Q2."""
    assert rng("1st qtr 2024") == (date(2023, 4, 1), date(2023, 7, 1))
    assert rng("first qtr 2024") == (date(2023, 4, 1), date(2023, 7, 1))
    assert rng("3rd qtr 2019") == (date(2018, 10, 1), date(2019, 1, 1))


def test_two_digit_years_use_the_pivot():
    """REGRESSION: every two-digit year mapped to 20xx, so FY99 meant 2099."""
    assert rng("fy99") == (date(1998, 4, 1), date(1999, 4, 1))
    assert rng("fy24") == (date(2023, 4, 1), date(2024, 4, 1))


def test_bare_year_is_a_calendar_year():
    assert rng("2024") == (date(2024, 1, 1), date(2025, 1, 1))


# ---------------------------------------------------------------------------
# Relative periods
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("this month", (date(2025, 9, 1), date(2025, 10, 1))),
        ("this quarter", (date(2025, 7, 1), date(2025, 10, 1))),
        ("this year", (date(2025, 4, 1), date(2026, 4, 1))),
        ("last month", (date(2025, 8, 1), date(2025, 9, 1))),
        ("last 3 months", (date(2025, 6, 1), date(2025, 9, 1))),
        ("next 2 quarters", (date(2025, 10, 1), date(2026, 4, 1))),
        ("last year", (date(2024, 4, 1), date(2025, 4, 1))),
        ("next cy", (date(2026, 1, 1), date(2027, 1, 1))),
        ("this week", (date(2025, 9, 1), date(2025, 9, 8))),
        ("last week", (date(2025, 8, 25), date(2025, 9, 1))),
        ("last 7 days", (date(2025, 8, 28), date(2025, 9, 4))),
        ("yesterday", (date(2025, 9, 3), date(2025, 9, 4))),
        ("today", (date(2025, 9, 4), date(2025, 9, 5))),
        ("tomorrow", (date(2025, 9, 5), date(2025, 9, 6))),
    ],
)
def test_relative_periods(text, expected):
    assert rng(text) == expected


def test_ago_names_one_period_not_a_span():
    """REGRESSION: "N units ago" was routed through the "last N units" handler, so
    "3 months ago" returned a three month span and "eleven months ago" returned a year."""
    assert rng("3 months ago") == (date(2025, 6, 1), date(2025, 7, 1))
    assert rng("eleven months ago") == (date(2024, 10, 1), date(2024, 11, 1))


def test_twelve_is_a_cardinal():
    """REGRESSION: NUMBER_WORDS held 'twelfth' (an ordinal) and lacked 'twelve'."""
    assert rng("twelve months ago") == (date(2024, 9, 1), date(2024, 10, 1))


def test_zero_count_is_reported_not_inverted():
    """REGRESSION: "last 0 months" produced a range whose end preceded its start."""
    matches, diags = diagnose("last 0 months", today=TODAY)
    assert matches == []
    assert any("at least 1" in d.reason for d in diags)


# ---------------------------------------------------------------------------
# To-date
# ---------------------------------------------------------------------------


def test_to_date_includes_today():
    assert rng("ytd") == (date(2025, 4, 1), date(2025, 9, 5))
    assert rng("mtd") == (date(2025, 9, 1), date(2025, 9, 5))
    assert rng("qtd") == (date(2025, 7, 1), date(2025, 9, 5))


def test_last_ytd_is_the_same_window_a_year_earlier():
    """REGRESSION: "last YTD" returned the whole prior fiscal year, so a five month
    year-to-date figure was compared against a twelve month one."""
    assert rng("last ytd") == (date(2024, 4, 1), date(2024, 9, 5))


# ---------------------------------------------------------------------------
# Absolute dates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2024-03-15", date(2024, 3, 15)),
        ("15 January 2024", date(2024, 1, 15)),
        ("15 jan 2024", date(2024, 1, 15)),
        ("31 March 2024", date(2024, 3, 31)),
    ],
)
def test_absolute_dates(text, expected):
    assert rng(text) == (expected, date.fromordinal(expected.toordinal() + 1))


def test_day_of_month_is_not_read_as_a_year():
    """REGRESSION: the year pattern accepted any 2-4 digit run, so "january 15, 2024"
    resolved to January 2015."""
    start, end = rng("january 15, 2024")
    assert start == date(2024, 1, 15)
    assert end == date(2024, 1, 16)


def test_numeric_date_order_is_configurable():
    dmy = WranglerConfig(date_order=DateOrder.DMY)
    mdy = WranglerConfig(date_order=DateOrder.MDY)
    assert rng("03/04/2024", dmy)[0] == date(2024, 4, 3)
    assert rng("03/04/2024", mdy)[0] == date(2024, 3, 4)
    # A component above 12 can only be a day, whatever the setting says.
    assert rng("25/04/2024", mdy)[0] == date(2024, 4, 25)


# ---------------------------------------------------------------------------
# Ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Q1 to Q2", (date(2025, 4, 1), date(2025, 10, 1))),
        ("Q1 to Q2 FY24", (date(2023, 4, 1), date(2023, 10, 1))),
        ("from Jan 2024 to Mar 2024", (date(2024, 1, 1), date(2024, 4, 1))),
        ("FY24 to FY25", (date(2023, 4, 1), date(2025, 4, 1))),
        ("2023 to 2024", (date(2023, 1, 1), date(2025, 1, 1))),
    ],
)
def test_ranges(text, expected):
    assert rng(text) == expected


@pytest.mark.parametrize(
    "connector", ["to", "through", "thru", "until", "till", "upto", "-", "–", "—", "and"]
)
def test_connector_vocabulary(connector):
    """REGRESSION: only to/and/- were connectors; everything else produced two separate
    matches that read as garbage, and Word turns "-" into an en dash on sight."""
    assert rng(f"Q1 {connector} Q2") == (date(2025, 4, 1), date(2025, 10, 1))


def test_range_endpoints_share_one_basis():
    """REGRESSION: "Q1 to Q2 of 2024" resolved its first endpoint fiscally and its second
    on the calendar, producing a fifteen month span."""
    assert rng("Q1 to Q2 of 2024") == (date(2023, 4, 1), date(2023, 10, 1))


def test_ranges_wrap_across_a_year_boundary():
    assert rng("Nov to Feb") == (date(2025, 11, 1), date(2026, 3, 1))
    assert rng("Q4 to Q1") == (date(2026, 1, 1), date(2026, 7, 1))


def test_between_is_consumed_into_the_match():
    (start, end), = ranges("How much did we book between April and September 2024?")
    assert (start, end) == (date(2024, 4, 1), date(2024, 10, 1))


# ---------------------------------------------------------------------------
# Comparisons and lists must not become ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "compare Q1 2024 to Q1 2025",
        "Q1 2024 vs Q1 2025",
        "Q1 2024 versus Q1 2025",
        "Q1 2024 against Q1 2025",
        "Q1 2024 compared with Q1 2025",
    ],
)
def test_comparisons_stay_two_matches(text):
    """REGRESSION: "compare Q1 2024 to Q1 2025" merged both comparands into one fifteen
    month span -- the most damaging behaviour for an analytics front end."""
    assert len(parse(text, today=TODAY)) == 2


def test_lists_stay_separate_and_share_a_year():
    """REGRESSION: "Q1, Q2 and Q3" read the trailing "and" as a range connector and
    merged the last two; "Jan, Feb, Mar 2024" resolved each month to a different year."""
    assert len(ranges("Q1, Q2 and Q3")) == 3
    assert ranges("Jan, Feb, Mar 2024") == [
        (date(2024, 1, 1), date(2024, 2, 1)),
        (date(2024, 2, 1), date(2024, 3, 1)),
        (date(2024, 3, 1), date(2024, 4, 1)),
    ]


def test_and_still_joins_a_plain_pair():
    assert rng("Q1 and Q2") == (date(2025, 4, 1), date(2025, 10, 1))


# ---------------------------------------------------------------------------
# Open-ended ranges and modifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,start,end,mod",
    [
        ("since April 2024", date(2024, 4, 1), None, Mod.SINCE),
        ("from Q1 onwards", date(2025, 4, 1), None, Mod.SINCE),
        ("after FY24", date(2024, 4, 1), None, Mod.AFTER),
        ("up to March 2024", None, date(2024, 4, 1), Mod.UNTIL),
        ("till March 2024", None, date(2024, 4, 1), Mod.UNTIL),
        ("before 2024", None, date(2024, 1, 1), Mod.BEFORE),
        ("prior to Q3", None, date(2025, 10, 1), Mod.BEFORE),
    ],
)
def test_open_ended_ranges(text, start, end, mod):
    r = parse_one(text, today=TODAY).range
    assert (r.start, r.end, r.mod) == (start, end, mod)
    assert not r.is_bounded


def test_until_includes_the_period_and_before_excludes_it():
    assert parse_one("until March 2024", today=TODAY).range.end == date(2024, 4, 1)
    assert parse_one("before March 2024", today=TODAY).range.end == date(2024, 3, 1)


def test_as_of_is_a_single_day():
    r = parse_one("as of March 2024", today=TODAY).range
    assert (r.start, r.end, r.grain, r.mod) == (
        date(2024, 3, 31), date(2024, 4, 1), Grain.DAY, Mod.AS_OF,
    )


def test_open_end_stays_none_until_the_caller_clamps():
    r = parse_one("since April 2024", today=TODAY).range
    assert r.end is None
    assert r.sql("d") == "d >= '2024-04-01'"
    assert r.clamp(hi=date(2025, 9, 5)).end == date(2025, 9, 5)


# ---------------------------------------------------------------------------
# Precision on running prose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "the march on Washington",
        "my august colleague",
        "a may-december romance",
        "Augusta and Marchetti",
        "show me the top 5 customers",
        "he said may be so",
    ],
)
def test_prose_does_not_produce_dates(text):
    assert parse(text, today=TODAY) == []


@pytest.mark.parametrize(
    "text", ["sales in March", "revenue for March", "March", "from Jan to Mar"]
)
def test_cued_months_still_parse(text):
    assert parse(text, today=TODAY) != []


def test_strictness_settings():
    greedy = WranglerConfig(strictness="greedy")
    strict = WranglerConfig(strictness="strict")
    assert parse("the march on Washington", today=TODAY, config=greedy) != []
    assert parse("sales in March", today=TODAY, config=strict) == []
    assert parse("sales in Q1", today=TODAY, config=strict) != []


# ---------------------------------------------------------------------------
# Spans, substitution and robustness
# ---------------------------------------------------------------------------


def test_spans_index_the_original_string():
    text = "revenue for Q1 FY25 please"
    m = parse_one(text, today=TODAY)
    assert text[m.span[0] : m.span[1]] == m.text == "Q1 FY25"


@pytest.mark.parametrize(
    "text",
    ["Q1 2024", "q1 ‘24", "Q1 2024​", "café sales in Q1 2024", "Q1 – Q2"],
)
def test_spans_survive_unicode_normalisation(text):
    for m in parse(text, today=TODAY):
        assert text[m.span[0] : m.span[1]] == m.text


def test_substitute_replaces_only_the_matched_phrase():
    """REGRESSION: patterns swallowed neighbouring filler, so "sales report of Q1" came
    back as "sales <range>" with "report" deleted."""
    out = substitute("sales report of Q1", today=TODAY)
    assert "report" in out
    assert "Q1" not in out


def test_substitute_output_does_not_re_match_itself():
    """REGRESSION: the output began "period from ...", which the parser then matched in
    its own result, so re-running grew "period period period from ..."."""
    once = substitute("q1 2024", today=TODAY)
    assert substitute(once, today=TODAY) == once


def test_output_is_locale_independent():
    """REGRESSION: strftime('%B') honours LC_TIME, so an unrelated setlocale elsewhere in
    the process changed "June" to "Juni"."""
    import locale

    baseline = substitute("q1 2024", today=TODAY)
    for name in ("de_DE.UTF-8", "de_DE", "German_Germany"):
        try:
            locale.setlocale(locale.LC_TIME, name)
        except locale.Error:
            continue
        try:
            assert substitute("q1 2024", today=TODAY) == baseline
        finally:
            locale.setlocale(locale.LC_TIME, "C")
        break


@pytest.mark.parametrize(
    "text", ["", "   ", "5000 years ago", "99999 months ago", "next 9999 years", "no dates here"]
)
def test_never_raises_on_hostile_input(text):
    assert isinstance(parse(text, today=TODAY), list)


def test_rejects_non_string_input():
    with pytest.raises(TypeError):
        parse(None, today=TODAY)  # type: ignore[arg-type]


def test_diagnose_separates_nothing_there_from_could_not_read():
    empty, no_diags = diagnose("show me the top customers", today=TODAY)
    assert (empty, no_diags) == ([], [])
    matches, diags = diagnose("5000 years ago", today=TODAY)
    assert matches == [] and diags


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_fiscal_calendar_is_honoured_per_call():
    us = WranglerConfig(fiscal=FiscalCalendar.us_federal())
    au = WranglerConfig(fiscal=FiscalCalendar.australia())
    assert rng("FY24", us) == (date(2023, 10, 1), date(2024, 10, 1))
    assert rng("FY24", au) == (date(2023, 7, 1), date(2024, 7, 1))
    assert rng("FY24", CFG) == (date(2023, 4, 1), date(2024, 4, 1))


def test_bare_period_basis_is_configurable():
    calendar_first = WranglerConfig(bare_period_basis=Basis.CALENDAR)
    assert rng("Q1", calendar_first) == (date(2025, 1, 1), date(2025, 4, 1))
    assert rng("Q1") == (date(2025, 4, 1), date(2025, 7, 1))


def test_q1_and_q1_of_year_agree_by_default():
    """The predecessor resolved these a year apart, which was its most surprising
    behaviour. Whatever the configured basis, the two phrasings must match."""
    for cfg in (CFG, WranglerConfig(bare_period_basis=Basis.CALENDAR)):
        assert rng("Q1 2024", cfg) == rng("Q1 of 2024", cfg)


def test_prefilter_never_hides_a_match():
    """The fast reject path must not be able to drop something the scanner would find."""
    from date_wrangler.wrangler import _PREFILTER, _SCANNER

    for text in ("Q1", "march", "last week", "ytd", "2024-01-01", "h1", "first quarter"):
        if _SCANNER.search(text):
            assert _PREFILTER.search(text), f"prefilter would drop {text!r}"


# ---------------------------------------------------------------------------
# Anchoring "today"
# ---------------------------------------------------------------------------


def _zone(name: str):
    """A ZoneInfo, or skip.

    Windows ships no system timezone database, so `zoneinfo` needs the `tzdata` package
    there. It is declared as a Windows-only dependency; this keeps the suite honest for
    anyone running it without one installed rather than erroring out mid-assertion.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:  # pragma: no cover - platform dependent
        pytest.skip(f"no timezone database available for {name!r}; install tzdata")


def test_today_is_optional():
    """Omitting it must keep working; only determinism is lost, not correctness."""
    assert parse("Q1") != []
    assert parse("Q1", today=None) == parse("Q1")


def test_tz_picks_the_date_in_that_zone():
    """The failure this prevents: a UTC server answering an Asia/Kolkata reader just
    after local midnight is still on the previous date, so "this month" quietly returns
    last month."""
    from datetime import datetime, timezone

    # 2025-08-31 22:00 UTC is already 2025-09-01 03:30 in Kolkata.
    instant = datetime(2025, 8, 31, 22, 0, tzinfo=timezone.utc)
    kolkata = _zone("Asia/Kolkata")

    utc_month = parse_one("this month", today=instant).range
    ist_month = parse_one("this month", today=instant, tz=kolkata).range
    assert utc_month.start == date(2025, 8, 1)
    assert ist_month.start == date(2025, 9, 1)


def test_datetime_is_accepted_wherever_a_date_is():
    from datetime import datetime

    assert rng("Q1") == (
        parse_one("Q1", today=datetime(2025, 9, 4, 13, 30)).range.start,
        parse_one("Q1", today=datetime(2025, 9, 4, 13, 30)).range.end,
    )


def test_explicit_today_wins_over_tz():
    r = parse_one("this month", today=TODAY, tz=_zone("Pacific/Kiritimati")).range
    assert r.start == date(2025, 9, 1)


def test_naive_datetime_with_tz_is_refused_not_guessed():
    from datetime import datetime

    kolkata = _zone("Asia/Kolkata")
    with pytest.raises(ValueError, match="naive datetime"):
        parse("Q1", today=datetime(2025, 9, 4), tz=kolkata)


def test_bad_today_type_is_rejected():
    with pytest.raises(TypeError):
        parse("Q1", today="2025-09-04")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Compact fiscal-year spellings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Q1FY24", (date(2023, 4, 1), date(2023, 7, 1))),
        ("Q1 FY24", (date(2023, 4, 1), date(2023, 7, 1))),
        ("F.Y. 2024", (date(2023, 4, 1), date(2024, 4, 1))),
        ("fy-24", (date(2023, 4, 1), date(2024, 4, 1))),
        ("C.Y. 2024", (date(2024, 1, 1), date(2025, 1, 1))),
    ],
)
def test_compact_fiscal_spellings(text, expected):
    assert rng(text) == expected


def test_a_bare_year_still_needs_a_space():
    """"Q12024" must not be read as Q1 plus 2024 -- only a marked year may abut."""
    assert parse("Q12024", today=TODAY) == []


# ---------------------------------------------------------------------------
# Reporting shorthand and period-ending phrases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("TTM", (date(2024, 9, 1), date(2025, 9, 1))),
        ("LTM", (date(2024, 9, 1), date(2025, 9, 1))),
        ("T12M", (date(2024, 9, 1), date(2025, 9, 1))),
        ("L3M", (date(2025, 6, 1), date(2025, 9, 1))),
    ],
)
def test_trailing_month_shorthand(text, expected):
    assert rng(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("quarter ending June 2024", (date(2024, 4, 1), date(2024, 7, 1))),
        ("year ended March 2024", (date(2023, 4, 1), date(2024, 4, 1))),
        ("month ended Jan 2024", (date(2024, 1, 1), date(2024, 2, 1))),
    ],
)
def test_period_ending_covers_the_whole_period(text, expected):
    """Reading only the month out of "quarter ending June 2024" narrows a three month
    figure to one month, silently."""
    assert rng(text) == expected


# ---------------------------------------------------------------------------
# Weekdays
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("last Monday", date(2025, 9, 1)),
        ("next Friday", date(2025, 9, 5)),
        ("this Tuesday", date(2025, 9, 2)),
        ("Monday", date(2025, 9, 1)),
    ],
)
def test_weekdays(text, expected):
    assert rng(text)[0] == expected


def test_last_and_next_weekday_are_strict():
    """TODAY is a Thursday. "Last Thursday" must be a week ago, not today."""
    assert TODAY.weekday() == 3
    assert rng("last Thursday")[0] == date(2025, 8, 28)
    assert rng("next Thursday")[0] == date(2025, 9, 11)


def test_bare_weekday_needs_a_cue_in_prose():
    assert parse("the monday meeting ran long", today=TODAY) == []
    assert parse("sales on Monday", today=TODAY) != []


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def test_formatted_range_has_no_leading_from():
    """"sales report of from April to June" reads as a typo."""
    out = substitute("sales report of Q1", today=TODAY)
    assert out == "sales report of April 2025 to June 2025"


def test_make_formatter_controls_dates_and_structure():
    from date_wrangler import make_formatter

    iso = make_formatter()
    assert substitute("Q1 FY25", today=TODAY, formatter=iso) == "2024-04-01 to 2024-06-30"

    slashes = make_formatter(date_format="%d/%m/%Y", closed="{start} - {end}")
    assert substitute("Q1 FY25", today=TODAY, formatter=slashes) == "01/04/2024 - 30/06/2024"

    bracketed = make_formatter(closed="[{start}, {end}]", single="[{start}]")
    assert substitute("Q1 FY25", today=TODAY, formatter=bracketed) == "[2024-04-01, 2024-06-30]"


def test_make_formatter_inclusive_end_switch():
    from date_wrangler import make_formatter

    inclusive = make_formatter()
    exclusive = make_formatter(inclusive_end=False)
    r = parse_one("Q1 FY25", today=TODAY).range
    assert inclusive(r) == "2024-04-01 to 2024-06-30"
    assert exclusive(r) == "2024-04-01 to 2024-07-01"


def test_make_formatter_handles_open_ranges():
    from date_wrangler import make_formatter

    fmt = make_formatter()
    assert fmt(parse_one("since April 2024", today=TODAY).range) == "2024-04-01 onwards"
    assert fmt(parse_one("up to March 2024", today=TODAY).range) == "up to 2024-03-31"
    assert fmt(parse_one("before 2024", today=TODAY).range) == "before 2024-01-01"
    assert fmt(parse_one("as of March 2024", today=TODAY).range) == "as of 2024-03-31"


def test_a_plain_callable_still_works_as_a_formatter():
    out = substitute("Q1 FY25", today=TODAY, formatter=lambda r: f"<{r.start}..{r.end})")
    assert out == "<2024-04-01..2024-07-01)"


# ---------------------------------------------------------------------------
# "jan 24": day or year
# ---------------------------------------------------------------------------


def test_a_bare_number_after_a_month_follows_the_setting():
    from date_wrangler import MonthNumber

    as_year = WranglerConfig(month_number=MonthNumber.YEAR)
    assert rng("jan 24") == (date(2025, 1, 24), date(2025, 1, 25))
    assert rng("jan 24", as_year) == (date(2024, 1, 1), date(2024, 2, 1))


@pytest.mark.parametrize("cfg_kind", ["day", "year"])
@pytest.mark.parametrize(
    "text,start,end",
    [
        # A single digit is never a year.
        ("march 3", date(2025, 3, 3), date(2025, 3, 4)),
        # An ordinal suffix is always a day.
        ("jan 24th", date(2025, 1, 24), date(2025, 1, 25)),
        # An apostrophe is always a year.
        ("jan '24", date(2024, 1, 1), date(2024, 2, 1)),
        # Four digits are always a year.
        ("jan 2024", date(2024, 1, 1), date(2024, 2, 1)),
        # Above 31 can only be a year.
        ("jan 87", date(1987, 1, 1), date(1987, 2, 1)),
        # An explicit year makes the other number a day.
        ("january 15, 2024", date(2024, 1, 15), date(2024, 1, 16)),
    ],
)
def test_unambiguous_forms_ignore_the_setting(cfg_kind, text, start, end):
    from date_wrangler import MonthNumber

    cfg = WranglerConfig(
        month_number=MonthNumber.YEAR if cfg_kind == "year" else MonthNumber.DAY
    )
    assert rng(text, cfg) == (start, end)


def test_month_number_year_reads_the_authors_range_correctly():
    """"jan 24-mar 2025" is Jan 2024 to Mar 2025 to anyone writing a finance sheet."""
    from date_wrangler import MonthNumber

    cfg = WranglerConfig(month_number=MonthNumber.YEAR)
    assert rng("jan 24-mar 2025", cfg) == (date(2024, 1, 1), date(2025, 4, 1))
