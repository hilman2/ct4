"""Korpusfaelle aus der Testsuite von ct3 ernten.

Jeder Ausgabetest von ct3 laeuft durch ``OutputTest.verify()``. Statt die
Testquellen zu zerlegen, haengt sich der Ernter dort ein und schreibt
jeden Fall mit, der durchlaeuft. Was ct3 selbst als richtig behauptet,
wird damit zum Massstab, und zwar in genau der Form, in der ct3 es
behauptet.

Aufgezeichnet wird erst nach dem Durchlauf. Ein Fall, der unter der
laufenden Implementierung scheitert, kommt nicht in den Korpus. Er waere
kein Massstab, sondern ein offener Befund.

Und er muss sich aus seiner eigenen Zeile rekonstruieren lassen. Einige
ct3-Tests haengen an etwas ausserhalb der Vorlage: ``Backslashes`` und
``IncludeDirective`` legen in ``setUp`` eine Datei an, die die Vorlage
per ``#include`` liest, ``CGI`` setzt ``os.environ`` in der Testmethode.
Solche Faelle wuerden im Pruefstand die Umgebung mitmessen statt die
Vorlage. Der Ernter erkennt sie, indem er jeden Fall sofort ein zweites
Mal rendert, in einem leeren Arbeitsverzeichnis. Was dort nicht dasselbe
liefert, faellt raus und wird gezaehlt.
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

# Testmodule, deren Faelle durch OutputTest.verify laufen. CheetahWrapper
# fehlt mit Absicht: es startet die Kommandozeile in einem Unterprozess
# und liefert keine Vorlage, die sich vergleichen liesse.
MODULES = ("SyntaxAndOutput", "Regressions", "Unicode")


class Report:
    """Was bei einer Ernte herauskam.

    ``skipped`` zaehlt nach Grund, damit sichtbar bleibt, welcher Anteil
    der Testsuite noch nicht im Korpus steckt und warum.
    """

    def __init__(self) -> None:
        self.cases: list[Case] = []
        self.skipped: Counter[str] = Counter()
        self.tests_run = 0
        self.tests_failed = 0

    def summary(self) -> str:
        lines = [
            "Tests gelaufen : %d" % self.tests_run,
            "davon gefallen : %d" % self.tests_failed,
            "Faelle geerntet: %d" % len(self.cases),
        ]
        for reason, count in sorted(self.skipped.items()):
            lines.append("uebersprungen  : %-24s %d" % (reason, count))
        return "\n".join(lines)


def _effective_eols(test: Any, text: str, convert: Any, marker: Any) -> str:
    """Wendet die Zeilenende-Ersetzung an, die verify vornimmt.

    ct3 erzeugt zu jeder Testklasse Varianten fuer LF, CRLF und CR und
    stellt die Vorlage vor dem Vergleich um. ``verify`` gibt die
    umgestellten Werte nicht heraus, deshalb steht die Regel hier ein
    zweites Mal. Massgeblich bleibt ``OutputTest.verify``. Laufen beide
    auseinander, faellt es sofort auf: der geerntete Fall passt dann
    nicht mehr zu seiner erwarteten Ausgabe, und ``corpus check`` meldet
    ihn.
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
    """Ob der Fall auch ohne die Testumgebung dieselbe Ausgabe liefert.

    Gerendert wird in einem leeren Arbeitsverzeichnis. Damit fallen die
    Faelle auf, die eine Datei neben sich brauchen. Umgebungsvariablen
    bleiben stehen; die mitten im laufenden Testlauf zu verstellen waere
    gefaehrlicher als der Gewinn, dafuer gibt es den Vergleich gegen
    ``baseline_environ`` in ``_record``.

    Das Verzeichnis wird nicht hier angelegt und geloescht: unter
    Windows laesst sich ein Verzeichnis nicht entfernen, solange es das
    Arbeitsverzeichnis des Prozesses ist. Es lebt deshalb ueber den
    ganzen Erntelauf.
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
    """Bestimmt, wie sich der Kontext eines Falls ablegen laesst.

    Gibt den Namen und den einzubettenden Kontext zurueck. Ein leerer
    Name heisst: nicht ablegbar, der Fall faellt raus.
    """
    search_list = test.searchList() or test._searchList
    if len(search_list) == 1 and search_list[0] is default_ns:
        return CT3_DEFAULT, []
    if is_jsonable(search_list):
        return INLINE, list(search_list)
    return "", []


def harvest() -> Report:
    """Laesst die ct3-Testsuite laufen und sammelt ihre Ausgabefaelle."""
    from Cheetah.Tests import SyntaxAndOutput as syntax

    report = Report()
    seen: Counter[str] = Counter()
    original = syntax.OutputTest.verify
    # Wie die Umgebung aussieht, bevor irgendein Test sie anfasst. Wer
    # davon abweicht, hat os.environ gesetzt und rendert etwas, das der
    # Pruefstand spaeter nicht wiederherstellen kann.
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
    """Legt einen durchgelaufenen Vergleich als Korpusfall ab."""
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
        report.skipped["nicht reproduzierbar"] += 1
        return
    report.cases.append(case)


def _run(syntax: Any) -> unittest.TestResult:
    """Laesst die Testmodule laufen, ohne ihre Ausgabe durchzureichen."""
    loader = unittest.defaultTestLoader
    suites = [loader.loadTestsFromModule(syntax)]
    for name in MODULES[1:]:
        module = __import__("Cheetah.Tests." + name, fromlist=[name])
        suites.append(loader.loadTestsFromModule(module))
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    return runner.run(unittest.TestSuite(suites))
