"""How long a render takes, under whichever Cheetah is importable.

Run once against ct3 and once against the fork and put the two next to
each other. The runner in the container does exactly that:

    docker compose -f tests/docker/compose.yml run --rm tests bench

Nothing here imports ct4, so the script runs under ct3 as well. The
JSON mode is the exception and is skipped where it does not exist,
because ct3 has none. Its counterpart is the text template above it:
that is how a skin produces JSON today.

The numbers are the best of five runs, not the mean. A mean over a
machine that is doing other things measures the other things.
"""

from __future__ import annotations

import json
import sys
import time

COUNT = 500
REPEATS = 5
CALLS = 200


class Point:
    """Stands in for a weewx archive record."""

    __slots__ = ("start", "value")

    def __init__(self, start: int, value: float):
        self.start = start
        self.value = value


class Reading:
    """Stands in for a weewx ValueHelper.

    Its point is the __getattr__: a skin writes $day.outTemp.max, and
    every part of that path runs Python. A template engine cannot make
    that cheaper, and a benchmark that leaves it out measures the
    engine on data no skin has.
    """

    def __init__(self, value: float):
        self._value = value

    def __getattr__(self, name: str) -> object:
        if name == "raw":
            return self._value
        if name == "formatted":
            return "%.1f" % self._value
        raise AttributeError(name)

    def __str__(self) -> str:
        return "%.1f C" % self._value


POINTS = [Point(1700000000 + i * 300, i * 0.5) for i in range(COUNT)]
READINGS = [Reading(i * 0.5) for i in range(COUNT)]
CONTEXT = [{"station": "Zuhause", "points": POINTS, "readings": READINGS}]

TABLE = """<table>
#for $p in $points
<tr><td>$p.start</td><td>$p.value</td></tr>
#end for
</table>"""

# The same, but reading through an object that runs Python for every
# part of the path. This is the shape of a real skin.
HELPERS = """<table>
#for $r in $readings
<tr><td>$r.formatted</td><td>$r.raw</td></tr>
#end for
</table>"""

# How a skin writes JSON today: text mode, commas placed by hand.
JSON_BY_HAND = """{"station": "$station", "series": [
#set $first = True
#for $p in $points
#if not $first
,
#end if
#set $first = False
[$p.start, $p.value]
#end for
]}
"""

JSON_MODE_SERIES = ('#mode json\n'
                    '{"station": "$station",\n'
                    ' "series": #series($points, layout="pairs",'
                    ' fields=["start", "value"])}\n')

JSON_MODE_LOOP = ('#mode json\n'
                  '{"station": "$station",\n'
                  ' "series": [#for $p in $points\n'
                  '[$p.start, $p.value]\n'
                  '#end for]}\n')


def best(fn, calls: int = CALLS) -> float:
    """Milliseconds per call, best of REPEATS."""
    fn()
    found = None
    for _ in range(REPEATS):
        start = time.perf_counter()
        for _ in range(calls):
            fn()
        each = (time.perf_counter() - start) / calls * 1000
        found = each if found is None else min(found, each)
    return found


def text_case(source):
    """Compiles a text template and returns a function that renders it."""
    from Cheetah.Template import Template

    klass = Template.compile(source=source, useCache=False,
                             cacheCompilationResults=False)

    def render():
        return str(klass(searchList=CONTEXT).respond())

    return render


def compile_case(source):
    from Cheetah.Template import Template

    def once():
        return Template.compile(source=source, useCache=False,
                                cacheCompilationResults=False)

    return once


def json_case(source, stream=False):
    """Returns a renderer for the JSON mode, or None under ct3."""
    try:
        from ct4.jsonmode import compile_template
    except ImportError:
        return None
    compiled = compile_template(source)
    if not stream:
        return lambda: compiled.render(CONTEXT)

    import io

    def run():
        buffer = io.StringIO()
        compiled.stream(buffer, CONTEXT)
        return buffer.getvalue()

    return run


def by_hand():
    return json.dumps(
        {"station": "Zuhause",
         "series": [[p.start, p.value] for p in POINTS]},
        ensure_ascii=False, separators=(",", ": "))


def cases():
    """Name to callable, in the order they should be printed."""
    return [
        ("text: plain objects", text_case(TABLE)),
        ("text: helper objects", text_case(HELPERS)),
        ("text: JSON by hand", text_case(JSON_BY_HAND)),
        ("compile: plain objects", compile_case(TABLE), 20),
        ("json mode: #series", json_case(JSON_MODE_SERIES)),
        ("json mode: #for", json_case(JSON_MODE_LOOP)),
        ("json mode: streaming", json_case(JSON_MODE_SERIES, stream=True)),
        ("reference: json.dumps by hand", by_hand),
    ]


def version() -> str:
    import Cheetah

    return "%s  %s" % (Cheetah.Version, Cheetah.__file__)


def main() -> int:
    """Prints one line per case, as JSON when asked.

    The JSON form is what the comparison between two runs reads; the
    plain form is for looking at.
    """
    as_json = "--json" in sys.argv
    found = {}
    for case in cases():
        name, fn = case[0], case[1]
        calls = case[2] if len(case) > 2 else CALLS
        if fn is None:
            continue
        found[name] = best(fn, calls)
    if as_json:
        print(json.dumps({"version": version(), "cases": found}))
        return 0
    print(version())
    for name, ms in found.items():
        print("  %-32s %7.3f ms" % (name, ms))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
