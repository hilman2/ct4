"""Rewrite a text-mode template for ``#mode strict`` and say what moved.

Strict mode calls nothing the author did not call. A text-mode
template relies on the opposite: ``$station.location`` calls the
method behind the last name and prints what it returned, and nothing
in the source says so. Only a run shows where that happened, so this
works from a recording, the kind ``ct4 fixture capture`` writes: what
the page read, and whether a callable was called with no arguments at
a place where the source wrote none. There the parentheses go in.

Then the rewritten template is rendered in strict mode against the
same recording and compared with the text-mode page, byte for byte.
A difference is reported as a diff and the exit code says so; a
template that renders the same is a migration with nothing lost.

    ct4 migrate index.html.tmpl --context fixtures/index.json
    ct4 migrate index.html.tmpl --context fixtures/index.json --write

Left alone, and named in the report: a placeholder in an enclosure
(``${x}``, ``$(x)``), one with a modifier, and a chain the recording
cannot follow, such as a call with arguments the source computes.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from ct4 import modes
from ct4.fixture.record import ATTRS, CALLS
from ct4.lang import lex

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
OPENERS = {"(": ")", "[": "]", "{": "}"}


@dataclass(frozen=True)
class Change:
    """One pair of parentheses put in."""

    line: int
    column: int
    before: str
    after: str


@dataclass(frozen=True)
class Skipped:
    """A placeholder the rewrite could not decide about."""

    line: int
    column: int
    text: str
    reason: str


@dataclass
class Result:
    source: str
    changes: list[Change] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    # None where nothing was verified, otherwise the unified diff
    # between the text-mode page and the strict-mode page; empty means
    # they are the same.
    diff: list[str] | None = None

    @property
    def same(self) -> bool | None:
        return None if self.diff is None else not self.diff


class MigrationError(Exception):
    """A template this cannot migrate, with the reason."""


def migrate(source: str, recording: Any = None) -> Result:
    """Rewrites the template and, given a recording, verifies it.

    Args:
        source (str): The text-mode template.
        recording (Any): The parsed JSON of a recording: the document
            ``ct4 fixture capture`` writes, or its ``context`` list of
            recorded namespaces. None rewrites nothing but the mode
            line, because without a run nothing says where a call
            happened.

    Returns:
        Result: The rewritten source, what changed, what was left, and
            the verification where a recording was given.

    Raises:
        MigrationError: for a JSON template, which has no strict mode.
    """
    declared = modes.declared(source)
    if modes.JSON in declared:
        raise MigrationError("a JSON template has no strict mode")
    trees = _trees_of(recording)
    made = source
    result = Result(source)
    if trees is not None:
        made, result.changes, result.skipped = _rewrite(source, trees)
    if modes.STRICT not in declared:
        made = _declare_strict(made, declared)
    result.source = made
    if trees is not None:
        result.diff = _verify(source, made, trees)
    return result


def _trees_of(recording: Any) -> list[dict[str, Any]] | None:
    if recording is None:
        return None
    if isinstance(recording, dict) and isinstance(recording.get("context"),
                                                  list):
        return list(recording["context"])
    if isinstance(recording, list):
        return list(recording)
    raise MigrationError("the recording is neither a capture document"
                         " nor a list of recorded namespaces")


def _declare_strict(source: str, declared: frozenset[str]) -> str:
    """The source with strict added to its mode line, or given one."""
    lines = source.splitlines(keepends=True)
    if declared:
        for index, line in enumerate(lines):
            if modes.is_skippable(line):
                continue
            ending = line[len(line.rstrip("\r\n")):]
            lines[index] = line.rstrip("\r\n") + " strict" + ending
            return "".join(lines)
    ending = "\r\n" if "\r\n" in source else "\n"
    for index, line in enumerate(lines):
        if modes.is_skippable(line):
            continue
        lines.insert(index, "#mode strict" + ending)
        return "".join(lines)
    return source + "#mode strict" + ending


# -- The rewrite --------------------------------------------------------

def _rewrite(source: str, trees: list[dict[str, Any]]
             ) -> tuple[str, list[Change], list[Skipped]]:
    insertions: list[tuple[int, int, int, str]] = []
    skipped: list[Skipped] = []
    for token in lex.walk(lex.tokens(source)):
        if token.kind != lex.PLACEHOLDER:
            continue
        reason = _unreadable(token.text)
        if reason is not None:
            skipped.append(Skipped(token.line, token.column, token.text,
                                   reason))
            continue
        segments = _segments(token.text)
        for offset in _autocalled(segments, trees):
            insertions.append((token.start + offset, token.line,
                               token.column, token.text))
    made = source
    for at, _, _, _ in sorted(insertions, reverse=True):
        made = made[:at] + "()" + made[at:]
    changes = [Change(line, column, text,
                      _with_parens(text, at, source, line, column))
               for at, line, column, text in sorted(insertions)]
    return made, changes, skipped


def _with_parens(text: str, at: int, source: str, line: int,
                 column: int) -> str:
    """The placeholder's text with this one insertion applied."""
    start = _start_of(source, line, column)
    inside = at - start
    return text[:inside] + "()" + text[inside:]


