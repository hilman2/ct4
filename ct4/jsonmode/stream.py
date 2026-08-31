"""A building site that writes instead of collecting.

The ordinary building site assembles the whole structure in memory and
hands it to ``json.dumps`` afterwards. For a time series over a year in
five-minute steps that is a hundred thousand points, and they are there
twice: as a structure and as a string.

Here they are written out at once instead. That works because the
compiled code drives the building site in exactly the order in which
JSON expects the parts. Commas arise from whether a container already
holds something, and therefore can neither be missing nor be one too
many.

Byte for byte the same as the other way. Every single value goes
through ``json.dumps``, so that escaping, number format and the
handling of ``null`` cannot differ. A test compares both ways across
all cases.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence, TextIO

from ct4.adapters import round_to
from ct4.jsonmode.build import DROP, NULL, OMIT, Builder
from ct4.jsonmode.render import SEPARATORS

# From the same source as the collecting way. Written down twice they
# would be wrong once: without being told, json.dumps uses ', ' instead
# of ',' and the comparison of both ways would fail over one space.
COMMA, COLON = SEPARATORS


def encode(value: Any) -> str:
    """A single value, just as json.dumps would write it."""
    return json.dumps(value, ensure_ascii=False, separators=SEPARATORS)


class StreamBuilder(Builder):
    """Writes the structure while it takes shape."""

    def __init__(self, out: TextIO, names: Sequence[Any],
                 consts: Sequence[Any],
                 precisions: dict[str, int] | None = None,
                 missing: str = NULL):
        super().__init__(names, consts, precisions, missing)
        self.out = out
        # Per open container: does it already hold something? The
        # commas arise from that.
        self._filled: list[bool] = []
        # Which bracket has to be closed. ``end()`` never learns the
        # kind of the container, so it is remembered when opening.
        self._closers: list[str] = []
        self._done = False

    # -- Containers -----------------------------------------------

    def _before_value(self) -> None:
        if self._filled and self._filled[-1]:
            self.out.write(COMMA)
        if self._filled:
            self._filled[-1] = True

    def _push(self, kind: str) -> None:
        self.out.write("{" if kind == "obj" else "[")
        self._closers.append("}" if kind == "obj" else "]")
        self._filled.append(False)

    def open(self, kind: str) -> None:
        self._before_value()
        self._push(kind)

    def open_key(self, name_index: int, kind: str) -> None:
        self.open_key_at(self.names[name_index], kind)

    def open_key_at(self, name: Any, kind: str) -> None:
        self._before_value()
        self.out.write(encode(str(name)) + COLON)
        self._push(kind)

    def open_item(self, kind: str) -> None:
        self._before_value()
        self._push(kind)

    def end(self) -> None:
        self._filled.pop()
        self.out.write(self._closers.pop())
        if not self._filled:
            self._done = True

    # -- Values ---------------------------------------------------

    def key(self, name_index: int, value: Any,
            precision: int | None = None) -> None:
        self.key_at(self.names[name_index], value, precision)

    def key_at(self, name: Any, value: Any,
               precision: int | None = None) -> None:
        text = str(name)
        prepared = self.prepare(value, precision, text)
        if prepared is DROP:
            return
        self._before_value()
        self.out.write(encode(text) + COLON + encode(prepared))

    def item(self, value: Any, precision: int | None = None) -> None:
        prepared = self.prepare(value, precision, None)
        if prepared is DROP:
            prepared = None
        self._before_value()
        self.out.write(encode(prepared))

    def take_first(self) -> None:
        raise NotImplementedError(
            "a document made of a single value is not streamed; "
            "render() is there for that")

    @property
    def result(self) -> Any:
        if not self._done:
            raise RuntimeError("the template built nothing")
        return None

    # -- Series ---------------------------------------------------

    def series_key(self, name_index: int, source: Iterable[Any],
                   options_index: int) -> None:
        self._before_value()
        self.out.write(encode(str(self.names[name_index])) + COLON)
        self._write_series(source, options_index)

    def series_item(self, source: Iterable[Any],
                    options_index: int) -> None:
        self._before_value()
        self._write_series(source, options_index)

    def _write_series(self, source: Iterable[Any],
                      options_index: int) -> None:
        """Writes a series without holding all of it in memory.

        ``columns`` breaks that: there the first value of every column
        stands next to the last, and for that the series has to be
        collected. Only this one form is collected, and it is the only
        one that cannot be streamed.
        """
        options = self.consts[options_index]
        if options["layout"] == "columns":
            self.out.write(encode(self.series(source, options_index)))
            return

        fields = options["fields"]
        precision = options["precision"]
        gaps = options["gaps"]
        records = options["layout"] == "records"

        self.out.write("[")
        first = True
        for element in source:
            values = [self.field_of(element, name) for name in fields]
            if gaps == OMIT and any(value is None for value in values):
                continue
            values = [self.convert(round_to(value, precision))
                      for value in values]
            if not first:
                self.out.write(COMMA)
            first = False
            self.out.write(encode(dict(zip(fields, values)) if records
                                  else values))
        self.out.write("]")
