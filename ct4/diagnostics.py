"""Findings as a data structure, not as prose.

A message from which nobody can derive a correction is not a message.
That is why every finding here carries a place, a code and, where it can
be had, concrete suggestions. And that is why it comes in three forms:
for humans, for programs and for the annotations of a CI.

The codes are stable. They are the only handle for talking about a
finding without quoting its wording, and the wording changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

ERROR = "error"
WARNING = "warning"
NOTE = "note"

# After SARIF: what a finding means for the run.
SARIF_LEVEL = {ERROR: "error", WARNING: "warning", NOTE: "note"}


@dataclass(frozen=True)
class Diagnostic:
    """A finding.

    ``path`` is the place inside the document, where one can be named
    (such as ``$.day.outTemp``); ``line`` and ``column`` point into the
    file. Having both at once is no luxury: one says where in the
    result, the other where in the text.
    """

    code: str
    severity: str
    message: str
    file: str = ""
    line: int = 0
    column: int = 0
    path: str = ""
    suggestions: Sequence[str] = field(default_factory=tuple)

    def __str__(self) -> str:
        where = self.file or "<template>"
        if self.line:
            where = "%s:%d:%d" % (where, self.line, self.column)
        head = "%s %s %s: %s" % (self.code, self.severity.upper(),
                                 where, self.message)
        if self.path:
            head += "  [%s]" % self.path
        if self.suggestions:
            head += "\n  Did you mean: %s?" % ", ".join(self.suggestions)
        return head

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "path": self.path,
            "suggestions": list(self.suggestions),
        }


def worst(findings: Iterable[Diagnostic]) -> str:
    """The worst grade among the findings, or ``note`` for none."""
    grades = {d.severity for d in findings}
    for grade in (ERROR, WARNING):
        if grade in grades:
            return grade
    return NOTE


def as_text(findings: Sequence[Diagnostic]) -> str:
    if not findings:
        return "No findings."
    return "\n".join(str(d) for d in findings)


def as_json(findings: Sequence[Diagnostic]) -> str:
    return json.dumps([d.as_dict() for d in findings],
                      ensure_ascii=False, indent=1)


def as_sarif(findings: Sequence[Diagnostic]) -> str:
    """SARIF 2.1.0, as much of it as makes sense here.

    With it a CI annotates the changed lines itself, instead of somebody
    reading a log.
    """
    rules: dict[str, dict[str, str]] = {}
    results: list[dict[str, Any]] = []
    for finding in findings:
        rules.setdefault(finding.code, {"id": finding.code})
        results.append({
            "ruleId": finding.code,
            "level": SARIF_LEVEL.get(finding.severity, "warning"),
            "message": {"text": finding.message},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": finding.file},
                    "region": {"startLine": max(finding.line, 1),
                               "startColumn": max(finding.column, 1)},
                },
            }],
        })
    return json.dumps({
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {"driver": {"name": "ct4",
                                "rules": list(rules.values())}},
            "results": results,
        }],
    }, ensure_ascii=False, indent=1)


FORMATS = {"text": as_text, "json": as_json, "sarif": as_sarif}


def render(findings: Sequence[Diagnostic], form: str = "text") -> str:
    try:
        return FORMATS[form](findings)
    except KeyError:
        raise ValueError("unknown form: %s" % form) from None
