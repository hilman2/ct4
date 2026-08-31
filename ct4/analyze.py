"""What a template reads from its context.

Not through a second parser, but from the code Cheetah generates
anyway. Every placeholder appears there as a path, together with the
line and column it stood at in the template:

    _v = VFFSL(SL,"day.outTemp.max",True) # '$day.outTemp.max' on line 5, col 7

This is not a stopgap but the most accurate source there is: it is what
actually gets looked up at run time. A parser of our own could diverge
from it, this route cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator

# A lookup in the searchList. VFFSL resolves the path through the frame
# and the searchList, VFSL only through the searchList.
LOOKUP = re.compile(r'VFF?SL\(SL,"([^"]+)",\s*(?:True|False)\)')

# A lookup the compiler shortened because it bound the name itself, as
# it does for a #for target. It reads the same value as a VFFSL would;
# the only difference is where the search starts. The backreference is
# what tells this generated form apart from a VFN somebody wrote by
# hand: only the compiler builds a namespace of the name for itself.
LOCAL = re.compile(r'VFN\(\{"(\w+)":\1\},"([^"]+)",\s*(?:True|False)\)')

# The origin note Cheetah writes behind the expression.
ORIGIN = re.compile(r"(?:on|from) line (\d+), col (\d+)")

# If a template contains an #errorCatcher, every placeholder moves into
# a method of its own. Cheetah then splits expression and origin across
# several lines, and only the call to warn() carries both. Without this
# second form the weewx skins lose their placeholders: Seasons sets the
# errorCatcher.
LINECOL = re.compile(r"lineCol=\((\d+),\s*(\d+)\)")


@dataclass(frozen=True)
class Placeholder:
    """A path the template looks up, and where it stands."""

    path: str
    line: int
    column: int

    @property
    def root(self) -> str:
        return self.path.split(".")[0]


def placeholders(source: str, settings: dict[str, Any] | None = None,
                 ) -> list[Placeholder]:
    """Every lookup of a template, in the order of the file."""
    from Cheetah.Compiler import ModuleCompiler

    options = dict(settings or {})
    options["addTimestampsToCompilerOutput"] = False
    compiler = ModuleCompiler(source, moduleName="ct4_analyze",
                              mainClassName="ct4_analyze", settings=options)
    return sorted(_scan(str(compiler)),
                  key=lambda item: (item.line, item.column))


def _scan(code: str) -> Iterator[Placeholder]:
    for text in code.splitlines():
        origin = LINECOL.search(text) or ORIGIN.search(text)
        if origin is None:
            continue
        line, column = int(origin.group(1)), int(origin.group(2))
        # Lookups in the searchList, plus the ones the compiler
        # shortened to a local it bound itself. Any other VFN stands
        # for an attribute on an already resolved value; no declaration
        # knows which ones exist there, and guessing would turn into a
        # false finding. If several expressions stand on one line, they
        # share its position.
        for path in LOOKUP.findall(text):
            yield Placeholder(path, line, column)
        for _, path in LOCAL.findall(text):
            yield Placeholder(path, line, column)


def roots(items: list[Placeholder]) -> list[str]:
    """The roots a template needs, without repetition."""
    return sorted({item.root for item in items})


def paths(items: list[Placeholder]) -> list[str]:
    """The full paths, without repetition."""
    return sorted({item.path for item in items})
