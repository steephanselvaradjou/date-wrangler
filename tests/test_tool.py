"""The agent tool: its schema, the answer it gives, and the assumptions it owns up to.

What a model is handed and what it gets back are a contract with code nobody here can
see -- prompts, agent frameworks, MCP clients -- so the shape is pinned as well as the
dates. The CLI's ``--json`` is the same answer, and is held to it here too.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from date_wrangler import DateMatch, parse, tool
from date_wrangler.__main__ import main
from date_wrangler.config import DEFAULT_CONFIG, DateOrder, MonthNumber, YearLabel
from date_wrangler.types import Anchor, Basis

TODAY = "2026-11-16"

#: JSON Schema keywords strict tool use does not accept. A schema using one is rejected
#: outright, or silently loosened, depending on the provider.
_NOT_STRICT = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
               "minLength", "maxLength", "pattern", "minItems", "maxItems"}


def answer(text: str, **settings: object) -> dict:
    return tool.call({"text": text, "today": TODAY, **settings})


def assumed(result: dict) -> dict[str, dict]:
    return {a["setting"]: a for a in result["assumptions"]}


# ---------------------------------------------------------------------------
# The definition a model is handed
# ---------------------------------------------------------------------------


def test_the_definition_is_name_description_and_schema():
    spec = tool.definition()
    assert set(spec) == {"name", "description", "input_schema"}
    assert spec["name"] == tool.NAME == "resolve_dates"
    assert spec["input_schema"] == tool.INPUT_SCHEMA
    assert spec["input_schema"]["required"] == ["text"]


def test_each_definition_is_a_fresh_copy():
    """Adding ``strict`` or ``cache_control`` to one must not leak into the next caller's."""
    first = tool.definition()
    first["input_schema"]["properties"]["text"]["description"] = "changed"
    first["strict"] = True
    again = tool.definition()
    assert "strict" not in again
    assert again["input_schema"]["properties"]["text"]["description"] != "changed"


def test_the_schema_is_one_strict_tool_use_accepts():
    def walk(node: object) -> None:
        if isinstance(node, dict):
            assert not _NOT_STRICT & set(node), node
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(tool.INPUT_SCHEMA)


def test_every_argument_is_described_for_the_model():
    for name, prop in tool.INPUT_SCHEMA["properties"].items():
        assert len(prop.get("description", "")) > 20, name


def test_the_schema_offers_exactly_the_values_the_library_has():
    props = tool.INPUT_SCHEMA["properties"]
    assert props["fiscal_start_month"]["enum"] == list(range(1, 13))
    assert props["fiscal_year_label"]["enum"] == [y.value for y in YearLabel]
    assert set(props["year_basis"]["enum"]) == {b.value for b in Basis}
    assert props["date_order"]["enum"] == [d.value for d in DateOrder]
    assert props["month_number"]["enum"] == [n.value for n in MonthNumber]
    assert props["anchor"]["enum"] == [a.value for a in Anchor]
    assert len(props["week_starts_on"]["enum"]) == 7
    assert props["weekend"]["items"]["enum"] == props["week_starts_on"]["enum"]
    assert props["holidays"]["items"] == {"type": "string", "format": "date"}


def test_the_schema_and_the_function_take_the_same_arguments():
    """A property the function does not take would fail every call that used it, and a
    parameter the schema leaves out could never be set by a model."""
    params = set(inspect.signature(tool.resolve_dates).parameters)
    python_only = {"strictness", "min_confidence"}
    assert params - python_only == set(tool.INPUT_SCHEMA["properties"])


