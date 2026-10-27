"""The stdio language server, driven as a real subprocess (LSP-01b)."""

from .assertions import required
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.lsp import config as lsp_config

from .lspclient import SRC, LspClient

pytestmark = pytest.mark.server

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT.parent / "crbasic").resolve()
FAST = {"debounceMs": 50, "analysisTimeoutMs": 60000}

SMALL = """Public x As Long
Include("CPU:Lib.crb")
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
LIB = "Public lib_v As Long\n"
BAD_LIB = "Public lib_v As Long\nPublic lib_v As Long\n"
BAD_LINE = 9                      # 1-based line of `x = x + 1` in SMALL


def bad(text: str, name: str = "y") -> str:
    return text.replace("x = x + 1", f"x = {name} + 1")


@pytest.fixture
def make_client(request):
    clients: list[LspClient] = []

    def factory(**kwargs) -> LspClient:
        client = LspClient(**kwargs)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.kill()
        time.sleep(0.1)
        assert "Traceback" not in client.stderr_text, client.stderr_text


def project(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text)
    return root


def small_project(tmp_path: Path, entries=("Small.CR6",), lib: str = LIB) -> Path:
    files = {name: SMALL for name in entries}
    files["Lib.crb"] = lib
    return project(tmp_path, **files)


def started(make_client, root: Path, options=None, **kwargs) -> LspClient:
    client = make_client(**kwargs)
    client.initialize(root, {**FAST, **(options or {})})
    return client


def summary(params: dict) -> list[tuple]:
    return [(d["range"]["start"]["line"], d["severity"], d["source"], d["message"])
            for d in params["diagnostics"]]


# ---- lifecycle ----------------------------------------------------------------------------

def test_lifecycle_and_capabilities(make_client, tmp_path):
    root = small_project(tmp_path)
    client = make_client()
    result = client.initialize(root, FAST)
    caps = result["capabilities"]
    assert caps["textDocumentSync"]["change"] == 1 and caps["textDocumentSync"]["openClose"]
    # pygls adds an empty executeCommandProvider and workspace-folder support unconditionally;
    # the language features are the ones tests/lsp/test_features_server.py exercises.
    assert set(caps) == {"textDocumentSync", "positionEncoding", "executeCommandProvider",
                         "workspace", "completionProvider", "hoverProvider",
                         "signatureHelpProvider", "documentSymbolProvider",
                         "foldingRangeProvider", "documentLinkProvider", "definitionProvider",
                         "referencesProvider", "documentHighlightProvider",
                         "semanticTokensProvider", "renameProvider",
                         "documentFormattingProvider", "documentRangeFormattingProvider",
                         "codeActionProvider", "inlayHintProvider"}, caps
    assert sorted(caps["executeCommandProvider"]["commands"]) == [
        "opencrbasic.showCompilerOutput", "opencrbasic.showTableFields"]
    assert result["serverInfo"]["name"] == "opencrbasic"
    assert client.shutdown() == 0
    assert "opencrbasic-lsp: opencrbasic=" in client.stderr_text
    assert "api_version=1" in client.stderr_text and str(SRC) in client.stderr_text


def test_startup_banner_is_also_an_info_log_message(make_client, tmp_path):
    client = make_client()
    client.initialize(small_project(tmp_path), FAST)
    logs = client.wait_for(lambda: [m for m in client.messages
                                    if m.get("method") == "window/logMessage"], 30, "logMessage")
    assert len(logs) == 1, logs
    assert logs[0]["params"]["type"] == 3          # MessageType.Info
    assert logs[0]["params"]["message"].startswith("opencrbasic-lsp: opencrbasic=")
    assert "opencrbasic-lsp: opencrbasic=" in client.stderr_text
    assert client.shutdown() == 0


def test_vscode_language_covers_logger_extensions():
    manifest = json.loads((ROOT / "vscode" / "package.json").read_text(encoding="utf-8"))
    language = next(lang for lang in manifest["contributes"]["languages"] if lang["id"] == "crbasic")
    declared = language["extensions"]
    lowered = {ext.lower() for ext in declared}
    logger_exts = set(profiles.EXTENSIONS)
    assert logger_exts <= lowered
    assert lowered - logger_exts == {".crb"}       # the only non-logger extension
    for ext in logger_exts:
        assert ext.upper() in declared, ext        # both spellings, as .CR6 / .cr6 are declared


# Every key `lsp/config.parse_config` accepts, with its Config attribute. featureWaitMs is internal:
# docs/lsp-api.md ("Configuration") says the extension does not contribute it.
SERVER_KEY_ATTRS = {
    "entries": "entries", "exclude": "exclude", "logger": "logger", "encoding": "encoding",
    "debounceMs": "debounce_ms", "analysisTimeoutMs": "analysis_timeout_ms",
    "featureWaitMs": "feature_wait_ms", "maxFileBytes": "max_file_bytes",
    "maxQueuedJobs": "max_queued_jobs", "maxCacheMb": "max_cache_mb",
    "helpDirectory": "help_directory", "inlayHints.parameterNames": "inlay_parameter_names",
}
INTERNAL_SERVER_KEYS = {"featureWaitMs"}


def test_vscode_contributes_server_tuning_keys():
    manifest = json.loads((ROOT / "vscode" / "package.json").read_text(encoding="utf-8"))
    props = manifest["contributes"]["configuration"]["properties"]
    contributed = {name.removeprefix("opencrbasic."): prop for name, prop in props.items()
                   if name.startswith("opencrbasic.")}
    server_keys = set(SERVER_KEY_ATTRS) - INTERNAL_SERVER_KEYS
    client_only = {"pythonPath", "serverArgs", "serverEnv", "trace.server"}
    assert set(contributed) == server_keys | client_only
    defaults = lsp_config.Config()
    for key in server_keys:
        prop = contributed[key]
        default = getattr(defaults, SERVER_KEY_ATTRS[key])
        expected = list(default) if isinstance(default, tuple) else default
        assert prop["default"] == expected, key
        _, warnings = lsp_config.parse_config({key: prop["default"]})
        assert warnings == [], key               # the contributed default is a value the server accepts
    for key in ("maxFileBytes", "maxQueuedJobs", "maxCacheMb"):
        assert contributed[key]["type"] == "integer" and contributed[key]["minimum"] == 1, key
    for key in ("debounceMs", "analysisTimeoutMs"):
        assert contributed[key]["type"] == "integer", key
    assert "featureWaitMs" not in contributed


def test_exit_without_shutdown_is_nonzero(make_client, tmp_path):
    client = make_client()
    root = project(tmp_path, **{"Broken.CR6": bad(SMALL), "Lib.crb": LIB})
    client.initialize(root, FAST)
    client.wait_diagnostics(root / "Broken.CR6", lambda d: bool(d))   # discovery finished
    client.notify("exit")
    assert client.proc.wait(30) == 1


def test_refuses_other_api_version():
    code = ("import opencrbasic.analysis as a; a.API_VERSION = 99; "
            "from opencrbasic.lsp.server import main; main()")
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          timeout=60)
    assert done.returncode != 0
    assert "refusing to start" in done.stderr and "99" in done.stderr


def test_requests_after_failure_still_answer(make_client, tmp_path):
    root = project(tmp_path, **{"Crash.CR6": SMALL, "Lib.crb": LIB})
    client = started(make_client, root, env={"OPENCRBASIC_LSP_TEST_CRASH": "Crash"})
    crash = root / "Crash.CR6"
    client.wait_diagnostics(crash, lambda d: any("terminated unexpectedly" in x["message"]
                                                 for x in d))
    assert client.request("shutdown") is None            # the server still answers requests
    client.notify("exit")
    assert client.proc.wait(30) == 0


# ---- diagnostics --------------------------------------------------------------------------

def test_clean_repository_program_matches_facade(make_client, tmp_path):
    root = tmp_path / "crbasic"
    shutil.copytree(CRBASIC, root)
    expected = analyze(AnalysisRequest(root / "Main.CR6"))
    assert expected.ok
    client = started(make_client, root, {"entries": ["Main.CR6"]})
    client.open(root / "Main.CR6")
    by_path: dict[Path, list[tuple]] = {}
    for d in expected.diagnostics:
        assert d.severity == "warning"
        by_path.setdefault(required(d.path), []).append(
            (max(d.line - 1, 0), 2, "opencrbasic", d.message))
    by_path.setdefault(root / "Main.CR6", [])           # a clean Main publishes an empty list
    for path, items in by_path.items():
        params = client.wait_diagnostics(path, lambda ds: len(ds) == len(items))
        assert sorted(summary(params)) == sorted(items)
    assert client.shutdown() == 0


def test_invalid_edit_then_fix(make_client, tmp_path):
    root = small_project(tmp_path)
    small = root / "Small.CR6"
    client = started(make_client, root)
    client.open(small, SMALL)
    first = client.wait_diagnostics(small, lambda d: True)
    assert first["diagnostics"] == [] and first["version"] == 1

    client.change(small, bad(SMALL), 2)
    params = client.wait_diagnostics(small, lambda d: bool(d))
    expected = analyze(AnalysisRequest(small, overlays={small: bad(SMALL).encode()}))
    (diag,) = params["diagnostics"]
    assert params["version"] == 2
    assert diag["severity"] == 1 and diag["source"] == "opencrbasic"
    assert diag["message"] == expected.diagnostics[0].message
    assert diag["range"]["start"] == {"line": BAD_LINE - 1, "character": 0}
    assert diag["range"]["end"] == {"line": BAD_LINE - 1,
                                    "character": len(bad(SMALL).splitlines()[BAD_LINE - 1])}

    client.change(small, SMALL, 3)
    cleared = client.wait_diagnostics(small, lambda d: not d, after=len(client.publishes(small)) - 1)
    assert cleared["version"] == 3 and cleared["diagnostics"] == []


def test_unsaved_include_edit_save_and_close(make_client, tmp_path):
    root = small_project(tmp_path)
    small, lib = root / "Small.CR6", root / "Lib.crb"
    client = started(make_client, root)
    assert client is not None
    client.open(small, SMALL)
    client.wait_diagnostics(small)
    client.open(lib, LIB)
    client.wait_diagnostics(lib, lambda d: not any(x["source"] == "opencrbasic"
                                                   and x["severity"] != 3 for x in d))

    client.change(lib, BAD_LIB, 2)
    params = client.wait_diagnostics(lib, lambda d: any(x["severity"] == 1 for x in d))
    (diag,) = params["diagnostics"]
    assert diag["message"] == "lib_v already declared as a variable."
    assert diag["range"]["start"]["line"] == 1 and diag["range"]["end"]["character"] == len(
        "Public lib_v As Long")
    assert required(client.latest(small))["diagnostics"] == []     # the entry itself is clean

    # closing without saving restores the disk state (valid)
    client.close(lib)
    client.wait_diagnostics(lib, lambda d: not d, after=len(client.publishes(lib)) - 1)

    # an edit that is then saved to disk stays after the document is closed
    client.open(lib, LIB, version=1)
    client.change(lib, BAD_LIB, 2)
    client.wait_diagnostics(lib, lambda d: any(x["severity"] == 1 for x in d),
                            after=len(client.publishes(lib)) - 1)
    lib.write_text(BAD_LIB)
    client.save(lib)
    time.sleep(0.5)
    n = len(client.publishes(lib))
    client.close(lib)
    final = client.wait_for(lambda: (client.latest(lib) if len(client.publishes(lib)) >= n
                                     and required(client.latest(lib)).get("version") is None else None))
    assert [x["message"] for x in final["diagnostics"]] == ["lib_v already declared as a variable."]
    assert client.shutdown() == 0


def test_rapid_versions_publish_only_latest(make_client, tmp_path):
    root = small_project(tmp_path)
    small = root / "Small.CR6"
    client = started(make_client, root)
    client.open(small, SMALL)
    client.wait_diagnostics(small)
    names = ["aa", "bb", "cc", "dd", "final_var"]
    for version, name in enumerate(names, start=2):
        client.change(small, bad(SMALL, name), version)
    client.wait_diagnostics(small, lambda d: any("final_var" in x["message"] for x in d))
    time.sleep(1.0)
    versions = [p["version"] for p in client.publishes(small)]
    assert versions == sorted(versions) and versions[-1] == 6
    last = client.latest(small)
    assert required(last)["version"] == 6 and "final_var" in required(last)["diagnostics"][0]["message"]


def test_uninclude_information_diagnostic(make_client, tmp_path):
    root = small_project(tmp_path)
    lone = root / "Lone.crb"
    lone.write_text(LIB)
    client = started(make_client, root)
    client.open(lone, LIB)
    params = client.wait_diagnostics(lone, lambda d: bool(d))
    (diag,) = params["diagnostics"]
    assert diag["severity"] == 3 and diag["range"]["start"]["line"] == 0
    assert diag["message"] == ("No configured entry program includes this file; diagnostics come "
                               "from the entry programs that include it.")
    # an included library gets no such note
    lib = root / "Lib.crb"
    client.open(lib, LIB)
    time.sleep(1.0)
    published = client.latest(lib)
    assert published is None or published["diagnostics"] == []


def test_program_file_that_is_not_an_entry_gets_the_information_note(make_client, tmp_path):
    root = small_project(tmp_path)
    other = root / "Other.CR300"                 # a program extension of another logger (H-06)
    other.write_text(SMALL)
    client = started(make_client, root)
    client.open(other, SMALL)
    params = client.wait_diagnostics(other, lambda d: bool(d))
    (diag,) = params["diagnostics"]
    assert diag["severity"] == 3 and diag["source"] == "opencrbasic"
    assert diag["message"] == ("This file is not an entry program for the configured logger and "
                               "entries, and no entry program includes it.")
    entry = root / "Small.CR6"                   # an analysed entry gets none
    client.open(entry, SMALL)
    time.sleep(1.0)
    published = client.latest(entry)
    assert published is None or published["diagnostics"] == []


def test_two_entries_get_prefixed_messages(make_client, tmp_path):
    root = small_project(tmp_path, entries=("A.CR6", "B.CR6"))
    lib = root / "Lib.crb"
    client = started(make_client, root)
    client.open(lib, LIB)
    client.change(lib, BAD_LIB, 2)
    params = client.wait_diagnostics(lib, lambda d: len(d) == 2 and d[0]["message"].startswith("["))
    assert sorted(d["message"] for d in params["diagnostics"]) == [
        "[A.CR6] lib_v already declared as a variable.",
        "[B.CR6] lib_v already declared as a variable."]
    # a single contributor is not prefixed
    client.change(lib, LIB, 3)
    client.wait_diagnostics(lib, lambda d: not d, after=len(client.publishes(lib)) - 1)


def test_unrepresentable_characters_warn(make_client, tmp_path):
    root = small_project(tmp_path)
    small = root / "Small.CR6"
    client = started(make_client, root, {"encoding": "ascii"})
    client.open(small, "' café\n" + SMALL)
    params = client.wait_diagnostics(small, lambda d: any(
        x["source"] == "opencrbasic (server)" for x in d))
    warning = next(x for x in params["diagnostics"] if x["source"] == "opencrbasic (server)")
    assert warning["severity"] == 2 and warning["range"]["start"]["line"] == 0
    assert "not representable" in warning["message"]


# A pulled `Public` initializer makes the compiler's line numbers differ from the physical ones
# (compiler line 4 is `  Q = Nope`, physical line 5); `Q = Nope2 + _` continues onto line 7.
PULLED = ("Public A = \n 1\nPublic Q\nBeginProg\n  Q = Nope\n  Q = Nope2 + _\n   1\nEndProg\n")


def test_diagnostic_uses_physical_line_after_initializer_pull(make_client, tmp_path):
    root = project(tmp_path, **{"Pull.CR6": PULLED, "Lib.crb": LIB})
    pull = root / "Pull.CR6"
    client = started(make_client, root)
    client.open(pull, PULLED)
    params = client.wait_diagnostics(pull, lambda d: any("Nope." in x["message"] for x in d))
    (diag,) = [x for x in params["diagnostics"] if "Nope." in x["message"]]
    assert diag["range"]["start"] == {"line": 4, "character": 0}
    assert diag["range"]["end"] == {"line": 4, "character": len("  Q = Nope")}
    assert client.shutdown() == 0


def test_continued_statement_range_covers_both_lines(make_client, tmp_path):
    root = project(tmp_path, **{"Pull.CR6": PULLED, "Lib.crb": LIB})
    pull = root / "Pull.CR6"
    client = started(make_client, root)
    client.open(pull, PULLED)
    params = client.wait_diagnostics(pull, lambda d: any("Nope2" in x["message"] for x in d))
    (diag,) = [x for x in params["diagnostics"] if "Nope2" in x["message"]]
    assert diag["range"]["start"] == {"line": 5, "character": 0}
    assert diag["range"]["end"] == {"line": 6, "character": len("   1")}
    assert client.shutdown() == 0


# ---- worker failures ----------------------------------------------------------------------

def test_worker_crash_recovers(make_client, tmp_path):
    root = project(tmp_path, **{"Crash.CR6": SMALL, "Ok.CR6": bad(SMALL), "Lib.crb": LIB})
    client = started(make_client, root, env={"OPENCRBASIC_LSP_TEST_CRASH": "Crash"})
    crash, ok = root / "Crash.CR6", root / "Ok.CR6"
    client.open(crash, SMALL)
    client.open(ok, bad(SMALL))
    err = client.wait_diagnostics(crash, lambda d: any(x["source"] == "opencrbasic (internal)"
                                                       for x in d))
    (diag,) = err["diagnostics"]
    assert diag["severity"] == 1 and diag["range"]["start"]["line"] == 0
    assert "terminated unexpectedly" in diag["message"]
    # the innocent entry running at the same time is analysed normally, not blamed
    good = client.wait_diagnostics(ok, lambda d: bool(d))
    assert [x["source"] for x in good["diagnostics"]] == ["opencrbasic"]
    # a later edit is analysed normally
    client.change(ok, SMALL, 2)
    client.wait_diagnostics(ok, lambda d: not d, after=len(client.publishes(ok)) - 1)
    client.change(ok, bad(SMALL, "later"), 3)
    client.wait_diagnostics(ok, lambda d: any("later" in x["message"] for x in d))
    assert client.shutdown() == 0


def test_analysis_timeout_recovers(make_client, tmp_path):
    root = project(tmp_path, **{"Slow.CR6": SMALL, "Fast.CR6": SMALL, "Lib.crb": LIB})
    client = started(make_client, root, {"analysisTimeoutMs": 1000},
                     settings={**FAST, "analysisTimeoutMs": 1000},
                     env={"OPENCRBASIC_LSP_TEST_SLEEP": "Slow:20"})
    slow, fast = root / "Slow.CR6", root / "Fast.CR6"
    err = client.wait_diagnostics(slow, lambda d: any(x["source"] == "opencrbasic (internal)"
                                                      for x in d))
    assert "timed out after 1000 ms" in err["diagnostics"][0]["message"]
    client.open(fast, SMALL)
    client.change(fast, bad(SMALL, "after_timeout"), 2)
    client.wait_diagnostics(fast, lambda d: any("after_timeout" in x["message"] for x in d))
    t0 = time.monotonic()
    assert client.shutdown() == 0
    assert time.monotonic() - t0 < 20


# ---- configuration ------------------------------------------------------------------------

def test_exclude_change_via_configuration(make_client, tmp_path):
    root = project(tmp_path, **{"Good.CR6": SMALL, "Broken.CR6": bad(SMALL), "Lib.crb": LIB})
    broken = root / "Broken.CR6"
    client = started(make_client, root, settings=dict(FAST))
    client.wait_diagnostics(broken, lambda d: bool(d))           # analysed from disk, not open
    client.settings = {**FAST, "exclude": ["Broken.CR6"]}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    cleared = client.wait_diagnostics(broken, lambda d: not d, after=1)
    assert cleared["diagnostics"] == []
    # an opened program is an entry for itself even when excluded
    client.open(broken, bad(SMALL))
    client.wait_diagnostics(broken, lambda d: bool(d), after=len(client.publishes(broken)) - 1)


def test_a_setting_removed_returns_to_its_default(make_client, tmp_path):
    root = project(tmp_path, **{"Broken.CR6": bad(SMALL), "Lib.crb": LIB})
    broken = root / "Broken.CR6"
    client = started(make_client, root, settings=dict(FAST))
    client.wait_diagnostics(broken, lambda d: bool(d))
    client.settings = {**FAST, "exclude": ["Broken.CR6"]}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_diagnostics(broken, lambda d: not d, after=1)
    client.settings = dict(FAST)                    # the key is gone: the default (no exclude)
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_diagnostics(broken, lambda d: bool(d), after=2)


def test_configuration_pushed_without_pull_support(make_client, tmp_path):
    root = project(tmp_path, **{"Broken.CR6": bad(SMALL), "Lib.crb": LIB})
    broken = root / "Broken.CR6"
    client = started(make_client, root, pull_configuration=False)
    client.wait_diagnostics(broken, lambda d: bool(d))
    client.notify("workspace/didChangeConfiguration",
                  {"settings": {"opencrbasic": {"exclude": ["**/Broken.CR6"]}}})
    client.wait_diagnostics(broken, lambda d: not d, after=1)


def test_entries_glob_restricts_discovery(make_client, tmp_path):
    root = project(tmp_path, **{"Good.CR6": bad(SMALL), "Other.CR6": bad(SMALL), "Lib.crb": LIB})
    client = started(make_client, root, {"entries": ["Good.CR6"]})
    client.wait_diagnostics(root / "Good.CR6", lambda d: bool(d))
    time.sleep(1.0)
    assert client.publishes(root / "Other.CR6") == []
