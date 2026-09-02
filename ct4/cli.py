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
from typing import Any, Sequence

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

    render = sub.add_parser(
        "render", help="render one template against a context, without"
                       " the application")
    render.add_argument("path", type=Path, help="the template")
    render.add_argument("--context", type=Path, action="append", default=[],
                        metavar="FILE",
                        help="JSON: a recording from ct4 fixture capture,"
                             " a plain object, or a list of namespaces;"
                             " may be repeated, first searched first")
    render.add_argument("--out", type=Path,
                        help="write the output there instead of stdout")
    render.add_argument("--encoding", default="utf-8")
    render.add_argument("--sandbox", action="store_true",
                        help="render in a child process with a time limit,"
                             " refusing what reaches outside the template")
    render.add_argument("--timeout", type=float, default=10.0,
                        metavar="SECONDS", help="the sandbox's limit")

    outline = sub.add_parser(
        "ast", help="the block tree of a template, for tools")
    outline.add_argument("path", type=Path)
    outline.add_argument("--json", action="store_true")

    fmt = sub.add_parser(
        "fmt", help="re-indent the directive lines whose indent is not"
                    " output; everything else stays byte for byte")
    fmt.add_argument("paths", type=Path, nargs="+")
    fmt.add_argument("--check", action="store_true",
                     help="change nothing, exit 1 where something would")
    fmt.add_argument("--indent", type=int, default=4, metavar="N",
                     help="spaces per step; 0 for a tab")

    migrate = sub.add_parser(
        "migrate", help="rewrite a text-mode template for #mode strict"
                        " and verify it against a recording")
    migrate.add_argument("path", type=Path)
    migrate.add_argument("--context", type=Path, metavar="FILE",
                         help="a recording from ct4 fixture capture;"
                              " without one only the mode line is added")
    migrate.add_argument("--write", action="store_true",
                         help="rewrite the file; otherwise only report")

    fixture = sub.add_parser(
        "fixture", help="record what templates read from a running"
                        " application")
    fixture_sub = fixture.add_subparsers(dest="fixture_command",
                                         required=True)
    capture = fixture_sub.add_parser(
        "capture", help="run weewx' own template tests and record every"
                        " page's context, one file each")
    capture.add_argument("--weewx", type=Path, required=True,
                         metavar="DIR",
                         help="a weewx source tree; its src/weewx/tests"
                              " are run")
    capture.add_argument("--out", type=Path, default=Path("fixtures"),
                         metavar="DIR", help="where the recordings go")

    sub.add_parser("mcp", help="speak as an MCP server over stdio")
    sub.add_parser("lsp", help="speak the language server protocol over"
                               " stdio: diagnostics and formatting")

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
    if args.command == "render":
        return _render(args)
    if args.command == "ast":
        return _ast(args)
    if args.command == "fmt":
        return _fmt(args)
    if args.command == "migrate":
        return _migrate(args)
    if args.command == "fixture":
        return _fixture(args)
    if args.command == "mcp":
        from ct4.mcp import serve

        return serve()
    if args.command == "lsp":
        from ct4.lsp import serve as serve_lsp

        return serve_lsp()
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


def _render(args: argparse.Namespace) -> int:
    from ct4 import render, sandbox, trace

    if args.sandbox and not sandbox.active():
        return sandbox.run(args.path, args.context, args.out, args.encoding,
                           args.timeout)
    if sandbox.active():
        # The child: every template, the page and its includes, goes
        # through the generator, where the guard stands.
        from ct4.lang import backend

        backend.install()
    source = args.path.read_text(encoding="utf-8")
    search_list: list[Any] = []
    output_filter = None
    for context_path in args.context:
        document = json.loads(context_path.read_text(encoding="utf-8"))
        search_list.extend(render.search_list_from(document))
        # The first recording that names a filter decides. A page is
        # rendered by one application, and that application set it.
        if output_filter is None:
            output_filter = render.filter_from(document)
    try:
        text = render.render_source(source, search_list, path=args.path,
                                    output_filter=output_filter)
    except Exception as error:                              # noqa: BLE001
        # The type and the message, then where in the template: the
        # traceback is for the engine's own bugs, and this is not one.
        print("ct4 render: %s: %s" % (type(error).__name__, error),
              file=sys.stderr)
        for remark in trace.notes_of(error):
            print("  " + remark, file=sys.stderr)
        return 1
    data = text.encode(args.encoding)
    if args.out is not None:
        args.out.write_bytes(data)
        return 0
    sys.stdout.buffer.write(data)
    sys.stdout.flush()
    return 0


