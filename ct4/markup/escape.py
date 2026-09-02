"""What a placeholder in markup mode is written through.

This is pure runtime. It knows nothing about the lexer, the tree, the
compiler or HTML parsing; it is what the generated Python calls at the
moment a placeholder becomes output. The compiler decides *which* of
the two write helpers a placeholder gets, and that decision is made
once, at compile time, from the position the placeholder stands in.

Why the escaping does not live in a Cheetah filter. A filter is
supplied by the application and receives the value plus at most
``rawExpr``; the position the value is being written into never
reaches it. weewx replaces the whole filter library
(``filtersLib=weewx.cheetahgenerator``), so ct3's escaping filters are
not even reachable in the one application that matters, and ct3's
``WebSafe`` escapes only ``&``, ``<`` and ``>`` and would leave every
attribute injectable. So the escape wraps the filter's *result* at the
generated call site, and the ``__html__`` protocol is resolved before
the filter runs. See :func:`write_escaped` for why that order and not
the other one.

Why this table and not :func:`html.escape`. The five substitutions
below are markupsafe 3.0.3's, character for character and in its
order, because that combination is what makes one escape correct in
element text and in a quoted attribute value of either quote kind at
the same time. ``html.escape`` emits the named entity ``&quot;`` and
does not touch the single quote unless asked, so a value escaped with
it is still injectable out of a single-quoted attribute. Numeric
references are used rather than ``&quot;`` and ``&apos;`` because
``&apos;`` is not defined in HTML 4 and numeric references are read
correctly by every parser that matters. markupsafe itself is not a
dependency of ct4 and must not become one; the table is thirty
characters and copying it costs less than an install.

What this module deliberately does not do. :class:`Markup` is a marker,
not markupsafe's safe-string algebra: no operator overloading, no
format hooks, no ``join``. A value either declares itself as markup or
it gets escaped, and there is no arithmetic in between that could get
the answer subtly wrong.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

__all__ = [
    "Markup",
    "MarkupError",
    "Quoted",
    "escape",
    "quoted",
    "safe",
    "write_escaped",
    "write_verbatim",
]


class Markup(str):
    """A string that is already markup and must not be escaped again.

    A marker and nothing more. markupsafe's ``Markup`` is a safe-string
    algebra whose operators keep the marking alive through
    concatenation, formatting and joining; this one carries the marking
    and stops there, because in markup mode a value is written exactly
    once and never assembled. Anything more would be an invitation to
    build a string out of pieces and lose track of which piece was
    checked.

    It is a ``str`` subclass on purpose, not a wrapper object: weewx's
    ``AssureUnicode`` filter calls ``str()`` on anything that is not
    already a ``str``, and a wrapper would be flattened into its repr
    on the way through. A subclass passes such a filter unchanged.
    """

    __slots__ = ()

    def __html__(self) -> str:
        """The markup this value stands for, which is the value itself.

        Returns:
            str: ``self``. Having it satisfies the protocol that
            :func:`escape` and the write helpers test for, which is
            what makes escaping idempotent.
        """
        return self


class MarkupError(Exception):
    """A value reached a position markup mode refuses to escape.

    Raised at render time, not at compile time: the compiler knows the
    position is one that cannot be escaped and emits the demand, but
    only the render knows what the placeholder actually evaluated to.
    The message carries the caller's position description verbatim, so
    the file, the line, the column and the name of the position reach
    whoever reads the traceback.
    """


def _as_markup(value: object) -> Markup | None:
    """The value's own markup, if its type declares the protocol.

    The lookup is on the type and never on the instance, because a
    ``Mapping`` carrying an ``'__html__'`` key would otherwise answer
    ``getattr`` and be written into the page unescaped. Templates read
    dictionaries all day, so this is a real value, not a hypothetical
    one.

    Args:
        value (object): Whatever a placeholder evaluated to.

    Returns:
        Markup|None: The result of ``value.__html__()`` wrapped in
        Markup, or None when the type does not define ``__html__``.
        The wrap accepts a foreign implementation, markupsafe's
        included, because the test is the protocol and not the class.
    """
    method = getattr(type(value), "__html__", None)
    if method is None:
        return None
    return Markup(method(value))


def _as_template_string(value: object) -> Markup | None:
    """A PEP 750 template string, its interpolations escaped.

    Python 3.14 brings ``t"<b>{name}</b>"``: a value that keeps the
    literal parts and the interpolated values apart instead of joining
    them into one string. That is exactly the distinction escaping
    needs. A context that hands the page such a value says which
    characters are its own markup and which came from data, so the
    markup stays and the data is escaped, one interpolation at a time,
    conversion and format spec applied first the way an f-string would.

    Returns:
        Markup|None: None where the value is no template string, and
        on a Python without them.
    """
    if Template is None or not isinstance(value, Template):
        return None
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
            continue
        inner = item.value
        if item.conversion == "r":
            inner = repr(inner)
        elif item.conversion == "s":
            inner = str(inner)
        elif item.conversion == "a":
            inner = ascii(inner)
        if item.format_spec:
            inner = format(inner, item.format_spec)
        parts.append(str(escape(inner)))
    return Markup("".join(parts))


# Through importlib, so that a type checker on a Python before 3.14
# does not go looking for a module it cannot find.
try:
    Template: Any = importlib.import_module("string.templatelib").Template
except ImportError:                     # Python before 3.14
    Template = None


def escape(value: object) -> Markup:
    """The value as markup: its own if it has one, else escaped.

    Anything that is not already markup is converted with ``str()``
    first, so an int, a float, a list or a weewx measurement object all
    take the same path as a string and none of them can slip through
    unescaped.

    Args:
        value (object): Whatever a placeholder evaluated to.

    Returns:
        Markup: The value's ``__html__()`` untouched, or the ``str()``
        of the value with the five characters replaced. Safe in
        element text and in an attribute quoted either way; nowhere
        else.
    """
    own = _as_markup(value)
    if own is not None:
        return own
    own = _as_template_string(value)
    if own is not None:
        return own
    text = str(value)
    # markupsafe 3.0.3's table and its order. The ampersand has to go
    # first or the substitutions would escape each other's output.
    text = (
        text.replace("&", "&amp;")
        .replace(">", "&gt;")
        .replace("<", "&lt;")
        .replace("'", "&#39;")
        .replace('"', "&#34;")
    )
    return Markup(text)


def write_escaped(
    value: object,
    filter: Callable[..., object],
    **kw: object,
) -> str:
    """Write a placeholder that stands in text or a quoted attribute.

    The order of the three steps is the whole point, and it is not
    interchangeable:

    1. The ``__html__`` protocol is resolved *before* the filter, so
       what the filter receives is a ``str`` subclass. weewx's
       ``AssureUnicode`` calls ``str()`` on anything that is not a
       ``str``, which would destroy a bare object that only declares
       ``__html__``; a ``Markup`` survives it unchanged.
    2. The application's filter runs, with ``kw`` passed straight
       through. The compiler puts ``rawExpr`` in there and the filter
       contract is ct3's, not this module's, so nothing is added to it.
    3. The result is escaped unless it, too, declares ``__html__``.

    The property this buys is worth stating plainly: when the
    composition goes wrong, the output is escaped twice and visibly
    wrong on the page. It is never left unescaped and silently
    exploitable. A filter that stringifies its argument therefore
    produces ``&amp;lt;`` rather than a live tag.

    Args:
        value (object): Whatever the placeholder evaluated to.
        filter (Callable[..., object]): The application's filter, bound
            as Cheetah binds it. Called as ``filter(value, **kw)``.
            Returns the text to write, or anything that survives
            ``str()``.
        kw (object): Passed to the filter unchanged. The compiler puts
            ``rawExpr`` here.

    Returns:
        str: The text to write, escaped unless it proved itself markup.
    """
    # A template string is resolved here for the same reason as the
    # protocol: the filter would str() it into its repr.
    own = _as_markup(value)
    if own is None:
        own = _as_template_string(value)
    if own is not None:
        value = own
    result = filter(value, **kw)
    done = _as_markup(result)
    if done is not None:
        return str(done)
    # A plain str, not the Markup that escape() hands back. What leaves
    # here is finished output, and marking it would tell a later
    # escape() that it is already safe.
    return str(escape(str(result)))


def write_verbatim(
    value: object,
    filter: Callable[..., object],
    where: str,
    **kw: object,
) -> str:
    """Write a placeholder in a position that cannot be escaped.

    Inside a ``<script>`` or a ``<style>``, in an event-handler or
    ``style`` attribute, in an unquoted attribute value, in an HTML
    comment, in a ``#def`` body, and anywhere the scan could not place
    the placeholder. HTML escaping in those positions is not merely
    useless but wrong: character references are not decoded in raw
    text, so an escaped ``<`` reaches the JavaScript engine as the four
    characters ``&lt;``.

    So this one demands proof instead of guessing, and the proof is
    :class:`Quoted` rather than ``__html__``. The difference is the
    whole point: ``__html__`` means HTML-safe, which is what its
    producers mean by it, and a markupsafe ``Markup("</script>")`` is
    correct HTML that ends a script block. Taking it as proof here
    would accept exactly the value that breaks out.

    Args:
        value (object): Whatever the placeholder evaluated to.
        filter (Callable[..., object]): The application's filter, bound
            as Cheetah binds it. Called as ``filter(value, **kw)``,
            with the value already resolved to its markup. Returns the
            text to write.
        where (str): The caller's description of the position,
            containing file, line, column and the name of the position,
            for instance ``index.html.tmpl:12:5 (inside <script>)``.
            Put into the message as it is, never parsed.
        kw (object): Passed to the filter unchanged.

    Returns:
        str: The text to write. Not escaped, by construction.

    Raises:
        MarkupError: The value is not :class:`Quoted`. The compile
            succeeded, so this is the render refusing rather than
            shipping a guess.
    """
    if not isinstance(value, Quoted):
        raise MarkupError(
            f"markup mode cannot escape a value at {where}; "
            f"pass it through ct4.markup.quoted() once the application "
            f"has quoted it for that position "
            f"(got {type(value).__name__})"
        )
    own = value
    # A filter that stringifies drops the marking again, and the result
    # is still written. The value proved itself before the filter ran,
    # and what the application does to its own values afterwards is the
    # application's business.
    return str(filter(own, **kw))


class Quoted(Markup):
    """Text quoted for a position HTML escaping cannot serve.

    A stronger claim than :class:`Markup`, and a separate one because
    the two are not the same promise. ``__html__`` means HTML-safe, and
    that is what every producer in the ecosystem means by it: a
    markupsafe ``Markup("</script>")`` is a correct piece of HTML and
    ends a script block. Accepting it as proof inside a ``<script>``
    would redefine somebody else's word into a claim they never made,
    and the value that arrives is exactly the one that breaks out.

    So the positions that cannot be escaped ask for this instead. It
    says: the application quoted this for the language it lands in,
    JavaScript or CSS or an unquoted attribute, and knows which one.

    A ``Markup`` is not a ``Quoted``. A ``Quoted`` is a ``Markup``,
    because text quoted for its position is safe to write where HTML
    would be too.
    """

    __slots__ = ()


def quoted(value: object) -> Quoted:
    """Declare that a value is quoted for where the template puts it.

    For the positions markup mode refuses to escape: inside a
    ``<script>`` or ``<style>``, in an event-handler or ``style``
    attribute, in an unquoted attribute value, in an HTML comment, and
    anywhere the scan could not place the placeholder.

    It checks nothing and cannot. What it does is make the claim
    explicit and greppable at the point where somebody made it, which
    is the only thing a marker can do.

    Args:
        value (object): Text the caller has quoted for its position.
            Converted with ``str()`` if it is not a string yet.

    Returns:
        Quoted: The value, marked.
    """
    return Quoted(value)


def safe(value: object) -> Markup:
    """Declare that a value is markup and must not be escaped.

    For applications and adapters, at the point where the application
    has decided the text is markup it produced itself. It checks
    nothing and cannot: calling it on data that came from outside is
    exactly the bug this module exists to prevent, and there is no way
    for the call to tell the two apart.

    Args:
        value (object): Text that is already markup. Converted with
            ``str()`` if it is not a string yet.

    Returns:
        Markup: The value, marked.
    """
    return Markup(value)
