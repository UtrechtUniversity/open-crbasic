"""Server state and scheduling, independent of the protocol library.

Owns the open documents (overlays), the configured entries, per-entry debounced analysis in a
spawn-context process pool, and the merge of per-entry results into per-file diagnostics. The
protocol adapter (`server.py`) feeds it events and supplies a `publish` callback. Analysis runs
in worker processes and is awaited, so the event loop never blocks on it.
"""

from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import pickle
import re
import sys
import threading
import traceback
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Config, discover
from .worker import JobResult, run_analysis

SRC_COMPILE = "opencrbasic"
SRC_INTERNAL = "opencrbasic (internal)"
SRC_SERVER = "opencrbasic (server)"
MARKERS = ("Internal error: unable to compile program.", "Internal error: unable to parse program.")
INFO_UNINCLUDED = ("No configured entry program includes this file; diagnostics come from the "
                   "entry programs that include it.")
_LINE_END = re.compile(r"\r\n|\r|\n")


@dataclass(frozen=True)
class Diag:
    line: int            # 0-based LSP line where the range starts; the range starts at character 0
    end_line: int        # 0-based LSP line where the range ends (== line for one physical line)
    end: int             # end character (negotiated position-encoding units) of end_line
    severity: str        # "error" | "warning" | "information"
    message: str
    source: str


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


class Analysed:
    """The parsed program of one entry for the language features. Built from the pickled program
    a worker returned; the unpickling happens lazily in a thread (`Session.load`). The caches hold
    objects the core feature modules derive from the program (declared names, binding index...)."""

    def __init__(self, entry: Path, logger: str, overlays: tuple[tuple[str, bytes], ...],
                 versions: dict[Path, int], blob: bytes):
        self.entry = entry
        self.logger = logger
        self.overlays = overlays
        self.versions = dict(versions)      # open documents (path -> version) the analysis read
        self.blob: bytes | None = blob
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
        return analysis.AnalysisResult(
            analysis.API_VERSION, request, True, None, (), (), (),
            analysis.Dependencies({}, {}), program)

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
    analysed: Analysed | None = None            # newest program that was parsed, for features
    analysed_current: bool = False              # the newest applied analysis produced it


@dataclass
class Snapshot:
    overlays: tuple[tuple[str, bytes], ...]
    versions: dict[Path, int]
    logger: str
    timeout: float


class ServerError(Exception):
    pass


_BROKEN = object()          # a worker pool break, internal to `_run_job`


def _key(path: Path) -> str:
    return str(path).lower()


