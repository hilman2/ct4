"""A language server over stdio.

With it an editor shows what ``ct4 check`` finds while the file is
being typed, and formats with ``ct4 fmt`` on request. The protocol is
JSON-RPC 2.0 with the framing the Language Server Protocol prescribes,
a ``Content-Length`` header before every message; what is spoken of
it is the part these two tools need, and a library for that would be
a dependency for a few hundred lines that do not change.

    ct4 lsp

Which files it applies to is the editor's decision: it sends what it
opens, and every document it sends is checked as a template.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, Callable

from ct4 import diagnostics
from ct4.check import check_source
from ct4.cli import load_declarations

# What LSP calls the three severities, and the sync mode in which the
# editor sends the whole text on every change. Full sync is the right
# trade: a template is small, and the check parses it whole anyway.
SEVERITY = {diagnostics.ERROR: 1, diagnostics.WARNING: 2}
FULL_SYNC = 1

METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class Server:
    """The state one editor session holds: the open documents."""

    def __init__(self, sink: BinaryIO):
        self.sink = sink
        self.documents: dict[str, str] = {}
        self.declarations = load_declarations([])
        self.stopped = False
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "initialize": self.initialize,
            "initialized": lambda params: None,
            "shutdown": lambda params: None,
            "exit": self.exit,
            "textDocument/didOpen": self.did_open,
            "textDocument/didChange": self.did_change,
            "textDocument/didSave": self.did_save,
            "textDocument/didClose": self.did_close,
            "textDocument/formatting": self.formatting,
        }

    # -- requests and notifications --------------------------------

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "capabilities": {
                "textDocumentSync": FULL_SYNC,
                "documentFormattingProvider": True,
            },
            "serverInfo": {"name": "ct4", "version": _version()},
        }

    def exit(self, params: dict[str, Any]) -> None:
        self.stopped = True

    def did_open(self, params: dict[str, Any]) -> None:
        document = params["textDocument"]
        self.documents[document["uri"]] = document["text"]
        self.publish(document["uri"])

    def did_change(self, params: dict[str, Any]) -> None:
        uri = params["textDocument"]["uri"]
        changes = params.get("contentChanges") or []
        if changes:
            # Full sync: the last change carries the whole text.
            self.documents[uri] = changes[-1]["text"]
        self.publish(uri)

    def did_save(self, params: dict[str, Any]) -> None:
        uri = params["textDocument"]["uri"]
        if "text" in params:
            self.documents[uri] = params["text"]
        self.publish(uri)

    def did_close(self, params: dict[str, Any]) -> None:
        uri = params["textDocument"]["uri"]
        self.documents.pop(uri, None)
        self.notify("textDocument/publishDiagnostics",
                    {"uri": uri, "diagnostics": []})

    def formatting(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """One edit that replaces the document, or none.

        A file that does not parse is not formatted, and the reason is
        already on screen as a diagnostic; an empty answer here keeps
        the editor quiet rather than raising twice.
        """
        from ct4 import fmt
        from ct4.lang import tree

        uri = params["textDocument"]["uri"]
        source = self.documents.get(uri, "")
        options = params.get("options") or {}
        unit = " " * int(options.get("tabSize", 4)) \
            if options.get("insertSpaces", True) else "\t"
        try:
            made = fmt.format_source(source, unit, _names_for(uri))
        except tree.StructureError:
            return []
        if made == source:
            return []
        lines = source.splitlines(keepends=True)
        end_line = len(lines)
        return [{"range": {"start": {"line": 0, "character": 0},
                           "end": {"line": end_line, "character": 0}},
                 "newText": made}]

    # -- diagnostics -----------------------------------------------

    def publish(self, uri: str) -> None:
        source = self.documents.get(uri, "")
        path = _path_of(uri)
        found = check_source(source, str(path) if path else "",
                             self.declarations,
                             base_dir=path.parent if path else None)
        self.notify("textDocument/publishDiagnostics",
                    {"uri": uri,
                     "diagnostics": [as_lsp(item, source) for item in found]})

    # -- the wire ----------------------------------------------------

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Answers a request, or acts on a notification and answers nothing."""
        identifier = message.get("id")
        method = message.get("method", "")
        handler = self.handlers.get(method)
        if handler is None:
            if identifier is None:
                return None
            return _fail(identifier, METHOD_NOT_FOUND,
                         "method not found: %s" % method)
        try:
            result = handler(message.get("params") or {})
        except (KeyError, TypeError, ValueError) as error:
            if identifier is None:
                return None
            return _fail(identifier, INVALID_PARAMS, str(error))
        except Exception as error:                          # noqa: BLE001
            if identifier is None:
                return None
            return _fail(identifier, INTERNAL_ERROR,
                         "%s: %s" % (type(error).__name__, error))
        if identifier is None:
            return None
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    def notify(self, method: str, params: dict[str, Any]) -> None:
        write_message(self.sink,
                      {"jsonrpc": "2.0", "method": method, "params": params})


def as_lsp(item: diagnostics.Diagnostic, source: str) -> dict[str, Any]:
    """A finding in the shape the editor draws."""
    line = max(item.line - 1, 0)
    character = max(item.column - 1, 0)
    lines = source.splitlines()
    end = len(lines[line]) if line < len(lines) else character
    if end <= character:
        end = character + 1
    message = item.message
    if item.suggestions:
        message += "  Did you mean: %s?" % ", ".join(item.suggestions)
    return {
        "range": {"start": {"line": line, "character": character},
                  "end": {"line": line, "character": end}},
        "severity": SEVERITY.get(item.severity, 3),
        "code": item.code,
        "source": "ct4",
        "message": message,
    }


def _path_of(uri: str) -> Path | None:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(urllib.request.url2pathname(parsed.path))


def _names_for(uri: str) -> Any:
    from ct4 import directives
    from ct4.lang import tree

    registered = directives.find_for(_path_of(uri))
    if not registered.names:
        return None
    return tree.syntax(registered.line, registered.block)


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("Cheetah4")
    except Exception:                                   # noqa: BLE001
        return "unknown"


def _fail(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier,
            "error": {"code": code, "message": message}}


# -- framing --------------------------------------------------------

def read_message(source: BinaryIO) -> dict[str, Any] | None:
    """One framed message, or None at the end of the stream."""
    length = None
    while True:
        line = source.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, _, value = line.decode("ascii", "replace").partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    if length is None:
        raise ValueError("a message without a Content-Length header")
    body = source.read(length)
    message: dict[str, Any] = json.loads(body.decode("utf-8"))
    return message


def write_message(sink: BinaryIO, message: dict[str, Any]) -> None:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sink.write(b"Content-Length: %d\r\n\r\n" % len(body))
    sink.write(body)
    sink.flush()


def serve(stdin: BinaryIO | None = None, stdout: BinaryIO | None = None
          ) -> int:
    """Reads messages until the editor says exit or the stream ends."""
    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout.buffer
    server = Server(sink)
    while not server.stopped:
        try:
            message = read_message(source)
        except (ValueError, UnicodeDecodeError):
            continue
        if message is None:
            break
        answer = server.handle(message)
        if answer is not None:
            write_message(sink, answer)
    return 0


def frame(message: dict[str, Any]) -> bytes:
    """A message as it travels, for a test that plays the editor."""
    buffer = io.BytesIO()
    write_message(buffer, message)
    return buffer.getvalue()
