"""``date-wrangler-mcp``: the tool over MCP, for Claude Desktop, Claude Code, Cursor and any
other MCP client.

A transport and nothing more. It lists the one tool from :mod:`date_wrangler.tool` and
hands every call there: the dates, the assumptions and the error messages are all worked
out in the library, so this server and a direct tool call cannot disagree.

Needs the optional MCP SDK, which is too large to impose on everyone who only parses
dates: ``pip install "date-wrangler[mcp]"``. Without it the command says so and exits.
"""

from __future__ import annotations

import copy
import json
import sys
from typing import Any

from . import __version__, tool

try:
    import anyio
    import mcp.types as types
    from mcp.server import Server, ServerRequestContext
    from mcp.server.stdio import stdio_server
    from mcp.shared.exceptions import MCPError
except ImportError as exc:
    _SDK = False
    # Not installed, or installed and broken (a missing pywintypes, say): telling someone
    # with the extra installed to install it hides the real fault, so the two differ.
    if exc.name in ("mcp", "anyio"):
        _INSTALL = 'date-wrangler-mcp needs the MCP SDK: pip install "date-wrangler[mcp]"'
    else:
        _INSTALL = f"date-wrangler-mcp could not load the MCP SDK: {exc}"
else:
    _SDK = True
    _INSTALL = ""

_INSTRUCTIONS = (
    f"One tool, {tool.NAME}: call it to turn dates written in words into exact first and "
    "last days instead of working them out. Pass today and the organisation's fiscal "
    "year, weekend and holidays when they are known, and tell the user about any "
    "assumptions it reports."
)


def build_server() -> Server[Any]:
    """The server, unstarted: ``date-wrangler-mcp`` runs it over stdio, and tests connect
    to it in-process."""
    if not _SDK:
        raise ImportError(_INSTALL)
    return Server(
        "date-wrangler",
        version=__version__,
        title="date-wrangler",
        description="Resolves dates written in words to exact ranges.",
        instructions=_INSTRUCTIONS,
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


async def _list_tools(
    ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    spec = tool.definition()
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name=spec["name"],
                title="Resolve dates",
                description=spec["description"],
                input_schema=spec["input_schema"],
                output_schema=copy.deepcopy(tool.OUTPUT_SCHEMA),
                # It reads text and computes; nothing outside the call changes.
                annotations=types.ToolAnnotations(
                    read_only_hint=True,
                    destructive_hint=False,
                    idempotent_hint=True,
                    open_world_hint=False,
                ),
            )
        ]
    )


async def _call_tool(
    ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
) -> types.CallToolResult:
    if params.name != tool.NAME:
        # A protocol error, as MCP asks for a tool the server does not have: the client
        # asked for something that is not there, and no model can fix that by retrying.
        raise MCPError(
            code=types.INVALID_PARAMS,
            message=f"unknown tool {params.name!r}; this server has one, {tool.NAME!r}",
        )
    try:
        answer = tool.call(params.arguments or {})
    except tool.ToolInputError as exc:
        # An error result, not a protocol error, as MCP asks for arguments that fail
        # validation: the model reads it and fixes the call.
        return _error(str(exc))
    except Exception as exc:  # a fault in the library, not in the call
        # Still a result, as MCP asks of a tool that fails while running, so the model can
        # say what happened; the server carries on for the next call.
        return _error(f"date-wrangler failed on this input: {type(exc).__name__}: {exc}")
    # The answer twice, as MCP asks of structured results: as data for clients that read
    # structured content, and as its JSON text for those that only show text -- compact,
    # since it is a copy and a long text's answer is large.
    return types.CallToolResult(
        content=[types.TextContent(text=json.dumps(answer, separators=(",", ":")))],
        structured_content=answer,
    )


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(text=message)], is_error=True)


async def _serve() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> int:
    if not _SDK:
        print(_INSTALL, file=sys.stderr)
        return 1
    try:
        anyio.run(_serve)
    except KeyboardInterrupt:  # Ctrl+C when run by hand at a terminal: no traceback
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
