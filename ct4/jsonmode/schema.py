"""Eine JSON-Vorlage gegen ihr Zielformat halten.

Zwei Pruefungen, und die statische ist die wertvollere. Sie braucht keine
Daten und keine Anwendung: sie liest die Vorlage und das Schema und
sagt, was nicht zusammenpasst. Damit findet ein Skin-Autor oder ein Agent
den Fehler beim Schreiben statt beim naechsten Report-Lauf.

Was statisch entschieden werden kann, ist begrenzt, und das ist hier
ausdruecklich so gemeint:

* Ein Feld, das die Vorlage nirgends erzeugt, fehlt sicher.
* Ein Feld, das nur in einem ``#if`` steht, kann fehlen. Das ist eine
  Warnung, keine Meldung: vielleicht ist genau das gewollt.
* Ein fester Wert, dessen Typ dem Schema widerspricht, ist ein Fehler.
* Ein Platzhalter sagt statisch nichts ueber seinen Typ. Dafuer gibt es
  die Laufzeitpruefung.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ct4.jsonmode.parse import Arr, Expr, For, If, Lit, Member, Obj, Series

ERROR = "error"
WARNING = "warning"

# Wie ein fester Wert der Vorlage auf die Typnamen von JSON Schema
# abgebildet wird. bool steht vor int, weil True in Python ein int ist.
JSON_TYPES: tuple[tuple[type, str], ...] = (
    (bool, "boolean"),
    (str, "string"),
    (int, "integer"),
    (float, "number"),
)


@dataclass(frozen=True)
class Diagnostic:
    """Ein Befund mit Pfad, damit man ihn wiederfindet."""

    severity: str
    code: str
    path: str
    message: str

    def __str__(self) -> str:
        return "%s %s %s: %s" % (self.code, self.severity.upper(),
                                 self.path, self.message)


def check(node: Any, schema: Any, path: str = "$") -> list[Diagnostic]:
    """Haelt einen Vorlagenknoten gegen ein Schema."""
    found: list[Diagnostic] = []
    _check(node, schema, path, found, certain=True)
    return found


def _check(node: Any, schema: Any, path: str,
           found: list[Diagnostic], certain: bool) -> None:
    if not isinstance(schema, dict):
        return
    kind = schema.get("type")

    if isinstance(node, Obj):
        if kind not in (None, "object"):
            found.append(Diagnostic(
                ERROR, "CT4210", path,
                "das Schema erwartet %s, die Vorlage baut ein Objekt" % kind))
            return
        _check_object(node, schema, path, found, certain)
        return

    if isinstance(node, Arr):
        if kind not in (None, "array"):
            found.append(Diagnostic(
                ERROR, "CT4211", path,
                "das Schema erwartet %s, die Vorlage baut eine Liste" % kind))
            return
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(node.items):
                _check(item, items, "%s[%d]" % (path, index), found, certain)
        return

    if isinstance(node, Lit) and kind is not None:
        actual = _json_type(node.value)
        if not _type_fits(actual, kind):
            found.append(Diagnostic(
                ERROR, "CT4212", path,
                "das Schema erwartet %s, im Template steht %s"
                % (kind, actual)))


def _check_object(node: Obj, schema: dict[str, Any], path: str,
                  found: list[Diagnostic], certain: bool) -> None:
    properties = schema.get("properties", {})
    always, sometimes = _keys_of(node)

    for name in schema.get("required", []):
        if name in always and certain:
            continue
        if name in sometimes or (name in always and not certain):
            found.append(Diagnostic(
                WARNING, "CT4201", "%s.%s" % (path, name),
                "das Schema verlangt das Feld, die Vorlage baut es nur "
                "unter einer Bedingung"))
        else:
            found.append(Diagnostic(
                ERROR, "CT4200", "%s.%s" % (path, name),
                "das Schema verlangt das Feld, die Vorlage baut es nicht"))

    if schema.get("additionalProperties") is False:
        for name in sorted(always | sometimes):
            if name not in properties:
                found.append(Diagnostic(
                    ERROR, "CT4202", "%s.%s" % (path, name),
                    "das Schema kennt das Feld nicht und laesst keine "
                    "weiteren zu"))

    for member, sure in _members_of(node, certain):
        if not isinstance(member.key, Lit):
            continue
        sub = properties.get(member.key.value)
        if sub is not None:
            _check(member.value, sub,
                   "%s.%s" % (path, member.key.value), found, sure)


def _members_of(node: Obj, certain: bool) -> list[tuple[Member, bool]]:
    """Alle Mitglieder samt der Frage, ob sie sicher entstehen."""
    out: list[tuple[Member, bool]] = []
    _walk(node.members, certain, out)
    return out


def _walk(nodes: list[Any], certain: bool,
          out: list[tuple[Member, bool]]) -> None:
    for entry in nodes:
        if isinstance(entry, Member):
            out.append((entry, certain))
        elif isinstance(entry, If):
            for _, body in entry.branches:
                _walk(body, False, out)
            if entry.otherwise is not None:
                _walk(entry.otherwise, False, out)
        elif isinstance(entry, For):
            _walk(entry.body, False, out)


def _keys_of(node: Obj) -> tuple[set[str], set[str]]:
    """Feste Schluessel, getrennt nach sicher und moeglich."""
    always: set[str] = set()
    sometimes: set[str] = set()
    for member, certain in _members_of(node, True):
        if not isinstance(member.key, Lit):
            continue
        (always if certain else sometimes).add(member.key.value)
    return always, sometimes - always


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    for kind, name in JSON_TYPES:
        if isinstance(value, kind):
            return name
    return "unknown"


def _type_fits(actual: str, expected: Any) -> bool:
    """Ob der Typ passt. ``integer`` erfuellt auch ``number``."""
    wanted = expected if isinstance(expected, list) else [expected]
    if actual in wanted:
        return True
    return actual == "integer" and "number" in wanted


def validate(value: Any, schema: Any) -> None:
    """Prueft ein fertiges Ergebnis gegen das Schema.

    Braucht ``jsonschema``. Die Bibliothek ist eine zusaetzliche
    Abhaengigkeit und deshalb wahlfrei; wer die Laufzeitpruefung will,
    installiert ``ct4[schema]``.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            "Die Laufzeitpruefung braucht jsonschema. "
            "Zu installieren mit: pip install 'ct4[schema]'") from None
    jsonschema.validate(value, schema)


def unknown_expressions(node: Any, path: str = "$") -> list[Diagnostic]:
    """Zaehlt auf, was statisch offen bleibt.

    Ein Platzhalter und eine ``#series`` liefern erst zur Laufzeit einen
    Typ. Diese Liste sagt, wo die statische Pruefung aufhoert, damit
    niemand sie fuer mehr haelt, als sie ist.
    """
    out: list[Diagnostic] = []
    if isinstance(node, (Expr, Series)):
        out.append(Diagnostic(
            WARNING, "CT4220", path,
            "der Typ steht erst zur Laufzeit fest"))
    elif isinstance(node, Obj):
        for member, _ in _members_of(node, True):
            key = (member.key.value if isinstance(member.key, Lit) else "?")
            out.extend(unknown_expressions(member.value,
                                           "%s.%s" % (path, key)))
    elif isinstance(node, Arr):
        for index, item in enumerate(node.items):
            out.extend(unknown_expressions(item, "%s[%d]" % (path, index)))
    return out
