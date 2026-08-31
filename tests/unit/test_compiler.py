"""Determinismus, Zwischenspeicher und die Zuordnung in die Vorlage."""

from __future__ import annotations

import traceback

import pytest
from Cheetah.Template import Template

from ct4 import cache, trace
from ct4.jsonmode import compile_template


def uebersetzen(quelle):
    klasse = Template.compile(source=quelle, keepRefToGeneratedCode=True,
                              useCache=False, cacheCompilationResults=False)
    return klasse, klasse._CHEETAH_generatedModuleCode


# -- Determinismus ---------------------------------------------------

def test_zwei_uebersetzungen_liefern_dieselben_bytes():
    # Ohne das laesst sich weder etwas vergleichen noch zwischenspeichern.
    _, a = uebersetzen("Hallo $name\n")
    _, b = uebersetzen("Hallo $name\n")
    assert a == b


def test_kein_zeitstempel_im_erzeugten_modul():
    _, code = uebersetzen("Hallo $name\n")
    assert "__CHEETAH_genTimestamp__" not in code
    assert "__CHEETAH_genTime__" not in code


# -- Zwischenspeicher ------------------------------------------------

@pytest.fixture
def speicher(tmp_path):
    store = cache.install(tmp_path)
    yield store
    cache.uninstall()


def test_zweiter_lauf_uebersetzt_nicht_noch_einmal(speicher):
    quelle = "Hallo $name und $andere\n"
    erste, code_a = uebersetzen(quelle)
    zweite, code_b = uebersetzen(quelle)
    assert speicher.hits == 1
    assert speicher.misses == 1
    assert code_a == code_b


def test_geaenderte_vorlage_bekommt_einen_neuen_platz(speicher):
    uebersetzen("Hallo $name\n")
    uebersetzen("Hallo $andere\n")
    assert speicher.hits == 0
    assert speicher.misses == 2


def test_andere_einstellungen_sind_ein_anderer_eintrag(speicher):
    quelle = "$x"
    Template.compile(source=quelle, useCache=False,
                     cacheCompilationResults=False)
    Template.compile(source=quelle, useCache=False,
                     cacheCompilationResults=False,
                     compilerSettings={"useAutocalling": False})
    assert speicher.hits == 0


def test_der_modulname_gehoert_nicht_zum_schluessel():
    # Er wechselt bei jedem dynamischen Uebersetzen. Wuerde er zaehlen,
    # traefe der Zwischenspeicher nie.
    erster = cache.key_for("$x", "K", None, None, {})
    zweiter = cache.key_for("$x", "K", None, None, {})
    assert erster == zweiter


def test_der_klassenname_gehoert_zum_schluessel():
    assert cache.key_for("$x", "A", None, None, {}) != \
        cache.key_for("$x", "B", None, None, {})


# -- In die Vorlage zeigen -------------------------------------------

class Sperrig:
    def kaputt(self):
        raise ValueError("die Datenbank antwortet nicht")


def test_traceback_nennt_die_zeile_der_vorlage():
    quelle = "Zeile eins\nZeile zwei\n$objekt.kaputt()\n"
    klasse, code = uebersetzen(quelle)
    template = klasse(searchList=[{"objekt": Sperrig()}])
    with pytest.raises(ValueError) as fehler:
        with trace.mapped(code, "bericht.tmpl"):
            template.respond()
    text = "".join(traceback.format_exception(fehler.value))
    assert "bericht.tmpl, Zeile 3" in text


def test_json_modus_nennt_die_zeile_der_vorlage():
    quelle = '#mode json\n{\n "a": 1,\n "b": $objekt.kaputt()\n}\n'
    compiled = compile_template(quelle, file="tag.json.tmpl")
    with pytest.raises(ValueError) as fehler:
        compiled.render([{"objekt": Sperrig()}])
    text = "".join(traceback.format_exception(fehler.value))
    assert "tag.json.tmpl, Zeile 4" in text


def test_ohne_fehler_wird_nichts_angehaengt():
    quelle = "Hallo $name\n"
    klasse, code = uebersetzen(quelle)
    with trace.mapped(code, "x.tmpl"):
        ausgabe = klasse(searchList=[{"name": "Welt"}]).respond()
    assert ausgabe == "Hallo Welt\n"


def test_zuordnung_nimmt_die_letzte_herkunft_davor():
    # Eine Anweisung erstreckt sich ueber mehrere erzeugte Zeilen. Die
    # Herkunft steht an ihrem Anfang, und die gilt bis zur naechsten.
    zuordnung = {10: (3, 1), 20: (7, 5)}
    assert trace.position_of(zuordnung, 10) == (3, 1)
    assert trace.position_of(zuordnung, 15) == (3, 1)
    assert trace.position_of(zuordnung, 20) == (7, 5)
    assert trace.position_of(zuordnung, 25) == (7, 5)
    assert trace.position_of(zuordnung, 5) is None
