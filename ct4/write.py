"""Write a file only when its bytes would differ.

A report generator regenerates a directory every few minutes and
something uploads it afterwards. rsync's quick check is size plus
mtime, and weewx runs ``rsync --archive`` without ``--checksum``, so a
file rewritten with identical content is transferred again every cycle.
weewx today writes a temporary file and renames it unconditionally;
measured on ext4 that installs a new inode with a new mtime on
byte-identical content, and the whole directory goes over the wire
again.

So the write is content based: compare first, and on no difference
leave the file completely alone. Untouched means untouched, mtime and
mode and inode included, because those three are what the uploader
looks at.

The comparison is always against the file that is on disk right now,
never against a remembered hash of what was written last run. Other
programs write into the same output directory: weewx's ImageGenerator
draws plots there, CopyGenerator copies files there, and admins edit.
A stale memory of the target would silently stop publishing real
changes, and that is the failure nobody notices for weeks.

The comparison takes bytes, not text, and refuses str. The same string
encodes to different bytes under utf-8, ascii or an entity filter, so
the encoding decides what is on disk and the comparison has to happen
after it.
"""

from __future__ import annotations

import hashlib
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

#: The target did not exist before this write.
CREATED = "created"
#: The target existed and held other bytes.
WRITTEN = "written"
#: The target already held exactly these bytes. Nothing was written.
UNCHANGED = "unchanged"

# Read the target back in pieces rather than in one buffer. A skin
# writes megabyte pages, and the comparison should not hold a second
# copy of one next to the proposal.
CHUNK = 64 * 1024

# Windows has no os.chown at all. Restoring the owner is best effort
# even where it exists, so the whole attempt hangs off one flag instead
# of a try around every call.
_CAN_CHOWN = hasattr(os, "chown")


@dataclass(frozen=True)
class Written:
    """What one call to :func:`write` did to one file.

    Attributes:
        path (pathlib.Path): The target, as it was passed in.
        status (str): One of ``CREATED``, ``WRITTEN``, ``UNCHANGED``.
        size (int): Length of the proposed bytes.
        digest (str): sha256 of the proposed bytes, hex. Set in every
            case, ``UNCHANGED`` included, because a caller keeping
            state wants the fingerprint of what is now on disk whether
            or not this run put it there.
        touched (bool): Whether os.utime was called on an unchanged
            file. Only ever true together with ``UNCHANGED``.
    """

    path: Path
    status: str
    size: int
    digest: str
    touched: bool = False


def digest_of(data: bytes) -> str:
    """The sha256 of some bytes, hex.

    Args:
        data (bytes): The content to fingerprint.

    Returns:
        str: The hex digest.
    """
    return hashlib.sha256(data).hexdigest()


def digest_of_file(path: Path) -> str | None:
    """The sha256 of a file's content, hex.

    Args:
        path (pathlib.Path): The file to read.

    Returns:
        str|None: The hex digest, or None if the file is not there. A
        file that exists and cannot be read raises; only absence is an
        answer rather than a fault.
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except FileNotFoundError:
        return None
    return digest.hexdigest()


def changed(path: Path, data: bytes) -> bool:
    """Whether writing these bytes would change the file.

    The verdict without the write, for a dry run. It does not hash:
    hashing reads the whole file even when the first byte already
    differs, and the size alone settles most cases.

    Args:
        path (pathlib.Path): The target to compare against.
        data (bytes): The content that would be written.

    Returns:
        bool: True if the file is missing, unreadable, of a different
        length, or differs somewhere in its content.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return True
    if size != len(data):
        return True
    offset = 0
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(CHUNK)
                if not chunk:
                    break
                if chunk != data[offset:offset + len(chunk)]:
                    return True
                offset += len(chunk)
    except OSError:
        return True
    # The stat said one length and the read delivered another, so
    # somebody truncated the file between the two. Call it changed and
    # let the write settle it.
    return offset != len(data)


def temporary_name(path: Path) -> Path:
    """The name to write under before replacing the target.

    Appends to the name instead of replacing the suffix, so
    ``index.html`` becomes ``#index.html.1234.5678.tmp`` beside it. The
    three parts each answer a measured failure:

    ``Path.with_suffix`` would turn ``index.html`` into
    ``index.1234.tmp`` and lose the ``.html``, which matters where the
    server or the uploader keys on the extension. The pid and the
    thread id are there because weewx uses one fixed ``.tmp`` name
    while its engine starts an overlapping report thread once the
    previous one exceeds max_wait, and the two threads then delete each
    other's file mid-flight. The ``#`` is there because weewx's FTP
    upload skips names starting with it, so a temporary left behind by
    a SIGKILL is not published to the web server as-is.

    Args:
        path (pathlib.Path): The target the write is aimed at.

    Returns:
        pathlib.Path: A sibling of the target, in the same directory
        and therefore on the same file system.
    """
    return path.with_name("#%s.%d.%d.tmp" % (path.name, os.getpid(),
                                             threading.get_ident()))


