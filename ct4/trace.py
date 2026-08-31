"""Aus einer Zeile im erzeugten Modul die Zeile in der Vorlage machen.

Ein Traceback aus einer Cheetah-Vorlage zeigt auf
``cheetah_DynamicallyCompiledCheetahTemplate_1788178423_42383.py``,
Zeile 243. Das ist die Wahrheit, aber niemandem geholfen: die Datei gibt
es nicht auf der Platte, und wer den Fehler beheben will, sucht die
Stelle in seiner Vorlage.

Die Zuordnung steht schon da. Cheetah schreibt hinter jede erzeugte
Anweisung, aus welcher Zeile und Spalte sie stammt. Hier wird das
gelesen und an die Ausnahme gehaengt, statt es wegzuwerfen.

Angehaengt, nicht ersetzt: der urspruengliche Traceback bleibt stehen.
Wer im erzeugten Code nachsehen will, kann das weiter.
"""

from __future__ import annotations

import re
from types import TracebackType
from typing import Any, Iterator, Literal

# Dieselben zwei Formen wie in ct4.analyze: die gewoehnliche und die,
# die ein #errorCatcher erzeugt.
ORIGIN = re.compile(r"(?:on|from) line (\d+), col (\d+)")
LINECOL = re.compile(r"lineCol=\((\d+),\s*(\d+)\)")

# Woran ein erzeugtes Modul zu erkennen ist.
GENERATED = "cheetah"


def line_map(code: str) -> dict[int, tuple[int, int]]:
    """Zeile im erzeugten Modul auf Zeile und Spalte in der Vorlage.

    Eingetragen wird nur, wo eine Herkunft steht. Fuer alles dazwischen
    gilt der letzte Eintrag davor; das erledigt ``position_of``.
    """
    found: dict[int, tuple[int, int]] = {}
    for number, text in enumerate(code.splitlines(), 1):
        match = LINECOL.search(text) or ORIGIN.search(text)
        if match is not None:
            found[number] = (int(match.group(1)), int(match.group(2)))
    return found


def position_of(mapping: dict[int, tuple[int, int]],
                line: int) -> tuple[int, int] | None:
    """Die Stelle in der Vorlage, die zu einer erzeugten Zeile gehoert.

    Genommen wird die letzte Herkunft an oder vor der Zeile. Eine
    Anweisung erstreckt sich ueber mehrere erzeugte Zeilen, und die
    Herkunft steht an ihrem Anfang.
    """
    candidates = [number for number in mapping if number <= line]
    if not candidates:
        return None
    return mapping[max(candidates)]


def note(error: BaseException, text: str) -> None:
    """Haengt eine Bemerkung an eine Ausnahme.

    ``add_note`` gibt es erst ab Python 3.11, und ct4 laeuft ab 3.10.
    Wo es fehlt, wird die Bemerkung an der Ausnahme abgelegt; wer sie
    braucht, findet sie unter ``ct4_notes``.
    """
    adder = getattr(error, "add_note", None)
    if adder is not None:
        adder(text)
        return
    notes = getattr(error, "ct4_notes", None)
    if notes is None:
        notes = []
        error.ct4_notes = notes                         # type: ignore[attr-defined]
    notes.append(text)


def frames(error: BaseException) -> Iterator[TracebackType]:
    traceback = error.__traceback__
    while traceback is not None:
        yield traceback
        traceback = traceback.tb_next


def is_generated(name: str) -> bool:
    return GENERATED in name.lower()


def describe(error: BaseException, code: str,
             file: str = "<vorlage>") -> list[str]:
    """Die Stellen in der Vorlage, durch die der Fehler gelaufen ist."""
    mapping = line_map(code)
    out = []
    for frame in frames(error):
        if not is_generated(frame.tb_frame.f_code.co_filename):
            continue
        where = position_of(mapping, frame.tb_lineno)
        if where is None:
            continue
        out.append("%s, Zeile %d, Spalte %d" % (file, where[0], where[1]))
    return out


def annotate(error: BaseException, code: str,
             file: str = "<vorlage>") -> BaseException:
    """Haengt die Stellen in der Vorlage an die Ausnahme.

    Ueber ``add_note``, damit sie beim Ausgeben des Tracebacks
    mitkommen, ohne dass irgendetwas ersetzt wird.
    """
    for line in describe(error, code, file):
        note(error, "Vorlage: %s" % line)
    return error


class mapped:
    """Ein Block, dessen Ausnahmen die Stelle in der Vorlage tragen.

    ``code`` ist der erzeugte Modulcode; er wird erst geholt, wenn
    wirklich etwas schiefgeht, weil das Aufbereiten sonst jeden Lauf
    kostet.
    """

    def __init__(self, code: Any, file: str = "<vorlage>"):
        self.code = code
        self.file = file

    def __enter__(self) -> "mapped":
        return self

    def __exit__(self, kind: Any, error: Any,
                 traceback: Any) -> Literal[False]:
        if error is not None:
            code = self.code() if callable(self.code) else self.code
            if code:
                annotate(error, code, self.file)
        return False


class mapped_via:
    """Wie ``mapped``, aber mit einer schon fertigen Zuordnung.

    Der JSON-Modus uebersetzt in eine Cheetah-Definition. Deren Zeilen
    stehen in keiner Datei, und die Herkunftsangaben darin zeigen auf sie
    selbst. Die Bruecke zur Vorlage baut der Emitter, und sie kommt hier
    an.
    """

    def __init__(self, origins: dict[int, int], file: str = "<vorlage>"):
        self.origins = origins
        self.file = file

    def __enter__(self) -> "mapped_via":
        return self

    def __exit__(self, kind: Any, error: Any,
                 traceback: Any) -> Literal[False]:
        if error is None or not self.origins:
            return False
        mapping = {generated: (source, 1)
                   for generated, source in self.origins.items()}
        for frame in frames(error):
            if not is_generated(frame.tb_frame.f_code.co_filename):
                continue
            where = position_of(mapping, frame.tb_lineno)
            if where is not None:
                note(error, "Vorlage: %s, Zeile %d"
                     % (self.file, where[0]))
                break
        return False
