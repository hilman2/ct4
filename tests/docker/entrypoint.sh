#!/bin/sh
# Runs the checks, the tests and the corpus test bench.
#
#   lint     ruff and mypy
#   unit     tests of the tool
#   cheetah  the test suite that ct3 brings along
#   evals    the diagnostics tasks
#   reach    how far the code generator gets, and what stops it
#   corpus   the test bench
#   fuzz     built templates, both engines, byte for byte
#   bench    render times, ct3 against the fork
#   large    one large series, time and memory
#   coverage what the corpus holds, per mechanism
#   sabotage break the generator on purpose, see who notices
#   all      everything (default)
#
# Work happens in /work, a tmpfs. The repo is mounted read-only under
# /repo: the run must leave nothing behind on the work machine, and the
# built C NameMapper lands next to the sources.
set -eu

cp -r /repo/Cheetah /repo/ct4 /repo/tests /repo/bin /repo/setup.py \
      /repo/pyproject.toml /repo/README.rst /repo/LICENSE \
      /work/
cd /work

python setup.py build_ext --inplace >/dev/null 2>&1
chmod +x bin/*

CORPUS="/repo/corpus/ct3-tests.jsonl /repo/corpus/skins.jsonl"
CORPUS="$CORPUS /repo/corpus/weewx-render.jsonl"
WHAT="${1:-all}"

run_lint() {
    echo "== ruff and mypy =="
    ruff check .
    mypy
}

run_unit() {
    echo "== Tool tests =="
    # -n auto spreads the work over all assigned cores. The time limit
    # is there to name a hanging test instead of choking off the run.
    python -m pytest tests/unit -q -n auto
}

run_cheetah() {
    echo "== ct3 test suite =="
    # Through their own runner. The test classes are not named after the
    # pytest pattern, and install_eols() creates the variants for the
    # three line endings only at run time.
    #
    # bin/ and PYTHONPATH apply to this one call only: the CheetahWrapper
    # tests start "cheetah" as a subprocess, and that one has to load the
    # fork. Outside this call the installed ct3 stays reachable, or the
    # test bench would have no reference left.
    PATH="/work/bin:$PATH" PYTHONPATH=/work python Cheetah/Tests/Test.py
}

# Every template of the corpus through ct4's own diagnostics. Two
# templates are found, and both findings are real: weewx' own test case
# for a wrong aggregate, and the line in series.html.tmpl that writes
# ".round(5)json()" where the label above it writes ".round(5).json()".
# Four rather than two, because the weewx corpus holds those two files
# twice, once harvested as a skin and once as a render case.
#
# Any further one would be a false finding, and a false finding gets
# people to ignore the tool. That is what the number is guarding: 2026
# real templates, no noise.
run_check() {
    echo "== ct4 check over the whole corpus =="
    python -m ct4.corpus --impl fork check-templates $CORPUS --expect 4

    # And over the harvested skins, where the findings are somebody
    # else's and there are eighty-five of them. Guarded the same way and
    # for the same reason: the number may only move when a rule
    # changes, and then it is read.
    #
    # 80 are CT4103, nearly all of them one skin writing $day.outTemp
    # .maxTime where weewx spells every aggregate in lower case. 4 are
    # CT4005, in three separate projects: "$station.latitude[0]lstrip"
    # and twice "$span($hour_delta=3)lightning_strike_count.sum", both
    # of them a dot somebody dropped and got away with. 1 is a #for
    # with no #end for, which ct3 will not parse either.
    echo
    echo "== ct4 check over the harvested skins =="
    python -m ct4.corpus --impl fork check-templates \
        /repo/corpus/skins-render.jsonl --expect 85
}

# "AI ready" is checkable or it is marketing. What gets measured is not
# a language model but the diagnostics: whether the correction follows
# from the message.
run_evals() {
    echo "== Evals: does the message imply the fix? =="
    python -c "import sys; from ct4 import evals; r = evals.run(); print(evals.report(r)); sys.exit(1 if evals.failed(r) else 0)"
}

# The same measurements once against the installed ct3 and once against
# the fork. Which Cheetah gets loaded is decided by PYTHONPATH alone:
# without it the interpreter finds the installed ct3 in site-packages,
# with /work on it the fork wins. The reference run finds no ct4 either,
# and that is right: ct3 has no JSON mode, and the run says so instead
# of quietly leaving the line out.
run_bench() {
    echo "== Render, ct3 against the fork =="
    python tests/bench/render.py --json > /tmp/reference.json
    PYTHONPATH=/work python tests/bench/render.py --json > /tmp/fork.json
    PYTHONPATH=/work python tests/bench/compare.py \
        /tmp/reference.json /tmp/fork.json --check

    echo
    echo "== The code generator against the compiler it stands in for =="
    PYTHONPATH=/work python tests/bench/backend.py --check
}

# A year of archive records at five-minute intervals, ten values each.
# The size at which the question stops being which way is faster.
run_large() {
    echo "== A large series, ct3 against the fork =="
    python tests/bench/large.py
    echo
    PYTHONPATH=/work python tests/bench/large.py
}

# What the corpus actually holds. Switches one mechanism off at a time
# and counts the cases that notice. Not part of the default run: it
# compiles the whole corpus once per mechanism, and its numbers are read
# when a change to the semantics is weighed, not on every push.
run_coverage() {
    echo "== What the corpus holds =="
    python -m ct4.corpus --impl fork coverage $CORPUS
}

# Three instruments, three blind spots, and the point is that they are
# different blind spots.
#
# whitespace builds templates out of fragments, so it sees the shapes
# nobody writes and nothing else. hostile takes the real templates and
# renders them against a context that answers everything and writes
# down what it was asked, which is how a difference that both engines
# spell the same way in bytes becomes visible; it is also the only run
# that renders the 390 skin templates at all, because those need a live
# weewx otherwise. perturb takes the real templates and moves the
# directives around inside them, which is the one that finds a rule
# whose shape the fragment list never composed.
run_fuzz() {
    echo "== Built templates, generator against the compiler =="
    PYTHONPATH=/work python tests/fuzz/whitespace.py
    echo
    PYTHONPATH=/work python tests/fuzz/hostile.py
    echo
    PYTHONPATH=/work python tests/fuzz/perturb.py
}

# Measures the checking rather than the code: one rule of the generator
# is broken at a time and the run says which instrument sees it. A
# sabotage nobody sees is a rule nobody holds. Not in the default run,
# because it compiles the corpus once per sabotage; read when the
# checking is being weighed, the way coverage is read when the
# semantics are.
run_sabotage() {
    echo "== Break the generator on purpose, see who notices =="
    PYTHONPATH=/work python tests/fuzz/sabotage.py
}

# How far the generator gets, with a floor under it. This is the one
# number nothing else in the suite reports: a template the generator
# stops taking still renders, because the caller falls back to ct3, so
# every other run goes on saying what it said before. The histogram
# under it says which rule to write next.
run_reach() {
    echo "== How far the code generator gets =="
    python -m ct4.corpus --impl fork reach $CORPUS \
        /repo/corpus/skins-render.jsonl --floor 1866
}

run_corpus() {
    echo "== Reference against the checked-in corpus =="
    python -m ct4.corpus --impl installed check $CORPUS

    echo
    echo "== Fork against the checked-in corpus =="
    python -m ct4.corpus --impl fork check $CORPUS
}

case "$WHAT" in
    lint)    run_lint ;;
    unit)    run_unit ;;
    cheetah) run_cheetah ;;
    corpus)  run_corpus ;;
    fuzz)    run_fuzz ;;
    check)   run_check ;;
    reach)   run_reach ;;
    evals)   run_evals ;;
    bench)   run_bench ;;
    large)   run_large ;;
    coverage) run_coverage ;;
    sabotage) run_sabotage ;;
    all)     run_lint; echo; run_unit; echo; run_cheetah; echo
             run_check; echo; run_reach; echo; run_evals; echo
             run_bench; echo
             run_corpus; echo; run_fuzz ;;
    *)       echo "Unknown: $WHAT" >&2; exit 2 ;;
esac
