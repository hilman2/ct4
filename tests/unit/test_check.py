"""Der Pruefstand: was er meldet und was er uebersieht."""

from __future__ import annotations

import pytest

from ct4.corpus import check as pruefstand
from ct4.corpus.case import COMPILE, Case, write_jsonl
from ct4.corpus.check import (check, check_files, compare, compile_code,
                              normalize_code, produce)


@pytest.fixture
def verteilt_erzwingen(monkeypatch):
    """Setzt die Schwelle ausser Kraft, ab der verteilt wird.

    Ohne das liefen die Tests der Parallelisierung unbemerkt seriell und
    behaupteten trotzdem, sie zu pruefen."""
    monkeypatch.setattr(pruefstand, "PARALLEL_THRESHOLD", 0)


TREFFER = Case(id="treffer", template="$aStr", expected="blarg")


def test_versionszeilen_fallen_raus():
    code = ("__CHEETAH_version__ = '3.4.0'\n"
            "__CHEETAH_versionTuple__ = (3, 4, 0)\n"
            "write('hallo')\n")
    assert normalize_code(code) == "write('hallo')"


def test_versionszeile_mitten_im_text_bleibt():
    # Nur Zeilenanfaenge zaehlen. Eine Vorlage, die den Namen als Text
    # ausgibt, darf nicht stillschweigend gekuerzt werden.
    code = "write('__CHEETAH_version__ = x')"
    assert normalize_code(code) == code


def test_treffer_meldet_nichts():
    assert compare(TREFFER) is None


def test_abweichung_wird_gemeldet():
    fall = Case(id="daneben", template="$aStr", expected="etwas anderes")
    abweichung = compare(fall)
    assert abweichung is not None
    assert abweichung.actual == "blarg"
    assert "blarg" in abweichung.diff()


def test_ausnahme_wird_zur_abweichung():
    fall = Case(id="kaputt", template="#for x in\n", expected="")
    abweichung = compare(fall)
    assert abweichung is not None
    assert abweichung.error
    assert abweichung.actual == ""


def test_uebersetzungsfall_vergleicht_den_modulcode():
    fall = Case(id="u", template="$aStr", expected="", kind=COMPILE)
    code = produce(fall)
    assert "def respond" in code
    assert code == compile_code(fall)


def test_uebersetzungsfall_ist_zwischen_laeufen_gleich():
    # Ohne diese Eigenschaft waere der Korpus wertlos: der erzeugte
    # Modulcode traegt sonst einen Zeitstempel.
    fall = Case(id="u", template="$aStr", expected="", kind=COMPILE)
    assert compile_code(fall) == compile_code(fall)


def test_verteilt_und_seriell_kommen_zum_selben_ergebnis(
        tmp_path, verteilt_erzwingen):
    # Die Eigenschaft, an der die ganze Parallelisierung haengt.
    faelle = []
    for index in range(500):
        erwartet = "blarg" if index % 7 else "falsch"
        faelle.append(Case(id="f%d" % index, template="$aStr",
                           expected=erwartet))
    pfad = tmp_path / "korpus.jsonl"
    write_jsonl(faelle, pfad)

    anzahl_seriell, seriell = check(faelle)
    anzahl_verteilt, verteilt = check_files([pfad], jobs=4)

    assert anzahl_seriell == anzahl_verteilt == 500
    assert [m.case.id for m in seriell] == [m.case.id for m in verteilt]


def test_reihenfolge_der_abweichungen_folgt_dem_korpus(
        tmp_path, verteilt_erzwingen):
    faelle = [Case(id="f%d" % i, template="$aStr", expected="falsch")
              for i in range(300)]
    pfad = tmp_path / "korpus.jsonl"
    write_jsonl(faelle, pfad)
    _, abweichungen = check_files([pfad], jobs=4)
    assert [m.case.id for m in abweichungen] == [f.id for f in faelle]


def test_kleiner_korpus_laeuft_seriell():
    # Unter der Schwelle kostet das Starten der Prozesse mehr, als das
    # Verteilen einbringt.
    assert not pruefstand.use_pool(10, jobs=8)


def test_grosser_korpus_wird_verteilt():
    assert pruefstand.use_pool(pruefstand.PARALLEL_THRESHOLD, jobs=8)


def test_ein_prozess_bleibt_ein_prozess():
    # -j1 muss seriell bleiben, egal wie gross der Korpus ist. Sonst
    # gibt es keinen Weg, einen Fehler ohne Prozessgrenzen zu suchen.
    assert not pruefstand.use_pool(1_000_000, jobs=1)
