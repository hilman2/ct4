"""Where a placeholder stands in the emitted HTML, or a refusal.

Markup mode escapes exactly two positions, element text and a quoted
attribute value, and demands proof everywhere else. Deciding which of
the two a placeholder is in is this module's whole job, and so is
saying "I cannot tell" loudly enough that the compiler fails instead of
guessing.

Four answers, and three of them are a position:

``TEXT``
    Data state. Content of a ``<p>``, of a ``<table>``, of anything a
    normal HTML tokenizer reads as character data.
``ATTRIBUTE``
    Inside a single- or double-quoted attribute value whose name is
    neither an event handler nor ``style``.
``URL_HEAD``
    The same, escaped the same way, but the placeholder is the first
    thing in the value of ``href``, ``src``, ``action``, ``formaction``
    or ``xlink:href``. A separate answer so the caller can warn: no
    character in ``javascript:alert(1)`` is HTML-special, so escaping
    cannot stop it.
``VERBATIM``
    A position that cannot be escaped. The caller compiles a runtime
    demand for ``__html__`` there and raises rather than guessing.

:func:`scan` returns a :class:`Site` for every placeholder that reaches
the output, keyed by ``Token.start``. A placeholder nested inside
another placeholder writes nothing, and neither does one standing in a
directive's arguments, so neither gets a Site. **A caller that looks a
placeholder up and finds no Site must treat it as VERBATIM.** That is
the fail-closed default on both sides of the API: this module reports
what it could place, and silence means it could not.

Two things about the scan that are not obvious and cost a day each if
they are guessed at.

It runs over the block tree and never over the token stream. A
directive keeps its arguments as ordinary TEXT tokens, so the ``<`` in
``#if $delta < 60`` opens a tag as far as the tokens are concerned.
Measured over the corpus: scanning tokens reports 1444 placeholders in
attribute-name position where scanning the tree reports 6.

And it runs over the bytes the template *emits*, not the bytes it
contains. ct3's whitespace rules move whole lines: the line ending
after a directive that does not stand alone on its line is kept, the
indentation in front of one that does is dropped, ``#slurp`` eats the
line ending wherever it stands, and the line ending after an ``#else``
belongs to the branch that ``#else`` opens. :func:`emitted` is the
reconstruction, and it is held against ct3's own output byte for byte
in tests/unit/test_markup_scan.py.

A fragment has no opening context, and this is a documented limit
rather than a solved problem. Every file is scanned from a fresh data
state. An ``.inc`` that begins inside a ``<table>`` is fine, because
table content is data state and wants the same escaping as any other
element text. A fragment that genuinely begins inside a tag, an
attribute or a ``<script>`` would be silently mis-escaped, nothing in
the file can reveal that, and the end-of-file refusal below is the only
half a per-file scanner can check: it catches the producer of such a
fragment, never its consumer. A file that is pulled into a non-data
position must stay in text mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ct4.lang import lex, tree
from ct4.lang.lex import Token

TEXT = "element text"
ATTRIBUTE = "quoted attribute value"
URL_HEAD = "head of a URL attribute"
VERBATIM = "a position that cannot be escaped"

# Attributes whose value starts with a URL. Only the head of the value
# matters: further in, a scheme can no longer be introduced.
URL_ATTRIBUTES = frozenset({"href", "src", "action", "formaction",
                            "xlink:href"})

# Elements whose content is raw text. A character reference is not
# decoded in there, so an HTML escape does not merely fail to help, it
# corrupts.
RAW_TEXT = frozenset({"script", "style"})

# What ends a tag name or an attribute name.
SPACE = " \t\n\r\f"


@dataclass(frozen=True)
class Site:
    """One placeholder, and where in the emitted HTML it stands.

    Attributes:
        start (int): ``Token.start`` of the placeholder, which is the
            key every caller looks it up by.
        line (int): Line of the placeholder in the source, from one.
        column (int): Column of the placeholder in the source, from one.
        context (str): One of TEXT, ATTRIBUTE, URL_HEAD, VERBATIM.
        note (str): The position in words, for the message a refusal
            shows the template author: ``'inside <script>'``, ``'in an
            onclick= attribute'``, ``'in the body of #def graph'``.
    """

    start: int
    line: int
    column: int
    context: str
    note: str


class ScanRefused(Exception):
    """The scan cannot be trusted over this file, so nothing is escaped.

    Whole-file rather than per-placeholder, because what goes wrong
    here goes wrong for everything after it: a tag left open at the end
    of the file means the scanner and the browser disagree about where
    the tags are, and a coarse scanner reads CDATA as element text with
    full confidence.

    Attributes:
        line (int): Where the reason stands, from one.
        column (int): Where the reason stands, from one.
        reason (str): What a template author can act on.
    """

    def __init__(self, line: int, column: int, reason: str) -> None:
        super().__init__("%s (line %d, column %d)" % (reason, line, column))
        self.line = line
        self.column = column
        self.reason = reason


# -- The pieces a template writes ------------------------------------
#
# Worked out as a flat list first and scanned after, because ct3's
# indent rule reaches backwards into what has already been written and
# doing that to a list is plain where doing it to a stream is not.
# ct4/lang/codegen.py builds the same list for a different purpose and
# the rules below are read off it; where the two disagree the generated
# Python is the authority, because that is what renders.

_TEXT_PIECE = "text"
_VALUE_PIECE = "value"
# Alternatives, exactly one of which is written: the arms of an #if, an
# #unless or a #try.
_BRANCHES_PIECE = "branches"
# A body that may be written any number of times, a #for or a #while.
_LOOP_PIECE = "loop"
# A body written somewhere else than where it stands: a #def is called
# from elsewhere, a #call body becomes an argument. Its bytes never
# reach this position, so it changes no state, and every placeholder in
# it is VERBATIM.
_ELSEWHERE_PIECE = "elsewhere"
# An #include. It writes content this scan cannot see.
_INCLUDE_PIECE = "include"
# A PSP token. The body of a PSP block is Python spliced as it stands.
_PSP_PIECE = "psp"

# Directives that continue the block above them rather than opening one.
_BRANCH_NAMES = frozenset({"else", "elif", "except", "finally"})

# Blocks whose body is written where it is called instead of where it
# stands. #block is not among them: ct3 writes the call for a #block at
# the point the tag stands, so its bytes really do land there.
_ELSEWHERE_BLOCKS = frozenset({"def", "call", "defmacro",
                               "compiler-settings"})

_LOOP_BLOCKS = frozenset({"for", "while", "repeat"})
_BRANCH_BLOCKS = frozenset({"if", "unless", "try"})


@dataclass
class _Piece:
    """One step of the output, as much of it as the scan needs."""

    kind: str
    text: str = ""
    token: Token | None = None
    arms: list[list["_Piece"]] = field(default_factory=list)
    body: list["_Piece"] = field(default_factory=list)
    # Whether a branch block can also be skipped altogether. An #if
    # without an #else has a path that writes nothing, and that path is
    # what catches a tag opened in a branch and closed after it.
    fallthrough: bool = False
    note: str = ""
    line: int = 0
    column: int = 0


def _text_piece(text: str) -> _Piece:
    return _Piece(_TEXT_PIECE, text=text)


def _trailing_eol(text: str) -> str:
    """The line ending a piece of text ends with, or nothing."""
    for ending in ("\r\n", "\n", "\r"):
        if text.endswith(ending):
            return ending
    return ""


def _drop_indent(out: list[_Piece]) -> None:
    """Removes what has been written since the start of the line.

    ct3 calls this handleWSBeforeDirective and truncates its pending
    text back to the last line break in it, looking at its own buffer
    and never at the source. One chunk and no further: where a
    directive has already eaten a line ending and left its indent
    pending, that indent survives the next drop.
    """
    while out:
        piece = out[-1]
        if piece.kind != _TEXT_PIECE:
            return
        if not piece.text:
            # ct3 never had a chunk here; commitStrConst drops an empty
            # one, so skipping it is not walking back a chunk.
            out.pop()
            continue
        match = lex.EOL.search(piece.text[::-1])
        if match is not None:
            out[-1] = _text_piece(piece.text[:len(piece.text)
                                             - match.start()])
            return
        out.pop()
        return


def _without_trailing_space(text: str, gobble_eol: bool) -> str:
    """Takes the whitespace up to the first line ending off some text.

    Only where nothing else stands there: ct3 tests ``restOfLine.strip()``
    before it consumes anything.
    """
    match = lex.EOL.search(text)
    head = text[:match.start()] if match else text
    if head.strip():
        return text
    if match is None:
        return ""
    return text[match.end():] if gobble_eol else text[match.start():]


# The comment forms ct3's addComment returns from without writing a
# chunk: a bar comment, the "name@" special variable and the docstring
# and header forms. Everything else reaches addMethComment, which
# commits the pending text and so keeps the indent in front of the
# directive alive.
_DOC_COMMENTS = ("doc:", "doc-method:", "doc-module:", "doc-class:",
                 "header:")


def _comment_commits(text: str) -> bool:
    """Whether ct3's addComment turns one ``##`` comment into a chunk."""
    from Cheetah.Parser import specialVarRE

    body = text[2:]
    body = body[:len(body) - len(_trailing_eol(body))]
    if not body.splitlines():
        return False
    if body.strip("#") == "" or specialVarRE.match(body):
        return False
    return not body.startswith(_DOC_COMMENTS)


