"""Named searchLists for corpus cases.

A context that holds functions or instances cannot be stored as JSON.
The case therefore stores only a name, and the builder behind it rebuilds
the context when the check runs.

The builders import Cheetah only when they are called. Otherwise the
choice of implementation from ``ct4.impl`` would already be settled
before the command could make it.
"""

from __future__ import annotations

from typing import Any, Callable

from ct4.corpus.case import CT3_DEFAULT, FIXTURE, INLINE, Case

Builder = Callable[[], "list[Any]"]

BUILDERS: dict[str, Builder] = {}


def register(name: str) -> Callable[[Builder], Builder]:
    """Enters a builder under its name."""

    def decorate(builder: Builder) -> Builder:
        BUILDERS[name] = builder
        return builder

    return decorate


def build(case: Case) -> list[Any]:
    """Builds the searchList a case is rendered with."""
    if case.namespace == INLINE:
        return list(case.context)
    if case.namespace == FIXTURE:
        # A recorded searchList: one tree per namespace, in the order in
        # which the application searched them.
        from ct4.fixture.record import replay

        return [replay(tree) for tree in case.context]
    try:
        builder = BUILDERS[case.namespace]
    except KeyError:
        raise KeyError(
            "case %s asks for the unknown context %r"
            % (case.id, case.namespace)) from None
    return builder()


@register(CT3_DEFAULT)
def _ct3_default() -> list[Any]:
    """The context that almost every ct3 test case works with.

    It is fetched from the loaded Cheetah implementation, not copied.
    The fork and the installed ct3 each bring their own, and a difference
    between them is a finding, not an error in the test bench.
    """
    from Cheetah.Tests.SyntaxAndOutput import defaultTestNameSpace

    return [defaultTestNameSpace]