def test_the_assumption_notes_describe_the_library_defaults():
    """The notes are written for these defaults ("read day first", "start on Monday", "no
    holidays"); if a default in the library moves, the notes have to move with it."""
    assert tool._DEFAULTS == {
        "timezone": None, "fiscal_start_month": 4, "fiscal_year_label": "end_year",
        "year_basis": "calendar", "date_order": "DMY", "month_number": "day",
        "two_digit_pivot": 68, "week_starts_on": "monday", "anchor": "anchored",
        "weekend": ["saturday", "sunday"], "holidays": [],
    }
    assert DEFAULT_CONFIG.two_digit_pivot == 68
    assert DEFAULT_CONFIG.fiscal.start_month == 4
    assert DEFAULT_CONFIG.fiscal.label_by is YearLabel.END_YEAR
    assert DEFAULT_CONFIG.effective_year_basis is Basis.CALENDAR
    assert DEFAULT_CONFIG.date_order is DateOrder.DMY
    assert DEFAULT_CONFIG.month_number is MonthNumber.DAY
    assert DEFAULT_CONFIG.week_starts_on == 0
    assert DEFAULT_CONFIG.anchor is Anchor.ANCHORED
    assert DEFAULT_CONFIG.weekend == (5, 6) and DEFAULT_CONFIG.holidays == frozenset()


def test_every_setting_has_a_default_and_an_entry_in_the_answer():
    assert set(tool._DEFAULTS) == set(tool.INPUT_SCHEMA["properties"]) - {"text", "today"}
    assert set(tool.OUTPUT_SCHEMA["properties"]["settings"]["properties"]) == set(tool._DEFAULTS)


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


def test_a_fiscal_quarter_comes_back_with_its_inclusive_days():
    result = answer("Revenue for Q3 FY24, please")
    (m,) = result["matches"]
    assert m["text"] == "Q3 FY24"
    assert (m["first_day"], m["last_day"]) == ("2023-10-01", "2023-12-31")
    assert m["reads_as"] == "October 2023 to December 2023"
    assert m["range"]["end"] == "2024-01-01", "range.end stays exclusive, as everywhere"
    assert m["iso"] == "2023-10-01/2024-01-01"
    assert m["confidence"] == 1.0 and m["notes"] == [] and m["relative_to_today"] is False
    assert result["today"] == TODAY
    assert result["text"] == "Revenue for Q3 FY24, please"


def test_each_match_carries_everything_to_dict_gives():
    """The answer adds to DateMatch.to_dict and never renames, so code reading the old
    ``--json`` keeps working."""
    text = "Q1-Q3 2024 vs Q1-Q3 2025, and 1-15 March"
    result = answer(text)
    found = parse(text, today=date.fromisoformat(TODAY))
    assert len(result["matches"]) == len(found)
    for got, m in zip(result["matches"], found, strict=True):
        for key, value in m.to_dict().items():
            assert got[key] == value, key
        assert DateMatch.from_dict(got) == m


def test_the_answer_is_plain_json():
    for text in ["since March", "Q3 FY24 vs Q3 FY23", "before 2024", "last 3 months",
                 "next week at 3pm", "w.e.f. 1 April 2024", "nothing to see here", ""]:
        result = tool.call({"text": text})
        assert json.loads(json.dumps(result)) == result
        assert set(result) == {"text", "today", "settings", "matches", "assumptions",
                               "diagnostics", "version"}


def test_an_open_end_has_no_last_day():
    (m,) = answer("w.e.f. 1 April 2024")["matches"]
    assert (m["first_day"], m["last_day"]) == ("2024-04-01", None)


def test_the_settings_used_are_echoed():
    result = answer("Q1", fiscal_start_month=7, week_starts_on="sunday",
                    holidays=["2026-12-25", "2026-01-01", "2026-12-25"])
    assert result["settings"] == {
        "fiscal_start_month": 7, "fiscal_year_label": "end_year", "year_basis": "calendar",
        "date_order": "DMY", "month_number": "day", "two_digit_pivot": 68,
        "week_starts_on": "sunday", "anchor": "anchored", "weekend": ["saturday", "sunday"],
        "holidays": ["2026-01-01", "2026-12-25"], "timezone": None,
    }


def test_settings_change_the_answer():
    (m,) = answer("Q3 FY24", fiscal_start_month=7)["matches"]
    assert (m["first_day"], m["last_day"]) == ("2024-01-01", "2024-03-31")
    (m,) = answer("Q1 2024", year_basis="fiscal")["matches"]
    assert m["first_day"] == "2023-04-01"
    (m,) = answer("03/04/2024", date_order="MDY")["matches"]
    assert m["first_day"] == "2024-03-04"


