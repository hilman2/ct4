"""PEP 750 template strings as context values in markup mode.

Built through the class API rather than written as t"..." literals,
so that the file imports on a Python that has none; the tests skip
there.
"""

from __future__ import annotations

import pytest

from ct4.lang import codegen
from ct4.markup import escape

templatelib = pytest.importorskip("string.templatelib")


def tstring(*parts):
    return templatelib.Template(*parts)


def interpolated(value, expression="x", conversion=None, spec=""):
    return templatelib.Interpolation(value, expression, conversion, spec)


def test_the_markup_stays_and_the_data_is_escaped():
    value = tstring("<b>", interpolated("<i>&</i>"), "</b>")
    assert str(escape.escape(value)) == "<b>&lt;i&gt;&amp;&lt;/i&gt;</b>"


def test_conversion_and_format_spec_apply_first():
    value = tstring("[", interpolated("a\"b", conversion="r"), "] ",
                    interpolated(3.14159, spec=".2f"))
    assert str(escape.escape(value)) == "[&#39;a&#34;b&#39;] 3.14"


def test_a_page_in_markup_mode_writes_it_that_way():
    source = "#mode markup\n<p>$note</p>\n"
    note = tstring("<em>", interpolated("<script>"), "</em>")
    assert codegen.render(source, [{"note": note}]) == \
        "<p><em>&lt;script&gt;</em></p>\n"


def test_a_nested_template_string_escapes_its_own_data():
    inner = tstring("<u>", interpolated("&"), "</u>")
    outer = tstring("<b>", interpolated(inner), "</b>")
    assert str(escape.escape(outer)) == "<b><u>&amp;</u></b>"
