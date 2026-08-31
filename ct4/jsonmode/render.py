"""Compiling and rendering a JSON template."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ct4.jsonmode.build import Builder
from ct4.jsonmode.emit import METHOD_NAME, emit_with_origins
from ct4.jsonmode.parse import Document, parse

# Fixed separators, so that two runs deliver the same bytes. What
# json.dumps defaults to depends on whether indent is set.
SEPARATORS = (",", ": ")


@dataclass(frozen=True)
class Compiled:
    """A compiled template, ready for any number of contexts."""

    document: Document
    template_class: Any
    names: Sequence[Any]
    consts: Sequence[Any]
    schema: Any = None
    origins: dict[int, int] | None = None
    file: str = "<template>"

    def build(self, search_list: Sequence[Any]) -> Any:
        """Builds the structure without serializing it."""
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
        """Makes sure an error names the line of the template."""
        from ct4 import trace

        return trace.mapped_via(self.origins or {}, self.file)

    def render(self, search_list: Sequence[Any],
               indent: int | None = None, validate: bool = False) -> str:
        """Builds the structure and writes it as JSON."""
        value = self.build(search_list)
        if validate:
            if self.schema is None:
                raise RuntimeError("the template names no #schema")
            from ct4.jsonmode import schema as schema_module

            schema_module.validate(value, self.schema)
        return dumps(value, indent=indent)

    def stream(self, out: Any, search_list: Sequence[Any]) -> None:
        """Writes the structure without holding it in memory.

        Delivers the same bytes as ``render`` without ``indent``. There
        is no indentation here: it would be mere decoration and would
        cost the advantage.
        """
        from ct4.jsonmode.stream import StreamBuilder

        builder = StreamBuilder(out, self.names, self.consts,
                                precisions=self.document.precisions,
                                missing=self.document.missing)
        template = self.template_class(searchList=list(search_list))
        try:
            with self._pointing_at_the_template():
                getattr(template, METHOD_NAME)(builder)
        finally:
            template.shutdown()

    def check(self) -> list[Any]:
        """Holds the template statically against its schema."""
        if self.schema is None:
            return []
        from ct4.jsonmode import schema as schema_module

        return schema_module.check(self.document.root, self.schema)


def dumps(value: Any, indent: int | None = None) -> str:
    """Writes a structure as JSON.

    ``ensure_ascii`` stays off: a station name with an umlaut should
    stand in the file as an umlaut, not as an escape sequence. The keys
    keep the order of the template; sorting them would be a second
    order next to the one the author wrote down.
    """
    return json.dumps(value, ensure_ascii=False, indent=indent,
                      separators=SEPARATORS if indent is None else None)


def compile_template(source: str, base_dir: Path | None = None,
                     file: str = "<template>") -> Compiled:
    """Compiles a JSON template.

    ``base_dir`` says what a ``#schema`` counts its path from. Without
    it the working directory applies.
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
    """Compiles and renders in one go."""
    compiled = compile_template(source, base_dir=base_dir)
    return compiled.render(search_list, indent=indent, validate=validate)
