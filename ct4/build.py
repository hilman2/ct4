"""Render a manifest of templates, once, for cron and for systemd.

The use case is a process that runs every few minutes and uploads what
it produced. Three things follow from that and they are the whole
module:

Nothing is written unless the bytes differ. That is ``ct4.write``'s job
here; this module only makes sure the comparison happens on the encoded
bytes and that the decision to skip a render can never be the reason a
changed file stays on disk unwritten.

Staleness is decided on content, never on mtime. A template that was
touched but not edited must not cost a render, and a clock that runs
backwards must not cost a stale file. Everything the decision reads is
a sha256: the template, every dependency ``ct4.depend`` resolved, the
context where it can be hashed, and the output as it lies on disk right
now. That last one is not optional. Other programs write into the same
output directory, so a remembered digest is a belief, not a fact.

Where the graph is unsure the answer is "render it". A wasted render
costs CPU; a wrong skip costs a stale file on a web server. So an
opaque include, an unhashable context and an unreadable state file all
lead to the same place, and none of them can produce a wrong file.

The exit codes are the contract with the scheduler: 0 for a finished
run whether or not anything changed, 1 for a target that failed, 2 for
a manifest or arguments that cannot be used, 3 for a run that found the
lock held. A deployment where 3 is ordinary sets ``SuccessExitStatus=3``
in its unit.
"""

from __future__ import annotations

import codecs
import collections.abc
import concurrent.futures
import fnmatch
import hashlib
import importlib
import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ct4 import cache, depend, diagnostics, write

# Bump on a change of the manifest format. A manifest from the future
# is refused rather than read optimistically.
MANIFEST_VERSION = 1

# Bump on a change of the state format or of how its key is built. Old
# state is then discarded whole instead of read wrongly, and the run
# regenerates everything, which is always safe.
STATE_FORMAT = 1

REPORT_VERSION = 1

OK = 0
FAILED = 1
UNUSABLE = 2
LOCKED = 3

MANIFEST_KEYS = frozenset({
    "version", "base", "output", "encoding", "errors", "settings",
    "cache", "state", "context", "targets"})
TARGET_KEYS = frozenset({
    "template", "output", "mode", "context", "always", "touch_unchanged"})
MODES = ("text", "json")

# What stands in the state where a dependency did not exist. A file
# appearing under a name that was absent has to invalidate the parent:
# most of those names are optional hooks, guarded by an os.path.exists
# on the line above, and the whole point of the hook is that dropping
# it in changes the page.
ABSENT = "-"

# And what stands for a file that is there and will not open. Not the
# same as absent: absence is an answer and permission trouble is not.
# A target with one of these renders on every run until somebody fixes
# it, which is the safe direction, and it never takes the rest of the
# run down with it.
UNREADABLE = "?"

# What a module dependency is called in the state, so that a module
# named "inc/head.inc" cannot exist and collide with the file of that
# name. A colon is legal in neither a dotted module name nor a POSIX
# path component that anybody writes.
MODULE_MARK = "module:"

# What stands where a module was found but has nothing on disk to hash:
# a built-in, something frozen into the interpreter, a namespace
# package. It changes when the interpreter is replaced and not
# otherwise, so it is a constant rather than a reason to regenerate.
BUILTIN = "*"

_STORES: dict[str, cache.Store] = {}


class ManifestError(Exception):
    """The manifest cannot be used. Nothing is built."""


@dataclass(frozen=True)
class Target:
    """One output, and everything the manifest says about it.

    ``context`` is a pair of kind and value: ``("none", "")``,
    ``("json", path)`` or ``("call", "pkg.mod:fn")``. A pair rather
    than the dict from the file because this travels to a worker
    process and is compared for equality.
    """

    output: str
    template: str
    mode: str = "text"
    context: tuple[str, str] = ("none", "")
    always: bool = False
    touch_unchanged: bool = False


@dataclass(frozen=True)
class Manifest:
    """A manifest, with every path already made absolute.

    The paths are resolved against the manifest file's own directory
    and never against the working directory, so that the same manifest
    means the same thing from a shell, from cron and from a unit file.
    """

    path: Path
    base: Path
    output: Path
    encoding: str
    errors: str
    settings: dict[str, Any]
    cache: Path
    state: Path
    targets: tuple[Target, ...]


# -- Reading the manifest ---------------------------------------------