def _tag_comment_commits(node: tree.Node) -> bool:
    """Whether a ``##`` on the tag's own line flushes the pending text."""
    return any(token.kind == lex.COMMENT and _comment_commits(token.text)
               for token in node.tokens)


def _definition_name(node: tree.Node) -> str:
    """The name a ``#def`` or ``#call`` block carries, for the note."""
    for token in node.tokens[1:]:
        if token.kind != lex.TEXT:
            continue
        for word in token.text.replace("(", " ").split():
            if word[0] in lex.IDENT_START:
                return word
        break
    return ""


def _split_branches(
        node: tree.Node) -> list[tuple[tree.Node, list[tree.Node]]]:
    """The block's children, cut at every branch directive."""
    found: list[tuple[tree.Node, list[tree.Node]]] = [(node, [])]
    for child in node.children:
        if child.kind == lex.DIRECTIVE and child.name in _BRANCH_NAMES:
            found.append((child, []))
            continue
        found[-1][1].append(child)
    return found


class _Emitter:
    """The bytes a template writes, with ct3's whitespace rules on them."""

    def __init__(self, source: str) -> None:
        self.source = source

    def clear(self, node: tree.Node) -> bool:
        """Whether only whitespace stands between the line start and here."""
        at = node.tokens[0].start
        starts = lex.line_starts(self.source)
        line, _ = lex.where(starts, at)
        begin = starts[line - 1]
        return begin == at or self.source[begin:at].isspace()

    def tag_line(self, node: tree.Node, out: list[_Piece]) -> str:
        """A directive decides about its own line. Returns what it kept.

        Two conditions, both ct3's. _eatRestOfDirectiveTag removes the
        whitespace in front of a directive only where the line was
        clear *and* the tag ran past the end of its own first line. A
        third sits on top and does not read like one: a ``##`` comment
        on the tag's line commits the pending text before the removal
        would happen, so the indent of ``  #if 1 ## note`` survives
        while the indent of ``  #if 1`` does not.
        """
        own = "".join(token.text for token in node.tokens)
        ending = _trailing_eol(own)
        past_its_line = bool(ending) or node.tokens[-1].end >= len(self.source)
        if self.clear(node):
            if past_its_line and not _tag_comment_commits(node):
                _drop_indent(out)
            return ""
        return ending

    def definition_line(self, node: tree.Node, out: list[_Piece]) -> str:
        """The line a ``#def`` or ``#block`` tag stands on.

        The colon short form stops the tag at the colon, so the tag
        itself never carries a line ending and a reader that asks it
        always hears no. ct3 asks after the body instead, and a
        ``#block`` is the one that keeps its indent, because the call it
        writes commits the pending text before the asking happens.
        """
        short = not any(child.kind == lex.DIRECTIVE and child.name == "end"
                        for child in node.children)
        if not short:
            return self.tag_line(node, out)
        if node.name == "block":
            return ""
        if self.clear(node):
            _drop_indent(out)
        return ""

    def line_comment(self, node: tree.Node, out: list[_Piece]) -> None:
        """``## to the end of the line``."""
        if self.clear(node):
            _drop_indent(out)
            return
        ending = _trailing_eol(node.text())
        if ending:
            out.append(_text_piece(ending))

    def block_comment(self, node: tree.Node, out: list[_Piece]) -> bool:
        """``#* over as many lines as it likes *#``.

        Returns whether the line ending after it goes as well. A comment
        that ends the template leaves everything alone, indent
        included; a one-line comment is already past ct3's
        endOfFirstLine the moment its line ending is taken, which is why
        the indent of ``  #* c *#`` goes when a line follows and stays
        when none does.
        """
        clear = self.clear(node)
        if not clear:
            return False
        rest = self.source[node.tokens[-1].end:]
        if not rest:
            return False
        match = lex.EOL.search(rest)
        head = rest[:match.start()] if match else rest
        at = node.tokens[-1].end
        if not head.strip():
            at += match.end() if match is not None else len(rest)
        first = lex.EOL.search(self.source, node.tokens[0].start)
        end_of_first_line = (first.start() if first is not None
                             else len(self.source))
        if at >= len(self.source) or at > end_of_first_line:
            _drop_indent(out)
        return clear

    # -- the walk ----------------------------------------------------

    def body_of(self, nodes: list[tree.Node],
                escaped: list[str] | None = None) -> list[_Piece]:
        """The pieces a run of sibling nodes writes.

        Args:
            escaped (list[str]|None): Where these nodes are the body of
                a block, the caller's place to receive the line ending
                the closing ``#end`` tag leaves behind. That ending is
                written after the block and not inside it, because ct3
                has closed the block before the text reaches the
                compiler.
        """
        out: list[_Piece] = []
        # Set by a block comment: whitespace up to the end of the line
        # is still to come off whatever follows, and the flag says
        # whether the line ending goes with it.
        pending: bool | None = None
        index = 0
        while index < len(nodes):
            node = nodes[index]
            index += 1
            if pending is not None:
                gobble = pending
                pending = None
                if node.kind == lex.TEXT:
                    text = _without_trailing_space(node.text(), gobble)
                    if text:
                        out.append(_text_piece(text))
                    continue
            if node.kind == lex.COMMENT:
                self.line_comment(node, out)
            elif node.kind == lex.BLOCK_COMMENT:
                pending = self.block_comment(node, out)
            elif node.kind == tree.BLOCK:
                self.block(node, out, escaped)
            elif node.kind == lex.DIRECTIVE:
                stopped, index = self.directive(node, nodes, index, out,
                                                escaped)
                if stopped:
                    return out
            elif node.kind == lex.PSP:
                out.append(self.psp(node))
            elif node.kind == lex.EOL_SLURP:
                # It writes nothing and has taken its own line ending
                # with it. What is left is the indent in front of it.
                if self.clear(node):
                    _drop_indent(out)
            elif node.kind == lex.PLACEHOLDER:
                out.append(_Piece(_VALUE_PIECE, token=node.tokens[0]))
            elif node.kind == lex.ESCAPE:
                # "\$" stands for a dollar, and what Cheetah writes is
                # the character behind the backslash, not both.
                out.append(_text_piece(node.text()[1:]))
            elif node.kind in (lex.TEXT, lex.RAW):
                out.append(_text_piece(node.text()))
        return out

    def directive(self, node: tree.Node, nodes: list[tree.Node], index: int,
                  out: list[_Piece],
                  escaped: list[str] | None) -> tuple[bool, int]:
        """One directive that opens no block. Says whether to stop."""
        name = node.name
        if name == "end":
            # The tag belongs to the block it closes. What is left is a
            # line ending that goes after that block, because ct3 has
            # written the dedent by the time the text arrives.
            ending = self.tag_line(node, out)
            if ending and escaped is not None:
                escaped.append(ending)
            elif ending:
                out.append(_text_piece(ending))
            return False, index
        if name == "slurp":
            # The line ending is already inside its own tokens, so it
            # never gets written. eatSlurp drops the indent wherever the
            # line was clear, and without the second condition the other
            # directives carry.
            if self.clear(node):
                _drop_indent(out)
            if node.tokens[-1].kind == lex.DIRECTIVE_END:
                index = self.swallow_line(nodes, index, out)
            return False, index
        if name in ("encoding", "unicode"):
            # eatEncoding calls neither handleWSBeforeDirective nor
            # _eatRestOfDirectiveTag, so it is the one directive that
            # leaves what stands in front of it on the line alone. The
            # #unicode line is cut out before anything parses.
            return False, index
        ending = self.tag_line(node, out)
        if name == "stop":
            # ct3 writes a return here and the rest of the template,
            # closing tags included, writes nothing. The ending stands
            # after that point, so nothing writes it either.
            return True, index
        if name == "echo":
            # After the line decision and before the ending it kept:
            # ct3 adds the chunk and only then commits what follows, so
            # "L#echo 1" puts the 1 in front of the line ending.
            for token in node.tokens[1:]:
                if token.kind == lex.PLACEHOLDER:
                    out.append(_Piece(_VALUE_PIECE, token=token))
        elif name == "include":
            out.append(_Piece(_INCLUDE_PIECE, line=node.line,
                              column=node.column))
        if ending:
            out.append(_text_piece(ending))
        return False, index

    def swallow_line(self, nodes: list[tree.Node], index: int,
                     out: list[_Piece]) -> int:
        """Drops what stands after a ``#slurp`` on its line, ending too.

        eatSlurp ends with readToEOL(gobble=True), which takes the rest
        of the line whatever stands on it. Where the tag stopped at a
        directive end token that rest is still in the stream:
        ``$job <!--#slurp#-->`` leaves the ``-->`` a sibling. A sibling
        that is not plain output is left alone rather than swallowed,
        because ct3 reads characters here and this scan has no way to
        put a parsed node back.
        """
        while index < len(nodes):
            node = nodes[index]
            if node.kind not in (lex.TEXT, lex.PLACEHOLDER, lex.ESCAPE):
                return index
            index += 1
            match = lex.EOL.search(node.text())
            if match is None:
                continue
            rest = node.text()[match.end():]
            if rest:
                out.append(_text_piece(rest))
            return index
        return index

    def psp(self, node: tree.Node) -> _Piece:
        """A PSP token, and whether it opens or closes a block.

        ct3's addPSP tests the stripped body: ``end`` closes, a trailing
        colon or dollar opens, anything else is a statement or a value.
        What such a block writes is Python this scan cannot read, so the
        placeholders inside one are refused rather than placed.
        """
        text = node.tokens[0].text
        body = text[2:-2].strip() if len(text) >= 4 else ""
        if body.lower() == "end":
            note = "close"
        elif body and body[-1] in ":$":
            note = "open"
        else:
            note = "plain"
        return _Piece(_PSP_PIECE, note=note, line=node.line,
                      column=node.column)

    def block(self, node: tree.Node, out: list[_Piece],
              escaped: list[str] | None) -> None:
        """One block directive and its body."""
        after: list[str] = []
        name = node.name
        if name in _ELSEWHERE_BLOCKS:
            self.definition_line(node, out)
            named = _definition_name(node)
            note = "in the body of #%s%s" % (name,
                                             " " + named if named else "")
            out.append(_Piece(_ELSEWHERE_PIECE,
                              body=self.body_of(node.children, after),
                              note=note, line=node.line, column=node.column))
        elif name in _LOOP_BLOCKS or name in _BRANCH_BLOCKS:
            leading = self.tag_line(node, out)
            split = _split_branches(node)
            arms = self.arms(split, leading, after)
            kind = _LOOP_PIECE if name in _LOOP_BLOCKS else _BRANCHES_PIECE
            fallthrough = not any(part.name == "else" for part, _ in split[1:])
            out.append(_Piece(kind, arms=arms, fallthrough=fallthrough,
                              note=name, line=node.line, column=node.column))
        else:
            # #block, #errorCatcher, #cache, #raw, #i18n and whatever
            # else opens a block: the body is written where it stands.
            leading = self.tag_line(node, out)
            pieces = self.body_of(node.children, after)
            if leading:
                pieces.insert(0, _text_piece(leading))
            out.extend(pieces)
        for text in after:
            out.append(_text_piece(text))

    def arms(self, split: list[tuple[tree.Node, list[tree.Node]]],
             leading: str, escaped: list[str]) -> list[list[_Piece]]:
        """Each arm's pieces, with the branch tags' own lines settled.

        A branch tag decides about its line like any other directive,
        and the two halves of that decision land in different arms: the
        indent in front of it was written by the arm above, the line
        ending behind it is the first thing the arm below writes.
        """
        built: list[list[_Piece]] = []
        carry = leading
        previous: list[_Piece] | None = None
        for directive, children in split:
            if previous is not None:
                carry = self.tag_line(directive, previous)
            pieces = self.body_of(children, escaped)
            if carry:
                pieces.insert(0, _text_piece(carry))
            built.append(pieces)
            previous = pieces
        return built


