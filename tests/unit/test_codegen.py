"""The code generator, held against ct3 over the corpus.

It is built to be incomplete and never wrong, so there are two things
to check and they pull in opposite directions.

Everything it accepts has to render byte for byte what the corpus
records. That one is absolute: a case it takes and gets wrong is a
defect, and no amount of coverage makes up for it.

And it has to keep taking at least as many cases as it does today. A
generator that quietly narrows what it claims would pass the first
check perfectly by refusing everything.
"""

from __future__ import annotations

import pytest

from ct4.corpus import namespaces
from ct4.corpus.case import RENDER
from ct4.lang import codegen

from tests.unit.test_lex import ALL, corpus_dir, needs_corpus

# What it reached when this was written. A floor, not a target: it goes
# up as directives are added, and it must never go down without
# somebody saying so here.
FLOOR = 731


def render_cases():
    """Corpus cases that render, with their contexts."""
    root = corpus_dir()
    if root is None:
        return []
    from ct4.corpus.case import read_jsonl

    found = []
    for name in ("ct3-tests.jsonl", "weewx-render.jsonl"):
        path = root / name
        if path.exists():
            found.extend(c for c in read_jsonl(path) if c.kind == RENDER)
    return found


CASES = render_cases()


# -- What it can do --------------------------------------------------

def out(source, context):
    return codegen.render(source, context)


def test_plain_text_comes_through():
    assert out("hello\n", [{}]) == "hello\n"


def test_a_placeholder_is_resolved():
    assert out("Hello $name!\n", [{"name": "Ada"}]) == "Hello Ada!\n"


def test_a_dotted_placeholder_is_resolved():
    assert out("$a.b\n", [{"a": {"b": 7}}]) == "7\n"


def test_a_call_splits_the_chain():
    # ct3 stops treating the path as one name the moment a bracket
    # appears: $a.b(1) becomes VFN(VFFSL(SL,"a",True),"b",False)(1).
    # Measured off ct3, whose own docstring says something else.
    names = [(c.name, c.autocall, c.remainder)
             for c in codegen.chunks_of("$a.b.c[1].d().x.y.z")]
    assert names == [("a.b", True, ""), ("c", True, "[1]"),
                     ("d", False, "()"), ("x.y.z", True, "")]


def test_a_subscript_keeps_autocalling_and_a_call_does_not():
    assert codegen.chunks_of("$a()")[0].autocall is False
    assert codegen.chunks_of("$a[0]")[0].autocall is True


def test_a_called_placeholder_renders():
    assert out("$a.b(2)\n", [{"a": {"b": lambda n: n * 3}}]) == "6\n"


def test_an_enclosure_makes_no_difference():
    # ct3 generates the same single VFFSL for both forms.
    assert out("${a.b}\n", [{"a": {"b": 7}}]) == "7\n"
    assert out("$(a.b)\n", [{"a": {"b": 7}}]) == "7\n"


def test_a_loop_runs():
    # The targets lose their dollar and become plain names, which is
    # what ct3 writes: for r in VFFSL(SL,"rows",True).
    source = "#for $r in $rows\n[$r]\n#end for\n"
    assert out(source, [{"rows": [1, 2]}]) == "[1]\n[2]\n"


def test_a_loop_with_two_targets():
    source = "#for $k, $v in $pairs\n$k=$v\n#end for\n"
    assert out(source, [{"pairs": [("a", 1)]}]) == "a=1\n"


def test_a_condition_and_its_branches():
    source = "#if $a\nA\n#elif $b\nB\n#else\nC\n#end if\n"
    assert out(source, [{"a": 0, "b": 0}]) == "C\n"
    assert out(source, [{"a": 0, "b": 1}]) == "B\n"
    assert out(source, [{"a": 1, "b": 0}]) == "A\n"


def test_else_if_is_a_second_spelling_of_elif():
    # A corpus template writes it that way. Read as a plain else, its
    # body would run whatever the condition said.
    source = "#if $a\nA\n#else if $b\nB\n#end if\n"
    assert out(source, [{"a": 0, "b": 1}]) == "B\n"
    assert out(source, [{"a": 0, "b": 0}]) == ""


def test_a_directive_on_a_line_of_its_own_leaves_no_blank_line():
    assert out("x\n#if $a\ny\n#end if\nz\n", [{"a": 1}]) == "x\ny\nz\n"


def test_an_indent_stays_where_the_directive_does_not_end_its_line():
    # ct3 removes the whitespace before a directive only where the tag
    # also ran past the end of its own line. Here it ends at the hash.
    source = "  #for $i in $r#$i#end for#  "
    assert out(source, [{"r": [1, 2]}]) == "  12  "


def test_set_assigns_a_plain_name():
    # The target loses its dollar; only the right-hand side is a
    # lookup. ct3 writes "a = 1".
    assert out("#set $a = $b\n$a\n", [{"b": 4}]) == "4\n"


