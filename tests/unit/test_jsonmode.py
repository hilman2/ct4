"""Der JSON-Modus.

Die Zusicherungen aus PLAN.md, Abschnitt 4, jede als Test. Was hier
gruen ist, kann ein Skin-Autor nicht mehr falsch machen.
"""

from __future__ import annotations

import json

import pytest

from ct4.adapters import Ct4Value
from ct4.jsonmode import render
from ct4.jsonmode.build import MissingValue
from ct4.jsonmode.parse import JsonTemplateError, parse


class Messwert:
    """Wie weewx' ValueHelper: kennt Rohwert, Stellen und Formatierung."""

    def __init__(self, wert, stellen=1, einheit="°C"):
        self.wert = wert
        self.stellen = stellen
        self.einheit = einheit

    def __ct4_value__(self):
        return Ct4Value(self.wert, precision=self.stellen)

    def __str__(self):
        if self.wert is None:
            return "N/A"
        return "%.*f %s" % (self.stellen, self.wert, self.einheit)


class Punkt:
    def __init__(self, start, value):
        self.start = start
        self.value = value


KONTEXT = [{
    "station": 'Zuhause "am" Berg',
    "temp": Messwert(12.3456),
    "leer": Messwert(None),
    "id": 42,
    "zeilen": [Punkt(1, 3.14159), Punkt(2, None), Punkt(3, 2.71828)],
    "wahr": True,
    "null": None,
}]


def geladen(quelle, kontext=None, **kw):
    return json.loads(render(quelle, kontext or KONTEXT, **kw))


# -- Die Zusicherungen ----------------------------------------------

