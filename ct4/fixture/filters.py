"""Output filters that belong to recorded contexts.

A fixture holds what a template reads from the context. What happens to
the value after that is up to the filter, and the application sets the
filter. weewx sets ``AssureUnicode``, whose behaviour shows at two
points: ``None`` becomes the empty string, and an ``AttributeError``
during conversion becomes the raw text of the placeholder instead of an
abort. A skin demonstrates this on purpose, for example with
``$day(data_binding='foo_binding')``.

The filter is therefore reproduced here. This is a copy of foreign
behaviour, and copies drift apart: ``tests/unit/test_weewx.py`` compares
both versions character by character as soon as weewx is available. Once
``ct4-weewx`` exists as a plugin, the filter belongs there and no longer
here.
"""

from __future__ import annotations

from typing import Any

from Cheetah.Filters import Filter

NAMES: dict[str, type] = {}


class WeewxAssureUnicode(Filter):  # type: ignore[misc]
    """Reproduction of ``weewx.cheetahgenerator.AssureUnicode``."""

    def filter(self, val: Any, **kwargs: Any) -> str:
        if val is None:
            return ""
        if isinstance(val, str):
            return val
        if isinstance(val, bytes):
            return val.decode("utf-8")
        try:
            return str(val)
        except AttributeError as exc:
            return str(kwargs.get("rawExpr", str(exc) + "?"))


NAMES["weewx.AssureUnicode"] = WeewxAssureUnicode


def resolve(name: str) -> type | None:
    """The filter class for a name, or None for the default filter."""
    if not name:
        return None
    try:
        return NAMES[name]
    except KeyError:
        raise KeyError("unknown filter: %s" % name) from None
