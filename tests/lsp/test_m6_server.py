"""opencrbasic checks and coverage notices in the language server (M6, PLAN.md "v1.0 milestones"):
channel, code and evidence link on the wire, unsaved includes, several entries, configuration
changes, stale results, programs with errors, formatting, and quick fixes matched by `data`."""

from pathlib import Path

import pytest

from .assertions import required
from .test_edits_server import FAST, apply, started
from .test_features_server import make_client  # noqa: F401

pytestmark = pytest.mark.server

CHECK, COVERAGE = "opencrbasic (check)", "opencrbasic (coverage)"
ENTRY = """Public D(3), X
Alias D(1)=Status
Include("CPU:Lib.crb")
BeginProg
  Scan(1,Sec,0,0)
    X = D(1)
  NextScan
EndProg
"""
LIB = """Public L As Long, S As String * 8
Sub Q
  L = 2000 + Mid(S,5,2)
EndSub
"""
CLEAN_LIB = "Public L As Long, S As String * 8\n"


def project(tmp_path: Path, **files: str) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8", newline="")
    return root


def codes(diags: list[dict], source: str = CHECK) -> list[str]:
    return [d["code"] for d in diags if d.get("source") == source]


def test_findings_carry_channel_code_and_evidence_link(make_client, tmp_path):
    root = project(tmp_path, **{"A.CR6": ENTRY, "Lib.crb": LIB})
    a, lib = root / "A.CR6", root / "Lib.crb"
    client = started(make_client, root)
    client.open(a)
    diags = client.wait_diagnostics(a, lambda d: "OCB001" in codes(d))["diagnostics"]
    ocb001 = next(d for d in diags if d.get("code") == "OCB001")
    assert ocb001["severity"] == 1 and ocb001["range"]["start"]["line"] == 1
    assert ocb001["codeDescription"]["href"].endswith("checker.md#alias-names-that-are-crbasic-names-round-33")
    assert not [d for d in diags if d.get("source") == "opencrbasic"]  # the reference accepts it
    included = client.wait_diagnostics(lib, lambda d: "OCB002" in codes(d))["diagnostics"]
    assert [d["range"]["start"]["line"] for d in included if d.get("code") == "OCB002"] == [2]
    assert required(next(d for d in included if d.get("code") == "OCB002"))["severity"] == 2
    assert codes(diags, COVERAGE) == []  # coverage notices are opt-in in the editor
    assert (
        client.request(
            "textDocument/codeAction",
            {  # quick fixes are for compiler diagnostics
                "textDocument": {"uri": a.as_uri()},
                "range": ocb001["range"],
                "context": {"diagnostics": [ocb001]},
            },
            60,
        )
        == []
    )
    assert client.shutdown() == 0


def test_unsaved_include_text_drives_findings(make_client, tmp_path):
    root = project(tmp_path, **{"A.CR6": ENTRY, "Lib.crb": CLEAN_LIB})
    a, lib = root / "A.CR6", root / "Lib.crb"
    client = started(make_client, root)
    client.open(a)
    client.wait_diagnostics(a, lambda d: "OCB001" in codes(d))
    client.open(lib, CLEAN_LIB, version=1)
    client.change(lib, LIB, 2)  # unsaved: the disk still has the clean library
    published = client.wait_diagnostics(lib, lambda d: "OCB002" in codes(d))
    assert published["version"] == 2
    before = len(client.publishes(lib))
    client.change(lib, CLEAN_LIB, 3)
    assert client.wait_diagnostics(lib, lambda d: "OCB002" not in codes(d), after=before)["version"] == 3
    assert client.shutdown() == 0


def test_findings_of_a_library_are_merged_per_entry(make_client, tmp_path):
    root = project(tmp_path, **{"A.CR6": ENTRY, "B.CR6": ENTRY, "Lib.crb": LIB})
    lib = root / "Lib.crb"
    client = started(make_client, root)
    client.open(lib)
    diags = client.wait_diagnostics(lib, lambda d: codes(d).count("OCB002") == 2)["diagnostics"]
    assert sorted(d["message"].split("]")[0] for d in diags if d.get("code") == "OCB002") == ["[A.CR6", "[B.CR6"]
    assert client.shutdown() == 0


def test_configuration_changes_clear_and_restore_findings(make_client, tmp_path):
    root = project(tmp_path, **{"A.CR6": ENTRY, "Lib.crb": CLEAN_LIB})
    a = root / "A.CR6"
    client = started(make_client, root, watched_files=True)
    client.open(a)
    client.wait_diagnostics(a, lambda d: "OCB001" in codes(d))
    # the LSP setting
    before = len(client.publishes(a))
    client.settings = {**FAST, "checks": {"OCB001": "off"}}
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_diagnostics(a, lambda d: "OCB001" not in codes(d), after=before)
    before = len(client.publishes(a))
    client.settings = dict(FAST)
    client.notify("workspace/didChangeConfiguration", {"settings": None})
    client.wait_diagnostics(a, lambda d: "OCB001" in codes(d), after=before)
    # `opencrbasic.toml` appearing, then going away
    toml = root / "opencrbasic.toml"
    toml.write_text('[checks]\nOCB001 = "off"\n', encoding="utf-8")
    before = len(client.publishes(a))
    client.watched((toml, 1))
    client.wait_diagnostics(a, lambda d: "OCB001" not in codes(d), after=before)
    toml.write_text('[checks]\nOCB001 = "nonsense"\n', encoding="utf-8")
    before = len(client.publishes(a))
    client.watched((toml, 2))
    invalid = client.wait_diagnostics(
        a, lambda d: any("Check configuration ignored" in x["message"] for x in d), after=before
    )["diagnostics"]
    assert codes(invalid) == []  # an invalid configuration runs no checks (as `crbc`)
    toml.unlink()
    before = len(client.publishes(a))
    client.watched((toml, 3))
    client.wait_diagnostics(a, lambda d: "OCB001" in codes(d), after=before)
    assert client.shutdown() == 0


