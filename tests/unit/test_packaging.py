"""What the built package promises must be in the built package.

The wheel declared a console script ct4 and a plugin entry point
pointing at ct4.plugins.weewx_adapter, and shipped neither: the package
list was inherited from ct3 and never learned about ct4. Nothing failed
anywhere. The tests import from the source tree, the corpus runs from
the source tree, and only somebody installing it would have found out.

So the check is on the metadata, not on a run: every module named in
[project.scripts] and in the entry points has to be covered by what
[tool.setuptools] ships, and has to exist.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"

tomllib = pytest.importorskip(
    "tomllib", reason="reading pyproject needs Python 3.11 or newer")


def config() -> dict:
    if not PYPROJECT.exists():
        pytest.skip("pyproject.toml is not reachable from here")
    with open(PYPROJECT, "rb") as handle:
        return tomllib.load(handle)


def targets(data: dict) -> list[tuple[str, str]]:
    """Every (where it was declared, dotted module) the metadata names."""
    project = data.get("project", {})
    found = []
    for name, target in project.get("scripts", {}).items():
        found.append(("scripts.%s" % name, target.split(":")[0]))
    for group, entries in project.get("entry-points", {}).items():
        for name, target in entries.items():
            found.append(("entry-points.%s.%s" % (group, name),
                          target.split(":")[0]))
    return found


def shipped_patterns(data: dict) -> list[str]:
    tool = data.get("tool", {}).get("setuptools", {})
    find = tool.get("packages", {})
    if isinstance(find, list):
        return find
    return find.get("find", {}).get("include", [])


def covered(module: str, patterns: list[str]) -> bool:
    """Whether a dotted module falls under one of the include patterns."""
    top = module.split(".")[0]
    for pattern in patterns:
        if pattern.endswith("*"):
            if top.startswith(pattern[:-1]):
                return True
        elif module == pattern or module.startswith(pattern + "."):
            return True
    return False


def test_every_declared_module_is_shipped():
    data = config()
    patterns = shipped_patterns(data)
    assert patterns, "no package list found in pyproject.toml"
    missing = [(where, module) for where, module in targets(data)
               if not covered(module, patterns)]
    assert not missing, (
        "declared but not shipped: %s (packages: %s)" % (missing, patterns))


def test_every_declared_module_imports():
    # A pattern can cover a module that does not exist. The command
    # would still fail, just one step later.
    data = config()
    sys.path.insert(0, str(ROOT))
    try:
        broken = []
        for where, module in targets(data):
            try:
                importlib.import_module(module)
            except ImportError as error:
                broken.append("%s -> %s (%s)" % (where, module, error))
    finally:
        sys.path.remove(str(ROOT))
    assert not broken, "declared but not importable: %s" % broken


def test_the_declarations_are_found_beside_the_code():
    # They were beside the repository root, which resolves to
    # site-packages once installed and is not there. "ct4 check" then
    # ran against nothing and reported no findings, which reads exactly
    # like a clean template.
    from ct4.cli import DECLARATIONS, load_declarations

    assert DECLARATIONS.is_dir(), DECLARATIONS
    assert load_declarations([]), "no declaration was loaded"


def test_the_declarations_are_declared_as_package_data():
    # Finding them in the source tree proves nothing about the wheel.
    data = config()
    package_data = data.get("tool", {}).get("setuptools", {}) \
                       .get("package-data", {})
    assert any(pattern.startswith("declarations/")
               for pattern in package_data.get("ct4", [])), package_data


def test_the_check_can_fail():
    # A guard that cannot fail says nothing. This is the shape the bug
    # had: a target outside every pattern.
    patterns = ["Cheetah*", "ct4*"]
    assert covered("ct4.cli", patterns)
    assert covered("ct4.plugins.weewx_adapter", patterns)
    assert not covered("ct4.cli", ["Cheetah", "Cheetah.Utils"])
