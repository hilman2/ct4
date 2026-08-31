"""Every accepted template, rendered by both engines and compared.

The corpus holds 2026 real cases and every one of them writes its
directives on lines of their own, so it says nothing about what happens
when a directive shares a line with output. This builds templates that
do, out of a fixed set of fragments, and holds the generator to the
same rule the corpus holds it to: what it accepts renders byte for byte
what Cheetah renders.

It found 1864 wrong templates out of 12627 the day it was written, in
five groups, all of them whitespace around a directive. The corpus saw
none of them.

    python tests/fuzz/whitespace.py            counts, grouped by shape
    python tests/fuzz/whitespace.py --examples up to six per group

Exits 1 where anything renders differently, so it can stand in the
Docker run next to the corpus.

Deterministic on purpose: the same 13072 templates every time, so two
runs are comparable and a number in a commit message can be checked.
The reference is the Cheetah in this repo rather than an installed ct3,
which is the right one: the generator has to agree with the engine it
ships beside. That the two agree with each other is what the corpus
run measures.
"""

from __future__ import annotations

import collections
import itertools
import random
import sys
import warnings

warnings.filterwarnings("ignore")

from Cheetah.Template import Template                          # noqa: E402
from ct4.lang import codegen, tree                             # noqa: E402

CONTEXT = {"x": 1, "y": "Y", "aStr": "blarg", "n": 3,
           "d": {"k": "v"}, "f": lambda a=1: a * 2}

# One fragment per shape worth crossing with another. Directives that
# open a block appear in both their forms, the one whose #end has a
# line to itself and the one whose #end shares a line with output,
# because that difference is what the corpus never shows.
FRAGMENTS = [
    "A", "A ", "  ", "$x", " $x ", "$aStr",
    "#slurp", "#echo 1", "#echo $x", "#silent 1", "#set $q = 2",
    "#stop", "#pass", "#import os",
    "#if 1\nB\n#end if", "#if 1\nB#end if", "#for $i in range(2)\nZ#end for",
    "#for $i in range(2)\nZ\n#end for", "#def g\nD\n#end def",
    "#def g\nD#end def", "#block b\nBB\n#end block",
    "#raw\nRR\n#end raw", "#* c *#", "##c", "#filter None\nP\n#end filter",
    "#cache\nC$x\n#end cache", "#call str\narg\n#end call",
    "#try\nT\n#except\nE\n#end try", "#while False\nW\n#end while",
    "#repeat 2\nR\n#end repeat", "#unless 0\nU\n#end unless",
    "<%= 1 %>", "<% write('p') %>", "#attr $a = 1",
    "#if 1: Q", "#for $i in range(2): $i",
]

# What stands before a fragment and after it. The point of the leads is
# that half of them leave the line dirty, which is the condition every
# whitespace rule in the compiler turns on.
LEAD = ["", "L", "L\n", "  ", "\t", "L "]
TAIL = ["", "T", "\nT", "\nT\n", "  ", "\n"]


def shape(source: str) -> str:
    """Which whitespace rule a wrong template belongs to.

    A label for reading a report, not a decision anything depends on.
    Tried in order, first match wins, and a template that fits none is
    "other". A growing "other" is the sign that this list has fallen
    behind what the generator does.
    """
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line in lines:
        if "#end " in line and not line.lstrip().startswith("#end "):
            return "end shares a line with output"
        if "#echo" in line and not line.lstrip().startswith("#echo"):
            return "echo behind text"
        if "#stop" in line and not line.lstrip().startswith("#stop"):
            return "stop behind text"
        if line.lstrip().startswith("#*") and line != line.lstrip():
            return "indented block comment"
    for line in lines:
        if not line.lstrip().startswith("#") and "#" in line:
            if line[:line.index("#")].strip():
                return "dirty opening tag"
    return "other"


def sources() -> list[str]:
    """The templates of the set, in a fixed order.

    Two passes and a seeded third. Every ordered pair of fragments on
    two lines, every fragment alone with every lead and tail, and then
    4000 runs of one to four fragments, three in ten of them rewritten
    to CRLF. The seed is what makes two runs comparable.
    """
    out = []
    for first, second in itertools.product(FRAGMENTS, repeat=2):
        for lead in LEAD:
            out.append(lead + first + "\n" + second + "\n")
    for fragment in FRAGMENTS:
        for lead in LEAD:
            for tail in TAIL:
                out.append(lead + fragment + tail)
    rng = random.Random(7)
    for _ in range(4000):
        count = rng.randint(1, 4)
        parts = [rng.choice(FRAGMENTS) for _ in range(count)]
        source = rng.choice(LEAD) + "\n".join(parts) + rng.choice(TAIL)
        if rng.random() < 0.3:
            source = source.replace("\n", "\r\n")
        out.append(source)
    return out


def by_cheetah(source: str) -> str:
    """What the compiler in this repo renders, or the failure it hit.

    A failure is a string like the output is, so that a template both
    engines refuse counts as agreement rather than as a crash in the
    middle of a run of 13072.
    """
    try:
        klass = Template.compile(source=source, useCache=False,
                                 cacheCompilationResults=False)
        return str(klass(searchList=[dict(CONTEXT)]).respond())
    except Exception as error:                                 # noqa: BLE001
        return "!!%s" % type(error).__name__


def by_generator(source: str) -> str:
    """The same for the generator under test."""
    try:
        return codegen.render(source, [dict(CONTEXT)])
    except Exception as error:                                 # noqa: BLE001
        return "!!%s" % type(error).__name__


def main() -> int:
    show = "--examples" in sys.argv
    every = sources()
    taken = 0
    wrong: collections.Counter[str] = collections.Counter()
    examples: dict[str, list[tuple[str, str, str]]] = {}
    for source in every:
        try:
            codegen.generate(source)
        except (codegen.Unsupported, tree.StructureError):
            continue
        except Exception:                                      # noqa: BLE001
            # Anything else is a defect in its own right, and the test
            # suite has a case for it. Not this run's business.
            continue
        taken += 1
        want = by_cheetah(source)
        got = by_generator(source)
        if got == want:
            continue
        key = shape(source)
        wrong[key] += 1
        examples.setdefault(key, []).append((source, got, want))

    total = sum(wrong.values())
    print("templates      %d" % len(every))
    print("accepted       %d" % taken)
    print("wrong          %d  (%.1f %% of accepted)"
          % (total, 100.0 * total / max(taken, 1)))
    for key, count in wrong.most_common():
        print("  %5d  %s" % (count, key))
    if show:
        for key, items in examples.items():
            print()
            print("== %s (%d) ==" % (key, len(items)))
            for source, got, want in items[:6]:
                print("  src %r" % source)
                print("      ct4 %r" % got[:90])
                print("      ct3 %r" % want[:90])
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
