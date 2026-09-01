"""Cheetah source as a lossless tree of tokens.

Lossless means what it says: every byte of the source sits in exactly
one leaf, and joining the leaves gives the source back. That assertion
is cheap to state and hard to cheat, and it is checked over all 1,772
corpus templates.

It is not enough on its own. A lexer that calls the whole file one text
token passes it and is worth nothing. The second assertion is what
gives it teeth: every name the real compiler resolves has to appear as
a PLACEHOLDER token here. ``ct4.analyze`` reads those out of the code
the compiler generates, so the ground truth comes from the
implementation rather than from a second opinion of my own.

A tree and not a flat list, because ``$func($anInt)`` holds two
lookups. Flattened, the inner one would be invisible to every layer
above; split into separate tokens, a ``#`` inside a Python expression
would start reading like a directive. Nesting keeps both straight:
inside a placeholder only placeholders are looked for.

What this layer does not do is decide what anything means. A directive
token is the hash and the name; its arguments stay in the stream. Where
those arguments end is a question about grammar, and grammar belongs to
the layer above.
"""

from __future__ import annotations

import bisect
import functools
import re
import string
from dataclasses import dataclass, field
from typing import Iterator, Sequence

# What the parser calls identchars.
IDENT_START = frozenset(string.ascii_letters + "_")
IDENT = IDENT_START | frozenset(string.digits)

# Characters a directive name may consist of, from
# Parser._LowLevelParser.matchDirectiveName.
NAME_CHARS = IDENT | frozenset("-@")

TEXT = "text"
PLACEHOLDER = "placeholder"
# The head of a placeholder that has something nested in it: the
# dollar, its modifiers and the dotted name. Only appears as a child,
# so that counting PLACEHOLDER tokens counts lookups and not halves.
NAME = "name"
DIRECTIVE = "directive"
DIRECTIVE_END = "directive_end"
COMMENT = "comment"
BLOCK_COMMENT = "block_comment"
PSP = "psp"
ESCAPE = "escape"
RAW = "raw"
# A hash alone at the end of a line. It writes nothing and takes the
# line ending with it, which is how a template breaks a long directive
# argument over several lines without putting blanks in the output.
EOL_SLURP = "eol_slurp"

# Cheetah's own EOLre. Templates with old Mac line endings are in the
# corpus, and a lexer that only knows "\n" swallows such a file whole
# from the first directive on.
EOL = re.compile(r"\r\n|\r|\n")

# ct3's EOLSlurpRE: the token, optional blanks, then a line ending.
SLURP = re.compile(r"#[ \t\f]*(?:\r\n|\r|\n)")

# A placeholder start, as Parser._makeCheetahVarREs builds it: the
# dollar, an optional silence token, an optional cache token, an
# optional enclosure, and then a name must follow.
START = re.compile(
    r"(?<!\\)\$"
    r"(?P<silent>!?)"
    r"(?P<cache>\*(?:[0-9.]+[smhdw]?\*)?|)"
    r"(?P<enclosure>[{(\[][ \t\f]*|)"
    r"(?=[A-Za-z_])")

# ct3's second placeholder start, expressionPlaceholderStartRE: no
# silence token, an optional cache token, one of the three enclosures,
# blanks, and then anything that is not a closer. What the enclosure
# holds is an expression instead of a name, which is the only
# difference from START. "$(6)" and "$('#id')" are placeholders; the
# second is why a jQuery call in a page comes out as "#id".
#
# ct3 tries this one only where it is scanning text. Inside an
# expression it looks for a bare name and nothing else, so "#if $(6)"
# leaves the dollar as a character. This lexer has no way to know which
# it is in, because a directive's arguments stay in the stream as
# ordinary tokens. So it lexes both alike and the layer that does know
# turns the second away.
EXPRESSION_START = re.compile(
    r"(?<!\\)\$"
    r"(?P<silent>)"
    r"(?P<cache>\*(?:[0-9.]+[smhdw]?\*)?|)"
    r"(?P<enclosure>[{(\[][ \t\f]*)"
    r"(?=[^)}\]])")

