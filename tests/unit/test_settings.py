"""The compiler settings the generator honours, and a template's own.

Every case that renders is rendered by ct3 as well and compared,
because a setting is only honoured if the page comes out the same.
"""

from __future__ import annotations

import pytest
from Cheetah.Template import Template

from ct4 import trace
from ct4.lang import codegen
from ct4.lang import settings as compiler_settings


def both(source, context, settings=None):
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False,
                              compilerSettings=dict(settings or {}))
    expected = str(theirs(searchList=[dict(context)]).respond())
    ours = codegen.render(source, [dict(context)], settings=settings)
    assert ours == expected
    return ours


CONTEXT = {"f": lambda: "called", "x": "plain"}


def test_autocalling_off_from_the_caller():
    made = both("$f\n", CONTEXT, {"useAutocalling": False})
    assert made.startswith("<function")
    assert both("$f()\n", CONTEXT, {"useAutocalling": False}) == "called\n"


def test_name_mapper_off_from_the_caller():
    assert both("#set $a = 1\n$a\n", {}, {"useNameMapper": False}) == "1\n"


def test_a_setting_at_its_default_changes_nothing():
    assert both("$f\n", CONTEXT, {"useLegacyImportMode": True,
                                  "useAutocalling": True}) == "called\n"


def test_other_settings_are_refused_by_name():
    with pytest.raises(codegen.Unsupported) as error:
        codegen.generate("x\n", {"gobbleWhitespaceAroundMultiLineComments":
                                 False})
    assert "gobbleWhitespaceAroundMultiLineComments" in str(error.value)
    with pytest.raises(codegen.Unsupported) as error:
        codegen.generate("x\n", {"noSuchSetting": 1})
    assert "noSuchSetting" in str(error.value)


def test_a_block_at_the_head_of_the_file():
    source = ("#compiler-settings\nuseAutocalling = False\n"
              "#end compiler-settings\n$f $f()\n")
    assert both(source, CONTEXT).startswith("<function")
    assert both(source, CONTEXT).endswith(" called\n")


def test_the_block_reads_values_the_way_ct3_does():
    # ct3's converter reads "False" and "0"; "no" stays the string
    # "no", which is true, and a skin that writes useAutocalling=no
    # has autocalling on. Measured, and matched rather than improved:
    # the page has to come out the same.
    for spelling in ("False", "0"):
        source = ("#compiler-settings\nuseAutocalling=%s\n"
                  "#end compiler-settings\n$f\n" % spelling)
        assert both(source, CONTEXT).startswith("<function")
    source = ("#compiler-settings\nuseAutocalling=no\n"
              "#end compiler-settings\n$f\n")
    assert both(source, CONTEXT) == "called\n"


def test_a_compiler_line_at_the_head_of_the_file():
    source = "#compiler useNameMapper = 0\n#set $testVar = 1\n$testVar\n"
    assert both(source, {}) == "1\n"
    source = ("#compiler useAutocalling = False\n"
              "#compiler useNameMapper = 1\n$f\n")
    assert both(source, CONTEXT).startswith("<function")


def test_comments_may_stand_in_front():
    source = ("## licence\n\n#compiler-settings\nuseAutocalling = False\n"
              "#end compiler-settings\n$f\n")
    assert both(source, CONTEXT).startswith("\n<function")


def test_the_lines_keep_their_numbers():
    source = ("#compiler-settings\nuseAutocalling = False\n"
              "#end compiler-settings\nx\n$missing\n")
    with pytest.raises(Exception) as error:
        codegen.render(source, [{}])
    assert trace.notes_of(error.value) == [
        "template: <template>, line 5, column 1"]


def test_reset_and_a_block_further_down_are_refused():
    with pytest.raises(codegen.Unsupported):
        codegen.generate("#compiler-settings reset\n")
    with pytest.raises(codegen.Unsupported):
        codegen.generate("#compiler reset\n")
    # Plain text before the block is still the head; a placeholder is
    # output, and from there on a block is the middle of the file.
    with pytest.raises(codegen.Unsupported):
        codegen.generate("$x\n#compiler-settings\nuseAutocalling = False\n"
                         "#end compiler-settings\n")
    assert both("plain\n#compiler-settings\nuseAutocalling = False\n"
                "#end compiler-settings\n$f\n", CONTEXT).startswith(
        "plain\n<function")


def test_the_head_is_read_off_the_source():
    source = "#compiler useAutocalling = False\n$f\n"
    found = compiler_settings.head(source)
    assert (found.first, found.past) == (0, 1)
    assert found.settings == {"useAutocalling": False}
    assert compiler_settings.commented(source, found) == "##\n$f\n"
    assert compiler_settings.commented(source, found, "//") == "//\n$f\n"
    assert compiler_settings.head("plain\n$x\n") is compiler_settings.NO_HEAD