def test_silent_evaluates_and_writes_nothing():
    seen = []

    def note():
        seen.append(1)
        return "written?"

    # Autocalling reaches it, and nothing lands in the output.
    assert out("#silent $note\n", [{"note": note}]) == ""
    assert seen == [1]


def test_echo_writes_like_a_placeholder():
    assert out("#echo $a\n", [{"a": 7}]) == "7"


def test_slurp_eats_the_line_ending():
    assert out("x#slurp\ny\n", [{}]) == "xy\n"
    # And the indent as well, wherever the line held nothing else.
    assert out("a\n   #slurp\nb\n", [{}]) == "a\nb\n"


def test_while_break_and_continue():
    source = ("#set $n = 0\n#while 1\n#set $n = $n + 1\n"
              "#if $n > 2\n#break\n#end if\nx\n#end while\n")
    assert out(source, [{}]) == "x\nx\n"


def test_unless_negates_the_whole_expression():
    # ct3 writes "if not (expr)". Without the parentheses an "or"
    # would bind only the first half.
    assert out("#unless $a or $b\nx\n#end unless\n", [{"a": 0, "b": 1}]) == ""
    assert out("#unless $a or $b\nx\n#end unless\n",
               [{"a": 0, "b": 0}]) == "x\n"


def test_repeat_counts():
    assert out("#repeat 3\nx\n#end repeat\n", [{}]) == "x\nx\nx\n"


def test_none_writes_nothing():
    # The guard ct3 writes: a placeholder that resolves to None puts
    # nothing in the output, and the filter never sees it.
    assert out("[$x]\n", [{"x": None}]) == "[]\n"


def test_a_comment_disappears():
    assert out("a\n## gone\nb\n", [{}]) == "a\nb\n"


def test_an_escaped_dollar_is_a_dollar():
    assert out("costs \\$5\n", [{}]) == "costs $5\n"


def test_the_generated_code_is_python():
    import ast

    ast.parse(codegen.generate("Hello $name\n").code)


# -- What it refuses -------------------------------------------------

@pytest.mark.parametrize("source", [
    "#set global $a = 1\n$a\n",              # writes into the instance
    "#def show\nx\n#end def\n",              # a method, not a statement
    "#import os\n$a\n",                      # hoisted out of the body
    "$getVar('x')\n",                        # needs a Template object
    "$self.foo\n",                           # needs a Template object
    "$!a\n",                                 # no silence token yet
    "#unicode utf-8\n1234",                  # rewritten before parsing
    "#encoding utf-8\n1234",                 # decoded before parsing
    "<% print('x') %>\n",                    # no PSP
])
def test_it_refuses_what_it_cannot_do(source):
    assert not codegen.supports(source)
    with pytest.raises(codegen.Unsupported):
        codegen.generate(source)


def test_refusing_is_not_the_same_as_failing():
    # An unsupported template must raise Unsupported and nothing else,
    # so a caller can fall back rather than crash.
    with pytest.raises(codegen.Unsupported):
        codegen.generate("#block b\nx\n#end block\n")


# -- Against the corpus ----------------------------------------------

def taken():
    """Corpus cases this layer claims it can render."""
    return [case for case in CASES if codegen.supports(case.template)]


@needs_corpus
def test_everything_it_takes_it_gets_right():
    # The absolute one. Coverage is worth nothing if a case it accepts
    # comes out different from what ct3 produced.
    wrong = []
    for case in taken():
        if case.settings or case.compile_kwargs or case.filter:
            # Those change how ct3 renders, and this layer does not
            # read them yet. Taking them would be guessing.
            continue
        try:
            actual = codegen.render(case.template, namespaces.build(case))
        except Exception as error:                          # noqa: BLE001
            wrong.append("%s: %s" % (case.id, error))
            continue
        if actual != case.expected:
            wrong.append("%s: %r != %r"
                         % (case.id, actual[:60], case.expected[:60]))
    assert not wrong, "%d of %d cases wrong: %s" % (
        len(wrong), len(taken()), wrong[:5])


@needs_corpus
def test_it_reaches_at_least_as_far_as_it_did():
    reached = len(taken())
    assert reached >= FLOOR, (
        "%d of %d render cases, was %d. Narrowing what the generator "
        "claims is how it would pass every other check in this file."
        % (reached, len(CASES), FLOOR))


@needs_corpus
def test_it_refuses_the_rest_rather_than_guessing():
    # Nothing it declines may raise something other than Unsupported or
    # a structure error, or a caller could not fall back on it.
    from ct4.lang import tree

    for name, source in ALL[:400]:
        try:
            codegen.generate(source)
        except (codegen.Unsupported, tree.StructureError):
            continue
        except Exception as error:                          # noqa: BLE001
            pytest.fail("%s raised %s: %s"
                        % (name, type(error).__name__, error))
