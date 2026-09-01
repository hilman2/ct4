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

With one exception, and it is the only one: a template that declares
markup mode is never handed to ct3. ct3 has no such mode, so the
fallback would print the declaration line into the page and escape
nothing. There the refusal comes out as :class:`MarkupRefused`.

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
from ct4.markup import mode as markup_mode
from ct4.markup import scan as markup_scan


class MarkupRefused(Exception):
    """A markup-mode template the generator will not compile.

    Markup mode is the one place where a refusal may not become a
    fallback. ct3 knows nothing about the declaration line, so a
    template handed to it would print ``#mode markup`` as the first
    line of the page and escape nothing: the wrong page and the missing
    escape at once, which is worse than either. So the refusal travels
    out to the caller with its reason instead.
    """


def _declares_markup(path: str) -> bool:
    """Whether a template file asks for markup mode.

    Read here and not left to the generator because the file form
    never reaches it. A file that cannot be read is not a declaration:
    ct3 is about to open it and will raise its own error, which is a
    better message than one invented here.
    """
    if not path:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            # The declaration stands in the first lines or nowhere, and
            # a page is not worth reading whole to find that out.
            head = handle.read(4096)
    except OSError:
        return False
    return markup_mode.declared(head)


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
            # Only for the message a refused placeholder raises in
            # markup mode. It is empty for the string form, which is
            # the only form that reaches the generator today.
            self._ct4_file = file if isinstance(file, str) else ""

        def compile(self) -> None:
            # A template from a file never reaches the generator: its
            # path goes into the generated module and nothing here
            # writes that yet. That is fine for text mode and not fine
            # for markup, where falling back to ct3 prints the
            # declaration into the page and escapes nothing. So the
            # file is read for the one line that decides it.
            if self._ct4_source is None and _declares_markup(self._ct4_file):
                raise MarkupRefused(
                    "%s declares markup mode and was compiled from a file;"
                    " markup mode needs the source, so compile it from a"
                    " string or keep it in text mode" % self._ct4_file)
            if self._ct4_source is not None:
                try:
                    made = codegen.generate(
                        self._ct4_source,
                        self._ct4_kwargs.get("settings"),
                        class_name=(self._ct4_kwargs.get("mainClassName")
                                    or codegen.CLASS),
                        base_class=self._ct4_kwargs.get("baseclassName"),
                        main_method=self._ct4_kwargs.get("mainMethodName"),
                        file=self._ct4_file)
                except markup_scan.ScanRefused as refused:
                    raise MarkupRefused(str(refused)) from refused
                except (codegen.Unsupported, tree.StructureError) as refused:
                    if markup_mode.declared(self._ct4_source):
                        raise MarkupRefused(str(refused)) from refused
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
