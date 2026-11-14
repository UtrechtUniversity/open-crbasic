"""Portable regressions for the Windows host findings; Win32 calls are simulated explicitly."""

from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, call

import lsp_install_check
import pytest

from opencrbasic.lsp import server
from opencrbasic.lsp.session import Diag, Session

from . import lspclient
from .test_server import FAST, make_client  # noqa: F401


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_client_kill_includes_pool_workers(monkeypatch, platform):
    proc = Mock(pid=1234)
    proc.poll.return_value = None
    client = object.__new__(lspclient.LspClient)
    client.proc = proc
    run = Mock()
    killpg = Mock(side_effect=lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError) if sig == 0 else None)
    monkeypatch.setattr(lspclient.sys, "platform", platform)
    monkeypatch.setattr(lspclient.subprocess, "run", run)
    monkeypatch.setattr(lspclient.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(lspclient.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(lspclient.signal, "SIGTERM", 15, raising=False)
    client.kill()
    if platform == "win32":
        run.assert_called_once_with(["taskkill", "/T", "/F", "/PID", "1234"], capture_output=True)
        killpg.assert_not_called()
    else:
        # terminate, wait for the group to empty (so the resource tracker can clean up), then kill
        assert killpg.call_args_list == [call(1234, 15), call(1234, 0), call(1234, 9)]
        run.assert_not_called()
    proc.kill.assert_called_once_with()
    proc.wait.assert_called_with()


@pytest.mark.parametrize(
    "platform, directory, suffix", [("win32", "Scripts", ".exe"), ("darwin", "bin", ""), ("linux", "bin", "")]
)
@pytest.mark.parametrize("name", ["python", "crbc", "crbc-lsp"])
def test_venv_executable(monkeypatch, tmp_path, platform, directory, suffix, name):
    monkeypatch.setattr(lsp_install_check.sys, "platform", platform)
    assert lsp_install_check.venv_executable(tmp_path, name) == tmp_path / directory / (name + suffix)


@pytest.mark.parametrize("root_uri", ["file:///C:/Users/Test%20%C3%89", "file:///c%3A/Users/Test%20%C3%89/"])
def test_unopened_windows_file_uri_uses_client_root(root_uri):
    # PureWindowsPath exercises Windows drive matching on any host, without pretending
    # the Windows filesystem exists. Publishing is independent of filesystem access.
    root = cast(Path, PureWindowsPath("C:/Users/Test É"))
    path = cast(Path, PureWindowsPath("c:/Users/Test É/sub/A #é.CR6"))
    published = []
    session = Session(lambda *args: published.append(args))
    session.folder_uris[root] = root_uri
    session._compose = lambda path: [Diag(0, 0, 1, "error", "bad", "opencrbasic")]
    session._publish({path})
    assert published[0][0] == root_uri.rstrip("/") + "/sub/A%20%23%C3%A9.CR6"
    session._uris[path] = "file:///C:/Users/Test%20%C3%89/sub/./A%20%23%C3%A9.CR6"
    assert session.uri(path) == session._uris[path]  # opened spelling still wins


@pytest.mark.parametrize("overlapping", [True, False])
def test_unopened_diagnostics_clear_previous_folder_spelling(overlapping):
    root = cast(Path, PureWindowsPath("C:/x"))
    path = root / "sub/Bad.CR6"
    published = []
    session = Session(lambda *args: published.append(args))
    session.folder_uris[root] = "file:///C:/x"
    diags = [Diag(0, 0, 1, "error", "bad", "opencrbasic")]
    session._compose = lambda path: list(diags)
    session._publish({path})
    session._publish({path})
    assert published == [("file:///C:/x/sub/Bad.CR6", None, diags)]
    session.folder_uris[root / "sub" if overlapping else root] = (
        "file:///c%3A/x/sub" if overlapping else "file:///c%3A/x"
    )
    diags.clear()
    session._publish({path})
    assert published[-1] == ("file:///C:/x/sub/Bad.CR6", None, [])
    assert len(published) == 2
    assert "file:///C:/x/sub/Bad.CR6" not in session._last


@pytest.mark.server
@pytest.mark.parametrize("workspace_folders", [True, False])
def test_unopened_diagnostics_and_folder_removal_keep_client_root(make_client, tmp_path, workspace_folders):
    root = tmp_path / "proj"
    root.mkdir()
    entry = root / "Bad.CR6"
    entry.write_text("BeginProg\nx = unknown\nEndProg\n")
    uri = root.as_uri().replace("/proj", "/%70roj")
    client = make_client()
    client.request(
        "initialize",
        {
            "processId": None,
            "rootUri": uri,
            "capabilities": {},
            "initializationOptions": FAST,
            "workspaceFolders": [{"uri": uri, "name": "proj"}] if workspace_folders else None,
        },
    )
    client.notify("initialized", {})
    expected = uri + "/Bad.CR6"
    client.wait_for(lambda: any(p["uri"] == expected and p["diagnostics"] for p in client.publishes()))
    client.notify(
        "workspace/didChangeWorkspaceFolders", {"event": {"added": [], "removed": [{"uri": uri, "name": "proj"}]}}
    )
    client.wait_for(lambda: any(p["uri"] == expected and not p["diagnostics"] for p in client.publishes()))
    # Adding a workspace root also registers its spelling before discovery publishes.
    client.notify(
        "workspace/didChangeWorkspaceFolders", {"event": {"added": [{"uri": uri, "name": "proj"}], "removed": []}}
    )
    client.wait_for(lambda: len([p for p in client.publishes() if p["uri"] == expected and p["diagnostics"]]) == 2)
    assert all(p["uri"] == expected for p in client.publishes())
    assert client.shutdown() == 0


def test_protocol_streams_leave_posix_unchanged(monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "darwin")
    dup = Mock(side_effect=AssertionError("must not duplicate POSIX descriptors"))
    monkeypatch.setattr(server.os, "dup", dup)
    with server.protocol_streams() as streams:
        assert streams == (None, None)


def test_windows_protocol_fds_with_simulated_win32_handles():
    # Real descriptor operations in an isolated subprocess; only SetStdHandle and
    # get_osfhandle are simulated. This cannot reproduce Windows' pipe startup stall.
    script = """
import ctypes, os, sys
from types import SimpleNamespace
from opencrbasic.lsp.server import protocol_streams
calls = []
def set_handle(which, handle):
    calls.append((which, handle))
    return True
ctypes.WinDLL = lambda *a, **k: SimpleNamespace(SetStdHandle=set_handle)
sys.modules["msvcrt"] = SimpleNamespace(get_osfhandle=lambda fd: fd)
sys.platform = "win32"
with protocol_streams() as (reader, writer):
    assert calls == [(-10, 0), (-11, 1)]
    assert not os.get_inheritable(reader.fileno())
    assert not os.get_inheritable(writer.fileno())
    assert os.read(0, 1) == b""
    os.write(1, b"not protocol")
    writer.write(reader.read())
    writer.flush()
    sys.stderr.write("stderr unchanged")
assert reader.closed and writer.closed
"""
    result = lspclient.subprocess.run(
        [lspclient.sys.executable, "-c", script], input=b"protocol\n", capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == b"protocol\n"
    assert result.stderr == b"stderr unchanged"


def test_main_detaches_streams_before_building_server(monkeypatch):
    events = []
    streams = Mock()
    streams.__enter__ = Mock(side_effect=lambda: events.append("streams") or ("in", "out"))
    streams.__exit__ = Mock()
    fake = SimpleNamespace(session=Mock(), state={"shutdown": True}, start_io=Mock())
    monkeypatch.setattr(server, "startup_line", lambda: "test")
    monkeypatch.setattr(server, "check_api_version", lambda: None)
    monkeypatch.setattr(server, "protocol_streams", lambda: streams)
    monkeypatch.setattr(server, "build_server", lambda: events.append("build") or fake)
    server.main()
    assert events == ["streams", "build"]
    fake.start_io.assert_called_once_with("in", "out")
    fake.session.close.assert_called_once_with()
