"""Puts two runs of render.py next to each other.

Reads two JSON lines, the reference first and the fork second, and
prints the factor. A case that only one side has, the JSON mode under
ct3, is named as missing instead of being left out silently.

    python tests/bench/compare.py reference.json fork.json
"""

from __future__ import annotations

import json
import sys


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    before, after = load(argv[1]), load(argv[2])
    print("reference  %s" % before["version"])
    print("fork       %s" % after["version"])
    print()
    print("  %-32s %9s %9s %8s" % ("", "reference", "fork", "factor"))
    for name, value in after["cases"].items():
        old = before["cases"].get(name)
        if old is None:
            print("  %-32s %9s %9.3f %8s"
                  % (name, "n/a", value, "-"))
            continue
        print("  %-32s %9.3f %9.3f %7.2fx"
              % (name, old, value, old / value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
