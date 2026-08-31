"""Aufzeichnen und Abspielen eines Vorlagen-Kontexts.

Aufgezeichnet wird nicht der Kontext, sondern der Zugriff darauf. Das
ist der Unterschied, auf den es ankommt: ein weewx-``ValueHelper`` laesst
sich nicht ablegen, aber was eine Vorlage aus ihm herausholt, sehr wohl.

Ein Knoten haelt vier Dinge, alle wahlfrei:

``text``
    Was ``str()`` geliefert hat. Cheetah gibt jeden Platzhalter durch
    einen Filter, und der ruft am Ende ``str()``. Ohne diesen Eintrag
    kaeme beim Abspielen die Ausgabe nicht heraus.
``attrs``
    Was ueber Punkt oder Schluessel gelesen wurde. Der NameMapper von
    Cheetah macht zwischen beidem keinen Unterschied, das Fixture
    deshalb auch nicht.
``items``
    Die Elemente, falls ueber den Knoten iteriert wurde.
``calls``
    Ergebnisse von Aufrufen, nach ihren Argumenten geordnet.

Beim Abspielen entsteht daraus wieder ein Objekt, das sich fuer den
NameMapper wie das Original verhaelt. Was die Vorlage beim Aufzeichnen
nicht gelesen hat, fehlt, und ein Zugriff darauf ist ein Fehler mit
klarer Meldung. Das ist Absicht: ein stiller leerer Wert waere eine
falsche gruene Pruefung.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

# Werte, die ohne Umweg als JSON liegen koennen. Alles andere wird ueber
# str() und seine Attribute beschrieben.
PLAIN = (str, int, float, bool, type(None))

TEXT = "text"
ATTRS = "attrs"
ITEMS = "items"
CALLS = "calls"
PLAIN_VALUE = "value"

# Eine Ausnahme, die beim Aufzeichnen geflogen ist. weewx-Skins fuehren
# solche Faelle absichtlich vor: $day(data_binding='foo_binding') zeigt,
# was bei einem falschen Binding herauskommt. Der Filter faengt die
# Ausnahme und schreibt einen Ersatztext. Ohne Aufzeichnung fehlte beim
# Abspielen genau dieser Text.
ERROR = "error"

# Die Speicheradresse in einem Standard-repr wechselt bei jedem Lauf.
# weewx-Skins reichen solche Objekte durch, etwa $jsonize(zip(...)).
# Ohne diese Kuerzung faende der Abspieler den Aufruf nie wieder.
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")

# Woran Cheetah erkennt, ob ein Wert eine gebundene Methode ist und
# deshalb aufgerufen werden muss (isInstanceOrClass in _namemapper.c).
# weewx' $day ist so eine Methode: sie liefert erst den TimespanBinder.
# Ein Rekorder, der diese Namen verschweigt, sieht wie eine Instanz aus,
# wird nicht aufgerufen, und $day.hours findet nichts mehr.
METHOD_MARKS = ("__func__", "__code__", "__self__")

# Dasselbe Verfahren fragt zuerst nach "mro", um Klassen zu erkennen.
# Die Antwort muss vom Original kommen und darf nicht im Baum landen.
CLASS_MARK = "mro"


class Missing(AttributeError):
    """Die Vorlage liest etwas, das beim Aufzeichnen nicht gelesen wurde.

    Erbt von ``AttributeError``, und das ist wesentlich: Cheetah fragt
    jeden Namensraum der searchList der Reihe nach, ob er einen Namen
    kennt. Ein Namensraum, der ihn nicht hat, muss schlicht nein sagen,
    sonst kommt die Suche nie beim naechsten an. Findet ihn keiner,
    meldet der NameMapper selbst ``NotFound``, und der Pruefstand sieht
    es.
    """


class Recorder:
    """Haengt sich vor ein Objekt und schreibt jeden Zugriff mit.

    Der aufgezeichnete Baum liegt in ``tree`` und laesst sich direkt als
    JSON ablegen.

    Ein Rekorder traegt nur die Sondermethoden, die auch das Original
    hat. Python sucht ``__call__``, ``__getitem__`` und ``__iter__`` am
    Typ, nicht an der Instanz: waeren sie immer da, hielte Cheetahs
    Autocalling jeden Knoten fuer eine Funktion, und der C-NameMapper
    versuchte auf allem einen Schluesselzugriff. Deshalb bekommt jede
    Faehigkeitskombination ihre eigene Klasse.
    """

    def __init__(self, target: Any, tree: dict[str, Any] | None = None,
                 path: str = "$"):
        # Nicht ueber self.x setzen: __setattr__ ist umgeleitet.
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "tree", {} if tree is None else tree)
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name: str) -> Any:
        if name in METHOD_MARKS or name == CLASS_MARK:
            # Durchreichen, nicht aufzeichnen: das ist eine Frage nach
            # der Art des Objekts, kein Zugriff der Vorlage.
            return getattr(self._target, name)
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        try:
            value = getattr(self._target, name)
        except AttributeError:
            # Ein Schluessel muss wie ein Attribut aussehen. Cheetahs
            # hasKey() fragt mit hasattr(), und die oberste Ebene einer
            # searchList ist bei weewx ein gewoehnliches dict. Ohne
            # diesen Zweig faende der NameMapper dort nichts.
            try:
                value = self._target[name]
            except (TypeError, KeyError, IndexError):
                raise AttributeError(name) from None
        return self._child(ATTRS, name, value)

    def __str__(self) -> str:
        try:
            text = str(self._target)
        except Exception as exc:                        # noqa: BLE001
            self.tree[ERROR] = [type(exc).__name__, str(exc)]
            raise
        self.tree[TEXT] = text
        return text

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("ein aufgezeichneter Kontext wird nicht geschrieben")

    def _child(self, section: str, key: Any, value: Any,
               separator: str = ".") -> Any:
        # Erst holen, dann ablegen. Wirft der Zugriff, soll kein leerer
        # Platz im Baum zurueckbleiben; der saehe beim Abspielen wie ein
        # aufgezeichneter Wert aus.
        slot = self.tree.setdefault(section, {}).setdefault(str(key), {})
        return _wrap(value, slot, "%s%s%s" % (self._path, separator, key))


class _Subscriptable:
    def __getitem__(self, key: Any) -> Any:
        if not isinstance(key, str):
            # Ein Index in eine Liste fuehrt ueber items, nicht ueber
            # attrs: sonst haette der Baum Schluessel "0", "1", "2" und
            # verlore die Reihenfolge.
            return self._child(ITEMS, key, self._target[key])
        return self._child(ATTRS, key, self._target[key])

    def __contains__(self, key: Any) -> bool:
        return key in self._target


class _Iterable:
    def __iter__(self) -> Iterator[Any]:
        items = self.tree.setdefault(ITEMS, {})
        for index, element in enumerate(self._target):
            yield _wrap(element, items.setdefault(str(index), {}),
                        "%s[%d]" % (self._path, index))


class _Sized:
    def __len__(self) -> int:
        return len(self._target)


class _Callable:
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Ohne Trennzeichen: ein Aufruf haengt direkt am Namen, und der
        # Pfad muss dem entsprechen, den CallableReplay bildet. Sonst
        # passen die Schluessel nicht, wenn ein Knoten als Argument
        # weitergereicht wird.
        return self._child(CALLS, _signature(args, kwargs),
                           self._target(*args, **kwargs), separator="")


_MIXINS = (
    ("__getitem__", _Subscriptable),
    ("__iter__", _Iterable),
    ("__len__", _Sized),
    ("__call__", _Callable),
)
_CLASSES: dict[tuple[bool, ...], type] = {}


def _recorder_class(target: Any) -> type:
    """Die Rekorderklasse, die zu den Faehigkeiten des Ziels passt."""
    kind = type(target)
    caps = tuple(hasattr(kind, name) for name, _ in _MIXINS)
    if caps not in _CLASSES:
        bases = tuple(mixin for (_, mixin), has in zip(_MIXINS, caps) if has)
        _CLASSES[caps] = type("Recorder<%s>" % "".join(
            name for (name, _), has in zip(_MIXINS, caps) if has),
            bases + (Recorder,), {})
    return _CLASSES[caps]


def _signature(args: tuple, kwargs: dict) -> str:
    """Ein stabiler Schluessel fuer einen Aufruf.

    ``repr`` reicht fuer Zahlen und Zeichenketten (``$span(day_delta=1)``).
    Objekte ohne eigenes ``repr`` tragen ihre Speicheradresse darin; die
    wird entfernt, sonst passte der Schluessel nur im Lauf, der ihn
    aufgezeichnet hat.
    """
    parts = [_argument(value) for value in args]
    parts += ["%s=%s" % (name, _argument(value))
              for name, value in sorted(kwargs.items())]
    return _ADDRESS.sub("", "(%s)" % ", ".join(parts))


def _argument(value: Any) -> str:
    """Ein Argument als Schluesselteil.

    Ein Knoten wird ueber seinen Pfad benannt, nicht ueber sein ``repr``.
    weewx-Skins reichen Werte weiter, etwa
    ``$colorize($current.outTemp)``. Beim Aufzeichnen steckt dort ein
    Rekorder, beim Abspielen ein Abspieler; ihre ``repr`` sind
    verschieden, ihre Pfade sind gleich.
    """
    if isinstance(value, (Recorder, Replay)):
        return "<%s>" % value._path
    return repr(value)


def _wrap(value: Any, slot: dict[str, Any], path: str = "$") -> Any:
    """Legt einen gelesenen Wert ab und gibt zurueck, was die Vorlage sieht."""
    if isinstance(value, PLAIN):
        slot[PLAIN_VALUE] = value
        return value
    return _recorder_class(value)(value, slot, path)


class Replay:
    """Spielt einen aufgezeichneten Knoten ab."""

    __slots__ = ("_tree", "_path")

    def __init__(self, tree: dict[str, Any], path: str = "$"):
        self._tree = tree
        self._path = path

    def __getattr__(self, name: str) -> Any:
        if name in METHOD_MARKS:
            # Beim Aufzeichnen war das eine Methode, die Cheetah ohne
            # Argumente aufgerufen hat. Damit es beim Abspielen wieder
            # passiert, muss der Knoten hier dieselbe Auskunft geben.
            if "()" in self._tree.get(CALLS, {}):
                return self
            raise AttributeError(name)
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if name == CLASS_MARK:
            raise AttributeError(name)
        return self._lookup(name)

    def __getitem__(self, key: Any) -> Any:
        if not isinstance(key, str):
            items = self._tree.get(ITEMS, {})
            return replay(items[str(key)], "%s[%s]" % (self._path, key))
        return self._lookup(key)

    def __contains__(self, key: Any) -> bool:
        return str(key) in self._tree.get(ATTRS, {})

    def __iter__(self) -> Iterator[Any]:
        items = self._tree.get(ITEMS, {})
        for index in sorted(items, key=int):
            yield replay(items[index], "%s[%s]" % (self._path, index))

    def __len__(self) -> int:
        return len(self._tree.get(ITEMS, {}))

    def __str__(self) -> str:
        if ERROR in self._tree:
            raise _rebuild(self._tree[ERROR])
        if TEXT not in self._tree:
            raise Missing(
                "%s wurde beim Aufzeichnen nie ausgegeben" % self._path)
        return self._tree[TEXT]

    def _lookup(self, name: str) -> Any:
        try:
            child = self._tree[ATTRS][name]
        except KeyError:
            known = sorted(self._tree.get(ATTRS, {}))
            raise Missing(
                "%s.%s fehlt im Fixture; aufgezeichnet wurden: %s"
                % (self._path, name, ", ".join(known) or "nichts")) from None
        return replay(child, "%s.%s" % (self._path, name))


class CallableReplay(Replay):
    """Ein Abspieler fuer etwas, das die Vorlage aufruft."""

    __slots__ = ()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        key = _signature(args, kwargs)
        try:
            child = self._tree[CALLS][key]
        except KeyError:
            known = sorted(self._tree.get(CALLS, {}))
            raise Missing(
                "%s%s fehlt im Fixture; aufgezeichnet wurden: %s"
                % (self._path, key, ", ".join(known) or "nichts")) from None
        return replay(child, "%s%s" % (self._path, key))


def _rebuild(recorded: list) -> Exception:
    """Baut eine aufgezeichnete Ausnahme nach.

    Der Typ zaehlt, nicht nur die Meldung: weewx' Ausgabefilter faengt
    ``AttributeError`` und laesst alles andere durch. Ein Ersatztyp
    aenderte, was am Ende in der Datei steht.
    """
    import builtins

    name, message = recorded
    kind = getattr(builtins, name, None)
    if not (isinstance(kind, type) and issubclass(kind, BaseException)):
        kind = RuntimeError
        message = "%s: %s" % (name, message)
    return kind(message)


def replay(tree: dict[str, Any], path: str = "$") -> Any:
    """Macht aus einem aufgezeichneten Knoten wieder etwas Lesbares."""
    if PLAIN_VALUE in tree:
        return tree[PLAIN_VALUE]
    if CALLS in tree:
        return CallableReplay(tree, path)
    return Replay(tree, path)
