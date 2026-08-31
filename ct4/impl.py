"""Auswahl der Cheetah-Implementierung, gegen die gemessen wird.

Der Pruefstand vergleicht zwei Implementierungen, die beide unter dem
Namen ``Cheetah`` importiert werden: den Fork in diesem Repo und das per
pip installierte ct3. Welche von beiden gewinnt, haengt allein daran, ob
das Repo-Wurzelverzeichnis auf ``sys.path`` steht. Deshalb wird die Wahl
hier einmal explizit getroffen und nicht dem Aufrufer und seinen
Umgebungsvariablen ueberlassen.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORK = "fork"
INSTALLED = "installed"
CHOICES = (FORK, INSTALLED)


def select(impl: str) -> None:
    """Legt fest, welches Cheetah-Paket ein spaeterer Import findet.

    Muss laufen, bevor irgendetwas ``Cheetah`` importiert hat. Bei
    ``installed`` faellt das Repo-Wurzelverzeichnis aus ``sys.path``, so
    dass nur noch das installierte ct3 uebrig bleibt. Das Paket ``ct4``
    ist zu diesem Zeitpunkt bereits geladen und ueberlebt das.
    """
    if impl not in CHOICES:
        raise ValueError("unbekannte Implementierung: %s" % impl)
    if "Cheetah" in sys.modules:
        raise RuntimeError(
            "Cheetah ist bereits importiert; select() kam zu spaet")
    if impl == INSTALLED:
        sys.path[:] = [
            entry for entry in sys.path
            if os.path.abspath(entry or os.curdir) != REPO_ROOT
        ]


def describe() -> str:
    """Version und Dateipfad des tatsaechlich geladenen Cheetah.

    Der Pfad steht dabei, weil die Version allein nicht verraet, ob der
    Fork oder das installierte Paket gewonnen hat: beide melden waehrend
    P0 dieselbe Versionsreihe.
    """
    import Cheetah
    from Cheetah import NameMapper
    from Cheetah.Version import Version

    return "%s  %s  C-NameMapper=%s" % (
        Version, os.path.dirname(Cheetah.__file__), NameMapper.C_VERSION)
