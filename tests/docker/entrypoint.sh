#!/bin/sh
# Laesst Tests und Korpus-Pruefstand laufen.
#
#   unit    nur die Tests des Werkzeugs
#   corpus  nur den Pruefstand
#   all     beides (Vorgabe)
#
# Gearbeitet wird in /work, einer tmpfs. Das Repo haengt schreibgeschuetzt
# unter /repo: der Lauf soll nichts auf der Arbeitsmaschine hinterlassen,
# und der gebaute C-NameMapper landet neben den Quellen.
set -eu

cp -r /repo/Cheetah /repo/ct4 /repo/tests /repo/setup.py /repo/SetupTools.py \
      /repo/SetupConfig.py /work/
cd /work
python setup.py build_ext --inplace >/dev/null 2>&1

CORPUS="/repo/corpus/ct3-tests.jsonl /repo/corpus/skins.jsonl"
WHAT="${1:-all}"

run_unit() {
    echo "== Tests des Werkzeugs =="
    # -n auto verteilt auf alle zugeteilten Kerne. Das Zeitlimit soll
    # einen haengenden Test benennen, statt den Lauf abzuwuergen.
    python -m pytest tests/unit -q -n auto --timeout=60
}

run_corpus() {
    echo "== Referenz gegen den eingecheckten Korpus =="
    python -m ct4.corpus --impl installed check $CORPUS

    echo
    echo "== Fork gegen den eingecheckten Korpus =="
    python -m ct4.corpus --impl fork check $CORPUS
}

case "$WHAT" in
    unit)   run_unit ;;
    corpus) run_corpus ;;
    all)    run_unit; echo; run_corpus ;;
    *)      echo "Unbekannt: $WHAT" >&2; exit 2 ;;
esac
