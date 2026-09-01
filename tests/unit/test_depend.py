"""The dependency graph, and the labels on what it cannot know.

Three things are measured here. That an ``#include`` is classified the
way its argument deserves: a constant name resolves to a file, a
concatenation with a hole in it names a set of files, and a call names
nothing at all. That a node whose answer is unreadable is opaque and
stays opaque all the way up, because a parent including an always-stale
child is itself always stale. And that the reading of the two include
flags agrees with the code generator's, over all 399 includes of the
skin corpus, so that the duplicated eight lines cannot drift apart.

The counts in the corpus test are the point of the module: 348 exact,
40 glob, 11 opaque is what the real skins hold, and a rule that turns
one of the 11 into an exact answer is how a stale file reaches a web
server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ct4 import analyze, depend
from ct4.lang import codegen, tree


def one_edge(source: str, settings: dict[str, object] | None = None,
             ) -> depend.Edge:
    """The single edge of a template written for one case."""
    found = depend.scan(source, settings)
    assert found.error == ""
    assert len(found.edges) == 1
    return found.edges[0]


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def corpus_file() -> Path | None:
    """The skin corpus, from a checkout or from the container mount.

    Looked for in both places, as test_lex does it: the Docker
    entrypoint copies a fixed list of directories into /work and
    corpus/ is not among them, but the repository is mounted read-only
    under /repo. Where neither has it, the corpus tests skip instead of
    failing.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "corpus" / "skins.jsonl"
        if candidate.exists():
            return candidate
    if Path("/repo/corpus/skins.jsonl").exists():
        return Path("/repo/corpus/skins.jsonl")
    return None


CORPUS = corpus_file()
needs_corpus = pytest.mark.skipif(
    CORPUS is None, reason="the corpus is not reachable from here")


# -- One include at a time -------------------------------------------

def test_a_constant_name_resolves_to_one_file():
    edge = one_edge('#include "part.inc"\n')
    assert edge.kind == depend.INCLUDE
    assert edge.certainty == depend.EXACT
    assert edge.target == "part.inc"
    assert edge.keys == ()
    assert not edge.conditional


def test_a_hole_in_the_middle_names_a_set_of_files():
    edge = one_edge('#include "sections/" + $section + ".inc"\n')
    assert edge.certainty == depend.GLOB
    assert edge.target == "sections/*.inc"
    # The key belongs to the edge: a changed $section names another
    # file, and staleness has to see that.
    assert edge.keys == ("section",)


def test_a_leading_hole_is_a_directory_of_unknown_depth():
    # $webdir is a path, not a file name, so the file can lie anywhere.
    edge = one_edge('#include $webdir + "/x.tmpl"\n')
    assert edge.certainty == depend.GLOB
    assert edge.target == "**/x.tmpl"
    assert edge.keys == ("webdir",)


def test_a_call_can_be_resolved_by_nobody():
    found = depend.scan('#include $get_icon($label, "rise")\n')
    edge = found.edges[0]
    assert edge.certainty == depend.OPAQUE
    assert edge.target == ""
    # The nested lookup counts too: a changed $label picks another
    # file, and only the inner placeholder says so.
    assert set(edge.keys) >= {"get_icon", "label"}
    assert found.opaque


def test_a_placeholder_standing_alone_is_opaque():
    # Not a glob over everything: not one character of the name is
    # known. Seven of the corpus's eleven opaque includes are this.
    edge = one_edge("#include $icon\n")
    assert edge.certainty == depend.OPAQUE
    assert edge.keys == ("icon",)


def test_the_raw_flag_is_read_by_startswith_and_not_by_words():
    assert one_edge('#include raw "part.inc"\n').raw is True
    # ct3 advances three characters and reads the rest as the
    # expression (Cheetah/Parser.py:2319), so what is left here is the
    # bare word "foo". A bare word is a name in the module namespace
    # and names no file, hence opaque.
    edge = one_edge("#include rawfoo\n")
    assert edge.raw is True
    assert edge.expression == "foo"
    assert edge.certainty == depend.OPAQUE


