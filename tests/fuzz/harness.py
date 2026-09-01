"""What every differential run needs: two engines and one comparison.

The rule this layer lives by is that what the generator accepts renders
byte for byte what Cheetah renders. Checking that needs a template, a
search list, and both engines, and every instrument in this directory
needs exactly those three things. They differ only in where the
templates come from and what they are rendered against.

A failure is a string like the output is, so that a template both
engines refuse counts as agreement rather than as a crash in the middle
of a run of thousands.
"""

from __future__ import annotations

import collections
import time
import warnings
from pathlib import Path
from typing import Any, Iterator, Sequence

warnings.filterwarnings("ignore")

# A template that reads the clock renders differently every time, and a
# differential run then reports a disagreement that is nobody's fault.
# The corpus holds such templates: one of them times its own rendering.
# Freezing the clock costs these instruments nothing, both engines see
# the same number, and a #cache timer that never expires is as good an
# answer as one that does as long as it is the same answer twice.
#
# Module scope on purpose: it has to stand before the first template is
# compiled, because a generated module binds currentTime at import.
time.time = lambda: 1767225600.0                               # type: ignore[assignment]

from Cheetah.Template import Template                          # noqa: E402
from ct4.corpus.case import read_jsonl                         # noqa: E402
from ct4.lang import codegen, tree                             # noqa: E402

# Every corpus file, whatever kind of case it holds. The templates are
# what matters here and not the recorded output: a differential run
# makes its own expectation by rendering twice.
CORPUS_FILES = ("ct3-tests.jsonl", "skins.jsonl", "weewx-render.jsonl")


def corpus_dir() -> Path | None:
    """Where the corpus is, mounted or in the working tree."""
    here = Path(__file__).resolve()
    for candidate in (Path("/repo/corpus"), here.parents[2] / "corpus"):
        if (candidate / CORPUS_FILES[0]).exists():
            return candidate
    return None


def corpus_templates() -> list[tuple[str, str]]:
    """Every distinct template in the corpus, with an id.

    Distinct by source text: the corpus holds each ct3 test case three
    times, once per line ending, and every one of those is a template
    of its own here. What it does not hold three times is a skin, and
    the 390 skin templates are the reason this reads skins.jsonl too:
    they are compile cases, so nothing has ever rendered them.
    """
    root = corpus_dir()
    if root is None:
        return []
    seen: dict[str, str] = {}
    for name in CORPUS_FILES:
        path = root / name
        if not path.exists():
            continue
        for case in read_jsonl(path):
            seen.setdefault(case.template, case.id)
    return [(case_id, template) for template, case_id in seen.items()]


def accepted(source: str) -> bool:
    """Whether the generator claims this template."""
    try:
        codegen.generate(source)
    except (codegen.Unsupported, tree.StructureError):
        return False
    except Exception:                                          # noqa: BLE001
        # Anything else is a defect of its own, and the unit suite has
        # a case for it. Not this run's business.
        return False
    return True


def by_cheetah(source: str, search_list: Sequence[Any],
               output_filter: Any = None) -> str:
    """What the compiler in this repo renders, or the failure it hit."""
    try:
        klass = Template.compile(source=source, useCache=False,
                                 cacheCompilationResults=False)
        keywords = {"filter": output_filter} if output_filter else {}
        template = klass(searchList=list(search_list), **keywords)
        try:
            return str(template.respond())
        finally:
            template.shutdown()
    except Exception as error:                                 # noqa: BLE001
        return "!!%s" % type(error).__name__


def by_generator(source: str, search_list: Sequence[Any],
                 output_filter: Any = None) -> str:
    """The same for the generator under test."""
    try:
        return codegen.render(source, search_list, output_filter)
    except Exception as error:                                 # noqa: BLE001
        return "!!%s" % type(error).__name__


