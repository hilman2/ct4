"""Ein JSON-Dokument mit Loechern lesen.

Der Parser kennt die JSON-Grammatik. Deshalb weiss er jederzeit, ob er an
einer Wert-, Schluessel- oder Elementposition steht, und deshalb sind
Kommas hier kein Autorenproblem: sie trennen, mehr nicht. Eines zu viel
oder eines zu wenig ist kein Fehler, weil am Ende keine Zeichenkette
zusammengesetzt wird, sondern eine Struktur.

``#`` und ``$`` sind in JSON frei. Direktiven stehen auf eigenen Zeilen,
Platzhalter an Wertpositionen.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

WHITESPACE = " \t\r\n"

# Ein Platzhalter, so weit ihn dieser Parser abgrenzen muss. Was darin
# steht, uebersetzt spaeter Cheetah; hier geht es nur darum, wo er
# aufhoert.
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

LITERALS = {"true": True, "false": False, "null": None}


class JsonTemplateError(SyntaxError):
    """Die Vorlage laesst sich nicht als JSON-Dokument lesen."""


@dataclass
class Lit:
    """Ein Wert, der schon im Template steht."""

    value: Any


@dataclass
class Expr:
    """Ein Platzhalter. ``precision`` kommt aus einem angehaengten ``@``."""

    text: str
    precision: int | None = None


@dataclass
class Str:
    """Eine Zeichenkette, in der Platzhalter stehen duerfen."""

    parts: list[Any]


@dataclass
class Obj:
    members: list[Any] = field(default_factory=list)


@dataclass
class Arr:
    items: list[Any] = field(default_factory=list)


@dataclass
class Member:
    key: Any
    value: Any


@dataclass
class For:
    """``#for`` um Mitglieder oder Elemente herum."""

    header: str
    body: list[Any]


@dataclass
class If:
    """``#if`` mit beliebig vielen ``#elif`` und einem ``#else``."""

    branches: list[tuple[str, list[Any]]]
    otherwise: list[Any] | None = None


@dataclass
class Series:
    """``#series`` an einer Wertposition."""

    expr: str
    layout: str = "records"
    fields: tuple[str, ...] = ()
    precision: int | None = None
    gaps: str = "null"


@dataclass
class Document:
    """Was eine Vorlage insgesamt beschreibt."""

    root: Any
    precisions: dict[str, int] = field(default_factory=dict)
    missing: str = "null"
    schema: str | None = None


def parse(source: str) -> Document:
    """Liest eine JSON-Vorlage."""
    return _Parser(source).document()


