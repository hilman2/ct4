"""Record and replay a template context.

What gets recorded is not the context, but the access to it. That is
the distinction that matters: a weewx ``ValueHelper`` cannot be stored,
but what a template pulls out of it can.

A node holds four things, all optional:

``text``
    What ``str()`` returned. Cheetah passes every placeholder through a
    filter, and that filter calls ``str()`` in the end. Without this
    entry the output would not come out on replay.
``attrs``
    What was read through a dot or a key. Cheetah's NameMapper makes no
    difference between the two, so the fixture does not either.
``items``
    The elements, if the node was iterated over.
``calls``
    Results of calls, keyed by their arguments.

On replay this turns back into an object that behaves like the original
as far as the NameMapper is concerned. Whatever the template did not
read while recording is missing, and an access to it is an error with a
clear message. That is intentional: a silent empty value would be a
false green test.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterator

# Values that can sit in the JSON directly. Everything else is
# described through str() and its attributes.
PLAIN = (str, int, float, bool, type(None))

TEXT = "text"
ATTRS = "attrs"
ITEMS = "items"
CALLS = "calls"
PLAIN_VALUE = "value"

# An exception that was raised while recording. weewx skins demonstrate
# such cases on purpose: $day(data_binding='foo_binding') shows what a
# wrong binding produces. The filter catches the exception and writes a
# replacement text. Without recording it, exactly that text would be
# missing on replay.
ERROR = "error"

# The memory address in a default repr changes with every run. weewx
# skins pass such objects around, for example $jsonize(zip(...)).
# Without trimming it away, the replayer would never find the call
# again.
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")

# How Cheetah tells whether a value is a bound method and therefore has
# to be called (isInstanceOrClass in _namemapper.c). weewx' $day is such
# a method: it yields the TimespanBinder in the first place. A recorder
# that withholds these names looks like an instance, does not get
# called, and $day.hours finds nothing any more.
METHOD_MARKS = ("__func__", "__code__", "__self__")

# The same procedure asks for "mro" first, to recognise classes. The
# answer has to come from the original and must not land in the tree.
CLASS_MARK = "mro"


class Missing(AttributeError):
    """The template reads something that was not read while recording.

    Inherits from ``AttributeError``, and that is essential: Cheetah
    asks every namespace of the searchList in turn whether it knows a
    name. A namespace that does not have it must simply say no,
    otherwise the search never gets to the next one. If none of them
    has it, the NameMapper reports ``NotFound`` itself, and the test rig
    sees it.
    """


class Recorder:
    """Sits in front of an object and records every access to it.

    The recorded tree lives in ``tree`` and can be stored as JSON
    directly.

    A recorder carries only those special methods that the original has
    too. Python looks up ``__call__``, ``__getitem__`` and ``__iter__``
    on the type, not on the instance: were they always there, Cheetah's
    autocalling would take every node for a function, and the C
    NameMapper would attempt a key access on everything. Each
    combination of capabilities therefore gets its own class.
    """

    def __init__(self, target: Any, tree: dict[str, Any] | None = None,
                 path: str = "$"):
        # Do not set through self.x: __setattr__ is redirected.
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "tree", {} if tree is None else tree)
        object.__setattr__(self, "_path", path)

    def __getattr__(self, name: str) -> Any:
        if name in METHOD_MARKS or name == CLASS_MARK:
            # Pass through, do not record: this asks what kind of
            # object this is, it is not an access by the template.
            return getattr(self._target, name)
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        try:
            value = getattr(self._target, name)
        except AttributeError:
            # A key has to look like an attribute. Cheetah's hasKey()
            # asks with hasattr(), and the top level of a searchList is
            # a plain dict in weewx. Without this branch the NameMapper
            # would find nothing there.
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
        raise TypeError("a recorded context is not written to")

    def _child(self, section: str, key: Any, value: Any,
               separator: str = ".") -> Any:
        # Fetch first, then store. If the access raises, no empty slot
        # should be left behind in the tree; on replay it would look
        # like a recorded value.
        slot = self.tree.setdefault(section, {}).setdefault(str(key), {})
        return _wrap(value, slot, "%s%s%s" % (self._path, separator, key))


class _Subscriptable:
    _target: Any
    _path: str
    tree: dict[str, Any]
    _child: Callable[..., Any]

    def __getitem__(self, key: Any) -> Any:
        if not isinstance(key, str):
            # An index into a list goes through items, not through
            # attrs: otherwise the tree would carry keys "0", "1", "2"
            # and lose the order.
            return self._child(ITEMS, key, self._target[key])
        return self._child(ATTRS, key, self._target[key])

    def __contains__(self, key: Any) -> bool:
        return key in self._target


class _Iterable:
    _target: Any
    _path: str
    tree: dict[str, Any]
    _child: Callable[..., Any]

    def __iter__(self) -> Iterator[Any]:
        items = self.tree.setdefault(ITEMS, {})
        for index, element in enumerate(self._target):
            yield _wrap(element, items.setdefault(str(index), {}),
                        "%s[%d]" % (self._path, index))


class _Sized:
    _target: Any
    _path: str
    tree: dict[str, Any]
    _child: Callable[..., Any]

    def __len__(self) -> int:
        return len(self._target)


class _Callable:
    _target: Any
    _path: str
    tree: dict[str, Any]
    _child: Callable[..., Any]

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # No separator: a call hangs directly off the name, and the
        # path has to match the one CallableReplay builds. Otherwise
        # the keys do not line up when a node is passed on as an
        # argument.
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
    """The recorder class that fits the target's capabilities."""
    kind = type(target)
    caps = tuple(hasattr(kind, name) for name, _ in _MIXINS)
    if caps not in _CLASSES:
        bases = tuple(mixin for (_, mixin), has in zip(_MIXINS, caps) if has)
        _CLASSES[caps] = type("Recorder<%s>" % "".join(
            name for (name, _), has in zip(_MIXINS, caps) if has),
            bases + (Recorder,), {})
    return _CLASSES[caps]


