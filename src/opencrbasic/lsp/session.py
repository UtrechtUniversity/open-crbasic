"""Server state and scheduling, independent of the protocol library.

Owns the open documents (overlays), the configured entries, per-entry debounced analysis in a
spawn-context process pool, and the merge of per-entry results into per-file diagnostics. The
protocol adapter (`server.py`) feeds it events and supplies a `publish` callback. Analysis runs
in worker processes and is awaited, so the event loop never blocks on it.
"""

import asyncio
import hashlib
import itertools
import multiprocessing
import os
import pickle
import re
import sys
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import quote

from .. import checkconfig, positions
from .config import Config, discover
from .worker import JobResult, OutputResult, run_analysis, run_compiler_output

SRC_COMPILE = "opencrbasic"
SRC_INTERNAL = "opencrbasic (internal)"
SRC_SERVER = "opencrbasic (server)"
SRC_CHECK = "opencrbasic (check)"          # opencrbasic checks (`OCB...`): the reference accepts these
SRC_COVERAGE = "opencrbasic (coverage)"    # coverage notices: reference rules opencrbasic does not model
_CHANNELS = {"check": SRC_CHECK, "notice": SRC_COVERAGE}
_SEVERITIES = {"info": "information"}     # check severity -> LSP severity name, where they differ
MARKERS = ("Internal error: unable to compile program.", "Internal error: unable to parse program.")
INFO_UNINCLUDED = ("No configured entry program includes this file; diagnostics come from the "
                   "entry programs that include it.")
INFO_NOT_ENTRY = ("This file is not an entry program for the configured logger and entries, and "
                  "no entry program includes it.")
_LINE_END = re.compile(r"\r\n|\r|\n")
MIB = 1024 * 1024
CREATED, CHANGED, DELETED = "created", "changed", "deleted"


@dataclass(frozen=True)
class Diag:
    line: int            # 0-based LSP line where the range starts; the range starts at character 0
    end_line: int        # 0-based LSP line where the range ends (== line for one physical line)
    end: int             # end character (negotiated position-encoding units) of end_line
    severity: str        # "error" | "warning" | "information" | "hint"
    message: str
    source: str
    code: str | None = None
    link: str | None = None                       # `codeDescription.href`
    data: tuple[tuple[str, object], ...] = ()     # `data`: the entry and the diagnostic's index (quick fixes)


# publish(uri, version or None, diagnostics)
Publish = Callable[[str, "int | None", "list[Diag]"], None]


@dataclass
class Doc:
    uri: str
    version: int
    text: str
    data: bytes = b""
    replaced: bool = False


@dataclass
class Raw:
    severity: str
    message: str
    line: int            # compiler line (0: none)
    internal: bool = False
    span: tuple[int, int] | None = None   # physical (first, last) line in the target file
    source: str = SRC_COMPILE
    code: str | None = None
    link: str | None = None
    index: int | None = None              # position in the job's diagnostics (quick fixes)


class Analysed:
    """The parsed program of one entry for the language features. Built from the pickled program
    a worker returned; the unpickling happens lazily in a thread (`Session.load`). The caches hold
    objects the core feature modules derive from the program (declared names, binding index...)."""

    def __init__(self, entry: Path, logger: str, overlays: tuple[tuple[str, bytes], ...],
                 versions: dict[Path, int], blob: bytes, job: JobResult | None = None):
        self.entry = entry
        self.token = 0                      # set by `Session._apply`: which applied result this is
        self.ok = True if job is None else job.ok
        self.failures = () if job is None else job.failures
        self.reads = {} if job is None else {Path(p): h for p, h in job.reads}
        self.raw_diagnostics = () if job is None else job.diagnostics
        self.logger = logger
        self.overlays = overlays
        self.versions = dict(versions)      # open documents (path -> version) the analysis read
        self.blob: bytes | None = blob
        self.size = len(blob)               # pickled bytes, for the cache budget
        self.result = None                  # analysis.AnalysisResult with the program only
        self.failed = False
        self.lock: asyncio.Lock | None = None
        self._cache: dict[str, object] = {}
        self._cache_lock = threading.Lock()
        self.guard = threading.RLock()      # serialises core feature calls on this program

    def build(self):
        """Runs in a thread. The result has the program and the request; its diagnostics are
        empty (diagnostics are published from the job result, not from here)."""
        from .. import analysis

        try:
            program = pickle.loads(self.blob or b"")
        except Exception:
            self.failed = True
            return None
        self.blob = None
        request = analysis.AnalysisRequest(
            self.entry, self.logger, {Path(p): data for p, data in self.overlays})
        diagnostics = tuple(
            analysis.AnalysisDiagnostic(sev, msg, line, file, None if path is None else Path(path),
                                        "", span)
            for sev, msg, line, path, file, span in self.raw_diagnostics)
        return analysis.AnalysisResult(
            analysis.API_VERSION, request, self.ok, None, diagnostics, (), tuple(self.failures),
            analysis.Dependencies(dict(self.reads), {}), program)

    def cached(self, key: str, make: Callable[[], object]) -> object:
        with self._cache_lock:
            if key not in self._cache:
                self._cache[key] = make()
            return self._cache[key]


