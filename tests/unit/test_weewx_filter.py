"""The reimplemented weewx filter against the original.

Only runs where weewx is installed, that is, in the recording
container. A copy of someone else's behaviour stays right only as
long as somebody keeps measuring it.
"""

from __future__ import annotations

import pytest

from ct4.fixture.filters import WeewxAssureUnicode

weewx_generator = pytest.importorskip("weewx.cheetahgenerator")


class Unstringable:
    def __str__(self):
        raise AttributeError("no value")


@pytest.mark.parametrize("value", [
    None, "", "text", b"bytes", 0, 1, 1.5, True, Unstringable(),
])
def test_the_replica_agrees_with_weewx(value):
    original = weewx_generator.AssureUnicode()
    replica = WeewxAssureUnicode()
    arguments = {"rawExpr": "$raw"}
    assert replica.filter(value, **arguments) == \
        original.filter(value, **arguments)
