"""How a JSON template reaches an application that only knows Cheetah.

JSON mode is not a code path a caller has to ask for. weewx renders a
report by handing ``Cheetah.Template.Template`` a file and calling
``respond()`` on what comes back, and it does that for every template
in a skin. A mode reachable only through ``ct4 build`` would therefore
be reachable from weewx not at all, and the plan's own rule is the
other way round: the mode comes out of the template, and the compiler
is what reads it.

So this makes a ``#mode json`` template into an ordinary compiled
Cheetah class whose main method returns the serialised document. The
application sees a class with a ``respond`` that gives it a string, the
same as always, and nothing in it has to know that the string was built
rather than concatenated.

It hangs on the same hook the compile cache and the code generator use,
``Template._CHEETAH_compilerClass``; :mod:`ct4.lang.backend` installs
it. Without that install nothing here runs and a JSON template renders
as the text it looks like, which is what ct3 does today.
"""

from __future__ import annotations

from pathlib import Path

from ct4.lang import codegen

# The module a JSON template becomes. Deliberately built out of the
# same INIT, ATTRIBUTES and PLUMBING the code generator uses: what ct3
# reads off a generated class is one list, and two places that write it
# would drift the day somebody adds to it.
#
# The source travels into the module as a constant rather than being
# re-read at render time. A report run may render the same template
# many times, and a file that changed underneath between the compile
# and the render would otherwise produce a document nobody asked for.
MODULE = """\
from Cheetah.Template import Template
from Cheetah.Version import Version as __CHEETAH_version__
from Cheetah.Version import VersionTuple as __CHEETAH_versionTuple__
from ct4 import jsonmode
from pathlib import Path


class %(name)s(%(base)s):
%(attributes)s
    _ct4_json_source = %(source)r
    _ct4_json_base = %(base_dir)r

    def %(main)s(self, trans=None):
        base = self._ct4_json_base
        return jsonmode.render(self._ct4_json_source,
                               self._CHEETAH__searchList,
                               base_dir=Path(base) if base else None)
"""


def module_for(source: str, class_name: str, main_method: str,
               base_class: str | None, base_dir: Path | None) -> str:
    """The text of a module that renders this template as JSON.

    Args:
        source (str): The template, declaration line and all. Parsed by
            :mod:`ct4.jsonmode` at render time, not here: a schema that
            cannot be read is the render's failure to report, and
            failing at compile time would take the whole report run
            down over one file.
        class_name (str): What the class must be called, because ct3
            pulls that name out of the module it execs.
        main_method (str): What the application will call. ``respond``
            unless #implements or a compile argument said otherwise.
        base_class (str|None): The name ct3 bound in the module before
            the exec, from Template.compile's baseclass argument.
        base_dir (Path|None): What a ``#schema`` path counts from,
            which is the template's own directory where there is one.

    Returns:
        str: A module ct3 can exec and take a class out of.
    """
    attributes = codegen.INIT + codegen.ATTRIBUTES % (class_name, main_method)
    return MODULE % {
        "name": class_name,
        "base": base_class or "Template",
        "main": main_method,
        "source": source,
        "base_dir": str(base_dir) if base_dir is not None else "",
        "attributes": "\n".join("    " + line if line else ""
                                for line in attributes.splitlines()),
    } + codegen.PLUMBING % (class_name, class_name, class_name)
