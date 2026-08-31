"""Python for a template, built with the ast module.

Third layer of the compiler core, and the one that has to earn its
place against a compiler that already works. So it is built to be
incomplete and never wrong: it says what it can do, refuses everything
else, and what it accepts has to render byte for byte the same as ct3.
The measure of progress is how many corpus cases it takes, and that
number has a floor a test holds it to.

Through ``ast`` and not through string concatenation, which is the
point of the exercise. Concatenation is why the old compiler cannot
give a traceback a real line number, and why every generated construct
has to be re-escaped by hand.

What it can do today is the slice the corpus said was worth building:
text, comments, escapes, and placeholders that are a plain dotted
name. That is 37 per cent of the render cases before a single
directive is understood.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any, Sequence

from ct4.lang import lex, tree

# The name the generated function takes its search list under, and the
# name of the filter. Both match what ct3 generates, so that a
# placeholder resolves through exactly the same call.
SEARCH_LIST = "SL"
FILTER = "_filter"
VALUE = "_v"
WRITE = "write"
FUNCTION = "_ct4_render"

# A placeholder this layer understands: a dollar, then a dotted name,
# optionally wrapped in one of the three enclosures. ct3 generates the
# same single VFFSL for "$a.b" and for "${a.b}", so the enclosure costs
# nothing to accept.
#
# What is still turned away: the silence token, the cache token, and
# any call or subscript in the chain. The last of those is not a
# detail. ct3 stops treating the path as one name the moment a call
# appears and walks it step by step instead, so "$a.b(1)" becomes
# VFN(VFFSL(SL,"a",True),"b",False)(1). That is a rule of its own and
# gets added when its cases are ready to be measured.
NAME = r"[A-Za-z_][A-Za-z_0-9]*"
PLAIN = re.compile(
    r"^\$(?:"
    r"(?P<bare>" + NAME + r"(?:\." + NAME + r")*)"
    r"|[{(\[][ \t\f]*(?P<wrapped>" + NAME + r"(?:\." + NAME + r")*)"
    r"[ \t\f]*[)}\]]"
    r")$")


def _plain_path(text: str) -> str | None:
    """The dotted name of a placeholder this layer can take, or None."""
    match = PLAIN.match(text)
    if match is None:
        return None
    path: str = match.group("bare") or match.group("wrapped")
    return path


# What a placeholder starts with and this layer does not read yet.
MODIFIERS = re.compile(r"^\$[!*]")

_template_names: frozenset[str] | None = None


def template_names() -> frozenset[str]:
    """Names a template gets from the Template object it runs inside.

    ct3 renders a template as a method, so its own search list holds
    the instance: $getVar('x') and $self.foo resolve against it. What
    this layer generates is a plain function with no instance anywhere,
    so those names cannot resolve and the template has to be refused
    rather than rendered wrong.

    Read off the class rather than listed here, so a name added to
    Template does not quietly become a wrong answer.
    """
    global _template_names

    if _template_names is None:
        from Cheetah.Template import Template

        _template_names = frozenset(dir(Template)) | {"self", "trans"}
    return _template_names


@dataclass(frozen=True)
class Chunk:
    """One step of a placeholder's chain.

    ``name`` is a dotted run, ``autocall`` says whether NameMapper may
    call what it finds, and ``remainder`` is the call or subscript
    hanging off it as Python source.
    """

    name: str
    autocall: bool
    remainder: str


def chunks_of(text: str) -> list[Chunk] | None:
    """A placeholder's chain, split the way ct3 splits it.

    Called as ``chunks_of("$a.b.c[1].d().x")``. Returns None where the
    text is not a chain this layer reads.

    The rule is measured off ct3 rather than taken from its docstring,
    which is out of date. The name carrying a bracket becomes a chunk
    of its own, so ``$a.b.c[1]`` splits into ``a.b`` and ``c[1]`` and
    not into ``a.b.c[1]``. Autocalling is off for a chunk that is
    called, and on for one that is only subscripted.
    """
    if MODIFIERS.match(text):
        return None
    inner = text[1:]
    if inner[:1] in "{([":
        closing = {"{": "}", "(": ")", "[": "]"}[inner[0]]
        if not inner.endswith(closing):
            return None
        inner = inner[1:-1].strip()
    found: list[Chunk] = []
    names: list[str] = []
    index = 0
    while index < len(inner):
        start = index
        while index < len(inner) and inner[index] in lex.IDENT:
            index += 1
        if index == start:
            return None
        names.append(inner[start:index])
        if index < len(inner) and inner[index] == "." \
                and inner[index + 1:index + 2] and \
                inner[index + 1] in lex.IDENT_START:
            index += 1
            continue
        if index < len(inner) and inner[index] in "([":
            remainder, index = _brackets(inner, index)
            if remainder is None:
                return None
            if len(names) > 1:
                found.append(Chunk(".".join(names[:-1]), True, ""))
            found.append(Chunk(names[-1], not remainder.startswith("("),
                               remainder))
            names = []
            if index < len(inner) and inner[index] == "." \
                    and inner[index + 1:index + 2] and \
                    inner[index + 1] in lex.IDENT_START:
                index += 1
                continue
        break
    if index != len(inner):
        return None
    if names:
        found.append(Chunk(".".join(names), True, ""))
    if not found:
        return None
    if found[0].name.split(".")[0] in template_names():
        return None
    return found


def _brackets(text: str, index: int) -> tuple[str | None, int]:
    """Every bracket group in a row from here, as source."""
    start = index
    while index < len(text) and text[index] in "([":
        closed = lex._balanced(text, index, text[index])
        if closed is None:
            return None, index
        index = closed
    return text[start:index], index

# Node kinds that carry no output at all.
SILENT_KINDS = frozenset({lex.COMMENT, lex.BLOCK_COMMENT})

# The frame the generated body is put into. Parsed, not assembled, so
# that whichever Python runs this fills in its own fields.
SKELETON = """\
from Cheetah.NameMapper import valueForName as VFN
from Cheetah.NameMapper import valueFromFrameOrSearchList as VFFSL


