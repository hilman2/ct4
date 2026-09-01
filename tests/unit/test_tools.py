"""The tool layer: analyse, declare, check, report."""

from __future__ import annotations

import json

import pytest

from ct4 import analyze, diagnostics, reference
from ct4.check import check_source, is_json_template, unresolved
from ct4.declare import Declaration, Node, resolve

# A declaration like the one weewx has: time span, open reading,
# closed aggregates.
WEEWX = Declaration(
    name="sample",
    roots={
        "day": Node(fields={
            "records": Node(open=True),
            "*": Node(fields={"max": Node(), "min": Node(),
                              "sum": Node(), "avg": Node()}),
        }),
        "station": Node(open=True),
    })


# -- Finding placeholders --------------------------------------------

def test_paths_with_line_and_column():
    found = analyze.placeholders("Hello $user.name\n$day.outTemp.max\n")
    assert [(p.path, p.line) for p in found] == [
        ("user.name", 1), ("day.outTemp.max", 2)]


def test_the_loop_variable_is_included():
    found = analyze.placeholders("#for $r in $rows\n$r.x\n#end for\n")
    assert analyze.paths(found) == ["r.x", "rows"]


def test_errorcatcher_hides_nothing():
    # Seasons sets #errorCatcher. Cheetah then puts every placeholder
    # into its own method, and the origin is recorded in a different
    # form. Without this case the weewx skins lose their placeholders.
    source = "#errorCatcher Echo\n$day.rain.sum\n"
    assert analyze.paths(analyze.placeholders(source)) == ["day.rain.sum"]


def test_roots_without_repetition():
    source = "$day.a.max $day.b.min $station.x"
    assert analyze.roots(analyze.placeholders(source)) == ["day", "station"]


# -- Declaration -----------------------------------------------------

def test_a_known_path_reports_nothing():
    assert resolve(WEEWX, "day.outTemp.max") is None


def test_an_unknown_aggregate_is_reported():
    unknown = resolve(WEEWX, "day.outTemp.mx")
    assert unknown is not None
    assert unknown.name == "mx"
    assert unknown.suggestions == ("max",)


def test_an_open_node_ends_the_check():
    assert resolve(WEEWX, "station.anything.deeper") is None


def test_an_unknown_root_is_not_an_error():
    # A root nobody declared is unknown territory, not an error.
    # Anything else would be a false positive.
    assert resolve(WEEWX, "brandNew.x") is None


def test_round_trip_through_json(tmp_path):
    path = tmp_path / "sample.json"
    WEEWX.save(path)
    reloaded = Declaration.load(path)
    assert resolve(reloaded, "day.outTemp.mx") is not None
    assert resolve(reloaded, "day.outTemp.max") is None


# -- Checking --------------------------------------------------------

def test_a_typo_is_found():
    found = check_source("$day.outTemp.mx\n", "sample.tmpl", [WEEWX])
    assert [d.code for d in found] == ["CT4103"]
    assert found[0].line == 1
    assert "max" in found[0].suggestions


def test_a_correct_template_stays_quiet():
    assert check_source("$day.outTemp.max\n", "sample.tmpl", [WEEWX]) == []


def test_a_dropped_dot_is_reported():
    # weewx' own test skin writes ".round(5)json()" one line under
    # ".round(5).json()". Cheetah compiles the two the same, so no
    # engine can tell them apart and nothing about the page says so.
    found = check_source("$day.outTemp.max.format(2)json()\n",
                         "sample.tmpl", [WEEWX])
    assert [d.code for d in found] == ["CT4005"]
    assert (found[0].line, found[0].column) == (1, 27)
    assert "json" in found[0].message


def test_text_swallowed_by_a_chain_is_reported():
    # The same rule read the other way round: the F was meant to be
    # printed and became an attribute lookup.
    found = check_source("It is $day.outTemp.max.format(2)F today.\n",
                         "sample.tmpl", [WEEWX])
    assert [d.code for d in found] == ["CT4005"]


def test_a_chain_written_with_its_dots_stays_quiet():
    assert check_source("$day.outTemp.max.format(2).json()\n",
                        "sample.tmpl", [WEEWX]) == []


def test_a_syntax_error_names_the_place():
    found = check_source("#for $x in $ys\nfoo\n", "sample.tmpl", [WEEWX])
    assert [d.code for d in found] == ["CT4001"]
    assert found[0].line


