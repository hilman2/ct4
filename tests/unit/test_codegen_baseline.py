"""The Python the generator writes in text mode, frozen.

Nothing else in the repo holds it. ``corpus/skins.jsonl`` records the
module code of the ct3-derived compiler and not of this generator, and
the render tests next door compare output, so a change that writes
different Python and the same bytes walks through both of them
untouched.

That is the assertion ``rawExpr`` went a working day without: the
keyword was added to every placeholder write, all 2026 corpus cases
stayed green because the default filter ignores what it is not asked
about, and only weewx's own filter could tell the difference. Markup
mode reaches the same two statements, so the gap gets closed before it
is walked through a second time.

The hashes are taken over ``ast.unparse`` output and are therefore tied
to the interpreter that wrote them. The baseline records which one that
was, and the failure says so: every line moving at once means the
interpreter moved, one line moving means the generator did.

Regenerating is a decision, not a repair. Do it only when the change to
the generated code is intended and has been read. The file is what
``_generated()`` below returns, sorted by case id and written as
``id<TAB>digest``, with the interpreter recorded on a ``## python``
line; it has to be produced inside the test image, because the digests
are of what that Python's unparser writes and of what the fork's own
Cheetah lexes.
"""

from __future__ import annotations

import hashlib
import platform
import sys
import time
from pathlib import Path

import pytest

from ct4.lang import codegen, tree

BASELINE = (Path(__file__).resolve().parents[1] / "data"
            / "codegen-text-baseline.tsv")

# The line in the baseline that names the interpreter it was written by.
VERSION_MARK = "## python\t"


def _recorded():
    """The frozen hashes by case id, and the interpreter that made them."""
    rows = {}
    version = ""
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        if line.startswith(VERSION_MARK):
            version = line[len(VERSION_MARK):].strip()
        elif line.strip() and not line.startswith("##"):
            case_id, digest = line.split("\t")
            rows[case_id] = digest
    return rows, version


def _corpus_templates():
    """Every corpus template, with the harness left as it was found.

    tests/fuzz/harness.py freezes ``time.time`` at import, so that a
    template reading the clock renders the same twice. Nothing is
    rendered here, and a frozen clock left standing would reach every
    other test that runs in this worker afterwards.
    """
    fuzz = str(Path(__file__).resolve().parents[1] / "fuzz")
    if fuzz not in sys.path:
        sys.path.insert(0, fuzz)
    real = time.time
    try:
        import harness
    finally:
        time.time = real
    return harness.corpus_templates()


def _generated():
    """A hash of the generated module for every template that is taken.

    The refusals are passed over exactly as the baseline script passed
    over them, so that the two sets of keys mean the same thing: a
    template that stops being accepted disappears from this side and
    the comparison of keys below is what reports it.
    """
    rows = {}
    for case_id, source in _corpus_templates():
        try:
            made = codegen.generate(source)
        except (codegen.Unsupported, tree.StructureError):
            continue
        except Exception:                                   # noqa: BLE001
            continue
        rows[case_id] = hashlib.sha256(
            made.code.encode("utf-8")).hexdigest()
    return rows


def test_text_mode_writes_the_python_it_wrote_before():
    want, version = _recorded()
    got = _generated()
    if not got:
        pytest.skip("the corpus is not mounted")
    here = platform.python_version()
    note = "" if version == here else (
        "; the baseline was written by Python %s and this is %s, so a "
        "whole-file mismatch is the unparser and not the generator"
        % (version, here))
    moved = sorted(case_id for case_id in want.keys() & got.keys()
                   if want[case_id] != got[case_id])
    assert not moved, (
        "text mode moved: %d of %d templates generate different Python, "
        "first %s%s" % (len(moved), len(want), moved[:5], note))
    assert sorted(got) == sorted(want), (
        "the generator now takes a different set of templates: %d gained, "
        "%d lost" % (len(got.keys() - want.keys()),
                     len(want.keys() - got.keys())))
