"""Holding a JSON template against its target format.

Two checks, and the static one is the more valuable. It needs no data
and no application: it reads the template and the schema and says what
does not fit together. A skin author or an agent thus finds the mistake
while writing instead of at the next report run.

What can be decided statically is limited, and that is meant
explicitly here:

* A field that the template produces nowhere is certainly missing.
* A field that stands only inside an ``#if`` may be missing. That is a
  warning, not an error: perhaps it is exactly what was wanted.
* A constant value whose type contradicts the schema is an error.
* A placeholder says nothing about its type statically. That is what
  the runtime check is for.
"""

from __future__ import annotations

from typing import Any

from ct4.diagnostics import ERROR, WARNING, Diagnostic
from ct4.jsonmode.parse import Arr, Expr, For, If, Lit, Member, Obj, Series

# How a constant value of the template maps onto the type names of JSON
# Schema. bool comes before int, because True is an int in Python.
JSON_TYPES: tuple[tuple[type, str], ...] = (
    (bool, "boolean"),
    (str, "string"),
    (int, "integer"),
    (float, "number"),
)


def check(node: Any, schema: Any, path: str = "$") -> list[Diagnostic]:
    """Holds a template node against a schema."""
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
                "CT4210", ERROR,
                "the schema expects %s, the template builds an object" % kind,
                path=path))
            return
        _check_object(node, schema, path, found, certain)
        return

    if isinstance(node, Arr):
        if kind not in (None, "array"):
            found.append(Diagnostic(
                "CT4211", ERROR,
                "the schema expects %s, the template builds an array" % kind,
                path=path))
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
                "CT4212", ERROR,
                "the schema expects %s, the template has %s"
                % (kind, actual), path=path))


def _check_object(node: Obj, schema: dict[str, Any], path: str,
                  found: list[Diagnostic], certain: bool) -> None:
    properties = schema.get("properties", {})
    always, sometimes = _keys_of(node)

    for name in schema.get("required", []):
        if name in always and certain:
            continue
        if name in sometimes or (name in always and not certain):
            found.append(Diagnostic(
                "CT4201", WARNING,
                "the schema requires %r, the template builds it only "
                "under a condition" % name, path="%s.%s" % (path, name)))
        else:
            found.append(Diagnostic(
                "CT4200", ERROR,
                "the schema requires %r, the template does not build it"
                % name, path="%s.%s" % (path, name)))

    if schema.get("additionalProperties") is False:
        for name in sorted(always | sometimes):
            if name not in properties:
                found.append(Diagnostic(
                    "CT4202", ERROR,
                    "the schema does not know %r and allows no further "
                    "fields" % name, path="%s.%s" % (path, name)))

    for member, sure in _members_of(node, certain):
        if not isinstance(member.key, Lit):
            continue
        sub = properties.get(member.key.value)
        if sub is not None:
            _check(member.value, sub,
                   "%s.%s" % (path, member.key.value), found, sure)


def _members_of(node: Obj, certain: bool) -> list[tuple[Member, bool]]:
    """All members together with whether they certainly arise."""
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
    """Constant keys, split into certain and possible."""
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
    """Whether the type fits. ``integer`` also satisfies ``number``."""
    wanted = expected if isinstance(expected, list) else [expected]
    if actual in wanted:
        return True
    return actual == "integer" and "number" in wanted


def validate(value: Any, schema: Any) -> None:
    """Checks a finished result against the schema.

    Needs ``jsonschema``. The library is an additional dependency and
    therefore optional; whoever wants the runtime check installs
    ``ct4[schema]``.
    """
    try:
        import jsonschema  # type: ignore[import-untyped]
    except ImportError:
        raise RuntimeError(
            "The runtime check needs jsonschema. "
            "Install it with: pip install 'ct4[schema]'") from None
    jsonschema.validate(value, schema)


def unknown_expressions(node: Any, path: str = "$") -> list[Diagnostic]:
    """Lists what stays open statically.

    A placeholder and a ``#series`` only settle their type at run time.
    This list says where the static check ends, so that nobody takes it
    for more than it is.
    """
    out: list[Diagnostic] = []
    if isinstance(node, (Expr, Series)):
        out.append(Diagnostic(
            "CT4220", WARNING,
            "the type is only settled at run time", path=path))
    elif isinstance(node, Obj):
        for member, _ in _members_of(node, True):
            key = (member.key.value if isinstance(member.key, Lit) else "?")
            out.extend(unknown_expressions(member.value,
                                           "%s.%s" % (path, key)))
    elif isinstance(node, Arr):
        for index, item in enumerate(node.items):
            out.extend(unknown_expressions(item, "%s[%d]" % (path, index)))
    return out
