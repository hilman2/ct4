#!/bin/sh
# Laesst Pruefungen, Tests und Korpus-Pruefstand laufen.
#
#   lint     ruff und mypy
#   unit     Tests des Werkzeugs
#   cheetah  die Testsuite, die ct3 mitbringt
#   corpus   den Pruefstand
#   all      alles (Vorgabe)
#
# Gearbeitet wird in /work, einer tmpfs. Das Repo haengt schreibgeschuetzt
# unter /repo: der Lauf soll nichts auf der Arbeitsmaschine hinterlassen,
# und der gebaute C-NameMapper landet neben den Quellen.
set -eu

cp -r /repo/Cheetah /repo/ct4 /repo/tests /repo/bin /repo/setup.py \
      /repo/pyproject.toml /repo/README.rst /repo/LICENSE /work/
cd /work

python setup.py build_ext --inplace >/dev/null 2>&1
chmod +x bin/*

CORPUS="/repo/corpus/ct3-tests.jsonl /repo/corpus/skins.jsonl"
CORPUS="$CORPUS /repo/corpus/weewx-render.jsonl"
WHAT="${1:-all}"

run_lint() {
    echo "== ruff und mypy =="
    ruff check .
    mypy
}

run_unit() {
    echo "== Tests des Werkzeugs =="
    # -n auto verteilt auf alle zugeteilten Kerne. Das Zeitlimit soll
    # einen haengenden Test benennen, statt den Lauf abzuwuergen.
    python -m pytest tests/unit -q -n auto
}

run_cheetah() {
    echo "== Testsuite von ct3 =="
    # Ueber deren eigenen Runner. Die Testklassen heissen nicht nach
    # pytest-Muster, und install_eols() erzeugt die Varianten fuer die
    # drei Zeilenenden erst zur Laufzeit.
    #
    # bin/ und PYTHONPATH gelten nur hier: die Tests von CheetahWrapper
    # starten "cheetah" als Unterprozess, und der muss den Fork laden.
    # Ausserhalb dieses Aufrufs bleibt das installierte ct3 erreichbar,
    # sonst haette der Pruefstand keine Referenz mehr.
    PATH="/work/bin:$PATH" PYTHONPATH=/work python Cheetah/Tests/Test.py
}

# Ueber alle Vorlagen des Korpus. Erwartet wird genau ein Befund:
# weewx' eigener Testfall fuer ein falsches Aggregat. Jeder weitere
# waere ein Falschbefund, und ein Falschbefund bringt Leute dazu, das
# Werkzeug zu ignorieren.
run_check() {
    echo "== ct4 check ueber die Skins des Korpus =="
    python -m ct4.corpus --impl fork check-templates \n        /repo/corpus/skins.jsonl --expect 1
}

run_corpus() {
    echo "== Referenz gegen den eingecheckten Korpus =="
    python -m ct4.corpus --impl installed check $CORPUS

    echo
    echo "== Fork gegen den eingecheckten Korpus =="
    python -m ct4.corpus --impl fork check $CORPUS
}

case "$WHAT" in
    lint)    run_lint ;;
    unit)    run_unit ;;
    cheetah) run_cheetah ;;
    corpus)  run_corpus ;;
    check)   run_check ;;
    all)     run_lint; echo; run_unit; echo; run_cheetah; echo
             run_check; echo; run_corpus ;;
    *)       echo "Unbekannt: $WHAT" >&2; exit 2 ;;
esac