def test_notes_say_why_confidence_is_low():
    (m,) = answer("Q1")["matches"]
    assert m["confidence"] < 1
    assert any("year is not written" in n for n in m["notes"])
    (m,) = answer("next week at 3pm")["matches"]
    assert m["confidence"] <= 0.5
    assert m["notes"], "the dropped time of day is explained"


def test_dates_that_count_from_today_are_marked():
    first, second = answer("last month vs March 2024")["matches"]
    assert first["relative_to_today"] is True
    assert second["relative_to_today"] is False


def test_min_confidence_drops_flagged_matches_but_keeps_diagnostics():
    full = answer("next week at 3pm")
    kept = tool.resolve_dates("next week at 3pm", today=TODAY, min_confidence=0.9)
    assert full["matches"] and not kept["matches"]
    assert kept["diagnostics"] == full["diagnostics"]


# ---------------------------------------------------------------------------
# Assumptions: a default reported only when it changed an answer
# ---------------------------------------------------------------------------


def test_a_fiscal_phrase_reports_the_fiscal_calendar_it_assumed():
    found = assumed(answer("Q3 FY24"))
    assert set(found) == {"fiscal_start_month", "fiscal_year_label"}
    assert found["fiscal_start_month"]["assumed"] == 4
    assert found["fiscal_start_month"]["matches"] == [{"text": "Q3 FY24", "span": [0, 7]}]
    (other,) = found["fiscal_year_label"]["matches"][0]["otherwise"]
    assert other == {"value": "start_year", "reads_as": "October 2024 to December 2024",
                     "first_day": "2024-10-01", "last_day": "2024-12-31"}


def test_settings_that_were_given_are_not_assumptions():
    result = answer("Q3 FY24", fiscal_start_month=4, fiscal_year_label="end_year")
    assert result["assumptions"] == []


def test_an_ambiguous_numeric_date_reports_the_order_it_assumed():
    found = assumed(answer("03/04/2024 and 13/04/2024"))
    (entry,) = found["date_order"]["matches"]
    assert entry["text"] == "03/04/2024", "13/04/2024 has only the one reading"
    (other,) = entry["otherwise"]
    assert (other["value"], other["first_day"]) == ("MDY", "2024-03-04")
    assert assumed(answer("03/04/2024", date_order="DMY")) == {}


@pytest.mark.parametrize(
    "text, setting, value, first_day",
    [
        ("last week", "week_starts_on", "sunday", "2026-11-08"),
        ("last month", "anchor", "rolling", "2026-10-16"),
        ("jan 24", "month_number", "year", "2024-01-01"),
        ("2024", "year_basis", "fiscal", "2023-04-01"),
    ],
)
def test_each_default_that_changed_a_date_says_what_it_would_be_otherwise(
    text, setting, value, first_day
):
    found = assumed(answer(text))
    (entry,) = found[setting]["matches"]
    readings = {o["value"]: o["first_day"] for o in entry["otherwise"]}
    assert readings[value] == first_day
    assert found[setting]["note"]
    assert setting not in assumed(answer(text, **{setting: value}))


def test_a_date_written_in_full_assumes_nothing():
    assert tool.call({"text": "15 March 2024"})["assumptions"] == []
    assert tool.call({"text": "from 1 April 2024 to 31 March 2025"})["assumptions"] == []


def test_today_is_an_assumption_only_when_left_out():
    before = date.today()
    left_out = assumed(tool.call({"text": "since March"}))
    after = date.today()  # read either side, so a run across midnight still passes
    assert left_out["today"]["matches"] == [{"text": "since March", "span": [0, 11]}]
    assert left_out["today"]["assumed"] in {before.isoformat(), after.isoformat()}
    assert "the clock where this runs" in left_out["today"]["note"]
    zoned = assumed(tool.call({"text": "since March", "timezone": "Asia/Kolkata"}))
    assert "the clock in Asia/Kolkata" in zoned["today"]["note"]
    given = answer("since March")
    assert "today" not in assumed(given)
    assert given["matches"][0]["relative_to_today"] is True


def test_today_left_out_follows_the_timezone():
    zone = ZoneInfo("Pacific/Kiritimati")  # UTC+14: a different date from most of the world
    before = datetime.now(zone).date()
    result = tool.call({"text": "today", "timezone": "Pacific/Kiritimati"})
    after = datetime.now(zone).date()
    assert result["today"] in {before.isoformat(), after.isoformat()}
    assert result["settings"]["timezone"] == "Pacific/Kiritimati"


