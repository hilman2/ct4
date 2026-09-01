"""Markup mode: HTML, with the escaping decided where the code is written.

The third output mode beside text and JSON, and the narrowest of the
three. It escapes a placeholder that stands in element text or in a
quoted attribute value, and it refuses every other position rather than
guessing at one: inside a ``<script>`` an HTML escape does not merely
fail to help, it corrupts, because character references are not decoded
in raw text and ``&lt;`` reaches the JavaScript engine as four
characters.

The mode is opt-in per template and says so in one declared line; see
``ct4.markup.mode``. Text mode does not move by a byte, which is the
constraint every part of this package is built around.
"""