CLOSING = {"{": "}", "(": ")", "[": "]"}


def start_of(text: str) -> re.Match[str] | None:
    """The placeholder start at the beginning of text, either form.

    Called as ``start_of(token.text)``. The groups are the same in both
    patterns, so a caller reading ``cache`` or ``enclosure`` needs to
    know which one matched only where it cares about the difference,
    and the only one that does is the layer that turns the expression
    form away inside a directive argument.
    """
    return START.match(text) or EXPRESSION_START.match(text)


# Blocks closed by _eatToThisEndDirective rather than by
# eatEndDirective. The difference is how far their "#end" tag reaches,
# which is a question about where the tokens are, so it is settled
# here; the layer above reads the same set for its own purposes.
SELF_CLOSING = frozenset({"raw", "compiler-settings", "defmacro", "i18n"})


def self_closing_end(source: str, after_end: int) -> int | None:
    """Where an ``#end raw`` stops, or None for every other end.

    Called with the offset just past the ``#end`` token itself.

    Two eaters, and they end differently. eatEndDirective reads an
    expression after the name and throws it away, so ``#end for $i``
    writes nothing and the ``$i`` is not output.
    _eatToThisEndDirective, which closes raw and the three other
    self-closing blocks, stops right after the name and the blanks
    behind it, so what follows on the line is output. Three skins write

        #raw $uomtemp = #end raw '$unit.unit_type.outTemp[-1:]';

    where the placeholder inside the quotes really is resolved.

    After the name goes a directive end token where there is one, or
    the line ending where the tag stood alone on its line.
    """
    at = after_end
    while at < len(source) and source[at] in " \t\f":
        at += 1
    start = at
    while at < len(source) and source[at] in NAME_CHARS:
        at += 1
    if source[start:at] not in SELF_CLOSING:
        return None
    while at < len(source) and source[at] in " \t\f":
        at += 1
    return _rest_of_tag(source, at, after_end - len("#end"))


def identifier_end(source: str, after_name: int) -> int:
    """Where a directive that takes one identifier stops.

    ``#errorCatcher`` is the one: eatErrorCatcher calls getIdentifier
    and reads nothing else, so a line that merely mentions it,

        // this file sets #errorCatcher Echo, so Cheetah does not

    is the directive and then the text ", so Cheetah does not". Reading
    the argument to the end of the line made a name of the whole
    sentence and cost a skin its file.

    Called with the offset just past the directive name.
    """
    at = after_name
    while at < len(source) and source[at] in " \t\f":
        at += 1
    while at < len(source) and source[at] in IDENT:
        at += 1
    return _rest_of_tag(source, at, after_name - len("#errorCatcher"))


def _rest_of_tag(source: str, at: int, tag_start: int) -> int:
    """What a directive tag takes after its argument has been read.

    ct3's _eatRestOfDirectiveTag: a directive end token where one
    stands there, or the line ending where the tag stood alone on its
    line. Where neither, the tag stops and the rest of the line is
    output.
    """
    if source[at:at + 1] == "#":
        return at + 1
    starts = line_starts(source)
    line = bisect.bisect_right(starts, tag_start)
    if not source[starts[line - 1]:tag_start].strip():
        match = EOL.match(source, at)
        if match is not None:
            return match.end()
    return at


def directive_names() -> frozenset[str]:
    """The directive names ct3 knows, read from ct3.

    Taken at call time rather than copied, because a copy drifts. Both
    sources matter: ``directiveNamesAndParsers`` holds the ordinary
    ones, and ``i18n`` is registered separately as a macro directive.
    Miss it and ``#i18n: $x`` lexes as plain text.
    """
    from Cheetah.Parser import directiveNamesAndParsers

    return frozenset(directiveNamesAndParsers) | {"i18n"}


