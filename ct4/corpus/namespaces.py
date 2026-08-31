"""Benannte searchLists fuer Korpusfaelle.

Ein Kontext, der Funktionen oder Instanzen enthaelt, laesst sich nicht
als JSON ablegen. Der Fall speichert deshalb nur einen Namen, und der
Erzeuger dahinter baut den Kontext beim Pruefen neu auf.

Die Erzeuger importieren Cheetah erst beim Aufruf. Sonst waere die Wahl
der Implementierung aus ``ct4.impl`` schon entschieden, bevor das
Kommando sie treffen konnte.
"""

from __future__ import annotations

from typing import Any, Callable

from ct4.corpus.case import CT3_DEFAULT, FIXTURE, INLINE, Case

Builder = Callable[[], "list[Any]"]

BUILDERS: dict[str, Builder] = {}


def register(name: str) -> Callable[[Builder], Builder]:
    """Traegt einen Erzeuger unter seinem Namen ein."""

    def decorate(builder: Builder) -> Builder:
        BUILDERS[name] = builder
        return builder

    return decorate


def build(case: Case) -> list[Any]:
    """Baut die searchList, mit der ein Fall gerendert wird."""
    if case.namespace == INLINE:
        return list(case.context)
    if case.namespace == FIXTURE:
        # Eine aufgezeichnete searchList: je Namensraum ein Baum, in der
        # Reihenfolge, in der die Anwendung sie durchsucht hat.
        from ct4.fixture.record import replay

        return [replay(tree) for tree in case.context]
    try:
        builder = BUILDERS[case.namespace]
    except KeyError:
        raise KeyError(
            "Fall %s verlangt den unbekannten Kontext %r"
            % (case.id, case.namespace)) from None
    return builder()


@register(CT3_DEFAULT)
def _ct3_default() -> list[Any]:
    """Der Kontext, mit dem fast jeder ct3-Testfall arbeitet.

    Er wird aus der geladenen Cheetah-Implementierung geholt, nicht
    kopiert. Fork und installiertes ct3 bringen ihn jeweils selbst mit,
    und ein Unterschied darin ist ein Befund, kein Fehler im Pruefstand.
    """
    from Cheetah.Tests.SyntaxAndOutput import defaultTestNameSpace

    return [defaultTestNameSpace]
