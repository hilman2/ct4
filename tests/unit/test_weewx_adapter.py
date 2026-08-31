"""The type adapter for weewx.

Only runs where weewx is installed, that is, in the recording
container.
"""

from __future__ import annotations

import json

import pytest

from ct4.adapters import as_value
from ct4.jsonmode import render
from ct4.plugins import weewx_adapter

units = pytest.importorskip("weewx.units")


@pytest.fixture(autouse=True)
def installed():
    weewx_adapter.install()


def helper(value, unit="degree_C", group="group_temperature"):
    return units.ValueHelper((value, unit, group))


def test_the_raw_value_comes_through():
    assert as_value(helper(12.3456)).value == 12.3456


def test_a_missing_value_stays_none():
    assert as_value(helper(None)).value is None


def test_the_value_becomes_a_number_not_a_string():
    result = json.loads(render('{"t": $t}', [{"t": helper(12.3456)}]))
    assert isinstance(result["t"], float)


def test_a_missing_value_becomes_null_not_the_text_na():
    # Without the adapter this would read "N/A", the ValueHelper str().
    assert json.loads(render('{"t": $t}', [{"t": helper(None)}])) == \
        {"t": None}


def test_the_digits_come_from_the_format_string():
    value = helper(12.3456)
    value.formatter.unit_format_dict["degree_C"] = "%.1f"
    assert as_value(value).precision == 1
    assert json.loads(render('{"t": $t}', [{"t": value}])) == {"t": 12.3}
