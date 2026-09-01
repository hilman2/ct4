"""Cheetah source as a tree of blocks, built on the token stream.

The layer below hands up a flat run of tokens that joins back to the
source. This one gives it shape: ``#for`` opens, ``#end for`` closes,
and what stood between them is inside. Its arguments are gathered, and
so is the colon short form, where the body is the rest of the line.

Two things it is measured on, both over the whole corpus:

Writing the tree back gives the source, byte for byte. Same assertion
as the layer below and for the same reason, because a formatter and a
structural edit are worth nothing without it.

Whether a template has a well-formed structure has to be decided the
same way ct3 decides it. Not by my reading of the rules: ct3 refuses a
template with an unbalanced block, and this has to refuse exactly
those and no others. Disagreement in either direction is a finding.

What it does not do is understand expressions. The argument of a
``#for`` stays a run of tokens. Turning that into Python is the layer
above, and it can lean on Python's own parser once it gets there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence

from ct4.lang import lex
from ct4.lang.lex import Token

# Directives closed by "#end <name>" that do not stand in ct3's table
# of them. Each has an eater of its own that calls
# _eatToThisEndDirective: raw, compiler-settings and defmacro do it by
# name, and every macro directive does it under whatever name it was
# registered as, i18n being the one ct3 ships.
#
# The set lives in lex, because how far their "#end" tag reaches is a
# question about where the tokens are and the lexer has to answer it
# before this layer sees anything.
SELF_CLOSING = lex.SELF_CLOSING


_required: frozenset[str] | None = None


def must_close() -> frozenset[str]:
    """Directives ct3 insists on seeing closed.

    Read off a real parser rather than copied, because the list lives
    on the instance and a copy would drift. Everything else that has an
    "#end" may be closed and need not be: "#errorCatcher Echo" on a
    line of its own is how every weewx skin starts, and "#raw" without
    an end runs to the end of the file.

    Cached, because building a parser to read one list is not
    something to do per template.
    """
    global _required

    if _required is None:
        from Cheetah.Compiler import ModuleCompiler

        # Not the empty string: ct3 warns about that, and a warning per
        # parsed template is its own kind of wrong.
        compiler = ModuleCompiler("x", moduleName="t", mainClassName="t")
        compiler.compile()
        _required = frozenset(compiler._parser._closeableDirectives)
    return _required


def closing_names() -> frozenset[str]:
    """Directives that can open a block at all."""
    from Cheetah.Parser import endDirectiveNamesAndHandlers

    return frozenset(endDirectiveNamesAndHandlers) | SELF_CLOSING


# Directives that continue a block someone else opened. They neither
# open nor close: an #else belongs to the #if above it.
CONTINUATIONS = frozenset({"else", "elif", "except", "finally"})


class StructureError(Exception):
    """A block that is not closed, or closed by the wrong end.

    Carries the position so the message can point at the template
    rather than at a token index.
    """

    def __init__(self, message: str, line: int, column: int):
        super().__init__("%s (line %d, column %d)" % (message, line, column))
        self.line = line
        self.column = column


@dataclass
class Node:
    """One piece of the tree.

    ``kind`` is the token kind for a leaf, or "block" for a directive
    that opens one, or "template" for the root. ``tokens`` are the
    tokens this node owns directly: for a block that is the opening
    directive and its arguments, not its body.
    """

    kind: str
    tokens: list[Token] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    name: str = ""

    @property
    def line(self) -> int:
        return self.tokens[0].line if self.tokens else 0

    @property
    def column(self) -> int:
        return self.tokens[0].column if self.tokens else 0

    def text(self) -> str:
        """The source this node covers, its children included."""
        return "".join(part.text for part in self.leaves())

    def leaves(self) -> Iterator[Token]:
        for token in self.tokens:
            yield from token.leaves()
        for child in self.children:
            yield from child.leaves()

    def walk(self) -> Iterator["Node"]:
        yield self
        for child in self.children:
            yield from child.walk()


TEMPLATE = "template"
BLOCK = "block"
# How a block directive ends. "block" is the ordinary form closed by an
# #end, "short" is the colon form closed by its line ending.
OPEN_BLOCK = "block"
OPEN_SHORT = "short"


def parse(source: str) -> Node:
    """The tree of a template.

    Raises:
        StructureError: where a block is left open or closed wrongly.
    """
    return _Builder(source).run()


def unparse(node: Node) -> str:
    """The source the tree came from."""
    return node.text()


class _Builder:
    """Walks the tokens once and stacks up the open blocks."""

    def __init__(self, source: str):
        self.source = source
        self.tokens = lex.tokens(source)
        self.starts = lex.line_starts(source)
        self.closing = closing_names()
        self.required = must_close()
        # Short forms waiting for their line to end, innermost last,
        # each with the offset it closes at.
        self.short: list[tuple[Node, int]] = []
        # Where every directive end token stands. Collected once from
        # the stream, because the question "where do this directive's
        # arguments stop" is asked before its tokens have been taken.
        self.ends = sorted(token.start for token in self.tokens
                           if token.kind == lex.DIRECTIVE_END)

    def run(self) -> Node:
        root = Node(TEMPLATE)
        stack = [root]
        index = 0
        while index < len(self.tokens):
            # Any short form whose line has ended closes before the
            # token that ended it is looked at.
            index = self._close_short(stack, index)
            if index >= len(self.tokens):
                break
            token = self.tokens[index]
            if token.kind != lex.DIRECTIVE:
                stack[-1].children.append(
                    Node(token.kind, [token]))
                index += 1
                continue

            name = token.text[1:]
            if name == "end":
                index = self._close(stack, index)
                continue

            index += 1
            node = Node(BLOCK if name in self.closing else lex.DIRECTIVE,
                        [token], name=name)
            mode = self._open_mode(node) if node.kind == BLOCK else None
            stop = None
            if mode == OPEN_SHORT:
                # The arguments end at the colon; the rest of the line
                # is the body and becomes children.
                stop = self._colon_end(node)
            elif name == "errorCatcher":
                # It takes one identifier and no more; see
                # lex.identifier_end.
                stop = lex.identifier_end(self.source, token.end)
            # Otherwise everything up to the end of the line belongs to
            # the directive: its arguments.
            index = self._take_arguments(node, index, stop)
            stack[-1].children.append(node)
            if mode == OPEN_BLOCK and self._is_closed(node, index):
                stack.append(node)
            elif mode == OPEN_SHORT:
                stack.append(node)
                self.short.append((node, self._line_end(node)))
        while self.short:
            # A short form at the end of the source has no token after
            # it to close against.
            self.short.pop()
            stack.pop()
        if len(stack) > 1:
            open_block = stack[-1]
            raise StructureError(
                "#%s was never closed" % open_block.name,
                open_block.line, open_block.column)
        return root

    def _is_closed(self, node: Node, index: int) -> bool:
        """Whether this directive is one that has to be closed here.

        The ones ct3 insists on always are. The rest may be closed and
        need not be, so they open a block only where an "#end" for them
        really follows: "#errorCatcher Echo" on a line of its own is how
        every weewx skin starts, and "#raw" without an end runs to the
        end of the file.
        """
        if node.name in self.required:
            return True
        for later in range(index, len(self.tokens)):
            token = self.tokens[later]
            if token.kind == lex.DIRECTIVE and token.text == "#end":
                following = self.tokens[later + 1:later + 2]
                if following and following[0].text.split()[:1] == [node.name]:
                    return True
        return False

    def _close_short(self, stack: list[Node], index: int) -> int:
        """Closes every short form whose line has ended here.

        The line ending goes into the block as a child of its own, so
        that the tree still writes back to the source and the layer
        above knows the block ate it.
        """
        while self.short:
            node, at = self.short[-1]
            if index >= len(self.tokens):
                return index
            token = self.tokens[index]
            if token.end <= at:
                return index
            if token.start < at:
                # The body and what follows arrive as one run of text:
                # the lexer has no reason to cut there. Everything up
                # to the offset is body and goes into the block, the
                # line ending included.
                head, rest = lex.split(token, at, self.starts)
                stack[-1].children.append(Node(head.kind, [head]))
                self.tokens[index] = rest
                continue
            self.short.pop()
            stack.pop()
        return index

    def _colon_end(self, node: Node) -> int:
        """Just past the colon that opens a short form's body.

        And past one space after it, because ct3 does
        getWhiteSpace(max=1) there. Exactly one: the second belongs to
        the body and gets written.
        """
        start = node.tokens[0].end
        line = self._rest_of_line(node)
        at = start + _top_level_colon(line) + 1
        if self.source[at:at + 1] in (" ", "\t"):
            at += 1
        return at

    def _line_end(self, node: Node) -> int:
        """Just past the end of the line the directive stands on.

        Past it, not at it: ct3 parses a short form's body with
        breakPoint=findEOL(gobble=True), so the line ending is inside
        the body and gets written from there.
        """
        match = lex.EOL.search(self.source, node.tokens[0].end)
        return match.end() if match else len(self.source)

    def _take_arguments(self, node: Node, index: int,
                        stop: int | None = None) -> int:
        """Takes the tokens up to the end of the directive's line.

        A directive ends at its line ending or at the directive end
        token, a bare hash. The line ending belongs to the directive,
        which is why a template full of directives leaves no blank
        lines behind.

        Which line ending that is, is line_that_closes' business: one
        inside an open bracket does not end anything. The caller passes
        a stop of its own only for the colon short form, where the
        arguments end at the colon and the rest of the line is body.
        """
        if stop is None:
            stop = lex.line_that_closes(self.source, node.tokens[0].end)
        while index < len(self.tokens):
            token = self.tokens[index]
            if token.kind == lex.RAW:
                # Whole or not at all. A raw body is source the lexer
                # has already measured from end to end, and a line
                # ending inside it ends nothing: "#raw" and its body
                # are one thing.
                node.tokens.append(token)
                index += 1
                continue
            if token.start >= stop:
                return index
            if token.end > stop:
                # The lexer hands up "\nbody\n" as one run of text: it
                # has no reason to care where lines end. Here it does,
                # because everything after the ending is the body.
                head, rest = lex.split(token, stop, self.starts)
                node.tokens.append(head)
                self.tokens[index] = rest
                return index
            if token.kind == lex.DIRECTIVE_END:
                node.tokens.append(token)
                return index + 1
            if token.kind == lex.DIRECTIVE:
                # A directive on the same line starts something of its
                # own. Its arguments are not ours.
                return index
            node.tokens.append(token)
            index += 1
        return index

    def _open_mode(self, node: Node) -> str | None:
        """How this block directive ends, or None where it opens nothing.

        Three ways it does not.

        The colon short form puts the body on the directive's own line.

        ``#if a then b else c`` is a ternary expression and has no
        body at all. ct3 decides that on the Python tokens of the
        expression holding both words; here they are looked for outside
        brackets and strings, which is the same thing for this purpose.
        A corpus case turns on it: the else inside ``''' else '''`` must
        not count.

        ``#compiler-settings reset`` restores the defaults and closes
        nothing; ct3's eater looks for that word before it starts
        reading to an end directive.

        And ``#raw`` wants its colon straight away. Every other eater
        reads an expression first and looks for the colon behind it, so
        a colon anywhere on the line is the short form. eatRaw reads no
        expression: it takes the blanks and then asks. A line from
        fprime's code generator turns on it,

            #raw    <%=#end raw render "x", locals: {id: $id} %>

        where the colon after "locals" is inside the raw body. Read as
        a short form the block was opened twice over and closed once,
        and the tree builder walked off the end of its own stack.
        """
        line = self._rest_of_line(node)
        if node.name == "raw":
            if not line.lstrip(" \t\f").startswith(":"):
                return OPEN_BLOCK
        if node.name == "compiler-settings":
            if line.split()[:1] == ["reset"]:
                return None
        if node.name == "if":
            words = set(_bare_words(line))
            if "then" in words and "else" in words:
                return None
        if self._is_short_form(line):
            return OPEN_SHORT
        return OPEN_BLOCK

    def _rest_of_line(self, node: Node) -> str:
        """The source from just after the directive name to the line end.

        Read from the source and not from the tokens the directive
        collected, because its body can begin on the same line and with
        a directive of its own: ``#if 1: #for i in x#$i#end for``. The
        arguments stop at that #for, and a check that only looked at
        them would find a colon with nothing behind it.
        """
        start = node.tokens[0].end
        # To the ending that closes the directive, not the first one:
        # "#def f($a,\n$b): body" is a short form and its colon stands
        # on the second line.
        end = lex.line_that_closes(self.source, start)
        match = lex.EOL.search(self.source, max(end - 2, start))
        if match is not None and match.end() >= end:
            end = match.start()
        # And no further than the directive end token, where there is
        # one on this line. ct3 parses the expression and stops there,
        # so what comes after is output and not argument. Without this
        # a colon in an HTML attribute after "<!--#if $cur != 0#-->"
        # reads as the short form, and a corpus template does exactly
        # that.
        import bisect

        at = bisect.bisect_left(self.ends, start)
        if at < len(self.ends) and self.ends[at] < end:
            end = self.ends[at]
        return self.source[start:end]

    def _is_short_form(self, line: str) -> bool:
        """Whether the body stood on the directive's own line.

        ``#block name: text`` is closed by its line ending, not by an
        ``#end``. ct3 decides this by looking for a colon right after
        the directive's expression, and then requiring the rest of the
        line to hold something that is neither whitespace nor a
        comment.

        The expression is not parsed here, so the colon is found by
        scanning instead: the first one outside brackets and strings.
        Which is the same colon, because the expression is what stands
        before it.
        """
        at = _top_level_colon(line)
        if at < 0:
            return False
        rest = line[at + 1:].strip()
        return bool(rest) and not rest.startswith("##")

    def _close(self, stack: list[Node], index: int) -> int:
        """Handles an ``#end`` and pops the block it closes."""
        token = self.tokens[index]
        node = Node(lex.DIRECTIVE, [token], name="end")
        index = self._take_arguments(
            node, index + 1, lex.self_closing_end(self.source, token.end))
        closes = _end_target(node)
        if len(stack) == 1:
            raise StructureError(
                "#end %s closes nothing" % closes, token.line, token.column)
        open_block = stack[-1]
        if closes and not closes.startswith(open_block.name):
            raise StructureError(
                "#end %s closes #%s" % (closes, open_block.name),
                token.line, token.column)
        open_block.children.append(node)
        stack.pop()
        return index


