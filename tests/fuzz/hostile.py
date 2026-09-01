"""Every corpus template, rendered against a context that talks back.

The corpus compares bytes, and bytes are only half the interface. The
other half is what the engine *asks* of the values it was given and of
the filter it was handed, and a passive fixture with a well-behaved
filter says nothing about that. One example cost a working day: ct3
hands the output filter the placeholder's own source as ``rawExpr`` on
every write, this layer did not, and all 2026 corpus cases went by
without noticing, because the default filter ignores what it is given.
weewx's own filter does not ignore it.

So instead of instrumenting the engines, this makes the data hostile.
Every value answers every question and writes down what was asked, and
the filter writes down every keyword it received. The interaction ends
up in the bytes, and the comparison that was already there compares it.

It buys a second thing for free. skins.jsonl holds 390 real skin
templates as compile cases, because rendering them needs a live weewx.
Against a context that answers everything they render here, so the
templates that matter most stop being unrenderable.

    python tests/fuzz/hostile.py
    python tests/fuzz/hostile.py --examples

Exits 1 where anything differs.
"""

from __future__ import annotations

import collections
import sys
from typing import Any, Iterator

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from Cheetah.Filters import Filter                             # noqa: E402
from harness import (corpus_templates, disagreements,          # noqa: E402
                     report)

# How often a value has been asked whether it is true, by path. A
# #while over a value that is always true never ends, and a value that
# is never true never enters an #if, so the answer changes with the
# asking: true twice, false after that. Keyed by path and not per
# object because every lookup builds a fresh one, which is exactly what
# a #while does on each turn.
#
# Deterministic, and that is the point: where the two engines ask the
# same questions in the same order they get the same answers, and where
# they do not the difference lands in the output.
ASKED: collections.Counter[str] = collections.Counter()

TRUE_FOR = 2


class Told:
    """A value that answers anything and says what it was asked.

    ``str`` of it is its own path, so a template that writes it writes
    down which name it reached for and what it did on the way:
    ``$day.outTemp.max`` comes out as ``{day.outTemp.max}`` and
    ``$day.rain.format('x')`` as ``{day.rain.format('x')}``.

    Autocalling shows up because NameMapper calls what it finds, and a
    call adds ``()`` to the path. An engine that autocalls where the
    other does not therefore writes different bytes.
    """

    __slots__ = ("_path",)

    def __init__(self, path: str) -> None:
        self._path = path

    # -- what a template does to a value ------------------------------

    def __getattr__(self, name: str) -> "Told":
        if name.startswith("__"):
            # Dunder lookups are the machinery asking, not the
            # template. Answering them would make this object claim to
            # be a bound method, an iterator and a context manager at
            # once, and Cheetah decides real things on those answers.
            raise AttributeError(name)
        return Told("%s.%s" % (self._path, name))

    def __call__(self, *args: Any, **keywords: Any) -> "Told":
        inside = [repr(arg) for arg in args]
        inside += ["%s=%r" % (key, keywords[key]) for key in sorted(keywords)]
        return Told("%s(%s)" % (self._path, ",".join(inside)))

    def __getitem__(self, key: Any) -> "Told":
        return Told("%s[%r]" % (self._path, key))

    def __iter__(self) -> Iterator["Told"]:
        return iter([Told("%s[0]" % self._path), Told("%s[1]" % self._path)])

    def __len__(self) -> int:
        return 2

    def __contains__(self, key: Any) -> bool:
        return True

    def __bool__(self) -> bool:
        ASKED[self._path] += 1
        return ASKED[self._path] <= TRUE_FOR

    # -- what an expression does to a value ---------------------------

    def __str__(self) -> str:
        return "{%s}" % self._path

    def __repr__(self) -> str:
        return "{%s}" % self._path

    def __hash__(self) -> int:
        return hash(self._path)

    # Fixed and not derived from the path: a number that changed with
    # the name would make every difference look like a difference in
    # arithmetic.
    def __float__(self) -> float:
        return 1.5

    def __int__(self) -> int:
        return 1

    def __index__(self) -> int:
        return 1

    def _binary(self, op: str, other: Any) -> "Told":
        return Told("%s%s%r" % (self._path, op, other))

    def __add__(self, other: Any) -> "Told":
        return self._binary("+", other)

    def __radd__(self, other: Any) -> "Told":
        return Told("%r+%s" % (other, self._path))

    def __sub__(self, other: Any) -> "Told":
        return self._binary("-", other)

    def __mul__(self, other: Any) -> "Told":
        return self._binary("*", other)

    def __truediv__(self, other: Any) -> "Told":
        return self._binary("/", other)

    def __mod__(self, other: Any) -> "Told":
        return self._binary("%", other)

    def __eq__(self, other: Any) -> "Told":                    # type: ignore[override]
        return self._binary("==", other)

    def __ne__(self, other: Any) -> "Told":                    # type: ignore[override]
        return self._binary("!=", other)

    def __lt__(self, other: Any) -> "Told":
        return self._binary("<", other)

    def __le__(self, other: Any) -> "Told":
        return self._binary("<=", other)

    def __gt__(self, other: Any) -> "Told":
        return self._binary(">", other)

    def __ge__(self, other: Any) -> "Told":
        return self._binary(">=", other)


class Everything(dict):
    """A namespace that holds every name there is.

    NameMapper asks whether a namespace has a key before it takes the
    value out of it, so both answers are here. Without it a real skin
    stops at its first lookup and the run measures the first line of
    390 templates.
    """

    def __contains__(self, key: Any) -> bool:
        return True

    def __missing__(self, key: Any) -> Told:
        return Told(str(key))

    def get(self, key: Any, default: Any = None) -> Told:
        return Told(str(key))

    def has_key(self, key: Any) -> bool:                       # noqa: A003
        return True


class Loudmouth(Filter):  # type: ignore[misc]
    """An output filter that writes down what it was handed.

    Every keyword, sorted, so the comparison sees them. This is the
    whole point of the run: two engines that write the same bytes
    through a filter that ignores its keywords are not doing the same
    thing, and the first custom filter a user plugs in finds out.
    """

    def filter(self, value: Any, **keywords: Any) -> str:
        marks = ";".join("%s=%s" % (key, keywords[key])
                         for key in sorted(keywords))
        return "%s<%s>" % (value, marks)


def build() -> tuple[list[Any], Any]:
    """A fresh context and filter, and the true-counter reset with it."""
    ASKED.clear()
    return [Everything()], Loudmouth


def main() -> int:
    sources = corpus_templates()
    if not sources:
        print("no corpus found")
        return 0
    seen, taken, found = disagreements(iter(sources), build)
    return report("Corpus templates against a context that talks back",
                  seen, taken, found,
                  examples=6 if "--examples" in sys.argv else 0)


if __name__ == "__main__":
    raise SystemExit(main())
