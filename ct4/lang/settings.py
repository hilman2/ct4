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

    def lexer_tokens(self) -> Any:
        """The token settings as the lexer takes them."""
        from ct4.lang import lex

        if not self.tokens:
            return lex.DEFAULT_TOKENS
        fields = {FIELDS[name]: value for name, value in self.tokens.items()}
        return lex.Tokens(**fields)


# Setting name to the field of lex.Tokens it fills.
FIELDS = {
    "cheetahVarStartToken": "var",
    "directiveStartToken": "directive",
    "directiveEndToken": "directive_end",
    "commentStartToken": "comment",
    "multiLineCommentStartToken": "block_comment",
    "multiLineCommentEndToken": "block_comment_end",
    "PSPStartToken": "psp",
    "PSPEndToken": "psp_end",
    "EOLSlurpToken": "slurp",
}


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


@dataclass(frozen=True)
class Head:
    """A template's own settings and where they stood."""

    first: int
    past: int
    settings: dict[str, Any]


NO_HEAD = Head(0, 0, {})


def head(source: str) -> Head:
    """A template's own settings, read off the head of the file.

    The head is what stands before the first line of output: blank
    lines, comments, and lines of plain text that hold no token at
    all, which is how a bash completion opens with a shell comment and
    sets its var token below it. Where the head holds no settings, the
    result names no lines.

    Raises:
        NotHonoured: for ``reset`` and for a value that is not a
            literal, which ct3 evaluates and this layer will not.
    """
    lines = source.splitlines(keepends=True)
    first = 0
    while first < len(lines) and (modes.is_skippable(lines[first])
                                  or _plain(lines[first])):
        first += 1
    if first >= len(lines):
        return NO_HEAD
    text = lines[first].rstrip("\r\n")
    block = BLOCK_START.match(text)
    if block is not None:
        return _block(lines, first, block.group(1))
    if LINE.match(text):
        return _lines(lines, first)
    return NO_HEAD


def commented(source: str, found: Head, comment: str = "##") -> str:
    """The source with the settings lines turned into comments.

    With the comment token the settings chose, because the lines are
    read again under those tokens; endings kept, so that every line
    keeps its number.
    """
    if found.past <= found.first:
        return source
    lines = source.splitlines(keepends=True)
    for index in range(found.first, found.past):
        line = lines[index]
        ending = line[len(line.rstrip("\r\n")):]
        lines[index] = comment + ending
    return "".join(lines)


def prefix_holds_no_token(source: str, found: Head, tokens: Any) -> bool:
    """Whether the lines before the settings are text under the new tokens.

    They were text under ct3's tokens, or the head would have ended
    there. Under the tokens the settings chose they have to be text
    as well, or a line the lexer read as output would be read again
    as something else.
    """
    from ct4.lang import lex

    lines = source.splitlines(keepends=True)
    prefix = "".join(lines[:found.first])
    if not prefix.strip():
        return True
    syntax = lex.Syntax(lex.directive_names(), tokens=tokens)
    return all(token.kind in (lex.TEXT, lex.COMMENT)
               for token in lex.tokens(prefix, syntax))


def _plain(line: str) -> bool:
    """A line of text with none of ct3's tokens in it."""
    from ct4.lang import lex

    return all(token.kind == lex.TEXT for token in lex.tokens(line))


def _block(lines: list[str], first: int, keyword: str | None) -> Head:
    if keyword is not None:
        raise NotHonoured("#compiler-settings %s" % keyword)
    last = first + 1
    while last < len(lines) \
            and not BLOCK_END.match(lines[last].rstrip("\r\n")):
        last += 1
    if last >= len(lines):
        raise NotHonoured("#compiler-settings without its #end")
    body = "".join(lines[first + 1:last])
    return Head(first, last + 1, _read_block(body))


def _lines(lines: list[str], first: int) -> Head:
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
    return Head(first, last, found)


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
