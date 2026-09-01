"""What the markup-mode runtime has to guarantee.

Two promises are measured here. The first is the escape table itself:
exactly five characters, numeric references and not ``&quot;``, so that
one escape is correct in element text and in an attribute quoted either
way. The second is the order in which the ``__html__`` protocol and the
application's filter run, and that one is the reason this module exists
in the shape it has: weewx's AssureUnicode calls ``str()`` on anything
that is not a ``str``, so an object that only declares ``__html__``
would be flattened into its repr if the filter went first.

The negative assertions carry most of the weight. Where the composition
goes wrong the output has to come out escaped twice and visibly broken,
never raw and silently exploitable, and the test named after that is
the one to read before changing :func:`ct4.markup.escape.write_escaped`.
"""

from __future__ import annotations

import pytest

from ct4.markup.escape import (
    Markup,
    MarkupError,
    escape,
    quoted,
    safe,
    write_escaped,
    write_verbatim,
)

WHERE = "index.html.tmpl:12:5 (inside <script>)"

# All five characters of the table in one string, pieced together so
# neither quote has to be escaped here.
ALL_FIVE = "&<>" + "'" + '"'


def keep(val, **kw):
    """A filter that hands the value through as text."""
    return str(val)


def assure_unicode(val, **kw):
    """Stand-in for weewx's AssureUnicode.

    Returns str values unchanged and calls str() on everything else,
    which is the behaviour a bare __html__ object cannot survive.
    """
    return val if isinstance(val, str) else str(val)


class Prepared:
    """Markup that is not a string: the case AssureUnicode destroys."""

    def __html__(self):
        return "<b>x</b>"


class ForeignStr(str):
    """A foreign markup type that is a str subclass, like Markup."""

    def __html__(self):
        return self


# -- The escape table ------------------------------------------------

def test_escape_replaces_exactly_the_five_characters():
    result = escape('a<b>&"c" 1')
    assert result == "a&lt;b&gt;&amp;&#34;c&#34; 1"
    assert isinstance(result, Markup)


def test_escape_writes_numeric_references_for_both_quotes():
    assert escape("'") == "&#39;"
    assert escape('"') == "&#34;"
    assert escape(ALL_FIVE) == "&amp;&lt;&gt;&#39;&#34;"
    assert "&quot;" not in escape(ALL_FIVE)
    assert "&apos;" not in escape(ALL_FIVE)


def test_escape_is_idempotent_because_its_result_carries_html():
    assert escape(escape("<")) == "&lt;"
    assert escape(escape("<")) != "&amp;lt;"


def test_escape_returns_the_markup_of_a_type_that_declares_it():
    assert escape(Prepared()) == "<b>x</b>"
    assert isinstance(escape(Prepared()), Markup)


def test_escape_looks_up_html_on_the_type_and_not_the_instance():
    result = escape({"__html__": "x"})
    assert result != "x"
    assert "&#39;__html__&#39;" in result


def test_escape_converts_anything_that_is_not_a_string():
    assert escape(1) == "1"
    assert escape(1.5) == "1.5"
    assert escape([1, "<"]) == "[1, &#39;&lt;&#39;]"


def test_safe_marks_without_checking_anything():
    marked = safe("<b>x</b>")
    assert isinstance(marked, Markup)
    assert escape(marked) == "<b>x</b>"


# -- write_escaped ---------------------------------------------------

def test_write_escaped_escapes_what_the_filter_returns():
    assert write_escaped("<b>", keep) == "&lt;b&gt;"


def test_assure_unicode_leaves_markup_alone_because_html_resolves_first():
    # The pair that decides the order of the steps. A Markup is a str
    # subclass and survives AssureUnicode; the bare object only does
    # because write_escaped wrapped it before the filter saw it.
    assert write_escaped(Markup("<b>x</b>"), assure_unicode) == "<b>x</b>"
    assert write_escaped(Prepared(), assure_unicode) == "<b>x</b>"


def test_a_filter_that_stringifies_makes_the_output_escaped_twice_never_raw():
    result = write_escaped(Prepared(), keep)
    assert result == "&lt;b&gt;x&lt;/b&gt;"
    assert "<b>" not in result


def test_write_escaped_passes_raw_expr_through_untouched():
    seen = {}

    def recording(val, **kw):
        seen.update(kw)
        seen["count"] = len(kw)
        return val

    write_escaped("x", recording, rawExpr="$x")
    assert seen["rawExpr"] == "$x"
    assert seen["count"] == 1


# -- write_verbatim --------------------------------------------------

def test_write_verbatim_returns_a_value_that_declares_itself_quoted():
    # And only that. __html__ means HTML-safe, which is what its
    # producers mean by it, and Markup("</script>") is correct HTML
    # that ends a script block. Taking it as proof here would
    # accept exactly the value that breaks out.
    assert write_verbatim(quoted("<b>x</b>"), keep, WHERE) == "<b>x</b>"
    assert write_verbatim(quoted("a<b"), keep, WHERE) == "a<b"
    with pytest.raises(MarkupError):
        write_verbatim(Markup("</script>"), keep, WHERE)
    with pytest.raises(MarkupError):
        write_verbatim(Prepared(), keep, WHERE)


@pytest.mark.parametrize("value, name", [(1, "int"),
                                         ("plain", "str"),
                                         (None, "NoneType")])
def test_write_verbatim_refuses_a_value_without_html(value, name):
    with pytest.raises(MarkupError) as caught:
        write_verbatim(value, keep, WHERE)
    message = str(caught.value)
    assert WHERE in message
    assert f"(got {name})" in message


def test_write_verbatim_keeps_a_result_the_filter_stringified():
    # The value proved itself before the filter ran. What the filter
    # does to its own values afterwards is not this module's business.
    assert write_verbatim(quoted("<b>x</b>"), keep, WHERE) == "<b>x</b>"


# -- The protocol, not the class -------------------------------------

@pytest.mark.parametrize("value", [Prepared(), ForeignStr("<b>x</b>")])
def test_a_foreign_markup_type_is_accepted_where_html_is_the_claim(value):
    # write_escaped takes anything that declares __html__, because
    # there the claim and the position agree. write_verbatim does not,
    # because there they do not: see the test above.
    assert write_escaped(value, assure_unicode) == "<b>x</b>"
    with pytest.raises(MarkupError):
        write_verbatim(value, assure_unicode, WHERE)


def test_markup_error_is_not_caught_by_a_broad_value_error_handler():
    assert issubclass(MarkupError, Exception)
    assert not issubclass(MarkupError, ValueError)
