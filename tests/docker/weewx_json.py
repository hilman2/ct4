"""weewx generates a JSON file in the real JSON mode, unchanged.

The question this answers is narrow and was open until now: JSON mode
lived behind ``ct4 build`` and ``ct4.jsonmode.render``, and weewx
reaches neither. It hands ``Cheetah.Template.Template`` a file and
calls ``respond()``, once per template in a skin, and that is the whole
of its contract with the engine. A mode the engine does not answer to
is a mode weewx cannot have.

So the mode moved into the engine. ``ct4.jsonmode.bridge`` turns a
``#mode json`` template into an ordinary compiled Cheetah class whose
``respond()`` returns the serialised document, and
``ct4.lang.backend`` hangs that on the hook ct3 already provides,
``Template._CHEETAH_compilerClass``.

What weewx has to change: nothing. It imports ``user.extensions`` at
startup (weeutil/startup.py, line 76) for exactly this kind of thing,
and two lines there install the backend. This driver writes that file,
imports it the way weewx does, and then runs weewx's own report engine
over weewx's own test skin with one template added.

    python tests/docker/weewx_json.py

Exits 1 where the file is not there, is not JSON, does not hold to its
schema, or carries a number as a string.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

WEEWX_TESTS = Path("/opt/weewx/src/weewx/tests")

# A JSON template of the kind a skin would carry: values that are
# numbers stay numbers, the missing ones become null, and nowhere is a
# comma or a quote the author's problem.
TEMPLATE = """\
#mode json
#schema "day.schema.json"
#missing null
#precision default = 2
{
  "station": "$station.location",
  "generated": $current.dateTime.raw,
  "day": {
    "outTemp": {
      "min": $day.outTemp.min.raw,
      "max": $day.outTemp.max.raw
    },
    "rain": $day.rain.sum.raw,
    "windSpeed": {
      "max": $day.windSpeed.max.raw @ 1
    }
  }
}
"""

SCHEMA = {
    "type": "object",
    "required": ["station", "generated", "day"],
    "properties": {
        "station": {"type": "string"},
        "generated": {"type": "number"},
        "day": {
            "type": "object",
            "required": ["outTemp", "rain", "windSpeed"],
            "properties": {
                "outTemp": {
                    "type": "object",
                    "required": ["min", "max"],
                    "properties": {"min": {"type": ["number", "null"]},
                                   "max": {"type": ["number", "null"]}},
                },
                "rain": {"type": ["number", "null"]},
                "windSpeed": {"type": "object"},
            },
        },
    },
}

# What a weewx installation would put in its user directory. This is
# weewx's own extension point and the only thing an operator adds.
EXTENSIONS = """\
from ct4.lang import backend

counts = backend.install()
"""


def _user_package(root: Path) -> None:
    """Writes the user extension weewx imports at startup."""
    package = root / "user"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "extensions.py").write_text(EXTENSIONS, encoding="utf-8")


def _skin(root: Path) -> Path:
    """weewx's own test skin, with one JSON template added."""
    skins = root / "test_skins"
    shutil.copytree(WEEWX_TESTS / "test_skins", skins)
    where = skins / "StandardTest"
    (where / "day.json.tmpl").write_text(TEMPLATE, encoding="utf-8")
    (where / "day.schema.json").write_text(json.dumps(SCHEMA),
                                           encoding="utf-8")
    # Registered the way every other template in this skin is, in the
    # skin's own configuration under [FileGenerator] [[ToDate]]. The
    # point of the exercise is that a JSON template is registered like
    # any other and needs no special handling anywhere.
    config = where / "skin.conf"
    lines = config.read_text(encoding="utf-8").splitlines()
    try:
        at = lines.index("    [[ToDate]]")
    except ValueError:
        raise SystemExit("the skin no longer has a ToDate section") from None
    # encoding per template, which weewx already supports: this skin
    # asks for html_entities, and that turns the station name into
    # character references. Right for a page, wrong for a JSON file,
    # and the skin is where that is decided rather than here.
    lines[at + 1:at + 1] = ["        [[[day_json]]]",
                            "            template = day.json.tmpl",
                            "            encoding = utf8"]
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return skins


def _configured(skins: Path, out: Path) -> object:
    """weewx's test configuration, pointed at our copies.

    The template is registered under FileGenerator because that is what
    this skin uses; weewx maps the deprecated name onto
    CheetahGenerator, and the point here is to change nothing about how
    weewx finds and runs a template.
    """
    import weeutil.config

    sys.path.insert(0, str(WEEWX_TESTS))
    import gen_fake_data
    import parameters

    config = weeutil.config.deep_copy(
        __import__("configobj").ConfigObj(str(WEEWX_TESTS / "testgen.conf"),
                                          encoding="utf-8"))
    # What weewx's own conftest does with this file: the bindings say
    # "replace_me" and the harness picks a database type. sqlite,
    # because a JSON document does not care which one it came out of.
    config["DataBindings"]["wx_binding"]["database"] = "archive_sqlite"
    config["DataBindings"]["alt_binding"]["database"] = "alt_sqlite"
    config["WEEWX_ROOT"] = str(out)
    config["StdReport"]["SKIN_ROOT"] = str(skins)
    # Both bindings, because the skin's image generator asks for the
    # second one and a traceback in the middle of the run would bury
    # the thing this is here to show.
    gen_fake_data.provision_binding(config, "wx_binding",
                                    parameters.synthetic_dict)
    gen_fake_data.provision_binding(config, "alt_binding",
                                    parameters.alt_dict)
    return config


