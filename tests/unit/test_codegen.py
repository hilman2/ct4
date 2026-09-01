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

import ast
import sys

import pytest

from Cheetah.Template import Template
from ct4.corpus import namespaces
from ct4.corpus.case import RENDER, decode
from ct4.fixture.filters import WeewxAssureUnicode, resolve
from ct4.lang import codegen, lex

from tests.unit.test_lex import ALL, corpus_dir, needs_corpus

# What it reached when this was written. A floor, not a target: it goes
# up as directives are added, and it must never go down without
# somebody saying so here.
#
# 1023 until the settings hole was closed. The number was 1359 in
# between, and 24 of those cases rendered differently from ct3: the
# generator read none of ct3's compiler settings and took the templates
# anyway. Refusing them costs 42 cases and buys back the invariant that
# every accepted case is right.
#
# The corpus is the wrong ruler for #errorCatcher: it moves 3 cases
# here and 83 of the 390 real skin templates.
FLOOR = 1431


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


class _Counter:
    """Says how often it was written, which is what the cache changes."""

    def __init__(self):
        self.seen = 0

    def __str__(self):
        self.seen += 1
        return "c%d" % self.seen


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


# -- What a placeholder carries inside it ----------------------------
#
# ct3 does not copy what stands in a call or a subscript: it reads it,
# resolves the placeholders in it and writes Python. The strings below
# are what ct3's own getPlaceholder produces for the same text, byte
# for byte.

def test_a_nested_placeholder_in_a_call_is_resolved():
    # Corpus: ForDirective.test9, "$func($anInt)".
    assert out("$f($n)\n", [{"f": lambda x: x * 3, "n": 2}]) == "6\n"


def test_a_dollar_in_a_string_literal_is_not_a_lookup():
    # Corpus: GetVar.test1, "$getVar('$anInt')", which renders 1 and
    # not True. The lexer offers a nested placeholder for the $anInt
    # inside the quotes; ct3's getCallArgString never sees it, because
    # the whole string is one Python token and is copied as it stands.
    assert codegen.placeholder_source("$getVar('$anInt')") == \
        'VFFSL(SL,"getVar",False)(\'$anInt\')'


def test_a_nested_placeholder_in_a_subscript_is_resolved():
    # Corpus: NameMapper.test17, "$aDict[$anObj.meth('x')].two", where
    # the chain carries on after the subscript.
    context = [{"d": {"k": {"two": "hit"}}, "o": {"m": lambda name: name}}]
    assert out("$d[$o.m('k')].two\n", context) == "hit\n"


def test_a_keyword_argument_loses_its_dollar():
    # Corpus: Placeholders_Calls.test17. A single "=" behind a $name in
    # an argument list makes it a Python keyword argument, and ct3
    # writes the dotted name alone: getCallArgString re-reads it with
    # plain=True.
    assert codegen.placeholder_source("$aFunc($arg=4.0)") == \
        'VFFSL(SL,"aFunc",False)(arg=4.0)'


def test_a_comparison_is_not_a_keyword_argument():
    # ct3 asks getPyToken, which returns "==" as one token, and only
    # the single "=" triggers the rewrite. No corpus case covers it;
    # measured off ct3.
    assert codegen.placeholder_source("$aFunc($arg==4.0)") == \
        'VFFSL(SL,"aFunc",False)(VFFSL(SL,"arg",True)==4.0)'


def test_the_value_behind_an_equals_is_still_looked_up():
    # Corpus: Placeholders_Calls.test19. Taking the dollar off the
    # value side as well turns the inner call into a NameError.
    assert out("$f($n=$g(1))\n",
               [{"f": lambda n: n, "g": lambda x: x + 1}]) == "2\n"


def test_the_blanks_around_an_equals_are_written_once():
    # Corpus: Placeholders_Calls.test20. ct3's getWhiteSpace advances
    # the position, so a reader that appends the blanks and then reads
    # them again writes them twice. Whitespace only, and exactly the
    # kind of drift that hides a real difference.
    assert codegen.placeholder_source(
        "$aFunc(  $arg = $aMeth( $arg = $aFunc( 1 ) ) )") == (
        'VFFSL(SL,"aFunc",False)(  arg = VFFSL(SL,"aMeth",False)'
        '( arg = VFFSL(SL,"aFunc",False)( 1 ) ) )')


def test_a_line_ending_inside_a_subscript_is_dropped():
    # Corpus: NameMapper.test20. getExpressionParts advances past a
    # line ending without writing it. Kept, it would leave a bare line
    # break at the top level of the generated expression.
    assert codegen.placeholder_source(
        "$( anObj.meth1[0:\n (\n(4//4*2)*2)//$anObj.meth1(2)\n ] )") == (
        'VFN(VFFSL(SL,"anObj",True),"meth1",True)[0: ((4//4*2)*2)//'
        'VFN(VFFSL(SL,"anObj",True),"meth1",False)(2) ] ')


def test_a_line_ending_inside_a_call_is_kept():
    # The same ending, the other reader: getCallArgString copies it.
    assert codegen.placeholder_source("$a.b(1,\n2)") == \
        'VFN(VFFSL(SL,"a",True),"b",False)(1,\n2)'


def test_the_blanks_behind_an_enclosed_name_reach_the_expression():
    # ct3 appends what getWhiteSpace ate to the expression, so the
    # generated code really does end in a space.
    assert codegen.placeholder_source("$( a + 1 )") == \
        'VFFSL(SL,"a",True) + 1 '


def test_only_the_leading_name_of_an_expression_is_looked_up():
    # Every other bare name is written out as itself.
    assert codegen.placeholder_source("$a(len(b))") == \
        'VFFSL(SL,"a",False)(len(b))'


def test_a_subscript_swallows_the_bracket_group_behind_it():
    # getExpressionParts tests "a bracket opens" before "the enclosure
    # is done", so "$a[1](2)" is one chunk with the remainder "[1](2)".
    # A call does not do that: "$a(1)[2]" ends after the call.
    chunks = codegen.chunks_of("$a[1](2)")
    assert [(c.name, c.autocall, c.remainder) for c in chunks] == \
        [("a", True, "[1](2)")]


def test_the_generated_code_says_where_it_came_from():
    # A traceback out of a template points at a module that does not
    # exist on disk. ct3 writes the origin behind every statement and
    # ct4.trace reads it back; ast.unparse writes no comments, so the
    # generator has to put them there itself.
    made = codegen.generate("one\ntwo\n$aStr\n")
    assert "# generated from line 3, col 1" in made.code


def test_the_origins_are_the_same_ones_ct3_writes():
    from ct4 import trace

    source = "line one\n#for $r in $rows\n  $r.name\n#end for\n"
    ours = trace.line_map(codegen.generate(source).code)
    theirs = trace.line_map(Template.compile(
        source=source, useCache=False,
        cacheCompilationResults=False)._CHEETAH_generatedModuleCode)
    # Not the same generated lines, the two write different Python.
    # The same places in the template, and that is what is read.
    assert sorted(set(ours.values())) == sorted(set(theirs.values()))


def test_an_origin_is_never_invented():
    # The map is recovered by walking the tree that went into
    # ast.unparse against the one that comes back out of ast.parse. If
    # those two ever stop lining up the comments have to go, not move:
    # a line number that sends its reader to the wrong line of their
    # template is worse than none.
    module = ast.parse("x = 1\ny = 2\n")
    setattr(module.body[0], codegen.ORIGIN, (7, 3))
    with pytest.raises(codegen._Mismatch):
        codegen._origin_lines(module, ast.parse("x = 1\n"))


def test_a_bare_name_behind_a_bracket_carries_the_chain_on():
    # The chunk loop breaks on "not in identchars + '.'", and the
    # period is thrown away anyway, so the dot in this position is
    # decoration: weewx' own test skin writes ".round(5)json()" where
    # the line above it writes ".round(5).json()", and ct3 cannot tell
    # the two apart.
    assert codegen.placeholder_source("$f(1)upper") == \
        codegen.placeholder_source("$f(1).upper")


@pytest.mark.parametrize("source,reach", [
    ("$a(1)[2]", "$a(1)"),       # getCallArgString takes the one group
    ("$a(1)(2)", "$a(1)"),
    ("$a[1](2)[3]", "$a[1](2)[3]"),   # getExpression takes them all
    ("$a[1][2]", "$a[1][2]"),
    ("$a.b(1)c(2)[3]", "$a.b(1)c(2)"),
])
def test_a_call_ends_the_brackets_and_a_subscript_does_not(source, reach):
    # Where the placeholder stops decides what is text, so the token
    # has to end where ct3 stops reading and not one group later.
    found = lex.tokens(source)[0]
    assert found.text == reach


def test_the_targets_of_a_comprehension_are_not_looked_up():
    # getExpressionParts reads what stands between "for" and "in" with
    # the name mapper off, so the y that is bound stays a plain y.
    assert codegen.placeholder_source("$a([$y for $y in $b])") == (
        'VFFSL(SL,"a",False)([VFFSL(SL,"y",True) for y '
        'in VFFSL(SL,"b",True)])')


def test_an_enclosure_may_hold_more_than_the_name():
    assert out("$(a.b + 1)\n", [{"a": {"b": 4}}]) == "5\n"


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


def test_import_is_hoisted_and_usable():
    # It lands at module level, where ct3 puts it, rather than running
    # on every render. Joining the tokens once put the hash in front of
    # it and Python read the whole line as a comment, so the statement
    # parsed to nothing at all.
    source = "#import os\n$os.sep\n"
    assert out(source, [{}]).strip() in ("/", "\\")