# -- The HTML state machine ------------------------------------------
#
# Character-driven and with no lookahead of its own, because a tag, a
# comment or a "</script>" can be split across a block boundary and
# then across two separate runs of text. Everything a multi-character
# token needs to remember lives in the state, so that a branch can be
# scanned from a snapshot and compared with another.

_DATA = "data"
_TAG_OPEN = "tag open"
_END_TAG_OPEN = "end tag open"
_DECL = "markup declaration"
_DECL_DASH = "markup declaration dash"
_BOGUS = "bogus comment"
_COMMENT = "comment"
_TAG_NAME = "tag name"
_BEFORE_ATTR = "before attribute name"
_ATTR_NAME = "attribute name"
_AFTER_ATTR = "after attribute name"
_BEFORE_VALUE = "before attribute value"
_VALUE_DQ = "double-quoted attribute value"
_VALUE_SQ = "single-quoted attribute value"
_VALUE_UQ = "unquoted attribute value"
_AFTER_VALUE = "after attribute value"
_SELF_CLOSING = "self-closing tag"
_RAWTEXT = "raw text"
_RAWTEXT_CLOSE = "raw text closing tag"

# States in which nothing but a tag has been opened, and the file must
# not end.
_OPEN_STATES = {
    _TAG_OPEN: "a tag", _END_TAG_OPEN: "an end tag", _DECL: "a declaration",
    _DECL_DASH: "a declaration", _BOGUS: "a comment",
    _COMMENT: "an HTML comment", _TAG_NAME: "a tag",
    _BEFORE_ATTR: "a tag", _ATTR_NAME: "a tag", _AFTER_ATTR: "a tag",
    _BEFORE_VALUE: "an attribute", _VALUE_DQ: "an attribute value",
    _VALUE_SQ: "an attribute value", _VALUE_UQ: "an attribute value",
    _AFTER_VALUE: "a tag", _SELF_CLOSING: "a tag",
    _RAWTEXT: "a raw text element", _RAWTEXT_CLOSE: "a raw text element",
}

