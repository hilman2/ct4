"""An MCP server over stdio.

With it an agent gets the same check run as the CI, without guessing
commands. The tools are the same as on the command line; here they are
only addressed differently.

What is spoken is JSON-RPC 2.0, one message per line. That covers the
part of the protocol this is about: ``initialize``, ``tools/list`` and
``tools/call``. A library for it would be a dependency for two hundred
lines that do not change.

    ct4 mcp
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from ct4 import analyze, diagnostics, reference
from ct4.check import check_source
from ct4.cli import load_declarations

PROTOCOL = "2024-11-05"

# JSON-RPC has fixed numbers for the cases that can go wrong.
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

TOOLS: list[dict[str, Any]] = [
    {
        "name": "check",
        "description": "Checks a template: syntax, unknown names and the "
                       "schema. Does not need the application.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string",
                           "description": "the text of the template"},
                "path": {"type": "string",
                         "description": "a file path instead"},
            },
        },
    },
    {
        "name": "context",
        "description": "What a template reads from its context, with "
                       "line and column.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "path": {"type": "string"},
            },
        },
    },
    {
        "name": "reference",
        "description": "All directives, compiler settings and the "
                       "directives of JSON mode.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "declare",
        "description": "Which names the declared applications know.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "render_json",
        "description": "Renders a template in JSON mode against a "
                       "context taken from JSON.",
        "inputSchema": {
            "type": "object",
            "required": ["source", "context"],
            "properties": {
                "source": {"type": "string"},
                "context": {"type": "object",
                            "description": "used as the only namespace "
                                           "of the searchList"},
            },
        },
    },
]


def _source_of(arguments: dict[str, Any]) -> tuple[str, str]:
    """Text and name of the template, from ``source`` or ``path``."""
    if arguments.get("source") is not None:
        return arguments["source"], arguments.get("path", "<template>")
    if arguments.get("path"):
        path = Path(arguments["path"])
        return path.read_text(encoding="utf-8"), str(path)
    raise ValueError("neither source nor path was given")


def tool_check(arguments: dict[str, Any]) -> Any:
    source, name = _source_of(arguments)
    base = Path(name).parent if arguments.get("path") else None
    found = check_source(source, name, load_declarations([]), base_dir=base)
    return [entry.as_dict() for entry in found]


def tool_context(arguments: dict[str, Any]) -> Any:
    source, _ = _source_of(arguments)
    items = analyze.placeholders(source)
    return {
        "roots": analyze.roots(items),
        "placeholders": [{"path": item.path, "line": item.line,
                          "column": item.column} for item in items],
    }


def tool_reference(arguments: dict[str, Any]) -> Any:
    return reference.reference()


def tool_declare(arguments: dict[str, Any]) -> Any:
    return [declaration.as_dict() for declaration in load_declarations([])]


def tool_render_json(arguments: dict[str, Any]) -> Any:
    from ct4.jsonmode import render

    source, _ = _source_of(arguments)
    return json.loads(render(source, [arguments["context"]]))


HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "check": tool_check,
    "context": tool_context,
    "reference": tool_reference,
    "declare": tool_declare,
    "render_json": tool_render_json,
}


def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Runs a tool and wraps up its result.

    An error in the tool is not a protocol error: it belongs in the
    answer, so that the agent reads it instead of failing on an
    exception.
    """
    try:
        handler = HANDLERS[name]
    except KeyError:
        return _content("Unknown tool: %s" % name, error=True)
    try:
        result = handler(arguments)
    except Exception as error:                          # noqa: BLE001
        return _content("%s: %s" % (type(error).__name__, error), error=True)
    return _content(json.dumps(result, ensure_ascii=False, indent=1))


def _content(text: str, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """Answers a message. ``None`` means: no answer needed."""
    method = message.get("method")
    identifier = message.get("id")

    if method == "initialize":
        return _ok(identifier, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ct4", "version": _version()},
        })
    if method == "tools/list":
        return _ok(identifier, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        return _ok(identifier, call(params.get("name", ""),
                                    params.get("arguments") or {}))
    if identifier is None:
        # A notification, such as notifications/initialized. It gets no
        # answer, and that is not an error.
        return None
    return _fail(identifier, METHOD_NOT_FOUND, "unknown: %s" % method)


def _version() -> str:
    try:
        from Cheetah.Version import Version

        return str(Version)
    except Exception:                                   # noqa: BLE001
        return "unknown"


def _ok(identifier: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _fail(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier,
            "error": {"code": code, "message": message}}


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Reads messages until the end of the stream."""
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        answer = handle(message)
        if answer is None:
            continue
        sink.write(json.dumps(answer, ensure_ascii=False) + "\n")
        sink.flush()
    return 0


__all__ = ["serve", "handle", "call", "TOOLS", "diagnostics"]
