"""The building site on which a JSON structure takes shape.

The compiled code calls methods here instead of writing characters.
That is why no comma can be missing and none can be one too many:
there are none.

Names and constant values from the template live in ``names`` and
``consts`` and are addressed by their index. Leaving them in the
compiled code would be risky, because Cheetah reads every ``$`` there
as a placeholder, even one in the middle of a key.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from ct4.adapters import Ct4Value, as_value, round_to

OMIT = "omit"
NULL = "null"
ERROR = "error"

# A value that does not belong in the output. Not None: None is a valid
# result and becomes null.
DROP = object()


class MissingValue(ValueError):
    """A field has no value, and the template demands one."""


class Builder:
    """Takes the calls the compiled code makes."""

    def __init__(self, names: Sequence[Any], consts: Sequence[Any],
                 precisions: dict[str, int] | None = None,
                 missing: str = NULL):
        self.names = names
        self.consts = consts
        self.precisions = precisions or {}
        self.missing = missing
        self._stack: list[Any] = []
        self._result: Any = DROP

    # -- Containers -----------------------------------------------

    def open(self, kind: str) -> None:
        """Starts the root container."""
        self._stack.append({} if kind == "obj" else [])

    def open_key(self, name_index: int, kind: str) -> None:
        """Starts a container under a key."""
        container: Any = {} if kind == "obj" else []
        self._stack[-1][self.names[name_index]] = container
        self._stack.append(container)

    def open_key_at(self, name: Any, kind: str) -> None:
        """Like ``open_key``, but the key arises at run time."""
        container: Any = {} if kind == "obj" else []
        self._stack[-1][str(name)] = container
        self._stack.append(container)

    def take_first(self) -> None:
        """Turns the root's helper container into its single value.

        A document may consist of a single value. The building site
        still needs a container, and that container is taken away
        again here.
        """
        self._result = self._result[0]

    def open_item(self, kind: str) -> None:
        """Starts a container as an element of a list."""
        container: Any = {} if kind == "obj" else []
        self._stack[-1].append(container)
        self._stack.append(container)

    def end(self) -> None:
        done = self._stack.pop()
        if not self._stack:
            self._result = done

    @property
    def result(self) -> Any:
        if self._result is DROP:
            raise RuntimeError("the template built nothing")
        return self._result

    # -- Values ---------------------------------------------------

    def key(self, name_index: int, value: Any,
            precision: int | None = None) -> None:
        name = self.names[name_index]
        prepared = self.prepare(value, precision, name)
        if prepared is DROP:
            return
        self._stack[-1][name] = prepared

    def key_at(self, name: Any, value: Any,
               precision: int | None = None) -> None:
        """Like ``key``, but the key only arises at run time."""
        text = str(name)
        prepared = self.prepare(value, precision, text)
        if prepared is DROP:
            return
        self._stack[-1][text] = prepared

    def item(self, value: Any, precision: int | None = None) -> None:
        prepared = self.prepare(value, precision, None)
        if prepared is DROP:
            # In a list nothing can be left out without shifting the
            # positions. A missing element becomes null, even when the
            # policy is omit otherwise.
            prepared = None
        self._stack[-1].append(prepared)

    def series_key(self, name_index: int, source: Iterable[Any],
                   options_index: int) -> None:
        """A series under a key."""
        self._stack[-1][self.names[name_index]] = self.series(
            source, options_index)

    def series_item(self, source: Iterable[Any],
                    options_index: int) -> None:
        self._stack[-1].append(self.series(source, options_index))

    def lit(self, const_index: int) -> Any:
        return self.consts[const_index]

    def cat(self, const_index: int, *values: Any) -> str:
        """Assembles a string out of fixed pieces and values.

        Here the object's ``str()`` applies, not its ``__ct4_value__``.
        The difference is deliberate: in a value position a placeholder
        is a value and turns into a number, inside a string it is text
        and keeps the formatting the object brings along itself. With
        weewx that means: ``$day.outTemp.max`` as a value becomes 12.3,
        inside a string it becomes "12.3 °C".
        """
        chunks = self.consts[const_index]
        out = [chunks[0]]
        for index, value in enumerate(values):
            out.append("" if value is None else str(value))
            out.append(chunks[index + 1])
        return "".join(out)

    # -- Conversion -----------------------------------------------

    def prepare(self, value: Any, precision: int | None,
                name: str | None) -> Any:
        """Turns a value that was read into what stands in the JSON."""
        # What a series is actually made of, taken first. A builtin
        # scalar can carry neither hook and describes no precision of
        # its own, so everything below it reduces to the rounding rule.
        # The general path costs two failing getattr calls, a Ct4Value
        # and four method calls per value, and a series of ten thousand
        # readings cannot afford that. The type is compared exactly:
        # a subclass, numpy.float64 among them, may well define the
        # hooks and has to take the long way.
        kind = type(value)
        if kind is float or kind is int or kind is str or kind is bool:
            if precision is None:
                precisions = self.precisions
                if name is not None and name in precisions:
                    precision = precisions[name]
                else:
                    precision = precisions.get("default")
            if precision is None:
                return value
            return round_to(value, precision)

        hook = getattr(value, "__ct4_json__", None)
        if hook is not None:
            return hook()

        described = as_value(value)
        raw = described.value
        if raw is None:
            return self.on_missing(name)
        if precision is None:
            precision = self.precision_for(name, described)
        return self.convert(round_to(raw, precision))

    def precision_for(self, name: str | None, described: Ct4Value) -> Any:
        """Which rounding wins: template over application, else default."""
        if name is not None and name in self.precisions:
            return self.precisions[name]
        if described.precision is not None:
            return described.precision
        return self.precisions.get("default")

    def on_missing(self, name: str | None) -> Any:
        if self.missing == NULL:
            return None
        if self.missing == OMIT:
            return DROP
        raise MissingValue(
            "%s has no value" % (name or "an element"))

    def convert(self, value: Any) -> Any:
        """Brings a value into a form that json.dumps knows."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [self.convert(item) for item in value]
        if isinstance(value, dict):
            return {str(k): self.convert(v) for k, v in value.items()}
        return str(value)

    # -- Series ---------------------------------------------------

    def series(self, source: Iterable[Any], options_index: int) -> Any:
        """Builds a time series in the layout the template asks for.

        The layout is a decision about serialization, not a loop.
        Making it here means nobody has to rebuild it by hand in every
        skin.
        """
        options = self.consts[options_index]
        layout = options["layout"]
        fields = options["fields"]
        precision = options["precision"]
        gaps = options["gaps"]
        # The paths are split once, not once per element. Ten thousand
        # readings would otherwise split the same two strings twenty
        # thousand times.
        paths = [name.split(".") for name in fields]
        omit_gaps = gaps == OMIT
        convert = self.convert
        field_at = self.field_at
        rows = []
        for element in source:
            values = [field_at(element, path) for path in paths]
            if omit_gaps and any(v is None for v in values):
                continue
            if precision is None:
                # round_to is the identity without a precision, and a
                # series without one is the common case.
                rows.append([convert(v) for v in values])
            else:
                rows.append([convert(round_to(v, precision))
                             for v in values])

        if layout == "records":
            return [dict(zip(fields, row)) for row in rows]
        if layout == "pairs":
            return rows
        return {name: [row[index] for row in rows]
                for index, name in enumerate(fields)}

    def field_of(self, element: Any, name: str) -> Any:
        """Gets a field out of an element of the series.

        Dot and key are the same thing, as everywhere in Cheetah.
        """
        return self.field_at(element, name.split("."))

    def field_at(self, element: Any, path: Sequence[str]) -> Any:
        """Like ``field_of``, with the path already split.

        Called as ``field_at(element, ["outTemp", "raw"])``. A series
        walks the same path for every one of its elements, and
        splitting the name there again each time is the one thing that
        grows with the length of the series without any reason to.
        """
        current = element
        for part in path:
            try:
                current = getattr(current, part)
            except AttributeError:
                current = current[part]
        # Same reasoning as in prepare: a builtin scalar carries no
        # __ct4_value__, so as_value would only wrap and unwrap it.
        kind = type(current)
        if (kind is float or kind is int or kind is str
                or kind is bool or current is None):
            return current
        return as_value(current).value