_Snapshot = tuple[str, str, bool, str, bool, str, int, int, int]


class _Machine:
    """Where in the HTML the output has got to.

    Mutable and snapshotted rather than immutable and copied: a corpus
    template is a few hundred thousand characters and building one
    frozen state per character costs seconds per run.
    """

    __slots__ = ("where", "tag", "end_tag", "attr", "value_empty",
                 "raw_kind", "raw_match", "dashes", "psp")

    def __init__(self) -> None:
        self.where = _DATA
        self.tag = ""
        self.end_tag = False
        self.attr = ""
        self.value_empty = True
        self.raw_kind = ""
        # How much of "</script" has matched so far, which is the whole
        # of the raw text scan: the closing tag beats every JavaScript
        # state, verified against html.parser and lxml, so there is no
        # string or comment state to lose.
        self.raw_match = 0
        # Trailing dashes of a "-->" that was split across two pieces.
        self.dashes = 0
        # How deep in PSP blocks, whose body is Python this cannot read.
        self.psp = 0

    def snapshot(self) -> _Snapshot:
        return (self.where, self.tag, self.end_tag, self.attr,
                self.value_empty, self.raw_kind, self.raw_match,
                self.dashes, self.psp)

    def restore(self, state: _Snapshot) -> None:
        (self.where, self.tag, self.end_tag, self.attr, self.value_empty,
         self.raw_kind, self.raw_match, self.dashes, self.psp) = state

    # -- feeding -----------------------------------------------------

    def feed(self, text: str) -> None:
        """Runs the machine over a run of literal output."""
        index = 0
        length = len(text)
        while index < length:
            where = self.where
            if where == _DATA:
                found = text.find("<", index)
                if found < 0:
                    return
                index = found + 1
                self.where = _TAG_OPEN
            elif where in (_VALUE_DQ, _VALUE_SQ):
                quote = '"' if where == _VALUE_DQ else "'"
                found = text.find(quote, index)
                if found < 0:
                    self.value_empty = False
                    return
                if found > index:
                    self.value_empty = False
                index = found + 1
                self.where = _AFTER_VALUE
            elif where == _COMMENT:
                index = self.comment(text, index)
            elif where == _BOGUS:
                found = text.find(">", index)
                if found < 0:
                    return
                index = found + 1
                self.where = _DATA
            elif where == _RAWTEXT:
                index = self.rawtext(text, index)
            else:
                index = self.step(text, index)

    def comment(self, text: str, index: int) -> int:
        """Reads on to the ``-->`` that closes an HTML comment."""
        carried = self.dashes
        if carried:
            # The closer straddles the boundary between two pieces. The
            # dashes are already behind us, so only the rest of the
            # match is consumed here.
            prefix = "-" * carried + text[index:index + 2]
            at = prefix.find("-->")
            self.dashes = 0
            if at >= 0:
                self.where = _DATA
                return index + at + 3 - carried
        found = text.find("-->", index)
        if found >= 0:
            self.where = _DATA
            return found + 3
        tail = 0
        while tail < 2 and tail < len(text) - index \
                and text[len(text) - 1 - tail] == "-":
            tail += 1
        self.dashes = tail
        return len(text)

    def rawtext(self, text: str, index: int) -> int:
        """Reads on to the ``</script`` that ends a raw text element.

        The closing tag beats every JavaScript state, so nothing else is
        tracked in here. Missing that keeps one ``//`` in a URL inside
        the element to the end of the file, which is what happened in
        cobbler's xcp_answerfile.xml.template.
        """
        closer = "</" + self.raw_kind
        length = len(text)
        while index < length:
            char = text[index]
            index += 1
            matched = self.raw_match
            if matched:
                if char.lower() == closer[matched]:
                    matched += 1
                    if matched == len(closer):
                        self.raw_match = 0
                        self.where = _RAWTEXT_CLOSE
                        return index
                    self.raw_match = matched
                    continue
                self.raw_match = 1 if char == "<" else 0
                continue
            if char == "<":
                self.raw_match = 1
        return index

    def step(self, text: str, index: int) -> int:
        """One character, for the states that need to see each one."""
        char = text[index]
        where = self.where
        if where == _RAWTEXT_CLOSE:
            # Only a delimiter really ends the element: "</scriptfoo" is
            # raw text like anything else.
            if char in SPACE or char in "/>":
                self.where = _TAG_NAME
                self.end_tag = True
                self.tag = self.raw_kind
                self.raw_kind = ""
                return index
            self.where = _RAWTEXT
            self.raw_match = 1 if char == "<" else 0
            return index + 1
        index += 1
        if where == _TAG_OPEN:
            if char == "!":
                self.where = _DECL
            elif char == "/":
                self.where = _END_TAG_OPEN
            elif char.isascii() and char.isalpha():
                self.where = _TAG_NAME
                self.end_tag = False
                self.tag = char.lower()
                self.attr = ""
            elif char != "<":
                self.where = _DATA
        elif where == _END_TAG_OPEN:
            if char.isascii() and char.isalpha():
                self.where = _TAG_NAME
                self.end_tag = True
                self.tag = char.lower()
                self.attr = ""
            elif char == ">":
                self.where = _DATA
            else:
                self.where = _BOGUS
        elif where == _DECL:
            if char == "-":
                self.where = _DECL_DASH
            elif char == ">":
                self.where = _DATA
            else:
                self.where = _BOGUS
        elif where == _DECL_DASH:
            if char == "-":
                self.where = _COMMENT
                self.dashes = 0
            elif char == ">":
                self.where = _DATA
            else:
                self.where = _BOGUS
        elif where == _TAG_NAME:
            if char in SPACE:
                self.where = _BEFORE_ATTR
            elif char == "/":
                self.where = _SELF_CLOSING
            elif char == ">":
                self.close_tag(False)
            else:
                self.tag += char.lower()
        elif where == _BEFORE_ATTR:
            if char in SPACE:
                pass
            elif char == "/":
                self.where = _SELF_CLOSING
            elif char == ">":
                self.close_tag(False)
            else:
                self.where = _ATTR_NAME
                self.attr = char.lower()
        elif where == _ATTR_NAME:
            if char in SPACE:
                self.where = _AFTER_ATTR
            elif char == "=":
                self.where = _BEFORE_VALUE
            elif char == "/":
                self.where = _SELF_CLOSING
            elif char == ">":
                self.close_tag(False)
            else:
                self.attr += char.lower()
        elif where == _AFTER_ATTR:
            if char in SPACE:
                pass
            elif char == "=":
                self.where = _BEFORE_VALUE
            elif char == "/":
                self.where = _SELF_CLOSING
            elif char == ">":
                self.close_tag(False)
            else:
                self.where = _ATTR_NAME
                self.attr = char.lower()
        elif where == _BEFORE_VALUE:
            if char in SPACE:
                pass
            elif char == '"':
                self.where = _VALUE_DQ
                self.value_empty = True
            elif char == "'":
                self.where = _VALUE_SQ
                self.value_empty = True
            elif char == ">":
                self.close_tag(False)
            else:
                self.where = _VALUE_UQ
                self.value_empty = False
        elif where == _VALUE_UQ:
            if char in SPACE:
                self.where = _BEFORE_ATTR
            elif char == ">":
                self.close_tag(False)
        elif where == _AFTER_VALUE:
            if char in SPACE:
                self.where = _BEFORE_ATTR
            elif char == "/":
                self.where = _SELF_CLOSING
            elif char == ">":
                self.close_tag(False)
            else:
                self.where = _ATTR_NAME
                self.attr = char.lower()
        elif where == _SELF_CLOSING:
            if char == ">":
                self.close_tag(True)
            else:
                self.where = _BEFORE_ATTR
                index -= 1
        return index

    def close_tag(self, self_closing: bool) -> None:
        """The ``>`` that ends a tag, and what the element opens.

        A start tag for ``<script>`` or ``<style>`` switches the content
        to raw text. Not a self-closing one: html.parser leaves
        ``<script/>`` in ordinary content and the oracle that holds this
        scan to account is html.parser.
        """
        name = self.tag
        self.tag = ""
        self.attr = ""
        # Reset so that two paths through a branch that differ only in
        # what the last attribute value held still compare equal: this
        # flag says something about a value that has been written out.
        self.value_empty = True
        was_end = self.end_tag
        self.end_tag = False
        if not was_end and not self_closing and name in RAW_TEXT:
            self.where = _RAWTEXT
            self.raw_kind = name
            self.raw_match = 0
        else:
            self.where = _DATA

    # -- reading it off ----------------------------------------------

    def context(self) -> tuple[str, str]:
        """What a placeholder standing here is, and where it stands."""
        if self.psp:
            return VERBATIM, "inside a PSP block"
        where = self.where
        if where == _DATA:
            return TEXT, "in element text"
        if where in (_RAWTEXT, _RAWTEXT_CLOSE):
            return VERBATIM, "inside <%s>" % self.raw_kind
        if where in (_COMMENT, _BOGUS, _DECL, _DECL_DASH):
            return VERBATIM, "inside an HTML comment"
        if self.tag in RAW_TEXT and not self.end_tag:
            return VERBATIM, "inside the <%s> tag" % self.tag
        if where in (_VALUE_DQ, _VALUE_SQ):
            name = self.attr
            if name.startswith("on"):
                return VERBATIM, "in an %s= attribute" % name
            if name == "style":
                return VERBATIM, "in a style= attribute"
            if self.value_empty and name in URL_ATTRIBUTES:
                return URL_HEAD, "at the head of the %s= attribute" % name
            return ATTRIBUTE, "in the %s= attribute" % name
        if where == _VALUE_UQ:
            return VERBATIM, "in an unquoted %s= attribute value" % self.attr
        if where == _BEFORE_VALUE:
            return (VERBATIM,
                    "after %s= with no quote, so the quoting would have to "
                    "come out of the value" % self.attr)
        if where in (_BEFORE_ATTR, _ATTR_NAME, _AFTER_ATTR, _AFTER_VALUE,
                     _SELF_CLOSING):
            return VERBATIM, "in attribute-name position in <%s>" % self.tag
        if where in (_TAG_NAME, _TAG_OPEN, _END_TAG_OPEN):
            return VERBATIM, "in the name of a tag"
        return VERBATIM, "in a position the scan could not place"

    def wrote_value(self) -> None:
        """Records that a placeholder has written into this position."""
        if self.where in (_VALUE_DQ, _VALUE_SQ):
            self.value_empty = False
        elif self.where == _BEFORE_VALUE:
            # The value came out of the placeholder, so what follows it
            # stands in an unquoted value.
            self.where = _VALUE_UQ
            self.value_empty = False

    def at_rest(self) -> bool:
        return self.where == _DATA and not self.psp


