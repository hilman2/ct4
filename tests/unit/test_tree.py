"""The block tree, and the two assertions it is measured on.

Writing it back gives the source. And whether a structure is well
formed has to be decided the way ct3 decides it, not the way I read
the rules: ct3 refuses a template with an unbalanced block, and this
has to refuse exactly those. Disagreement in either direction is a
finding, and both directions are checked over the whole corpus.
"""

from __future__ import annotations

import pytest

from ct4.lang import tree
from tests.unit.test_lex import ALL, needs_corpus


def compiles_under_ct3(source: str) -> bool:
    """Whether ct3's own parser accepts this template."""
    from Cheetah.Compiler import ModuleCompiler

    try:
        str(ModuleCompiler(source, moduleName="t", mainClassName="t",
                           settings={"addTimestampsToCompilerOutput": False}))
    except Exception:                                       # noqa: BLE001
        return False
    return True


def accepted(source: str) -> bool:
    try:
        tree.parse(source)
    except tree.StructureError:
        return False
    return True


# -- Shape -----------------------------------------------------------

def test_a_loop_becomes_a_block():
    root = tree.parse("#for $r in $rows\n$r.name\n#end for\n")
    found = list(tree.blocks(root))
    assert [b.name for b in found] == ["for"]
    assert "$r.name" in found[0].text()


def test_blocks_nest():
    source = ("#for $r in $rows\n#if $r.ok\n$r.name\n#end if\n"
              "#end for\n")
    root = tree.parse(source)
    assert [b.name for b in tree.blocks(root)] == ["for", "if"]
    assert tree.depth_of(root) == {(1, 1): 0, (2, 1): 1}


def test_a_directive_without_an_end_is_not_a_block():
    root = tree.parse("#set $a = 1\ntext\n")
    assert not list(tree.blocks(root))


def test_the_arguments_belong_to_the_directive():
    root = tree.parse("#for $r in $rows\nbody\n#end for\n")
    block = list(tree.blocks(root))[0]
    # The opening directive owns its line, the body is a child.
    own = "".join(t.text for t in block.tokens)
    assert own.startswith("#for") and own.endswith("\n")
    assert "body" not in own


def test_the_short_form_closes_with_its_line():
    root = tree.parse("#if $x: yes\nafter\n")
    # It opens no block that anything else falls into.
    assert tree.depth_of(root) in ({}, {(1, 1): 0})
    assert "after" in root.text()


# -- Losslessness ----------------------------------------------------

@pytest.mark.parametrize("source", [
    "plain text\n",
    "#for $r in $rows\n$r.name\n#end for\n",
    "#if $a\nx\n#else\ny\n#end if\n",
    "#def show($x)\n$x\n#end def\n",
    "## comment\n",
    "#raw\n$not.a.placeholder\n#end raw\n",
    "a { color: #fff; }\n",
    # The end token form, taken from the corpus rather than invented:
    # every hash here closes a directive tag, and the if is closed by
    # its own #end at the end.
    "#if 1##for i in [1]#x#end for##end if",
    "",
])
def test_the_source_comes_back(source):
    assert tree.unparse(tree.parse(source)) == source


@needs_corpus
def test_every_template_comes_back_byte_for_byte():
    broken = []
    for name, source in ALL:
        try:
            root = tree.parse(source)
        except tree.StructureError:
            continue
        if tree.unparse(root) != source:
            broken.append(name)
    assert not broken, "%d of %d templates: %s" % (
        len(broken), len(ALL), broken[:5])


# -- Agreement with ct3 ----------------------------------------------

@needs_corpus
def test_the_same_templates_are_accepted_as_by_ct3():
    # Both directions. A tree builder that accepts everything passes
    # the losslessness assertion above and is worth nothing.
    refused_here = []
    refused_there = []
    for name, source in ALL:
        mine = accepted(source)
        theirs = compiles_under_ct3(source)
        if theirs and not mine:
            refused_here.append(name)
        elif mine and not theirs:
            refused_there.append(name)
    assert not refused_here, (
        "%d templates ct3 accepts and this refuses: %s"
        % (len(refused_here), refused_here[:10]))
    assert not refused_there, (
        "%d templates ct3 refuses and this accepts: %s"
        % (len(refused_there), refused_there[:10]))


def test_an_unclosed_block_is_refused():
    with pytest.raises(tree.StructureError):
        tree.parse("#for $r in $rows\n$r\n")


def test_a_wrong_end_is_refused():
    with pytest.raises(tree.StructureError):
        tree.parse("#if $a\nx\n#end for\n")


def test_an_end_without_a_block_is_refused():
    with pytest.raises(tree.StructureError):
        tree.parse("text\n#end for\n")


def test_the_error_names_the_place():
    with pytest.raises(tree.StructureError) as error:
        tree.parse("text\n#for $r in $rows\n$r\n")
    assert error.value.line == 2
