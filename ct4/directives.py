"""Directives a project registers, and what a handler gets and gives.

ct3 has ``macroDirectives``: a compiler setting holding callables that
receive a directive's body as text and hand text back, which the
parser then reads as if the author had written it. Positions are lost
in that switch, a tool that only looks at the file never learns the
names, and the setting has to be carried through every ``Template``
construction. This is the successor the plan asks for: registered in a
file beside the templates, and handled at the level of the generated
code.

A project registers its directives in a ``ct4.toml`` next to its
templates or in a directory above them::

    [directives]
    greet = "myskin.directives:greet"

    [blocks]
    box = "myskin.directives:box"

A name under ``[directives]`` stands in a tag of its own and has no
body. One under ``[blocks]`` runs to its ``#end name``, or to the end
of its line in the colon short form, the way ct3's own macro
directives do. Nothing else in the file is read.

The handler is called once per use, while the template is compiled,
with a ``Call``. It returns the statements that stand where the
directive stood: ``ast`` statements, and for a block ``BODY`` among
them where the body's own code goes. They run inside the template's
method, where ``write`` writes output and ``_filter`` is the filter in
force; ``write``, ``write_value`` and ``expression`` build the usual
shapes.

A template that uses a registered directive is compiled by the
generator and by nothing else. ct3's compiler does not know the name,
so there is no falling back to it: what the generator cannot take in
such a template is an error with the reason, not a page rendered by an
engine that read the directive as text.
"""

from __future__ import annotations

import ast
import importlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, cast

FILE_NAME = "ct4.toml"
LINE_TABLE = "directives"
BLOCK_TABLE = "blocks"

# What a directive may be called. The lexer reads a name with these
# characters, so anything else could never be found in a template.
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
TARGET = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*):([A-Za-z_][A-Za-z0-9_]*)$")


class DirectiveError(Exception):
    """A registration that cannot be used, or a handler that misbehaved.

    Deliberately not an ``Unsupported``: that one means "ct3 renders
    this instead", and ct3 cannot render a template whose directives
    it does not know.
    """


class _Body:
    def __repr__(self) -> str:
        return "BODY"


# Where a block's own body goes among the statements a handler returns.
BODY = _Body()


@dataclass(frozen=True)
class Call:
    """One use of a registered directive, as the handler sees it.

    ``arguments`` is the text of the tag after the name, blanks and
    the colon of the short form stripped; ``expression`` reads a
    Cheetah expression out of it. ``short`` is the colon form, whose
    body is the rest of the tag's line.
    """

    name: str
    arguments: str
    line: int
    column: int
    short: bool = False
    block: bool = False


@dataclass(frozen=True)
class Registration:
    """The directives one ``ct4.toml`` registers."""

    path: Path | None = None
    line: dict[str, str] = field(default_factory=dict)
    block: dict[str, str] = field(default_factory=dict)
    _handlers: dict[str, Callable[[Call], Any]] = field(default_factory=dict,
                                                       compare=False)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.line) | frozenset(self.block)

    def is_block(self, name: str) -> bool:
        return name in self.block

    def handler(self, name: str) -> Callable[[Call], Any]:
        """The callable behind a name, imported on first use."""
        found = self._handlers.get(name)
        if found is None:
            target = self.block.get(name) or self.line[name]
            found = _load(target, name, self.path)
            self._handlers[name] = found
        return found

    def run(self, call: Call) -> list[Any]:
        """Calls the handler and checks what came back.

        Returns:
            list[Any]: ``ast.stmt`` items, and ``BODY`` where the
            block's body goes.
        """
        result = self.handler(call.name)(call)
        items = list(result) if isinstance(result, (list, tuple)) else [result]
        for item in items:
            if item is BODY:
                if not call.block:
                    raise DirectiveError(
                        "#%s is registered under [%s] and has no body to"
                        " put where BODY stands" % (call.name, LINE_TABLE))
            elif not isinstance(item, ast.stmt):
                raise DirectiveError(
                    "#%s: the handler returned %s where an ast statement"
                    " was expected" % (call.name, type(item).__name__))
        return items


NONE = Registration()


# -- Building the statements a handler returns -------------------------

def write(text: str) -> ast.stmt:
    """A statement that writes the text as it is, unfiltered."""
    return ast.Expr(ast.Call(ast.Name("write", ast.Load()),
                             [ast.Constant(text)], []))


def expression(text: str) -> ast.expr:
    """A Cheetah expression as Python, ``$name`` and all.

    Read the way the generator reads a directive's argument, so a name
    the template bound itself, a ``#for`` target say, resolves the
    same way here as in a placeholder.
    """
    from ct4.lang import codegen

    return codegen.expression_ast(text)


