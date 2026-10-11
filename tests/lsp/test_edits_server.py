"""Rename, formatting and quick fixes (and the message display encoding) over real stdio transcripts
(LSP-07-wire). The project is a small temporary one: two entries that include one library. The
expected values are computed from the document text or from the core modules, so these tests check
the adapter (refusal mapping, versions, positions, scheduling), not CRBasic rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opencrbasic import formatting
from opencrbasic.lsp.session import display_text

from .lspclient import LspClient

pytestmark = pytest.mark.server
from .test_features_server import copy_crbasic, locate, make_client, units  # noqa: F401

FAST = {"debounceMs": 50, "analysisTimeoutMs": 60000}
REQUEST_FAILED = -32803

ENTRY = """Public x As Long
Include("CPU:Lib.crb")
DataTable(T,True,-1)
  DataInterval(0,1,Min,10)
  Sample(1,x,Long)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    x = x + lib_v
    CallTable T
  NextScan
EndProg
"""
LIB = "Public lib_v As Long\n"
FORMATTED = ENTRY
MESSY = """Public x As Long
Include("CPU:Lib.crb")
DataTable(T,True,-1)
DataInterval(0,1,Min,10)
    Sample(1,x,Long)
EndTable
BeginProg
Scan(1,Sec,0,0)
x = x + lib_v
      If x > 3 Then
