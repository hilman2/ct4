"""Die Werkzeugschicht: analysieren, anmelden, pruefen, berichten."""

from __future__ import annotations

import json

import pytest

from ct4 import analyze, diagnostics, reference
from ct4.check import check_source, is_json_template, unresolved
from ct4.declare import Declaration, Node, resolve

# Eine Anmeldung wie die von weewx: Zeitraum, offener Messwert,
# geschlossene Aggregate.
WEEWX = Declaration(
    name="probe",
    roots={
        "day": Node(fields={
            "records": Node(open=True),
            "*": Node(fields={"max": Node(), "min": Node(),
                              "sum": Node(), "avg": Node()}),
        }),
        "station": Node(open=True),
    })


# -- Platzhalter finden ----------------------------------------------

def test_pfade_mit_zeile_und_spalte():
    found = analyze.placeholders("Hallo $user.name\n$day.outTemp.max\n")
    assert [(p.path, p.line) for p in found] == [
        ("user.name", 1), ("day.outTemp.max", 2)]


def test_schleifenvariable_kommt_mit():
    found = analyze.placeholders("#for $r in $rows\n$r.x\n#end for\n")
    assert analyze.paths(found) == ["r.x", "rows"]


def test_errorcatcher_verdeckt_nichts():
    # Seasons setzt #errorCatcher. Dann legt Cheetah jeden Platzhalter
    # in eine eigene Methode, und die Herkunft steht in einer anderen
    # Form. Ohne diesen Fall verlaeren die weewx-Skins ihre Platzhalter.
    quelle = "#errorCatcher Echo\n$day.rain.sum\n"
    assert analyze.paths(analyze.placeholders(quelle)) == ["day.rain.sum"]


def test_wurzeln_ohne_wiederholung():
    quelle = "$day.a.max $day.b.min $station.x"
    assert analyze.roots(analyze.placeholders(quelle)) == ["day", "station"]


# -- Anmeldung -------------------------------------------------------

def test_bekannter_pfad_gibt_nichts():
    assert resolve(WEEWX, "day.outTemp.max") is None


def test_unbekanntes_aggregat_wird_gemeldet():
    unknown = resolve(WEEWX, "day.outTemp.mx")
    assert unknown is not None
    assert unknown.name == "mx"
    assert unknown.suggestions == ("max",)


def test_offener_knoten_beendet_die_pruefung():
    assert resolve(WEEWX, "station.irgendwas.tiefer") is None


def test_unbekannte_wurzel_ist_kein_fehler():
    # Eine Wurzel, die niemand angemeldet hat, ist unbekanntes Gebiet,
    # kein Fehler. Alles andere waere ein Falschbefund.
    assert resolve(WEEWX, "voelligNeu.x") is None


def test_hin_und_zurueck_ueber_json(tmp_path):
    pfad = tmp_path / "probe.json"
    WEEWX.save(pfad)
    wieder = Declaration.load(pfad)
    assert resolve(wieder, "day.outTemp.mx") is not None
    assert resolve(wieder, "day.outTemp.max") is None


# -- Pruefen ---------------------------------------------------------

def test_tippfehler_wird_gefunden():
    found = check_source("$day.outTemp.mx\n", "probe.tmpl", [WEEWX])
    assert [d.code for d in found] == ["CT4103"]
    assert found[0].line == 1
    assert "max" in found[0].suggestions


def test_richtige_vorlage_bleibt_still():
    assert check_source("$day.outTemp.max\n", "probe.tmpl", [WEEWX]) == []


def test_syntaxfehler_nennt_ort():
    found = check_source("#for $x in $ys\nfoo\n", "probe.tmpl", [WEEWX])
    assert [d.code for d in found] == ["CT4001"]
    assert found[0].line


def test_unbekannte_wurzel_wird_auf_wunsch_gemeldet():
    found = unresolved("$fremd.x", [WEEWX])
    assert [d.code for d in found] == ["CT4110"]
    assert found[0].severity == diagnostics.WARNING


# -- Der JSON-Modus wird angesagt, nicht geraten ----------------------

def test_ansage_entscheidet():
    assert is_json_template("#mode json\n{}")
    assert is_json_template("## Kommentar\n#mode json\n{}")


def test_endung_entscheidet_nicht():
    # weewx-Skins liefern .json.tmpl aus, und das sind Textvorlagen.
    assert not is_json_template('{"a": $x}')


def test_json_vorlage_wird_als_dokument_geprueft(tmp_path):
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(
        {"type": "object", "required": ["fehlt"],
         "properties": {"fehlt": {"type": "string"}}}), encoding="utf-8")
    quelle = '#mode json\n#schema "s.json"\n{"da": 1}'
    found = check_source(quelle, "x.tmpl", [], base_dir=tmp_path)
    assert [d.code for d in found] == ["CT4200"]


def test_fehlendes_schema_ist_ein_befund(tmp_path):
    quelle = '#mode json\n#schema "gibtsnicht.json"\n{}'
    found = check_source(quelle, "x.tmpl", [], base_dir=tmp_path)
    assert [d.code for d in found] == ["CT4004"]


# -- Ausgabeformen ---------------------------------------------------

BEFUND = diagnostics.Diagnostic(
    "CT4103", diagnostics.ERROR, "kennt kein Feld", file="a.tmpl",
    line=3, column=7, path="$day.x", suggestions=("max",))


def test_text_nennt_ort_und_vorschlag():
    text = diagnostics.render([BEFUND], "text")
    assert "a.tmpl:3:7" in text
    assert "max" in text


def test_json_ist_json():
    data = json.loads(diagnostics.render([BEFUND], "json"))
    assert data[0]["code"] == "CT4103"
    assert data[0]["suggestions"] == ["max"]


def test_sarif_traegt_regel_und_ort():
    data = json.loads(diagnostics.render([BEFUND], "sarif"))
    result = data["runs"][0]["results"][0]
    assert result["ruleId"] == "CT4103"
    assert result["level"] == "error"
    region = result["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] == 3


def test_unbekannte_form_wird_abgelehnt():
    with pytest.raises(ValueError):
        diagnostics.render([], "xml")


def test_schwerster_grad():
    warnung = diagnostics.Diagnostic("X", diagnostics.WARNING, "")
    assert diagnostics.worst([warnung, BEFUND]) == diagnostics.ERROR
    assert diagnostics.worst([warnung]) == diagnostics.WARNING
    assert diagnostics.worst([]) == diagnostics.NOTE


# -- Referenz --------------------------------------------------------

def test_referenz_kommt_aus_den_tabellen_des_compilers():
    data = reference.reference()
    namen = {entry["name"] for entry in data["directives"]}
    assert {"for", "if", "def", "compiler-settings"} <= namen
    assert len(data["settings"]) > 40
    assert json.dumps(data)


def test_referenz_sagt_was_geschlossen_werden_muss():
    closeable = {entry["name"] for entry in reference.directives()
                 if entry["closeable"]}
    assert "for" in closeable
    assert "set" not in closeable
