"""Check a template without starting the application.

Three questions, in this order:

1. Does it compile?
2. Does it read names that do not exist?
3. Does it fit the schema it names?

The second one is the one that matters, and it only became possible once
an application declares its names. ``$day.outTemp.mx`` shows up here
without weewx running and without a database answering.

A markup-mode template gets a fourth question, and it is the reason
this module exists at all: which of its placeholders markup mode will
not escape. Those refuse at render time, one page at a time and only
where the value happens to be reached, so an author who has to find
them by rendering finds them one by one. Here the whole list arrives
before a page is built.
"""

from __future__ import annotations

import bisect
from pathlib import Path
from typing import Any, Sequence

from ct4 import analyze
from ct4.declare import Declaration, resolve
from ct4.diagnostics import ERROR, WARNING, Diagnostic
from ct4.lang import lex
from ct4.markup import mode as markup_mode

# JSON mode is announced, not guessed from the file extension. weewx
# skins have always shipped .json.tmpl, and those are text templates
# that assemble JSON by hand. Exactly those ct4 shall keep compiling
# like ct3, not read as a JSON document.
MODE_LINE = "#mode json"


def is_json_template(source: str) -> bool:
    """Whether the template announces JSON mode.

    The announcement stands on the first line that is not a comment.
    """
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("##"):
            continue
        return stripped == MODE_LINE
    return False


def check_source(source: str, file: str = "",
                 declarations: Sequence[Declaration] = (),
                 base_dir: Path | None = None,
                 settings: dict[str, Any] | None = None) -> list[Diagnostic]:
    """Checks a template and returns the findings.

    Args:
        source (str): the template.
        file (str): the name the findings carry.
        declarations (Sequence[ct4.declare.Declaration]): the
            applications whose names the lookups are held against.
        base_dir (pathlib.Path|None): where a JSON template's schema
            and includes are resolved from.
        settings (dict[str, Any]|None): the compiler settings it will
            be compiled with. Only the text mode has any, and one of
            them decides whether the file parses at all: a template
            written for ``allowWhitespaceAfterDirectiveStartToken``
            reads as text with a stray ``#end`` in it without.

    Returns:
        list[ct4.diagnostics.Diagnostic]: the findings, in the order
            the author reads the file.
    """
    if is_json_template(source):
        return _check_json(source, file, declarations, base_dir)
    if markup_mode.declared(source):
        return _check_markup(source, file, declarations)
    return _check_text(source, file, declarations, settings)


def check_file(path: Path,
               declarations: Sequence[Declaration] = ()) -> list[Diagnostic]:
    return check_source(path.read_text(encoding="utf-8"), str(path),
                        declarations, base_dir=path.parent)


def _check_text(source: str, file: str,
                declarations: Sequence[Declaration],
                settings: dict[str, Any] | None = None) -> list[Diagnostic]:
    from Cheetah.Parser import ParseError

    try:
        found = analyze.placeholders(source, settings, file)
    except ParseError as error:
        return [_parse_error(error, file)]
    except Exception as error:                          # noqa: BLE001
        return [Diagnostic("CT4002", ERROR, str(error), file=file)]
    return _check_dotless_links(source, file) + \
        _check_names(found, file, declarations)