@pytest.mark.parametrize(
    "text",
    [
        # Each moves with the start month, but not with every one: tried against January
        # alone, the first three were missed, and the last three are calendar days or a
        # calendar span whose year the fiscal calendar chose.
        "June of this fiscal year",
        "October of this fiscal year",  # moves only for a November start
        "same quarter last FY",
        "from April 2023 to the end of FY24",
        "15 March to FY25",
        "15-20 June FY25",
        # A day picked out of a fiscal year comes back as a calendar day.
        "the last day of FY2024",
        "the first day of Q3 FY24",
        # Quarters repeat every three months, so an April, July, October and January start
        # give the same quarters: only a start in another month of the three moves these.
        "this fiscal quarter",
        "fiscal QTD",
        # Written the short way round, and read as the long way is.
        "fiscal week 3 FY25",
        "month 3 of FY24",
        "fiscal year end",
        "FYE 2024",
    ],
)
def test_a_date_the_fiscal_calendar_reaches_reports_the_start_month(text):
    assert "fiscal_start_month" in assumed(answer(text))
    assert "fiscal_start_month" not in assumed(answer(text, fiscal_start_month=4))


@pytest.mark.parametrize(
    "text", ["03/04/2024", "03–04–2024", "03—04—2024", "03／04／2024", "03−04−2024"]
)
def test_an_ambiguous_numeric_date_is_reported_whatever_its_separators(text):
    """The parser folds en dashes, minus signs and fullwidth slashes -- what a pasted
    spreadsheet date often has -- so the date order has to be reported for them too."""
    (entry,) = assumed(answer(text))["date_order"]["matches"]
    assert {o["value"]: o["first_day"] for o in entry["otherwise"]}["MDY"] == "2024-03-04"


def test_a_two_digit_year_reports_the_century_it_assumed():
    """Only where the conventions in use disagree, '30 to '68: Windows and Excel read those
    in the 1900s. "FY24" and "'85" read the same either way, and are not offered as 1924
    or 2085."""
    found = assumed(answer("12/31/68, Q1 '45, Q3 FY24 and Q1 '85", date_order="MDY"))
    entries = {e["text"]: e for e in found["two_digit_pivot"]["matches"]}
    assert set(entries) == {"12/31/68", "Q1 '45"}
    assert [o["first_day"] for o in entries["12/31/68"]["otherwise"]] == ["1968-12-31"]
    assert [o["first_day"] for o in entries["Q1 '45"]["otherwise"]] == ["1945-01-01"]
    assert "1969 to 2068" in found["two_digit_pivot"]["note"]
    (m,) = answer("12/31/68", date_order="MDY", two_digit_pivot=70)["matches"]
    assert m["first_day"] == "2068-12-31"
    (m,) = answer("12/31/68", date_order="MDY", two_digit_pivot=50)["matches"]
    assert m["first_day"] == "1968-12-31"
    assert "two_digit_pivot" not in assumed(answer("12/31/1968", date_order="MDY"))


@pytest.mark.parametrize("text", ["last quarter", "this quarter", "QTD"])
def test_year_basis_is_tried_with_every_start_month_when_both_are_left_out(text):
    """On an April year, fiscal quarters are the calendar's, so the fiscal reading under
    the default start month is the same -- and on a February year it is not. With both
    left out, the answer depends on both, and the tool says so."""
    entry = assumed(answer(text))["year_basis"]
    (match,) = entry["matches"]
    assert "otherwise" not in match, "no single other reading: it takes two settings"
    assert "fiscal_start_month" in entry["note"]
    # Given the start month, year_basis is tried at it alone: no difference on April...
    assert "year_basis" not in assumed(answer(text, fiscal_start_month=4))
    # ...and on February the fiscal reading is there to give.
    (match,) = assumed(answer(text, fiscal_start_month=2))["year_basis"]["matches"]
    assert match["otherwise"][0]["value"] == "fiscal"


