"""Record contexts from a running weewx.

Meant as a pytest plugin. In ``src/weewx/tests`` weewx brings along
everything a real report run needs: generated measurements, a database,
skins and the report engine. Instead of rebuilding that, this module
hooks into the run:

    pytest src/weewx/tests/test_templates.py -p ct4.fixture.weewx_capture

The hook goes into weewx only, never into Cheetah. Substituting
``Cheetah.Template.Template`` does not work: ``Template.__init__`` looks
up its own name in the module, and a replacement leads into an endless
loop.

What comes out is one file per generated page, holding the template, the
recorded context and the output weewx wrote along the way. Those three
pieces make a corpus case that runs without weewx.
"""

from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
from typing import Any

from ct4.fixture.record import Recorder

# Where the recordings go. An environment variable, because the plugin
# is loaded by pytest and has no command line of its own.
OUT_ENV = "CT4_FIXTURE_DIR"

# When set, a JSON template is additionally rendered against the same
# context. That is the acid test for JSON mode: real weewx objects, not
# a recording.
JSON_TEMPLATE_ENV = "CT4_JSON_TEMPLATE"
JSON_OUT_ENV = "CT4_JSON_OUT"

# What accumulates during the run. One entry per generated page, with
# template, output path and recorded context. The output itself is only
# read at the end: during the run it is not on disk yet.
_recorded: list[dict[str, Any]] = []
_rendered: list[bool] = []


def pytest_configure(config: Any) -> None:
    install()


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    written = write_all(Path(os.environ.get(OUT_ENV, "fixtures")))
    print("\nct4: %d recordings written" % written)


def install() -> None:
    """Hooks the recorder into weewx' Cheetah generator."""
    import weewx.cheetahgenerator
    from weewx.cheetahgenerator import CheetahGenerator

    # weewx catches every error while compiling and evaluating a
    # template, reports it to the log and carries on. In the container
    # the log goes to syslog, which does not exist, and a run without a
    # single generated page would look successful. This logger
    # therefore gets an outlet to stderr.
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("weewx: %(levelname)s %(message)s"))
    weewx.cheetahgenerator.log.addHandler(handler)
    weewx.cheetahgenerator.log.setLevel(logging.ERROR)

    original_prep = CheetahGenerator._prepGen
    original_list = CheetahGenerator._getSearchList

    def _prepGen(self: Any, report_dict: Any) -> tuple[Any, ...]:
        template, destination, encoding, binding = original_prep(
            self, report_dict)
        # Both of these are needed by _getSearchList, which does not
        # get them passed. The generator is the only place where they
        # can survive between the two calls.
        self._ct4_template = template
        self._ct4_destination = destination
        # The output is written as UTF-8, whatever the skin intends.
        # weewx encodes the finished string only when writing:
        # 'html_entities' turns the degree sign into &#176;,
        # 'strict_ascii' throws accents away. That is a property of the
        # writing, not of the template. A fixture that took it along
        # would require the test rig to rebuild weewx' encoder.
        return template, destination, "utf8", binding

    def _getSearchList(self: Any, encoding: Any, timespan: Any,
                       default_binding: Any, section_name: Any,
                       file_name: str) -> list[Any]:
        search_list = original_list(self, encoding, timespan,
                                    default_binding, section_name, file_name)
        trees: list[dict[str, Any]] = []
        wrapped = []
        for namespace in search_list:
            tree: dict[str, Any] = {}
            trees.append(tree)
            wrapped.append(Recorder(namespace, tree))
        _render_json(search_list)
        _recorded.append({
            "template_path": self._ct4_template,
            "output_path": os.path.join(self._ct4_destination,
                                        os.path.basename(file_name)),
            "context": trees,
        })
        return wrapped

    CheetahGenerator._prepGen = _prepGen
    CheetahGenerator._getSearchList = _getSearchList


def _render_json(search_list: list[Any]) -> None:
    """Renders the JSON template, if one is asked for.

    The first searchList of the run is taken. It belongs to some
    arbitrary page; for the tags the template uses that makes no
    difference.
    """
    source = os.environ.get(JSON_TEMPLATE_ENV)
    if not source or _rendered:
        return
    from ct4.jsonmode import compile_template
    from ct4.plugins import weewx_adapter

    weewx_adapter.install()
    path = Path(source)
    compiled = compile_template(path.read_text(encoding="utf-8"),
                                base_dir=path.parent)
    for finding in compiled.check():
        print("ct4 schema: %s" % finding)
    text = compiled.render(search_list, indent=1, validate=True)
    out = Path(os.environ.get(JSON_OUT_ENV, "day.json"))
    out.write_text(text, encoding="utf-8", newline="\n")

    # The same template once more, this time streaming. Both ways have
    # to yield the same bytes; the second way rests on that, and here
    # the assertion runs against real data instead of test objects.
    buffer = io.StringIO()
    compiled.stream(buffer, search_list)
    collected = compiled.render(search_list)
    if buffer.getvalue() != collected:
        raise AssertionError(
            "streaming and collecting yield different bytes")
    print("ct4: streaming and collecting agree (%d bytes)" % len(collected))
    _rendered.append(True)


def write_all(out_dir: Path) -> int:
    """Stores the run's recordings and returns how many there were.

    A recording without an output file is dropped. That happens when
    weewx could not compile the template; then the run has a different
    problem, and a fixture without expected output would be nothing but
    ballast.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for entry in _recorded:
        output = Path(entry["output_path"])
        template = Path(entry["template_path"])
        if not output.exists() or not template.exists():
            continue
        record = {
            "template_path": str(template),
            "output_path": str(output),
            "template": template.read_text(encoding="utf-8"),
            "expected": output.read_text(encoding="utf-8"),
            "context": entry["context"],
            "filter": "weewx.AssureUnicode",
        }
        name = "_".join(template.parts[-3:]).replace(".", "_")
        target = out_dir / (name + ".json")
        target.write_text(
            json.dumps(record, ensure_ascii=False, indent=1),
            encoding="utf-8")
        written += 1
    return written