def test_the_source_form_is_no_file_edge():
    edge = one_edge("#include source=$text\n")
    assert edge.from_string is True
    assert edge.target == ""
    graph = depend.Graph(Path("."))
    assert graph.targets_of(edge) == []


def test_a_commented_out_include_is_no_include():
    # The case that matters is exfoliation/forecast_strip.inc, which
    # holds "## #include forecast_strip.inc": a regex over the source
    # gives that file an edge to itself and puts a cycle in the graph
    # that is not there.
    assert depend.scan("## #include part.inc\n").edges == ()


def test_an_include_inside_raw_is_text():
    assert depend.scan('#raw\n#include "part.inc"\n#end raw\n').edges == ()


def test_an_include_under_an_if_is_conditional():
    edge = one_edge('#if $x\n#include "part.inc"\n#end if\n')
    assert edge.conditional is True
    assert edge.line == 2
    # The edge still exists. 181 of the corpus's 399 stand under a
    # block whose body may not run, and dropping them would drop most
    # of the graph.
    assert edge.certainty == depend.EXACT


def test_an_indented_include_is_found():
    # A line-anchored regex misses 31 of the corpus's includes.
    edge = one_edge('  #include "part.inc"\n')
    assert edge.target == "part.inc"


# -- Modules are not files -------------------------------------------

def test_extends_is_a_module_edge():
    edge = one_edge("#extends Cheetah.Templates.SkeletonPage\n")
    assert edge.kind == depend.EXTENDS
    assert edge.certainty == depend.MODULE
    assert edge.target == "Cheetah.Templates.SkeletonPage"


def test_extends_with_expressions_allowed_says_nothing():
    edge = one_edge("#extends Cheetah.Templates.SkeletonPage\n",
                    {"allowExpressionsInExtendsDirective": True})
    assert edge.certainty == depend.OPAQUE


def test_import_names_its_module():
    edge = one_edge("#from os import path\n")
    assert edge.kind == depend.IMPORT
    assert edge.certainty == depend.MODULE
    assert edge.target == "os"


def test_a_module_that_is_there_is_no_finding_and_no_file(tmp_path):
    graph = depend.Graph(tmp_path)
    graph.add_source("a.tmpl", "#import os\n$os.sep\n")
    assert graph.findings() == []
    assert graph.dependencies("a.tmpl") == set()


def test_a_module_that_is_nowhere_gets_named(tmp_path):
    graph = depend.Graph(tmp_path)
    graph.add_source("a.tmpl", "#from ct4_nosuch_module import x\n")
    found = graph.findings()
    assert [item.code for item in found] == ["CT4315"]
    assert found[0].severity == "note"


# -- A template that cannot be read ----------------------------------

def test_an_unbalanced_block_is_opaque():
    found = depend.scan("#for $i in $rows\n$i\n")
    assert found.error
    assert found.opaque
    graph = depend.Graph(Path("."))
    graph.add_source("broken.tmpl", "#for $i in $rows\n$i\n")
    codes = [item.code for item in graph.findings()]
    assert codes == ["CT4313"]
    assert graph.opaque("broken.tmpl")


# -- The graph over a tree of files ----------------------------------

def test_a_chain_is_walked_to_the_end(tmp_path):
    write(tmp_path, "a.tmpl", '#include "b.inc"\n')
    write(tmp_path, "b.inc", '#include "c.inc"\n')
    write(tmp_path, "c.inc", '#include "d.inc"\n')
    write(tmp_path, "d.inc", "text\n")
    graph = depend.Graph(tmp_path)
    name = graph.add(tmp_path / "a.tmpl")
    assert name == "a.tmpl"
    assert graph.dependencies("a.tmpl") == {"b.inc", "c.inc", "d.inc"}
    assert graph.dependents("d.inc") == {"a.tmpl", "b.inc", "c.inc"}


