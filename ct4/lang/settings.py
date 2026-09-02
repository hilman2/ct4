"""Which compiler settings the generator honours, and a template's own.

ct3 has some forty compiler settings, and a template may set them in
two directives: a ``#compiler-settings`` block or a ``#compiler name =
value`` line. Most change how ct3 parses, and this layer reads none of
those; a template that sets one falls back to ct3, which is right.

What is honoured is small and named. A setting at its default value
changes nothing and is let through. ``useAutocalling`` and
``useNameMapper`` change what a placeholder becomes, and the reader
has a switch for each. The token settings, ``cheetahVarStartToken``
and its kin, change what the lexer looks for, and the lexer takes
them through its Syntax. Everything else is refused by name.

A template's own settings are read only at the head of the file: the
first lines that are neither blank nor comments. ct3 applies a
setting from the point where the directive stands, and a switch in
the middle of a file would mean a lexer that changes its mind; the
head is where every real template puts them. The lines are replaced
by ``##`` comments of the same count, so that every line keeps its
number and a comment on a line of its own writes nothing, the way the
directive wrote nothing.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any

from ct4 import modes

TOKENS = ("cheetahVarStartToken", "commentStartToken",
          "multiLineCommentStartToken", "multiLineCommentEndToken",
          "directiveStartToken", "directiveEndToken", "PSPStartToken",
          "PSPEndToken", "EOLSlurpToken")

BLOCK_START = re.compile(r"^#compiler-settings(?:\s+(\w+))?\s*$")
BLOCK_END = re.compile(r"^#end\s+compiler-settings\s*$")
LINE = re.compile(r"^#compiler\s+(\w+)\s*(?:=\s*(.*?))?\s*$")


class NotHonoured(Exception):
    """A setting this layer does not read; the caller falls back."""


@dataclass(frozen=True)
class Honoured:
    """What the generator does differently because of the settings."""

    autocalling: bool = True
    name_mapper: bool = True
    tokens: dict[str, str] = field(default_factory=dict)


DEFAULT = Honoured()


def defaults() -> dict[str, Any]:
    from Cheetah.Compiler import _DEFAULT_COMPILER_SETTINGS

    return {name: value for name, value, _ in _DEFAULT_COMPILER_SETTINGS}


def honour(settings: Any) -> Honoured:
    """The settings as the generator will apply them.

    Raises:
        NotHonoured: for a setting the layer does not read, or one it
            does not know, naming it.
    """
    if not settings:
        return DEFAULT
    known = defaults()
    autocalling = True
    name_mapper = True
    tokens: dict[str, str] = {}
    refused = []
    for name, value in sorted(dict(settings).items()):
        if name not in known:
            raise NotHonoured("unknown compiler setting %r" % name)
        if value == known[name]:
            continue
        if name == "useAutocalling":
            autocalling = bool(value)
        elif name == "useNameMapper":
            name_mapper = bool(value)
        elif name in TOKENS:
            if not isinstance(value, str) or not value:
                raise NotHonoured("%s must be a non-empty string" % name)
            tokens[name] = value
        else:
            refused.append(name)
    if refused:
        raise NotHonoured(
            "compiler settings change how ct3 parses and this layer "
            "reads none of these: %s" % ", ".join(refused))
    return Honoured(autocalling, name_mapper, tokens)


def head(source: str) -> tuple[str, dict[str, Any]]:
    """A template's own settings, taken off the head of the file.

    Returns:
        tuple[str, dict[str, Any]]: The source with the settings lines
            turned into comments, and the settings. The source comes
            back unchanged, with an empty dict, where the head holds
            none.

    Raises:
        NotHonoured: for ``reset`` and for a value that is not a
            literal, which ct3 evaluates and this layer will not.
    """
    lines = source.splitlines(keepends=True)
    first = 0
    while first < len(lines) and modes.is_skippable(lines[first]):
        first += 1
    if first >= len(lines):
        return source, {}
    text = lines[first].rstrip("\r\n")
    block = BLOCK_START.match(text)
    if block is not None:
        return _block(lines, first, block.group(1))
    if LINE.match(text):
        return _lines(lines, first)
    return source, {}


def _block(lines: list[str], first: int,
           keyword: str | None) -> tuple[str, dict[str, Any]]:
    if keyword is not None:
        raise NotHonoured("#compiler-settings %s" % keyword)
    last = first + 1
    while last < len(lines) \
            and not BLOCK_END.match(lines[last].rstrip("\r\n")):
        last += 1
    if last >= len(lines):
        raise NotHonoured("#compiler-settings without its #end")
    body = "".join(lines[first + 1:last])
    return _commented(lines, first, last + 1), _read_block(body)


def _lines(lines: list[str], first: int) -> tuple[str, dict[str, Any]]:
    found: dict[str, Any] = {}
    last = first
    while last < len(lines):
        match = LINE.match(lines[last].rstrip("\r\n"))
        if match is None:
            break
        name, value = match.group(1), match.group(2)
        if name.lower() == "reset" or value is None:
            raise NotHonoured("#compiler %s" % name)
        try:
            found[name] = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            raise NotHonoured("#compiler %s = %s is not a literal"
                              % (name, value)) from None
        last += 1
    return _commented(lines, first, last), found


def _commented(lines: list[str], first: int, past: int) -> str:
    """The lines from ``first`` to ``past`` as comments, endings kept."""
    made = list(lines)
    for index in range(first, past):
        line = made[index]
        ending = line[len(line.rstrip("\r\n")):]
        made[index] = "##" + ending
    return "".join(made)


def _read_block(body: str) -> dict[str, Any]:
    """The settings of a block, read the way ct3 reads them.

    Through ct3's own SettingsManager, so that "no", "False" and "0"
    mean here what they mean there.
    """
    from Cheetah.SettingsManager import SettingsManager

    manager = SettingsManager()
    manager.updateSettingsFromConfigStr(body)
    found: dict[str, Any] = dict(manager.settings())
    return found
