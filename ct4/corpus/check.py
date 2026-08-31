"""Den Korpus gegen eine Cheetah-Implementierung pruefen.

Jeder Fall wird erzeugt und Byte fuer Byte mit dem verglichen, was
abgelegt ist. Ein Unterschied ist ein Befund, egal wie klein: der Korpus
haelt fest, was ct3 tut, und ct4 hat das zu treffen, solange es im
Textmodus laeuft.

Der Korpus soll wachsen, bis er weh tut. Deshalb laeuft die Pruefung von
Anfang an auf allen Kernen. Ein Fall haengt von keinem anderen ab, das
ist die ganze Voraussetzung dafuer.
"""

from __future__ import annotations

import difflib
import multiprocessing
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ct4.corpus import namespaces
from ct4.corpus.case import COMPILE, Case, decode, read_jsonl

# Zeilen im erzeugten Modul, die von Lauf zu Lauf oder von Version zu
# Version wechseln. Zeitstempel schaltet der Compiler auf Wunsch selbst
# ab; die beiden Versionszeilen bleiben und wuerden jeden Vergleich
# zwischen zwei Cheetah-Staenden scheitern lassen, ohne etwas ueber die
# Vorlage zu sagen.
VOLATILE_LINES = ("__CHEETAH_version__", "__CHEETAH_versionTuple__")

# Unter diesem Umfang kostet das Starten der Arbeitsprozesse mehr, als
# die Verteilung einbringt.
PARALLEL_THRESHOLD = 2000

# Die Faelle des Arbeitsprozesses. Er laedt sie selbst von der Platte,
# statt sie geschickt zu bekommen: ein Fall traegt eine ganze Vorlage
# und deren erwartete Ausgabe, und den Korpus an jeden Arbeiter zu
# pickeln kostet mehr als das Pruefen. Gemessen auf 24 Kernen war die
# Fassung mit Versand langsamer als der serielle Lauf.
_worker_cases: Sequence[Case] = ()


@dataclass(frozen=True)
class Mismatch:
    """Ein Fall, der anders herauskam als abgelegt.

    ``error`` traegt die Ausnahme, wenn das Erzeugen gar nicht so weit
    kam. Dann ist ``actual`` leer.
    """

    case: Case
    actual: str
    error: str = ""

    def diff(self, context: int = 2) -> str:
        if self.error:
            return self.error
        return "".join(difflib.unified_diff(
            self.case.expected.splitlines(keepends=True),
            self.actual.splitlines(keepends=True),
            fromfile="erwartet", tofile="erhalten", n=context))


def normalize_code(code: str) -> str:
    """Entfernt aus erzeugtem Modulcode, was nichts ueber die Vorlage sagt."""
    return "\n".join(
        line for line in code.splitlines()
        if not line.startswith(VOLATILE_LINES))


def compile_code(case: Case) -> str:
    """Uebersetzt einen Fall und gibt den erzeugten Modulcode zurueck.

    Der Weg ueber ``ModuleCompiler`` statt ueber ``Template.compile``
    ist Absicht: hier soll nichts ausgefuehrt und nichts zwischen-
    gespeichert werden, es geht allein um den erzeugten Text.
    """
    from Cheetah.Compiler import ModuleCompiler

    settings = dict(decode(case.settings))
    settings["addTimestampsToCompilerOutput"] = False
    compiler = ModuleCompiler(
        case.template,
        moduleName="ct4_corpus",
        mainClassName="ct4_corpus",
        settings=settings)
    return normalize_code(str(compiler))


def render(case: Case) -> str:
    """Rendert einen Fall mit der gerade geladenen Implementierung."""
    from Cheetah.Template import Template

    template_class = Template.compile(
        source=case.template,
        compilerSettings=decode(case.settings),
        **decode(case.compile_kwargs))
    template = template_class(searchList=namespaces.build(case))
    try:
        return template.respond()
    finally:
        template.shutdown()


def produce(case: Case) -> str:
    """Erzeugt, was bei diesem Fall verglichen wird."""
    if case.kind == COMPILE:
        return compile_code(case)
    return render(case)


