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
call and subscript chains, with whatever Python those carry inside
them, and with the silence and cache tokens in front of them, and
#for, #if, #while, #unless, #repeat, #try, #set, #silent, #echo,
#slurp, #break, #continue, #pass, #import #from, #def and #block, in
their block form and in the colon short form, plus #attr, #raise,
#set global and the #unicode line ct3 cuts out before it parses,
#stop where it stands at the top level, #raw, whose body is written
out with nothing done to it, #include, #extends and #implements, the
three regions that redirect the output, #filter, #call and #cache, the
#encoding line, which writes nothing and is a no-op before parsing for
every codec that reads ASCII the way ASCII does, PSP, the second block
structure, whose body is spliced as the raw Python it is, and
#errorCatcher, which sends every placeholder after it through a wrapper
that hands a NotFound to the catcher.

Placeholders come in ct3's two forms: the name-led one and the one
whose enclosure holds an expression, "$(6)" and "$('#id')", which is
how a jQuery call in a page comes out as "#id".

That is 1431 of the 1636 render cases. The corpus is not the only ruler
worth reading: of the 390 real skin templates in it, 344. The two
numbers move at different rates, and the difference is the point.
#errorCatcher moved 3 corpus cases and 83 skins; the expression
placeholder moved 15 and 25; the head of a #def moved 64 and none at
all. ct3's own test suite has no use for a directive every weewx skin
opens with, and a real skin has no use for four ways of writing a
method head, so a plan read off either ruler alone builds the wrong
things first.

ct4/lang/backend.py hooks this in where ct3 provides for it, so a
caller reaches it through Template.compile and never has to know. What
this refuses ct3 compiles instead. Over the corpus that is 1337 taken
and 301 fallen back, with no difference either way.

What it generates is a subclass of ct3's Template, not a plain
function. A #def has to be a method, and $self and $getVar have to
resolve, and both need the instance that ct3 puts in the template's
own search list. #extends replaces that base and renames the main
method to writeBody; #implements names it whatever it says. An
#include is one runtime call and nothing more: the nested template is
compiled where the render reaches it, by ct3's own Template, which is
what shares the search list and the globalSetVars and copies the
filter across.

What it turns away, in the order the corpus says it costs most, and
counted rather than estimated: any template that sets a compiler
setting, 40, on which see below; the c'...' string, 20; #arg, whose
colon form puts its value where the tree keeps a directive's
arguments, 20; and then #compiler-settings, #breakpoint, #compiler,
#@, #i18n, an #elif whose #if closed on the line before, and a #stop
inside a block, 16 or fewer each.

A compiler setting is refused rather than ignored. This layer reads
none of them, and two of them change what ct3 renders: with
gobbleWhitespaceAroundMultiLineComments off a block comment leaves its
whitespace behind, and with allowWhitespaceAfterDirectiveStartToken on
"# if" is a directive instead of text. Until the settings arrive here,
taking such a template is guessing, and guessing was what it did: for a
while it took 24 of them and rendered them differently from ct3.

One of its refusals is there because the lexer and ct3 disagree about
how much source a placeholder covers, rather than because a rule is
missing: a token that stops short of what ct3 read, "$a[\n1]" and
"$f(1)upper", is turned away rather than read half.

The expression placeholder is a refusal in the other direction. The
lexer knows the form now, and it has to lex it wherever it stands,
because a directive's arguments are ordinary tokens to it. ct3 looks
for that form only while it is scanning text, so "#if $(6)" is a
ParseError there. Such a template is refused rather than resolved.

The whitespace around a directive is decided here from the source and
in ct3 from a buffer of text not yet written. Mostly the two agree, and
where they do not this layer follows the buffer: an indent drop takes
one pending chunk and stops, never the one before it, which is why
"  #encoding x" followed by "  #import os" writes two blanks. Where it
cannot tell whether a piece here is a whole chunk there, the template
is refused rather than guessed at; see _drop_indent.

Three instruments hold that to account, and the point is that their
blind spots are different ones. tests/fuzz/whitespace.py builds
templates out of fragments, so it sees the shapes nobody writes.
tests/fuzz/hostile.py renders the real ones against a context that
answers everything and writes down what it was asked, which is how a
difference both engines spell the same way in bytes becomes visible,
and it is the only run that renders the 390 skin templates at all.
tests/fuzz/perturb.py takes the real ones and moves the directives
around inside them. Each of the three found a rule the other two could
not see.
"""

from __future__ import annotations

import ast
import copy
import re
import threading
from dataclasses import dataclass, field
from tokenize import PseudoToken
from typing import Any, Sequence

from ct4.lang import lex, tree
from ct4.markup import mode as markup_mode
from ct4.markup import scan as markup_scan

# The name the generated function takes its search list under, and the
# name of the filter. Both match what ct3 generates, so that a
# placeholder resolves through exactly the same call.
SEARCH_LIST = "SL"
FILTER = "_filter"
VALUE = "_v"
WRITE = "write"
CLASS = "_Ct4Template"
MAIN = "respond"

TEXT_MODE = "text"
MARKUP_MODE = "markup"

# What the two write helpers of ct4.markup.escape are called inside a
# generated module. Bound by an import that is written only where the
# template declared markup mode, so a text-mode module carries neither
# the names nor the import.
MARKUP_ESCAPED = "_markup_escaped"
MARKUP_VERBATIM = "_markup_verbatim"

MARKUP_IMPORT = ("from ct4.markup.escape import write_escaped as %s\n"
                 "from ct4.markup.escape import write_verbatim as %s\n"
                 % (MARKUP_ESCAPED, MARKUP_VERBATIM))

# The positions markup mode escapes. Everything else, and everything the
# scan could not place at all, gets the runtime demand for __html__.
MARKUP_ESCAPES = frozenset({markup_scan.TEXT, markup_scan.ATTRIBUTE,
                            markup_scan.URL_HEAD})

NAME = r"[A-Za-z_][A-Za-z_0-9]*"


# The silence and cache tokens, which belong to a top-level placeholder
# and nowhere else: ct3 raises a ParseError for "#set $a = $*x" and for
# "#if $!x". _piece takes them off before it reads the chain behind
# them, and every other reader here refuses them.
MODIFIERS = re.compile(r"^\$[!*]")

# ct3's identRE, asked of a token that has already been read.
IDENT_RE = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]*")

# One Python token. The same expression the tokenize module builds and
# ct3's getPyToken matches with, and not one of my own: where a string
# literal ends, whether "==" is one token or two, and what a backslash
# in front of a line ending is all have to be decided exactly as ct3
# decides them, or what comes out is not the same Python.
PY_TOKEN = re.compile(PseudoToken)

# The triple-quoted string starts ct3 knows, each with the expression
# that reads the whole string. tokenize's own pattern matches the three
# opening quotes alone, and ct3 puts the rest back with a second match.
TRIPLE = {prefix + quote:
          re.compile(re.escape(prefix + quote) + ".*?" + re.escape(quote),
                     re.S)
          for prefix in ("", "r", "R", "u", "U", "ur", "uR", "Ur", "UR")
          for quote in ("'''", '"""')}

# Which bracket a closer belongs to.
OPENING = {")": "(", "]": "[", "}": "{"}

# The names ct3's generated module carries and this one does not, read
# off the two module texts. A template can reach them: a bare
# identifier in an expression is written out as itself, and VFFSL falls
# back on the module globals where neither the frame nor the search
# list holds the name. So "$time" writes the time module in ct3 and
# raises NotFound here. No corpus case wants any of them, and
# _refuse_preamble_names turns away the templates that would.
#
# The five __CHEETAH_ names and the six Python gives a real module are
# in the list for the same reason, and a PSP body is what reaches them:
# it is spliced as plain Python, so "<% write(__file__) %>" writes a
# path in ct3 and raises NameError here. ct3 execs its code into a
# module and this layer execs into a bare dict, so the module
# attributes are a difference too.
#
# test_the_preamble_lists_what_ct3_actually_carries computes both sets
# from a compiled template of each engine and holds this list to them.
# Written by hand it went stale within one working day.
PREAMBLE = frozenset((
    "CacheRegion", "DummyResponse", "DummyResponseFailure",
    "DynamicallyCompiledCheetahTemplate",
    "Filters", "RequiredCheetahVersion",
    "RequiredCheetahVersionTuple", "TransformerResponse",
    "TransformerTransaction", "VFSL", "builtin", "exists", "getmtime",
    "logging", "os", "sys", "templateAPIClass", "time", "types",
    "unicode", "valueForName", "valueFromFrameOrSearchList",
    "valueFromSearchList",
    "__CHEETAH_docstring__", "__CHEETAH_src__",
    "__CHEETAH_srcLastModified__",
    "__doc__", "__file__", "__loader__", "__name__", "__package__",
    "__spec__"))

# Of those, the ones ct3's preamble binds by importing, with the import
# that binds them. The same object arrives either way, so a template
# that reaches one of these is given the import rather than turned
# away: over the corpus and the harvested skins that is 57 templates,
# and 56 of them want nothing more exotic than os or time.
#
# What is deliberately not here is everything ct3's preamble *computes*
# rather than imports: the class it generates, templateAPIClass, the
# module constants carrying the source and its timestamp, and the
# module dunders. Those genuinely differ between the two modules, and
# a template reaching one of them still gets a refusal.
#
# ct3 writes the builtins import inside a try/except for Python 2. This
# fork runs from 3.10, where the except branch is dead, so the binding
# is written the one way it can end.
PREAMBLE_IMPORTS = {
    "CacheRegion": "from Cheetah.CacheRegion import CacheRegion",
    "DummyResponse": "from Cheetah.DummyTransaction import DummyResponse",
    "DummyResponseFailure":
        "from Cheetah.DummyTransaction import DummyResponseFailure",
    "Filters": "import Cheetah.Filters as Filters",
    "RequiredCheetahVersion":
        "from Cheetah.Version import MinCompatibleVersion"
        " as RequiredCheetahVersion",
    "RequiredCheetahVersionTuple":
        "from Cheetah.Version import MinCompatibleVersionTuple"
        " as RequiredCheetahVersionTuple",
    "TransformerResponse":
        "from Cheetah.DummyTransaction import TransformerResponse",
    "TransformerTransaction":
        "from Cheetah.DummyTransaction import TransformerTransaction",
    "VFSL": "from Cheetah.NameMapper import valueFromSearchList as VFSL",
    "builtin": "import builtins as builtin",
    "exists": "from os.path import exists",
    "getmtime": "from os.path import getmtime",
    "os": "import os",
    "sys": "import sys",
    "time": "import time",
    "types": "import types",
    "unicode": "from Cheetah.compat import unicode",
    "valueForName": "from Cheetah.NameMapper import valueForName",
    "valueFromFrameOrSearchList":
        "from Cheetah.NameMapper import valueFromFrameOrSearchList",
    "valueFromSearchList":
        "from Cheetah.NameMapper import valueFromSearchList",
}

# The other direction, and the shorter one. A name this layer's module
# carries and ct3's does not resolves here and raises NotFound there,
# which is the same defect with the engines swapped. Today that is the
# class the skeleton declares: "$_Ct4Template" would write a class.
# The default name, and the one the test measures against. The guard
# itself asks about whatever name the module was generated under: a
# module standing in for ct3's carries the class name ct3 asked for.
OURS_ONLY = frozenset((CLASS,))


# -- #errorCatcher ---------------------------------------------------
#
# The one piece of state that spans the whole module being generated.
# ct3 keeps it on the compiler instance: #errorCatcher turns it on and
# every placeholder written after that, in any method, is replaced by a
# call to a wrapper that evaluates the lookup and hands a NotFound to
# the catcher. #end errorCatcher turns it off again.
#
# This generator is a tree of functions and has no instance to hang it
# on. Threading it through the twenty signatures between generate() and
# _placeholder would say nothing except "still on", so it lives here,
# per thread and for the length of one generate() call. Per thread
# because a compile cache may well call generate() from more than one.


class Catcher:
    """The error catcher in force, and the wrappers written for it.

    Args:
        methods (list[ast.stmt]): The class body being built. Wrappers
            are appended to it, the way ct3 spawns them into the class
            compiler rather than into the method that needs them.
    """

    def __init__(self, methods: list[ast.stmt]) -> None:
        self.name: str | None = None
        self.methods = methods
        # Raw placeholder text to the wrapper already written for it.
        # ct3 keeps the same map and for the same reason: a page that
        # writes $current.outTemp forty times gets one wrapper.
        self.seen: dict[str, str] = {}


_ACTIVE = threading.local()


def _catcher() -> Catcher | None:
    """The catcher of the generate() call this is running inside."""
    return getattr(_ACTIVE, "catcher", None)


# What a note is about. A refusal renders only where the value carries
# __html__; a URL head is escaped like any attribute value and that
# stops a quote and not a scheme.
MARKUP_REFUSED = "refused"
MARKUP_URL = "url head"

# What stands where the scan has no site at all. Three writes are not
# placeholders and never stood anywhere the scan looked: #echo, the
# one-line #if, and the value a #call returns. They are refused like
# any unescapable position, and this is what their message says they
# are, because "a position that cannot be escaped" would say nothing
# about a write that has no position.
MARKUP_UNPLACED = "a write the scan could not place"


@dataclass(frozen=True)
class MarkupNote:
    """One placeholder markup mode has something to say about.

    Collected while the code is generated and read by ``ct4.check``,
    which turns each into a finding. Written here and not worked out a
    second time over there, because the list has to be the one the
    render will act on: a note the compiler did not make is a refusal
    nobody was warned about, and a note it made for a write it did not
    emit is a warning about nothing.

    Attributes:
        kind (str): MARKUP_REFUSED or MARKUP_URL.
        note (str): The position in words, as the scan names it.
        line (int): Line in the author's file, from one.
        column (int): Column in the author's file, from one.
    """

    kind: str
    note: str
    line: int
    column: int


@dataclass(frozen=True)
class MarkupState:
    """Markup mode, and what the writes need to know while it is on.

    Present only while a template that declared markup mode is being
    generated, and absent otherwise, which is how text mode is kept out
    of this by construction rather than by a flag that could be read
    wrongly: with no state there is no branch to take and the emitted
    ast is the one that was there before markup mode existed.

    Attributes:
        sites (dict[int, markup_scan.Site]): Where each placeholder that
            writes stands, keyed by ``Token.start``. A placeholder that
            is not in here could not be placed and is refused, which is
            the fail-closed default both sides of that API agree on.
        file (str): The template's path, for the message a refused
            placeholder raises at render time. Empty where the template
            came from a string and there is no path to name.
        shift (int): How many lines the preprocessing cut out of the
            source before it was parsed. Added back to every line this
            reports; see :meth:`where`.
        notes (list[MarkupNote]): Filled while the writes are
            generated, in the order they stand in the template.
    """

    sites: dict[int, markup_scan.Site]
    file: str = ""
    shift: int = 0
    notes: list[MarkupNote] = field(default_factory=list)

    def refuse(self, site: markup_scan.Site | None,
               written: "Written | None") -> str:
        """Records a placeholder that has to prove itself, and says where.

        The line it reports is the author's and not the compiler's.
        ``#mode markup`` is cut out of the source before anything
        parses, the way ct3 cuts ``#unicode``, so every line after it
        has moved up and every placeholder is after it. Putting those
        lines back is the difference between a message that points at
        the placeholder and one that points above it.

        Args:
            site (markup_scan.Site|None): What the scan found, or None
                where it could not place the placeholder.
            written (Written|None): The placeholder, where the caller
                has one. The three writes that are not placeholders
                (#echo, the one-line #if, the result of a #call) pass
                what they know instead.

        Returns:
            str: The position for the message the render raises, as
            ``'file:line:column (position)'``, or the file alone where
            neither the scan nor the caller knows a position.
        """
        note = site.note if site is not None else MARKUP_UNPLACED
        if site is not None:
            line, column = site.line, site.column
        elif written is not None:
            line, column = written.line, written.column
        else:
            self.notes.append(MarkupNote(MARKUP_REFUSED, note, 0, 0))
            return "%s (%s)" % (self.file or "<template>", note)
        line += self.shift
        self.notes.append(MarkupNote(MARKUP_REFUSED, note, line, column))
        return "%s:%d:%d (%s)" % (self.file or "<template>", line, column,
                                  note)

    def url(self, site: markup_scan.Site) -> None:
        """Records a placeholder that is the whole head of a URL."""
        self.notes.append(MarkupNote(MARKUP_URL, site.note,
                                     site.line + self.shift, site.column))


def _markup() -> MarkupState | None:
    """Markup mode of the generate() call this is running inside."""
    return getattr(_ACTIVE, "markup", None)


# -- Names the generator bound itself --------------------------------
#
# A lookup that starts at a name the generator put there does not have
# to walk the search list for it. ``#for $r in $rows`` binds r, so
# "$r.name" resolves on r directly and NameMapper only has to find name
# on it. The fork's own compiler has done this since the scope work in
# P4 and it is worth 1.9x on a loop; without it here the module this
# layer writes renders slower than the one ct3 writes, which would make
# the whole exercise a step backwards.
#
# The rules are the compiler's, not new ones. Only a #for binds
# (Compiler.addFor), only a name with a dot is worth rewriting
# (_knownLocalBase), and a method starts with an empty stack because
# ct3 gives every method compiler its own.


def _scopes() -> list[tuple[str, ...]]:
    """The stack of names bound around the point being generated."""
    stack = getattr(_ACTIVE, "scopes", None)
    if stack is None:
        stack = []
        _ACTIVE.scopes = stack
    return stack


def _knows_local(name: str) -> bool:
    """Whether a lookup may start at a local rather than at the list.

    Needs a dot: the part before it is the local and the rest is what
    NameMapper resolves on it. A name without one *is* the local, and
    then there is nothing to save.
    """
    if "." not in name:
        return False
    base = name.split(".")[0]
    return any(base in scope for scope in _scopes())


def loop_targets(header: str) -> tuple[str, ...]:
    """The names a generated for statement binds.

    Read with Python's own parser rather than by splitting on " in ": a
    target can be a tuple, and the iterable can hold the word. Anything
    that does not parse binds nothing, and then nothing is rewritten.
    """
    try:
        made = ast.parse("%s:\n    pass\n" % header.rstrip(":"))
    except SyntaxError:
        return ()
    statement = made.body[0]
    if not isinstance(statement, ast.For):
        return ()
    return tuple(node.id for node in ast.walk(statement.target)
                 if isinstance(node, ast.Name))


@dataclass(frozen=True)
class Written:
    """A placeholder, as much of it as the error catcher needs.

    ``code`` is the Python the lookup became and is what a wrapper
    evaluates. ``raw`` is the template's own text for it, which is what
    ErrorCatchers.Echo writes in its place, and what the wrappers are
    keyed on. The position goes to ErrorCatchers.ListErrors, which
    records where each failure stood.

    ``start`` is the offset of the placeholder's token in the source,
    which is what ``ct4.markup.scan`` keys its sites on. It stays -1
    for the three writes that are not placeholders at all: #echo, the
    one-line #if and the value a #call returns. In markup mode those
    are refused, and -1 is how they say they have no site to look up.
    """

    code: str
    raw: str
    line: int
    column: int
    start: int = -1


@dataclass(frozen=True)
class Chunk:
    """One step of a placeholder's chain.

    ``name`` is a dotted run, ``autocall`` says whether NameMapper may
    call what it finds, and ``remainder`` is the call or subscript
    hanging off it, as Python with every placeholder inside it already
    resolved.
    """

    name: str
    autocall: bool
    remainder: str


