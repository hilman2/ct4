"""What a template depends on, with every unknown labelled.

Incremental generation needs to know which files a template reads, and
it needs to say where it cannot know. A template whose ``#include``
target is computed at render time has an edge nobody can see. A graph
that quietly dropped that edge would leave a stale file on a web
server, and that is the failure nobody notices for weeks.

So every edge carries a certainty, and a node whose own answer is
unreadable is marked opaque: the build regenerates it every run. Being
unsure costs one render. Being wrongly sure costs a wrong file.

Three certainties, because over the 390 skin templates of the corpus
the 399 ``#include`` directives really are three different things: 348
name a constant string, 40 concatenate constants with placeholders, and
11 call a function. Only the first resolves to a file, the second names
a set of files, the third names nothing at all.

The base directory comes from the caller and is never derived from the
file being read. Include names go through ``Template.serverSidePath``,
which calls ``abspath``, so they resolve against the working directory
of the process. Measured over eight skins: 209 static includes resolve
the same from the skin root and from the includer's directory, 70
resolve only from the skin root, and none only from the includer's own
directory. One base per run, named by the caller, or the graph
describes a different program than the one that runs.

Nothing here is cached. ``scan`` is pure, and it is expensive enough
(``tree.parse`` 1.27 ms, the compile behind the context keys about
6.2 ms per template) that the caller wants to keep the result in its
own state file, keyed by the hash of the source.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from ct4 import analyze, diagnostics
from ct4.lang import lex, tree

# What an edge is about.
INCLUDE = "include"
EXTENDS = "extends"
IMPORT = "import"

# How far it can be resolved.
EXACT = "exact"
GLOB = "glob"
OPAQUE = "opaque"
MODULE = "module"

# Blocks whose body may or may not run. An include under one still has
# an edge; it just may not be taken. 173 of the corpus's 399 stand
# under one, which is why this is a flag and not a reason to drop them.
CONDITIONAL = frozenset({"if", "elif", "else", "for", "while", "try",
                         "except", "unless"})

# What getWhiteSpace steps over.
BLANKS = " \t\f"

# What a placeholder is called while the argument is read as Python.
# Numbered, so that two occurrences of the same name stay two holes and
# the expression keeps its shape.
HOLE = "_ct4_hole_"


@dataclass(frozen=True)
class Edge:
    """One dependency a template declares, and how sure it is.

    ``target`` is what the edge points at, and its meaning follows
    ``certainty``: the name as written for EXACT, a glob pattern for
    GLOB, a dotted module name for MODULE, and nothing at all for
    OPAQUE. ``expression`` is always the argument as it stands in the
    source, because a message that cannot quote the line is not worth
    reading.
    """

    kind: str
    certainty: str
    target: str
    expression: str
    keys: tuple[str, ...]
    line: int
    column: int
    conditional: bool = False
    raw: bool = False
    from_string: bool = False


@dataclass(frozen=True)
class Scan:
    """Everything one template says about what it reads.

    ``keys`` are the context roots, deliberately a superset: see
    ``ct4.analyze.lookup_roots``. ``error`` is empty for a template that
    could be read; where it is not, the node is opaque and the caller
    regenerates it instead of guessing, and ``error_line`` and
    ``error_column`` point at the place in the template so a message
    about it can too.
    """

    edges: tuple[Edge, ...]
    keys: frozenset[str]
    error: str = ""
    error_line: int = 0
    error_column: int = 0

    @property
    def opaque(self) -> bool:
        """Whether this template has to be regenerated every run."""
        if self.error:
            return True
        return any(edge.certainty == OPAQUE for edge in self.edges)


def scan(source: str, settings: dict[str, Any] | None = None) -> Scan:
    """Every dependency one template declares.

    Args:
        source (str): the template.
        settings (dict[str, Any]|None): the compiler settings this
            template will be compiled with. They belong to the answer:
            ``directiveStartToken`` and ``cheetahVarStartToken`` decide
            what counts as a directive at all, which is why a graph
            built under one settings dict says nothing about another.

    Returns:
        Scan: the edges in the order of the file, and the context
            roots. Never raises. A template that cannot be read carries
            the reason in ``error`` and is opaque, which costs a render
            and never a stale file.
    """
    try:
        root = tree.parse(source)
    except tree.StructureError as exc:
        return Scan((), frozenset(), str(exc), exc.line, exc.column)
    edges = tuple(_edges(root, dict(settings or {})))
    try:
        keys = analyze.lookup_roots(source, settings)
    except Exception as exc:
        # ct3 accepts a narrower set of templates than the tree does,
        # and it is ct3 that will have to render this one. A template
        # whose keys cannot be read is opaque for the same reason as one
        # that cannot be parsed.
        return Scan(edges, frozenset(),
                    "%s: %s" % (type(exc).__name__, exc))
    return Scan(edges, keys)


def read_include(node: tree.Node, conditional: bool) -> Edge:
    """One ``#include`` directive as an edge.

    Args:
        node (ct4.lang.tree.Node): the directive node, its arguments
            among its own tokens.
        conditional (bool): whether it stands inside a block whose body
            may not run.

    Returns:
        Edge: with ``certainty`` EXACT for a constant name, GLOB for a
            concatenation with holes in it, and OPAQUE for everything
            else.

    The two flags are read exactly as ``eatInclude`` reads them
    (Cheetah/Parser.py:2319), with a plain ``startswith`` after the
    blanks and not by whole words. So ``#include rawsource=$a`` is a raw
    include of a string, and in ``#include rawfoo`` the flag eats three
    characters and leaves ``foo`` as the expression naming the file.
    ``ct4.lang.codegen`` scans the same two flags for the
    same reason; the corpus test holds the two readings against each
    other so the duplication cannot drift.
    """
    tokens, commented = _directive_tokens(node)
    text = "".join(token.text for token in tokens)
    at = _skip_blanks(text, 0)
    raw = text.startswith("raw", at)
    if raw:
        at += len("raw")
    at = _skip_blanks(text, at)
    from_string = text.startswith("source", at)
    broken = False
    if from_string:
        at += len("source")
        at = _skip_blanks(text, at)
        if text[at:at + 1] == "=":
            at += 1
        else:
            # ct3 raises a ParseError here. There is nothing to resolve
            # and nothing to be faithful to.
            broken = True
    holed, expression, keys = _argument(tokens, at)
    certainty, target = _classify(holed)
    if commented or broken:
        certainty, target = OPAQUE, ""
    elif from_string:
        # The "source=" form includes a string, not a file, so there is
        # no file edge either way. A constant string depends on nothing
        # at all; anything else is computed at render time and the node
        # has to be regenerated. Measured: 0 of 399 corpus includes.
        target = ""
        if certainty != EXACT:
            certainty = OPAQUE
    return Edge(INCLUDE, certainty, target, expression, keys,
                node.line, node.column, conditional, raw, from_string)


# -- Reading one directive -------------------------------------------

def _edges(node: tree.Node, settings: dict[str, Any],
           conditional: bool = False) -> Iterator[Edge]:
    """Every edge under this node, in the order of the file."""
    for child in node.children:
        if child.name == INCLUDE:
            yield read_include(child, conditional)
        elif child.name in (EXTENDS, IMPORT, "from"):
            yield from _module_edges(child, conditional, settings)
        inside = conditional or (child.kind == tree.BLOCK
                                 and child.name in CONDITIONAL)
        yield from _edges(child, settings, inside)


def _directive_tokens(node: tree.Node) -> tuple[list[lex.Token], bool]:
    """A directive's argument tokens, and whether a comment stood in it.

    The directive's own name token is dropped, and so is the hash that
    closes it. A ## or a #* on the line changes what ct3 does with the
    line, and the caller records the directive as unreadable rather
    than reading past it.
    """
    tokens: list[lex.Token] = []
    commented = False
    for token in node.tokens[1:]:
        if token.kind == lex.DIRECTIVE_END:
            continue
        if token.kind in (lex.COMMENT, lex.BLOCK_COMMENT):
            commented = True
            continue
        tokens.append(token)
    return tokens, commented


def _module_edges(node: tree.Node, conditional: bool,
                  settings: dict[str, Any]) -> Iterator[Edge]:
    """``#extends``, ``#import`` and ``#from`` as module edges.

    Not template edges: ``#extends`` compiles to a Python import
    (Cheetah/Compiler.py:1935) and can never name a file. It appears 0
    times in the 390 skins and 22 times in the ct3 corpus, every time as
    a dotted class name. So the graph says "depends on the module X" and
    stops there; whether that module has a file is a question for
    ``importlib`` and for the caller.
    """
    tokens, commented = _directive_tokens(node)
    _, expression, keys = _argument(tokens, 0)
    if commented:
        yield Edge(node.name if node.name == EXTENDS else IMPORT,
                   OPAQUE, "", expression, keys,
                   node.line, node.column, conditional)
        return
    if node.name == EXTENDS:
        yield from _extends_edges(node, expression, keys, conditional,
                                  settings)
        return
    yield from _import_edges(node, expression, keys, conditional)


def _extends_edges(node: tree.Node, expression: str,
                   keys: tuple[str, ...], conditional: bool,
                   settings: dict[str, Any]) -> Iterator[Edge]:
    """The base classes of an ``#extends``, one edge each.

    The whole dotted name is recorded as the module, because that is
    what ct3 imports in the ordinary case: setBaseClass splits off the
    last chunk as the class name and, where that differs from the chunk
    before it, imports the full name as the module
    (Cheetah/Compiler.py:1988). A caller that finds nothing under the
    full name should try it without its last segment.
    """
    if settings.get("allowExpressionsInExtendsDirective") \
            or settings.get("handlerForExtendsDirective"):
        # The argument is then an arbitrary expression, or a callable
        # rewrites it. Either way this reading of it is worthless.
        yield Edge(EXTENDS, OPAQUE, "", expression, keys,
                   node.line, node.column, conditional)
        return
    for part in expression.split(","):
        name = part.strip()
        if not name:
            continue
        certainty = MODULE if _is_dotted(name) else OPAQUE
        yield Edge(EXTENDS, certainty, name if certainty == MODULE else "",
                   expression, keys, node.line, node.column, conditional)


def _import_edges(node: tree.Node, expression: str,
                  keys: tuple[str, ...],
                  conditional: bool) -> Iterator[Edge]:
    """The modules an ``#import`` or ``#from`` names.

    ct3 hands the whole line to addImportStatement unchanged
    (Cheetah/Parser.py:1660), so it is a Python import statement and
    Python's own parser is the right reader for it. A relative import
    keeps its dots in the target; nothing on ``sys.path`` will match it,
    and the caller reports that rather than pretending.
    """
    statement = "%s %s" % (node.name, expression)
    try:
        parsed = ast.parse(statement)
    except SyntaxError:
        yield Edge(IMPORT, OPAQUE, "", expression, keys,
                   node.line, node.column, conditional)
        return
    for found in parsed.body:
        if isinstance(found, ast.Import):
            for alias in found.names:
                yield Edge(IMPORT, MODULE, alias.name, expression, keys,
                           node.line, node.column, conditional)
        elif isinstance(found, ast.ImportFrom):
            yield Edge(IMPORT, MODULE,
                       "." * found.level + (found.module or ""),
                       expression, keys, node.line, node.column,
                       conditional)


def _argument(tokens: list[lex.Token],
              at: int) -> tuple[str, str, tuple[str, ...]]:
    """A directive's argument from that offset on, three ways.

    Args:
        tokens (list[ct4.lang.lex.Token]): the argument tokens, the
            directive's own name token already dropped.
        at (int): where the argument starts, counted over the joined
            text of those tokens.

    Returns:
        tuple[str, str, tuple[str, ...]]: the argument as Python with
            every placeholder replaced by a name of its own, the
            argument as it stands in the source, and the context roots
            the placeholders read, sorted.

    One walk for all three, because the holes have to be numbered in
    the same order in each of them.
    """
    holed: list[str] = []
    raw: list[str] = []
    roots: set[str] = set()
    holes = 0
    offset = 0
    for token in tokens:
        end = offset + len(token.text)
        if end <= at:
            offset = end
            continue
        text = token.text
        if offset < at:
            # "raw" and "source=" are literals, so the cut always falls
            # inside a text token and never inside a placeholder.
            text = text[at - offset:]
            holed.append(text)
            raw.append(text)
        elif token.kind == lex.PLACEHOLDER:
            holed.append("%s%d" % (HOLE, holes))
            holes += 1
            raw.append(text)
            roots.update(_roots_of(token))
        else:
            holed.append(text)
            raw.append(text)
        offset = end
    return "".join(holed).strip(), "".join(raw).strip(), \
        tuple(sorted(roots))


def _roots_of(token: lex.Token) -> Iterator[str]:
    """The context roots a placeholder and its nested ones read.

    Nested ones included, because ``$get_icon($label, 'rise')`` reads
    two names and a graph that saw only the outer one would not notice
    a changed ``label``. The scan stops at the first character that
    cannot be part of a name, and a dot is one of those, so what it
    collects is the root already.
    """
    for found in lex.walk([token]):
        if found.kind != lex.PLACEHOLDER:
            continue
        match = lex.start_of(found.text)
        if match is None:
            continue
        rest = found.text[match.end():]
        if not rest or rest[0] not in lex.IDENT_START:
            # "$(6)" is a placeholder around an expression, not around
            # a name.
            continue
        name = ""
        for char in rest:
            if char not in lex.IDENT:
                break
            name += char
        yield name


def _classify(expression: str) -> tuple[str, str]:
    """How far an include's argument can be resolved, and to what.

    Args:
        expression (str): the argument as Python, every placeholder
            replaced by a hole name.

    Returns:
        tuple[str, str]: the certainty and what it points at, which is
            the file name for EXACT, the pattern for GLOB and nothing
            for OPAQUE.

    Nothing is ever evaluated here, and no context is fabricated to
    evaluate it against. What can be read off the syntax is read off the
    syntax, and the rest is admitted to be unknown.
    """
    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError:
        return OPAQUE, ""
    body = parsed.body
    if isinstance(body, ast.Constant) and isinstance(body.value, str):
        return EXACT, body.value
    if isinstance(body, ast.BinOp) and isinstance(body.op, ast.Add):
        return GLOB, _pattern("".join(_concatenated(body)))
    # A call, an attribute, a subscript, a bare placeholder: the whole
    # name comes from somewhere else and not one character of it is
    # known here. 11 of the 399 corpus includes are of this kind, four
    # calls of $get_celestial_icon and seven placeholders standing
    # alone.
    return OPAQUE, ""


def _concatenated(node: ast.expr) -> list[str]:
    """The pieces of a name built with ``+``.

    A constant string contributes its text and everything else
    contributes a star, because everything else is text this layer does
    not know: ``'sections/' + $section + '.inc'`` is honestly every
    ``sections/*.inc``, and so is ``"dwd/VHDL" + str($day) + "_LATEST"``
    for its own shape. The constants are what carries the answer, and
    they survive whatever stands between them.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _concatenated(node.left) + _concatenated(node.right)
    return ["*"]


def _pattern(text: str) -> str:
    """A glob for a name with holes in it.

    A leading hole is a directory of unknown depth rather than one path
    segment: ``$webdir + "/x.tmpl"`` has to match ``x.tmpl`` wherever it
    lies, so it becomes ``**/x.tmpl``. Runs of stars collapse first,
    because two holes next to each other are still one unknown.
    """
    while "**" in text:
        text = text.replace("**", "*")
    if text.startswith("*/"):
        return "**/" + text[2:]
    if text.startswith("*"):
        return "**/" + text
    return text


def _is_dotted(name: str) -> bool:
    """Whether this is a dotted name and nothing else."""
    parts = name.split(".")
    return all(part.isidentifier() for part in parts)


def _skip_blanks(text: str, at: int) -> int:
    """getWhiteSpace, as an offset rather than the text it read."""
    while at < len(text) and text[at] in BLANKS:
        at += 1
    return at


# -- The graph -------------------------------------------------------

class Graph:
    """Which template reads which file, under one base directory.

    Built by adding templates; every file an added template can be seen
    to include is added with it. A node's name is its path relative to
    the base, so one file has exactly one name. Names may point above
    the base, and that is normal: belchertown's ``*/index.html.tmpl``
    reaches ``../header.html.tmpl``.
    """

    def __init__(self, base: Path,
                 settings: dict[str, Any] | None = None) -> None:
        """
        Args:
            base (pathlib.Path): the directory include names resolve
                against. The caller says which one; it is never derived
                from the file being read, because ct3 resolves include
                names against the working directory of the process and
                not against the includer.
            settings (dict[str, Any]|None): the compiler settings every
                template in this graph will be compiled with.
        """
        self.base = Path(base).resolve()
        self.settings = dict(settings or {})
        self.nodes: dict[str, Scan] = {}
        self.missing: dict[str, list[str]] = {}
        # What each edge pointed at when the graph was built. The graph
        # is a picture of one moment: add() has already walked the file
        # system, and remembering what it found keeps dependencies()
        # over many nodes from walking it again per glob.
        self._resolved: dict[Edge, tuple[str, ...]] = {}
        # Per module: whether importlib found it, and the file it named.
        self._modules: dict[str, tuple[bool, str]] = {}

    # -- filling it ---------------------------------------------------

    def name_for(self, path: Path) -> str:
        """The name this file has in the graph.

        Resolved first, so that ``a/../b.tmpl`` and ``b.tmpl`` are one
        node and not two.
        """
        found = Path(path).resolve()
        try:
            return found.relative_to(self.base).as_posix()
        except ValueError:
            return found.as_posix()

    def add(self, path: Path) -> str:
        """Reads a template into the graph and returns its name.

        Everything it includes is added with it, as far as that can be
        resolved. A file that cannot be read or parsed becomes an opaque
        node rather than an exception: the caller regenerates it, which
        is the same answer it would get from a template whose include is
        computed at run time.
        """
        name = self.name_for(path)
        if name in self.nodes:
            return name
        try:
            source = Path(path).read_bytes().decode("utf-8", "replace")
        except OSError as exc:
            self.nodes[name] = Scan((), frozenset(), str(exc))
            return name
        self.add_source(name, source)
        return name

    def add_source(self, name: str, source: str) -> None:
        """Puts a template into the graph under a name of its own.

        For the file that is being built as much as for a template that
        exists only in memory. Included files are added from disk, and
        the node is registered before they are, so a cycle terminates.
        """
        if name in self.nodes:
            return
        self.nodes[name] = scan(source, self.settings)
        for edge in self.nodes[name].edges:
            self._follow(name, edge)

    def _follow(self, name: str, edge: Edge) -> None:
        """Adds what one edge points at, and notes what is not there."""
        if edge.kind != INCLUDE or edge.from_string:
            return
        if edge.certainty == EXACT:
            if not edge.target:
                return
            path = self._resolve(edge.target)
            if path.is_file():
                self.add(path)
                return
            # Not an error. 69 of 348 constant include names have no
            # file, most of them optional user hooks that belchertown
            # guards with "#if os.path.exists(...)" on the line above.
            # The caller has to hear about it all the same, because the
            # file appearing later invalidates this template.
            absent = self.missing.setdefault(name, [])
            target = self.name_for(path)
            if target not in absent:
                absent.append(target)
            return
        if edge.certainty == GLOB:
            for found in self._matches(edge):
                self.add(found)

    def _resolve(self, target: str) -> Path:
        """An include name as a path, always against the base."""
        return self.base / target

    def _matches(self, edge: Edge) -> list[Path]:
        """The files a glob edge stands for, right now."""
        try:
            return sorted(found for found in self.base.glob(edge.target)
                          if found.is_file())
        except (ValueError, NotImplementedError, OSError):
            # An absolute pattern is one pathlib refuses, and a name
            # built from a hole can be one. Nothing is then known about
            # the tree, which is the same answer as no match: the file
            # gets rendered.
            return []

    # -- reading it ---------------------------------------------------

    def targets_of(self, edge: Edge) -> list[str]:
        """The node names one edge points at.

        Only files that are there: a name with no file behind it stands
        in ``missing`` instead, and a module edge points at no file at
        all.
        """
        found = self._resolved.get(edge)
        if found is None:
            found = tuple(self._targets_of(edge))
            self._resolved[edge] = found
        return list(found)

    def _targets_of(self, edge: Edge) -> Iterator[str]:
        if edge.kind != INCLUDE or edge.from_string:
            return
        if edge.certainty == EXACT and edge.target:
            path = self._resolve(edge.target)
            if path.is_file():
                yield self.name_for(path)
        elif edge.certainty == GLOB:
            seen: set[str] = set()
            for path in self._matches(edge):
                name = self.name_for(path)
                if name not in seen:
                    seen.add(name)
                    yield name

    def direct(self, name: str) -> set[str]:
        """What this template includes itself, one step only."""
        found = self.nodes.get(name)
        if found is None:
            return set()
        return {target for edge in found.edges
                for target in self.targets_of(edge)}

    def dependencies(self, name: str) -> set[str]:
        """Every file this template reads, however deeply.

        Without itself, even where it is part of a cycle. Terminates on
        a cycle, because nothing in Cheetah forbids one.
        """
        return self._reach(name) - {name}

    def _reach(self, name: str) -> set[str]:
        """What this template reads, itself included where it loops.

        The difference from ``dependencies`` is the whole point of it:
        a name that reaches itself is in a cycle, and that is how
        ``cycles`` finds them.
        """
        seen: set[str] = set()
        queue = [name]
        while queue:
            for target in self.direct(queue.pop()):
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        return seen

    def dependents(self, name: str) -> set[str]:
        """Every template that reads this file, however deeply."""
        parents: dict[str, set[str]] = {}
        for node in self.nodes:
            for target in self.direct(node):
                parents.setdefault(target, set()).add(node)
        seen: set[str] = set()
        queue = [name]
        while queue:
            for parent in parents.get(queue.pop(), ()):
                if parent not in seen:
                    seen.add(parent)
                    queue.append(parent)
        seen.discard(name)
        return seen

    def opaque(self, name: str) -> bool:
        """Whether this template has to be regenerated every run.

        Opacity propagates upward: a template that includes an
        always-stale one is itself always stale. An unknown name is
        opaque too, because nothing is known about it.

        Module edges are not part of this. Whether ``importlib`` finds
        the module is a question about ``sys.path`` rather than about
        the template, and ``findings`` reports it as CT4315 so the
        caller can add it to its own answer.
        """
        found = self.nodes.get(name)
        if found is None:
            return True
        if found.opaque:
            return True
        return any(self.nodes[other].opaque
                   for other in self.dependencies(name)
                   if other in self.nodes)

    def cycles(self) -> list[tuple[str, ...]]:
        """Include cycles, each as its sorted members.

        Not fatal, and not a reason to refuse the graph: the closure
        terminates on one, and ct3 renders one until its own recursion
        limit. Measured over the corpus: deepest chain 4 nodes, no
        cycles.
        """
        reach = {name: self._reach(name) for name in self.nodes}
        looped = sorted(name for name in reach if name in reach[name])
        seen: set[str] = set()
        found: list[tuple[str, ...]] = []
        for name in looped:
            if name in seen:
                continue
            group = tuple(sorted(
                other for other in looped
                if other == name or (other in reach[name]
                                     and name in reach[other])))
            seen.update(group)
            found.append(group)
        return sorted(found)

    # -- what the caller should hear about -----------------------------

    def findings(self) -> list[diagnostics.Diagnostic]:
        """What is worth saying about this graph.

        All of it notes but one. An include nobody can resolve and a
        file that is not there are both normal in real skins; they cost
        a render, and a message that calls them errors is a message
        people learn to ignore.
        """
        found: list[diagnostics.Diagnostic] = []
        for name, scanned in sorted(self.nodes.items()):
            if scanned.error:
                found.append(diagnostics.Diagnostic(
                    "CT4313", diagnostics.WARNING,
                    "this template could not be read (%s); it is "
                    "regenerated on every run" % scanned.error,
                    name, scanned.error_line, scanned.error_column))
            absent = self.missing.get(name, [])
            for edge in scanned.edges:
                found.extend(self._edge_findings(name, edge, absent))
        for group in self.cycles():
            found.append(diagnostics.Diagnostic(
                "CT4312", diagnostics.NOTE,
                "include cycle: %s" % " -> ".join(group), group[0]))
        return sorted(found, key=lambda item: (item.file, item.line,
                                               item.column, item.code))

    def _edge_findings(self, name: str, edge: Edge,
                       absent: list[str]) -> Iterator[
                           diagnostics.Diagnostic]:
        if edge.certainty == OPAQUE:
            if edge.kind == INCLUDE:
                yield diagnostics.Diagnostic(
                    "CT4310", diagnostics.NOTE,
                    "the target of this #include is computed at run "
                    "time (%s); this template is regenerated on every "
                    "run" % edge.expression, name, edge.line, edge.column)
            else:
                yield diagnostics.Diagnostic(
                    "CT4315", diagnostics.NOTE,
                    "this #%s names no module that can be located (%s); "
                    "the template is regenerated on every run"
                    % (edge.kind, edge.expression),
                    name, edge.line, edge.column)
            return
        if edge.certainty == MODULE and not self._locatable(edge.target):
            yield diagnostics.Diagnostic(
                "CT4315", diagnostics.NOTE,
                "the module %s is not on sys.path; the template is "
                "regenerated on every run" % edge.target,
                name, edge.line, edge.column)
            return
        if edge.certainty == EXACT and not edge.from_string \
                and edge.kind == INCLUDE:
            target = self.name_for(self._resolve(edge.target))
            if target in absent:
                yield diagnostics.Diagnostic(
                    "CT4311", diagnostics.NOTE,
                    "#include names %s, which is not there" % edge.target,
                    name, edge.line, edge.column)

    def module_origin(self, module: str) -> str | None:
        """Where importlib says a module edge's module lives.

        A build fingerprints the module it can see, so that editing a
        skin's own helper invalidates the templates that ``#import`` it.
        The three answers are three different things to the build and
        must not be collapsed into two: a module that is not there
        cannot be proven unchanged and makes the template always stale,
        while a built-in has nothing to hash and is not a reason to
        regenerate anything, because ``#import time`` appears in real
        skins and would otherwise cost a render every cycle forever.

        Args:
            module (str): The dotted name from a module edge's target.

        Returns:
            str|None: The file importlib named; ``""`` for a module that
            was found but has no file of its own - a built-in, something
            frozen into the interpreter, a namespace package; None for a
            module that was not found at all.
        """
        found, origin = self._spec(module)
        if not found:
            return None
        return origin if origin and Path(origin).is_file() else ""

    def _locatable(self, module: str) -> bool:
        """Whether ``importlib`` finds this module."""
        return self._spec(module)[0]

    def _spec(self, module: str) -> tuple[bool, str]:
        """Whether importlib finds this module, and the file it named.

        Asking imports the packages above it, which is what ct3 will do
        as well when it renders the template. A module whose parent
        cannot even be imported counts as not found. Memoised, because
        this is the most expensive question the graph asks and the graph
        describes one moment anyway.
        """
        known = self._modules.get(module)
        if known is not None:
            return known
        import importlib.util

        answer = (False, "")
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, AttributeError, ValueError, TypeError):
            spec = None
        if spec is not None:
            answer = (True, spec.origin or "")
        self._modules[module] = answer
        return answer
