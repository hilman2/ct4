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
from ct4.declare import Declaration, Node

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


def aggregate_names() -> set[str]:
    """Die Aggregate, die weewx kennt, aus weewx' eigenen Tabellen.

    Nicht abgeschrieben: abgeschrieben liefe die Liste auseinander, und
    ein Skin bekaeme Meldungen ueber Namen, die es sehr wohl gibt.
    """
    import weewx.units
    import weewx.xtypes

    names = set(weewx.units.agg_group)
    for member in vars(weewx.xtypes).values():
        table = getattr(member, "agg_sql_dict", None)
        if isinstance(table, dict):
            names |= set(table)
    return names


def declare() -> Declaration:
    """Meldet an, welche Namen ein weewx-Skin lesen darf.

    Die Struktur ist dreistufig und genau dort geschlossen, wo Tippfehler
    passieren: ``$day.outTemp.mx``. Der mittlere Teil ist ein
    Messwerttyp, den weewx erst zur Laufzeit kennt, und bleibt deshalb
    offen.
    """
    import weewx
    import weewx.tags

    aggregates = {name: Node() for name in sorted(aggregate_names())}
    observation = Node(fields=dict(
        aggregates,
        exists=Node(kind="boolean"),
        has_data=Node(kind="boolean"),
        series=Node(open=True),
    ))

    # Was ein Zeitraum ausser Messwerten noch anbietet. Aus der Klasse
    # gelesen, damit die Liste mit weewx mitwaechst.
    span_fields = {
        name: Node(open=True)
        for name in dir(weewx.tags.TimespanBinder)
        if not name.startswith("_")
    }
    span_fields["*"] = observation
    span = Node(fields=span_fields)

    # Nur diese Namen liefern einen Zeitraum. $trend liefert Werte,
    # $span und $days_ago sind Aufrufe. Sie hier mitzunehmen ergaebe
    # Falschbefunde im unveraenderten Seasons-Skin, und ein Falschbefund
    # ist schlimmer als ein uebersehener Tippfehler: er bringt Leute
    # dazu, das Werkzeug zu ignorieren.
    periods = ("hour", "day", "yesterday", "week", "month", "year",
               "rainyear", "season", "seasonsyear", "alltime")
    roots = {name: span for name in periods}
    # Was TimeBinder sonst noch anbietet, bleibt offen. Das ist die
    # sichere Richtung: hier wird dann nicht geprueft statt falsch.
    for name in dir(weewx.tags.TimeBinder):
        if not name.startswith("_"):
            roots.setdefault(name, Node(open=True))
    roots["current"] = Node(fields={"*": Node(open=True)})
    for name in ("station", "unit", "obs", "Extras", "almanac",
                 "DisplayOptions", "SkinInfo", "gettext", "page",
                 "filename", "encoding", "month_name", "year_name",
                 "jsonize", "rnd", "to_int", "to_bool", "to_list",
                 "getobs", "latest", "trend"):
        roots.setdefault(name, Node(open=True))

    return Declaration(
        name="weewx",
        source="weewx %s" % getattr(weewx, "__version__", "unbekannt"),
        roots=roots)
