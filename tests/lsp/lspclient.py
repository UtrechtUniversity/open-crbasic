"""A small JSON-RPC-over-stdio client for testing `python -m opencrbasic.lsp`."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[2] / "src"


class LspClient:
    def __init__(self, env: dict[str, str] | None = None, settings: Any = None, pull_configuration: bool = True):
        child_env = dict(os.environ)
        child_env.pop("OPENCRBASIC_STRICT", None)
        child_env["PYTHONPATH"] = str(SRC)
        child_env.update(env or {})
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "opencrbasic.lsp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            start_new_session=True,
        )
        self.settings = settings  # answer to workspace/configuration (None: null)
        self.pull_configuration = pull_configuration
        self.messages: list[dict] = []  # every message the server sent, in order
        self.stderr_text = ""
        self._cond = threading.Condition()
        self._next_id = 0
        self._responses: dict[int, dict] = {}
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ---- transport ----

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self.stderr_text += raw.decode("utf-8", "replace")

    def _read_loop(self) -> None:
        out = self.proc.stdout
        assert out is not None
        while True:
            length = None
            while True:
                line = out.readline()
                if not line:
                    with self._cond:
                        self._cond.notify_all()
                    return
                line = line.strip()
                if not line:
                    break
                name, _, value = line.decode("ascii").partition(":")
                if name.lower() == "content-length":
                    length = int(value)
            assert length is not None, "message without Content-Length"
            msg = json.loads(out.read(length).decode("utf-8"))
            if "method" in msg and "id" in msg:
                self._answer_request(msg)
            with self._cond:
                if "method" not in msg and "id" in msg:
                    self._responses[msg["id"]] = msg
                self.messages.append(msg)
                self._cond.notify_all()

    def _send(self, msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        with self._write_lock:
            assert self.proc.stdin is not None
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self.proc.stdin.flush()

    def _answer_request(self, msg: dict) -> None:
        result: Any = None
        if msg["method"] == "workspace/configuration":
            result = [self.settings for _ in msg["params"]["items"]]
        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    # ---- protocol ----

    def send_request(self, method: str, params: Any = None) -> int:
        """Send a request without waiting; `response(id)` collects the raw reply."""
        with self._cond:
            self._next_id += 1
            rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return rid

    def response(self, rid: int, timeout: float = 30, what: str = "request") -> dict:
        deadline = time.monotonic() + timeout
        with self._cond:
            while rid not in self._responses:
                left = deadline - time.monotonic()
                if left <= 0 or self.proc.poll() is not None and rid not in self._responses:
                    raise TimeoutError(f"no response to {what}\nstderr:\n{self.stderr_text}")
                self._cond.wait(min(left, 0.2))
            return self._responses.pop(rid)

    def cancel(self, rid: int) -> None:
        self.notify("$/cancelRequest", {"id": rid})

    def request(self, method: str, params: Any = None, timeout: float = 30) -> Any:
        msg = self.response(self.send_request(method, params), timeout, method)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result")

    def notify(self, method: str, params: Any = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(
        self,
        folder: Path | None,
        options: dict | None = None,
        position_encodings: list[str] | None = None,
        semantic_refresh: bool = False,
        watched_files: bool = False,
        inlay_refresh: bool = False,
    ) -> dict:
        caps: dict = {
            "workspace": {"configuration": self.pull_configuration, "workspaceEdit": {"documentChanges": True}},
            "textDocument": {"rename": {"prepareSupport": True}},
        }
        if watched_files:
            caps["workspace"]["didChangeWatchedFiles"] = {"dynamicRegistration": True}
        if position_encodings is not None:
            caps["general"] = {"positionEncodings": position_encodings}
        if semantic_refresh:
            caps["workspace"]["semanticTokens"] = {"refreshSupport": True}
        if inlay_refresh:
            caps["workspace"]["inlayHint"] = {"refreshSupport": True}
        params = {
            "processId": os.getpid(),
            "rootUri": folder.as_uri() if folder else None,
            "capabilities": caps,
            "initializationOptions": options,
            "workspaceFolders": [{"uri": folder.as_uri(), "name": folder.name}] if folder else None,
        }
        result = self.request("initialize", params)
        self.notify("initialized", {})
        return result

    def open(self, path: Path, text: str | None = None, version: int = 1) -> str:
        uri = path.as_uri()
        text = path.read_text(encoding="utf-8") if text is None else text
        self.notify(
            "textDocument/didOpen",
            {"textDocument": {"uri": uri, "languageId": "crbasic", "version": version, "text": text}},
        )
        return uri

    def change(self, path: Path, text: str, version: int) -> None:
        self.notify(
            "textDocument/didChange",
            {"textDocument": {"uri": path.as_uri(), "version": version}, "contentChanges": [{"text": text}]},
        )

    def save(self, path: Path) -> None:
        self.notify("textDocument/didSave", {"textDocument": {"uri": path.as_uri()}})

    def watched(self, *changes: tuple[Path, int]) -> None:
        """`workspace/didChangeWatchedFiles` (1 created, 2 changed, 3 deleted)."""
        self.notify(
            "workspace/didChangeWatchedFiles",
            {"changes": [{"uri": path.as_uri(), "type": kind} for path, kind in changes]},
        )

    def change_folders(
        self, added: list[Path] | tuple[Path, ...] = (), removed: list[Path] | tuple[Path, ...] = ()
    ) -> None:
        def folder(p: Path) -> dict:
            return {"uri": p.as_uri(), "name": p.name}

        self.notify(
            "workspace/didChangeWorkspaceFolders",
            {"event": {"added": [folder(p) for p in added], "removed": [folder(p) for p in removed]}},
        )

    def close(self, path: Path) -> None:
        self.notify("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})

    # ---- language features (text document requests) ----

    def at(self, method: str, path: Path, line: int, character: int, timeout: float = 60, **extra: Any) -> Any:
        return self.request(
            method,
            {"textDocument": {"uri": path.as_uri()}, "position": {"line": line, "character": character}, **extra},
            timeout,
        )

    def on_file(self, method: str, path: Path, timeout: float = 60) -> Any:
        return self.request(method, {"textDocument": {"uri": path.as_uri()}}, timeout)

    def inlay_hints(
        self, path: Path, start: tuple[int, int] = (0, 0), end: tuple[int, int] = (100000, 0), timeout: float = 60
    ) -> list[dict]:
        return (
            self.request(
                "textDocument/inlayHint",
                {
                    "textDocument": {"uri": path.as_uri()},
                    "range": {
                        "start": {"line": start[0], "character": start[1]},
                        "end": {"line": end[0], "character": end[1]},
                    },
                },
                timeout,
            )
            or []
        )

    def execute(self, command: str, *arguments: Any, timeout: float = 60) -> Any:
        return self.request("workspace/executeCommand", {"command": command, "arguments": list(arguments)}, timeout)

    def server_requests(self, method: str) -> list[dict]:
        with self._cond:
            return [m for m in self.messages if m.get("method") == method and "id" in m]

    def shutdown(self, timeout: float = 30) -> int:
        self.request("shutdown")
        self.notify("exit")
        return self.proc.wait(timeout)

    def kill(self) -> None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True
            )  # include spawned pool workers
        else:
            # SIGTERM first. multiprocessing's resource tracker ignores it and unlinks the pool's
            # named semaphores once the server and its workers are gone; SIGKILL alone kills the
            # tracker too and leaks them until macOS has none left.
            self._signal_group(signal.SIGTERM)
            try:
                self.proc.wait(5)  # reaped: only the tracker and the workers keep the group alive
            except subprocess.TimeoutExpired:
                pass
            deadline = time.monotonic() + 5
            while self._signal_group(0) and time.monotonic() < deadline:
                time.sleep(0.01)
            self._signal_group(signal.SIGKILL)
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()

    def _signal_group(self, sig: int) -> bool:
        """Signal the server's own session; whether it still has a process."""
        try:
            os.killpg(self.proc.pid, sig)
        except ProcessLookupError, PermissionError:
            return False
        return True

    # ---- observation ----

    def publishes(self, path: Path | None = None) -> list[dict]:
        with self._cond:
            msgs = [m["params"] for m in self.messages if m.get("method") == "textDocument/publishDiagnostics"]
        return [m for m in msgs if path is None or m["uri"] == path.as_uri()]

    def latest(self, path: Path) -> dict | None:
        found = self.publishes(path)
        return found[-1] if found else None

    def wait_for(self, predicate: Callable[[], Any], timeout: float = 60, what: str = "") -> Any:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                value = predicate()
                if value:
                    return value
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(
                        f"timed out waiting for {what or predicate}\n"
                        f"stderr:\n{self.stderr_text}\n"
                        f"publishes: {self.publishes()}"
                    )
                self._cond.wait(min(left, 0.2))

    def wait_diagnostics(
        self, path: Path, predicate: Callable[[list[dict]], bool] = lambda d: True, timeout: float = 60, after: int = 0
    ) -> dict:
        """The first publish for `path` (index >= `after` in its publish list) whose diagnostics
        satisfy `predicate`."""

        def probe():
            for params in self.publishes(path)[after:]:
                if predicate(params["diagnostics"]):
                    return params
            return None

        return self.wait_for(probe, timeout, f"diagnostics for {path.name}")