def write_value(text: str) -> ast.stmt:
    """A statement that writes a Cheetah expression through the filter.

    What a placeholder does: the value goes through the filter in
    force, so ``None`` and a number come out the way the rest of the
    page prints them.
    """
    call = ast.Call(ast.Name("_filter", ast.Load()), [expression(text)], [])
    return ast.Expr(ast.Call(ast.Name("write", ast.Load()), [call], []))


def statements(python: str) -> list[ast.stmt]:
    """Python source as statements, for a handler that would rather write."""
    return ast.parse(python).body


# -- Finding and reading the registration ------------------------------

_CACHE: dict[Path, tuple[float, Registration]] = {}


def find_for(file: str | Path | None) -> Registration:
    """The registration that applies to a template.

    Searched upward from the template's directory, or from the working
    directory for a template that has none, the way a ``pyproject.toml``
    is found. The nearest file wins; none means no directives.
    """
    start = Path.cwd()
    if file:
        candidate = Path(file)
        if candidate.is_absolute() or candidate.exists():
            start = candidate.resolve().parent
    for directory in (start, *start.parents):
        path = directory / FILE_NAME
        if path.is_file():
            return load(path)
    return NONE


def load(path: Path) -> Registration:
    """Reads one ``ct4.toml``, remembering it until the file changes."""
    stamp = path.stat().st_mtime
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    tables = _tables(path.read_text(encoding="utf-8"), path)
    line = _table(tables.get(LINE_TABLE, {}), LINE_TABLE, path)
    block = _table(tables.get(BLOCK_TABLE, {}), BLOCK_TABLE, path)
    both = set(line) & set(block)
    if both:
        raise DirectiveError("%s: %s under [%s] and under [%s] at once"
                             % (path, ", ".join(sorted(both)), LINE_TABLE,
                                BLOCK_TABLE))
    made = Registration(path, line, block)
    _CACHE[path] = (stamp, made)
    return made


def _table(entries: Any, name: str, path: Path) -> dict[str, str]:
    from ct4.lang import lex

    if not isinstance(entries, dict):
        raise DirectiveError("%s: [%s] is not a table" % (path, name))
    taken = lex.directive_names()
    out: dict[str, str] = {}
    for key, value in entries.items():
        if not NAME.match(key):
            raise DirectiveError("%s: %r cannot be a directive name"
                                 % (path, key))
        if key in taken:
            raise DirectiveError("%s: #%s is Cheetah's own and cannot be"
                                 " registered" % (path, key))
        if not isinstance(value, str) or not TARGET.match(value):
            raise DirectiveError(
                '%s: %s = %r, expected "package.module:function"'
                % (path, key, value))
        out[key] = value
    return out


def _load(target: str, name: str, path: Path | None) -> Callable[[Call], Any]:
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
        found = getattr(module, attribute)
    except (ImportError, AttributeError) as error:
        raise DirectiveError("%s: #%s names %s, which cannot be loaded: %s"
                             % (path, name, target, error)) from None
    if not callable(found):
        raise DirectiveError("%s: #%s names %s, which is not callable"
                             % (path, name, target))
    return cast(Callable[[Call], Any], found)


def _tables(text: str, path: Path) -> dict[str, Any]:
    """The tables of the file, through tomllib where Python has it."""
    if sys.version_info >= (3, 11):
        import tomllib

        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            raise DirectiveError("%s: %s" % (path, error)) from None
    return read_plain_tables(text, path)


# A line of the shape the two tables are made of.
ASSIGNMENT = re.compile(r'^([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"\s*(?:#.*)?$')
HEADER = re.compile(r"^\[([A-Za-z0-9_.-]+)\]\s*(?:#.*)?$")


def read_plain_tables(text: str, path: Path) -> dict[str, Any]:
    """The subset of TOML this file needs, for a Python without tomllib.

    ``tomllib`` came with 3.11 and the project runs from 3.10 with no
    dependency at all. What the file holds is table headers and
    ``name = "target"`` lines, so that is what is read here, plus
    comments and blank lines; any other line is refused by name rather
    than misread. On 3.11 and up ``tomllib`` reads the same file, and a
    test holds the two readers to the same answer.
    """
    tables: dict[str, Any] = {}
    current: dict[str, str] | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = HEADER.match(line)
        if header is not None:
            current = tables.setdefault(header.group(1), {})
            continue
        assignment = ASSIGNMENT.match(line)
        if assignment is None or current is None:
            raise DirectiveError(
                '%s, line %d: on Python 3.10 this file may hold only'
                ' [table] headers and name = "target" lines' % (path, number))
        current[assignment.group(1)] = assignment.group(2)
    return tables
