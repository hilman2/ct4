"""Command line of the corpus test bench.

    python -m ct4.corpus harvest --impl installed
    python -m ct4.corpus check corpus/ct3-tests.jsonl --impl fork

``--impl`` decides which Cheetah is loaded, and it has to take effect
before any module imports Cheetah. That is why the choice is made here,
at the entry point, and not deeper inside the program.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ct4 import impl

DEFAULT_OUT = Path("corpus/ct3-tests.jsonl")
DEFAULT_SKINS = Path("corpus/skins.jsonl")
DEFAULT_FIXTURES = Path("corpus/weewx-render.jsonl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ct4.corpus")
    parser.add_argument(
        "--impl", choices=impl.CHOICES, default=impl.FORK,
        help="which Cheetah is loaded (default: fork)")
    sub = parser.add_subparsers(dest="command", required=True)

    harvest_cmd = sub.add_parser(
        "harvest", help="harvest cases from the ct3 test suite")
    harvest_cmd.add_argument("--out", type=Path, default=DEFAULT_OUT)

    skins_cmd = sub.add_parser(
        "harvest-skins",
        help="take third-party skins in as compilation cases")
    skins_cmd.add_argument(
        "sources", nargs="+", metavar="NAME=PATH",
        help="name of the skin and root directory of its templates")
    skins_cmd.add_argument("--out", type=Path, default=DEFAULT_SKINS)

    fixtures_cmd = sub.add_parser(
        "harvest-fixtures",
        help="turn recorded contexts into cases")
    fixtures_cmd.add_argument("root", type=Path)
    fixtures_cmd.add_argument("--name", default="weewx")
    fixtures_cmd.add_argument("--out", type=Path, default=DEFAULT_FIXTURES)

    templates_cmd = sub.add_parser(
        "check-templates",
        help="run ct4 check over the templates of a corpus")
    templates_cmd.add_argument("paths", type=Path, nargs="+")
    templates_cmd.add_argument(
        "--expect", type=int, default=0,
        help="how many findings are expected; more are an error")

    check_cmd = sub.add_parser(
        "check", help="check the corpus against the implementation")
    check_cmd.add_argument("paths", type=Path, nargs="+")
    check_cmd.add_argument(
        "--show", type=int, default=5,
        help="how many mismatches are shown in full")
    check_cmd.add_argument(
        "--jobs", "-j", type=int, default=0,
        help="worker processes; 0 means all cores (default)")

    args = parser.parse_args(argv)
    impl.select(args.impl)
    print("Cheetah: %s" % impl.describe(), file=sys.stderr)

    if args.command == "harvest":
        return _harvest(args.out)
    if args.command == "harvest-skins":
        return _harvest_skins(args.sources, args.out)
    if args.command == "harvest-fixtures":
        return _harvest_fixtures(args.root, args.name, args.out)
    if args.command == "check-templates":
        return _check_templates(args.paths, args.expect)
    return _check(args.paths, args.show, args.jobs)


def _harvest(out: Path) -> int:
    from ct4.corpus import harvest as harvester
    from ct4.corpus.case import write_jsonl

    report = harvester.harvest()
    print(report.summary())
    if report.tests_failed:
        print("\nThe test suite did not run cleanly. The corpus holds "
              "only the cases that passed.")
    count = write_jsonl(report.cases, out)
    print("\n%d cases written to %s" % (count, out))
    return 0


def _harvest_skins(sources: list[str], out: Path) -> int:
    from ct4.corpus import skins
    from ct4.corpus.case import write_jsonl

    cases = []
    for source in sources:
        name, _, root = source.partition("=")
        if not root:
            print("expected NAME=PATH, got: %s" % source,
                  file=sys.stderr)
            return 2
        found, skipped = skins.harvest(Path(root), name)
        cases.extend(found)
        detail = ", ".join("%s %d" % (reason, count)
                           for reason, count in sorted(skipped.items()))
        note = "  (skipped: %s)" % detail if detail else ""
        print("%-22s %4d templates%s" % (name, len(found), note))

    count = write_jsonl(cases, out)
    print("\n%d cases written to %s" % (count, out))
    return 0


def _harvest_fixtures(root: Path, name: str, out: Path) -> int:
    from ct4.corpus import fixtures
    from ct4.corpus.case import write_jsonl

    cases, skipped = fixtures.harvest(root, name)
    for reason, count in sorted(skipped.items()):
        print("skipped: %-20s %d" % (reason, count))
    print("%d cases written to %s" % (write_jsonl(cases, out), out))
    return 0


def _check_templates(paths: list[Path], expect: int) -> int:
    """Checks every template of the corpus and counts the findings.

    The guard against false findings. A declaration that reports too
    much is worse than none at all: it makes people switch the tool off.
    That is why the expected number is part of the run.
    """
    from ct4.check import check_source
    from ct4.cli import load_declarations
    from ct4.corpus import check as checker

    declarations = load_declarations([])
    found = []
    for case in checker.load(paths):
        found.extend(check_source(case.template, case.id, declarations))
    print("%d templates checked, %d findings (expected: %d)"
          % (len(checker.load(paths)), len(found), expect))
    for finding in found:
        print("  %s" % finding)
    if len(found) > expect:
        print("\nMore findings than expected. Either the declaration is "
              "too strict or a template is newly broken.")
        return 1
    return 0


def _check(paths: list[Path], show: int, jobs: int) -> int:
    import time

    from ct4.corpus import check as checker

    workers = jobs or checker.default_jobs()
    started = time.perf_counter()
    total, mismatches = checker.check_files(paths, jobs=jobs)
    elapsed = time.perf_counter() - started

    hits = total - len(mismatches)
    share = 100.0 * hits / total if total else 0.0
    print("%d of %d cases identical (%.2f %%)" % (hits, total, share))
    print("%.2f s on %d processes, %.0f cases/s"
          % (elapsed, workers, total / elapsed if elapsed else 0.0))

    for mismatch in mismatches[:show]:
        print("\n--- %s" % mismatch.case.id)
        print(mismatch.diff())
    if len(mismatches) > show:
        print("\n... and %d more" % (len(mismatches) - show))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
