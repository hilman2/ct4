"""Kommandozeile des Korpus-Pruefstands.

    python -m ct4.corpus harvest --impl installed
    python -m ct4.corpus check corpus/ct3-tests.jsonl --impl fork

``--impl`` entscheidet, welches Cheetah geladen wird, und muss wirken,
bevor irgendein Modul Cheetah importiert. Deshalb faellt die Wahl hier,
im Einstiegspunkt, und nicht tiefer im Programm.
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
        help="welches Cheetah geladen wird (Vorgabe: fork)")
    sub = parser.add_subparsers(dest="command", required=True)

    harvest_cmd = sub.add_parser(
        "harvest", help="Faelle aus der ct3-Testsuite ernten")
    harvest_cmd.add_argument("--out", type=Path, default=DEFAULT_OUT)

    skins_cmd = sub.add_parser(
        "harvest-skins",
        help="fremde Skins als Uebersetzungsfaelle aufnehmen")
    skins_cmd.add_argument(
        "sources", nargs="+", metavar="NAME=PFAD",
        help="Name des Skins und Wurzelverzeichnis seiner Vorlagen")
    skins_cmd.add_argument("--out", type=Path, default=DEFAULT_SKINS)

    fixtures_cmd = sub.add_parser(
        "harvest-fixtures",
        help="aufgezeichnete Kontexte in Faelle umwandeln")
    fixtures_cmd.add_argument("root", type=Path)
    fixtures_cmd.add_argument("--name", default="weewx")
    fixtures_cmd.add_argument("--out", type=Path, default=DEFAULT_FIXTURES)

    check_cmd = sub.add_parser(
        "check", help="Korpus gegen die Implementierung pruefen")
    check_cmd.add_argument("paths", type=Path, nargs="+")
    check_cmd.add_argument(
        "--show", type=int, default=5,
        help="wie viele Abweichungen ausfuehrlich gezeigt werden")
    check_cmd.add_argument(
        "--jobs", "-j", type=int, default=0,
        help="Arbeitsprozesse; 0 heisst alle Kerne (Vorgabe)")

    args = parser.parse_args(argv)
    impl.select(args.impl)
    print("Cheetah: %s" % impl.describe(), file=sys.stderr)

    if args.command == "harvest":
        return _harvest(args.out)
    if args.command == "harvest-skins":
        return _harvest_skins(args.sources, args.out)
    if args.command == "harvest-fixtures":
        return _harvest_fixtures(args.root, args.name, args.out)
    return _check(args.paths, args.show, args.jobs)


def _harvest(out: Path) -> int:
    from ct4.corpus import harvest as harvester
    from ct4.corpus.case import write_jsonl

    report = harvester.harvest()
    print(report.summary())
    if report.tests_failed:
        print("\nDie Testsuite ist nicht sauber durchgelaufen. Der Korpus "
              "enthaelt nur die Faelle, die bestanden haben.")
    count = write_jsonl(report.cases, out)
    print("\n%d Faelle geschrieben nach %s" % (count, out))
    return 0


def _harvest_skins(sources: list[str], out: Path) -> int:
    from ct4.corpus import skins
    from ct4.corpus.case import write_jsonl

    cases = []
    for source in sources:
        name, _, root = source.partition("=")
        if not root:
            print("Erwartet NAME=PFAD, bekommen: %s" % source,
                  file=sys.stderr)
            return 2
        found, skipped = skins.harvest(Path(root), name)
        cases.extend(found)
        detail = ", ".join("%s %d" % (reason, count)
                           for reason, count in sorted(skipped.items()))
        note = "  (uebersprungen: %s)" % detail if detail else ""
        print("%-22s %4d Vorlagen%s" % (name, len(found), note))

    count = write_jsonl(cases, out)
    print("\n%d Faelle geschrieben nach %s" % (count, out))
    return 0


def _harvest_fixtures(root: Path, name: str, out: Path) -> int:
    from ct4.corpus import fixtures
    from ct4.corpus.case import write_jsonl

    cases, skipped = fixtures.harvest(root, name)
    for reason, count in sorted(skipped.items()):
        print("uebersprungen: %-20s %d" % (reason, count))
    print("%d Faelle geschrieben nach %s" % (write_jsonl(cases, out), out))
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
    print("%d von %d Faellen identisch (%.2f %%)" % (hits, total, share))
    print("%.2f s auf %d Prozessen, %.0f Faelle/s"
          % (elapsed, workers, total / elapsed if elapsed else 0.0))

    for mismatch in mismatches[:show]:
        print("\n--- %s" % mismatch.case.id)
        print(mismatch.diff())
    if len(mismatches) > show:
        print("\n... und %d weitere" % (len(mismatches) - show))
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
