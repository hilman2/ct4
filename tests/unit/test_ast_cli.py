"""ct4 ast: the block tree as data, for tools that read a template."""

from __future__ import annotations

import json

from ct4 import cli


def joined(node):
    """The source put back together from the document, in order."""
    if node["kind"] == "template":
        return "".join(joined(child) for child in node["children"])
    if node["kind"] == "block":
        return node["head"] + "".join(joined(c) for c in node["children"])
    return node.get("text", "")


def test_the_document_holds_the_whole_source(tmp_path, capsys):
    source = "#if $x\nyes $y\n#end if\ntail\n"
    path = tmp_path / "p.tmpl"
    path.write_text(source, encoding="utf-8")
    assert cli.main(["ast", str(path), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["kind"] == "template"
    block = document["children"][0]
    assert (block["kind"], block["name"], block["line"], block["column"],
            block["head"]) == ("block", "if", 1, 1, "#if $x\n")
    assert [child["kind"] for child in block["children"]] == \
        ["text", "placeholder", "text", "directive"]
    assert joined(document) == source


def test_the_text_form_names_kind_place_and_text(tmp_path, capsys):
    path = tmp_path / "p.tmpl"
    path.write_text("#for $i in [1]\n$i\n#end for\n", encoding="utf-8")
    assert cli.main(["ast", str(path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("template")
    assert lines[1].startswith("  block      for          1:1")
    assert any(line.strip().startswith("placeholder") for line in lines)
    assert lines[-1].strip().startswith("directive  end")


def test_a_registered_block_is_a_block_here_too(tmp_path, capsys):
    (tmp_path / "ct4.toml").write_text('[blocks]\nbox = "m:f"\n',
                                       encoding="utf-8")
    path = tmp_path / "p.tmpl"
    path.write_text("#box\nx\n#end box\n", encoding="utf-8")
    assert cli.main(["ast", str(path), "--json"]) == 0
    block = json.loads(capsys.readouterr().out)["children"][0]
    assert (block["kind"], block["name"]) == ("block", "box")


def test_an_open_block_is_an_error_with_its_place(tmp_path, capsys):
    path = tmp_path / "p.tmpl"
    path.write_text("#for $i in [1]\n$i\n", encoding="utf-8")
    assert cli.main(["ast", str(path)]) == 1
    assert "line 1" in capsys.readouterr().err
