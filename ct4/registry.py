"""Anwendungen melden sich selbst an.

Ein Paket traegt sich in seiner eigenen Metadatei ein:

    [project.entry-points."ct4.plugins"]
    weewx = "weewx.ct4:plugin"

Danach findet ct4 es ueber die installierte Umgebung, ohne dass jemand
eine Einstellung setzt. Das ist der Unterschied zu ct3s
``macroDirectives``: das war ein Compiler-Setting, musste durch jede
``Template``-Konstruktion getragen werden, und ein Werkzeug, das nur die
Datei ansieht, fand es nie.

Ein Plugin ist ein Modul oder ein Objekt. Was es kann, wird gefragt, nicht
verlangt:

``declare()``
    gibt eine ``Declaration`` zurueck: welche Namen es gibt.
``install()``
    haengt Typ-Adapter ein, damit Werte sich selbst erklaeren.

Fehlt eines davon, ist das kein Fehler. Ein Plugin, das nur Typen
anmeldet, ist ein vollstaendiges Plugin.

In diesem Repo findet die Suche nichts: ct4 ist hier nicht installiert,
und Entry Points gibt es nur fuer installierte Pakete. Die eingecheckten
Anmeldungen unter ``declarations/`` bleiben deshalb der Hauptweg.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ct4.declare import Declaration

GROUP = "ct4.plugins"


@dataclass(frozen=True)
class Plugin:
    """Ein gefundenes Plugin und was es kann."""

    name: str
    target: Any

    def can(self, what: str) -> bool:
        return callable(getattr(self.target, what, None))

    def call(self, what: str) -> Any:
        return getattr(self.target, what)()


def entry_points(loader: Callable[[], Any] | None = None) -> list[Any]:
    """Die Eintraege der Gruppe, oder nichts, wenn es keine gibt."""
    if loader is not None:
        return list(loader())
    from importlib.metadata import entry_points as builtin

    try:
        return list(builtin(group=GROUP))
    except Exception:                                   # noqa: BLE001
        return []


def discover(loader: Callable[[], Any] | None = None) -> list[Plugin]:
    """Laedt alle gefundenen Plugins.

    Ein Plugin, das sich nicht laden laesst, wird uebergangen und nicht
    zum Fehler des ganzen Laufs. Ein kaputtes Fremdpaket soll ``ct4
    check`` nicht unbrauchbar machen.
    """
    found = []
    for entry in entry_points(loader):
        try:
            found.append(Plugin(entry.name, entry.load()))
        except Exception:                               # noqa: BLE001
            continue
    return found


def declarations(plugins: list[Plugin] | None = None) -> list[Declaration]:
    """Was die Plugins an Namen anmelden."""
    out = []
    for plugin in (discover() if plugins is None else plugins):
        if plugin.can("declare"):
            result = plugin.call("declare")
            if isinstance(result, Declaration):
                out.append(result)
    return out


def install_all(plugins: list[Plugin] | None = None) -> list[str]:
    """Haengt die Typ-Adapter aller Plugins ein.

    Gibt zurueck, welche das getan haben. Wer wissen will, ob ein
    bestimmtes Plugin gegriffen hat, sieht dort nach; ein stilles
    Nichtstun waere schwer zu finden.
    """
    done = []
    for plugin in (discover() if plugins is None else plugins):
        if plugin.can("install"):
            plugin.call("install")
            done.append(plugin.name)
    return done