class _Reader:
    """ct3's placeholder readers, over one placeholder's text.

    Mirrors four functions of Cheetah/Parser.py: getPlaceholder reads a
    placeholder, getCheetahVarNameChunks splits it into a chain, and
    getCallArgString and getExpressionParts read what hangs off the
    chain. The last two are kept apart here as they are there, because
    they do not agree: a line ending inside a call is copied and the
    same line ending inside a subscript is dropped, and the "$kw ="
    rewrite that turns an argument into a Python keyword argument
    exists in the call reader alone.

    The work happens on the token's own text and never on its children.
    lex.inner() scans the whole argument range for a dollar byte by
    byte with no idea of Python strings, so it invents nested
    placeholders that ct3 never resolves: in "$getVar('$anInt')" the
    child has to stay the six characters it is, and a corpus case says
    so.

    Everything it cannot read raises Unsupported, ct3's own ParseErrors
    included. Where ct3 would read on past the end of the token the
    reader simply stops, and the caller refuses what is left over.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.at = 0
        # ct3 turns useNameMapper off while it reads the target of a
        # "for" inside an expression, and back on after. It does that
        # with a setting rather than an argument, so it reaches every
        # reader that runs while it is off.
        self.name_mapper = True

    # -- position ----------------------------------------------------

    def done(self) -> bool:
        return self.at >= len(self.text)

    def peek(self, ahead: int = 0) -> str:
        """The character at the position, or "" past the end."""
        index = self.at + ahead
        return self.text[index] if 0 <= index < len(self.text) else ""

    def whitespace(self) -> str:
        """getWhiteSpace: blanks and tabs, never a line ending."""
        start = self.at
        while self.peek() and self.peek() in " \f\t":
            self.at += 1
        return self.text[start:self.at]

    def dotted_name(self) -> str:
        """getDottedName: names joined by dots, and no trailing dot."""
        start = self.at
        while not self.done():
            char = self.text[self.at]
            if char in lex.IDENT:
                self.at += 1
                continue
            if char == "." and self.peek(1) in lex.IDENT_START:
                self.at += 1
                continue
            break
        return self.text[start:self.at]

    def py_token(self) -> str:
        """getPyToken: one Python token, tokenize's own expression."""
        match = PY_TOKEN.match(self.text, self.at)
        if match is None or match.end() == self.at:
            raise Unsupported("cannot read Python at %r"
                              % self.text[self.at:self.at + 20])
        token = match.group()
        if token in TRIPLE:
            whole = TRIPLE[token].match(self.text, self.at)
            if whole is None:
                raise Unsupported("malformed triple-quoted string")
            self.at = whole.end()
            return whole.group()
        self.at = match.end()
        return token

    def transform(self, token: str) -> str:
        """transformToken, which has one case: the c'...' string.

        ct3 turns ``c"a $b c"`` into ``''.join(['a ',str(VFFSL(SL,
        "b",True)),' c'])``: the characters between the quotes are
        walked one at a time, a placeholder start hands over to
        getPlaceholder and comes back as str() of the lookup, and every
        run of other characters goes out through repr. The raw
        characters, not the evaluated string, which is why the repr is
        needed at all.

        An empty one is turned away. ct3 returns None for it and the
        token vanishes from the expression, which is nothing to be
        faithful to.
        """
        if token != "c" or not self.peek() or self.peek() not in "'\"":
            return token
        literal = self.py_token()
        try:
            # transformToken evaluates the token upper-cased, which is
            # a slip nobody fixed: a "\n" in the string is "\N" to
            # eval, the start of a named escape with no name, and ct3
            # dies of a SyntaxError right there. So does this, by the
            # same road, rather than render what ct3 never does.
            content = ast.literal_eval(literal.upper())
        except (SyntaxError, ValueError):
            raise Unsupported("a c'...' string ct3 cannot evaluate") \
                from None
        marker = 3 if literal[:3] in ('"""', "'''") else 1
        raw = literal[marker:-marker]
        if not raw or not content:
            raise Unsupported("an empty c'...' placeholder string")
        parts: list[str] = []
        run = ""
        inner = _Reader(raw)
        inner.name_mapper = self.name_mapper
        at = 0
        while at < len(raw):
            if raw[at] == "$" and lex.start_of(raw[at:]) is not None:
                if run:
                    parts.append(repr(run))
                    run = ""
                inner.at = at
                parts.append("str(%s)" % inner.placeholder())
                at = inner.at
                continue
            run += raw[at]
            at += 1
        if run:
            parts.append(repr(run))
        return "''.join([%s])" % ",".join(parts)

    # -- the readers -------------------------------------------------

    def chunks(self) -> list[Chunk]:
        """getCheetahVarNameChunks: the chain, from behind the dollar.

        The name carrying a bracket becomes a chunk of its own, so
        "$a.b.c[1]" splits into "a.b" and "c[1]" and not into
        "a.b.c[1]". Autocalling is off for a chunk that is called and
        on for one that is only subscripted.

        The loop head is ct3's ``self.peek() not in identchars + '.'``,
        and identchars holds no digits: a bare letter after a bracket
        opens another chunk, "$f(1)upper" included, while a digit ends
        the chain.
        """
        found: list[Chunk] = []
        while not self.done():
            remainder = ""
            autocall = True
            char = self.text[self.at]
            if char not in lex.IDENT_START and char != ".":
                break
            if char == ".":
                # The period goes: NameMapper does not need it.
                if self.peek(1) in lex.IDENT_START:
                    self.at += 1
                else:
                    break
            name = self.dotted_name()
            if not self.done() and self.text[self.at] in "([":
                if self.text[self.at] == "(":
                    remainder = self.call_arguments()
                else:
                    remainder = self.expression(enclosed=True)
                period = max(name.rfind("."), 0)
                if period:
                    found.append(Chunk(name[:period], autocall, ""))
                    name = name[period + 1:]
                if remainder.startswith("("):
                    autocall = False
            found.append(Chunk(name, autocall, remainder))
        return found

    def cheetah_var(self, plain: bool = False) -> str:
        """getCheetahVar: the dollar and the chain behind it.

        Only reached where a name follows the dollar. An enclosure and
        the two modifiers are a ParseError inside an expression, and
        the callers refuse them before they get here.
        """
        self.at += 1
        found = self.chunks()
        if not found:
            raise Unsupported("a dollar with no name behind it")
        if plain or not self.name_mapper:
            return _plain_expression(found)
        return _expression(found)

    def call_arguments(self) -> str:
        """getCallArgString: what a "(" opens, as Python.

        Whitespace and line endings are copied verbatim, a string
        literal is copied and never scanned for placeholders, a bare
        identifier is copied and not looked up, and a "$name" becomes a
        chain of its own.
        """
        if self.peek() != "(":
            raise Unsupported("expected a call argument list")
        self.at += 1
        bits = ["("]
        while True:
            if self.done():
                raise Unsupported("the call argument list does not close")
            char = self.text[self.at]
            if char in ")}]":
                # Only the bracket that opened it: ct3 raises for the
                # others, and a nested group never gets here because
                # the branch below hands it to the expression reader.
                if char != ")":
                    raise Unsupported("a %r closes a call argument list"
                                      % char)
                self.at += 1
                bits.append(")")
                break
            if char in " \t\f\r\n":
                self.at += 1
                bits.append(char)
            elif char == "$" and self.peek(1) in lex.IDENT_START:
                bits.append(self.keyword_or_lookup())
            elif char == "$":
                raise Unsupported("%r inside an expression"
                                  % self.text[self.at:self.at + 3])
            else:
                token = self.py_token()
                if token in ("{", "(", "["):
                    # A bracket that opens a value inside the argument
                    # list goes to the expression reader, which has no
                    # "$kw =" rewrite of its own. The asymmetry is
                    # ct3's, and it is why "$a(f($x=1))" generates
                    # Python that does not compile.
                    self.at -= 1
                    token = self.expression(enclosed=True)
                bits.append(self.transform(token))
        return "".join(bits)

    def keyword_or_lookup(self) -> str:
        """A "$name" in an argument list, and ct3's "$kw =" rewrite.

        A single "=" behind the name makes it a Python keyword
        argument: the dollar and the whole NameMapper call go and the
        dotted name is written on its own. A "==" is not that case, and
        the value behind the "=" is resolved as usual, so
        "$aFunc($arg=$aMeth(1))" keeps the lookup on the right. The
        whitespace between the name and the "=" is read once and
        written once, which is what ct3's WS = self.getWhiteSpace()
        does.
        """
        start = self.at
        code = self.cheetah_var()
        blanks = self.whitespace()
        if self.peek() != "=":
            return code + blanks
        token = self.py_token()
        if token == "=":
            end = self.at
            self.at = start
            code = self.cheetah_var(plain=True)
            self.at = end
        return code + blanks + token

    def expression(self, enclosed: bool = False,
                   enclosures: list[str] | None = None,
                   break_at: tuple[str, ...] = ()) -> str:
        """getExpressionParts: what a "[" opens, and an enclosure's tail.

        Five things it does differently from the call reader. A line
        ending inside a bracket is dropped rather than copied, and a
        backslash in front of one takes both away. A bare identifier
        followed by a "(" pulls the call in through the call reader.
        The names between a "for" and its "in" are written plainly. And
        it keeps reading across a bracket group that closes back to
        nothing, because the branch that opens a bracket is tested
        before the one that ends the expression: that is why the
        remainder of "$a[1](2)" is the whole "[1](2)".
        """
        if enclosures is None:
            enclosures = []
        bits: list[str] = []
        while True:
            if self.done():
                if enclosures:
                    raise Unsupported("the expression does not close")
                break
            char = self.text[self.at]
            if char in "{([":
                self.at += 1
                enclosures.append(char)
                bits.append(char)
            elif enclosed and not enclosures:
                break
            elif char in "])}":
                if not enclosures or enclosures[-1] != OPENING[char]:
                    raise Unsupported("a %r closes nothing" % char)
                self.at += 1
                enclosures.pop()
                bits.append(char)
            elif char in " \f\t":
                bits.append(self.whitespace())
            elif char == "#" and not enclosures \
                    and (self.at == 0 or self.text[self.at - 1] != "\\"):
                # The token that ends a directive. Inside a placeholder
                # it means the token holds more than ct3 read, and the
                # caller refuses what is left over.
                break
            elif char == "\\" and self.at + 1 < len(self.text):
                match = lex.EOL.match(self.text, self.at + 1)
                if match is None:
                    raise Unsupported("a backslash with no line ending")
                # Both the backslash and the ending go.
                self.at = match.end()
            elif char in "\r\n":
                if not enclosures:
                    break
                # Dropped and not copied: this is where the two readers
                # part company, and a kept ending would leave a bare
                # line break at the top level of the expression.
                self.at += 1
            elif char == "$" and self.peek(1) in lex.IDENT_START:
                bits.append(self.cheetah_var())
            elif char == "$":
                raise Unsupported("%r inside an expression"
                                  % self.text[self.at:self.at + 3])
            else:
                before = self.at
                token = self.py_token()
                if not enclosures and break_at and token in break_at:
                    self.at = before
                    break
                bits.append(self.transform(token))
                if IDENT_RE.match(token):
                    if token == "for":
                        bits.append(self.loop_targets())
                    else:
                        bits.append(self.whitespace())
                        if self.peek() == "(":
                            bits.append(self.call_arguments())
        return "".join(bits)

    def loop_targets(self) -> str:
        """What stands between a "for" and its "in", written plainly.

        ct3 reads it with useNameMapper off, so "$a([$y for $y in $b])"
        binds a plain y and looks up only b.
        """
        keep = self.name_mapper
        self.name_mapper = False
        try:
            return self.expression(break_at=("in",))
        finally:
            self.name_mapper = keep

    def placeholder(self) -> str:
        """getPlaceholder: a top-level placeholder, enclosure and all."""
        self.at += 1
        opener = ""
        if self.peek() and self.peek() in "({[":
            opener = self.text[self.at]
            self.at += 1
            self.whitespace()
        if self.peek() not in lex.IDENT_START:
            # getPlaceholder's else branch: the enclosure holds an
            # expression and not a name. The whole of it is read as
            # Python, with the placeholders inside it resolved, and the
            # closer is sliced off. The opener is gone too, so "$(a+b)"
            # writes "a + b" and not "(a + b)".
            if not opener:
                raise Unsupported("placeholder %r" % self.text)
            made = self.expression(enclosed=True, enclosures=[opener])
            if made.endswith(lex.CLOSING[opener]):
                made = made[:-1]
            return made
        found = self.chunks()
        if not found:
            raise Unsupported("placeholder %r" % self.text)
        made = (_expression(found) if self.name_mapper
                else _plain_expression(found))
        if opener:
            # The blanks behind the chain really do end up in the
            # generated expression: "$( a + 1 )" ends in a space.
            made += self.whitespace()
            if self.peek() == ",":
                # ct3 reads what follows as arguments for the filter
                # and leaves the value the chain alone. There is no way
                # to pass them here, and read as expression text the
                # comma would quietly build a tuple.
                raise Unsupported("filter arguments in a placeholder")
            if self.peek() == lex.CLOSING[opener]:
                self.at += 1
            else:
                rest = self.expression(enclosed=True, enclosures=[opener])
                # Exactly one closer comes off, which is what ct3
                # slices; anything else leaves the brackets unbalanced.
                if rest.endswith(lex.CLOSING[opener]):
                    rest = rest[:-1]
                made += rest
        return made


def chunks_of(text: str) -> list[Chunk]:
    """A placeholder's chain, split the way ct3 splits it.

    Called as ``chunks_of("$a.b.c[1].d().x.y.z")``. The rule is
    measured off ct3 rather than taken from its docstring, which is out
    of date.

    Raises:
        Unsupported: where the text is not a chain and nothing else.
            That includes the enclosure forms, the two modifiers, and a
            text the chain does not reach the end of.
    """
    if not text.startswith("$") or text[1:2] not in lex.IDENT_START:
        raise Unsupported("placeholder %r" % text)
    reader = _Reader(text)
    reader.at = 1
    found = reader.chunks()
    if not found or not reader.done():
        raise Unsupported("placeholder %r" % text)
    return found


def placeholder_source(text: str, name_mapper: bool = True) -> str:
    """The Python ct3 writes for a placeholder where it stands.

    Called as ``placeholder_source("$a.b($c)")`` with the raw text of a
    PLACEHOLDER token, the modifiers already taken off.

    Args:
        text (str): the placeholder's own source.
        name_mapper (bool): whether the names are looked up. False is
            what ct3 reads an assignment target with, and it reaches
            all the way in: "$d[$k]" on the left of a "#set" is "d[k]",
            not "d[VFFSL(SL,'k',True)]".

    Raises:
        Unsupported: where ct3 reads the text differently, or not at
            all. Text left over at the end is such a case: "$a(1)[2]"
            is one token here and two things in ct3, a placeholder
            "$a(1)" and the plain output text "[2]".
    """
    if MODIFIERS.match(text):
        raise Unsupported("placeholder %r" % text)
    reader = _Reader(text)
    reader.name_mapper = name_mapper
    made = reader.placeholder()
    if not reader.done():
        raise Unsupported("placeholder %r" % text)
    return made


def argument_source(text: str) -> str:
    """The Python ct3 writes for a placeholder in a directive argument.

    A different reader, and it has to be one. ct3 reads a directive's
    argument with getExpression and never with getPlaceholder, and
    inside an expression a placeholder is a bare chain: an enclosure or
    a modifier raises a ParseError there, so "#echo ${b}" is a template
    ct3 refuses outright.
    """
    return _expression(chunks_of(text))


# Node kinds that carry no output at all.
SILENT_KINDS = frozenset({lex.COMMENT, lex.BLOCK_COMMENT})

# The frame the generated body is put into. Parsed, not assembled, so
# that whichever Python runs this fills in its own fields.
#
# A class and not a function, because #def has to become a method and
# because $self and $getVar resolve against the instance, which ct3
# puts in the template's own search list.
SKELETON = """\
from time import time as currentTime
from Cheetah import ErrorCatchers
from Cheetah.DummyTransaction import DummyTransaction
from Cheetah.NameMapper import NotFound
from Cheetah.NameMapper import valueForName as VFN
from Cheetah.NameMapper import valueFromFrameOrSearchList as VFFSL
from Cheetah.Template import Template
from Cheetah.Version import Version as __CHEETAH_version__
from Cheetah.Version import VersionTuple as __CHEETAH_versionTuple__


class %s(Template):
    pass
""" % CLASS

# What every generated method opens with, ct3's prologue word for word.
# The transaction is its own because a method can be called on its own,
# and then it collects into a throwaway response and returns the text.
#
# Three slots: the name, the parameter list, and the name of the keyword
# dictionary the transaction arrives in. ct3 adds a **KWS of its own
# only where the method does not already have one
# (AutoMethodCompiler.cleanupState, line 1172), and reads the
# transaction out of whichever it ends up with. So "#def m($**kw)" is
# "def m(self, **kw)" and reads kw, not a second dictionary that would
# not even be valid Python.
PROLOGUE = """\
def %%s(self, %%s):
    trans = %%s.get("trans")
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


# The same frame for the one method ct3 signs differently. A method
# called "respond" takes its transaction as an argument rather than out
# of a keyword dictionary, and ct3 decides that on the name alone:
# AutoMethodCompiler._useKWsDictArgForPassingTrans, line 1146. It
# matters outside the generated module, because Template.respond and
# _handleCheetahInclude both call it positionally.
MAIN_PROLOGUE = """\
def %%s(self, %%strans=None):
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

# What ct3 puts in a generated class besides its methods. Without the
# __init__ a template compiled onto a baseclass that is itself a
# generated template is never initialised, because that base's own
# __init__ runs and stops at its _CHEETAH__instanceInitialized guard.
# Zero-argument super() rather than ct3's super(Name, self): the same
# call, and it does not have to be told the class name.
INIT = """\
def __init__(self, *args, **KWs):
    super().__init__(*args, **KWs)
    if not self._CHEETAH__instanceInitialized:
        cheetahKWArgs = {}
        allowed = "searchList namespaces filter filtersLib errorCatcher"
        for key, value in KWs.items():
            if key in allowed.split():
                cheetahKWArgs[key] = value
        self._initCheetahInstance(**cheetahKWArgs)
"""


# The class attributes ct3 writes, and every one of them is read
# somewhere outside the generated module.
#
# _CHEETAH__instanceInitialized is what the __init__ above tests, and a
# template compiled onto a baseclass outside Cheetah's own hierarchy
# has nowhere else to inherit it from. ct3's own test suite compiles
# against baseclass=dict, so this is not a corner.
#
# _CHEETAH_versionTuple keeps Template.__init__ from going to the
# module for a version number, and _mainCheetahMethod_for_<class> is
# what Template._handleCheetahInclude reads to find out what to call.
ATTRIBUTES = """\
_CHEETAH__instanceInitialized = False
_CHEETAH_version = __CHEETAH_version__
_CHEETAH_versionTuple = __CHEETAH_versionTuple__
_mainCheetahMethod_for_%s = %r
"""


# What ct3 writes after the class. A template compiled onto a baseclass
# that is not a Template has none of Cheetah's own methods, and this
# grafts them on. ct3's own test suite compiles every syntax case a
# second time against baseclass=dict, so a module that leaves this out
# fails 338 corpus cases at the first instantiation.
PLUMBING = """\
if not hasattr(%s, "_initCheetahAttributes"):
    getattr(%s, "_CHEETAH_templateClass",
            Template)._addCheetahPlumbingCodeToClass(%s)
"""


def _class_attributes(class_name: str, main: str) -> list[ast.stmt]:
    """The __init__ and the attributes ct3 puts beside the methods."""
    made = ast.parse("%s\n%s" % (INIT, ATTRIBUTES % (class_name, main)))
    return made.body


def _plumbing(class_name: str) -> list[ast.stmt]:
    """The call that gives a foreign baseclass Cheetah's own methods."""
    return ast.parse(PLUMBING % (class_name, class_name, class_name)).body


# The keyword dictionary a parameter list already declares, if it does.
# ct3 looks for exactly this and stops at the first one
# (AutoMethodCompiler.cleanupState, line 1174).
OWN_KWARGS = re.compile(r"(?:^|,)\s*\*\*([A-Za-z_][A-Za-z_0-9]*)")


def _method(name: str, arguments: str,
            body: list[ast.stmt]) -> ast.stmt:
    """One generated method, with the body in ct3's frame."""
    if name == MAIN:
        if "*" in arguments or re.search(r"\btrans\b", arguments):
            # ct3 turns streaming off for these and writes a different
            # body: the transaction is always a throwaway one. Rare
            # enough to refuse rather than reproduce.
            raise Unsupported("#implements %s with %r" % (name, arguments))
        text = MAIN_PROLOGUE % (name, arguments)
    else:
        own = OWN_KWARGS.search(arguments)
        if own is None:
            text = PROLOGUE % (name, arguments + "**KWS", "KWS")
        else:
            # The list already ends in one, so no second dictionary and
            # no trailing comma: "def m(self, **kw,)" does not parse.
            text = PROLOGUE % (name, arguments.rstrip(" ,"), own.group(1))
    try:
        made = ast.parse(text).body[0]
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
    """The Python of a template, and the source it came from.

    ``markup`` is None for a text-mode template and carries what markup
    mode decided for every placeholder otherwise. ``ct4.check`` reads
    its notes rather than working the same question out a second time,
    so what an author is warned about and what the render does are the
    same list by construction.
    """

    code: str
    module: ast.Module
    class_name: str = CLASS
    markup: MarkupState | None = None

    def compile(self) -> Any:
        """The template class, ready to be given a search list."""
        namespace: dict[str, Any] = {}
        exec(compile(self.module, "<ct4>", "exec"), namespace)
        return namespace[self.class_name]


# Compiler settings this layer knows it does not honour. It reads
# none of them, so any that is set has to be refused: two of them
# change what ct3 renders and 24 corpus cases were coming out wrong
# behind a skip in the test that was supposed to catch exactly that.
def _before_breakpoint(source: str) -> str:
    """The source up to a ``#breakpoint``, which ends the parse.

    "Tells the parser to stop parsing at this point and completely
    ignore everything else", and eatBreakPoint means it: what follows
    is not parsed at all. Not the rest of the text, not a #def, not an
    #end that would have closed an open block, which is why a
    #breakpoint inside one is a ParseError there and a StructureError
    here.

    Cut before the tree is built rather than while it is walked,
    because the tree would otherwise go on to read what ct3 never saw.
    """
    if "#breakpoint" not in source:
        return source
    for token in lex.tokens(source):
        if token.kind == lex.DIRECTIVE and token.text == "#breakpoint":
            return source[:token.start]
    return source


def _refuse_settings(settings: Any) -> None:
    """Turns away a template whose compiler settings change ct3.

    Every setting, not a chosen few. gobbleWhitespaceAroundMultiLine-
    Comments and allowWhitespaceAfterDirectiveStartToken are the two
    the corpus exercises, and enumerating from a corpus is how a
    blind spot becomes a rule. When a setting is honoured, take it off
    this list and say which.

    Raises:
        Unsupported: where any setting is given.
    """
    if settings:
        raise Unsupported(
            "compiler settings change how ct3 parses and this layer "
            "reads none of them: %s" % ", ".join(sorted(settings)))


def supports(source: str, settings: Any = None) -> bool:
    """Whether this layer can generate code for the template.

    A markup-mode template the scan refuses counts as unsupported here,
    even though the compile itself does not fall back for it. The two
    questions are different: this one is asked by the test bench about
    what the layer can carry, and a refused file is one it cannot.
    """
    try:
        generate(source, settings)
    except (Unsupported, tree.StructureError, markup_scan.ScanRefused):
        return False
    return True


def generate(source: str, settings: Any = None,
             class_name: str = CLASS, base_class: str | None = None,
             main_method: str | None = None, *,
             mode: str = TEXT_MODE, file: str = "") -> Generated:
    """Python for a template.

    Args:
        class_name (str): What the generated class is called. ct3 hands
            its compiler a mainClassName and then pulls that name out
            of the module it execs, so a module meant to stand in for
            ct3's has to use it.
        base_class (str|None): The name ct3 will have bound in the
            module before it execs it, from the baseclass argument of
            Template.compile. An #extends in the template overrides it,
            the way it overrides ct3's.
        main_method (str|None): What the template's own body is called.
            Also overridden by #extends and #implements.
        mode (str): What the caller expects, not what it decides. The
            mode is read out of the source and out of nothing else, so
            "markup" here only asserts that the declaration is there
            and is refused when it is not. The default is text, and
            text is what a source without the declaration gets whatever
            is passed.
        file (str): The template's path, where the caller has one. It
            reaches nothing but the message a refused placeholder
            raises in markup mode.

    Raises:
        Unsupported: where the template uses something this layer does
            not understand yet, and where markup mode is asked for a
            source that does not declare it.
        tree.StructureError: where the template is not well formed.
        markup_scan.ScanRefused: in markup mode, where the scan cannot
            be trusted over the file. Nothing is escaped then and the
            compile fails with the reason.
    """
    _refuse_settings(settings)
    markup = markup_mode.declared(source)
    if mode not in (TEXT_MODE, MARKUP_MODE):
        raise Unsupported("no such mode: %r" % mode[:40])
    if mode == MARKUP_MODE and not markup:
        raise Unsupported("markup mode without a %r line"
                          % markup_mode.MODE_LINE)
    declared = source
    source = _before_breakpoint(_preprocess(source))
    shift = _lines_cut(declared, source) if markup else 0
    root = tree.parse(source)
    _refuse_raw_in_short_form(source, root)
    shape = _class_shape(root, base_class, main_method)
    # The imports #extends synthesises stand with the template's own.
    hoisted: list[ast.stmt] = list(shape.imports)
    if markup:
        # Before the placeholders are generated, because what each one
        # becomes is decided from the scan.
        hoisted[:0] = ast.parse(MARKUP_IMPORT).body
    # Scanned before anything is put on the thread, so that a refused
    # file leaves no state of this call standing behind it.
    state = MarkupState(_scanned(root, shift), file, shift) if markup else None
    methods: list[ast.stmt] = []
    # Torn down whatever happens: a refusal halfway through must not
    # leave a catcher standing for the next template on this thread.
    previous = _catcher()
    previous_markup = _markup()
    _ACTIVE.catcher = Catcher(methods)
    _ACTIVE.markup = state
    _ACTIVE.scopes = []
    # What "#super" names: the class being generated and the method
    # the directive stands in, which is the main one until a #def
    # says otherwise.
    _ACTIVE.class_name = class_name
    _ACTIVE.method = shape.main
    try:
        body = _statements(_pieces(root, source, hoisted, methods))
    finally:
        _ACTIVE.catcher = previous
        _ACTIVE.markup = previous_markup
        _ACTIVE.scopes = []
    module = ast.parse(SKELETON)
    # Before the class, the way ct3 puts them at module level. An
    # import inside a method would run on every render.
    module.body[-1:-1] = hoisted
    made = module.body[-1]
    assert isinstance(made, ast.ClassDef)
    made.name = class_name
    made.bases = [_parsed(name) for name in shape.bases]
    made.body = (_class_attributes(class_name, shape.main) + methods
                 + [_method(shape.main, shape.arguments, body)])
    # At the very head, in front of the skeleton's own imports and in
    # front of what the template imported: ct3's preamble stands there
    # too, and a template that binds one of these names itself never
    # gets one from here.
    module.body[:0] = _preamble_names(module, source, class_name)
    _refuse_unbound_bases(module, shape.bases, base_class)
    # After the guards, not before: the plumbing call names the class,
    # and the preamble guard asks who reaches for a name. It is asking
    # about the template, and this line is not the template's.
    module.body.extend(_plumbing(class_name))
    ast.fix_missing_locations(module)
    return Generated(_with_origins(ast.unparse(module), module),
                     module, class_name, state)