# -- The scan --------------------------------------------------------


def scan(root: tree.Node) -> dict[int, Site]:
    """Where every placeholder that writes stands, keyed by Token.start.

    Args:
        root (tree.Node): What ``ct4.lang.tree.parse`` built for the
            template. The scan starts from a fresh data state; see the
            module docstring for why a fragment's real opening context
            cannot be read out of the fragment.

    Returns:
        dict[int, Site]: One Site per placeholder in output position.
            A placeholder that is not in the map does not write, or
            could not be placed, and the caller treats it as VERBATIM.

    Raises:
        ScanRefused: where the scan itself cannot be trusted over this
            file. Nothing is escaped then, and the compile fails with
            the reason rather than falling back to text mode.
    """
    source = root.text()
    _refuse_cdata(source)
    _refuse_swapped_filters(root)
    _refuse_what_the_scan_cannot_model(root)
    pieces = _Emitter(source).body_of(root.children)
    machine = _Machine()
    sites: dict[int, Site] = {}
    _run(pieces, machine, sites)
    if not machine.at_rest():
        starts = lex.line_starts(source)
        line, column = lex.where(starts, max(len(source) - 1, 0))
        still = ("a PSP block" if machine.psp
                 else _OPEN_STATES.get(machine.where, "something"))
        raise ScanRefused(
            line, column,
            "the file does not end in element text: %s is still open"
            % still)
    return sites


