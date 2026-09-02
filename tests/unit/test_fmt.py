"""ct4 fmt: the one whitespace Cheetah does not output, made regular.

The promise is in two halves. The page is the same before and after,
which the corpus proves by rendering both. And the #end stands under
its opener, which the small cases show.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ct4 import cli, fmt
from ct4.corpus import check as corpus_check
from ct4.corpus.case import RENDER, read_jsonl
from ct4.lang import lex, tree


def formatted(source, unit="    "):
    return fmt.format_source(source, unit)


# -- What changes ------------------------------------------------------

def test_the_end_stands_under_its_opener():
    assert formatted("#if $x\nyes\n    #end if\n") == "#if $x\nyes\n#end if\n"
    assert formatted("  #for $i in $r\n$i\n#end for\n") == \
        "  #for $i in $r\n$i\n  #end for\n"


def test_a_branch_aligns_and_a_line_inside_steps_in():
    source = "#if $x\n#set $a = 1\n  #else\n      ## note\n#end if\n"
    assert formatted(source) == \
        "#if $x\n    #set $a = 1\n#else\n    ## note\n#end if\n"


def test_blocks_nest_from_the_outer_baseline():
    source = "  #for $i in $r\n#if $i\n#echo $i\n#end if\n#end for\n"
    assert formatted(source) == (
        "  #for $i in $r\n      #if $i\n          #echo $i\n"
        "      #end if\n  #end for\n")


def test_a_top_level_line_keeps_its_own_indent():
    # The author put it there to sit in the markup around it, and it
    # is the baseline the block hangs from.
    source = "<ul>\n    #for $i in $r\n    <li>$i</li>\n    #end for\n</ul>\n"
    assert formatted(source) == source
    assert formatted("    #set $a = 1\n") == "    #set $a = 1\n"


def test_a_tab_is_a_unit_too():
    assert formatted("#if 1\n#echo 1\n#end if\n", "\t") == \
        "#if 1\n\t#echo 1\n#end if\n"


def test_crlf_stays_crlf():
    assert formatted("#if 1\r\n#echo 1\r\n  #end if\r\n") == \
        "#if 1\r\n    #echo 1\r\n#end if\r\n"


# -- What is left alone ------------------------------------------------

@pytest.mark.parametrize("source", [
    # Text before the tag: the indent is output.
    "#if 1\nx #end if\n",
    # A hash ends the tag, so the indent before it stays in the page.
    "#if 1\n  #echo 1#\n#end if\n",
    # A comment on the tag's line commits the pending text first.
    "#if 1\n  #set $a = 1 ## note\n#end if\n",
    # The colon short form writes its indent.
    "#if 1\n  #if 2: y\n#end if\n",
    # Nothing inside a #raw is a directive line.
    "#if 1\n#raw\n  #end if\n#end raw\n#end if\n",
    # Settings are not template text.
    "#compiler-settings\n  useNameMapper = False\n#end compiler-settings\n",
    # A JSON-mode template has a parser of its own.
    '#mode json\n{\n#for $i in $r\n  "a": $i\n#end for\n}\n',
])
def test_the_indent_that_is_output_is_not_touched(source):
    assert formatted(source) == source


def test_an_open_block_is_refused_rather_than_formatted():
    with pytest.raises(tree.StructureError):
        formatted("#if 1\n  #echo 1\n")


# -- The command --------------------------------------------------------

def test_the_command_rewrites_and_check_only_reports(tmp_path, capsys):
    path = tmp_path / "p.tmpl"
    path.write_bytes(b"#if 1\r\n#echo 1\r\n  #end if\r\n")
    assert cli.main(["fmt", "--check", str(path)]) == 1
    # The #echo line steps in and the #end if steps out.
    assert "2 line(s) would change" in capsys.readouterr().out
    assert path.read_bytes() == b"#if 1\r\n#echo 1\r\n  #end if\r\n"
    assert cli.main(["fmt", str(path)]) == 0
    assert path.read_bytes() == b"#if 1\r\n    #echo 1\r\n#end if\r\n"
    assert cli.main(["fmt", "--check", str(path)]) == 0


def test_the_command_reports_a_broken_file_and_goes_on(tmp_path, capsys):
    broken = tmp_path / "a.tmpl"
    broken.write_text("#if 1\n", encoding="utf-8")
    fine = tmp_path / "b.tmpl"
    fine.write_text("#if 1\n  #end if\n", encoding="utf-8")
    assert cli.main(["fmt", str(broken), str(fine)]) == 2
    assert "line 1" in capsys.readouterr().err
    assert fine.read_text(encoding="utf-8") == "#if 1\n#end if\n"


# -- The corpus says the page is the same -----------------------------

def corpus_dir():
    here = Path(__file__).resolve()
    for candidate in (Path("/repo/corpus"), here.parents[2] / "corpus"):
        if (candidate / "ct3-tests.jsonl").exists():
            return candidate
    return None


def corpus_cases():
    root = corpus_dir()
    if root is None:
        return []
    cases = []
    for name in ("ct3-tests.jsonl", "weewx-render.jsonl", "skins.jsonl"):
        cases.extend(read_jsonl(root / name))
    return cases


def test_formatting_is_idempotent_and_keeps_the_token_kinds():
    cases = corpus_cases()
    if not cases:
        pytest.skip("no corpus")
    seen = 0
    for case in cases:
        try:
            once = formatted(case.template)
        except tree.StructureError:
            continue
        seen += 1
        assert formatted(once) == once, case.id
        # The same directives, placeholders and comments in the same
        # order; only the text between them may have moved.
        assert [t.kind for t in lex.tokens(once) if t.kind != lex.TEXT] == \
            [t.kind for t in lex.tokens(case.template)
             if t.kind != lex.TEXT], case.id
    assert seen > 1000


def test_the_rendered_page_does_not_change():
    cases = [case for case in corpus_cases() if case.kind == RENDER]
    if not cases:
        pytest.skip("no corpus")
    compared = 0
    for case in cases:
        try:
            once = formatted(case.template)
        except tree.StructureError:
            continue
        if once == case.template:
            continue
        try:
            before = corpus_check.render(case)
        except Exception:                                   # noqa: BLE001
            continue
        after = corpus_check.render(dataclasses.replace(case, template=once))
        assert after == before, case.id
        compared += 1
    assert compared > 50