@dataclass(frozen=True)
class Token:
    """One piece of the source, and where it stood.

    ``line`` and ``column`` count from one, as every Cheetah error
    message does. ``children`` is empty for a leaf; where it is not,
    the text of this token is the text of its children joined, and only
    the children carry source.
    """

    kind: str
    text: str
    start: int
    line: int
    column: int
    children: tuple["Token", ...] = field(default=())

    @property
    def end(self) -> int:
        return self.start + len(self.text)

    def leaves(self) -> Iterator["Token"]:
        if not self.children:
            yield self
            return
        for child in self.children:
            yield from child.leaves()


def tokens(source: str) -> list[Token]:
    """The whole source as tokens, in order."""
    return _Lexer(source).run()


def leaves(items: Sequence[Token]) -> Iterator[Token]:
    for token in items:
        yield from token.leaves()


def walk(items: Sequence[Token]) -> Iterator[Token]:
    """Every token, parents before their children."""
    for token in items:
        yield token
        yield from walk(token.children)


def joined(items: Sequence[Token]) -> str:
    """The source the tokens came from."""
    return "".join(token.text for token in leaves(items))


@functools.lru_cache(maxsize=8)
def line_starts(source: str) -> tuple[int, ...]:
    """Offset of the first character of every line.

    Public because the layer above splits tokens at line endings and
    has to give the halves the right position. Computing that from a
    count of newlines is the mistake this file already made once: a
    template with old Mac line endings holds none at all.

    Cached, and a tuple so that a caller cannot write into the cache.
    The layer above asks whether a line is clear once per directive and
    once per placeholder, and each of those asks for this: over the 390
    skin templates it was called ten thousand times and scanned the
    whole source each time, two seconds of the six the generator took.
    A string remembers its own hash, so the lookup is cheap after the
    first call on it.
    """
    starts = [0]
    for match in EOL.finditer(source):
        starts.append(match.end())
    return tuple(starts)


def where(starts: Sequence[int], offset: int) -> tuple[int, int]:
    """Line and column of an offset, both counting from one."""
    line = bisect.bisect_right(starts, offset)
    return line, offset - starts[line - 1] + 1


def split(token: Token, at: int, starts: Sequence[int]) -> tuple[Token,
                                                                Token]:
    """Cuts a leaf token in two at an offset inside it.

    Called as ``split(token, offset, starts)`` where ``offset`` counts
    from the start of the source, not of the token. Returns the part
    before and the part from there on; either can be empty of content
    only if the caller asked for that.

    Only for leaves. A token with children owns no text of its own to
    cut.
    """
    if token.children:
        raise ValueError("cannot split a token that has children")
    cut = at - token.start
    line, column = where(starts, at)
    first = Token(token.kind, token.text[:cut], token.start,
                  token.line, token.column)
    second = Token(token.kind, token.text[cut:], at, line, column)
    return first, second


def path_of(token: Token) -> str:
    """The dotted name a placeholder token reads.

    Called as ``path_of(token)`` on a PLACEHOLDER token. Returns
    ``"day.outTemp.max"`` for ``$day.outTemp.max`` and for
    ``${day.outTemp.max}``, and stops where a call or a subscript
    starts: ``$day.rain.format($fmt)`` gives ``day.rain.format``.

    Longer than what NameMapper resolves in one go, on purpose. The
    compiler splits ``$a.b.c(x)`` into a lookup of ``a.b`` and a call
    of ``c``, so what it reports is a prefix of this.
    """
    text = token.text
    match = START.match(text)
    if match is None:
        return ""
    index = match.end()
    name = ""
    while index < len(text):
        char = text[index]
        if char in IDENT or (char == "." and name):
            name += char
            index += 1
            continue
        break
    return name.rstrip(".")


