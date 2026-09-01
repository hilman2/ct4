"""What content-based writing has to guarantee.

rsync's quick check is size plus mtime, and weewx runs it without
--checksum. A file rewritten with identical bytes is therefore uploaded
again every cycle, and the assertion that matters most here is a
negative one: on identical content the target keeps its mtime, its
inode and its mode, and nothing opens it at all.

The rest measures the failure modes found in weewx's own write path: a
mode that flaps between 0640 and 0644 depending on whether the weather
changed, one fixed temporary name two report threads share, and a
temporary name built with Path.with_suffix that eats the .html of
index.html.

Everything happens under tmp_path. The Docker entrypoint copies a fixed
list into a tmpfs and mounts the repo read-only, and the run spreads
over all cores, so a test that reads outside its own directory or
depends on the order is a test that fails somewhere else.
"""

from __future__ import annotations

import os
import stat

import pytest

from ct4 import write

# A fixed point in the past for the pre-existing file. Comparing a
# later stat against the one taken before the call says "the mtime
# moved" without depending on the file system's timestamp resolution,
# which is a whole second on some of them.
OLD = 1_000_000_000

posix_only = pytest.mark.skipif(os.name == "nt",
                                reason="no POSIX permission bits")


def existing(tmp_path, content=b"old", name="page.html"):
    """A target that is already there, carrying an old mtime."""
    path = tmp_path / name
    path.write_bytes(content)
    os.utime(path, (OLD, OLD))
    return path


# -- Identical bytes -------------------------------------------------

def test_identical_bytes_leave_the_file_completely_alone(tmp_path):
    path = existing(tmp_path, b"same")
    before = path.stat()
    result = write.write(path, b"same")
    after = path.stat()
    assert result.status == write.UNCHANGED
    assert after.st_ino == before.st_ino
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_mode == before.st_mode
    assert result.digest == write.digest_of(b"same")
    assert result.touched is False


def test_touching_an_unchanged_file_moves_only_its_mtime(tmp_path):
    path = existing(tmp_path, b"same")
    before = path.stat()
    result = write.write(path, b"same", touch_unchanged=True)
    after = path.stat()
    assert result.status == write.UNCHANGED
    assert result.touched is True
    assert after.st_mtime_ns > before.st_mtime_ns
    assert after.st_ino == before.st_ino
    assert path.read_bytes() == b"same"


def test_an_empty_proposal_against_an_empty_file_is_unchanged(tmp_path):
    path = existing(tmp_path, b"")
    before = path.stat()
    result = write.write(path, b"")
    assert result.status == write.UNCHANGED
    assert path.stat().st_mtime_ns == before.st_mtime_ns


# -- Different bytes -------------------------------------------------

def test_different_bytes_replace_the_content(tmp_path):
    path = existing(tmp_path, b"old")
    before = path.stat()
    result = write.write(path, b"new content")
    after = path.stat()
    assert result.status == write.WRITTEN
    assert result.size == len(b"new content")
    assert path.read_bytes() == b"new content"
    assert after.st_mtime_ns > before.st_mtime_ns


def test_an_empty_proposal_against_a_full_file_is_written(tmp_path):
    path = existing(tmp_path, b"content")
    result = write.write(path, b"")
    assert result.status == write.WRITTEN
    assert path.read_bytes() == b""


def test_a_durable_write_puts_the_same_bytes_there(tmp_path):
    path = tmp_path / "page.html"
    result = write.write(path, b"hello", durable=True)
    assert result.status == write.CREATED
    assert path.read_bytes() == b"hello"


# -- Permissions -----------------------------------------------------

@posix_only
def test_the_mode_of_the_target_survives_a_write(tmp_path):
    # The one that catches the flapping-permission bug: the temporary
    # file is created fresh under the umask, so without carrying the
    # mode over the report file would be 0644 on a change and 0640 on
    # a skip.
    path = existing(tmp_path, b"old")
    os.chmod(path, 0o640)
    write.write(path, b"new")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


# -- New files -------------------------------------------------------

def test_a_new_file_gets_its_directories(tmp_path):
    path = tmp_path / "deep" / "down" / "index.html"
    result = write.write(path, b"hello")
    assert result.status == write.CREATED
    assert path.read_bytes() == b"hello"


@posix_only
def test_an_explicit_mode_applies_to_a_new_file(tmp_path):
    path = tmp_path / "new.html"
    write.write(path, b"hello", mode=0o604)
    assert stat.S_IMODE(path.stat().st_mode) == 0o604


# -- Failure ---------------------------------------------------------

def test_a_failed_replace_leaves_the_target_as_it_was(tmp_path,
                                                      monkeypatch):
    path = existing(tmp_path, b"published")
    before = path.stat()

    def refuse(source, target):
        raise OSError("no")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        write.write(path, b"something else")

    assert path.read_bytes() == b"published"
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert list(tmp_path.glob("#*")) == []
    assert list(tmp_path.glob("*.tmp")) == []


# -- The temporary name ----------------------------------------------

def test_the_temporary_name_keeps_the_suffix_and_hides_from_ftp(
        tmp_path):
    temporary = write.temporary_name(tmp_path / "index.html")
    assert temporary.parent == tmp_path
    assert temporary.name.startswith("#")
    assert "index.html" in temporary.name
    assert str(os.getpid()) in temporary.name
    assert temporary.name.endswith(".tmp")


# -- changed() -------------------------------------------------------

def test_changed_says_no_for_identical_content(tmp_path):
    path = existing(tmp_path, b"same")
    assert write.changed(path, b"same") is False


def test_changed_says_yes_for_a_missing_file(tmp_path):
    assert write.changed(tmp_path / "gone.html", b"x") is True


def test_changed_says_yes_at_the_same_length(tmp_path):
    path = existing(tmp_path, b"abc")
    assert write.changed(path, b"abd") is True


def test_changed_says_yes_when_the_file_is_the_longer_one(tmp_path):
    # The file opens with exactly the proposed bytes. A comparison
    # that stopped at the end of the proposal would call this equal
    # and never publish the shortened page.
    path = existing(tmp_path, b"abcdef")
    assert write.changed(path, b"abc") is True


def test_a_difference_past_the_first_chunk_is_found(tmp_path):
    body = b"a" * (write.CHUNK * 2 + 17)
    path = existing(tmp_path, body)
    assert write.changed(path, body) is False
    assert write.changed(path, body[:-1] + b"b") is True


# -- Digests ---------------------------------------------------------

def test_the_digest_of_a_missing_file_is_none(tmp_path):
    assert write.digest_of_file(tmp_path / "gone.html") is None


def test_the_digest_on_disk_matches_the_reported_one(tmp_path):
    path = tmp_path / "page.html"
    result = write.write(path, b"hello")
    assert result.digest == write.digest_of(b"hello")
    assert write.digest_of_file(path) == result.digest


# -- Types -----------------------------------------------------------

def test_write_refuses_a_str(tmp_path):
    path = tmp_path / "page.html"
    with pytest.raises(TypeError):
        write.write(path, "text")
    assert not path.exists()