def test_year_basis_is_not_blamed_for_what_the_start_month_moves():
    """"FY24 Q1" says fiscal: it moves with the start month, which is reported, and not
    with year_basis -- tried together, the two are compared at the same start month."""
    found = assumed(answer("FY24 Q1"))
    assert "fiscal_start_month" in found and "year_basis" not in found
    assert "year_basis" not in assumed(answer("15 March 2024 and March 2024"))


def test_a_week_start_is_tried_at_saturday_as_well_as_sunday():
    """Saturday is the first day of the week in parts of the Middle East, and "Saturday last
    week" moves for it alone."""
    (entry,) = assumed(answer("Saturday last week"))["week_starts_on"]["matches"]
    assert [(o["value"], o["first_day"]) for o in entry["otherwise"]] == [
        ("saturday", "2026-11-07")
    ]


def test_business_days_report_the_weekend_and_holidays_they_assumed():
    found = assumed(answer("5 business days ago"))
    assert found["weekend"]["assumed"] == ["saturday", "sunday"]
    assert found["holidays"]["assumed"] == []
    for setting in ("weekend", "holidays"):
        assert [m["text"] for m in found[setting]["matches"]] == ["5 business days ago"]
    # A Friday-Saturday weekend lands on the same day here, so there is no other reading
    # to give -- but the answer still depends on the weekend, and says so.
    assert "otherwise" not in found["weekend"]["matches"][0]


def test_a_weekend_reading_is_given_where_it_differs():
    found = assumed(answer("5 business days ago", holidays=["2026-11-13"]))
    assert "holidays" not in found
    (other,) = found["weekend"]["matches"][0]["otherwise"]
    assert other["value"] == ["friday", "saturday"] and other["first_day"] == "2026-11-09"


def test_business_settings_given_are_not_assumptions():
    assert set(assumed(answer("5 business days ago", weekend=["sat", "sun"]))) == {"holidays"}
    assert assumed(answer("5 business days ago", weekend=["sat", "sun"], holidays=[])) == {}


@pytest.mark.parametrize(
    "text",
    ["next business day", "the last working day of the month",
     "first business day of next month", "in 3 working days", "last 10 business days"],
)
def test_every_business_day_phrase_is_found(text):
    assert {"weekend", "holidays"} <= set(assumed(answer(text)))


def test_dates_not_counted_in_business_days_assume_no_weekend():
    for text in ["last week", "15 March 2024", "Q3 FY24", "the last day of the month"]:
        found = assumed(answer(text))
        assert "weekend" not in found and "holidays" not in found, text


#: Phrases where a shortcut could go wrong: each kind of setting, mixed with dates it
#: cannot reach, in spans, lists and comparisons.
_HARD = [
    "Q3 FY24 and 15 March 2024", "June of this fiscal year", "15-20 June FY25",
    "from April 2023 to the end of FY24", "15 March to FY25", "15 June 2024-25",
    "03/04/2024 vs 13/04/2024", "March and 24/03/04", "jan, feb 24", "jan 24 to mar 24",
    "last week vs Saturday last week", "last month and 15 March 2024", "since March",
    "5 business days ago and last week", "Q1-Q3 FY24 vs Q1-Q3 FY23", "YTD vs FYTD",
    "the last month of the fiscal year", "H1 2025 vs H1 2024", "w.e.f. 1 April 2024",
    "03–04–2024 and 03／04／2024", "12/31/68 vs Q1 '85", "the last day of FY2024",
    "this fiscal quarter and fiscal QTD", "this Friday and Monday through Friday",
    "fiscal week 3 and month 3 of FY24", "FYE 2024 vs fiscal year end", "week 3 of jan",
]


@pytest.mark.parametrize("given", [{}, {"year_basis": "fiscal"}, {"date_order": "MDY"}])
def test_the_shortcuts_change_no_answer(monkeypatch, given):
    """``_reach`` and the early stop only skip work. Trying every setting on every match
    must give exactly the same answer. (Checked once over the whole format catalogue and
    snapshot, under three days and three settings; these are the cases that broke.)"""
    fast = [answer(text, **given) for text in _HARD]
    monkeypatch.setattr(tool, "_reach", lambda reach, found, relative: found)
    assert [answer(text, **given) for text in _HARD] == fast


