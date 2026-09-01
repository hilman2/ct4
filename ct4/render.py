"""Render one template against a context, without the application.

The one place that decides how a template is rendered: ``ct4 build``
comes through here for every target and ``ct4 render`` for the one
file on its command line, so the two can never disagree about a mode.

The mode is the template's own statement about itself. A first line of
``#mode json`` or ``#mode markup`` decides it, and a template without
one is text, which is what ct3 renders and renders unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ct4.markup import mode as markup_mode

TEXT = "text"
JSON = "json"
MARKUP = "markup"


def mode_of(source: str) -> str:
    """Which mode a template declares, or text where it declares none."""
    from ct4.check import is_json_template

    if is_json_template(source):
        return JSON
    if markup_mode.declared(source):
        return MARKUP
    return TEXT


def search_list_from(document: Any) -> list[Any]:
    """The search list a context document describes.

    Three shapes are read. A recording from ``ct4 fixture capture``
    carries a ``context`` list of recorded namespaces, and each becomes
    an object that answers the way the original did. A plain object is
    one namespace. A list is the search list itself, first searched
    first.

    Args:
        document (Any): The parsed JSON.

    Returns:
        list[Any]: What to hand ``Template`` as its searchList.
    """
    if isinstance(document, dict) and isinstance(document.get("context"),
                                                 list):
        from ct4.fixture.record import replay

        return [replay(tree) for tree in document["context"]]
    if isinstance(document, list):
        return list(document)
    return [document]


def filter_from(document: Any) -> type | None:
    """The output filter a recording names, where it names one.

    A recording holds what the template read; what became of a value
    after that is the application's filter, and weewx' turns None into
    the empty string. Without it a replay would print "None" where the
    page showed nothing.
    """
    from ct4.fixture.filters import resolve

    if isinstance(document, dict):
        return resolve(document.get("filter", ""))
    return None


def render_source(source: str, search_list: Sequence[Any], *,
                  path: Path | None = None,
                  settings: dict[str, Any] | None = None,
                  mode: str | None = None,
                  output_filter: type | None = None) -> str:
    """Renders a template's source and returns the text.

    Args:
        source (str): The template.
        search_list (Sequence[Any]): The namespaces, first searched
            first.
        path (Path|None): Where the template lives. A JSON template
            resolves its ``#schema`` beside it, and an error names it.
        settings (dict|None): Compiler settings, the manifest's in a
            build.
        mode (str|None): One of TEXT, JSON and MARKUP to insist on a
            mode, None to read it from the source. A build insists on
            JSON where its manifest says so.
        output_filter (type|None): A Cheetah filter class; None takes
            Cheetah's default.

    Raises:
        Whatever the template raises. An error at render time carries
        the template's line as a note, see ``ct4.trace``.
    """
    mode = mode or mode_of(source)
    if mode == JSON:
        from ct4 import jsonmode

        base_dir = path.parent if path is not None else None
        return jsonmode.render(source, search_list, base_dir=base_dir)
    if mode == MARKUP:
        return _render_markup(source, search_list, settings or {},
                              path, output_filter)
    return _render_text(source, search_list, settings or {}, output_filter)


def _render_markup(source: str, search_list: Sequence[Any],
                   settings: dict[str, Any], path: Path | None,
                   output_filter: type | None) -> str:
    """Compiles and renders a template that declared markup mode.

    Through the code generator rather than through ct3, and directly
    rather than by installing the generating compiler: installing it
    would change how every other template in the same process is
    compiled, and text mode not moving is the promise this whole mode
    is built around. A markup template the generator refuses fails
    with the reason; a fallback to ct3 would print the declaration
    line into the page and escape nothing.
    """
    from ct4.lang import codegen

    return codegen.render(source, search_list, output_filter=output_filter,
                          settings=settings or None,
                          file=str(path) if path is not None else "")


def _render_text(source: str, search_list: Sequence[Any],
                 settings: dict[str, Any],
                 output_filter: type | None) -> str:
    """Compiles from the source string and renders.

    From the string, not from the file, and that is the whole point:
    ``ct4.cache`` builds its key only when the compiler is handed a
    source, so ``Template(file=path)`` gets a key of None and a miss
    every single time, which looks exactly like a working cache.

    Cheetah's own in-process cache is switched off here. It would
    answer the second compilation of a source inside one process
    before the persistent cache is ever asked, which makes the hit
    count in a build's report say nothing about the thing it is there
    to watch.
    """
    from Cheetah.Template import Template

    klass = Template.compile(source=source, compilerSettings=dict(settings),
                             useCache=False, cacheCompilationResults=False)
    keywords = {"filter": output_filter} if output_filter else {}
    template = klass(searchList=list(search_list), **keywords)
    try:
        return str(template)
    finally:
        template.shutdown()
