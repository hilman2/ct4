"""The checker: what it reports and what it overlooks."""

from __future__ import annotations

import pytest

from ct4.corpus import check as checker
from ct4.corpus.case import COMPILE, Case, write_jsonl
from ct4.corpus.check import (check, check_files, compare, compile_code,
                              normalize_code, produce)


@pytest.fixture
def force_distributed(monkeypatch):
    """Disables the threshold above which the work is distributed.

    Without this the parallelism tests quietly ran serially and still
    claimed to be checking it."""
    monkeypatch.setattr(checker, "PARALLEL_THRESHOLD", 0)


MATCH = Case(id="match", template="$aStr", expected="blarg")


def test_version_lines_are_dropped():
    code = ("__CHEETAH_version__ = '3.4.0'\n"
            "__CHEETAH_versionTuple__ = (3, 4, 0)\n"
            "write('hello')\n")
    assert normalize_code(code) == "write('hello')"


def test_version_line_inside_the_text_stays():
    # Only line beginnings count. A template that prints the name as
    # text must not be silently truncated.
    code = "write('__CHEETAH_version__ = x')"
    assert normalize_code(code) == code


def test_a_match_reports_nothing():
    assert compare(MATCH) is None


def test_a_mismatch_is_reported():
    case = Case(id="off", template="$aStr", expected="something else")
    mismatch = compare(case)
    assert mismatch is not None
    assert mismatch.actual == "blarg"
    assert "blarg" in mismatch.diff()


def test_an_exception_becomes_a_mismatch():
    case = Case(id="broken", template="#for x in\n", expected="")
    mismatch = compare(case)
    assert mismatch is not None
    assert mismatch.error
    assert mismatch.actual == ""


def test_compile_case_compares_the_module_code():
    case = Case(id="c", template="$aStr", expected="", kind=COMPILE)
    code = produce(case)
    assert "def respond" in code
    assert code == compile_code(case)


def test_compile_case_is_the_same_between_runs():
    # Without this property the corpus would be worthless: the
    # generated module code otherwise carries a timestamp.
    case = Case(id="c", template="$aStr", expected="", kind=COMPILE)
    assert compile_code(case) == compile_code(case)


def test_distributed_and_serial_reach_the_same_result(
        tmp_path, force_distributed):
    # The property the whole parallelisation hinges on.
    cases = []
    for index in range(500):
        expected = "blarg" if index % 7 else "wrong"
        cases.append(Case(id="c%d" % index, template="$aStr",
                          expected=expected))
    path = tmp_path / "corpus.jsonl"
    write_jsonl(cases, path)

    count_serial, serial = check(cases)
    count_distributed, distributed = check_files([path], jobs=4)

    assert count_serial == count_distributed == 500
    assert [m.case.id for m in serial] == [m.case.id for m in distributed]


def test_mismatch_order_follows_the_corpus(tmp_path, force_distributed):
    cases = [Case(id="c%d" % i, template="$aStr", expected="wrong")
             for i in range(300)]
    path = tmp_path / "corpus.jsonl"
    write_jsonl(cases, path)
    _, mismatches = check_files([path], jobs=4)
    assert [m.case.id for m in mismatches] == [c.id for c in cases]


def test_a_small_corpus_runs_serially():
    # Below the threshold, starting the processes costs more than
    # distributing the work gains.
    assert not checker.use_pool(10, jobs=8)


def test_a_large_corpus_is_distributed():
    assert checker.use_pool(checker.PARALLEL_THRESHOLD, jobs=8)


def test_one_process_stays_one_process():
    # -j1 must stay serial however large the corpus is. Otherwise
    # there is no way to hunt a bug without process boundaries.
    assert not checker.use_pool(1_000_000, jobs=1)
