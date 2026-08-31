"""Register weewx with ct4.

The first plugin, and therefore the yardstick for the rest: it
contributes knowledge that ct4 does not have, and it computes nothing.
What an observation is, which unit it carries and how it is aggregated
stays in weewx. Only the information about it moves here.

What gets registered is a type adapter on ``ValueHelper``. After that
``$day.outTemp.max`` yields a number in JSON mode, rounded the way the
skin intends anyway, and nobody has to write ``.raw`` or reach for
``$jsonize``.

As long as ``ct4-weewx`` is not a package of its own, the method is
attached to the class here after the fact. That is the current state,
not the goal: the method belongs to ``ValueHelper``, and that is where
it should eventually live.
"""

from __future__ import annotations

import re
from typing import Any

from ct4.adapters import Ct4Value
from ct4.declare import Declaration, Node

# '%.1f' becomes 1. weewx carries the decimal places in format strings
# because it formats; ct4 needs the number because it rounds.
DIGITS = re.compile(r"%[^%a-zA-Z]*\.(\d+)[eEfgG]")


def precision_of(helper: Any) -> int | None:
    """How many decimal places the skin intends for this value.

    ``None`` when that cannot be determined. Then ct4 does not round,
    and the full value goes into the JSON. That is the right default:
    too precise rather than silently truncated.
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
    """Attaches the adapter to weewx' ValueHelper."""
    from weewx.units import ValueHelper

    ValueHelper.__ct4_value__ = value_of


def aggregate_names() -> set[str]:
    """The aggregates weewx knows, from weewx' own tables.

    Not transcribed: a transcript would drift apart from weewx, and a
    skin would get complaints about names that do exist.
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
    """Registers which names a weewx skin may read.

    The structure has three levels and is closed exactly where typos
    happen: ``$day.outTemp.mx``. The middle part is an observation type
    that weewx only knows at runtime, so it stays open.
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

    # What a period offers besides observations. Read from the class so
    # that the list grows along with weewx.
    span_fields = {
        name: Node(open=True)
        for name in dir(weewx.tags.TimespanBinder)
        if not name.startswith("_")
    }
    span_fields["*"] = observation
    span = Node(fields=span_fields)

    # Only these names yield a period. $trend yields values, $span and
    # $days_ago are calls. Taking them along here would produce false
    # findings in the unmodified Seasons skin, and a false finding is
    # worse than a missed typo: it makes people ignore the tool.
    periods = ("hour", "day", "yesterday", "week", "month", "year",
               "rainyear", "season", "seasonsyear", "alltime")
    roots = {name: span for name in periods}
    # Whatever else TimeBinder offers stays open. That is the safe
    # direction: nothing is checked here rather than checked wrongly.
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
        source="weewx %s" % getattr(weewx, "__version__", "unknown"),
        roots=roots)