# Where a generated statement came from, as (line, column) in the
# template. Set on the ast node, because that is the only thing that
# survives from the piece that built it to the module that is unparsed.
ORIGIN = "ct4_origin"


def _at(statement: ast.stmt, line: int, column: int) -> ast.stmt:
    """Records where in the template a statement came from.

    The first writer wins. A block writes its own statement here and
    its body has already been through this on the way up, so an inner
    statement keeps the position of the placeholder or directive it
    really came from and only what has none takes the block's.
    """
    if not hasattr(statement, ORIGIN):
        setattr(statement, ORIGIN, (line, column))
    return statement


def _with_origins(code: str, module: ast.Module) -> str:
    """Writes ct3's origin comments behind the generated statements.

    ``# generated from line 4, col 3``. ct3's compiler emits these as
    it writes the source, and everything downstream reads them:
    ``ct4.trace`` turns a traceback into a place in the template, and
    without them a runtime error names a file that does not exist on
    disk and nothing else. ``ast.unparse`` writes no comments, so they
    have to be put back.

    Which generated line belongs to which statement is recovered
    structurally rather than guessed. Unparsing and parsing again gives
    a tree of the same shape as the one that was unparsed, so the two
    can be walked side by side and each generated line read off the
    node that stands where the origin was recorded.

    That is an assumption about ``ast.unparse``, and it is checked
    rather than trusted: the walk gives up on the first pair of nodes
    that do not match and the code goes out without comments. A missing
    origin costs a line number. A wrong one sends its reader to the
    wrong line of their template, which is worse than none.
    """
    try:
        found = _origin_lines(module, ast.parse(code))
    except (SyntaxError, _Mismatch):
        return code
    if not found:
        return code
    lines = code.splitlines()
    for number, (line, column) in found.items():
        if 1 <= number <= len(lines):
            lines[number - 1] += " # generated from line %d, col %d" % (
                line, column)
    return "\n".join(lines) + "\n"


class _Mismatch(Exception):
    """The unparsed code did not parse back to the same shape."""


# The fields a statement holds statements in. Descending through these
# alone reaches every node an origin can sit on, and it skips the
# expressions, which is nearly all of the tree: over the corpus the
# walk cost more than the reparse before it was cut down to these.
BODIES = ("body", "orelse", "finalbody", "handlers")


def _sub_statements(node: ast.AST) -> list[ast.AST]:
    found: list[ast.AST] = []
    for name in BODIES:
        held = getattr(node, name, None)
        if isinstance(held, list):
            found.extend(held)
    return found


def _origin_lines(made: ast.Module,
                  back: ast.Module) -> dict[int, tuple[int, int]]:
    """Generated line number to the template position recorded on it.

    Walked with a stack rather than by recursion: a template nests as
    deeply as its author nested it, and a page with two hundred nested
    conditionals should generate slowly, not raise RecursionError.

    Only the statements are walked, and that is enough for the pairing
    to be sound. ``ast.unparse`` writes the statements of a body in
    order and one for one, so the nth statement of a body in the tree
    that went in is the nth statement of that body in the tree that
    came back. An expression that unparsed differently could not move
    a statement.
    """
    found: dict[int, tuple[int, int]] = {}
    stack: list[tuple[ast.AST, ast.AST]] = [(made, back)]
    while stack:
        one, other = stack.pop()
        if type(one) is not type(other):
            raise _Mismatch
        origin = getattr(one, ORIGIN, None)
        number = getattr(other, "lineno", None)
        if origin is not None and number is not None:
            found.setdefault(number, origin)
        left = _sub_statements(one)
        right = _sub_statements(other)
        if len(left) != len(right):
            raise _Mismatch
        stack.extend(zip(left, right))
    return found


# Every name the guard below asks about, as one search over the source.
# A name can only be reached in the generated module if it stood in the
# template: an #extends base, a placeholder, a PSP body. So a source
# that mentions none of them cannot trip the guard, and the walk over
# the whole module can be skipped. That walk was the single largest
# item in generating the 390 skin templates.
PREAMBLE_RE = re.compile(r"\b(?:%s)\b"
                         % "|".join(sorted(PREAMBLE, key=len, reverse=True)))


