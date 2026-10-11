"""A small JSON-RPC-over-stdio client for testing `python -m opencrbasic.lsp`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

SRC = Path(__file__).resolve().parents[2] / "src"


class LspClient:
    def __init__(self, env: dict[str, str] | None = None, settings: Any = None,
                 pull_configuration: bool = True):
        child_env = dict(os.environ)
        child_env.pop("OPENCRBASIC_STRICT", None)
        child_env["PYTHONPATH"] = str(SRC)
        child_env.update(env or {})
        self.proc = subprocess.Popen([sys.executable, "-m", "opencrbasic.lsp"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=child_env)
        self.settings = settings            # answer to workspace/configuration (None: null)
        self.pull_configuration = pull_configuration
        self.messages: list[dict] = []      # every message the server sent, in order
        self.stderr_text = ""
        self._cond = threading.Condition()
        self._next_id = 0
        self._responses: dict[int, dict] = {}
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    # ---- transport ----

    def _read_stderr(self) -> None:
        for raw in self.proc.stderr:
            self.stderr_text += raw.decode("utf-8", "replace")

    def _read_loop(self) -> None:
        out = self.proc.stdout
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
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
            self.proc.stdin.flush()

    def _answer_request(self, msg: dict) -> None:
        result: Any = None
        if msg["method"] == "workspace/configuration":
            result = [self.settings for _ in msg["params"]["items"]]
        self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    # ---- protocol ----

    def request(self, method: str, params: Any = None, timeout: float = 30) -> Any:
        with self._cond:
            self._next_id += 1
            rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        with self._cond:
            while rid not in self._responses:
                left = deadline - time.monotonic()
                if left <= 0 or self.proc.poll() is not None and rid not in self._responses:
                    raise TimeoutError(f"no response to {method}\nstderr:\n{self.stderr_text}")
                self._cond.wait(min(left, 0.2))
            msg = self._responses.pop(rid)
        if "error" in msg:
            raise RuntimeError(f"{method}: {msg['error']}")
        return msg.get("result")

    def notify(self, method: str, params: Any = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def initialize(self, folder: Path | None, options: dict | None = None) -> dict:
        caps: dict = {"workspace": {"configuration": self.pull_configuration}}
        params = {"processId": os.getpid(), "rootUri": folder.as_uri() if folder else None,
                  "capabilities": caps, "initializationOptions": options,
                  "workspaceFolders": [{"uri": folder.as_uri(), "name": folder.name}]
                  if folder else None}
        result = self.request("initialize", params)
        self.notify("initialized", {})
        return result

    def open(self, path: Path, text: str | None = None, version: int = 1) -> str:
        uri = path.as_uri()
        text = path.read_text(encoding="utf-8") if text is None else text
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "crbasic", "version": version, "text": text}})
        return uri

    def change(self, path: Path, text: str, version: int) -> None:
        self.notify("textDocument/didChange", {
            "textDocument": {"uri": path.as_uri(), "version": version},
            "contentChanges": [{"text": text}]})

    def save(self, path: Path) -> None:
        self.notify("textDocument/didSave", {"textDocument": {"uri": path.as_uri()}})

    def close(self, path: Path) -> None:
        self.notify("textDocument/didClose", {"textDocument": {"uri": path.as_uri()}})

    def shutdown(self, timeout: float = 30) -> int:
        self.request("shutdown")
        self.notify("exit")
        return self.proc.wait(timeout)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()

    # ---- observation ----

    def publishes(self, path: Path | None = None) -> list[dict]:
        with self._cond:
            msgs = [m["params"] for m in self.messages
                    if m.get("method") == "textDocument/publishDiagnostics"]
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
                    raise TimeoutError(f"timed out waiting for {what or predicate}\n"
                                       f"stderr:\n{self.stderr_text}\n"
                                       f"publishes: {self.publishes()}")
                self._cond.wait(min(left, 0.2))

    def wait_diagnostics(self, path: Path, predicate: Callable[[list[dict]], bool] = lambda d: True,
                         timeout: float = 60, after: int = 0) -> dict:
        """The first publish for `path` (index >= `after` in its publish list) whose diagnostics
        satisfy `predicate`."""
        def probe():
            for params in self.publishes(path)[after:]:
                if predicate(params["diagnostics"]):
                    return params
            return None
        return self.wait_for(probe, timeout, f"diagnostics for {path.name}")