class _Lexer:
    """Scans once, left to right, and never backs up over a decision."""

    def __init__(self, source: str):
        self.source = source
        self.names = directive_names()
        self.line_starts = [0]
        for match in EOL.finditer(source):
            self.line_starts.append(match.end())

    # -- position ---------------------------------------------------

    def where(self, offset: int) -> tuple[int, int]:
        line = bisect.bisect_right(self.line_starts, offset)
        return line, offset - self.line_starts[line - 1] + 1

    def make(self, kind: str, start: int, end: int,
             children: Sequence[Token] = ()) -> Token:
        line, column = self.where(start)
        return Token(kind, self.source[start:end], start, line, column,
                     tuple(children))

    # -- the scan ---------------------------------------------------

    def run(self) -> list[Token]:
        source = self.source
        found: list[Token] = []
        index = 0
        text_from = 0
        # Where the argument text of the current directive ends. Inside
        # it a "##" is not a comment: "#if 1##for x in y#" is an if
        # that ends at the hash and a for that starts at the next one.
        directive_until = -1

        def flush(upto: int) -> None:
            nonlocal text_from
            if upto > text_from:
                found.append(self.make(TEXT, text_from, upto))
            text_from = upto

        while index < len(source):
            char = source[index]

            if char == "\\" and index + 1 < len(source) \
                    and source[index + 1] in "$#":
                flush(index)
                found.append(self.make(ESCAPE, index, index + 2))
                index = text_from = index + 2
                continue

            # A backslash in front of it means it is no PSP at all:
            # Parser._makePspREs builds both PSP tokens with
            # escCharLookBehind. The backslash is not removed the way
            # the one in front of a "$" is, so "a\\<%= 1 %>b" comes out
            # as itself.
            if char == "<" and source.startswith("<%", index) \
                    and not (index and source[index - 1] == "\\"):
                end = _psp_end(source, index)
                flush(index)
                found.append(self.make(PSP, index, end))
                index = text_from = end
                continue

            if index < directive_until and char in "\"'":
                # A string literal in a directive's arguments is one
                # token to ct3: getPyToken takes the whole of it, so
                # the branch that ends a directive at a bare hash is
                # never reached inside one. Without this a CSS colour
                # closes the directive it stands in, and 21 templates
                # were refused because they set a list of them.
                #
                # It also settles the dollar: ct3 copies a string
                # literal verbatim and does not look for placeholders
                # in it, and neither does this.
                closed = string_span(source, index)
                if closed is not None:
                    index = closed
                    continue

            if char == "#":
                inside = index < directive_until
                made = self.hash_at(index, inside)
                if made is not None:
                    kind, end, name = made
                    flush(index)
                    found.append(self.make(kind, index, end))
                    index = text_from = end
                    if name == "raw":
                        stop = self.raw_end(end)
                        if stop > end:
                            found.append(self.make(RAW, end, stop))
                            index = text_from = stop
                        directive_until = -1
                    elif kind == DIRECTIVE:
                        # An "#end raw" reaches only to the end of the
                        # name, and what follows is output. Without
                        # this the string in "#end raw '$unit.x'" is
                        # stepped over as a directive's own literal and
                        # the placeholder in it never becomes a token.
                        if name == "end":
                            reach = self_closing_end(source, end)
                        elif name == "errorCatcher":
                            reach = identifier_end(source, end)
                        else:
                            reach = None
                        directive_until = (reach if reach is not None
                                           else self.argument_end(end))
                    elif kind == DIRECTIVE_END:
                        directive_until = -1
                    continue

            if char == "$":
                token = self.placeholder_at(index)
                if token is not None:
                    flush(index)
                    found.append(token)
                    index = text_from = token.end
                    continue

            index += 1

        flush(len(source))
        return found

    def raw_end(self, index: int) -> int:
        """Where the contents of a raw block stop.

        Two forms, and they end in different places. ``#raw: one line``
        is raw to the end of that line and no further; the corpus has a
        case where the line after it holds a placeholder that really is
        resolved. ``#raw`` on its own runs to its ``#end raw``, which
        is source again and gets scanned like any other directive.
        """
        source = self.source
        if index < len(source) and source[index] == ":":
            match = EOL.search(source, index)
            return len(source) if match is None else match.start()
        stop = source.find("#end raw", index)
        return len(source) if stop < 0 else stop

    def argument_end(self, index: int) -> int:
        """Where the arguments of the directive just read stop.

        At the ending that closes the directive, or at the directive
        end token, which is a bare hash outside a string. Only used to
        keep a following "##" from reading as a comment and to know
        where a string literal is one; nothing is cut here.
        """
        source = self.source
        line_end = line_that_closes(source, index)
        while index < line_end:
            char = source[index]
            if char in "\"'":
                closed = string_span(source, index)
                if closed is not None:
                    index = closed
                    continue
            if char == "#":
                return index + 1
            index += 1
        return line_end

    def hash_at(self, index: int,
                inside_directive: bool) -> tuple[str, int, str] | None:
        """What a ``#`` starts here, where it ends, and its name.

        Returns None where the hash is ordinary text, which is what it
        is in a CSS file and in a colour literal.
        """
        source = self.source
        if source.startswith("##", index):
            # Inside a directive's arguments a double hash is two
            # different things, and what follows decides which. In
            # "#if 1##for i in [1]#" the first hash closes the if and
            # the second opens a for. In "#def name: ## comment" it is
            # a comment, because "# comment" begins no directive.
            if inside_directive and _directive_name(source, index + 2,
                                                    self.names):
                return DIRECTIVE_END, index + 1, ""
            match = EOL.search(source, index)
            end = len(source) if match is None else match.end()
            return COMMENT, end, "##"
        if source.startswith("#*", index):
            return BLOCK_COMMENT, _end_of_block_comment(source, index), "#*"
        name = _directive_name(source, index + 1, self.names)
        if name is None:
            if inside_directive:
                # The hash that closes the directive we are inside.
                return DIRECTIVE_END, index + 1, ""
            # Last of all, the way ct3 orders its matchers: a directive
            # always wins over the slurp token.
            match = SLURP.match(source, index)
            if match is not None:
                return EOL_SLURP, match.end(), ""
            return None
        return DIRECTIVE, index + 1 + len(name), name

    def placeholder_at(self, index: int) -> Token | None:
        """The placeholder starting here, with what nests inside it.

        Returns None where the dollar is not a placeholder at all,
        which is what a price in a text and a jQuery call both are.
        """
        source = self.source
        match = START.match(source, index) or EXPRESSION_START.match(source,
                                                                    index)
        if match is None:
            return None
        opener = match.group("enclosure")[:1]
        end = None
        if opener:
            end = _balanced(source, match.end("cache"), opener)
        if end is None:
            end = _end_of_chain(source, match.end())
        head_end = _end_of_name(source, match.end())
        nested = self.inner(head_end, end)
        if not nested:
            return self.make(PLACEHOLDER, index, end)
        head = self.make(NAME, index, head_end)
        return self.make(PLACEHOLDER, index, end, [head] + nested)

    def inner(self, start: int, end: int) -> list[Token]:
        """What nests inside a placeholder, between start and end.

        Only placeholders are looked for. Inside an expression a hash
        is a Python comment or part of a string, never a directive, and
        a scanner that forgot this would read ``${a['#for']}`` as
        source.

        Returns nothing where nothing nests, and then the placeholder
        stays a leaf.
        """
        found: list[Token] = []
        index = start
        text_from = start
        while index < end:
            if self.source[index] == "$":
                token = self.placeholder_at(index)
                if token is not None and token.end <= end:
                    if index > text_from:
                        found.append(self.make(TEXT, text_from, index))
                    found.append(token)
                    index = text_from = token.end
                    continue
            index += 1
        if not found:
            return []
        if end > text_from:
            found.append(self.make(TEXT, text_from, end))
        return found