def _preamble_names(module: ast.Module, source: str,
                    class_name: str = CLASS) -> list[ast.stmt]:
    """The imports a template needs from ct3's preamble, or a refusal.

    Both namespaces, not just ct3's. A name only ct3's module has
    resolves there and raises here; a name only this one has does the
    reverse, and the reverse is worse, because the template renders
    and the difference shows up as output.

    A name ct3 binds by importing is not a difference between the two
    modules at all, only a difference between what they happen to have
    imported. Those are handed back as import statements to put at the
    head of the module, and PREAMBLE_IMPORTS says which they are. The
    rest is what ct3's preamble computes, and that still refuses.

    Checked on the finished module rather than while it is built,
    because that is the one place where both halves of the question are
    answered: which names the code reaches for, and which ones the
    template's own #import statements bind. ``#import os.path`` then
    ``$os.path.exists('.')`` resolves the same in both and stays.

    A local binding is not an exemption. ct3 looks in the frame first,
    so a name bound in the method that uses it resolves the same
    either way; a name bound in another method does not, and that is
    the case this is here for.
    """
    if not PREAMBLE_RE.search(source) and class_name not in source:
        return []
    bound: set[str] = set()
    reached: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            reached.add(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "VFFSL":
            # The name a lookup starts at, which VFFSL resolves against
            # the module globals when nothing else has it.
            looked_up = node.args[1]
            if isinstance(looked_up, ast.Constant):
                reached.add(str(looked_up.value).split(".")[0])
    wanted = sorted(reached & PREAMBLE - bound)
    for name in wanted:
        if name not in PREAMBLE_IMPORTS:
            raise Unsupported(
                "%r is a name ct3's own module computes" % name)
    for name in sorted(reached & {class_name} - bound):
        raise Unsupported(
            "%r is a name this module carries and ct3's does not" % name)
    return [ast.parse(PREAMBLE_IMPORTS[name]).body[0] for name in wanted]


def render(source: str, search_list: Sequence[Any],
           output_filter: Any = None, settings: Any = None,
           *, file: str = "") -> str:
    """Generates, runs, and returns what the template produces.

    Args:
        file (str): The template's path, where the caller has one. It
            reaches nothing but the message a refused placeholder
            raises in markup mode.
    """
    klass = generate(source, settings, file=file).compile()
    keywords = {"filter": output_filter} if output_filter else {}
    template = klass(searchList=list(search_list), **keywords)
    try:
        text: str = str(template.respond())
    finally:
        template.shutdown()
    return text


def _scanned(root: tree.Node, shift: int) -> dict[int, markup_scan.Site]:
    """The scan, with a refusal made to name the author's line.

    Every caller of a refusal reads it as a place in the file the
    author edits, and one of them is ``ct4.build``, which catches
    whatever a render raises and has no way to know that the compiler
    parsed a source one line shorter than the file. So the correction
    happens once, here, and what leaves this module is already right.
    """
    try:
        return markup_scan.scan(root)
    except markup_scan.ScanRefused as refused:
        raise markup_scan.ScanRefused(refused.line + shift, refused.column,
                                      refused.reason) from None


def _lines_cut(before: str, after: str) -> int:
    """How many lines the preprocessing took out of the source.

    Every one of them stands at the top, ahead of anything that writes:
    the ``#mode markup`` declaration is on the first line that is not a
    comment, and ``#unicode`` in front of that or right behind it. So
    adding this to a line read off the parsed source gives the line in
    the author's file, for every placeholder there is.
    """
    return len(before.splitlines()) - len(after.splitlines())


def preparsed(source: str) -> tuple[tree.Node, int]:
    """The tree the compiler builds, and the lines it cut to get there.

    For a caller that has to walk the same template and point at the
    same lines: ``ct4.check`` looks for ``#include`` in this tree and
    raises every line it reports by the second value. It goes through
    the compiler's own preprocessing, so the two cannot end up reading
    different bytes or counting different lines.

    Args:
        source (str): The template as it stands in the file, with the
            declaration line still in it.

    Returns:
        tuple[tree.Node, int]: The parsed template, and how many lines
        the preprocessing cut, which is what a line read off that tree
        has to be raised by to name the author's file.

    Raises:
        tree.StructureError: where the template is not well formed.
    """
    body = _preprocess(source)
    return tree.parse(body), _lines_cut(source, body)


def _preprocess(source: str) -> str:
    """What ct3 does to a template before it parses it.

    ``#mode markup`` is cut here for the same reason and in the same
    place. It is no directive either: registering the name would leave
    ct3's parser spinning on a line it has no eater for, and
    ``lex.directive_names()`` reads ct3's table at call time, so the
    registration would move both engines at once and every differential
    instrument would compare two changed engines. A source that does
    not declare markup mode comes back from that step unchanged, which
    is why text mode cannot see it.

    ``#unicode`` is no directive at all: ct3 finds the line with a
    regular expression and cuts it out. The same pattern is used here,
    so the two cannot disagree about what counts as one.

    ``#encoding`` is found by a regular expression as well, and its
    line is *not* cut out: it reaches the parser a second time as a
    real directive. The two mechanisms have different trigger
    conditions, so a source can be put through the encoding step
    without holding a directive at all. ``#  encoding : utf-8`` is such
    a line, and ct3 writes it out as ordinary text.

    Raises:
        Unsupported: where #unicode and #encoding stand together, which
            ct3 means to refuse and instead dies of a RecursionError
            while it formats the error, and where the encoding step
            would not leave the source as it is.
    """
    from Cheetah.Parser import encodingDirectiveRE, unicodeDirectiveRE

    source = markup_mode.strip(source)
    if unicodeDirectiveRE.search(source):
        if encodingDirectiveRE.search(source):
            raise Unsupported("#encoding and #unicode together")
        without: str = unicodeDirectiveRE.sub("", source)
        return without
    found = encodingDirectiveRE.search(source)
    if found is not None and not _encoding_is_transparent(source,
                                                         found.group(1)):
        raise Unsupported("#encoding %r changes the source"
                          % found.group(1)[:40])
    return source


def _encoding_is_transparent(source: str, name: str) -> bool:
    """Whether ct3's #encoding step gives the source back unchanged.

    Before it parses anything ct3 replaces the whole template with
    ``eval(repr(source).encode("ascii", "backslashreplace")
    .decode(name))`` (Cheetah/Compiler.py, ModuleCompiler.__init__).
    The bytes handed to the decode are ASCII by construction and eval
    undoes repr, so the decode is the only step that can change a
    character. Where the codec reads those bytes the way ASCII does,
    the whole round trip is the identity and ct4 can leave the source
    alone; where it does not, ct3 goes on to parse something else and
    this layer has nothing to be faithful to.

    Decided by comparison and not by running ct3's eval, which would
    evaluate whatever the codec made of the template: with
    ``#encoding utf-7`` a decode can synthesise a quote and close the
    string literal eval is given.

    Against the ASCII reading of those bytes, and not against
    ``repr(source)``: repr leaves a printable non-ASCII character where
    it stands while backslashreplace escapes it, so the two differ for
    a template holding one even though the round trip is the identity.
    Seventeen corpus templates would be refused for nothing.
    """
    raw = repr(source).encode("ascii", "backslashreplace")
    try:
        return raw.decode(name) == raw.decode("ascii")
    except (LookupError, UnicodeError):
        # An unknown name, a codec that is not a text codec at all, and
        # one that cannot read ASCII. ct3 raises the same two.
        return False


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
# The body of a #raw. Written out like text, but kept apart from it,
# because the whitespace rules must not reach into one: ct3 would
# truncate a raw body like any other pending chunk, and this layer
# refuses that case rather than working out where its chunks fall.
RAW_PIECE = "raw"
# Writes nothing. It marks the point where ct3 called commitStrConst,
# which flushes its pending text and puts it out of reach of the next
# handleWSBeforeDirective. Every statement-producing directive commits
# through addChunk, so a STMT_PIECE is already such a mark; this one is
# for the constructs that commit and produce no statement, which are
# #slurp and the two kinds of comment. Without it "L#slurp" followed by
# a #slurp on a clear line looks like an indent drop over an L that ct3
# had already written out.
BARRIER_PIECE = "barrier"

# What ct3's addStop writes, word for word. Not "return" alone: a
# method called on its own collects into a throwaway response and hands
# back the text, and #stop has to hand back the same thing the method's
# own ending does.
STOP_RETURN = 'return _dummyTrans and trans.response().getvalue() or ""'

# Directives that only announce a branch of the block they sit in.
BRANCHES = ("else", "elif")


def _pieces(root: tree.Node, source: str,
            hoisted: list[ast.stmt],
            methods: list[ast.stmt]) -> list[tuple[str, Any]]:
    return _pieces_of(root.children, source, hoisted, methods, top=True)


def _pieces_of(nodes: Sequence[tree.Node], source: str,
               hoisted: list[ast.stmt], methods: list[ast.stmt],
               top: bool = False,
               escaped: list[str] | None = None) -> list[tuple[str, Any]]:
    """The output of a template, as text and values in order.

    A comment writes nothing, but it decides what happens to the
    whitespace around it, and the two kinds decide differently. Both
    rules are ct3's, read off eatComment and eatMultiLineComment.

    Walked by index rather than by iteration, because a PSP block is
    not a node: its opening token and its ``<%end%>`` are two siblings
    in this list, and everything between them is the body.

    Args:
        escaped (list[str]|None): Where these nodes are the body of a
            block, the caller's place to receive the line ending the
            closing #end tag leaves behind. That ending is written
            after the block, not inside it, because ct3 has closed the
            block before the text reaches the compiler. Without it a
            "#for" whose "#end for" shares a line with output puts a
            line ending into every turn of the loop.
    """
    out: list[tuple[str, Any]] = []
    # Set by a block comment: the whitespace up to the end of the line
    # is still to be taken off whatever comes next, and the flag says
    # whether the line ending goes with it. Carried rather than applied
    # to the tree, because the tree has to stay what the layer below
    # built: it is the thing that writes back to the source.
    pending: bool | None = None
    # The node whose pieces are still to be given their origin, and
    # where in "out" they start. Stamped one turn late, because the
    # body below leaves through a dozen different "continue"s and a
    # single place that runs for every node is worth more than a dozen
    # calls that have to stay in step with them.
    stamping: tree.Node | None = None
    mark = 0
    index = 0
    # "#@testdecorator" waiting for the #def it belongs to. ct3 keeps
    # the same list on the compiler and hands it to the next method
    # compiler it spawns; here the definition takes it and empties it.
    decorators: list[str] = []
    # eatDecorator ends with getWhiteSpace(), and by then it has eaten
    # its own line ending, so what that call takes is the indent of the
    # line the #def stands on. Without it "  #@d\n  #block b: x" keeps
    # two blanks ct3 ate, which is two bytes and the only thing the
    # perturbation run found wrong about decorators.
    swallow_indent = False
    while index < len(nodes):
        node = nodes[index]
        index += 1
        if stamping is not None:
            _stamp(out, mark, stamping)
        stamping, mark = node, len(out)
        if pending is not None:
            gobble = pending
            pending = None
            if node.kind == lex.TEXT:
                text = _without_trailing_space(node.text(), gobble)
                if text:
                    out.append((TEXT_PIECE, text))
                continue
        if swallow_indent:
            swallow_indent = False
            if node.kind == lex.TEXT:
                text = node.text().lstrip(" \t\f")
                if text:
                    out.append((TEXT_PIECE, text))
                continue
        if node.kind == lex.COMMENT:
            _line_comment(node, source, out)
            out.append((BARRIER_PIECE, ""))
        elif node.kind == lex.BLOCK_COMMENT:
            pending = _block_comment(node, source, out)
            out.append((BARRIER_PIECE, ""))
        elif node.kind == tree.BLOCK:
            if node.name in ("def", "block"):
                after: list[str] = []
                call = _definition(node, source, hoisted, methods,
                                   _definition_line(node, source, out),
                                   after, decorators)
                decorators = []
                if call is not None:
                    out.append((STMT_PIECE, call))
                for text in after:
                    out.append((TEXT_PIECE, text))
                continue
            if node.name == "raw":
                # Read off the source from end to end, its own closing
                # directive included, so its children are not walked.
                _raw_block(node, source, out)
                continue
            if node.name == "errorCatcher":
                _error_catcher(node, source, hoisted, methods, out)
                continue
            if node.name in ("filter", "call", "cache", "capture", "i18n"):
                # Several statements around the body rather than one
                # block, and the tags decide about their lines before
                # any of them is written.
                _region(node, source, hoisted, methods, out)
                continue
            # The two line endings a block's tags leave behind fall on
            # opposite sides of it. The opening tag's is body, because
            # ct3 has written the loop header before the text arrives;
            # the #end tag's is not, and _pieces_of hands it back
            # through this list.
            leading = _eat_region_line(node, source, out)
            after = []
            if node.name == "if" and not node.children:
                # The ternary form has no body, so a line ending left
                # over cannot go inside it. It goes after: on a dirty
                # line _eatRestOfDirectiveTag leaves the ending in the
                # source, and the parser reaches it once addTernaryExpr
                # has already written the if.
                out.append((STMT_PIECE,
                            _block(node, source, hoisted, methods)))
                if leading:
                    out.append((TEXT_PIECE, leading))
                continue
            out.append((STMT_PIECE, _block(node, source, hoisted, methods,
                                           leading, after)))
            for text in after:
                out.append((TEXT_PIECE, text))
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
                # The tag itself is handled by the block it closes.
                # What it leaves behind is a line ending that belongs
                # after that block: ct3 has already written the
                # dedent when the text arrives.
                ending = _directive_line_ending(node, source, out)
                if ending and escaped is not None:
                    escaped.append(ending)
                elif ending:
                    out.append((TEXT_PIECE, ending))
            elif node.name == "slurp":
                # It exists to swallow the line ending after it, and
                # that ending is already inside its own tokens, so it
                # never gets written. What is left is the indent, and
                # eatSlurp drops that wherever the line was clear. Note
                # that it does so without the second condition the
                # other directives carry: a slurp always ends its line.
                if _line_is_clear(source, node.tokens[0].start):
                    _drop_indent(out)
                out.append((BARRIER_PIECE, ""))
                if node.tokens[-1].kind == lex.DIRECTIVE_END:
                    # eatSlurp ends with readToEOL(gobble=True), which
                    # takes the rest of the line whatever stands on it.
                    # Where the tag stopped at a directive end token
                    # that rest is still in the stream:
                    # "$job <!--#slurp#-->" leaves the "-->" a sibling,
                    # and ct3 swallows it with the line ending.
                    index = _swallow_line(nodes, index, out)
            elif node.name == "stop":
                # addStop writes one line and nothing else: a return of
                # what has been collected so far. It is not a signal to
                # stop generating, and everything after it is generated
                # as usual and simply never reached. Which is why it
                # works inside a block too, where dropping the rest
                # would have left a header with no body.
                #
                # Called for the indent it drops, not for the ending it
                # returns: that ending stands after the return, so
                # nothing writes it. "L#stop" renders "L" and not "L"
                # and a line ending.
                _directive_line_ending(node, source, out)
                out.append((STMT_PIECE, _framed_statement(STOP_RETURN)))
            elif node.name == "@":
                # A decorator for the #def that follows. eatDecorator
                # writes nothing itself and insists on a #def, #block
                # or another decorator coming next; here an unclaimed
                # one is simply dropped, the way an unclaimed one in
                # ct3 would attach to whatever method came later.
                _eat_directive_line(node, source, out)
                decorators.append(_argument(node, node))
                swallow_indent = True
            elif node.name == "super":
                # addSuper: a filtered write of what the parent's
                # method of the same name returns,
                #
                #     _v = super(Klass, self).m(a,b=3)
                #
                # with the arguments spelt the way the #def head spells
                # them, dollar off and joined by bare commas. No
                # rawExpr, because addFilteredChunk is called without
                # one. A template that has no #extends still writes
                # it and finds Template's method, or does not.
                _eat_directive_line(node, source, out)
                for statement in _placeholder_value(
                        _parsed(_super_call(_argument_text(node)))):
                    out.append((STMT_PIECE, statement))
            elif node.name == "arg":
                # setCallArg: the first #arg opens the dictionary and
                # names the argument the collector has been filling
                # all along, so text before it joins that argument;
                # every later one closes the argument before it and
                # opens a fresh collector. The statements go out before
                # the line is decided about, because setCallArg commits
                # through addChunk and the indent before an #arg on a
                # clear line is already in the collector by the time
                # _eatRestOfDirectiveTag looks for it: "  #arg a\n  x"
                # hands a four blanks and an x.
                stack = getattr(_ACTIVE, "calls", None) or []
                if not stack:
                    raise Unsupported("#arg outside a #call")
                call = stack[-1]
                match = ARG_NAME.match(_argument_text(node))
                if match is None:
                    raise Unsupported("#arg %r" % _argument_text(node)[:40])
                name = match.group("name")
                if call["args"]:
                    switch = CALL_ARG_CLOSE % {"id": call["id"],
                                               "name": call["args"][-1]}
                else:
                    switch = CALL_ARG_FIRST % {"id": call["id"],
                                               "name": name}
                call["args"].append(name)
                for statement in ast.parse(switch).body:
                    out.append((STMT_PIECE, statement))
                if match.group("after") is None:
                    _eat_directive_line(node, source, out)
                # The colon form ends at its colon and the tree keeps
                # what follows as siblings: template text, placeholders
                # resolved and directives read, from the first
                # character after the colon on. Nothing of it is the
                # tag's, so nothing is written here.
            elif node.name == "encoding":
                # Not through _eat_directive_line: it is the one
                # directive that leaves the whitespace in front of it
                # alone.
                _encoding_directive(node, source)
            elif node.name == "attr":
                _eat_directive_line(node, source, out)
                methods.append(_attr_statement(node))
            elif node.name in ("import", "from"):
                _eat_directive_line(node, source, out)
                hoisted.append(_import_statement(node))
            elif node.name in ("extends", "implements"):
                # Already read by _class_shape, which settled the base
                # list and the main method before any of this ran. What
                # is left is the line they stand on, which they decide
                # about like every other directive.
                _eat_directive_line(node, source, out)
            elif node.name == "include":
                _include(node, source, out)
            else:
                # After the statements, not before them. #echo writes,
                # and "L#echo 1" puts the 1 in front of the line ending
                # rather than behind it.
                ending = _directive_line_ending(node, source, out)
                for made in _simple_directive(node):
                    out.append((STMT_PIECE, made))
                if ending:
                    out.append((TEXT_PIECE, ending))
        elif node.kind == lex.PSP:
            index = _psp(nodes, index - 1, source, hoisted, methods, out)
        elif node.kind == lex.EOL_SLURP:
            # It writes nothing and has already taken its line ending
            # with it. What is left is the indent before it.
            if _line_is_clear(source, node.tokens[0].start):
                _drop_indent(out)
            out.append((BARRIER_PIECE, ""))
        else:
            _piece(node, source, out)
    if stamping is not None:
        _stamp(out, mark, stamping)
    return out


def _stamp(out: list[tuple[str, Any]], mark: int, node: tree.Node) -> None:
    """Gives the statements a node produced the node's own position.

    Only what has none yet, which is how a placeholder keeps the
    position of the placeholder rather than of the block it stands in.
    """
    for kind, value in out[mark:]:
        if kind == STMT_PIECE:
            _at(value, node.line, node.column)


def _swallow_line(nodes: Sequence[tree.Node], index: int,
                  out: list[tuple[str, Any]]) -> int:
    """Drops what stands after a #slurp on its line, the ending too.

    Called as ``_swallow_line(nodes, index, out)`` where index is the
    first sibling after the slurp. Returns the index to carry on from.
    A node that runs past the line ending keeps the part beyond it, so
    the next line is written.

    ct3 reads characters here and does not parse them, which is why a
    directive on the rest of the line is refused rather than swallowed:
    ct3 never sees it, and everything after it would then be read
    differently, body and all.

    Raises:
        Unsupported: where a directive or a comment stands on the rest
            of the line.
    """
    while index < len(nodes):
        node = nodes[index]
        if node.kind not in (lex.TEXT, lex.PLACEHOLDER, lex.ESCAPE):
            raise Unsupported("a %s on the rest of a #slurp line"
                              % node.kind)
        index += 1
        match = lex.EOL.search(node.text())
        if match is None:
            continue
        rest = node.text()[match.end():]
        if rest:
            out.append((TEXT_PIECE, rest))
        return index
    return index


def _piece(node: tree.Node, source: str,
           out: list[tuple[str, Any]]) -> None:
    if node.kind == lex.TEXT:
        out.append((TEXT_PIECE, node.text()))
        return
    if node.kind == lex.RAW:
        # A raw body outside the block that owns it: lex.raw_end and
        # _raw_block disagree about where the block stopped, and what
        # is left over here is source ct3 parses. Written out as text
        # it would put an unresolved placeholder in the output.
        raise Unsupported("a #raw body the block does not cover")
    if node.kind == lex.ESCAPE:
        # "\$" stands for a dollar. What Cheetah writes is the
        # character behind the backslash, not both.
        out.append((TEXT_PIECE, node.text()[1:]))
        return
    if node.kind == lex.PLACEHOLDER:
        token = node.tokens[0]
        _refuse_a_short_token(token, source)
        marked = lex.start_of(token.text)
        if marked is not None and (marked.group("silent")
                                   or marked.group("cache")):
            _modified_placeholder(node, token, marked, out)
            return
        written = Written(placeholder_source(token.text), token.text,
                          node.line, node.column, token.start)
        catcher = _catcher()
        if catcher is not None and catcher.name is not None:
            # Turned into statements here and not in _statements. The
            # catcher is switched on and off as the walk passes the
            # directives, and _statements runs over the whole top level
            # afterwards, by which time an #end errorCatcher has
            # already switched it back off.
            for statement in _placeholder(written):
                out.append((STMT_PIECE, statement))
            return
        out.append((VALUE_PIECE, written))
        return
    raise Unsupported("no code for a %s node" % node.kind)


def _refuse_a_short_token(token: lex.Token, source: str) -> None:
    """Where ct3 reads further than the placeholder token reaches.

    lex._balanced gives up at a line ending inside a bracket, so
    ``$a[\\n1]`` is the token ``$a`` while ct3 reads the subscript and
    writes VFFSL(SL,"a",True)[1]. Nothing inside the token says so; the
    character after it does. A token ending on a call is the one case
    where a bracket may follow legitimately, because there ct3 stops
    too: ``$a(1)[2]`` is a placeholder and then the text ``[2]``.

    The enclosure forms end where ct3 ends them, so they are left
    alone: ``${a}[1]`` writes the same bytes either way.
    """
    marked = lex.start_of(token.text)
    if marked is not None and marked.group("enclosure"):
        return
    following = source[token.end:token.end + 1]
    if following in ("(", "[") and not token.text.endswith(")"):
        raise Unsupported("placeholder %r followed by %r"
                          % (token.text, following))


def _modified_placeholder(node: tree.Node, token: lex.Token,
                          marked: re.Match[str],
                          out: list[tuple[str, Any]]) -> None:
    """A placeholder carrying a silence token, a cache token, or both.

    ct3 reads them between the dollar and the name, the silence token
    first and at most one of each, and turns each into a wrapper around
    the two statements the placeholder would write anyway. The pieces
    are statements and carry no text, so the whitespace rules stop at
    them exactly as they do at a block.

    The nesting order is ct3's and it is not free: addPlaceholder
    starts the cache region, applies the silence inside it, and closes
    the region last. Wrapped the other way round a NotFound escapes
    before the region puts ``trans`` and ``write`` back, and everything
    written after that lands in the dead collector. The corpus says so:
    Placeholders.test20, ``$!aStr$!nonExistant$!*nonExistant$!{...}``,
    renders empty instead of ``blarg``.
    """
    # Only the two tokens come off. marked.end() would be past the
    # enclosure and the blanks behind it, and "$*( aStr   )" would be
    # rebuilt as "$aStr   )", which costs 32 corpus cases.
    body = _placeholder(Written(
        placeholder_source("$" + token.text[marked.end("cache"):]),
        token.text, node.line, node.column, token.start))
    if marked.group("silent"):
        body = [_silenced(body)]
    if marked.group("cache"):
        body = _cache_region(token, body, _interval(marked.group("cache")))
    for statement in body:
        out.append((STMT_PIECE, statement))


def _silenced(body: list[ast.stmt]) -> ast.stmt:
    """``$!x``: the placeholder's statements, and NotFound swallowed.

    Both statements go inside the try, which is where ct3 puts them, so
    a NotFound raised by the lookup and one raised by the filter or by
    a called function are equally swallowed. NotFound exactly and not
    LookupError: NotFound is a LookupError subclass, and ct3 lets a
    plain KeyError out of user code.
    """
    made = _framed("try:\n    pass\nexcept NotFound:")
    assert isinstance(made, ast.Try)
    made.body = body
    return made


def _interval(cache: str) -> float | None:
    """The seconds a ``$*5m*x`` cache token stands for, or None.

    None where there is no interval at all, which is ``$*x``. ct3's
    GenUtils.genTimeInterval: a trailing s, m, h, d or w scales the
    number, and a bare number is minutes.

    Raises:
        Unsupported: where the number is not a float. ct3's regular
            expression takes "$*1.2.3*x" and "$*.*x" and then dies at
            compile time, and where ct3 does not compile this layer has
            nothing to be faithful to.
    """
    inner = cache[1:-1]
    if not inner:
        return None
    try:
        if inner.endswith("s"):
            return float(inner[:-1])
        if inner.endswith("m"):
            return float(inner[:-1]) * 60
        if inner.endswith("h"):
            return float(inner[:-1]) * 60 * 60
        if inner.endswith("d"):
            return float(inner[:-1]) * 60 * 60 * 24
        if inner.endswith("w"):
            return float(inner[:-1]) * 60 * 60 * 24 * 7
        return float(inner) * 60
    except ValueError:
        raise Unsupported("cache interval %r" % inner) from None


# The cache region ct3 wraps a cached placeholder in, read off
# Compiler.startCacheRegion and endCacheRegion. Everything written
# while the placeholder runs is collected into a throwaway transaction
# and stored on the region's cache item; on every later evaluation of
# the same region on the same instance the stored text is written and
# the placeholder is not evaluated again. That is the only place the
# cache is observable, and the corpus never gets there: a loop over a
# cached counter is what tells the two apart.
#
# Parsed rather than assembled, and the placeholder's own statements go
# where the lone "pass" stands, the way a method body goes into the
# prologue.
CACHE_REGION = """\
_ct4_recache%(id)s = False
_ct4_region%(id)s = self.getCacheRegion(regionID=%(region)r,
                                        cacheInfo=%(info)r)
if _ct4_region%(id)s.isNew():
    _ct4_recache%(id)s = True
_ct4_item%(id)s = _ct4_region%(id)s.getCacheItem(%(region)r)
if _ct4_item%(id)s.hasExpired():
    _ct4_recache%(id)s = True
if (not _ct4_recache%(id)s) and _ct4_item%(id)s.getRefreshTime():
    try:
        _ct4_output%(id)s = _ct4_item%(id)s.renderOutput()
    except KeyError:
        _ct4_recache%(id)s = True
    else:
        write(_ct4_output%(id)s)
        del _ct4_output%(id)s
if _ct4_recache%(id)s or not _ct4_item%(id)s.getRefreshTime():
    _ct4_orig_trans%(id)s = trans
    trans = _ct4_collector%(id)s = DummyTransaction()
    write = _ct4_collector%(id)s.response().write
%(expiry)s    pass
    trans = _ct4_orig_trans%(id)s
    write = trans.response().write
    _ct4_data%(id)s = _ct4_collector%(id)s.response().getvalue()
    _ct4_item%(id)s.setData(_ct4_data%(id)s)
    write(_ct4_data%(id)s)
    del _ct4_data%(id)s
    del _ct4_collector%(id)s
    del _ct4_orig_trans%(id)s
"""

# ct3's STATIC_CACHE and REFRESH_CACHE. getCacheRegion never reads the
# cacheInfo it is handed, but a template may be given a cacheRegionClass
# of its own, so it is passed on as ct3 builds it.
STATIC_CACHE = 1
REFRESH_CACHE = 2


def _cache_region(token: lex.Token, body: list[ast.stmt],
                  interval: float | None) -> list[ast.stmt]:
    """``$*x``: the placeholder's statements inside a cache region.

    Every cache token in a module needs a region of its own, because
    the regions are kept per instance under their ID. ct3 draws a fresh
    random one for each; naming it after where the placeholder stands
    keeps them apart just as well and gives the same code twice over.
    Two of them sharing an ID would share a cache item, and the second
    would write the first one's text.
    """
    identifier = "_%d_%d" % (token.line, token.column)
    # ct3 builds the ID this way, quotes stripped off a repr, and it
    # keeps the modifiers on: cacheInfo['ID'] = repr(rawPlaceholder)[1:-1].
    info: dict[str, Any] = {"type": STATIC_CACHE}
    if interval is not None:
        info = {"type": REFRESH_CACHE, "interval": interval}
    info["ID"] = repr(token.text)[1:-1]
    expiry = ""
    if interval:
        # ct3 guards the line with a plain "if interval:", so an
        # interval of 0 emits nothing at all and the item never
        # expires. "$*0*x" behaves exactly like "$*x".
        expiry = ("    _ct4_item%s.setExpiryTime(currentTime() + %r)\n"
                  % (identifier, interval))
    made = ast.parse(CACHE_REGION % {
        "id": identifier, "region": "_ct4_cache" + identifier,
        "info": info, "expiry": expiry}).body
    branch = made[-1]
    assert isinstance(branch, ast.If)
    at = [i for i, statement in enumerate(branch.body)
          if isinstance(statement, ast.Pass)][0]
    branch.body[at:at + 1] = body
    return made


def _directive_line_ending(node: tree.Node, source: str,
                           out: list[tuple[str, Any]]) -> str:
    """A directive decides about its own line. Returns what it kept.

    Two conditions, both ct3's, and the second is easy to miss.
    _eatRestOfDirectiveTag removes the whitespace before a directive
    only where the line was clear *and* the tag ran past the end of its
    own first line. In ``  #for $i in range(5)#$i#end for#`` the tag
    ends at the hash in the middle of the line, so the two spaces stay
    and a corpus case says so.

    A third condition sits on top of them and does not read like one:
    a "##" comment on the tag's own line can keep the indent alive.
    _eatRestOfDirectiveTag eats that comment first, and most comments
    reach addChunk, which commits the pending text before
    handleWSBeforeDirective ever gets to truncate it. So the indent of
    ``  #if 1 ## note`` survives while the indent of ``  #if 1``
    does not. The line ending is gobbled either way.

    The line ending is inside the directive's own tokens where it took
    one, so keeping it means writing it out again. It is returned
    rather than written, because a directive that writes something has
    to put its own output in front of it: ct3 adds the chunk and only
    then commits the text that follows.
    """
    own = "".join(t.text for t in node.tokens)
    ending = _trailing_eol(own)
    past_its_line = bool(ending) or node.tokens[-1].end >= len(source)
    if _line_is_clear(source, node.tokens[0].start):
        if past_its_line and not _tag_comment_commits(node):
            _drop_indent(out)
        return ""
    return ending


# The comment forms addComment returns from without writing a chunk: a
# bar comment, the "name@" special variable, and the five docstring and
# header forms. Everything else reaches addMethComment.
DOC_COMMENTS = ("doc:", "doc-method:", "doc-module:", "doc-class:",
                "header:")
BAR_COMMENT = re.compile(r"#+$")


def _tag_comment_commits(node: tree.Node) -> bool:
    """Whether a comment on the tag's line flushes the pending text.

    Read off Compiler.addComment. What it writes is a Python comment
    inside the generated method, which is nothing at all in the output
    -- but it goes through addChunk, and addChunk commits the string
    constant that was still pending. That is the whole effect: the
    whitespace before the directive is already written out by the time
    handleWSBeforeDirective would have removed it.
    """
    return any(token.kind == lex.COMMENT and _comment_commits(token.text)
               for token in node.tokens)


def _comment_commits(text: str) -> bool:
    """Whether ct3's addComment turns one comment into a chunk."""
    from Cheetah.Parser import specialVarRE

    # Past the "##", and without the line ending readToEOL leaves off.
    text = text[2:]
    text = text[:len(text) - len(_trailing_eol(text))]
    if not text.splitlines():
        # "for line in comm.splitlines()" never runs for an empty one.
        return False
    if BAR_COMMENT.match(text) or specialVarRE.match(text):
        return False
    return not text.startswith(DOC_COMMENTS)


def _encoding_directive(node: tree.Node, source: str) -> None:
    """``#encoding``: nothing written, and the rest of its line eaten.

    The odd one out. eatEncoding (Cheetah/Parser.py) calls
    readToEOL(gobble=True) and neither _eatRestOfDirectiveTag nor
    handleWSBeforeDirective, so unlike every other directive it leaves
    what stands in front of it on the line alone: ct3 renders
    ``  #encoding utf-8\\n1234`` as ``  1234`` and
    ``x #encoding utf-8\\ny`` as ``x y``. No corpus case has one
    anywhere but at the start of a line.

    Which codec it names has already been settled by _preprocess,
    which reads it off the source with ct3's own regular expression and
    not off this node. The two disagree: for
    ``#encoding utf-8 junk here`` the directive carries the whole
    string while the expression does not match at all, and ct3 then
    does no preprocessing. Nothing else reaches the generated module
    either, because setModuleEncoding only stores the name and nothing
    ever reads it.

    Raises:
        Unsupported: where the directive's tokens stop before a line
            ending. readToEOL always reaches one; the lexer stops a
            directive's arguments at a bare hash, so the ending of
            ``#encoding utf-8#\\n1234`` is left behind as text and
            writing it out would put a blank line in the output.
    """
    own = "".join(t.text for t in node.tokens)
    if not _trailing_eol(own) and node.tokens[-1].end < len(source):
        raise Unsupported("a #encoding that stops before its line ends")


def _eat_directive_line(node: tree.Node, source: str,
                        out: list[tuple[str, Any]]) -> None:
    """A directive that writes nothing, and its own line with it."""
    ending = _directive_line_ending(node, source, out)
    if ending:
        out.append((TEXT_PIECE, ending))


# -- PSP -------------------------------------------------------------
#
# A second block structure over the same source, and ct3 runs it
# through the same indentation counter as the directives: addPSP
# (Cheetah/Compiler.py line 811) writes the body out one source line at
# a time and calls indent() or dedent() itself. What an ast can express
# of that is an opener and its <%end%> standing among the same
# siblings; the shapes that cross a directive block are refused.
#
# Nothing here touches the whitespace. eatPSP (Cheetah/Parser.py line
# 1635) calls neither handleWSBeforeDirective nor
# _eatRestOfDirectiveTag, so the indent in front of a PSP and the line
# ending behind it are ordinary text: ct3 renders "a\n   <%= 1 %>\nb"
# as "a\n   1\nb".

# The name put where the body of a block goes, so that Python's own
# parser decides what the header opened: "for i in x:" with an indented
# marker under it parses to a For whose body is the marker, and
# "if 1:\n    pass\nelse:" to an If whose orelse is. ct3 indents by
# four spaces (Compiler.indentationStep), so a body whose own
# continuation lines are indented by anything else fails to parse here
# exactly as it fails to compile there.
PSP_MARKER = "__ct4_psp_body__"

# The four arms of addPSP, in the order it tests them.
PSP_VALUE = "value"
PSP_END = "end"
PSP_OPEN = "open"
PSP_STATEMENTS = "statements"


def _psp_body(node: tree.Node) -> str:
    """What stands between the two PSP tokens, stripped.

    Raises:
        Unsupported: where the token does not close, which is a
            ParseError in ct3, and where nothing is left after the
            strip, where addPSP subscripts an empty string and dies of
            an IndexError.
    """
    text = node.tokens[0].text
    if len(text) < 4 or not text.startswith("<%") or not text.endswith("%>"):
        raise Unsupported("a PSP that does not close")
    body = text[2:-2].strip()
    if not body:
        raise Unsupported("an empty PSP")
    return body


def _psp_kind(body: str) -> str:
    """Which arm of addPSP a body takes."""
    if body[0] == "=":
        return PSP_VALUE
    if body.lower() == "end":
        # Case and blanks do not matter, and nothing else in the token
        # is looked at.
        return PSP_END
    if body[-1] in ":$":
        return PSP_OPEN
    return PSP_STATEMENTS


def _psp(nodes: Sequence[tree.Node], at: int, source: str,
         hoisted: list[ast.stmt], methods: list[ast.stmt],
         out: list[tuple[str, Any]]) -> int:
    """One PSP, and the block it opens where it opens one.

    Returns the index just past what was taken, which for a block is
    everything up to and including its ``<%end%>``.

    A PSP body is raw Python and is spliced as it stands: a dollar in
    it is no placeholder, and the six names it is likely to reach for
    -- write, _filter, SL, trans, _dummyTrans and self -- are bound by
    this layer's prologue under ct3's own spelling. The names only
    ct3's generated module carries are refused by
    _refuse_preamble_names once the module is built.
    """
    body = _psp_body(nodes[at])
    kind = _psp_kind(body)
    if kind == PSP_OPEN:
        return _psp_block(nodes, at, body, source, hoisted, methods, out)
    if kind == PSP_END:
        raise Unsupported("a PSP end token that closes nothing")
    if kind == PSP_VALUE:
        made = _psp_write(body[1:].strip())
    else:
        made = _psp_statements(body)
    # Never nothing at all. addPSP commits the pending text before it
    # looks at the body, so whatever a "<%=%>" or a body that is all
    # comment writes -- which is nothing -- the whitespace in front of
    # it can no longer be reached backwards into.
    for statement in made or [ast.Pass()]:
        out.append((STMT_PIECE, statement))
    return at + 1


def _psp_write(expression: str) -> list[ast.stmt]:
    """``<%= x %>``: the value written through the filter.

    One statement and not the two a placeholder gets: addPSP writes
    ``write(_filter(x))`` with no ``_v`` and no ``is not None`` guard.
    The two agree under the default filter, where None renders empty,
    and part company under one that renders it as something.

    The expression is stripped first. addPSP takes only the ``=`` off,
    so the blank ct3 leaves behind sits harmlessly inside the call
    brackets where ast.parse would call it an indent.
    """
    if not expression:
        return []
    return [_write(ast.Call(func=ast.Name(id=FILTER, ctx=ast.Load()),
                            args=[_parsed(expression)], keywords=[]))]


def _psp_statements(text: str) -> list[ast.stmt]:
    """A run of Python out of a PSP body, as statements.

    Refused where indenting it changes what it means, because that is
    what ct3 does to it: addPSP walks PSP.splitlines() and addChunk
    puts the method's indentation in front of every one, so a line
    ending inside a string literal silently gains eight spaces.
    ``<% x = \"\"\"a\\nb\"\"\"\\nwrite(x) %>`` renders ``a\\n        b``
    there. An ast splice cannot reproduce that, so the body is turned
    away rather than rendered without the spaces.

    A docstring is the one string the indent may change. It is a
    statement that names nothing and is written nowhere, so eight
    spaces more inside it change no byte of the output, and SickGear
    puts one at the head of every helper it defines in a PSP block.
    Both trees are compared with their docstrings blanked; a string
    that is bound or written is not a docstring and still refuses.

    Raises:
        Unsupported: where the body is not Python, or is Python that
            reads differently once indented.
    """
    try:
        parsed = ast.parse(text)
        indented = ast.parse("if True:\n" + "\n".join(
            "    " + line for line in text.splitlines()))
    except SyntaxError as error:
        raise Unsupported("cannot read a PSP body: %s" % error) from None
    branch = indented.body[0]
    assert isinstance(branch, ast.If)
    if [ast.dump(one) for one in _without_docstrings(parsed.body)] \
            != [ast.dump(one) for one in _without_docstrings(branch.body)]:
        raise Unsupported("a PSP body the indentation would change")
    return parsed.body


def _without_docstrings(statements: list[ast.stmt]) -> list[ast.stmt]:
    """A copy of the statements with every docstring blanked.

    The first statement of a def or class body, where it is a bare
    string, and the same again in every def and class nested inside.
    """
    made = copy.deepcopy(statements)
    for statement in made:
        for node in ast.walk(statement):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and node.body:
                head = node.body[0]
                if isinstance(head, ast.Expr) \
                        and isinstance(head.value, ast.Constant) \
                        and isinstance(head.value.value, str):
                    head.value.value = ""
    return made


def _psp_block(nodes: Sequence[tree.Node], at: int, body: str, source: str,
               hoisted: list[ast.stmt], methods: list[ast.stmt],
               out: list[tuple[str, Any]]) -> int:
    """A PSP body that ends in ``:`` or ``$``, and what it encloses.

    The ``$`` is a marker and comes off; a ``:`` is Python and stays.
    Everything the header parses to above the block stands where the
    PSP stands, which is what ct3's line-by-line write does with a body
    like ``x = 1\\nif x:``.
    """
    if body[-1] == "$":
        body = body[:-1]
    closer = _psp_closer(nodes, at)
    made = _psp_statements(body + "\n    " + PSP_MARKER)
    inner = _statements(_pieces_of(nodes[at + 1:closer], source,
                                   hoisted, methods))
    if not inner:
        # ct3 writes the header, then dedents on the <%end%>, and a
        # header with nothing under it does not compile.
        raise Unsupported("a PSP block with no body")
    if not _fill_marker(made, inner):
        raise Unsupported("a PSP body that opens no block")
    for statement in made:
        out.append((STMT_PIECE, statement))
    return closer + 1


def _psp_closer(nodes: Sequence[tree.Node], at: int) -> int:
    """Where the ``<%end%>`` closing the PSP at ``at`` stands.

    Among the siblings and nowhere else. ct3 shares one indentation
    counter between the directives and PSP, so an opener inside an #if
    can be closed by an <%end%> outside it, and what that nests is
    something no ast can hold: ``#if 1\\n<% if 1:%>\\n#end if\\nx<%end%>``
    renders ``\\nx`` there, with the write inside the #if and outside
    the PSP's if. Refused rather than read as either.

    Raises:
        Unsupported: where no sibling closes it.
    """
    depth = 0
    for index in range(at + 1, len(nodes)):
        node = nodes[index]
        if node.kind != lex.PSP:
            continue
        kind = _psp_kind(_psp_body(node))
        if kind == PSP_OPEN:
            depth += 1
        elif kind == PSP_END:
            if not depth:
                return index
            depth -= 1
    raise Unsupported("a PSP block no sibling closes")


def _is_marker(node: ast.AST) -> bool:
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Name)
            and node.value.id == PSP_MARKER)