def _signature(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    """A stable key for a call.

    ``repr`` is enough for numbers and strings (``$span(day_delta=1)``).
    Objects without a ``repr`` of their own carry their memory address
    in it; that gets removed, otherwise the key would only fit the run
    that recorded it.
    """
    parts = [_argument(value) for value in args]
    parts += ["%s=%s" % (name, _argument(value))
              for name, value in sorted(kwargs.items())]
    return _ADDRESS.sub("", "(%s)" % ", ".join(parts))


def _argument(value: Any) -> str:
    """An argument as part of a key.

    A node is named by its path, not by its ``repr``. weewx skins pass
    values on, for example ``$colorize($current.outTemp)``. While
    recording there is a recorder in that spot, on replay a replayer;
    their ``repr`` differ, their paths are the same.
    """
    if isinstance(value, (Recorder, Replay)):
        return "<%s>" % value._path
    return repr(value)


def _wrap(value: Any, slot: dict[str, Any], path: str = "$") -> Any:
    """Stores a value that was read and returns what the template sees."""
    if isinstance(value, PLAIN):
        slot[PLAIN_VALUE] = value
        return value
    return _recorder_class(value)(value, slot, path)


class Replay:
    """Replays a recorded node."""

    __slots__ = ("_tree", "_path")

    def __init__(self, tree: dict[str, Any], path: str = "$"):
        self._tree = tree
        self._path = path

    def __getattr__(self, name: str) -> Any:
        if name in METHOD_MARKS:
            # While recording this was a method that Cheetah called
            # without arguments. For that to happen again on replay,
            # the node has to give the same answer here.
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
        try:
            return self._lookup(key)
        except Missing as exc:
            # A key access that goes nowhere reports KeyError. Missing
            # is an AttributeError, and PyMapping_HasKeyString does not
            # expect one: it reports it as an ignored exception, and
            # the run carries on regardless.
            raise KeyError(str(exc)) from None

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
                "%s was never rendered while recording" % self._path)
        return str(self._tree[TEXT])

    def _lookup(self, name: str) -> Any:
        try:
            child = self._tree[ATTRS][name]
        except KeyError:
            known = sorted(self._tree.get(ATTRS, {}))
            raise Missing(
                "%s.%s is missing from the fixture; recorded were: %s"
                % (self._path, name, ", ".join(known) or "nothing")) from None
        return replay(child, "%s.%s" % (self._path, name))


class CallableReplay(Replay):
    """A replayer for something the template calls."""

    __slots__ = ()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        key = _signature(args, kwargs)
        try:
            child = self._tree[CALLS][key]
        except KeyError:
            known = sorted(self._tree.get(CALLS, {}))
            raise Missing(
                "%s%s is missing from the fixture; recorded were: %s"
                % (self._path, key, ", ".join(known) or "nothing")) from None
        return replay(child, "%s%s" % (self._path, key))


def _rebuild(recorded: list[str]) -> Exception:
    """Rebuilds a recorded exception.

    The type counts, not just the message: weewx' output filter catches
    ``AttributeError`` and lets everything else through. A substitute
    type would change what ends up in the file.
    """
    import builtins

    name, message = recorded
    kind = getattr(builtins, name, None)
    if not (isinstance(kind, type) and issubclass(kind, Exception)):
        kind = RuntimeError
        message = "%s: %s" % (name, message)
    return kind(message)


def replay(tree: dict[str, Any], path: str = "$") -> Any:
    """Turns a recorded node back into something readable."""
    if PLAIN_VALUE in tree:
        return tree[PLAIN_VALUE]
    if CALLS in tree:
        return CallableReplay(tree, path)
    return Replay(tree, path)
