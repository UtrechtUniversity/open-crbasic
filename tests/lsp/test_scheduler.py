"""Scheduler and session regressions of review round 3 (LSP-R3c): the `maxFileBytes` bound on
the files the current analysis reads (A2), worker permits on a cancelled isolated retry (A3), the
client's URI after a close (F5), unrelated requests during a first analysis (F6), the startup
revision (F8). Real stdio server processes through `lspclient.py`, and in-process `Session` tests
where the scheduler is driven with controlled job outcomes."""

import asyncio
import hashlib
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.lsp import session as session_mod
from opencrbasic.lsp.config import Config
from opencrbasic.lsp.server import _git_revision
from opencrbasic.lsp.session import Entry, ServerError, Session, Snapshot
from opencrbasic.lsp.worker import JobResult
from opencrbasic.sources import DiskSource, LimitedSource, OverlaySource

from .lspclient import LspClient
from .test_server import FAST, make_client, project, started  # noqa: F401

pytestmark = pytest.mark.server

INCLUDING = 'Include("CPU:Lib.crb")\nBeginProg\nEndProg\n'
PLAIN = "BeginProg\nEndProg\n"
BIG_LIB = "Public lib_v As Long\n" * 8                      # 168 bytes


def count(client: LspClient, path: Path) -> int:
    return len(client.publishes(path))


# ---- A2: maxFileBytes applies to what the current text reads ------------------------------

def test_removing_the_include_of_an_oversized_library_recovers(make_client, tmp_path):
    root = project(tmp_path, **{"A.CR6": INCLUDING, "Lib.crb": BIG_LIB})
    a = root / "A.CR6"
    client = started(make_client, root, {"maxFileBytes": 100})
    client.open(a, INCLUDING)
    warned = client.wait_diagnostics(a, lambda d: bool(d))
    assert "Lib.crb is 168 bytes" in warned["diagnostics"][0]["message"]
    client.change(a, "Public z\n" + PLAIN, 2)               # the library is unchanged on disk
    clean = client.wait_diagnostics(a, lambda d: not d, after=count(client, a))
    assert clean["version"] == 2
    symbols = client.on_file("textDocument/documentSymbol", a)     # answered from the program
    assert "z" in [s["name"] for s in symbols], symbols
    # including it again is refused again
    client.change(a, INCLUDING, 3)
    again = client.wait_diagnostics(a, lambda d: bool(d), after=count(client, a))
    assert again["version"] == 3 and "maxFileBytes limit of 100" in again["diagnostics"][0]["message"]
    assert client.shutdown() == 0


class _CountingDisk(DiskSource):
    def __init__(self):
        self.read_paths: list[Path] = []

    def read(self, path):
        self.read_paths.append(Path(path))
        return super().read(path)


def test_limited_source_refuses_an_oversized_file_without_reading_it(tmp_path):
    big, small = tmp_path / "Big.crb", tmp_path / "Small.crb"
    big.write_bytes(b"x" * 50)
    small.write_bytes(b"y" * 5)
    disk = _CountingDisk()
    open_doc = tmp_path / "Open.crb"
    source = LimitedSource(OverlaySource(disk, {open_doc: b"z" * 40}), 10)
    assert source.read(big) is None and source.read(small) == b"y" * 5
    assert source.read(open_doc) is None                   # an overlay is measured by its length
    assert source.read(tmp_path / "Missing.crb") is None
    assert source.oversized == {big: 50, open_doc: 40}
    assert big not in disk.read_paths and small in disk.read_paths
    assert source.read(tmp_path) is None and tmp_path not in source.oversized   # a directory


def test_analysis_reports_oversized_files_it_did_not_read(tmp_path):
    (tmp_path / "Lib.crb").write_text(BIG_LIB)
    entry = tmp_path / "A.CR6"
    entry.write_text(INCLUDING)
    result = analyze(AnalysisRequest(entry, "cr6", max_file_bytes=100))
    lib = (tmp_path / "Lib.crb").absolute()
    assert dict(result.dependencies.oversized) == {lib: 168}
    assert result.dependencies.reads[lib] is None
    unlimited = analyze(AnalysisRequest(entry, "cr6"))
    assert dict(unlimited.dependencies.oversized) == {}
    assert unlimited.dependencies.reads[lib] is not None


# ---- A3: isolated retry permits ---------------------------------------------------------

def test_cancelled_isolated_retry_returns_every_permit():
    async def scenario() -> None:
        session = Session(lambda *a: None, workers=2)
        hold = asyncio.Event()
        outcomes: dict[str, ServerError | session_mod._Broken] = {
            "B": session_mod._BROKEN, "C": session_mod._BROKEN, "N": ServerError("normal")}
        calls: list[str] = []

        async def attempt(entry: Entry, snap: Snapshot) -> ServerError | session_mod._Broken:
            calls.append(entry.path.name)
            if entry.path.name == "H":
                await hold.wait()
                return ServerError("held")
            outcome = outcomes[entry.path.name]
            if entry.path.name == "C" and calls.count("C") == 2:
                return ServerError("recovered")
            return outcome

        session._attempt = attempt
        snap = Snapshot((), {}, "cr6", 1.0)
        held = asyncio.create_task(session._run_job(Entry(Path("H")), snap))
        await asyncio.sleep(0.01)
        broken = asyncio.create_task(session._run_job(Entry(Path("B")), snap))
        await asyncio.sleep(0.05)                          # B holds one permit, waits for H's
        assert not broken.done()
        broken.cancel()
        await asyncio.gather(broken, return_exceptions=True)
        hold.set()
        await held
        assert session._semaphore()._value == 2
        recovered = await asyncio.wait_for(session._run_job(Entry(Path("C")), snap), 5)
        assert str(recovered) == "recovered" and calls.count("C") == 2
        normal = await asyncio.wait_for(session._run_job(Entry(Path("N")), snap), 5)
        assert str(normal) == "normal" and session._semaphore()._value == 2

    asyncio.run(scenario())


