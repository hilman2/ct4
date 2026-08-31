"""A corpus case and how it is stored.

Storage is JSONL, one line per case. That is no accident: with several
thousand cases a diff stays readable this way, because a changed
template touches exactly one line, and the file can be streamed line by
line without pulling all of it into memory.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

CT3_DEFAULT = "ct3_default"
INLINE = "inline"
FIXTURE = "fixture"

# Two kinds of case. "render" compares the output and needs a context
# for that. "compile" compares the generated module code and gets by
# without one. That is the only reason third-party skins can be taken
# into the corpus at all: their context would be a running application.
RENDER = "render"
COMPILE = "compile"

# Marker for a value that JSON does not know. So far only one shows up:
# ct3 passes a base class through extraCompileKwArgs
# (`{'baseclass': dict}`) and thereby produces a second version of every
# test class. Without this marker a third of the test suite drops out of
# the corpus.
TYPE_TAG = "__type__"


@dataclass(frozen=True)
class Case:
    """Template plus context yields the expected output.

    ``namespace`` says where the searchList comes from: ``inline`` takes
    ``context``, any other value names a builder from
    ``ct4.corpus.namespaces``. The ct3 test cases need the second route,
    because their context holds lambdas and instances that cannot be
    stored as JSON.
    """

    id: str
    template: str
    expected: str
    kind: str = RENDER
    namespace: str = CT3_DEFAULT
    context: list[Any] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    compile_kwargs: dict[str, Any] = field(default_factory=dict)
    filter: str = ""
    origin: str = ""


def is_jsonable(value: Any) -> bool:
    """Whether the value can be stored as JSON without loss.

    The harvester needs this to sort out cases whose context or compiler
    settings hold functions or instances. A case that the test bench
    cannot reconstruct later does not belong in the corpus, not even
    halfway.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def encode(value: Any) -> Any:
    """Makes a value storable that JSON does not know.

    Classes become their dotted name. Everything else stays as it is;
    whether it can be stored is then decided by ``is_jsonable``.
    """
    if isinstance(value, type):
        return {TYPE_TAG: "%s.%s" % (value.__module__, value.__qualname__)}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def decode(value: Any) -> Any:
    """Restores what ``encode`` stored."""
    if isinstance(value, dict) and TYPE_TAG in value and len(value) == 1:
        module_name, _, attribute = value[TYPE_TAG].rpartition(".")
        module = __import__(module_name, fromlist=[attribute])
        return getattr(module, attribute)
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def write_jsonl(cases: Iterable[Case], path: Path) -> int:
    """Writes the cases and returns how many there were."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(asdict(case), ensure_ascii=False))
            handle.write("\n")
            count += 1
    return count


def read_jsonl(path: Path) -> Iterator[Case]:
    """Reads the cases of a file, in the order of the file."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield Case(**json.loads(line))
