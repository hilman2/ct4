"""Ein MCP-Server ueber stdio.

Damit bekommt ein Agent denselben Prueflauf wie die CI, ohne Kommandos zu
raten. Die Werkzeuge sind dieselben wie auf der Kommandozeile; hier
werden sie nur anders angesprochen.

Gesprochen wird JSON-RPC 2.0, eine Nachricht je Zeile. Das reicht fuer
den Teil des Protokolls, um den es geht: ``initialize``, ``tools/list``
und ``tools/call``. Eine Bibliothek dafuer waere eine Abhaengigkeit fuer
zweihundert Zeilen, die sich nicht aendern.

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

# JSON-RPC kennt feste Nummern fuer die Faelle, die schiefgehen koennen.
INVALID_PARAMS = -32602
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

TOOLS: list[dict[str, Any]] = [
    {
        "name": "check",
        "description": "Prueft eine Vorlage: Syntax, unbekannte Namen und "
                       "das Schema. Braucht die Anwendung nicht.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string",
                           "description": "der Text der Vorlage"},
                "path": {"type": "string",
                         "description": "alternativ ein Dateipfad"},
            },
        },
    },
    {
        "name": "context",
        "description": "Was eine Vorlage aus dem Kontext liest, mit Zeile "
                       "und Spalte.",
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
        "description": "Alle Direktiven, Compiler-Einstellungen und die "
                       "Direktiven des JSON-Modus.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "declare",
        "description": "Welche Namen die angemeldeten Anwendungen kennen.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "render_json",
        "description": "Rendert eine Vorlage im JSON-Modus gegen einen "
                       "Kontext aus JSON.",
        "inputSchema": {
            "type": "object",
            "required": ["source", "context"],
            "properties": {
                "source": {"type": "string"},
                "context": {"type": "object",
                            "description": "wird als einziger Namensraum "
                                           "der searchList benutzt"},
            },
        },
    },
]


def _source_of(arguments: dict[str, Any]) -> tuple[str, str]:
    """Text und Name der Vorlage, aus ``source`` oder ``path``."""
    if arguments.get("source") is not None:
        return arguments["source"], arguments.get("path", "<vorlage>")
    if arguments.get("path"):
        path = Path(arguments["path"])
        return path.read_text(encoding="utf-8"), str(path)
    raise ValueError("weder source noch path angegeben")


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
    """Fuehrt ein Werkzeug aus und verpackt sein Ergebnis.

    Ein Fehler im Werkzeug ist kein Protokollfehler: er gehoert in die
    Antwort, damit der Agent ihn liest, statt an einer Ausnahme zu
    scheitern.
    """
    try:
        handler = HANDLERS[name]
    except KeyError:
        return _content("Unbekanntes Werkzeug: %s" % name, error=True)
    try:
        result = handler(arguments)
    except Exception as error:                          # noqa: BLE001
        return _content("%s: %s" % (type(error).__name__, error), error=True)
    return _content(json.dumps(result, ensure_ascii=False, indent=1))


def _content(text: str, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """Beantwortet eine Nachricht. ``None`` heisst: keine Antwort noetig."""
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
        # Eine Benachrichtigung, etwa notifications/initialized. Sie
        # bekommt keine Antwort, und das ist kein Fehler.
        return None
    return _fail(identifier, METHOD_NOT_FOUND, "unbekannt: %s" % method)


def _version() -> str:
    try:
        from Cheetah.Version import Version

        return str(Version)
    except Exception:                                   # noqa: BLE001
        return "unbekannt"


def _ok(identifier: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _fail(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier,
            "error": {"code": code, "message": message}}


def serve(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Liest Nachrichten bis zum Ende des Stroms."""
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
