"""How foreign objects explain themselves to ct4.

The core of ct4 never learns what a measurement is. It asks. An object
that offers ``__ct4_value__`` says: here is my bare value, and this
many decimal places make sense for it. An object with ``__ct4_json__``
decides its serialization entirely on its own.

Whatever offers neither is treated by the rules of JSON, and what has
no place there becomes its ``str()``. That is the only point where
anything is guessed, and it is deliberately the last one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# Values that JSON knows directly.
NATIVE = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class Ct4Value:
    """A value together with what the application knows about it.

    ``precision`` is a suggestion, not an order: a setting in the
    template overrides it. ``label`` applies to text mode only; in JSON
    a unit has no business sitting on a number.
    """

    value: Any
    precision: int | None = None
    label: str | None = None


@runtime_checkable
class SupportsCt4Value(Protocol):
    def __ct4_value__(self) -> Ct4Value: ...


@runtime_checkable
class SupportsCt4Json(Protocol):
    def __ct4_json__(self) -> Any: ...


def as_value(obj: Any) -> Ct4Value:
    """Gets an object's value and what is known about it."""
    if isinstance(obj, Ct4Value):
        return obj
    hook = getattr(obj, "__ct4_value__", None)
    if hook is not None:
        result = hook()
        if not isinstance(result, Ct4Value):
            raise TypeError(
                "%s.__ct4_value__ returned %s instead of Ct4Value"
                % (type(obj).__name__, type(result).__name__))
        return result
    return Ct4Value(obj)


def round_to(value: Any, precision: int | None) -> Any:
    """Rounds a numeric value, leaves everything else alone.

    Rounding happens on the number itself, not through a format string.
    A format string turns a number into a string, and that string then
    sits in the JSON in quotes. This is the most common mistake in
    skins that build JSON by hand.
    """
    if precision is None or value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        rounded = round(value, precision)
        # round() on an int returns an int; that stays that way.
        return rounded
    return value
