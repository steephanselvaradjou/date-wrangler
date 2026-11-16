"""The agent tool: its schema, the answer it gives, and the assumptions it owns up to.

What a model is handed and what it gets back are a contract with code nobody here can
see -- prompts, agent frameworks, MCP clients -- so the shape is pinned as well as the
dates. The CLI's ``--json`` is the same answer, and is held to it here too.
"""

from __future__ import annotations

import inspect
import json
from datetime import date, datetime
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


def test_the_schema_and_the_function_take_the_same_arguments():
    """A property the function does not take would fail every call that used it, and a
    parameter the schema leaves out could never be set by a model."""
    params = set(inspect.signature(tool.resolve_dates).parameters)
    python_only = {"strictness", "min_confidence"}
    assert params - python_only == set(tool.INPUT_SCHEMA["properties"])


def test_the_assumption_notes_describe_the_library_defaults():
    """The notes are written for these defaults ("read day first", "start on Monday"); if
    a default moves, the note has to move with it."""
    defaults = {s.name: s.default for s in tool._SETTINGS}
    assert defaults == {
        "fiscal_start_month": DEFAULT_CONFIG.fiscal.start_month,
        "fiscal_year_label": DEFAULT_CONFIG.fiscal.label_by.value,
        "year_basis": DEFAULT_CONFIG.effective_year_basis.value,
        "date_order": DEFAULT_CONFIG.date_order.value,
        "month_number": DEFAULT_CONFIG.month_number.value,
        "week_starts_on": "monday",
        "anchor": DEFAULT_CONFIG.anchor.value,
    }
    assert DEFAULT_CONFIG.week_starts_on == 0
    assert defaults == {
        "fiscal_start_month": 4, "fiscal_year_label": "end_year", "year_basis": "calendar",
        "date_order": "DMY", "month_number": "day", "week_starts_on": "monday",
        "anchor": "anchored",
    }


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
    result = answer("Q1", fiscal_start_month=7, week_starts_on="sunday")
    assert result["settings"] == {
        "fiscal_start_month": 7, "fiscal_year_label": "end_year", "year_basis": "calendar",
        "date_order": "DMY", "month_number": "day", "week_starts_on": "sunday",
        "anchor": "anchored", "timezone": None,
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
    (other,) = entry["otherwise"]
    assert (other["value"], other["first_day"]) == (value, first_day)
    assert found[setting]["note"]
    assert setting not in assumed(answer(text, **{setting: value}))


def test_a_date_written_in_full_assumes_nothing():
    assert tool.call({"text": "15 March 2024"})["assumptions"] == []
    assert tool.call({"text": "from 1 April 2024 to 31 March 2025"})["assumptions"] == []


def test_today_is_an_assumption_only_when_left_out():
    left_out = assumed(tool.call({"text": "since March"}))
    assert left_out["today"]["matches"] == [{"text": "since March", "span": [0, 11]}]
    assert left_out["today"]["assumed"] == tool.call({"text": "x"})["today"]
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
        ({"text": "Q1", "week_starts_on": 7}, "week_starts_on"),
        ({"text": "Q1", "today": "16/11/2026"}, "YYYY-MM-DD"),
        ({"text": "Q1", "timezone": "IST"}, "IANA"),
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


def test_near_misses_a_model_sends_are_read_as_meant():
    reference = answer("last week Q3 FY24 03/04/2024", fiscal_start_month=7, date_order="MDY",
                       week_starts_on="sunday")
    loose = answer("last week Q3 FY24 03/04/2024", fiscal_start_month="7", date_order="mdy",
                   week_starts_on="Sunday")
    numbered = answer("last week Q3 FY24 03/04/2024", fiscal_start_month=7, date_order="MDY",
                      week_starts_on=6)
    assert loose == reference == numbered


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
            "--anchor", "rolling", "--tz", "Asia/Kolkata", "last week, Q1 and 03/04/2024"]
    assert main(argv) == 0
    settings = json.loads(capsys.readouterr().out)["settings"]
    assert settings == {
        "fiscal_start_month": 4, "fiscal_year_label": "start_year", "year_basis": "fiscal",
        "date_order": "MDY", "month_number": "year", "week_starts_on": "sunday",
        "anchor": "rolling", "timezone": "Asia/Kolkata",
    }


def test_cli_table_shows_the_reading_and_reports_assumptions(capsys):
    assert main(["--today", TODAY, "03/04/2024"]) == 0
    out, err = capsys.readouterr()
    assert "3 April 2024" in out and "SQL: d >= '2024-04-03' AND d < '2024-04-04'" in out
    assert "assumed date_order=DMY" in err


def test_cli_table_leaves_today_out_of_the_assumptions(capsys):
    assert main(["since March"]) == 0
    assert "assumed" not in capsys.readouterr().err


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


def test_the_mcp_command_without_the_sdk_says_how_to_install_it(monkeypatch, capsys):
    from date_wrangler import mcp_server

    monkeypatch.setattr(mcp_server, "_SDK", False)
    assert mcp_server.main() == 1
    assert 'pip install "date-wrangler[mcp]"' in capsys.readouterr().err
    with pytest.raises(ImportError, match="date-wrangler\\[mcp\\]"):
        mcp_server.build_server()