def _check_dotless_links(source: str, file: str) -> list[Diagnostic]:
    """Placeholders whose chain carries on over a missing dot.

    Cheetah's chunk loop does not stop at a bracket: after ``(args)``
    or ``[key]`` a bare letter opens the next link of the chain, as a
    dot would, and the dot is discarded anyway. So the two spellings
    compile to the same code and no engine can tell them apart.

    That is worth a warning in both directions it goes wrong.
    ``.round(5)json()`` is a dot the author dropped and gets away with;
    ``$temp.formatted(2)F`` is a letter the author meant to print and
    which turns into an attribute lookup instead. Neither leaves a
    trace in the rendered page.
    """
    out: list[Diagnostic] = []
    for token in lex.tokens(source):
        if token.kind != lex.PLACEHOLDER:
            continue
        marked = lex.start_of(token.text)
        if marked is None or marked.group("enclosure"):
            continue
        starts = lex.line_starts(source)
        for offset in lex.dotless_links(token.text, marked.end()):
            at = token.start + offset
            line = bisect.bisect_right(starts, at)
            end = offset
            while end < len(token.text) and token.text[end] in lex.IDENT:
                end += 1
            name = token.text[offset:end]
            out.append(Diagnostic(
                "CT4005", WARNING,
                "%r continues the chain as if a dot stood before it; "
                "write the dot, or move the text out of the placeholder"
                % name,
                file=file, line=line, column=at - starts[line - 1] + 1,
                path=token.text))
    return out


def _check_json(source: str, file: str, declarations: Sequence[Declaration],
                base_dir: Path | None) -> list[Diagnostic]:
    from ct4.jsonmode import compile_template
    from ct4.jsonmode.parse import JsonTemplateError

    try:
        compiled = compile_template(source, base_dir=base_dir)
    except JsonTemplateError as error:
        return [Diagnostic("CT4003", ERROR, str(error), file=file)]
    except FileNotFoundError as error:
        return [Diagnostic("CT4004", ERROR,
                           "the schema is missing: %s" % error.filename,
                           file=file)]

    found: list[Diagnostic] = []
    for entry in compiled.check():
        found.append(Diagnostic(entry.code, entry.severity, entry.message,
                                file=file, path=entry.path))
    # The generated code of the definition carries the same origin notes
    # as an ordinary template, but they point into the definition, not
    # into the template. Hence without a line.
    names = analyze.placeholders(_expression_probe(compiled))
    found.extend(_check_names(names, file, declarations, with_position=False))
    return found


def _check_markup(source: str, file: str,
                  declarations: Sequence[Declaration]) -> list[Diagnostic]:
    """What markup mode will not escape in this template, all of it.

    Four findings, and each one is a decision the author has to make
    rather than a defect the tool found:

    * CT4402, an error, is the file being refused whole. Markup mode
      never falls back to ct3, so this is the compile failing, and it
      is reported on its own because nothing after it is worth saying.
    * CT4400, a warning, is a placeholder in a position that cannot be
      escaped. It renders only if the value was passed through
      ``ct4.markup.quoted()``.
    * CT4401, a warning, is a placeholder at the head of a URL
      attribute. It is escaped like any attribute value, and that stops
      quote breaking and not ``javascript:``.

    The first two are read off the compiler rather than worked out
    again from the scan, and that is the point of them: what an author
    is warned about here is the very list the render will act on, down
    to the writes that are not placeholders at all. ``#echo`` is one of
    those, and a scan of the template alone does not find it.

    The line numbers are the author's throughout. The declaration line
    is cut before anything parses, so a line read off the parsed source
    is short of the file by however many lines that cut, and every one
    of them is put back; the names are read off the source with the
    line still in it and need no such correction.
    """
    from ct4.lang import codegen, tree
    from ct4.markup import scan as markup_scan

    try:
        # Generated first, because markup mode does not fall back: a
        # template the generator refuses does not render at all, and
        # that is the first thing the author has to hear. What comes
        # back carries the decision taken for every placeholder.
        made = codegen.generate(source, mode=codegen.MARKUP_MODE, file=file)
        root, shift = codegen.preparsed(source)
    except markup_scan.ScanRefused as refused:
        # Already the author's line: the compiler corrects a refusal on
        # the way out, because the build catches these too and cannot
        # know what was cut.
        return [Diagnostic("CT4402", ERROR, refused.reason, file=file,
                           line=refused.line, column=refused.column)]
    except tree.StructureError as refused:
        # ct3's parser is what is unhappy here, and it counts lines in
        # the source it was handed. One line was taken out of it.
        return [Diagnostic("CT4402", ERROR, str(refused), file=file,
                           line=refused.line + 1, column=refused.column)]
    except codegen.Unsupported as refused:
        return [Diagnostic(
            "CT4402", ERROR,
            "markup mode does not fall back to ct3, and this template "
            "cannot be generated: %s" % refused, file=file)]
    except Exception as error:                          # noqa: BLE001
        return [Diagnostic("CT4002", ERROR, str(error), file=file)]

    found: list[Diagnostic] = []
    assert made.markup is not None
    for note in made.markup.notes:
        if note.kind == codegen.MARKUP_REFUSED:
            found.append(Diagnostic(
                "CT4400", WARNING,
                "markup mode cannot escape %s; pass the value through "
                "ct4.markup.quoted() or the render stops" % note.note,
                file=file, line=note.line, column=note.column))
        else:
            found.append(Diagnostic(
                "CT4401", WARNING,
                "this placeholder is the whole head of a URL; escaping "
                "stops a quote and not a javascript: scheme",
                file=file, line=note.line, column=note.column))
    # In the order the author reads the file. The compiler writes the
    # directives as it walks the tree and the plain placeholders in a
    # pass after that, so its own order is the generator's and not the
    # template's.
    found.extend(_check_dotless_links(source, file))
    found.sort(key=lambda one: (one.line, one.column))
    found.extend(_check_names(analyze.placeholders(source), file,
                              declarations))
    return found


