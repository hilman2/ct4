"""What a large series costs in time and in memory.

A year of weewx archive records at five-minute intervals is around
105,000 rows. Ten values each is an ordinary plot page. That is a
million numbers, and at that size the question is no longer which way
is faster but which ways finish at all on a Raspberry Pi.

The source is a generator in every case, so what is measured is what
the engine holds, not what the caller handed it. In weewx the source
really is a cursor.

    docker compose -f tests/docker/compose.yml run --rm tests large

Runs under ct3 as well; the JSON mode is skipped there, because ct3 has
none. Its counterpart is the text template, which is how a skin writes
JSON today.
"""

from __future__ import annotations

import gc
import io
import json
import sys
import time
import tracemalloc

ROWS = 100_000
FIELDS = ("f0", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9")


class Record:
    """One archive record. __slots__, as a real one would have."""

    __slots__ = ("start", *FIELDS)

    def __init__(self, start: int):
        self.start = start
        for index, name in enumerate(FIELDS):
            setattr(self, name, start * 0.001 + index)


def records():
    """The source, as a generator. Nothing is held here."""
    for i in range(ROWS):
        yield Record(1700000000 + i * 300)


def context():
    return [{"station": "Zuhause", "points": records()}]


TEXT = """{"station": "$station", "series": [
#set $first = True
#for $p in $points
#if not $first
,
#end if
#set $first = False
[$p.start, $p.f0, $p.f1, $p.f2, $p.f3, $p.f4,
 $p.f5, $p.f6, $p.f7, $p.f8, $p.f9]
#end for
]}
"""

SERIES = ('#mode json\n'
          '{"station": "$station",\n'
          ' "series": #series($points, layout="pairs",'
          ' fields=["start", "f0", "f1", "f2", "f3", "f4",'
          ' "f5", "f6", "f7", "f8", "f9"])}\n')


class Sink:
    """Counts what is written and keeps none of it.

    A StringIO would collect the whole output, and then the run would
    measure its own buffer instead of the engine.
    """

    def __init__(self):
        self.count = 0

    def write(self, text: str) -> None:
        self.count += len(text)


def measure(name: str, run) -> dict:
    """Wall time, peak memory and output size of one way.

    Args:
        name (str): what to call it in the report.
        run (Callable[[], int]): Called as ``run()``. Returns the
            number of characters it produced.

    Returns:
        dict: name, seconds, peak megabytes, megabytes of output.
    """
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    size = run()
    seconds = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    gc.collect()
    return {"name": name, "seconds": seconds,
            "peak_mb": peak / 1048576.0, "out_mb": size / 1048576.0}


def by_hand() -> int:
    text = json.dumps(
        {"station": "Zuhause",
         "series": [[r.start] + [getattr(r, f) for f in FIELDS]
                    for r in records()]},
        ensure_ascii=False, separators=(",", ": "))
    return len(text)


def text_template() -> int:
    from Cheetah.Template import Template

    klass = Template.compile(source=TEXT, useCache=False,
                             cacheCompilationResults=False)
    return len(str(klass(searchList=context()).respond()))


def json_collect() -> int:
    from ct4.jsonmode import compile_template

    return len(compile_template(SERIES).render(context()))


def json_stream() -> int:
    from ct4.jsonmode import compile_template

    sink = Sink()
    compile_template(SERIES).stream(sink, context())
    return sink.count


def json_stream_to_file(path: str):
    """The way it would actually be used: straight into the file."""
    def run() -> int:
        from ct4.jsonmode import compile_template

        with io.open(path, "w", encoding="utf-8") as handle:
            compile_template(SERIES).stream(handle, context())
        import os

        return os.path.getsize(path)

    return run


def have_json_mode() -> bool:
    try:
        import ct4.jsonmode                                    # noqa: F401
    except ImportError:
        return False
    return True


def main() -> int:
    ways = [("json.dumps by hand", by_hand),
            ("text template, as today", text_template)]
    if have_json_mode():
        ways += [("json mode, collecting", json_collect),
                 ("json mode, streaming", json_stream),
                 ("json mode, straight to file",
                  json_stream_to_file("/tmp/large.json"))]

    import Cheetah

    found = []
    for name, run in ways:
        found.append(measure(name, run))

    if "--json" in sys.argv:
        print(json.dumps({"version": Cheetah.Version, "rows": ROWS,
                          "cases": found}))
        return 0

    print("%s rows, %d values each, Cheetah %s"
          % ("{:,}".format(ROWS), len(FIELDS) + 1, Cheetah.Version))
    print("  %-30s %8s %10s %9s" % ("", "seconds", "peak MB", "out MB"))
    for row in found:
        print("  %-30s %8.2f %10.1f %9.1f"
              % (row["name"], row["seconds"], row["peak_mb"], row["out_mb"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