def _units(text: str, encoding: str) -> int:
    """Length of `text` in the negotiated position encoding's units."""
    if encoding == "utf-8":
        return len(text.encode("utf-8", errors="replace"))
    if encoding == "utf-32":
        return len(text)
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


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

    # ---- logging -------------------------------------------------------------------------

    @staticmethod
    def log(message: str) -> None:
        print(f"opencrbasic-lsp: {message}", file=sys.stderr, flush=True)

    # ---- configuration and entries -------------------------------------------------------

    def _encode(self, text: str) -> tuple[bytes, bool]:
        enc = self.config.encoding
        try:
            return text.encode(enc), False
        except UnicodeEncodeError:
            return text.encode(enc, errors="replace"), True

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
        self.discovered = set(await asyncio.to_thread(
            discover, self.folders, config.entries, config.exclude))
        self._ready = True
        self._configured.set()
        self._sync_entries(force=old.analysis_key() != config.analysis_key())
        if touched:
            self._publish(touched)
        self._warm_help()

    def _wanted_entries(self) -> set[Path]:
        suffixes = self.config.entry_suffixes()
        return set(self.discovered) | {p for p in self.docs if p.suffix.lower() in suffixes}

    def _sync_entries(self, force: bool = False) -> None:
        wanted = self._wanted_entries()
        touched: set[Path] = set()
        for path in [p for p in self.entries if p not in wanted]:
            entry = self.entries.pop(path)
            entry.removed = True
            if entry.task:
                entry.task.cancel()
            touched |= set(entry.contrib) | {path}
            self._notify()
        for path in sorted(wanted):
            entry = self.entries.get(path)
            if entry is None:
                entry = self.entries[path] = Entry(path)
                self._request(entry, 0.0)
            elif force:
                self._request(entry, 0.0)
        self._publish(touched | self._open_crb_paths())

    # ---- document events -----------------------------------------------------------------

    def did_open(self, path: Path, uri: str, version: int, text: str) -> None:
        doc = Doc(uri, version, text)
        doc.data, doc.replaced = self._encode(text)
        self.docs[path] = doc
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
                                 entry.analysed.versions if entry.analysed_current else {}):
                    if doc is None:
                        versions.pop(path, None)
                    else:
                        versions[path] = doc.version
                self._notify()
                continue
            self._request(entry, delay)

    # ---- scheduling ----------------------------------------------------------------------

    def _request(self, entry: Entry, delay: float) -> None:
        loop = asyncio.get_running_loop()
        entry.requested += 1
        entry.not_before = loop.time() + delay
        if entry.task is None or entry.task.done():
            entry.task = loop.create_task(self._entry_loop(entry))

    def _snapshot(self) -> Snapshot:
        return Snapshot(tuple((str(p), d.data) for p, d in self.docs.items()),
                        {p: d.version for p, d in self.docs.items()},
                        self.config.logger, self.config.analysis_timeout_ms / 1000)

    async def _entry_loop(self, entry: Entry) -> None:
        loop = asyncio.get_running_loop()
        try:
            while not entry.removed and entry.done != entry.requested:
                while (wait := entry.not_before - loop.time()) > 0:
                    await asyncio.sleep(wait)
                if entry.removed:
                    break
                target = entry.requested
                snap = self._snapshot()
                outcome = await self._run_job(entry, snap)
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

    async def _run_job(self, entry: Entry, snap: Snapshot) -> JobResult | ServerError:
        """One analysis in the pool. When the pool breaks (a crash, or a kill after another
        entry's timeout) the pool is recreated and the job is retried once with the pool to itself,
        so a crash is only ever blamed on the job that was running alone."""
        async with self._semaphore():
            outcome = await self._attempt(entry, snap)
        if outcome is not _BROKEN:
            return outcome
        async with self._solo_lock():
            slots = self._semaphore()
            for _ in range(self.workers):
                await slots.acquire()
            try:
                outcome = await self._attempt(entry, snap)
            finally:
                for _ in range(self.workers):
                    slots.release()
        if outcome is _BROKEN:
            return ServerError(f"The analysis worker process terminated unexpectedly while "
                               f"analysing {entry.path.name}; the worker pool was restarted.")
        return outcome

    def _solo_lock(self) -> asyncio.Lock:
        if self._solo is None:
            self._solo = asyncio.Lock()
        return self._solo

    async def _attempt(self, entry: Entry, snap: Snapshot):
        if self._closed:
            return ServerError("server is shutting down")
        loop = asyncio.get_running_loop()
        pool, gen = self._get_pool()
        try:
            future = loop.run_in_executor(
                pool, run_analysis, str(entry.path), snap.logger, snap.overlays)
            return await asyncio.wait_for(future, snap.timeout)
        except asyncio.TimeoutError:
            self._reset_pool(gen)
            return ServerError(f"Analysis of {entry.path.name} timed out after "
                               f"{round(snap.timeout * 1000)} ms; the analysis worker was "
                               "restarted.")
        except BrokenProcessPool:
            self._reset_pool(gen)
            return _BROKEN
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ServerError(f"Analysis of {entry.path.name} failed in the worker: "
                               f"{type(exc).__name__}: {exc}")

    # ---- applying results ----------------------------------------------------------------

    def _apply(self, entry: Entry, snap: Snapshot, outcome: JobResult | ServerError) -> None:
        old_paths = set(entry.contrib)
        installed = False
        if isinstance(outcome, ServerError):
            entry.error = str(outcome)
            entry.contrib = {}
            entry.analysed_current = False
        else:
            reads = {_key(Path(p)): h for p, h in outcome.reads}
            stale = any((self.docs[p].version if p in self.docs else None) != snap.versions.get(p)
                        for p in set(self.docs) | set(snap.versions)
                        if _key(p) in reads or p == entry.path)
            if stale:
                self._request(entry, 0.0)
                return
            entry.error = None
            entry.reads = reads
            entry.dirs = frozenset(_key(Path(d)) for d in outcome.listings)
            entry.contrib = self._contributions(entry, outcome)
            entry.analysed_current = outcome.program is not None
            if entry.analysed_current:
                entry.analysed = Analysed(entry.path, snap.logger, snap.overlays, snap.versions,
                                          outcome.program)
                installed = True
        entry.versions = dict(snap.versions)
        entry.settled = True
        self._publish(old_paths | set(entry.contrib) | {entry.path} | self._open_crb_paths())
        self._notify()
        if installed and self.on_analysed is not None:
            self.on_analysed()

    @staticmethod
    def _contributions(entry: Entry, result: JobResult) -> dict[Path, list[Raw]]:
        out: dict[Path, list[Raw]] = {}
        failures = "\n".join(result.failures)
        attached = False
        for severity, message, line, path, file, span in result.diagnostics:
            target = Path(path) if path else entry.path
            if not path:
                message = f"{file}:{line}: {message}" if file else message
                line, span = 0, None
            internal = message in MARKERS
            if internal and failures and not attached:
                message, attached = f"{message}\n{failures}", True
            out.setdefault(target, []).append(Raw(severity, message, line, internal, span))
        if failures and not attached:
            out.setdefault(entry.path, []).append(
                Raw("error", f"Internal error in the compiler:\n{failures}", 0, True))
        return out

    # ---- publishing ----------------------------------------------------------------------

    def _open_crb_paths(self) -> set[Path]:
        return {p for p in self.docs if p.suffix.lower() == ".crb"}

    def _text_of(self, path: Path) -> str:
        doc = self.docs.get(path)
        if doc is not None:
            return doc.text
        try:
            return path.read_bytes().decode(self.config.encoding, errors="replace")
        except (OSError, LookupError):
            return ""

    def _compose(self, path: Path) -> list[Diag]:
        text = self._text_of(path)
        lines = _LINE_END.split(text)
        doc = self.docs.get(path)
        current = doc.version if doc else None

        last_idx = len(lines) - 1

        def make(line: int, severity: str, message: str, source: str,
                 span: tuple[int, int] | None = None) -> Diag:
            if span is not None:
                first = min(max(span[0] - 1, 0), last_idx)
                last = min(max(span[1] - 1, first), last_idx)
            else:
                first = last = min(max(line - 1, 0), last_idx) if line > 0 else 0
            return Diag(first, last, _units(lines[last], self.position_encoding), severity, message, source)

        contributors = [e for e in self.entries.values()
                        if e.contrib.get(path) and e.versions.get(path) == current]
        prefix = len(contributors) > 1
        out: list[Diag] = []
        for e in contributors:
            for raw in e.contrib[path]:
                message = f"[{e.path.name}] {raw.message}" if prefix else raw.message
                out.append(make(raw.line, raw.severity, message,
                                SRC_INTERNAL if raw.internal else SRC_COMPILE, raw.span))
        for e in self.entries.values():
            if e.error and e.path == path:
                out.append(make(0, "error", e.error, SRC_INTERNAL))
        if doc is not None and doc.replaced:
            out.append(make(0, "warning",
                            f"Some characters in this file are not representable in the "
                            f"configured encoding {self.config.encoding!r} and were replaced "
                            "in the text sent to the compiler.", SRC_SERVER))
        if doc is not None and path.suffix.lower() == ".crb" and self._ready \
                and all(e.settled for e in self.entries.values()) \
                and not any(_key(path) in e.reads for e in self.entries.values()):
            out.append(make(0, "information", INFO_UNINCLUDED, SRC_COMPILE))
        return out

    def _publish(self, paths: set[Path]) -> None:
        if self._closed:
            return
        for path in sorted(paths):
            diags = self._compose(path)
            doc = self.docs.get(path)
            uri = doc.uri if doc else path.as_uri()
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
        except asyncio.TimeoutError:
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
        return next((e.analysed for e in candidates if e.analysed is not None), None)

    def _fresh(self, path: Path, ana: Analysed | None) -> bool:
        doc = self.docs.get(path)
        return ana is not None and ana.versions.get(path) == (doc.version if doc else None)

    def _pending(self) -> bool:
        return any(not e.settled or (e.task is not None and not e.task.done())
                   for e in self.entries.values())

    async def analysed_for(self, path: Path, wait: bool = True) -> Analysed | None:
        """The program to answer a request about `path`. With `wait`, a program that does not
        reflect the document's newest version is waited for while an analysis is pending: up to
        `featureWaitMs` when an older program exists, up to the analysis timeout when none does.
        After that the older program (possibly stale) or None is returned; never an error."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        while True:
            ana = self._pick(path)
            if not wait or self._fresh(path, ana) or not self._pending() or self._closed:
                return ana
            limit = (self.config.analysis_timeout_ms / 1000 + 2 if ana is None
                     else self.config.feature_wait_ms / 1000)
            left = start + limit - loop.time()
            if left <= 0:
                return ana
            if self._event is None:
                self._event = asyncio.Event()
            try:
                await asyncio.wait_for(self._event.wait(), left)
            except asyncio.TimeoutError:
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
        self._notify()
        for entry in self.entries.values():
            entry.removed = True
            if entry.task:
                entry.task.cancel()
        if self._pool is not None:
            pool, self._pool = self._pool, None
            _kill(pool)