def test_a_def_becomes_a_method_and_resolves_by_name():
    # The instance sits in the template's own search list, so $show
    # finds the method and autocalling calls it.
    assert out("#def show\nhi\n#end def\n$show\n", [{}]) == "hi\n\n"


def test_a_def_takes_arguments():
    source = "#def show($x, $y=1)\n$x-$y\n#end def\n$show(2)\n"
    assert out(source, [{}]) == "2-1\n\n"


def test_a_block_is_a_method_and_a_call_where_it_stands():
    assert out("a\n#block mid\nhi\n#end block\nb\n", [{}]) == "a\nhi\nb\n"


def test_the_template_object_is_reachable():
    # $getVar and $self need an instance, and there is one now.
    assert out("$getVar('name')\n", [{"name": "Ada"}]) == "Ada\n"


def test_set_global_outlives_its_method():
    # ct3 writes it into the instance, so a later lookup finds it.
    source = "#def put\n#set global $a = 7\n#end def\n$put$a\n"
    assert out(source, [{}]) == "7\n"


def test_attr_becomes_a_class_variable():
    assert out("#attr $x = 1\n$x\n", [{}]) == "1\n"


def test_raise_raises():
    with pytest.raises(ValueError):
        out("#raise ValueError('x')\n", [{}])


def test_unicode_is_cut_out_before_parsing():
    # It is no directive at all: ct3 finds the line with a regular
    # expression and removes it. Left in, it would be written as text.
    assert out("#unicode utf-8\n1234\n", [{}]) == "1234\n"


def test_the_colon_short_form_runs():
    # The line ending belongs to the body: ct3 parses it with
    # breakPoint=findEOL(gobble=True), which is past the ending.
    assert out("#if $a: yes\nafter\n", [{"a": 1}]) == "yes\nafter\n"
    assert out("#if $a: yes\nafter\n", [{"a": 0}]) == "after\n"


def test_a_chained_short_form_is_refused():
    # "#if 0: a" then "#else: b" chains in the code ct3 generates,
    # because its dedent puts the else back at the same level. Read as
    # a stray directive the body would simply vanish, so it is turned
    # away instead.
    assert not codegen.supports("#if 0: a\n#else: b\n")


def test_none_writes_nothing():
    # The guard ct3 writes: a placeholder that resolves to None puts
    # nothing in the output, and the filter never sees it.
    assert out("[$x]\n", [{"x": None}]) == "[]\n"


def test_a_comment_disappears():
    assert out("a\n## gone\nb\n", [{}]) == "a\nb\n"


def test_an_escaped_dollar_is_a_dollar():
    assert out("costs \\$5\n", [{}]) == "costs $5\n"


# -- #raw ------------------------------------------------------------

def test_a_raw_block_writes_its_body_and_nothing_else():
    # Corpus: RawDirective.test2. Both line endings belong to the tags,
    # so a body of "\n$aFunc().\n" would leave a blank line in front.
    source = "#raw\n$aFunc().\n#end raw\n$anInt"
    assert out(source, [{"anInt": 1}]) == "$aFunc().\n1"


def test_a_raw_block_with_no_end_runs_to_the_end_of_the_file():
    # Corpus: RawDirective.test1, and the shape every cobbler skin uses.
    assert out("#raw\n$aFunc().\n\n", [{}]) == "$aFunc().\n\n"


def test_both_tags_drop_their_indent_and_eat_their_line_ending():
    # Corpus: RawDirective.test3.
    source = "  #raw  \n$aFunc().\n   #end raw  \n$anInt"
    assert out(source, [{"anInt": 1}]) == "$aFunc().\n1"


def test_a_tag_that_ends_at_a_hash_keeps_its_indent_and_its_ending():
    # Corpus: RawDirective.test4, the same template with a directive
    # end token on both tags. _eatRestOfDirectiveTag drops the indent
    # only where the tag also ran past the end of its own line, and
    # here neither did.
    source = "  #raw  #\n$aFunc().\n   #end raw  #\n$anInt"
    assert out(source, [{"anInt": 1}]) == "  \n$aFunc().\n\n1"


def test_the_rules_hold_for_old_mac_line_endings():
    # Corpus: RawDirective_MacEOL.test4. findBOL and findEOL know
    # "\r\n", "\r" and "\n", and seven corpus cases are "\r" alone.
    source = "  #raw  #\r$aFunc().\r   #end raw  #\r$anInt"
    assert out(source, [{"anInt": 1}]) == "  \r$aFunc().\r\r1"


def test_the_colon_short_form_ends_with_its_line():
    # Corpus: RawDirective.test5. The line after it is source again.
    assert out("#raw: $aFunc().\n$anInt", [{"anInt": 1}]) == "$aFunc().\n1"


def test_the_short_form_eats_exactly_one_blank_behind_the_colon():
    # getWhiteSpace(max=1): the second blank is already body.
    assert out("#raw:   X Y\n", [{}]) == "  X Y\n"


def test_the_short_form_keeps_the_indent_the_block_form_drops():
    # It does none of _eatRestOfDirectiveTag's work at all.
    assert out("  #raw: X\nB\n", [{}]) == "  X\nB\n"
    assert out("  #raw\nX\n#end raw\nB\n", [{}]) == "X\nB\n"


def test_a_raw_body_is_not_unescaped():
    # Corpus: RawDirective.test6. addRawText is addStrConst, so unlike
    # eatPlainText it runs neither _unescapeCheetahVars nor
    # _unescapeDirectives over the body.
    source = "#raw: keep \\$x and \\#if\n"
    assert out(source, [{}]) == "keep \\$x and \\#if\n"
    assert out("\\$x and \\#if\n", [{}]) == "$x and #if\n"


def test_raw_has_no_arguments_at_all():
    # Whatever else stands on the line is body: the tag stops where
    # _eatRestOfDirectiveTag leaves it, which is right here.
    assert out("#raw foo\nX\n#end raw\nB\n", [{}]) == "foo\nX\nB\n"


def test_the_end_tag_is_matched_by_prefix_and_not_as_a_word():
    # _eatToThisEndDirective asks startswith('raw'), so "#endraw" is
    # body text and does not close anything.
    assert out("#raw\nA\n#endraw\nB\n", [{}]) == "A\n#endraw\nB\n"


def test_text_before_an_end_tag_on_the_same_line_is_body():
    # isLineClearToStartToken decides: where the line is not clear the
    # body stops at the hash, so the three blanks are still body.
    assert out("#raw\nX   #end raw\nB\n", [{}]) == "X   \nB\n"


def test_a_placeholder_inside_a_raw_body_is_not_resolved():
    assert out("#raw\n$anInt\n#end raw\n$anInt", [{"anInt": 1}]) \
        == "$anInt\n1"


@pytest.mark.parametrize("source", [
    # ct3 peeks past the end of the source and raises an IndexError.
    "#raw",
    "A\n#raw  ",
    # ct3 advances past the end of the stream and raises.
    "#raw\n#end",
    # A "##" on the #raw line is a comment or a directive end token
    # depending on what follows, and the comment reading also commits
    # the pending text, which changes what a later drop finds.
    "#raw ##c\nX\n#end raw\nB\n",
    # lex.raw_end looks for "#end raw" as a literal string, so it
    # misses this one and the span check turns that into a refusal.
    "#raw\nA\n#end   raw\nB\n",
    # The closing tag eats the hash and TAIL becomes text; the lexer
    # reads the line ending as part of the tag.
    "#raw\nX\n#end raw TAIL\nB\n",
    # The drop of the closing tag reaches back onto the #raw line and
    # ct3 deletes the "A  " there. Reproducing that needs ct3's chunk
    # boundaries rather than this layer's pieces.
    "A  #raw\nX\n  #end raw\nB\n",
    # Inside a colon short form ct3 breaks off at the end of the host's
    # line, so the body is "q" and not the rest of the file.
    "#def f: #raw q\n$f",
])
def test_it_refuses_the_raw_shapes_it_cannot_match(source):
    assert not codegen.supports(source)


def test_the_short_form_of_raw_is_taken_inside_a_short_form():
    # Its body is source[pos:findEOL()] either way, so the host's lower
    # break point cannot tell it apart.
    assert out("#def f: #raw: q\nZ\n$f", [{}]) == "Z\nq"


# -- #include --------------------------------------------------------
#
# The search list below is the one the IncludeDirective corpus cases
# use, so the templates here are their templates.

INCLUDED = {"blockToBeParsed": "$numOne $numTwo", "numOne": 1, "numTwo": 2,
            "emptyString": ""}


def test_an_included_string_is_parsed_against_the_same_search_list():
    # Corpus: IncludeDirective.test7.
    assert out("#include source=$blockToBeParsed", [INCLUDED]) == "1 2"


def test_a_raw_include_is_not_parsed():
    # Corpus: IncludeDirective.test2.
    assert out("#include raw source=$blockToBeParsed",
               [INCLUDED]) == "$numOne $numTwo"


def test_the_two_words_are_matched_by_prefix_and_not_as_words():
    # eatInclude asks startswith('raw') and startswith('source'), so
    # "rawsource=" is both of them at once (Cheetah/Parser.py line 2319).
    assert out("#include rawsource=$blockToBeParsed",
               [INCLUDED]) == "$numOne $numTwo"


