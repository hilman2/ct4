"""JSON mode: a template describes a structure, not text.

The path a template takes:

1. ``parse`` reads it as a JSON document with holes.
2. ``emit`` turns that into a Cheetah definition that drives a
   building site.
3. Cheetah compiles the definition. The expressions therefore follow
   the same rules as in text mode.
4. The building site assembles the structure, ``json.dumps`` writes it.

Commas, escaping, types and ``null`` are no longer the author's
problem. They do not exist: nowhere is a string pieced together.
"""

from ct4.jsonmode.render import compile_template, render

__all__ = ["compile_template", "render"]
