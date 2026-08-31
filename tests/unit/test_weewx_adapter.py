"""Der Typ-Adapter fuer weewx.

Laeuft nur, wo weewx installiert ist, also im Aufzeichnungs-Container.
"""

from __future__ import annotations

import json

import pytest

from ct4.adapters import as_value
from ct4.jsonmode import render
from ct4.plugins import weewx_adapter

units = pytest.importorskip("weewx.units")


@pytest.fixture(autouse=True)
def angemeldet():
    weewx_adapter.install()


def helper(wert, einheit="degree_C", gruppe="group_temperature"):
    return units.ValueHelper((wert, einheit, gruppe))


def test_rohwert_kommt_durch():
    assert as_value(helper(12.3456)).value == 12.3456


def test_fehlender_wert_bleibt_none():
    assert as_value(helper(None)).value is None


def test_wert_wird_zur_zahl_nicht_zur_zeichenkette():
    ergebnis = json.loads(render('{"t": $t}', [{"t": helper(12.3456)}]))
    assert isinstance(ergebnis["t"], float)


def test_fehlender_wert_wird_null_nicht_der_text_na():
    # Ohne Adapter stuende hier "N/A", weil das ValueHelpers str() ist.
    assert json.loads(render('{"t": $t}', [{"t": helper(None)}])) == \
        {"t": None}


def test_stellen_aus_dem_formatstring():
    wert = helper(12.3456)
    wert.formatter.unit_format_dict["degree_C"] = "%.1f"
    assert as_value(wert).precision == 1
    assert json.loads(render('{"t": $t}', [{"t": wert}])) == {"t": 12.3}
