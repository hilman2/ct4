"""The language, machine readable.

Directives and settings come from the tables the parser and the compiler
read from themselves. A copied list would drift apart, and then the
reference would say something other than the code.

What for: whoever works on a template, human or agent, should be able to
look things up without going to the web or searching the source.
"""

from __future__ import annotations

from typing import Any


def directives() -> list[dict[str, Any]]:
    """All directives, with a note whether they need an ``#end``."""
    from Cheetah.Parser import (directiveNamesAndParsers,
                                endDirectiveNamesAndHandlers)

    closeable = set(endDirectiveNamesAndHandlers)
    return [{"name": name, "closeable": name in closeable}
            for name in sorted(directiveNamesAndParsers)]


def settings() -> list[dict[str, Any]]:
    """All compiler settings with default and description."""
    from Cheetah.Compiler import _DEFAULT_COMPILER_SETTINGS

    return [{"name": name, "default": _describe(default),
             "description": description}
            for name, default, description in _DEFAULT_COMPILER_SETTINGS]


def _describe(value: Any) -> Any:
    """Brings a default into a form that JSON can store."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_describe(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _describe(v) for k, v in value.items()}
    return repr(value)


def json_modes() -> list[dict[str, Any]]:
    """What JSON mode knows on top of that.

    Written by hand here, because there is no table in the code yet to
    derive it from. As soon as there is one, it comes from there.
    """
    return [
        {"name": "precision",
         "where": "header",
         "syntax": "#precision NAME = NUMBER",
         "description": "decimal places for an output field; the NAME "
                        "'default' applies to all the rest"},
        {"name": "missing",
         "where": "header",
         "syntax": "#missing omit|null|error",
         "description": "what happens to a field without a value"},
        {"name": "schema",
         "where": "header",
         "syntax": '#schema "path.json"',
         "description": "JSON Schema to check against"},
        {"name": "series",
         "where": "value position",
         "syntax": "#series(EXPRESSION, layout=..., fields=[...], "
                   "precision=N, gaps=...)",
         "description": "a series as records, columns or pairs"},
        {"name": "@",
         "where": "after a placeholder",
         "syntax": "$path @ N",
         "description": "decimal places for this one value"},
    ]


def reference() -> dict[str, Any]:
    return {"directives": directives(), "settings": settings(),
            "json_mode": json_modes()}
