"""Harvest corpus cases from the test suite of ct3.

Every output test of ct3 runs through ``OutputTest.verify()``. Instead
of taking the test sources apart, the harvester hooks in there and
writes down every case that passes. What ct3 itself claims to be correct
thereby becomes the yardstick, and in exactly the form in which ct3
claims it.

Recording happens only after the run. A case that fails under the
running implementation does not get into the corpus. It would not be a
yardstick but an open finding.

And it has to be reconstructible from its own line. Some ct3 tests hang
on something outside the template: ``Backslashes`` and
``IncludeDirective`` create a file in ``setUp`` that the template reads
via ``#include``, ``CGI`` sets ``os.environ`` in the test method. In the
test bench such cases would measure the environment along with the
template. The harvester spots them by rendering every case a second time
right away, in an empty working directory. Whatever does not deliver the
same there drops out and is counted.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from collections import Counter
from typing import Any

from ct4.corpus.case import (CT3_DEFAULT, INLINE, Case, encode,
                             is_jsonable)

# Test modules whose cases run through OutputTest.verify. CheetahWrapper
# is missing on purpose: it starts the command line in a subprocess and
# delivers no template that could be compared.
MODULES = ("SyntaxAndOutput", "Regressions", "Unicode")


class Report:
    """What came out of a harvest.

    ``skipped`` counts by reason, so that it stays visible which share of
    the test suite is not in the corpus yet, and why.
    """

    def __init__(self) -> None:
        self.cases: list[Case] = []
        self.skipped: Counter[str] = Counter()
        self.tests_run = 0
        self.tests_failed = 0

    def summary(self) -> str:
        lines = [
            "tests run      : %d" % self.tests_run,
            "of which failed: %d" % self.tests_failed,
            "cases harvested: %d" % len(self.cases),
        ]
        for reason, count in sorted(self.skipped.items()):
            lines.append("skipped        : %-24s %d" % (reason, count))
        return "\n".join(lines)


def _effective_eols(test: Any, text: str, convert: Any, marker: Any) -> str:
    """Applies the line-ending replacement that verify performs.

    ct3 creates variants of every test class for LF, CRLF and CR and
    converts the template before comparing. ``verify`` does not hand out
    the converted values, which is why the rule stands here a second
    time. ``OutputTest.verify`` remains authoritative. If the two drift
    apart, it shows up at once: the harvested case then no longer matches
    its expected output, and ``corpus check`` reports it.
    """
    replacement = test._EOLreplacement
    if not replacement:
        return text
    if convert is marker:
        convert = test.convertEOLs
    if not convert:
        return text
    return text.replace("\n", replacement)


def _reproduces(case: Case, empty_dir: str) -> bool:
    """Whether the case delivers the same output without the test setup.

    Rendering happens in an empty working directory. That exposes the
    cases which need a file next to them. Environment variables are left
    alone; changing them in the middle of a running test run would be
    more dangerous than the gain, and for that there is the comparison
    against ``baseline_environ`` in ``_record``.

    The directory is not created and deleted here: under Windows a
    directory cannot be removed as long as it is the working directory
    of the process. It therefore lives for the whole harvest run.
    """
    from ct4.corpus.check import produce

    previous = os.getcwd()
    try:
        os.chdir(empty_dir)
        return produce(case) == case.expected
    except Exception:                                   # noqa: BLE001
        return False
    finally:
        os.chdir(previous)


def _namespace_of(test: Any, default_ns: Any) -> tuple[str, list[Any]]:
    """Determines how the context of a case can be stored.

    Returns the name and the context to embed. An empty name means: not
    storable, the case drops out.
    """
    search_list = test.searchList() or test._searchList
    if len(search_list) == 1 and search_list[0] is default_ns:
        return CT3_DEFAULT, []
    if is_jsonable(search_list):
        return INLINE, list(search_list)
    return "", []


def harvest() -> Report:
    """Runs the ct3 test suite and collects its output cases."""
    from Cheetah.Tests import SyntaxAndOutput as syntax

    report = Report()
    seen: Counter[str] = Counter()
    original = syntax.OutputTest.verify
    # What the environment looks like before any test touches it. A test
    # that deviates from this has set os.environ and renders something
    # the test bench cannot reproduce later.
    baseline_environ = dict(os.environ)
    empty_dir = tempfile.mkdtemp(prefix="ct4-corpus-")

    def recording_verify(self: Any, input: str,            # noqa: A002
                         expectedOutput: str,
                         inputEncoding: Any = None,
                         outputEncoding: Any = None,
                         convertEOLs: Any = syntax.Unspecified) -> None:
        original(self, input, expectedOutput, inputEncoding,
                 outputEncoding, convertEOLs)
        _record(self, input, expectedOutput, convertEOLs,
                syntax, report, seen, baseline_environ, empty_dir)

    syntax.OutputTest.verify = recording_verify
    try:
        syntax.install_eols()
        result = _run(syntax)
    finally:
        syntax.OutputTest.verify = original
        shutil.rmtree(empty_dir, ignore_errors=True)

    report.tests_run = result.testsRun
    report.tests_failed = len(result.failures) + len(result.errors)
    return report


def _record(test: Any, source: str, expected: str, convert: Any,
            syntax: Any, report: Report, seen: Counter[str],
            baseline_environ: dict[str, str], empty_dir: str) -> None:
    """Stores a comparison that passed as a corpus case."""
    settings = encode(test._getCompilerSettings())
    compile_kwargs = encode(test._extraCompileKwArgs or {})
    if not is_jsonable(settings):
        report.skipped["compilerSettings"] += 1
        return
    if not is_jsonable(compile_kwargs):
        report.skipped["extraCompileKwArgs"] += 1
        return

    namespace, context = _namespace_of(test, syntax.defaultTestNameSpace)
    if not namespace:
        report.skipped["searchList"] += 1
        return

    if dict(os.environ) != baseline_environ:
        report.skipped["os.environ"] += 1
        return

    marker = syntax.Unspecified
    test_id = test.id()
    seen[test_id] += 1
    case = Case(
        id="%s#%d" % (test_id, seen[test_id]),
        template=_effective_eols(test, source, convert, marker),
        expected=_effective_eols(test, expected, convert, marker),
        namespace=namespace,
        context=context,
        settings=settings,
        compile_kwargs=compile_kwargs,
        origin=test.__class__.__module__,
    )
    if not _reproduces(case, empty_dir):
        report.skipped["not reproducible"] += 1
        return
    report.cases.append(case)


def _run(syntax: Any) -> unittest.TestResult:
    """Runs the test modules without passing their output through."""
    loader = unittest.defaultTestLoader
    suites = [loader.loadTestsFromModule(syntax)]
    for name in MODULES[1:]:
        module = __import__("Cheetah.Tests." + name, fromlist=[name])
        suites.append(loader.loadTestsFromModule(module))
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    return runner.run(unittest.TestSuite(suites))
