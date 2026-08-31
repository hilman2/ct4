"""Was eine Anwendung ueber ihre Namen anmeldet.

Ein Plugin, das nur ausfuehrt, bringt fuer die Werkzeuge nichts. Der Wert
von ``ct4 check`` haengt daran, Dinge zu wissen, ohne sie laufen zu
lassen. Also meldet eine Anwendung an, welche Namen es gibt, und die
Anmeldung wird einmal erhoben und abgelegt.

Danach findet ``ct4 check`` einen Tippfehler in einem weewx-Skin, ohne
dass weewx laeuft, ohne Datenbank und in Millisekunden.

Ein Knoten hat Felder, und ein Feld ``*`` steht fuer einen Namen, den die
Anwendung erst zur Laufzeit kennt. Bei weewx ist das der Messwerttyp:
``$day.outTemp.max`` ist ``day``, dann irgendein Messwert, dann ein
Aggregat aus einer geschlossenen Liste. Genau dort passieren die
Tippfehler, und genau dort lassen sie sich finden.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ANY = "*"


@dataclass
class Node:
    """Ein Name und was unter ihm steht.

    ``open`` heisst: hier darf alles stehen, tiefer wird nicht geprueft.
    Das ist die ehrliche Antwort fuer Objekte, deren Felder die Anwendung
    selbst nicht aufzaehlen kann.
    """

    fields: dict[str, "Node"] = field(default_factory=dict)
    open: bool = False
    kind: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.fields:
            out["fields"] = {name: node.as_dict()
                             for name, node in self.fields.items()}
        if self.open:
            out["open"] = True
        if self.kind:
            out["kind"] = self.kind
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Node":
        return cls(
            fields={name: cls.from_dict(sub)
                    for name, sub in data.get("fields", {}).items()},
            open=bool(data.get("open", False)),
            kind=data.get("kind"),
            note=data.get("note"))


@dataclass
class Declaration:
    """Die Anmeldung einer Anwendung."""

    name: str
    roots: dict[str, Node] = field(default_factory=dict)
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "source": self.source,
                "roots": {name: node.as_dict()
                          for name, node in self.roots.items()}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Declaration":
        return cls(name=data["name"], source=data.get("source", ""),
                   roots={name: Node.from_dict(sub)
                          for name, sub in data.get("roots", {}).items()})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=1,
                       sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")

    @classmethod
    def load(cls, path: Path) -> "Declaration":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Unknown:
    """Ein Pfad, den die Anmeldung nicht kennt."""

    prefix: str
    name: str
    candidates: tuple[str, ...]

    @property
    def suggestions(self) -> tuple[str, ...]:
        return tuple(difflib.get_close_matches(self.name, self.candidates,
                                               n=3, cutoff=0.6))


def resolve(declaration: Declaration, path: str) -> Unknown | None:
    """Prueft einen Platzhalterpfad gegen die Anmeldung.

    Gibt ``None`` zurueck, wenn der Pfad passt oder wenn die Anmeldung
    zu ihm nichts sagt. Das Schweigen ist Absicht: eine Wurzel, die
    niemand angemeldet hat, ist kein Fehler, sondern unbekanntes Gebiet.
    Nur wo eine geschlossene Liste steht, wird widersprochen.
    """
    parts = path.split(".")
    node = declaration.roots.get(parts[0])
    if node is None:
        return None

    seen = [parts[0]]
    for name in parts[1:]:
        if node.open:
            return None
        if name in node.fields:
            node = node.fields[name]
        elif ANY in node.fields:
            node = node.fields[ANY]
        else:
            candidates = tuple(sorted(n for n in node.fields if n != ANY))
            if not candidates:
                return None
            return Unknown(".".join(seen), name, candidates)
        seen.append(name)
    return None
