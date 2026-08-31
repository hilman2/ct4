"""Recording and replaying a context.

The decisive test is ``test_replay_gives_the_same_output``: the same
template, once against the live objects, once against the stored
recording, and both must be equal byte for byte.
"""

from __future__ import annotations

import json

import pytest
from Cheetah.Template import Template

from ct4.fixture.record import Missing, Recorder, replay


class Reading:
    """Stands for weewx' ValueHelper: formats itself, knows its raw value."""

    def __init__(self, value, fmt="%.1f"):
        self.raw = value
        self._fmt = fmt

    def __str__(self):
        return "N/A" if self.raw is None else self._fmt % self.raw


class Aggregate:
    def __init__(self, smallest, largest):
        self.min = Reading(smallest)
        self.max = Reading(largest)


class Record:
    def __init__(self, stamp, temperature):
        self.dateTime = Reading(stamp, "%d")
        self.outTemp = Reading(temperature)


class Day:
    def __init__(self):
        self.outTemp = Aggregate(3.25, 17.5)
        self.rain = Aggregate(0.0, None)
        self.records = [Record(100, 3.25), Record(200, 17.5)]

    def span(self, delta=1):
        return Aggregate(delta, delta * 2)


TEMPLATE = """\
Max: $day.outTemp.max, raw $day.outTemp.max.raw
Rain: $day.rain.max
#for $r in $day.records
  $r.dateTime = $r.outTemp
#end for
Span: $day.span(delta=3).max
"""


def _render(context):
    template = Template(TEMPLATE, searchList=[{"day": context}])
    try:
        return template.respond()
    finally:
        template.shutdown()


def test_replay_gives_the_same_output():
    tree = {}
    live = _render(Recorder(Day(), tree))
    # The detour through JSON belongs here: the fixture lives on disk.
    recorded = json.loads(json.dumps(tree))
    assert _render(replay(recorded)) == live


def test_a_missing_value_survives_as_none():
    tree = {}
    live = _render(Recorder(Day(), tree))
    assert "N/A" in live
    assert _render(replay(tree)) == live


def test_an_unread_field_reports_itself():
    # A fixture that silently returns nothing would be worse than no
    # fixture at all: the check would be green and see nothing.
    tree = {}
    _render(Recorder(Day(), tree))
    with pytest.raises(Missing) as error:
        replay(tree).outTemp.avg
    assert "avg" in str(error.value)
    assert "max" in str(error.value)


def test_an_unknown_call_reports_itself():
    tree = {}
    _render(Recorder(Day(), tree))
    with pytest.raises(Missing):
        replay(tree).span(delta=99)


def test_the_recording_is_json():
    tree = {}
    _render(Recorder(Day(), tree))
    assert json.dumps(tree)


def test_the_recorded_context_is_read_only():
    with pytest.raises(TypeError):
        Recorder(Day(), {}).outTemp = 1


class WithMethod:
    """Like weewx' TimeBinder: $day is a method, not an attribute."""

    def day(self):
        return Aggregate(1.0, 2.0)


def test_a_bound_method_is_still_called():
    # Cheetah calls a method without parentheses on its own. A
    # recorder that looks like an instance breaks that, and then
    # $day.hours finds nothing any more.
    source = "$obj.day.max"
    tree = {}

    def render(context):
        template = Template(source, searchList=[{"obj": context}])
        try:
            return template.respond()
        finally:
            template.shutdown()

    live = render(Recorder(WithMethod(), tree))
    assert live == "2.0"
    assert render(replay(json.loads(json.dumps(tree)))) == live


def test_a_missing_key_raises_keyerror():
    # Cheetah probes namespaces with a key lookup. An AttributeError
    # at that point makes CPython report an ignored exception, and the
    # run drowns in noise.
    tree = {}
    _render(Recorder(Day(), tree))
    node = replay(tree)
    with pytest.raises(KeyError):
        node["nosuchkey"]
    with pytest.raises(AttributeError):
        node.nosuchkey