def test_everyone_who_reads_a_fragment_is_a_dependent(tmp_path):
    for name in ("one.tmpl", "two.tmpl", "three.tmpl"):
        write(tmp_path, name, '#include "shared.inc"\n')
    write(tmp_path, "shared.inc", "text\n")
    graph = depend.Graph(tmp_path)
    for name in ("one.tmpl", "two.tmpl", "three.tmpl"):
        graph.add(tmp_path / name)
    assert graph.dependents("shared.inc") == {"one.tmpl", "two.tmpl",
                                              "three.tmpl"}


def test_a_name_with_no_file_is_a_note_and_not_an_error(tmp_path):
    # 69 of the corpus's 348 constant names have no file, most of them
    # optional hooks guarded by os.path.exists on the line above.
    write(tmp_path, "a.tmpl", '#include "hook.inc"\n')
    graph = depend.Graph(tmp_path)
    graph.add(tmp_path / "a.tmpl")
    assert graph.missing == {"a.tmpl": ["hook.inc"]}
    assert graph.dependencies("a.tmpl") == set()
    found = graph.findings()
    assert [item.code for item in found] == ["CT4311"]
    assert found[0].severity == "note"
    assert found[0].file == "a.tmpl"
    assert found[0].line == 1
    # Nothing about it makes the template always stale: the caller
    # watches the name and regenerates when the file turns up.
    assert not graph.opaque("a.tmpl")


def test_a_cycle_terminates_and_gets_named(tmp_path):
    write(tmp_path, "a.tmpl", '#include "b.tmpl"\n')
    write(tmp_path, "b.tmpl", '#include "a.tmpl"\n')
    graph = depend.Graph(tmp_path)
    graph.add(tmp_path / "a.tmpl")
    assert graph.dependencies("a.tmpl") == {"b.tmpl"}
    assert graph.dependencies("b.tmpl") == {"a.tmpl"}
    assert graph.cycles() == [("a.tmpl", "b.tmpl")]
    assert [item.code for item in graph.findings()] == ["CT4312"]


def test_a_glob_takes_every_file_it_matches(tmp_path):
    write(tmp_path, "a.tmpl", "#include 'sections/' + $section + '.inc'\n")
    write(tmp_path, "sections/one.inc", "one\n")
    write(tmp_path, "sections/two.inc", "two\n")
    write(tmp_path, "sections/other.txt", "not a match\n")
    graph = depend.Graph(tmp_path)
    graph.add(tmp_path / "a.tmpl")
    assert graph.dependencies("a.tmpl") == {"sections/one.inc",
                                           "sections/two.inc"}
    # A set of files is a resolvable answer, not an unknown one.
    assert not graph.opaque("a.tmpl")


def test_opacity_travels_upward(tmp_path):
    write(tmp_path, "a.tmpl", '#include "b.inc"\n')
    write(tmp_path, "b.inc", "#include $icon\n")
    graph = depend.Graph(tmp_path)
    graph.add(tmp_path / "a.tmpl")
    assert graph.opaque("b.inc")
    # The parent renders the child, so the parent is stale whenever the
    # child is.
    assert graph.opaque("a.tmpl")
    assert [item.code for item in graph.findings()] == ["CT4310"]


def test_a_name_above_the_base_keeps_its_full_path(tmp_path):
    # belchertown's */index.html.tmpl reaches ../header.html.tmpl. That
    # is normal and no finding.
    base = tmp_path / "skin"
    write(base, "index.tmpl", '#include "../above.tmpl"\n')
    write(tmp_path, "above.tmpl", "text\n")
    graph = depend.Graph(base)
    graph.add(base / "index.tmpl")
    above = (tmp_path / "above.tmpl").resolve().as_posix()
    assert graph.dependencies("index.tmpl") == {above}
    assert graph.findings() == []


def test_a_name_resolves_against_the_base_and_not_the_includer(tmp_path):
    # The load-bearing rule. Include names go through serverSidePath ->
    # abspath, so they resolve against the working directory of the
    # process. Measured over eight skins: 70 of 279 static includes
    # resolve only from the skin root and none only from the directory
    # of the file they stand in.
    write(tmp_path, "sub/a.tmpl", '#include "part.inc"\n')
    write(tmp_path, "part.inc", "from the base\n")
    write(tmp_path, "sub/part.inc", "beside the includer\n")
    graph = depend.Graph(tmp_path)
    graph.add(tmp_path / "sub" / "a.tmpl")
    assert graph.dependencies("sub/a.tmpl") == {"part.inc"}


