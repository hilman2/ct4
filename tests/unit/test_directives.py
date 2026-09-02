"""Directives a project registers in a ct4.toml, handled on the ast.

The successor of ct3's macroDirectives: registered in a file beside
the templates, found by every tool that looks at the file, and given
statements to put in place rather than text to parse again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from Cheetah.Template import Template

from ct4 import check, cli, directives, trace
from ct4.lang import backend, codegen, lex, tree

TOML = """\
# The sample handlers live beside the tests.
[directives]
greet = "sample_directives:greet"
fail = "sample_directives:failing"
wrong = "sample_directives:wrong"

[blocks]
box = "sample_directives:box"
twice = "sample_directives:twice"
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "ct4.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    return tmp_path


def render(project, source, context=None, name="page.tmpl"):
    path = project / name
    path.write_text(source, encoding="utf-8")
    return codegen.render(source, [context or {}], file=str(path))


# -- What a handler can do -------------------------------------------

def test_a_line_directive_writes_where_it_stood(project):
    # On a line of its own the tag takes its line ending with it, the
    # way #echo does. Inside a line the argument runs to the end token,
    # and what follows that is text.
    assert render(project, "A\n#greet $name\nB\n", {"name": "World"}) == \
        "A\nHello, World!B\n"
    assert render(project, "A #greet $name# B\n", {"name": "World"}) == \
        "A Hello, World! B\n"


def test_a_block_puts_its_body_where_BODY_stands(project):
    assert render(project, "#box card\n  hi $v\n#end box\nZ\n", {"v": 1}) \
        == '<div class="card">  hi 1\n</div>Z\n'
    assert render(project, "#box: hi $v\nZ\n", {"v": 1}) == \
        "<div>hi 1</div>\nZ\n"
    assert render(project, "#box\n#end box\n") == "<div></div>"


def test_the_body_can_stand_twice(project):
    assert render(project, "#twice\nx$v\n#end twice\n", {"v": 1}) == \
        "x1\nx1\n"


def test_blocks_nest_with_the_rest_of_the_language(project):
    source = "#for $i in [1, 2]\n#box\n$i#slurp\n#end box\n#end for\n"
    assert render(project, source) == "<div>1</div><div>2</div>"
    source = "#box\n#for $i in [1, 2]\n$i\n#end for\n#end box\n"
    assert render(project, source) == "<div>1\n2\n</div>"


def test_an_argument_resolves_a_name_the_template_bound(project):
    # expression() goes through the same reader as a #set, so a #for
    # target is a local here as it is in a placeholder.
    source = "#for $n in ['a', 'b']\n#greet $n\n#end for\n"
    assert render(project, source) == "Hello, a!Hello, b!"


# -- Every road leads through the generator ---------------------------

def test_weewx_path_compiles_it(project):
    # Template(file=...) is how weewx compiles every page. The backend
    # knows the file, so it finds the ct4.toml beside it.
    path = project / "page.tmpl"
    path.write_text("#box\n$v\n#end box\n", encoding="utf-8")
    backend.install()
    try:
        made = Template(file=str(path), searchList=[{"v": 7}]).respond()
    finally:
        backend.uninstall()
    assert made == "<div>7\n</div>"


