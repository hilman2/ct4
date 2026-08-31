"""Turning the parsed document into a Cheetah definition.

The template's expressions are never interpreted. They go into a
``#def`` unchanged, and Cheetah compiles them. JSON mode therefore
follows the same rules as text mode: the same searchList, the same
autocalling, the same dot notation. A second expression parser would be
a second semantics, and one of the two would be wrong.

What the definition does is open and close containers and enter values.
The structure takes shape on the building site, not in the text.
"""

from __future__ import annotations

from typing import Any

from ct4.jsonmode.parse import (Arr, Document, Expr, For, If, Lit, Member,
                                Obj, Series, Str)

METHOD_NAME = "_ct4_build"


class Emitter:
    """Collects the lines of the definition and the names beside it."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.names: list[Any] = []
        self.consts: list[Any] = []
        # Line in the definition to line in the template. Without it a
        # rendering error points into the definition, and nobody ever
        # wrote that.
        self.origins: dict[int, int] = {}

    def name_index(self, value: Any) -> int:
        self.names.append(value)
        return len(self.names) - 1

    def const_index(self, value: Any) -> int:
        self.consts.append(value)
        return len(self.consts) - 1

    def add(self, line: str, origin: int = 0) -> None:
        self.lines.append(line)
        if origin:
            self.origins[len(self.lines)] = origin

    def call(self, text: str, origin: int = 0) -> None:
        # #silent evaluates the expression and writes nothing. That is
        # exactly what should happen: the output of the definition is
        # its return value, not its text.
        self.add("#silent $B.%s" % text, origin)


def emit(document: Document) -> tuple[str, list[Any], list[Any]]:
    """Returns source code, names and constant values."""
    code, names, consts, _ = emit_with_origins(document)
    return code, names, consts


def emit_with_origins(
        document: Document,
        ) -> tuple[str, list[Any], list[Any], dict[int, int]]:
    """Like ``emit``, plus definition line to template line.

    ``ct4.trace`` needs that mapping: a rendering error should point at
    the line of the template, not at a line of the definition that
    nobody ever wrote.
    """
    out = Emitter()
    out.add("#def %s($B)" % METHOD_NAME)
    _value(out, document.root, holder=None)
    out.add("#return $B.result")
    out.add("#end def")
    return ("\n".join(out.lines) + "\n", out.names, out.consts, out.origins)


def _value(out: Emitter, node: Any, holder: str | None,
           precision: int | None = None) -> None:
    """Writes a value at the place that ``holder`` designates.

    ``holder`` is None for the root, a call to ``open_key`` for a
    member and ``open_item`` for an element. Containers and plain
    values take different paths, because a container has to be opened
    and closed again.
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
        # A document that consists of a single value. Rare, but valid
        # JSON, and the building site still needs a root.
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

    origin = getattr(node.value, "line", 0)
    if isinstance(node.value, Series) and isinstance(node.key, Lit):
        out.call("series_key(%d, %s, %d)"
                 % (out.name_index(node.key.value), node.value.expr,
                    out.const_index(_series_options(node.value))), origin)
        return

    text = _expression(out, node.value)
    precision = getattr(node.value, "precision", None)
    suffix = "" if precision is None else ", %d" % precision
    if isinstance(node.key, Lit):
        out.call("key(%d, %s%s)"
                 % (out.name_index(node.key.value), text, suffix), origin)
    else:
        out.call("key_at(%s, %s%s)"
                 % (_expression(out, node.key), text, suffix), origin)


def _open_key(out: Emitter, key: Any, kind: str) -> str:
    if isinstance(key, Lit):
        return "open_key(%d, '%s')" % (out.name_index(key.value), kind)
    # A key that only arises at run time is first created as an empty
    # container and then entered.
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

    if isinstance(node, Series):
        out.call("series_item(%s, %d)"
                 % (node.expr, out.const_index(_series_options(node))),
                 getattr(node, "line", 0))
        return

    text = _expression(out, node)
    precision = getattr(node, "precision", None)
    suffix = "" if precision is None else ", %d" % precision
    out.call("item(%s%s)" % (text, suffix), getattr(node, "line", 0))


def _expression(out: Emitter, node: Any) -> str:
    """The Cheetah expression that yields this node."""
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
    raise TypeError("unknown node: %s" % type(node).__name__)


def _series_options(node: Series) -> dict[str, Any]:
    return {"layout": node.layout, "fields": node.fields,
            "precision": node.precision, "gaps": node.gaps}
