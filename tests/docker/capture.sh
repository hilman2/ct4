#!/bin/sh
# Works against a real weewx.
#
#   capture  record contexts from a report run (default)
#   verify   do the same run once with ct3 and once with the fork and
#            compare the pages they produce
#   suite    weewx own test suite against both engines, outcomes compared
#
# The recordings land under /out, which the caller mounts.
set -eu

cp -r /repo/Cheetah /repo/ct4 /repo/tests /repo/setup.py \
      /repo/pyproject.toml /work/
cd /work
python setup.py build_ext --inplace >/dev/null 2>&1

# Half of weewx' tests want MySQL, and testgen.conf and test_manager.py
# hardcode host = localhost. Without a server those forty cases error
# out, and a report saying "280 of 334 passed" gets put down before the
# sentence that matters is read.
start_mysql() {
    mariadbd --datadir=/var/lib/mysql-ct4 --skip-grant-tables \
        --socket=/run/mysqld/mysqld.sock >/tmp/mysqld.log 2>&1 &
    for _ in $(seq 1 60); do
        if mariadb-admin --socket=/run/mysqld/mysqld.sock ping \
                >/dev/null 2>&1; then
            break
        fi
        sleep 0.5
    done
    if ! mariadb-admin --socket=/run/mysqld/mysqld.sock ping >/dev/null 2>&1
    then
        echo "MySQL did not come up:" >&2
        tail -20 /tmp/mysqld.log >&2
        exit 1
    fi
    # skip-grant-tables leaves every user in place and asks nobody for a
    # password, which is exactly what a throwaway test server wants. The
    # accounts still have to exist, because weewx connects as weewx1 and
    # as weewx and both names are hardcoded.
    mariadb --socket=/run/mysqld/mysqld.sock < /opt/mysql-init.sql \
        >/dev/null 2>&1 || true
}

start_mysql

TESTS=/opt/weewx/src/weewx/tests
RESULTS=/var/tmp/weewx-test/test_results

run_capture() {
    cd "$TESTS"
    export PYTHONPATH=/work
    export CT4_FIXTURE_DIR="${CT4_FIXTURE_DIR:-/out}"
    mkdir -p "$CT4_FIXTURE_DIR"

    # This run cannot meet weewx' expectation files, and it is meant
    # not to: the recorder forces the output encoding to utf8 so that a
    # fixture does not have to rebuild weewx' encoder. StandardTest
    # asks for strict_ascii, which throws the degree sign away, so the
    # recording writes 46.8 degrees F where weewx writes 46.8F. What
    # is wanted here are the contexts, not the verdict.
    #
    # It used to say something else here: that the test fails anyway.
    # It did, for a second and unrelated reason. The image had no
    # de_DE.UTF-8, which testgen.conf runs the metric report in, and
    # German writes 8,2 where English writes 8.2. That was a defect in
    # the image, it is fixed, and without our plugin weewx' own test
    # now passes. Two causes had been rolled into one excuse.
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

# weewx' own test suite, run once against each engine. What counts is
# not whether its tests pass, some do not pass here with nothing of
# ours loaded, but whether Cheetah4 changes which ones do.
run_suite() {
    python /repo/tests/docker/weewx_suite.py
}

case "${1:-capture}" in
    capture) run_capture ;;
    verify)  run_verify ;;
    json)    run_json ;;
    suite)   run_suite ;;
    *)       echo "Unknown: $1" >&2; exit 2 ;;
esac