def _start_of(source: str, line: int, column: int) -> int:
    starts = lex.line_starts(source)
    return starts[line - 1] + column - 1


def _unreadable(text: str) -> str | None:
    """Why a placeholder is left alone, or None where it is read."""
    if len(text) < 2 or text[0] != "$":
        return "not a placeholder"
    if text[1] in "{([":
        return "an enclosure"
    if text[1] in "!*":
        return "a modifier"
    return None


def _segments(text: str) -> list[tuple[str, int, str]]:
    """The names of a chain and what follows each.

    Returns:
        list[tuple[str, int, str]]: For each name its text, the offset
            just past it inside ``text``, and the bracket that follows
            it: "(" for a call, "[" for a subscript, "" for a dot or
            the end.
    """
    found: list[tuple[str, int, str]] = []
    at = 1
    while at < len(text):
        match = NAME.match(text, at)
        if match is None:
            break
        end = match.end()
        following = text[end:end + 1]
        found.append((match.group(0), end,
                      following if following in "([" else ""))
        at = end
        # Skip every bracket group that hangs off the name.
        while at < len(text) and text[at] in OPENERS:
            at = _past_group(text, at)
        if at < len(text) and text[at] == ".":
            at += 1
            continue
        break
    return found


def _past_group(text: str, at: int) -> int:
    closing = OPENERS[text[at]]
    depth = 0
    while at < len(text):
        char = text[at]
        if char in "\"'":
            at = _past_string(text, at)
            continue
        if char in OPENERS:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return at + 1
        at += 1
    return at


def _past_string(text: str, at: int) -> int:
    quote = text[at]
    at += 1
    while at < len(text) and text[at] != quote:
        at += 2 if text[at] == "\\" else 1
    return at + 1


def _autocalled(segments: list[tuple[str, int, str]],
                trees: list[dict[str, Any]]) -> list[int]:
    """Offsets after which a pair of parentheses belongs.

    Follows the chain through the recording. A name whose node holds
    a call with no arguments, where the source wrote none, was called
    by the name mapper; the walk carries on inside that call's result.
    An explicit call with arguments is followed only where the
    recording holds exactly one call, because the key of the call is
    the arguments' values and the source has only their text.
    """
    if not segments:
        return []
    first = segments[0][0]
    node = None
    for tree in trees:
        if first in tree.get(ATTRS, {}):
            node = tree[ATTRS][first]
            break
    if node is None:
        return []
    found = []
    for index, (name, end, following) in enumerate(segments):
        if index:
            node = node.get(ATTRS, {}).get(name)
            if node is None:
                break
        calls = node.get(CALLS, {})
        if following == "(":
            if len(calls) != 1:
                break
            node = next(iter(calls.values()))
        elif "()" in calls and following != "[":
            found.append(end)
            node = calls["()"]
        elif following == "[":
            # A subscript the source spells: only a literal index is
            # followed, and what it reads is either an item or a key.
            break
    return found


# -- Verification -------------------------------------------------------

def _verify(before: str, after: str,
            trees: list[dict[str, Any]]) -> list[str]:
    from ct4 import render
    from ct4.fixture.record import replay

    def page(source: str) -> str:
        return render.render_source(source, [replay(tree) for tree in trees])

    try:
        old = page(before)
    except Exception as error:                              # noqa: BLE001
        raise MigrationError("the recording does not replay the text-mode"
                             " page: %s: %s" % (type(error).__name__, error))
    try:
        new = page(after)
    except Exception as error:                              # noqa: BLE001
        # A chain the rewrite could not follow is called by nobody in
        # strict mode, and the replay says which one.
        return ["strict mode raised %s: %s" % (type(error).__name__, error)]
    if old == new:
        return []
    return list(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                     "text mode", "strict mode", lineterm=""))
