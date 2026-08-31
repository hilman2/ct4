"""The JSON mode.

The guarantees from PLAN.md, section 4, each one as a test. What is
green here, a skin author can no longer get wrong.
"""

from __future__ import annotations

import json

import pytest

from ct4.adapters import Ct4Value
from ct4.jsonmode import render
from ct4.jsonmode.build import MissingValue
from ct4.jsonmode.parse import JsonTemplateError, parse


class Reading:
    """Like weewx' ValueHelper: knows raw value, digits and format."""

    def __init__(self, value, digits=1, unit="°C"):
        self.value = value
        self.digits = digits
        self.unit = unit

    def __ct4_value__(self):
        return Ct4Value(self.value, precision=self.digits)

    def __str__(self):
        if self.value is None:
            return "N/A"
        return "%.*f %s" % (self.digits, self.value, self.unit)


class Point:
    def __init__(self, start, value):
        self.start = start
        self.value = value


CONTEXT = [{
    "station": 'Zuhause "am" Berg',
    "temp": Reading(12.3456),
    "leer": Reading(None),
    "id": 42,
    "zeilen": [Point(1, 3.14159), Point(2, None), Point(3, 2.71828)],
    "wahr": True,
    "null": None,
}]


def loaded(source, context=None, **kw):
    return json.loads(render(source, context or CONTEXT, **kw))


# -- The guarantees --------------------------------------------------

