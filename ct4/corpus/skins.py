"""Take third-party skins into the corpus as compilation cases.

A weewx skin cannot be rendered without starting weewx along with its
database. Its context is a running application, not a file. Compiling it
works perfectly well, though, and that checks exactly what P4 of the
plan sets out to replace: parser and code generator.

A compilation case records the generated module code. If ct4 changes the
language by accident, that shows up here, long before anyone needs a
weather station.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Iterator

from ct4.corpus.case import COMPILE, Case

# weewx skins store templates under both suffixes: .tmpl for a page,
# .inc for a building block that a page pulls in via #include. Cobbler
# writes .template, and its kickstart and preseed files are the most
# unlike a weewx skin anything in the corpus gets: files where a hash
# is a shell comment on most lines, with directives in between.
SUFFIXES = (".tmpl", ".inc", ".template")


def harvest(root: Path, name: str) -> tuple[list[Case], Counter[str]]:
    """Compiles every template under ``root`` and stores it as a case.

    ``name`` becomes the namespace of the case ids, so that a skin can
    be found again in the corpus. Returned are the cases and a count of
    what could not be compiled.
    """
    from ct4.corpus.check import compile_code

    cases: list[Case] = []
    skipped: Counter[str] = Counter()
    for path in sorted(_templates(root)):
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped["not UTF-8"] += 1
            continue
        case = Case(
            id="%s/%s" % (name, relative),
            template=source,
            expected="",
            kind=COMPILE,
            origin=name,
        )
        try:
            code = compile_code(case)
        except Exception as exc:                        # noqa: BLE001
            # A template that ct3 does not compile is no yardstick. It
            # is counted by exception type, so that it stays visible
            # whether the template or our own call is to blame.
            skipped[type(exc).__name__] += 1
            continue
        cases.append(Case(**{**case.__dict__, "expected": code}))
    return cases, skipped


def _templates(root: Path) -> Iterator[Path]:
    for suffix in SUFFIXES:
        yield from root.rglob("*" + suffix)
