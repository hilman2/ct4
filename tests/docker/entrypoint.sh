#!/bin/sh
# Runs the checks, the tests and the corpus test bench.
#
#   lint     ruff and mypy
#   unit     tests of the tool
#   cheetah  the test suite that ct3 brings along
#   evals    the diagnostics tasks
#   corpus   the test bench
#   fuzz     built templates, both engines, byte for byte
#   bench    render times, ct3 against the fork
#   large    one large series, time and memory
#   coverage what the corpus holds, per mechanism
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

# Over all templates of the corpus. Exactly one finding is expected:
# weewx' own test case for a wrong aggregate. Any further one would be a
# false finding, and a false finding gets people to ignore the tool.
run_check() {
    echo "== ct4 check over the corpus skins =="
    python -m ct4.corpus --impl fork check-templates /repo/corpus/skins.jsonl --expect 1
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

# The corpus is 2026 real templates and every one of them puts its
# directives on lines of their own. This builds the templates that do
# not, which is where the whitespace rules live, and holds the code
# generator to the same rule: what it accepts renders byte for byte
# what the compiler renders.
run_fuzz() {
    echo "== Built templates, generator against the compiler =="
    PYTHONPATH=/work python tests/fuzz/whitespace.py
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
    evals)   run_evals ;;
    bench)   run_bench ;;
    large)   run_large ;;
    coverage) run_coverage ;;
    all)     run_lint; echo; run_unit; echo; run_cheetah; echo
             run_check; echo; run_evals; echo; run_bench; echo
             run_corpus; echo; run_fuzz ;;
    *)       echo "Unknown: $WHAT" >&2; exit 2 ;;
esac
