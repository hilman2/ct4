"""Der Korpusfall und seine Ablage."""

from __future__ import annotations

import json

import pytest

from ct4.corpus.case import (COMPILE, Case, decode, encode, is_jsonable,
                             read_jsonl, write_jsonl)


def test_klasse_ueberlebt_die_ablage():
    # Der einzige Wert, den ct3 durchreicht und JSON nicht kennt.
    encoded = encode({"baseclass": dict})
    assert json.dumps(encoded)
    assert decode(encoded) == {"baseclass": dict}


def test_klasse_auch_verschachtelt():
    encoded = encode({"a": [{"b": int}]})
    assert decode(encoded) == {"a": [{"b": int}]}


def test_gewoehnliche_werte_bleiben_wie_sie_sind():
    value = {"zahl": 1, "text": "x", "liste": [True, None]}
    assert decode(encode(value)) == value


def test_funktion_bleibt_unablegbar():
    # macroDirectives traegt eine Funktion. Fuer die gibt es keinen Weg,
    # und der Ernter muss den Fall verwerfen statt ihn halb abzulegen.
    assert not is_jsonable(encode({"macroDirectives": {"m": len}}))


@pytest.mark.parametrize("kind", ["render", COMPILE])
def test_jsonl_hin_und_zurueck(tmp_path, kind):
    cases = [
        Case(id="a", template="$x", expected="1", kind=kind),
        Case(id="b", template="$y", expected="2", kind=kind,
             namespace="inline", context=[{"y": 2}]),
    ]
    path = tmp_path / "korpus.jsonl"
    assert write_jsonl(cases, path) == 2
    assert list(read_jsonl(path)) == cases


def test_alte_zeile_ohne_fallart_bleibt_lesbar(tmp_path):
    # Der Korpus wird aelter als seine Felder. Eine Zeile aus der Zeit
    # vor den Uebersetzungsfaellen muss weiter laden, sonst muss man den
    # Korpus bei jeder Erweiterung neu erheben.
    path = tmp_path / "alt.jsonl"
    path.write_text(
        json.dumps({"id": "a", "template": "$x", "expected": "1"}) + "\n",
        encoding="utf-8")
    assert list(read_jsonl(path))[0].kind == "render"
