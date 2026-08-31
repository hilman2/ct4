"""Ein Bauplatz, der schreibt statt zu sammeln.

Der gewoehnliche Bauplatz baut die ganze Struktur im Speicher und gibt
sie danach an ``json.dumps``. Bei einer Zeitreihe ueber ein Jahr in
Fuenf-Minuten-Schritten sind das hunderttausend Punkte, und sie liegen
zweimal da: als Struktur und als Zeichenkette.

Hier wird stattdessen sofort geschrieben. Das geht, weil der uebersetzte
Code den Bauplatz in genau der Reihenfolge bedient, in der JSON die Teile
erwartet. Kommas entstehen daraus, ob ein Behaelter schon etwas enthaelt,
und koennen deshalb weder fehlen noch zu viel sein.

Byte fuer Byte dasselbe wie der andere Weg. Jeder einzelne Wert geht
durch ``json.dumps``, damit Escaping, Zahlformat und die Behandlung von
``null`` sich nicht unterscheiden koennen. Ein Test vergleicht beide Wege
ueber alle Faelle.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence, TextIO

from ct4.adapters import round_to
from ct4.jsonmode.build import DROP, NULL, OMIT, Builder
from ct4.jsonmode.render import SEPARATORS

# Aus derselben Quelle wie der sammelnde Weg. Zweimal hingeschrieben
# waeren sie einmal falsch: json.dumps setzt ohne Angabe ', ' statt ','
# und der Vergleich beider Wege scheiterte an einem Leerzeichen.
COMMA, COLON = SEPARATORS


def encode(value: Any) -> str:
    """Ein einzelner Wert, so wie json.dumps ihn schreiben wuerde."""
    return json.dumps(value, ensure_ascii=False, separators=SEPARATORS)


class StreamBuilder(Builder):
    """Schreibt die Struktur, waehrend sie entsteht."""

    def __init__(self, out: TextIO, names: Sequence[Any],
                 consts: Sequence[Any],
                 precisions: dict[str, int] | None = None,
                 missing: str = NULL):
        super().__init__(names, consts, precisions, missing)
        self.out = out
        # Je offenem Behaelter: steht schon etwas darin? Daraus
        # entstehen die Kommas.
        self._filled: list[bool] = []
        # Welche Klammer zu schliessen ist. ``end()`` erfaehrt die Art
        # des Behaelters nicht, also wird sie beim Oeffnen gemerkt.
        self._closers: list[str] = []
        self._done = False

    # -- Behaelter ------------------------------------------------

    def _before_value(self) -> None:
        if self._filled and self._filled[-1]:
            self.out.write(COMMA)
        if self._filled:
            self._filled[-1] = True

    def _push(self, kind: str) -> None:
        self.out.write("{" if kind == "obj" else "[")
        self._closers.append("}" if kind == "obj" else "]")
        self._filled.append(False)

    def open(self, kind: str) -> None:
        self._before_value()
        self._push(kind)

    def open_key(self, name_index: int, kind: str) -> None:
        self.open_key_at(self.names[name_index], kind)

    def open_key_at(self, name: Any, kind: str) -> None:
        self._before_value()
        self.out.write(encode(str(name)) + COLON)
        self._push(kind)

    def open_item(self, kind: str) -> None:
        self._before_value()
        self._push(kind)

    def end(self) -> None:
        self._filled.pop()
        self.out.write(self._closers.pop())
        if not self._filled:
            self._done = True

    # -- Werte ----------------------------------------------------

    def key(self, name_index: int, value: Any,
            precision: int | None = None) -> None:
        self.key_at(self.names[name_index], value, precision)

    def key_at(self, name: Any, value: Any,
               precision: int | None = None) -> None:
        text = str(name)
        prepared = self.prepare(value, precision, text)
        if prepared is DROP:
            return
        self._before_value()
        self.out.write(encode(text) + COLON + encode(prepared))

    def item(self, value: Any, precision: int | None = None) -> None:
        prepared = self.prepare(value, precision, None)
        if prepared is DROP:
            prepared = None
        self._before_value()
        self.out.write(encode(prepared))

    def take_first(self) -> None:
        raise NotImplementedError(
            "ein Dokument aus einem einzelnen Wert wird nicht gestroemt; "
            "dafuer gibt es render()")

    @property
    def result(self) -> Any:
        if not self._done:
            raise RuntimeError("die Vorlage hat nichts gebaut")
        return None

    # -- Reihen ---------------------------------------------------

    def series_key(self, name_index: int, source: Iterable[Any],
                   options_index: int) -> None:
        self._before_value()
        self.out.write(encode(str(self.names[name_index])) + COLON)
        self._write_series(source, options_index)

    def series_item(self, source: Iterable[Any],
                    options_index: int) -> None:
        self._before_value()
        self._write_series(source, options_index)

    def _write_series(self, source: Iterable[Any],
                      options_index: int) -> None:
        """Schreibt eine Reihe, ohne sie ganz im Speicher zu halten.

        ``columns`` bricht das: dort steht der erste Wert jeder Spalte
        neben dem letzten, und dafuer muss die Reihe gesammelt werden.
        Nur diese eine Form wird gesammelt, und sie ist als einzige
        nicht stroembar.
        """
        options = self.consts[options_index]
        if options["layout"] == "columns":
            self.out.write(encode(self.series(source, options_index)))
            return

        fields = options["fields"]
        precision = options["precision"]
        gaps = options["gaps"]
        records = options["layout"] == "records"

        self.out.write("[")
        first = True
        for element in source:
            values = [self.field_of(element, name) for name in fields]
            if gaps == OMIT and any(value is None for value in values):
                continue
            values = [self.convert(round_to(value, precision))
                      for value in values]
            if not first:
                self.out.write(COMMA)
            first = False
            self.out.write(encode(dict(zip(fields, values)) if records
                                  else values))
        self.out.write("]")