def _refuse_cdata(source: str) -> None:
    """Markup mode is HTML only, and this is where it says so.

    ``<![CDATA[`` is ordinary content in XML and a bogus comment in
    HTML, so a coarse scanner reads what stands in it as element text
    with full confidence: 404 corpus placeholders in six RSS feeds, and
    only html.parser disagreed. An HTML escape corrupts a feed, so the
    file is refused rather than half-served.
    """
    at = source.find("<![CDATA[")
    if at < 0:
        return
    line, column = lex.where(lex.line_starts(source), at)
    raise ScanRefused(
        line, column,
        "<![CDATA[ is XML and markup mode escapes HTML; keep this "
        "template in text mode")


# Constructs whose output the scan does not model, so that the state
# machine walks past them believing it is somewhere it is not. Each one
# was a live hole before it was listed here, found by attacking the
# finished mode rather than by reading it:
#
#   raw       its body is written out untouched and the scan never sees
#             it, so "#raw <a href=#end raw" leaves the machine in
#             element text while the browser is inside an unquoted
#             attribute. The next value is escaped for text and lands
#             live.
#   psp       "<%= x %>" is a value write that does not go through the
#             placeholder path at all, so it was neither escaped nor
#             refused.
#   include   an included file can open a tag, and the includer cannot
#             know: 349 of the corpus's 399 includes stand in element
#             text, and not one of them is checked by whoever includes
#             it.
#   extends,  a child that overrides a #block is scanned from a fresh
#   implements  data state, so a tag the base leaves open puts every
#             placeholder of the override into an unquoted attribute.
#
# All four are refusals and not omissions. A markup template may not
# hold them at all, which is a smaller feature than the plan asked for
# and the only version of it that is true.
UNMODELLED = ("raw", "include", "extends", "implements")


