"""Check the corpus against a Cheetah implementation.

Every case is produced and compared byte for byte with what is stored.
A difference is a finding, however small: the corpus records what ct3
does, and ct4 has to match that as long as it runs in text mode.

The corpus is meant to grow until it hurts. That is why the check runs
on all cores from the start. No case depends on any other, and that is
the whole prerequisite for it.
"""

from __future__ import annotations

import difflib
import multiprocessing
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ct4.corpus import namespaces
from ct4.corpus.case import COMPILE, Case, decode, read_jsonl

# Lines in the generated module that change from run to run or from
# version to version. Timestamps the compiler switches off itself on
# request; the two version lines stay and would make every comparison
# between two Cheetah revisions fail without saying anything about the
# template.
VOLATILE_LINES = ("__CHEETAH_version__", "__CHEETAH_versionTuple__")

# The one place where ct4 deliberately generates other code than ct3:
# where the compiler bound a name itself, it starts the lookup at that
# local instead of walking the search list. The rewrite is undone here
# so that the compile cases keep comparing against ct3. Undoing it,
# rather than recording ct4's own output as the expectation, is what
# keeps the 136 third-party skins worth anything: they have no context
# to render with, so the generated code is the only evidence that ct4
# still does what ct3 did, and a baseline taken from ct4 would only
# prove that ct4 agrees with itself. Every other difference still
# shows up as a finding.
LOCAL_LOOKUP = re.compile(
    r'VFN\(\{"(\w+)":\1\},"([^"]+)",(True|False)\)')

# Below this many cases, starting the worker processes costs more than
# the distribution brings in.
PARALLEL_THRESHOLD = 2000

# The cases of the worker process. It loads them from disk itself
# instead of having them sent over: a case carries a whole template and
# its expected output, and pickling the corpus to every worker costs
# more than the checking does. Measured on 24 cores, the version that
# sent them was slower than the serial run.
_worker_cases: Sequence[Case] = ()


@dataclass(frozen=True)
class Mismatch:
    """A case that came out different from what was stored.

    ``error`` carries the exception if producing it never got that far.
    ``actual`` is then empty.
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
            fromfile="expected", tofile="actual", n=context))


def normalize_code(code: str) -> str:
    """Strips from generated code what says nothing about the template."""
    kept = "\n".join(
        line for line in code.splitlines()
        if not line.startswith(VOLATILE_LINES))
    return LOCAL_LOOKUP.sub(r'VFFSL(SL,"\2",\3)', kept)


def compile_code(case: Case) -> str:
    """Compiles a case and returns the generated module code.

    Going through ``ModuleCompiler`` instead of ``Template.compile`` is
    deliberate: nothing is meant to be executed and nothing cached here,
    this is only about the generated text.
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
    """Renders a case with the implementation currently loaded."""
    from Cheetah.Template import Template

    from ct4.fixture.filters import resolve

    template_class = Template.compile(
        source=case.template,
        compilerSettings=decode(case.settings),
        **decode(case.compile_kwargs))
    # The filter belongs to the case, not to the test bench: which
    # application renders the template decides what becomes of None and
    # of an error during conversion.
    filter_class = resolve(case.filter)
    kwargs = {"filter": filter_class} if filter_class else {}
    template = template_class(searchList=namespaces.build(case), **kwargs)
    try:
        return str(template.respond())
    finally:
        template.shutdown()


def produce(case: Case) -> str:
    """Produces whatever is compared for this case."""
    if case.kind == COMPILE:
        return compile_code(case)
    return render(case)


def compare(case: Case) -> Mismatch | None:
    """Checks one case. Returns None if it is correct."""
    try:
        actual = produce(case)
    except Exception as exc:                            # noqa: BLE001
        return Mismatch(case, "", "%s: %s" % (type(exc).__name__, exc))
    if actual != case.expected:
        return Mismatch(case, actual)
    return None


def check(cases: Iterable[Case]) -> tuple[int, list[Mismatch]]:
    """Checks the cases in the current process."""
    found = [compare(case) for case in cases]
    return len(found), [m for m in found if m is not None]


def check_files(paths: Sequence[Path],
                jobs: int = 0) -> tuple[int, list[Mismatch]]:
    """Checks the cases of the given files, spread over the cores.

    ``jobs`` is the number of worker processes, 0 means all of them and
    1 means in the current process. The order of the mismatches follows
    the order of the cases, even when spread out: otherwise the output
    would differ between two identical runs, and one report could not be
    compared against another.
    """
    cases = load(paths)
    if jobs == 0:
        jobs = default_jobs()
    if not use_pool(len(cases), jobs):
        return check(cases)

    # Large chunks, because every job costs no more than one number and
    # every switch leaves the worker's compilation cache colder.
    chunk = max(1, len(cases) // (jobs * 4))
    context = multiprocessing.get_context()
    arguments = (_selected_impl(), [str(path) for path in paths])
    with context.Pool(jobs, _init_worker, arguments) as pool:
        found = pool.map(_compare_index, range(len(cases)), chunk)
    return len(cases), [m for m in found if m is not None]


def use_pool(count: int, jobs: int) -> bool:
    """Whether distributing pays off for this many cases.

    It stands here as a function of its own, because otherwise the
    decision could not be tested: a result does not show whether a run
    was distributed, only its duration does.
    """
    return jobs != 1 and count >= PARALLEL_THRESHOLD


def default_jobs() -> int:
    """How many worker processes run without an explicit setting.

    ``os.process_cpu_count`` takes the allocation to the process into
    account, not just the cores of the machine. That is exactly what a
    container needs that was given fewer ``--cpus`` than the machine
    has. Before Python 3.13 the function does not exist.
    """
    counter = getattr(os, "process_cpu_count", os.cpu_count)
    return counter() or 1


def _selected_impl() -> str:
    """Where the loaded Cheetah comes from, as a word for ct4.impl."""
    from ct4 import impl

    import Cheetah
    package = os.path.dirname(os.path.abspath(Cheetah.__file__))
    if os.path.dirname(package) == impl.REPO_ROOT:
        return impl.FORK
    return impl.INSTALLED


def _init_worker(impl_name: str, paths: Sequence[str]) -> None:
    """Sets up a worker process.

    Under ``fork`` Cheetah is already loaded and the choice is settled;
    under ``spawn`` it is made here. Both cases have to work, because
    Linux and Windows have different start methods.
    """
    global _worker_cases

    from ct4 import impl

    if "Cheetah" not in sys.modules:
        impl.select(impl_name)
    _worker_cases = load([Path(path) for path in paths])


def _compare_index(index: int) -> Mismatch | None:
    return compare(_worker_cases[index])


def load(paths: Iterable[Path]) -> list[Case]:
    """Reads all the given corpus files in file order."""
    cases: list[Case] = []
    for path in paths:
        cases.extend(read_jsonl(path))
    return cases