def _expression_probe(compiled: object) -> str:
    """A template that looks up exactly the expressions of the document.

    JSON mode compiles into a ``#def``; its line numbers belong to that
    and not to the author's template. For checking the names it is
    enough all the same, because what is checked is the path, not its
    place.
    """
    from ct4.jsonmode.emit import emit

    return emit(compiled.document)[0]                   # type: ignore[attr-defined]


def _check_names(found: list[analyze.Placeholder], file: str,
                 declarations: Sequence[Declaration],
                 with_position: bool = True) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    seen: set[tuple[str, int, int]] = set()
    for item in found:
        for declaration in declarations:
            unknown = resolve(declaration, item.path)
            if unknown is None:
                continue
            marker = (item.path, item.line, item.column)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(Diagnostic(
                "CT4103", ERROR,
                "%s knows no field %r on %s"
                % (declaration.name, unknown.name, unknown.prefix),
                file=file,
                line=item.line if with_position else 0,
                column=item.column if with_position else 0,
                path="$" + item.path,
                suggestions=unknown.suggestions))
    return out


def _parse_error(error: object, file: str) -> Diagnostic:
    """Turns Cheetah's ParseError into a finding with a place.

    ``lineno`` and ``col`` are mostly None on the exception; the position
    stands in the stream Cheetah builds its report from. The report
    itself does not go into the message: it spans several lines and
    repeats the template, which only gets in the way in a list of
    findings.
    """
    line = getattr(error, "lineno", None)
    column = getattr(error, "col", None)
    stream = getattr(error, "stream", None)
    if (line is None or column is None) and stream is not None:
        try:
            line, column = stream.getRowCol()
        except Exception:                               # noqa: BLE001
            line, column = 0, 0
    message = getattr(error, "msg", None) or str(error)
    return Diagnostic("CT4001", ERROR, " ".join(str(message).split()),
                      file=file, line=line or 0, column=column or 0)


def unresolved(source: str, declarations: Sequence[Declaration],
               ) -> list[Diagnostic]:
    """Reports roots that no declaration says anything about.

    Not an error, but the honest word on where the check stops.
    """
    known = {name for declaration in declarations
             for name in declaration.roots}
    out = []
    for root in analyze.roots(analyze.placeholders(source)):
        if root not in known:
            out.append(Diagnostic(
                "CT4110", WARNING,
                "no declaration says anything about $%s; nothing is "
                "checked here" % root, path="$" + root))
    return out