def compare(case: Case) -> Mismatch | None:
    """Prueft einen Fall. Gibt None zurueck, wenn er stimmt."""
    try:
        actual = produce(case)
    except Exception as exc:                            # noqa: BLE001
        return Mismatch(case, "", "%s: %s" % (type(exc).__name__, exc))
    if actual != case.expected:
        return Mismatch(case, actual)
    return None


def check(cases: Iterable[Case]) -> tuple[int, list[Mismatch]]:
    """Prueft die Faelle im eigenen Prozess."""
    found = [compare(case) for case in cases]
    return len(found), [m for m in found if m is not None]


def check_files(paths: Sequence[Path],
                jobs: int = 0) -> tuple[int, list[Mismatch]]:
    """Prueft die Faelle der angegebenen Dateien, verteilt auf Kerne.

    ``jobs`` ist die Zahl der Arbeitsprozesse, 0 heisst alle und 1 heisst
    im eigenen Prozess. Die Reihenfolge der Abweichungen folgt der
    Reihenfolge der Faelle, auch verteilt: sonst wechselte die Ausgabe
    zwischen zwei gleichen Laeufen, und ein Bericht liesse sich nicht
    vergleichen.
    """
    cases = load(paths)
    if jobs == 0:
        jobs = default_jobs()
    if not use_pool(len(cases), jobs):
        return check(cases)

    # Grosse Bloecke, weil jeder Auftrag nur eine Zahl kostet und jeder
    # Wechsel den Uebersetzungs-Zwischenspeicher des Arbeiters kaelter
    # macht.
    chunk = max(1, len(cases) // (jobs * 4))
    context = multiprocessing.get_context()
    arguments = (_selected_impl(), [str(path) for path in paths])
    with context.Pool(jobs, _init_worker, arguments) as pool:
        found = pool.map(_compare_index, range(len(cases)), chunk)
    return len(cases), [m for m in found if m is not None]


def use_pool(count: int, jobs: int) -> bool:
    """Ob sich das Verteilen bei dieser Menge lohnt.

    Steht als eigene Funktion da, weil sich die Entscheidung sonst nicht
    pruefen laesst: ob ein Lauf verteilt war, sieht man einem Ergebnis
    nicht an, nur seiner Dauer.
    """
    return jobs != 1 and count >= PARALLEL_THRESHOLD


def default_jobs() -> int:
    """Wie viele Arbeitsprozesse ohne ausdrueckliche Angabe laufen.

    ``os.process_cpu_count`` beruecksichtigt die Zuteilung an den
    Prozess, nicht nur die Kerne der Maschine. Genau das braucht ein
    Container, dem ``--cpus`` weniger zugeteilt wurde als die Maschine
    hat. Vor Python 3.13 gibt es die Funktion nicht.
    """
    counter = getattr(os, "process_cpu_count", os.cpu_count)
    return counter() or 1


def _selected_impl() -> str:
    """Woher das gerade geladene Cheetah kommt, als Wort fuer ct4.impl."""
    from ct4 import impl

    import Cheetah
    package = os.path.dirname(os.path.abspath(Cheetah.__file__))
    if os.path.dirname(package) == impl.REPO_ROOT:
        return impl.FORK
    return impl.INSTALLED


def _init_worker(impl_name: str, paths: Sequence[str]) -> None:
    """Richtet einen Arbeitsprozess ein.

    Unter ``fork`` ist Cheetah schon geladen und die Wahl steht; unter
    ``spawn`` faellt sie hier. Beide Faelle muessen gehen, weil Linux
    und Windows verschiedene Startarten haben.
    """
    global _worker_cases

    from ct4 import impl

    if "Cheetah" not in sys.modules:
        impl.select(impl_name)
    _worker_cases = load([Path(path) for path in paths])


def _compare_index(index: int) -> Mismatch | None:
    return compare(_worker_cases[index])


def load(paths: Iterable[Path]) -> list[Case]:
    """Liest alle angegebenen Korpusdateien in Dateireihenfolge."""
    cases: list[Case] = []
    for path in paths:
        cases.extend(read_jsonl(path))
    return cases
