(#5) Directives a project registers in a file beside its templates
===================================================================


:CHEP: 5
:Title: Directives a project registers in a file beside its templates
:Version: 1
:Author: Manuel
:Status: Implemented in Cheetah4
:Type: Standards Track
:Content-Type: text/x-rst
:Created: 02-Sep-2026

----

Abstract
--------

A project registers directives of its own in a ``ct4.toml`` next to
its templates. A registered directive is handled when the template is
compiled: the handler receives the tag's name, argument text and
position and returns the statements that stand where the tag stood.
This replaces ``macroDirectives``, which received text and gave text
back.

Specification
-------------

The file is ``ct4.toml``, found by searching upward from the
template's directory, or from the working directory for a template
that has no file. The nearest file wins. Two tables are read::

    [directives]
    greet = "myskin.directives:greet"

    [blocks]
    box = "myskin.directives:box"

A name under ``[directives]`` stands in a tag of its own and has no
body. A name under ``[blocks]`` runs to its ``#end name``, or to the
end of its line in the colon short form; the ``#end`` is required,
and the tag stops after the name, the way Cheetah closes a macro
directive. A name must be one the lexer can read and must not be one
of Cheetah's own. A value is ``"package.module:callable"``.

The handler is called once per use, while the template is compiled,
with a ``Call`` holding the name, the argument text with the blanks
and the short form's colon stripped, the line and column, whether it
is the short form, and whether it is a block. It returns ``ast``
statements, one or a list, and for a block ``BODY`` among them where
the body's own code goes. The statements run inside the template's
method, where ``write`` writes output and ``_filter`` is the filter
in force. ``ct4.directives`` offers ``write``, ``write_value``,
``expression`` and ``statements`` to build the usual shapes;
``expression`` reads a Cheetah expression through the same reader a
``#set`` argument goes through.

A template that uses a registered directive is compiled by the code
generator and by nothing else. Where the generator cannot compile it,
the compilation fails with the reason.

Motivation
----------

Cheetah's ``macroDirectives`` is a compiler setting holding callables
that receive a directive's body as text and hand text back, which the
parser then reads again. Positions are lost in that switch. The
setting has to be carried through every ``Template`` construction, so
a tool that only looks at the file never learns the names, and an
application that hands the engine a file cannot register anything at
all.

Rationale
---------

**Why a file and not an entry point.** A plugin that contributes
names, types or filters may do so through the installed environment.
A directive is syntax, and if every installed package could add
syntax, no template would look like Cheetah for long. The file says
in one place which language this project speaks.

**Why statements and not text.** Text has to be parsed again, and the
parse loses where the text came from. A statement carries its
position, so an error inside one names the directive's line, and the
body of a block keeps the positions it came with.

**Why no fallback.** Cheetah3 does not know the name and would read
the tag as text, or stop on its ``#end`` as an invalid end directive.
A page rendered that way is a page nobody wrote.

Backwards Compatibility
-----------------------

A project without a ``ct4.toml`` sees no difference. ``macroDirectives``
keeps working through Cheetah3's compiler, which the generator falls
back to for a template that sets it.

Reference Implementation
------------------------

``ct4.directives`` reads the file and holds the contract;
``ct4.lang.lex`` and ``ct4.lang.tree`` take the names through a
``Syntax`` object; ``ct4.lang.codegen`` calls the handler.

Copyright
---------
This document has been placed in the public domain.