def test_findings_follow_the_document_version(make_client, tmp_path):
    root = project(tmp_path, **{"A.CR6": ENTRY, "Lib.crb": CLEAN_LIB})
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, version=1)
    client.wait_diagnostics(a, lambda d: "OCB001" in codes(d))
    before = len(client.publishes(a))
    client.change(a, ENTRY.replace("Alias D(1)=Status", "Alias D(1)=Stat"), 2)
    published = client.wait_diagnostics(a, lambda d: "OCB001" not in codes(d), after=before)
    assert published["version"] == 2
    assert all(p["version"] == 2 for p in client.publishes(a)[before:])  # never an older result
    assert client.shutdown() == 0


def test_a_program_with_errors_runs_no_checks(make_client, tmp_path):
    broken = ENTRY.replace("X = D(1)", "X = D(1) +")
    root = project(tmp_path, **{"A.CR6": broken, "Lib.crb": LIB})
    a, lib = root / "A.CR6", root / "Lib.crb"
    client = started(make_client, root)
    client.open(a)
    client.open(lib)
    diags = client.wait_diagnostics(a, lambda d: any(x.get("source") == "opencrbasic" for x in d))["diagnostics"]
    assert codes(diags) == []
    assert codes(client.wait_diagnostics(lib)["diagnostics"]) == []
    assert client.shutdown() == 0


def test_a_check_error_does_not_disable_formatting(make_client, tmp_path):
    messy = ENTRY.replace("    X = D(1)", "X = D(1)")
    root = project(tmp_path, **{"A.CR6": messy, "Lib.crb": CLEAN_LIB})
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a)
    client.wait_diagnostics(a, lambda d: "OCB001" in codes(d))  # an error under `--strict`
    edits = client.request(
        "textDocument/formatting",
        {"textDocument": {"uri": a.as_uri()}, "options": {"tabSize": 2, "insertSpaces": True}},
        60,
    )
    assert apply(messy, edits) == ENTRY
    assert client.shutdown() == 0


def test_quick_fixes_match_the_published_data_not_the_text(make_client, tmp_path):
    typo = ENTRY.replace("X = D(1)", "X = Dx(1)")
    root = project(tmp_path, **{"A.CR6": typo, "Lib.crb": CLEAN_LIB})
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, typo, version=4)
    diags = client.wait_diagnostics(a, lambda d: any("Invalid symbol" in x["message"] for x in d))["diagnostics"]
    target = next(d for d in diags if "Invalid symbol" in d["message"])
    assert set(target["data"]) == {"entry", "result", "index"}
    renamed = {**target, "message": "a client may rewrite or translate the text"}
    actions = client.request(
        "textDocument/codeAction",
        {"textDocument": {"uri": a.as_uri()}, "range": target["range"], "context": {"diagnostics": [renamed]}},
        60,
    )
    assert [x["title"] for x in actions][:1] == ["Change to `D`"]
    assert client.shutdown() == 0


def test_coverage_notices_are_hints_when_enabled(make_client, tmp_path):
    root = project(tmp_path, **{"A.CR6": ENTRY, "Lib.crb": CLEAN_LIB})
    a = root / "A.CR6"
    client = started(make_client, root, {"coverageNotices": True})
    client.open(a)
    diags = client.wait_diagnostics(a, lambda d: bool(codes(d, COVERAGE)))["diagnostics"]
    notice = next(d for d in diags if d.get("source") == COVERAGE)
    assert notice["severity"] == 4 and notice["code"] == "notice"
    assert notice["message"].startswith("opencrbasic does not model") and "SCAN" in notice["message"]
    assert notice["codeDescription"]["href"].endswith("inventory.md#coverage-notices")
    assert client.shutdown() == 0


def test_a_diagnostic_of_an_older_result_gets_no_quick_fix(make_client, tmp_path):
    first = ENTRY.replace("X = D(1)", "X = Dx(1)")
    second = ENTRY.replace("X = D(1)", "X = Dy(1)")
    root = project(tmp_path, **{"A.CR6": first, "Lib.crb": CLEAN_LIB})
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a, first, version=1)
    old = next(
        d
        for d in client.wait_diagnostics(a, lambda d: any("Dx" in x["message"] for x in d))["diagnostics"]
        if "Dx" in d["message"]
    )
    client.change(a, second, 2)
    new = next(
        d
        for d in client.wait_diagnostics(a, lambda d: any("Dy" in x["message"] for x in d))["diagnostics"]
        if "Dy" in d["message"]
    )
    assert old["data"]["index"] == new["data"]["index"] and old["data"]["result"] != new["data"]["result"]

    def actions(diag):
        return client.request(
            "textDocument/codeAction",
            {"textDocument": {"uri": a.as_uri()}, "range": diag["range"], "context": {"diagnostics": [diag]}},
            60,
        )

    assert actions(old) == []  # it would have fixed `Dy` with `Dx`'s action
    assert [x["title"] for x in actions(new)][:1] == ["Change to `D`"]
    assert client.shutdown() == 0


def test_configuration_above_the_workspace_folder_is_not_used(make_client, tmp_path):
    (tmp_path / "opencrbasic.toml").write_text('[checks]\nOCB001 = "off"\n', encoding="utf-8")
    root = project(tmp_path, **{"A.CR6": ENTRY, "Lib.crb": CLEAN_LIB})
    a = root / "A.CR6"
    client = started(make_client, root)
    client.open(a)
    client.wait_diagnostics(a, lambda d: "OCB001" in codes(d))  # no watcher reaches above the folder
    assert client.shutdown() == 0