def _fill_marker(statements: list[ast.stmt], body: list[ast.stmt]) -> bool:
    """Puts the block's statements where the marker stands.

    Returns whether it found one. The parse cannot hold two, because
    only one was written.
    """
    for statement in statements:
        for node in ast.walk(statement):
            for _, value in ast.iter_fields(node):
                if not isinstance(value, list):
                    continue
                for index, item in enumerate(value):
                    if _is_marker(item):
                        value[index:index + 1] = body
                        return True
    return False


# -- #raw ------------------------------------------------------------
#
# Everything below is measured off the source and not off the tree,
# because every rule in ct3's eatRaw is about absolute offsets: which
# line the tag started on, where that line ends, and how far the tag
# ran past it. The tree keeps none of that. What the tree is used for
# is the check at the end: where this stops at an offset other than the
# one the tree handed over, the two read the template differently, and
# then it is refused rather than guessed at.

# ct3's WSchars. A line ending is not whitespace to getWhiteSpace.
BLANKS = " \f\t"

# What a tag can leave behind of its own line: one line ending, in any
# of the three spellings, or nothing at all.
ENDINGS = ("\r\n", "\n", "\r", "")


def _find_eol(source: str, at: int) -> int:
    """findEOL: the first line ending at or after here, or the end."""
    match = lex.EOL.search(source, at)
    return match.start() if match else len(source)


def _find_bol(source: str, at: int) -> int:
    """findBOL: just past the last line ending before here."""
    return max(source.rfind("\n", 0, at) + 1,
               source.rfind("\r", 0, at) + 1, 0)


def _skip_blanks(source: str, at: int, limit: int | None = None) -> int:
    """getWhiteSpace, as an offset rather than the text it read."""
    stop = len(source) if limit is None else min(len(source), at + limit)
    while at < stop and source[at] in BLANKS:
        at += 1
    return at


def _directive_at(source: str, at: int, names: frozenset[str]) -> str | None:
    """matchDirective: the directive starting here, by name, or None.

    Two conditions besides the name, both from the regular expressions
    _makeDirectiveREs builds. A backslash in front of the hash escapes
    it, which is why ``\\#end raw`` does not close a raw block; and a
    letter, an underscore or an ``@`` has to follow it.
    """
    if at and source[at - 1] == "\\":
        return None
    if source[at:at + 1] != "#":
        return None
    if source[at + 1:at + 2] not in lex.IDENT_START and \
            source[at + 1:at + 2] != "@":
        return None
    return lex._directive_name(source, at + 1, names)


def _raw_colon(source: str, at: int) -> tuple[int, bool]:
    """Where the ``#raw`` tag's name and blanks end, and which form it is.

    matchColonForSingleLineShortFormDirective decides the second part,
    and the whole rest of the line is what it looks at, not the rest of
    the directive: ``#raw: x#`` is a short form whose body is ``x#``.
    Nothing there at all, or a comment, means the block form.

    Raises:
        Unsupported: where nothing but blanks follows. ct3 peeks past
            the end of the source there and dies with an IndexError,
            so there is no output to be faithful to.
    """
    p = _skip_blanks(source, at + len("#raw"))
    if p >= len(source):
        raise Unsupported("#raw at the end of the template")
    if source[p] != ":":
        return p, False
    rest = source[p + 1:_find_eol(source, at)].strip()
    return p, bool(rest) and not rest.startswith("##")


def _raw_block(node: tree.Node, source: str,
               out: list[tuple[str, Any]]) -> None:
    """``#raw``: its body written out with nothing done to it.

    ct3's eatRaw, with _eatRestOfDirectiveTag and _eatToThisEndDirective
    behind it. The body reaches addRawText, which is addStrConst, so
    unlike ordinary text it is not unescaped: ``\\$x`` inside a raw
    block keeps its backslash, and a corpus case (RawDirective.test6)
    says so. No filter runs over it and no placeholder in it resolves.

    What is difficult is the whitespace, and both tags do their own.
    The opening tag decides whether it eats its own line ending and
    whether the indent before it goes; the closing tag decides the same
    again, and its drop reaches back into the text that stood before
    the ``#raw``.
    """
    names = lex.directive_names()
    at = node.tokens[0].start
    clear = _line_is_clear(source, at)
    eol1 = _find_eol(source, at)
    # getDirectiveStartToken, past the name, then blanks.
    p, short = _raw_colon(source, at)

    if short:
        # The short form does none of the block form's whitespace work.
        # The indent before it is written out, the line ending after it
        # stays behind, and exactly one blank behind the colon is eaten.
        p = _skip_blanks(source, p + 1, limit=1)
        body = source[p:eol1]
        p = eol1
    else:
        if source[p] == ":":
            p += 1
        p = _skip_blanks(source, p)
        p = _raw_tag_end(source, p, clear, eol1, out)
        body, p = _raw_body(source, p, names, out)
    trailing = _check_raw_span(node, source, p)
    out.append((RAW_PIECE, body))
    if trailing:
        # A line ending the block covers and ct3 did not eat. Written
        # out here as the ordinary text ct3 parses it as.
        out.append((TEXT_PIECE, trailing))


def _raw_tag_end(source: str, p: int, clear: bool, eol1: int,
                 out: list[tuple[str, Any]]) -> int:
    """_eatRestOfDirectiveTag, for the opening tag of a raw block.

    The body starts wherever this leaves the cursor, so whatever else
    stands on the ``#raw`` line becomes body: ``#raw foo`` writes
    ``foo``, and there is no such thing as an argument to #raw.
    """
    if source.startswith("##", p):
        # Two readings, and which one it is depends on whether a
        # directive name follows the two hashes. The comment reading
        # also commits the pending text, which changes what a later
        # drop finds. Neither shape is in the corpus.
        raise Unsupported("a ## on the #raw line")
    if source[p:p + 1] == "#":
        # The directive end token. The tag stops here, so the line
        # ending behind it stays and becomes output text.
        p += 1
    elif clear and p < len(source) and source[p] in "\r\n":
        match = lex.EOL.match(source, p)
        assert match is not None
        p = match.end()
    # Both conditions, and the second is the one to miss: a tag that
    # stopped at a hash before its own line ending keeps its indent.
    # RawDirective.test3 and test4 are the same template with and
    # without that hash, and they pin it from both sides.
    if clear and (p >= len(source) or p > eol1):
        _drop_indent(out)
    return p


def _raw_body(source: str, start: int, names: frozenset[str],
              out: list[tuple[str, Any]]) -> tuple[str, int]:
    """_eatToThisEndDirective: the body, and where parsing goes on.

    The scan walks one character at a time looking for an ``#end``
    whose whitespace is followed by ``raw``. That is a prefix test and
    not a word test, so ``#end rawX`` closes the block and leaves ``X``
    as text. Where nothing closes it the body runs to the end.
    """
    p = start
    body_end = start
    end_clear = False
    closed = False
    while p < len(source):
        if source[p] == "#" and _directive_at(source, p, names) == "end":
            hash_at = p
            after = _skip_blanks(source, p + len("#end"))
            if source.startswith("raw", after):
                if _line_is_clear(source, hash_at):
                    # The indent of the #end raw line is not body.
                    end_clear = True
                    body_end = _find_bol(source, hash_at)
                else:
                    body_end = hash_at
                p = _skip_blanks(source, after + len("raw"))
                closed = True
                break
            if after >= len(source):
                # ct3 advances past the end of the stream and raises.
                raise Unsupported("#end at the end of a #raw body")
            # The scan goes on from behind the whitespace, so a hash
            # inside what was skipped is never looked at again.
            p = after + 1
            body_end = p
            continue
        p += 1
        body_end = p
    body = source[start:body_end]
    if not closed:
        return body, p

    eol2 = _find_eol(source, p)
    if source.startswith("##", p):
        # The tag eats one hash. Where a directive name follows the
        # other, that is the start of a directive and the tree already
        # holds it as one: SickGear writes
        #
        #     sortBy: '#end raw##if $desc#by_order#else#$saved#end if##raw#',
        #
        # inside a JavaScript literal, and ct3 renders the branch. Where
        # no directive follows, ct3 writes the second hash and the rest
        # of the line as text, "# note", and this layer's lexer read
        # the pair as a comment and dropped it. That shape stays
        # refused.
        if _directive_at(source, p + 1, names) is None:
            raise Unsupported("a ## behind #end raw")
        p += 1
    elif source[p:p + 1] == "#":
        if _directive_at(source, p, names) is not None:
            # ct3 eats it as the directive end token and reads the rest
            # of the line as text; this layer's lexer sees a directive
            # and builds a block out of it.
            raise Unsupported("a directive behind #end raw")
        p += 1
    elif end_clear and p < len(source) and source[p] in "\r\n":
        match = lex.EOL.match(source, p)
        assert match is not None
        p = match.end()
    if end_clear and p > eol2:
        # Reaches back onto the #raw line, because the body is not
        # pending yet: addRawText comes after both drops.
        _drop_indent(out)
    return body, p


def _check_raw_span(node: tree.Node, source: str, p: int) -> str:
    """Refuses where the tree read the raw block differently.

    The catch-all for the lexer's raw_end, which finds ``#end raw`` as
    a literal string: it misses ``#end   raw``, it accepts an escaped
    ``\\#end raw``, and it knows nothing of the rescan rule. Every one
    of those ends up as a disagreement about where the block stops, and
    a disagreement is a refusal.

    One line ending of slack, in both directions. Where a tag did not
    eat its own line ending the tree still puts it inside the block:
    tree._close_short pulls it in for the short form, and
    _take_arguments for the ``#end raw`` tag of the block form. What
    the block covers beyond where ct3 stopped is returned so the caller
    can write it out.
    """
    end = node.tokens[0].start + len(node.text())
    if end < p or source[p:end] not in ENDINGS:
        # By line, not by offset. Whoever reads this is holding the
        # template and comparing the two readers, and an offset into
        # the whole source is a lookup they have to do by hand.
        starts = lex.line_starts(source)
        raise Unsupported(
            "#raw: this layer ends the block on line %d, the tree on "
            "line %d" % (lex.where(starts, p)[0],
                         lex.where(starts, min(end, len(source)))[0]))
    return source[p:end]


def _refuse_raw_in_short_form(source: str, root: tree.Node) -> None:
    """Turns away the block form of #raw inside a colon short form.

    ct3 parses the body of every colon short form with
    ``breakPoint=findEOL()``, and atEnd is measured against that break
    point and not against the end of the source. An unterminated
    ``#raw`` inside one therefore stops at the end of the host's line:
    ``#def f: #raw q\\n$f`` gives f the body ``q`` and renders ``q``,
    where reading to the end of the source would put the whole rest of
    the file inside f. The tree reads it the same wrong way, so the
    span check above cannot be the guard here.

    Only the block form. The short form reads to its own line ending,
    which is exactly where the host stops as well, so the two break
    points cannot tell it apart.
    """
    def walk(node: tree.Node, inside: bool) -> None:
        for child in node.children:
            deeper = inside
            if child.kind == tree.BLOCK:
                if child.name == "raw":
                    if inside and not _raw_colon(source,
                                                 child.tokens[0].start)[1]:
                        raise Unsupported(
                            "the block form of #raw inside a short form")
                elif not any(part.kind == lex.DIRECTIVE
                             and part.name == "end"
                             for part in child.children):
                    # Nothing closes it, so it is the colon short form
                    # and its body ends with the line it stands on.
                    deeper = True
            walk(child, deeper)

    walk(root, False)


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


# -- #include, #extends and #implements ------------------------------
#
# The three that say what the generated class is rather than what it
# writes. #include is one runtime call and nothing else; #extends and
# #implements write nothing at all and instead settle the base list,
# the main method's name and its arguments.

# What ct3 renames the main method to as soon as a template extends
# something: setting 'mainMethodNameForSubclasses', Cheetah/Compiler.py
# line 92, applied by setBaseClass at line 1936.
MAIN_FOR_SUBCLASS = "writeBody"

# The names ModuleCompiler puts in _importedVarNames before it parses
# anything (Cheetah/Compiler.py line 1861). #extends consults that set
# to decide whether it has to synthesise an import, so these eleven
# change what it does: "#extends Template" adds no import at all, and
# "#extends os.path.Thing" stops its walk on the first chunk.
CT3_IMPORTED = ("sys", "os", "os.path", "time", "types", "Template",
                "DummyTransaction", "NotFound", "Filters",
                "ErrorCatchers", "CacheRegion")

# What getCommaSeparatedSymbols (Cheetah/Parser.py line 643) will read
# whole. It skips blanks and tabs but not form feeds, takes a dot only
# where an identifier character follows it, and breaks on anything
# else without consuming it. A break leaves the rest of the line to be
# written out as text, so anything that does not match end to end has
# to be refused rather than read.
SYMBOLS = re.compile(
    r"^[ \f\t]*" + NAME + r"(?:[ \t]*\." + NAME + r")*"
    r"(?:[ \t]*,[ \t]*" + NAME + r"(?:[ \t]*\." + NAME + r")*)*[ \t]*$")

# ``#implements name`` and ``#implements name(args)``. ct3 reads the
# name with getIdentifier and looks for the bracket at once, with no
# whitespace between, and throws away whatever follows the arguments.
# Here that trailing text is refused instead of dropped.
IMPLEMENTS = re.compile(
    r"^[ \f\t]*(?P<name>" + NAME + r")(?:\((?P<params>.*)\))?[ \f\t]*$",
    re.S)


@dataclass(frozen=True)
class _ClassShape:
    """What #extends and #implements make of the generated class."""

    bases: list[str]
    main: str
    arguments: str
    imports: list[ast.stmt]


def _class_shape(root: tree.Node, base_class: str | None = None,
                 main_method: str | None = None) -> _ClassShape:
    """The base list, the main method and the imports #extends needs.

    ct3 has no such pass: eatExtends and eatImplements call the
    compiler as the single forward parse reaches them. Two things
    follow from that and both are load-bearing. The last of the two in
    source order decides the main method's name, because both call
    setMainMethodName; and the arguments an #implements added survive a
    later #extends, because setBaseClass renames the method compiler
    that is already there instead of making a new one
    (Cheetah/Compiler.py lines 1435 and 1936).
    """
    _refuse_nested_class_directives(root)
    # What the caller asked for, until the template says otherwise.
    # ct3 hands both to its compiler and lets eatExtends and
    # eatImplements overwrite them where they appear.
    bases = [base_class or "Template"]
    main = main_method or MAIN
    arguments = ""
    imports: list[ast.stmt] = []
    known = list(CT3_IMPORTED)
    for node in root.children:
        if node.kind != lex.DIRECTIVE:
            continue
        if node.name in ("import", "from"):
            known.extend(_ct3_imported_names(node))
        elif node.name == "extends":
            bases = _extends_bases(node, imports, known)
            main = MAIN_FOR_SUBCLASS
        elif node.name == "implements":
            main, added = _implements(node)
            arguments += added
    return _ClassShape(bases, main, arguments, imports)


def _refuse_nested_class_directives(root: tree.Node) -> None:
    """#extends or #implements anywhere but the top level.

    ct3 applies them to the whole class wherever they stand. This layer
    reads them off the top-level nodes, so one inside a block would be
    quietly ignored and the class would come out with the wrong base or
    the wrong main method. Compared by identity, because a Node is a
    plain dataclass.
    """
    top = {id(child) for child in root.children}
    stack = list(root.children)
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.name in ("extends", "implements") and id(node) not in top:
            raise Unsupported("#%s inside a block" % node.name)


def _ct3_imported_names(node: tree.Node) -> list[str]:
    """The names an #import or #from binds, as ct3 counts them.

    addImportStatement (Cheetah/Compiler.py line 2056) cuts the
    statement at the first ``import`` in it and takes the last word of
    every comma-separated piece behind it, so ``#import a.b.c`` binds
    the whole dotted name and ``#from x import y as z`` binds ``z``.
    The names only ever feed the #extends auto-import, and they are
    read the way that code reads them rather than the way Python binds
    them.
    """
    # The same text _import_statement builds, so the two cannot
    # disagree about what the statement is; that it is a real import
    # is checked there.
    arguments = "".join(t.text for t in node.tokens[1:])
    statement = "%s %s" % (node.name, arguments.strip())
    tail = statement[statement.find("import") + len("import"):]
    names = []
    for piece in tail.split(","):
        words = piece.split()
        if words and words[-1] != "*":
            names.append(words[-1])
    return names


def _extends_bases(node: tree.Node, imports: list[ast.stmt],
                   known: list[str]) -> list[str]:
    """``#extends A.B, C`` as a base list, with the imports it implies.

    ModuleCompiler.setBaseClass (Cheetah/Compiler.py line 1945) is
    copied here step for step, the ``final != chunks[-2]`` correction
    included: without it ``#extends Cheetah.Templates.SkeletonPage``
    imports the module instead of the class and the class statement
    raises a TypeError. Every name it imports is added to the set it
    consults, so a second #extends of the same name adds nothing.

    Appends to ``imports`` and ``known``, and returns the bases.
    """
    text = _class_directive_argument(node, "extends")
    if SYMBOLS.match(text) is None:
        raise Unsupported("#extends %r" % text.strip()[:40])
    # getCommaSeparatedSymbols drops every blank and tab it walks over,
    # so "A .B" is one name and "A B" would be the single name "AB".
    names = re.sub(r"[ \f\t]", "", text).split(",")
    if ", ".join(names) == "object" or ", ".join(names) in known:
        # The whole argument, not one name of it: ct3 asks this
        # question of the string it rejoined.
        return names
    bases = []
    for klass in names:
        chunks = klass.split(".")
        if len(chunks) == 1:
            bases.append(klass)
            if klass not in known:
                imports.append(_framed_statement("from %s import %s"
                                                 % (klass, klass)))
                known.append(klass)
            continue
        needed = True
        module = chunks[0]
        for chunk in chunks[1:-1]:
            if module in known:
                needed = False
                bases.append(klass.replace(module + ".", ""))
                break
            module += "." + chunk
        if needed:
            module, final = ".".join(chunks[:-1]), chunks[-1]
            if final != chunks[-2]:
                # ct3 assumes the last chunk names the class and the
                # one before it the module; where they are the same
                # word the whole name is the module instead.
                module = ".".join(chunks)
            bases.append(final)
            imports.append(_framed_statement("from %s import %s"
                                             % (module, final)))
            known.append(final)
    return bases


def _implements(node: tree.Node) -> tuple[str, str]:
    """``#implements name(args)``: the main method's name and arguments.

    ct3 drops an explicit ``self`` from the list (Cheetah/Parser.py
    eatImplements line 2195) and appends the rest to whatever the
    method already has. What it writes is
    ``def respond(self, foo=1234, trans=None)`` where this layer's
    prologue writes ``**KWS`` in place of the transaction; the two
    render the same.
    """
    text = _class_directive_argument(node, "implements")
    match = IMPLEMENTS.match(text)
    if match is None:
        raise Unsupported("#implements %r" % text.strip()[:40])
    return match.group("name"), _implements_parameters(match.group("params"))


def _implements_parameters(text: str | None) -> str:
    """An #implements argument list as Python, ready for the frame."""
    if text is None or not text.strip():
        return ""
    if "$" in text:
        # getDefArgList reads a name and a default value, and neither
        # is a placeholder there.
        raise Unsupported("a placeholder in an #implements argument list")
    parts = _split_arguments(text)
    if parts and parts[0].partition("=")[0].strip() == "self":
        del parts[0]
    if not parts:
        return ""
    return ", ".join(part.strip() for part in parts) + ", "