x = 0
EndIf
CallTable T
NextScan
EndProg
"""


def project(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    for name, text in files.items():
        (root / name.replace("_", ".")).write_text(text, encoding="utf-8")
    return root


def edit_project(tmp_path: Path) -> Path:
    return project(tmp_path, A_CR6=ENTRY, B_CR6=ENTRY, Lib_crb=LIB)


def started(make_client, root: Path, options=None, **init) -> LspClient:
    client = make_client()
    client.initialize(root, {**FAST, **(options or {})}, **init)
    return client


def apply(text: str, edits: list[dict]) -> str:
    """Apply wire edits (ASCII documents: utf-16 characters equal indexes)."""
    assert text.isascii()
    lines = text.split("\n")
    for e in sorted(edits, key=lambda e: (e["range"]["start"]["line"],
                                          e["range"]["start"]["character"]), reverse=True):
        start, end = e["range"]["start"], e["range"]["end"]
        assert start["line"] == end["line"]
        line = lines[start["line"]]
        lines[start["line"]] = line[:start["character"]] + e["newText"] + line[end["character"]:]
    return "\n".join(lines)


def rename_at(client: LspClient, path: Path, line: int, character: int, new_name: str):
    return client.at("textDocument/rename", path, line, character, newName=new_name)


def error_of(client: LspClient, method: str, path: Path, line: int, character: int,
             **extra) -> dict:
    msg = client.response(client.send_request(method, {
        "textDocument": {"uri": path.as_uri()},
        "position": {"line": line, "character": character}, **extra}), 60, method)
    assert "error" in msg, msg
    return msg["error"]



# ---- capabilities -------------------------------------------------------------------------


def test_capabilities_advertise_the_edit_features(make_client, tmp_path):
    client = make_client()
    caps = client.initialize(edit_project(tmp_path), FAST)["capabilities"]
    assert caps["renameProvider"] == {"prepareProvider": True}
    assert caps["documentFormattingProvider"] is True
    assert caps["documentRangeFormattingProvider"] is True
    assert caps["codeActionProvider"]["codeActionKinds"] == ["quickfix"]
    assert client.shutdown() == 0


# ---- rename -------------------------------------------------------------------------------


def test_rename_across_a_library_and_two_entries(make_client, tmp_path):
    root = edit_project(tmp_path)
    a, b, lib = root / "A.CR6", root / "B.CR6", root / "Lib.crb"
    client = started(make_client, root)
    client.open(lib, LIB, version=7)
    client.open(a, ENTRY, version=3)
    prepared = client.at("textDocument/prepareRename", lib, 0, 9)
    assert prepared == {"range": {"start": {"line": 0, "character": 7},
                                  "end": {"line": 0, "character": 12}}, "placeholder": "lib_v"}
    edit = rename_at(client, lib, 0, 9, "lib_w")
    changes = edit["documentChanges"]
    by_uri = {c["textDocument"]["uri"]: c for c in changes}
    assert set(by_uri) == {lib.as_uri(), a.as_uri(), b.as_uri()}
    assert by_uri[lib.as_uri()]["textDocument"]["version"] == 7      # open: its version
    assert by_uri[a.as_uri()]["textDocument"]["version"] == 3
    assert by_uri[b.as_uri()]["textDocument"]["version"] is None     # closed: no version
    assert len(by_uri[lib.as_uri()]["edits"]) == 1
    for c in changes:                                  # one edit per use, none duplicated
        assert all(e["newText"] == "lib_w" for e in c["edits"])
    new_lib = apply(LIB, by_uri[lib.as_uri()]["edits"])
    new_a = apply(ENTRY, by_uri[a.as_uri()]["edits"])
    new_b = apply(ENTRY, by_uri[b.as_uri()]["edits"])
    assert new_lib == "Public lib_w As Long\n"
    assert new_a == ENTRY.replace("lib_v", "lib_w") == new_b
    # apply the edit the way a client would: the library first, so both entries fail, then the rest
    client.change(lib, new_lib, 8)
    for path in (a, b):
        client.wait_diagnostics(path, lambda d: any("lib_v" in x["message"] or "Invalid symbol"
                                                    in x["message"] for x in d))
    client.change(a, new_a, 4)
    b.write_text(new_b, encoding="utf-8")
    client.watched((b, 2))
    client.wait_for(lambda: client.latest(a)["version"] == 4 and client.latest(a)["diagnostics"] == [],
                    what="A clean")
    client.wait_for(lambda: client.latest(b)["diagnostics"] == [], what="B clean")
    again = client.at("textDocument/prepareRename", lib, 0, 9)
    assert again["placeholder"] == "lib_w"
    assert client.shutdown() == 0


def test_rename_refusals_are_lsp_errors_with_the_reason(make_client, tmp_path):
    root = edit_project(tmp_path)
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, ENTRY)
    scan = locate(ENTRY, "Scan(")
    err = error_of(client, "textDocument/prepareRename", a, *scan)
    assert err["code"] == REQUEST_FAILED
    assert err["message"].startswith("Cannot rename: ")
    assert err["data"]["reason"] in {"not_a_name", "external", "unresolved"}
    where = locate(ENTRY, "x", 0)
    clash = error_of(client, "textDocument/rename", a, *where, newName="lib_v")
    assert clash["code"] == REQUEST_FAILED and clash["data"] == {"reason": "collision"}
    assert "lib_v" in clash["message"]
    bad_name = error_of(client, "textDocument/rename", a, *where, newName="1bad")
    assert bad_name["data"] == {"reason": "invalid_name"}
    reserved = error_of(client, "textDocument/rename", a, *where, newName="Scan")
    assert reserved["data"] == {"reason": "reserved_name"}
    unchanged = error_of(client, "textDocument/rename", a, *where, newName="x")
    assert unchanged["data"] == {"reason": "unchanged"}
    assert client.shutdown() == 0


def test_rename_in_a_file_no_entry_reads_is_refused(make_client, tmp_path):
    root = project(tmp_path, A_CR6=ENTRY, Lib_crb=LIB, Other_crb="Public other_v As Long\n")
    client = started(make_client, root)
    other = root / "Other.crb"
    client.open(other, "Public other_v As Long\n")
    err = error_of(client, "textDocument/rename", other, 0, 9, newName="other_w")
    assert err["data"] == {"reason": "no_entry"}
    assert client.shutdown() == 0


def test_rename_waits_for_the_analysis_of_a_changed_document(make_client, tmp_path):
    root = edit_project(tmp_path)
    a, lib = root / "A.CR6", root / "Lib.crb"
    client = started(make_client, root)
    client.open(lib, LIB, version=1)
    client.open(a, ENTRY, version=1)
    client.on_file("textDocument/documentSymbol", a)
    changed = ENTRY.replace("x = x + lib_v", "x = x + lib_v + lib_v")
    client.change(a, changed, 2)                       # the analysis of version 2 is pending
    edit = rename_at(client, lib, 0, 9, "lib_w")
    by_uri = {c["textDocument"]["uri"]: c for c in edit["documentChanges"]}
    assert by_uri[a.as_uri()]["textDocument"]["version"] == 2
    assert apply(changed, by_uri[a.as_uri()]["edits"]) == changed.replace("lib_v", "lib_w")
    assert client.shutdown() == 0


def test_rename_refuses_while_an_entry_is_unanalysed(make_client, tmp_path):
    root = edit_project(tmp_path)
    lib = root / "Lib.crb"
    client = make_client(env={"OPENCRBASIC_LSP_TEST_SLEEP": "B.CR6:20"})
    client.initialize(root, {"debounceMs": 50, "analysisTimeoutMs": 1500})
    client.open(lib, LIB)
    err = error_of(client, "textDocument/rename", lib, 0, 9, newName="lib_w")
    assert err["code"] == REQUEST_FAILED
    assert err["data"]["reason"] in {"unanalysed", "pending"}
    assert "B.CR6" in err["message"]
    client.kill()


def test_rename_edit_columns_use_the_negotiated_encoding(make_client, tmp_path):
    text = 'Public s As String * 20\nPublic y As Long\nBeginProg\n  Scan(1,Sec,0,0)\n' \
           '    s = "é😀" : y = 1\n  NextScan\nEndProg\n'
    root = project(tmp_path, U_CR6=text)
    u = root / "U.CR6"
    for encoding in ("utf-16", "utf-8", "utf-32"):
        client = started(make_client, root, position_encodings=[encoding])
        client.open(u, text)
        edit = rename_at(client, u, *locate(text, "y", 0, 0), "yy")
        edits = edit["documentChanges"][0]["edits"]
        line = text.split("\n")[4]
        on_line = [e for e in edits if e["range"]["start"]["line"] == 4]
        assert len(on_line) == 1
        col = line.index("y =")
        assert on_line[0]["range"]["start"]["character"] == units(line, col, encoding)
        assert on_line[0]["range"]["end"]["character"] == units(line, col + 1, encoding)
        client.shutdown()


# ---- formatting ---------------------------------------------------------------------------


def edited_text(client: LspClient, path: Path, text: str, method: str, **params) -> str:
    edits = client.request(method, {"textDocument": {"uri": path.as_uri()}, **params}, 60)
    return apply(text, edits)


def test_formatting_an_indented_copy(make_client, tmp_path):
    root = project(tmp_path, A_CR6=MESSY, Lib_crb=LIB)
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, MESSY)
    options = {"tabSize": 2, "insertSpaces": True}
    got = edited_text(client, a, MESSY, "textDocument/formatting", options=options)
    want = formatting.apply_edits(MESSY, formatting.format_indentation(MESSY, "cr6", "  "))
    assert got == want
    assert "\n    x = x + lib_v\n" in got and "\n      If x > 3 Then\n" not in got
    assert got.count("\n") == MESSY.count("\n")
    tabs = edited_text(client, a, MESSY, "textDocument/formatting",
                       options={"tabSize": 4, "insertSpaces": False})
    assert "\n\t\tx = x + lib_v\n" in tabs
    assert client.shutdown() == 0


def test_range_formatting_touches_only_the_lines_asked(make_client, tmp_path):
    root = project(tmp_path, A_CR6=MESSY, Lib_crb=LIB)
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, MESSY)
    first, last = MESSY.split("\n").index("Scan(1,Sec,0,0)"), MESSY.split("\n").index("x = x + lib_v")
    # the range ends at character 0 of the line after `last`: that line is not included
    got = edited_text(client, a, MESSY, "textDocument/rangeFormatting", options={
        "tabSize": 2, "insertSpaces": True}, range={
        "start": {"line": first, "character": 3}, "end": {"line": last + 1, "character": 0}})
    want = formatting.apply_edits(MESSY, formatting.format_range(MESSY, first, last, "cr6", "  "))
    assert got == want != MESSY
    changed = [n for n, (p, q) in enumerate(zip(MESSY.split("\n"), got.split("\n"))) if p != q]
    assert changed == [first, last]
    assert client.shutdown() == 0


def test_formatting_a_file_with_errors_is_refused_with_a_message(make_client, tmp_path):
    broken = MESSY.replace("x = x + lib_v", "x = x + nothing_here")
    root = project(tmp_path, A_CR6=broken, Lib_crb=LIB)
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, broken)
    edits = client.request("textDocument/formatting", {
        "textDocument": {"uri": a.as_uri()}, "options": {"tabSize": 2, "insertSpaces": True}}, 60)
    assert edits == []
    shown = client.wait_for(lambda: [m for m in client.messages
                                     if m.get("method") == "window/showMessage"],
                            what="showMessage")
    assert shown[-1]["params"]["type"] == 3
    assert "errors" in shown[-1]["params"]["message"]
    assert client.shutdown() == 0


def test_formatting_a_library_follows_every_entry_that_reads_it(make_client, tmp_path):
    lib = "Public lib_v As Long\nSub Bump\n x = 1\nEndSub\n".replace("x = 1", "lib_v = 1")
    root = project(tmp_path, A_CR6=ENTRY.replace("x + lib_v", "x + lib_v"), Lib_crb=lib)
    client = started(make_client, root)
    path = root / "Lib.crb"
    client.open(path, lib)
    got = edited_text(client, path, lib, "textDocument/formatting",
                      options={"tabSize": 2, "insertSpaces": True})
    assert got == "Public lib_v As Long\nSub Bump\n  lib_v = 1\nEndSub\n"
    assert client.shutdown() == 0


# ---- quick fixes --------------------------------------------------------------------------


def test_quick_fix_for_a_misspelled_name_is_applied_and_clean(make_client, tmp_path):
    typo = ENTRY.replace("x = x + lib_v", "x = x + lib_w")
    root = project(tmp_path, A_CR6=typo, Lib_crb=LIB)
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, typo, version=5)
    published = client.wait_diagnostics(a, lambda d: any("Invalid symbol" in x["message"] for x in d))
    diags = published["diagnostics"]
    actions = client.request("textDocument/codeAction", {
        "textDocument": {"uri": a.as_uri()},
        "range": diags[0]["range"], "context": {"diagnostics": diags}}, 60)
    assert actions and actions[0]["kind"] == "quickfix"
    assert actions[0]["title"] == "Change to `lib_v`"
    assert actions[0]["diagnostics"] == [diags[0]]
    change = actions[0]["edit"]["documentChanges"][0]
    assert change["textDocument"] == {"uri": a.as_uri(), "version": 5}
    fixed = apply(typo, change["edits"])
    assert fixed == ENTRY
    before = len(client.publishes(a))
    client.change(a, fixed, 6)
    client.wait_diagnostics(a, lambda d: d == [], after=before)
    assert client.latest(a)["version"] == 6
    assert client.shutdown() == 0


def test_code_actions_ignore_other_diagnostics_and_kinds(make_client, tmp_path):
    typo = ENTRY.replace("x = x + lib_v", "x = x + lib_w")
    root = project(tmp_path, A_CR6=typo, Lib_crb=LIB)
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, typo)
    diags = client.wait_diagnostics(a, lambda d: bool(d))["diagnostics"]
    base = {"textDocument": {"uri": a.as_uri()}, "range": diags[0]["range"]}
    foreign = [{**diags[0], "source": "other"}]
    assert client.request("textDocument/codeAction", {
        **base, "context": {"diagnostics": foreign}}, 60) == []
    assert client.request("textDocument/codeAction", {
        **base, "context": {"diagnostics": diags, "only": ["refactor"]}}, 60) == []
    assert client.request("textDocument/codeAction", {
        **base, "context": {"diagnostics": []}}, 60) == []
    assert client.shutdown() == 0


# ---- H-04: message display ----------------------------------------------------------------


def test_display_text_redecodes_with_the_editor_encoding():
    mojibake = "Cannot open include file  CPU:É space.crb.".encode("utf-8").decode("latin-1")
    assert "Ã" in mojibake
    assert display_text(mojibake, "utf-8") == "Cannot open include file  CPU:É space.crb."
    assert display_text("plain ASCII", "utf-8") == "plain ASCII"
    assert display_text("café".encode("cp1252").decode("latin-1"), "cp1252") == "café"
    assert display_text("beyond ’", "utf-8") == "beyond ’"        # not compiler text
    assert display_text("x", "no-such-codec") == "x"


def test_utf8_include_name_shows_as_written(make_client, tmp_path):
    entry = ENTRY.replace('Include("CPU:Lib.crb")', 'Include("CPU:É space.crb")')
    root = project(tmp_path, A_CR6=entry)
    (root / "É space.crb").write_text(LIB, encoding="utf-8")
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, entry)
    params = client.wait_diagnostics(a, lambda d: any("include" in x["message"].lower() for x in d))
    messages = [d["message"] for d in params["diagnostics"] if "include" in d["message"].lower()]
    assert any("É space.crb" in m for m in messages), messages
    assert not any("Ã" in m for m in messages), messages
    assert client.shutdown() == 0


def test_feature_rename_reads_freshness_through_the_given_source(tmp_path):
    """LSP-R2a: the server passes the open buffers on top of the disk, so a buffer edited after
    the analysis is stale even though the analysed overlays and the disk still agree."""
    from lsprotocol import types
    from opencrbasic.analysis import AnalysisRequest, analyze
    from opencrbasic.lsp import features
    from opencrbasic.sources import DiskSource, OverlaySource

    root = project(tmp_path, A_CR6=ENTRY, Lib_crb=LIB)
    entry = root / "A.CR6"
    result = analyze(AnalysisRequest(entry))
    ctx = features.Ctx("cr6", "utf-16", "utf-8", {}, {})
    at = types.Position(8, 8)                                        # `x` in `x = x + lib_v`
    plain = features.rename_edit(ctx, [result], entry, at, "y", {})
    assert plain[1] is None
    fresh = OverlaySource(DiskSource(), {root / "Lib.crb": b"Public lib_v As Long\n"})
    assert features.rename_edit(ctx, [result], entry, at, "y", {}, source=fresh)[1] is None
    edited = OverlaySource(DiskSource(), {root / "Lib.crb": b"Public lib_w As Long\n"})
    _, refusal, _ = features.rename_edit(ctx, [result], entry, at, "y", {}, source=edited)
    assert refusal.code == "stale"
    assert features.prepare_rename(ctx, [result], entry, at, source=edited)[1].code == "stale"
