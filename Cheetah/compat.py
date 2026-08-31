"""Namen, die aus der Python-2-Zeit uebrig sind, und Importhelfer.

``unicode`` und ``string_type`` sind unter Python 3 beide ``str``. Sie
bleiben trotzdem, und zwar nicht aus Bequemlichkeit: der Codegenerator
schreibt ``from Cheetah.compat import unicode`` in jedes erzeugte Modul.
Ein Modul, das ct3 uebersetzt hat, muss unter ct4 weiterlaufen (K3 in
PLAN.md). Faellt der Name weg, faellt jedes vorkompilierte Template.

Neuer Code benutzt ``str``.
"""

import types

import importlib.machinery
import importlib.util

string_type = str
unicode = str

# Seit Python 3.6 eingebaut. Sie stehen hier weiter, weil sie zur
# oeffentlichen Flaeche von ct3 gehoerten und jemand sie importiert
# haben koennte.
ModuleNotFoundError = ModuleNotFoundError
RecursionError = RecursionError


def load_module_from_file(base_name, module_name, filename):
    specs = importlib.util.spec_from_file_location(module_name, filename)
    module = importlib.util.module_from_spec(specs)
    specs.loader.exec_module(module)
    return module


new_module = types.ModuleType


def get_suffixes():
    extensions = [
        (s, 'rb', 3) for s in importlib.machinery.EXTENSION_SUFFIXES
    ]
    source = [
        (s, 'r', 1) for s in importlib.machinery.SOURCE_SUFFIXES
    ]
    bytecode = [
        (s, 'rb', 2) for s in importlib.machinery.BYTECODE_SUFFIXES
    ]

    return extensions + source + bytecode


from importlib.util import cache_from_source  # noqa: E402,F401
