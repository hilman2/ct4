"""Die Auswahl der Implementierung und die Skin-Ernte."""

from __future__ import annotations

import pytest

from ct4 import impl
from ct4.corpus import skins
from ct4.corpus.case import COMPILE


def test_unbekannte_implementierung_wird_abgelehnt():
    with pytest.raises(ValueError):
        impl.select("irgendwas")


def test_auswahl_nach_dem_import_wird_abgelehnt():
    # Ein spaeter Aufruf waere wirkungslos, und wirkungslos ist hier das
    # Schlimmste: der Lauf misst dann die falsche Implementierung und
    # meldet trotzdem gruen.
    import Cheetah                                     # noqa: F401

    with pytest.raises(RuntimeError):
        impl.select(impl.INSTALLED)


def test_beschreibung_nennt_pfad_und_c_erweiterung():
    beschreibung = impl.describe()
    assert "C-NameMapper=" in beschreibung
    assert "Cheetah" in beschreibung


def test_skin_ernte_findet_beide_endungen(tmp_path):
    (tmp_path / "seite.html.tmpl").write_text("$aStr", encoding="utf-8")
    (tmp_path / "teil.inc").write_text("$anInt", encoding="utf-8")
    (tmp_path / "keine-vorlage.css").write_text("body{}", encoding="utf-8")

    faelle, uebersprungen = skins.harvest(tmp_path, "probe")

    assert not uebersprungen
    assert {f.id for f in faelle} == {"probe/seite.html.tmpl",
                                      "probe/teil.inc"}
    assert all(f.kind == COMPILE and f.expected for f in faelle)


def test_unuebersetzbare_vorlage_wird_gezaehlt_nicht_abgelegt(tmp_path):
    (tmp_path / "kaputt.tmpl").write_text("#for x in\n", encoding="utf-8")
    faelle, uebersprungen = skins.harvest(tmp_path, "probe")
    assert faelle == []
    assert sum(uebersprungen.values()) == 1


def test_unterverzeichnisse_kommen_mit(tmp_path):
    tief = tmp_path / "a" / "b"
    tief.mkdir(parents=True)
    (tief / "seite.tmpl").write_text("$aStr", encoding="utf-8")
    faelle, _ = skins.harvest(tmp_path, "probe")
    assert [f.id for f in faelle] == ["probe/a/b/seite.tmpl"]
