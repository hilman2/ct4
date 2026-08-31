"""Der JSON-Modus: eine Vorlage beschreibt eine Struktur, keinen Text.

Der Weg einer Vorlage:

1. ``parse`` liest sie als JSON-Dokument mit Loechern.
2. ``emit`` macht daraus eine Cheetah-Definition, die einen Bauplatz
   bedient.
3. Cheetah uebersetzt die Definition. Damit gelten fuer die Ausdruecke
   dieselben Regeln wie im Textmodus.
4. Der Bauplatz baut die Struktur, ``json.dumps`` schreibt sie.

Kommas, Escaping, Typen und ``null`` sind damit keine Autorenprobleme
mehr. Es gibt sie nicht: an keiner Stelle wird eine Zeichenkette
zusammengesetzt.
"""

from ct4.jsonmode.render import compile_template, render

__all__ = ["compile_template", "render"]
