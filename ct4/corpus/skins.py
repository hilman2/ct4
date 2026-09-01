"""Take other people's templates into the corpus.

A weewx skin cannot be rendered without starting weewx along with its
database. Its context is a running application, not a file. Compiling it
works perfectly well, though, and that checks exactly what P4 of the
plan sets out to replace: parser and code generator.

A compilation case records the generated module code. If ct4 changes the
language by accident, that shows up here, long before anyone needs a
weather station.

Three harvests, and they differ in what they look for and in what they
record. :func:`harvest` takes one named directory as compilation cases.
:func:`harvest_sources` finds skins by their skin.conf and records the
source alone, for the differential runs. :func:`harvest_templates` does
not look for a skin at all: it takes any file a Cheetah template could
be and asks ct3 whether it is one, which is how the corpus reaches
past the weather station to the web interfaces, the mail bodies and
the configuration files.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from pathlib import Path
from typing import Iterator

from ct4.corpus.case import COMPILE, Case
from ct4.lang import lex

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


# What marks a directory as a skin. weewx reads this file to find out
# which reports a skin produces and what to call them, so a directory
# that has one is a skin and one that has not is a checkout with some
# templates lying around in it.
SKIN_CONF = "skin.conf"


def fetch(urls: list[str], out: Path) -> tuple[int, list[tuple[str, str]]]:
    """Clones the listed repositories under ``out``, one level deep.

    Args:
        urls (list[str]): clone URLs, as corpus/skin-sources.txt holds
            them.
        out (pathlib.Path): the directory the checkouts go in, one per
            repository, named owner--repo.

    Returns:
        tuple[int, list[tuple[str, str]]]: how many are there now, and
            the ones that could not be cloned with the reason. A
            repository that has been deleted or renamed is reported
            rather than raised: the list is a hundred and fifty other
            people's projects and one of them going away is not a
            reason to fetch none of the rest.
    """
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    def clone(url: str) -> tuple[str, str]:
        parts = url.removesuffix(".git").split("/")
        name = "%s--%s" % (parts[-2], parts[-1])
        target = out / name
        if target.exists():
            return name, ""
        done = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", url, str(target)],
            capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            return name, (done.stderr.strip().splitlines() or ["failed"])[-1]
        return name, ""

    out.mkdir(parents=True, exist_ok=True)
    failed = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, why in pool.map(clone, urls):
            if why:
                failed.append((name, why))
    return len(urls) - len(failed), failed


def harvest_sources(root: Path) -> tuple[list[Case], Counter[str]]:
    """Every template of every skin under ``root``, as a render case.

    Deduplicated by content, which takes out a lot: half of these
    repositories are forks of the other half, and a skin that was
    copied and had its colours changed brings the same #include files
    along untouched.

    No expected output and no compile, unlike :func:`harvest`. These
    are for the differential runs, which make their own expectation by
    rendering twice, and recording the module code of a thousand more
    templates would put twenty megabytes in the repository to say what
    the render already says.

    Args:
        root (pathlib.Path): the directory the checkouts are in.

    Returns:
        tuple[list[ct4.corpus.case.Case], collections.Counter[str]]:
            the cases, and a count of what was passed over.
    """
    rows: dict[str, str] = {}
    skipped: Counter[str] = Counter()
    for conf in sorted(root.rglob(SKIN_CONF)):
        skin = conf.parent
        inside = skin.relative_to(root).as_posix()
        for path in sorted(_templates(skin)):
            try:
                source = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                skipped["not UTF-8"] += 1
                continue
            if not source.strip():
                skipped["empty"] += 1
                continue
            name = "%s/%s" % (inside, path.relative_to(skin).as_posix())
            if source in rows:
                skipped["a copy of another skin's"] += 1
                continue
            rows[source] = name
    return ([Case(id=case_id, template=source, expected="", kind=COMPILE,
                  origin=case_id.split("/")[0])
             for source, case_id in sorted(rows.items(),
                                           key=lambda pair: pair[1])],
            skipped)


@functools.lru_cache(maxsize=1)
def _directive_line() -> re.Pattern[str]:
    """A line that opens with a directive ct3 knows.

    Which is what tells a Cheetah template from the half-dozen other
    things a ".tmpl" file can be: Go templates, Jinja and Mustache all
    put their tags in braces, and only this one opens a line with a
    hash and a name.
    """
    names = sorted(lex.directive_names() | {"end"}, key=len, reverse=True)
    return re.compile(r"^[ \t]*#(?:%s)\b"
                      % "|".join(re.escape(name) for name in names),
                      re.MULTILINE)


ESCAPED_HASH = re.compile(r"\\#")


def _looks_like_cheetah(source: str) -> bool:
    """Whether the file is a Cheetah template rather than some other.

    Three marks are taken: a line that opens with a directive ct3
    knows, a placeholder, or an escaped hash. For the placeholder the
    lexer's own pattern is used so that an escaped dollar and a "$"
    inside a name do not count; half of these files are shell and PHP.

    The escaped hash is what a template writes where the output needs
    one, and fprime's C++ generators are full of it:

        \\#include <cstring>

    Nothing else escapes a hash, so it is as good a mark as a
    directive. None of the three is proof, which is why the caller
    asks ct3 to compile it as well.
    """
    return bool(_directive_line().search(source)
                or lex.START.search(source)
                or ESCAPED_HASH.search(source))


def harvest_templates(root: Path,
                      known: set[str] | None = None,
                      ) -> tuple[list[Case], Counter[str]]:
    """Every Cheetah template under ``root``, whoever wrote it.

    No skin.conf and no directory layout: the file is taken if it
    carries Cheetah syntax and ct3 compiles it. Both tests are needed.
    The syntax alone lets in a Go template that happens to hold a
    dollar; ct3 alone lets in every plain text file there is, because a
    template with no directives compiles to a single write.

    Compiled through ModuleCompiler and not Template.compile, which
    would execute the generated module: a template whose #extends names
    a class from the application it belongs to parses perfectly and
    raises ModuleNotFoundError on the import. Nine of these repositories
    are like that and their templates are as good as any.

    The generated code is then handed to Python, because ModuleCompiler
    writing something is not the same as ct3 accepting it. A C header
    passes the syntax test on its ``#include`` and passes ModuleCompiler
    too, which turns ``#include <atomic>`` into a call with a less-than
    in the argument list and never looks at it again. Python does.

    Args:
        root (pathlib.Path): the directory the checkouts are in.
        known (set[str]|None): template sources the corpus already
            holds, so that the sets stay disjoint. A skin repository
            with a template directory of its own turns up in both
            harvests, and rendering the same file twice says nothing
            the first time did not.

    Returns:
        tuple[list[ct4.corpus.case.Case], collections.Counter[str]]:
            the cases, and a count of what was passed over and why.
    """
    from Cheetah.Compiler import ModuleCompiler

    known = known or set()
    rows: dict[str, str] = {}
    skipped: Counter[str] = Counter()
    for path in sorted(_templates(root)):
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped["not UTF-8"] += 1
            continue
        if not source.strip():
            skipped["empty"] += 1
            continue
        if not _looks_like_cheetah(source):
            skipped["some other engine's"] += 1
            continue
        if source in rows:
            skipped["a copy of another one"] += 1
            continue
        if source in known:
            skipped["already in the corpus"] += 1
            continue
        try:
            compile(str(ModuleCompiler(source, moduleName="ct4_corpus",
                                       mainClassName="ct4_corpus")),
                    "<corpus>", "exec")
        except Exception:                               # noqa: BLE001
            # ct3 cannot read it, so there is nothing to be faithful to
            # and nothing to compare against.
            skipped["ct3 will not compile it"] += 1
            continue
        rows[source] = path.relative_to(root).as_posix()
    return ([Case(id=case_id, template=source, expected="", kind=COMPILE,
                  origin=case_id.split("/")[0])
             for source, case_id in sorted(rows.items(),
                                           key=lambda pair: pair[1])],
            skipped)
