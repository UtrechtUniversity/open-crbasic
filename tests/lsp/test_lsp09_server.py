"""Inlay hints and commands over real stdio transcripts (LSP-09c).

Expected values come from the core modules (`inlay`, `commands`) applied to the same program, so
these tests check the adapter: ranges, position encodings, the setting, refresh, overlays, entry
resolution and refusals.
"""

import time
from pathlib import Path

import pytest

from opencrbasic import commands, inlay
from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.helpdocs import HelpProvider

from .lspclient import LspClient
from .test_features_server import locate, make_client, units  # noqa: F401

pytestmark = pytest.mark.server

ROOT = Path(__file__).resolve().parents[2]
CHM = ROOT / "extracted_chm"
FAST = {"debounceMs": 50, "analysisTimeoutMs": 60000}
HELP = {**FAST, "helpDirectory": str(CHM)}
need_help = pytest.mark.skipif(not CHM.is_dir(), reason="extracted_chm not present")

COMPILER_OUTPUT = "opencrbasic.showCompilerOutput"
TABLE_FIELDS = "opencrbasic.showTableFields"

PROGRAM = """Public T As Float
Public x As Long
DataTable(Out,True,-1)
  DataInterval(0,10,Min,10)
  Sample(1,x,Long)
  Average(1,T,FP2,False)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    VoltSe(T,1,mV200,U1,False,0,250,1,0)
    CallTable Out
  NextScan
EndProg
"""
UNICODE = PROGRAM.replace("    VoltSe(", '    x = 1 : s = "é😀" : VoltSe(').replace(
    "Public x As Long\n", "Public x As Long\nPublic s As String * 20\n"
)
ENTRY = """Public x As Long
Include("CPU:Lib.crb")
BeginProg
  Scan(1,Sec,0,0)
    x = x + lib_v
  NextScan
EndProg
"""
LIB = "Public lib_v As Long\n"


