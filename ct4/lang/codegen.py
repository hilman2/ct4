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

What it can do today: text, comments, escapes, placeholders with their
call and subscript chains, and #for, #if, #while, #unless, #repeat,
#try, #set, #silent, #echo, #slurp, #break, #continue, #pass, #import
#from, #def and #block, in their block form and in the colon short
form, plus #attr, #raise, #set global and the #unicode line ct3
cuts out before it parses. That is 943 of the 1636 render cases.

What it generates is a subclass of ct3's Template, not a plain
function. A #def has to be a method, and $self and $getVar have to
resolve, and both need the instance that ct3 puts in the template's
own search list.

What it turns away, in the order the corpus says it costs most: the
cache token; PSP; #include and #extends, which need a template to
include into; #filter and #call, which change how the output is
written; and the chained short form, where an #else on the next line
joins an #if that has already closed.
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
CLASS = "_Ct4Template"
MAIN = "respond"

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
    return found or None


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
#
# A class and not a function, because #def has to become a method and
# because $self and $getVar resolve against the instance, which ct3
# puts in the template's own search list.
SKELETON = """\
from Cheetah.DummyTransaction import DummyTransaction
from Cheetah.NameMapper import valueForName as VFN
from Cheetah.NameMapper import valueFromFrameOrSearchList as VFFSL
from Cheetah.Template import Template


class %s(Template):
    pass
""" % CLASS

# What every generated method opens with, ct3's prologue word for word.
# The transaction is its own because a method can be called on its own,
# and then it collects into a throwaway response and returns the text.
PROLOGUE = """\
def %%s(self, %%s**KWS):
    trans = KWS.get("trans")
    if (not trans and not self._CHEETAH__isBuffering
            and not callable(self.transaction)):
        trans = self.transaction
    if not trans:
        trans = DummyTransaction()
        _dummyTrans = True
    else:
        _dummyTrans = False
    write = trans.response().write
    %s = self._CHEETAH__searchList
    %s = self._CHEETAH__currentFilter
    pass
    return _dummyTrans and trans.response().getvalue() or ""
""" % (SEARCH_LIST, FILTER)


def _method(name: str, arguments: str,
            body: list[ast.stmt]) -> ast.stmt:
    """One generated method, with the body in ct3's frame."""
    try:
        made = ast.parse(PROLOGUE % (name, arguments)).body[0]
    except SyntaxError as error:
        # A parameter list this layer cannot read must be refused, not
        # let out as a SyntaxError: a caller falls back on Unsupported
        # and crashes on anything else.
        raise Unsupported("cannot read the arguments of #def %s: %s"
                          % (name, error)) from None
    assert isinstance(made, ast.FunctionDef)
    # The lone "pass" between the prologue and the return.
    made.body[-2:-1] = body
    return made


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
        """The template class, ready to be given a search list."""
        namespace: dict[str, Any] = {}
        exec(compile(self.module, "<ct4>", "exec"), namespace)
        return namespace[CLASS]


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
    source = _preprocess(source)
    root = tree.parse(source)
    hoisted: list[ast.stmt] = []
    methods: list[ast.stmt] = []
    body = _statements(_pieces(root, source, hoisted, methods))
    module = ast.parse(SKELETON)
    # Before the class, the way ct3 puts them at module level. An
    # import inside a method would run on every render.
    module.body[-1:-1] = hoisted
    made = module.body[-1]
    assert isinstance(made, ast.ClassDef)
    made.body = methods + [_method(MAIN, "", body)]
    ast.fix_missing_locations(module)
    return Generated(ast.unparse(module), module)


def render(source: str, search_list: Sequence[Any],
           output_filter: Any = None) -> str:
    """Generates, runs, and returns what the template produces."""
    klass = generate(source).compile()
    keywords = {"filter": output_filter} if output_filter else {}
    template = klass(searchList=list(search_list), **keywords)
    try:
        text: str = str(template.respond())
    finally:
        template.shutdown()
    return text


