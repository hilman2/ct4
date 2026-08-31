"""Run weewx's own test suite twice and compare the outcomes.

Not "do weewx's tests pass". Some of them do not pass in this image
even with nothing of ours loaded, and chasing that is somebody else's
work. The question worth answering before asking weewx to depend on
Cheetah4 is narrower and harder to argue with: does Cheetah4 change
which of weewx's tests pass, compared with the CT3 they depend on
today.

So the suite runs twice, once against each engine, and the per-test
outcomes are held against each other. A test that passes under CT3 and
fails under Cheetah4 is a finding. So is the reverse, because a test
that starts passing is still a change in behaviour and somebody should
know why.

    python tests/docker/weewx_suite.py            report
    python tests/docker/weewx_suite.py --json     one line, machine
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

WEEWX = Path("/opt/weewx/src")
FORK = Path("/work")

# Where weewx keeps its tests. Every directory is collected, so a new
# one is not silently left out.
TEST_DIRS = ["weewx/tests", "weeutil/tests", "weecfg/tests",
             "weectllib/tests", "weeplot/tests",
             "weewx/drivers/tests"]

# Below this many collected tests the run is broken, not empty. It is
# a floor against a bad invocation, not a pin on weewx's test count:
# two runs that collect nothing agree with each other perfectly, and
# the comparison would report that as a clean result.
MINIMUM = 50


def outcomes(report: Path) -> dict[str, str]:
    """Test id to outcome, read from a JUnit report.

    Args:
        report (pathlib.Path): the XML pytest wrote.

    Returns:
        dict: "file::class::name" to one of passed, failed, error,
        skipped.
    """
    found: dict[str, str] = {}
    if not report.exists():
        return found
    for case in ET.parse(report).getroot().iter("testcase"):
        name = "%s::%s" % (case.get("classname", ""), case.get("name", ""))
        state = "passed"
        for child in case:
            tag = child.tag
            if tag in ("failure", "error", "skipped"):
                state = tag
                break
        found[name] = state
    return found


def test_files() -> list[Path]:
    """Every test file weewx ships, in a fixed order."""
    found: list[Path] = []
    for directory in TEST_DIRS:
        root = WEEWX / directory
        if root.is_dir():
            found.extend(sorted(root.glob("test_*.py")))
    return found


def run(engine: str) -> dict[str, str]:
    """Runs the suite against one engine and returns the outcomes.

    Which Cheetah gets loaded is decided by PYTHONPATH alone: with the
    fork on it the fork wins, without it the installed CT3 does. The
    same lever the corpus test bench uses.

    One process per file, not one for the whole suite. Three of weewx'
    test modules call locale.setlocale at import time, and they
    disagree: test_almanac asks for C, test_templates and test_xtypes
    for en_US.UTF-8. Whichever is imported last decides the format for
    every test that runs afterwards, and pytest imports alphabetically,
    so almanac's C loses. Its tests then read '06:59:14 AM' where they
    expect '06:59:14', and seven cases fail across two files.

    That is weewx' own ordering problem, not a result about Cheetah,
    and in a shared process this runner would report it as one. Each
    file gets its own interpreter, and all 334 pass.
    """
    environment = dict(os.environ)
    if engine == "cheetah4":
        environment["PYTHONPATH"] = str(FORK)
    else:
        environment.pop("PYTHONPATH", None)

    found: dict[str, str] = {}
    trouble = []
    for index, path in enumerate(test_files()):
        report = Path("/tmp/weewx-%s-%d.xml" % (engine, index))
        # importlib, because weewx has a weeutil/tests/test_config.py
        # and a weecfg/tests/test_config.py and neither directory is a
        # package. The default import mode reads that as one module
        # twice and stops the collection.
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no",
             "--import-mode=importlib", "--junit-xml=%s" % report,
             str(path)],
            cwd=str(WEEWX / "weewx" / "tests"),
            env=environment, capture_output=True, text=True)
        part = outcomes(report)
        if not part:
            trouble.append((path.name, done.stdout[-400:]))
        found.update(part)

    if len(found) < MINIMUM:
        # A run that collected almost nothing is a broken invocation,
        # and the comparison below would call it a clean result: two
        # empty runs agree perfectly. This happened once already,
        # through a -p no:randomly for a plugin that is not installed.
        print("The %s run collected %d tests, fewer than %d. That is a "
              "broken run, not a result." % (engine, len(found), MINIMUM),
              file=sys.stderr)
        for name, output in trouble:
            print("  %s\n%s" % (name, output), file=sys.stderr)
        raise SystemExit(2)
    return found


def compare(before: dict[str, str],
            after: dict[str, str]) -> list[tuple[str, str, str]]:
    """Tests whose outcome differs between the two engines."""
    changed = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name, "missing")
        new = after.get(name, "missing")
        if old != new:
            changed.append((name, old, new))
    return changed


def tally(found: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for state in found.values():
        counts[state] = counts.get(state, 0) + 1
    return counts


def by_file(found: dict[str, str]) -> list[tuple[str, dict[str, int]]]:
    """Modules with a non-passing test, and what happened in them.

    The test id starts with the dotted module, so the part before the
    last dot names it.
    """
    grouped: dict[str, dict[str, int]] = {}
    for name, state in found.items():
        if state == "passed":
            continue
        module = name.split("::")[0] or "?"
        counts = grouped.setdefault(module, {})
        counts[state] = counts.get(state, 0) + 1
    return sorted(grouped.items())


def main() -> int:
    before = run("ct3")
    after = run("cheetah4")
    changed = compare(before, after)

    if "--json" in sys.argv:
        print(json.dumps({
            "ct3": tally(before), "cheetah4": tally(after),
            "changed": [{"test": n, "ct3": o, "cheetah4": c}
                        for n, o, c in changed]}))
        return 1 if changed else 0

    print("weewx' own test suite, the same run against both engines")
    print("  under CT3      : %d tests  %s" % (len(before), tally(before)))
    print("  under Cheetah4 : %d tests  %s" % (len(after), tally(after)))
    print()
    # The one test this whole question is about, named on its own.
    # Buried in a count of 334 nobody would find it.
    for name in sorted(after):
        if "test_templates" not in name:
            continue
        print("  %-52s CT3 %s, Cheetah4 %s"
              % (name.split(".")[-1], before.get(name, "missing"),
                 after[name]))
    print()

    troubled = by_file(before)
    if troubled:
        # Named, because "280 of 334 passed" invites the question and
        # the answer is the point: what fails, fails under CT3 too, and
        # has nothing to do with templates.
        print("Not passing under CT3 either, by module:")
        for name, counts in troubled:
            print("  %-44s %s" % (name, counts))
        print()
    if not before:
        print("No test was collected. That is a broken run, not a result.")
        return 1
    if not changed:
        print("No test changed its outcome.")
        return 0
    print("%d tests changed their outcome:" % len(changed))
    for name, old, new in changed:
        print("  %-70s %s -> %s" % (name, old, new))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
