"""Switching a mechanism off, and the coverage run built on it.

The instrument corrected two readings of its own already. Both times
the cause was the same: a mechanism applied after the templates had
been compiled, which changes nothing because a generated module binds
its lookup functions at exec time. So the cases here are mostly about
the switch really arriving.
"""

from __future__ import annotations

import pytest

from ct4.corpus import check as checker
from ct4.corpus import weaken
from ct4.corpus.case import COMPILE, RENDER, Case

CONTEXT = [{"rows": [{"name": "Ada"}]}]


def source(mark: str) -> str:
    """The same template, but unique to the caller.

    Template.compile keys its cache on the source, and a module that is
    handed back from that cache bound its lookup functions when it was
    first executed. Two cases sharing a source would mean the second one
    running against the first one's module, and a patched VFFSL would
    never arrive. Which is the very mistake this file exists for.
    """
    return "## %s\n#for $r in $rows\n$r.name\n#end for\n" % mark


@pytest.fixture(autouse=True)
def clean():
    """Every case starts from an unweakened process.

    Without this the order of the cases would decide their result:
    OVERRIDES is state of the module, and weaken.apply only ever adds.
    """
    saved = dict(checker.OVERRIDES), checker.WEAKENED
    checker.OVERRIDES.clear()
    checker.WEAKENED = ""
    yield
    checker.OVERRIDES.clear()
    checker.OVERRIDES.update(saved[0])
    checker.WEAKENED = saved[1]


def render_case(mark, expected="Ada\n"):
    return Case(id="t", template=source(mark), expected=expected,
                kind=RENDER, namespace="inline", context=CONTEXT)


def compile_case(mark):
    return Case(id="t", template=source(mark), expected="",
                kind=COMPILE, namespace="inline")


# -- The switch arrives ----------------------------------------------

def test_a_setting_reaches_the_render():
    assert checker.render(render_case("plain")) == "Ada\n"
    weaken.apply("namemapper")
    # Without the NameMapper $r.name is a plain attribute access, and a
    # dict has no attribute of that name.
    with pytest.raises(Exception):
        checker.render(render_case("no-namemapper"))


def test_a_setting_reaches_the_generated_code():
    before = checker.compile_code(compile_case("code"))
    weaken.apply("stackframes")
    after = checker.compile_code(compile_case("code"))
    assert before != after


def test_the_patch_reaches_the_lookup():
    weaken.apply("locals")
    # $rows still comes from the search list, $r.name from a local the
    # patched lookup no longer sees.
    with pytest.raises(Exception):
        checker.render(render_case("no-locals"))


def test_the_workers_are_told_which_mechanism():
    # A spawned worker inherits nothing, so the name has to travel.
    weaken.apply("filters")
    assert checker.WEAKENED == "filters"


# -- The instrument itself -------------------------------------------

def test_an_unknown_mechanism_is_refused():
    # A typo would otherwise measure a run with nothing switched off
    # and report it as a mechanism nothing depends on.
    with pytest.raises(KeyError):
        weaken.apply("stackframe")


def test_every_mechanism_is_described():
    # The description is what the report says the number means.
    for name in weaken.NAMES:
        assert weaken.describe(name), name


def test_every_name_has_a_way_to_apply_it():
    for name in weaken.NAMES:
        assert name in weaken.SETTINGS or name in weaken.PATCHES


def test_the_control_changes_nothing():
    # resolveKnownLocals is a pure optimisation. If this ever moves,
    # it stopped being one, and the whole coverage table stops being
    # believable, because it would have no case that earns a zero.
    before = checker.render(render_case("control-on"))
    weaken.apply("knownlocals")
    assert checker.render(render_case("control-off")) == before