class _Parser:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.doc = Document(root=None)

    # -- Grundlagen -----------------------------------------------

    def error(self, message: str) -> JsonTemplateError:
        line = self.src.count("\n", 0, self.pos) + 1
        column = self.pos - (self.src.rfind("\n", 0, self.pos) + 1) + 1
        return JsonTemplateError("Zeile %d, Spalte %d: %s"
                                 % (line, column, message))

    def at_end(self) -> bool:
        return self.pos >= len(self.src)

    def peek(self) -> str:
        return "" if self.at_end() else self.src[self.pos]

    def skip_space(self) -> None:
        """Ueberspringt Leerraum und Kommentare.

        ``##`` bis zum Zeilenende ist wie in Cheetah ein Kommentar. JSON
        kennt keine, und ein Skin braucht sie.
        """
        while not self.at_end():
            char = self.src[self.pos]
            if char in WHITESPACE:
                self.pos += 1
            elif self.src.startswith("##", self.pos):
                end = self.src.find("\n", self.pos)
                self.pos = len(self.src) if end < 0 else end
            else:
                return

    def expect(self, char: str) -> None:
        self.skip_space()
        if self.peek() != char:
            raise self.error("%r erwartet, %r gefunden"
                             % (char, self.peek() or "<Ende>"))
        self.pos += 1

    def line_rest(self) -> str:
        end = self.src.find("\n", self.pos)
        if end < 0:
            end = len(self.src)
        text = self.src[self.pos:end]
        self.pos = end
        return text.strip()

    # -- Dokument -------------------------------------------------

    def document(self) -> Document:
        self.header()
        self.doc.root = self.value()
        self.skip_space()
        if not self.at_end():
            raise self.error("nach dem Dokument steht noch etwas")
        return self.doc

    def header(self) -> None:
        """Liest die Direktiven vor dem eigentlichen Dokument."""
        while True:
            self.skip_space()
            if self.peek() != "#" or self.src.startswith("##", self.pos):
                return
            mark = self.pos
            self.pos += 1
            word = self.word()
            if word == "precision":
                self.precision_directive()
            elif word == "missing":
                self.doc.missing = self.missing_value(self.line_rest())
            elif word == "schema":
                self.doc.schema = json.loads(self.line_rest())
            else:
                self.pos = mark
                return

    def missing_value(self, text: str) -> str:
        if text not in ("omit", "null", "error"):
            raise self.error("#missing kennt omit, null und error, "
                             "nicht %r" % text)
        return text

    def precision_directive(self) -> None:
        text = self.line_rest()
        name, _, digits = text.partition("=")
        try:
            self.doc.precisions[name.strip()] = int(digits)
        except ValueError:
            raise self.error("#precision braucht NAME = ZAHL, "
                             "bekommen: %r" % text) from None

    def word(self) -> str:
        match = NAME.match(self.src, self.pos)
        if match is None:
            return ""
        self.pos = match.end()
        return match.group()

    # -- Werte ----------------------------------------------------

    def value(self) -> Any:
        self.skip_space()
        char = self.peek()
        if char == "{":
            return self.obj()
        if char == "[":
            return self.arr()
        if char == '"':
            return self.string()
        if char == "$":
            return self.expr()
        if char == "#":
            return self.value_directive()
        return self.literal()

    def value_directive(self) -> Any:
        self.pos += 1
        word = self.word()
        if word != "series":
            raise self.error("an einer Wertposition steht nur #series, "
                             "nicht #%s" % word)
        return self.series()

    def literal(self) -> Lit:
        start = self.pos
        while not self.at_end() and self.src[self.pos] not in ",]} \t\r\n":
            self.pos += 1
        text = self.src[start:self.pos]
        if text in LITERALS:
            return Lit(LITERALS[text])
        try:
            return Lit(json.loads(text))
        except ValueError:
            self.pos = start
            raise self.error("kein Wert: %r" % text) from None

    def expr(self) -> Expr:
        text = self.placeholder()
        precision = None
        save = self.pos
        self.skip_space()
        if self.peek() == "@":
            self.pos += 1
            self.skip_space()
            start = self.pos
            while not self.at_end() and self.src[self.pos].isdigit():
                self.pos += 1
            if start == self.pos:
                raise self.error("nach @ fehlt die Zahl der Stellen")
            precision = int(self.src[start:self.pos])
        else:
            self.pos = save
        return Expr(text, precision)

    def placeholder(self) -> str:
        """Grenzt einen Platzhalter ab, ohne ihn zu deuten.

        ``${...}`` reicht bis zur schliessenden Klammer. Sonst gilt ein
        Name, gefolgt von beliebig vielen Punkten, Indizes und Aufrufen.
        Was daraus wird, entscheidet Cheetah.
        """
        start = self.pos
        self.pos += 1                      # das $
        if self.peek() == "{":
            self.pos = self.balanced("{", "}")
            return self.src[start:self.pos]
        if not self.word():
            raise self.error("nach $ fehlt ein Name")
        while not self.at_end():
            char = self.src[self.pos]
            if char == ".":
                save = self.pos
                self.pos += 1
                if not self.word():
                    self.pos = save
                    break
            elif char == "[":
                self.pos = self.balanced("[", "]")
            elif char == "(":
                self.pos = self.balanced("(", ")")
            else:
                break
        return self.src[start:self.pos]

    def balanced(self, opening: str, closing: str) -> int:
        """Position hinter der passenden schliessenden Klammer."""
        depth = 0
        index = self.pos
        while index < len(self.src):
            char = self.src[index]
            if char == '"':
                index = self.skip_json_string(index)
                continue
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        raise self.error("%r wird nicht geschlossen" % opening)

    def skip_json_string(self, index: int) -> int:
        index += 1
        while index < len(self.src):
            if self.src[index] == "\\":
                index += 2
                continue
            if self.src[index] == '"':
                return index + 1
            index += 1
        raise self.error("Zeichenkette wird nicht geschlossen")

    def string(self) -> Any:
        """Liest eine Zeichenkette; Platzhalter darin werden eingesetzt."""
        end = self.skip_json_string(self.pos)
        raw = self.src[self.pos:end]
        self.pos = end
        text = json.loads(raw)
        if "$" not in text:
            return Lit(text)
        return Str(self.interpolate(text))

    def interpolate(self, text: str) -> list[Any]:
        parts: list[Any] = []
        inner = _Parser(text)
        plain: list[str] = []
        while not inner.at_end():
            char = inner.peek()
            if char != "$":
                plain.append(char)
                inner.pos += 1
                continue
            if plain:
                parts.append(Lit("".join(plain)))
                plain = []
            parts.append(Expr(inner.placeholder()))
        if plain:
            parts.append(Lit("".join(plain)))
        return parts

    # -- Sammlungen -----------------------------------------------

    def obj(self) -> Obj:
        self.expect("{")
        node = Obj()
        self.collect(node.members, "}", self.member)
        return node

    def arr(self) -> Arr:
        self.expect("[")
        node = Arr()
        self.collect(node.items, "]", self.value)
        return node

    def collect(self, into: list[Any], closing: str, read: Any) -> None:
        """Liest Mitglieder oder Elemente bis zur schliessenden Klammer.

        Kommas werden gelesen und weggeworfen. Sie stehen im Template,
        damit es wie JSON aussieht; fuer die Struktur sind sie ohne
        Bedeutung, und genau deshalb kann hier keins zu viel sein.
        """
        while True:
            self.skip_space()
            if self.peek() == ",":
                self.pos += 1
                continue
            if self.peek() == closing:
                self.pos += 1
                return
            if self.at_end():
                raise self.error("%r fehlt" % closing)
            if self.peek() == "#" and not self.src.startswith("##", self.pos):
                node = self.control(closing, read)
                if node is None:
                    return
                into.append(node)
                continue
            into.append(read())

    def member(self) -> Member:
        self.skip_space()
        key = self.string() if self.peek() == '"' else self.expr()
        self.expect(":")
        return Member(key, self.value())

    # -- Steuerung ------------------------------------------------

    def control(self, closing: str, read: Any) -> Any:
        self.pos += 1
        word = self.word()
        if word == "for":
            return For(self.line_rest(), self.block(closing, read, ("for",)))
        if word == "if":
            return self.if_node(closing, read)
        raise self.error("hier steht nur #for oder #if, nicht #%s" % word)

    def if_node(self, closing: str, read: Any) -> If:
        branches: list[tuple[str, list[Any]]] = []
        condition = self.line_rest()
        node = If(branches)
        while True:
            body, ended = self.block_until(closing, read,
                                           ("elif", "else", "end"))
            branches.append((condition, body))
            if ended == "elif":
                condition = self.line_rest()
                continue
            if ended == "else":
                self.line_rest()
                node.otherwise, _ = self.block_until(closing, read, ("end",))
            self.finish_end("if")
            return node

    def block(self, closing: str, read: Any,
              ends: tuple[str, ...]) -> list[Any]:
        body, _ = self.block_until(closing, read, ("end",))
        self.finish_end(ends[0])
        return body

    def block_until(self, closing: str, read: Any,
                    stops: tuple[str, ...]) -> tuple[list[Any], str]:
        body: list[Any] = []
        while True:
            self.skip_space()
            if self.at_end():
                raise self.error("Block wird nicht geschlossen")
            if self.peek() == ",":
                self.pos += 1
                continue
            if self.peek() == "#" and not self.src.startswith("##", self.pos):
                mark = self.pos
                self.pos += 1
                word = self.word()
                if word in stops:
                    return body, word
                self.pos = mark
                body.append(self.control(closing, read))
                continue
            if self.peek() == closing:
                raise self.error("Block wird nicht geschlossen")
            body.append(read())

    def finish_end(self, expected: str) -> None:
        """Liest den Rest einer #end-Zeile und prueft, was sie schliesst."""
        self.skip_space()
        word = self.word()
        if word and word != expected:
            raise self.error("#end %s erwartet, #end %s gefunden"
                             % (expected, word))

    # -- #series --------------------------------------------------

    def series(self) -> Series:
        end = self.balanced("(", ")")
        inner = self.src[self.pos + 1:end - 1]
        self.pos = end
        expr, options = self.split_arguments(inner)
        node = Series(expr)
        for name, raw in options.items():
            if name == "layout":
                node.layout = self.literal_option(name, raw, str)
            elif name == "fields":
                node.fields = tuple(json.loads(raw.replace("'", '"')))
            elif name == "precision":
                node.precision = self.literal_option(name, raw, int)
            elif name == "gaps":
                node.gaps = self.literal_option(name, raw, str)
            else:
                raise self.error("#series kennt %r nicht" % name)
        if node.layout not in ("records", "columns", "pairs"):
            raise self.error("layout kennt records, columns und pairs, "
                             "nicht %r" % node.layout)
        return node

    def literal_option(self, name: str, raw: str, kind: type) -> Any:
        try:
            value = json.loads(raw.replace("'", '"'))
        except ValueError:
            raise self.error("%s=%s ist kein Wert" % (name, raw)) from None
        if not isinstance(value, kind):
            raise self.error("%s erwartet %s" % (name, kind.__name__))
        return value

    def split_arguments(self, text: str) -> tuple[str, dict[str, str]]:
        """Trennt den Ausdruck von den benannten Angaben.

        Getrennt wird nur an Kommas der obersten Ebene, damit ein Aufruf
        im Ausdruck selbst nicht zerrissen wird.
        """
        parts: list[str] = []
        depth = 0
        current: list[str] = []
        for char in text:
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            if char == "," and depth == 0:
                parts.append("".join(current))
                current = []
                continue
            current.append(char)
        parts.append("".join(current))

        options = {}
        for part in parts[1:]:
            name, _, raw = part.partition("=")
            options[name.strip()] = raw.strip()
        return parts[0].strip(), options
