"""Eine Vorlage pruefen, ohne die Anwendung zu starten.

Drei Fragen, in dieser Reihenfolge:

1. Laesst sie sich uebersetzen?
2. Liest sie Namen, die es nicht gibt?
3. Passt sie zu dem Schema, das sie nennt?

Die zweite ist die, auf die es ankommt, und sie ist erst moeglich, seit
eine Anwendung ihre Namen anmeldet. ``$day.outTemp.mx`` faellt hier auf,
ohne dass weewx laeuft und ohne dass eine Datenbank antwortet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ct4 import analyze
from ct4.declare import Declaration, resolve
from ct4.diagnostics import ERROR, WARNING, Diagnostic

# Der JSON-Modus wird angesagt, nicht an der Dateiendung erraten.
# weewx-Skins liefern seit jeher .json.tmpl aus, und das sind
# Textvorlagen, die JSON von Hand zusammensetzen. Genau die soll ct4
# weiter uebersetzen wie ct3, nicht als JSON-Dokument lesen.
MODE_LINE = "#mode json"


def is_json_template(source: str) -> bool:
    """Ob die Vorlage den JSON-Modus ansagt.

    Die Ansage steht in der ersten Zeile, die kein Kommentar ist.
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
    """Prueft eine Vorlage und gibt die Befunde zurueck."""
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
                           "das Schema fehlt: %s" % error.filename,
                           file=file)]

    found: list[Diagnostic] = []
    for entry in compiled.check():
        found.append(Diagnostic(entry.code, entry.severity, entry.message,
                                file=file, path=entry.path))
    # Der erzeugte Code der Definition traegt dieselben Herkunftsangaben
    # wie eine gewoehnliche Vorlage, aber sie zeigen in die Definition,
    # nicht in die Vorlage. Deshalb ohne Zeile.
    names = analyze.placeholders(_expression_probe(compiled))
    found.extend(_check_names(names, file, declarations, with_position=False))
    return found


def _expression_probe(compiled: object) -> str:
    """Eine Vorlage, die genau die Ausdruecke des Dokuments nachschlaegt.

    Der JSON-Modus uebersetzt in eine ``#def``; deren Zeilennummern
    gehoeren zu ihr und nicht zur Vorlage des Autors. Zum Pruefen der
    Namen reicht das trotzdem, denn geprueft wird der Pfad, nicht sein
    Ort.
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
                "%s kennt kein Feld %r auf %s"
                % (declaration.name, unknown.name, unknown.prefix),
                file=file,
                line=item.line if with_position else 0,
                column=item.column if with_position else 0,
                path="$" + item.path,
                suggestions=unknown.suggestions))
    return out


def _parse_error(error: object, file: str) -> Diagnostic:
    """Macht aus Cheetahs ParseError einen Befund mit Ort.

    ``lineno`` und ``col`` sind an der Ausnahme meistens None; die
    Position steht im Datenstrom, aus dem Cheetah seinen Bericht baut.
    Der Bericht selbst wandert nicht in die Meldung: er ist mehrzeilig
    und wiederholt die Vorlage, was in einer Liste von Befunden nur
    stoert.
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
    """Meldet Wurzeln, zu denen keine Anmeldung etwas sagt.

    Kein Fehler, sondern die ehrliche Auskunft, wo die Pruefung aufhoert.
    """
    known = {name for declaration in declarations
             for name in declaration.roots}
    out = []
    for root in analyze.roots(analyze.placeholders(source)):
        if root not in known:
            out.append(Diagnostic(
                "CT4110", WARNING,
                "zu $%s sagt keine Anmeldung etwas; hier wird nicht "
                "geprueft" % root, path="$" + root))
    return out