def _preprocess(source: str) -> str:
    """What ct3 does to a template before it parses it.

    ``#unicode`` is no directive at all: ct3 finds the line with a
    regular expression and cuts it out. The same pattern is used here,
    so the two cannot disagree about what counts as one.

    ``#encoding`` is still turned away. With a str source ct3 puts it
    back through repr, encodes it with backslashreplace and decodes it
    again, and that can change what gets parsed.

    Raises:
        Unsupported: where #encoding appears.
    """
    from Cheetah.Parser import encodingDirectiveRE, unicodeDirectiveRE

    if unicodeDirectiveRE.search(source):
        if encodingDirectiveRE.search(source):
            raise Unsupported("#encoding and #unicode together")
        without: str = unicodeDirectiveRE.sub("", source)
        return without
    if encodingDirectiveRE.search(source):
        raise Unsupported("#encoding is applied before parsing")
    return source


# -- What the template writes ----------------------------------------
#
# Worked out as a list of pieces first, and turned into statements
# after. The whitespace rule below reaches backwards into what was
# already written, and doing that to a list of strings is plain where
# doing it to a list of ast nodes would not be.

TEXT_PIECE = "text"
VALUE_PIECE = "value"
# A block already turned into statements. It carries no text, so the
# whitespace rules never reach into it.
STMT_PIECE = "stmt"

# Directives that only announce a branch of the block they sit in.
BRANCHES = ("else", "elif")


def _pieces(root: tree.Node, source: str,
            hoisted: list[ast.stmt],
               methods: list[ast.stmt]) -> list[tuple[str, Any]]:
    return _pieces_of(root.children, source, hoisted, methods)


def _pieces_of(nodes: Sequence[tree.Node], source: str,
               hoisted: list[ast.stmt],
               methods: list[ast.stmt]) -> list[tuple[str, Any]]:
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
    for node in nodes:
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
        elif node.kind == tree.BLOCK:
            if node.name in ("def", "block"):
                _eat_directive_line(node, source, out)
                call = _definition(node, source, hoisted, methods)
                if call is not None:
                    out.append((STMT_PIECE, call))
                continue
            if node.name == "raw":
                # More shapes than this layer reads: a colon short
                # form, a line ending that belongs to the directive
                # rather than the contents, and an indent rule that
                # reaches back past the start of the line.
                raise Unsupported("#raw")
            _eat_directive_line(node, source, out)
            out.append((STMT_PIECE, _block(node, source, hoisted, methods)))
        elif node.kind == lex.DIRECTIVE:
            if node.name in BRANCHES:
                # A branch that reached here belongs to no block this
                # layer built. It is the chained colon short form,
                # "#if 0: a" then "#else: b", which ct3 joins in the
                # generated Python because its dedent puts them back at
                # the same level. Read as a stray directive its body
                # would simply vanish.
                raise Unsupported("#%s outside a block" % node.name)
            if node.name == "end":
                # Handled by the block it closes.
                _eat_directive_line(node, source, out)
            elif node.name == "slurp":
                # It exists to swallow the line ending after it, and
                # that ending is already inside its own tokens, so it
                # never gets written. What is left is the indent, and
                # eatSlurp drops that wherever the line was clear. Note
                # that it does so without the second condition the
                # other directives carry: a slurp always ends its line.
                if _line_is_clear(source, node.tokens[0].start):
                    _drop_indent(out)
            elif node.name == "attr":
                _eat_directive_line(node, source, out)
                methods.append(_attr_statement(node))
            elif node.name in ("import", "from"):
                _eat_directive_line(node, source, out)
                hoisted.append(_import_statement(node))
            else:
                _eat_directive_line(node, source, out)
                for made in _simple_directive(node):
                    out.append((STMT_PIECE, made))
        elif node.kind == lex.EOL_SLURP:
            # It writes nothing and has already taken its line ending
            # with it. What is left is the indent before it.
            if _line_is_clear(source, node.tokens[0].start):
                _drop_indent(out)
        else:
            _piece(node, out)
    return out


def _piece(node: tree.Node, out: list[tuple[str, Any]]) -> None:
    if node.kind in (lex.TEXT, lex.RAW):
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


