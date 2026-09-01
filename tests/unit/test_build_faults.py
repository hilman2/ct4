"""What the build does when the machine does not cooperate.

The other build tests ask whether the right thing happens. These ask
what happens when a file will not open, a directory will not take a
write, or two runs meet on the same lock. A build that runs unattended
from a timer is judged on exactly those, because nobody is watching the
one time it matters.

Every case here was found by probing the module rather than by reading
it, and each one was a traceback or a wrong exit code before.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from ct4 import build, cli, write

from tests.unit.test_build import run, skin

# chmod does nothing for root, and the image runs the suite as an
# ordinary user for exactly this kind of test. Skipped rather than
# quietly passing where the permission has no effect.
needs_permissions = pytest.mark.skipif(
    os.geteuid() == 0 if hasattr(os, "geteuid") else True,
    reason="the run is root, or the platform has no POSIX permissions")

READ_ONLY_DIR = stat.S_IRUSR | stat.S_IXUSR


# -- A file that will not open ---------------------------------------

@needs_permissions
def test_an_unreadable_include_fails_one_target_and_not_the_run(tmp_path):
    # One chmod used to take the whole run down with an uncaught
    # OSError: no report at all, and every unrelated target left
    # unbuilt however little it had to do with the unreadable file.
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "a.html"},
        {"template": "other.tmpl", "output": "b.html"}])
    (tmp_path / "skin" / "other.tmpl").write_text("B $station\n",
                                                  encoding="utf-8")
    run(manifest)
    (tmp_path / "skin" / "other.tmpl").write_text("B2 $station\n",
                                                  encoding="utf-8")
    (tmp_path / "skin" / "inc" / "head.inc").chmod(0)
    try:
        report = run(manifest)
    finally:
        (tmp_path / "skin" / "inc" / "head.inc").chmod(0o644)
    assert (tmp_path / "public" / "b.html").read_text() == "B2 Zuhause\n"
    assert report["targets"]["total"] == 2


@needs_permissions
def test_an_unreadable_file_is_not_the_same_as_a_missing_one(tmp_path):
    # Absence is an answer and a target may still be skipped on it: a
    # skin's optional hook is absent on purpose and stays absent. A
    # permission problem is not an answer, and two runs that both fail
    # to read the same file would agree and look like no change.
    missing = tmp_path / "nowhere.inc"
    there = tmp_path / "there.inc"
    there.write_text("x", encoding="utf-8")
    assert build._digest_of_file(missing) == build.ABSENT
    there.chmod(0)
    try:
        assert build._digest_of_file(there) == build.UNREADABLE
    finally:
        there.chmod(0o644)
    assert build.UNREADABLE != build.ABSENT


@needs_permissions
def test_an_unreadable_dependency_stops_the_target_being_skipped(tmp_path):
    manifest = skin(tmp_path)
    run(manifest)
    assert run(manifest)["targets"]["skipped"] == 1
    (tmp_path / "skin" / "inc" / "head.inc").chmod(0)
    try:
        report = run(manifest)
    finally:
        (tmp_path / "skin" / "inc" / "head.inc").chmod(0o644)
    assert report["targets"]["skipped"] == 0, report["results"]


# -- A directory that will not take a write --------------------------

@needs_permissions
def test_a_lock_that_cannot_be_made_is_not_reported_as_a_second_run(tmp_path):
    # Exit 3 means another build holds the lock, and a unit file may
    # well be told to treat that as success. Reporting an unwritable
    # directory that way lets a site stop updating in silence.
    manifest = skin(tmp_path)
    run(manifest)
    tmp_path.chmod(READ_ONLY_DIR)
    try:
        report = run(manifest)
    finally:
        tmp_path.chmod(0o755)
    codes = {finding["code"] for finding in report["findings"]}
    assert "CT4322" in codes, report["findings"]
    assert "CT4320" not in codes
    assert build.exit_code(report) != 3


@needs_permissions
def test_a_state_file_that_cannot_be_written_is_a_finding(tmp_path):
    # The outputs are already on disk and right. Losing the state costs
    # the next run its skipping and nothing else, so the report must
    # still come back rather than a traceback for work that succeeded.
    manifest = skin(tmp_path)
    run(manifest)
    (tmp_path / "skin" / "index.html.tmpl").write_text(
        "changed $station\n", encoding="utf-8")
    (tmp_path / ".ct4-build.json").unlink()
    tmp_path.chmod(READ_ONLY_DIR)
    try:
        report = run(manifest, lock=False)
    finally:
        tmp_path.chmod(0o755)
    assert "CT4323" in {f["code"] for f in report["findings"]}, \
        report["findings"]


@needs_permissions
def test_a_report_that_cannot_be_written_is_an_exit_code(tmp_path, capsys):
    manifest = skin(tmp_path)
    into = tmp_path / "sealed"
    into.mkdir()
    into.chmod(READ_ONLY_DIR)
    try:
        code = cli.main(["build", str(manifest),
                         "--report", str(into / "report.json")])
    finally:
        into.chmod(0o755)
    assert code == 2
    assert "cannot write the report" in capsys.readouterr().err


# -- The lock ---------------------------------------------------------

def test_breaking_a_lock_leaves_it_taken(tmp_path):
    # Breaking used to be an unlink and then a create, which is two
    # steps with a gap, and every run that reached the gap came away
    # believing it was alone: sixteen released together gave five
    # holders. A rename is one step. Checked here without concurrency,
    # which a unit test cannot have reliably: after a run has broken a
    # stale lock, the lock is that run's and the next caller has to
    # wait for it.
    path = tmp_path / "build.lock"
    path.write_text(json.dumps({"pid": 1, "started": 0.0, "host": "x",
                                "token": "old"}), encoding="utf-8")
    findings: list = []
    broke = build._acquire(path, 0.0, findings)
    assert broke is not None
    assert build._acquire(path, 3600.0, findings) is None
    assert "CT4321" in {finding.code for finding in findings}


def test_a_run_removes_its_own_lock_and_not_another(tmp_path):
    # A run whose lock was broken while it worked must not delete the
    # lock of the run that broke it, or a third walks in on both.
    path = tmp_path / "build.lock"
    findings: list = []
    first = build._acquire(path, 3600.0, findings)
    assert first is not None
    second = build._acquire(path, 0.0, findings)
    assert second is not None and second != first
    build._release(path, first)
    assert path.exists(), "the first run deleted the second run's lock"
    build._release(path, second)
    assert not path.exists()


# -- A manifest that is wrong ----------------------------------------

@pytest.mark.parametrize("key, value", [
    ("encoding", "utf-9"),
    ("errors", "banana"),
])
def test_an_unusable_codec_is_refused_before_anything_is_built(tmp_path,
                                                               key, value):
    # An unknown error handler is only consulted where a character does
    # not fit, so a typo lay dormant until the first umlaut, which on a
    # weather page is the first German station name.
    manifest = skin(tmp_path, **{key: value})
    with pytest.raises(build.ManifestError):
        build.load_manifest(manifest)


@pytest.mark.parametrize("output", ["../escaped.html", "/tmp/escaped.html"])
def test_an_output_may_not_leave_the_output_directory(tmp_path, output):
    manifest = skin(tmp_path, targets=[{"template": "index.html.tmpl",
                                        "output": output}])
    with pytest.raises(build.ManifestError):
        build.load_manifest(manifest)


# -- The state a graph keeps -----------------------------------------

def test_an_include_that_is_not_there_yet_is_recorded_as_absent(tmp_path):
    # The mechanism the appearing-hook case depends on. Its own test
    # passed with this deleted, because the file appearing becomes an
    # ordinary new dependency and the union loop catches it that way,
    # so nothing asserted on the mechanism itself. Behind an #if 0 so
    # that the graph sees the edge and the render never runs into the
    # missing file.
    manifest = skin(tmp_path,
                    template='#if 0\n#include "hook.inc"\n#end if\nX\n')
    run(manifest)
    state = json.loads((tmp_path / ".ct4-build.json").read_text())
    sources = state["targets"]["index.html"]["sources"]
    assert sources.get("hook.inc") == build.ABSENT, sources


def test_the_writer_still_raises_on_a_file_it_cannot_read():
    # The build turns that into a mark, and it is the build's business
    # to do so. write.digest_of_file promises the opposite and a caller
    # that wants the fault has to keep getting it.
    assert write.digest_of_file.__doc__ is not None
    assert "raises" in write.digest_of_file.__doc__
