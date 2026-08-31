"""Kontexte aus einem laufenden weewx mitschreiben.

Als pytest-Plugin gedacht. weewx bringt in ``src/weewx/tests`` alles mit,
was ein echter Report-Lauf braucht: erzeugte Messdaten, eine Datenbank,
Skins und die Report-Engine. Statt das nachzubauen, haengt sich dieses
Modul in den Lauf ein:

    pytest src/weewx/tests/test_templates.py -p ct4.fixture.weewx_capture

Eingehaengt wird ausschliesslich in weewx, nicht in Cheetah. Ein
Unterschieben von ``Cheetah.Template.Template`` geht nicht:
``Template.__init__`` schlaegt seinen eigenen Namen im Modul nach, und
eine Ersetzung fuehrt in eine Endlosschleife.

Heraus kommt je erzeugter Seite eine Datei mit der Vorlage, dem
aufgezeichneten Kontext und der Ausgabe, die weewx dabei geschrieben
hat. Aus diesen drei Stuecken wird ein Korpusfall, der ohne weewx laeuft.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ct4.fixture.record import Recorder

# Wohin die Aufzeichnungen gehen. Als Umgebungsvariable, weil das Plugin
# von pytest geladen wird und keine eigene Kommandozeile hat.
OUT_ENV = "CT4_FIXTURE_DIR"

# Was waehrend des Laufs zusammenkommt. Je erzeugter Seite ein Eintrag
# mit Vorlage, Ausgabepfad und aufgezeichnetem Kontext. Die Ausgabe
# selbst wird erst am Ende gelesen: waehrend des Laufs steht sie noch
# nicht auf der Platte.
_recorded: list[dict[str, Any]] = []


def pytest_configure(config: Any) -> None:
    install()


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    written = write_all(Path(os.environ.get(OUT_ENV, "fixtures")))
    print("\nct4: %d Aufzeichnungen geschrieben" % written)


def install() -> None:
    """Haengt den Mitschreiber in weewx' Cheetah-Generator ein."""
    import weewx.cheetahgenerator
    from weewx.cheetahgenerator import CheetahGenerator

    # weewx faengt jeden Fehler beim Uebersetzen und Auswerten einer
    # Vorlage, meldet ihn ins Log und macht weiter. Im Container geht
    # das Log nach syslog, das es nicht gibt, und ein Lauf ohne eine
    # einzige erzeugte Seite saehe erfolgreich aus. Deshalb bekommt
    # dieser Logger einen Ausgang nach stderr.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("weewx: %(levelname)s %(message)s"))
    weewx.cheetahgenerator.log.addHandler(handler)
    weewx.cheetahgenerator.log.setLevel(logging.ERROR)

    original_prep = CheetahGenerator._prepGen
    original_list = CheetahGenerator._getSearchList

    def _prepGen(self, report_dict):
        template, destination, encoding, binding = original_prep(
            self, report_dict)
        # Beide Angaben braucht erst _getSearchList, das sie nicht
        # bekommt. Der Generator ist der einzige Ort, an dem sie
        # zwischen den beiden Aufrufen ueberleben koennen.
        self._ct4_template = template
        self._ct4_destination = destination
        # Die Ausgabe wird als UTF-8 geschrieben, egal was der Skin
        # vorsieht. weewx kodiert die fertige Zeichenkette erst beim
        # Schreiben: 'html_entities' macht aus dem Grad-Zeichen &#176;,
        # 'strict_ascii' wirft Akzente weg. Das ist eine Eigenschaft des
        # Schreibens, nicht der Vorlage. Ein Fixture, das sie mitnaehme,
        # verlangte vom Pruefstand, weewx' Kodierer nachzubauen.
        return template, destination, "utf8", binding

    def _getSearchList(self, encoding, timespan, default_binding,
                       section_name, file_name):
        search_list = original_list(self, encoding, timespan,
                                    default_binding, section_name, file_name)
        trees: list[dict[str, Any]] = []
        wrapped = []
        for namespace in search_list:
            tree: dict[str, Any] = {}
            trees.append(tree)
            wrapped.append(Recorder(namespace, tree))
        _recorded.append({
            "template_path": self._ct4_template,
            "output_path": os.path.join(self._ct4_destination,
                                        os.path.basename(file_name)),
            "context": trees,
        })
        return wrapped

    CheetahGenerator._prepGen = _prepGen
    CheetahGenerator._getSearchList = _getSearchList


def write_all(out_dir: Path) -> int:
    """Legt die Aufzeichnungen des Laufs ab und gibt ihre Anzahl zurueck.

    Eine Aufzeichnung ohne Ausgabedatei faellt weg. Das passiert, wenn
    weewx die Vorlage nicht uebersetzen konnte; dann hat der Lauf ein
    anderes Problem, und ein Fixture ohne erwartete Ausgabe waere nur
    Ballast.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for entry in _recorded:
        output = Path(entry["output_path"])
        template = Path(entry["template_path"])
        if not output.exists() or not template.exists():
            continue
        record = {
            "template_path": str(template),
            "output_path": str(output),
            "template": template.read_text(encoding="utf-8"),
            "expected": output.read_text(encoding="utf-8"),
            "context": entry["context"],
            "filter": "weewx.AssureUnicode",
        }
        name = "_".join(template.parts[-3:]).replace(".", "_")
        target = out_dir / (name + ".json")
        target.write_text(
            json.dumps(record, ensure_ascii=False, indent=1),
            encoding="utf-8")
        written += 1
    return written
