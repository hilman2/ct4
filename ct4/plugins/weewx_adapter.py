"""weewx an ct4 anmelden.

Das erste Plugin, und deshalb der Massstab fuer die uebrigen: es traegt
Wissen ein, das ct4 nicht hat, und es rechnet nichts. Was ein Messwert
ist, welche Einheit er traegt und wie aggregiert wird, bleibt in weewx.
Hierher wandert nur die Auskunft darueber.

Angemeldet wird ein Typ-Adapter auf ``ValueHelper``. Danach liefert
``$day.outTemp.max`` im JSON-Modus eine Zahl mit der Rundung, die der
Skin ohnehin vorsieht, und niemand muss mehr ``.raw`` schreiben oder
``$jsonize`` bemuehen.

Solange ``ct4-weewx`` kein eigenes Paket ist, wird die Methode hier
nachtraeglich an die Klasse gehaengt. Das ist der Zustand, nicht das
Ziel: die Methode gehoert zu ``ValueHelper``, und dort sollte sie
irgendwann auch stehen.
"""

from __future__ import annotations

import re
from typing import Any

from ct4.adapters import Ct4Value

# Aus '%.1f' wird 1. weewx traegt die Nachkommastellen in Formatstrings,
# weil es formatiert; ct4 braucht die Zahl, weil es rundet.
DIGITS = re.compile(r"%[^%a-zA-Z]*\.(\d+)[eEfgG]")


def precision_of(helper: Any) -> int | None:
    """Wie viele Nachkommastellen der Skin fuer diesen Wert vorsieht.

    ``None``, wenn sich das nicht sagen laesst. Dann rundet ct4 nicht,
    und der volle Wert steht im JSON. Das ist die richtige Vorgabe:
    lieber zu genau als still gekuerzt.
    """
    unit = helper.value_t[1]
    try:
        form = helper.formatter.get_format_string(unit)
    except Exception:                                   # noqa: BLE001
        return None
    match = DIGITS.match(form or "")
    return int(match.group(1)) if match else None


def value_of(helper: Any) -> Ct4Value:
    return Ct4Value(helper.raw, precision=precision_of(helper))


def install() -> None:
    """Haengt den Adapter an weewx' ValueHelper."""
    from weewx.units import ValueHelper

    ValueHelper.__ct4_value__ = value_of