# ---- F5: the clear after a close goes to the client's URI ---------------------------------

def test_close_clears_diagnostics_under_the_uri_the_client_used(make_client, tmp_path):
    root = tmp_path / "proj"
    sub = root / "d"
    sub.mkdir(parents=True)
    path = sub / "X.CR6"
    text = "Public x\nBeginProg\nx = Missing\nEndProg\n"
    path.write_text(text)
    client = started(make_client, root, {"debounceMs": 0, "entries": ["nothing/**"]},
                     pull_configuration=False)
    uri = path.as_uri().replace("/d/X.CR6", "/d/./X.CR6")    # the same file, another spelling
    client.notify("textDocument/didOpen", {"textDocument": {
        "uri": uri, "languageId": "crbasic", "version": 1, "text": text}})
    client.wait_for(lambda: any(m["uri"] == uri and m["diagnostics"] for m in client.publishes()),
                    30, "diagnostics")
    client.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
    client.wait_for(lambda: client.publishes()[-1]["diagnostics"] == [], 30, "clear")
    last = client.publishes()[-1]
    assert last["uri"] == uri and last.get("version") is None
    assert all(m["uri"] == uri for m in client.publishes())
    assert client.shutdown() == 0


# ---- F6: an unrelated request does not discard a first analysis ---------------------------

def _scenario(tmp_path: Path, act) -> list[str]:
    root = tmp_path / "proj"
    root.mkdir()
    slow, other, lib = root / "Slow.CR6", root / "Other.CR6", root / "Lib.crb"
    slow.write_text(INCLUDING)
    other.write_text(PLAIN)
    lib.write_text("Public lib_v As Long\n")
    runs: list[str] = []

    async def scenario() -> None:
        session = Session(lambda *a: None, workers=2)
        release = asyncio.Event()

        async def job(entry, snap):
            runs.append(entry.path.name)
            if entry.path == slow and runs.count("Slow.CR6") == 1:
                await release.wait()
            reads = ((str(entry.path), hashlib.sha256(entry.path.read_bytes()).hexdigest()),)
            if entry.path == slow:
                reads += ((str(lib), hashlib.sha256(lib.read_bytes()).hexdigest()),)
            return JobResult(True, (), (), reads, ())

        session._run_job = job
        await session.configure(Config(debounce_ms=0, analysis_timeout_ms=60000), [root])
        await asyncio.sleep(0.05)                          # Slow's first job is running
        act(session, root)
        await asyncio.sleep(0.05)
        release.set()
        deadline = time.monotonic() + 30
        while session._pending():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.02)
        assert session.entries[slow].settled and not session.entries[slow].error
        session.close()

    asyncio.run(scenario())
    return runs


def test_unrelated_open_and_edit_keep_the_first_analysis(tmp_path):
    def act(session, root):
        session.did_open(root / "Other.CR6", (root / "Other.CR6").as_uri(), 1, PLAIN)
        session.did_change(root / "Other.CR6", 2, PLAIN + "\n")
    runs = _scenario(tmp_path, act)
    assert runs.count("Slow.CR6") == 1, runs


def test_a_change_to_a_file_the_analysis_read_still_reruns_it(tmp_path):
    def act(session, root):
        session.did_open(root / "Lib.crb", (root / "Lib.crb").as_uri(), 1, "Public lib_w\n")
    runs = _scenario(tmp_path, act)
    assert runs.count("Slow.CR6") == 2, runs


def test_a_change_to_the_entry_itself_still_reruns_it(tmp_path):
    def act(session, root):
        session.did_open(root / "Slow.CR6", (root / "Slow.CR6").as_uri(), 1, PLAIN)
    runs = _scenario(tmp_path, act)
    assert runs.count("Slow.CR6") == 2, runs


# ---- F8: the startup revision ---------------------------------------------------------------

def _repo(path: Path) -> str:
    path.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True,  # noqa: E731
                                    capture_output=True, text=True).stdout.strip()
    run("init", "-q")
    run("-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false", "commit", "-q",
        "--allow-empty", "-m", "x")
    return run("rev-parse", "--short", "HEAD")


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_revision_only_for_a_checkout_of_this_package(tmp_path):
    other = tmp_path / "other"
    _repo(other)
    wheel = other / ".venv" / "lib" / "python3.13" / "site-packages" / "opencrbasic"
    wheel.mkdir(parents=True)
    assert _git_revision(wheel) is None
    for layout in ("src/opencrbasic", "opencrbasic/src/opencrbasic"):
        repo = tmp_path / layout.replace("/", "_")
        rev = _repo(repo)
        (repo / layout).mkdir(parents=True)
        assert _git_revision(repo / layout) == rev
    assert _git_revision(tmp_path / "nothing-here") is None
