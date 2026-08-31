"""The corpus case and how it is stored."""

from __future__ import annotations

import json

import pytest

from ct4.corpus.case import (COMPILE, Case, decode, encode, is_jsonable,
                             read_jsonl, write_jsonl)


def test_class_survives_the_store():
    # The only value ct3 passes through that JSON does not know.
    encoded = encode({"baseclass": dict})
    assert json.dumps(encoded)
    assert decode(encoded) == {"baseclass": dict}


def test_class_survives_when_nested():
    encoded = encode({"a": [{"b": int}]})
    assert decode(encoded) == {"a": [{"b": int}]}


def test_ordinary_values_stay_as_they_are():
    value = {"number": 1, "text": "x", "list": [True, None]}
    assert decode(encode(value)) == value


def test_function_stays_unstorable():
    # macroDirectives carries a function. There is no way to store one,
    # and the harvester must drop the case instead of storing half of
    # it.
    assert not is_jsonable(encode({"macroDirectives": {"m": len}}))


@pytest.mark.parametrize("kind", ["render", COMPILE])
def test_jsonl_round_trip(tmp_path, kind):
    cases = [
        Case(id="a", template="$x", expected="1", kind=kind),
        Case(id="b", template="$y", expected="2", kind=kind,
             namespace="inline", context=[{"y": 2}]),
    ]
    path = tmp_path / "corpus.jsonl"
    assert write_jsonl(cases, path) == 2
    assert list(read_jsonl(path)) == cases


def test_old_line_without_a_kind_stays_readable(tmp_path):
    # The corpus outlives its fields. A line written before the
    # compile cases existed must still load, otherwise the corpus has
    # to be harvested again with every extension.
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps({"id": "a", "template": "$x", "expected": "1"}) + "\n",
        encoding="utf-8")
    assert list(read_jsonl(path))[0].kind == "render"
