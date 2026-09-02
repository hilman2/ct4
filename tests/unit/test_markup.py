"""Markup mode from the declared line to the bytes on the page.

The three modules underneath this have their own tests: the escape
table in test_markup_escape.py, the placing of a placeholder in
test_markup_scan.py, the declaration and its cut in
test_markup_mode.py. What is left, and what stands here, is whether
they are wired together into a mode: whether a page a person would
actually write comes out escaped where it must be, whether a value
that has not proved itself can reach a position that cannot be
escaped, and whether the compiler, ``ct4.check`` and ``ct4.build`` all
answer with the same list.

The assertion that outranks all of those is the one at the bottom.
Text mode must not have moved, and here that is asserted on the shape
of the generated Python: no import, no wrapper, the same call to the
filter with the same single keyword. The corpus and the three fuzz
instruments hold the same line over 2026 real cases; this file holds
it where it can name the reason.
"""

from __future__ import annotations

import ast
import json

import pytest

from ct4 import build, check
from ct4.lang import backend, codegen
from ct4.markup import scan
from ct4.markup.escape import Markup, MarkupError, quoted, safe


def render(source, values=None, **keywords):
    """One template, one search list, the text it writes."""
    return codegen.render(source, [values or {}], **keywords)


# A page of the kind this mode exists for: a heading, a loop over rows,
# an attribute, a URL, and a value with three of the five special
# characters in it.
PAGE = """\
#mode markup
<!DOCTYPE html>
<title>$station</title>
<h1>$station</h1>
<table>
#for $row in $rows
<tr><td title="$row.note">$row.name</td></tr>
#end for
</table>
<p><a href="$link">more</a></p>
"""

VALUES = {
    "station": 'Haus & Garten <"Nord">',
    "rows": [{"name": "<b>Regen</b>", "note": 'er sagte "ja"'}],
    "link": "/tag.html?a=1&b=2",
}


# -- A page somebody would write -------------------------------------

def test_a_page_comes_out_escaped_where_it_has_to_be():
    assert render(PAGE, VALUES) == (
        "<!DOCTYPE html>\n"
        "<title>Haus &amp; Garten &lt;&#34;Nord&#34;&gt;</title>\n"
        "<h1>Haus &amp; Garten &lt;&#34;Nord&#34;&gt;</h1>\n"
        "<table>\n"
        '<tr><td title="er sagte &#34;ja&#34;">&lt;b&gt;Regen&lt;/b&gt;'
        "</td></tr>\n"
        "</table>\n"
        '<p><a href="/tag.html?a=1&amp;b=2">more</a></p>\n')


def test_the_declaration_line_is_not_on_the_page():
    # The failure this rules out is the one a fallback to ct3 would
    # produce: the page renders, the line stands in it, and nothing is
    # escaped.
    assert "#mode" not in render(PAGE, VALUES)


def test_the_same_page_in_text_mode_is_ct3s_page():
    """Without the declaration it is an ordinary template, and stays one.

    Held against ct3 itself rather than against a literal written down
    here, because the promise is byte equality with ct3.
    """
    from Cheetah.Template import Template

    source = PAGE[PAGE.index("\n") + 1:]
    klass = Template.compile(source=source, useCache=False,
                             cacheCompilationResults=False)
    template = klass(searchList=[VALUES])
    try:
        expected = str(template.respond())
    finally:
        template.shutdown()
    assert render(source, VALUES) == expected
    assert "<b>Regen</b>" in expected


def test_a_quote_cannot_break_out_of_an_attribute():
    source = '#mode markup\n<img alt="$x">\n'
    written = render(source, {"x": '" onerror="alert(1)'})
    assert written == '<img alt="&#34; onerror=&#34;alert(1)">\n'


def test_a_single_quoted_attribute_is_escaped_too():
    # markupsafe's table and not html.escape's: a value escaped for a
    # double quote alone is still injectable out of a single-quoted
    # attribute.
    source = "#mode markup\n<img alt='$x'>\n"
    assert render(source, {"x": "' onerror='x"}) == \
        "<img alt='&#39; onerror=&#39;x'>\n"


# -- Getting an unescaped value through ------------------------------

