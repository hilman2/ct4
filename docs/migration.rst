From Cheetah3 to Cheetah4
=========================

Cheetah4 is a fork of Cheetah3. It renders the templates you have,
byte for byte, and adds what the templates you write next can say.
This guide is for someone who has templates today and wants to know
what changes when the engine underneath them does, and what they can
do afterwards that they could not do before.

Installing it
-------------

The distribution is ``Cheetah4``; the import name stays ``Cheetah``.
Only one of the two distributions can be installed in an environment,
because both own that package. ``pip install Cheetah4`` over an
existing Cheetah3 replaces it, and an application that imports
``Cheetah.Template`` finds the fork without changing a line.

Every ``ct4`` command checks that the engine on the path is the fork
and says so if it is not.

What is the same
----------------

A template that renders under Cheetah3 renders the same under
Cheetah4, and that is measured, not promised: the test suite of
Cheetah3, the templates weewx ships, a hundred and seventy-five
third-party weewx skins and fifteen hundred templates from other
projects are rendered on both engines and compared. Autocalling, the
name mapper, the filters, the whitespace rules around directives,
all of it stays.

What changes without asking
---------------------------

An error at render time names the line of the template. A traceback
out of a Cheetah3 template points at a generated module that is not
on disk; Cheetah4 adds the template's file, line and column as a
note on the exception, and an ``#include`` names its own line first
and the including template's after it.

Two compilations of the same template give the same bytes. Cheetah3
wrote a timestamp into every generated module; that is gone, so a
compiled template can be cached and compared.

Getting the code generator
--------------------------

Cheetah4 carries a second compiler beside Cheetah3's: a code generator
that reads the template into a tree and writes the Python through
``ast``. It is what the modes below and the registered directives run
on. It is not installed by default. Two lines install it, and an
application that has an import hook of its own puts them there. For
weewx that is ``user/extensions.py``::

    from ct4.lang import backend
    backend.install()

What the generator cannot compile, Cheetah3's compiler compiles
instead, and the page comes out the same either way. The exceptions
are the modes, which Cheetah3 does not have: a template that declares
one and cannot be compiled is an error with the reason.

Declaring a mode
----------------

A template says what it is on its first line that is neither blank
nor a ``##`` comment::

    #mode json
    #mode markup
    #mode strict
    #mode markup strict

Written with two blanks, further down the file, or with a word
nobody knows, it is refused or read as text, so a template either
declares a mode or does not.

``json``
    The template is a JSON document with holes. The engine builds the
    value and serialises it: numbers stay numbers, a trailing comma
    is not your problem, ``None`` becomes ``null``, and ``#series``
    writes a sequence. See the users guide for the directives.

``markup``
    The template is HTML, and every placeholder is escaped for the
    position it stands in: element text, a quoted attribute, an
    unquoted one. What the mode refuses, it refuses with the reason:
    a placeholder inside ``<script>``, an ``#include``, a file that
    does not end in text.

``strict``
    Python semantics for the lookups. Nothing is called that you did
    not call: ``$station.location`` is the method and
    ``$station.location()`` is what it returns. A name the template
    bound itself, a ``#for`` target, a ``#set``, a ``#def``
    parameter, is a Python name, and ``$r.name`` is ``r.name``. A
    name out of the search list is found there once and what hangs
    off it is looked up the way Cheetah3 does with autocalling off,
    so ``$Extras.key`` out of a dict keeps working. Twice as fast on a
    page of plain objects, and the one mode you migrate to.

Moving a template to strict mode
--------------------------------

The parentheses that autocalling supplied have to be written. Only a
run shows where that was, so ``ct4 migrate`` works from a recording
of what the page read::

    ct4 fixture capture --weewx ~/src/weewx --out fixtures
    ct4 migrate skins/Seasons/index.html.tmpl --context fixtures/index.json
    ct4 migrate skins/Seasons/index.html.tmpl --context fixtures/index.json --write

The first command runs weewx' own template tests with a recorder in
between and writes one file per page. The second reports every
placeholder it would change, renders the rewritten template in strict
mode against the same recording and compares it with the text-mode
page, byte for byte. A difference is a diff and exit code 1. The
third rewrites the file.

What ``migrate`` cannot decide, it names: a placeholder in an
enclosure (``${x}``), one with a modifier, and a chain it cannot
follow through the recording, such as one behind a call whose
arguments the template computes. Those you read yourself.

Rendering without the application
---------------------------------

``ct4 render`` takes a template and a context and writes the page::

    ct4 render index.html.tmpl --context fixtures/index.json
    ct4 render index.html.tmpl --context values.json --out /tmp/index.html
    ct4 render draft.tmpl --sandbox

The context is a recording, a plain JSON object, or a list of
namespaces. The mode comes from the template. ``--sandbox`` renders
in a child process with a time limit and refuses what reaches outside
the template: ``#import``, PSP, an ``#include`` with a computed name,
``open`` and its friends, any dunder. It is against accidents and
careless edits, not against an adversary.

Checking a template
-------------------

``ct4 check`` finds a misspelt field without the application running,
because the application declared its names::

    ct4 check skins/Seasons/*.tmpl
    ct4 check skins/Seasons/index.html.tmpl --format=json

Findings carry a code, a place and where possible a suggestion. The
same check runs in an editor through ``ct4 lsp``, and an agent gets
it through ``ct4 mcp``.

Building many templates
-----------------------

``ct4 build`` renders every target of a manifest, writes only what
changed, and keeps the mtime of everything that did not, so rsync and
FTP transfer nothing for an unchanged page::

    ct4 build site.json -j4
    ct4 build site.json --dry-run

The manifest names the base directory, the output directory, the
context and the targets. Staleness is decided on content, never on
mtime, and a dependency the build cannot follow costs a render, never
a stale file.

Registering your own directives
-------------------------------

A ``ct4.toml`` next to the templates or in a directory above them
registers directives, without a body under ``[directives]`` and with
one under ``[blocks]``::

    [directives]
    greet = "myskin.directives:greet"

    [blocks]
    box = "myskin.directives:box"

The handler is called while the template is compiled and returns the
statements that stand where the tag stood::

    from ct4 import directives as d

    def greet(call):
        return [d.write("Hello, "), d.write_value(call.arguments), d.write("!")]

    def box(call):
        return [d.write("<div>"), d.BODY, d.write("</div>")]

``ct4 check``, ``ct4 render`` and ``ct4 build`` find the file from the
template's path; weewx finds it through ``Template(file=...)`` once the
generator is installed. A template that uses a registered directive is
compiled by the generator alone.

Keeping templates tidy
----------------------

``ct4 fmt`` re-indents the one whitespace Cheetah throws away, the
indent before a directive that stands on a line of its own, so that
an ``#end`` stands under its opener and a line inside a block one step
further in. Everything else is output and stays byte for byte::

    ct4 fmt --check skins/Seasons/*.tmpl
    ct4 fmt skins/Seasons/*.tmpl

``ct4 ast`` prints the block tree, for tools.

What is not there
-----------------

No XML mode, no escaping for PHP or JavaScript, no ``javascript:``
check, and no ``#include`` in markup mode; the reasons are in the
design plan, and each is a case where a wrong answer would be worse
than none. Python 3.10 is the floor.