def test_commas_are_not_the_authors_problem():
    # One too many, one too few, both are fine: there are no commas,
    # there is a structure.
    assert loaded('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}
    assert loaded('{"a": 1 "b": 2}') == {"a": 1, "b": 2}
    assert loaded('[1, 2, 3,]') == [1, 2, 3]


def test_a_loop_at_the_end_needs_no_comma():
    source = '{"r": [#for $p in $zeilen\n$p.start\n#end for]}'
    assert loaded(source) == {"r": [1, 2, 3]}


def test_an_empty_loop_leaves_nothing_behind():
    source = '{"r": [#for $p in []\n$p\n#end for]}'
    assert loaded(source) == {"r": []}


def test_numbers_stay_numbers():
    result = loaded('{"t": $temp}')
    assert result == {"t": 12.3}
    assert isinstance(result["t"], float)


def test_a_missing_value_becomes_null():
    assert loaded('{"t": $leer}') == {"t": None}


def test_a_missing_value_can_be_omitted():
    assert loaded('#missing omit\n{"t": $leer, "i": $id}') == {"i": 42}


def test_a_missing_value_can_be_an_error():
    with pytest.raises(MissingValue):
        render('#missing error\n{"t": $leer}', CONTEXT)


def test_a_missing_element_stays_null_despite_omit():
    # Inside a list, omitting would shift the positions.
    source = '#missing omit\n{"r": [$leer, $id]}'
    assert loaded(source) == {"r": [None, 42]}


def test_quotes_do_not_break_the_document():
    assert loaded('{"s": $station}') == {"s": 'Zuhause "am" Berg'}


def test_umlauts_stay_umlauts():
    text = render('{"s": "grün"}', CONTEXT)
    assert "grün" in text


def test_booleans_and_null_from_the_template():
    assert loaded('{"a": true, "b": false, "c": null}') == {
        "a": True, "b": False, "c": None}


# -- Precision -------------------------------------------------------

def test_the_precision_comes_from_the_object():
    assert loaded('{"t": $temp}') == {"t": 12.3}


def test_the_template_beats_the_object():
    assert loaded('{"t": $temp @ 3}') == {"t": 12.346}


def test_the_default_applies_where_the_object_is_silent():
    source = '#precision default = 2\n{"x": 3.14159}'
    assert loaded(source) == {"x": 3.14}


def test_precision_per_field_name():
    source = '#precision default = 3\n#precision kurz = 0\n' \
             '{"kurz": 3.9, "lang": 3.14159}'
    assert loaded(source) == {"kurz": 4, "lang": 3.142}


def test_rounding_does_not_make_a_string():
    result = loaded('{"x": $temp @ 0}')
    assert result == {"x": 12}
    assert not isinstance(result["x"], str)


# -- Value or text ---------------------------------------------------

def test_at_a_value_position_the_value_counts():
    assert loaded('{"t": $temp}') == {"t": 12.3}


def test_inside_a_string_the_formatting_counts():
    # The difference is deliberate. In text the author wants what the
    # object itself displays, unit included.
    assert loaded('{"t": "$temp"}') == {"t": "12.3 °C"}


def test_a_key_may_be_built_from_values():
    assert loaded('{"kanal-$id": 1}') == {"kanal-42": 1}


# -- Control flow ----------------------------------------------------

def test_if_leaves_a_field_out():
    source = '{"a": 1,\n#if $null\n"b": 2,\n#end if\n"c": 3}'
    assert loaded(source) == {"a": 1, "c": 3}


def test_if_else():
    source = '{\n#if $null\n"a": 1\n#else\n"b": 2\n#end if\n}'
    assert loaded(source) == {"b": 2}


def test_elif():
    source = '{\n#if $null\n"a": 1\n#elif $wahr\n"b": 2\n#else\n"c": 3\n' \
             '#end if\n}'
    assert loaded(source) == {"b": 2}


def test_a_nested_loop():
    source = '{"r": [#for $p in $zeilen\n{"t": $p.start}\n#end for]}'
    assert loaded(source) == {"r": [{"t": 1}, {"t": 2}, {"t": 3}]}


# -- Series ----------------------------------------------------------

def test_a_series_as_records():
    source = '{"r": #series($zeilen, fields=["start", "value"], ' \
             'precision=2)}'
    assert loaded(source) == {"r": [
        {"start": 1, "value": 3.14},
        {"start": 2, "value": None},
        {"start": 3, "value": 2.72}]}


def test_a_series_as_columns():
    source = '{"r": #series($zeilen, layout="columns", ' \
             'fields=["start", "value"], precision=2)}'
    assert loaded(source) == {"r": {"start": [1, 2, 3],
                                    "value": [3.14, None, 2.72]}}


def test_a_series_as_pairs_without_gaps():
    source = '{"r": #series($zeilen, layout="pairs", ' \
             'fields=["start", "value"], gaps="omit")}'
    assert loaded(source) == {"r": [[1, 3.14159], [3, 2.71828]]}


# -- Determinism -----------------------------------------------------

def test_two_runs_give_the_same_bytes():
    source = '{"b": 2, "a": 1, "r": [#for $p in $zeilen\n$p.start\n' \
             '#end for]}'
    first = render(source, CONTEXT)
    assert first == render(source, CONTEXT)


def test_keys_keep_the_order_of_the_template():
    text = render('{"z": 1, "a": 2, "m": 3}', CONTEXT)
    assert text.index('"z"') < text.index('"a"') < text.index('"m"')


# -- Errors ----------------------------------------------------------

def test_an_unknown_directive_is_reported():
    with pytest.raises(JsonTemplateError):
        parse('{\n#foo\n"a": 1\n#end foo\n}')


def test_an_open_block_is_reported():
    with pytest.raises(JsonTemplateError):
        parse('{"r": [#for $p in $zeilen\n$p\n]}')


def test_a_wrong_end_is_reported():
    with pytest.raises(JsonTemplateError):
        parse('{\n#if $wahr\n"a": 1\n#end for\n}')


def test_the_error_message_names_line_and_column():
    with pytest.raises(JsonTemplateError) as error:
        parse('{\n  "a": nichts\n}')
    assert "line 2" in str(error.value)


def test_a_comment_works_as_in_cheetah():
    assert loaded('{\n## this one is gone\n"a": 1\n}') == {"a": 1}


# -- Streaming -------------------------------------------------------

STREAM_SAMPLES = [
    '#mode json\n{"a": 1, "b": $station, "c": $leer}',
    '#mode json\n{"r": [#for $p in $zeilen\n{"t": $p.start}\n#end for]}',
    '#mode json\n{"r": [#for $p in []\n$p\n#end for]}',
    '#mode json\n{"s": #series($zeilen, fields=["start","value"],'
    ' precision=1)}',
    '#mode json\n{"s": #series($zeilen, layout="pairs",'
    ' fields=["start","value"])}',
    '#mode json\n{"s": #series($zeilen, layout="columns",'
    ' fields=["start","value"])}',
    '#mode json\n{"s": #series($zeilen, layout="pairs",'
    ' fields=["start","value"], gaps="omit")}',
    '#mode json\n#missing omit\n{"a": $leer, "b": 1}',
    '#mode json\n[1, [2, {"x": $station}], []]',
    '#mode json\n{"kanal-$id": 1}',
    '#mode json\n{\n#if $wahr\n"a": 1\n#else\n"b": 2\n#end if\n}',
]


@pytest.mark.parametrize("source", STREAM_SAMPLES)
def test_streaming_gives_the_same_bytes(source):
    # The property the whole second path hinges on. Without it, one
    # would have to settle for a single path.
    import io

    from ct4.jsonmode import compile_template

    compiled = compile_template(source)
    buffer = io.StringIO()
    compiled.stream(buffer, CONTEXT)
    assert buffer.getvalue() == compiled.render(CONTEXT)


class Sink:
    """Takes everything and keeps nothing.

    A StringIO would collect the whole output, and then the test would
    measure its own buffer instead of the build area.
    """

    def write(self, text):
        return len(text)


SERIES_SOURCE = ('#mode json\n{"s": #series($punkte, layout="pairs",'
                 ' fields=["start","value"])}')


def _peak(compiled, count, write):
    import tracemalloc

    points = (Point(i, float(i)) for i in range(count))
    tracemalloc.start()
    write(compiled, points)
    _, high = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return high


def test_streaming_keeps_the_memory_constant():
    # The whole point. Two sizes are compared, not one absolute
    # number, and the first call does not count: it builds the
    # compiled template, and that happens only once.
    from ct4.jsonmode import compile_template

    compiled = compile_template(SERIES_SOURCE)

    def write(c, points):
        c.stream(Sink(), [{"punkte": points}])

    _peak(compiled, 100, write)
    small = _peak(compiled, 10_000, write)
    large = _peak(compiled, 50_000, write)
    assert large < small * 2, "%d against %d bytes" % (small, large)


def test_the_collecting_path_grows_with_the_series():
    # The counter-check. Without it the test above says nothing: it
    # could be green because both paths are constant.
    from ct4.jsonmode import compile_template

    compiled = compile_template(SERIES_SOURCE)

    def write(c, points):
        c.render([{"punkte": points}])

    _peak(compiled, 100, write)
    small = _peak(compiled, 10_000, write)
    large = _peak(compiled, 50_000, write)
    assert large > small * 3, "%d against %d bytes" % (small, large)


def test_a_single_value_root_is_not_streamed():
    import io

    from ct4.jsonmode import compile_template

    compiled = compile_template('#mode json\n$id')
    assert compiled.render(CONTEXT) == "42"
    with pytest.raises(NotImplementedError):
        compiled.stream(io.StringIO(), CONTEXT)
