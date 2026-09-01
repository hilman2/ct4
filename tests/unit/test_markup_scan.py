"""Where markup mode thinks a placeholder stands, held to three rulers.

The scan is only worth as much as the bytes it scans, so the first
group holds the reconstruction against ct3 itself: build a template,
render it with ct3, and require the reconstruction to be the same
string. Everything after that rests on it, and the shapes chosen are
the ones that moved the answer while the scanner was being built -- an
indent dropped in front of a directive, a line ending kept behind one,
a ``#slurp``, an ``#else`` whose line ending belongs to the branch it
opens, and sabnzbd's habit of writing its directives inside HTML
comment markers that ct3 leaves in the output.

The second group is hand-written classification, one assert each. The
third is the refusals. The fourth runs the whole corpus through and
demands that nothing but ScanRefused ever comes out.

The last one is the oracle, and it is the one that found CDATA. For
every corpus file the scan accepts, the emitted text is rebuilt with a
unique marker in place of every placeholder, handed to Python's own
html.parser, and the parser is asked where each marker ended up. Zero
disagreements is the assertion. It is not a percentage on purpose: a
disagreement means either the scanner is wrong or the position belongs
on the refusal list, and both are repairs rather than tolerances.
"""

from __future__ import annotations

import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest

from ct4.lang import tree
from ct4.markup import scan


def render_with_ct3(source: str, names: dict) -> str:
    """What ct3 writes for this template, with nothing in between."""
    from Cheetah.Template import Template

    klass = Template.compile(source, keepRefToGeneratedCode=False)
    template = klass(searchList=[names])
    try:
        return str(template.respond())
    finally:
        template.shutdown()


def sites_of(source: str) -> dict:
    return scan.scan(tree.parse(source))


def only_site(source: str) -> scan.Site:
    """The one placeholder of a one-placeholder template."""
    found = sites_of(source)
    assert len(found) == 1, found
    return next(iter(found.values()))


def context_of(source: str) -> str:
    return only_site(source).context


# -- A. The emitted-byte model, against ct3 --------------------------

# Each case is a template, the names it reads, and which arm of its
# conditionals ct3 will take with those names.
EMITTED = [
    # A directive alone on its line: ct3 drops the indent in front of
    # it and the line ending behind it.
    ("  #set $a = 1\nx", {}, 0),
    # The same directive with text before it on the line: the line
    # ending stays, because the tag never ran past the end of its line
    # with a clear line behind it.
    ("y  #set $a = 1\nx", {}, 0),
    # #slurp eats the line ending wherever it stands.
    ("a#slurp\nb", {}, 0),
    ("  #slurp\nb", {}, 0),
    # The line ending after an #else belongs to the branch #else opens,
    # so it is written by the else arm and not by the arm above it.
    ("#if $a\nA\n#else\nB\n#end if\n", {"a": 1}, 0),
    ("#if $a\nA\n#else\nB\n#end if\n", {"a": 0}, 1),
    ("x#if $a\nA\n#else\nB\n#end if\ny\n", {"a": 0}, 1),
    # sabnzbd writes its directives inside HTML comment markers, and
    # ct3 leaves those markers in the output.
    ('<!--#set global $pane="Config"#-->', {}, 0),
    ('<div class="a" <!--#if $c#-->style="none"<!--#end if#-->>',
     {"c": 1}, 0),
    # A ## comment on a clear line takes its whole line with it; one
    # with text in front of it takes only itself.
    ("  ## note\nx", {}, 0),
    ("y ## note\nx", {}, 0),
    # A one-line block comment loses its indent when a line follows.
    ("  #* c *#\nx", {}, 0),
    ("#encoding utf-8\nx", {}, 0),
]


@pytest.mark.parametrize("source, names, branch", EMITTED)
def test_the_reconstruction_is_what_ct3_writes(source, names, branch):
    got, _ = scan.emitted(tree.parse(source), branch=branch)
    assert got == render_with_ct3(source, names)


def test_a_placeholder_lands_where_the_reconstruction_says():
    source = "<p>$x</p>\n"
    root = tree.parse(source)
    start = next(iter(scan.scan(root)))
    text, offsets = scan.emitted(root, {start: "VALUE"})
    assert text == render_with_ct3(source, {"x": "VALUE"})
    assert text[offsets[start]:offsets[start] + 5] == "VALUE"


