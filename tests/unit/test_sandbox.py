"""The sandbox: what it refuses, and that a hang is a reported failure."""

from __future__ import annotations

import json

import pytest

from ct4 import cli, sandbox
from ct4.lang import codegen, tree


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setenv(sandbox.ENV, "1")


def refused(source):
    with pytest.raises(sandbox.SandboxViolation) as error:
        codegen.generate(source)
    return str(error.value)


@pytest.mark.parametrize("source, named", [
    ("#import os\n$os.getcwd()\n", "#import"),
    ("#from os import path\n", "#from"),
    ("#extends mymodule\n", "#extends"),
    ("#set module $x = 1\n", "#set module"),
    ("<% write('x') %>\n", "PSP"),
    ("#include $name\n", "#include"),
    ("$x.__class__\n", "__class__"),
    ("$open('/etc/passwd')\n", "open"),
    ("#if $getattr($x, 'y')\n#end if\n", "getattr"),
    ("#set $y = $__import__('os')\n", "__import__"),
])
def test_what_reaches_outside_is_refused(guarded, source, named):
    message = refused(source)
    assert named in message
    assert "line 1" in message


def test_a_literal_include_is_allowed_and_checked_when_compiled(
        guarded, tmp_path, monkeypatch):
    # The include is compiled at render time through the same
    # generator, so its own #import is refused there, not skipped.
    (tmp_path / "inner.inc").write_text("#import os\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    made = codegen.generate("#include 'inner.inc'\n")
    assert made is not None
    from ct4.lang import backend

    backend.install()
    try:
        with pytest.raises(sandbox.SandboxViolation):
            codegen.render("#include 'inner.inc'\n", [{}])
    finally:
        backend.uninstall()


def test_an_ordinary_page_renders_the_same(guarded):
    source = "#for $r in $rows\n<td>$r.name</td>\n#end for\n"
    context = [{"rows": [{"name": "a"}, {"name": "b"}]}]
    assert codegen.render(source, context) == "<td>a</td>\n<td>b</td>\n"


def test_nothing_is_refused_outside_the_sandbox():
    made = codegen.generate("#import os\n$os.sep\n")
    assert made is not None
    sandbox.check(tree.parse("$x\n"))


def test_the_command_renders_in_a_child_and_stops_a_hang(
        tmp_path, capsysbinary):
    path = tmp_path / "page.tmpl"
    path.write_text("hello $name\n", encoding="utf-8")
    context = tmp_path / "c.json"
    context.write_text(json.dumps({"name": "sandbox"}), encoding="utf-8")
    assert cli.main(["render", str(path), "--sandbox",
                     "--context", str(context)]) == 0
    assert capsysbinary.readouterr().out == b"hello sandbox\n"

    path.write_text("#while True\n#end while\n", encoding="utf-8")
    assert cli.main(["render", str(path), "--sandbox", "--timeout", "2"]) \
        == sandbox.TIMED_OUT
    assert b"did not finish" in capsysbinary.readouterr().err

    path.write_text("#import os\n", encoding="utf-8")
    assert cli.main(["render", str(path), "--sandbox"]) == 1
    assert b"#import is not rendered in the sandbox" in \
        capsysbinary.readouterr().err