def _eat_directive_line(node: tree.Node, source: str,
                        out: list[tuple[str, Any]]) -> None:
    """A directive writes nothing, and decides about its own line.

    Two conditions, both ct3's, and the second is easy to miss.
    _eatRestOfDirectiveTag removes the whitespace before a directive
    only where the line was clear *and* the tag ran past the end of its
    own first line. In ``  #for $i in range(5)#$i#end for#`` the tag
    ends at the hash in the middle of the line, so the two spaces stay
    and a corpus case says so.

    The line ending is inside the directive's own tokens where it took
    one, so keeping it means writing it out again.
    """
    own = "".join(t.text for t in node.tokens)
    ending = _trailing_eol(own)
    past_its_line = bool(ending) or node.tokens[-1].end >= len(source)
    if _line_is_clear(source, node.tokens[0].start):
        if past_its_line:
            _drop_indent(out)
        return
    if ending:
        out.append((TEXT_PIECE, ending))



def _import_statement(node: tree.Node) -> ast.stmt:
    """``#import os`` and ``#from os import path`` as themselves.

    No placeholders are resolved: an import names modules, and a dollar
    in one would be a mistake rather than a lookup.
    """
    # The name comes from the node and the rest from its arguments.
    # Joining the tokens would put the hash in front of it, and Python
    # would read the whole line as a comment: the statement then parses
    # to nothing at all, which is how this went unnoticed.
    arguments = "".join(t.text for t in node.tokens[1:])
    made = _framed_statement("%s %s" % (node.name, arguments.strip()))
    if not isinstance(made, (ast.Import, ast.ImportFrom)):
        raise Unsupported("#%s that is not an import" % node.name)
    return made


# A definition's header: a name, and optionally a parameter list.
DEFINITION = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z_0-9]*)\s*(?:\((?P<params>.*)\))?\s*$",
    re.S)

# A dollar in front of a parameter name. ct3 writes "def show(self, x,
# y=1, **KWS)" for "#def show($x, $y=1)".
PARAM_DOLLAR = re.compile(r"\$(?=[A-Za-z_])")


def _parameters(text: str | None) -> str:
    """A definition's parameter list as Python, ready for the frame.

    ct3 writes "def show(self, x, y=1, **KWS)" for "#def show($x,
    $y=1)": the dollar goes and the name stays. A default value with a
    placeholder in it is turned away rather than stripped the same
    way, because a bare name there would resolve against whatever
    happened to be in scope.
    """
    if not text or not text.strip():
        return ""
    out = []
    for part in _split_arguments(text):
        name, _, default = part.partition("=")
        name = PARAM_DOLLAR.sub("", name.strip())
        if "$" in default:
            raise Unsupported("a placeholder in a default value")
        out.append(name if not default else "%s=%s" % (name, default))
    return ", ".join(out) + ", "


def _split_arguments(text: str) -> list[str]:
    """Splits on the commas that are not inside brackets or strings."""
    parts = []
    depth = 0
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in "\"'":
            index = lex._end_of_string(text, index)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
        index += 1
    parts.append(text[start:])
    return [part for part in parts if part.strip()]


def _definition(node: tree.Node, source: str, hoisted: list[ast.stmt],
                methods: list[ast.stmt]) -> ast.stmt | None:
    """``#def`` and ``#block`` as methods on the generated class.

    Returns the call a block makes where it stands, or None for a def,
    which is only called by name from somewhere else. ct3 writes
    ``self.mid(trans=trans)`` for a block and nothing at all for a def.

    The method resolves like any other name because the instance is in
    the template's own search list, and autocalling reaches it.
    """
    # The raw text, not the resolved one: a dollar in a definition's
    # header names a parameter and is not a lookup. Running it through
    # _token_source would turn "#def show($x)" into a call to VFFSL.
    header = "".join(t.text for t in node.tokens[1:]).strip()
    match = DEFINITION.match(header)
    if match is None:
        raise Unsupported("#%s %r" % (node.name, header[:40]))
    params = _parameters(match.group("params"))
    name = match.group("name")
    body = _statements(_pieces_of(node.children, source, hoisted, methods))
    methods.append(_method(name, params, body or [ast.Pass()]))
    if node.name == "def":
        return None
    return ast.Expr(value=ast.Call(
        func=_attribute("self", name), args=[],
        keywords=[ast.keyword(arg="trans",
                              value=ast.Name(id="trans", ctx=ast.Load()))]))


