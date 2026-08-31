"""Der Bauplatz, auf dem eine JSON-Struktur entsteht.

Der uebersetzte Code ruft hier Methoden auf, statt Zeichen zu schreiben.
Deshalb kann kein Komma fehlen und keins zu viel sein: es gibt keine.

Namen und feste Werte aus der Vorlage stehen in ``names`` und ``consts``
und werden ueber ihren Index angesprochen. Sie im uebersetzten Code
stehen zu lassen waere heikel, weil Cheetah dort jedes ``$`` als
Platzhalter liest, auch eines mitten in einem Schluessel.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from ct4.adapters import Ct4Value, as_value, round_to

OMIT = "omit"
NULL = "null"
ERROR = "error"

# Ein Wert, der nicht in die Ausgabe gehoert. Nicht None: None ist ein
# gueltiges Ergebnis und wird zu null.
_DROP = object()


class MissingValue(ValueError):
    """Ein Feld hat keinen Wert, und die Vorlage verlangt einen."""


class Builder:
    """Nimmt die Aufrufe des uebersetzten Codes entgegen."""

    def __init__(self, names: Sequence[Any], consts: Sequence[Any],
                 precisions: dict[str, int] | None = None,
                 missing: str = NULL):
        self.names = names
        self.consts = consts
        self.precisions = precisions or {}
        self.missing = missing
        self._stack: list[Any] = []
        self._result: Any = _DROP

    # -- Behaelter ------------------------------------------------

    def open(self, kind: str) -> None:
        """Beginnt den Wurzelbehaelter."""
        self._stack.append({} if kind == "obj" else [])

    def open_key(self, name_index: int, kind: str) -> None:
        """Beginnt einen Behaelter unter einem Schluessel."""
        container: Any = {} if kind == "obj" else []
        self._stack[-1][self.names[name_index]] = container
        self._stack.append(container)

    def open_key_at(self, name: Any, kind: str) -> None:
        """Wie ``open_key``, aber der Schluessel entsteht zur Laufzeit."""
        container: Any = {} if kind == "obj" else []
        self._stack[-1][str(name)] = container
        self._stack.append(container)

    def take_first(self) -> None:
        """Macht aus dem Hilfsbehaelter der Wurzel deren einzigen Wert.

        Ein Dokument darf aus einem einzelnen Wert bestehen. Der Bauplatz
        braucht trotzdem einen Behaelter, und der wird hier wieder
        abgezogen.
        """
        self._result = self._result[0]

    def open_item(self, kind: str) -> None:
        """Beginnt einen Behaelter als Element einer Liste."""
        container: Any = {} if kind == "obj" else []
        self._stack[-1].append(container)
        self._stack.append(container)

    def end(self) -> None:
        done = self._stack.pop()
        if not self._stack:
            self._result = done

    @property
    def result(self) -> Any:
        if self._result is _DROP:
            raise RuntimeError("die Vorlage hat nichts gebaut")
        return self._result

    # -- Werte ----------------------------------------------------

    def key(self, name_index: int, value: Any,
            precision: int | None = None) -> None:
        name = self.names[name_index]
        prepared = self.prepare(value, precision, name)
        if prepared is _DROP:
            return
        self._stack[-1][name] = prepared

    def key_at(self, name: Any, value: Any,
               precision: int | None = None) -> None:
        """Wie ``key``, aber der Schluessel entsteht erst zur Laufzeit."""
        text = str(name)
        prepared = self.prepare(value, precision, text)
        if prepared is _DROP:
            return
        self._stack[-1][text] = prepared

    def item(self, value: Any, precision: int | None = None) -> None:
        prepared = self.prepare(value, precision, None)
        if prepared is _DROP:
            # In einer Liste laesst sich nichts weglassen, ohne die
            # Stellen zu verschieben. Ein fehlendes Element wird null,
            # auch wenn die Politik sonst omit heisst.
            prepared = None
        self._stack[-1].append(prepared)

    def lit(self, const_index: int) -> Any:
        return self.consts[const_index]

    def cat(self, const_index: int, *values: Any) -> str:
        """Setzt eine Zeichenkette aus festen Stuecken und Werten zusammen.

        Hier gilt ``str()`` des Objekts, nicht sein ``__ct4_value__``.
        Der Unterschied ist Absicht: an einer Wertposition ist ein
        Platzhalter ein Wert und wird zur Zahl, in einer Zeichenkette ist
        er Text und behaelt die Formatierung, die das Objekt selbst
        mitbringt. Bei weewx heisst das: ``$day.outTemp.max`` als Wert
        wird 12.3, in einer Zeichenkette wird es "12.3 °C".
        """
        chunks = self.consts[const_index]
        out = [chunks[0]]
        for index, value in enumerate(values):
            out.append("" if value is None else str(value))
            out.append(chunks[index + 1])
        return "".join(out)

    # -- Umwandlung -----------------------------------------------

    def prepare(self, value: Any, precision: int | None,
                name: str | None) -> Any:
        """Macht aus einem gelesenen Wert das, was im JSON steht."""
        hook = getattr(value, "__ct4_json__", None)
        if hook is not None:
            return hook()

        described = as_value(value)
        raw = described.value
        if raw is None:
            return self.on_missing(name)
        if precision is None:
            precision = self.precision_for(name, described)
        return self.convert(round_to(raw, precision))

    def precision_for(self, name: str | None, described: Ct4Value) -> Any:
        """Welche Rundung gilt: Vorlage vor Anwendung, sonst die Vorgabe."""
        if name is not None and name in self.precisions:
            return self.precisions[name]
        if described.precision is not None:
            return described.precision
        return self.precisions.get("default")

    def on_missing(self, name: str | None) -> Any:
        if self.missing == NULL:
            return None
        if self.missing == OMIT:
            return _DROP
        raise MissingValue(
            "%s hat keinen Wert" % (name or "ein Element"))

    def convert(self, value: Any) -> Any:
        """Bringt einen Wert in eine Form, die json.dumps kennt."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [self.convert(item) for item in value]
        if isinstance(value, dict):
            return {str(k): self.convert(v) for k, v in value.items()}
        return str(value)

    # -- Reihen ---------------------------------------------------

    def series(self, source: Iterable[Any], options_index: int) -> Any:
        """Baut eine Zeitreihe in dem Layout, das die Vorlage verlangt.

        Das Layout ist eine Entscheidung ueber die Serialisierung, keine
        Schleife. Wer es hier trifft, muss es nicht in jedem Skin von
        Hand nachbauen.
        """
        options = self.consts[options_index]
        layout = options["layout"]
        fields = options["fields"]
        precision = options["precision"]
        gaps = options["gaps"]
        rows = []
        for element in source:
            values = [self.field_of(element, name) for name in fields]
            if gaps == OMIT and any(v is None for v in values):
                continue
            rows.append([self.convert(round_to(v, precision))
                         for v in values])

        if layout == "records":
            return [dict(zip(fields, row)) for row in rows]
        if layout == "pairs":
            return rows
        return {name: [row[index] for row in rows]
                for index, name in enumerate(fields)}

    def field_of(self, element: Any, name: str) -> Any:
        """Holt ein Feld aus einem Element der Reihe.

        Punkt und Schluessel sind dasselbe, wie ueberall in Cheetah.
        """
        current = element
        for part in name.split("."):
            try:
                current = getattr(current, part)
            except AttributeError:
                current = current[part]
        return as_value(current).value