def _class_directive_argument(node: tree.Node, name: str) -> str:
    """The first line of what #extends or #implements was given.

    Cut on a bare ``\\r`` as well as on a newline: the MacEOL corpus
    variants put the line ending inside the directive's own text token,
    and it is not part of the argument either way. A comment is turned
    away rather than skipped, because it changes what happens to the
    line: addComment commits the pending text, so ct3 keeps the indent
    of a directive line that ends in one where it would otherwise drop
    it.
    """
    parts = []
    for token in node.tokens[1:]:
        if token.kind == lex.DIRECTIVE_END:
            continue
        if token.kind != lex.TEXT:
            raise Unsupported("#%s with a %s in it" % (name, token.kind))
        parts.append(token.text)
    text = "".join(parts)
    found = lex.EOL.search(text)
    return text[:found.start()] if found else text


def _refuse_unbound_bases(module: ast.Module, bases: list[str],
                          given: str | None = None) -> None:
    """A base class whose name this module never binds.

    ct3's generated module imports eleven names this one does not, and
    #extends is the one place a template can name them without writing
    a placeholder: ``#extends os.path.Thing`` adds no import there
    because ``os`` is already in _importedVarNames, and the class
    statement then reads a name that only ct3's preamble has.
    """
    # ct3 binds the baseclass it was handed into the module before it
    # execs it, so a name that came in that way is bound even though
    # nothing here imports it.
    bound = {"object"} | ({given} if given else set())
    for statement in module.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            for alias in statement.names:
                bound.add(alias.asname or alias.name.split(".")[0])
    for base in bases:
        if base.split(".")[0] not in bound:
            raise Unsupported("#extends %s, which nothing imports" % base)


def _include(node: tree.Node, source: str,
             out: list[tuple[str, Any]]) -> None:
    """``#include``: one call, and the template compiled at render time.

    ct3 generates nothing else (Cheetah/Compiler.py addInclude, line
    677). Template._handleCheetahInclude (Cheetah/Template.py line
    1629) compiles the nested template when the render reaches it,
    hands it this template's search list and its globalSetVars, copies
    the initial filter onto it, and caches it under the source
    expression. Compiling it here and writing the result inline would
    lose all four, and would not do the file form at all.

    ``trans`` is bound by the prologue of every method this layer
    generates, so an #include inside a #def needs nothing extra.
    """
    raw, from_string, expression = _include_argument(node)
    made = _framed_statement(
        "self._handleCheetahInclude(%s, trans=trans, includeFrom=%r, raw=%r)"
        % (expression, "str" if from_string else "file", raw))
    # The ending goes behind the call and not in front of it: this is
    # the first directive here that both writes something and decides
    # about its own line, and ct3 writes the call first.
    ending = _directive_line_ending(node, source, out)
    out.append((STMT_PIECE, made))
    if ending:
        out.append((TEXT_PIECE, ending))


def _include_argument(node: tree.Node) -> tuple[bool, bool, str]:
    """eatInclude's two flags and its expression.

    Both words are tested with a plain startswith and not as words
    (Cheetah/Parser.py line 2319), so ``#include rawsource=$a`` is a
    raw include of a string and ``#include rawfoo`` a raw include of
    the file ``foo``. The scan runs over the raw characters, and the
    point it stops at always falls inside a text token because ``raw``
    and ``source=`` are literals.
    """
    tokens = []
    for token in node.tokens[1:]:
        if token.kind == lex.DIRECTIVE_END:
            continue
        if token.kind in (lex.COMMENT, lex.BLOCK_COMMENT):
            # A ## changes what happens to the line, and a #* is no
            # comment at all to ct3: getExpressionParts breaks at the
            # hash, the directive end token eats it, and the rest of
            # the block comment is written out as template text.
            raise Unsupported("a %s on the #include line" % token.kind)
        tokens.append(token)
    text = "".join(t.text for t in tokens)
    at = _skip_blanks(text, 0)
    raw = text.startswith("raw", at)
    if raw:
        at += len("raw")
    at = _skip_blanks(text, at)
    from_string = text.startswith("source", at)
    if from_string:
        at += len("source")
        at = _skip_blanks(text, at)
        if text[at:at + 1] != "=":
            raise Unsupported("#include source without an =")
        at += 1
    expression = _resolved_from(tokens, at)
    if not expression.strip():
        # ct3 writes the call with an empty first argument and dies on
        # its own SyntaxError. There is nothing to be faithful to.
        raise Unsupported("#include without an expression")
    return raw, from_string, expression


def _resolved_from(tokens: list[lex.Token], at: int) -> str:
    """The tokens from that offset on, as Python.

    Refuses a placeholder that stands inside a string literal. The
    lexer does not skip strings in a directive's arguments, so
    ``#include raw source='This is my $Source '*2`` offers a
    placeholder ct3 never sees, and resolving it would build a valid
    expression that includes the wrong file. The test is positional, so
    ``#include $webdir + "/header.tmpl"`` is untouched.
    """
    raw = "".join(t.text for t in tokens)
    parts = []
    offset = 0
    for token in tokens:
        end = offset + len(token.text)
        if end <= at:
            offset = end
            continue
        if offset < at:
            if token.kind != lex.TEXT:
                raise Unsupported("#include this layer cannot read")
            parts.append(token.text[at - offset:])
        elif token.kind == lex.PLACEHOLDER and _quote_is_open(raw, offset):
            raise Unsupported("a placeholder inside a string in #include")
        else:
            parts.append(_token_source(token))
        offset = end
    return "".join(parts)


def _quote_is_open(text: str, at: int) -> bool:
    """Whether a Python string literal is still open at that offset."""
    index = 0
    while index < at:
        if text[index] in "\"'":
            end = lex._end_of_string(text, index)
            if end > at:
                return True
            index = end
            continue
        index += 1
    return False


# A definition's header: a name, and optionally a parameter list. The
# dollar in front of the name is optional because ct3 eats one
# (_eatDefOrBlock calls getCheetahVarStartToken before getIdentifier),
# and "#def $show" is how a good half of ct3's own test cases write it.
DEFINITION = re.compile(
    r"^\$?(?P<name>[A-Za-z_][A-Za-z_0-9]*)\s*(?:\((?P<params>.*)\))?\s*$",
    re.S)

# A dollar in front of a parameter name or its stars. ct3 writes
# "def show(self, x, y=1, **KWS)" for "#def show($x, $y=1)" and
# "def show(self, *args, **KWS)" for "#def show($*args)".
PARAM_DOLLAR = re.compile(r"\$(?=[*A-Za-z_])")


def _parameters(text: str | None) -> str:
    """A definition's parameter list as Python, ready for the frame.

    ct3 writes "def show(self, x, y=1, **KWS)" for "#def show($x,
    $y=1)": the dollar goes and the name stays. A default value with a
    placeholder in it loses the dollar the same way, "#def f($x=$y)"
    is "def f(self, x=y, **KWS)", and the bare name resolves against
    the module the way any default does. Usually that is a NameError
    the moment the class is defined, and it is ct3's NameError.
    """
    if not text or not text.strip():
        return ""
    out = []
    for part in _split_arguments(text):
        name, _, default = part.partition("=")
        name = PARAM_DOLLAR.sub("", name.strip())
        default = PARAM_DOLLAR.sub("", default)
        out.append(name if not default else "%s=%s" % (name, default))
    return ", ".join(out) + ", "


SUPER_ARGS = re.compile(r"^\s*(?:\((?P<params>.*)\))?\s*$", re.S)


def _super_call(text: str) -> str:
    """``super(Klass, self).m(a,b=3)`` for a ``#super($a, $b=3)``.

    eatSuper reads the arguments with getDefArgList, the same reader a
    #def head uses, drops a leading self, and addSuper joins what is
    left with bare commas: the name, or name=default. The class is the
    one being generated and the method is the one the directive stands
    in, which is the main one outside any #def.
    """
    match = SUPER_ARGS.match(text)
    if match is None:
        raise Unsupported("#super %r" % text.strip()[:40])
    parts = []
    for part in _split_arguments(match.group("params") or ""):
        if not part.strip():
            continue
        name, _, default = part.partition("=")
        name = PARAM_DOLLAR.sub("", name.strip())
        if "$" in default:
            raise Unsupported("a placeholder in a default value")
        parts.append(name if not default else "%s=%s" % (name, default))
    if parts and parts[0] == "self":
        del parts[0]
    return "super(%s, self).%s(%s)" % (
        getattr(_ACTIVE, "class_name", CLASS),
        getattr(_ACTIVE, "method", MAIN), ",".join(parts))


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


def _take_trailing_eol(pieces: list[tuple[str, Any]]) -> str:
    """Takes a line ending off the end of a body, if it ends with one.

    Returns what was taken, or nothing. Only a piece that is a line
    ending and nothing else: a body ending in "hi\n" keeps its text
    and gives up the ending.
    """
    if not pieces or pieces[-1][0] != TEXT_PIECE:
        return ""
    kind, value = pieces[-1]
    ending = _trailing_eol(value)
    if not ending:
        return ""
    rest = value[:-len(ending)]
    if rest:
        pieces[-1] = (kind, rest)
    else:
        pieces.pop()
    return ending


def _definition_line(node: tree.Node, source: str,
                     out: list[tuple[str, Any]]) -> str:
    """The line a #def or #block tag stands on.

    The long form is the ordinary case and _eat_region_line settles it.
    The short form is not: its tag stops at the colon and its body runs
    to the line ending, so the tag itself never carries one, and a
    reader that asks the tag whether it reached the end of its line
    always hears no. ct3 asks after the body instead: _eatDefOrBlock
    hands _eatRestOfDirectiveTag the position of that line ending, and
    the indent goes wherever the line was clear.

    Missing that left the two blanks of ``  #def m: hi`` in the output.
    The corpus writes its short forms at column zero; the perturbation
    run indents every directive in the corpus and found 20 of them.
    """
    short = not any(child.kind == lex.DIRECTIVE and child.name == "end"
                    for child in node.children)
    if not short:
        return _eat_region_line(node, source, out)
    if node.name == "block":
        # And #block is the one that does not drop it. closeBlock
        # writes the call where the tag stood, addChunk commits the
        # pending text before it, and only then does ct3 ask about the
        # whitespace, by which time there is none left pending. So
        # "  #block m: hi" keeps its two blanks and "  #def m: hi",
        # which writes nothing there, does not. The long form of both
        # drops it, because there the asking comes first.
        return ""
    if _line_is_clear(source, node.tokens[0].start):
        _drop_indent(out)
    # Never a leading ending: the body starts on the tag's own line.
    return ""


def _definition(node: tree.Node, source: str, hoisted: list[ast.stmt],
                methods: list[ast.stmt],
                leading: str = "",
                escaped: list[str] | None = None,
                decorators: Sequence[str] = ()) -> ast.stmt | None:
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
    # A comment does come off, and so does the directive end token:
    # ct3 eats both with _eatRestOfDirectiveTag, which is how sabnzbd
    # gets to write its definitions inside an HTML comment,
    #
    #     <!--#def show_notify_checkboxes($section_label)#-->
    #
    # and have the page still look like a page in an editor.
    header = "".join(t.text for t in node.tokens[1:]
                     if t.kind not in SILENT_KINDS
                     and t.kind != lex.DIRECTIVE_END).strip()
    # The colon short form leaves its colon on the header: the tree
    # cuts the arguments right after it. "#block mid: hi" defines mid
    # and calls it, same as the long form.
    header = header.rstrip(":").strip()
    match = DEFINITION.match(header)
    if match is None:
        raise Unsupported("#%s %r" % (node.name, header[:40]))
    params = _parameters(match.group("params"))
    name = match.group("name")
    # An error catcher does not reach into a method. ct3 keeps the flag
    # on the method compiler, and #def spawns a fresh one, so a
    # placeholder in this body is written plain even where the catcher
    # is on outside. It comes back on when the method is done.
    catcher = _catcher()
    was = catcher.name if catcher is not None else None
    if catcher is not None:
        catcher.name = None
    # And a method starts with no locals bound, because ct3 gives every
    # method compiler its own stack. A #for outside this #def does not
    # reach into it.
    outer = _scopes()
    _ACTIVE.scopes = []
    inside = getattr(_ACTIVE, "definition", False)
    _ACTIVE.definition = True
    enclosing = getattr(_ACTIVE, "method", MAIN)
    _ACTIVE.method = name
    try:
        pieces = _pieces_of(node.children, source, hoisted, methods,
                            escaped=escaped)
        if leading:
            pieces.insert(0, (TEXT_PIECE, leading))
        # Only the short form. The long one is closed by an #end, and
        # its body keeps every line ending it holds.
        short = not any(child.kind == lex.DIRECTIVE and child.name == "end"
                        for child in node.children)
        trailing = _take_trailing_eol(pieces) if short else ""
        # Inside the try as well: _statements is where a placeholder
        # piece decides whether it goes through a wrapper, so it has to
        # run while the catcher is still off.
        body = _statements(pieces)
    finally:
        if catcher is not None:
            catcher.name = was
        _ACTIVE.scopes = outer
        _ACTIVE.definition = inside
        _ACTIVE.method = enclosing
    made = _method(name, params, body or [ast.Pass()])
    if decorators and inside:
        # A #def inside a #def is a nested function in ct3 and not a
        # method, so _spawnMethodCompiler never runs for it and never
        # takes the decorators: they stay pending for whatever method
        # comes next. Reproducing where they land is more than this is
        # worth, and one test fixture in one repository writes it.
        raise Unsupported("a decorator on a #%s inside a #def" % node.name)
    if decorators:
        # "#@testdecorator" before a #def. eatDecorator keeps the "@"
        # in the expression it reads, so it is taken off here and the
        # rest is parsed as one.
        assert isinstance(made, ast.FunctionDef)
        made.decorator_list = [_parsed(one.lstrip("@").strip())
                               for one in decorators]
    methods.append(made)
    if trailing and escaped is not None \
            and not _line_is_clear(source, node.tokens[0].start):
        # The ending is not part of the method, and it survives where
        # something else stood on the line before the directive. It
        # goes out through escaped and not straight into out, because
        # a #block writes its call first and the ending follows it.
        escaped.append(trailing)
    if node.name == "def":
        return None
    return ast.Expr(value=ast.Call(
        func=_attribute("self", name), args=[],
        keywords=[ast.keyword(arg="trans",
                              value=ast.Name(id="trans", ctx=ast.Load()))]))


# -- #filter, #call and #cache ---------------------------------------
#
# Three regions in ct3's sense: setup statements, the body, teardown
# statements. #filter swaps the local _filter for the length of the
# region and puts the old one back. #call and #cache point trans and
# write at a throwaway DummyTransaction and then do something with what
# was collected: hand it to a function, or store it on a cache item and
# write it through. Nothing new is needed on the generated class,
# because getCacheRegion, _CHEETAH__filters, _CHEETAH__filtersLib and
# _CHEETAH__isBuffering are all ct3's own.
#
# The shapes below stand in Compiler.py line for line, which is why
# they are parsed rather than assembled. The names are this layer's:
# ct3 draws a random ID per region, so byte-equal code was never on the
# table, and what has to hold is only that two regions in one method
# stay apart and that two runs over one template agree.

# ct3's setFilter, the three forms of its argument. The plain local
# "filterName" is ct3's and it is not noise: VFFSL searches the calling
# frame before the search list, so a template that writes $filterName
# inside the region sees the filter's name.
FILTER_SETUP = """\
_ct4_orig_filter%(id)s = _filter
filterName = %(name)r
if %(name)r in self._CHEETAH__filters:
    _filter = self._CHEETAH__currentFilter = self._CHEETAH__filters[filterName]
else:
    _filter = self._CHEETAH__currentFilter = \
self._CHEETAH__filters[filterName] = \
getattr(self._CHEETAH__filtersLib, filterName)(self).filter
"""

# "#filter None" is the one form that does not touch
# _CHEETAH__currentFilter on the way in, and that is observable: a
# method called from inside the region filters with what the attribute
# still holds, not with the local.
FILTER_NONE = """\
_ct4_orig_filter%(id)s = _filter
_filter = self._CHEETAH__initialFilter
"""

FILTER_CLOSE = """\
_filter = self._CHEETAH__currentFilter = _ct4_orig_filter%(id)s
"""

# ct3's startCallRegion. The restore order in CALL_CLOSE is not free:
# write is recovered from the restored trans rather than from a saved
# copy, and the collected string is read after the restore, which is
# what makes a #call inside a #call come out right.
CALL_OPEN = """\
_ct4_orig_trans%(id)s = trans
_ct4_was_buffering%(id)s = self._CHEETAH__isBuffering
self._CHEETAH__isBuffering = True
trans = _ct4_call_collector%(id)s = DummyTransaction()
write = _ct4_call_collector%(id)s.response().write
"""

CALL_CLOSE = """\
trans = _ct4_orig_trans%(id)s
write = trans.response().write
self._CHEETAH__isBuffering = _ct4_was_buffering%(id)s
del _ct4_was_buffering%(id)s
del _ct4_orig_trans%(id)s
_ct4_call_arg%(id)s = _ct4_call_collector%(id)s.response().getvalue()
del _ct4_call_collector%(id)s
"""

CALL_DELETE = """\
del _ct4_call_arg%(id)s
"""

# What setCallArg and _endCallArg write. The first #arg opens the
# dictionary and records the name; every close puts the collector's
# text under the previous name and opens a fresh collector, the last
# one included, which nothing then deletes.
CALL_ARG_FIRST = """\
_ct4_call_kws%(id)s = {}
_ct4_call_current%(id)s = %(name)r
"""

CALL_ARG_CLOSE = """\
_ct4_call_kws%(id)s[%(name)r] = _ct4_call_collector%(id)s.response().getvalue()
del _ct4_call_collector%(id)s
trans = _ct4_call_collector%(id)s = DummyTransaction()
write = _ct4_call_collector%(id)s.response().write
"""

CALL_KW_RESET = """\
trans = _ct4_orig_trans%(id)s
write = trans.response().write
self._CHEETAH__isBuffering = _ct4_was_buffering%(id)s
del _ct4_was_buffering%(id)s
del _ct4_orig_trans%(id)s
"""

# "#arg name" or "#arg name: the value". eatCallArg reads one
# identifier, then either takes the colon and nothing after it or eats
# the rest of the tag, so what stands behind the colon is the value
# from its first character on.
ARG_NAME = re.compile(
    r"\s*(?P<name>[A-Za-z_][A-Za-z_0-9]*)\s*(?::(?P<after>.*))?$", re.S)

# ct3's startCacheRegion for the directive form. Two branches at the
# level of the surrounding code: the first writes the stored output
# where there is one, the second holds the whole body. What the body
# collects is stored with setData and written through unfiltered.
CACHE_OPEN = """\
_ct4_recache%(id)s = False
_ct4_region%(id)s = self.getCacheRegion(regionID=%(region)r,
                                        cacheInfo=%(info)r)
if _ct4_region%(id)s.isNew():
    _ct4_recache%(id)s = True
_ct4_item%(id)s = _ct4_region%(id)s.getCacheItem(%(region)r)
if _ct4_item%(id)s.hasExpired():
    _ct4_recache%(id)s = True
if (not _ct4_recache%(id)s) and _ct4_item%(id)s.getRefreshTime():
    try:
        _ct4_output%(id)s = _ct4_item%(id)s.renderOutput()
    except KeyError:
        _ct4_recache%(id)s = True
    else:
        write(_ct4_output%(id)s)
        del _ct4_output%(id)s
if _ct4_recache%(id)s or not _ct4_item%(id)s.getRefreshTime():
    pass
"""

CACHE_BODY_OPEN = """\
_ct4_orig_trans%(id)s = trans
trans = _ct4_collector%(id)s = DummyTransaction()
write = _ct4_collector%(id)s.response().write
"""

CACHE_EXPIRY = """\
_ct4_item%(id)s.setExpiryTime(currentTime() + %(interval)r)
"""

CACHE_BODY_CLOSE = """\
trans = _ct4_orig_trans%(id)s
write = trans.response().write
_ct4_data%(id)s = _ct4_collector%(id)s.response().getvalue()
_ct4_item%(id)s.setData(_ct4_data%(id)s)
write(_ct4_data%(id)s)
del _ct4_data%(id)s
del _ct4_collector%(id)s
del _ct4_orig_trans%(id)s
"""

# What startCaptureRegion and endCaptureRegion write, with the ID
# replaced by the region's own. The body's output is collected the way
# a cache body's is, and then assigned instead of written.
CAPTURE_OPEN = """\
_ct4_orig_trans%(id)s = trans
_ct4_was_buffering%(id)s = self._CHEETAH__isBuffering
self._CHEETAH__isBuffering = True
trans = _ct4_collector%(id)s = DummyTransaction()
write = _ct4_collector%(id)s.response().write
"""

CAPTURE_CLOSE = """\
trans = _ct4_orig_trans%(id)s
write = trans.response().write
self._CHEETAH__isBuffering = _ct4_was_buffering%(id)s
%(target)s = _ct4_collector%(id)s.response().getvalue()
del _ct4_orig_trans%(id)s
del _ct4_collector%(id)s
del _ct4_was_buffering%(id)s
"""


def _capture_region(node: tree.Node, identifier: str,
                    body: list[ast.stmt]) -> list[ast.stmt]:
    """``#capture cap``: the body's output bound to a name.

    The target is what eatCapture read with getExpression, so it may
    be an attribute, ``self._foo``, and ct3 writes it as it stands.
    ``#capture $x`` is a lookup on the left of an assignment, which
    ct3 generates and Python refuses; that ends up an Unsupported here
    by the same road.
    """
    target = _without_trailing_colon(
        _read_expression(_argument_text(node)), "capture")
    return (ast.parse(CAPTURE_OPEN % {"id": identifier}).body + body
            + [_framed_statement(line) for line in
               (CAPTURE_CLOSE % {"id": identifier,
                                 "target": target}).splitlines()])


# A filter is named by a bare identifier, which is what getIdentifier
# reads. The $-class form is turned away: ct3 reads it with
# getExpression, which is more than a placeholder, and no corpus case
# has one.
FILTER_NAME = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")

# A #cache key, and the timer value genTimeInterval can read.
CACHE_KEY = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
TIMER = re.compile(r"^[0-9.]+[smhdw]?$")

# The name a #call calls, where it is written as plain Python rather
# than as a placeholder. ct3 reads it with getCheetahVar(plain=True),
# which stops at the first character that is not part of a dotted name.
CALL_NAME = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_0-9]+)*)")


