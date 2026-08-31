#!/bin/sh
# Works against a real weewx.
#
#   capture  record contexts from a report run (default)
#   verify   do the same run once with ct3 and once with the fork and
#            compare the pages they produce
#
# The recordings land under /out, which the caller mounts.
set -eu

cp -r /repo/Cheetah /repo/ct4 /repo/tests /repo/setup.py \
      /repo/pyproject.toml /work/
cd /work
python setup.py build_ext --inplace >/dev/null 2>&1

TESTS=/opt/weewx/src/weewx/tests
RESULTS=/var/tmp/weewx-test/test_results

run_capture() {
    cd "$TESTS"
    export PYTHONPATH=/work
    export CT4_FIXTURE_DIR="${CT4_FIXTURE_DIR:-/out}"
    mkdir -p "$CT4_FIXTURE_DIR"

    # The weewx test fails here, even without our plugin: the pages it
    # produces differ from its own expectation files. We are not after
    # its verdict, but after the contexts that accumulate along the
    # way.
    python -m pytest test_templates.py -q -p ct4.fixture.weewx_capture \
        -k sqlite || true

    echo
    echo "== Reimplementation of the weewx filter =="
    cd /work && python -m pytest tests/unit/test_weewx_filter.py -q

    echo
    echo "Recordings:"
    ls -1 "$CT4_FIXTURE_DIR" | wc -l
}

# The acceptance criterion from PLAN.md, section 8: weewx runs against
# ct4 with unchanged code and unchanged skins and produces byte-identical
# output. Measured by letting the same report run happen twice and
# comparing the trees it produces.
run_verify() {
    cd "$TESTS"
    for impl in ct3 ct4; do
        rm -rf "$RESULTS"
        if [ "$impl" = ct4 ]; then
            PYTHONPATH=/work python -m pytest test_templates.py -q \
                -k sqlite >/dev/null 2>&1 || true
        else
            python -m pytest test_templates.py -q -k sqlite \
                >/dev/null 2>&1 || true
        fi
        cp -r "$RESULTS" "/tmp/out-$impl"
        echo "$impl: $(find "/tmp/out-$impl" -type f | wc -l) files"
    done

    echo
    if diff -r /tmp/out-ct3 /tmp/out-ct4; then
        echo "Same: weewx under ct4 makes the same pages as under ct3."
    else
        echo "Differences found." >&2
        exit 1
    fi
}

# The acceptance criterion for P2: a skin produces its JSON in the new
# mode, the result holds to its schema, and two runs deliver the same
# bytes.
run_json() {
    cd "$TESTS"
    export PYTHONPATH=/work
    export CT4_JSON_TEMPLATE=/repo/examples/weewx-json/day.json.tmpl
    for lauf in 1 2; do
        export CT4_JSON_OUT="/tmp/day-$lauf.json"
        python -m pytest test_templates.py -q             -p ct4.fixture.weewx_capture -k sqlite 2>&1 |
            grep -E "^ct4" || true
    done

    echo
    if cmp -s /tmp/day-1.json /tmp/day-2.json; then
        echo "Two runs, the same bytes."
    else
        echo "The runs differ." >&2
        diff /tmp/day-1.json /tmp/day-2.json | head -20 >&2
        exit 1
    fi
    echo
    head -40 /tmp/day-1.json
}

case "${1:-capture}" in
    capture) run_capture ;;
    verify)  run_verify ;;
    json)    run_json ;;
    *)       echo "Unknown: $1" >&2; exit 2 ;;
esac
