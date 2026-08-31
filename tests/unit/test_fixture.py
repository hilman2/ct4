"""Aufzeichnen und Abspielen eines Kontexts.

Der entscheidende Test ist ``test_abspielen_liefert_dieselbe_ausgabe``:
dieselbe Vorlage, einmal gegen die lebenden Objekte, einmal gegen die
abgelegte Aufzeichnung, und beides muss Byte fuer Byte gleich sein.
"""

from __future__ import annotations

import json

import pytest
from Cheetah.Template import Template

from ct4.fixture.record import Missing, Recorder, replay


class Messwert:
    """Steht fuer weewx' ValueHelper: formatiert sich, kennt seinen Rohwert."""

    def __init__(self, wert, form="%.1f"):
        self.raw = wert
        self._form = form

    def __str__(self):
        return "N/A" if self.raw is None else self._form % self.raw


class Aggregat:
    def __init__(self, kleinst, groesst):
        self.min = Messwert(kleinst)
        self.max = Messwert(groesst)


class Datensatz:
    def __init__(self, stempel, temperatur):
        self.dateTime = Messwert(stempel, "%d")
        self.outTemp = Messwert(temperatur)


class Tag:
    def __init__(self):
        self.outTemp = Aggregat(3.25, 17.5)
        self.rain = Aggregat(0.0, None)
        self.records = [Datensatz(100, 3.25), Datensatz(200, 17.5)]

    def span(self, delta=1):
        return Aggregat(delta, delta * 2)


VORLAGE = """\
Max: $day.outTemp.max, roh $day.outTemp.max.raw
Regen: $day.rain.max
#for $r in $day.records
  $r.dateTime = $r.outTemp
#end for
Spanne: $day.span(delta=3).max
"""


def _rendern(kontext):
    template = Template(VORLAGE, searchList=[{"day": kontext}])
    try:
        return template.respond()
    finally:
        template.shutdown()


def test_abspielen_liefert_dieselbe_ausgabe():
    baum = {}
    lebendig = _rendern(Recorder(Tag(), baum))
    # Der Umweg ueber JSON gehoert dazu: das Fixture liegt auf Platte.
    aufgezeichnet = json.loads(json.dumps(baum))
    assert _rendern(replay(aufgezeichnet)) == lebendig


def test_fehlender_wert_bleibt_als_none_erhalten():
    baum = {}
    lebendig = _rendern(Recorder(Tag(), baum))
    assert "N/A" in lebendig
    assert _rendern(replay(baum)) == lebendig


def test_ungelesenes_feld_meldet_sich():
    # Ein Fixture, das stillschweigend Leeres liefert, waere schlimmer
    # als keines: die Pruefung waere gruen und saehe nichts.
    baum = {}
    _rendern(Recorder(Tag(), baum))
    with pytest.raises(Missing) as fehler:
        replay(baum).outTemp.avg
    assert "avg" in str(fehler.value)
    assert "max" in str(fehler.value)


def test_unbekannter_aufruf_meldet_sich():
    baum = {}
    _rendern(Recorder(Tag(), baum))
    with pytest.raises(Missing):
        replay(baum).span(delta=99)


def test_aufzeichnung_ist_json():
    baum = {}
    _rendern(Recorder(Tag(), baum))
    assert json.dumps(baum)


def test_aufgezeichneter_kontext_ist_schreibgeschuetzt():
    with pytest.raises(TypeError):
        Recorder(Tag(), {}).outTemp = 1


class MitMethode:
    """Wie weewx' TimeBinder: $day ist eine Methode, kein Attribut."""

    def tag(self):
        return Aggregat(1.0, 2.0)


def test_gebundene_methode_wird_weiter_aufgerufen():
    # Cheetah ruft eine Methode ohne Klammern von selbst auf. Ein
    # Rekorder, der wie eine Instanz aussieht, bricht das, und
    # $day.hours findet dann nichts mehr.
    vorlage = "$obj.tag.max"
    baum = {}

    def rendern(kontext):
        template = Template(vorlage, searchList=[{"obj": kontext}])
        try:
            return template.respond()
        finally:
            template.shutdown()

    lebendig = rendern(Recorder(MitMethode(), baum))
    assert lebendig == "2.0"
    assert rendern(replay(json.loads(json.dumps(baum)))) == lebendig
