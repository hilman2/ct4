"""ct4 migrate: the parentheses a recording shows to be missing."""

from __future__ import annotations

import json

import pytest
from Cheetah.Template import Template

from ct4 import cli, migrate
from ct4.fixture.record import Recorder


class Station:
    def __init__(self):
        self.altitude = 42

    def location(self):
        return "Hamburg"

    def details(self):
        return Detail()


class Detail:
    def name(self):
        return "north"


def recorded(source, namespace):
    """Renders in text mode with a recorder in between; returns the trees."""
    tree: dict = {}
    Template.compile(source=source, useCache=False,
                     cacheCompilationResults=False)(
        searchList=[Recorder(namespace, tree)]).respond()
    return [json.loads(json.dumps(tree))]


def context():
    return {"station": Station(), "f": lambda: "called",
            "g": lambda: "explicit", "rows": [Station()], "flag": lambda: 1}


def test_the_parentheses_go_where_the_name_mapper_called():
    source = ("$station.location $station.altitude $f $g()\n"
              "#if $flag\nyes\n#end if\n")
    result = migrate.migrate(source, recorded(source, context()))
    assert result.source == (
        "#mode strict\n"
        "$station.location() $station.altitude $f() $g()\n"
        "#if $flag()\nyes\n#end if\n")
    assert [(c.before, c.after) for c in result.changes] == [
        ("$station.location", "$station.location()"),
        ("$f", "$f()"),
        ("$flag", "$flag()")]
    assert result.same is True


def test_a_call_in_the_middle_of_a_chain():
    source = "$station.details.name\n"
    result = migrate.migrate(source, recorded(source, context()))
    assert result.source == "#mode strict\n$station.details().name()\n"
    assert result.same is True


def test_what_the_recording_cannot_follow_is_left_and_named():
    source = "#for $r in $rows\n$r.location\n#end for\n${f}\n"
    result = migrate.migrate(source, recorded(source, context()))
    # The loop variable is not in the recording, so nothing is known
    # about it, and the enclosure is left alone by rule. Both show up
    # as a difference when verified, which is the point of verifying.
    assert "$r.location\n" in result.source
    assert [(s.text, s.reason) for s in result.skipped] == [
        ("${f}", "an enclosure")]
    assert result.same is False
    assert "location" in "".join(result.diff)


def test_without_a_recording_only_the_mode_line_is_added():
    result = migrate.migrate("## licence\n$f\n")
    assert result.source == "## licence\n#mode strict\n$f\n"
    assert result.changes == [] and result.diff is None


def test_a_declared_mode_gets_strict_added_and_strict_stays():
    assert migrate.migrate("#mode markup\n<p>$x</p>\n").source == \
        "#mode markup strict\n<p>$x</p>\n"
    assert migrate.migrate("#mode strict\n$x\n").source == "#mode strict\n$x\n"
    with pytest.raises(migrate.MigrationError):
        migrate.migrate("#mode json\n{}\n")


def test_crlf_stays_crlf():
    assert migrate.migrate("a\r\n$f\r\n").source == \
        "#mode strict\r\na\r\n$f\r\n"


def test_the_command_reports_and_writes(tmp_path, capsys):
    source = "$station.location\n"
    path = tmp_path / "page.tmpl"
    path.write_text(source, encoding="utf-8")
    recording = tmp_path / "rec.json"
    recording.write_text(json.dumps({"template": source,
                                     "context": recorded(source, context())}),
                         encoding="utf-8")
    assert cli.main(["migrate", str(path), "--context", str(recording)]) == 0
    out = capsys.readouterr().out
    assert "$station.location -> $station.location()" in out
    assert "renders the same" in out
    assert path.read_text(encoding="utf-8") == source
    assert cli.main(["migrate", str(path), "--context", str(recording),
                     "--write"]) == 0
    assert path.read_text(encoding="utf-8") == \
        "#mode strict\n$station.location()\n"


def test_the_command_exits_one_where_the_page_differs(tmp_path, capsys):
    source = "#for $r in $rows\n$r.location\n#end for\n"
    path = tmp_path / "page.tmpl"
    path.write_text(source, encoding="utf-8")
    recording = tmp_path / "rec.json"
    recording.write_text(json.dumps(recorded(source, context())),
                         encoding="utf-8")
    assert cli.main(["migrate", str(path), "--context", str(recording)]) == 1
    assert "differs in strict mode" in capsys.readouterr().out
