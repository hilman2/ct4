"""Determinism, the cache, and mapping back into the template."""

from __future__ import annotations

import contextlib
import traceback

import pytest
from Cheetah.Template import Template

from ct4 import cache, trace
from ct4.jsonmode import compile_template
from ct4.lang import backend


def compile_source(source):
    cls = Template.compile(source=source, keepRefToGeneratedCode=True,
                           useCache=False, cacheCompilationResults=False)
    return cls, cls._CHEETAH_generatedModuleCode


# -- Determinism -----------------------------------------------------

def test_two_compilations_give_the_same_bytes():
    # Without it there is nothing to compare and nothing to cache.
    _, first = compile_source("Hello $name\n")
    _, second = compile_source("Hello $name\n")
    assert first == second


def test_no_timestamp_in_the_generated_module():
    _, code = compile_source("Hello $name\n")
    assert "__CHEETAH_genTimestamp__" not in code
    assert "__CHEETAH_genTime__" not in code


# -- Cache -----------------------------------------------------------

@pytest.fixture
def cache_store(tmp_path):
    store = cache.install(tmp_path)
    yield store
    cache.uninstall()


def test_the_second_run_does_not_compile_again(cache_store):
    source = "Hello $name and $other\n"
    _, code_a = compile_source(source)
    _, code_b = compile_source(source)
    assert cache_store.hits == 1
    assert cache_store.misses == 1
    assert code_a == code_b


def test_a_changed_template_gets_a_new_slot(cache_store):
    compile_source("Hello $name\n")
    compile_source("Hello $other\n")
    assert cache_store.hits == 0
    assert cache_store.misses == 2


def test_other_settings_are_a_different_entry(cache_store):
    source = "$x"
    Template.compile(source=source, useCache=False,
                     cacheCompilationResults=False)
    Template.compile(source=source, useCache=False,
                     cacheCompilationResults=False,
                     compilerSettings={"useAutocalling": False})
    assert cache_store.hits == 0


def test_the_module_name_is_not_part_of_the_key():
    # It changes with every dynamic compile. If it counted, the cache
    # would never hit.
    first = cache.key_for("$x", "K", None, None, {})
    second = cache.key_for("$x", "K", None, None, {})
    assert first == second


def test_the_class_name_is_part_of_the_key():
    assert cache.key_for("$x", "A", None, None, {}) != \
        cache.key_for("$x", "B", None, None, {})


# -- Pointing back into the template ---------------------------------

class Failing:
    def broken(self):
        raise ValueError("the database does not answer")


def test_the_traceback_names_the_template_line():
    source = "Line one\nLine two\n$obj.broken()\n"
    cls, code = compile_source(source)
    template = cls(searchList=[{"obj": Failing()}])
    with pytest.raises(ValueError) as error:
        with trace.mapped(code, "report.tmpl"):
            template.respond()
    text = "".join(traceback.format_exception(error.value))
    assert "report.tmpl, line 3" in text


def test_json_mode_names_the_template_line():
    source = '#mode json\n{\n "a": 1,\n "b": $obj.broken()\n}\n'
    compiled = compile_template(source, file="day.json.tmpl")
    with pytest.raises(ValueError) as error:
        compiled.render([{"obj": Failing()}])
    text = "".join(traceback.format_exception(error.value))
    assert "day.json.tmpl, line 4" in text


def test_nothing_is_appended_without_an_error():
    source = "Hello $name\n"
    cls, code = compile_source(source)
    with trace.mapped(code, "x.tmpl"):
        output = cls(searchList=[{"name": "World"}]).respond()
    assert output == "Hello World\n"


@contextlib.contextmanager
def engine(which):
    """The compiler ct3 brought, or the generator standing in for it."""
    if which == "generator":
        backend.install()
    try:
        yield
    finally:
        if which == "generator":
            backend.uninstall()


@pytest.mark.parametrize("which", ["ct3", "generator"])
def test_a_render_error_names_its_line_by_itself(which):
    # Nobody wraps anything here, which is the point: weewx calls
    # respond() and knows nothing of ct4.trace. The class that
    # Template.compile hands back carries the mapping itself.
    with engine(which):
        cls, _ = compile_source("Line one\nLine two\n$obj.broken()\n")
    with pytest.raises(ValueError) as error:
        cls(searchList=[{"obj": Failing()}]).respond()
    assert trace.notes_of(error.value) == [
        "template: <template>, line 3, column 1"]
    text = "".join(traceback.format_exception(error.value))
    assert "line 3, column 1" in text

    with pytest.raises(ValueError) as error:
        str(cls(searchList=[{"obj": Failing()}]))
    assert trace.notes_of(error.value) == [
        "template: <template>, line 3, column 1"]


@pytest.mark.parametrize("which", ["ct3", "generator"])
def test_an_include_names_its_own_line_before_the_includer(
        which, tmp_path, monkeypatch):
    # An #include is compiled at render time into a module of its own,
    # and each module maps only its own frames. Read against the
    # includer's map, the include's frames would name lines of the
    # wrong file.
    (tmp_path / "inner.inc").write_text("inner one\n$obj.broken()\n",
                                        encoding="utf-8")
    (tmp_path / "outer.tmpl").write_text(
        "outer one\nouter two\n#include 'inner.inc'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with engine(which):
        with pytest.raises(ValueError) as error:
            Template(file="outer.tmpl",
                     searchList=[{"obj": Failing()}]).respond()
    notes = trace.notes_of(error.value)
    assert notes[0].endswith("inner.inc, line 2, column 1")
    # ct3's compiler writes an origin behind a placeholder and behind
    # nothing else, so in its module the #include line has none and
    # the includer stays unnamed. The generator records one on every
    # statement, and that is the one place its traceback says more.
    if which == "generator":
        assert notes[1].endswith("outer.tmpl, line 3, column 1")
    assert len(notes) == (2 if which == "generator" else 1), notes


def test_the_mapping_takes_the_last_origin_before_it():
    # One directive spans several generated lines. Its origin is
    # recorded at the start and holds until the next one.
    mapping = {10: (3, 1), 20: (7, 5)}
    assert trace.position_of(mapping, 10) == (3, 1)
    assert trace.position_of(mapping, 15) == (3, 1)
    assert trace.position_of(mapping, 20) == (7, 5)
    assert trace.position_of(mapping, 25) == (7, 5)
    assert trace.position_of(mapping, 5) is None
