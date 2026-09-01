"""Break the generator on purpose and see who notices.

Every other run here asks whether the code is right. This one asks
whether the checking is any good, which is a different question and
the one that decides how fast the work goes. A rule nobody would
notice being broken is a rule nobody is holding, and the next change
to it lands blind.

So each sabotage below is a small, plausible wrong answer: a whitespace
drop that does nothing, a line ending on the wrong side of a block, a
filter that is handed no rawExpr. It is switched on, the instruments
run, and the first one that sees it is written down.

    python tests/fuzz/sabotage.py          the cheapest witness for each
    python tests/fuzz/sabotage.py --full   every witness for each

**A sabotage nobody sees is the finding.** It means that rule is held
by nothing, and the entry says so.

Not part of the default Docker run: it compiles the corpus once per
sabotage. Its numbers are read when the checking is being weighed, the
way ct4/corpus/weaken.py is read when the semantics are.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import hostile                                                 # noqa: E402
import perturb                                                 # noqa: E402
import whitespace                                              # noqa: E402
from harness import (BYTES, corpus_render_wrong, corpus_templates,  # noqa: E402
                     disagreements, severity)

from ct4.lang import codegen, lex, tree                        # noqa: E402


# -- The sabotages ---------------------------------------------------
#
# Each one replaces exactly one function of the generator, so that what
# it measures is one rule and not a tangle. Where a rule has no such
# seam it is not in this list, and that is a gap in the list rather
# than a claim about the rule.

def _drop_indent_noop(out: list[Any]) -> None:
    """Whitespace before a directive is never taken away."""


def _drop_indent_walks_back(out: list[Any]) -> None:
    """The loop this had until the one-chunk rule was measured.

    ct3 truncates one pending chunk and stops. Walking further back is
    what lost the two blanks of "  #encoding x" before "  #import os".
    """
    while out:
        kind, text = out[-1]
        if kind != codegen.TEXT_PIECE:
            return
        match = lex.EOL.search(text[::-1])
        if match is not None:
            keep = len(text) - match.start()
            if text[keep:].strip():
                return
            out[-1] = (codegen.TEXT_PIECE, text[:keep])
            return
        if text.strip():
            return
        out.pop()


# Held before anything is replaced, so a sabotage can still reach the
# real one. Taking it by name inside the sabotage would find the
# sabotage itself.
REAL_PLACEHOLDER_VALUE = codegen._placeholder_value


def _no_raw_expr(lookup: ast.expr, raw: str = "") -> list[ast.stmt]:
    """The filter is handed the value and nothing else."""
    return REAL_PLACEHOLDER_VALUE(lookup, "")


def _region_line_keeps_everything(node: tree.Node, source: str,
                                  out: list[Any]) -> str:
    """A block tag decides nothing about the line it stands on."""
    return ""


def _definition_line_always_drops(node: tree.Node, source: str,
                                  out: list[Any]) -> str:
    """The short form of #block drops its indent like #def does."""
    short = not any(child.kind == lex.DIRECTIVE and child.name == "end"
                    for child in node.children)
    if not short:
        return codegen._eat_region_line(node, source, out)
    if codegen._line_is_clear(source, node.tokens[0].start):
        codegen._drop_indent(out)
    return ""


def _swallow_nothing(nodes: Any, index: int, out: list[Any]) -> int:
    """#slurp leaves the rest of its line where it is."""
    return index


def _block_comment_spans_only(node: tree.Node, source: str,
                              out: list[Any]) -> bool:
    """The indent goes only where the comment ran past its first line.

    The reading this had before ct3's own atEnd guard was found.
    """
    clear = codegen._line_is_clear(source, node.tokens[0].start)
    if not clear:
        return False
    if lex.EOL.search(node.text()) is not None:
        codegen._drop_indent(out)
    return clear


def _take_every_setting(settings: Any) -> None:
    """A compiler setting is ignored instead of refused."""


def _preamble_guard_off(module: ast.Module) -> None:
    """A template may reach a name only one engine's module carries."""