def _directive_name(source: str, index: int,
                    names: frozenset[str]) -> str | None:
    """The directive name at this position, following ct3's rule.

    Longest prefix that is a known name and is not itself followed by a
    name character. ``@`` is its own case: a decorator, and only when a
    name follows it.
    """
    possible = names
    name = ""
    while index < len(source):
        char = source[index]
        if char not in NAME_CHARS:
            break
        name += char
        index += 1
        if name == "@":
            if index < len(source) and source[index] in IDENT_START:
                return "@"
            return None
        possible = frozenset(n for n in possible if n.startswith(name))
        if not possible:
            return None
        following = source[index] if index < len(source) else ""
        if name in possible and following not in NAME_CHARS:
            return name
    return None


def _psp_end(source: str, index: int) -> int:
    """Past the ``%>`` that closes the PSP starting at this position.

    The closing token carries the same escape look-behind as the
    opening one, so a ``%>`` with a backslash in front of it closes
    nothing and eatPSP reads on: ``<% write('a') #\\%> junk %>`` is one
    PSP whose body ends at the second one. Runs to the end of the
    source where nothing closes it, and the layer above refuses that.
    """
    at = index + 2
    while True:
        found = source.find("%>", at)
        if found < 0:
            return len(source)
        if source[found - 1] != "\\":
            return found + 2
        at = found + 1