def test_one_file_gets_one_name(tmp_path):
    write(tmp_path, "a.tmpl", '#include "sub/../part.inc"\n')
    write(tmp_path, "b.tmpl", '#include "part.inc"\n')
    write(tmp_path, "sub/other.inc", "text\n")
    write(tmp_path, "part.inc", "text\n")
    graph = depend.Graph(tmp_path)
    graph.add(tmp_path / "a.tmpl")
    graph.add(tmp_path / "b.tmpl")
    # Resolved before it is named, so the detour through sub/ is the
    # same node as the direct name and not a second one.
    assert graph.dependencies("a.tmpl") == {"part.inc"}
    assert graph.dependents("part.inc") == {"a.tmpl", "b.tmpl"}


# -- The context keys ------------------------------------------------

def test_a_set_target_is_invisible_to_placeholders():
    # ct3 writes no origin comment on a #set line, so analyze.
    # placeholders never reports what stands there. Measured over the
    # corpus: 21,439 lookups on lines with an origin, 9,249 on lines
    # without one, and 139 of 390 templates read at least one root that
    # placeholders never sees. webdir is one of them, and webdir is the
    # key that decides which file sabnzbd's #include names.
    source = "#set $n = $station.name\n$n\n"
    assert "station" in analyze.lookup_roots(source)
    assert "station" not in {p.root for p in analyze.placeholders(source)}


def test_an_include_argument_is_a_lookup_like_any_other():
    assert "webdir" in analyze.lookup_roots('#include $webdir + "/x"\n')


def test_the_placeholders_are_what_they_were():
    found = analyze.placeholders("$day.outTemp.max\n")
    assert [(p.path, p.line, p.column) for p in found] == [
        ("day.outTemp.max", 1, 1)]


def test_the_keys_of_a_scan_are_the_roots():
    found = depend.scan("$station.location\n#include $webdir + '/x'\n")
    assert {"station", "webdir"} <= found.keys


# -- Over the whole corpus -------------------------------------------

@needs_corpus
def test_the_corpus_holds_three_kinds_of_include():
    from ct4.corpus.case import read_jsonl

    assert CORPUS is not None
    counted = {depend.EXACT: 0, depend.GLOB: 0, depend.OPAQUE: 0}
    nodes = 0
    conditional = 0
    for case in read_jsonl(CORPUS):
        found = depend.scan(case.template)
        # Every one of the 390 can be read: the tree parses it and ct3
        # compiles it, so no skin is opaque for want of a reader.
        assert found.error == ""
        for edge in found.edges:
            if edge.kind != depend.INCLUDE:
                continue
            nodes += 1
            counted[edge.certainty] += 1
            conditional += edge.conditional
    # 410 occurrences of the text "#include", 368 of them at the start
    # of a line, 399 real directives. Only the tree gets that right.
    assert nodes == 399
    assert counted == {depend.EXACT: 348, depend.GLOB: 40,
                       depend.OPAQUE: 11}
    # Every one of them stands under an #if, 38 of them under a #for
    # as well. They are edges all the same.
    assert conditional == 181


@needs_corpus
def test_the_two_readings_of_the_include_flags_agree():
    # The eight lines are duplicated on purpose: codegen's version is
    # private, rewrites the expression into VFFSL text and refuses
    # cases a graph has to record. This is what keeps the copy honest.
    from ct4.corpus.case import read_jsonl

    assert CORPUS is not None
    checked = 0
    for case in read_jsonl(CORPUS):
        root = tree.parse(case.template)
        for node in root.walk():
            if node.name != depend.INCLUDE:
                continue
            edge = depend.read_include(node, False)
            raw, from_string, _ = codegen._include_argument(node)
            assert (raw, from_string) == (edge.raw, edge.from_string)
            checked += 1
    assert checked == 399
