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

CLOSING = {"{": "}", "(": ")", "[": "]"}


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


def line_starts(source: str) -> list[int]:
    """Offset of the first character of every line.

    Public because the layer above splits tokens at line endings and
    has to give the halves the right position. Computing that from a
    count of newlines is the mistake this file already made once: a
    template with old Mac line endings holds none at all.
    """
    starts = [0]
    for match in EOL.finditer(source):
        starts.append(match.end())
    return starts


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

            if char == "<" and source.startswith("<%", index):
                flush(index)
                end = source.find("%>", index + 2)
                end = len(source) if end < 0 else end + 2
                found.append(self.make(PSP, index, end))
                index = text_from = end
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
                        directive_until = self.argument_end(end)
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

        At the end of the line, or at the directive end token, which is
        a bare hash. Only used to keep a following "##" from reading as
        a comment; nothing is cut here.
        """
        match = EOL.search(self.source, index)
        line_end = len(self.source) if match is None else match.start()
        hash_at = self.source.find("#", index)
        if 0 <= hash_at < line_end:
            return hash_at + 1
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
        match = START.match(source, index)
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


def _end_of_chain(source: str, index: int) -> int:
    """A name and what hangs off it: ``.attr``, ``(args)``, ``[key]``."""
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
        if char in "([":
            closed = _balanced(source, index, char)
            if closed is None:
                return index
            index = closed
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