def _region(node: tree.Node, source: str, hoisted: list[ast.stmt],
            methods: list[ast.stmt],
            out: list[tuple[str, Any]]) -> None:
    """``#filter``, ``#call`` and ``#cache``, which redirect the output.

    Not through _block, which returns one statement: a region is
    several, and where they fall decides what lands in the collector.
    Both tags are eaten before the code around them is written, so the
    line ending the opening tag leaves behind is parsed *inside* the
    region and the one the #end tag leaves behind lands outside it.
    """
    # The colon short form is the one whose opening tag stops before
    # the end of its line. Every other tag ends at a line ending, at a
    # bare hash, or at the end of the file.
    own = "".join(t.text for t in node.tokens)
    short = not (_trailing_eol(own)
                 or node.tokens[-1].kind == lex.DIRECTIVE_END
                 or node.tokens[-1].end >= len(source))
    children = list(node.children)
    closed = any(child.kind == lex.DIRECTIVE and child.name == "end"
                 for child in children)
    if node.name == "i18n" and not _line_is_clear(source,
                                                  node.tokens[0].start):
        # The macro's output is parsed as a source of its own, and
        # whatever text stood pending before the tag is lost in the
        # switch: "A#i18n\n  hi\n#end i18n" renders without the A.
        # Not a rule worth having.
        raise Unsupported("#i18n with text before it on its line")
    if short and closed:
        # "#call int: 10#end call" is a ParseError in ct3: the short
        # form closed at the line ending and the #end closes nothing.
        raise Unsupported("#end inside the short form of #%s" % node.name)
    if not children:
        raise Unsupported("#%s with no body" % node.name)
    identifier = "_%d_%d" % (node.line, node.column)
    # What the #arg directives inside a #call need to know: the region
    # they belong to, and which arguments have been named so far. A
    # stack on the walk state, because a #call can stand inside one.
    call: dict[str, Any] = {"id": identifier, "args": []}
    if node.name != "call" and any(child.kind == lex.DIRECTIVE
                                   and child.name == "arg"
                                   for child in children):
        raise Unsupported("#arg outside a #call")
    leading = ""
    end = None
    if not short:
        leading = _eat_region_line(node, source, out)
        if children[-1].kind == lex.DIRECTIVE and children[-1].name == "end":
            end = children.pop()
    stack = getattr(_ACTIVE, "calls", None)
    if stack is None:
        stack = _ACTIVE.calls = []
    stack.append(call)
    try:
        pieces = _pieces_of(children, source, hoisted, methods)
    finally:
        stack.pop()
    if leading:
        pieces.insert(0, (TEXT_PIECE, leading))
    trailing = ""
    if end is not None:
        trailing = _eat_region_line(end, source, pieces)
    if short and node.name in ("call", "filter", "capture", "i18n"):
        # ct3 parses these three short forms with findEOL(gobble=False),
        # so the line ending stays outside the region. #cache uses
        # gobble=True and keeps it inside. CallDirective.test1#3,
        # "#call int: 10\n$aStr", is the case that says so, and
        # "#capture cap: hi\n[$cap]" renders "\n[hi]".
        trailing = _take_trailing_eol(pieces)
        covered = source[:children[-1].tokens[-1].end]
        if not trailing and _trailing_eol(covered):
            # Something in the body swallowed the ending, a #slurp or a
            # nested block. Where it belongs cannot be worked out from
            # here without guessing.
            raise Unsupported("the short form of #%s whose line ending is"
                              " not the last thing in its body" % node.name)
    body = _statements(pieces)
    if node.name == "filter":
        made = _filter_region(node, identifier, body)
    elif node.name == "call":
        made = _call_region(node, identifier, body, call["args"])
    elif node.name == "capture":
        made = _capture_region(node, identifier, body)
    elif node.name == "i18n":
        # The macro ct3 ships is a stub: it hands its body back
        # unchanged, gettext'd with no catalogue, and the parser reads
        # that as template source. So the region is its body and
        # nothing around it. The arguments, "id" and the rest, are read
        # and dropped there too.
        made = body
    else:
        made = _cache_block(node, identifier, body)
    for statement in made:
        out.append((STMT_PIECE, statement))
    if trailing:
        out.append((TEXT_PIECE, trailing))


def _eat_region_line(node: tree.Node, source: str,
                     out: list[tuple[str, Any]]) -> str:
    """_eat_directive_line, with the line ending handed back instead.

    A region's tags are eaten at a different point in ct3 than the code
    they stand for is written, so a line ending either of them leaves
    behind belongs on the other side of the region from the tag.
    """
    own = "".join(t.text for t in node.tokens)
    ending = _trailing_eol(own)
    past_its_line = bool(ending) or node.tokens[-1].end >= len(source)
    if _line_is_clear(source, node.tokens[0].start):
        if past_its_line and not _tag_comment_commits(node):
            _drop_indent(out)
        return ""
    return ending


CATCHER_NAME = re.compile(r"[ \t\f]*([A-Za-z_][A-Za-z_0-9]*)[ \t\f]*$")

# What ct3's setErrorCatcher writes: the catcher is built once per
# template object and kept, so two #errorCatcher lines naming the same
# class share the instance. %s is the class name in ErrorCatchers.
INSTALL = """\
if "%s" in self._CHEETAH__errorCatchers:
    self._CHEETAH__errorCatcher = self._CHEETAH__errorCatchers["%s"]
else:
    self._CHEETAH__errorCatcher = self._CHEETAH__errorCatchers["%s"] \
= ErrorCatchers.%s(self)
"""

# The wrapper ct3 spawns per distinct placeholder. eval and not the
# expression itself, because that is what ct3 writes and the difference
# shows: the lookup runs against the caller's locals, handed in, rather
# than against this method's.
WRAPPER = """\
def %s(self, localsDict={}):
    try:
        return eval(%s, globals(), localsDict)
    except self._CHEETAH__errorCatcher.exceptions() as e:
        return self._CHEETAH__errorCatcher.warn(
            exc_val=e, code=%s, rawCode=%s, lineCol=%s)
"""


def _error_catcher(node: tree.Node, source: str, hoisted: list[ast.stmt],
                   methods: list[ast.stmt],
                   out: list[tuple[str, Any]]) -> None:
    """``#errorCatcher Echo``, and what it does to every placeholder after.

    Not a block in ct3's sense: errorCatcher is not in
    _closeableDirectives, so the form every weewx skin opens with, one
    line and no #end, is the ordinary one. The tree still builds a node
    for it, because #end errorCatcher exists and turns the catcher off
    again, and that form has children.

    ct3 eats the rest of the tag's line before it writes the install
    code, so a line ending left over on a dirty line is written after
    the install and not before it.
    """
    leading = _eat_region_line(node, source, out)
    text = "".join(_token_source(t) for t in node.tokens[1:])
    match = CATCHER_NAME.match(text)
    if match is None:
        raise Unsupported("#errorCatcher %r" % text.strip()[:40])
    from Cheetah import ErrorCatchers

    name = match.group(1)
    if not hasattr(ErrorCatchers, name):
        # ct3 writes ErrorCatchers.<name>(self) and lets it fail at
        # import time. Saying so here beats an AttributeError out of a
        # generated module.
        raise Unsupported("no error catcher named %r" % name)
    for statement in ast.parse(INSTALL % (name, name, name, name)).body:
        out.append((STMT_PIECE, statement))
    if leading:
        out.append((TEXT_PIECE, leading))
    catcher = _catcher()
    assert catcher is not None
    catcher.name = name
    if not node.children:
        return
    after: list[str] = []
    out.extend(_pieces_of(node.children, source, hoisted, methods,
                          escaped=after))
    # turnErrorCatcherOff, which switches off rather than restoring
    # whatever was on before. Two nested #errorCatcher tags leave the
    # inner #end with nothing on.
    catcher.name = None
    for ending in after:
        out.append((TEXT_PIECE, ending))


def _caught(written: Written) -> ast.expr:
    """The call that replaces a placeholder while a catcher is on.

    One wrapper method per distinct placeholder text, appended to the
    class body. The name is not a dunder on purpose: Python mangles
    those inside a class, and ct3's __errorCatcher1 only survives that
    because the call it writes sits in the same class.
    """
    catcher = _catcher()
    assert catcher is not None and catcher.name is not None
    name = catcher.seen.get(written.raw)
    if name is None:
        name = "_ct4_caught_%d" % (len(catcher.seen) + 1)
        catcher.seen[written.raw] = name
        made = ast.parse(WRAPPER % (name, repr(written.code),
                                    repr(written.code), repr(written.raw),
                                    repr((written.line, written.column))))
        catcher.methods.append(made.body[0])
    return ast.Call(func=_attribute("self", name), args=[],
                    keywords=[ast.keyword(
                        arg="localsDict",
                        value=ast.Call(func=ast.Name(id="locals",
                                                     ctx=ast.Load()),
                                       args=[], keywords=[]))])


def _region_argument(node: tree.Node) -> str:
    """The raw text of a region tag's argument, comments dropped.

    Raw and not resolved: what a #filter names is a class in the filter
    library rather than a lookup, and a #cache argument list is not
    Python at all.
    """
    parts = []
    for token in node.tokens[1:]:
        if token.kind in (lex.DIRECTIVE_END, lex.COMMENT):
            continue
        if token.kind == lex.BLOCK_COMMENT:
            # _eatRestOfDirectiveTag matches "##" and nothing else, so
            # a "#* *#" on the tag line stays in the stream and ct3
            # parses it inside the region. The tree puts it here.
            raise Unsupported("a block comment on a #%s tag" % node.name)
        parts.append(token.text)
    return "".join(parts)


def _region_head(node: tree.Node) -> str:
    """A region tag's argument, stripped of its blanks and its colon."""
    raw = _region_argument(node).strip()
    if raw.endswith(":"):
        raw = raw[:-1].strip()
    return raw


def _filter_region(node: tree.Node, identifier: str,
                   body: list[ast.stmt]) -> list[ast.stmt]:
    """``#filter``: the body with another filter in force.

    Two forms taken, both read off Parser.eatFilter, which calls
    getIdentifier: the bare word None, compared without regard to case,
    restores the initial filter, and any other plain identifier names a
    class in the filter library. Text after the identifier is turned
    away, because ct3 leaves it in the stream and writes it out as
    output while the tree keeps it here.
    """
    name = _region_head(node)
    if name.lower() == "none":
        opening = ast.parse(FILTER_NONE % {"id": identifier}).body
    elif FILTER_NAME.match(name):
        opening = ast.parse(FILTER_SETUP % {"id": identifier,
                                            "name": name}).body
    else:
        raise Unsupported("#filter %r" % name[:40])
    return opening + body \
        + ast.parse(FILTER_CLOSE % {"id": identifier}).body


def _call_region(node: tree.Node, identifier: str,
                 body: list[ast.stmt],
                 named: Sequence[str] = ()) -> list[ast.stmt]:
    """``#call f``: the body collected and handed to f as its first argument.

    What comes back is written through the placeholder shape, filtered
    like any other value. The filter is not part of the redirect, so a
    #call inside a #filter escapes its body once on the way in and the
    function's result a second time on the way out. That is ct3's, and
    a corpus-shaped probe says so.
    """
    function, arguments = _call_target(node)
    if named:
        # endCallRegion with usesKeywordArgs on: the last argument is
        # closed the way every earlier one was, the transaction put
        # back, and the function called with the tag's own arguments
        # first and then the dictionary. The collector the last close
        # opens is never deleted; ct3 leaves it too.
        made = (ast.parse(CALL_OPEN % {"id": identifier}).body + body
                + ast.parse(CALL_ARG_CLOSE % {"id": identifier,
                                              "name": named[-1]}).body
                + ast.parse(CALL_KW_RESET % {"id": identifier}).body)
        call = "%s(%s**_ct4_call_kws%s)" % (
            function, arguments + ", " if arguments else "", identifier)
        made += _placeholder_value(_parsed(call),
                                   Written(call, "", node.line, node.column))
        return made + ast.parse("del _ct4_call_kws%s" % identifier).body
    made = ast.parse(CALL_OPEN % {"id": identifier}).body + body \
        + ast.parse(CALL_CLOSE % {"id": identifier}).body
    call = "%s(_ct4_call_arg%s%s)" % (
        function, identifier, ", " + arguments if arguments else "")
    made += _placeholder_value(_parsed(call),
                               Written(call, "", node.line, node.column))
    return made + ast.parse(CALL_DELETE % {"id": identifier}).body


def _call_target(node: tree.Node) -> tuple[str, str]:
    """The function a ``#call`` calls, and what stands after its name.

    eatCall turns useAutocalling off around the name and back on for
    the arguments, so ``#call $meth`` resolves to VFFSL(SL,"meth",False)
    and is called once, by the region and not by NameMapper. With True
    NameMapper would call meth() with no arguments and the region would
    then call whatever came back.
    """
    tokens = [t for t in node.tokens[1:]
              if t.kind not in (lex.DIRECTIVE_END, lex.COMMENT)]
    if any(t.kind == lex.BLOCK_COMMENT for t in tokens):
        raise Unsupported("a block comment on a #call tag")
    if tokens and tokens[0].kind == lex.TEXT and not tokens[0].text.strip():
        tokens = tokens[1:]
    if not tokens:
        raise Unsupported("#call without a function")
    first, rest_tokens = tokens[0], tokens[1:]
    if first.kind == lex.PLACEHOLDER:
        chunks = chunks_of(first.text)
        if len(chunks) != 1 or chunks[0].remainder:
            raise Unsupported("#call %r" % first.text)
        function = 'VFFSL(%s,"%s",False)' % (SEARCH_LIST, chunks[0].name)
        rest = ""
    elif first.kind == lex.TEXT:
        match = CALL_NAME.match(first.text)
        if match is None:
            raise Unsupported("#call %r" % first.text[:40])
        function = match.group(1)
        rest = first.text[match.end():]
    else:
        raise Unsupported("#call %s" % first.kind)
    # The arguments are read with autocalling back on, so a placeholder
    # among them goes through the ordinary reader. That reader refuses
    # the enclosure forms, which is what ct3's getExpression does too:
    # "#call $rec ${sep}" is a ParseError there.
    rest += "".join(_token_source(t) for t in rest_tokens)
    rest = rest.strip()
    if rest.endswith(":"):
        rest = rest[:-1].strip()
    if rest.startswith(("(", "[")):
        # "#call getattr(self, 'x')": the argument list belongs to the
        # name and ct3 reads it as part of the name, not as extra
        # arguments. Not read here.
        raise Unsupported("#call with a call in its function name")
    if rest:
        _parsed("f(%s)" % rest)
    return function, rest


def _cache_block(node: tree.Node, identifier: str,
                 body: list[ast.stmt]) -> list[ast.stmt]:
    """``#cache``: the body written once and served from a cache after.

    The region ID is what makes two blocks share a cache, so a custom
    ``id=`` has to survive into it. ct3 splices that same id straight
    into the names of its own locals, which is why one that is not an
    identifier is turned away: ct3 generates a SyntaxError there.
    """
    info = _cache_info(node)
    region = str(info.get("id", "_ct4_cache" + identifier))
    if not CACHE_KEY.match(region):
        # ct3 writes "_RECACHE_a-b = False" for id='a-b'.
        raise Unsupported("#cache id=%r" % region)
    # Named after the region and not after the position, so that two
    # blocks sharing an id share their locals exactly as ct3's do.
    own = "_" + region
    interval = info.get("interval")
    made = ast.parse(CACHE_OPEN % {"id": own, "region": region,
                                   "info": info}).body
    inner = ast.parse(CACHE_BODY_OPEN % {"id": own}).body
    if interval:
        # ct3 guards the line with a plain "if interval:", so timer=0
        # emits nothing and the item never expires.
        inner += ast.parse(CACHE_EXPIRY % {"id": own,
                                           "interval": interval}).body
    inner += body + ast.parse(CACHE_BODY_CLOSE % {"id": own}).body
    guard = made[-1]
    assert isinstance(guard, ast.If)
    guard.body = inner
    return made


def _cache_info(node: tree.Node) -> dict[str, Any]:
    """``#cache id='x', timer=150m`` as ct3's cacheInfo dict.

    getDefArgList splits on the top-level commas and strips both sides;
    genCacheInfoFromArgList takes the first and last character off a
    value that starts with a quote and turns timer into interval.
    Only "id" reaches the generated code, and only in lower case:
    startCacheRegion reads cacheInfo.get('id'), so an "ID=" is inert
    and CacheDirective.test4 has one.
    """
    raw = _region_head(node)
    info: dict[str, Any] = {"type": REFRESH_CACHE}
    if raw.startswith("("):
        # getDefArgList reads a parenthesised list to its closing
        # bracket instead of to the end of the line.
        raise Unsupported("#cache with a bracketed argument list")
    for part in _split_arguments(raw):
        key, assigned, value = part.partition("=")
        key = key.strip().lstrip("$")
        value = value.strip()
        if not assigned or not value:
            # genCacheInfoFromArgList subscripts a None default for a
            # bare argument and dies at compile time.
            raise Unsupported("#cache %r" % part.strip()[:40])
        if not CACHE_KEY.match(key):
            raise Unsupported("#cache key %r" % key[:40])
        if value[0] in "\"'":
            value = value[1:-1]
        if key == "timer":
            if not TIMER.match(value):
                raise Unsupported("#cache timer=%r" % value[:40])
            info["interval"] = _timer_interval(value)
            continue
        if key not in ("id", "ID"):
            # startCacheRegion also reads test and varyBy and splices
            # both into the generated Python as expressions. Neither
            # has a corpus case and both have real behaviour, so there
            # is nothing here to measure an acceptance against.
            raise Unsupported("#cache %s=" % key)
        info[key] = value
    return info


def _timer_interval(text: str) -> float:
    """genTimeInterval, for the ``timer=`` of a #cache."""
    scale = {"s": 1, "m": 60, "h": 60 * 60,
             "d": 60 * 60 * 24, "w": 60 * 60 * 24 * 7}
    if text[-1] in scale:
        return float(text[:-1]) * scale[text[-1]]
    return float(text) * 60


def _try_block(node: tree.Node, source: str,
               hoisted: list[ast.stmt],
               methods: list[ast.stmt],
               leading: str = "",
               escaped: list[str] | None = None) -> ast.stmt:
    """``#try`` with its ``#except`` and ``#finally`` arms."""
    statement = ast.Try(body=[], handlers=[], orelse=[], finalbody=[])
    for directive, pieces in _branch_pieces(node, source, hoisted, methods,
                                            leading, escaped,
                                            ("except", "finally")):
        made = _statements(pieces) or [ast.Pass()]
        if directive is node:
            statement.body = made
            continue
        if directive.name == "finally":
            statement.finalbody = made
            continue
        # Through the same reader and the same colon rule as every
        # other directive argument. Sick-Beard writes
        # "#except NameMapper.NotFound:" and the colon it wrote was
        # kept, which made two of them.
        caught = _without_trailing_colon(
            _read_expression(_argument_text(directive)), "except",
            allow_empty=True)
        header = _framed("try:\n    pass\nexcept%s:"
                         % (" " + caught if caught else ""))
        assert isinstance(header, ast.Try)
        handler = header.handlers[0]
        handler.body = made
        statement.handlers.append(handler)
    if not statement.handlers and not statement.finalbody:
        raise Unsupported("#try without an #except or #finally")
    return statement


def _block(node: tree.Node, source: str,
           hoisted: list[ast.stmt],
               methods: list[ast.stmt],
           leading: str = "", escaped: list[str] | None = None) -> ast.stmt:
    """The statement a block directive becomes.

    A block with no children at all never opened: it is the colon short
    form, whose body sits on the directive's own line, or the ternary
    ``#if a then b else c``. Both put the body somewhere this layer does
    not look, so they are turned away rather than read wrong.

    Args:
        leading (str): A line ending the opening tag left behind, which
            is the first thing the body writes.
        escaped (list[str]|None): Where the line ending of the closing
            #end tag is put, for the caller to write after the block.
    """
    if not node.children:
        # No children means the block never opened. Two shapes do that:
        # the colon short form, whose body sits on the tag's own line,
        # and the ternary #if, which has no body at all.
        if node.name == "if":
            return _ternary_block(node)
        raise Unsupported("the one-line form of #%s" % node.name)
    if node.name == "for":
        return _for_block(node, source, hoisted, methods, leading, escaped)
    if node.name == "if":
        return _if_block(node, source, hoisted, methods, leading, escaped)
    if node.name == "try":
        return _try_block(node, source, hoisted, methods, leading, escaped)
    if node.name == "while":
        return _headed_block("while %s:", node, source, hoisted, methods,
                             leading, escaped)
    if node.name == "unless":
        # ct3 writes the parentheses, and they matter: without them
        # "#unless $a or $b" would negate only the first.
        return _headed_block("if not (%s):", node, source, hoisted, methods,
                             leading, escaped)
    if node.name == "repeat":
        # A counter nobody can collide with, named after where the
        # directive stands so that two runs give the same code.
        name = "__ct4_repeat_%d_%d" % (node.line, node.column)
        return _headed_block("for " + name + " in range(%s):", node,
                             source, hoisted, methods, leading, escaped)
    raise Unsupported("#%s" % node.name)


def _headed_block(shape: str, node: tree.Node, source: str,
                  hoisted: list[ast.stmt],
               methods: list[ast.stmt],
                  leading: str = "",
                  escaped: list[str] | None = None) -> ast.stmt:
    """A block whose header is the shape with its argument in it."""
    statement = _framed(shape % _argument(node, node))
    statement.body = _body(node, source, hoisted, methods,       # type: ignore[attr-defined]
                           leading, escaped)
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
        # addSilent is addChunk: the argument is written out as a line
        # of the method and whatever it is, it is. Usually an
        # expression whose value is dropped, and six skins write an
        # assignment there instead:
        #
        #   #silent $xaggs['aggregate_types'] = $list(filter(...))
        #
        # which is a lookup with a subscript assigned to, and perfectly
        # good Python. Read as an expression it is a syntax error.
        return [_framed_statement(_argument(node, node))]
    if node.name == "echo":
        # The same two statements a placeholder writes, but without a
        # rawExpr: addFilteredChunk is called here without one, and in
        # markup mode without a site either, because #echo writes an
        # expression the scan never saw stand anywhere.
        return _placeholder_value(
            _parsed(_argument(node, node)),
            Written(_argument(node, node), "", node.line, node.column))
    if node.name == "set":
        return [_set_statement(node)]
    if node.name == "raise":
        return [_framed_statement("raise %s" % _argument(node, node))]
    if node.name == "assert":
        # ct3 writes the statement and nothing else, so a failing
        # assertion raises out of the render the way it would out of
        # any Python. The message, where there is one, is the second
        # half of the argument and stays where it is.
        return [_framed_statement("assert %s" % _argument(node, node))]
    if node.name == "return":
        # This returns from the generated method, which means the text
        # collected so far is dropped rather than written. That is what
        # ct3 does, and a #def is where it makes sense: the method
        # returns a value instead of its output.
        #
        # And it may stand alone. ct3 writes a bare "return" for a
        # bare "#return", which is how two skins end a #def early.
        return [_framed_statement("return %s" % _without_trailing_colon(
            _read_expression(_argument_text(node)), "return",
            allow_empty=True))]
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