# -- B. Classification, one assert each ------------------------------

def test_element_text():
    assert context_of("<p>$x</p>") == scan.TEXT


def test_a_quoted_attribute_value():
    assert context_of('<a href="/a/$x">') == scan.ATTRIBUTE


def test_the_head_of_a_url_attribute():
    assert context_of('<a href="$x">') == scan.URL_HEAD


def test_a_single_quoted_attribute_value():
    assert context_of("<a title='$x'>") == scan.ATTRIBUTE


def test_an_unquoted_attribute_value():
    assert context_of("<img src=$x>") == scan.VERBATIM


def test_a_value_whose_quoting_would_come_out_of_it():
    assert context_of("<input value=$x>") == scan.VERBATIM


def test_an_event_handler_attribute():
    site = only_site('<b onclick="go($x)">')
    assert site.context == scan.VERBATIM
    assert "onclick" in site.note


def test_a_style_attribute():
    assert context_of('<b style="$x">') == scan.VERBATIM


def test_inside_a_script():
    site = only_site("<script>var a = $x;</script>")
    assert site.context == scan.VERBATIM
    assert site.note == "inside <script>"


def test_inside_a_javascript_string():
    assert context_of('<script>var s = "$x";</script>') == scan.VERBATIM


def test_inside_a_style_element():
    assert context_of("<style>a{color:$x}</style>") == scan.VERBATIM


def test_inside_an_html_comment():
    site = only_site("<!-- $x -->")
    assert site.context == scan.VERBATIM
    assert site.note == "inside an HTML comment"


def test_the_body_of_a_def_names_the_def():
    site = only_site("#def graph()\n$x#end def")
    assert site.context == scan.VERBATIM
    assert site.note == "in the body of #def graph"


def test_a_less_than_in_a_directive_argument_opens_no_tag():
    # The trap the token stream falls into: scanning tokens reports
    # 1444 placeholders in attribute-name position across the corpus
    # where scanning the tree reports 6.
    site = only_site("#if $delta < 60\n<b>$x</b>\n#end if")
    assert site.context == scan.TEXT
    assert "attribute-name" not in site.note


def test_the_closing_tag_beats_the_javascript_string():
    source = '<script>var s = "a</script>b";</script>$x'
    assert context_of(source) == scan.TEXT


def test_two_slashes_in_a_url_start_no_comment():
    source = '<script>var u = "http://h/p";</script>$x'
    assert context_of(source) == scan.TEXT


def test_a_second_placeholder_in_a_url_is_no_longer_the_head():
    found = sites_of('<a href="$a$b">')
    contexts = [site.context for site in sorted(found.values(),
                                                key=lambda s: s.start)]
    assert contexts == [scan.URL_HEAD, scan.ATTRIBUTE]


def test_a_placeholder_in_a_directive_argument_gets_no_site():
    assert sites_of("#if $a\ntext\n#end if\n") == {}


def test_the_argument_of_an_echo_is_a_site():
    assert context_of("<p>#echo $x#</p>") == scan.TEXT


def test_a_fragment_is_scanned_from_element_text():
    # An .inc that begins inside a <table> is fine; table content is
    # data state and wants the same escaping as any other text.
    assert context_of("  <tr><td>$x</td></tr>\n") == scan.TEXT


# -- C. Refusals -----------------------------------------------------

REFUSALS = [
    ('<a href="$x', "does not end"),
    ("<![CDATA[$x]]>", "CDATA"),
    ('#if $a\n<a href="\n#end if\n$x', "different markup states"),
    ('<script>\n#include "a.inc"\n</script>', "#include"),
    ("#filter WebSafe\n$x#end filter", "#filter"),
]


@pytest.mark.parametrize("source, expected", REFUSALS)
def test_the_scan_refuses_and_says_where(source, expected):
    with pytest.raises(scan.ScanRefused) as caught:
        sites_of(source)
    assert expected in caught.value.reason
    assert caught.value.line >= 1
    assert caught.value.column >= 1


def test_a_loop_that_leaves_a_tag_open_is_refused():
    with pytest.raises(scan.ScanRefused):
        sites_of("#for $i in $rows\n<a href='\n#end for\n$x")


def test_branches_that_agree_are_not_refused():
    found = sites_of("#if $a\n<b>$x</b>\n#else\n<i>$x</i>\n#end if")
    assert len(found) == 2
    assert {site.context for site in found.values()} == {scan.TEXT}