def test_every_answer_matches_the_output_schema():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(
        tool.OUTPUT_SCHEMA, format_checker=jsonschema.FormatChecker()
    )
    validator.check_schema(tool.OUTPUT_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(tool.INPUT_SCHEMA)
    for text in [*_HARD, "nothing", "", "next week at 3pm", "before 2024", "Q3 2024-25"]:
        validator.validate(tool.call({"text": text}))
        validator.validate(answer(text, weekend=["fri", "sat"], holidays=["2026-12-25"]))


def test_the_module_docstring_example_runs():
    import doctest

    assert doctest.testmod(tool).failed == 0


def test_the_readme_example_answer_is_what_the_tool_gives():
    """The README shows the answer for "Q3 FY24", shortened: every key and value it shows
    must be in the real answer. "..." stands for what was left out."""
    readme = (Path(__file__).resolve().parent.parent / "README.md")
    if not readme.is_file():
        pytest.skip("README.md is not beside the tests")
    block = re.search(r"`Q3 FY24`, with nothing but today given.*?```json\n(.*?)\n```",
                      readme.read_text(encoding="utf-8"), re.S)
    assert block, "the README's example answer is gone"

    def holds(shown: object, real: object, where: str) -> None:
        if isinstance(shown, dict):
            assert isinstance(real, dict), where
            for key, value in shown.items():
                if key != "...":
                    assert key in real, f"{where}.{key}"
                    holds(value, real[key], f"{where}.{key}")
        elif isinstance(shown, list):
            assert isinstance(real, list) and len(shown) == len(real), where
            for i, (a, b) in enumerate(zip(shown, real, strict=True)):
                holds(a, b, f"{where}[{i}]")
        elif isinstance(shown, str) and shown.endswith("..."):
            assert isinstance(real, str) and real.startswith(shown[:-3].rstrip()), where
        else:
            assert shown == real, where

    holds(json.loads(block.group(1)), answer("Q3 FY24"), "answer")


# ---------------------------------------------------------------------------
# Arguments as a model sends them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments, says",
    [
        ({"text": "Q1", "fiscal_start": 4}, "unknown argument 'fiscal_start'"),
        ({"today": TODAY}, "text is required"),
        ({"text": None}, "text is required"),
        ({"text": 2024}, "text must be a string"),
        ({"text": "Q1", "fiscal_start_month": 13}, "1-12"),
        ({"text": "Q1", "fiscal_start_month": True}, "1-12"),
        ({"text": "Q1", "fiscal_start_month": "April"}, "1-12"),
        ({"text": "Q1", "date_order": "DYM"}, "'DMY', 'MDY', 'YMD'"),
        ({"text": "Q1", "fiscal_start_month": 7.5}, "1-12"),
        # A digit to str.isdigit, and no number to int(): refused, never a crash.
        ({"text": "Q1", "fiscal_start_month": "¹"}, "1-12"),
        ({"text": "Q1", "week_starts_on": "²"}, "week_starts_on"),
        ({"text": "Q1", "two_digit_pivot": 100}, "0-99"),
        ({"text": "Q1", "two_digit_pivot": "sixty"}, "0-99"),
        ({"text": "Q1", "week_starts_on": 7}, "week_starts_on"),
        ({"text": "Q1", "week_starts_on": "su"}, "week_starts_on"),
        ({"text": "Q1", "today": "16/11/2026"}, "YYYY-MM-DD"),
        # Taken by date.fromisoformat from Python 3.11, and not by 3.10: refused on all.
        ({"text": "Q1", "today": "20261116"}, "YYYY-MM-DD"),
        ({"text": "Q1", "today": "2026-W47-1"}, "YYYY-MM-DD"),
        ({"text": "Q1", "today": "2026-02-30"}, "not a date in the calendar"),
        ({"text": "Q1", "timezone": "IST"}, "IANA"),
        # A directory of the zone database: PermissionError on Windows, not a zone anywhere.
        ({"text": "Q1", "timezone": "America"}, "IANA"),
        ({"text": "Q1", "timezone": ""}, "IANA"),
        ({"text": "Q1", "weekend": ["saturday", "funday"]}, "each day of weekend"),
        ({"text": "Q1", "weekend": list(tool._WEEKDAYS)}, "at least one working day"),
        ({"text": "Q1", "weekend": {"saturday": True}}, "weekend must be a list"),
        ({"text": "Q1", "holidays": ["2026-12-25", "25/12/2026"]}, "each holiday"),
        ({"text": "Q1", "holidays": 20261225}, "holidays must be a list"),
    ],
)
def test_bad_arguments_come_back_as_a_message_the_model_can_act_on(arguments, says):
    with pytest.raises(tool.ToolInputError, match=says):
        tool.call(arguments)


