"""Workspace correctness and resilience (LSP-06a): watched files, workspace folders, conditional
`IncludeSection` contexts, bounds, cancellation and an edit-sequence replay. Real stdio server
processes through `lspclient.py`; in-process `Session` tests where a bound cannot be reached
through the protocol cheaply."""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.lsp import session as session_mod
from opencrbasic.lsp.config import Config, parse_config, watch_pattern
from opencrbasic.lsp.session import Session

from .lspclient import LspClient

pytestmark = pytest.mark.server
from .test_server import (BAD_LIB, FAST, LIB, SMALL, bad, make_client, project,  # noqa: F401
                          small_project, started, summary)

CREATED, CHANGED, DELETED = 1, 2, 3
MISSING = "Cannot open include file"


def count(client: LspClient, path: Path) -> int:
    return len(client.publishes(path))


def messages(params: dict) -> list[str]:
    return [d["message"] for d in params["diagnostics"]]


def has(text: str):
    return lambda diags: any(text in d["message"] for d in diags)


def server_messages(client: LspClient) -> list[str]:
    return [m["params"]["message"] for m in client.messages
            if m.get("method") == "window/logMessage" and m["params"]["type"] == 2]


# ---- watched files ------------------------------------------------------------------------

def test_watch_pattern_covers_program_and_library_extensions():
    pattern = watch_pattern()
    assert pattern.startswith("**/*.{") and pattern.endswith("}")
    names = set(pattern[len("**/*.{"):-1].split(","))
    assert {"crb", "CRB", "cr6", "CR6"} <= names
    assert {ext[1:] for ext in profiles.EXTENSIONS} <= names


def test_registers_watched_files_when_the_client_supports_it(make_client, tmp_path):
    client = make_client()
    client.initialize(small_project(tmp_path), FAST, watched_files=True)
    (request,) = client.wait_for(lambda: client.server_requests("client/registerCapability"),
                                 30, "client/registerCapability")
    (registration,) = request["params"]["registrations"]
    assert registration["method"] == "workspace/didChangeWatchedFiles"
    watchers = registration["registerOptions"]["watchers"]
    assert [w["globPattern"] for w in watchers] == [watch_pattern(), "**/*"]
    assert "kind" not in watchers[0]
    assert watchers[1]["kind"] == 4                        # WatchKind.Delete only (H-07)
    assert client.shutdown() == 0


def test_no_registration_without_client_support(make_client, tmp_path):
    client = make_client()
    client.initialize(small_project(tmp_path), FAST)
    client.wait_for(lambda: "watched-file registration" in client.stderr_text, 30, "stderr note")
    assert client.server_requests("client/registerCapability") == []
    assert client.shutdown() == 0


def test_created_include_resolves_missing_and_deleted_include_reports_it(make_client, tmp_path):
    root = project(tmp_path, **{"Small.CR6": SMALL})
    small, lib = root / "Small.CR6", root / "Lib.crb"
    client = started(make_client, root)
    first = client.wait_diagnostics(small, lambda d: bool(d))
    assert MISSING in messages(first)[0] and first["diagnostics"][0]["severity"] == 2

    lib.write_text(LIB)                                    # created on disk, not open
    client.watched((lib, CREATED))
    client.wait_diagnostics(small, lambda d: not d, after=count(client, small))

    lib.write_text(BAD_LIB)                                # changed on disk
    client.watched((lib, CHANGED))
    broken = client.wait_diagnostics(lib, has("already declared"))
    assert broken.get("version") is None

    lib.unlink()                                           # deleted: missing-include warning
    client.watched((lib, DELETED))
    client.wait_diagnostics(small, has(MISSING), after=count(client, small) - 1)
    cleared = client.wait_diagnostics(lib, lambda d: not d, after=count(client, lib) - 1)
    assert cleared["diagnostics"] == []
    assert client.shutdown() == 0


def test_deleted_folder_reported_only_as_its_uri_drops_the_entries_below_it(make_client, tmp_path):
    root = project(tmp_path, **{"Lib.crb": LIB})
    gone = root / "gone"
    gone.mkdir()
    entry = gone / "Gone.CR6"
    entry.write_text(bad(SMALL))
    (gone / "Lib.crb").write_text(LIB)
    client = started(make_client, root)
    client.wait_diagnostics(entry, has("y"))
    for child in gone.iterdir():
        child.unlink()
    gone.rmdir()
    client.watched((gone, DELETED))                        # what VS Code sends: no per-file events
    cleared = client.wait_diagnostics(entry, lambda d: not d, after=count(client, entry))
    assert cleared["diagnostics"] == []
    assert client.shutdown() == 0