def _ast(args: argparse.Namespace) -> int:
    from ct4 import directives
    from ct4.lang import tree

    source = args.path.read_text(encoding="utf-8")
    registered = directives.find_for(args.path)
    names = tree.syntax(registered.line, registered.block) \
        if registered.names else None
    try:
        root = tree.parse(source, names)
    except tree.StructureError as error:
        print("ct4 ast: %s: %s" % (args.path, error), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(tree.as_dict(root), ensure_ascii=False, indent=1))
        return 0
    for depth, node in _outline(root, 0):
        where = "%d:%d" % (node.line, node.column) if node.tokens else ""
        shown = "".join(token.text for token in node.tokens)
        print("%s%-10s %-12s %-7s %s"
              % ("  " * depth, node.kind, node.name, where,
                 json.dumps(shown[:60]) if shown else ""))
    return 0


def _fmt(args: argparse.Namespace) -> int:
    from ct4 import directives, fmt
    from ct4.lang import tree

    unit = " " * args.indent if args.indent > 0 else "\t"
    changed = 0
    broken = 0
    for path in args.paths:
        # Bytes in and bytes out, so that a CRLF file stays one and an
        # encoding this tool does not understand is left alone.
        raw = path.read_bytes()
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            print("ct4 fmt: %s is not UTF-8, left alone" % path,
                  file=sys.stderr)
            broken += 1
            continue
        registered = directives.find_for(path)
        names = tree.syntax(registered.line, registered.block) \
            if registered.names else None
        try:
            made = fmt.format_source(source, unit, names)
        except tree.StructureError as error:
            print("ct4 fmt: %s: %s" % (path, error), file=sys.stderr)
            broken += 1
            continue
        if made == source:
            continue
        changed += 1
        if args.check:
            print("%s: %d line(s) would change"
                  % (path, _lines_differing(source, made)))
            continue
        path.write_bytes(made.encode("utf-8"))
        print("reformatted %s" % path)
    if broken:
        return 2
    return 1 if args.check and changed else 0


def _lines_differing(before: str, after: str) -> int:
    return sum(1 for a, b in zip(before.splitlines(), after.splitlines())
               if a != b)


def _outline(node: Any, depth: int) -> Any:
    yield depth, node
    for child in node.children:
        yield from _outline(child, depth + 1)


def _migrate(args: argparse.Namespace) -> int:
    from ct4 import migrate

    source = args.path.read_text(encoding="utf-8")
    recording = None
    if args.context is not None:
        recording = json.loads(args.context.read_text(encoding="utf-8"))
    try:
        result = migrate.migrate(source, recording)
    except migrate.MigrationError as error:
        print("ct4 migrate: %s: %s" % (args.path, error), file=sys.stderr)
        return 2
    for change in result.changes:
        print("%s:%d:%d: %s -> %s" % (args.path, change.line, change.column,
                                      change.before, change.after))
    for left in result.skipped:
        print("%s:%d:%d: %s left alone, %s"
              % (args.path, left.line, left.column, left.text, left.reason))
    if result.diff is None:
        print("%d change(s); no recording, so nothing was verified"
              % len(result.changes))
    elif result.same:
        print("%d change(s); the page renders the same in strict mode"
              % len(result.changes))
    else:
        print("%d change(s); the page differs in strict mode:"
              % len(result.changes))
        for line in result.diff:
            print("  " + line)
    if args.write and result.source != source:
        args.path.write_text(result.source, encoding="utf-8")
        print("rewrote %s" % args.path)
    return 1 if result.same is False else 0


def _fixture(args: argparse.Namespace) -> int:
    import os
    import subprocess

    from ct4.fixture import weewx_capture

    tests = args.weewx / "src" / "weewx" / "tests" / "test_templates.py"
    if not tests.is_file():
        print("ct4 fixture capture: no weewx test suite at %s" % tests,
              file=sys.stderr)
        return 2
    # weewx' own tests do the run: they bring the database, the skins
    # and the report engine, and the plugin records beside them. The
    # output directory travels in the environment, because pytest
    # owns the command line.
    environment = dict(os.environ)
    environment[weewx_capture.OUT_ENV] = str(args.out.resolve())
    return subprocess.call(
        [sys.executable, "-m", "pytest", str(tests), "-q",
         "-p", "ct4.fixture.weewx_capture"],
        cwd=str(args.weewx), env=environment)


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
