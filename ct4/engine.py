"""Is the Cheetah on the path the one this tooling belongs to?

Two distributions claim the import package ``Cheetah``: CT3, which
weewx depends on, and this one. Installed next to each other, whichever
was installed last overwrites the other's files, and pip says nothing
about it. Measured on 31-Aug-2026 in a clean environment:

    ct4 installed, then CT3 pulled in as weewx pulls it
        -> the engine is 3.4.0.post5 again
        -> pip lists both, and reports this distribution as installed
        -> every ct4 command still runs, against the older engine

That is the failure worth guarding: everything looks fine. The tooling
answers, the JSON mode renders, and the engine underneath is the one
this project forked away from.

The check lives here rather than in ``Cheetah``, and it has to. If CT3
overwrote the package, any check inside it went with the files.

It cannot protect weewx itself, which never calls a ct4 command. The
only thing that would is a distribution name weewx's requirement
already accepts.
"""

from __future__ import annotations

# The first version of the engine that belongs to this tooling.
REQUIRED = 4


def version() -> tuple[object, ...]:
    """The version tuple of the Cheetah that will be imported."""
    import Cheetah

    return tuple(Cheetah.VersionTuple)


def location() -> str:
    import Cheetah

    return str(Cheetah.__file__)


def matches() -> bool:
    """Whether the engine on the path is the one this tooling expects."""
    try:
        major = version()[0]
    except ImportError:
        return False
    return isinstance(major, int) and major >= REQUIRED


def owners() -> list[str]:
    """Which installed distributions ship a package named Cheetah.

    Only asked when something is already wrong: walking the installed
    distributions reads metadata from disk, and no normal run should
    pay for that.

    Returns:
        list[str]: One "name version -> path" line per distribution, or
        an empty list where the environment cannot be inspected.
    """
    try:
        from importlib.metadata import distributions
    except ImportError:                                     # noqa: BLE001
        return []
    found = []
    for dist in distributions():
        files = dist.files or []
        if not any(str(f).startswith("Cheetah/") for f in files):
            continue
        try:
            where = str(dist.locate_file("Cheetah/__init__.py"))
        except Exception:                                   # noqa: BLE001
            where = "?"
        found.append("%s %s -> %s"
                     % (dist.metadata["Name"], dist.version, where))
    return sorted(found)


def complaint() -> str:
    """What to tell somebody whose engine is the wrong one."""
    lines = [
        "This is Cheetah4 tooling, but the Cheetah on the import path is"
        " version %s." % ".".join(str(p) for p in version()[:3]),
        "  loaded from: %s" % location(),
        "",
        "CT3 and Cheetah4 both install a package called Cheetah. Whichever"
        " was installed last overwrote the other, and pip does not warn"
        " about it. weewx depends on CT3, so installing or upgrading weewx"
        " brings CT3 back.",
    ]
    installed = owners()
    if installed:
        lines += ["", "Distributions shipping a Cheetah package:"]
        lines += ["  %s" % line for line in installed]
    lines += [
        "",
        "To get the Cheetah4 engine back:",
        "  pip install --force-reinstall --no-deps Cheetah4",
        "",
        "Do not uninstall CT3 while Cheetah4 is installed: its file list"
        " still names Cheetah/, so removing it deletes the files Cheetah4"
        " now owns and leaves an installation pip believes is intact.",
    ]
    return "\n".join(lines)


def require() -> None:
    """Stops with an explanation where the engine is the wrong one.

    Raises:
        SystemExit: with the text of ``complaint``.
    """
    if matches():
        return
    raise SystemExit(complaint())