def _branch_pieces_plain(node: tree.Node, source: str, hoisted: list[Any],
                         methods: list[Any], leading: str,
                         escaped: list[str] | None,
                         at: Any = codegen.BRANCHES) -> list[Any]:
    """A branch tag decides nothing about its own line.

    Which left the two blanks of "  #else" in the output for as long as
    the corpus was the only ruler.
    """
    built = []
    carry = leading
    for directive, children in codegen._branches(node, at):
        pieces = codegen._pieces_of(children, source, hoisted, methods,
                                    escaped=escaped)
        if carry:
            pieces.insert(0, (codegen.TEXT_PIECE, carry))
            carry = ""
        built.append((directive, pieces))
    return built


SABOTAGES: dict[str, tuple[str, Callable[..., Any]]] = {
    "drop_indent_noop": ("_drop_indent", _drop_indent_noop),
    "drop_indent_walks_back": ("_drop_indent", _drop_indent_walks_back),
    "no_raw_expr": ("_placeholder_value", _no_raw_expr),
    "block_tag_ignores_its_line": ("_eat_region_line",
                                   _region_line_keeps_everything),
    "block_short_drops_indent": ("_definition_line",
                                 _definition_line_always_drops),
    "slurp_keeps_its_line": ("_swallow_line", _swallow_nothing),
    "block_comment_spans_only": ("_block_comment", _block_comment_spans_only),
    "settings_ignored": ("_refuse_settings", _take_every_setting),
    "preamble_guard_off": ("_refuse_preamble_names", _preamble_guard_off),
    "branch_tag_ignores_its_line": ("_branch_pieces", _branch_pieces_plain),
}


# -- The witnesses ---------------------------------------------------
#
# Cheapest first, because the run stops at the first one that sees the
# sabotage and most of them die at the corpus.

def _unit() -> int:
    """The generator's own test file, run in this process.

    In this process on purpose: the sabotage is an attribute of an
    imported module, and a subprocess would load a clean one. The tests
    call codegen.render, which looks its helpers up as module globals
    at call time, so the sabotage reaches them.

    Last of the witnesses. It catches everything, because a case was
    written for each of these rules as it was found, and a run that
    stopped here would say nothing about the instruments.
    """
    import contextlib
    import io

    import pytest

    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        code = pytest.main(["-q", "-p", "no:cacheprovider",
                            "--no-header", "-x",
                            str(Path(__file__).parents[1] / "unit"
                                / "test_codegen.py")])
    return 1 if int(code) else 0


def _corpus() -> int:
    return corpus_render_wrong()


def _whitespace() -> int:
    total = 0
    for source in whitespace.sources():
        if not codegen.supports(source):
            continue
        if whitespace.by_cheetah(source) != whitespace.by_generator(source):
            total += 1
    return total


def _hostile() -> int:
    _, _, found = disagreements(iter(corpus_templates()), hostile.build)
    return len(found)


def _perturb() -> int:
    _, _, found = disagreements(perturb.sources(), hostile.build)
    return sum(1 for _, _, got, want in found
               if severity(got, want) == BYTES)


# The four differential runs first and the unit file last, and the
# order is the report. A rule one of the four holds is held by a
# machine that generates its own cases and keeps holding it when the
# code around it moves. A rule only the unit file holds is held by one
# hand-written case, which is a fine place to be and worth knowing:
# delete that case and nothing is left.
WITNESSES: list[tuple[str, Callable[[], int]]] = [
    ("corpus", _corpus),
    ("whitespace", _whitespace),
    ("hostile", _hostile),
    ("perturb", _perturb),
    ("unit", _unit),
]


def main() -> int:
    full = "--full" in sys.argv
    survivors = []
    print("%-30s %s" % ("sabotage", "who sees it"))
    for name, (attribute, replacement) in SABOTAGES.items():
        original = getattr(codegen, attribute)
        setattr(codegen, attribute, replacement)
        try:
            seen = []
            for witness, run in WITNESSES:
                try:
                    count = run()
                except Exception as error:                     # noqa: BLE001
                    # A sabotage that makes the generator crash is seen
                    # by whoever ran into it, which is an answer too.
                    seen.append("%s(crash %s)" % (witness,
                                                  type(error).__name__))
                    if not full:
                        break
                    continue
                if count:
                    seen.append("%s(%d)" % (witness, count))
                    if not full:
                        break
        finally:
            setattr(codegen, attribute, original)
        if not seen:
            survivors.append(name)
        print("%-30s %s" % (name, ", ".join(seen) or "NOBODY"))
    if survivors:
        print()
        print("%d sabotage(s) nobody sees: %s"
              % (len(survivors), ", ".join(survivors)))
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
