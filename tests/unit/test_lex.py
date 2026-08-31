"""The lexer, and the two assertions that make it worth having.

Losslessness on its own proves nothing: a lexer that calls the whole
file one text token passes it. The second assertion is what gives it
teeth. Every placeholder the real compiler resolves has to be a
PLACEHOLDER token here, at the same line and column, and ct4.analyze
reads those out of the code the compiler generates. So the ground truth
comes from the implementation and not from a second opinion.

Both run over every template of the corpus. The corpus is mounted
read-only in the container and is not copied into the work directory,
so it is looked for in both places.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ct4 import analyze
from ct4.corpus.case import read_jsonl
from ct4.lang import lex

CORPUS_FILES = ("ct3-tests.jsonl", "skins.jsonl", "weewx-render.jsonl")


def corpus_dir() -> Path | None:
    here = Path(__file__).resolve()
    for candidate in (Path("/repo/corpus"), here.parents[2] / "corpus"):
        if (candidate / CORPUS_FILES[0]).exists():
            return candidate
    return None


def templates() -> list[tuple[str, str]]:
    root = corpus_dir()
    if root is None:
        return []
    found = []
    for name in CORPUS_FILES:
        path = root / name
        if path.exists():
            found.extend((case.id, case.template) for case in read_jsonl(path))
    return found


ALL = templates()
needs_corpus = pytest.mark.skipif(
    not ALL, reason="the corpus is not reachable from here")


# -- Small cases, for fast feedback ----------------------------------

@pytest.mark.parametrize("source", [
    "plain text\n",
    "Hello $name\n",
    "$day.outTemp.max\n",
    "${day.outTemp.max}\n",
    "$day.outTemp.format($fmt, add_label=False)\n",
    "#for $r in $rows\n$r.name\n#end for\n",
    "## a comment\n",
    "#* a block\ncomment *#\n",
    "<% print('psp') %>\n",
    "costs \\$5 and \\#1\n",
    "#raw\n$not.a.placeholder\n#end raw\n",
    "a { color: #fff; }\n",
    "$5.00 is not a placeholder\n",
    "#set $a = 1# and on we go\n",
    "",
])
def test_the_source_comes_back(source):
    assert lex.joined(lex.tokens(source)) == source


def kinds(source):
    return [token.kind for token in lex.tokens(source)]


def test_a_placeholder_is_one_token():
    found = lex.tokens("Hello $day.outTemp.max!\n")
    assert [t.kind for t in found] == [lex.TEXT, lex.PLACEHOLDER, lex.TEXT]
    assert found[1].text == "$day.outTemp.max"


def test_a_hash_in_css_is_text():
    assert kinds("a { color: #fff; }\n") == [lex.TEXT]


def test_a_price_is_text():
    assert kinds("$5.00 and $ alone\n") == [lex.TEXT]


def test_an_escape_is_its_own_token():
    found = lex.tokens("costs \\$5\n")
    assert lex.ESCAPE in [t.kind for t in found]


def test_a_directive_token_is_the_hash_and_the_name():
    # Its arguments stay in the stream, because they hold placeholders
    # that a formatter and a language server have to see. Where the
    # arguments end is a question about grammar, and that belongs to
    # the layer above.
    found = lex.tokens("#set $a = 1\ntext\n")
    assert found[0].kind == lex.DIRECTIVE
    assert found[0].text == "#set"
    assert [t.kind for t in found] == [
        lex.DIRECTIVE, lex.TEXT, lex.PLACEHOLDER, lex.TEXT]


def test_a_for_directive_shows_both_its_placeholders():
    found = lex.tokens("#for $r in $rows\n")
    assert [t.text for t in found if t.kind == lex.PLACEHOLDER] == \
        ["$r", "$rows"]


def test_a_raw_block_keeps_its_contents_out_of_the_scan():
    found = lex.tokens("#raw\n$not.a.placeholder\n#end raw\n")
    assert lex.PLACEHOLDER not in [t.kind for t in found]
    assert found[1].kind == lex.RAW
    assert found[1].text == "\n$not.a.placeholder\n"


def test_a_nested_placeholder_is_found():
    # $func($anInt) holds two lookups. Flattened, the inner one is
    # invisible to every layer above.
    found = lex.walk(lex.tokens("$func($anInt)\n"))
    assert [lex.path_of(t) for t in found
            if t.kind == lex.PLACEHOLDER] == ["func", "anInt"]


def test_a_placeholder_inside_a_subscript_is_found():
    source = "$aDict[$anObj.meth('nested')].two\n"
    found = lex.walk(lex.tokens(source))
    assert [lex.path_of(t) for t in found
            if t.kind == lex.PLACEHOLDER] == ["aDict", "anObj.meth"]


def test_a_hash_inside_an_expression_is_not_a_directive():
    # The reason placeholders nest instead of being split apart: inside
    # a Python expression a hash is a string or a comment, never source.
    found = lex.tokens("${a['#for']}\n")
    assert lex.DIRECTIVE not in [t.kind for t in lex.walk(found)]


def test_i18n_is_a_directive():
    # It is registered as a macro directive rather than standing in
    # directiveNamesAndParsers. Read only the one list and "#i18n: $x"
    # lexes as plain text.
    assert lex.tokens("#i18n: $x\n")[0].kind == lex.DIRECTIVE


def test_the_short_form_of_raw_ends_with_its_line():
    # "#raw: ..." is raw to the end of that line. The next line is
    # source again, and the corpus has a case whose placeholder sits
    # there.
    found = lex.walk(lex.tokens("#raw: $aFunc().\n$anInt"))
    assert [lex.path_of(t) for t in found
            if t.kind == lex.PLACEHOLDER] == ["anInt"]


def test_a_hash_that_closes_a_directive_is_not_a_comment():
    # "#if 1##for i in range(10)#" is an if that ends at the hash and a
    # for that starts at the next one, not a comment.
    source = "#if 1##for i in [1]#x#end for#"
    seen = [t.kind for t in lex.tokens(source)]
    assert seen.count(lex.DIRECTIVE) >= 2
    assert lex.COMMENT not in seen


def test_mac_line_endings_still_count_as_lines():
    # A template saved with old Mac line endings has no newline in it.
    # Counting those would put every placeholder on line 1.
    found = lex.tokens("ab\rcd $x\r")
    placeholder = [t for t in found if t.kind == lex.PLACEHOLDER][0]
    assert (placeholder.line, placeholder.column) == (2, 4)


def test_line_and_column_count_from_one():
    found = lex.tokens("ab\ncd $x\n")
    placeholder = [t for t in found if t.kind == lex.PLACEHOLDER][0]
    assert (placeholder.line, placeholder.column) == (2, 4)


# -- Over the whole corpus -------------------------------------------

@needs_corpus
def test_every_template_comes_back_byte_for_byte():
    broken = []
    for name, source in ALL:
        if lex.joined(lex.tokens(source)) != source:
            broken.append(name)
    assert not broken, "%d of %d templates: %s" % (
        len(broken), len(ALL), broken[:5])


def paths_of(source):
    """The dotted name of every PLACEHOLDER token, nested ones too.

    Through walk and not over the top level: $func($anInt) holds two
    lookups, and the inner one is a child.
    """
    return [lex.path_of(t) for t in lex.walk(lex.tokens(source))
            if t.kind == lex.PLACEHOLDER]


def resolved(source):
    """What the real compiler looks up, or None if it will not compile."""
    try:
        return analyze.placeholders(source)
    except Exception:                                       # noqa: BLE001
        # A template the compiler itself refuses is not the lexer's
        # problem. The corpus holds a few by design.
        return None


@needs_corpus
def test_every_name_the_compiler_resolves_is_a_placeholder_here():
    # The compiler is the authority: a name it resolves and the lexer
    # calls text would be silently lost by every layer above this one.
    #
    # Compared by path and not by position. For a placeholder inside a
    # directive the compiler records the position of the directive,
    # not of the placeholder, so positions would be comparing two
    # different things. They are checked below, where they mean the
    # same thing.
    #
    # A prefix match, because the compiler splits $a.b.c($x) into a
    # lookup of a.b and a call of c, while the token holds the whole
    # chain.
    missed = []
    checked = 0
    for name, source in ALL:
        expected = resolved(source)
        if expected is None:
            continue
        checked += 1
        found = paths_of(source)
        for item in expected:
            if not any(path == item.path or path.startswith(item.path + ".")
                       for path in found):
                missed.append("%s: %s" % (name, item.path))
    assert checked > 1000, "only %d templates were checkable" % checked
    assert not missed, "%d names missed over %d templates: %s" % (
        len(missed), checked, missed[:10])


@needs_corpus
def test_positions_agree_where_they_mean_the_same_thing():
    # Only templates without directives. There the compiler records the
    # position of the placeholder itself, so the two can be held
    # against each other.
    wrong = []
    checked = 0
    for name, source in ALL:
        if any(t.kind == lex.DIRECTIVE for t in lex.tokens(source)):
            continue
        expected = resolved(source)
        if expected is None:
            continue
        checked += 1
        found = {(t.line, t.column) for t in lex.tokens(source)
                 if t.kind == lex.PLACEHOLDER}
        for item in expected:
            if (item.line, item.column) not in found:
                wrong.append("%s: %s at %d:%d"
                             % (name, item.path, item.line, item.column))
    assert checked > 100, "only %d templates were checkable" % checked
    assert not wrong, "%d positions off over %d templates: %s" % (
        len(wrong), checked, wrong[:10])


@needs_corpus
def test_the_tokens_cover_the_source_without_overlap():
    # Positions have to line up, or a later layer that slices the
    # source by them takes the wrong bytes.
    for name, source in ALL[:200]:
        offset = 0
        for token in lex.tokens(source):
            assert token.start == offset, name
            offset = token.end
        assert offset == len(source), name


def test_the_corpus_is_actually_reachable():
    # Otherwise the three cases above skip and this file says nothing.
    if os.environ.get("CT4_CORPUS_OPTIONAL"):
        pytest.skip("explicitly allowed to be absent")
    assert ALL, "the corpus was not found; the assertions above skipped"