def test_unrelated_deleted_file_does_not_rediscover(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Lib.crb").write_text(LIB)
    (root / "notes.txt").write_text("x")

    async def scenario() -> None:
        session = Session(lambda *_: None, workers=1)
        await session.configure(Config(debounce_ms=0, analysis_timeout_ms=60000), [root])
        await settle(session)
        calls = []
        original = session._rediscover

        async def counted():
            calls.append(1)
            await original()
        session._rediscover = counted
        (root / "notes.txt").unlink()
        await session.files_changed([(root / "notes.txt", "deleted")])
        assert calls == []
        session.close()

    asyncio.run(scenario())


def test_rename_is_delete_plus_create(make_client, tmp_path):
    root = small_project(tmp_path, entries=("Old.CR6",))
    old, new = root / "Old.CR6", root / "New.CR6"
    old.write_text(bad(SMALL))
    client = started(make_client, root)
    client.wait_diagnostics(old, lambda d: bool(d))
    old.rename(new)
    client.watched((old, DELETED), (new, CREATED))
    client.wait_diagnostics(old, lambda d: not d, after=1)           # the old entry is gone
    created = client.wait_diagnostics(new, has("y"))                # the new one is an entry
    assert created.get("version") is None and len(created["diagnostics"]) == 1
    new.rename(old)
    client.watched((new, DELETED), (old, CREATED))
    client.wait_diagnostics(new, lambda d: not d, after=count(client, new) - 1)
    client.wait_diagnostics(old, has("y"), after=count(client, old) - 1)
    assert client.shutdown() == 0


def test_disk_changes_of_an_open_document_are_ignored_until_it_closes(make_client, tmp_path):
    root = small_project(tmp_path, entries=("A.CR6", "B.CR6"))
    lib = root / "Lib.crb"
    client = started(make_client, root)
    client.open(lib, LIB)
    client.wait_diagnostics(lib, lambda d: not d)
    before = count(client, lib)
    lib.write_text(BAD_LIB)                                # the editor buffer still says LIB
    client.watched((lib, CHANGED))
    other = root / "B.CR6"
    other.write_text(bad(SMALL))
    client.watched((other, CHANGED))
    client.wait_diagnostics(other, has("y"))               # a later event was handled
    assert count(client, lib) == before
    client.close(lib)                                      # now the disk text is in effect
    client.wait_diagnostics(lib, has("already declared"), after=before)
    assert client.shutdown() == 0


def test_directory_events_add_and_remove_the_entries_below_them(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "Lib.crb").write_text(LIB)
    published: dict[str, list] = {}

    async def scenario() -> None:
        session = Session(lambda uri, version, diags: published.setdefault(uri, []).append(diags),
                          workers=1)
        await session.configure(Config(debounce_ms=0, analysis_timeout_ms=60000), [root])
        await settle(session)
        sub = root / "sub"
        sub.mkdir()
        (sub / "B.CR6").write_text(bad(SMALL))
        (sub / "Lib.crb").write_text(LIB)
        await session.files_changed([(sub, "created")])     # one event for the whole directory
        await settle(session)
        assert sub / "B.CR6" in session.entries
        assert len(published[(sub / "B.CR6").as_uri()][-1]) == 1     # its undeclared `y`
        (sub / "B.CR6").unlink()
        (sub / "Lib.crb").unlink()
        sub.rmdir()
        await session.files_changed([(sub, "deleted")])
        await settle(session)
        assert sub / "B.CR6" not in session.entries
        assert published[(sub / "B.CR6").as_uri()][-1] == []
        session.close()

    asyncio.run(scenario())


async def settle(session: Session, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while session._pending() or session._deferred:
        assert time.monotonic() < deadline, "analysis did not settle"
        await asyncio.sleep(0.05)


# ---- workspace folders --------------------------------------------------------------------

def test_workspace_folders_update_entry_discovery(make_client, tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    for folder, name in ((one, "A.CR6"), (two, "B.CR6")):
        folder.mkdir()
        (folder / name).write_text(bad(SMALL))
        (folder / "Lib.crb").write_text(LIB)
    a, b = one / "A.CR6", two / "B.CR6"
    client = started(make_client, one)
    client.wait_diagnostics(a, has("y"))
    assert client.publishes(b) == []
    client.change_folders(added=[two])
    client.wait_diagnostics(b, has("y"))
    client.change_folders(removed=[one])
    cleared = client.wait_diagnostics(a, lambda d: not d, after=1)
    assert cleared["diagnostics"] == []
    client.change_folders(removed=[two])
    client.wait_diagnostics(b, lambda d: not d, after=1)
    assert client.shutdown() == 0


# ---- conditional IncludeSection contexts --------------------------------------------------

SECTIONED = ('#If Section = "One" Then\nPublic p1 As Long\nPublic p1 As Long\n#EndIf\n'
             '#If Section = "Two" Then\nPublic p2 As Long\nPublic p2 As Long\n#EndIf\n')


def test_two_sections_keep_one_result_per_entry(make_client, tmp_path):
    prog = 'IncludeSection("{}", "CPU:Lib.crb")\nBeginProg\nEndProg\n'
    root = project(tmp_path, **{"One.CR6": prog.format("One"), "Two.CR6": prog.format("Two"),
                                "Lib.crb": SECTIONED})
    lib = root / "Lib.crb"
    client = started(make_client, root)
    client.open(lib, SECTIONED)
    both = client.wait_diagnostics(lib, lambda d: len(d) == 2 and all(
        x["message"].startswith("[") for x in d))
    assert sorted(messages(both)) == ["[One.CR6] p1 already declared as a variable.",
                                      "[Two.CR6] p2 already declared as a variable."]
    # the same physical line in both contexts: the line of the second declaration of each name
    assert sorted(d["range"]["start"]["line"] for d in both["diagnostics"]) == [2, 6]
    # fixing section One leaves the Two context's result alone, and its message is not prefixed
    client.change(lib, SECTIONED.replace("Public p1 As Long\nPublic p1 As Long",
                                         "Public p1 As Long"), 2)
    one_left = client.wait_diagnostics(lib, lambda d: len(d) == 1 and d[0]["message"].startswith(
        "p2"), after=count(client, lib) - 1)
    assert one_left["version"] == 2
    client.change(lib, SECTIONED.replace("Public p1 As Long\nPublic p1 As Long", "Public p1 As Long")
                  .replace("Public p2 As Long\nPublic p2 As Long", "Public p2 As Long"), 3)
    client.wait_diagnostics(lib, lambda d: not d, after=count(client, lib) - 1)
    assert client.shutdown() == 0


# ---- bounds -------------------------------------------------------------------------------

def test_oversized_include_is_a_server_warning_not_a_compile_error(make_client, tmp_path):
    root = small_project(tmp_path, lib=LIB + "\n" * 400)
    small = root / "Small.CR6"
    client = started(make_client, root, {"maxFileBytes": 300})
    params = client.wait_diagnostics(small, lambda d: bool(d))
    (diag,) = params["diagnostics"]
    assert diag["severity"] == 2 and diag["source"] == "opencrbasic (server)"
    assert "maxFileBytes limit of 300" in diag["message"] and "Lib.crb" in diag["message"]
    assert diag["range"]["start"] == {"line": 0, "character": 0}
    assert any("Lib.crb" in m and "maxFileBytes" in m for m in server_messages(client))
    # a larger limit analyses the program normally
    client.settings = {**FAST, "maxFileBytes": 100000}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_diagnostics(small, lambda d: not d, after=count(client, small))
    # the entry file itself over the limit is the same warning (no analysis, no language error)
    client.settings = {**FAST, "maxFileBytes": 100}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    again = client.wait_diagnostics(small, lambda d: bool(d), after=count(client, small))
    assert again["diagnostics"][0]["source"] == "opencrbasic (server)"
    assert client.shutdown() == 0


def test_open_document_over_the_limit_is_not_analysed(make_client, tmp_path):
    root = small_project(tmp_path)
    small = root / "Small.CR6"
    client = started(make_client, root, {"maxFileBytes": 400})
    client.open(small, SMALL)
    client.wait_diagnostics(small, lambda d: not d)
    client.change(small, SMALL + "\n" * 600, 2)
    params = client.wait_diagnostics(small, lambda d: bool(d), after=count(client, small))
    assert params["version"] == 2 and params["diagnostics"][0]["source"] == "opencrbasic (server)"
    client.change(small, SMALL, 3)
    client.wait_diagnostics(small, lambda d: not d, after=count(client, small))
    assert client.shutdown() == 0


def test_queued_jobs_are_bounded_and_all_entries_are_still_analysed(make_client, tmp_path):
    names = ("A.CR6", "B.CR6", "C.CR6", "D.CR6")
    root = project(tmp_path, **{n: bad(SMALL) for n in names}, **{"Lib.crb": LIB})
    client = started(make_client, root, {"maxQueuedJobs": 1})
    for name in names:
        client.wait_diagnostics(root / name, has("y"))
    assert len([m for m in server_messages(client) if "maxQueuedJobs=1" in m]) == 1
    # a later change still schedules work after the queue drained
    (root / "Lib.crb").write_text(BAD_LIB)
    client.watched((root / "Lib.crb", CHANGED))
    client.wait_diagnostics(root / "Lib.crb", has("already declared"))
    assert client.shutdown() == 0


def test_cache_budget_drops_programs_and_rebuilds_them_on_demand(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "MIB", 1)             # maxCacheMb=1 now means one byte
    root = tmp_path / "proj"
    root.mkdir()
    for name in ("A.CR6", "B.CR6", "C.CR6"):
        (root / name).write_text(SMALL)
    (root / "Lib.crb").write_text(LIB)
    published: list[tuple] = []

    async def scenario() -> None:
        session = Session(lambda uri, version, diags: published.append((uri, diags)), workers=1)
        await session.configure(Config(debounce_ms=0, analysis_timeout_ms=60000,
                                       max_cache_mb=1), [root])
        await settle(session)
        held = [e for e in session.entries.values() if e.analysed is not None]
        assert len(held) == 1                              # only the newest program is kept
        assert sum(e.evicted for e in session.entries.values()) == 2
        for name in ("A.CR6", "B.CR6", "C.CR6"):           # every entry still answers features
            ana = await session.analysed_for(root / name)
            assert ana is not None and ana.entry == root / name
            assert (await session.load(ana)) is not None
        assert len([e for e in session.entries.values() if e.analysed is not None]) == 1
        assert not any(diags for _, diags in published)    # eviction publishes nothing
        session.close()

    asyncio.run(scenario())


def test_self_including_entry_is_reported_and_the_server_stays_usable(make_client, tmp_path):
    cyc = 'IncludeSection("A", "CPU:Cycle.CR6")\nBeginProg\nEndProg\n'
    root = project(tmp_path, **{"Cycle.CR6": cyc, "Ok.CR6": bad(SMALL), "Lib.crb": LIB})
    client = started(make_client, root)
    ok, cycle = root / "Ok.CR6", root / "Cycle.CR6"
    params = client.wait_diagnostics(cycle, lambda d: bool(d))
    assert all(d["source"] == "opencrbasic (internal)" for d in params["diagnostics"])
    client.wait_diagnostics(ok, has("y"))
    client.open(ok, bad(SMALL))
    client.change(ok, SMALL, 2)
    client.wait_diagnostics(ok, lambda d: not d, after=count(client, ok) - 1)
    assert client.shutdown() == 0


def test_config_limits_parse():
    cfg, warnings = parse_config({"maxFileBytes": 10, "maxQueuedJobs": 3, "maxCacheMb": 5})
    assert (cfg.max_file_bytes, cfg.max_queued_jobs, cfg.max_cache_mb) == (10, 3, 5)
    assert warnings == []
    kept, warnings = parse_config({"maxFileBytes": 0, "maxQueuedJobs": "x", "maxCacheMb": -1}, cfg)
    assert kept == cfg and len(warnings) == 3
    defaults = Config()
    assert (defaults.max_file_bytes, defaults.max_queued_jobs, defaults.max_cache_mb) == (
        2 * 1024 * 1024, 64, 256)


# ---- cancellation -------------------------------------------------------------------------

def test_cancel_request_answers_request_cancelled_and_the_server_goes_on(make_client, tmp_path):
    root = project(tmp_path, **{"Slow.CR6": SMALL, "Lib.crb": LIB})
    client = started(make_client, root, {"analysisTimeoutMs": 60000},
                     env={"OPENCRBASIC_LSP_TEST_SLEEP": "Slow:6"})
    slow = root / "Slow.CR6"
    client.open(slow, SMALL)
    rid = client.send_request("textDocument/documentSymbol",
                              {"textDocument": {"uri": slow.as_uri()}})
    time.sleep(0.3)                                        # the request is waiting for a program
    t0 = time.monotonic()
    client.cancel(rid)
    reply = client.response(rid, 5, "cancelled documentSymbol")
    assert reply["error"]["code"] == -32800, reply         # RequestCancelled
    assert time.monotonic() - t0 < 4                       # not after the 6 s analysis
    # a text-based request is answered at once afterwards
    folds = client.on_file("textDocument/foldingRange", slow, timeout=10)
    assert isinstance(folds, list)
    # cancelling a request that already finished is ignored
    done = client.send_request("textDocument/foldingRange", {"textDocument": {"uri": slow.as_uri()}})
    client.response(done, 10, "foldingRange")
    client.cancel(done)
    assert isinstance(client.on_file("textDocument/foldingRange", slow, timeout=10), list)
    assert "Traceback" not in client.stderr_text
    client.kill()


# ---- edit-sequence replay -----------------------------------------------------------------

ENTRY = """Public x As Long
Include("CPU:Lib.crb")
Include("CPU:Extra.crb")
DataTable(T,True,-1)
  DataInterval(0,1,Min,10)
  Sample(1,x,Long)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    x = x + 1
    CallTable T
  NextScan
EndProg
"""
ENTRY_VARIANTS = [
    ENTRY,
    ENTRY.replace("x = x + 1", "x = nope + 1"),
    ENTRY.replace("Scan(1,Sec,0,0)", "Scan(1,Sec,0"),                 # unbalanced
    ENTRY.replace("EndProg\n", ""),                                      # truncated
    ENTRY + "Public Public\n",
    "\n",
    ENTRY.replace('"CPU:Extra.crb"', '"CPU:Extra.crb'),                  # unterminated string
    ENTRY.replace("x = x + 1", "x = x + 1 +\n    2"),
    ENTRY.replace("Sample(1,x,Long)", "Sample(1,y,Long)"),
]
LIB_VARIANTS = [LIB, BAD_LIB, "Public lib_v As Long\nPublic q As\n", "", "Const = 3\n"]
EXTRA = "Public extra_v As Long\n"
EXTRA_BAD = "Public extra_v As Long\nPublic extra_v As Long\n"


def final_summary(client: LspClient, path: Path) -> list[tuple]:
    latest = client.latest(path)
    return sorted(summary(latest)) if latest else []


def test_edit_sequence_ends_equal_to_a_fresh_analysis(make_client, tmp_path):
    root = project(tmp_path, **{"Main.CR6": ENTRY, "Lib.crb": LIB})
    main, lib, extra = root / "Main.CR6", root / "Lib.crb", root / "Extra.crb"
    client = started(make_client, root, {"entries": ["Main.CR6"]})
    client.open(main, ENTRY)
    client.open(lib, LIB)
    versions = {main: 1, lib: 1}
    rng = random.Random(20261011)
    extra_state = "absent"
    for step in range(30):
        pick = rng.randrange(4)
        if pick == 0:
            versions[main] += 1
            client.change(main, rng.choice(ENTRY_VARIANTS), versions[main])
        elif pick == 1:
            versions[lib] += 1
            client.change(lib, rng.choice(LIB_VARIANTS), versions[lib])
        elif pick == 2:                                   # the closed include appears/changes/goes
            if extra_state == "absent":
                extra.write_text(EXTRA)
                client.watched((extra, CREATED))
                extra_state = "good"
            elif extra_state == "good" and rng.random() < 0.5:
                extra.write_text(EXTRA_BAD)
                client.watched((extra, CHANGED))
                extra_state = "bad"
            else:
                extra.unlink()
                client.watched((extra, DELETED))
                extra_state = "absent"
        else:
            client.save(main)
        if step % 7 == 0:
            time.sleep(0.12)                              # let some analyses finish mid-sequence
    # a defined final state: the sequence ends on text that has diagnostics in several files
    versions[main] += 1
    client.change(main, ENTRY, versions[main])
    versions[lib] += 1
    client.change(lib, BAD_LIB, versions[lib])
    if extra_state == "absent":
        extra.write_text(EXTRA_BAD)
        client.watched((extra, CREATED))
    else:
        extra.write_text(EXTRA_BAD)
        client.watched((extra, CHANGED))
    main.write_text(ENTRY)                                # what the editor saved
    lib.write_text(BAD_LIB)
    client.save(main)
    client.save(lib)
    client.close(main)
    client.close(lib)

    fresh_client = make_client()
    fresh_client.initialize(root, {**FAST, "entries": ["Main.CR6"]})
    fresh = {p: fresh_client.wait_diagnostics(p, lambda d: bool(d))
             for p in (lib, extra)}
    expected = {p: sorted(summary(params)) for p, params in fresh.items()}
    assert any("already declared" in m for _, _, _, m in expected[lib])
    assert any("already declared" in m for _, _, _, m in expected[extra])
    for path in (lib, extra):
        client.wait_for(lambda p=path: final_summary(client, p) == expected[p], 60,
                        f"replayed diagnostics of {path.name} to equal a fresh analysis")
    # a request that waits for pending work, then everything still equal and clean for Main
    client.on_file("textDocument/documentSymbol", main, timeout=60)
    for path in (lib, extra):
        assert final_summary(client, path) == expected[path]
    assert final_summary(client, main) == []
    # published versions never ran backwards and never exceeded what the client sent
    for path in (main, lib):
        seen = [p["version"] for p in client.publishes(path) if p.get("version") is not None]
        assert seen == sorted(seen) and (not seen or seen[-1] <= versions[path])
    assert client.shutdown() == 0
