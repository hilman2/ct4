"""Render a template that nobody has read yet, without regret.

For a preview in an editor and for an agent that rewrites a template
and looks at the result: both feed the engine text that may be wrong
in ways a human would not write. ``#import os`` and ``$os.system``
are two lines away, a ``#while`` with no way out hangs the process,
and a lookup of ``__class__`` reaches everything Python has.

Two guards, and what they are against is said plainly: accidents
and careless edits, not an adversary. Python has no sound sandbox and
this does not claim one.

The first guard is static and runs before anything is generated, in
the parse step every template goes through, includes as well: the
directives that load modules are refused, so is PSP, so is an
``#include`` whose name is computed, and so is any name on a short
list or any dunder in a placeholder or a directive argument.

The second guard is the process: ``ct4 render --sandbox`` renders in
a child with a time limit, so a hang is a reported failure and not a
stuck editor.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from ct4.lang import lex, tree

ENV = "CT4_SANDBOX"

# Directives that reach outside the template.
REFUSED_DIRECTIVES = frozenset({"import", "from", "extends", "compiler",
                                "compiler-settings", "defmacro"})

# Names that reach outside the template from inside an expression.
# The search list is looked up first, so a context could shadow them;
# it is still a name nobody puts in a page on purpose.
DENIED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "breakpoint", "input",
    "os", "sys", "subprocess", "importlib", "builtins", "shutil",
    "socket", "pathlib",
})

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DUNDER = re.compile(r"__[A-Za-z0-9_]+__")


class SandboxViolation(Exception):
    """A template the sandbox will not render, and where."""

    def __init__(self, message: str, line: int, column: int):
        super().__init__("%s (line %d, column %d)" % (message, line, column))
        self.line = line
        self.column = column


def active() -> bool:
    return bool(os.environ.get(ENV))


def check(root: tree.Node) -> None:
    """Refuses what the sandbox does not render.

    Raises:
        SandboxViolation: on the first thing found, naming it.
    """
    for node in root.walk():
        if node.kind in (lex.DIRECTIVE, tree.BLOCK):
            _check_directive(node)
        elif node.kind == lex.PSP:
            raise SandboxViolation("PSP is not rendered in the sandbox",
                                   node.line, node.column)
        for token in node.tokens:
            if token.kind == lex.PLACEHOLDER:
                _check_expression(token.text, token.line, token.column)


def _check_directive(node: tree.Node) -> None:
    if node.name in REFUSED_DIRECTIVES:
        raise SandboxViolation("#%s is not rendered in the sandbox"
                               % node.name, node.line, node.column)
    arguments = "".join(t.text for t in node.tokens[1:]
                        if t.kind != lex.DIRECTIVE_END)
    if node.name == "set" and re.match(r"\s*module\b", arguments):
        raise SandboxViolation("#set module is not rendered in the sandbox",
                               node.line, node.column)
    if node.name == "include":
        stripped = arguments.strip()
        if not re.match(r"""(raw\s+)?(source\s*=\s*)?["']""", stripped):
            raise SandboxViolation(
                "an #include whose name is not a literal is not rendered"
                " in the sandbox", node.line, node.column)
    _check_expression(arguments, node.line, node.column)


def _check_expression(text: str, line: int, column: int) -> None:
    dunder = DUNDER.search(text)
    if dunder is not None:
        raise SandboxViolation("%s is not looked up in the sandbox"
                               % dunder.group(0), line, column)
    for match in IDENTIFIER.finditer(text):
        if match.group(0) in DENIED_NAMES:
            raise SandboxViolation("%s is not looked up in the sandbox"
                                   % match.group(0), line, column)


# -- The child process -------------------------------------------------

TIMED_OUT = 3


def run(path: Path, contexts: Sequence[Path], out: Path | None,
        encoding: str, timeout: float) -> int:
    """Renders in a child process under the static guard, with a limit.

    Returns the child's exit code, or TIMED_OUT where the limit ran
    out; then the child is killed and nothing was written.
    """
    argv = [sys.executable, "-m", "ct4.cli", "render", str(path),
            "--encoding", encoding]
    for context in contexts:
        argv += ["--context", str(context)]
    if out is not None:
        argv += ["--out", str(out)]
    environment = dict(os.environ)
    environment[ENV] = "1"
    try:
        done = subprocess.run(argv, env=environment, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        print("ct4 render: %s did not finish within %g s and was stopped"
              % (path, timeout), file=sys.stderr)
        return TIMED_OUT
    sys.stdout.buffer.write(done.stdout)
    sys.stdout.flush()
    sys.stderr.write(done.stderr.decode("utf-8", "replace"))
    return done.returncode
