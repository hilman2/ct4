"""The code generator against ct3's compiler, both through Cheetah.

Not codegen.render but Template.compile, which is the path a caller
takes. Two numbers, and they pull in opposite directions.

Compiling is slower here and by construction: this builds an ast, hands
it back as text, and Python parses that text again, where ct3 pastes
strings together and parses once. It is paid once per template and the
persistent compile cache is what pays it.

Rendering is the one that matters, because it is paid on every run, and
it must not be slower. It was, by a factor of two, until the generator
learned what the fork's own compiler already knew: a lookup that starts
at a name the compiler bound does not have to walk the search list for
it. That is the single largest item in a loop.

    python tests/bench/backend.py
    python tests/bench/backend.py --check

``--check`` holds both factors against FLOOR. Deliberately factors and
not milliseconds: both runs happen on the same machine within the same
second, so the speed of the machine cancels out.
"""

from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

from Cheetah.Template import Template                          # noqa: E402
from ct4.corpus.case import read_jsonl                         # noqa: E402
from ct4.lang import backend                                   # noqa: E402

# Measured 01-Sep-2026: compile 0.76 on the work machine and 0.67 in
# the image, render 0.99 in both. The compile floor is set below the
# slower of the two, because it is a ratio of two things that scale
# differently with the machine and a tight floor there would fail on
# somebody else's.
#
# The render floor is the load-bearing one. Below 0.95 the generated
# module is doing something the compiler's is not, and the first place
# to look is whether the scope rewrite still fires.
FLOOR = {"compile": 0.60, "render": 0.95}

ROWS = [{"name": "r%d" % i, "value": i * 1.5, "flag": i % 2 == 0}
        for i in range(200)]
CONTEXT = {"rows": ROWS, "title": "Report", "count": len(ROWS)}

SHAPES = {
    "plain": ("<h1>$title</h1>\n"
              "#for $r in $rows\n"
              "<tr><td>$r.name</td><td>$r.value</td></tr>\n"
              "#end for\n"),
    "branches": ("#for $r in $rows\n"
                 "#if $r.flag\n<b>$r.name</b>\n#else\n<i>$r.name</i>\n"
                 "#end if\n#end for\n"),
    "method": ("#def cell($v)\n<td>$v</td>#slurp\n#end def\n"
               "#for $r in $rows\n$cell($r.name)$cell($r.value)\n"
               "#end for\n"),
}


def corpus_dir() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (Path("/repo/corpus"), here.parents[2] / "corpus"):
        if (candidate / "skins.jsonl").exists():
            return candidate
    return None


def compiled(source: str, **kwargs: Any) -> Any:
    return Template.compile(source=source, useCache=False,
                            cacheCompilationResults=False, **kwargs)


def compile_time(templates: list[str]) -> float:
    """Seconds for one pass over the templates, best of three."""
    best = None
    for _ in range(3):
        start = time.perf_counter()
        for source in templates:
            try:
                compiled(source)
            except Exception:                                  # noqa: BLE001
                pass
        taken = time.perf_counter() - start
        best = taken if best is None else min(best, taken)
    return best or 0.0


def render_time(klass: Any, repeats: int = 40) -> float:
    """Milliseconds for one render, best of five runs of `repeats`."""
    best = None
    for _ in range(5):
        start = time.perf_counter()
        for _ in range(repeats):
            str(klass(searchList=[CONTEXT]).respond())
        taken = time.perf_counter() - start
        best = taken if best is None else min(best, taken)
    return (best or 0.0) / repeats * 1000.0


def main() -> int:
    root = corpus_dir()
    if root is None:
        print("no corpus found")
        return 0
    templates = [case.template for case in read_jsonl(root / "skins.jsonl")]

    theirs = compile_time(templates)
    counts = backend.install()
    ours = compile_time(templates)
    backend.uninstall()
    factors = {"compile": theirs / ours}
    print("%-22s %9s %9s %8s" % ("", "ct3", "generator", "factor"))
    print("%-22s %9.3f %9.3f %7.2fx   %s"
          % ("compile %d skins" % len(templates), theirs, ours,
             factors["compile"], counts))

    worst = None
    for name, source in SHAPES.items():
        by_ct3 = compiled(source)
        counts = backend.install()
        by_ct4 = compiled(source)
        backend.uninstall()
        if not counts.taken:
            print("%-22s  refused by the generator" % ("render " + name))
            continue
        a, b = render_time(by_ct3), render_time(by_ct4)
        factor = a / b
        worst = factor if worst is None else min(worst, factor)
        print("%-22s %9.3f %9.3f %7.2fx"
              % ("render " + name, a, b, factor))
    if worst is not None:
        factors["render"] = worst

    if "--check" not in sys.argv:
        return 0
    below = [(name, factors[name], FLOOR[name])
             for name in sorted(factors) if factors[name] < FLOOR[name]]
    for name, factor, floor in below:
        print("  %s is %.2fx, below %.2fx" % (name, factor, floor))
    return 1 if below else 0


if __name__ == "__main__":
    raise SystemExit(main())
