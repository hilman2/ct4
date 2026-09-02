"""Re-indent the directive lines whose indent Cheetah throws away.

In a Cheetah template the whitespace is output, so a formatter has
almost nothing it may touch. The one thing it may is the indent in
front of a directive, a comment or an ``#end`` that stands on a line
of its own: ct3 drops that indent before it writes anything
(handleWSBeforeDirective, called from _eatRestOfDirectiveTag and
eatComment where the line is clear and the tag runs to its end), so
what stands there is for the reader alone and the page is the same
whatever it is.

What is done with it is what a reader expects of a block: the
``#end`` stands under its opener, a branch stands under its opener,
and a directive line inside the block stands one step further in. A
top-level line keeps the indent its author gave it; that is the
baseline the block hangs from, and the author chose it to sit in the
markup around it.

Left alone, because there the indent is output or the rules are
another module's: a tag with text before it on its line, a tag
followed by text or by a comment on its line, the colon short forms,
the bodies of ``#raw``, ``#compiler-settings``, ``#defmacro`` and
``#call``, PSP, and JSON-mode templates.
"""

from __future__ import annotations

from ct4.lang import lex, tree

UNIT = "    "

# Blocks whose lines are not touched, tags included. A #raw closes
# with a reach back into the text before it; compiler-settings and
# defmacro hold no template at all; and inside a #call the text is
# collected for the call, where the indent before an #arg line is
# already in the collector when the line is decided about, so it is
# output there. CallDirective.test4 in ct3's suite says so.
OPAQUE = frozenset({"raw", "compiler-settings", "defmacro", "call"})


def format_source(source: str, unit: str = UNIT,
                  names: lex.Syntax | None = None) -> str:
    """The template with its own-line directives re-indented.

    Args:
        source (str): The template.
        unit (str): One step of indentation.
        names (lex.Syntax|None): The names the template is read with;
            None is ct3's own.

    Returns:
        str: The same template, or the same with other indents in front
            of the lines described above. Everything else is byte for
            byte what came in.

    Raises:
        tree.StructureError: where a block is left open or closed
            wrongly; such a file is not formatted.
    """
    from ct4.check import is_json_template

    if is_json_template(source):
        return source
    root = tree.parse(source, names)
    edits: list[tuple[int, int, str]] = []
    _collect(root, source, None, unit, edits)
    made = source
    for start, end, text in sorted(edits, reverse=True):
        made = made[:start] + text + made[end:]
    return made


def _collect(node: tree.Node, source: str, base: str | None, unit: str,
             edits: list[tuple[int, int, str]]) -> None:
    """Walks a block's children and records the indents to set.

    ``base`` is the indent of the opener's line, and None for the
    template itself, whose lines keep their own.
    """
    for child in node.children:
        if child.kind == tree.BLOCK:
            if child.name in OPAQUE:
                continue
            inner = base + unit if base is not None else None
            opener = _own_line(child, source)
            if inner is not None and opener is not None:
                edits.append((opener[0], opener[1], inner))
            # The opener's own line is the baseline the body hangs
            # from, whether the opener was moved or kept.
            line = inner if opener is not None and inner is not None \
                else _leading(source, child.tokens[0].start)
            _collect(child, source, line, unit, edits)
        elif child.kind in (lex.DIRECTIVE, lex.COMMENT) and base is not None:
            own = _own_line(child, source)
            if own is None:
                continue
            aligned = child.name == "end" or child.name in tree.CONTINUATIONS
            edits.append((own[0], own[1], base if aligned else base + unit))


def _own_line(node: tree.Node, source: str) -> tuple[int, int] | None:
    """The indent span of a tag that stands on a line of its own.

    Returns:
        tuple[int, int]|None: The (start, end) of the whitespace before
            the tag, or None where the line is not the tag's alone and
            the indent is therefore output.
    """
    if not node.tokens:
        return None
    start = node.tokens[0].start
    bol = max(source.rfind("\n", 0, start), source.rfind("\r", 0, start)) + 1
    if source[bol:start].strip(" \t\f"):
        return None
    # The tag has to run to its line ending, or to the end of the file:
    # a tag that stops at a hash keeps its indent, and so does one with
    # a comment on its line, which commits the pending text first.
    own = "".join(token.text for token in node.tokens)
    if not (own.endswith(("\n", "\r")) or node.tokens[-1].end >= len(source)):
        return None
    if node.kind != lex.COMMENT and any(
            token.kind in (lex.COMMENT, lex.BLOCK_COMMENT)
            for token in node.tokens[1:]):
        return None
    return (bol, start)


def _leading(source: str, at: int) -> str:
    """The whitespace at the start of the line ``at`` stands on."""
    bol = max(source.rfind("\n", 0, at), source.rfind("\r", 0, at)) + 1
    text = source[bol:at]
    return text[:len(text) - len(text.lstrip(" \t"))]
