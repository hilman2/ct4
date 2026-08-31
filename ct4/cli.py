"""Die Kommandozeile von ct4.

Jedes Kommando kann seine Ausgabe als JSON. Das ist kein Zusatz: ein
Werkzeug, dessen Ergebnis nur ein Mensch lesen kann, taugt weder fuer
eine CI noch fuer einen Agenten.

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

from ct4 import diagnostics
from ct4.declare import Declaration

# Wo Anmeldungen liegen, wenn keine genannt wird.
DECLARATIONS = Path(__file__).resolve().parent.parent / "declarations"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ct4")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="eine Vorlage pruefen")
    check.add_argument("paths", type=Path, nargs="+")
    check.add_argument("--format", default="text",
                       choices=sorted(diagnostics.FORMATS))
    check.add_argument("--declare", type=Path, action="append", default=[],
                       help="Anmeldung; ohne Angabe alle aus declarations/")
    check.add_argument("--unresolved", action="store_true",
                       help="auch melden, wo nicht geprueft werden konnte")

    context = sub.add_parser(
        "context", help="was eine Vorlage aus dem Kontext liest")
    context.add_argument("path", type=Path)
    context.add_argument("--json", action="store_true")
    context.add_argument("--roots", action="store_true",
                         help="nur die Wurzeln, nicht die vollen Pfade")

    reference = sub.add_parser(
        "reference", help="Direktiven und Einstellungen, maschinenlesbar")
    reference.add_argument("--json", action="store_true")

    declare = sub.add_parser("declare", help="Anmeldungen anzeigen")
    declare.add_argument("--json", action="store_true")

    sub.add_parser("mcp", help="als MCP-Server ueber stdio sprechen")

    args = parser.parse_args(argv)
    if args.command == "check":
        return _check(args)
    if args.command == "context":
        return _context(args)
    if args.command == "reference":
        return _reference(args)
    if args.command == "mcp":
        from ct4.mcp import serve

        return serve()
    return _declare(args)


def load_declarations(paths: Sequence[Path]) -> list[Declaration]:
    """Laedt die genannten Anmeldungen, sonst alle mitgelieferten."""
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
    print("Direktiven (%d):" % len(data["directives"]))
    for entry in data["directives"]:
        print("  #%-18s %s" % (entry["name"],
                               "schliessbar" if entry["closeable"] else ""))
    print("\nEinstellungen (%d):" % len(data["settings"]))
    for entry in data["settings"]:
        print("  %-38s %r" % (entry["name"], entry["default"]))
    return 0


def _declare(args: argparse.Namespace) -> int:
    declarations = load_declarations([])
    if args.json:
        print(json.dumps([d.as_dict() for d in declarations],
                         ensure_ascii=False, indent=1))
        return 0
    for declaration in declarations:
        print("%s (%s): %d Wurzeln"
              % (declaration.name, declaration.source or "ohne Angabe",
                 len(declaration.roots)))
        print("  " + ", ".join(sorted(declaration.roots)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