def _refuse_what_the_scan_cannot_model(root: tree.Node) -> None:
    """Turns away a file holding output the state machine walks past.

    Raises:
        ScanRefused: on the first one found, naming it and where.
    """
    for node in root.walk():
        if node.kind == lex.PSP:
            raise ScanRefused(
                node.line, node.column,
                "a PSP block writes without going through the escape; "
                "keep this template in text mode")
        if node.kind in (tree.BLOCK, lex.DIRECTIVE) \
                and node.name in UNMODELLED:
            raise ScanRefused(
                node.line, node.column,
                "#%s writes output this scan cannot follow, so what "
                "comes after it would be escaped for the wrong place"
                % node.name)


def _refuse_swapped_filters(root: tree.Node) -> None:
    """``#filter`` and ``#transform`` break what the escape composes with.

    Markup mode wraps the result of exactly one filter. A template that
    swaps the filter under it, or replaces the whole response, has
    changed the composition the escape was built on.
    """
    for node in root.walk():
        if node.kind in (tree.BLOCK, lex.DIRECTIVE) \
                and node.name in ("filter", "transform"):
            raise ScanRefused(
                node.line, node.column,
                "#%s changes the filter markup mode composes with"
                % node.name)


def _run(pieces: list[_Piece], machine: _Machine,
         sites: dict[int, Site]) -> None:
    """Walks the pieces once, forking at every block that branches."""
    for piece in pieces:
        kind = piece.kind
        if kind == _TEXT_PIECE:
            machine.feed(piece.text)
        elif kind == _VALUE_PIECE:
            token = piece.token
            assert token is not None
            context, note = machine.context()
            machine.wrote_value()
            sites[token.start] = Site(token.start, token.line, token.column,
                                      context, note)
        elif kind == _ELSEWHERE_PIECE:
            _elsewhere(piece.body, sites, piece.note)
        elif kind == _INCLUDE_PIECE:
            _check_include(piece, machine)
        elif kind == _PSP_PIECE:
            if piece.note == "open":
                machine.psp += 1
            elif piece.note == "close" and machine.psp:
                machine.psp -= 1
        elif kind == _BRANCHES_PIECE:
            _branches(piece, machine, sites)
        elif kind == _LOOP_PIECE:
            _loop(piece, machine, sites)