def disagreements(sources: Iterator[tuple[str, str]],
                  build: Any) -> tuple[int, int, list[tuple[str, str, str,
                                                            str]]]:
    """Runs both engines over the sources and collects what differs.

    Args:
        sources (Iterator[tuple[str, str]]): Pairs of label and
            template source.
        build (Callable[[], tuple[Sequence[Any], Any]]): Called as
            ``build()`` before each template. Returns the search list
            and the output filter to render it against. A fresh call
            per template, because an instrument may hand out objects
            that remember what was done to them.

    Returns:
        tuple[int, int, list[tuple[str, str, str, str]]]: How many were
        seen, how many the generator took, and one entry per
        disagreement holding label, source, what the generator wrote
        and what Cheetah wrote. A template that does not render the
        same twice is counted as noise instead, and the count is
        printed rather than returned: a clock or a random number in a
        template is not something either engine got wrong.
    """
    seen = taken = noisy = 0
    found = []
    for label, source in sources:
        seen += 1
        if not accepted(source):
            continue
        taken += 1
        search_list, output_filter = build()
        want = by_cheetah(source, search_list, output_filter)
        search_list, output_filter = build()
        got = by_generator(source, search_list, output_filter)
        if got == want:
            continue
        # Before calling it a disagreement, ask whether the template
        # even agrees with itself. "#set $t = time.time()" does not.
        search_list, output_filter = build()
        if by_generator(source, search_list, output_filter) != got:
            noisy += 1
            continue
        found.append((label, source, got, want))
    if noisy:
        print("  (%d template(s) render differently twice in a row and are "
              "left out)" % noisy)
    return seen, taken, found


# How bad a disagreement is. All four break the rule, and they do not
# cost the same to fix or to live with.
BYTES = "different bytes"
CT3_REFUSES = "ct4 renders what ct3 refuses"
CT4_FAILS = "ct4 fails where ct3 renders"
BOTH_FAIL = "both fail, differently"


def severity(got: str, want: str) -> str:
    """Which of the four a disagreement is.

    A rendered failure is a string starting with "!!" here, so the two
    can be told apart without a second run.
    """
    ours, theirs = got.startswith("!!"), want.startswith("!!")
    if ours and theirs:
        return BOTH_FAIL
    if theirs:
        return CT3_REFUSES
    if ours:
        return CT4_FAILS
    return BYTES


def where_they_part(got: str, want: str, around: int = 60) -> str:
    """The two outputs around the first character they differ at.

    A disagreement 4,000 characters into a skin is invisible in a
    prefix, and a prefix is what the first version of this printed.
    """
    at = 0
    while at < min(len(got), len(want)) and got[at] == want[at]:
        at += 1
    start = max(0, at - around)
    return ("    at %d\n      ct4 %r\n      ct3 %r"
            % (at, got[start:at + around], want[start:at + around]))


def report(title: str, seen: int, taken: int,
           found: Sequence[tuple[str, str, str, str]],
           examples: int = 0, tolerate: Sequence[str] = ()) -> int:
    """Prints the counts, and returns the exit code the run deserves.

    Args:
        tolerate (Sequence[str]): Severities that are counted and
            printed but do not fail the run. Every entry needs a reason
            written down where the run is set up, because a tolerated
            difference is still a difference.
    """
    print("== %s" % title)
    print("  templates   %d" % seen)
    print("  accepted    %d" % taken)
    print("  disagree    %d" % len(found))
    counts: collections.Counter[str] = collections.Counter(
        severity(got, want) for _, _, got, want in found)
    for kind, count in counts.most_common():
        mark = "  (tolerated)" if kind in tolerate else ""
        print("    %5d  %s%s" % (count, kind, mark))
    shown = 0
    for label, source, got, want in found:
        if shown >= examples:
            break
        if severity(got, want) in tolerate:
            continue
        shown += 1
        print()
        print("  %s  [%s]" % (label, severity(got, want)))
        print("    src %r" % source[:120])
        print(where_they_part(got, want))
    return 1 if any(severity(got, want) not in tolerate
                    for _, _, got, want in found) else 0
