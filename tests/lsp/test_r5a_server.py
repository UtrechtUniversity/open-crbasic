"""`opencrbasic.showCompilerOutput` under the server's bounds (LSP-R5a, review 743da2ac finding 1).

The command runs in the worker pool: `maxFileBytes` applies to the entry and to the files its
current text includes, `analysisTimeoutMs` kills the worker, and the refusal is an LSP error with a
reason. Each test then checks that a normal command and diagnostics still work.
"""

import time
from pathlib import Path

import pytest

from opencrbasic import commands

from .test_features_server import make_client  # noqa: F401
from .test_lsp09_server import COMPILER_OUTPUT, ENTRY, LIB, PROGRAM, failure, project, wait_clean

pytestmark = pytest.mark.server

BIG = "Public x\n" + "' padding\n" * 40 + "BeginProg\nx = 1\nEndProg\n"      # 433 bytes
SMALL = "Public x\nBeginProg\nx = 1\nEndProg\n"


def still_usable(client, root: Path) -> None:
    """A fresh entry gets its diagnostics and the exact compiler text."""
    path = root / "Fine.CR6"
    path.write_text(SMALL)
    uri = client.open(path)
    wait_clean(client, path)
    assert client.execute(COMPILER_OUTPUT, uri) == commands.compiler_output(path, "cr6", {}).text


def test_an_oversized_entry_is_refused(make_client, tmp_path):
    root = project(tmp_path, Big_CR6=BIG)
    path = root / "Big.CR6"
    client = make_client(pull_configuration=False)
    client.initialize(root, {"debounceMs": 0, "maxFileBytes": 100})
    uri = client.open(path)
    client.wait_diagnostics(path, lambda ds: any("maxFileBytes" in d["message"] for d in ds))
    error = failure(client, COMPILER_OUTPUT, uri)
    assert error["code"] == -32803 and error["data"] == {"reason": "too_large"}
    assert error["message"] == ("Cannot show compiler output: Big.CR6 is 433 bytes, above the "
                                "maxFileBytes limit of 100.")
    still_usable(client, root)
    assert client.shutdown() == 0


def test_an_oversized_include_on_disk_is_refused(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=ENTRY, Lib_crb=LIB + "' padding\n" * 20)
    entry, lib = root / "Small.CR6", root / "Lib.crb"
    size = lib.stat().st_size
    assert len(ENTRY.encode()) < 150 < size
    client = make_client(pull_configuration=False)
    client.initialize(root, {"debounceMs": 0, "maxFileBytes": 150})
    uri = client.open(entry)
    client.wait_diagnostics(entry, lambda ds: any("maxFileBytes" in d["message"] for d in ds))
    error = failure(client, COMPILER_OUTPUT, uri)
    assert error["data"] == {"reason": "too_large"}
    assert error["message"] == (f"Cannot show compiler output: Lib.crb is {size} bytes, above "
                                "the maxFileBytes limit of 150.")
    still_usable(client, root)
    assert client.shutdown() == 0


def test_an_oversized_unsaved_include_buffer_is_refused_and_the_bound_follows_the_buffer(
        make_client, tmp_path):
    root = project(tmp_path, Small_CR6=ENTRY, Lib_crb=LIB)
    entry, lib = root / "Small.CR6", root / "Lib.crb"
    client = make_client(pull_configuration=False)
    client.initialize(root, {"debounceMs": 0, "maxFileBytes": 150})
    uri = client.open(entry)
    wait_clean(client, entry)
    big = LIB + "' padding\n" * 20
    client.open(lib, big)
    error = failure(client, COMPILER_OUTPUT, uri)
    assert error["data"] == {"reason": "too_large"}
    assert f"Lib.crb is {len(big)} bytes" in error["message"]
    client.change(lib, LIB, 2)                   # back under the limit: the buffer is compiled
    assert client.execute(COMPILER_OUTPUT, uri) == commands.compiler_output(
        entry, "cr6", {lib: LIB.encode()}).text
    assert client.shutdown() == 0


def test_a_compilation_over_the_timeout_is_refused(make_client, tmp_path):
    root = project(tmp_path, Slow_CR6=PROGRAM)
    path = root / "Slow.CR6"
    client = make_client(env={"OPENCRBASIC_LSP_TEST_SLEEP": "Slow:30"}, pull_configuration=False)
    client.initialize(root, {"debounceMs": 0, "analysisTimeoutMs": 1000})
    uri = client.open(path)
    client.wait_diagnostics(path, lambda ds: any("timed out" in d["message"] for d in ds))
    start = time.monotonic()
    error = failure(client, COMPILER_OUTPUT, uri)
    assert time.monotonic() - start < 10
    assert error["code"] == -32803 and error["data"] == {"reason": "timeout"}
    assert error["message"] == ("Cannot show compiler output: Compilation of Slow.CR6 timed out "
                                "after 1000 ms; the analysis worker was restarted.")
    still_usable(client, root)
    assert client.shutdown() == 0


def test_a_cancelled_command_answers_cancelled_and_the_server_stays_usable(make_client, tmp_path):
    root = project(tmp_path, Slow_CR6=PROGRAM)
    path = root / "Slow.CR6"
    client = make_client(env={"OPENCRBASIC_LSP_TEST_SLEEP": "Slow:30"}, pull_configuration=False)
    client.initialize(root, {"debounceMs": 0, "analysisTimeoutMs": 1000})
    uri = client.open(path)
    client.wait_diagnostics(path, lambda ds: any("timed out" in d["message"] for d in ds))
    rid = client.send_request("workspace/executeCommand",
                              {"command": COMPILER_OUTPUT, "arguments": [uri]})
    time.sleep(0.3)
    client.cancel(rid)
    assert client.response(rid, 10, "cancelled command")["error"]["code"] == -32800
    time.sleep(1.5)                              # the job reaches its timeout in the worker
    still_usable(client, root)
    assert client.shutdown() == 0