def _bare_words(text: str) -> Iterator[str]:
    """Identifiers standing outside brackets and outside strings.

    Enough to tell ``then`` and ``else`` in a ternary from the ones
    inside ``''' else '''`` or inside a call.
    """
    index = 0
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
            start = index
            while index < len(text) and text[index] in lex.IDENT:
                index += 1
            if depth == 0:
                yield text[start:index]
            continue
        index += 1


def _top_level_colon(text: str) -> int:
    """Offset of the first colon outside brackets and strings.

    Returns -1 where there is none before the line ends. A colon in
    ``{'x': 1}`` or in ``$d['a:b']`` closes nothing and must not count.
    """
    depth = 0
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
        elif char == ":" and depth == 0:
            return index
        elif char in "\r\n":
            return -1
        index += 1
    return -1


def _end_target(node: Node) -> str:
    """Which block an ``#end`` names, as a bare word.

    Compared against the open block by prefix, not by equality, and
    that is ct3 rather than sloppiness. eatEndDirective walks the keys
    of its end-directive table and takes the first one the text starts
    with, so "#end forecast" finds the key "for" and closes a #for. A
    corpus template does exactly that.
    """
    for token in node.tokens[1:]:
        if token.kind != lex.TEXT:
            continue
        word = token.text.strip().split()
        if word:
            return word[0]
    return ""


def blocks(node: Node) -> Iterator[Node]:
    """Every block in the tree, outermost first."""
    for found in node.walk():
        if found.kind == BLOCK:
            yield found


def depth_of(root: Node) -> dict[tuple[int, int], int]:
    """How deeply nested each block sits, by line and column.

    Keyed by position so it can be held against what another
    implementation says about the same template.
    """
    found: dict[tuple[int, int], int] = {}

    def walk(node: Node, depth: int) -> None:
        for child in node.children:
            if child.kind == BLOCK:
                found[(child.line, child.column)] = depth
                walk(child, depth + 1)
            else:
                walk(child, depth)

    walk(root, 0)
    return found


def summary(root: Node) -> Sequence[str]:
    """A readable outline, for looking at a tree by hand."""
    lines = []

    def walk(node: Node, depth: int) -> None:
        for child in node.children:
            if child.kind in (BLOCK, lex.DIRECTIVE):
                lines.append("%s#%s" % ("  " * depth, child.name))
            walk(child, depth + 1 if child.kind == BLOCK else depth)

    walk(root, 0)
    return lines