def _check_include(piece: _Piece, machine: _Machine) -> None:
    """An ``#include`` writes content this scan cannot see.

    Where it stands in element text, treating it as writing nothing is
    what the corpus supports: 349 of 399 includes stand there and no
    included file continues a tag, an attribute or a script into the
    file that includes it. Where it stands anywhere else that
    assumption is unchecked, so the file is refused.
    """
    if machine.at_rest():
        return
    _, note = machine.context()
    raise ScanRefused(
        piece.line, piece.column,
        "an #include %s: what it writes cannot be seen from here" % note)


def _branches(piece: _Piece, machine: _Machine,
              sites: dict[int, Site]) -> None:
    """Every arm from the same state, and they have to agree at the end.

    The path that writes nothing counts as an arm too, where there is
    no ``#else``: that is the one that catches a tag opened in a branch
    and closed after the block. Measured over the corpus, 1867
    conditional blocks and not one disagreement, so the refusal is
    cheap.
    """
    entry = machine.snapshot()
    ends: list[_Snapshot] = []
    for arm in piece.arms:
        machine.restore(entry)
        _run(arm, machine, sites)
        ends.append(machine.snapshot())
    if piece.fallthrough:
        ends.append(entry)
    if len(set(ends)) > 1:
        raise ScanRefused(
            piece.line, piece.column,
            "the arms of this #%s end in different markup states, so what "
            "follows it cannot be placed" % piece.note)
    machine.restore(ends[0] if ends else entry)


def _loop(piece: _Piece, machine: _Machine, sites: dict[int, Site]) -> None:
    """A loop body has to end in the state it began in.

    It may run any number of times, the empty one included, so nothing
    it leaves behind can be relied on afterwards.
    """
    entry = machine.snapshot()
    for arm in piece.arms:
        machine.restore(entry)
        _run(arm, machine, sites)
        if machine.snapshot() != entry:
            raise ScanRefused(
                piece.line, piece.column,
                "this #%s does not end in the markup state it began in, so "
                "the number of turns would decide where a placeholder stands"
                % piece.note)
    machine.restore(entry)


def _elsewhere(pieces: list[_Piece], sites: dict[int, Site],
               note: str) -> None:
    """Every placeholder of a body that is written somewhere else.

    A ``#def`` body stands in one place and is written in another, and
    the position that decides the escaping is the call site. Nothing
    here can be placed, so all of it is VERBATIM and none of it touches
    the state of the file it stands in.
    """
    for piece in pieces:
        if piece.kind == _VALUE_PIECE:
            token = piece.token
            assert token is not None
            sites[token.start] = Site(token.start, token.line, token.column,
                                      VERBATIM, note)
        elif piece.kind == _ELSEWHERE_PIECE:
            _elsewhere(piece.body, sites, piece.note)
        else:
            for arm in piece.arms:
                _elsewhere(arm, sites, note)


# -- What the template writes, as bytes ------------------------------


class _Writer:
    """Linearises the pieces into the text a render would produce."""

    def __init__(self, values: dict[int, str], branch: int) -> None:
        self.parts: list[str] = []
        self.offsets: dict[int, int] = {}
        self.at = 0
        self.values = values
        self.branch = branch

    def add(self, text: str) -> None:
        if text:
            self.parts.append(text)
            self.at += len(text)

    def run(self, pieces: list[_Piece]) -> None:
        for piece in pieces:
            if piece.kind == _TEXT_PIECE:
                self.add(piece.text)
            elif piece.kind == _VALUE_PIECE:
                token = piece.token
                assert token is not None
                self.offsets[token.start] = self.at
                self.add(self.values.get(token.start, ""))
            elif piece.arms:
                index = min(self.branch, len(piece.arms) - 1)
                self.run(piece.arms[index])


def emitted(root: tree.Node, values: dict[int, str] | None = None,
            branch: int = 0) -> tuple[str, dict[int, int]]:
    """The bytes the template writes, and where each placeholder lands.

    The reconstruction the whole scan rests on, exposed because it is
    the only thing that can be held against ct3 directly: render the
    same template with ct3 and the two strings have to match byte for
    byte.

    Args:
        root (tree.Node): What ``ct4.lang.tree.parse`` built.
        values (dict[int, str]|None): What to write for a placeholder,
            by ``Token.start``. Anything not named writes nothing,
            which is the assumption the scan itself makes about a
            placeholder's value.
        branch (int): Which arm of every conditional to take, clamped to
            the arms there are, and how often a loop body runs is once.
            A file whose arms disagree is refused by :func:`scan`, so
            which arm this takes changes the offsets and not the
            markup states around them.

    Returns:
        tuple[str, dict[int, int]]: The text, and the offset in it of
            every placeholder that was written.
    """
    writer = _Writer(values or {}, branch)
    writer.run(_Emitter(root.text()).body_of(root.children))
    return "".join(writer.parts), writer.offsets
