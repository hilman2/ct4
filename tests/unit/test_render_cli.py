"""ct4 render: one template, one context, no application.

The promise from the plan's section 9: skin development without a
running weather station, in milliseconds. The context comes from a
recording or from a plain JSON object, and the mode from the template.
"""

from __future__ import annotations

import json

from Cheetah.Template import Template

from ct4 import cli
from ct4.fixture.filters import WeewxAssureUnicode
from ct4.fixture.record import Recorder


class Reading:
    def __init__(self, value, fmt="%.1f"):
        self.raw = value
        self._fmt = fmt

    def __str__(self):
        return "N/A" if self.raw is None else self._fmt % self.raw


class Day:
    def __init__(self):
        self.outTemp = type("Agg", (), {})()
        self.outTemp.max = Reading(21.46)
        self.outTemp.min = Reading(9.0)


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def render(tmp_path, source, *contexts, name="page.tmpl"):
    template = write(tmp_path / name, source)
    out = tmp_path / "out.txt"
    if out.exists():
        out.unlink()
    argv = ["render", str(template), "--out", str(out)]
    for number, document in enumerate(contexts):
        argv += ["--context", str(write(tmp_path / ("c%d.json" % number),
                                        json.dumps(document)))]
    code = cli.main(argv)
    return code, (out.read_text(encoding="utf-8") if out.exists() else None)


def test_a_plain_object_is_the_context(tmp_path):
    code, text = render(tmp_path, "Hello $name\n", {"name": "World"})
    assert (code, text) == (0, "Hello World\n")


def test_a_recording_replays_what_the_page_read(tmp_path):
    # The same template, once against live objects with a recorder in
    # between, once through ct4 render against the recording.
    source = "High $day.outTemp.max, low $day.outTemp.min\n"
    tree: dict = {}
    live = Template.compile(source=source, useCache=False,
                            cacheCompilationResults=False)(
        searchList=[Recorder({"day": Day()}, tree)])
    expected = str(live.respond())
    recording = {"template": source, "context": [tree],
                 "expected": expected}
    code, text = render(tmp_path, source, recording)
    assert (code, text) == (0, expected)
    assert text == "High 21.5, low 9.0\n"


def test_the_recorded_filter_is_applied(tmp_path):
    # weewx' filter turns an AttributeError during conversion into the
    # placeholder's own text, where Cheetah's default filter lets it
    # through. A recording carries the failure, and which of the two
    # happens on replay is the filter's decision, so the recording
    # names the filter.
    class Broken:
        def __str__(self):
            raise AttributeError("no such binding")

    tree: dict = {}
    source = "[$day.outTemp.max]\n"
    day = Day()
    day.outTemp.max = Broken()
    Template.compile(source=source, useCache=False,
                     cacheCompilationResults=False)(
        searchList=[Recorder({"day": day}, tree)],
        filter=WeewxAssureUnicode).respond()
    recorded = json.loads(json.dumps(tree))
    recording = {"template": source, "context": [recorded],
                 "filter": "weewx.AssureUnicode"}
    code, text = render(tmp_path, source, recording)
    assert (code, text) == (0, "[$day.outTemp.max]\n")

    del recording["filter"]
    code, text = render(tmp_path, source, recording)
    assert (code, text) == (1, None)


def test_several_contexts_are_searched_in_order(tmp_path):
    code, text = render(tmp_path, "$a $b\n", {"a": 1}, {"a": 2, "b": 3})
    assert (code, text) == (0, "1 3\n")


def test_a_list_is_the_search_list_itself(tmp_path):
    code, text = render(tmp_path, "$a $b\n", [{"a": 1}, {"a": 2, "b": 3}])
    assert (code, text) == (0, "1 3\n")


def test_the_mode_comes_from_the_template(tmp_path):
    code, text = render(tmp_path, '#mode json\n{"high": $day.outTemp.max}\n',
                        {"day": {"outTemp": {"max": 21.46}}},
                        name="day.json.tmpl")
    assert code == 0
    assert json.loads(text) == {"high": 21.46}

    code, text = render(tmp_path, "#mode markup\n<p>$x</p>\n",
                        {"x": "<b>"}, name="page.html.tmpl")
    assert (code, text) == (0, "<p>&lt;b&gt;</p>\n")


def test_stdout_without_out(tmp_path, capsysbinary):
    template = write(tmp_path / "page.tmpl", "x\n")
    assert cli.main(["render", str(template)]) == 0
    assert capsysbinary.readouterr().out == b"x\n"


def test_a_render_error_names_the_line(tmp_path, capsys):
    code, text = render(tmp_path, "one\n$missing\n", {})
    assert (code, text) == (1, None)
    err = capsys.readouterr().err
    assert "ct4 render: NotFound: cannot find 'missing'" in err
    assert "line 2, column 1" in err


def test_capture_needs_a_weewx_tree(tmp_path, capsys):
    assert cli.main(["fixture", "capture", "--weewx", str(tmp_path)]) == 2
    assert "no weewx test suite" in capsys.readouterr().err


def test_capture_runs_weewx_tests_with_the_plugin(tmp_path, monkeypatch):
    tests = tmp_path / "src" / "weewx" / "tests"
    tests.mkdir(parents=True)
    write(tests / "test_templates.py", "")
    seen = {}

    def call(argv, cwd, env):
        seen.update(argv=argv, cwd=cwd, env=env)
        return 0

    monkeypatch.setattr("subprocess.call", call)
    out = tmp_path / "recordings"
    assert cli.main(["fixture", "capture", "--weewx", str(tmp_path),
                     "--out", str(out)]) == 0
    assert seen["argv"][-2:] == ["-p", "ct4.fixture.weewx_capture"]
    assert seen["argv"][3] == str(tests / "test_templates.py")
    assert seen["cwd"] == str(tmp_path)
    assert seen["env"]["CT4_FIXTURE_DIR"] == str(out.resolve())
