"""Aus dem gelesenen Dokument eine Cheetah-Definition machen.

Die Ausdruecke der Vorlage werden nicht gedeutet. Sie wandern
unveraendert in eine ``#def``, und Cheetah uebersetzt sie. Damit gelten
im JSON-Modus dieselben Regeln wie im Textmodus: dieselbe searchList,
dasselbe Autocalling, dieselbe Punktschreibweise. Ein zweiter
Ausdrucksparser waere eine zweite Semantik, und eine davon waere falsch.

Was die Definition tut, ist Behaelter oeffnen und schliessen und Werte
eintragen. Die Struktur entsteht auf dem Bauplatz, nicht im Text.
"""

from __future__ import annotations

from typing import Any

from ct4.jsonmode.parse import (Arr, Document, Expr, For, If, Lit, Member,
                                Obj, Series, Str)

METHOD_NAME = "_ct4_build"


class Emitter:
    """Sammelt die Zeilen der Definition und die Namen daneben."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.names: list[Any] = []
        self.consts: list[Any] = []

    def name_index(self, value: Any) -> int:
        self.names.append(value)
        return len(self.names) - 1

    def const_index(self, value: Any) -> int:
        self.consts.append(value)
        return len(self.consts) - 1

    def add(self, line: str) -> None:
        self.lines.append(line)

    def call(self, text: str) -> None:
        # #silent wertet den Ausdruck aus und schreibt nichts. Genau das
        # soll passieren: die Ausgabe der Definition ist ihr Rueckgabe-
        # wert, nicht ihr Text.
        self.lines.append("#silent $B.%s" % text)


def emit(document: Document) -> tuple[str, list[Any], list[Any]]:
    """Gibt Quelltext, Namen und feste Werte zurueck."""
    out = Emitter()
    out.add("#def %s($B)" % METHOD_NAME)
    _value(out, document.root, holder=None)
    out.add("#return $B.result")
    out.add("#end def")
    return "\n".join(out.lines) + "\n", out.names, out.consts


def _value(out: Emitter, node: Any, holder: str | None,
           precision: int | None = None) -> None:
    """Schreibt einen Wert an die Stelle, die ``holder`` bezeichnet.

    ``holder`` ist None fuer die Wurzel, ein Aufruf von ``open_key`` fuer
    ein Mitglied und ``open_item`` fuer ein Element. Behaelter und
    einfache Werte gehen verschiedene Wege, weil ein Behaelter geoeffnet
    und wieder geschlossen werden muss.
    """
    if isinstance(node, (Obj, Arr)):
        kind = "obj" if isinstance(node, Obj) else "arr"
        if holder is None:
            out.call("open('%s')" % kind)
        else:
            out.call(holder % kind)
        if isinstance(node, Obj):
            for member in node.members:
                _member(out, member)
        else:
            for item in node.items:
                _item(out, item)
        out.call("end()")
        return

    text = _expression(out, node)
    suffix = "" if precision is None else ", %d" % precision
    if holder is None:
        # Ein Dokument, das nur aus einem Wert besteht. Selten, aber
        # gueltiges JSON, und der Bauplatz braucht trotzdem eine Wurzel.
        out.call("open('arr')")
        out.call("item(%s%s)" % (text, suffix))
        out.call("end()")
        out.add("#silent $B.take_first()")
        return
    out.call(holder % text if "%s" in holder else holder)


def _member(out: Emitter, node: Any) -> None:
    if isinstance(node, For):
        out.add("#for %s" % node.header)
        for inner in node.body:
            _member(out, inner)
        out.add("#end for")
        return
    if isinstance(node, If):
        for index, (condition, body) in enumerate(node.branches):
            out.add("#%s %s" % ("if" if index == 0 else "elif", condition))
            for inner in body:
                _member(out, inner)
        if node.otherwise is not None:
            out.add("#else")
            for inner in node.otherwise:
                _member(out, inner)
        out.add("#end if")
        return

    assert isinstance(node, Member)
    if isinstance(node.value, (Obj, Arr)):
        kind = "obj" if isinstance(node.value, Obj) else "arr"
        out.call("%s" % _open_key(out, node.key, kind))
        if isinstance(node.value, Obj):
            for member in node.value.members:
                _member(out, member)
        else:
            for item in node.value.items:
                _item(out, item)
        out.call("end()")
        return

    text = _expression(out, node.value)
    precision = getattr(node.value, "precision", None)
    suffix = "" if precision is None else ", %d" % precision
    if isinstance(node.key, Lit):
        out.call("key(%d, %s%s)"
                 % (out.name_index(node.key.value), text, suffix))
    else:
        out.call("key_at(%s, %s%s)"
                 % (_expression(out, node.key), text, suffix))


def _open_key(out: Emitter, key: Any, kind: str) -> str:
    if isinstance(key, Lit):
        return "open_key(%d, '%s')" % (out.name_index(key.value), kind)
    # Ein Schluessel, der erst zur Laufzeit entsteht, wird zuerst als
    # leerer Behaelter angelegt und dann betreten.
    return "open_key_at(%s, '%s')" % (_expression(out, key), kind)


def _item(out: Emitter, node: Any) -> None:
    if isinstance(node, For):
        out.add("#for %s" % node.header)
        for inner in node.body:
            _item(out, inner)
        out.add("#end for")
        return
    if isinstance(node, If):
        for index, (condition, body) in enumerate(node.branches):
            out.add("#%s %s" % ("if" if index == 0 else "elif", condition))
            for inner in body:
                _item(out, inner)
        if node.otherwise is not None:
            out.add("#else")
            for inner in node.otherwise:
                _item(out, inner)
        out.add("#end if")
        return
    if isinstance(node, (Obj, Arr)):
        kind = "obj" if isinstance(node, Obj) else "arr"
        out.call("open_item('%s')" % kind)
        if isinstance(node, Obj):
            for member in node.members:
                _member(out, member)
        else:
            for item in node.items:
                _item(out, item)
        out.call("end()")
        return

    text = _expression(out, node)
    precision = getattr(node, "precision", None)
    suffix = "" if precision is None else ", %d" % precision
    out.call("item(%s%s)" % (text, suffix))


def _expression(out: Emitter, node: Any) -> str:
    """Der Cheetah-Ausdruck, der diesen Knoten liefert."""
    if isinstance(node, Lit):
        return "$B.lit(%d)" % out.const_index(node.value)
    if isinstance(node, Expr):
        return node.text
    if isinstance(node, Str):
        chunks: list[str] = [""]
        arguments: list[str] = []
        for part in node.parts:
            if isinstance(part, Lit):
                chunks[-1] += part.value
            else:
                arguments.append(part.text)
                chunks.append("")
        return "$B.cat(%d%s)" % (
            out.const_index(chunks),
            "".join(", " + argument for argument in arguments))
    if isinstance(node, Series):
        return "$B.series(%s, %d)" % (node.expr,
                                      out.const_index(_series_options(node)))
    raise TypeError("unbekannter Knoten: %s" % type(node).__name__)


def _series_options(node: Series) -> dict[str, Any]:
    return {"layout": node.layout, "fields": node.fields,
            "precision": node.precision, "gaps": node.gaps}