def %s(%s, %s):
    pass
""" % (FUNCTION, SEARCH_LIST, FILTER)


class Unsupported(Exception):
    """This layer will not generate code for that.

    Raised rather than guessed at. An incomplete generator that refuses
    is useful; one that quietly produces something else is worse than
    none, because the corpus would have to catch every case of it.
    """


@dataclass(frozen=True)
class Generated:
    """The Python of a template, and the source it came from."""

    code: str
    module: ast.Module

    def compile(self) -> Any:
        """The callable, ready to be handed a search list and a filter."""
        namespace: dict[str, Any] = {}
        exec(compile(self.module, "<ct4>", "exec"), namespace)
        return namespace[FUNCTION]


def supports(source: str) -> bool:
    """Whether this layer can generate code for the template."""
    try:
        generate(source)
    except (Unsupported, tree.StructureError):
        return False
    return True


def generate(source: str) -> Generated:
    """Python for a template.

    Raises:
        Unsupported: where the template uses something this layer does
            not understand yet.
        tree.StructureError: where the template is not well formed.
    """
    _refuse_preprocessed(source)
    root = tree.parse(source)
    body: list[ast.stmt] = [
        _assign("_out", ast.List(elts=[], ctx=ast.Load())),
        _assign(WRITE, _attribute("_out", "append")),
    ]
    body.extend(_statements(_pieces(root, source)))
    body.append(ast.Return(
        value=ast.Call(func=_attribute_of(ast.Constant(""), "join"),
                       args=[ast.Name(id="_out", ctx=ast.Load())],
                       keywords=[])))
    # The frame is parsed rather than assembled. ast.FunctionDef has
    # gained fields between Python versions, type_params in 3.12 among
    # them, and this project runs from 3.10. Parsing a skeleton gets
    # every field right for whichever version is running it.
    module = ast.parse(SKELETON)
    function = module.body[-1]
    assert isinstance(function, ast.FunctionDef)
    function.body = body
    ast.fix_missing_locations(module)
    return Generated(ast.unparse(module), module)


def render(source: str, search_list: Sequence[Any],
           output_filter: Any = None) -> str:
    """Generates, runs, and returns what the template produces."""
    if output_filter is None:
        from Cheetah.Filters import Filter

        output_filter = Filter().filter
    text: str = generate(source).compile()(list(search_list), output_filter)
    return text


def _refuse_preprocessed(source: str) -> None:
    """Turns away templates ct3 rewrites before it parses them.

    ``#unicode`` is no directive at all: ct3 finds the line with a
    regular expression, cuts it out of the source and decodes the rest
    with what it named. ``#encoding`` decodes too. Neither reaches the
    parser as itself, so a layer that starts at the parser would write
    the line out as text and be quietly wrong. ct3's own patterns are
    used here, so the two cannot disagree about what counts.

    Raises:
        Unsupported: where either appears.
    """
    from Cheetah.Parser import encodingDirectiveRE, unicodeDirectiveRE

    for pattern, name in ((unicodeDirectiveRE, "#unicode"),
                          (encodingDirectiveRE, "#encoding")):
        if pattern.search(source):
            raise Unsupported("%s is applied before parsing" % name)


# -- What the template writes ----------------------------------------
#
# Worked out as a list of pieces first, and turned into statements
# after. The whitespace rule below reaches backwards into what was
# already written, and doing that to a list of strings is plain where
# doing it to a list of ast nodes would not be.

TEXT_PIECE = "text"
VALUE_PIECE = "value"


def _pieces(root: tree.Node, source: str) -> list[tuple[str, Any]]:
    """The output of a template, as text and values in order.

    A comment writes nothing, but it decides what happens to the
    whitespace around it, and the two kinds decide differently. Both
    rules are ct3's, read off eatComment and eatMultiLineComment.
    """
    out: list[tuple[str, Any]] = []
    # Set by a block comment: the whitespace up to the end of the line
    # is still to be taken off whatever comes next, and the flag says
    # whether the line ending goes with it. Carried rather than applied
    # to the tree, because the tree has to stay what the layer below
    # built: it is the thing that writes back to the source.
    pending: bool | None = None
    for node in root.children:
        if pending is not None:
            gobble = pending
            pending = None
            if node.kind == lex.TEXT:
                text = _without_trailing_space(node.text(), gobble)
                if text:
                    out.append((TEXT_PIECE, text))
                continue
        if node.kind == lex.COMMENT:
            _line_comment(node, source, out)
        elif node.kind == lex.BLOCK_COMMENT:
            pending = _block_comment(node, source, out)
        elif node.kind == lex.EOL_SLURP:
            # It writes nothing and has already taken its line ending
            # with it. What is left is the indent before it.
            if _line_is_clear(source, node.tokens[0].start):
                _drop_indent(out)
        else:
            _piece(node, out)
    return out


def _piece(node: tree.Node, out: list[tuple[str, Any]]) -> None:
    if node.kind == lex.TEXT:
        out.append((TEXT_PIECE, node.text()))
        return
    if node.kind == lex.ESCAPE:
        # "\$" stands for a dollar. What Cheetah writes is the
        # character behind the backslash, not both.
        out.append((TEXT_PIECE, node.text()[1:]))
        return
    if node.kind == lex.PLACEHOLDER:
        token = node.tokens[0]
        # A nested placeholder means the chain carries another lookup
        # inside its arguments, and those are not read yet.
        chunks = None if token.children else chunks_of(token.text)
        if chunks is None:
            raise Unsupported("placeholder %r" % token.text)
        out.append((VALUE_PIECE, _expression(chunks)))
        return
    raise Unsupported("no code for a %s node" % node.kind)


def _line_comment(node: tree.Node, source: str,
                  out: list[tuple[str, Any]]) -> None:
    """``## to the end of the line``.

    Where nothing but whitespace stands before it, the whitespace goes
    and the line ending goes with it, which is why a template full of
    comment lines leaves no blank lines behind. Where something does
    stand before it, both stay and only the comment goes.
    """
    if _line_is_clear(source, node.tokens[0].start):
        _drop_indent(out)
        return
    ending = _trailing_eol(node.text())
    if ending:
        out.append((TEXT_PIECE, ending))


def _block_comment(node: tree.Node, source: str,
                   out: list[tuple[str, Any]]) -> bool:
    """``#* over as many lines as it likes *#``.

    Returns whether the line ending after it is to be swallowed as
    well; the whitespace up to it always is, where there is nothing
    else there. The indent before it goes where the line was clear and
    either the comment ran past its own first line or nothing follows
    it at all, which is ct3's ``self.atEnd() or pos > endOfFirstLine``.
    """
    clear = _line_is_clear(source, node.tokens[0].start)
    if not clear:
        return False
    spans_lines = lex.EOL.search(node.text()) is not None
    # What would be left of the source once the trailing whitespace and
    # the line ending are taken. ct3 asks self.atEnd() at exactly this
    # point, after its readToEOL.
    remaining = _without_trailing_space(source[node.tokens[-1].end:], True)
    if spans_lines or not remaining:
        _drop_indent(out)
    return clear


def _without_trailing_space(text: str, gobble_eol: bool) -> str:
    """Takes the whitespace up to the first line ending off some text.

    Only where there is nothing but whitespace there: ct3 checks
    ``restOfLine.strip()`` before it consumes anything.
    """
    match = lex.EOL.search(text)
    head = text[:match.start()] if match else text
    if head.strip():
        return text
    if match is None:
        return ""
    return text[match.end():] if gobble_eol else text[match.start():]


def _trailing_eol(text: str) -> str:
    """The line ending a token ends with, or nothing."""
    for ending in ("\r\n", "\n", "\r"):
        if text.endswith(ending):
            return ending
    return ""


def _line_is_clear(source: str, at: int) -> bool:
    """Whether only whitespace stands between the line start and here."""
    starts = lex.line_starts(source)
    line, _ = lex.where(starts, at)
    begin = starts[line - 1]
    return begin == at or source[begin:at].isspace()


def _drop_indent(out: list[tuple[str, Any]]) -> None:
    """Removes the whitespace already written for the current line.

    ct3 calls it handleWSBeforeDirective and truncates its pending text
    back to the start of the line.
    """
    while out:
        kind, text = out[-1]
        if kind != TEXT_PIECE:
            return
        match = lex.EOL.search(text[::-1])
        if match is not None:
            keep = len(text) - match.start()
            if text[keep:].strip():
                return
            out[-1] = (TEXT_PIECE, text[:keep])
            return
        if text.strip():
            return
        out.pop()


# -- Statements ------------------------------------------------------

def _statements(pieces: list[tuple[str, Any]]) -> list[ast.stmt]:
    out: list[ast.stmt] = []
    for kind, value in pieces:
        if kind == TEXT_PIECE:
            if value:
                out.append(_write(ast.Constant(value)))
            continue
        out.extend(_placeholder(value))
    return out


def _expression(chunks: list[Chunk]) -> str:
    """The Python ct3 writes for a chain of chunks.

    The first is looked up in the search list, every one after it on
    what the previous returned. Built as source and handed to Python's
    own parser, because a remainder like ``(1, 2)`` is Python and there
    is no reason to assemble it node by node.
    """
    first = chunks[0]
    text = 'VFFSL(%s,"%s",%r)%s' % (SEARCH_LIST, first.name,
                                    first.autocall, first.remainder)
    for chunk in chunks[1:]:
        text = 'VFN(%s,"%s",%r)%s' % (text, chunk.name, chunk.autocall,
                                      chunk.remainder)
    return text


def _placeholder(expression: str) -> list[ast.stmt]:
    """The two statements ct3 writes for a placeholder.

    The value first, then the write behind a guard: a placeholder that
    resolves to None writes nothing, and the filter never sees it.
    """
    lookup = ast.parse(expression, mode="eval").body
    return [
        _assign(VALUE, lookup),
        ast.If(
            test=ast.Compare(left=ast.Name(id=VALUE, ctx=ast.Load()),
                             ops=[ast.IsNot()],
                             comparators=[ast.Constant(None)]),
            body=[_write(ast.Call(
                func=ast.Name(id=FILTER, ctx=ast.Load()),
                args=[ast.Name(id=VALUE, ctx=ast.Load())], keywords=[]))],
            orelse=[]),
    ]


# -- Small builders --------------------------------------------------

def _assign(name: str, value: ast.expr) -> ast.stmt:
    return ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())],
                      value=value)


def _attribute(name: str, attribute: str) -> ast.expr:
    return _attribute_of(ast.Name(id=name, ctx=ast.Load()), attribute)


def _attribute_of(value: ast.expr, attribute: str) -> ast.expr:
    return ast.Attribute(value=value, attr=attribute, ctx=ast.Load())


def _write(value: ast.expr) -> ast.stmt:
    return ast.Expr(value=ast.Call(
        func=ast.Name(id=WRITE, ctx=ast.Load()), args=[value], keywords=[]))
