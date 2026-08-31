"""Turn a line in the generated module into a line in the template.

A traceback out of a Cheetah template points at
``cheetah_DynamicallyCompiledCheetahTemplate_1788178423_42383.py``,
line 243. That is the truth, but it helps nobody: the file does not
exist on disk, and whoever wants to fix the error looks for the place in
their own template.

The mapping is already there. Cheetah writes behind every generated
statement which line and column it came from. Here that is read and
hung on the exception instead of thrown away.

Hung on, not replaced: the original traceback stays. Whoever wants to
look into the generated code still can.
"""

from __future__ import annotations

import re
from types import TracebackType
from typing import Any, Iterator, Literal

# The same two forms as in ct4.analyze: the ordinary one and the one an
# #errorCatcher produces.
ORIGIN = re.compile(r"(?:on|from) line (\d+), col (\d+)")
LINECOL = re.compile(r"lineCol=\((\d+),\s*(\d+)\)")

# How a generated module is recognized.
GENERATED = "cheetah"


def line_map(code: str) -> dict[int, tuple[int, int]]:
    """Line in the generated module to line and column in the template.

    An entry is made only where an origin stands. For everything in
    between, the last entry before it applies; ``position_of`` takes
    care of that.
    """
    found: dict[int, tuple[int, int]] = {}
    for number, text in enumerate(code.splitlines(), 1):
        match = LINECOL.search(text) or ORIGIN.search(text)
        if match is not None:
            found[number] = (int(match.group(1)), int(match.group(2)))
    return found


def position_of(mapping: dict[int, tuple[int, int]],
                line: int) -> tuple[int, int] | None:
    """The place in the template that belongs to a generated line.

    Taken is the last origin at or before the line. A statement spans
    several generated lines, and the origin stands at its start.
    """
    candidates = [number for number in mapping if number <= line]
    if not candidates:
        return None
    return mapping[max(candidates)]


def note(error: BaseException, text: str) -> None:
    """Hangs a remark on an exception.

    ``add_note`` exists only from Python 3.11 on, and ct4 runs from
    3.10. Where it is missing, the remark is stored on the exception;
    whoever needs it finds it under ``ct4_notes``.
    """
    adder = getattr(error, "add_note", None)
    if adder is not None:
        adder(text)
        return
    notes = getattr(error, "ct4_notes", None)
    if notes is None:
        notes = []
        error.ct4_notes = notes                         # type: ignore[attr-defined]
    notes.append(text)


def frames(error: BaseException) -> Iterator[TracebackType]:
    traceback = error.__traceback__
    while traceback is not None:
        yield traceback
        traceback = traceback.tb_next


def is_generated(name: str) -> bool:
    return GENERATED in name.lower()


def describe(error: BaseException, code: str,
             file: str = "<template>") -> list[str]:
    """The places in the template the error ran through."""
    mapping = line_map(code)
    out = []
    for frame in frames(error):
        if not is_generated(frame.tb_frame.f_code.co_filename):
            continue
        where = position_of(mapping, frame.tb_lineno)
        if where is None:
            continue
        out.append("%s, line %d, column %d" % (file, where[0], where[1]))
    return out


def annotate(error: BaseException, code: str,
             file: str = "<template>") -> BaseException:
    """Hangs the places in the template on the exception.

    Through ``add_note``, so that they come along when the traceback is
    printed, without anything being replaced.
    """
    for line in describe(error, code, file):
        note(error, "template: %s" % line)
    return error


class mapped:
    """A block whose exceptions carry the place in the template.

    ``code`` is the generated module code; it is fetched only when
    something really goes wrong, because preparing it would otherwise
    cost every run.
    """

    def __init__(self, code: Any, file: str = "<template>"):
        self.code = code
        self.file = file

    def __enter__(self) -> "mapped":
        return self

    def __exit__(self, kind: Any, error: Any,
                 traceback: Any) -> Literal[False]:
        if error is not None:
            code = self.code() if callable(self.code) else self.code
            if code:
                annotate(error, code, self.file)
        return False


class mapped_via:
    """Like ``mapped``, but with a mapping that is already done.

    JSON mode compiles into a Cheetah definition. Its lines stand in no
    file, and the origin notes inside it point at itself. The bridge to
    the template is built by the emitter, and it arrives here.
    """

    def __init__(self, origins: dict[int, int], file: str = "<template>"):
        self.origins = origins
        self.file = file

    def __enter__(self) -> "mapped_via":
        return self

    def __exit__(self, kind: Any, error: Any,
                 traceback: Any) -> Literal[False]:
        if error is None or not self.origins:
            return False
        mapping = {generated: (source, 1)
                   for generated, source in self.origins.items()}
        for frame in frames(error):
            if not is_generated(frame.tb_frame.f_code.co_filename):
                continue
            where = position_of(mapping, frame.tb_lineno)
            if where is not None:
                note(error, "template: %s, line %d"
                     % (self.file, where[0]))
                break
        return False
