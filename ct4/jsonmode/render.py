"""Eine JSON-Vorlage uebersetzen und rendern."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ct4.jsonmode.build import Builder
from ct4.jsonmode.emit import METHOD_NAME, emit_with_origins
from ct4.jsonmode.parse import Document, parse

# Feste Trennzeichen, damit zwei Laeufe dieselben Bytes liefern. Die
# Vorgabe von json.dumps haengt daran, ob indent gesetzt ist.
SEPARATORS = (",", ": ")


@dataclass(frozen=True)
class Compiled:
    """Eine uebersetzte Vorlage, bereit fuer beliebig viele Kontexte."""

    document: Document
    template_class: Any
    names: Sequence[Any]
    consts: Sequence[Any]
    schema: Any = None
    origins: dict[int, int] | None = None
    file: str = "<vorlage>"

    def build(self, search_list: Sequence[Any]) -> Any:
        """Baut die Struktur, ohne sie zu serialisieren."""
        builder = Builder(self.names, self.consts,
                          precisions=self.document.precisions,
                          missing=self.document.missing)
        template = self.template_class(searchList=list(search_list))
        try:
            with self._pointing_at_the_template():
                return getattr(template, METHOD_NAME)(builder)
        finally:
            template.shutdown()

    def _pointing_at_the_template(self) -> Any:
        """Sorgt dafuer, dass ein Fehler die Zeile der Vorlage nennt."""
        from ct4 import trace

        return trace.mapped_via(self.origins or {}, self.file)

    def render(self, search_list: Sequence[Any],
               indent: int | None = None, validate: bool = False) -> str:
        """Baut die Struktur und schreibt sie als JSON."""
        value = self.build(search_list)
        if validate:
            if self.schema is None:
                raise RuntimeError("die Vorlage nennt kein #schema")
            from ct4.jsonmode import schema as schema_module

            schema_module.validate(value, self.schema)
        return dumps(value, indent=indent)

    def check(self) -> list[Any]:
        """Haelt die Vorlage statisch gegen ihr Schema."""
        if self.schema is None:
            return []
        from ct4.jsonmode import schema as schema_module

        return schema_module.check(self.document.root, self.schema)


def dumps(value: Any, indent: int | None = None) -> str:
    """Schreibt eine Struktur als JSON.

    ``ensure_ascii`` bleibt aus: ein Stationsname mit Umlaut soll als
    Umlaut in der Datei stehen, nicht als Fluchtfolge. Die Schluessel
    behalten die Reihenfolge der Vorlage; sie zu sortieren waere eine
    zweite Ordnung neben der, die der Autor hingeschrieben hat.
    """
    return json.dumps(value, ensure_ascii=False, indent=indent,
                      separators=SEPARATORS if indent is None else None)


def compile_template(source: str, base_dir: Path | None = None,
                     file: str = "<vorlage>") -> Compiled:
    """Uebersetzt eine JSON-Vorlage.

    ``base_dir`` sagt, wovon ein ``#schema`` seinen Pfad aus zaehlt.
    Ohne Angabe gilt das Arbeitsverzeichnis.
    """
    from Cheetah.Template import Template

    document = parse(source)
    code, names, consts, origins = emit_with_origins(document)
    schema = None
    if document.schema is not None:
        path = Path(document.schema)
        if base_dir is not None and not path.is_absolute():
            path = base_dir / path
        schema = json.loads(path.read_text(encoding="utf-8"))
    return Compiled(document, Template.compile(source=code), names, consts,
                    schema, origins, file)


def render(source: str, search_list: Sequence[Any],
           indent: int | None = None, base_dir: Path | None = None,
           validate: bool = False) -> str:
    """Uebersetzt und rendert in einem Zug."""
    compiled = compile_template(source, base_dir=base_dir)
    return compiled.render(search_list, indent=indent, validate=validate)
