#!/bin/sh
# Runs the checks, the tests and the corpus test bench.
#
#   lint     ruff and mypy
#   unit     tests of the tool
#   cheetah  the test suite that ct3 brings along
#   evals    the diagnostics tasks
#   corpus   the test bench
#   all      everything (default)
#
# Work happens in /work, a tmpfs. The repo is mounted read-only under
# /repo: the run must leave nothing behind on the work machine, and the
# built C NameMapper lands next to the sources.
set -eu

cp -r /repo/Cheetah /repo/ct4 /repo/tests /repo/bin /repo/setup.py \
      /repo/pyproject.toml /repo/README.rst /repo/LICENSE \
      /repo/declarations /work/
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
    check)   run_check ;;
    evals)   run_evals ;;
    all)     run_lint; echo; run_unit; echo; run_cheetah; echo
             run_check; echo; run_evals; echo; run_corpus ;;
    *)       echo "Unknown: $WHAT" >&2; exit 2 ;;
esac