class _LockedHelp:
    """A `helpdocs.HelpProvider` shared by the request threads (its lazy index is not locked)."""

    def __init__(self, directory: Path):
        from ..helpdocs import HelpProvider

        self.directory = directory
        self._provider = HelpProvider(directory)
        self._lock = threading.Lock()
        self.warming = False

    def lookup(self, name: str):
        with self._lock:
            return self._provider.lookup(name)

    def warm(self) -> None:
        with self._lock:
            self._provider.names()


@dataclass
class Entry:
    path: Path
    requested: int = 0
    done: int = 0
    not_before: float = 0.0
    task: asyncio.Task | None = None
    removed: bool = False
    settled: bool = False                       # a result or an error has been applied
    reads: dict[str, str | None] = field(default_factory=dict)   # lower-cased path -> sha256
    dirs: frozenset[str] = frozenset()                            # lower-cased listed directories
    contrib: dict[Path, list[Raw]] = field(default_factory=dict)
    versions: dict[Path, int] = field(default_factory=dict)       # open docs at capture
    error: str | None = None
    error_limit: bool = False                   # `error` is a server limit, not a failure
    triggers: set[Path] | None = field(default_factory=set)  # paths re-requesting it while a job
                                                # runs (None: a request not tied to a path)
    used: int = 0                               # last feature use (cache eviction order)
    evicted: bool = False                       # its program was dropped by the cache budget
    analysed: Analysed | None = None            # newest program that was parsed, for features
    analysed_current: bool = False              # the newest applied analysis produced it
    root_used: tuple[str | None] | None = None  # (config root,) its last job searched; None: no job yet


@dataclass
class Snapshot:
    overlays: tuple[tuple[str, bytes], ...]
    versions: dict[Path, int]
    logger: str
    timeout: float
    max_file_bytes: int | None = None
    kind: str = "analysis"              # the worker job: "analysis" or "compiler_output"
    checks: tuple[tuple[str, str], ...] = ()   # LSP check severities (analysis only)
    notices: bool = False                      # coverage notices (analysis only)


class ServerError(Exception):
    def __init__(self, message: str, limit: bool = False, result: JobResult | None = None,
                 reason: str = "failed"):
        super().__init__(message)
        self.limit = limit              # a configured bound was hit: reported as a server warning
        self.result = result            # the analysis that was discarded (its reads still count)
        self.reason = reason            # "failed", "timeout" or "too_large" (command refusals)


class _Broken:
    """Marks a worker pool break, internal to `_run_job`."""


_BROKEN = _Broken()


def _oversize(path: Path, overlays: dict[Path, int], limit: int) -> tuple[Path, int] | None:
    """`path` and its size (open text, else the disk file) when larger than `limit` bytes."""
    try:
        size = overlays[path] if path in overlays else path.stat().st_size
    except OSError:
        return None
    return (path, size) if size > limit else None


def _key(path: Path) -> str:
    return str(path).lower()


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def display_text(message: str, encoding: str) -> str:
    """Compiler message text for display. The compiler decodes source bytes as Latin-1, so a
    name written in the editor's encoding shows as mojibake; encode back to the original bytes and
    decode with the configured editor encoding. Display only (LSP-01f, host finding H-04).
    Text with a character above U+00FF cannot be compiler text and is left unchanged."""
    try:
        return message.encode("latin-1").decode(encoding, "replace")
    except (UnicodeEncodeError, LookupError):
        return message


def _kill(pool: ProcessPoolExecutor) -> None:
    for proc in list((getattr(pool, "_processes", None) or {}).values()):
        try:
            proc.kill()
        except Exception:
            pass
    pool.shutdown(wait=False, cancel_futures=True)


