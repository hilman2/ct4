"""Was eine Vorlage aus ihrem Kontext liest.

Nicht durch einen zweiten Parser, sondern aus dem Code, den Cheetah
ohnehin erzeugt. Dort steht jeder Platzhalter als Pfad, mitsamt Zeile
und Spalte, an der er im Template stand:

    _v = VFFSL(SL,"day.outTemp.max",True) # '$day.outTemp.max' on line 5, col 7

Das ist keine Notloesung, sondern die genaueste Quelle, die es gibt: es
ist das, was zur Laufzeit wirklich nachgeschlagen wird. Ein eigener
Parser koennte davon abweichen, dieser Weg nicht.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

# Ein Nachschlagen in der searchList. VFFSL loest den Pfad ueber Frame
# und searchList auf, VFSL nur ueber die searchList.
LOOKUP = re.compile(r'VFF?SL\(SL,"([^"]+)",\s*(?:True|False)\)')

# Die Herkunftsangabe, die Cheetah hinter den Ausdruck schreibt.
ORIGIN = re.compile(r"(?:on|from) line (\d+), col (\d+)")

# Steht in der Vorlage ein #errorCatcher, wandert jeder Platzhalter in
# eine eigene Methode. Dann trennt Cheetah Ausdruck und Herkunft auf
# mehrere Zeilen, und nur der Aufruf von warn() traegt beides. Ohne
# diesen zweiten Fall verlaeren die Skins von weewx ihre Platzhalter:
# Seasons setzt den errorCatcher.
LINECOL = re.compile(r"lineCol=\((\d+),\s*(\d+)\)")


@dataclass(frozen=True)
class Placeholder:
    """Ein Pfad, den die Vorlage nachschlaegt, und wo er steht."""

    path: str
    line: int
    column: int

    @property
    def root(self) -> str:
        return self.path.split(".")[0]


def placeholders(source: str, settings: dict[str, Any] | None = None,
                 ) -> list[Placeholder]:
    """Alle Nachschlagevorgaenge einer Vorlage, in Reihenfolge der Datei."""
    from Cheetah.Compiler import ModuleCompiler

    options = dict(settings or {})
    options["addTimestampsToCompilerOutput"] = False
    compiler = ModuleCompiler(source, moduleName="ct4_analyze",
                              mainClassName="ct4_analyze", settings=options)
    return sorted(_scan(str(compiler)),
                  key=lambda item: (item.line, item.column))


def _scan(code: str) -> Iterator[Placeholder]:
    for text in code.splitlines():
        origin = LINECOL.search(text) or ORIGIN.search(text)
        if origin is None:
            continue
        line, column = int(origin.group(1)), int(origin.group(2))
        # Nur die Nachschlagevorgaenge in der searchList. Ein VFN steht
        # fuer ein Attribut auf einem schon aufgeloesten Wert; welche es
        # dort gibt, weiss keine Anmeldung, und geraten wuerde daraus ein
        # Falschbefund. Stehen mehrere Ausdruecke auf einer Zeile,
        # teilen sie sich deren Position.
        for path in LOOKUP.findall(text):
            yield Placeholder(path, line, column)


def roots(items: list[Placeholder]) -> list[str]:
    """Die Wurzeln, die eine Vorlage braucht, ohne Wiederholung."""
    return sorted({item.root for item in items})


def paths(items: list[Placeholder]) -> list[str]:
    """Die vollen Pfade, ohne Wiederholung."""
    return sorted({item.path for item in items})
