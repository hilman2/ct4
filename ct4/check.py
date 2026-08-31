"""Check a template without starting the application.

Three questions, in this order:

1. Does it compile?
2. Does it read names that do not exist?
3. Does it fit the schema it names?

The second one is the one that matters, and it only became possible once
an application declares its names. ``$day.outTemp.mx`` shows up here
without weewx running and without a database answering.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ct4 import analyze
from ct4.declare import Declaration, resolve
from ct4.diagnostics import ERROR, WARNING, Diagnostic

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
                 base_dir: Path | None = None) -> list[Diagnostic]:
    """Checks a template and returns the findings."""
    if is_json_template(source):
        return _check_json(source, file, declarations, base_dir)
    return _check_text(source, file, declarations)


def check_file(path: Path,
               declarations: Sequence[Declaration] = ()) -> list[Diagnostic]:
    return check_source(path.read_text(encoding="utf-8"), str(path),
                        declarations, base_dir=path.parent)


def _check_text(source: str, file: str,
                declarations: Sequence[Declaration]) -> list[Diagnostic]:
    from Cheetah.Parser import ParseError

    try:
        found = analyze.placeholders(source)
    except ParseError as error:
        return [_parse_error(error, file)]
    except Exception as error:                          # noqa: BLE001
        return [Diagnostic("CT4002", ERROR, str(error), file=file)]
    return _check_names(found, file, declarations)


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
