"""JSON mode through Cheetah's own entry point.

An application that only knows Cheetah hands ``Template`` a file or a
string and calls ``respond()``. That is weewx's whole contract with the
engine, and a mode reachable only through ``ct4 build`` would be a mode
weewx cannot have. These hold the bridge to that shape: the class that
comes back is an ordinary compiled template as far as its caller can
tell, and the string it returns is the serialised document.

tests/docker/weewx_json.py does the same thing with a real weewx report
engine. This file is what runs on every push.
"""

from __future__ import annotations

import json

import pytest

from Cheetah.Template import Template
from ct4.lang import backend

SOURCE = """\
#mode json
#precision default = 2
{
  "station": "$station",
  "outTemp": $temp,
  "rain": $rain
}
"""

CONTEXT = {"station": "Zuhause", "temp": 21.456, "rain": 0.0}


@pytest.fixture(autouse=True)
def hooked():
    counts = backend.install()
    yield counts
    backend.uninstall()


def rendered(**kwargs) -> str:
    klass = Template.compile(useCache=False, cacheCompilationResults=False,
                             **kwargs)
    return str(klass(searchList=[dict(CONTEXT)]).respond())


def test_a_json_template_compiled_from_a_string(hooked):
    document = json.loads(rendered(source=SOURCE))
    assert document == {"station": "Zuhause", "outTemp": 21.46, "rain": 0.0}
    assert hooked.json == 1


def test_a_number_stays_a_number(hooked):
    # The whole point against a text template that writes JSON by hand:
    # there the value goes through str() and comes out quoted or not
    # depending on how the author wrote the line.
    document = json.loads(rendered(source=SOURCE))
    assert isinstance(document["outTemp"], float)
    assert isinstance(document["rain"], float)


def test_the_file_form_is_what_weewx_uses(tmp_path, hooked):
    # weewx passes file=, never source=. The bridge has to read the
    # declaration off the file itself, because the generator never sees
    # a template that came in that way.
    path = tmp_path / "day.json.tmpl"
    path.write_text(SOURCE, encoding="utf-8")
    template = Template(file=str(path), searchList=[dict(CONTEXT)])
    assert json.loads(str(template.respond()))["outTemp"] == 21.46
    assert hooked.json == 1


def test_a_schema_is_found_next_to_the_template(tmp_path, hooked):
    # A #schema path counts from the template's own directory, which
    # for the file form is the only sensible base: a report engine's
    # working directory is not the skin.
    (tmp_path / "day.schema.json").write_text(
        json.dumps({"type": "object", "required": ["station"]}),
        encoding="utf-8")
    path = tmp_path / "day.json.tmpl"
    path.write_text('#mode json\n#schema "day.schema.json"\n'
                    '{"station": "$station"}\n', encoding="utf-8")
    template = Template(file=str(path), searchList=[dict(CONTEXT)])
    assert json.loads(str(template.respond())) == {"station": "Zuhause"}


def test_a_text_template_is_untouched(hooked):
    # The mode is read out of the template and nothing else decides it,
    # so a template that does not declare it takes the path it always
    # took.
    assert rendered(source="hello $station\n") == "hello Zuhause\n"
    assert hooked.json == 0


def test_a_json_template_without_the_backend_is_text():
    # Nothing installs the bridge by default, and then a JSON template
    # renders as the text it looks like, which is what ct3 does today.
    # Said out loud because it is the difference between a skin that
    # works and one that ships its own source.
    backend.uninstall()
    out = rendered(source=SOURCE)
    assert out.startswith("#mode json")


def test_the_class_carries_what_an_application_reads_off_it(hooked):
    klass = Template.compile(source=SOURCE, useCache=False,
                             cacheCompilationResults=False)
    assert klass._CHEETAH__instanceInitialized is False
    assert klass._CHEETAH_versionTuple
    assert getattr(klass, "_mainCheetahMethod_for_" + klass.__name__) \
        == "respond"


def test_two_renders_of_one_class_give_the_same_bytes(hooked):
    klass = Template.compile(source=SOURCE, useCache=False,
                             cacheCompilationResults=False)
    first = str(klass(searchList=[dict(CONTEXT)]).respond())
    second = str(klass(searchList=[dict(CONTEXT)]).respond())
    assert first == second
