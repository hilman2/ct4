"""Measure whether the correction follows from a message.

What is tested here is not a language model. It is the diagnostics:
whether a finding carries enough to derive the correction from it,
without knowing the template and without starting the application. That
is the part of "AI ready" that can be measured, and the rest is
assertion.

A case consists of a broken template, its correct version and what the
finding has to carry:

    {"id": "...", "task": "...", "broken": "...", "fixed": "...",
     "expect": {"code": "CT4103", "suggests": "max", "line": 1}}

It passes when all expectations hold **and** the correct version runs
through without a finding. The second half is the more important one:
diagnostics that also nag at correct templates help nobody.
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
    # Only *.case.json. Beside them lie files that belong to the
    # cases, such as a schema, and those are not tasks.
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
            reasons.append("expected no finding, %d arrived"
                           % len(found))
    else:
        code = case.expect.get("code")
        match = [d for d in found if d.code == code]
        if not match:
            reasons.append("no finding %s; arrived: %s"
                           % (code, [d.code for d in found] or "none"))
        else:
            reasons.extend(_check_expectations(case, match[0]))

    if case.fixed is not None:
        rest = check_source(case.fixed, case.file, declarations,
                            base_dir=base_dir)
        if rest:
            reasons.append("the correct version produces findings: %s"
                           % [d.code for d in rest])

    return Result(case, not reasons, tuple(reasons))


def _check_expectations(case: Case, finding: Any) -> list[str]:
    reasons = []
    wanted = case.expect.get("suggests")
    if wanted is not None and wanted not in finding.suggestions:
        reasons.append(
            "the correction does not follow from the finding: expected "
            "the suggestion %r, suggested was %s"
            % (wanted, list(finding.suggestions) or "nothing"))
    line = case.expect.get("line")
    if line is not None and finding.line != line:
        reasons.append("expected line %s, reported was %s"
                       % (line, finding.line))
    mentions = case.expect.get("mentions", [])
    # A single word may stand there without a list. Without this line
    # the loop would run over its letters.
    if isinstance(mentions, str):
        mentions = [mentions]
    for word in mentions:
        if word not in finding.message:
            reasons.append("the message does not name %r: %r"
                           % (word, finding.message))
    return reasons


def run(cases: Sequence[Case] | None = None,
        base_dir: Path | None = None) -> list[Result]:
    """Runs all the tasks.

    ``base_dir`` defaults to the directory of the cases: a ``#schema``
    inside one names its path relative to that, not relative to the
    caller's working directory.
    """
    return [run_case(case, base_dir or CASES) for case in (cases or load())]


def failed(results: Sequence[Result]) -> int:
    return sum(1 for result in results if not result.passed)


def report(results: Sequence[Result]) -> str:
    passed = sum(1 for result in results if result.passed)
    lines = ["%d of %d tasks passed (%.0f %%)"
             % (passed, len(results),
                100.0 * passed / len(results) if results else 0.0)]
    for result in results:
        if result.passed:
            continue
        lines.append("")
        lines.append("FAILED %s: %s" % (result.case.id, result.case.task))
        for reason in result.reasons:
            lines.append("       %s" % reason)
    return "\n".join(lines)
