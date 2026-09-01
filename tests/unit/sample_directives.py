"""Handlers the directive tests register through a ct4.toml.

A module of its own, because a registration names a module and a
callable, and that is the shape a skin's own package would have.
"""

from ct4 import directives as d


def greet(call):
    """``#greet $name``: a line directive, a placeholder in its argument."""
    return [d.write("Hello, "), d.write_value(call.arguments), d.write("!")]


def box(call):
    """``#box [class]``: the body inside a div."""
    attributes = ' class="%s"' % call.arguments if call.arguments else ""
    return [d.write("<div%s>" % attributes), d.BODY, d.write("</div>")]


def twice(call):
    """``#twice``: the body two times over.

    Text substitution could only say this by copying the source; at
    the level of the generated code the body's statements simply stand
    there twice.
    """
    return [d.BODY, d.BODY]


def failing(call):
    return d.statements("raise ValueError('boom')")


def wrong(call):
    return "not a statement"