class Session:
    def __init__(self, publish: Publish, config: Config | None = None, workers: int = 2):
        self.publish = publish
        self.config = config or Config()
        self.workers = workers
        self.position_encoding = "utf-16"          # negotiated by the server adapter
        self.folders: list[Path] = []
        self.docs: dict[Path, Doc] = {}
        self.entries: dict[Path, Entry] = {}
        self.discovered: set[Path] = set()
        self._last: dict[str, tuple] = {}
        self._last_uris: dict[Path, str] = {}
        self._uris: dict[Path, str] = {}           # the client's URI of every path it opened
        self.folder_uris: dict[Path, str] = {}     # the client's URI of each workspace folder
        self._pool: ProcessPoolExecutor | None = None
        self._pool_gen = 0
        self._sem: asyncio.Semaphore | None = None
        self._solo: asyncio.Lock | None = None
        self._closed = False
        self._ready = False                       # first configure() has finished
        self._configured = asyncio.Event()
        self.on_analysed: Callable[[], None] | None = None    # a new program is available
        self._event: asyncio.Event | None = None
        self._help: dict[str, _LockedHelp] = {}
        self._background: set[asyncio.Task] = set()
        self._running = 0                          # entry tasks alive (<= config.max_queued_jobs)
        self._deferred: dict[Path, Entry] = {}     # entries waiting for a task slot
        self._discover_lock: asyncio.Lock | None = None
        self._use = itertools.count(1)
        self._tokens = itertools.count(1)
        self._noticed: set[str] = set()
        self.on_message: Callable[[str], None] | None = None   # server message (limits)

    # ---- logging -------------------------------------------------------------------------

    @staticmethod
    def log(message: str) -> None:
        print(f"opencrbasic-lsp: {message}", file=sys.stderr, flush=True)

    def notice(self, key: str, message: str) -> None:
        """A bound was hit: logged and sent to the client once per `key` until `reset_notice`."""
        if key in self._noticed:
            return
        self._noticed.add(key)
        self.log(message)
        if self.on_message is not None:
            self.on_message(message)

    # ---- configuration and entries -------------------------------------------------------

    def _encode(self, text: str) -> tuple[positions.EditorBytes, bool]:
        """The compiler bytes of `text`, which keep `text` for the position mapping."""
        enc = self.config.encoding
        try:
            return positions.EditorBytes(text.encode(enc), text), False
        except UnicodeEncodeError:
            return positions.EditorBytes(text.encode(enc, errors="replace"), text), True

    async def configure(self, config: Config, folders: list[Path] | None = None) -> None:
        old = self.config
        self.config = config
        if folders is not None:
            self.folders = folders
        touched: set[Path] = set()
        if old.encoding != config.encoding:
            for path, doc in self.docs.items():
                doc.data, doc.replaced = self._encode(doc.text)
                touched.add(path)
        await self._rediscover()
        self._ready = True
        self._configured.set()
        self._sync_entries(force=old.analysis_key() != config.analysis_key())
        if touched:
            self._publish(touched)
        self._warm_help()

    async def _rediscover(self) -> None:
        if self._discover_lock is None:
            self._discover_lock = asyncio.Lock()
        async with self._discover_lock:           # the newest folders/config win, in order
            self.discovered = set(await asyncio.to_thread(
                discover, list(self.folders), self.config.entries, self.config.exclude))

    async def set_folders(self, added: list[Path], removed: list[Path]) -> None:
        """`workspace/didChangeWorkspaceFolders`: discovery runs again over the new folder set;
        entries of a removed folder drop out (their diagnostics are cleared) unless open."""
        gone = {p.absolute() for p in removed}
        folders = [f for f in self.folders if f not in gone]
        folders += [a.absolute() for a in added if a.absolute() not in folders]
        if folders == self.folders:
            return
        self.folders = folders
        await self._rediscover()
        self._sync_entries()

    async def files_changed(self, events: list[tuple[Path, str]]) -> None:
        """`workspace/didChangeWatchedFiles` for files that are not open (an open document's text
        wins over the disk, so its disk changes are ignored until it is closed). A created or
        deleted file may be an entry, so discovery runs again; every entry whose last analysis
        read the file, or listed its directory, is re-analysed (that is how a created include
        resolves a missing one, and a deleted one is reported missing). An event for a path that
        is not a program or library file is a directory: everything read below it is affected."""
        if self._closed:
            return
        known = self.config.entry_suffixes() | {".crb"}
        rediscover = False
        files: list[Path] = []
        trees: list[Path] = []
        for path, kind in events:
            path = path.absolute()
            if path.name.lower() == checkconfig.FILE_NAME:   # the entries that probed it are re-analysed
                if path not in self.docs:
                    files.append(path)
            elif path.suffix.lower() in known:
                if kind != CHANGED:
                    rediscover = rediscover or path.suffix.lower() != ".crb"
                if path not in self.docs:
                    files.append(path)
            elif kind == CREATED or (kind == DELETED and self._known_below(path)):
                rediscover = True
                trees.append(path)
        if rediscover:
            await self._rediscover()
            self._sync_entries()
        delay = self.config.debounce_ms / 1000
        for path in dict.fromkeys(files):
            shortcut = await asyncio.to_thread(_digest, path)
            self._changed(path, delay, shortcut=shortcut)
        for path in dict.fromkeys(trees):
            self._changed_tree(path, delay)

    def _known_below(self, path: Path) -> bool:
        """Whether `path` is, or contains, something the session knows: a discovered or open
        file, or a file or directory an entry read or listed. (A deleted file of another type, or a
        folder with no program in it, changes nothing.)"""
        key = _key(path)
        below = key.rstrip(os.sep) + os.sep
        names = [_key(p) for p in (*self.discovered, *self.docs)]
        for entry in self.entries.values():
            names += [*entry.reads, *entry.dirs, _key(entry.path)]
        return any(n == key or n.startswith(below) for n in names)

    def _config_root(self, path: Path) -> str | None:
        """The workspace folder holding `path`: the check configuration is searched no higher,
        which is the part the `**/opencrbasic.toml` watcher covers."""
        return next((str(f) for f in self.folders if f == path or f in path.parents), None)

    def _wanted_entries(self) -> set[Path]:
        suffixes = self.config.entry_suffixes()
        return set(self.discovered) | {p for p in self.docs if p.suffix.lower() in suffixes}

    def _sync_entries(self, force: bool = False) -> None:
        wanted = self._wanted_entries()
        touched: set[Path] = set()
        for path in [p for p in self.entries if p not in wanted]:
            entry = self.entries.pop(path)
            entry.removed = True
            self._deferred.pop(path, None)
            if entry.task:
                entry.task.cancel()
            touched |= set(entry.contrib) | {path}
            self._notify()
        for path in sorted(wanted):
            entry = self.entries.get(path)
            if entry is None:
                entry = self.entries[path] = Entry(path)
                self._request(entry, 0.0)
            elif force or (entry.root_used is not None and entry.root_used != (self._config_root(path),)):
                self._request(entry, 0.0)       # (folders arrived or changed: another config root)
        self._publish(touched | self._open_non_entry_paths())

    # ---- document events -----------------------------------------------------------------

    def did_open(self, path: Path, uri: str, version: int, text: str) -> None:
        doc = Doc(uri, version, text)
        doc.data, doc.replaced = self._encode(text)
        self.docs[path] = doc
        self._uris[path] = uri
        self._sync_entries()
        self._changed(path, 0.0, shortcut=hashlib.sha256(doc.data).hexdigest())
        self._publish({path})

    def did_change(self, path: Path, version: int, text: str) -> None:
        doc = self.docs.get(path)
        if doc is None:
            return
        replaced_before = doc.replaced
        doc.version, doc.text = version, text
        doc.data, doc.replaced = self._encode(text)
        self._changed(path, self.config.debounce_ms / 1000)
        if doc.replaced != replaced_before:
            self._publish({path})

    def did_save(self, path: Path) -> None:
        if path in self.docs:
            self._changed(path, 0.0)

    def did_close(self, path: Path) -> None:
        if self.docs.pop(path, None) is None:
            return
        try:
            data = path.read_bytes()
            shortcut = hashlib.sha256(data).hexdigest()
        except OSError:
            shortcut = None
        self._sync_entries()
        self._changed(path, 0.0, shortcut=shortcut)
        self._publish({path})

    def _affected(self, path: Path) -> list[Entry]:
        key, parent = _key(path), _key(path.parent)
        return [e for e in self.entries.values()
                if not e.settled or e.path == path or key in e.reads or parent in e.dirs]

    def _changed(self, path: Path, delay: float, shortcut: str | None = None) -> None:
        """Re-analyse the entries `path` affects. With `shortcut` (the sha256 of the text the
        entries should now read), an entry whose last analysis read exactly that is not re-run;
        only its captured overlay version is updated."""
        doc = self.docs.get(path)
        for entry in self._affected(path):
            if entry.settled and shortcut is not None and not entry.error \
                    and entry.reads.get(_key(path)) == shortcut:
                for versions in (entry.versions,
                                 entry.analysed.versions
                                 if entry.analysed is not None and entry.analysed_current else {}):
                    if doc is None:
                        versions.pop(path, None)
                    else:
                        versions[path] = doc.version
                self._notify()
                continue
            self._request(entry, delay, path)

    def _changed_tree(self, path: Path, delay: float) -> None:
        """A directory appeared or disappeared: re-analyse the entries that read or listed it or
        anything below it, and the entries that have not completed a first analysis."""
        key = _key(path)
        below = key.rstrip(os.sep) + os.sep
        for entry in list(self.entries.values()):
            names = [*entry.reads, *entry.dirs, _key(entry.path)]
            if not entry.settled or any(n == key or n.startswith(below) for n in names):
                self._request(entry, delay)

    # ---- scheduling ----------------------------------------------------------------------

    def _request(self, entry: Entry, delay: float, path: Path | None = None) -> None:
        """Re-analyse `entry`. `path`: the file whose change asks for it (see `_unaffected`)."""
        loop = asyncio.get_running_loop()
        entry.requested += 1
        if path is None:
            entry.triggers = None
        elif entry.triggers is not None:
            entry.triggers.add(path)
        entry.not_before = loop.time() + delay
        if entry.task is None or entry.task.done():
            self._start(entry)

    def _start(self, entry: Entry) -> None:
        """Give `entry` a task, or hold it back while `maxQueuedJobs` entry tasks are alive."""
        if self._running >= self.config.max_queued_jobs:
            self._deferred[entry.path] = entry
            if len(self._deferred) == 1:
                self.notice("queue", f"{self._running} analyses are queued (maxQueuedJobs="
                            f"{self.config.max_queued_jobs}); further entry programs wait for a "
                            "free slot and are analysed in turn.")
            return
        self._running += 1
        entry.task = asyncio.get_running_loop().create_task(self._entry_loop(entry))
        entry.task.add_done_callback(self._task_done)

    def _task_done(self, _task: asyncio.Task) -> None:
        self._running -= 1
        while self._deferred and self._running < self.config.max_queued_jobs and not self._closed:
            first = next((p for p in self._deferred if p in self.docs), next(iter(self._deferred)))
            entry = self._deferred.pop(first)
            if not entry.removed and entry.done != entry.requested:
                self._start(entry)
        if not self._deferred:
            self._noticed.discard("queue")

    def _snapshot(self) -> Snapshot:
        return Snapshot(tuple((str(p), d.data) for p, d in self.docs.items()),
                        {p: d.version for p, d in self.docs.items()},
                        self.config.logger, self.config.analysis_timeout_ms / 1000,
                        self.config.max_file_bytes, checks=self.config.checks,
                        notices=self.config.coverage_notices)

    async def _entry_loop(self, entry: Entry) -> None:
        loop = asyncio.get_running_loop()
        try:
            while not entry.removed and entry.done != entry.requested:
                while (wait := entry.not_before - loop.time()) > 0:
                    await asyncio.sleep(wait)
                if entry.removed:
                    break
                target = entry.requested
                entry.triggers = set()
                snap = self._snapshot()
                outcome = await self._guarded_job(entry, snap)
                if not entry.removed and entry.requested != target \
                        and self._unaffected(entry, outcome):
                    target = entry.requested         # the newer requests cannot change it
                entry.done = target
                if entry.removed or entry.requested != target:
                    continue                         # superseded: discard
                self._apply(entry, snap, outcome)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log("internal error in the scheduler:\n" + traceback.format_exc())
            entry.done = entry.requested
            self._apply(entry, self._snapshot(), ServerError(
                "Internal error in the language server scheduler; see the server log."))

    @staticmethod
    def _unaffected(entry: Entry, outcome: JobResult | ServerError) -> bool:
        """Whether the requests made while `outcome` was computed are all for files it neither
        read (or missed) nor could find through a directory listing, the entry itself excluded:
        the result then still stands, and `_apply` checks the open versions it read."""
        if entry.triggers is None or not isinstance(outcome, JobResult):
            return False
        reads = {_key(Path(p)) for p, _ in outcome.reads}
        dirs = {_key(Path(d)) for d in outcome.listings}
        return all(p != entry.path and _key(p) not in reads and _key(p.parent) not in dirs
                   for p in entry.triggers)

    async def _guarded_job(self, entry: Entry, snap: Snapshot) -> JobResult | ServerError:
        """`_run_job` with the `maxFileBytes` bound: the entry file is checked before the job, and
        the worker refuses (without reading) every file of this analysis that is over the limit, so
        the bound applies to what the current text includes, not to what an earlier one did."""
        limit = self.config.max_file_bytes
        sizes = {Path(p): len(data) for p, data in snap.overlays}
        over = await asyncio.to_thread(_oversize, entry.path, sizes, limit)
        result = None
        if over is None:
            outcome = await self._run_job(entry, snap)
            assert not isinstance(outcome, OutputResult)
            if isinstance(outcome, ServerError) or not outcome.oversized:
                return outcome
            over, result = (Path(outcome.oversized[0][0]), outcome.oversized[0][1]), outcome
        path, size = over
        self.notice(f"size:{path}", f"{path.name} is {size} bytes, above the maxFileBytes limit "
                    f"of {limit}; the entry programs that read it are not analysed.")
        return ServerError(f"Analysis of {entry.path.name} skipped: {path.name} is {size} bytes, "
                           f"above the maxFileBytes limit of {limit}.", limit=True, result=result,
                           reason="too_large")

    async def compiler_output(self, entry: Entry) -> OutputResult | ServerError:
        """The `crbc` text for `entry` over the open documents, computed in the worker pool with
        the analysis bounds: the `maxFileBytes` limit applies to the entry (checked first) and to
        every file this compilation reads, and `analysisTimeoutMs` kills the worker as for an
        analysis (docs/lsp-api.md "Inlay hints and commands (LSP-09c)")."""
        snap = replace(self._snapshot(), kind="compiler_output")
        limit = self.config.max_file_bytes
        sizes = {Path(p): len(data) for p, data in snap.overlays}
        over = await asyncio.to_thread(_oversize, entry.path, sizes, limit)
        if over is None:
            outcome = await self._run_job(entry, snap)
            assert not isinstance(outcome, JobResult)
            if isinstance(outcome, ServerError) or not outcome.oversized:
                return outcome
            over = Path(outcome.oversized[0][0]), outcome.oversized[0][1]
        path, size = over
        return ServerError(f"{path.name} is {size} bytes, above the maxFileBytes limit of "
                           f"{limit}.", limit=True, reason="too_large")

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.workers)
        return self._sem

    def _get_pool(self) -> tuple[ProcessPoolExecutor, int]:
        if self._pool is None:
            self._pool = ProcessPoolExecutor(
                max_workers=self.workers, mp_context=multiprocessing.get_context("spawn"))
        return self._pool, self._pool_gen

    def _reset_pool(self, gen: int) -> None:
        if self._pool is not None and self._pool_gen == gen:
            pool, self._pool = self._pool, None
            self._pool_gen += 1
            _kill(pool)

    async def _run_job(self, entry: Entry, snap: Snapshot) -> JobResult | OutputResult | ServerError:
        """One worker job (`snap.kind`) in the pool. When the pool breaks (a crash, or a kill after
        another job's timeout) the pool is recreated and the job is retried once with the pool to
        itself, so a crash is only ever blamed on the job that was running alone."""
        async with self._semaphore():
            outcome = await self._attempt(entry, snap)
        if not isinstance(outcome, _Broken):
            return outcome
        async with self._solo_lock():
            slots = self._semaphore()
            held = 0
            try:
                while held < self.workers:      # every permit taken is released, even on cancel
                    await slots.acquire()
                    held += 1
                outcome = await self._attempt(entry, snap)
            finally:
                for _ in range(held):
                    slots.release()
        if isinstance(outcome, _Broken):
            doing = "analysing" if snap.kind == "analysis" else "compiling"
            return ServerError(f"The analysis worker process terminated unexpectedly while "
                               f"{doing} {entry.path.name}; the worker pool was restarted.")
        return outcome

    def _solo_lock(self) -> asyncio.Lock:
        if self._solo is None:
            self._solo = asyncio.Lock()
        return self._solo

    async def _attempt(self, entry: Entry, snap: Snapshot
                       ) -> JobResult | OutputResult | ServerError | _Broken:
        if self._closed:
            return ServerError("server is shutting down")
        loop = asyncio.get_running_loop()
        label = "Analysis" if snap.kind == "analysis" else "Compilation"
        pool, gen = self._get_pool()
        try:
            if snap.kind == "analysis":
                root = self._config_root(entry.path)
                entry.root_used = (root,)
                future = loop.run_in_executor(pool, run_analysis, str(entry.path), snap.logger,
                                              snap.overlays, snap.max_file_bytes, snap.checks, snap.notices,
                                              root)
            else:
                future = loop.run_in_executor(pool, run_compiler_output, str(entry.path), snap.logger,
                                              snap.overlays, snap.max_file_bytes)
            return await asyncio.wait_for(future, snap.timeout)
        except TimeoutError:
            self._reset_pool(gen)
            return ServerError(f"{label} of {entry.path.name} timed out after "
                               f"{round(snap.timeout * 1000)} ms; the analysis worker was "
                               "restarted.", reason="timeout")
        except BrokenProcessPool:
            self._reset_pool(gen)
            return _BROKEN
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ServerError(f"{label} of {entry.path.name} failed in the worker: "
                               f"{type(exc).__name__}: {exc}")

    # ---- applying results ----------------------------------------------------------------

    def _apply(self, entry: Entry, snap: Snapshot, outcome: JobResult | ServerError) -> None:
        old_paths = set(entry.contrib)
        installed = False
        if isinstance(outcome, ServerError):
            entry.error = str(outcome)
            entry.error_limit = outcome.limit
            entry.contrib = {}
            entry.analysed_current = False
            if outcome.result is not None:          # keep watching what it read
                self._record_reads(entry, outcome.result)
        else:
            reads = {_key(Path(p)): h for p, h in outcome.reads}
            stale = any((self.docs[p].version if p in self.docs else None) != snap.versions.get(p)
                        for p in set(self.docs) | set(snap.versions)
                        if _key(p) in reads or p == entry.path)
            if stale:
                self._request(entry, 0.0)
                return
            entry.error = None
            entry.error_limit = False
            self._record_reads(entry, outcome)
            entry.contrib = self._contributions(entry, outcome)
            program = outcome.program
            entry.analysed_current = program is not None
            if program is not None:
                entry.analysed = Analysed(entry.path, snap.logger, snap.overlays, snap.versions,
                                          program, outcome)
                entry.analysed.token = next(self._tokens)   # names this result in quick-fix `data`
                entry.used = next(self._use)
                entry.evicted = False
                installed = True
                self._enforce_cache(entry)
        entry.versions = dict(snap.versions)
        entry.settled = True
        self._publish(old_paths | set(entry.contrib) | {entry.path} | self._open_non_entry_paths())
        self._notify()
        if installed and self.on_analysed is not None:
            self.on_analysed()

    @staticmethod
    def _record_reads(entry: Entry, result: JobResult) -> None:
        entry.reads = {_key(Path(p)): h for p, h in result.reads}
        entry.dirs = frozenset(_key(Path(d)) for d in result.listings)

    def _enforce_cache(self, keep: Entry) -> None:
        """Programs kept for the features stay within `maxCacheMb`: the least recently used
        program of another entry is dropped (its diagnostics stay) and rebuilt by a new analysis
        when a feature request needs it."""
        budget = self.config.max_cache_mb * MIB
        held = [e for e in self.entries.values() if e.analysed is not None]
        sizes = {id(e): e.analysed.size for e in held if e.analysed is not None}
        total = sum(sizes.values())
        for victim in sorted((e for e in held if e is not keep), key=lambda e: e.used):
            if total <= budget:
                break
            total -= sizes[id(victim)]
            victim.analysed, victim.analysed_current, victim.evicted = None, False, True
            self.notice("cache", f"the parsed programs exceed maxCacheMb={self.config.max_cache_mb}"
                        "; the least recently used ones are dropped and rebuilt when a language "
                        "feature needs them.")

    @staticmethod
    def _contributions(entry: Entry, result: JobResult) -> dict[Path, list[Raw]]:
        out: dict[Path, list[Raw]] = {}
        failures = "\n".join(result.failures)
        attached = False
        for index, (severity, message, line, path, file, span) in enumerate(result.diagnostics):
            target = Path(path) if path else entry.path
            if not path:
                message = f"{file}:{line}: {message}" if file else message
                line, span = 0, None
            internal = message in MARKERS
            if internal and failures and not attached:
                message, attached = f"{message}\n{failures}", True
            out.setdefault(target, []).append(Raw(severity, message, line, internal, span, index=index))
        for channel, code, severity, message, line, path, file, span, link in result.findings:
            target = Path(path) if path else entry.path
            if not path:
                message = f"{file}:{line}: {message}" if file else message
                line, span = 0, None
            out.setdefault(target, []).append(Raw(_SEVERITIES.get(severity, severity), message, line,
                                                  span=span, source=_CHANNELS[channel], code=code, link=link))
        if result.config_error:
            out.setdefault(entry.path, []).append(Raw(
                "warning", f"Check configuration ignored, no checks ran: {result.config_error}", 0,
                source=SRC_SERVER))
        if failures and not attached:
            out.setdefault(entry.path, []).append(
                Raw("error", f"Internal error in the compiler:\n{failures}", 0, True))
        return out

    # ---- publishing ----------------------------------------------------------------------

    def _open_non_entry_paths(self) -> set[Path]:
        return {p for p in self.docs if p not in self.entries}

    def _text_of(self, path: Path) -> str:
        doc = self.docs.get(path)
        if doc is not None:
            return doc.text
        try:
            return path.read_bytes().decode(self.config.encoding, errors="replace")
        except (OSError, LookupError):
            return ""

    def display(self, message: str) -> str:
        return display_text(message, self.config.encoding)

    def _compose(self, path: Path) -> list[Diag]:
        text = self._text_of(path)
        lines = _LINE_END.split(text)
        doc = self.docs.get(path)
        current = doc.version if doc else None

        last_idx = len(lines) - 1

        def make(line: int, severity: str, message: str, source: str,
                 span: tuple[int, int] | None = None, raw: Raw | None = None, entry: Path | None = None,
                 token: int | None = None) -> Diag:
            if span is not None:
                first = min(max(span[0] - 1, 0), last_idx)
                last = min(max(span[1] - 1, first), last_idx)
            else:
                first = last = min(max(line - 1, 0), last_idx) if line > 0 else 0
            data = () if raw is None or raw.index is None or token is None else (
                ("entry", str(entry)), ("result", token), ("index", raw.index))
            return Diag(first, last, positions.units(lines[last], self.position_encoding),
                        severity, message, source, raw.code if raw else None, raw.link if raw else None, data)

        contributors = [e for e in self.entries.values()
                        if e.contrib.get(path) and e.versions.get(path) == current]
        prefix = len(contributors) > 1
        out: list[Diag] = []
        for e in contributors:
            for raw in e.contrib[path]:
                text = raw.message if raw.internal else self.display(raw.message)
                message = f"[{e.path.name}] {text}" if prefix else text
                token = e.analysed.token if e.analysed is not None and e.analysed_current else None
                out.append(make(raw.line, raw.severity, message,
                                SRC_INTERNAL if raw.internal else raw.source, raw.span, raw, e.path, token))
        for e in self.entries.values():
            if e.error and e.path == path:
                out.append(make(0, "warning", e.error, SRC_SERVER) if e.error_limit
                           else make(0, "error", e.error, SRC_INTERNAL))
        if doc is not None and doc.replaced:
            out.append(make(0, "warning",
                            f"Some characters in this file are not representable in the "
                            f"configured encoding {self.config.encoding!r} and were replaced "
                            "in the text sent to the compiler.", SRC_SERVER))
        if doc is not None and path not in self.entries and self._ready \
                and path.name.lower() != checkconfig.FILE_NAME \
                and all(e.settled for e in self.entries.values()) \
                and not any(_key(path) in e.reads for e in self.entries.values()):
            out.append(make(0, "information",
                            INFO_UNINCLUDED if path.suffix.lower() == ".crb" else INFO_NOT_ENTRY,
                            SRC_COMPILE))
        return out

    def uri(self, path: Path) -> str:
        if path in self._uris:
            return self._uris[path]
        for parent in path.parents:
            if parent in self.folder_uris:
                relative = quote(path.relative_to(parent).as_posix(), safe="/")
                return self.folder_uris[parent].rstrip("/") + "/" + relative
        return path.as_uri()

    def _publish(self, paths: set[Path]) -> None:
        if self._closed:
            return
        for path in sorted(paths):
            diags = self._compose(path)
            doc = self.docs.get(path)
            uri = doc.uri if doc else self.uri(path)
            old_uri = self._last_uris.get(path)
            if old_uri != uri and old_uri in self._last:
                self.publish(old_uri, None, [])
                self._last.pop(old_uri, None)
            self._last_uris[path] = uri
            version = doc.version if doc else None
            key = (version, tuple(diags))
            last = self._last.get(uri)
            if key == last or (last is None and not diags and doc is None):
                continue
            if diags or doc is not None:
                self._last[uri] = key
            else:
                self._last.pop(uri, None)
            self.publish(uri, version, diags)

    # ---- language features ---------------------------------------------------------------

    def _notify(self) -> None:
        event, self._event = self._event, None
        if event is not None:
            event.set()

    async def configured(self, timeout: float = 15.0) -> None:
        """Returns once the first configuration is applied (bounded: a client that never answers
        the configuration pull must not hang requests)."""
        try:
            await asyncio.wait_for(self._configured.wait(), timeout)
        except TimeoutError:
            pass

    def blobs(self) -> dict[Path, bytes]:
        """Bytes of every open document (what the compiler would read), by path."""
        return {p: d.data for p, d in self.docs.items()}

    def _pick(self, path: Path) -> Analysed | None:
        """The program features use for `path`: its own entry's, else the first entry (sorted by
        path) whose last analysis read the file."""
        if path in self.entries:
            candidates = [self.entries[path]]
        else:
            key = _key(path)
            candidates = [e for _, e in sorted(self.entries.items()) if e.reads.get(key)]
        entry = next((e for e in candidates if e.analysed is not None), None)
        if entry is None:
            for e in candidates:                  # dropped by the cache budget: rebuild
                if e.evicted:
                    e.evicted = False
                    self._request(e, 0.0)
            return None
        entry.used = next(self._use)
        return entry.analysed

    def _fresh(self, path: Path, ana: Analysed | None) -> bool:
        doc = self.docs.get(path)
        return ana is not None and ana.versions.get(path) == (doc.version if doc else None)

    def fresh(self, path: Path, ana: Analysed | None) -> bool:
        """`ana` reflects the newest version of the open document `path` (a closed file: always)."""
        return self._fresh(path, ana)

    def _pending(self) -> bool:
        return any(not e.settled or e.done != e.requested
                   or (e.task is not None and not e.task.done())
                   for e in self.entries.values())

    async def analysed_for(self, path: Path, wait: bool = True,
                           first: bool = False) -> Analysed | None:
        """The program to answer a request about `path`. With `wait`, a program that does not
        reflect the document's newest version is waited for while an analysis is pending: up to
        `featureWaitMs` when an older program exists, up to the analysis timeout when none does.
        With `first` instead (completion), only a missing program is waited for, up to
        `featureWaitMs`; any program, stale or not, is returned at once. After that the older
        program (possibly stale) or None is returned; never an error."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        while True:
            ana = self._pick(path)
            if (not wait and not (first and ana is None)) or self._fresh(path, ana) \
                    or not self._pending() or self._closed:
                return ana
            limit = (self.config.feature_wait_ms / 1000 if first
                     else self.config.analysis_timeout_ms / 1000 + 2 if ana is None
                     else self.config.feature_wait_ms / 1000)
            left = start + limit - loop.time()
            if left <= 0:
                return ana
            if self._event is None:
                self._event = asyncio.Event()
            try:
                await asyncio.wait_for(self._event.wait(), left)
            except TimeoutError:
                return self._pick(path)

    async def load(self, ana: Analysed | None):
        """The `AnalysisResult` (program only) of `ana`, unpickled once in a thread."""
        if ana is None:
            return None
        if ana.result is None and not ana.failed:
            if ana.lock is None:
                ana.lock = asyncio.Lock()
            async with ana.lock:
                if ana.result is None and not ana.failed:
                    ana.result = await asyncio.to_thread(ana.build)
        return ana.result

    # ---- edit features (rename, formatting, quick fixes) --------------------------------

    @staticmethod
    def _entry_pending(entry: Entry) -> bool:
        return (not entry.settled or entry.done != entry.requested
                or (entry.task is not None and not entry.task.done()))

    async def entries_reading(self, path: Path) -> list[Entry]:
        """The entries whose last analysis read `path`, sorted by path, once no entry that `path`
        can affect has an analysis pending (bounded by the analysis timeout plus 2 s; after that
        the entries as they are). An entry whose program the cache budget dropped is re-analysed."""
        loop = asyncio.get_running_loop()
        key = _key(path)
        deadline = loop.time() + self.config.analysis_timeout_ms / 1000 + 2
        while True:
            affected = self._affected(path)
            for entry in affected:
                if entry.analysed is None and entry.evicted and entry.reads.get(key):
                    entry.evicted = False
                    self._request(entry, 0.0)
            if self._closed or not any(self._entry_pending(e) for e in affected):
                break
            left = deadline - loop.time()
            if left <= 0:
                break
            if self._event is None:
                self._event = asyncio.Event()
            try:
                await asyncio.wait_for(self._event.wait(), min(left, 0.25))
            except TimeoutError:
                pass
        return [e for _, e in sorted(self.entries.items()) if e.reads.get(key)]

    def unknown_entries(self) -> list[Entry]:
        """Entries that never completed an analysis (failed, timed out, still waiting): what they
        read is unknown, so an edit that must consider every dependent entry cannot be proven."""
        return [e for _, e in sorted(self.entries.items())
                if not e.reads and (e.error or not e.settled)]

    def current(self, entry: Entry) -> bool:
        """The entry's parsed program is the newest analysis and reflects the version of every
        open document the entry read."""
        ana = entry.analysed
        if ana is None or not entry.analysed_current or entry.error:
            return False
        return all(ana.versions.get(p) == d.version for p, d in self.docs.items()
                   if _key(p) in entry.reads)

    def current_readers(self, path: Path) -> list[Entry]:
        """Entries whose last analysis read `path` and whose program is current (`current`),
        sorted by path; no waiting (read-only features skip the others, LSP-R4a)."""
        key = _key(path)
        return [e for _, e in sorted(self.entries.items()) if e.reads.get(key) and self.current(e)]

    def fresh_contributors(self, path: Path) -> list[Entry]:
        """Entries whose diagnostics for `path` are published now (same rule as `_compose`) and
        whose parsed program is the one that produced them."""
        doc = self.docs.get(path)
        current = doc.version if doc else None
        return [e for _, e in sorted(self.entries.items())
                if e.contrib.get(path) and e.versions.get(path) == current
                and e.analysed is not None and e.analysed_current
                and e.analysed.versions.get(path) == current]

    def help_provider(self) -> _LockedHelp | None:
        directory = self.config.help_directory
        if not directory:
            return None
        path = Path(directory).expanduser()
        if not path.is_absolute():
            path = (self.folders[0] if self.folders else Path.cwd()) / path
        key = str(path)
        if key not in self._help:
            self._help[key] = _LockedHelp(path)
        return self._help[key]

    def _warm_help(self) -> None:
        provider = self.help_provider()
        if provider is None or provider.warming:
            return
        provider.warming = True
        task = asyncio.get_running_loop().create_task(asyncio.to_thread(provider.warm))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ---- shutdown ------------------------------------------------------------------------

    def close(self) -> None:
        self._closed = True
        self._deferred.clear()
        self._notify()
        for entry in self.entries.values():
            entry.removed = True
            if entry.task:
                entry.task.cancel()
        if self._pool is not None:
            pool, self._pool = self._pool, None
            _kill(pool)