def test_an_include_is_compiled_at_render_time_and_not_inlined():
    # Nothing but the one runtime call: Template._handleCheetahInclude
    # is what hands the nested template the search list, the
    # globalSetVars and the outer filter. Nothing in the corpus catches
    # an inlined include, so the test is on the generated code.
    code = codegen.generate("#include source=$b").code
    assert "_handleCheetahInclude" in code
    assert "includeFrom='str'" in code and "raw=False" in code


def test_an_included_template_sees_a_global_set_before_it():
    # Corpus: SetDirective.test4. It only works because the include
    # shares _CHEETAH__globalSetVars with the template it stands in.
    source = "#set global $aSetVar = 1234\n#include source=$includeBlock2"
    context = dict(INCLUDED, includeBlock2="$numOne $numTwo $aSetVar")
    assert out(source, [context]) == "1 2 1234"


def test_an_include_drops_its_indent_where_the_tag_ran_past_its_line():
    # Corpus: IncludeDirective.test8, "   #include source=$b   ": the
    # trailing blanks go with getExpression and the indent with
    # handleWSBeforeDirective.
    assert out("   #include source=$blockToBeParsed   ",
               [INCLUDED]) == "1 2"


def test_an_include_that_ends_at_a_hash_keeps_its_indent():
    # Corpus: IncludeDirective.test12. The tag stopped before its own
    # line ending, so ct3 leaves the two spaces where they are.
    assert out("  #include source=$blockToBeParsed#  ",
               [INCLUDED]) == "  1 2  "


def test_an_include_writes_before_the_line_ending_it_kept():
    # On a line that is not clear the ending stays, and it belongs
    # behind the include's output: ct3 adds the call chunk and only
    # then commits the text that follows it.
    assert out("x #include source=$blockToBeParsed\ntail\n",
               [INCLUDED]) == "x 1 2\ntail\n"


def test_an_include_inside_a_def_finds_its_transaction():
    # The prologue binds trans in every method here, which is what the
    # call passes on.
    source = "#def m\n#include source=$blockToBeParsed\n#end def\n[$m()]\n"
    assert out(source, [INCLUDED]) == "[1 2]\n"


# -- #extends and #implements ----------------------------------------

def test_extends_renames_the_main_method_to_writebody():
    # Corpus: ExtendsDirective.test2. setBaseClass calls
    # setMainMethodName('writeBody') before it touches the base list,
    # so the base class's respond runs and calls back into writeBody.
    source = ("#extends Cheetah.Templates.SkeletonPage\n"
              "#implements respond\n$spacer()\n")
    assert out(source, [{}]) == \
        '<img src="spacer.gif" width="1" height="1" alt="" />\n'


def test_extends_synthesises_the_import_ct3_synthesises():
    # ModuleCompiler.setBaseClass assumes the last chunk names the
    # class and the one before it the module, and corrects that back to
    # the whole name where the two are the same word.
    code = codegen.generate("#extends Cheetah.Templates.SkeletonPage\n").code
    assert "from Cheetah.Templates.SkeletonPage import SkeletonPage" in code
    assert "class _Ct4Template(SkeletonPage):" in code


def test_extends_adds_no_import_for_a_name_the_template_already_bound():
    # Corpus: ExtendsDirective.test1. An import of its own would be
    # "from _SkeletonPage import _SkeletonPage" and would not resolve.
    source = ("#from Cheetah.Templates._SkeletonPage import _SkeletonPage\n"
              "#extends _SkeletonPage\n#implements respond\n$spacer()\n")
    code = codegen.generate(source).code
    assert "from _SkeletonPage import" not in code
    assert out(source, [{}]) == \
        '<img src="spacer.gif" width="1" height="1" alt="" />\n'


def test_the_last_of_the_two_names_the_main_method():
    # Both call setMainMethodName as the single forward parse reaches
    # them, so source order decides and nothing else.
    after = codegen.generate("#extends Foo\n#implements respond\nhi\n").code
    assert "def respond(self" in after
    before = codegen.generate("#implements respond\n#extends Foo\nhi\n").code
    assert "def writeBody(self" in before


def test_implements_arguments_accumulate_and_survive_a_rename():
    # setMainMethodArgs looks the method compiler up by its current
    # name and adds to it, and setMainMethodName renames that same
    # object, so neither a second #implements nor a later #extends
    # throws the earlier arguments away.
    # And respond takes its transaction as an argument rather than out
    # of a keyword dictionary, whatever #implements added: ct3 decides
    # that on the method's name alone.
    twice = codegen.generate(
        "#implements respond(a=1)\n#implements respond(b=2)\n$a $b\n").code
    assert "def respond(self, a=1, b=2, trans=None):" in twice
    renamed = codegen.generate(
        "#implements respond(foo=9)\n#extends Foo\n$foo\n").code
    assert "def writeBody(self, foo=9, **KWS):" in renamed


def test_implements_takes_a_default_argument_and_drops_an_explicit_self():
    # Corpus: ExtendsDirective.test1#2 for the default, eatImplements
    # line 2195 for the self.
    source = ("#from Cheetah.Templates._SkeletonPage import _SkeletonPage\n"
              "#extends _SkeletonPage\n#implements respond(self, foo=1234)\n"
              "$foo\n")
    assert out(source, [{}]) == "1234\n"


def test_extends_and_implements_leave_no_blank_line_behind():
    source = "#implements respond\n#implements respond\nhi\n"
    assert out(source, [{}]) == "hi\n"


# -- The silence and cache tokens ------------------------------------

def test_the_silence_token_swallows_a_missing_name():
    assert out("[$!missing]\n", [{}]) == "[]\n"


def test_the_silence_token_covers_the_write_as_well():
    # ct3 puts both of the placeholder's statements inside the try, so
    # a NotFound raised by a called function is swallowed too.
    def boom():
        from Cheetah.NameMapper import NotFound
        raise NotFound("nope")

    assert out("[$!boom]\n", [{"boom": boom}]) == "[]\n"


def test_the_silence_token_lets_a_keyerror_out():
    # NotFound is a LookupError subclass, so "except LookupError" would
    # swallow a KeyError from user code. ct3 lets that one through.
    def boom():
        raise KeyError("nope")

    with pytest.raises(KeyError):
        out("$!boom\n", [{"boom": boom}])


def test_the_silence_token_alone_does_not_cache():
    counter = _Counter()
    assert out("#for $i in $r#[$!c]#end for#",
               [{"r": [1, 2, 3], "c": counter}]) == "[c1][c2][c3]"


def test_the_cache_token_evaluates_the_placeholder_once():
    # The only place the cache is observable, and the corpus never gets
    # there: no corpus template renders a cached placeholder twice.
    counter = _Counter()
    assert out("#for $i in $r#[$*c]#end for#",
               [{"r": [1, 2, 3], "c": counter}]) == "[c1][c1][c1]"


def test_the_cache_holds_across_calls_of_one_method():
    counter = _Counter()
    source = "#def f\n$*c#slurp\n#end def\n$f()$f()$f()"
    assert out(source, [{"c": counter}]) == "c1c1c1"


def test_two_cache_tokens_do_not_share_a_region():
    # ct3 gives every cache token a fresh region ID. Two placeholders
    # sharing one would share a cache item, and the second would write
    # the first one's text.
    assert out("[$*x][$*y]", [{"x": "X", "y": "Y"}]) == "[X][Y]"


def test_an_interval_of_zero_never_expires():
    # ct3 guards setExpiryTime with a plain "if interval:", so 0 emits
    # no expiry line at all and "$*0*x" behaves like "$*x".
    counter = _Counter()
    assert out("#for $i in $r#[$*0*c]#end for#",
               [{"r": [1, 2], "c": counter}]) == "[c1][c1]"


def test_an_interval_that_has_not_elapsed_keeps_the_cached_text():
    counter = _Counter()
    assert out("#for $i in $r#[$*5m*c]#end for#",
               [{"r": [1, 2], "c": counter}]) == "[c1][c1]"


def test_the_cache_token_takes_the_three_enclosures():
    # Corpus: Placeholders test10, test11 and test12. The modifiers
    # come off, the enclosure stays, and the blanks inside it with it.
    for source in ("$*( aStr   )", "$*{ aStr   }", "$*[ aStr   ]"):
        assert out(source, [{"aStr": "blarg"}]) == "blarg"


def test_the_silence_token_goes_inside_the_cache_region():
    # Corpus: Placeholders.test20. Wrapped the other way round the
    # NotFound escapes before the region puts trans and write back, and
    # everything written after it lands in the dead collector.
    source = "$!aStr$!nonExistant$!*nonExistant$!{nonExistant}"
    assert out(source, [{"aStr": "blarg"}]) == "blarg"


def test_a_name_is_not_an_interval():
    # The interval is [0-9.]+ with an optional lowercase s/m/h/d/w and
    # a closing star. "$*w*x" is a static cache on w, and "*x" is text.
    assert out("$*w*x", [{"w": "WW"}]) == "WW*x"


def test_the_tokens_have_to_come_in_ct3s_order():
    # Cache before silence is not a placeholder at all, and neither is
    # a blank between the token and the name.
    for source in ("$*!x", "$!!x", "$**x", "$* x", "$!  x", "$*", "$!"):
        assert out(source, [{"x": "X"}]) == source


# -- #filter, #call and #cache ---------------------------------------
#
# Three regions: setup statements, the body, teardown statements. What
# they are measured on is not only the value that comes out but where
# the whitespace around their two tags lands, because for #call and
# #cache a line ending on the wrong side of the region goes into the
# string the function or the cache item is handed.

