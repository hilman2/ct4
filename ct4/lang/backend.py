"""Let the new code generator compile, and ct3 take what it will not.

The generator has been measured against ct3 for weeks and nothing has
called it. A layer with no caller is unproven however green its numbers
are, and it stays unproven until real templates go through it.

This hooks it in where ct3 provides for it, the same place the compile
cache uses: ``Template._CHEETAH_compilerClass``. That interface is two
methods, ``compile()`` and ``getModuleCode()``, and what comes back is
the text of a module ct3 execs and pulls a class out of. So the
generator does not have to replace anything ct3 does afterwards. It
produces the same kind of module, under the class name ct3 asked for,
and ct3 goes on as before.

Where it refuses, ct3's own compiler runs instead and the caller
notices nothing. That is the whole design: the generator says what it
can do, and what it cannot costs a fallback and not a failure. Which is
why ``Unsupported`` has been kept an honest signal all along.

    from ct4.lang import backend

    counts = backend.install()
    ...
    print(counts)                    # taken=336 fell_back=54

Nothing installs it by default. Rendering still goes through ct3 unless
somebody asks for this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ct4.lang import codegen, tree


@dataclass
class Counts:
    """How the work split between the two compilers.

    Read after a run to see what the generator is actually carrying,
    which is a different number from what the corpus says it could
    carry: a report renders the templates it has, not the ones a test
    bench holds.
    """

    taken: int = 0
    fell_back: int = 0

    def __str__(self) -> str:
        return "taken=%d fell_back=%d" % (self.taken, self.fell_back)


def generating_compiler(counts: Counts) -> type:
    """Builds a compiler class that tries the generator first."""
    from Cheetah.Compiler import ModuleCompiler

    class GeneratingCompiler(ModuleCompiler):  # type: ignore[misc]
        """ct3's compiler, with the generator in front of it."""

        def __init__(self, source: Any = None, file: Any = None,
                     **kwargs: Any) -> None:
            super().__init__(source, file, **kwargs)
            self._ct4_code: str | None = None
            self._ct4_source = source
            self._ct4_kwargs = kwargs

        def compile(self) -> None:
            # Only a template from a string. One from a file carries its
            # path into the generated module, and nothing here writes
            # that yet.
            if self._ct4_source is not None:
                try:
                    made = codegen.generate(
                        self._ct4_source,
                        self._ct4_kwargs.get("settings"),
                        class_name=(self._ct4_kwargs.get("mainClassName")
                                    or codegen.CLASS),
                        base_class=self._ct4_kwargs.get("baseclassName"),
                        main_method=self._ct4_kwargs.get("mainMethodName"))
                except (codegen.Unsupported, tree.StructureError):
                    counts.fell_back += 1
                else:
                    self._ct4_code = made.code
                    counts.taken += 1
                    return
            super().compile()

        def getModuleCode(self) -> str:
            if self._ct4_code is not None:
                return self._ct4_code
            return str(super().getModuleCode())

    return GeneratingCompiler


def install() -> Counts:
    """Hooks the generator into Cheetah and returns the tally."""
    from Cheetah.Template import Template

    counts = Counts()
    Template._CHEETAH_compilerClass = generating_compiler(counts)
    return counts


def uninstall() -> None:
    from Cheetah.Compiler import Compiler
    from Cheetah.Template import Template

    Template._CHEETAH_compilerClass = Compiler
