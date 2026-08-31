"""Kontexte einfrieren, damit Vorlagen ohne ihre Anwendung laufen.

Ein weewx-Skin liest aus Objekten, die eine laufende Anwendung samt
Datenbank bereitstellt. Zum Pruefen ist das zu teuer, und fuer einen
Agenten ist es unerreichbar.

Ein Fixture loest das, indem es einmal mitschreibt, was eine Vorlage aus
dem Kontext tatsaechlich liest, und das als JSON ablegt. Danach rendert
dieselbe Vorlage aus der Datei heraus, in Millisekunden, ohne die
Anwendung.
"""

from ct4.fixture.record import Recorder, replay

__all__ = ["Recorder", "replay"]