def _try_block(node: tree.Node, source: str,
               hoisted: list[ast.stmt],
               methods: list[ast.stmt]) -> ast.stmt:
    """``#try`` with its ``#except`` and ``#finally`` arms."""
    statement = ast.Try(body=[], handlers=[], orelse=[], finalbody=[])
    for directive, children in _branches(node, ("except", "finally")):
        made = _statements(_pieces_of(children, source, hoisted, methods)) \
            or [ast.Pass()]
        if directive is node:
            statement.body = made
            continue
        if directive.name == "finally":
            statement.finalbody = made
            continue
        header = _framed("try:\n    pass\nexcept %s:"
                         % (_argument(directive, node) or "Exception"))
        assert isinstance(header, ast.Try)
        handler = header.handlers[0]
        handler.body = made
        statement.handlers.append(handler)
    if not statement.handlers and not statement.finalbody:
        raise Unsupported("#try without an #except or #finally")
    return statement


def _block(node: tree.Node, source: str,
           hoisted: list[ast.stmt],
               methods: list[ast.stmt]) -> ast.stmt:
    """The statement a block directive becomes.

    A block with no children at all never opened: it is the colon short
    form, whose body sits on the directive's own line, or the ternary
    ``#if a then b else c``. Both put the body somewhere this layer does
    not look, so they are turned away rather than read wrong.
    """
    if not node.children:
        raise Unsupported("the one-line form of #%s" % node.name)
    if node.name == "for":
        return _for_block(node, source, hoisted, methods)
    if node.name == "if":
        return _if_block(node, source, hoisted, methods)
    if node.name == "try":
        return _try_block(node, source, hoisted, methods)
    if node.name == "while":
        return _headed_block("while %s:", node, source, hoisted, methods)
    if node.name == "unless":
        # ct3 writes the parentheses, and they matter: without them
        # "#unless $a or $b" would negate only the first.
        return _headed_block("if not (%s):", node, source, hoisted, methods)
    if node.name == "repeat":
        # A counter nobody can collide with, named after where the
        # directive stands so that two runs give the same code.
        name = "__ct4_repeat_%d_%d" % (node.line, node.column)
        return _headed_block("for " + name + " in range(%s):", node,
                             source, hoisted, methods)
    raise Unsupported("#%s" % node.name)


def _headed_block(shape: str, node: tree.Node, source: str,
                  hoisted: list[ast.stmt],
               methods: list[ast.stmt]) -> ast.stmt:
    """A block whose header is the shape with its argument in it."""
    statement = _framed(shape % _argument(node, node))
    statement.body = _body(node, source, hoisted, methods)       # type: ignore[attr-defined]
    return statement


def _simple_directive(node: tree.Node) -> list[ast.stmt]:
    """A directive that is one or two statements and opens nothing."""
    if node.name == "pass":
        return [ast.Pass()]
    if node.name == "break":
        return [ast.Break()]
    if node.name == "continue":
        return [ast.Continue()]
    if node.name == "silent":
        # The expression is evaluated and its value dropped.
        return [ast.Expr(value=_parsed(_argument(node, node)))]
    if node.name == "echo":
        # The same two statements a placeholder writes.
        return _placeholder_value(_parsed(_argument(node, node)))
    if node.name == "set":
        return [_set_statement(node)]
    if node.name == "raise":
        return [_framed_statement("raise %s" % _argument(node, node))]
    raise Unsupported("#%s" % node.name)


def _attr_statement(node: tree.Node) -> ast.stmt:
    """``#attr $x = 1`` as a class variable.

    ct3 puts it on the class rather than in a method, so it is there
    before any render and every instance shares it.
    """
    made = _set_statement(node, allow_global=False)
    if not isinstance(made, ast.Assign):
        raise Unsupported("#attr that is not an assignment")
    return made


NAME_ONLY = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")


