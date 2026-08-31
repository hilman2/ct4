"""Aufgezeichnete Kontexte in Korpusfaelle umwandeln.

``ct4.fixture.weewx_capture`` legt je erzeugter Seite eine Datei mit
Vorlage, Kontext und Ausgabe ab. Hier wird daraus ein Fall, den der
Pruefstand ohne weewx rendern kann.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ct4.corpus.case import FIXTURE, RENDER, Case


def harvest(root: Path, name: str) -> tuple[list[Case], Counter[str]]:
    """Liest die Aufzeichnungen unter ``root`` und macht Faelle daraus."""
    cases: list[Case] = []
    skipped: Counter[str] = Counter()
    for path in sorted(root.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if not record.get("context"):
            skipped["kein Kontext"] += 1
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
