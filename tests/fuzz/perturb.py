"""Every corpus template, moved off the clean shapes it was written in.

The corpus is 2026 real templates and every one of them was written by
somebody who puts directives on lines of their own. That is a habit,
not a rule, and the compiler has a rule for every other case. So the
templates that show whether those rules are right are exactly the ones
nobody writes.

tests/fuzz/whitespace.py builds such templates out of fragments, and
its blind spot is the fragment list: it writes its #except at column
zero, so the indent before a branch tag went wrong for weeks without it
noticing. This one has no fragment list. It takes real templates, all
of their content and all of their nesting, and moves the directives
around inside them:

    indent      two blanks before every directive line
    dirty       an L before every directive line, so no line is clear
    join_ends   every #end pulled onto the line above it
    crlf        every line ending doubled
    cr          every line ending an old Mac one
    bare        the last line ending taken away

Six shapes each, rendered against the context from hostile.py so that
the 390 skin templates render at all, and compared byte for byte.

    python tests/fuzz/perturb.py
    python tests/fuzz/perturb.py --examples

Exits 1 where anything differs.
"""

from __future__ import annotations

import collections
import re
import sys
from pathlib import Path
from typing import Callable, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (BOTH_FAIL, CT3_REFUSES,                   # noqa: E402
                     corpus_templates, disagreements, report, severity)
from hostile import build                                      # noqa: E402

# A line that opens with a directive, which is the only kind these
# transformations touch. The negative lookahead keeps the two comment
# forms and the bare slurp out of it: a "##" is not a directive name,
# and moving a "#" that ends a line changes what it means.
DIRECTIVE_LINE = re.compile(r"^([ \t]*)(#[A-Za-z@][A-Za-z0-9_@-]*)",
                            re.MULTILINE)

END_LINE = re.compile(r"(\r\n|\r|\n)[ \t]*(#end\b)")


def indent(source: str) -> str:
    """Two blanks in front of every directive."""
    return DIRECTIVE_LINE.sub(lambda m: "  " + m.group(1) + m.group(2),
                              source)


def dirty(source: str) -> str:
    """An L in front of every directive, so no line is clear.

    The whole whitespace machinery turns on isLineClearToStartToken,
    and this is the switch that flips it for a real template.
    """
    return DIRECTIVE_LINE.sub(lambda m: m.group(1) + "L" + m.group(2),
                              source)


def join_ends(source: str) -> str:
    """Every #end pulled onto the line above it.

    Which is the shape that put a line ending into every turn of a
    loop, and the corpus holds not one of them.
    """
    return END_LINE.sub(lambda m: m.group(2), source)


def crlf(source: str) -> str:
    return re.sub(r"\r\n|\r|\n", "\r\n", source)


def cr(source: str) -> str:
    return re.sub(r"\r\n|\r|\n", "\r", source)


def bare(source: str) -> str:
    """The last line ending taken away.

    ct3 asks atEnd() in four places and the answer decides three
    different rules, so a template that stops without a line ending is
    its own shape.
    """
    return re.sub(r"(\r\n|\r|\n)$", "", source)


SHAPES: dict[str, Callable[[str], str]] = {
    "indent": indent,
    "dirty": dirty,
    "join_ends": join_ends,
    "crlf": crlf,
    "cr": cr,
    "bare": bare,
}


def sources() -> Iterator[tuple[str, str]]:
    """Every template in every shape, the unchanged one left out."""
    for case_id, template in corpus_templates():
        for name, change in SHAPES.items():
            made = change(template)
            if made != template:
                yield "%s [%s]" % (case_id, name), made


# Two of the four kinds of disagreement are counted here rather than
# fixed, and the total is written down so it cannot grow quietly.
#
# Moving a directive can make a template ct3 refuses: "#break#end if"
# on one line is a ParseError there and renders here. Reproducing ct3's
# parse errors is a goal of its own and a wide one, and a caller that
# falls back on ct3 loses nothing by it, because the template was
# broken either way. What this run does promise is the other line:
# where both engines render, the bytes match.
#
# One number and not one per kind. Which of the two a broken template
# lands in depends on which exception each engine happens to raise
# first, and that moves between a run on Windows and a run in the
# image, without either engine having changed.
TOLERATED = (CT3_REFUSES, BOTH_FAIL)
# 179 until #assert, #return and the one-line #if landed. Those took 91
# more perturbed templates, and 9 of them are shapes ct3 stops parsing
# once a directive has been moved. The number is raised deliberately
# and only after checking that the byte-difference count is still zero,
# which is the line this run actually promises.
CEILING = 188


def main() -> int:
    if not corpus_templates():
        print("no corpus found")
        return 0
    seen, taken, found = disagreements(sources(), build)
    code = report("Corpus templates, directives moved around",
                  seen, taken, found,
                  examples=8 if "--examples" in sys.argv else 0,
                  tolerate=TOLERATED)
    counts = collections.Counter(severity(got, want)
                                 for _, _, got, want in found)
    total = sum(counts[kind] for kind in TOLERATED)
    if total > CEILING:
        print("  tolerated disagreements went from %d to %d"
              % (CEILING, total))
        code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