def _restore_owner(temporary: Path, previous: os.stat_result) -> None:
    """Give the new file the owner the old one had, if that is allowed.

    Args:
        temporary (pathlib.Path): The file about to replace the target.
        previous (os.stat_result): The target as it was before.
    """
    if not _CAN_CHOWN:
        return
    current = temporary.stat()
    if (current.st_uid, current.st_gid) == (previous.st_uid,
                                            previous.st_gid):
        return
    try:
        os.chown(temporary, previous.st_uid, previous.st_gid)
    except OSError:
        # An unprivileged process cannot give a file away, and that is
        # the normal case. The content is what the caller asked for;
        # refusing the write over the owner would be worse than the
        # wrong owner.
        pass


def atomic_write(path: Path, data: bytes, *, mode: int | None = None,
                 durable: bool = False, parents: bool = True) -> None:
    """Put these bytes at this path, all at once or not at all.

    Writes a sibling temporary file and moves it over the target with
    os.replace. Same directory means same file system, which is what
    makes the move atomic, and it inherits the group a setgid output
    directory hands out. os.replace rather than os.rename because
    os.rename fails on Windows when the destination exists.

    If the target existed, its permissions and, where the process may,
    its owner are carried over to the replacement. Otherwise ``mode``
    applies if given and the umask decides if not. Preserving on the
    write path and not only on the skip path is what keeps a report
    file from flapping between 0640 and 0644 depending on whether its
    content changed.

    On any failure the target is left exactly as it was: it is never
    opened for writing, truncated or unlinked, and the temporary file
    is removed.

    Args:
        path (pathlib.Path): The target.
        data (bytes): The content to put there.
        mode (int|None): Permission bits for a target that does not
            exist yet. Ignored when it does, because the existing
            file's own mode wins.
        durable (bool): Whether to fsync the temporary file before
            replacing. Off by default: a report regenerated in five
            minutes does not justify an fsync per file. Without it a
            power cut can leave the new file short while the old one is
            already gone.
        parents (bool): Whether to create the target's directory.
    """
    if parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous: os.stat_result | None = path.stat()
    except OSError:
        previous = None
    temporary = temporary_name(path)
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        if previous is not None:
            os.chmod(temporary, stat.S_IMODE(previous.st_mode))
            _restore_owner(temporary, previous)
        elif mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        # Gone already after a successful replace. Anything else here
        # is a leftover of a failure, and failing to clean it up must
        # not mask the failure that caused it.
        try:
            temporary.unlink()
        except OSError:
            pass


def write(path: Path, data: bytes, *, mode: int | None = None,
          touch_unchanged: bool = False, durable: bool = False,
          parents: bool = True) -> Written:
    """Write these bytes, unless they are already there.

    On no difference nothing at all happens to the target: no open, no
    temporary file, no new mtime, no new mode, no new inode. That is
    the point of the module, because it is what keeps rsync and FTP
    from transferring the file again.

    A target that is a symlink is replaced by a regular file, the same
    way weewx's rename does. The link is not followed and not kept.

    Args:
        path (pathlib.Path): The target.
        data (bytes): The content. Must be bytes; the caller encodes,
            because the encoding decides the bytes the comparison runs
            on.
        mode (int|None): Permission bits for a target that does not
            exist yet. An existing target keeps its own.
        touch_unchanged (bool): Whether to os.utime an unchanged file.
            Off by default, because leaving it alone is the whole
            point. Set it for outputs whose own mtime another program
            reads as a clock: weewx's stale_age and its report_timing
            @createIfMissing both do, and for those an mtime that never
            moves means the age never resets and the template
            regenerates every cycle forever.
        durable (bool): Passed to :func:`atomic_write`.
        parents (bool): Whether to create the target's directory.

    Returns:
        Written: What happened, with the digest of ``data`` set in
        every case.

    Raises:
        TypeError: If ``data`` is a str.
    """
    if isinstance(data, str):
        raise TypeError(
            "write() takes bytes, not str: encode first, because the "
            "encoding decides the bytes that get compared")
    digest = digest_of(data)
    if not changed(path, data):
        touched = False
        if touch_unchanged:
            os.utime(path, None)
            touched = True
        return Written(path, UNCHANGED, len(data), digest, touched)
    status = WRITTEN if path.exists() else CREATED
    atomic_write(path, data, mode=mode, durable=durable, parents=parents)
    return Written(path, status, len(data), digest)
