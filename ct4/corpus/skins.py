"""Fremde Skins als Uebersetzungsfaelle in den Korpus nehmen.

Ein weewx-Skin laesst sich nicht rendern, ohne weewx samt Datenbank zu
starten. Sein Kontext ist eine laufende Anwendung, keine Datei. Uebersetzt
werden kann er aber sehr wohl, und damit wird genau das geprueft, was P4
im Plan ersetzen will: Parser und Codegenerator.

Ein Uebersetzungsfall haelt den erzeugten Modulcode fest. Aendert ct4 die
Sprache versehentlich, faellt das hier auf, lange bevor irgendwer eine
Wetterstation braucht.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterator

from ct4.corpus.case import COMPILE, Case

# weewx-Skins legen Vorlagen unter beiden Endungen ab: .tmpl fuer eine
# Seite, .inc fuer einen Baustein, den eine Seite per #include holt.
SUFFIXES = (".tmpl", ".inc")


def harvest(root: Path, name: str) -> tuple[list[Case], Counter[str]]:
    """Uebersetzt jede Vorlage unter ``root`` und legt sie als Fall ab.

    ``name`` wird der Namensraum der Fall-Kennungen, damit sich ein Skin
    im Korpus wiederfinden laesst. Zurueck kommen die Faelle und eine
    Zaehlung dessen, was nicht uebersetzbar war.
    """
    from ct4.corpus.check import compile_code

    cases: list[Case] = []
    skipped: Counter[str] = Counter()
    for path in sorted(_templates(root)):
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped["nicht UTF-8"] += 1
            continue
        case = Case(
            id="%s/%s" % (name, relative),
            template=source,
            expected="",
            kind=COMPILE,
            origin=name,
        )
        try:
            code = compile_code(case)
        except Exception as exc:                        # noqa: BLE001
            # Eine Vorlage, die ct3 nicht uebersetzt, ist kein Massstab.
            # Gezaehlt wird sie nach Ausnahmetyp, damit sichtbar bleibt,
            # ob es an der Vorlage liegt oder an unserem Aufruf.
            skipped[type(exc).__name__] += 1
            continue
        cases.append(Case(**{**case.__dict__, "expected": code}))
    return cases, skipped


def _templates(root: Path) -> Iterator[Path]:
    for suffix in SUFFIXES:
        yield from root.rglob("*" + suffix)
