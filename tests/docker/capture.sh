#!/bin/sh
# Schreibt Kontexte aus einem echten weewx-Report-Lauf mit.
#
# Die Aufzeichnungen landen unter /out, das der Aufrufer einhaengt.
set -eu

cp -r /repo/ct4 /repo/tests /work/
cd /opt/weewx/src/weewx/tests

export PYTHONPATH=/work
export CT4_FIXTURE_DIR="${CT4_FIXTURE_DIR:-/out}"
mkdir -p "$CT4_FIXTURE_DIR"

# Der Test von weewx faellt hier durch, auch ohne unser Plugin: die
# erzeugten Seiten weichen von seinen Erwartungsdateien ab. Uns geht es
# nicht um sein Urteil, sondern um die Kontexte, die dabei anfallen.
python -m pytest test_templates.py -q -p ct4.fixture.weewx_capture     -k sqlite || true

echo
echo "== Nachbau des weewx-Filters =="
cd /work && python -m pytest tests/unit/test_weewx_filter.py -q

echo
echo "Aufzeichnungen:"
ls -1 "$CT4_FIXTURE_DIR" | head -20
ls -1 "$CT4_FIXTURE_DIR" | wc -l
