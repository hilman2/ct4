"""Choice of the Cheetah implementation that is measured against.

The test bench compares two implementations, both of which are imported
under the name ``Cheetah``: the fork in this repository and the ct3
installed by pip. Which of the two wins depends solely on whether the
repository root is on ``sys.path``. That is why the choice is made here,
once and explicitly, instead of being left to the caller and their
environment variables.
"""

from __future__ import annotations

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORK = "fork"
INSTALLED = "installed"
CHOICES = (FORK, INSTALLED)


def select(impl: str) -> None:
    """Determines which Cheetah package a later import will find.

    Must run before anything has imported ``Cheetah``. With
    ``installed``, the repository root drops out of ``sys.path``, so that
    only the installed ct3 is left. The ``ct4`` package is already loaded
    at that point and survives it.
    """
    if impl not in CHOICES:
        raise ValueError("unknown implementation: %s" % impl)
    if "Cheetah" in sys.modules:
        raise RuntimeError(
            "Cheetah is already imported; select() came too late")
    if impl == INSTALLED:
        sys.path[:] = [
            entry for entry in sys.path
            if os.path.abspath(entry or os.curdir) != REPO_ROOT
        ]


def describe() -> str:
    """Version and file path of the Cheetah that actually got loaded.

    The path is reported alongside because the version alone does not
    reveal whether the fork or the installed package won: during P0 both
    report the same version series.
    """
    import Cheetah
    from Cheetah import NameMapper
    from Cheetah.Version import Version

    return "%s  %s  C-NameMapper=%s" % (
        Version, os.path.dirname(Cheetah.__file__), NameMapper.C_VERSION)
