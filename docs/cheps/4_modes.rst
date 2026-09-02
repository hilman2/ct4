(#4) A mode declared on the first line of a template
=====================================================


:CHEP: 4
:Title: A mode declared on the first line of a template
:Version: 1
:Author: Manuel
:Status: Implemented in Cheetah4
:Type: Standards Track
:Content-Type: text/x-rst
:Created: 02-Sep-2026

----

Abstract
--------

A template may open with one line, ``#mode`` followed by one or two
words, that says how it is compiled: ``json`` for a document that is
built and then serialised, ``markup`` for HTML escaped by position,
``strict`` for Python semantics in the lookups. ``markup`` and
``strict`` combine. A template without the line is what it always
was.

Specification
-------------

The declaration stands on the first line of the template that is
neither blank nor a ``##`` comment. It is the whole line: the
keyword ``#mode``, one blank, the words separated by one blank each,
nothing else. Anywhere else in the file the same text is output.

The words are ``json``, ``markup`` and ``strict``. ``json`` stands
alone. ``markup`` and ``strict`` may stand together in either order.
A word not in that list is refused by name when the template is
compiled.

The line is not a directive. It is cut out of the source before the
parser sees it, next to where Cheetah cuts its ``#unicode`` line, and
the ``json`` line is read by the JSON parser instead. Every line
after it therefore moves up by one in a line number read off the
compiled source; the lines in front of it, being blank or comments,
keep theirs.

``json``: the template is a JSON document whose values are Cheetah
expressions. The engine builds the document and serialises it, and
the directives ``#series``, ``#precision`` and ``#schema`` apply.

``markup``: the template is HTML. Every placeholder is escaped for the
position it stands in, and the compiler refuses a template whose
positions it cannot trust, with the reason.

``strict``: nothing is called that the author did not call. A name
the template bound itself, a ``#for`` target, a ``#set`` target, a
``#def`` parameter, is a Python name and its chain is Python attribute
access. A name out of the search list is found there once, without
autocalling, and the chain behind it is resolved the way Cheetah
resolves a chain with autocalling off.

A template that declares ``markup`` or ``strict`` is compiled by the
code generator and by nothing else. Where the generator cannot
compile it, the compilation fails with the reason.

Motivation
----------

Three things a template author wants and the engine cannot give
without being told: numbers that stay numbers in a JSON file, escaping
that depends on where a value lands, and lookups that do what Python
does. Each is a different reading of the same source, so the source
has to say which reading it wants.

Rationale
---------

**Why a line and not a setting.** weewx hands ``Template`` a file and
calls ``respond()``; that is its whole contract with the engine. A
mode that only a ct4 command can switch on is no mode for weewx. A
line in the file travels with the file.

**Why the first line and one spelling.** So that a template either
declares a mode or does not, and nobody reads the engine to find out
which. A licence header of ``##`` comments may stand in front of it,
because those write nothing.

**Why not the file extension.** ``index.html.tmpl`` has the extension
``.tmpl``; the part of the name that says HTML is the part
``splitext`` throws away. weewx ships ``.json.tmpl`` skins that write
JSON by hand and are text templates. And every differential
instrument compiles from a source without a path.

**Why not a directive.** Measured: Cheetah's parser has no eater for
the name and stops on it. Registering one would move both engines at
once, and an instrument that compares them would compare two changed
engines and report nothing.

Backwards Compatibility
-----------------------

A template without the line compiles as before, byte for byte over
the corpus. A template with the line, handed to Cheetah3, prints the
line as its first line of output: visible, and therefore better than
a page that quietly differs.

Reference Implementation
------------------------

``ct4.modes`` reads the line. ``ct4.jsonmode`` implements ``json``,
``ct4.markup`` implements ``markup``, and ``ct4.lang.codegen``
implements ``strict`` in the expression it writes for a chain.

Copyright
---------
This document has been placed in the public domain.