def load_manifest(path: Path) -> Manifest:
    """Reads a manifest and checks it completely.

    Every key is checked here rather than where it is used, because a
    manifest that is half accepted has already written half a run.

    Args:
        path (Path): The manifest file.

    Returns:
        Manifest: With all paths absolute.

    Raises:
        ManifestError: On anything that makes the manifest unusable,
            including a key nobody knows.
    """
    if path.suffix.lower() == ".toml":
        raise ManifestError(
            "%s: the manifest is JSON, not TOML. tomllib arrived in"
            " Python 3.11, this project supports 3.10, and it has no"
            " mandatory dependencies to add a reader with. A format"
            " that works only on newer interpreters is worse than one"
            " format that works everywhere." % path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError("%s: %s" % (path, exc)) from None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ManifestError("%s: not JSON: %s" % (path, exc)) from None
    if not isinstance(data, dict):
        raise ManifestError("%s: the manifest must be a JSON object" % path)
    _refuse_unknown(str(path), data, MANIFEST_KEYS)
    if data.get("version") != MANIFEST_VERSION:
        raise ManifestError(
            '%s: "version" must be %d, not %r'
            % (path, MANIFEST_VERSION, data.get("version")))

    where = path.resolve()
    home = where.parent
    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise ManifestError('%s: "settings" must be an object' % path)
    default = _context_spec(str(path), data.get("context"))
    targets = _targets(str(path), data.get("targets"), default)
    return Manifest(
        path=where,
        # Resolved, because depend.Graph resolves its own base and
        # names its nodes relative to that. Two spellings of the same
        # directory would put the graph's names and this module's
        # names in different shapes.
        base=(home / _text(str(path), data, "base", ".")).resolve(),
        output=home / _text(str(path), data, "output", "."),
        encoding=_codec(str(path), data),
        errors=_handler(str(path), data),
        settings=dict(settings),
        cache=home / _text(str(path), data, "cache", ".ct4-cache"),
        state=home / _text(str(path), data, "state", ".ct4-build.json"),
        targets=targets)


def _targets(where: str, value: Any,
             default: tuple[str, str]) -> tuple[Target, ...]:
    if not isinstance(value, list) or not value:
        raise ManifestError(
            '%s: "targets" must be a list with at least one entry' % where)
    seen: set[str] = set()
    built = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ManifestError("%s: every target must be an object" % where)
        _refuse_unknown(where, entry, TARGET_KEYS)
        template = entry.get("template")
        output = entry.get("output")
        if not isinstance(template, str) or not template:
            raise ManifestError(
                '%s: every target needs a "template"' % where)
        if not isinstance(output, str) or not output:
            raise ManifestError('%s: every target needs an "output"' % where)
        if Path(output).is_absolute() or ".." in Path(output).parts:
            # The manifest documents "output" as the directory the
            # results are written under, and a name that leaves it
            # makes that untrue without saying so. A build run from
            # cron writes where the manifest says and nowhere else.
            raise ManifestError(
                '%s: an "output" may not leave the output directory: %s'
                % (where, output))
        if output in seen:
            raise ManifestError(
                '%s: two targets write "%s"; an output names a target in'
                " the state file and in the report, so it has to be"
                " unique" % (where, output))
        seen.add(output)
        mode = entry.get("mode", "text")
        if mode not in MODES:
            raise ManifestError('%s: "mode" must be one of %s, not %r'
                                % (where, ", ".join(MODES), mode))
        context = default
        if "context" in entry:
            context = _context_spec(where, entry["context"])
        built.append(Target(
            output=output,
            template=template,
            mode=mode,
            context=context,
            always=_flag(where, entry, "always"),
            touch_unchanged=_flag(where, entry, "touch_unchanged")))
    return tuple(built)


def _context_spec(where: str, value: Any) -> tuple[str, str]:
    """Reads a context declaration into a kind and a value."""
    if value is None:
        return ("none", "")
    if isinstance(value, dict) and len(value) == 1:
        kind, target = next(iter(value.items()))
        if kind in ("json", "call") and isinstance(target, str) and target:
            return (str(kind), target)
    raise ManifestError(
        '%s: "context" is {"json": "path.json"} or'
        ' {"call": "pkg.mod:function"}, or null' % where)


def _refuse_unknown(where: str, data: dict[str, Any],
                    known: frozenset[str]) -> None:
    """A key nobody knows is an error, never a warning.

    A misspelled ``touch_unchanged`` that is quietly ignored is a
    stale file six months later, and nobody will connect the two.
    """
    for key in sorted(data):
        if key not in known:
            raise ManifestError('%s: unknown key "%s"' % (where, key))


def _text(where: str, data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ManifestError('%s: "%s" must be a string' % (where, key))
    return value


def _codec(where: str, data: dict[str, Any]) -> str:
    """The manifest's encoding, checked against Python's own list.

    Checked here and not at the first write, because this module's own
    docstring says a manifest that is half accepted has already written
    half a run. An unknown codec left to the encode step comes out as a
    per-target failure with exit 1, where an unusable manifest is
    documented as exit 2 and stops before anything is built.
    """
    name = _text(where, data, "encoding", "utf-8")
    try:
        codecs.lookup(name)
    except LookupError:
        raise ManifestError('%s: "encoding" is not a codec Python knows: %s'
                            % (where, name)) from None
    return name


def _handler(where: str, data: dict[str, Any]) -> str:
    """The manifest's error handler, checked the same way.

    Worse than a bad codec if it is left: a handler is only consulted
    where a character does not fit, so a typo lies dormant until the
    first umlaut, which on a weather page is the first German station
    name.
    """
    name = _text(where, data, "errors", "strict")
    try:
        codecs.lookup_error(name)
    except LookupError:
        raise ManifestError('%s: "errors" is not an error handler Python '
                            'knows: %s' % (where, name)) from None
    return name


def _flag(where: str, data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ManifestError('%s: "%s" must be true or false' % (where, key))
    return value


# -- The run ----------------------------------------------------------

def build(manifest: Manifest, *, jobs: int = 1, force: bool = False,
          dry_run: bool = False, only: Sequence[str] = (),
          lock: bool = True, lock_timeout: float = 3600.0,
          ) -> dict[str, Any]:
    """Builds everything the manifest names and reports on it.

    Never raises for a template that fails: a failure is a result, the
    remaining targets are still attempted, and the report says which
    one it was. Only a manifest that cannot be read at all raises, and
    that happens in ``load_manifest`` before this is called.

    Args:
        manifest (Manifest): From ``load_manifest``.
        jobs (int): Worker processes. 1 renders inline, so that a
            traceback stays a traceback in cron's log.
        force (bool): Render every target, write only what differs.
        dry_run (bool): Render and compare, write nothing at all, not
            the outputs and not the state.
        only (Sequence[str]): Output names to restrict the run to.
        lock (bool): Whether to take the lock beside the state file.
        lock_timeout (float): Seconds after which a lock counts as
            left behind by a killed run and may be broken.

    Returns:
        dict[str, Any]: The report. ``exit_code`` reads it.
    """
    clock = time.perf_counter()
    started = datetime.now(timezone.utc).isoformat()
    findings: list[diagnostics.Diagnostic] = []
    results: list[dict[str, Any]] = []
    counts = {"hits": 0, "misses": 0}

    def done() -> dict[str, Any]:
        return _report(manifest, started, time.perf_counter() - clock,
                       jobs, dry_run, results, findings, counts)

    targets = list(manifest.targets)
    if jobs < 1:
        findings.append(_problem("CT4300", "--jobs must be at least 1"))
        return done()
    if only:
        empty = [pattern for pattern in only
                 if not _chosen(manifest.targets, [pattern])]
        if empty:
            findings.append(_problem(
                "CT4300", "--only matches no target of this manifest: %s"
                % ", ".join(sorted(empty)), str(manifest.path)))
            return done()
        targets = _chosen(targets, only)
    if not manifest.base.is_dir():
        # The document is fine, the tree it points at is not. Caught
        # here rather than at the chdir below, because a failed chdir
        # would be a traceback where the caller is owed a report.
        findings.append(_problem(
            "CT4300", '"base" is not a directory: %s' % manifest.base,
            str(manifest.path)))
        return done()

    lock_path = Path(str(manifest.state) + ".lock")
    held: str | None = None
    if lock:
        held = _acquire(lock_path, lock_timeout, findings)
        if held is None:
            return done()
    previous = os.getcwd()
    try:
        # The render resolves #include names against the process
        # working directory, and the graph resolved them against the
        # base. Both halves have to stand in the same place or the
        # graph describes a different program than the one that runs.
        os.chdir(manifest.base)
        results, counts = _execute(manifest, targets, findings,
                                   jobs=jobs, force=force, dry_run=dry_run)
    finally:
        os.chdir(previous)
        if held is not None:
            _release(lock_path, held)
    return done()


def _chosen(targets: Sequence[Target],
            patterns: Sequence[str]) -> list[Target]:
    """The targets ``--only`` selects.

    ``fnmatchcase`` and not ``fnmatch``: the latter folds case on
    Windows, and an output name is a path written into the manifest.
    Which targets a manifest builds must not depend on the operating
    system reading it. A pattern with no wildcard in it matches its own
    name, so naming targets outright still works.
    """
    return [target for target in targets
            if any(fnmatch.fnmatchcase(target.output, pattern)
                   for pattern in patterns)]


def _execute(manifest: Manifest, targets: list[Target],
             findings: list[diagnostics.Diagnostic], *, jobs: int,
             force: bool, dry_run: bool,
             ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Decides, renders, writes and records. Called under the chdir."""
    key = _state_key(manifest)
    state = _load_state(manifest.state, key)
    graph = depend.Graph(manifest.base, manifest.settings)

    results: list[dict[str, Any]] = []
    pending: list[tuple[Target, str, dict[str, Any]]] = []
    for target in targets:
        record = _examine(manifest, target, graph, state, force=force)
        if record["reason"] is None:
            results.append(_skipped(manifest, target, state))
            continue
        pending.append((target, str(record["reason"]), record))

    rendered = _render_all(manifest, pending, jobs=jobs, dry_run=dry_run)
    for (target, reason, record), result in zip(pending, rendered):
        result["reason"] = reason
        findings.extend(_findings_from(result))
        results.append(_public(result))
        if dry_run:
            continue
        if result["status"] == "failed":
            state.pop(target.output, None)
            continue
        state[target.output] = {
            "template": _posix(target.template),
            "sources": record["sources"],
            "context": record["context"],
            "output": result["digest"],
            "graph": record["sources"].get(_posix(target.template), ABSENT),
            "generated": int(time.time()),
        }
    findings.extend(graph.findings())
    if not dry_run:
        try:
            _save_state(manifest.state, key, state)
        except OSError as error:
            # The outputs are already on disk and correct. Losing the
            # state costs the next run its skipping and nothing else,
            # so this is a finding and not the end of the run: raising
            # here would throw away the report for work that succeeded.
            findings.append(_problem(
                "CT4323", "cannot write the state file %s: %s"
                % (manifest.state, error), str(manifest.state)))

    # Only from the results, never from this process's own store as
    # well. Every compilation happens inside _render, which measures it
    # there so that a worker process can report it at all; adding the
    # parent's delta on top would count an inline render twice and make
    # the one number that watches for a silent 100 % miss rate say double
    # what it saw.
    counts = {"hits": 0, "misses": 0}
    for result in rendered:
        counts["hits"] += int(result.get("cache", {}).get("hits", 0))
        counts["misses"] += int(result.get("cache", {}).get("misses", 0))
    results.sort(key=lambda item: str(item["output"]))
    return results, counts


def _render_all(manifest: Manifest,
                pending: list[tuple[Target, str, dict[str, Any]]], *,
                jobs: int, dry_run: bool) -> list[dict[str, Any]]:
    """Renders the pending targets, inline or in worker processes.

    A dry run stays inline whatever ``-j`` says: the worker entry point
    writes, by design, and nothing that writes belongs in a dry run.
    """
    if jobs <= 1 or dry_run or len(pending) < 2:
        return [_render(manifest, target, dry_run=dry_run)
                for target, _, _ in pending]
    made: list[dict[str, Any]] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(render_one, str(manifest.path), target.output)
                   for target, _, _ in pending]
        for (target, _, _), future in zip(pending, futures):
            try:
                made.append(future.result())
            except Exception as error:                      # noqa: BLE001
                # A worker that dies takes its future with it and, if
                # this were pool.map, the whole run. This promises a
                # report for every target, so the death of one is that
                # target's failure and nothing else's. Submitted rather
                # than mapped so the survivors can still be collected.
                made.append(_failed(target, error))
    return made


def _failed(target: Target, error: BaseException) -> dict[str, Any]:
    """The result of a target whose render never came back."""
    return {"output": target.output, "template": target.template,
            "status": "failed", "reason": "worker did not return",
            "error": "%s: %s" % (type(error).__name__, error),
            "bytes": 0, "seconds": 0.0,
            "cache": {"hits": 0, "misses": 0}}


def render_one(manifest_path: str, output: str) -> dict[str, Any]:
    """Renders one target of a manifest, from scratch.

    The entry point of a worker process, and therefore built from two
    strings: it re-reads the manifest, moves into the base and installs
    the compile cache itself, so nothing has to survive being pickled
    and ``spawn`` behaves like ``fork``. It does not restore the
    working directory; a worker has no other work to go back to.

    Args:
        manifest_path (str): Absolute path of the manifest.
        output (str): The target's output name.

    Returns:
        dict[str, Any]: The result of the render, JSON-able.
    """
    manifest = load_manifest(Path(manifest_path))
    for target in manifest.targets:
        if target.output == output:
            os.chdir(manifest.base)
            return _render(manifest, target, dry_run=False)
    raise ManifestError("%s: no target writes %s" % (manifest_path, output))


def _render(manifest: Manifest, target: Target, *,
            dry_run: bool) -> dict[str, Any]:
    """Renders one target and writes it if the bytes differ."""
    clock = time.perf_counter()
    store = _store(manifest.cache)
    before = (store.hits, store.misses)
    result: dict[str, Any] = {
        "output": target.output, "template": target.template,
        "status": "failed", "reason": "", "bytes": 0, "digest": "",
        "seconds": 0.0, "findings": [], "cache": {"hits": 0, "misses": 0}}
    path = manifest.output / target.output
    try:
        data = _bytes_of(manifest, target)
    except Exception as exc:                                # noqa: BLE001
        line, column = _where(exc)
        result["findings"].append(_problem(
            "CT4301", "%s: %s" % (type(exc).__name__, exc),
            target.template, line, column).as_dict())
    else:
        try:
            result.update(_publish(path, data, target, dry_run=dry_run))
        except OSError as exc:
            result["findings"].append(_problem(
                "CT4302", "%s: %s" % (path, exc), target.template).as_dict())
    result["seconds"] = round(time.perf_counter() - clock, 3)
    result["cache"] = {"hits": store.hits - before[0],
                       "misses": store.misses - before[1]}
    return result


def _publish(path: Path, data: bytes, target: Target, *,
             dry_run: bool) -> dict[str, Any]:
    """Writes the bytes, or says what writing them would have done."""
    if dry_run:
        digest = write.digest_of(data)
        same = write.digest_of_file(path) == digest
        return {"status": "unchanged" if same else "written",
                "bytes": len(data), "digest": digest}
    written = write.write(path, data,
                          touch_unchanged=target.touch_unchanged)
    return {"status": "unchanged" if written.status == write.UNCHANGED
            else "written",
            "bytes": written.size, "digest": written.digest}


def _bytes_of(manifest: Manifest, target: Target) -> bytes:
    """The finished output of a target, encoded.

    The encoding happens here and not at the call site because the
    comparison that decides whether anything is written has to see the
    same bytes the file would get: one string becomes four different
    byte sequences under four encodings.
    """
    source = (manifest.base / target.template).read_text(encoding="utf-8")
    search_list = _search_list(manifest, target)
    if target.mode == "json":
        from ct4 import jsonmode

        text = jsonmode.render(source, search_list, base_dir=manifest.base)
    else:
        text = _render_text(source, search_list, manifest.settings)
    return text.encode(manifest.encoding, manifest.errors)


def _render_text(source: str, search_list: Sequence[Any],
                 settings: dict[str, Any]) -> str:
    """Compiles from the source string and renders.

    From the string, not from the file, and that is the whole point:
    ``ct4.cache`` builds its key only when the compiler is handed a
    source, so ``Template(file=path)`` gets a key of None and a miss
    every single time, which looks exactly like a working cache.

    Cheetah's own in-process cache is switched off here. It would
    answer the second compilation of a source inside one process
    before the persistent cache is ever asked, which makes the hit
    count in the report say nothing about the thing it is there to
    watch.
    """
    from Cheetah.Template import Template

    klass = Template.compile(source=source, compilerSettings=dict(settings),
                             useCache=False, cacheCompilationResults=False)
    template = klass(searchList=list(search_list))
    try:
        return str(template)
    finally:
        template.shutdown()


def _search_list(manifest: Manifest, target: Target) -> list[Any]:
    """The search list of a target.

    A callable gets the output name, because that is the one thing
    that tells it which target it is speaking for.
    """
    kind, value = target.context
    if kind == "none":
        return []
    if kind == "json":
        path = manifest.path.parent / value
        return [json.loads(path.read_text(encoding="utf-8"))]
    module_name, _, attribute = value.partition(":")
    if not module_name or not attribute:
        raise ValueError('a context call reads "pkg.module:function",'
                         " not %r" % value)
    function = getattr(importlib.import_module(module_name), attribute)
    result = function(target.output)
    if isinstance(result, (str, bytes)) or \
            not isinstance(result, collections.abc.Sequence):
        raise TypeError("the context callable %s returned %s, but the"
                        " search list is a sequence"
                        % (value, type(result).__name__))
    return list(result)


# -- Deciding what has to be rendered ---------------------------------

def _examine(manifest: Manifest, target: Target, graph: depend.Graph,
             state: dict[str, Any], *, force: bool) -> dict[str, Any]:
    """Everything known about a target before it is rendered.

    Always asks the graph, even for a target that is going to be
    rendered anyway: the answer is what the next run compares against,
    so it has to be current, not remembered.

    Returns:
        dict[str, Any]: ``reason`` is None where the target can be
        skipped and otherwise names what forced the render; ``sources``,
        ``context`` is what the state records. There is no edge list
        beside them: which names are present is the same question as
        which digests are the absent mark, and a second copy of an
        answer is a second thing to keep true.
    """
    name = graph.add(manifest.base / target.template)
    stale = "include computed at run time" if graph.opaque(name) else None
    names = set(graph.dependencies(name))
    # The absent ones too, and not only this template's own: an
    # optional hook two includes down still invalidates this page when
    # somebody drops the file in.
    for reached in [name, *names]:
        names.update(graph.missing.get(reached, ()))
    sources = {_posix(target.template):
               _digest_of_file(manifest.base / target.template)}
    for dependency in sorted(names):
        short = _relative(manifest.base, dependency)
        if short == _posix(target.template):
            continue
        sources[short] = _digest_of_file(manifest.base / dependency)
    modules, unseen = _modules_of(graph, [name, *sorted(names)])
    sources.update(modules)
    if stale is None and unseen:
        stale = "module %s cannot be located" % unseen
    context = _context_digest(manifest, target)
    return {
        "reason": _reason(manifest, target, state.get(target.output),
                          sources, context, stale=stale, force=force),
        "sources": sources,
        "context": context,
    }


def _modules_of(graph: depend.Graph,
                nodes: Sequence[str]) -> tuple[dict[str, str], str]:
    """The module edges of these nodes, fingerprinted where possible.

    ``#extends`` and ``#import`` make a template depend on a Python
    module, and ``ct4.depend`` deliberately stops at the name: whether
    sys.path holds the module is a question about sys.path and not about
    the template. This is where it has to be answered, because a skin's
    own helper module gets edited as often as the template importing it,
    and nothing else in the state would notice.

    Args:
        graph (ct4.depend.Graph): The graph the node names come from.
        nodes (Sequence[str]): Node names whose edges to collect.

    Returns:
        tuple[dict[str, str], str]: The digests, keyed by module name
        under a prefix no file name can carry, and the first module that
        could not be located at all, or ``""`` where every one of them
        could. A module that cannot be located makes its template always
        stale, because there is nothing to compare it against.
    """
    digests: dict[str, str] = {}
    unseen = ""
    for node in nodes:
        scanned = graph.nodes.get(node)
        if scanned is None:
            continue
        for edge in scanned.edges:
            if edge.certainty != depend.MODULE or not edge.target:
                continue
            origin = graph.module_origin(edge.target)
            if origin is None:
                unseen = unseen or edge.target
            else:
                digests[MODULE_MARK + edge.target] = (
                    _digest_of_file(Path(origin)) if origin else BUILTIN)
    return digests, unseen


def _reason(manifest: Manifest, target: Target, entry: Any,
            sources: dict[str, str], context: str | None, *,
            stale: str | None, force: bool) -> str | None:
    """What forces this render, or None where nothing does.

    The order is the order of the answers: what the user asked for
    first, then the template, then what the template pulls in, then the
    context, and the file on disk last, because reading it is the only
    check here that costs an I/O the others may have made unnecessary.
    """
    if force:
        return "--force"
    if target.always:
        return "always"
    if not isinstance(entry, dict):
        return "first run"
    recorded = entry.get("sources")
    if not isinstance(recorded, dict):
        return "first run"
    template = _posix(target.template)
    if recorded.get(template) != sources.get(template):
        return "template changed"
    if stale is not None:
        return stale
    for name in sorted(set(recorded) | set(sources)):
        if sources.get(name) == UNREADABLE:
            # Not a comparison: two runs both fail to read the same
            # file and would agree, which would look like no change.
            return "cannot read %s" % name
        if name != template and recorded.get(name) != sources.get(name):
            if name.startswith(MODULE_MARK):
                return "module %s changed" % name[len(MODULE_MARK):]
            return "include %s changed" % name
    if target.context[0] == "call":
        return "context cannot be hashed"
    if entry.get("context") != context:
        return "context changed"
    if _digest_of_file(manifest.output / target.output) != \
            entry.get("output"):
        return "output changed on disk"
    return None


def _context_digest(manifest: Manifest, target: Target) -> str | None:
    """The hash of a context, or None where there is nothing to hash.

    A callable has no hash and its target is therefore never skipped.
    That is not expensive: it renders, the bytes match what is on disk,
    and nothing is written and nothing uploaded.
    """
    kind, value = target.context
    if kind != "json":
        return None
    return _digest_of_file(manifest.path.parent / value)


def _skipped(manifest: Manifest, target: Target,
             state: dict[str, Any]) -> dict[str, Any]:
    entry = state.get(target.output)
    digest = entry.get("output", "") if isinstance(entry, dict) else ""
    try:
        size = (manifest.output / target.output).stat().st_size
    except OSError:
        size = 0
    return {"output": target.output, "template": target.template,
            "status": "skipped", "reason": "up to date", "bytes": size,
            "digest": digest, "seconds": 0.0}


# -- State ------------------------------------------------------------

def _state_key(manifest: Manifest) -> str:
    """What the recorded digests are only valid under.

    The same reasoning as ``ct4.cache.key_for``: everything that
    changes the meaning of a recorded hash goes into the key, and a
    key that does not match throws the whole file away.
    """
    from Cheetah.Version import Version

    digest = hashlib.sha256()
    for part in (str(STATE_FORMAT), Version, str(manifest.base),
                 manifest.encoding, manifest.errors,
                 repr(sorted((str(k), repr(v))
                             for k, v in manifest.settings.items()))):
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def _load_state(path: Path, key: str) -> dict[str, Any]:
    """The recorded state, or nothing at all.

    Anything unreadable, from another format or from another key is
    discarded whole. The run then regenerates everything and still
    writes only what differs, so the worst case of throwing it away is
    CPU.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("format") != STATE_FORMAT or data.get("key") != key:
        return {}
    targets = data.get("targets")
    return dict(targets) if isinstance(targets, dict) else {}


def _save_state(path: Path, key: str, targets: dict[str, Any]) -> None:
    document = {"format": STATE_FORMAT, "key": key, "targets": targets}
    text = json.dumps(document, sort_keys=True, ensure_ascii=False,
                      indent=1) + "\n"
    write.atomic_write(path, text.encode("utf-8"))


# -- The lock ---------------------------------------------------------

def _acquire(path: Path, timeout: float,
             findings: list[diagnostics.Diagnostic]) -> str | None:
    """Takes the lock, breaks it where it was left behind.

    Overlapping runs are not hypothetical in this deployment: weewx
    declines to start a second report thread while the first is alive
    and then starts one anyway once its wait runs out.

    Breaking is a rename and not an unlink, and the difference is the
    whole of it. Unlinking and then creating again is two steps with a
    gap, and every run that reaches the gap creates a lock and believes
    it is alone. Measured: sixteen runs released together, five holders.
    A rename is one step, so exactly one run moves the stale file aside
    and the rest find it gone and race for the create, which is
    O_EXCL and settles it.

    Returns:
        str|None: The token written into the lock, to be handed to
        _release, or None where the lock was not taken. A token rather
        than a bare yes: the run that finishes must remove its own lock
        and not whatever file happens to be lying there, or it deletes
        the lock of the run that broke its own.
    """
    token = "%s:%d:%f" % (socket.gethostname(), os.getpid(), time.time())
    taken, why = _create_lock(path, token)
    if taken:
        return token
    if why is not None:
        # Not "somebody else has it" but "this cannot be locked at
        # all", which is a different thing to tell an operator. The
        # documented meaning of exit 3 is that another run holds it,
        # and a unit file may well treat that as success; reporting an
        # unwritable directory that way would let a site stop updating
        # in silence.
        findings.append(_problem(
            "CT4322", "cannot lock %s: %s" % (path, why), str(path)))
        return None
    age = _lock_age(path)
    if age is None or age >= timeout:
        if age is not None:
            findings.append(diagnostics.Diagnostic(
                "CT4321", diagnostics.WARNING,
                "broke a lock left behind %d s ago: %s" % (age, path),
                str(path)))
        aside = path.with_name("%s.broken.%d" % (path.name, os.getpid()))
        try:
            os.rename(path, aside)
        except OSError:                                     # noqa: BLE001
            # Somebody moved it first. Theirs to re-create, and the
            # create below is what settles who gets it.
            pass
        else:
            try:
                aside.unlink()
            except OSError:                                 # noqa: BLE001
                pass
        taken, why = _create_lock(path, token)
        if taken:
            return token
        if why is not None:
            findings.append(_problem(
                "CT4322", "cannot lock %s: %s" % (path, why), str(path)))
            return None
    findings.append(_problem(
        "CT4320", "another build holds %s; nothing was done" % path,
        str(path)))
    return None


def _release(path: Path, token: str) -> None:
    """Removes the lock, but only while it is still ours.

    A run whose lock was broken while it worked must not delete the
    lock of the run that broke it, and then a third would walk in.
    """
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("token") != token:
            return
    except (OSError, ValueError, AttributeError):
        return
    try:
        path.unlink()
    except OSError:                                         # noqa: BLE001
        pass


def _create_lock(path: Path, token: str) -> tuple[bool, str | None]:
    """Creates the lock file, or says why not.

    Returns:
        tuple[bool, str|None]: Whether it was created, and where it was
        not, the reason unless the reason is that somebody else holds
        it. None for that one case, because it is the only one the
        caller may answer by waiting or breaking.
    """
    for attempt in (0, 1):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                             0o644)
        except FileExistsError:
            return False, None
        except OSError as error:
            if attempt:
                return False, str(error)
            # There may be no directory to lock in yet. The state file
            # lives there too, so make it and try once more. A
            # permission problem comes back on the second turn with its
            # own message rather than as "somebody else has it".
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as made:
                return False, str(made)
            continue
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump({"pid": os.getpid(), "started": time.time(),
                       "host": socket.gethostname(), "token": token}, out)
        return True, None
    return False, "could not be created"


def _lock_age(path: Path) -> float | None:
    """How long the lock has stood, in seconds, or None where it is gone."""
    stamp: Any = None
    try:
        stamp = json.loads(path.read_text(encoding="utf-8")).get("started")
    except (OSError, ValueError, AttributeError):
        stamp = None
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return None
    return max(time.time() - float(stamp), 0.0)


# -- The report -------------------------------------------------------

def _report(manifest: Manifest, started: str, duration: float, jobs: int,
            dry_run: bool, results: list[dict[str, Any]],
            findings: list[diagnostics.Diagnostic],
            counts: dict[str, int]) -> dict[str, Any]:
    """One JSON document, whatever happened.

    Renders and writes are counted apart on purpose. weewx's visible
    "Generated %d files" is a write count, and watching it fall from 14
    to 2 is the intended effect of this module, not a breakage.
    """
    grades = [str(result["status"]) for result in results]
    return {
        "version": REPORT_VERSION,
        "manifest": str(manifest.path),
        "started": started,
        "duration": round(duration, 3),
        "jobs": jobs,
        "dry_run": dry_run,
        "targets": {
            "total": len(results),
            "skipped": grades.count("skipped"),
            "rendered": len(grades) - grades.count("skipped"),
            "written": grades.count("written"),
            "unchanged": grades.count("unchanged"),
            "failed": grades.count("failed"),
        },
        "cache": {"hits": counts["hits"], "misses": counts["misses"],
                  "directory": str(manifest.cache)},
        "results": results,
        "findings": [finding.as_dict() for finding in findings],
    }


def unusable(path: Path, message: str) -> dict[str, Any]:
    """The report of a run that never started.

    A manifest that cannot be read still owes the caller a document:
    ``--report`` is what a scheduler reads, and a missing file there
    says nothing while an empty run says why.
    """
    return {
        "version": REPORT_VERSION,
        "manifest": str(path),
        "started": datetime.now(timezone.utc).isoformat(),
        "duration": 0.0,
        "jobs": 0,
        "dry_run": False,
        "targets": {"total": 0, "skipped": 0, "rendered": 0, "written": 0,
                    "unchanged": 0, "failed": 0},
        "cache": {"hits": 0, "misses": 0, "directory": ""},
        "results": [],
        "findings": [_problem("CT4300", message, str(path)).as_dict()],
    }


def exit_code(report: dict[str, Any]) -> int:
    """What the process should exit with.

    Nothing changed is the ordinary case for a five-minute timer and
    has to be 0, or every unit in the world reports failed.
    """
    codes = {str(finding.get("code")) for finding in report["findings"]}
    if "CT4320" in codes:
        return LOCKED
    if "CT4300" in codes:
        return UNUSABLE
    if int(report["targets"]["failed"]):
        return FAILED
    return OK


def findings_of(report: dict[str, Any]) -> list[diagnostics.Diagnostic]:
    """The findings of a report, as diagnostics again."""
    return [diagnostics.Diagnostic(**dict(finding))
            for finding in report["findings"]]


def as_text(report: dict[str, Any]) -> str:
    """A summary short enough for a cron mail nobody asked for."""
    counts = report["targets"]
    head = "%s: %d targets, %d written, %d unchanged, %d skipped, %d failed"\
        % (report["manifest"], counts["total"], counts["written"],
           counts["unchanged"], counts["skipped"], counts["failed"])
    lines = [head, "  %d rendered in %.3f s, cache %d hits, %d misses"
             % (counts["rendered"], report["duration"],
                report["cache"]["hits"], report["cache"]["misses"])]
    if report["dry_run"]:
        lines.append("  dry run: nothing was written")
    for result in report["results"]:
        if result["status"] in ("written", "failed"):
            lines.append("  %-9s %s (%s)"
                         % (result["status"], result["output"],
                            result["reason"]))
    return "\n".join(lines)


# -- Small change ------------------------------------------------------

def _store(directory: Path) -> cache.Store:
    """Installs the persistent compile cache, once per process."""
    key = str(directory)
    store = _STORES.get(key)
    if store is None:
        store = cache.install(directory)
        _STORES[key] = store
    return store


def _digest_of_file(path: Path) -> str:
    """The hash of a file, or a mark for one that yields none.

    Two marks and they mean different things. ABSENT is a file that is
    not there, which is an answer: a skin's optional hook is absent on
    purpose and its absence is stable, so a target may still be
    skipped. UNREADABLE is a file that is there and will not open, and
    that is not an answer. Letting the OSError out killed the whole run
    over one chmod, taking every unrelated target and the report with
    it; recording it instead means this target renders every time until
    somebody fixes the permission, which is the safe direction.
    """
    try:
        digest = write.digest_of_file(path)
    except OSError:                                         # noqa: BLE001
        return UNREADABLE
    return digest if digest is not None else ABSENT


def _posix(name: str) -> str:
    return Path(name).as_posix()


def _relative(base: Path, name: str) -> str:
    """A dependency's name as the state records it.

    Relative to the base where it can be, so that the state file says
    the same thing on two machines and after a move.
    """
    path = Path(name)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _problem(code: str, message: str, file: str = "", line: int = 0,
             column: int = 0) -> diagnostics.Diagnostic:
    return diagnostics.Diagnostic(code, diagnostics.ERROR, message, file,
                                  line, column)


def _findings_from(result: dict[str, Any]) -> list[diagnostics.Diagnostic]:
    return [diagnostics.Diagnostic(**dict(finding))
            for finding in result.get("findings", [])]


def _public(result: dict[str, Any]) -> dict[str, Any]:
    """The part of a render result that belongs in the report."""
    return {key: result[key] for key in
            ("output", "template", "status", "reason", "bytes", "digest",
             "seconds")}


def _where(exc: BaseException) -> tuple[int, int]:
    """Line and column of an error, where Cheetah names them."""
    place = getattr(exc, "lineCol", None)
    if isinstance(place, tuple) and len(place) == 2:
        return (_number(place[0]), _number(place[1]))
    return (_number(getattr(exc, "lineno", 0)),
            _number(getattr(exc, "col", 0)))


def _number(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) \
        else 0