def test_an_unknown_root_is_reported_on_request():
    found = unresolved("$foreign.x", [WEEWX])
    assert [d.code for d in found] == ["CT4110"]
    assert found[0].severity == diagnostics.WARNING


# -- JSON mode is declared, not guessed ------------------------------

def test_the_declaration_decides():
    assert is_json_template("#mode json\n{}")
    assert is_json_template("## comment\n#mode json\n{}")


def test_the_extension_does_not_decide():
    # weewx skins ship .json.tmpl files, and those are text templates.
    assert not is_json_template('{"a": $x}')


def test_a_json_template_is_checked_as_a_document(tmp_path):
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(
        {"type": "object", "required": ["missing"],
         "properties": {"missing": {"type": "string"}}}), encoding="utf-8")
    source = '#mode json\n#schema "s.json"\n{"there": 1}'
    found = check_source(source, "x.tmpl", [], base_dir=tmp_path)
    assert [d.code for d in found] == ["CT4200"]


def test_a_missing_schema_is_a_finding(tmp_path):
    source = '#mode json\n#schema "nosuchfile.json"\n{}'
    found = check_source(source, "x.tmpl", [], base_dir=tmp_path)
    assert [d.code for d in found] == ["CT4004"]


# -- Output formats --------------------------------------------------

FINDING = diagnostics.Diagnostic(
    "CT4103", diagnostics.ERROR, "knows no such field", file="a.tmpl",
    line=3, column=7, path="$day.x", suggestions=("max",))


def test_the_text_form_names_place_and_suggestion():
    text = diagnostics.render([FINDING], "text")
    assert "a.tmpl:3:7" in text
    assert "max" in text


def test_the_json_form_is_json():
    data = json.loads(diagnostics.render([FINDING], "json"))
    assert data[0]["code"] == "CT4103"
    assert data[0]["suggestions"] == ["max"]


def test_the_sarif_form_carries_rule_and_place():
    data = json.loads(diagnostics.render([FINDING], "sarif"))
    result = data["runs"][0]["results"][0]
    assert result["ruleId"] == "CT4103"
    assert result["level"] == "error"
    region = result["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] == 3


def test_an_unknown_form_is_rejected():
    with pytest.raises(ValueError):
        diagnostics.render([], "xml")


def test_the_worst_severity_wins():
    warning = diagnostics.Diagnostic("X", diagnostics.WARNING, "")
    assert diagnostics.worst([warning, FINDING]) == diagnostics.ERROR
    assert diagnostics.worst([warning]) == diagnostics.WARNING
    assert diagnostics.worst([]) == diagnostics.NOTE


# -- Reference -------------------------------------------------------

def test_the_reference_comes_from_the_compiler_tables():
    data = reference.reference()
    names = {entry["name"] for entry in data["directives"]}
    assert {"for", "if", "def", "compiler-settings"} <= names
    assert len(data["settings"]) > 40
    assert json.dumps(data)


def test_the_reference_says_what_must_be_closed():
    closeable = {entry["name"] for entry in reference.directives()
                 if entry["closeable"]}
    assert "for" in closeable
    assert "set" not in closeable


# -- Plugin registry -------------------------------------------------

class FakeEntry:
    """An entry point, the way importlib.metadata hands it over."""

    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


class OnlyDeclares:
    @staticmethod
    def declare():
        return Declaration(name="sample2", roots={"x": Node(open=True)})


class OnlyAdapts:
    installed = False

    @classmethod
    def install(cls):
        cls.installed = True


def test_a_plugin_declares_its_names():
    from ct4 import registry

    plugins = registry.discover(lambda: [FakeEntry("a", OnlyDeclares)])
    declared = registry.declarations(plugins)
    assert [d.name for d in declared] == ["sample2"]


def test_a_plugin_without_a_declaration_is_complete():
    # A plugin that only registers types is not half a plugin.
    from ct4 import registry

    OnlyAdapts.installed = False
    plugins = registry.discover(lambda: [FakeEntry("b", OnlyAdapts)])
    assert registry.declarations(plugins) == []
    assert registry.install_all(plugins) == ["b"]
    assert OnlyAdapts.installed


def test_a_broken_plugin_does_not_cripple_the_run():
    # A third-party package that fails to load must not make ct4 check
    # unusable.
    from ct4 import registry

    plugins = registry.discover(lambda: [
        FakeEntry("broken", ImportError("missing")),
        FakeEntry("good", OnlyDeclares)])
    assert [p.name for p in plugins] == ["good"]
