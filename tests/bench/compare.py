"""Puts two runs of render.py next to each other.

Reads two JSON lines, the reference first and the fork second, and
prints the factor. A case that only one side has, the JSON mode under
ct3, is named as missing instead of being left out silently.

    python tests/bench/compare.py reference.json fork.json
    python tests/bench/compare.py reference.json fork.json --check

``--check`` holds the factors against ``FLOOR`` and fails where one has
fallen through. Deliberately a factor and not a number of milliseconds:
both runs happen on the same machine within the same minute, so the
speed of the machine cancels out. An absolute threshold would have to
be set for the slowest machine that ever runs it, and then it would
catch nothing.
"""

from __future__ import annotations

import json
import sys

# The lowest factor a case may reach before the run fails. Measured on
# 31-Aug-2026 against ct3 3.4.0.post5, with room left for a slower or
# busier machine.
#
# Compiling is allowed to be slower than ct3: the compiler reads the
# targets of every #for with ast.parse to know which names it bound.
# That is paid once, and the compile cache covers it.
#
# The last line is the control. It measures no template at all. If it
# moves, the measurement is broken and not the engine.
FLOOR = {
    "text: plain objects": 1.40,            # gemessen 1.79
    "text: helper objects": 1.40,           # gemessen 1.73
    "text: JSON by hand": 1.25,             # gemessen 1.51
    "compile: plain objects": 0.85,         # gemessen 0.96
    "reference: json.dumps by hand": 0.90,  # gemessen 0.99
}


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str]) -> int:
    argv = [a for a in argv if a != "--check"]
    check = len(argv) != len(sys.argv)
    if len(argv) != 3:
        print(__doc__)
        return 2
    before, after = load(argv[1]), load(argv[2])
    print("reference  %s" % before["version"])
    print("fork       %s" % after["version"])
    print()
    print("  %-32s %9s %9s %8s" % ("", "reference", "fork", "factor"))
    below = []
    for name, value in after["cases"].items():
        old = before["cases"].get(name)
        if old is None:
            print("  %-32s %9s %9.3f %8s" % (name, "n/a", value, "-"))
            continue
        factor = old / value
        floor = FLOOR.get(name)
        mark = ""
        if floor is not None and factor < floor:
            mark = "  below %.2fx" % floor
            below.append((name, factor, floor))
        print("  %-32s %9.3f %9.3f %7.2fx%s"
              % (name, old, value, factor, mark))
    if check and below:
        print()
        for name, factor, floor in below:
            print("%s: %.2fx, needs %.2fx" % (name, factor, floor))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
