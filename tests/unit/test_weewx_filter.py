"""Der nachgebildete weewx-Filter gegen das Original.

Laeuft nur, wo weewx installiert ist, also im Aufzeichnungs-Container.
Eine Kopie fremden Verhaltens ist nur so lange richtig, wie jemand
nachmisst.
"""

from __future__ import annotations

import pytest

from ct4.fixture.filters import WeewxAssureUnicode

weewx_generator = pytest.importorskip("weewx.cheetahgenerator")


class Sperrig:
    def __str__(self):
        raise AttributeError("kein Wert")


@pytest.mark.parametrize("wert", [
    None, "", "text", b"bytes", 0, 1, 1.5, True, Sperrig(),
])
def test_nachbau_stimmt_mit_weewx_ueberein(wert):
    original = weewx_generator.AssureUnicode()
    nachbau = WeewxAssureUnicode()
    argumente = {"rawExpr": "$roh"}
    assert nachbau.filter(wert, **argumente) == \
        original.filter(wert, **argumente)
