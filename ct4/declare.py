"""What an application declares about its names.

A plugin that only runs things brings nothing to the tools. The worth of
``ct4 check`` hangs on knowing things without letting them run. So an
application declares which names exist, and the declaration is gathered
once and stored.

After that ``ct4 check`` finds a typo in a weewx skin without weewx
running, without a database and in milliseconds.

A node has fields, and a field ``*`` stands for a name the application
only knows at run time. In weewx that is the observation type:
``$day.outTemp.max`` is ``day``, then some observation, then an
aggregate out of a closed list. That is exactly where the typos happen,
and exactly where they can be found.
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
    """A name and what stands below it.

    ``open`` means: anything may stand here, deeper down nothing is
    checked. That is the honest answer for objects whose fields the
    application cannot enumerate itself.
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
    """The declaration of an application."""

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
    """A path the declaration does not know."""

    prefix: str
    name: str
    candidates: tuple[str, ...]

    @property
    def suggestions(self) -> tuple[str, ...]:
        return tuple(difflib.get_close_matches(self.name, self.candidates,
                                               n=3, cutoff=0.6))


def resolve(declaration: Declaration, path: str) -> Unknown | None:
    """Checks a placeholder path against the declaration.

    Returns ``None`` when the path fits or when the declaration says
    nothing about it. The silence is deliberate: a root that nobody has
    declared is not an error but unknown ground. Only where a closed
    list stands is there any objection.
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