def test_filter_swaps_the_filter_for_the_region():
    # Corpus: FilterDirective.test5, "#filter WebSafe".
    source = "#filter WebSafe\n$x#end filter\n$x"
    assert out(source, [{"x": "a<b"}]) == "a&lt;b\na<b"


def test_filter_none_restores_the_initial_filter():
    # Parser.eatFilter compares the identifier without regard to case.
    source = "#filter WebSafe\n$x #filter None\n$x#end filter\n#end filter"
    assert out(source, [{"x": "a<b"}]) == "a&lt;b \na<b\n"


def test_filter_binds_the_name_it_looked_the_filter_up_under():
    # setFilter writes "filterName = 'WebSafe'" as a plain local, and
    # VFFSL searches the calling frame before the search list. So the
    # local is visible to the template, and nothing deletes it again.
    assert out("#filter WebSafe\n$filterName#end filter",
               [{"filterName": "from the search list"}]) == "WebSafe"


def test_call_hands_the_body_to_the_function():
    # Corpus: CallDirective.test1#1.
    assert out("#call int\n$anInt#end call", [{"anInt": 1}]) == "1"


def test_call_writes_the_result_through_the_filter():
    # endCallRegion uses addFilteredChunk, so a #call inside a #filter
    # escapes its body once on the way in and the result a second time.
    source = "#filter WebSafe\n#call str\n$x#end call\n#end filter\nT"
    assert out(source, [{"x": "a<b"}]) == "a&amp;lt;b\nT"


def test_call_reads_its_function_with_autocalling_off():
    # eatCall turns useAutocalling off around getCheetahVar. With it on
    # NameMapper would call meth() with no arguments and the region
    # would then call whatever came back.
    assert out("#call $meth\nq#end call", [{"meth": str.upper}]) == "Q"


def test_call_appends_what_stood_after_the_function():
    two = lambda first, b: "%s/%s" % (first, b)          # noqa: E731
    assert out('#call $two b="B"\nq#end call', [{"two": two}]) == "q/B"


def test_nested_calls_restore_the_transaction_in_order():
    # write is recovered as trans.response().write after trans is put
    # back, and the collected string is read after that restore.
    assert out("#call int\n#call int\n7#end call\n#end call\n", [{}]) == "7"


def test_cache_writes_the_body_once_and_serves_it_after():
    # The body sits inside "if _recache or not getRefreshTime():", and
    # the regions live on the instance, so the second call through the
    # #def hits the cache instead of running the body again.
    counter = _Counter()
    source = "#def d\n#cache id='z'\n$c#end cache\n#end def\n$d|$d"
    assert out(source, [{"c": counter}]) == "c1\n|c1\n"


def test_cache_takes_its_region_id_from_the_argument_list():
    # Two blocks with one id share a cache item, which is the only
    # reason a custom id exists. Corpus: CacheDirective.test3#1.
    counter = _Counter()
    source = "#cache id='z'\n$c#end cache\n#cache id='z'\n$c#end cache"
    assert out(source, [{"c": counter}]) == "c1\nc1"


def test_an_uppercase_id_is_inert():
    # Corpus: CacheDirective.test4#1 uses ID= for its outer region.
    # genCacheInfoFromArgList copies the key as it stands and
    # startCacheRegion reads cacheInfo.get('id'), so ID= names nothing
    # and the two blocks below get a region each.
    counter = _Counter()
    source = "#cache ID='z'\n$c#end cache\n#cache ID='z'\n$c#end cache"
    assert out(source, [{"c": counter}]) == "c1\nc2"


def test_a_cache_timer_is_read_as_ct3_reads_it():
    # genTimeInterval: a trailing s/m/h/d/w scales, a bare number is
    # minutes. Corpus: CacheDirective.test3#1, "timer=150m".
    source = "#cache id='z', timer=150m\n$anInt\n#end cache\n$aStr"
    assert out(source, [{"anInt": 1, "aStr": "blarg"}]) == "1\nblarg"


def test_the_opening_tag_drops_the_indent_outside_the_region():
    # Corpus: CacheDirective.test2#1. _eatRestOfDirectiveTag runs
    # before the region's first chunk is written, so what it drops is
    # the text in front of the region.
    assert out("  #cache  \n$anInt#end cache", [{"anInt": 1}]) == "1"


def test_a_line_ending_the_opening_tag_leaves_is_inside_the_region():
    # No corpus case: every corpus region opens on a clear line. A
    # generator that leaves the ending outside renders "A\n1B".
    assert out("A#call int\n$anInt#end call\nB", [{"anInt": 1}]) == "A1\nB"


def test_a_line_ending_the_end_tag_leaves_is_outside_the_region():
    # eatEndDirective eats the tag before it closes the region, so the
    # ending is written after it. Kept inside, int() would see it.
    assert out("  #call int\n$anInt#end call\n", [{"anInt": 1}]) == "1\n"


def test_the_end_tag_keeps_its_indent_where_the_line_is_not_clear():
    # Corpus: CallDirective.test2#1, "$anInt  #end call".
    assert out("#call str\n$anInt  #end call", [{"anInt": 1}]) == "1  "


def test_a_comment_on_the_tag_line_keeps_the_indent():
    # addComment reaches addChunk, and addChunk commits the pending
    # text before handleWSBeforeDirective could truncate it. The line
    # ending is still gobbled.
    assert out("  #call str ## note\n$anInt#end call\nZ",
               [{"anInt": 1}]) == "  1\nZ"
    assert out("  #call str\n$anInt#end call\nZ",
               [{"anInt": 1}]) == "1\nZ"


def test_a_comment_that_writes_no_chunk_does_not_keep_the_indent():
    # addComment returns early for a bar comment, for "name@" and for
    # the doc: and header: forms, and an empty one has no lines at all.
    for tail in ("##", " #####"):
        assert out("  #call str %s\n$anInt#end call\nZ" % tail,
                   [{"anInt": 1}]) == "1\nZ"


def test_the_same_rule_holds_for_the_directives_that_open_a_block():
    # Not new with the regions: _eatRestOfDirectiveTag is one function
    # and every directive goes through it. weewx' forecast_table.inc
    # writes "  #end if ## lastday != thisday".
    assert out("  #if 1 ## note\nQ\n#end if\nZ", [{}]) == "  Q\nZ"
    assert out("#if 1\nQ\n  #end if ## note\nZ", [{}]) == "Q\n  Z"


def test_the_colon_short_form_of_cache_swallows_its_line_ending():
    # eatCache parses to findEOL(gobble=True), so the ending is part of
    # the region. Corpus: CacheDirective.test1#1, "#cache:$anInt".
    assert out("#cache: 10\nX", [{}]) == "10\nX"


def test_the_colon_short_form_of_call_does_not():
    # Corpus: CallDirective.test1#3. eatCall and eatFilter parse to
    # findEOL(gobble=False), so the ending is written after the region
    # closes and int() never sees it.
    assert out("#call int: 10\n$aStr", [{"aStr": "blarg"}]) == "10\nblarg"


def test_no_short_form_touches_the_whitespace_in_front_of_it():
    # None of the three runs _eatRestOfDirectiveTag at all.
    assert out("  #call int: 10\nX", [{}]) == "  10\nX"


def test_the_generated_code_is_python():
    import ast

    ast.parse(codegen.generate("Hello $name\n").code)


# -- #encoding -------------------------------------------------------
#
# Two things with one name. The line is a directive that writes
# nothing, and the same line found a second time by a regular
# expression makes ct3 put the whole source through a codec before it
# parses. Where that codec reads ASCII the way ASCII does, the second
# is the identity and there is nothing to reproduce.

def test_encoding_writes_nothing():
    # Corpus: EncodingDirective.test1, "#encoding utf-8\n1234".
    assert out("#encoding utf-8\n1234", [{}]) == "1234"


def test_encoding_keeps_the_whitespace_in_front_of_it():
    # eatEncoding calls readToEOL and nothing else. It is the only
    # directive that does not go through handleWSBeforeDirective, so
    # the indent every other one drops survives here.
    assert out("  #encoding utf-8\n1234", [{}]) == "  1234"


def test_encoding_eats_its_line_ending_even_mid_line():
    assert out("x #encoding utf-8\ny", [{}]) == "x y"


def test_a_codec_that_reads_ascii_as_ascii_leaves_the_source_alone():
    # Corpus: EncodingDirective.test5, "#encoding latin-1\nAndr\x82".
    # repr escapes the character, latin-1 reads the escape unchanged,
    # and ct3's eval puts it back.
    assert out("#encoding latin-1\nAndr\x82", [{}]) == "Andr\x82"


def test_a_printable_non_ascii_character_is_not_a_reason_to_refuse():
    # Corpus: EncodingDirective.test3. repr keeps the character where
    # it stands while backslashreplace escapes it, which is why the
    # comparison is against the ASCII reading of the escaped bytes.
    assert out("#encoding utf-8\nሴ", [{}]) == "ሴ"


def test_the_codec_comes_from_the_regular_expression_and_not_the_node():
    # The directive carries "utf-8 junk here", which no codec is
    # called; encodingDirectiveRE does not match the line at all, so
    # ct3 does no preprocessing and still renders "1234".
    assert out("#encoding utf-8 junk here\n1234", [{}]) == "1234"


def test_a_line_the_regular_expression_takes_and_the_parser_does_not():
    # The expression allows up to five blanks after the hash; the
    # parser has no directive called "  encoding". So the codec is
    # checked and the line is written out as ordinary text.
    source = "#  encoding  : utf-8\n1234"
    assert out(source, [{}]) == source


