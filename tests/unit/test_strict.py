"""#mode strict: Python semantics for the lookups, opt-in per file.

No autocalling, and a name the template bound itself is a Python
name. The gain is measured in the plan's section 12; the cases here
say what changes and what does not.
"""

from __future__ import annotations

import pytest
from Cheetah.Template import Template

from ct4 import modes, render
from ct4.lang import backend, codegen


class Row:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def shout(self):
        return self.name.upper()


def rendered(source, context):
    return codegen.render(source, [context])


def test_nothing_is_called_that_the_author_did_not_call():
    context = {"f": lambda: "called", "row": Row("a", 1)}
    assert rendered("$f $row.shout\n", context) == "called A\n"
    strict = rendered("#mode strict\n$f() $row.shout()\n", context)
    assert strict == "called A\n"
    loose = rendered("#mode strict\n$row.shout\n", context)
    assert loose.startswith("<bound method Row.shout")


def test_a_bound_name_is_a_python_name():
    source = "#mode strict\n#for $r in $rows\n$r.name=$r.value\n#end for\n"
    context = {"rows": [Row("a", 1), Row("b", 2)]}
    assert rendered(source, context) == "a=1\nb=2\n"
    code = codegen.generate(source).code
    assert "r.name" in code and "VFN(" not in code.split("for r in")[1]
    assert "VFFSL(" not in code


def test_a_root_is_found_without_autocalling_and_a_key_still_resolves():
    # The search list holds dicts and objects alike, and a weewx skin
    # reads $Extras.key out of a dict. The root goes through the name
    # mapper with autocalling off, so both spellings keep working.
    context = {"Extras": {"key": "v", "nested": {"deep": 1}},
               "row": Row("a", 1)}
    source = "#mode strict\n$Extras.key $Extras['key'] $Extras.nested.deep\n"
    assert rendered(source, context) == "v v 1\n"
    assert rendered("#mode strict\n$row.value\n", context) == "1\n"


def test_a_bound_dict_is_subscripted_not_dotted():
    source = "#mode strict\n#for $d in $rows\n$d['k']\n#end for\n"
    assert rendered(source, {"rows": [{"k": 1}]}) == "1\n"
    with pytest.raises(AttributeError):
        rendered("#mode strict\n#for $d in $rows\n$d.k\n#end for\n",
                 {"rows": [{"k": 1}]})


def test_set_binds_a_python_name():
    source = "#mode strict\n#set $row = $rows[0]\n$row.name $row.shout()\n"
    assert rendered(source, {"rows": [Row("x", 0)]}) == "x X\n"
    code = codegen.generate(source).code
    assert "row.name" in code and 'VFSL(SL,"row' not in code


def test_a_use_before_the_set_still_reaches_the_search_list():
    # Python semantics up to a point: before the #set the name is not
    # bound in the method, so the lookup goes to the search list, the
    # way ct3's frame walk would fall through. After it, it is local.
    source = "#mode strict\n$row.name\n#set $row = $rows[1]\n$row.name\n"
    context = {"row": Row("outer", 0), "rows": [Row("a", 1), Row("b", 2)]}
    assert rendered(source, context) == "outer\nb\n"


def test_a_definition_parameter_is_a_python_name():
    source = ("#mode strict\n#def show($r, $unit='C')\n$r.name/$unit\n"
              "#end def\n$show($rows[0])$show($rows[1], 'F')\n")
    context = {"rows": [Row("a", 1), Row("b", 2)]}
    assert rendered(source, context) == "a/C\nb/F\n\n"
    code = codegen.generate(source).code
    assert 'VFSL(SL,"r' not in code and 'VFSL(SL,"unit' not in code


def test_the_declaration_line_is_not_output():
    assert rendered("#mode strict\nhi\n", {}) == "hi\n"
    assert rendered("## licence\n\n#mode strict\nhi\n", {}) == "\nhi\n"


def test_markup_and_strict_combine():
    source = "#mode markup strict\n<p>$x $f()</p>\n"
    context = {"x": "<b>", "f": lambda: "&"}
    assert rendered(source, context) == "<p>&lt;b&gt; &amp;</p>\n"
    assert modes.declared(source) == {"markup", "strict"}


def test_an_unknown_mode_word_is_refused():
    with pytest.raises(codegen.Unsupported) as error:
        codegen.generate("#mode stric\n")
    assert "stric" in str(error.value)


def test_the_generator_and_nothing_else_compiles_it():
    # Through Template.compile with the generator installed, which is
    # weewx's path; and a refusal is an error, not a fall back to a
    # compiler that would autocall.
    backend.install()
    try:
        klass = Template.compile(source="#mode strict\n$f\n", useCache=False,
                                 cacheCompilationResults=False)
        assert str(klass(searchList=[{"f": lambda: "x"}]).respond()) \
            .startswith("<function")
        with pytest.raises(backend.StrictRefused):
            Template.compile(source="#mode strict\n#compiler-settings\n"
                                    "useNameMapper = False\n"
                                    "#end compiler-settings\n",
                             useCache=False, cacheCompilationResults=False)
    finally:
        backend.uninstall()


def test_render_source_routes_a_strict_template_to_the_generator():
    text = render.render_source("#mode strict\n$f\n", [{"f": lambda: "x"}])
    assert text.startswith("<function")
    assert render.mode_of("#mode strict\nx\n") == render.TEXT
