"""Turn recorded contexts into corpus cases.

``ct4.fixture.weewx_capture`` stores one file per generated page, with
template, context and output. Here that becomes a case which the test
bench can render without weewx.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ct4.corpus.case import FIXTURE, RENDER, Case


def harvest(root: Path, name: str) -> tuple[list[Case], Counter[str]]:
    """Reads the recordings under ``root`` and makes cases of them."""
    cases: list[Case] = []
    skipped: Counter[str] = Counter()
    for path in sorted(root.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if not record.get("context"):
            skipped["no context"] += 1
            continue
        cases.append(Case(
            id="%s/%s" % (name, path.stem),
            template=record["template"],
            expected=record["expected"],
            kind=RENDER,
            namespace=FIXTURE,
            context=record["context"],
            filter=record.get("filter", ""),
            origin=record.get("template_path", name),
        ))
    return cases, skipped
