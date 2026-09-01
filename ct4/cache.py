"""Keep compiled templates on disk.

ct3 remembers compiled templates in a ``dict`` inside the process. A
daemon pays for compiling once, every fresh process pays in full.
Measured against the 136 skin templates of the corpus that is 7.7 ms per
template, a good second per run. For ``weectl report run`` and in an
agent loop it is the largest single item.

It hooks in where ct3 provides for it:
``Template._CHEETAH_compilerClass``. So the cache does not replace
compiling, it skips it. What happens afterwards ct3 still does itself,
unchanged, and that is why nothing here can drift apart.

The module name is not part of the key: it changes with every dynamic
compilation, and the generated code demonstrably does not depend on it.
The class name very much does.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from ct4 import write

# Bump on a change of the format or of how the key is built. Old entries
# are then not read at all instead of read wrongly.
FORMAT = 1

DEFAULT_DIR = Path(os.environ.get("CT4_CACHE_DIR", ".ct4-cache"))


def key_for(source: str, class_name: str, base_class: str | None,
            main_method: str | None, settings: Any) -> str:
    """The key of a compilation result.

    Everything that influences the generated text goes in: the source,
    the names, the settings and the version of Cheetah, whose number
    stands in the generated module.
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
    """A directory full of generated modules."""

    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or DEFAULT_DIR)
        self.hits = 0
        self.misses = 0

    def path_for(self, key: str) -> Path:
        # Two characters as a subdirectory. A directory with ten
        # thousand files is slow on some file systems.
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
        # Write beside it first, then replace. Two processes compiling
        # the same template should not leave half a file behind. No
        # content comparison here: an entry that exists is by
        # construction the right bytes, because the key is a digest of
        # everything the code depends on.
        write.atomic_write(self.path_for(key), code.encode("utf-8"))

    def clear(self) -> None:
        import shutil

        shutil.rmtree(self.directory, ignore_errors=True)


def caching_compiler(store: Store) -> type:
    """Builds a compiler class that uses the cache."""
    from Cheetah.Compiler import ModuleCompiler

    class CachingCompiler(ModuleCompiler):    # type: ignore[misc]
        """Compiles only what is not compiled yet."""

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
    """Hooks the cache into Cheetah and returns it.

    Only for templates from a string. A template from a file carries its
    path in the generated module; caching it would need the mtime as
    well, and that case does not exist here yet.
    """
    from Cheetah.Template import Template

    store = Store(directory)
    Template._CHEETAH_compilerClass = caching_compiler(store)
    return store


def uninstall() -> None:
    from Cheetah.Compiler import Compiler
    from Cheetah.Template import Template

    Template._CHEETAH_compilerClass = Compiler