def test_kommas_sind_kein_autorenproblem():
    # Eines zu viel, eines zu wenig, beides ist in Ordnung: es gibt
    # keine Kommas, es gibt eine Struktur.
    assert geladen('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}
    assert geladen('{"a": 1 "b": 2}') == {"a": 1, "b": 2}
    assert geladen('[1, 2, 3,]') == [1, 2, 3]


def test_schleife_am_ende_ohne_komma():
    quelle = '{"r": [#for $p in $zeilen\n$p.start\n#end for]}'
    assert geladen(quelle) == {"r": [1, 2, 3]}


def test_leere_schleife_laesst_nichts_zurueck():
    quelle = '{"r": [#for $p in []\n$p\n#end for]}'
    assert geladen(quelle) == {"r": []}


def test_zahlen_bleiben_zahlen():
    ergebnis = geladen('{"t": $temp}')
    assert ergebnis == {"t": 12.3}
    assert isinstance(ergebnis["t"], float)


def test_fehlender_wert_wird_null():
    assert geladen('{"t": $leer}') == {"t": None}


def test_fehlender_wert_kann_weggelassen_werden():
    assert geladen('#missing omit\n{"t": $leer, "i": $id}') == {"i": 42}


def test_fehlender_wert_kann_ein_fehler_sein():
    with pytest.raises(MissingValue):
        render('#missing error\n{"t": $leer}', KONTEXT)


def test_fehlendes_element_bleibt_null_trotz_omit():
    # In einer Liste wuerde Weglassen die Stellen verschieben.
    quelle = '#missing omit\n{"r": [$leer, $id]}'
    assert geladen(quelle) == {"r": [None, 42]}


def test_anfuehrungszeichen_zerlegen_die_datei_nicht():
    assert geladen('{"s": $station}') == {"s": 'Zuhause "am" Berg'}


def test_umlaute_bleiben_umlaute():
    text = render('{"s": "grün"}', KONTEXT)
    assert "grün" in text


def test_wahrheitswerte_und_null_aus_der_vorlage():
    assert geladen('{"a": true, "b": false, "c": null}') == {
        "a": True, "b": False, "c": None}


# -- Praezision ------------------------------------------------------

def test_praezision_kommt_vom_objekt():
    assert geladen('{"t": $temp}') == {"t": 12.3}


def test_vorlage_schlaegt_objekt():
    assert geladen('{"t": $temp @ 3}') == {"t": 12.346}


def test_vorgabe_gilt_wo_das_objekt_nichts_sagt():
    quelle = '#precision default = 2\n{"x": 3.14159}'
    assert geladen(quelle) == {"x": 3.14}


def test_praezision_je_feldname():
    quelle = '#precision default = 3\n#precision kurz = 0\n' \
             '{"kurz": 3.9, "lang": 3.14159}'
    assert geladen(quelle) == {"kurz": 4, "lang": 3.142}


def test_rundung_macht_keine_zeichenkette():
    ergebnis = geladen('{"x": $temp @ 0}')
    assert ergebnis == {"x": 12}
    assert not isinstance(ergebnis["x"], str)


# -- Wert oder Text --------------------------------------------------

def test_an_der_wertposition_zaehlt_der_wert():
    assert geladen('{"t": $temp}') == {"t": 12.3}


def test_in_der_zeichenkette_zaehlt_die_formatierung():
    # Der Unterschied ist Absicht. Im Text will der Autor das, was das
    # Objekt selbst anzeigt, samt Einheit.
    assert geladen('{"t": "$temp"}') == {"t": "12.3 °C"}


def test_schluessel_darf_aus_werten_entstehen():
    assert geladen('{"kanal-$id": 1}') == {"kanal-42": 1}


# -- Steuerung -------------------------------------------------------

def test_if_laesst_ein_feld_weg():
    quelle = '{"a": 1,\n#if $null\n"b": 2,\n#end if\n"c": 3}'
    assert geladen(quelle) == {"a": 1, "c": 3}


def test_if_else():
    quelle = '{\n#if $null\n"a": 1\n#else\n"b": 2\n#end if\n}'
    assert geladen(quelle) == {"b": 2}


def test_elif():
    quelle = '{\n#if $null\n"a": 1\n#elif $wahr\n"b": 2\n#else\n"c": 3\n' \
             '#end if\n}'
    assert geladen(quelle) == {"b": 2}


def test_verschachtelte_schleife():
    quelle = '{"r": [#for $p in $zeilen\n{"t": $p.start}\n#end for]}'
    assert geladen(quelle) == {"r": [{"t": 1}, {"t": 2}, {"t": 3}]}


# -- Reihen ----------------------------------------------------------

def test_reihe_als_datensaetze():
    quelle = '{"r": #series($zeilen, fields=["start", "value"], ' \
             'precision=2)}'
    assert geladen(quelle) == {"r": [
        {"start": 1, "value": 3.14},
        {"start": 2, "value": None},
        {"start": 3, "value": 2.72}]}


def test_reihe_als_spalten():
    quelle = '{"r": #series($zeilen, layout="columns", ' \
             'fields=["start", "value"], precision=2)}'
    assert geladen(quelle) == {"r": {"start": [1, 2, 3],
                                     "value": [3.14, None, 2.72]}}


def test_reihe_als_paare_ohne_luecken():
    quelle = '{"r": #series($zeilen, layout="pairs", ' \
             'fields=["start", "value"], gaps="omit")}'
    assert geladen(quelle) == {"r": [[1, 3.14159], [3, 2.71828]]}


# -- Determinismus ---------------------------------------------------

def test_zwei_laeufe_liefern_dieselben_bytes():
    quelle = '{"b": 2, "a": 1, "r": [#for $p in $zeilen\n$p.start\n' \
             '#end for]}'
    erster = render(quelle, KONTEXT)
    assert erster == render(quelle, KONTEXT)


def test_schluessel_behalten_die_reihenfolge_der_vorlage():
    text = render('{"z": 1, "a": 2, "m": 3}', KONTEXT)
    assert text.index('"z"') < text.index('"a"') < text.index('"m"')


# -- Fehler ----------------------------------------------------------

def test_unbekannte_direktive_wird_gemeldet():
    with pytest.raises(JsonTemplateError):
        parse('{\n#foo\n"a": 1\n#end foo\n}')


def test_offener_block_wird_gemeldet():
    with pytest.raises(JsonTemplateError):
        parse('{"r": [#for $p in $zeilen\n$p\n]}')


def test_falsches_end_wird_gemeldet():
    with pytest.raises(JsonTemplateError):
        parse('{\n#if $wahr\n"a": 1\n#end for\n}')


def test_fehlermeldung_nennt_zeile_und_spalte():
    with pytest.raises(JsonTemplateError) as fehler:
        parse('{\n  "a": nichts\n}')
    assert "Zeile 2" in str(fehler.value)


def test_kommentar_wie_in_cheetah():
    assert geladen('{\n## das hier ist weg\n"a": 1\n}') == {"a": 1}
