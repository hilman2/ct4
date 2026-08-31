"""Lookups that start at a local the compiler bound itself.

Where the compiler knows a name because it wrote the binding, a
placeholder on that name does not need the search list walked for it.
The saving is real: 1.62 times on a table of 200 rows with three
placeholders each, measured on 31-Aug-2026.

The point of this file is the other half. The shortcut is only allowed
because it changes nothing, and "nothing" here means every rule
NameMapper applies: unified dotted notation, autocalling, and which
namespace wins. Each of those is pinned below, because the strict mode
planned in PLAN.md section 12 will give some of them up deliberately,
and then these tests are what says what it is giving up.
"""

from __future__ import annotations

import pytest
from Cheetah.Compiler import ModuleCompiler
from Cheetah.Template import Template


def code(source, **settings):
    """The generated module code of a template."""
    settings["addTimestampsToCompilerOutput"] = False
    compiler = ModuleCompiler(source, moduleName="t", mainClassName="t",
                              settings=settings)
    return str(compiler)


def out(source, context, **settings):
    cls = Template.compile(source=source, useCache=False,
                           cacheCompilationResults=False,
                           compilerSettings=settings)
    return str(cls(searchList=[context]).respond())


LOOP = "#for $r in $rows\n$r.name\n#end for\n"


# -- Which names get shortened ---------------------------------------

def test_a_loop_target_is_resolved_as_a_local():
    assert 'VFN({"r":r},"r.name",True)' in code(LOOP)


def test_a_name_from_the_searchlist_is_not():
    # $rows comes from the context. Nothing here may assume it is a
    # local, or the lookup would find the wrong thing.
    assert 'VFFSL(SL,"rows",True)' in code(LOOP)


def test_a_tuple_target_binds_both_names():
    generated = code("#for $k, $v in $pairs\n$k$v.x\n#end for\n")
    assert 'VFN({"v":v},"v.x",True)' in generated


def test_a_name_without_a_dot_is_left_alone():
    # $r on its own is the local. There is nothing to resolve on it,
    # and the form would have to carry the autocalling of the first
    # component, which generated code cannot say cheaply.
    assert 'VFFSL(SL,"r",True)' in code("#for $r in $rows\n$r\n#end for\n")


def test_a_def_inside_a_loop_does_not_inherit_the_local():
    # The #def becomes a method of its own. The loop variable is not a
    # local there, whatever the indentation suggests.
    generated = code("#for $r in $rows\n#def show\n$r.name\n#end def\n"
                     "#end for\n")
    assert 'VFFSL(SL,"r.name",True)' in generated


def test_the_setting_switches_it_off():
    assert 'VFFSL(SL,"r.name",True)' in code(LOOP, resolveKnownLocals=False)


# -- What must not change --------------------------------------------

def fetcher():
    """Carries an attribute and is a function.

    Both are needed. NameMapper autocalls the first component too and
    then reads the rest off the *result*, not off the function. A class
    with ``__call__`` will not do here: autocalling is decided in
    ``isInstanceOrClass`` by probing ``__func__``, ``__code__`` and
    ``__self__``, and a callable instance has none of them.
    """
    return {"value": "from the result"}


fetcher.value = "from the function itself"


class Bag(dict):
    """A dict whose keys NameMapper reaches through the dot."""


def test_autocalling_of_the_first_component_survives():
    # The reason a loop target is not shortened to a plain attribute
    # access. $f.value calls f() and then reads .value off what came
    # back. Proven on 31-Aug-2026 to differ from VFN(f,"value",True),
    # which yields the attribute on the function.
    source = "#for $f in $callables\n$f.value\n#end for\n"
    assert out(source, {"callables": [fetcher]}) == "from the result\n"


def test_autocalling_is_the_same_with_the_shortcut_off():
    source = "#for $f in $callables\n$f.value\n#end for\n"
    context = {"callables": [fetcher]}
    assert out(source, context) == out(source, context,
                                       resolveKnownLocals=False)


def test_a_dict_key_is_still_reached_through_the_dot():
    # Unified dotted notation: $r.name finds r["name"] as readily as
    # r.name. A plain attribute access would raise here.
    source = "#for $r in $rows\n$r.name\n#end for\n"
    assert out(source, {"rows": [Bag(name="Ada")]}) == "Ada\n"


def test_the_local_wins_over_the_same_name_in_the_searchlist():
    # VFFSL looks in the frame locals before the search list. The
    # shortcut has to keep that order, or a context that happens to
    # carry the loop variable's name would change the result.
    source = "#for $r in $rows\n$r.name\n#end for\n"
    context = {"rows": [Bag(name="local")], "r": Bag(name="context")}
    assert out(source, context) == "local\n"


@pytest.mark.parametrize("source", [
    LOOP,
    "#for $r in $rows\n#for $s in $r.kids\n$s.name $r.name\n"
    "#end for\n#end for\n",
    "#for $k, $v in $pairs\n$v.name\n#end for\n",
    "#repeat 2\nx\n#end repeat\n",
    "#for $r in $rows\n#if $r.name\n$r.name\n#end if\n#end for\n",
])
def test_the_shortcut_changes_no_output(source):
    # The whole justification in one test, over the shapes that put the
    # scope stack out of step if the bookkeeping is wrong.
    context = {"rows": [Bag(name="Ada", kids=[Bag(name="Kid")])],
               "pairs": [("a", Bag(name="Bea"))]}
    assert out(source, context) == out(source, context,
                                       resolveKnownLocals=False)