# -- PSP -------------------------------------------------------------
#
# A block structure of its own over the same source. What it is
# measured on is where its statements land and that it takes part in
# no whitespace handling at all.

def test_a_psp_value_is_written_through_the_filter():
    # Corpus: PSP.test1, "<%= 1234 %>".
    assert out("<%= 1234 %>", [{}]) == "1234"


def test_a_psp_value_is_not_the_two_statements_a_placeholder_writes():
    # addPSP writes write(_filter(x)) with no _v and no "is not None"
    # guard. The two agree under the default filter and part company
    # under one that renders None as something.
    code = codegen.generate("<%= None %>").code
    assert "_filter(None)" in code and "_v =" not in code
    assert out("<%= None %>", [{}]) == ""


def test_a_psp_value_with_nothing_in_it_writes_nothing():
    assert out("a<%=   %>b", [{}]) == "ab"


def test_a_psp_body_ending_in_a_colon_opens_a_block():
    # Corpus: PSP.test6, "<% for i in range(5):%>1<%end%>".
    assert out("<% for i in range(5):%>1<%end%>", [{}]) == "11111"


def test_a_dollar_opens_a_block_and_comes_off():
    # Corpus: PSP.test8. The last line of the body is a statement, so
    # ct3 needs a marker of its own to know a block was opened.
    source = "<% for i in range(5):\n    i=i*2$%><%=i%><%end%>"
    assert out(source, [{}]) == "02468"


def test_what_stands_above_the_block_header_stays_outside_it():
    assert out("<% x = 1\nif x:%>y<%end%>z", [{}]) == "yz"


def test_the_end_token_is_read_without_regard_to_case_or_blanks():
    assert out("<% if 1:%>x<%  END  %>", [{}]) == "x"


def test_psp_blocks_nest():
    source = "<% for i in range(2):%><% for j in range(2):%>.<%end%><%end%>"
    assert out(source, [{}]) == "...."


def test_a_psp_body_is_raw_python_and_reaches_the_prologue():
    # write, _filter, SL, trans, _dummyTrans and self are bound by this
    # layer's prologue under ct3's own spelling, so nothing is renamed.
    assert out("<% write(_filter('x')) %><% write(str(bool(trans))) %>",
               [{}]) == "xTrue"


def test_a_psp_takes_part_in_no_whitespace_handling():
    # eatPSP calls neither handleWSBeforeDirective nor
    # _eatRestOfDirectiveTag, so the indent in front and the line
    # ending behind are ordinary text.
    assert out("a\n   <%= 1 %>\nb", [{}]) == "a\n   1\nb"
    assert out("a\n   <% if 1:%>\n   x\n   <%end%>\nb", [{}]) \
        == "a\n   \n   x\n   \nb"


def test_a_block_inside_a_psp_block_still_becomes_a_method():
    # Only the call self.foo(trans=trans) lands inside the PSP block.
    assert out("<% if 1:%>\n#block foo\nx\n#end block\n<%end%>", [{}]) \
        == "\nx\n"


def test_a_backslash_in_front_of_a_psp_start_makes_it_text():
    # Both PSP tokens carry escCharLookBehind. The backslash is not
    # removed the way the one in front of a "$" is.
    source = "a\\<%= 1 %>b"
    assert out(source, [{}]) == source


def test_a_backslash_in_front_of_the_end_token_does_not_close_the_psp():
    # The PSP runs to the second "%>", so its body is the whole
    # "write('a') #\%> junk" and the text after it is "B".
    assert out("A<% write('a') #\\%> junk %>B", [{}]) == "AaB"


# -- What it refuses -------------------------------------------------

@pytest.mark.parametrize("source", [
    "#filter $Klass\nx\n#end filter\n",      # ct3 reads an expression there
    "#filter WebSafe junk\nx\n#end filter\n",   # ct3 writes the junk out
    "#call $f ${a}\nx\n#end call\n",         # ParseError inside an expression
    "#cache test=True\nx\n#end cache\n",     # test= has behaviour of its own
    "#cache id='a-b'\nx\n#end cache\n",      # ct3 generates a SyntaxError
    "#cache foo\nx\n#end cache\n",           # ct3 dies at compile time
    "#call int: 10#end call\n",              # a ParseError in ct3
    "#call f\nx\n#arg a\ny\n#end call\n",    # the tree swallows an #arg body
    "#if 0: a\n#else: b\n",                  # the chained short form
    "#include\n",                            # ct3 writes a syntax error
    "#include source=$a ## why\n",           # the comment keeps the indent
    "#extends Foo Bar\n",                    # ct3 glues that into "FooBar"
    "#extends os.path.Thing\n",              # a name only ct3's module has
    "#implements $respond\n",                # a ParseError in ct3
    "#if $!x\ny\n#end if\n",                 # a token inside an expression
    "#set $a = $*x\n",                       # ct3 raises a ParseError
    "$*1.2.3*x\n",                           # ct3 does not compile it
    "#encoding utf-16\n1234",                # ct3 parses a U+2327 then
    "#encoding no-such-codec\n1234",         # ct3 raises a LookupError
    "#encoding\n1234",                       # the empty name, the same
    "#encoding undefined\n1234",             # a UnicodeError, not a subclass
    "#encoding utf-8#\n1234",                # readToEOL reached further
    "#encoding utf-8\n#unicode utf-8\nx",    # ct3 dies of a RecursionError
    "a<%%>b",                                # addPSP subscripts an empty body
    "a<% x = 1",                             # the PSP never closes
    "a<%end%>b",                             # an end with no open PSP
    "<% if 1:%>x",                           # ... and an open PSP with no end
    "<% if 1:%><%end%>",                     # ct3 writes a header with no body
    "<% x = 1$%>y<%end%>",                   # the $ opened nothing
    "<% if 0:%>a<%end%><% else:%>b<%end%>",  # ct3 chains it onto the if
    "<% for i in range(2):\n  x=i$%>a<%end%>",   # ct3 indents by four
    '<% x = """a\nb""" \nwrite(x) %>',       # ct3 indents into the string
    "#if 1\n<% if 1:%>\n#end if\nx<%end%>",  # the two structures cross
    "<%= $anInt %>",                         # a PSP resolves no names
    "<% write(str(__file__)) %>",            # a name only ct3's module has
    "$a[\n1]\n",                             # the lexer stopped early
    "${aFunc(\n\n)}\n",                      # ... and left the "}" over
    "$(a, 'x')\n",                           # arguments for the filter
    "#echo ${b}\n",                          # ParseError inside an expression
    "$str(c'$aStr')\n",                      # a c'...' placeholder string
    "$a[1 +]\n",                             # ct3 does not compile it
])
def test_it_refuses_what_it_cannot_do(source):
    assert not codegen.supports(source)
    with pytest.raises(codegen.Unsupported):
        codegen.generate(source)


def test_refusing_is_not_the_same_as_failing():
    # An unsupported template must raise Unsupported and nothing else,
    # so a caller can fall back rather than crash.
    with pytest.raises(codegen.Unsupported):
        codegen.generate("#filter $Klass\nx\n#end filter\n")


def module_names():
    """The globals of a template compiled by each engine.

    Returns a two-way tuple (ct3, this layer). The same template both
    times, and one with no imports of its own, so what comes back is
    the two preambles and nothing else.
    """
    source = "hello $name\n"
    theirs = Template.compile(source, keepRefToGeneratedCode=True)
    ours: dict = {}
    exec(compile(codegen.generate(source).module, "<ct4>", "exec"), ours)
    return set(sys.modules[theirs.__module__].__dict__), set(ours)


def test_the_preamble_lists_what_ct3_actually_carries():
    # PREAMBLE and OURS_ONLY are the difference between the two module
    # namespaces, and a template can reach into either. Written by hand
    # the list went stale inside a day: it was missing five of ct3's
    # names and the whole reverse direction. So it is measured.
    theirs, ours = module_names()
    assert theirs - ours == codegen.PREAMBLE, (
        "ct3 carries names the guard does not list: %s"
        % sorted(theirs - ours - codegen.PREAMBLE))
    assert ours - theirs == codegen.OURS_ONLY, (
        "this layer carries names the guard does not list: %s"
        % sorted(ours - theirs - codegen.OURS_ONLY))


def test_every_name_the_table_carries_is_one_ct3_carries():
    assert set(codegen.PREAMBLE_IMPORTS) <= codegen.PREAMBLE


# Reaching each name in a way that says something about the object
# rather than about its address. Four of them are functions the name
# mapper autocalls on the spot, and what is compared there is the
# TypeError, message and all.
PREAMBLE_PROBES = {
    "CacheRegion": "$CacheRegion.__name__",
    "DummyResponse": "$DummyResponse.__name__",
    "DummyResponseFailure": "$DummyResponseFailure.__name__",
    "Filters": "$Filters.Filter.__name__",
    "RequiredCheetahVersion": "$str($RequiredCheetahVersion)",
    "RequiredCheetahVersionTuple": "$str($RequiredCheetahVersionTuple)",
    "TransformerResponse": "$TransformerResponse.__name__",
    "TransformerTransaction": "$TransformerTransaction.__name__",
    "VFSL": "$VFSL.__name__",
    "builtin": "$builtin.len([1, 2])",
    "exists": "$exists('.')",
    "getmtime": "$getmtime.__name__",
    "os": "$os.path.sep",
    "sys": "$sys.maxsize",
    "time": "$time.gmtime(0).tm_year",
    "types": "$types.ModuleType.__name__",
    "unicode": "$unicode.__name__",
    "valueForName": "$valueForName.__name__",
    "valueFromFrameOrSearchList": "$valueFromFrameOrSearchList.__name__",
    "valueFromSearchList": "$valueFromSearchList.__name__",
}