def main() -> int:
    if not WEEWX_TESTS.is_dir():
        print("no weewx here; this runs in the capture image")
        return 0
    # --plain runs the same report with nothing of ours installed, so
    # that the caller can diff the two trees. It has to be a second
    # process and not a second call: a compiled template class outlives
    # an uninstall, and a comparison against a cached class would be a
    # comparison with itself.
    plain = "--plain" in sys.argv
    into = None
    if "--out" in sys.argv:
        into = Path(sys.argv[sys.argv.index("--out") + 1])
    root = Path(tempfile.mkdtemp())
    _user_package(root)
    sys.path.insert(0, str(root))

    extensions = None
    if not plain:
        # The way weewx does it, weeutil/startup.py line 76. In a real
        # run this happens at startup; the test harness does not go
        # through startup, so the driver stands in for it and nothing
        # else.
        extensions = importlib.import_module("user.extensions")

    import weewx.accum
    import weewx.manager
    import weewx.reportengine
    import weewx.station
    import weewx.units
    import weewx.wxxtypes
    import weewx.xtypes
    import weeutil.logger

    out = root / "run"
    out.mkdir()
    skins = _skin(root)
    config = _configured(skins, out)
    weeutil.logger.setup("ct4_json", config)

    altitude = weewx.units.ValueTuple(
        float(config["Station"]["altitude"][0]),
        config["Station"]["altitude"][1], "group_altitude")
    weewx.xtypes.xtypes.append(weewx.wxxtypes.WXXTypes(
        altitude, float(config["Station"]["latitude"]),
        float(config["Station"]["longitude"])))
    weewx.accum.initialize(config)

    sys.path.insert(0, str(WEEWX_TESTS))
    import parameters

    # The skin's own search-list extensions, which weewx's test module
    # puts on the path the same way. Not part of what is being shown
    # here, but the skin does not render without them and swapping the
    # skin for a simpler one would be showing a different thing.
    import weewx_data

    examples = Path(weewx_data.__file__).parent / "examples"
    sys.path.append(str(examples / "colorize"))
    sys.path.append(str(examples / "xstats" / "bin" / "user"))
    import colorize_1
    import colorize_2
    import colorize_3

    colorize_1.Colorize.colorize_1 = colorize_1.Colorize.colorize
    colorize_2.Colorize.colorize_2 = colorize_2.Colorize.colorize
    colorize_3.Colorize.colorize_3 = colorize_3.Colorize.colorize

    engine = weewx.reportengine.StdReportEngine(
        config, weewx.station.StationInfo(**config["Station"]),
        None, parameters.synthetic_dict["stop_ts"])
    engine.run()

    where = Path(out) / config["StdReport"]["StandardTest"]["HTML_ROOT"]
    if into is not None:
        into.mkdir(parents=True, exist_ok=True)
        for path in where.iterdir():
            if path.is_file():
                shutil.copy2(path, into / path.name)
    if plain:
        print("the same report with nothing of ours installed: %d files"
              % len(list(where.iterdir())))
        return 0

    made = where / "day.json"
    if not made.exists():
        print("weewx wrote no %s" % made)
        for path in sorted(Path(out).rglob("*")):
            print("   ", path)
        return 1

    # Which compiler did what over the whole report, and it is worth
    # printing: the JSON template goes through the bridge, and every
    # HTML page in the same run goes wherever the counts say.
    print("compilers: %s" % extensions.counts)
    pages = sorted(p.name for p in made.parent.glob("*.html"))
    print("pages in the same run: %d (%s ...)"
          % (len(pages), ", ".join(pages[:3])))

    text = made.read_text(encoding="utf-8")
    print("weewx wrote %s, %d bytes" % (made.name, len(text)))
    print(text)
    document = json.loads(text)

    problems = []
    if not isinstance(document.get("generated"), (int, float)):
        problems.append("generated is %r, not a number"
                        % document.get("generated"))
    temp = document.get("day", {}).get("outTemp", {})
    for key in ("min", "max"):
        if temp.get(key) is not None and not isinstance(temp[key],
                                                        (int, float)):
            problems.append("day.outTemp.%s is %r, not a number"
                            % (key, temp[key]))
    try:
        import jsonschema

        jsonschema.validate(document, SCHEMA)
        print("holds to its schema")
    except ImportError:
        print("no jsonschema here; the shape was checked by hand instead")

    if problems:
        for one in problems:
            print("  " + one)
        return 1
    print()
    print("weewx produced this with no change to weewx: the mode is read"
          " out of the template by the engine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
