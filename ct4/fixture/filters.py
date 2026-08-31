"""Ausgabefilter, die zu aufgezeichneten Kontexten gehoeren.

Ein Fixture haelt fest, was eine Vorlage aus dem Kontext liest. Was
danach mit dem gelesenen Wert passiert, bestimmt der Filter, und den
setzt die Anwendung. weewx setzt ``AssureUnicode``, und dessen Verhalten
ist an zwei Stellen sichtbar: ``None`` wird zur leeren Zeichenkette, und
ein ``AttributeError`` beim Umwandeln wird zum Rohtext des Platzhalters
statt zu einem Abbruch. Ein Skin fuehrt das absichtlich vor, etwa mit
``$day(data_binding='foo_binding')``.

Der Filter steht deshalb hier nachgebildet. Das ist eine Kopie fremden
Verhaltens, und Kopien laufen auseinander: ``tests/unit/test_weewx.py``
vergleicht beide Fassungen Zeichen fuer Zeichen, sobald weewx zur
Verfuegung steht. Sobald es ``ct4-weewx`` als Plugin gibt, gehoert der
Filter dorthin und nicht mehr hierher.
"""

from __future__ import annotations

from typing import Any

from Cheetah.Filters import Filter

NAMES: dict[str, type] = {}


class WeewxAssureUnicode(Filter):
    """Nachbildung von ``weewx.cheetahgenerator.AssureUnicode``."""

    def filter(self, val: Any, **kwargs: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, str):
            return val
        if isinstance(val, bytes):
            return val.decode("utf-8")
        try:
            return str(val)
        except AttributeError as exc:
            return kwargs.get("rawExpr", str(exc) + "?")


NAMES["weewx.AssureUnicode"] = WeewxAssureUnicode


def resolve(name: str) -> type | None:
    """Die Filterklasse zu einem Namen, oder None fuer den Vorgabefilter."""
    if not name:
        return None
    try:
        return NAMES[name]
    except KeyError:
        raise KeyError("unbekannter Filter: %s" % name) from None