def project(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    for name, text in files.items():
        (root / name.replace("_", ".")).write_text(text, encoding="utf-8", newline="")
    return root


def wire(hints: list[dict]) -> list[tuple[int, int, str]]:
    return [(h["position"]["line"], h["position"]["character"], h["label"]) for h in hints]


def core_hints(path: Path, text: str | None = None) -> list[tuple[int, int, str]]:
    result = analyze(AnalysisRequest(path, "cr6", {path: text.encode()} if text else {}))
    found = inlay.parameter_hints(result, path, 0, 10_000, "cr6", HelpProvider(CHM))
    return [(h.line, h.column, h.label) for h in found]


def started(make_client, root: Path, options=None, **init) -> LspClient:
    client = make_client()
    client.initialize(root, {**(options or HELP)}, **init)
    return client


def wait_clean(client: LspClient, path: Path, after: int = 0) -> dict:
    return client.wait_diagnostics(path, lambda d: not d, after=after)


def failure(client: LspClient, command: str, *arguments) -> dict:
    rid = client.send_request("workspace/executeCommand", {"command": command, "arguments": list(arguments)})
    message = client.response(rid, 60, command)
    assert "error" in message, message
    return message["error"]


# ---- capabilities -------------------------------------------------------------------------


def test_capabilities_list_the_hint_provider_and_both_commands(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    client = make_client()
    caps = client.initialize(root, FAST)["capabilities"]
    assert caps["inlayHintProvider"] in (True, {"resolveProvider": False})
    assert {COMPILER_OUTPUT, TABLE_FIELDS} <= set(caps["executeCommandProvider"]["commands"])
    assert client.shutdown() == 0


# ---- inlay hints --------------------------------------------------------------------------


@need_help
def test_hints_equal_the_core_and_sit_before_the_arguments(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root)
    client.open(path)
    got = wire(client.inlay_hints(path))
    assert got == core_hints(path)
    line = PROGRAM.split("\n")[9]
    labels = [lab for ln, _, lab in got if ln == 9]
    assert labels[:3] == ["Dest:", "Reps:", "Range:"] and len(labels) == 9
    first = next(c for ln, c, lab in got if ln == 9 and lab == "Dest:")
    assert line[first : first + 1] == "T"
    assert client.shutdown() == 0


@need_help
def test_a_request_range_selects_the_lines(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root)
    client.open(path)
    everything = wire(client.inlay_hints(path))
    assert {ln for ln, _, _ in everything} >= {2, 3, 5, 9}
    only = wire(client.inlay_hints(path, (9, 0), (10, 0)))
    assert only == [h for h in everything if h[0] == 9] and only
    assert client.inlay_hints(path, (10, 0), (11, 0)) == []
    # a range ending at column 0 does not include that line
    assert all(ln < 9 for ln, _, _ in wire(client.inlay_hints(path, (0, 0), (9, 0))))
    assert client.shutdown() == 0


@need_help
@pytest.mark.parametrize("encoding", ["utf-16", "utf-8", "utf-32"])
def test_hint_columns_use_the_negotiated_encoding(make_client, tmp_path, encoding):
    root = project(tmp_path, Small_CR6=UNICODE)
    path = root / "Small.CR6"
    client = started(make_client, root, position_encodings=[encoding])
    client.open(path)
    got = wire(client.inlay_hints(path))
    lines = UNICODE.split("\n")
    row = next(i for i, text in enumerate(lines) if "VoltSe(" in text)
    dest = next(c for ln, c, lab in got if ln == row and lab == "Dest:")
    start = lines[row].index("VoltSe(") + len("VoltSe(")
    assert dest == units(lines[row], start, encoding)
    if encoding != "utf-32":
        assert dest != start  # the emoji makes the encodings differ
    assert client.shutdown() == 0


def test_no_help_directory_gives_no_hints(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root, FAST)
    client.open(path)
    assert client.inlay_hints(path) == []
    assert client.shutdown() == 0


@need_help
def test_the_setting_disables_hints_and_sends_a_refresh(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root, inlay_refresh=True)
    client.open(path)
    assert client.inlay_hints(path)
    before = len(client.server_requests("workspace/inlayHint/refresh"))
    client.settings = {**HELP, "inlayHints": {"parameterNames": False}}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_for(
        lambda: len(client.server_requests("workspace/inlayHint/refresh")) > before,
        what="a refresh after the setting changed",
    )
    assert client.inlay_hints(path) == []
    client.settings = {**HELP, "inlayHints": {"parameterNames": True}}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_for(lambda: client.inlay_hints(path), what="hints after enabling again")
    assert client.shutdown() == 0


@need_help
def test_the_setting_in_the_initialization_options(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root, {**HELP, "inlayHints": {"parameterNames": False}})
    client.open(path)
    assert client.inlay_hints(path) == []
    assert client.shutdown() == 0


@need_help
def test_a_new_program_asks_the_client_to_refresh(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root, inlay_refresh=True)
    client.open(path)
    client.wait_for(
        lambda: client.server_requests("workspace/inlayHint/refresh"), what="a refresh after the first analysis"
    )
    count = len(client.server_requests("workspace/inlayHint/refresh"))
    client.change(path, PROGRAM.replace("mV200", "mV20"), 2)
    client.wait_for(
        lambda: len(client.server_requests("workspace/inlayHint/refresh")) > count, what="a refresh after an edit"
    )
    assert client.shutdown() == 0


@need_help
def test_no_refresh_request_without_client_support(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root)
    client.open(path)
    assert client.inlay_hints(path)
    assert client.server_requests("workspace/inlayHint/refresh") == []
    assert client.shutdown() == 0


@need_help
def test_hints_follow_an_unsaved_buffer(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root)
    edited = "' unsaved\n" + PROGRAM
    client.open(path, edited)
    got = wire(client.inlay_hints(path))
    assert got == core_hints(path, edited)
    assert {ln for ln, _, _ in got} >= {10}
    assert path.read_text(encoding="utf-8") == PROGRAM
    assert client.shutdown() == 0


@need_help
def test_a_library_gets_its_hints_from_the_entry(make_client, tmp_path):
    lib = "Public v As Float\nSub Read1\n  VoltSe(v,1,mV200,U1,False,0,250,1,0)\nEndSub\n"
    entry = 'Include("CPU:Lib.crb")\nBeginProg\n  Scan(1,Sec,0,0)\n    Read1\n  NextScan\nEndProg\n'
    root = project(tmp_path, Small_CR6=entry, Lib_crb=lib)
    path = root / "Lib.crb"
    client = started(make_client, root)
    client.open(path)
    got = wire(client.inlay_hints(path))
    assert [lab for ln, _, lab in got if ln == 2][:2] == ["Dest:", "Reps:"]
    assert client.shutdown() == 0


# ---- commands -----------------------------------------------------------------------------


def test_compiler_output_is_what_the_compiler_prints(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root, FAST)
    uri = client.open(path)
    got = client.execute(COMPILER_OUTPUT, uri)
    want = commands.compiler_output(path, "cr6", {})
    assert got == want.text and want.ok
    assert "Small.CR6" in got.splitlines()[0]
    assert client.shutdown() == 0


def test_compiler_output_uses_the_unsaved_buffer(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root, FAST)
    broken = PROGRAM.replace("CallTable Out", "CallTable Missing")
    uri = client.open(path, broken)
    got = client.execute(COMPILER_OUTPUT, uri)
    assert got == commands.compiler_output(path, "cr6", {path: broken.encode()}).text
    assert "line 11" in got and got != commands.compiler_output(path, "cr6", {}).text
    client.change(path, PROGRAM, 2)
    assert client.execute(COMPILER_OUTPUT, uri) == commands.compiler_output(path, "cr6", {}).text
    assert client.shutdown() == 0


def test_a_library_resolves_to_its_entry(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=ENTRY, Lib_crb=LIB)
    entry, lib = root / "Small.CR6", root / "Lib.crb"
    client = started(make_client, root, FAST)
    client.open(entry)
    wait_clean(client, entry)
    lib_uri = lib.as_uri()  # not open: only the entry read it
    assert client.execute(COMPILER_OUTPUT, lib_uri) == commands.compiler_output(entry, "cr6", {}).text
    assert client.execute(TABLE_FIELDS, lib_uri) == client.execute(TABLE_FIELDS, entry.as_uri())
    assert client.shutdown() == 0


def test_a_library_buffer_is_compiled_through_its_entry(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=ENTRY, Lib_crb=LIB)
    entry, lib = root / "Small.CR6", root / "Lib.crb"
    client = started(make_client, root, FAST)
    client.open(entry)
    wait_clean(client, entry)
    uri = client.open(lib, LIB.replace("lib_v", "lib_w"))
    got = client.execute(COMPILER_OUTPUT, uri)
    assert got == commands.compiler_output(entry, "cr6", {lib: LIB.replace("lib_v", "lib_w").encode()}).text
    assert "lib_v" in got  # the entry still uses the old name
    assert client.shutdown() == 0


def test_table_fields_render_the_program_tables(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root, FAST)
    uri = client.open(path)
    got = client.execute(TABLE_FIELDS, uri)
    result = analyze(AnalysisRequest(path, "cr6"))
    assert result.ok
    assert got == commands.render_table_fields(commands.table_fields(result))
    assert "## Out (interval 10 MIN)" in got
    assert "| T_Avg | FP2 |  | Avg |" in got
    assert client.shutdown() == 0


def test_table_fields_follow_the_unsaved_buffer(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM)
    path = root / "Small.CR6"
    client = started(make_client, root, FAST)
    edited = PROGRAM.replace("DataTable(Out", "DataTable(Renamed").replace("CallTable Out", "CallTable Renamed")
    uri = client.open(path, edited)
    assert "## Renamed" in client.execute(TABLE_FIELDS, uri)
    assert "## Out" not in client.execute(TABLE_FIELDS, uri)
    client.change(path, PROGRAM, 2)
    deadline = time.monotonic() + 60
    while "## Out" not in (text := client.execute(TABLE_FIELDS, uri)):
        assert time.monotonic() < deadline, text
        time.sleep(0.2)
    assert client.shutdown() == 0


def test_table_fields_of_a_program_with_errors(make_client, tmp_path):
    broken = PROGRAM.replace("CallTable Out", "CallTable Missing")
    root = project(tmp_path, Small_CR6=broken)
    path = root / "Small.CR6"
    client = started(make_client, root, FAST)
    uri = client.open(path)
    got = client.execute(TABLE_FIELDS, uri)
    assert got == commands.render_table_fields([]) and got.startswith("No tables")
    assert client.shutdown() == 0


def test_command_refusals_are_errors_with_a_reason(make_client, tmp_path):
    root = project(tmp_path, Small_CR6=PROGRAM, Orphan_crb=LIB)
    client = started(make_client, root, FAST)
    client.open(root / "Small.CR6")
    orphan = (root / "Orphan.crb").as_uri()
    for command in (COMPILER_OUTPUT, TABLE_FIELDS):
        assert failure(client, command, orphan)["data"]["reason"] == "no_entry"
        assert failure(client, command)["data"]["reason"] == "bad_arguments"
        assert failure(client, command, 7)["data"]["reason"] == "bad_arguments"
        assert failure(client, command, "untitled:Untitled-1")["data"]["reason"] == "bad_arguments"
        assert failure(client, command, orphan, orphan)["data"]["reason"] == "bad_arguments"
    assert client.shutdown() == 0
    assert "Traceback" not in client.stderr_text, client.stderr_text
