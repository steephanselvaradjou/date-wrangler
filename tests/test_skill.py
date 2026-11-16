"""The skill in skills/date-wrangler: what it tells a model has to stay true of the code.

It holds no date logic, only instructions for running the CLI and reading its answer, so
the ways it can go wrong are all drift: a flag renamed, a key it tells the model to quote
dropped, an example that no longer runs. Each is checked here against the real thing.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from date_wrangler import tool
from date_wrangler.__main__ import _parser, main

SKILL = Path(__file__).resolve().parent.parent / "skills" / "date-wrangler" / "SKILL.md"

if not SKILL.exists():  # tests copied somewhere without the rest of the checkout
    pytest.skip("skills/ is not beside the tests", allow_module_level=True)

TEXT = SKILL.read_text(encoding="utf-8")


def frontmatter() -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", TEXT, re.S)
    assert match, "SKILL.md must open with YAML frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_the_frontmatter_is_valid_yaml():
    """Read the way skill loaders read it. A ": " inside the description, say, would turn
    the plain scalar into a mapping, and the line-by-line reading below would not see it."""
    yaml = pytest.importorskip("yaml")
    match = re.match(r"---\n(.*?)\n---\n", TEXT, re.S)
    assert match
    assert yaml.safe_load(match.group(1)) == frontmatter()


def test_the_description_is_a_safe_plain_scalar():
    """What makes a YAML plain scalar misread, checked without a YAML parser."""
    description = frontmatter()["description"]
    assert ": " not in description and " #" not in description
    assert description[0] not in "!&*[]{}|>'\"%@`#,?:-"


def test_the_frontmatter_is_what_skill_loaders_require():
    fields = frontmatter()
    assert set(fields) == {"name", "description"}
    assert fields["name"] == SKILL.parent.name
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fields["name"])
    assert not {"claude", "anthropic"} & set(fields["name"].split("-"))
    assert 0 < len(fields["description"]) <= 1024
    assert "<" not in fields["description"] and ">" not in fields["description"]


def test_every_flag_it_names_is_a_cli_flag():
    flags = {opt for action in _parser()._actions for opt in action.option_strings}
    named = set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", TEXT))
    assert named, "the skill names the flags to pass"
    assert named <= flags, sorted(named - flags)


def test_every_setting_the_tool_takes_has_its_flag_in_the_skill():
    """A model following the skill can set everything a model calling the tool can."""
    flag_for = {
        "today": "--today", "timezone": "--tz", "fiscal_start_month": "--fiscal-start",
        "fiscal_year_label": "--label-by", "year_basis": "--year-basis",
        "date_order": "--date-order", "month_number": "--month-number",
        "two_digit_pivot": "--two-digit-pivot",
        "week_starts_on": "--week-starts-on", "anchor": "--anchor",
        "weekend": "--weekend", "holidays": "--holidays",
    }
    assert set(flag_for) == set(tool.INPUT_SCHEMA["properties"]) - {"text"}
    for flag in flag_for.values():
        assert f"`{flag}" in TEXT, flag


def test_the_keys_it_tells_a_model_to_read_are_in_the_answer():
    result = tool.call({"text": "since March and 03/04/2024", "today": "2026-11-16"})
    m = result["matches"][0]
    for key in ("first_day", "last_day", "reads_as", "confidence", "notes", "relative_to_today"):
        assert f"`{key}`" in TEXT and key in m, key
    assert "range" in m and {"start", "end"} <= set(m["range"])
    for key in ("matches", "assumptions", "diagnostics"):
        assert f"`{key}`" in TEXT and key in result, key
    entry = next(a for a in result["assumptions"] if a["setting"] == "date_order")
    assert "`note`" in TEXT and "note" in entry
    assert "`otherwise`" in TEXT and "otherwise" in entry["matches"][0]


def test_its_shell_example_runs(capsys):
    block = re.search(r"## Resolve\s+```bash\n(.*?)\n```", TEXT, re.S)
    assert block
    argv = shlex.split(block.group(1))
    assert argv[0] == "date-wrangler"
    assert main(argv[1:]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [m["text"] for m in result["matches"]] == ["Q3 FY24", "the same quarter last FY"]


def test_its_python_example_runs():
    block = re.search(r"```python\n(.*?)\n```", TEXT, re.S)
    assert block
    scope: dict[str, object] = {}
    exec(block.group(1), scope)
    answer = scope["answer"]
    assert isinstance(answer, dict)
    assert answer["matches"][0]["first_day"] == "2023-10-01"
