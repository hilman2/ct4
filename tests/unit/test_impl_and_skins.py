"""Choosing the implementation, and harvesting the skins."""

from __future__ import annotations

import pytest

from ct4 import impl
from ct4.corpus import skins
from ct4.corpus.case import COMPILE


def test_an_unknown_implementation_is_rejected():
    with pytest.raises(ValueError):
        impl.select("something")


def test_selecting_after_the_import_is_rejected():
    # A late call would have no effect, and no effect is the worst
    # outcome here: the run then measures the wrong implementation and
    # still reports green.
    import Cheetah                                     # noqa: F401

    with pytest.raises(RuntimeError):
        impl.select(impl.INSTALLED)


def test_the_description_names_path_and_c_extension():
    description = impl.describe()
    assert "C-NameMapper=" in description
    assert "Cheetah" in description


def test_the_skin_harvest_finds_both_extensions(tmp_path):
    (tmp_path / "page.html.tmpl").write_text("$aStr", encoding="utf-8")
    (tmp_path / "part.inc").write_text("$anInt", encoding="utf-8")
    (tmp_path / "not-a-template.css").write_text("body{}", encoding="utf-8")

    cases, skipped = skins.harvest(tmp_path, "sample")

    assert not skipped
    assert {c.id for c in cases} == {"sample/page.html.tmpl",
                                     "sample/part.inc"}
    assert all(c.kind == COMPILE and c.expected for c in cases)


def test_an_uncompilable_template_is_counted_not_stored(tmp_path):
    (tmp_path / "broken.tmpl").write_text("#for x in\n", encoding="utf-8")
    cases, skipped = skins.harvest(tmp_path, "sample")
    assert cases == []
    assert sum(skipped.values()) == 1


def test_subdirectories_are_included(tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    (deep / "page.tmpl").write_text("$aStr", encoding="utf-8")
    cases, _ = skins.harvest(tmp_path, "sample")
    assert [c.id for c in cases] == ["sample/a/b/page.tmpl"]
