"""render_async: the same page, with the event loop kept free."""

from __future__ import annotations

import asyncio

from ct4 import render


def test_the_page_is_the_same_and_the_loop_stays_free():
    ticks = []

    async def tick():
        for _ in range(3):
            ticks.append(1)
            await asyncio.sleep(0)

    async def both():
        source = "#for $i in $rows\n$i\n#end for\n"
        page, _ = await asyncio.gather(
            render.render_async(source, [{"rows": range(3)}]), tick())
        return page

    assert asyncio.run(both()) == "0\n1\n2\n"
    assert len(ticks) == 3


def test_an_error_comes_back_through_the_await():
    async def run():
        await render.render_async("$missing\n", [{}])

    try:
        asyncio.run(run())
    except Exception as error:                              # noqa: BLE001
        assert type(error).__name__ == "NotFound"
    else:
        raise AssertionError("the NotFound did not come through")
