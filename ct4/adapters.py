"""Wie fremde Objekte sich ct4 erklaeren.

Der Kern von ct4 lernt nicht, was ein Messwert ist. Er fragt danach. Ein
Objekt, das ``__ct4_value__`` anbietet, sagt damit: hier ist mein nackter
Wert, und so viele Nachkommastellen sind dafuer sinnvoll. Ein Objekt mit
``__ct4_json__`` bestimmt seine Serialisierung ganz selbst.

Wer nichts davon anbietet, wird nach den Regeln von JSON behandelt, und
was dort keinen Platz hat, wird zu seinem ``str()``. Das ist die einzige
Stelle, an der geraten wird, und sie ist bewusst die letzte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# Werte, die JSON unmittelbar kennt.
NATIVE = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class Ct4Value:
    """Ein Wert mitsamt dem, was die Anwendung ueber ihn weiss.

    ``precision`` ist ein Vorschlag, kein Befehl: eine Angabe im Template
    schlaegt ihn. ``label`` gilt nur fuer den Textmodus; im JSON hat eine
    Einheit am Zahlwert nichts zu suchen.
    """

    value: Any
    precision: int | None = None
    label: str | None = None


@runtime_checkable
class SupportsCt4Value(Protocol):
    def __ct4_value__(self) -> Ct4Value: ...


@runtime_checkable
class SupportsCt4Json(Protocol):
    def __ct4_json__(self) -> Any: ...


def as_value(obj: Any) -> Ct4Value:
    """Holt aus einem Objekt seinen Wert und was daran bekannt ist."""
    if isinstance(obj, Ct4Value):
        return obj
    hook = getattr(obj, "__ct4_value__", None)
    if hook is not None:
        result = hook()
        if not isinstance(result, Ct4Value):
            raise TypeError(
                "%s.__ct4_value__ lieferte %s statt Ct4Value"
                % (type(obj).__name__, type(result).__name__))
        return result
    return Ct4Value(obj)


def round_to(value: Any, precision: int | None) -> Any:
    """Rundet einen Zahlwert, laesst alles andere in Ruhe.

    Gerundet wird auf dem Zahlwert, nicht ueber einen Formatstring. Ein
    Formatstring macht aus einer Zahl eine Zeichenkette, und die steht im
    JSON dann in Anfuehrungszeichen. Das ist der haeufigste Fehler in
    Skins, die JSON von Hand bauen.
    """
    if precision is None or value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        rounded = round(value, precision)
        # round() auf einem int gibt ein int zurueck; das bleibt so.
        return rounded
    return value
