"""Uebersetzte Vorlagen auf der Platte behalten.

ct3 merkt sich uebersetzte Vorlagen in einem ``dict`` im Prozess. Ein
Daemon zahlt das Uebersetzen einmal, jeder frische Prozess zahlt es
voll. Gemessen an den 136 Skin-Vorlagen des Korpus sind das 7,7 ms je
Vorlage, gut eine Sekunde je Lauf. Bei ``weectl report run`` und in einer
Agent-Schleife ist das der groesste einzelne Posten.

Eingehaengt wird an der Stelle, die ct3 dafuer vorsieht:
``Template._CHEETAH_compilerClass``. Der Cache ersetzt also nicht das
Uebersetzen, er ueberspringt es. Was danach passiert, macht ct3
unveraendert selbst, und deshalb kann hier nichts auseinanderlaufen.

Der Modulname geht nicht in den Schluessel ein: er wechselt bei jedem
dynamischen Uebersetzen, und der erzeugte Code haengt nachweislich nicht
an ihm. Der Klassenname sehr wohl.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

# Bei einem Wechsel des Formats oder der Schluesselbildung hochzaehlen.
# Alte Eintraege werden dann nicht gelesen statt falsch gelesen.
FORMAT = 1

DEFAULT_DIR = Path(os.environ.get("CT4_CACHE_DIR", ".ct4-cache"))


def key_for(source: str, class_name: str, base_class: str | None,
            main_method: str | None, settings: Any) -> str:
    """Der Schluessel eines Uebersetzungsergebnisses.

    Alles, was den erzeugten Text beeinflusst, geht ein: die Quelle, die
    Namen, die Einstellungen und die Version von Cheetah, deren Nummer im
    erzeugten Modul steht.
    """
    from Cheetah.Version import Version

    digest = hashlib.sha256()
    for part in (str(FORMAT), Version, source, class_name or "",
                 base_class or "", main_method or "", repr(sorted(
                     (str(k), repr(v)) for k, v in (settings or {}).items()))):
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


class Store:
    """Ein Verzeichnis voller erzeugter Module."""

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or DEFAULT_DIR)
        self.hits = 0
        self.misses = 0

    def path_for(self, key: str) -> Path:
        # Zwei Zeichen als Unterverzeichnis. Ein Verzeichnis mit
        # zehntausend Dateien ist auf manchen Dateisystemen langsam.
        return self.directory / key[:2] / (key[2:] + ".py")

    def get(self, key: str) -> str | None:
        path = self.path_for(key)
        try:
            code = path.read_text(encoding="utf-8")
        except OSError:
            self.misses += 1
            return None
        self.hits += 1
        return code

    def put(self, key: str, code: str) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Erst daneben schreiben, dann umbenennen. Zwei Prozesse, die
        # dieselbe Vorlage uebersetzen, sollen sich keine halbe Datei
        # hinterlassen.
        temporary = path.with_suffix(".%d.tmp" % os.getpid())
        temporary.write_text(code, encoding="utf-8", newline="\n")
        os.replace(temporary, path)

    def clear(self) -> None:
        import shutil

        shutil.rmtree(self.directory, ignore_errors=True)


def caching_compiler(store: Store) -> type:
    """Baut eine Compiler-Klasse, die den Cache benutzt."""
    from Cheetah.Compiler import ModuleCompiler

    class CachingCompiler(ModuleCompiler):    # type: ignore[misc]
        """Uebersetzt nur, was noch nicht uebersetzt ist."""

        def __init__(self, source: Any = None, file: Any = None,
                     **kwargs: Any) -> None:
            super().__init__(source, file, **kwargs)
            self._ct4_cached: str | None = None
            self._ct4_key: str | None = None
            if source is not None:
                self._ct4_key = key_for(
                    source,
                    kwargs.get("mainClassName") or "",
                    kwargs.get("baseclassName"),
                    kwargs.get("mainMethodName"),
                    kwargs.get("settings"))

        def compile(self) -> None:
            if self._ct4_key is not None:
                self._ct4_cached = store.get(self._ct4_key)
                if self._ct4_cached is not None:
                    return
            super().compile()

        def getModuleCode(self) -> str:
            if self._ct4_cached is not None:
                return str(self._ct4_cached)
            code = str(super().getModuleCode())
            if self._ct4_key is not None:
                store.put(self._ct4_key, code)
            return code

    return CachingCompiler


def install(directory: Path | None = None) -> Store:
    """Haengt den Cache in Cheetah ein und gibt ihn zurueck.

    Nur fuer Vorlagen aus einer Zeichenkette. Eine Vorlage aus einer
    Datei traegt ihren Pfad im erzeugten Modul; sie zu cachen brauchte
    zusaetzlich die mtime, und den Fall gibt es hier noch nicht.
    """
    from Cheetah.Template import Template

    store = Store(directory)
    Template._CHEETAH_compilerClass = caching_compiler(store)
    return store


def uninstall() -> None:
    from Cheetah.Compiler import Compiler
    from Cheetah.Template import Template

    Template._CHEETAH_compilerClass = Compiler