def test_a_value_cannot_reach_a_script_without_proving_itself():
    """The attempt this mode exists to refuse.

    An HTML escape inside a <script> does not merely fail to help. A
    character reference is not decoded in raw text, so an escaped "<"
    reaches the JavaScript engine as the four characters "&lt;". So
    the value has to say it is markup, and where it does not the
    render stops with the position in the message.
    """
    source = '#mode markup\n<script>var x = "$x";</script>\n'
    with pytest.raises(MarkupError) as raised:
        render(source, {"x": "</script><script>alert(1)"}, file="index.tmpl")
    message = str(raised.value)
    assert "index.tmpl:2:18" in message
    assert "inside <script>" in message
    assert "quoted()" in message


def test_a_value_that_says_it_is_quoted_goes_through_verbatim():
    source = "#mode markup\n<script>var x = $x;</script>\n"
    assert render(source, {"x": quoted('"ok"')}) == \
        '<script>var x = "ok";</script>\n'


def test_prepared_markup_is_not_escaped_a_second_time():
    source = "#mode markup\n<p>$x</p>\n"
    assert render(source, {"x": safe("<b>bold</b>")}) == "<p><b>bold</b></p>\n"


def test_the_protocol_is_read_off_the_type_and_not_the_instance():
    class Cell:
        def __html__(self):
            return "<td>7</td>"

    source = "#mode markup\n<tr>$x</tr>\n"
    assert render(source, {"x": Cell()}) == "<tr><td>7</td></tr>\n"
    # A mapping carrying the name as a key is data, not markup.
    assert render(source, {"x": {"__html__": "<td>7</td>"}}) == \
        "<tr>{&#39;__html__&#39;: &#39;&lt;td&gt;7&lt;/td&gt;&#39;}</tr>\n"


def test_a_value_of_none_still_writes_nothing():
    # ct3's guard, untouched: the filter never sees a None and neither
    # does the escape.
    assert render("#mode markup\n<p>$x</p>\n", {"x": None}) == "<p></p>\n"


# -- The URL head, which is escaped and not vetted -------------------

def test_a_url_is_escaped_as_an_attribute_and_the_scheme_is_not_checked():
    """Said out loud rather than half-solved.

    No character in "javascript:alert(1)" is HTML-special, so escaping
    cannot stop it, and neither jinja2 nor markupsafe guards it. The
    value is escaped like any attribute value and the position is
    reported as a warning; there is no scheme allowlist in this
    version.
    """
    source = '#mode markup\n<a href="$x">k</a>\n'
    assert render(source, {"x": "javascript:alert(1)"}) == \
        '<a href="javascript:alert(1)">k</a>\n'
    codes = {finding.code for finding in check.check_source(source)}
    assert "CT4401" in codes


# -- The filter the application brought ------------------------------

def test_the_filter_still_gets_its_value_and_its_rawExpr():
    """The composition, on the shape weewx actually ships.

    weewx replaces the whole filter library, so the escape can never be
    one of its filters and can never be an argument to one either. It
    wraps the filter's result, and rawExpr keeps exactly the shape it
    has in text mode: one keyword, ct3's.
    """
    from Cheetah.Filters import Filter

    seen = []

    class Recording(Filter):
        def filter(self, value, **keywords):
            seen.append((value, keywords.get("rawExpr")))
            return str(value)

    written = render("#mode markup\n<p>$x</p>\n", {"x": "<b>"},
                     output_filter=Recording)
    assert written == "<p>&lt;b&gt;</p>\n"
    assert seen == [("<b>", "$x")]


def test_the_protocol_is_resolved_before_the_filter_runs():
    """Which is what makes a filter that stringifies survivable.

    weewx's AssureUnicode calls str() on anything that is not a str,
    and would flatten a bare object that only declares __html__ into
    its repr. A Markup is a str subclass and comes through it as
    itself, so the filter sees the markup and not the object.
    """
    from Cheetah.Filters import Filter

    class Stringifying(Filter):
        def filter(self, value, **keywords):
            return str(value)

    class Cell:
        def __html__(self):
            return "<td>7</td>"

    seen = []

    class Watching(Filter):
        def filter(self, value, **keywords):
            seen.append(type(value))
            return value

    render("#mode markup\n<tr>$x</tr>\n", {"x": Cell()},
           output_filter=Watching)
    assert seen == [Markup]
    # And where the filter does flatten it, the result is escaped a
    # second time: visibly wrong on the page, never unescaped.
    assert render("#mode markup\n<tr>$x</tr>\n", {"x": Cell()},
                  output_filter=Stringifying) == \
        "<tr>&lt;td&gt;7&lt;/td&gt;</tr>\n"


# -- Positions with no site at all -----------------------------------