# -- D. The whole corpus ---------------------------------------------

def corpus_skins():
    """The skin templates, with the fuzz harness left as it was found.

    tests/fuzz/harness.py freezes ``time.time`` at import so that a
    template reading the clock renders the same twice. Nothing is
    rendered here, and a frozen clock left standing would reach every
    other test in this worker.
    """
    fuzz = str(Path(__file__).resolve().parents[1] / "fuzz")
    if fuzz not in sys.path:
        sys.path.insert(0, fuzz)
    real = time.time
    try:
        import harness
    finally:
        time.time = real
    return [(case_id, source)
            for case_id, source in harness.corpus_templates()
            if "/" in case_id and not case_id.startswith("weewx/")]


SKINS = corpus_skins()
needs_corpus = pytest.mark.skipif(
    not SKINS, reason="the corpus is not reachable from here")


@needs_corpus
def test_every_skin_either_scans_or_refuses():
    settled = 0
    for case_id, source in SKINS:
        root = tree.parse(source)
        try:
            found = scan.scan(root)
        except scan.ScanRefused as refusal:
            assert refusal.reason, case_id
            settled += 1
            continue
        assert isinstance(found, dict), case_id
        settled += 1
    assert settled >= 300, settled


# -- E. The oracle that found CDATA ----------------------------------

MARKUP_NAMES = (".html.tmpl", ".inc", ".htm", ".html")

# A marker that cannot be a prefix of another one and holds no
# character an HTML parser reads as anything but text.
MARKER = "zqmark%dz"


class Where(HTMLParser):
    """Records, for each marker, where html.parser found it.

    The quoting is read off the raw tag text via the attribute name and
    never off the value: html.parser decodes character references in a
    value, so a URL holding an entity comes back looking unquoted and
    produces dozens of disagreements that are not there.
    """

    def __init__(self, markers):
        super().__init__(convert_charrefs=False)
        self.markers = markers
        self.seen = {}

    def _note(self, text, kind, attribute="", quoted=True):
        for marker in self.markers:
            if marker in text:
                self.seen.setdefault(marker, (kind, attribute, quoted))

    def _tag(self, attrs):
        raw = self.get_starttag_text() or ""
        for attribute, value in attrs:
            if value is None:
                continue
            quoted = bool(re.search(
                re.escape(attribute) + r"\s*=\s*[\"']", raw, re.I))
            self._note(value, "attr", attribute, quoted)

    def handle_data(self, data):
        self._note(data, "data")

    def handle_starttag(self, tag, attrs):
        self._tag(attrs)

    def handle_startendtag(self, tag, attrs):
        self._tag(attrs)

    def handle_comment(self, data):
        self._note(data, "comment")

    def handle_decl(self, data):
        self._note(data, "decl")

    def unknown_decl(self, data):
        self._note(data, "decl")

    def handle_pi(self, data):
        self._note(data, "pi")


def disagreements_in(source):
    """Every marker whose place html.parser reads differently."""
    root = tree.parse(source)
    try:
        sites = scan.scan(root)
    except scan.ScanRefused:
        return []
    found = []
    for branch in range(3):
        values = {start: MARKER % start for start in sites}
        text, offsets = scan.emitted(root, values, branch)
        markers = {MARKER % start: start for start in offsets}
        reader = Where(set(markers))
        reader.feed(text)
        reader.close()
        for marker, start in markers.items():
            site = sites.get(start)
            place = reader.seen.get(marker)
            if site is None or place is None:
                continue
            kind, attribute, quoted = place
            if site.context == scan.TEXT and kind != "data":
                found.append((site, kind, attribute))
            elif site.context in (scan.ATTRIBUTE, scan.URL_HEAD) \
                    and not (kind == "attr" and quoted):
                found.append((site, kind, attribute))
    return found


@needs_corpus
def test_html_parser_puts_every_placeholder_where_the_scan_does():
    broken = []
    files = 0
    for case_id, source in SKINS:
        if not case_id.endswith(MARKUP_NAMES):
            continue
        files += 1
        for site, kind, attribute in disagreements_in(source):
            broken.append("%s line %d: %s, parser says %s %s"
                          % (case_id, site.line, site.note, kind, attribute))
    assert files >= 100, files
    assert not broken, "%d disagreements: %s" % (len(broken), broken[:5])
