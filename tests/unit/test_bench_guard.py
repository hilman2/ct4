"""The benchmark threshold, held against a regression.

A guard that only ever passes says nothing. These cases feed compare.py
a pair of runs it should wave through and a pair it should stop, so a
threshold that quietly stopped working shows up here rather than in a
release.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "bench"
COMPARE = BENCH / "compare.py"


def write(path, cases):
    path.write_text(json.dumps({"version": "test", "cases": cases}),
                    encoding="utf-8")
    return str(path)


def run(tmp_path, reference, fork, *args):
    before = write(tmp_path / "reference.json", reference)
    after = write(tmp_path / "fork.json", fork)
    return subprocess.run(
        [sys.executable, str(COMPARE), before, after, *args],
        capture_output=True, text=True)


# The fork is twice as fast everywhere: comfortably above every floor.
GOOD_REFERENCE = {"text: plain objects": 1.0,
                  "text: helper objects": 1.0,
                  "text: JSON by hand": 1.0,
                  "compile: plain objects": 1.0,
                  "reference: json.dumps by hand": 1.0}
GOOD_FORK = {name: 0.5 for name in GOOD_REFERENCE}


def test_a_healthy_pair_passes(tmp_path):
    result = run(tmp_path, GOOD_REFERENCE, GOOD_FORK, "--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_regression_fails(tmp_path):
    # One case slower than ct3. That is what the guard exists for.
    fork = dict(GOOD_FORK)
    fork["text: plain objects"] = 1.2
    result = run(tmp_path, GOOD_REFERENCE, fork, "--check")
    assert result.returncode == 1
    assert "text: plain objects" in result.stdout


def test_without_check_a_regression_only_gets_reported(tmp_path):
    # The plain form is for looking at, and must not fail a run.
    fork = dict(GOOD_FORK)
    fork["text: plain objects"] = 1.2
    result = run(tmp_path, GOOD_REFERENCE, fork)
    assert result.returncode == 0
    assert "below" in result.stdout


def test_a_case_the_reference_does_not_have_is_named(tmp_path):
    # The JSON mode under ct3. Leaving it out silently would make the
    # table look like ct3 had been measured on it.
    fork = dict(GOOD_FORK)
    fork["json mode: #series"] = 0.3
    result = run(tmp_path, GOOD_REFERENCE, fork, "--check")
    assert result.returncode == 0
    assert "n/a" in result.stdout


def test_every_floor_names_a_case_the_benchmark_produces(tmp_path):
    # A floor whose name has drifted from the case guards nothing, and
    # nothing would say so.
    sys.path.insert(0, str(BENCH))
    try:
        import compare
        import render
    finally:
        sys.path.pop(0)
    produced = {case[0] for case in render.cases()}
    assert set(compare.FLOOR) <= produced
