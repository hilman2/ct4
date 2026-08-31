"""Ein Korpusfall und seine Ablage.

Die Ablage ist JSONL, eine Zeile je Fall. Das ist kein Zufall: bei
mehreren tausend Faellen bleibt ein Diff so lesbar, weil eine geaenderte
Vorlage genau eine Zeile beruehrt, und die Datei laesst sich zeilenweise
streamen, ohne sie ganz in den Speicher zu holen.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

CT3_DEFAULT = "ct3_default"
INLINE = "inline"

# Zwei Arten von Fall. "render" vergleicht die Ausgabe und braucht
# dafuer einen Kontext. "compile" vergleicht den erzeugten Modulcode
# und kommt ohne aus. Nur deshalb lassen sich fremde Skins in den
# Korpus nehmen, deren Kontext eine laufende Anwendung waere.
RENDER = "render"
COMPILE = "compile"

# Marke fuer einen Wert, den JSON nicht kennt. Bisher tritt nur einer
# auf: ct3 reicht in extraCompileKwArgs eine Basisklasse durch
# (`{'baseclass': dict}`) und erzeugt damit zu jeder Testklasse eine
# zweite Fassung. Ohne diese Marke faellt ein Drittel der Testsuite aus
# dem Korpus.
TYPE_TAG = "__type__"


@dataclass(frozen=True)
class Case:
    """Vorlage plus Kontext ergibt erwartete Ausgabe.

    ``namespace`` sagt, woher die searchList kommt: ``inline`` nimmt
    ``context``, jeder andere Wert benennt einen Erzeuger aus
    ``ct4.corpus.namespaces``. Die ct3-Testfaelle brauchen den zweiten
    Weg, weil ihr Kontext Lambdas und Instanzen enthaelt, die sich nicht
    als JSON ablegen lassen.
    """

    id: str
    template: str
    expected: str
    kind: str = RENDER
    namespace: str = CT3_DEFAULT
    context: list[Any] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    compile_kwargs: dict[str, Any] = field(default_factory=dict)
    origin: str = ""


def is_jsonable(value: Any) -> bool:
    """Ob sich der Wert verlustfrei als JSON ablegen laesst.

    Der Ernter braucht das, um Faelle auszusortieren, deren Kontext oder
    Compiler-Einstellungen Funktionen oder Instanzen enthalten. Ein Fall,
    den der Pruefstand spaeter nicht rekonstruieren kann, gehoert nicht
    in den Korpus, auch nicht halb.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def encode(value: Any) -> Any:
    """Macht einen Wert ablegbar, den JSON nicht kennt.

    Klassen werden zu ihrem gepunkteten Namen. Alles andere bleibt, wie
    es ist; ob es sich ablegen laesst, entscheidet danach
    ``is_jsonable``.
    """
    if isinstance(value, type):
        return {TYPE_TAG: "%s.%s" % (value.__module__, value.__qualname__)}
    if isinstance(value, dict):
        return {key: encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def decode(value: Any) -> Any:
    """Stellt her, was ``encode`` abgelegt hat."""
    if isinstance(value, dict) and TYPE_TAG in value and len(value) == 1:
        module_name, _, attribute = value[TYPE_TAG].rpartition(".")
        module = __import__(module_name, fromlist=[attribute])
        return getattr(module, attribute)
    if isinstance(value, dict):
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def write_jsonl(cases: Iterable[Case], path: Path) -> int:
    """Schreibt die Faelle und gibt ihre Anzahl zurueck."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(asdict(case), ensure_ascii=False))
            handle.write("\n")
            count += 1
    return count


def read_jsonl(path: Path) -> Iterator[Case]:
    """Liest die Faelle einer Datei, in der Reihenfolge der Datei."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield Case(**json.loads(line))
