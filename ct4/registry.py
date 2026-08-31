"""Applications declare themselves.

A package registers itself in its own metadata file:

    [project.entry-points."ct4.plugins"]
    weewx = "weewx.ct4:plugin"

After that ct4 finds it through the installed environment, without
anybody setting an option. That is the difference to ct3's
``macroDirectives``: that was a compiler setting, had to be carried
through every ``Template`` construction, and a tool that only looks at
the file never found it.

A plugin is a module or an object. What it can do is asked for, not
demanded:

``declare()``
    returns a ``Declaration``: which names exist.
``install()``
    hooks in type adapters so that values explain themselves.

If one of them is missing, that is not an error. A plugin that only
declares types is a complete plugin.

In this repo the search finds nothing: ct4 is not installed here, and
entry points exist only for installed packages. The checked-in
declarations under ``declarations/`` therefore remain the main route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ct4.declare import Declaration

GROUP = "ct4.plugins"


@dataclass(frozen=True)
class Plugin:
    """A plugin that was found, and what it can do."""

    name: str
    target: Any

    def can(self, what: str) -> bool:
        return callable(getattr(self.target, what, None))

    def call(self, what: str) -> Any:
        return getattr(self.target, what)()


def entry_points(loader: Callable[[], Any] | None = None) -> list[Any]:
    """The entries of the group, or nothing when there are none."""
    if loader is not None:
        return list(loader())
    from importlib.metadata import entry_points as builtin

    try:
        return list(builtin(group=GROUP))
    except Exception:                                   # noqa: BLE001
        return []


def discover(loader: Callable[[], Any] | None = None) -> list[Plugin]:
    """Loads all the plugins that were found.

    A plugin that cannot be loaded is passed over and does not become an
    error of the whole run. A broken third-party package should not make
    ``ct4 check`` unusable.
    """
    found = []
    for entry in entry_points(loader):
        try:
            found.append(Plugin(entry.name, entry.load()))
        except Exception:                               # noqa: BLE001
            continue
    return found


def declarations(plugins: list[Plugin] | None = None) -> list[Declaration]:
    """What names the plugins declare."""
    out = []
    for plugin in (discover() if plugins is None else plugins):
        if plugin.can("declare"):
            result = plugin.call("declare")
            if isinstance(result, Declaration):
                out.append(result)
    return out


def install_all(plugins: list[Plugin] | None = None) -> list[str]:
    """Hooks in the type adapters of all the plugins.

    Returns which ones did so. Whoever wants to know whether a certain
    plugin took hold looks there; a silent doing-nothing would be hard
    to find.
    """
    done = []
    for plugin in (discover() if plugins is None else plugins):
        if plugin.can("install"):
            plugin.call("install")
            done.append(plugin.name)
    return done