def _end_of_block_comment(source: str, index: int) -> int:
    """Past the ``*#`` that closes the ``#*`` at this position.

    They nest, which is why the levels are counted rather than the
    first closer taken: ct3's eatMultiLineComment does the same. An
    unterminated one runs to the end of the file.
    """
    level = 0
    length = len(source)
    while index < length:
        if source.startswith("#*", index):
            level += 1
            index += 2
            continue
        if source.startswith("*#", index):
            level -= 1
            index += 2
            if not level:
                return index
            continue
        index += 1
    return length


def _end_of_name(source: str, index: int) -> int:
    """The dotted name alone, stopping before any bracket."""
    length = len(source)
    while index < length and source[index] in IDENT:
        index += 1
    while index < length and source[index] == "." \
            and index + 1 < length and source[index + 1] in IDENT_START:
        index += 2
        while index < length and source[index] in IDENT:
            index += 1
    return index


def dotless_links(source: str, index: int = 0) -> list[int]:
    """Where a chain carries on over a bare name, by offset.

    One rule, one implementation: this runs the same walk the lexer
    runs and collects what the walk stepped over. The check reports
    these, because the position is a trap. ``$temp.formatted(2)F`` is
    an attribute lookup for ``F`` and not the letter F in the output,
    and nothing about the rendered page says so.
    """
    marks: list[int] = []
    _end_of_chain(source, index, marks)
    return marks


def _end_of_chain(source: str, index: int,
                  marks: list[int] | None = None) -> int:
    """A name and what hangs off it: ``.attr``, ``(args)``, ``[key]``.

    Two rules that are not obvious and are both ct3's, measured off it
    rather than read out of its docstring.

    A bare name after a bracket continues the chain as a dot would.
    ct3's loop head is ``self.peek() not in identchars + '.'``, so
    ``$f(1)upper`` is one chain and compiles exactly like
    ``$f(1).upper``. weewx's own test skin has a line that relies on
    it, and by the look of it not on purpose: it writes
    ``.round(5)json()`` where the label above it says
    ``.round(5).json()``.

    And which bracket comes first decides how far the brackets reach.
    ct3 reads a "(" with getCallArgString, which takes that one group
    and no more, so ``$a(1)[2]`` is a placeholder and then the text
    ``[2]``. It reads a "[" with getExpression, whose loop opens the
    next group before it tests whether the expression has ended, so
    ``$a[1](2)[3]`` is all one subscript.
    """
    length = len(source)
    while index < length and source[index] in IDENT:
        index += 1
    while index < length:
        char = source[index]
        if char == "." and index + 1 < length \
                and source[index + 1] in IDENT_START:
            index += 2
            while index < length and source[index] in IDENT:
                index += 1
            continue
        if char in IDENT_START:
            # Only reachable behind a bracket: the runs above have
            # already taken every name character there was.
            if marks is not None:
                marks.append(index)
            while index < length and source[index] in IDENT:
                index += 1
            continue
        if char in "([":
            closed = _balanced(source, index, char)
            if closed is None:
                return index
            index = closed
            if char == "[":
                while index < length and source[index] in "{([":
                    closed = _balanced(source, index, source[index])
                    if closed is None:
                        return index
                    index = closed
            elif index < length and source[index] in "{([":
                return index
            continue
        break
    return index


