#!/bin/sh
# Arbeitet mit einem echten weewx.
#
#   capture  Kontexte aus einem Report-Lauf mitschreiben (Vorgabe)
#   verify   denselben Lauf einmal mit ct3 und einmal mit dem Fork
#            machen und die erzeugten Seiten vergleichen
#
# Die Aufzeichnungen landen unter /out, das der Aufrufer einhaengt.
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

    # Der Test von weewx faellt hier durch, auch ohne unser Plugin: die
    # erzeugten Seiten weichen von seinen Erwartungsdateien ab. Uns geht
    # es nicht um sein Urteil, sondern um die Kontexte, die dabei
    # anfallen.
    python -m pytest test_templates.py -q -p ct4.fixture.weewx_capture \
        -k sqlite || true

    echo
    echo "== Nachbau des weewx-Filters =="
    cd /work && python -m pytest tests/unit/test_weewx_filter.py -q

    echo
    echo "Aufzeichnungen:"
    ls -1 "$CT4_FIXTURE_DIR" | wc -l
}

# Das Abnahmekriterium aus PLAN.md, Abschnitt 8: weewx laeuft mit
# unveraendertem Code und unveraenderten Skins gegen ct4 und erzeugt
# byte-identische Ausgabe. Gemessen wird, indem derselbe Report-Lauf
# zweimal stattfindet und die erzeugten Baeume verglichen werden.
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
        echo "$impl: $(find "/tmp/out-$impl" -type f | wc -l) Dateien"
    done

    echo
    if diff -r /tmp/out-ct3 /tmp/out-ct4; then
        echo "Gleich: weewx erzeugt unter ct4 dieselben Seiten wie unter ct3."
    else
        echo "Unterschiede gefunden." >&2
        exit 1
    fi
}

case "${1:-capture}" in
    capture) run_capture ;;
    verify)  run_verify ;;
    *)       echo "Unbekannt: $1" >&2; exit 2 ;;
esac
