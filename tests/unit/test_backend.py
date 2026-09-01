"""The generator hooked into ct3's own entry point.

Everything else tests codegen.render, which is this project's own way
in. This tests Template.compile, which is weewx's, and the difference
is the whole point: a class that ct3 execs and hands back has to carry
what ct3 puts in one, or it fails at the first instantiation rather
than at the first byte.
"""

from __future__ import annotations

import pytest

from Cheetah.Template import Template
from ct4.lang import backend


@pytest.fixture(autouse=True)
def hooked():
    """The generator in front of ct3's compiler, and out again after."""
    counts = backend.install()
    yield counts
    backend.uninstall()


def rendered(source: str, context: dict, **kwargs) -> str:
    klass = Template.compile(source=source, useCache=False,
                             cacheCompilationResults=False, **kwargs)
    return str(klass(searchList=[context]).respond())


def test_it_takes_what_it_can_and_renders_it(hooked):
    assert rendered("hello $name\n", {"name": "world"}) == "hello world\n"
    assert (hooked.taken, hooked.fell_back) == (1, 0)


def test_it_falls_back_on_what_it_refuses(hooked):
    # #compiler-settings is a directive this layer has no code for, and
    # the caller must not be able to tell. Compared against a live ct3
    # rather than a written-down string: what ct3 does with a changed
    # variable token is the question, and a guess at it would test the
    # guess.
    source = ("#compiler-settings\ncheetahVarStartToken = @\n"
              "#end compiler-settings\n@aStr\n")
    backend.uninstall()
    want = rendered(source, {"aStr": "blarg"})
    # A fresh install brings a fresh tally, so the count to read is the
    # one this call hands back and not the fixture's.
    counts = backend.install()
    assert rendered(source, {"aStr": "blarg"}) == want
    assert (counts.taken, counts.fell_back) == (0, 1)


def test_a_template_carries_what_ct3_puts_in_one(hooked):
    klass = Template.compile(source="hi\n", useCache=False,
                             cacheCompilationResults=False)
    assert klass._CHEETAH__instanceInitialized is False
    assert klass._CHEETAH_versionTuple
    assert getattr(klass, "_mainCheetahMethod_for_" + klass.__name__) \
        == "respond"


def test_a_baseclass_outside_cheetahs_hierarchy(hooked):
    # ct3's own test suite compiles every syntax case a second time
    # against baseclass=dict, and such a class has none of Cheetah's
    # methods until _addCheetahPlumbingCodeToClass grafts them on.
    # Without that call 338 corpus cases fail at instantiation.
    assert rendered("#attr $test = 1234\n$test", {},
                    baseclass=dict) == "1234"
    assert hooked.taken == 1


def test_a_baseclass_that_is_another_template(hooked):
    backend.uninstall()
    base = Template.compile(source="base\n", useCache=False,
                            cacheCompilationResults=False)
    backend.install()
    assert rendered("hi $x\n", {"x": 1}, baseclass=base) == "hi 1\n"


def test_the_main_method_takes_its_transaction_positionally(hooked):
    # Template.respond and _handleCheetahInclude both call it that way.
    # A method called anything else takes it out of a keyword dict, and
    # ct3 decides that on the name alone.
    klass = Template.compile(source="hi\n", useCache=False,
                             cacheCompilationResults=False)
    made = klass(searchList=[{}])
    assert str(made.respond(None)) == "hi\n"


def test_an_include_finds_the_main_method(hooked):
    # #include compiles the nested template at render time and calls
    # the method named by _mainCheetahMethod_for_<class>.
    assert rendered("a $x b\n", {"x": "X"}) == "a X b\n"
    assert rendered("#include source=$inner\n",
                    {"inner": "nested $x", "x": "N"}) == "nested N"