def _global_set(node: tree.Node) -> ast.stmt:
    """``#set global $a = 1`` writes into the template instance.

    ct3 generates self._CHEETAH__globalSetVars["a"] = 1, so the value
    outlives the method it was set in and every later lookup finds it.
    """
    left, operator, right = _assignment_parts(
        re.sub(r"^\s*global\b", "", _argument_text(node), count=1))
    primary, secondary = _global_target(left)
    return _framed_statement(
        'self._CHEETAH__globalSetVars["%s"]%s %s %s'
        % (primary, secondary, operator, right))


def _global_target(lvalue: str) -> tuple[str, str]:
    """A global target cut into the name and what hangs off it.

    ``$forecast_settings[$k]`` becomes globalSetVars["forecast_settings"]
    and then ``[k]``, which is how two skins keep a dictionary that
    outlives the method. Only the name goes in the quotes.

    ct3's own arithmetic, transliterated rather than tidied
    (Compiler.addSet). Where a dot and a bracket both stand in the
    target it takes whichever is first, except that a bracket at
    offset zero counts as no bracket at all, which is what the max()
    in the original is for.

    Returns:
        tuple[str, str]: the name, and the rest including its opening
            dot or bracket. The rest is empty for a plain name.
    """
    dot = lvalue.find(".")
    bracket = lvalue.find("[")
    if dot > 0 and bracket == -1:
        at = dot
    elif dot > 0 and dot < max(bracket, 0):
        at = dot
    else:
        at = bracket
    if at > 0:
        return lvalue[:at], lvalue[at:]
    return lvalue, ""


def _set_statement(node: tree.Node, allow_global: bool = True) -> ast.stmt:
    """``#set $a = 1`` as ``a = 1``.

    The target loses its dollar and becomes a plain name; only the
    right-hand side is looked up. ``#set global`` is a different thing
    altogether: ct3 writes it into the template instance, and there is
    no instance here.

    Cut the way eatSet cuts it: the left-hand side is one expression
    read with the name mapper off and stopped at an assignment
    operator, the operator is one token, and the rest is another
    expression. Deciding where the target ends by looking for an "="
    in a token got "$d[$k]" right and "[$s for $s in $x]" wrong.
    """
    text = _argument_text(node)
    if re.match(r"\s*global\b", text):
        if not allow_global:
            raise Unsupported("#attr global")
        return _global_set(node)
    made = _framed_statement(_assignment(text))
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
               methods: list[ast.stmt],
               leading: str = "",
               escaped: list[str] | None = None) -> ast.stmt:
    """``#for $r in $rows`` as a Python for statement.

    The targets lose their dollar and become plain names, which is what
    ct3 writes: ``for r in VFFSL(SL,"rows",True):``. Only the iterable
    is looked up.
    """
    header = _for_header(node)
    statement = _framed(header + ":")
    assert isinstance(statement, ast.For)
    # The targets are bound for the length of the body and no longer,
    # which is where ct3's indent and dedent put them.
    _scopes().append(loop_targets(header))
    try:
        statement.body = _body(node, source, hoisted, methods, leading,
                               escaped)
    finally:
        _scopes().pop()
    return statement


def _if_block(node: tree.Node, source: str,
              hoisted: list[ast.stmt],
               methods: list[ast.stmt],
              leading: str = "",
              escaped: list[str] | None = None) -> ast.stmt:
    """``#if`` with its ``#elif`` and ``#else`` branches.

    The branches are children of the if in the tree, not blocks of
    their own, so the children are cut at them and each piece becomes
    the body of one arm.

    Only the first arm can hold the opening tag's line ending and only
    the last one holds the #end, but escaped goes to every arm: which
    is the last is the tree's business, not this function's.
    """
    branches = _branch_pieces(node, source, hoisted, methods, leading,
                              escaped)
    statement = _framed("if %s:" % _argument(branches[0][0], node))
    assert isinstance(statement, ast.If)
    current = statement
    statement.body = _statements(branches[0][1])
    for directive, pieces in branches[1:]:
        body = _statements(pieces)
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

    Through the same reader and the same colon rule as every other
    directive argument. This branch had its own two lines for years and
    they were missing the colon: ``#elif $mac != "":`` came out as
    ``if ... != ""::``, which is not Python, and two cobbler templates
    were refused for it.
    """
    text = _argument_text(directive)
    if directive.name == "else":
        match = re.match(r"\s*if\b(.*)", text, re.S)
        if match is None:
            return None
        text = match.group(1)
    elif directive.name != "elif":
        return None
    if not text.strip():
        return None
    return _without_trailing_colon(_read_expression(text), directive.name)


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


def _branch_pieces(node: tree.Node, source: str, hoisted: list[ast.stmt],
                   methods: list[ast.stmt], leading: str,
                   escaped: list[str] | None,
                   at: Sequence[str] = BRANCHES,
                   ) -> list[tuple[Any, list[tuple[str, Any]]]]:
    """Each arm's pieces, with the branch tags' own lines settled.

    An #else decides about the line it stands on like any other
    directive, and the two halves of that decision land in different
    arms: the indent before it was written by the arm above, and the
    line ending after it is the first thing the arm below writes. Which
    is why this cannot be done inside _pieces_of, where each arm is
    walked without knowing what came before it.

    Missing it left the two blanks of ``  #else`` in the output, and
    neither the corpus nor the whitespace fuzz caught it: the corpus
    puts no text on a branch tag's line, and the fuzz writes its
    #except at column zero, where there is no indent to drop.
    """
    built: list[tuple[Any, list[tuple[str, Any]]]] = []
    carry = leading
    previous: list[tuple[str, Any]] | None = None
    for directive, children in _branches(node, at):
        if previous is not None:
            carry = _eat_region_line(directive, source, previous)
        pieces = _pieces_of(children, source, hoisted, methods,
                            escaped=escaped)
        if carry:
            pieces.insert(0, (TEXT_PIECE, carry))
        built.append((directive, pieces))
        previous = pieces
    return built


def _body(node: tree.Node, source: str,
          hoisted: list[ast.stmt],
               methods: list[ast.stmt],
          leading: str = "",
          escaped: list[str] | None = None) -> list[ast.stmt]:
    """The statements of a block's body, never empty.

    Python needs something between the colon and the next line, and a
    template may well have a loop that writes nothing.
    """
    pieces = _pieces_of(node.children, source, hoisted, methods,
                        escaped=escaped)
    if leading:
        pieces.insert(0, (TEXT_PIECE, leading))
    return _statements(pieces) or [ast.Pass()]


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
    """A directive's argument as Python, placeholders resolved.

    Read in one pass and not token by token, because ct3 reads it in
    one pass: getExpression knows what it is inside. A comprehension is
    where that shows. ``#set $n = [$s for $s in $items]`` is

        n = [VFFSL(SL,"s",True) for s in VFFSL(SL,"items",True)]

    in ct3, the target after the "for" plain and the one in the body
    still a lookup, which is surprising and is what ct3 does. Converting
    each placeholder on its own cannot know about the "for" and writes
    a lookup in both places, which is Python that will not parse. A
    backslash before a line ending is the same story: the reader knows
    to drop both and a token walk does not.
    """
    return _without_trailing_colon(
        _read_expression(_argument_text(directive)), owner.name)


def _argument_text(directive: tree.Node) -> str:
    """The raw source of a directive's arguments.

    Comments and the directive end token are not part of the
    expression: "#set $a = 1 ## why" is an assignment, and ct3 eats the
    comment before it looks at the expression.
    """
    parts = []
    for token in directive.tokens[1:]:
        if token.kind in (lex.PLACEHOLDER, lex.TEXT):
            parts.append(token.text)
        elif token.kind not in (lex.DIRECTIVE_END, lex.COMMENT,
                                lex.BLOCK_COMMENT):
            raise Unsupported("%s in a directive argument" % token.kind)
    return "".join(parts)


def _assignment_parts(text: str) -> tuple[str, str, str]:
    """``$a = 1`` cut the way ct3's eatSet cuts it.

    Three reads: the target with the name mapper off and stopping at
    an assignment operator, the operator, and the value.

    Returns:
        tuple[str, str, str]: the target, the operator, and the value,
            each already stripped. ct3 joins them with single blanks
            whatever the template wrote.
    """
    from Cheetah.Parser import assignmentOps

    reader = _Reader(text)
    reader.name_mapper = False
    try:
        left = reader.expression(break_at=tuple(assignmentOps)).strip()
    finally:
        reader.name_mapper = True
    operator = reader.py_token()
    if operator not in assignmentOps:
        raise Unsupported("#set without an assignment operator: %r" % text)
    right = reader.expression().strip()
    if text[reader.at:].strip():
        raise Unsupported("cannot read %r from %r"
                          % (text[reader.at:reader.at + 20], text))
    return left, operator, right


def _assignment(text: str) -> str:
    """``$a = 1`` as ``a = 1``."""
    return "%s %s %s" % _assignment_parts(text)


def _read_expression(text: str) -> str:
    """One directive argument through the expression reader.

    What is left over has to be whitespace. The reader stops at a line
    ending it is not inside brackets for, and that ending is the one
    that closed the directive.
    """
    reader = _Reader(text)
    made = reader.expression()
    if text[reader.at:].strip():
        raise Unsupported("cannot read %r from %r"
                          % (text[reader.at:reader.at + 20], text))
    return made


def _bare_word_at(text: str, word: str, start: int = 0) -> int:
    """Offset of a word standing outside brackets and outside strings.

    Returns -1 where there is none. The same scan tree._bare_words
    makes, reporting where rather than what, because the ternary form
    has to be cut at the word and not merely told that it is there.
    """
    index = start
    depth = 0
    while index < len(text):
        char = text[index]
        if char in "\"'":
            index = lex._end_of_string(text, index)
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char in lex.IDENT_START:
            begin = index
            while index < len(text) and text[index] in lex.IDENT:
                index += 1
            if depth == 0 and text[begin:index] == word:
                return begin
            continue
        index += 1
    return -1


def _ternary(node: tree.Node) -> tuple[str, str, str]:
    """``#if a then b else c`` cut into its three expressions.

    ct3 cuts on whole expression parts, so a "then" inside a string or
    a call does not count, and the words themselves are dropped. A
    second one of either is not something to guess about: ct3 would
    switch back and forth and the result would be nobody's intention.

    Returns:
        tuple[str, str, str]: The condition, what is written when it
        holds, and what is written when it does not.

    Raises:
        Unsupported: where a word stands more than once, so that the
            cut is not obvious.
    """
    text = _argument(node, node)
    then = _bare_word_at(text, "then")
    otherwise = _bare_word_at(text, "else", then + 4)
    if then < 0 or otherwise < 0:
        raise Unsupported("the one-line form of #if")
    if _bare_word_at(text, "then", then + 4) >= 0 \
            or _bare_word_at(text, "else", otherwise + 4) >= 0:
        raise Unsupported("#if with more than one then or else")
    return (text[:then], text[then + 4:otherwise], text[otherwise + 4:])


def _ternary_block(node: tree.Node) -> ast.stmt:
    """The if statement ct3 writes for the one-line form.

    Compiler.addTernaryExpr: an if around the one expression and an
    else around the other, each written the way a placeholder is. Not a
    Python conditional expression, which would evaluate the same but
    write a None the guard here drops.
    """
    condition, yes, no = _ternary(node)
    statement = _framed("if %s:" % condition.strip())
    assert isinstance(statement, ast.If)
    # No raw text for the filter: addFilteredChunk is called here
    # without one, so a filter that reads rawExpr sees nothing. What
    # the arms do carry is the position, for the refusal markup mode
    # writes for both of them.
    arm = Written("", "", node.line, node.column)
    statement.body = _placeholder_value(_parsed(yes.strip()), arm)
    statement.orelse = _placeholder_value(_parsed(no.strip()), arm)
    return statement


def _for_header(node: tree.Node) -> str:
    """``#for $r in $rows`` as ``for r in VFFSL(...)``.

    The word "for" is put back in front, because ct3 never took it out:
    eatSimpleIndentingDirective does not advance past the name for
    "for", so getExpression reads the whole header and its own rule for
    the names between a "for" and its "in" does the rest. Only the
    iterable is looked up.

    That the reader does it and not a walk over the tokens is what
    lets the iterable hold a comprehension of its own, which is how
    Sick-Beard lists its providers:

        #for $cur_provider in $providers + [$p for $p in $more if $p.id]
    """
    return _without_trailing_colon(
        _read_expression("for" + _argument_text(node)), "for")


def _without_trailing_colon(text: str, name: str,
                            allow_empty: bool = False) -> str:
    """An argument without the colon that may already close it.

    ``#for $i in range(5):`` is written both ways, and the header this
    layer builds adds a colon of its own. Two of them are a syntax
    error, and 88 corpus cases were refused for it.

    Args:
        allow_empty (bool): whether a directive with no argument at all
            is one this layer can write. Only #return is: ct3 writes a
            bare "return" for a bare "#return", and everything else
            here needs an expression to put in its header.
    """
    stripped = text.strip()
    if stripped.endswith(":"):
        stripped = stripped[:-1].rstrip()
    if not stripped and not allow_empty:
        raise Unsupported("#%s without an expression" % name)
    return stripped


def _token_source(token: lex.Token) -> str:
    """One argument token as Python source."""
    if token.kind == lex.PLACEHOLDER:
        return argument_source(token.text)
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
    else there.

    Three things decide the indent, and ct3 spells them out in this
    order. First, the whole whitespace block is guarded by ``not
    self.atEnd()``: a comment that ends the template leaves everything
    alone, indent included. Then the rest of its line is consumed where
    nothing but whitespace stands there. Only then comes ``self.atEnd()
    or self.pos() > endOfFirstLine``, and endOfFirstLine was measured
    before the comment was eaten while pos is read after that consuming
    step. So a one-line comment is already past endOfFirstLine the
    moment its line ending is taken, which is why the indent of
    ``  #* c *#`` goes when a line follows and stays when none does.
    """
    clear = _line_is_clear(source, node.tokens[0].start)
    if not clear:
        return False
    rest = source[node.tokens[-1].end:]
    if not rest:
        return False
    match = lex.EOL.search(rest)
    head = rest[:match.start()] if match else rest
    # Where ct3 stands when it asks. readToEOL runs only over a rest of
    # line that is whitespace, and it lands past the line ending.
    at = node.tokens[-1].end
    if not head.strip():
        at += match.end() if match is not None else len(rest)
    first = lex.EOL.search(source, node.tokens[0].start)
    end_of_first_line = first.start() if first is not None else len(source)
    if at >= len(source) or at > end_of_first_line:
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
    """Removes what has been written since the start of the line.

    ct3 calls it handleWSBeforeDirective and truncates its pending text
    back to the last line break in it, without asking whether what goes
    is whitespace. Usually it is: every caller here asks _line_is_clear
    first, so the source between the line start and the directive holds
    nothing else.

    Usually, not always. Two things put text into this list that no
    longer stands on the line the source says it does. A #def or #block
    carries its body off into a method, so ``L#def g`` leaves the L
    pending with the def's lines gone from the list; and the closing tag
    of a #raw block drops from a position that can be on another line
    than the pending text. ct3 deletes the L in both, because it looks
    at its buffer and not at the source.

    Reproducing that needs ct3's chunk boundaries rather than this
    layer's pieces, and they fall in different places: a run of text
    with an escape in it is one chunk there and three pieces here, and
    ct3 truncates one chunk while this walks back through as many
    pieces as it takes. So where what would go is not whitespace, the
    template is refused.
    """
    while out:
        kind, text = out[-1]
        if kind == RAW_PIECE:
            if _trailing_eol(text) or not text:
                # Nothing stands after the last line break, so ct3
                # removes nothing here either and the two agree.
                return
            # A raw body is a pending chunk like any other in ct3, and
            # this is where it would be cut. Refused rather than
            # stopped short of, which would keep text ct3 removes.
            raise Unsupported("an indent drop that reaches a #raw body")
        if kind != TEXT_PIECE:
            return
        if not text:
            # ct3 never had a chunk here: commitStrConst drops an empty
            # one. Skipping it is not walking back a chunk.
            out.pop()
            continue
        match = lex.EOL.search(text[::-1])
        if match is not None:
            keep = len(text) - match.start()
            if text[keep:].strip():
                raise Unsupported("an indent drop that removes %r"
                                  % text[keep:][:20])
            out[-1] = (TEXT_PIECE, text[:keep])
            return
        if text.strip():
            raise Unsupported("an indent drop that removes %r" % text[:20])
        # One chunk and no further. ct3 truncates
        # _pendingStrConstChunks[-1] and never looks at the one before
        # it, so where a directive has eaten a line ending and left its
        # own indent pending, that indent survives the next drop:
        # "  #encoding x" then "  #import os" writes two blanks.
        out.pop()
        return


# -- Statements ------------------------------------------------------

def _statements(pieces: list[tuple[str, Any]]) -> list[ast.stmt]:
    out: list[ast.stmt] = []
    for kind, value in pieces:
        if kind == BARRIER_PIECE:
            continue
        if kind in (TEXT_PIECE, RAW_PIECE):
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
    if _knows_local(first.name):
        # The compiler's own rewrite: hand NameMapper a namespace of
        # one instead of the whole search list.
        base = first.name.split(".")[0]
        text = 'VFN({"%s":%s},"%s",%r)%s' % (base, base, first.name,
                                             first.autocall,
                                             first.remainder)
    else:
        text = 'VFFSL(%s,"%s",%r)%s' % (SEARCH_LIST, first.name,
                                        first.autocall, first.remainder)
    for chunk in chunks[1:]:
        text = 'VFN(%s,"%s",%r)%s' % (text, chunk.name, chunk.autocall,
                                      chunk.remainder)
    return text


def _plain_expression(chunks: list[Chunk]) -> str:
    """The Python ct3 writes where the name is not looked up at all.

    Compiler.genPlainVar: the chain is written back as the dotted name
    it came from. That is what a keyword argument needs, and what the
    target of a "for" inside an expression needs.
    """
    return ".".join(chunk.name + chunk.remainder for chunk in chunks)


def _placeholder(written: Written) -> list[ast.stmt]:
    """The two statements ct3 writes for a placeholder.

    Through _parsed, because ct3 does emit Python that does not
    compile: "$a(f($x=1))" becomes f(VFFSL(SL,"x",True)=1) and "$(not
    a)" becomes VFFSL(SL,"not",True) a. Where ct3 does not compile
    there is nothing to be faithful to, and a SyntaxError out of this
    layer would crash a caller that only expects Unsupported.

    While an error catcher is on the lookup is not written here at all.
    It goes into a wrapper method and what stands here is the call.
    """
    catcher = _catcher()
    if catcher is not None and catcher.name is not None:
        return _placeholder_value(_caught(written), written)
    return _placeholder_value(_parsed(written.code), written)


def _placeholder_value(lookup: ast.expr,
                       written: Written | None = None) -> list[ast.stmt]:
    """The value first, then the write behind a guard.

    A placeholder that resolves to None writes nothing, and the filter
    never sees it. In markup mode the guard and the lookup are the same
    and only the write differs; see :func:`_filtered`.

    Args:
        written (Written|None): The placeholder this write belongs to.
            Its ``raw`` goes to the filter as rawExpr: ct3 adds that to
            every placeholder write and to nothing else, #echo
            included, so None is how a caller says "not a placeholder".
            The default filter ignores it and the corpus was blind to
            it for that reason. weewx's AssureUnicode is not: where
            str(value) raises, it writes rawExpr in its place, which is
            how "$day.foobar.min" on a page comes out as itself rather
            than as "foobar?".
    """
    raw = written.raw if written is not None else ""
    keywords = [ast.keyword(arg="rawExpr", value=ast.Constant(raw))] \
        if raw else []
    made: list[ast.stmt] = [
        _assign(VALUE, lookup),
        ast.If(
            test=ast.Compare(left=ast.Name(id=VALUE, ctx=ast.Load()),
                             ops=[ast.IsNot()],
                             comparators=[ast.Constant(None)]),
            body=[_write(_filtered(written, keywords))],
            orelse=[]),
    ]
    if written is not None:
        # The lookup is where a template raises, so this is the origin
        # that matters most: a page that says "line 4, column 3" over
        # a name that does not resolve is the whole point of keeping
        # the origins at all.
        for statement in made:
            _at(statement, written.line, written.column)
    return made


def _filtered(written: Written | None,
              keywords: list[ast.keyword]) -> ast.expr:
    """What the value is written through, which is where the mode shows.

    In text mode the filter call itself, the same node it has always
    been. In markup mode a wrapper around the filter, chosen once here
    from the position the scan put the placeholder in, so that nothing
    at render time has to decide anything.

    The wrapper takes the filter as an argument rather than the escape
    being a filter or an argument to one. A filter is the application's
    and never learns the position: weewx replaces the whole filter
    library, so ct3's escaping filters are not even reachable there,
    and a filter written ``def filter(self, val, rawExpr=None)`` dies
    on a keyword it does not know. So ``rawExpr`` keeps exactly the
    shape it has in text mode and travels through the wrapper
    untouched.

    Args:
        written (Written|None): The placeholder, for the site to look
            up and for the position a refusal names.
        keywords (list[ast.keyword]): What goes to the filter, which is
            rawExpr or nothing.

    Returns:
        ast.expr: The call whose value is written.
    """
    value = ast.Name(id=VALUE, ctx=ast.Load())
    filter_name = ast.Name(id=FILTER, ctx=ast.Load())
    state = _markup()
    if state is None:
        return ast.Call(func=filter_name, args=[value], keywords=keywords)
    site = state.sites.get(written.start) if written is not None else None
    if site is not None and site.context in MARKUP_ESCAPES:
        if site.context == markup_scan.URL_HEAD:
            state.url(site)
        return ast.Call(func=ast.Name(id=MARKUP_ESCAPED, ctx=ast.Load()),
                        args=[value, filter_name], keywords=keywords)
    # Everything else, the placeholder the scan could not place
    # included: the value has to prove it is markup or the render
    # stops. Fail closed, never a guessed escape.
    return ast.Call(func=ast.Name(id=MARKUP_VERBATIM, ctx=ast.Load()),
                    args=[value, filter_name,
                          ast.Constant(state.refuse(site, written))],
                    keywords=keywords)


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