def _global_set(node: tree.Node) -> ast.stmt:
    """``#set global $a = 1`` writes into the template instance.

    ct3 generates self._CHEETAH__globalSetVars["a"] = 1, so the value
    outlives the method it was set in and every later lookup finds it.
    """
    raw = "".join(t.text for t in node.tokens[1:])
    raw = re.sub(r"^\s*global\b", "", raw, count=1)
    name, sign, _ = raw.partition("=")
    name = name.strip().lstrip("$")
    if not sign or not NAME_ONLY.match(name):
        raise Unsupported("#set global %r" % raw.strip()[:40])
    resolved = "".join(_token_source(t) for t in node.tokens[1:])
    _, _, value = resolved.partition("=")
    return _framed_statement(
        'self._CHEETAH__globalSetVars["%s"] =%s' % (name, value))


def _set_statement(node: tree.Node, allow_global: bool = True) -> ast.stmt:
    """``#set $a = 1`` as ``a = 1``.

    The target loses its dollar and becomes a plain name; only the
    right-hand side is looked up. ``#set global`` is a different thing
    altogether: ct3 writes it into the template instance, and there is
    no instance here.
    """
    parts = []
    target = True
    for token in node.tokens[1:]:
        if target and token.kind == lex.PLACEHOLDER:
            path = _plain_path(token.text)
            if path is None or token.children:
                raise Unsupported("assignment target %r" % token.text)
            parts.append(path)
            continue
        if target and token.kind == lex.TEXT and \
                re.match(r"\s*global\b", token.text):
            if not allow_global:
                raise Unsupported("#attr global")
            return _global_set(node)
        parts.append(_token_source(token))
        if target and token.kind == lex.TEXT and "=" in token.text:
            target = False
    made = _framed_statement("".join(parts).strip())
    if not isinstance(made, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        raise Unsupported("#set that is not an assignment")
    return made


def _framed_statement(source: str) -> ast.stmt:
    try:
        parsed = ast.parse(source)
    except SyntaxError as error:
        raise Unsupported("cannot read %r: %s" % (source, error)) from None
    if len(parsed.body) != 1:
        raise Unsupported("more than one statement in %r" % source)
    return parsed.body[0]


def _parsed(source: str) -> ast.expr:
    try:
        return ast.parse(source, mode="eval").body
    except SyntaxError as error:
        raise Unsupported("cannot read %r: %s" % (source, error)) from None


def _for_block(node: tree.Node, source: str,
               hoisted: list[ast.stmt],
               methods: list[ast.stmt]) -> ast.stmt:
    """``#for $r in $rows`` as a Python for statement.

    The targets lose their dollar and become plain names, which is what
    ct3 writes: ``for r in VFFSL(SL,"rows",True):``. Only the iterable
    is looked up.
    """
    statement = _framed("for %s:" % _for_argument(node))
    assert isinstance(statement, ast.For)
    statement.body = _body(node, source, hoisted, methods)
    return statement


def _if_block(node: tree.Node, source: str,
              hoisted: list[ast.stmt],
               methods: list[ast.stmt]) -> ast.stmt:
    """``#if`` with its ``#elif`` and ``#else`` branches.

    The branches are children of the if in the tree, not blocks of
    their own, so the children are cut at them and each piece becomes
    the body of one arm.
    """
    branches = _branches(node)
    statement = _framed("if %s:" % _argument(branches[0][0], node))
    assert isinstance(statement, ast.If)
    current = statement
    statement.body = _statements(
        _pieces_of(branches[0][1], source, hoisted, methods))
    for directive, children in branches[1:]:
        body = _statements(_pieces_of(children, source, hoisted, methods))
        condition = _branch_condition(directive)
        if condition is None:
            current.orelse = body
            continue
        nested = _framed("if %s:" % condition)
        assert isinstance(nested, ast.If)
        nested.body = body
        current.orelse = [nested]
        current = nested
    return statement


def _branch_condition(directive: tree.Node) -> str | None:
    """The condition of a branch, or None where it is a plain else.

    ``#else if x`` is a second spelling of ``#elif x``, and a corpus
    template uses it. Read as an else, its body would run whatever the
    condition said.
    """
    text = "".join(_token_source(t) for t in directive.tokens[1:]).strip()
    if directive.name == "elif":
        return text or None
    if directive.name != "else":
        return None
    match = re.match(r"if\b(.*)", text, re.S)
    return match.group(1).strip() if match else None


def _branches(node: tree.Node,
              at: Sequence[str] = BRANCHES,
              ) -> list[tuple[Any, list[tree.Node]]]:
    """The block's children cut at every branch directive."""
    found: list[tuple[Any, list[tree.Node]]] = [(node, [])]
    for child in node.children:
        if child.kind == lex.DIRECTIVE and child.name in at:
            found.append((child, []))
            continue
        found[-1][1].append(child)
    return found


def _body(node: tree.Node, source: str,
          hoisted: list[ast.stmt],
               methods: list[ast.stmt]) -> list[ast.stmt]:
    """The statements of a block's body, never empty.

    Python needs something between the colon and the next line, and a
    template may well have a loop that writes nothing.
    """
    made = _statements(_pieces_of(node.children, source, hoisted, methods))
    return made or [ast.Pass()]


def _framed(header: str) -> ast.stmt:
    """The statement a header line opens, with a placeholder body.

    Parsed rather than assembled, for the reason the module frame is:
    the node classes have gained fields between Python versions.
    """
    try:
        parsed = ast.parse("%s\n    pass\n" % header)
    except SyntaxError as error:
        raise Unsupported("cannot read %r: %s" % (header, error)) from None
    return parsed.body[0]


def _argument(directive: tree.Node, owner: tree.Node) -> str:
    """A directive's argument as Python, placeholders resolved."""
    parts = []
    for token in directive.tokens[1:]:
        parts.append(_token_source(token))
    return _without_trailing_colon("".join(parts), owner.name)


def _for_argument(node: tree.Node) -> str:
    """``$r in $rows`` as ``r in VFFSL(...)``.

    Before the ``in`` the placeholders are targets and keep only their
    name; after it they are looked up.
    """
    parts = []
    target = True
    for token in node.tokens[1:]:
        if target and token.kind == lex.PLACEHOLDER:
            path = _plain_path(token.text)
            if path is None or token.children:
                raise Unsupported("loop target %r" % token.text)
            parts.append(path)
            continue
        parts.append(_token_source(token))
        if target and token.kind == lex.TEXT and \
                re.search(r"\bin\b", token.text):
            target = False
    return _without_trailing_colon("".join(parts), "for")


def _without_trailing_colon(text: str, name: str) -> str:
    """An argument without the colon that may already close it.

    ``#for $i in range(5):`` is written both ways, and the header this
    layer builds adds a colon of its own. Two of them are a syntax
    error, and 88 corpus cases were refused for it.
    """
    stripped = text.strip()
    if stripped.endswith(":"):
        stripped = stripped[:-1].rstrip()
    if not stripped:
        raise Unsupported("#%s without an expression" % name)
    return stripped


def _token_source(token: lex.Token) -> str:
    """One argument token as Python source."""
    if token.kind == lex.PLACEHOLDER:
        chunks = None if token.children else chunks_of(token.text)
        if chunks is None:
            raise Unsupported("placeholder %r" % token.text)
        return _expression(chunks)
    if token.kind == lex.TEXT:
        return token.text
    if token.kind in (lex.DIRECTIVE_END, lex.COMMENT, lex.BLOCK_COMMENT):
        # Punctuation and comments are not part of the expression.
        # "#set $a = 1 ## why" is an assignment, and ct3 eats the
        # comment before it looks at the expression.
        return ""
    raise Unsupported("%s in a directive argument" % token.kind)


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
        if kind == STMT_PIECE:
            out.append(value)
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
    """The two statements ct3 writes for a placeholder."""
    return _placeholder_value(ast.parse(expression, mode="eval").body)


def _placeholder_value(lookup: ast.expr) -> list[ast.stmt]:
    """The value first, then the write behind a guard.

    A placeholder that resolves to None writes nothing, and the filter
    never sees it.
    """
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
