"""What a template declares about itself on its first line.

One line, ``#mode`` and one or two words, on the first line that is
neither blank nor a ``##`` comment. ``json`` is a document built and
then serialised, ``markup`` is HTML with escaping by position, and
``strict`` is Python semantics for the lookups: no autocalling, and a
name the template bound itself is a Python name. ``markup`` and
``strict`` combine; ``json`` stands alone.

The same line for every mode, and the same place, so that a template
either says it or does not and nobody has to read the engine to find
out which. Written further down the file it is ordinary text, and ct3
writes it out as such.

No ``#mode`` directive exists, and that is measured: ct3's parser has
no eater for the name and stops on it, and registering one would move
both engines at once, which no differential instrument could see. The
line is cut out before parsing, next to where ct3 cuts ``#unicode``.
"""

from __future__ import annotations

KEYWORD = "#mode"
JSON = "json"
MARKUP = "markup"
STRICT = "strict"
KNOWN = frozenset({JSON, MARKUP, STRICT})


def is_skippable(line: str) -> bool:
    """Whether a line may stand in front of the declaration.

    Only what carries no output of its own: an empty line and a ``##``
    comment, which is what a licence header at the top of a skin is
    made of.
    """
    stripped = line.strip()
    return not stripped or stripped.startswith("##")


def declared(source: str) -> frozenset[str]:
    """The modes the template declares, an empty set for none.

    The words as written, so that a misspelt one comes back and the
    reader can say so rather than quietly fall into text mode.
    """
    for line in source.splitlines():
        if is_skippable(line):
            continue
        words = line.split()
        # The whole line, one blank between the words: "#mode  markup"
        # with two is not it, so that there is one spelling.
        if len(words) > 1 and words[0] == KEYWORD \
                and line.strip() == " ".join(words):
            return frozenset(words[1:])
        return frozenset()
    return frozenset()


def strict(source: str) -> bool:
    return STRICT in declared(source)


def strip(source: str) -> str:
    """The template with a markup or strict declaration line taken out.

    Called from the compiler's preprocessing step, because the mode
    has to be known before there is a parser to ask. A JSON template
    keeps its line: its own parser reads it.

    One consequence to carry rather than to hide: the line is removed
    with its line ending, so every line after it moves up by one and a
    line number read off the stripped source is one short of the
    author's file. The lines in front of the declaration keep their
    numbers, being blanks and ``##`` comments. ct3's ``#unicode``
    substitution shifts its template the same way.
    """
    found = declared(source)
    if not found or JSON in found:
        return source
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if is_skippable(line):
            continue
        return "".join(lines[:index] + lines[index + 1:])
    return source
