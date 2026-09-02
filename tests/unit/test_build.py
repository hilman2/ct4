"""The batch build.

The promise this makes to a five-minute cron job is narrow and it is
testable: a target whose inputs did not change is not rendered, and a
target whose output bytes did not change is not written. Everything
here holds that promise against one small skin in tmp_path, and the
assertion the whole feature exists for is the one on st_mtime_ns.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

from ct4 import build, cli, diagnostics

TEMPLATE = '#include "inc/head.inc"\nStation: $station\n'
HEAD = "<head>\n"
CONTEXT = {"station": "Zuhause"}


@pytest.fixture(autouse=True)
def clean_caches():
    """Every case starts with nothing remembered from the last one.

    Two caches outlive a test and both decide what a build does. ct3
    keeps compiled classes in a dict on Template, keyed by the source,
    so two cases using the same template string share a class and the
    second one's #include is resolved against the first one's file.
    ct4.build keeps a compile-cache Store per directory, and a case
    that reuses a directory name reuses the store.

    Without this the order of the cases decides their result: a serial
    run failed on a different case than a shuffled one, which is the
    shape of a leak rather than of a defect in either case.
    """
    from Cheetah.Template import Template

    Template._CHEETAH_compileCache.clear()
    build._STORES.clear()
    yield
    Template._CHEETAH_compileCache.clear()
    build._STORES.clear()


def skin(root, template=TEMPLATE, targets=None, **extra):
    """Writes a small skin and returns the path of its manifest.

    The base is a directory of its own, so that a template resolving
    its #include against the base and a manifest resolving its context
    against its own directory can be told apart.
    """
    root.mkdir(parents=True, exist_ok=True)
    base = root / "skin"
    (base / "inc").mkdir(parents=True, exist_ok=True)
    (base / "index.html.tmpl").write_text(template, encoding="utf-8")
    (base / "inc" / "head.inc").write_text(HEAD, encoding="utf-8")
    (root / "context").mkdir(exist_ok=True)
    (root / "context" / "common.json").write_text(
        json.dumps(CONTEXT), encoding="utf-8")
    document = {
        "version": 1,
        "base": "skin",
        "output": "public",
        "context": {"json": "context/common.json"},
        "targets": targets or [{"template": "index.html.tmpl",
                                "output": "index.html"}],
    }
    document.update(extra)
    path = root / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def run(manifest, **options):
    """One build, through the API, returning the report."""
    return build.build(build.load_manifest(manifest), **options)


def only(report):
    """The single result of a single-target manifest."""
    assert len(report["results"]) == 1, report["results"]
    return report["results"][0]


def codes(report):
    return {finding["code"] for finding in report["findings"]}


# -- The first run and the second ------------------------------------

def test_first_run_writes_the_output(tmp_path):
    manifest = skin(tmp_path)
    report = run(manifest)
    assert build.exit_code(report) == 0
    assert report["targets"]["written"] == 1
    assert report["targets"]["rendered"] == 1
    result = only(report)
    assert result["status"] == "written"
    assert result["reason"] == "first run"
    text = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")
    assert "<head>" in text and "Zuhause" in text
    assert json.loads(json.dumps(report))["version"] == 1


def test_second_run_leaves_the_file_completely_alone(tmp_path):
    # The assertion the whole feature exists for: same inode, same
    # mtime, therefore no rsync transfer and no FTP upload.
    manifest = skin(tmp_path)
    run(manifest)
    output = tmp_path / "public" / "index.html"
    before = output.stat()
    report = run(manifest)
    after = output.stat()
    assert only(report)["status"] == "skipped"
    assert report["targets"]["skipped"] == 1
    assert build.exit_code(report) == 0
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ino == before.st_ino


def test_a_touched_template_is_not_a_changed_template(tmp_path):
    # Staleness is content, never mtime. A backup that restores the
    # timestamps must not cost a full regeneration.
    manifest = skin(tmp_path)
    run(manifest)
    template = tmp_path / "skin" / "index.html.tmpl"
    later = time.time() + 120
    os.utime(template, (later, later))
    assert only(run(manifest))["status"] == "skipped"


def test_a_changed_template_with_an_unchanged_result_is_not_written(
        tmp_path):
    manifest = skin(tmp_path)
    run(manifest)
    output = tmp_path / "public" / "index.html"
    before = output.stat()
    (tmp_path / "skin" / "index.html.tmpl").write_text(
        TEMPLATE + "## a comment changes nothing that is rendered\n",
        encoding="utf-8")
    report = run(manifest)
    result = only(report)
    assert result["reason"] == "template changed"
    assert result["status"] == "unchanged"
    assert report["targets"]["written"] == 0
    assert report["targets"]["rendered"] == 1
    assert output.stat().st_mtime_ns == before.st_mtime_ns


# -- What the graph contributes --------------------------------------

def test_a_changed_include_renders_the_parent(tmp_path):
    manifest = skin(tmp_path)
    run(manifest)
    (tmp_path / "skin" / "inc" / "head.inc").write_text(
        "<head lang=de>\n", encoding="utf-8")
    result = only(run(manifest))
    assert result["status"] == "written"
    assert result["reason"].startswith("include ")
    assert "head.inc" in result["reason"]


def test_a_rewrite_inside_one_clock_tick_is_still_a_change(tmp_path):
    # ct3 keys its class cache for a file on the path and the mtime,
    # and the kernel stamps a file at tick resolution, so a rewrite
    # that lands in the tick of the write before it carries the same
    # mtime and ct3 hands back the class it compiled from the old
    # content. Staleness here is content, and the rewritten include
    # has to reach the page whatever the clock says. Measured in the
    # image: a run takes 2.5 ms, and 56 of 200 rewrites fell into the
    # tick of the write before them.
    manifest = skin(tmp_path)
    head = tmp_path / "skin" / "inc" / "head.inc"
    stamp = head.stat()
    run(manifest)
    head.write_text("<head lang=de>\n", encoding="utf-8")
    os.utime(head, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert only(run(manifest))["status"] == "written"
    text = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")
    assert "<head lang=de>" in text


def test_a_render_error_names_the_template_line(tmp_path):
    # A NotFound at render time carries no line of its own. The class
    # ct3 hands back hangs the template's line on it, and the finding
    # reads it from there.
    source = "line one\n#for $i in [1]\n  $missing\n#end for\n"
    manifest = skin(tmp_path, template=source)
    report = run(manifest)
    finding = report["findings"][0]
    assert finding["code"] == "CT4301"
    assert (finding["line"], finding["column"]) == (3, 3)


def test_an_include_that_appears_later_renders_the_parent(tmp_path):
    # belchertown's optional hooks: 69 of 348 constant include names in
    # the corpus have no file. The absent name is recorded, and the
    # file appearing has to invalidate the parent.
    guarded = ("#set $enabled = False\n"
               "#if $enabled\n"
               '#include "hook.inc"\n'
               "#end if\n") + TEMPLATE
    manifest = skin(tmp_path, template=guarded)
    assert only(run(manifest))["status"] == "written"
    assert only(run(manifest))["status"] == "skipped"
    (tmp_path / "skin" / "hook.inc").write_text("<hook>\n", encoding="utf-8")
    result = only(run(manifest))
    assert result["status"] != "skipped"
    assert "hook.inc" in result["reason"]


def test_a_computed_include_is_rendered_every_run(tmp_path):
    # Nobody can resolve an include the template computes, so the
    # target is never skipped. CT4310 is the graph's word for it.
    manifest = skin(tmp_path, template="#include $part.strip()\n"
                                       "Station: $station\n")
    (tmp_path / "context" / "common.json").write_text(
        json.dumps({"station": "Zuhause", "part": " inc/head.inc "}),
        encoding="utf-8")
    assert only(run(manifest))["status"] == "written"
    report = run(manifest)
    assert only(report)["status"] == "unchanged"
    assert only(report)["reason"] == "include computed at run time"
    assert "CT4310" in codes(report), codes(report)


# -- The output on disk ----------------------------------------------

def test_an_output_changed_behind_our_back_is_written_again(tmp_path):
    # A remembered hash is a belief. weewx' ImageGenerator, its
    # CopyGenerator and an administrator all write into this directory.
    manifest = skin(tmp_path)
    run(manifest)
    output = tmp_path / "public" / "index.html"
    output.write_text("somebody else was here\n", encoding="utf-8")
    result = only(run(manifest))
    assert result["reason"] == "output changed on disk"
    assert result["status"] == "written"
    assert "Zuhause" in output.read_text(encoding="utf-8")


# -- Failure -----------------------------------------------------------

def test_a_failing_target_does_not_take_the_others_with_it(tmp_path):
    manifest = skin(tmp_path, targets=[
        {"template": "broken.tmpl", "output": "broken.html"},
        {"template": "index.html.tmpl", "output": "index.html"}])
    (tmp_path / "skin" / "broken.tmpl").write_text(
        "$no_such_name_anywhere\n", encoding="utf-8")
    report = run(manifest)
    assert build.exit_code(report) == 1
    assert report["targets"]["failed"] == 1
    assert report["targets"]["written"] == 1
    failed = [f for f in report["findings"] if f["code"] == "CT4301"]
    assert len(failed) == 1
    assert failed[0]["file"] == "broken.tmpl"
    assert (tmp_path / "public" / "index.html").is_file()
    assert not (tmp_path / "public" / "broken.html").exists()


def test_a_failed_target_is_rendered_again_next_run(tmp_path):
    manifest = skin(tmp_path, targets=[
        {"template": "broken.tmpl", "output": "broken.html"}])
    (tmp_path / "skin" / "broken.tmpl").write_text(
        "$no_such_name_anywhere\n", encoding="utf-8")
    run(manifest)
    assert only(run(manifest))["status"] == "failed"


# -- Manifests that cannot be used -------------------------------------

def test_a_missing_manifest_is_exit_two(tmp_path):
    assert cli.main(["build", str(tmp_path / "nothing.json")]) == 2


def test_a_manifest_that_is_not_json_is_exit_two(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("not json at all", encoding="utf-8")
    assert cli.main(["build", str(path)]) == 2


def test_a_manifest_from_the_future_is_refused(tmp_path):
    manifest = skin(tmp_path, version=2)
    assert cli.main(["build", str(manifest)]) == 2
    assert not (tmp_path / "public").exists()


def test_an_unknown_key_is_refused(tmp_path):
    manifest = skin(tmp_path, touch_unchange=True)
    assert cli.main(["build", str(manifest)]) == 2
    assert not (tmp_path / "public").exists()


def test_an_unknown_key_in_a_target_is_refused(tmp_path):
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "index.html",
         "touch_unchange": True}])
    assert cli.main(["build", str(manifest)]) == 2


def test_two_targets_with_the_same_output_are_refused(tmp_path):
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "index.html"},
        {"template": "index.html.tmpl", "output": "index.html"}])
    assert cli.main(["build", str(manifest)]) == 2
    assert not (tmp_path / "public").exists()


def test_a_toml_manifest_is_refused_with_a_reason(tmp_path, capsys):
    manifest = skin(tmp_path)
    toml = manifest.with_name("manifest.toml")
    toml.write_text("version = 1\n", encoding="utf-8")
    assert cli.main(["build", str(toml)]) == 2
    printed = capsys.readouterr().out
    assert "CT4300" in printed and "3.11" in printed


# -- The lock ----------------------------------------------------------

def lock_of(manifest):
    return manifest.parent / ".ct4-build.json.lock"


def test_a_held_lock_stops_the_run(tmp_path):
    manifest = skin(tmp_path)
    lock_of(manifest).write_text(
        json.dumps({"pid": 1, "started": time.time(), "host": "x"}),
        encoding="utf-8")
    assert cli.main(["build", str(manifest)]) == 3
    assert not (tmp_path / "public").exists()


def test_a_lock_older_than_the_timeout_is_broken(tmp_path):
    manifest = skin(tmp_path)
    lock_of(manifest).write_text(
        json.dumps({"pid": 1, "started": time.time() - 7200, "host": "x"}),
        encoding="utf-8")
    report = run(manifest, lock_timeout=60.0)
    assert build.exit_code(report) == 0
    warned = [f for f in report["findings"] if f["code"] == "CT4321"]
    assert len(warned) == 1 and warned[0]["severity"] == "warning"
    assert (tmp_path / "public" / "index.html").is_file()
    assert not lock_of(manifest).exists()


def test_no_lock_ignores_a_held_lock(tmp_path):
    manifest = skin(tmp_path)
    lock_of(manifest).write_text("{}", encoding="utf-8")
    assert cli.main(["build", str(manifest), "--no-lock"]) == 0


# -- The switches ------------------------------------------------------

def test_dry_run_writes_nothing_at_all(tmp_path):
    manifest = skin(tmp_path)
    report = run(manifest, dry_run=True)
    assert build.exit_code(report) == 0
    assert report["dry_run"] is True
    assert only(report)["status"] == "written"
    assert not (tmp_path / "public").exists()
    assert not (tmp_path / ".ct4-build.json").exists()


def test_force_renders_but_still_writes_only_a_difference(tmp_path):
    manifest = skin(tmp_path)
    run(manifest)
    output = tmp_path / "public" / "index.html"
    before = output.stat()
    report = run(manifest, force=True)
    assert only(report)["reason"] == "--force"
    assert only(report)["status"] == "unchanged"
    assert output.stat().st_mtime_ns == before.st_mtime_ns


def test_always_is_never_skipped(tmp_path):
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "index.html",
         "always": True}])
    run(manifest)
    result = only(run(manifest))
    assert result["reason"] == "always"
    assert result["status"] == "unchanged"


def test_only_restricts_the_run(tmp_path):
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "index.html"},
        {"template": "index.html.tmpl", "output": "second.html"}])
    report = run(manifest, only=["second.html"])
    assert only(report)["output"] == "second.html"
    assert not (tmp_path / "public" / "index.html").exists()


def test_report_goes_to_the_file_and_the_summary_to_stdout(
        tmp_path, capsys):
    manifest = skin(tmp_path)
    where = tmp_path / "report.json"
    assert cli.main(["build", str(manifest), "--report", str(where)]) == 0
    document = json.loads(where.read_text(encoding="utf-8"))
    assert document["targets"]["written"] == 1
    printed = capsys.readouterr().out
    assert "1 targets" in printed
    assert not printed.startswith("{")


def test_format_json_prints_the_document(tmp_path, capsys):
    manifest = skin(tmp_path)
    assert cli.main(["build", str(manifest), "--format", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["manifest"] == str(manifest.resolve())


# -- Parallel ----------------------------------------------------------

def four_targets():
    return [{"template": "index.html.tmpl", "output": "%d.html" % n}
            for n in range(4)]


def test_two_jobs_deliver_what_one_job_delivers(tmp_path):
    one = run(skin(tmp_path / "one", targets=four_targets()), jobs=1)
    two = run(skin(tmp_path / "two", targets=four_targets()), jobs=2)
    assert one["targets"] == two["targets"]
    assert [r["output"] for r in one["results"]] == \
           [r["output"] for r in two["results"]]
    assert [r["digest"] for r in one["results"]] == \
           [r["digest"] for r in two["results"]]
    for name in ("0.html", "1.html", "2.html", "3.html"):
        assert (tmp_path / "one" / "public" / name).read_bytes() == \
               (tmp_path / "two" / "public" / name).read_bytes()
    assert run(skin(tmp_path / "two", targets=four_targets()),
               jobs=2)["targets"]["skipped"] == 4


# -- The context -------------------------------------------------------

def test_a_callable_context_is_never_skipped(tmp_path, monkeypatch):
    (tmp_path / "ct4_test_context.py").write_text(
        "def make(output):\n"
        "    return [{'station': 'Zuhause'}]\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "index.html",
         "context": {"call": "ct4_test_context:make"}}])
    assert only(run(manifest))["status"] == "written"
    output = tmp_path / "public" / "index.html"
    before = output.stat()
    result = only(run(manifest))
    assert result["reason"] == "context cannot be hashed"
    assert result["status"] == "unchanged"
    assert output.stat().st_mtime_ns == before.st_mtime_ns


def test_a_changed_context_file_renders(tmp_path):
    manifest = skin(tmp_path)
    run(manifest)
    (tmp_path / "context" / "common.json").write_text(
        json.dumps({"station": "Woanders"}), encoding="utf-8")
    result = only(run(manifest))
    assert result["reason"] == "context changed"
    assert result["status"] == "written"


def test_a_context_that_raises_is_a_target_failure(tmp_path, monkeypatch):
    (tmp_path / "ct4_test_angry.py").write_text(
        "def make(output):\n"
        "    raise RuntimeError('no context today')\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "index.html",
         "context": {"call": "ct4_test_angry:make"}}])
    report = run(manifest)
    assert build.exit_code(report) == 1
    assert "CT4301" in codes(report)


# -- The compile cache -------------------------------------------------

def test_the_compile_cache_engages(tmp_path):
    # The trap researcher 3 named: a key is built only from a source
    # string, so Template(file=path) means a 100 % miss rate that looks
    # exactly like a working cache. Two runs in one process, and the
    # second one has to hit.
    manifest = skin(tmp_path)
    first = run(manifest, force=True)
    assert first["cache"]["misses"] > 0
    second = run(manifest, force=True)
    assert second["cache"]["hits"] > 0
    assert second["cache"]["directory"] == str(tmp_path / ".ct4-cache")


# -- Where the run stands ----------------------------------------------

def test_the_working_directory_does_not_decide_anything(
        tmp_path, monkeypatch):
    manifest = skin(tmp_path / "skinroot")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert cli.main(["build", str(manifest)]) == 0
    assert (tmp_path / "skinroot" / "public" / "index.html").is_file()
    assert list(elsewhere.iterdir()) == []
    assert os.getcwd() == str(elsewhere)


def test_a_base_that_is_not_there_is_exit_two(tmp_path):
    # The document parses and the tree it names does not exist. Caught
    # before the chdir, or the caller gets a traceback where the whole
    # point of the command is that it owes a scheduler a report.
    manifest = skin(tmp_path, base="nowhere")
    assert cli.main(["build", str(manifest)]) == 2
    assert not (tmp_path / "public").exists()


# -- The command, end to end -----------------------------------------

def two_pages(root):
    """A manifest with two pages, each with a template of its own."""
    manifest = skin(root, targets=[
        {"template": "index.html.tmpl", "output": "index.html"},
        {"template": "about.html.tmpl", "output": "sub/about.html"}])
    (root / "skin" / "about.html.tmpl").write_text(
        "About $station\n", encoding="utf-8")
    return manifest


def test_the_command_builds_two_templates(tmp_path, capsys):
    manifest = two_pages(tmp_path)
    assert cli.main(["build", str(manifest)]) == 0
    assert "<head>" in (tmp_path / "public" / "index.html").read_text(
        encoding="utf-8")
    # Under the manifest's own output directory, subdirectory and all.
    assert (tmp_path / "public" / "sub" / "about.html").read_text(
        encoding="utf-8") == "About Zuhause\n"
    assert "2 targets, 2 written" in capsys.readouterr().out


def test_a_second_command_run_rewrites_nothing(tmp_path, capsys):
    manifest = two_pages(tmp_path)
    assert cli.main(["build", str(manifest)]) == 0
    pages = [tmp_path / "public" / "index.html",
             tmp_path / "public" / "sub" / "about.html"]
    before = [page.stat() for page in pages]
    capsys.readouterr()
    # Still 0: nothing changed is the steady state of a five-minute
    # timer, and a non-zero here would train operators to ignore it.
    assert cli.main(["build", str(manifest)]) == 0
    for page, was in zip(pages, before):
        assert page.stat().st_mtime_ns == was.st_mtime_ns
        assert page.stat().st_ino == was.st_ino
    printed = capsys.readouterr().out
    assert "0 written" in printed and "2 skipped" in printed


def test_a_failing_template_makes_the_command_exit_one(tmp_path, capsys):
    manifest = two_pages(tmp_path)
    (tmp_path / "skin" / "about.html.tmpl").write_text(
        "#if\nAbout\n#end if\n", encoding="utf-8")
    assert cli.main(["build", str(manifest)]) == 1
    printed = capsys.readouterr().out
    assert "CT4301" in printed
    assert "about.html.tmpl" in printed
    # The other target was still attempted and still written.
    assert (tmp_path / "public" / "index.html").is_file()
    assert not (tmp_path / "public" / "sub" / "about.html").exists()


def test_the_json_report_has_the_documented_shape(tmp_path, capsys):
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "b.html"},
        {"template": "index.html.tmpl", "output": "a.html"}])
    assert cli.main(["build", str(manifest), "--format", "json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert set(document) == {
        "version", "manifest", "started", "duration", "jobs", "dry_run",
        "targets", "cache", "results", "findings"}
    assert set(document["targets"]) == {
        "total", "skipped", "rendered", "written", "unchanged", "failed"}
    assert set(document["cache"]) == {"hits", "misses", "directory"}
    assert (document["version"], document["jobs"]) == (1, 1)
    assert document["dry_run"] is False
    # Sorted by output, so -j1 and -j4 hand out the same document.
    assert [r["output"] for r in document["results"]] == ["a.html", "b.html"]
    for result in document["results"]:
        assert set(result) == {"output", "template", "status", "reason",
                               "bytes", "digest", "seconds"}


def test_a_report_finding_is_shaped_like_every_other_finding(tmp_path):
    # One schema across "ct4 check" and "ct4 build", or the CI that
    # reads one of them has to learn the other.
    manifest = skin(tmp_path, template="#include $part.strip()\n")
    (tmp_path / "context" / "common.json").write_text(
        json.dumps({"part": " inc/head.inc "}), encoding="utf-8")
    run(manifest)
    findings = run(manifest)["findings"]
    assert findings
    shape = set(diagnostics.Diagnostic("CT0000", "note", "").as_dict())
    for finding in findings:
        assert set(finding) == shape


# -- Picking targets out ---------------------------------------------

def test_only_takes_a_glob(tmp_path):
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "data/a.html"},
        {"template": "index.html.tmpl", "output": "data/b.html"},
        {"template": "index.html.tmpl", "output": "index.html"}])
    assert cli.main(["build", str(manifest), "--only", "data/*.html"]) == 0
    assert (tmp_path / "public" / "data" / "a.html").is_file()
    assert (tmp_path / "public" / "data" / "b.html").is_file()
    assert not (tmp_path / "public" / "index.html").exists()


def test_a_subset_run_leaves_the_other_targets_alone(tmp_path):
    # State is per target, so a subset run has to update only what it
    # ran and the next full run still builds the rest.
    manifest = skin(tmp_path, targets=[
        {"template": "index.html.tmpl", "output": "index.html"},
        {"template": "index.html.tmpl", "output": "second.html"}])
    assert cli.main(["build", str(manifest), "--only", "second.html"]) == 0
    report = run(manifest)
    grades = {r["output"]: r["status"] for r in report["results"]}
    assert grades == {"index.html": "written", "second.html": "skipped"}


def test_an_only_that_matches_nothing_is_exit_two(tmp_path):
    assert cli.main(["build", str(skin(tmp_path)),
                     "--only", "nothing.*"]) == 2
    assert not (tmp_path / "public").exists()


# -- Modules a template imports --------------------------------------

def helper(tmp_path, monkeypatch, text):
    """A module the template can import, on sys.path.

    Evicted from sys.modules first, and that line is the whole test.
    importlib.util.find_spec answers out of sys.modules for a module
    that is already imported, so a helper another test imported from
    its own tmp_path would be the one ct4.depend fingerprints here: an
    edited file the build reports as up to date, and a green test that
    proves nothing. Which of the two tests runs first depends on how
    xdist spreads them, so it broke on adding an unrelated test file.
    """
    sys.modules.pop("ct4_test_helper", None)
    (tmp_path / "ct4_test_helper.py").write_text(text, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))


IMPORTING = "#from ct4_test_helper import GREETING\n$GREETING $station\n"


def test_the_state_records_the_module_a_template_imports(
        tmp_path, monkeypatch):
    helper(tmp_path, monkeypatch, "GREETING = 'Moin'\n")
    manifest = skin(tmp_path, template=IMPORTING)
    run(manifest)
    state = json.loads(
        (tmp_path / ".ct4-build.json").read_text(encoding="utf-8"))
    sources = state["targets"]["index.html"]["sources"]
    assert sources["module:ct4_test_helper"] not in ("-", "*")


def test_an_edited_module_renders_the_page_again(tmp_path, monkeypatch):
    helper(tmp_path, monkeypatch, "GREETING = 'Moin'\n")
    manifest = skin(tmp_path, template=IMPORTING)
    assert only(run(manifest))["status"] == "written"
    assert only(run(manifest))["status"] == "skipped"
    # A different length on purpose. Python trusts a cached .pyc whose
    # source has the same size and the same mtime second, so 'Tach'
    # for 'Moin' inside one second rendered the old greeting and the
    # status flipped with the clock. helper() drops the module from
    # sys.modules, so with the cache seen through the render reads
    # the new file.
    helper(tmp_path, monkeypatch, "GREETING = 'Servus'\n")
    result = only(run(manifest))
    assert result["reason"] == "module ct4_test_helper changed"
    assert result["status"] == "written"
    text = (tmp_path / "public" / "index.html").read_text(encoding="utf-8")
    assert "Servus" in text


def test_a_built_in_module_is_not_a_reason_to_render_every_run(tmp_path):
    # "#import time" stands in real skins. It has no file to hash, and
    # reading that as "unknown" would cost a render every cycle for
    # ever, which is the opposite of what this module is for.
    manifest = skin(tmp_path, template="#import time\n" + TEMPLATE)
    assert only(run(manifest))["status"] == "written"
    assert only(run(manifest))["status"] == "skipped"


def test_a_module_nobody_can_find_is_a_failure_and_a_finding(tmp_path):
    manifest = skin(
        tmp_path, template="#import ct4_no_such_module_anywhere\n" + TEMPLATE)
    report = run(manifest)
    assert build.exit_code(report) == 1
    assert only(report)["status"] == "failed"
    # A note, not an error: the failure above already says the run is
    # broken. The note says why the target could never be skipped even
    # if the import were lazy enough to render.
    assert "CT4315" in codes(report)


# -- What the cache numbers count ------------------------------------

def test_the_cache_count_is_not_doubled(tmp_path):
    # One target, one compilation, one miss, then one hit. Counting this
    # process's own store on top of what the render measured would say
    # two, and the number exists to make a silent 100 % miss rate
    # visible rather than to be roughly right.
    manifest = skin(tmp_path, template="Station: $station\n")
    first = run(manifest, force=True)
    assert (first["cache"]["hits"], first["cache"]["misses"]) == (0, 1)
    second = run(manifest, force=True)
    assert (second["cache"]["hits"], second["cache"]["misses"]) == (1, 0)
