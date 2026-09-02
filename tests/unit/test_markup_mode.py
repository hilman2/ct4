"""Selecting markup mode, and the line that says so.

Two questions only, because selection is the whole of this module: does
a template declare markup mode, and does cutting the declaration leave
everything else where it was. The escaping and the scan live elsewhere.

The third question is the one that has to keep being asked: whether
saying any of this moved text mode. It has not, and the assertions at
the bottom hold the two places it could have moved from - the shape of
the call a placeholder writes, and Cheetah's own directive table.
"""

from __future__ import annotations

import ast

import pytest

from ct4.lang import codegen
from ct4.markup.mode import MODE_LINE, declared, strip


# -- The declaration -------------------------------------------------

def test_the_declaration_decides():
    assert declared("#mode markup\n<p>$x</p>\n")
    assert declared("## a licence header\n#mode markup\n<p>$x</p>\n")
    assert declared("\n\n## and a blank line\n\n" + MODE_LINE + "\n")


def test_the_declaration_is_the_whole_line_and_nothing_near_it():
    # One spelling. Two blanks is a template that renders the line.
    assert not declared("#mode  markup\n<p>$x</p>\n")
    assert not declared("#modemarkup\n")
    assert not declared("#mode json\n{}\n")
    # More than one word is a declaration too: strict combines with
    # markup, and a word nobody knows is refused by the generator with
    # its name rather than read as text.
    assert declared("#mode markup strict\n")
    assert declared("#mode markup extra\n")


def test_the_declaration_stands_on_the_first_line_only():
    assert not declared("<p>hello</p>\n#mode markup\n<p>$x</p>\n")
    # A ## comment may stand in front of it, output text may not.
    assert not declared("$x\n#mode markup\n")


def test_the_extension_does_not_decide():
    # A .json.tmpl skin is a text template that writes JSON by hand,
    # and a .html.tmpl one is text until it says otherwise. Neither
    # name reaches this function, which is the point: the build, the
    # corpus checker and the fuzz instruments all compile from a source
    # string with no path at all.
    assert not declared('{"a": $x}')
    assert not declared("<html><body>$x</body></html>")


def test_an_empty_template_declares_nothing():
    assert not declared("")
    assert not declared("\n\n")
    assert not declared("## nothing but a comment\n")


# -- Cutting it out --------------------------------------------------

def test_strip_removes_the_line_and_leaves_the_rest():
    assert strip("#mode markup\n<p>$x</p>\n") == "<p>$x</p>\n"
    assert strip("#mode markup") == ""


def test_strip_keeps_what_stands_in_front_of_the_declaration():
    source = "## head\n\n#mode markup\n<p>$x</p>\n"
    assert strip(source) == "## head\n\n<p>$x</p>\n"


def test_strip_takes_the_line_ending_the_declaration_came_with():
    # The corpus holds every template under all three line endings, so
    # the cut has to know all three.
    assert strip("#mode markup\r\n<p>$x</p>") == "<p>$x</p>"
    assert strip("#mode markup\r<p>$x</p>") == "<p>$x</p>"


def test_strip_leaves_a_template_that_declares_nothing_alone():
    for source in ('{"a": $x}', "#mode  markup\n$x\n",
                   "<p>hi</p>\n#mode markup\n", "", "## only a comment\n"):
        assert strip(source) == source


# -- Text mode did not move ------------------------------------------

def test_a_mode_line_in_the_body_is_output_text():
    """What ct3 does with the line where it is not the declaration.

    ``mode`` is no directive, so ct3 writes the line out as it stands.
    Asserted against ct3 itself rather than against a literal, because
    the promise is byte equality with ct3 and not with an expectation
    written down here.
    """
    from Cheetah.Template import Template

    source = "hello\n#mode markup\nbye\n"
    assert not declared(source)
    klass = Template.compile(source=source, useCache=False,
                             cacheCompilationResults=False)
    template = klass(searchList=[{}])
    try:
        assert str(template.respond()) == source
    finally:
        template.shutdown()


def test_mode_is_not_a_cheetah_directive():
    """The registration that must never happen, asserted rather than meant.

    With no eater behind it ct3's parser spins on the line for as long
    as it is given. And ``ct4.lang.lex.directive_names()`` reads this
    same dictionary at call time, so a registration would move ct4's
    lexer along with ct3's and every differential instrument would then
    compare two changed engines and report nothing.
    """
    import Cheetah.Parser

    from ct4.lang import lex

    assert "mode" not in Cheetah.Parser.directiveNamesAndParsers
    assert "mode" not in lex.directive_names()


def _filter_calls(code):
    """Every call to the output filter in a generated module."""
    return [node for node in ast.walk(ast.parse(code))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == codegen.FILTER]


def test_a_placeholder_still_writes_the_call_it_always_wrote():
    """One value, one keyword, and no import that was not there before.

    A filter in the wild is written ``def filter(self, val,
    rawExpr=None)`` and dies on a keyword it does not name, so the
    number of keywords on this call is part of the compatibility
    promise and not an implementation detail. weewx replaces the whole
    filter library, which is how a template gets one of those.
    """
    made = codegen.generate("hello $x")
    calls = _filter_calls(made.code)
    assert len(calls) == 1
    assert len(calls[0].args) == 1
    assert [keyword.arg for keyword in calls[0].keywords] == ["rawExpr"]
    assert "ct4.markup" not in made.code


@pytest.mark.parametrize("source", [
    "hello $x",
    "$x",
    "#for $i in $rows\n$i\n#end for\n",
])
def test_nothing_of_this_module_reaches_the_generated_module(source):
    made = codegen.generate(source)
    assert "markup" not in made.code
