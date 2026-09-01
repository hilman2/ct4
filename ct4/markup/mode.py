"""How a template says it wants markup mode, and how that line is cut.

One declared line and nothing else. A template is in markup mode when
its first line that is neither blank nor a ``##`` comment is exactly
``#mode markup``. ``ct4.check.is_json_template`` decides JSON mode by
the same rule, and this is the same rule for the same reasons.

Not the file extension. ``os.path.splitext("index.html.tmpl")`` is
``".tmpl"``, so the one part of that name which says HTML is the part
splitext throws away. And an extension rule could not be measured here
even where it guessed right: ct4's build, its corpus checker and all
three instruments under tests/fuzz compile from a source string with no
path at all, so a rule keyed on a name would never fire in any run that
holds this engine to account. Where it did fire it would be wrong: the
``.json.tmpl`` skins in the corpus are hand-written text templates that
assemble JSON by hand, which is why tests/unit/test_tools.py already
asserts that the extension does not decide for JSON mode.

Not a directive either, and that one is measured rather than argued.
``mode`` is not in ``Cheetah.Parser.directiveNamesAndParsers`` and must
not be put there. Registering a name with no eater behind it leaves
ct3's parser spinning on the line for as long as it is given (killed
after six seconds), and ``ct4.lang.lex.directive_names()`` reads that
same dictionary at call time, so the registration would move ct4's
lexer along with ct3's. Every differential instrument would then be
comparing two changed engines and reporting no difference, which is the
one failure a test bench cannot tell from success.

So the declaration is cut out before anything parses, the way ct3 cuts
``#unicode`` with a regular expression in ``ModuleCompiler.__init__``,
at a point where there is no parser object to tell.
"""

from __future__ import annotations

MODE_LINE = "#mode markup"


def _is_skippable(line: str) -> bool:
    """Whether a line may stand in front of the declaration.

    Only what carries no output of its own: an empty line and a ``##``
    comment, which is what a licence header at the top of a skin is
    made of.
    """
    stripped = line.strip()
    return not stripped or stripped.startswith("##")


def declared(source: str) -> bool:
    """Whether the template announces markup mode.

    The announcement stands on the first line that is neither blank nor
    a ``##`` comment, and it has to be that whole line: ``#mode
    markup`` written with two blanks is not it, and neither is the same
    text further down the file, where it is ordinary output text and
    ct3 writes it out as such. One spelling in one place, so that a
    template either says it or does not and no one has to read the
    engine to find out which.

    Args:
        source (str): The template as it stands in the file.

    Returns:
        bool: True where the template declares markup mode.
    """
    for line in source.splitlines():
        if _is_skippable(line):
            continue
        return line.strip() == MODE_LINE
    return False


def strip(source: str) -> str:
    """The template with the declaration line taken out.

    Called from the compiler's preprocessing step, next to where ct3's
    ``#unicode`` line is cut, because the mode has to be known before
    there is a parser to ask.

    One consequence to carry rather than to hide: the line is removed
    with its line ending, so every line after it moves up by one and a
    line number read off the stripped source is one short of the
    author's file. The lines in front of the declaration keep their
    numbers, being blanks and ``##`` comments. ct3's ``#unicode``
    substitution shifts its template the same way.

    Args:
        source (str): The template as it stands in the file.

    Returns:
        str: The source without that one line, every other byte where
            it was. A source that does not declare markup mode comes
            back unchanged.
    """
    lines = source.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if _is_skippable(line):
            continue
        if line.strip() != MODE_LINE:
            return source
        del lines[index]
        return "".join(lines)
    return source