@pytest.mark.parametrize("source", [
    "#mode markup\n#echo $x\n",
    "#mode markup\n#if $x then $x else $x\n",
])
def test_a_write_the_scan_never_saw_is_refused_as_well(source):
    # #echo and the one-line #if write an expression that stood in a
    # directive's arguments, so the scan has no site for it. No site is
    # a refusal and not a guess.
    with pytest.raises(MarkupError):
        render(source, {"x": "<b>"})
    # The directive eats its own line ending, so the value is all there
    # is. That is ct3's rule and markup mode does not touch it.
    assert render(source, {"x": quoted("<b>")}) == "<b>"


def test_a_def_body_belongs_to_wherever_it_is_called():
    source = ("#mode markup\n#def graph\n<p>$x</p>\n#end def\n"
              "<div>$graph</div>\n")
    with pytest.raises(MarkupError) as raised:
        render(source, {"x": "1"})
    assert "in the body of #def graph" in str(raised.value)


# -- Refusing the whole file -----------------------------------------

@pytest.mark.parametrize("source, reason", [
    ("#mode markup\n<![CDATA[$x]]>\n", "CDATA"),
    ("#mode markup\n<p class=\"$x\n", "does not end in element text"),
    ("#mode markup\n#filter WebSafe\n$x\n#end filter\n", "#filter"),
])
def test_a_file_the_scan_cannot_be_trusted_over_is_refused_whole(
        source, reason):
    with pytest.raises(scan.ScanRefused) as raised:
        codegen.generate(source)
    assert reason in str(raised.value)


def test_markup_mode_never_falls_back_to_ct3():
    """The fallback that would be worse than the refusal.

    ct3 knows nothing about the declaration, so a template it took
    would print the line into the page and escape nothing: the wrong
    page and the missing escape at once.
    """
    from Cheetah.Template import Template

    counts = backend.install()
    try:
        good = Template.compile(source="#mode markup\n<p>$x</p>\n",
                                useCache=False,
                                cacheCompilationResults=False)
        template = good(searchList=[{"x": "<b>"}])
        try:
            assert str(template.respond()) == "<p>&lt;b&gt;</p>\n"
        finally:
            template.shutdown()
        with pytest.raises(backend.MarkupRefused):
            Template.compile(source="#mode markup\n<![CDATA[$x]]>\n",
                             useCache=False, cacheCompilationResults=False)
    finally:
        backend.uninstall()
    assert counts.taken == 1


def test_a_text_template_the_generator_refuses_still_falls_back():
    # The other half of the same branch, and the one 46 corpus skins
    # depend on.
    from Cheetah.Template import Template

    counts = backend.install()
    try:
        klass = Template.compile(
            source="#compiler-settings\n"
                   "gobbleWhitespaceAroundMultiLineComments=False\n"
                   "#end compiler-settings\nplain\n",
            useCache=False, cacheCompilationResults=False)
        template = klass(searchList=[{}])
        try:
            assert "plain" in str(template.respond())
        finally:
            template.shutdown()
    finally:
        backend.uninstall()
    assert counts.fell_back == 1


def test_the_mode_is_read_out_of_the_source_and_not_out_of_the_call():
    # Passing the mode asserts what the source says; it cannot decide
    # it. Anything else would be a second place for the answer to live.
    with pytest.raises(codegen.Unsupported):
        codegen.generate("<p>$x</p>\n", mode=codegen.MARKUP_MODE)
    with pytest.raises(codegen.Unsupported):
        codegen.generate("<p>$x</p>\n", mode="html")
    made = codegen.generate("#mode markup\n<p>$x</p>\n")
    assert made.markup is not None


# -- The whole list, before a page is built --------------------------

# No #include here, and that is the rule rather than an oversight: an
# included file can open a tag and the includer cannot know, so a
# markup template may not hold one at all. The case below pins that.
CHECKED = """\
#mode markup
<h1>$title</h1>
<a href="$link">k</a>
<script>var x = $x;</script>
#echo $y
"""


def test_check_lists_every_decision_the_render_will_make():
    found = check.check_source(CHECKED, "index.html.tmpl")
    assert [(one.code, one.line) for one in found] == [
        ("CT4401", 3), ("CT4400", 4), ("CT4400", 5)]
    assert all(one.file == "index.html.tmpl" for one in found)