def test_every_name_in_the_table_has_a_probe():
    # A row nobody reaches is a row nobody has checked, and the table
    # is the one place where this layer claims two modules agree.
    assert sorted(PREAMBLE_PROBES) == sorted(codegen.PREAMBLE_IMPORTS)


@pytest.mark.parametrize("name", sorted(PREAMBLE_PROBES))
def test_a_preamble_name_resolves_to_what_ct3_resolves_it_to(name):
    source = PREAMBLE_PROBES[name] + "\n"

    def rendered(work):
        try:
            return work()
        except Exception as error:                      # noqa: BLE001
            return "!!%s: %s" % (type(error).__name__, error)

    theirs = rendered(lambda: str(Template.compile(
        source=source, useCache=False,
        cacheCompilationResults=False)(searchList=[{}]).respond()))
    assert rendered(lambda: codegen.render(source, [{}])) == theirs


# A directive whose argument runs past the end of its line. ct3's
# getExpressionParts opens a bracket before it tests whether the
# expression has ended, so a line ending inside one is read and thrown
# away. Every skin that writes a list of station fields writes it this
# way, and 19 templates in the corpus and the harvested skins turn on
# it.
SPANNING_SHAPES = [
    "#set $a = [1,\n2]\ngot $a\n",
    "#set $a = [\n    1,\n    2,\n]\ngot $a\n",
    "#set $d = {'x': 1,\n 'y': 2}\ngot $d.x\n",
    "#set $a = [1,\n2] \ngot $a\n",
    "#set $a = str(\n1)\ngot $a\n",
    "#set $a = [$b,\n2]\ngot $a\n",
    "#set $a = [1,\n2]\n#set $c = 3\ngot $a $c\n",
    "#if [1,\n2]\nyes\n#end if\n",
    "#echo [1,\n2]\n",
    # The ending still closes the directive once the bracket is shut,
    # and the indent that stood on the continuation line stays inside
    # the brackets where it does no harm.
    "#set $a = [1,\n      2]\ngot $a\n",
    # Nested, so that the first closing bracket does not end it.
    "#set $a = [[1,\n2],\n[3]]\ngot $a\n",
    # A bracket inside a string opens nothing.
    "#set $a = ['[',\n']']\ngot $a\n",
    # And the plain single-line forms still end where they did.
    "#set $a = [1, 2]\ngot $a\n",
    "#set $a = 1\ngot $a\n",
]


@pytest.mark.parametrize("source", SPANNING_SHAPES)
def test_a_directive_argument_may_run_past_its_line(source):
    context = {"b": [7, 8, 9]}
    theirs = str(Template.compile(
        source=source, useCache=False,
        cacheCompilationResults=False)(searchList=[context]).respond())
    assert codegen.render(source, [context]) == theirs


# What stands on the left of a "#set", and between a "#for" and its
# "in". ct3 reads a target with useNameMapper off and that reaches all
# the way in: "$d[$k]" is "d[k]", the subscript as plain as the name it
# hangs off. Six skins keep a dictionary that way.
TARGET_SHAPES = [
    "#set $d['x'] = 1\ngot $d.x\n",
    "#set $d[$k] = 1\ngot $d.x\n",
    "#set $e = {}\n#set $e['x'] = 2\ngot $e.x\n",
    "#set $e = {'a': {}}\n#set $e['a']['b'] = 3\ngot $e.a.b\n",
    "#set $obj.x = 1\ngot $obj.x\n",
    "#set $a, $b = 1, 2\ngot $a $b\n",
    "#set ($a, $b) = (1, 2)\ngot $a $b\n",
    "#set [$a, $b] = [1, 2]\ngot $a $b\n",
    "#set $n = 1\n#set $n += 2\ngot $n\n",
    "#set $lst = [1, 2, 3]\n#set $lst[0:2] = [9]\ngot $lst\n",
    "#set $a = 1\ngot $a\n",
    "#for $r in $rows\n$r\n#end for\n",
]


@pytest.mark.parametrize("source", TARGET_SHAPES)
def test_an_assignment_target_is_written_as_plainly_as_ct3_writes_it(source):
    class Obj:
        x = 0

    def context():
        return {"d": {}, "k": "x", "obj": Obj(), "rows": [1, 2]}

    # Three of these raise in both engines: a target whose base comes
    # from the search list is written as a plain name and there is no
    # such local. What is compared there is the NameError, message and
    # all, because that is the behaviour ct3 has and the one a caller
    # that swapped engines would see.
    def rendered(work):
        try:
            return work()
        except Exception as error:                      # noqa: BLE001
            return "!!%s: %s" % (type(error).__name__, error)

    theirs = rendered(lambda: str(Template.compile(
        source=source, useCache=False,
        cacheCompilationResults=False)(searchList=[context()]).respond()))
    assert rendered(lambda: codegen.render(source, [context()])) == theirs


# A hash inside a string literal in a directive's arguments. ct3's
# getPyToken takes the whole literal, so the branch that ends a
# directive at a bare hash is never reached inside one. Skins keep
# lists of CSS colours and anchors, and 21 templates were refused
# because the first colour closed the #set it stood in.
HASH_IN_STRING_SHAPES = [
    '#set $a = ["#fff", "#000"]\ngot $a\n',
    "#set $href = '#top'\ngot $href\n",
    '<!--#set $c = "#59cc33" #-->got $c\n',
    '#if "#" == "#"\nyes\n#end if\n',
    # A dollar inside one is not a placeholder either: ct3 copies a
    # string literal verbatim and does not look in it.
    '#set $a = "$b"\ngot $a\n',
    "#set $a = 'it is #1'\ngot $a\n",
    # And the hash still ends a directive where it stands outside one.
    "#if 1#yes#end if\n",
    "#set $a = 1#got $a\n",
    # The dollar in the included source is resolved all the same, and
    # not by the lexer: ct3 includes the string as a template and
    # parses it there. This layer was refusing it out of caution.
    "#include source='a$b'\n",
]


@pytest.mark.parametrize("source", HASH_IN_STRING_SHAPES)
def test_a_hash_inside_a_string_ends_no_directive(source):
    context = {"b": "resolved"}
    theirs = str(Template.compile(
        source=source, useCache=False,
        cacheCompilationResults=False)(searchList=[context]).respond())
    assert codegen.render(source, [context]) == theirs


def test_an_apostrophe_in_prose_opens_no_string():
    # Python has no one-line string that crosses a line ending, so the
    # apostrophe in "today's" opens nothing. A scan that took it for a
    # quote ran to the next apostrophe wherever it was, swallowed
    # whatever stood between, and emptied the block stack: three real
    # skins stopped refusing and started crashing.
    prose = "#raw x = #end raw 'a'; //today's high\n"
    assert lex.string_span(prose, prose.index("//today") + 7) is None
    assert lex.string_span("'a' and more", 0) == 3
    assert lex.string_span("'''a\nb''' and more", 0) == 9

    for source in ("#set $a = 1#it's fine\n$a\n",
                   "#echo 1#it's fine\n",
                   "#if 1#it's true#end if\n",
                   "#set $a = 1\nit's fine $a\nand 'quoted'\n"):
        theirs = str(Template.compile(
            source=source, useCache=False,
            cacheCompilationResults=False)(searchList=[{}]).respond())
        assert codegen.render(source, [{}]) == theirs


def test_a_raw_body_is_never_cut_by_the_argument_scan():
    # The raw body is source the lexer has already measured from end to
    # end. Cutting it at the line ending that closes "#raw" took three
    # corpus cases out of reach and nothing else noticed.
    source = "#raw\n$aFunc().\n\n"
    assert codegen.render(source, [{}]) == "$aFunc().\n\n"


def test_a_psp_reaches_a_preamble_name_too():
    # The guard reads the finished module and a PSP body is part of it,
    # so a plain Python name in one is reached the same way a
    # placeholder is. Concatenated: a PSP end token is a percent and a
    # closer, and %-formatting reads it as a conversion.
    source = "<% write(str(" + "time)) %>\n"
    theirs = str(Template.compile(
        source=source, useCache=False,
        cacheCompilationResults=False)(searchList=[{}]).respond())
    assert codegen.render(source, [{}]) == theirs


@pytest.mark.parametrize("name", ["__loader__", "_Ct4Template"])
def test_it_refuses_a_name_only_one_engine_has(name):
    # One from each direction. $__loader__ resolves in ct3 and not
    # here; $_Ct4Template resolves here and not in ct3.
    # Concatenated: a PSP end token is a percent and a closer, and
    # %-formatting reads it as a conversion.
    assert not codegen.supports("<% write(" + name + ") %>\n")


# -- Whitespace around a directive -----------------------------------
#
# The corpus holds not one of these shapes: 2026 real cases, the whole
# weewx skin set among them, and every one of them writes its
# directives on lines of their own. A differential fuzz found 1864 of
# 12627 accepted templates rendering differently from ct3, all of them
# here. So the suite carries the shapes itself, compared against a real
# ct3 rather than against a recorded string, which is what keeps them
# from going stale when a rule is understood better.

