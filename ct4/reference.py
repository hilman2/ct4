"""Die Sprache, maschinenlesbar.

Direktiven und Einstellungen kommen aus den Tabellen, aus denen der
Parser und der Compiler selbst lesen. Eine abgeschriebene Liste liefe
auseinander, und dann stuende in der Referenz etwas anderes als im Code.

Wozu: wer an einer Vorlage arbeitet, Mensch oder Agent, soll nachsehen
koennen, ohne ins Web zu gehen oder den Quelltext zu durchsuchen.
"""

from __future__ import annotations

from typing import Any


def directives() -> list[dict[str, Any]]:
    """Alle Direktiven, mit der Angabe, ob sie ein ``#end`` brauchen."""
    from Cheetah.Parser import (directiveNamesAndParsers,
                                endDirectiveNamesAndHandlers)

    closeable = set(endDirectiveNamesAndHandlers)
    return [{"name": name, "closeable": name in closeable}
            for name in sorted(directiveNamesAndParsers)]


def settings() -> list[dict[str, Any]]:
    """Alle Compiler-Einstellungen mit Vorgabe und Beschreibung."""
    from Cheetah.Compiler import _DEFAULT_COMPILER_SETTINGS

    return [{"name": name, "default": _describe(default),
             "description": description}
            for name, default, description in _DEFAULT_COMPILER_SETTINGS]


def _describe(value: Any) -> Any:
    """Bringt eine Vorgabe in eine Form, die JSON ablegen kann."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_describe(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _describe(v) for k, v in value.items()}
    return repr(value)


def json_modes() -> list[dict[str, Any]]:
    """Was der JSON-Modus zusaetzlich kennt.

    Steht hier von Hand, weil es dafuer noch keine Tabelle im Code gibt,
    aus der sich das ableiten liesse. Sobald es eine gibt, kommt es von
    dort.
    """
    return [
        {"name": "precision",
         "where": "Kopf",
         "syntax": "#precision NAME = ZAHL",
         "description": "Nachkommastellen fuer ein Ausgabefeld; NAME "
                        "'default' gilt fuer alle uebrigen"},
        {"name": "missing",
         "where": "Kopf",
         "syntax": "#missing omit|null|error",
         "description": "was mit einem Feld ohne Wert geschieht"},
        {"name": "schema",
         "where": "Kopf",
         "syntax": '#schema "pfad.json"',
         "description": "JSON Schema, gegen das geprueft wird"},
        {"name": "series",
         "where": "Wertposition",
         "syntax": "#series(AUSDRUCK, layout=..., fields=[...], "
                   "precision=N, gaps=...)",
         "description": "eine Reihe als records, columns oder pairs"},
        {"name": "@",
         "where": "hinter einem Platzhalter",
         "syntax": "$pfad @ N",
         "description": "Nachkommastellen fuer diesen einen Wert"},
    ]


def reference() -> dict[str, Any]:
    return {"directives": directives(), "settings": settings(),
            "json_mode": json_modes()}