@pytest.mark.parametrize("source", [
    '#mode markup\n<p>$x</p>\n#include "foot.inc"\n',
    "#mode markup\n<p>$x</p>\n#raw\n<a href=\n#end raw\n",
    "#mode markup\n<p>$x</p>\n<%= $x %>\n",
    "#mode markup\n#extends Base\n<p>$x</p>\n",
])
def test_a_construct_the_scan_walks_past_refuses_the_file(source):
    # Each of these was a live hole, found by attacking the finished
    # mode. A #raw body is written out untouched and never scanned, so
    # a tag opened in one leaves the machine in element text while the
    # browser is inside an unquoted attribute; a PSP value write does
    # not go through the placeholder path at all; an include and a base
    # class both bring markup the scan never sees. The scan cannot
    # follow any of them, so the file is refused whole.
    found = check.check_source(source, "page.tmpl")
    assert [one.code for one in found] == ["CT4402"], found


def test_the_lines_are_the_authors_and_not_the_compilers():
    # The declaration is cut before anything parses, so every line
    # after it has moved up by one inside the compiler. A message that
    # points at the line above the placeholder is worse than none.
    found = check.check_source(CHECKED, "index.html.tmpl")
    lines = CHECKED.splitlines()
    for one in found:
        assert "$" in lines[one.line - 1] or "#include" in lines[one.line - 1]


def test_check_reports_a_refused_file_as_an_error_and_says_why():
    source = "#mode markup\n<p>$x</p>\n<![CDATA[$x]]>\n"
    found = check.check_source(source, "feed.tmpl")
    assert len(found) == 1
    assert found[0].code == "CT4402"
    assert found[0].severity == "error"
    assert "text mode" in found[0].message
    # The third line of the author's file, which is the second of the
    # one the compiler parsed. A refusal is corrected on the way out of
    # the compiler, because ct4.build catches these as well and has no
    # way of knowing what was cut.
    assert (found[0].line, found[0].column) == (3, 1)


def test_a_template_without_the_declaration_is_checked_as_text():
    found = check.check_source(CHECKED[CHECKED.index("\n") + 1:])
    assert [one.code for one in found] == []


# -- The build ------------------------------------------------------

def test_the_build_asks_the_template_and_not_the_manifest(tmp_path):
    """No "markup" in the manifest, and none needed.

    The mode stands in the template's first line, so a manifest key
    would be a second place to be wrong, and the one thing it could
    add is a build that prints the declaration into the page.
    """
    base = tmp_path / "skin"
    base.mkdir(parents=True)
    (base / "index.html.tmpl").write_text("#mode markup\n<p>$name</p>\n",
                                          encoding="utf-8")
    (tmp_path / "context.json").write_text(json.dumps({"name": "<b>"}),
                                           encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "base": "skin",
        "output": "public",
        "context": {"json": "context.json"},
        "targets": [{"template": "index.html.tmpl", "output": "index.html"}],
    }), encoding="utf-8")

    report = build.build(build.load_manifest(manifest))
    assert build.exit_code(report) == 0
    assert (tmp_path / "public" / "index.html").read_text(
        encoding="utf-8") == "<p>&lt;b&gt;</p>\n"
    assert "markup" not in build.MODES


# -- Text mode did not move ------------------------------------------

def _writes(code):
    """Every call whose value is handed to write(), by function name."""
    out = []
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == codegen.WRITE:
            argument = node.args[0]
            if isinstance(argument, ast.Call) \
                    and isinstance(argument.func, ast.Name):
                out.append(argument.func.id)
    return out


def test_in_text_mode_the_escape_is_absent_and_not_neutral():
    """The structural half of the promise, and the reason for it.

    A wrapper that is present and does nothing in text mode is a
    wrapper that can start doing something. So in text mode the write
    is the filter call itself, the module carries no import of
    ct4.markup, and nothing of this mode is reachable from the
    generated code at all.
    """
    made = codegen.generate(PAGE[PAGE.index("\n") + 1:])
    assert _writes(made.code) == [codegen.FILTER] * 5
    assert "ct4.markup" not in made.code
    assert made.markup is None


def test_in_markup_mode_the_write_is_the_wrapper_and_the_filter_travels():
    made = codegen.generate(PAGE)
    assert _writes(made.code) == [codegen.MARKUP_ESCAPED] * 5
    assert "from ct4.markup.escape import" in made.code
    # One value, one filter, and ct3's single keyword. A filter written
    # "def filter(self, val, rawExpr=None)" dies on a second one.
    for node in ast.walk(ast.parse(made.code)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == codegen.MARKUP_ESCAPED:
            assert len(node.args) == 2
            assert [keyword.arg for keyword in node.keywords] == ["rawExpr"]