WHITESPACE_SHAPES = [
    # An #end tag sharing its line with output. The line ending after
    # it belongs after the block: ct3 has closed the loop before the
    # text arrives, so it is written once and not once per turn.
    "A\n#for $i in range(2)\nZ#end for\n",
    "#for $i in range(2)\nZ#end for\n",
    "A\n#if 1\nB#end if\nC\n",
    "#for $i in range(2)\n#for $j in range(2)\nZ#end for\n#end for\n",
    # An opening tag with output before it on the line. Its line ending
    # is the other way round: it is the first thing the body writes.
    "L#for $i in range(2)\nZ\n#end for\nA\n",
    "L #while 0\nZ\n#end while\nA\n",
    "$aStr#for $i in range(2)\nZ\n#end for\n",
    # #echo writes, so its line ending comes after what it wrote.
    "L#echo 1\nT\n",
    "L#echo $aStr\nT\n",
    # #stop ends the template. What stands behind it is never written,
    # its own line ending included.
    "L#stop\nT\n",
    "L #stop\n",
    # A block comment takes the rest of its line and its indent, unless
    # it is the last thing in the template, where ct3 leaves the whole
    # question alone.
    "  #* c *#\nT\n",
    "  #* c *#",
    "  #* c *#X\n",
    "  #* a\n   *#\nT\n",
    "\t#* c *#  ",
    # The same, with the other two line endings.
    "A\r\n#for $i in range(2)\r\nZ#end for\r\n",
    "A\r#for $i in range(2)\rZ#end for\r",
]


@pytest.mark.parametrize("source", WHITESPACE_SHAPES)
def test_whitespace_around_a_directive_matches_ct3(source):
    context = {"x": 1, "aStr": "blarg"}
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    want = str(theirs(searchList=[dict(context)]).respond())
    assert codegen.supports(source), "refused: %r" % source
    assert codegen.render(source, [dict(context)]) == want


def test_text_before_a_def_is_refused_rather_than_kept():
    # ct3 decides from its pending buffer and this layer from the
    # source, and a #def is where the two part company: it carries its
    # body off into a method, so the L is still pending when the #slurp
    # two lines down truncates the buffer to the start of its line.
    # ct3 renders nothing at all here. Reproducing that needs ct3's
    # chunk boundaries, so the template is refused instead.
    assert not codegen.supports("L#def g\nD\n#end def\n#slurp\n")


# -- Three directives that were only ever refused --------------------

LATE_SHAPES = [
    # #assert and #return are one statement each, the way #raise is.
    "#assert $x == 1\nok\n",
    '#assert $x == 2, "no"\nok\n',
    "#assert $x == 1\n",
    "#if 1\n#assert $x == 1\n#end if\nok\n",
    "#def m\n#return 42\n#end def\n$m",
    "#def m\n#return $x\n#end def\n$m",
    "#def m($a)\n#return $a * 2\n#end def\n$m(3)",
    # A #return drops what the method has written so far, because it
    # returns instead of falling through to the collected output.
    "#def m\nX\n#return 1\nY\n#end def\n$m",
    # The one-line #if. Not a Python conditional expression: ct3 writes
    # an if and an else with a filtered write in each, and the None
    # guard on those writes is what makes the difference show.
    "#if $x then $a else $b\n",
    "#if 0 then $a else $b\n",
    "#if $x then None else 2\n",
    "#if $x then 1 else 2\n",
    # A "then" inside a string or a subscript is not the cut.
    '#if $x then "then" else "else"\n',
    '#if $d["else"] then 1 else 2\n',
    # And the line it stands on, clean and dirty. The ending goes after
    # the if, because on a dirty line the parser reaches it once the
    # statement is already written.
    "A#if $x then $a else $b\nB\n",
    "A#if 0 then $a else $b\nB\n",
    "  #if $x then $a else $b\nB\n",
    "#if $x then $a else $b",
    "#for $i in [1,2]\n#if $i then $a else $b\n#end for\n",
]


@pytest.mark.parametrize("source", LATE_SHAPES)
def test_the_directives_added_last_match_ct3(source):
    context = {"x": 1, "a": "A", "b": "B", "d": {"else": 1}}
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    try:
        want = str(theirs(searchList=[dict(context)]).respond())
    except Exception as error:                                 # noqa: BLE001
        want = "!!%s" % type(error).__name__
    assert codegen.supports(source), "refused: %r" % source
    try:
        got = codegen.render(source, [dict(context)])
    except Exception as error:                                 # noqa: BLE001
        got = "!!%s" % type(error).__name__
    assert got == want


def test_a_ternary_with_two_thens_is_refused():
    # ct3 switches its target back and forth on a second one, and what
    # comes out is nobody's intention. Refused rather than guessed at.
    assert not codegen.supports("#if $x then $a then $b else $c\n")


# -- The head of a #def or a #block ----------------------------------
#
# 64 corpus cases out of 16 distinct sources, the rest being line
# ending variants. Four shapes, and ct3 reads all four in
# _eatDefOrBlock: a dollar in front of the name, a comment behind it,
# star parameters, and the colon form carrying both.

DEFINITION_SHAPES = [
    "#def $m\n1234\n#end def\n$m",
    "#def m ## why\n1234\n#end def\n$m",
    "#def $m($arg=1234)\n$arg\n#end def\n$m",
    "#def $m:1234\n$m",
    "#def m    : hi\n$m",
    "#def $m($a, $b=2)\n$a$b\n#end def\n$m(1)",
    # A star list, and a keyword dictionary the method brings itself.
    # ct3 adds its own **KWS only where there is none, and reads the
    # transaction out of whichever it ends up with.
    "#def m($*args)\n$args\n#end def\n$m(1,2)",
    "#def m($**kw)\n$kw\n#end def\n$m(a=1)",
    "#def m($*a, $**k)\n$a$k\n#end def\n$m(1,x=2)",
    "#block $b\nX\n#end block\n",
    "#block b ##why\nX\n#end block\n",
]


@pytest.mark.parametrize("source", DEFINITION_SHAPES)
def test_a_definition_head_matches_ct3(source):
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    want = str(theirs(searchList=[{}]).respond())
    assert codegen.supports(source), "refused: %r" % source
    assert codegen.render(source, [{}]) == want


# -- Names the generator bound itself --------------------------------

SCOPE_SHAPES = [
    "#for $r in $rows\n$r.name\n#end for\n",
    # A bare name is the local itself, so there is nothing to rewrite.
    "#for $r in $rows\n$r\n#end for\n",
    # And the binding ends with the body.
    "#for $r in $rows\n$r.name\n#end for\n$r.name\n",
    "#for $a, $b in $pairs\n$a.x $b.x\n#end for\n",
    "#for $r in $rows\n#for $c in $r.cells\n$c.x $r.name\n#end for\n"
    "#end for\n",
    "#for $r in $rows\n#if $r.flag\n$r.name\n#end if\n#end for\n",
]


@pytest.mark.parametrize("source", SCOPE_SHAPES)
def test_a_lookup_starts_at_a_local_the_way_ct3_writes_it(source):
    # The fork's own compiler rewrites a lookup whose base it bound
    # itself, and it is worth 1.9x on a loop. Without the same rewrite
    # here the module this layer writes renders half as fast as the one
    # it stands in for. Compared line for line against ct3 rather than
    # against a written-down string.
    context = {"rows": [{"name": "a", "flag": 1, "cells": [{"x": 1}]}],
               "pairs": [({"x": 1}, {"x": 2})]}
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False,
                              keepRefToGeneratedCode=True)
    want = [line.strip().split("#")[0].strip()
            for line in theirs._CHEETAH_generatedModuleCode.splitlines()
            if "_v =" in line]
    # Both sides lose their origin comment: ct3 writes one behind every
    # statement and so, since the traceback needs it, does the
    # generator. What is compared is the lookup.
    got = [line.strip().split("#")[0].strip()
           for line in codegen.generate(source).code.splitlines()
           if "_v =" in line]
    # ct3 writes no blanks after a comma and quotes with ", where
    # ast.unparse writes a blank and '. Neither is the point here.
    def plain(lines):
        return [line.replace(" ", "").replace('"', "'") for line in lines]

    assert plain(got) == plain(want)
    assert codegen.render(source, [context]) == \
        str(theirs(searchList=[context]).respond())


def test_a_method_starts_with_nothing_bound():
    # ct3 gives every method compiler its own scope stack, so a #for
    # around a #def does not reach into it. Not reachable through #for
    # here, which ct3 itself cannot compile that way round, so the
    # binding is checked where it is: a #def after the loop.
    source = "#for $r in $rows\n$r.name\n#end for\n#def m\n$r.name\n#end def\n"
    code = codegen.generate(source).code
    assert 'VFN({\'r\': r}, \'r.name\', True)' in code
    assert "VFFSL(SL, 'r.name', True)" in code


# -- What the perturbation run found ---------------------------------
#
# tests/fuzz/perturb.py takes the corpus and indents every directive,
# dirties every directive line, and pulls every #end onto the line
# above. Real content in shapes nobody writes. These are the rules it
# turned up, each one wrong in the corpus's own templates the moment
# they were moved.