def test_arguments_must_be_an_object():
    with pytest.raises(tool.ToolInputError, match="object"):
        tool.call(["Q1"])  # type: ignore[arg-type]


def test_a_tool_input_error_is_a_value_error():
    assert issubclass(tool.ToolInputError, ValueError)


def test_a_timezone_in_the_wrong_case_is_told_its_name(monkeypatch):
    """On a case-insensitive disk "asia/kolkata" may simply load, so the lookup is made to
    fail here as it does on Linux and Windows."""

    def not_found(key: str) -> None:
        raise tool.ZoneInfoNotFoundError(key)

    monkeypatch.setattr(tool, "ZoneInfo", not_found)
    with pytest.raises(tool.ToolInputError, match="it is 'Asia/Kolkata'"):
        tool.call({"text": "Q1", "timezone": "asia/kolkata"})


def test_near_misses_a_model_sends_are_read_as_meant():
    text = "last week Q3 FY24 03/04/2024 5 business days ago"
    reference = answer(text, fiscal_start_month=7, date_order="MDY", week_starts_on="sunday",
                       weekend=["friday", "saturday"], holidays=["2026-11-12"])
    loose = answer(text, fiscal_start_month="7", date_order="mdy", week_starts_on="Sun",
                   weekend="fri, SAT", holidays="2026-11-12")
    numbered = answer(text, fiscal_start_month=7.0, date_order="MDY", week_starts_on=6,
                      weekend=[4, "5"], holidays=[date(2026, 11, 12), "2026-11-12"])
    assert loose == reference == numbered


def test_an_empty_weekend_is_a_weekend_of_no_days():
    result = answer("5 business days ago", weekend=[], holidays=[])
    assert result["settings"]["weekend"] == [] and result["assumptions"] == []
    assert result["matches"][0]["first_day"] == "2026-11-11"


def test_a_today_at_the_end_of_the_calendar_still_answers():
    """Which dates count from today is found by looking a year on; at 9999 that is past
    the last date Python has, so it looks a year back instead."""
    (m,) = tool.call({"text": "last month", "today": "9999-12-01"})["matches"]
    assert m["first_day"] == "9999-11-01" and m["relative_to_today"] is True


def test_null_means_left_out():
    assert tool.call({"text": "Q3 FY24", "today": TODAY, "fiscal_start_month": None}) == answer(
        "Q3 FY24"
    )


def test_python_callers_may_pass_dates_and_enums():
    result = tool.resolve_dates(
        "Q3 FY24 03/04/2024", today=date(2026, 11, 16), fiscal_year_label=YearLabel.START_YEAR,
        date_order=DateOrder.MDY,
    )
    assert result["settings"]["fiscal_year_label"] == "start_year"
    assert result["matches"][1]["first_day"] == "2024-03-04"
    with pytest.raises(tool.ToolInputError, match="strictness"):
        tool.resolve_dates("Q1", strictness="loose")


# ---------------------------------------------------------------------------
# The CLI: --json is the tool's answer
# ---------------------------------------------------------------------------


