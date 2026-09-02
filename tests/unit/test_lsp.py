"""ct4 lsp: the check and the formatter, spoken to an editor."""

from __future__ import annotations

import io
import json

from ct4 import lsp


def played(*messages):
    """Runs the server over the messages and returns what it wrote."""
    stdin = io.BytesIO(b"".join(lsp.frame(m) for m in messages))
    stdout = io.BytesIO()
    assert lsp.serve(stdin, stdout) == 0
    stdout.seek(0)
    out = []
    while True:
        message = lsp.read_message(stdout)
        if message is None:
            return out
        out.append(message)


def request(identifier, method, params=None):
    return {"jsonrpc": "2.0", "id": identifier, "method": method,
            "params": params or {}}


def notification(method, params):
    return {"jsonrpc": "2.0", "method": method, "params": params}


def opened(uri, text):
    return notification("textDocument/didOpen",
                        {"textDocument": {"uri": uri, "languageId": "cheetah",
                                          "version": 1, "text": text}})


def diagnostics_in(messages, uri):
    found = [m["params"]["diagnostics"] for m in messages
             if m.get("method") == "textDocument/publishDiagnostics"
             and m["params"]["uri"] == uri]
    assert found, messages
    return found


def test_initialize_offers_sync_and_formatting():
    out = played(request(1, "initialize", {"capabilities": {}}),
                 request(2, "shutdown"), notification("exit", {}))
    assert out[0]["id"] == 1
    capabilities = out[0]["result"]["capabilities"]
    assert capabilities["textDocumentSync"] == lsp.FULL_SYNC
    assert capabilities["documentFormattingProvider"] is True
    assert out[0]["result"]["serverInfo"]["name"] == "ct4"
    assert out[1] == {"jsonrpc": "2.0", "id": 2, "result": None}
    assert len(out) == 2


def test_a_typo_is_published_where_it_stands(tmp_path):
    uri = (tmp_path / "index.html.tmpl").as_uri()
    text = "<p>$day.outTemp.max</p>\n<p>$day.outTemp.mx</p>\n"
    out = played(opened(uri, text), notification("exit", {}))
    published = diagnostics_in(out, uri)[-1]
    assert len(published) == 1
    item = published[0]
    assert item["code"] == "CT4103"
    assert item["severity"] == 1
    assert item["source"] == "ct4"
    assert item["range"]["start"] == {"line": 1, "character": 3}
    assert "Did you mean" in item["message"]


def test_a_change_clears_what_it_fixed(tmp_path):
    uri = (tmp_path / "page.tmpl").as_uri()
    out = played(
        opened(uri, "$day.outTemp.mx\n"),
        notification("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": "$day.outTemp.max\n"}]}),
        notification("exit", {}))
    seen = diagnostics_in(out, uri)
    assert [len(batch) for batch in seen] == [1, 0]


def test_closing_clears_the_diagnostics(tmp_path):
    uri = (tmp_path / "page.tmpl").as_uri()
    out = played(opened(uri, "#if 1\n"),
                 notification("textDocument/didClose",
                              {"textDocument": {"uri": uri}}),
                 notification("exit", {}))
    seen = diagnostics_in(out, uri)
    assert seen[0][0]["code"] == "CT4001"
    assert seen[-1] == []


def test_formatting_answers_with_one_edit(tmp_path):
    uri = (tmp_path / "page.tmpl").as_uri()
    out = played(opened(uri, "#if 1\n#echo 1\n  #end if\n"),
                 request(5, "textDocument/formatting", {
                     "textDocument": {"uri": uri},
                     "options": {"tabSize": 2, "insertSpaces": True}}),
                 notification("exit", {}))
    answer = [m for m in out if m.get("id") == 5][0]
    assert answer["result"] == [{
        "range": {"start": {"line": 0, "character": 0},
                  "end": {"line": 3, "character": 0}},
        "newText": "#if 1\n  #echo 1\n#end if\n"}]


def test_nothing_to_format_and_nothing_parseable_answer_empty(tmp_path):
    uri = (tmp_path / "page.tmpl").as_uri()
    out = played(opened(uri, "#if 1\n#end if\n"),
                 request(6, "textDocument/formatting",
                         {"textDocument": {"uri": uri}}),
                 notification("textDocument/didChange", {
                     "textDocument": {"uri": uri, "version": 2},
                     "contentChanges": [{"text": "#if 1\n"}]}),
                 request(7, "textDocument/formatting",
                         {"textDocument": {"uri": uri}}),
                 notification("exit", {}))
    answers = {m["id"]: m["result"] for m in out if "id" in m}
    assert answers == {6: [], 7: []}


def test_an_unknown_request_is_an_error_and_an_unknown_notice_is_not():
    out = played(request(9, "textDocument/hover", {}),
                 notification("workspace/didChangeConfiguration", {}),
                 notification("exit", {}))
    assert out == [{"jsonrpc": "2.0", "id": 9,
                    "error": {"code": lsp.METHOD_NOT_FOUND,
                              "message": "method not found:"
                                         " textDocument/hover"}}]


def test_a_stream_that_ends_ends_the_server():
    assert played() == []


def test_the_frame_carries_its_length():
    body = json.dumps({"jsonrpc": "2.0", "method": "x", "params": {}},
                      ensure_ascii=False).encode("utf-8")
    assert lsp.frame({"jsonrpc": "2.0", "method": "x", "params": {}}) == \
        b"Content-Length: %d\r\n\r\n" % len(body) + body
