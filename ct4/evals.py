"""Messen, ob aus einer Meldung die Korrektur folgt.

Was hier geprueft wird, ist nicht ein Sprachmodell. Es ist die
Diagnostik: ob ein Befund genug traegt, um daraus die Korrektur
abzuleiten, ohne die Vorlage zu kennen und ohne die Anwendung zu
starten. Das ist der Teil von "AI ready", der sich messen laesst, und
der Rest ist Behauptung.

Ein Fall besteht aus einer kaputten Vorlage, ihrer richtigen Fassung und
dem, was der Befund tragen muss:

    {"id": "...", "task": "...", "broken": "...", "fixed": "...",
     "expect": {"code": "CT4103", "suggests": "max", "line": 1}}

Bestanden ist er, wenn alle Erwartungen zutreffen **und** die richtige
Fassung ohne Befund durchlaeuft. Die zweite Haelfte ist die wichtigere:
eine Diagnostik, die auch richtige Vorlagen anmeckert, hilft niemandem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

CASES = Path(__file__).resolve().parent.parent / "tests" / "evals"


@dataclass(frozen=True)
class Case:
    id: str
    task: str
    broken: str
    expect: dict[str, Any]
    fixed: str | None = None
    file: str = "probe.tmpl"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Case":
        return cls(id=data["id"], task=data["task"], broken=data["broken"],
                   expect=data.get("expect", {}), fixed=data.get("fixed"),
                   file=data.get("file", "probe.tmpl"))


@dataclass(frozen=True)
class Result:
    case: Case
    passed: bool
    reasons: tuple[str, ...]


def load(directory: Path | None = None) -> list[Case]:
    # Nur *.case.json. Daneben liegen Dateien, die zu den
    # Faellen gehoeren, etwa ein Schema, und die sind keine Aufgaben.
    root = directory or CASES
    return [Case.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(root.glob("*.case.json"))]


def run_case(case: Case, base_dir: Path | None = None) -> Result:
    from ct4.check import check_source
    from ct4.cli import load_declarations

    declarations = load_declarations([])
    found = check_source(case.broken, case.file, declarations,
                         base_dir=base_dir)
    reasons: list[str] = []

    if case.expect.get("clean"):
        if found:
            reasons.append("erwartet war kein Befund, gekommen sind %d"
                           % len(found))
    else:
        code = case.expect.get("code")
        match = [d for d in found if d.code == code]
        if not match:
            reasons.append("kein Befund %s; gekommen: %s"
                           % (code, [d.code for d in found] or "keiner"))
        else:
            reasons.extend(_check_expectations(case, match[0]))

    if case.fixed is not None:
        rest = check_source(case.fixed, case.file, declarations,
                            base_dir=base_dir)
        if rest:
            reasons.append("die richtige Fassung erzeugt Befunde: %s"
                           % [d.code for d in rest])

    return Result(case, not reasons, tuple(reasons))


def _check_expectations(case: Case, finding: Any) -> list[str]:
    reasons = []
    wanted = case.expect.get("suggests")
    if wanted is not None and wanted not in finding.suggestions:
        reasons.append(
            "aus dem Befund folgt die Korrektur nicht: erwartet war der "
            "Vorschlag %r, vorgeschlagen wurde %s"
            % (wanted, list(finding.suggestions) or "nichts"))
    line = case.expect.get("line")
    if line is not None and finding.line != line:
        reasons.append("erwartet war Zeile %s, gemeldet wurde %s"
                       % (line, finding.line))
    mentions = case.expect.get("mentions", [])
    # Ein einzelnes Wort darf ohne Liste dastehen. Ohne diese Zeile
    # liefe die Schleife ueber seine Buchstaben.
    if isinstance(mentions, str):
        mentions = [mentions]
    for word in mentions:
        if word not in finding.message:
            reasons.append("die Meldung nennt %r nicht: %r"
                           % (word, finding.message))
    return reasons


def run(cases: Sequence[Case] | None = None,
        base_dir: Path | None = None) -> list[Result]:
    """Laesst alle Aufgaben laufen.

    ``base_dir`` ist standardmaessig das Verzeichnis der Faelle: ein
    ``#schema`` darin nennt seinen Pfad relativ dazu, nicht relativ zum
    Arbeitsverzeichnis des Aufrufers.
    """
    return [run_case(case, base_dir or CASES) for case in (cases or load())]


def failed(results: Sequence[Result]) -> int:
    return sum(1 for result in results if not result.passed)


def report(results: Sequence[Result]) -> str:
    passed = sum(1 for result in results if result.passed)
    lines = ["%d von %d Aufgaben bestanden (%.0f %%)"
             % (passed, len(results),
                100.0 * passed / len(results) if results else 0.0)]
    for result in results:
        if result.passed:
            continue
        lines.append("")
        lines.append("FEHLT  %s: %s" % (result.case.id, result.case.task))
        for reason in result.reasons:
            lines.append("       %s" % reason)
    return "\n".join(lines)