def test_cli_json_is_the_tool_answer(capsys):
    assert main(["--json", "--today", TODAY, "--fiscal-start", "7", "Q3", "FY24"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == tool.call({"text": "Q3 FY24", "today": TODAY, "fiscal_start_month": 7})


def test_cli_flags_map_onto_the_tool_settings(capsys):
    argv = ["--json", "--today", TODAY, "--label-by", "start_year", "--basis", "fiscal",
            "--date-order", "MDY", "--month-number", "year", "--week-starts-on", "sunday",
            "--anchor", "rolling", "--tz", "Asia/Kolkata", "--weekend", "fri,sat",
            "--holidays", "2026-12-25,2026-11-20", "--two-digit-pivot", "30",
            "last week, Q1 and 03/04/2024"]
    assert main(argv) == 0
    settings = json.loads(capsys.readouterr().out)["settings"]
    assert settings == {
        "fiscal_start_month": 4, "fiscal_year_label": "start_year", "year_basis": "fiscal",
        "date_order": "MDY", "month_number": "year", "two_digit_pivot": 30,
        "week_starts_on": "sunday", "anchor": "rolling", "weekend": ["friday", "saturday"],
        "holidays": ["2026-11-20", "2026-12-25"], "timezone": "Asia/Kolkata",
    }


def test_cli_today_takes_an_unpadded_month_and_day(capsys):
    """As --today always has: it was read with strptime's %m and %d, which take one digit."""
    assert main(["--json", "--today", "2026-1-5", "today"]) == 0
    assert json.loads(capsys.readouterr().out)["today"] == "2026-01-05"


def test_cli_table_shows_the_reading_and_reports_assumptions(capsys):
    assert main(["--today", TODAY, "03/04/2024"]) == 0
    out, err = capsys.readouterr()
    assert "3 April 2024" in out and "SQL: d >= '2024-04-03' AND d < '2024-04-04'" in out
    assert "assumed date_order=DMY" in err


def test_cli_table_leaves_today_out_of_the_assumptions(capsys):
    assert main(["since March"]) == 0
    assert "assumed" not in capsys.readouterr().err


def test_cli_table_shows_list_settings_as_the_flag_takes_them(capsys):
    assert main(["--today", TODAY, "5 business days ago"]) == 0
    err = capsys.readouterr().err
    assert "assumed weekend=saturday,sunday:" in err
    assert "assumed holidays=none:" in err


@pytest.mark.parametrize(
    "argv, says",
    [
        (["--today", "16/11/2026", "Q1"], "YYYY-MM-DD"),
        (["--tz", "Not/AZone", "Q1"], "IANA"),
        (["--week-starts-on", "funday", "Q1"], "week_starts_on"),
    ],
)
def test_cli_bad_settings_exit_2_with_the_reason(capsys, argv, says):
    assert main(argv) == 2
    assert says in capsys.readouterr().err


def test_cli_with_nothing_found_exits_1(capsys):
    assert main(["no", "dates", "here"]) == 1
    assert "no dates found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The MCP command without its extra
# ---------------------------------------------------------------------------


def _load_mcp_server_failing_with(monkeypatch, error: ImportError):
    """The server module as it loads when importing the SDK raises ``error``."""
    import builtins
    import importlib

    import date_wrangler.mcp_server as mcp_server

    real_import = builtins.__import__

    def failing(name, *args, **kwargs):
        if name == "anyio" or name.startswith("mcp"):
            raise error
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing)
    try:
        return importlib.reload(mcp_server)
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)


@pytest.fixture
def reload_mcp_server_after():
    yield
    import importlib

    import date_wrangler.mcp_server as mcp_server

    importlib.reload(mcp_server)


def test_the_mcp_command_without_the_sdk_says_how_to_install_it(
    monkeypatch, capsys, reload_mcp_server_after
):
    server = _load_mcp_server_failing_with(
        monkeypatch, ModuleNotFoundError("No module named 'mcp'", name="mcp")
    )
    assert server.main() == 1
    assert 'pip install "date-wrangler[mcp]"' in capsys.readouterr().err
    with pytest.raises(ImportError, match="date-wrangler\\[mcp\\]"):
        server.build_server()


def test_a_broken_sdk_is_reported_as_broken_not_as_missing(
    monkeypatch, capsys, reload_mcp_server_after
):
    """With the extra installed but a dependency of it missing, "install the extra" would
    send someone round in a circle: the message names what actually failed."""
    server = _load_mcp_server_failing_with(
        monkeypatch, ModuleNotFoundError("No module named 'pywintypes'", name="pywintypes")
    )
    assert server.main() == 1
    err = capsys.readouterr().err
    assert "could not load the MCP SDK" in err and "pywintypes" in err
    assert "pip install" not in err
