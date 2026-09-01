"""The command line of ct4.

Every command can give its output as JSON. That is not an extra: a tool
whose result only a human can read is good for neither a CI nor an
agent.

    ct4 check skins/Seasons/index.html.tmpl --format=json
    ct4 context index.html.tmpl
    ct4 reference --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from ct4 import diagnostics, engine
from ct4.declare import Declaration

# Where declarations live when none is named. Inside the package, not
# beside it: pointed at the repository root, this resolved to
# site-packages/declarations once installed, which does not exist. The
# directory was then empty, "ct4 check" ran with no declarations at all
# and reported no findings, which reads exactly like a clean template.
DECLARATIONS = Path(__file__).resolve().parent / "declarations"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ct4")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="check a template")
    check.add_argument("paths", type=Path, nargs="+")
    check.add_argument("--format", default="text",
                       choices=sorted(diagnostics.FORMATS))
    check.add_argument("--declare", type=Path, action="append", default=[],
                       help="declaration; without one all of declarations/")
    check.add_argument("--unresolved", action="store_true",
                       help="also report where nothing could be checked")

    context = sub.add_parser(
        "context", help="what a template reads from its context")
    context.add_argument("path", type=Path)
    context.add_argument("--json", action="store_true")
    context.add_argument("--roots", action="store_true",
                         help="only the roots, not the full paths")

    reference = sub.add_parser(
        "reference", help="directives and settings, machine readable")
    reference.add_argument("--json", action="store_true")

    build = sub.add_parser("build", help="render a manifest of templates")
    build.add_argument("manifest", type=Path, help="the JSON manifest")
    build.add_argument("-j", "--jobs", type=int, default=1,
                       help="worker processes; 1 renders inline, so that"
                            " a traceback stays readable in cron's mail")
    build.add_argument("--format", default="text",
                       choices=("text", "json"))
    build.add_argument("--report", type=Path,
                       help="write the report document there as well")
    build.add_argument("--only", metavar="GLOB", action="append", default=[],
                       help="output name or pattern; may be repeated")
    build.add_argument("--force", action="store_true",
                       help="render everything; still writes only what"
                            " differs")
    build.add_argument("--dry-run", action="store_true",
                       help="render and compare, write nothing")
    build.add_argument("--no-lock", dest="lock", action="store_false")
    build.add_argument("--lock-timeout", type=float, default=3600.0,
                       metavar="SECONDS",
                       help="age at which a lock counts as left behind")

    declare = sub.add_parser("declare", help="show the declarations")
    declare.add_argument("--json", action="store_true")

    sub.add_parser("mcp", help="speak as an MCP server over stdio")

    args = parser.parse_args(argv)
    # After parsing, so that --help still answers on a broken install.
    # Everything below this line renders or compiles, and doing that
    # against a CT3 engine would look like it worked.
    engine.require()
    if args.command == "check":
        return _check(args)
    if args.command == "context":
        return _context(args)
    if args.command == "reference":
        return _reference(args)
    if args.command == "build":
        return _build(args)
    if args.command == "mcp":
        from ct4.mcp import serve

        return serve()
    return _declare(args)


def load_declarations(paths: Sequence[Path]) -> list[Declaration]:
    """Loads the named declarations, otherwise all the shipped ones."""
    if not paths:
        paths = sorted(DECLARATIONS.glob("*.json")) if \
            DECLARATIONS.is_dir() else []
    return [Declaration.load(path) for path in paths]


def _check(args: argparse.Namespace) -> int:
    from ct4.check import check_file, unresolved

    declarations = load_declarations(args.declare)
    found = []
    for path in args.paths:
        found.extend(check_file(path, declarations))
        if args.unresolved:
            found.extend(unresolved(path.read_text(encoding="utf-8"),
                                    declarations))
    print(diagnostics.render(found, args.format))
    return 1 if diagnostics.worst(found) == diagnostics.ERROR else 0


def _context(args: argparse.Namespace) -> int:
    from ct4 import analyze

    source = args.path.read_text(encoding="utf-8")
    items = analyze.placeholders(source)
    if args.roots:
        names = analyze.roots(items)
        print(json.dumps(names, indent=1) if args.json else "\n".join(names))
        return 0
    if args.json:
        print(json.dumps(
            [{"path": item.path, "line": item.line, "column": item.column}
             for item in items], indent=1))
        return 0
    for item in items:
        print("%5d:%-3d $%s" % (item.line, item.column, item.path))
    return 0


def _reference(args: argparse.Namespace) -> int:
    from ct4.reference import reference

    data = reference()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    print("Directives (%d):" % len(data["directives"]))
    for entry in data["directives"]:
        print("  #%-18s %s" % (entry["name"],
                               "closeable" if entry["closeable"] else ""))
    print("\nSettings (%d):" % len(data["settings"]))
    for entry in data["settings"]:
        print("  %-38s %r" % (entry["name"], entry["default"]))
    return 0


def _build(args: argparse.Namespace) -> int:
    from ct4 import build, write

    # Before the build, because it moves into the manifest's base for
    # the render and a relative --report would land there instead.
    report_path = args.report.resolve() if args.report else None
    try:
        manifest = build.load_manifest(args.manifest)
    except build.ManifestError as error:
        report = build.unusable(args.manifest, str(error))
    else:
        report = build.build(manifest, jobs=args.jobs, force=args.force,
                             dry_run=args.dry_run, only=args.only,
                             lock=args.lock,
                             lock_timeout=args.lock_timeout)
    document = json.dumps(report, ensure_ascii=False, indent=1)
    # Always, whatever --format says: a timer wants a quiet stdout and
    # a file it can read afterwards.
    if report_path is not None:
        try:
            write.atomic_write(report_path, (document + "\n").encode("utf-8"))
        except OSError as error:
            # The report is the documented way a timer reads the run,
            # so failing to write it is worth an exit code and a line
            # on stderr, not a traceback that a cron mail turns into
            # noise nobody reads twice.
            print("ct4 build: cannot write the report to %s: %s"
                  % (report_path, error), file=sys.stderr)
            return 2
    if args.format == "json":
        print(document)
        return build.exit_code(report)
    print(build.as_text(report))
    findings = build.findings_of(report)
    if findings:
        print(diagnostics.as_text(findings))
    return build.exit_code(report)


def _declare(args: argparse.Namespace) -> int:
    declarations = load_declarations([])
    if args.json:
        print(json.dumps([d.as_dict() for d in declarations],
                         ensure_ascii=False, indent=1))
        return 0
    for declaration in declarations:
        print("%s (%s): %d roots"
              % (declaration.name, declaration.source or "not stated",
                 len(declaration.roots)))
        print("  " + ", ".join(sorted(declaration.roots)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
