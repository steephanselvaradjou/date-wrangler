"""The MCP server, listed and called the way an MCP client does it.

Skipped without the optional SDK (``pip install "date-wrangler[mcp]"``). What the server
must show is that it adds nothing: the tool it lists is the library's definition, and a
call returns exactly what :func:`date_wrangler.tool.call` returns.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")

import anyio  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

import date_wrangler  # noqa: E402
from date_wrangler import tool  # noqa: E402
from date_wrangler.mcp_server import build_server  # noqa: E402

TODAY = "2026-11-16"


def in_process(work: Any) -> Any:
    async def run() -> Any:
        async with Client(build_server()) as client:
            return await work(client)

    return anyio.run(run)


def test_the_server_lists_the_library_tool_unchanged():
    listed = in_process(lambda client: client.list_tools())
    (only,) = listed.tools
    spec = tool.definition()
    assert only.name == spec["name"]
    assert only.description == spec["description"]
    assert only.input_schema == spec["input_schema"]
    assert only.annotations.read_only_hint is True
    assert only.annotations.destructive_hint is False


def test_a_call_returns_the_library_answer_as_data_and_as_text():
    arguments = {"text": "Q3 FY24 vs Q3 FY23", "today": TODAY, "fiscal_start_month": 7}
    result = in_process(lambda client: client.call_tool(tool.NAME, arguments))
    assert result.is_error is False
    expected = tool.call(arguments)
    assert result.structured_content == expected
    (text,) = result.content
    assert json.loads(text.text) == expected


def test_bad_arguments_come_back_as_a_tool_error_the_model_can_read():
    arguments = {"text": "Q1", "fiscal_start_month": 13}
    result = in_process(lambda client: client.call_tool(tool.NAME, arguments))
    assert result.is_error is True
    assert "1-12" in result.content[0].text


def test_an_unknown_tool_name_is_an_error_that_names_the_real_one():
    result = in_process(lambda client: client.call_tool("parse_dates", {"text": "Q1"}))
    assert result.is_error is True
    assert tool.NAME in result.content[0].text


def test_the_command_serves_over_stdio():
    """The real entry point, as a client starts it: a subprocess spoken to over stdin and
    stdout. Anything the server printed to stdout would corrupt the stream and fail here."""
    src = Path(date_wrangler.__file__).resolve().parent.parent
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "date_wrangler.mcp_server"],
        env={"PYTHONPATH": str(src)},
    )

    async def run() -> Any:
        async with Client(params) as client:
            listed = await client.list_tools()
            called = await client.call_tool(tool.NAME, {"text": "since March", "today": TODAY})
            return listed, called

    listed, called = anyio.run(run)
    assert [t.name for t in listed.tools] == [tool.NAME]
    assert called.structured_content == tool.call({"text": "since March", "today": TODAY})