def test_ct4_render_finds_the_registration(project):
    path = project / "page.tmpl"
    path.write_text("#greet $name\n", encoding="utf-8")
    context = project / "c.json"
    context.write_text(json.dumps({"name": "cli"}), encoding="utf-8")
    out = project / "out.txt"
    assert cli.main(["render", str(path), "--context", str(context),
                     "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "Hello, cli!"


def test_ct4_check_knows_the_names(project):
    # ct3 reads "#end box" as an invalid end directive. With the
    # registration the check goes through the generator and finds
    # nothing; without it the same file is a parse error.
    path = project / "page.tmpl"
    path.write_text("#box\n$v\n#end box\n", encoding="utf-8")
    assert check.check_file(path, []) == []
    (project / "ct4.toml").unlink()
    found = check.check_file(path, [])
    assert [f.code for f in found] == ["CT4001"]


def test_no_falling_back_to_ct3(project):
    # A template that uses a registered directive and something the
    # generator refuses is an error with the reason, because ct3 would
    # read the directive as text and render a page nobody wrote.
    path = project / "page.tmpl"
    path.write_text("#compiler-settings\n"
                    "gobbleWhitespaceAroundMultiLineComments = False\n"
                    "#end compiler-settings\n#box\nx\n#end box\n",
                    encoding="utf-8")
    backend.install()
    try:
        with pytest.raises(directives.DirectiveError) as error:
            Template(file=str(path), searchList=[{}]).respond()
    finally:
        backend.uninstall()
    assert "#box" in str(error.value) and "ct4.toml" in str(error.value)


# -- Errors name their place -----------------------------------------

def test_an_error_inside_a_handler_names_the_directive_line(project):
    with pytest.raises(ValueError) as error:
        render(project, "x\n#fail\n")
    assert trace.notes_of(error.value) == [
        "template: %s, line 2, column 1" % (project / "page.tmpl")]


def test_a_handler_that_returns_no_statement_is_refused(project):
    with pytest.raises(directives.DirectiveError) as error:
        render(project, "#wrong\n")
    assert "#wrong" in str(error.value) and "str" in str(error.value)


def test_a_target_that_cannot_be_loaded_names_the_file(project):
    (project / "ct4.toml").write_text(
        '[directives]\nnope = "sample_directives:missing"\n',
        encoding="utf-8")
    with pytest.raises(directives.DirectiveError) as error:
        render(project, "#nope\n")
    assert "ct4.toml" in str(error.value)
    assert "#nope" in str(error.value)
    assert "sample_directives:missing" in str(error.value)


def test_BODY_is_only_for_a_block(project):
    (project / "ct4.toml").write_text(
        '[directives]\nbox = "sample_directives:box"\n', encoding="utf-8")
    with pytest.raises(directives.DirectiveError) as error:
        render(project, "#box\n")
    assert "BODY" in str(error.value)


# -- The registration file -------------------------------------------

def test_cheetahs_own_names_cannot_be_registered(tmp_path):
    path = tmp_path / "ct4.toml"
    path.write_text('[directives]\nset = "a:b"\n', encoding="utf-8")
    with pytest.raises(directives.DirectiveError) as error:
        directives.load(path)
    assert "#set" in str(error.value)


def test_a_name_in_both_tables_is_refused(tmp_path):
    path = tmp_path / "ct4.toml"
    path.write_text('[directives]\nx = "a:b"\n[blocks]\nx = "a:b"\n',
                    encoding="utf-8")
    with pytest.raises(directives.DirectiveError) as error:
        directives.load(path)
    assert "[directives]" in str(error.value)


def test_a_target_has_to_name_module_and_callable(tmp_path):
    path = tmp_path / "ct4.toml"
    path.write_text('[directives]\nx = "just_a_module"\n', encoding="utf-8")
    with pytest.raises(directives.DirectiveError) as error:
        directives.load(path)
    assert "package.module:function" in str(error.value)


def test_the_nearest_file_wins_and_none_means_none(tmp_path):
    (tmp_path / "ct4.toml").write_text('[directives]\na = "m:f"\n',
                                       encoding="utf-8")
    nested = tmp_path / "skins" / "one"
    nested.mkdir(parents=True)
    assert directives.find_for(str(nested / "page.tmpl")).names == {"a"}
    (nested / "ct4.toml").write_text('[directives]\nb = "m:f"\n',
                                     encoding="utf-8")
    assert directives.find_for(str(nested / "page.tmpl")).names == {"b"}
    assert directives.find_for(None) is directives.NONE or \
        directives.find_for(None).path != nested / "ct4.toml"


def test_the_plain_reader_agrees_with_tomllib(tmp_path):
    if sys.version_info < (3, 11):
        pytest.skip("no tomllib to compare with")
    import tomllib

    text = TOML + '\n[other]\nx = "y"  # a comment\n'
    assert directives.read_plain_tables(text, tmp_path / "ct4.toml") == \
        tomllib.loads(text)


def test_the_plain_reader_refuses_what_it_cannot_read(tmp_path):
    with pytest.raises(directives.DirectiveError) as error:
        directives.read_plain_tables("[directives]\nx = 1\n",
                                     tmp_path / "ct4.toml")
    assert "line 2" in str(error.value)


# -- The tree ----------------------------------------------------------

def test_a_registered_block_must_be_closed():
    names = tree.syntax(block=["box"])
    with pytest.raises(tree.StructureError):
        tree.parse("#box\nx\n", names)
    # The end tag stops after the name, the way ct3 closes a macro
    # directive, so what follows it on the line is text again.
    root = tree.parse("#box a\nx\n#end box tail\n", names)
    assert tree.unparse(root) == "#box a\nx\n#end box tail\n"
    assert [(node.kind, node.name) for node in root.children] == \
        [(tree.BLOCK, "box"), (lex.TEXT, "")]
    assert root.children[1].tokens[0].text == "tail\n"


def test_without_a_registration_the_name_is_text():
    root = tree.parse("#box\nx\n")
    assert [node.kind for node in root.children] == ["text"]
