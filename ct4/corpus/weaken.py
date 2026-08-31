"""Switch a mechanism off and count the cases that notice.

A corpus of 1,772 cases is a number until somebody asks what those
cases exercise. Coverage of lines says nothing here: every case runs
the same compiler and the same NameMapper. What matters is whether a
case would still pass if a guarantee were quietly dropped, because that
is the question every proposed change to the semantics runs into.

So each mechanism is switched off in turn and the corpus is run again.
The cases that change are the ones that hold that mechanism.

This is a measuring instrument, not a test. It is run when a change to
the semantics is being weighed, and its numbers belong in the plan.

The measurement corrected one of its own conclusions once already. An
earlier run flipped ``useStackFrames`` and found nothing, which was read
as the corpus not testing frame resolution at all. It was the wrong
experiment: with the setting off the compiler generates
``VFSL([locals()]+SL+[globals(), builtin], ...)``, and that walks the
same four namespaces in the same order as the C ``VFFSL``. The two are
equal by construction. Removing the locals namespace itself, which is
what ``locals`` below does, moves 301 cases.
"""

from __future__ import annotations

import builtins
import sys
from typing import Any, Callable

# Mechanisms that are a compiler setting. The value replaces whatever
# the case itself asks for; a template with its own
# #compiler-settings directive still wins, and that is correct.
SETTINGS: dict[str, dict[str, Any]] = {
    "namemapper": {"useNameMapper": False},
    "autocalling": {"useAutocalling": False},
    "filters": {"useFilters": False},
    "stackframes": {"useStackFrames": False},
    # The control. This one is a pure optimisation and must move
    # nothing. A count above zero here means it stopped being one.
    "knownlocals": {"resolveKnownLocals": False},
    # Paired with the patch below. The shortcut for names the compiler
    # bound is itself a resolution out of the locals, and it does not
    # go through VFFSL, so the patch alone would look straight past
    # every loop variable in the corpus and report far too few.
    "locals": {"resolveKnownLocals": False},
}


def _without_locals(searchList: Any, name: str,
                    executeCallables: bool = False) -> Any:
    """``VFFSL`` minus the frame locals. Everything else stays.

    Called as ``VFFSL(searchList, "day.outTemp", True)``, in place of
    ``Cheetah.NameMapper.valueFromFrameOrSearchList``. What a template
    finds through this and not through the real one is what it takes
    from a name the compiler bound.
    """
    import Cheetah.NameMapper as mapper

    frame = sys._getframe(1)
    first = name.split(".")[0]
    for namespace in list(searchList) + [frame.f_globals, vars(builtins)]:
        if mapper.hasKey(namespace, first):
            return mapper.valueForName(namespace, name, executeCallables)
    raise mapper.NotFound("cannot find '%s'" % first)


def _patch_locals() -> None:
    import Cheetah.NameMapper as mapper

    mapper.valueFromFrameOrSearchList = _without_locals


# Mechanisms that no setting reaches. They are applied before the first
# template is compiled: a generated module binds VFFSL at exec time,
# and patching afterwards would leave every already compiled module on
# the original.
PATCHES: dict[str, Callable[[], None]] = {
    "locals": _patch_locals,
}

NAMES = sorted(set(SETTINGS) | set(PATCHES))


def describe(name: str) -> str:
    """One line on what switching this off takes away."""
    return {
        "namemapper": "unified dotted notation, $a.b as a['b']",
        "autocalling": "a callable in a placeholder calls itself",
        "filters": "the output filter between value and text",
        "stackframes": "frame walking instead of an explicit locals()",
        "knownlocals": "the shortcut for names the compiler bound",
        "locals": "the locals namespace in a lookup",
    }.get(name, "")


def apply(name: str) -> None:
    """Switches one mechanism off in this process.

    Has to run before the first template of the run is compiled. There
    is no way back inside the process; the caller starts a new one.
    """
    from ct4.corpus import check

    if name not in NAMES:
        raise KeyError("no such mechanism: %s" % name)
    check.WEAKENED = name
    if name in SETTINGS:
        check.OVERRIDES.update(SETTINGS[name])
    if name in PATCHES:
        PATCHES[name]()