PERTURBED_SHAPES = [
    # eatSlurp ends with readToEOL(gobble=True), so a #slurp closed by
    # a directive end token swallows the rest of its line, text,
    # placeholders and all. "$job <!--#slurp#-->" is how a real skin
    # writes it.
    "A#slurp#-->\nC\n",
    "A#slurp# B\nC\n",
    "A#slurp#$x\nC\n",
    "  #slurp#-->\nC\n",
    "A#slurp#-->",
    "A#slurp B\nC\n",
    "A#slurp##c\nC\n",
    # The short form of #def drops the indent in front of it and the
    # short form of #block does not: closeBlock writes the call where
    # the tag stood and commits the pending text before ct3 asks about
    # the whitespace. The long form of both drops it.
    "  #def m: hi\nX\n",
    "  #def m: hi",
    "  #block m: hi\nX\n",
    "  #block m: hi",
    "  #block m\nhi\n  #end block\nX\n",
    "  #def m\nhi\n  #end def\nX$m\n",
    "A  #block m: hi\nX\n",
    "A  #def m: hi\nX\n",
    # handleWSBeforeDirective truncates one pending chunk and never
    # the one before it. #encoding leaves its own indent pending, so
    # the next directive's drop takes its own indent and stops, and
    # two blanks reach the output.
    "  #encoding UTF-8\n  #import os\nX\n",
    "  #encoding UTF-8\nX\n",
    "  #encoding UTF-8\n\nX\n",
    "A\n  #encoding UTF-8\nX\n",
]


@pytest.mark.parametrize("source", PERTURBED_SHAPES)
def test_the_shapes_the_perturbation_run_found(source):
    context = {"x": 1}
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    want = str(theirs(searchList=[dict(context)]).respond())
    assert codegen.supports(source), "refused: %r" % source
    assert codegen.render(source, [dict(context)]) == want


# -- The expression placeholder --------------------------------------
#
# ct3's second placeholder start, where the enclosure holds an
# expression instead of a name. 46 corpus cases and 25 of the 390 skin
# templates, so it is not an exotic corner: three weewx skins use it to
# format a number.

EXPRESSION_SHAPES = [
    "$(6)\n",
    "$(1+2)\n",
    "$[1,2]\n",
    '$("%.3f" % $pi)\n',
    "$( 6 )\n",
    "$($pi)\n",
    "$($pi + 1)\n",
    "$*(6)\n",
    "$( $pi )\n",
    "$[$a['b']]\n",
    "$(1)$(2)\n",
    "#set $x = 1\n$($x + 1)\n",
    "$($a.b)\n",
    # A jQuery call in a page is one of these, and ct3 writes what the
    # expression evaluates to rather than the source.
    "a$('#id')b\n",
    "<a href=\"$('x')\">\n",
    # Neither of these is one: the enclosure closes at once, so the
    # dollar is a character.
    "$()\n",
    "$(]\n",
    "price: $(5.00)\n",
]


@pytest.mark.parametrize("source", EXPRESSION_SHAPES)
def test_the_expression_placeholder_matches_ct3(source):
    context = {"pi": 3.14159, "a": {"b": "B"}}
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    want = str(theirs(searchList=[dict(context)]).respond())
    assert codegen.supports(source), "refused: %r" % source
    assert codegen.render(source, [dict(context)]) == want


# -- The line a branch directive stands on ---------------------------

BRANCH_SHAPES = [
    "#if 1\nT\n  #else\nF\n#end if\n",
    "#if 0\nT\n  #else\nF\n#end if\n",
    "#if 1\nT#slurp\n  #else\nF#slurp\n  #end if\n",
    "#if 1\nT\n  #elif 0\nX\n#end if\n",
    "#if 0\nT\n  #elif 1\nX\n#end if\n",
    "#try\nT\n  #except\nE\n#end try\n",
    "#try\nT\n  #finally\nF\n#end try\n",
    # Dirty line: the indent stays and the ending is the arm's output.
    "#if 1\nA  #else\nB\n#end if\n",
    "#if 0\nA  #else\nB\n#end if\n",
]


@pytest.mark.parametrize("source", BRANCH_SHAPES)
def test_a_branch_tag_decides_about_its_own_line(source):
    # An #else eats the whitespace before it and the line ending after
    # it, like every other directive. Missing that left the two blanks
    # of "  #else" in the output. Neither the corpus nor the whitespace
    # fuzz saw it: the corpus writes no text on a branch tag's line,
    # and the fuzz puts its #except at column zero.
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    want = str(theirs(searchList=[{}]).respond())
    assert codegen.supports(source), "refused: %r" % source
    assert codegen.render(source, [{}]) == want


class Unprintable:
    """A value whose str() raises, the way a weewx unknown type does."""

    def __str__(self) -> str:
        raise AttributeError("foobar")


def test_the_filter_gets_the_raw_placeholder():
    # ct3 hands the filter the placeholder's own text as rawExpr on
    # every placeholder write. The default filter ignores it, which is
    # why 2026 corpus cases went by without noticing it was missing.
    # weewx's AssureUnicode does not ignore it: where str(value)
    # raises it writes rawExpr, so a page shows "$day.foobar.min"
    # rather than "foobar?".
    source = "$broken\n"
    context = {"broken": Unprintable()}
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    want = str(theirs(searchList=[dict(context)],
                      filter=WeewxAssureUnicode).respond())
    assert want == "$broken\n"
    assert codegen.render(source, [dict(context)],
                          output_filter=WeewxAssureUnicode) == want


def test_echo_does_not_get_the_raw_placeholder():
    # And #echo is the one write that does not, which is ct3's own
    # asymmetry: addEcho passes rawExpr=None.
    source = "#echo $broken\n"
    context = {"broken": Unprintable()}
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    want = str(theirs(searchList=[dict(context)],
                      filter=WeewxAssureUnicode).respond())
    assert want == "foobar?"
    assert codegen.render(source, [dict(context)],
                          output_filter=WeewxAssureUnicode) == want


# -- #errorCatcher ---------------------------------------------------
#
# The line every weewx skin opens with, and 103 of the 390 skin
# templates in the corpus stop at it. What it does is replace every
# placeholder after it with a wrapper that hands a NotFound to the
# catcher, and the two things that are easy to get wrong are where it
# stops: at an #end errorCatcher, and at the edge of a method, because
# ct3 keeps the flag on the method compiler and #def spawns a new one.

ERROR_CATCHER_SHAPES = [
    "#errorCatcher Echo\n$missing\n",
    "#errorCatcher Echo\n$a.b.c\n",
    "#errorCatcher Echo\n$known\n",
    "#errorCatcher BigEcho\n$missing\n",
    "#errorCatcher ListErrors\n$missing\n",
    # The same placeholder twice shares one wrapper.
    "#errorCatcher Echo\n$missing$missing\n",
    "#errorCatcher Echo\n#for $i in range(2)\n$missing\n#end for\n",
    # Off again, and the catcher stops there.
    "#errorCatcher Echo\n$missing\n#end errorCatcher\n$known\n",
    # A method is its own compiler in ct3, so its body is written
    # plain and the NotFound comes out at the call.
    "#errorCatcher Echo\n#def g\n$missing\n#end def\n$g\n",
    "#errorCatcher Echo\n#block b\n$missing\n#end block\n",
    "#errorCatcher Echo\n#def g\n$known\n#end def\n$g$missing\n",
    # And the other way round: on inside the method only.
    "#def g\n#errorCatcher Echo\n$missing\n#end def\n$g\n",
    # The silence and cache tokens wrap around it.
    "#errorCatcher Echo\n$!missing\n",
    "#errorCatcher Echo\n$*missing\n",
    # A region is not a method, so the catcher reaches into it.
    "#errorCatcher Echo\n#filter None\n$missing\n#end filter\n",
    "#errorCatcher Echo\n#call str\n$missing\n#end call\n",
    # And the tag decides about its own line like any other directive.
    "L#errorCatcher Echo\nT\n",
    "  #errorCatcher Echo\nT\n",
]


@pytest.mark.parametrize("source", ERROR_CATCHER_SHAPES)
def test_the_error_catcher_matches_ct3(source):
    context = {"known": "K"}
    theirs = Template.compile(source=source, useCache=False,
                              cacheCompilationResults=False)
    try:
        want = str(theirs(searchList=[dict(context)]).respond())
    except Exception as error:                                 # noqa: BLE001
        want = "!!%s" % type(error).__name__
    assert codegen.supports(source), "refused: %r" % source
    try:
        got = codegen.render(source, [dict(context)])
    except Exception as error:                                 # noqa: BLE001
        got = "!!%s" % type(error).__name__
    assert got == want


def test_it_refuses_a_catcher_that_does_not_exist():
    # ct3 writes ErrorCatchers.Nope(self) and lets it fail at render.
    # Saying so at generate time beats an AttributeError out of a
    # module nobody wrote by hand.
    assert not codegen.supports("#errorCatcher Nope\n$missing\n")


def test_it_reads_past_a_stop():
    # ct3 writes a return and carries on generating, so a template that
    # is malformed after #stop still fails to compile. Stopping the
    # walk there would render it.
    assert not codegen.supports("A\n#stop\n#attr $a = 1T")


# -- Against the corpus ----------------------------------------------

def taken():
    """Corpus cases this layer claims it can render.

    The case's compiler settings go in with it. Without them the
    generator silently assumed ct3's defaults, and the test below
    skipped exactly the cases where that assumption is wrong.
    """
    return [case for case in CASES
            if codegen.supports(case.template, decode(case.settings))]


@needs_corpus
def test_everything_it_takes_it_gets_right():
    # The absolute one. Coverage is worth nothing if a case it accepts
    # comes out different from what ct3 produced.
    # Nothing is skipped. The skip that used to stand here covered 384
    # of the accepted cases, and all 24 that came out wrong lived in
    # it: the generator was reading none of ct3's compiler settings and
    # the one test that would have noticed looked away. A case this
    # layer accepts is rendered and compared, whatever it carries.
    wrong = []
    for case in taken():
        try:
            actual = codegen.render(
                case.template, namespaces.build(case),
                output_filter=resolve(case.filter),
                settings=decode(case.settings))
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