def _balanced(source: str, index: int, opening: str) -> int | None:
    """Past the bracket that closes the one at ``index``.

    Strings are stepped over, because a bracket inside one closes
    nothing. Returns None where nothing closes it, and then the caller
    treats what it has as the end.
    """
    closing = CLOSING[opening]
    depth = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char in "\"'":
            index = _end_of_string(source, index)
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        elif char == "\n" and opening != "(":
            return None
        index += 1
    return None


def line_that_closes(source: str, start: int) -> int:
    """Offset just past the line ending that closes a directive.

    Not the first line ending. ct3's getExpressionParts opens a bracket
    before it tests whether the expression has ended, and that test is
    never reached while one is open, so a line ending inside brackets
    is read and thrown away and the expression carries on. The skins
    write their lists that way and so does jas:

        #set $params = [
            {'name': 'barometer', 'agg': None},
            {'name': 'outTemp', 'agg': None},
        ]

    is one directive whose argument holds no line ending at all. ct3
    writes it out as ``params = [    {...},    {...},]``: the endings
    are gone and the indent that stood after each of them is still
    there, harmlessly, inside the brackets.

    Returns the end of the source where a bracket is still open at the
    end of it. ct3 raises a ParseError there and the caller will refuse
    what it cannot read, which is the same outcome by a shorter road.
    """
    depth = 0
    index = start
    length = len(source)
    while index < length:
        char = source[index]
        if char in "\"'":
            closed = string_span(source, index)
            if closed is not None:
                index = closed
                continue
        if char == "\\":
            # getExpressionParts drops a backslash that stands before a
            # line ending, and the ending with it, so the expression
            # carries on whether or not a bracket is open. jas ends five
            # of its #if lines that way. A backslash anywhere else is
            # one character like any other.
            match = EOL.match(source, index + 1)
            index = match.end() if match is not None else index + 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            # Never below zero: a stray closer in a directive's
            # arguments is the template's problem, and letting it open
            # a negative depth would make the next line ending close
            # nothing.
            depth = max(depth - 1, 0)
        elif depth == 0:
            match = EOL.match(source, index)
            if match is not None:
                return match.end()
        index += 1
    return length



def string_span(source: str, index: int) -> int | None:
    """Past the string literal starting here, or None if it is not one.

    Python has no one-line string that crosses a line ending, so the
    apostrophe in ``//today's`` opens nothing. A scan that took it for
    a quote ran on to the next apostrophe, wherever in the file that
    was, and a real skin has one on a line that reads

        #raw $todayhihumidex = #end raw '$day...'; //today's high

    Called where a scan has to step over a literal and must not walk
    off the end of the world when the quote was prose.
    """
    quote = source[index]
    triple = source.startswith(quote * 3, index)
    end = _end_of_string(source, index)
    if end >= len(source) and not source.endswith(quote, 0, end):
        return None
    if not triple and EOL.search(source, index, end):
        return None
    return end


def _end_of_string(source: str, index: int) -> int:
    quote = source[index]
    triple = source.startswith(quote * 3, index)
    marker = quote * 3 if triple else quote
    index += len(marker)
    length = len(source)
    while index < length:
        if source[index] == "\\":
            index += 2
            continue
        if source.startswith(marker, index):
            return index + len(marker)
        index += 1
    return length
