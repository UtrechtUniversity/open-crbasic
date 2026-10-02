from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

import difftest
import fuzz
import fuzz_replay


def _row(cls: str | None) -> dict:
    return {"status": "exact" if cls is None else "mismatch", "class": cls}


def _replay_row(cls: str | None) -> dict:
    return {
        "file": "case.CR6", "logger": "cr6", "recorded_class": cls,
        "now_class": cls, "status": "fixed" if cls is None else f"still {cls}",
        "our_exit": 0, "their_exit": 0, "ours": "same", "theirs": "same",
    }


def test_equal_output_with_different_exit_is_not_exact():
    assert fuzz.classify(0, "same", 1, "same") == "false_accept"
    assert fuzz.classify(1, "same", 0, "same") == "false_reject"


def test_fuzz_seed_oracle_failure_propagates(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise fuzz.oracle.OracleError("runner failed")

    monkeypatch.setattr(fuzz.oracle, "run_oracle", fail)
    with pytest.raises(fuzz.oracle.OracleError, match="runner failed"):
        fuzz._accepted(tmp_path / "case.CR6", None, tmp_path / "compiler.exe")


@pytest.mark.parametrize(
    ("gate", "cls", "failed"),
    [
        ("report", "false_reject", False),
        ("no-regressions", "false_reject", True),
        ("no-regressions", "implementation_exception", True),
        ("exact", "false_accept", True),
        ("exact", None, False),
    ],
)
def test_difftest_gate_status(gate, cls, failed):
    assert difftest.gate_failed([_row(cls)], gate) is failed


@pytest.mark.parametrize("gate", ["report", "no-regressions", "exact"])
def test_difftest_oracle_error_fails_every_gate(gate):
    assert difftest.gate_failed([_row("oracle_error")], gate)


@pytest.mark.parametrize("gate", ["report", "no-regressions", "exact"])
def test_difftest_empty_input_fails_every_gate(gate):
    assert difftest.gate_failed([], gate)


def test_difftest_keeps_bad_oracle_row_without_losing_good_row(monkeypatch, tmp_path):
    bad = tmp_path / "bad.CR6"
    good = tmp_path / "good.CR6"

    def fake_theirs(path, profile, siblings):
        if path == bad:
            raise difftest.oracle.OracleError("runner emitted no compiler verdict")
        return 0, "good.CR6 -- Compiled OK."

    monkeypatch.setattr(difftest, "theirs", fake_theirs)
    monkeypatch.setattr(difftest, "ours", lambda path, profile: (0, "good.CR6 -- Compiled OK."))
    profile = object()
    rows = [
        difftest.compare_one(("cli", bad, None), profile),
        difftest.compare_one(("cli", good, None), profile),
    ]

    assert rows[0] == {
        "group": "cli", "file": str(bad), "status": "oracle_error", "class": "oracle_error",
        "ours": "", "theirs": "runner emitted no compiler verdict",
        "our_exit": None, "their_exit": None,
    }
    assert rows[1]["status"] == "exact"
    assert rows[1]["class"] is None
    assert difftest.summarise(rows)["cli"] == {
        "exact": 1, "verdict": 0, "mismatch": 0, "oracle_error": 1,
        "implementation_exception": 0,
    }
    assert difftest.gate_failed(rows, "report")


def test_difftest_does_not_hide_arbitrary_oracle_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        difftest, "theirs", lambda *args: (_ for _ in ()).throw(ValueError("implementation bug"))
    )
    with pytest.raises(ValueError, match="implementation bug"):
        difftest.compare_one(("cli", tmp_path / "case.CR6", None), object())


def test_replay_no_regressions_rejects_preexisting_false_reject():
    assert fuzz_replay.gate_failed([_replay_row("false_reject")], "no-regressions")
    assert fuzz_replay.gate_failed([_replay_row("implementation_exception")], "no-regressions")
    assert not fuzz_replay.gate_failed([_replay_row("false_accept")], "no-regressions")
    assert fuzz_replay.gate_failed([_replay_row("message_diff")], "exact")


@pytest.mark.parametrize("gate", ["report", "no-regressions", "exact"])
def test_replay_oracle_error_fails_every_gate(gate):
    assert fuzz_replay.gate_failed([_replay_row("oracle_error")], gate)


def test_replay_oracle_error_becomes_row(monkeypatch, tmp_path):
    path = tmp_path / "false_reject" / "case.CR6"
    path.parent.mkdir()
    path.write_text("BeginProg\nEndProg\n")
    path.with_suffix(".json").write_text(json.dumps({
        "logger": "cr6", "class": "false_reject",
    }))

    def fail(*args, **kwargs):
        raise fuzz_replay.fuzz.oracle.OracleError("runner failed")

    monkeypatch.setattr(fuzz_replay.fuzz, "run_mutant", fail)
    row = fuzz_replay.replay_one(path)
    assert row["recorded_class"] == "false_reject"
    assert row["now_class"] == "oracle_error"
    assert row["status"] == "oracle error: runner failed"
    assert row["our_exit"] is None and row["their_exit"] is None
    assert row["ours"] == ""
    assert row["theirs"] == "runner failed"


def test_replay_json_summary_includes_oracle_error(tmp_path):
    path = tmp_path / "replay.json"
    fuzz_replay.write_json_report(path, [_replay_row("oracle_error")])
    report = json.loads(path.read_text())
    assert report["summary"] == {"total": 1, "counts": {"oracle_error": 1}}


def test_prune_fixed_keeps_oracle_error(monkeypatch, tmp_path):
    path = tmp_path / "cr6" / "false_reject" / "case.CR6"
    path.parent.mkdir(parents=True)
    path.write_text("BeginProg\nEndProg\n")
    path.with_suffix(".json").write_text("{}")
    row = _replay_row("oracle_error")
    row.update({"file": str(path), "logger": "cr6", "recorded_class": "false_reject"})
    monkeypatch.setattr(fuzz_replay, "replay_one", lambda *args, **kwargs: row)

    assert fuzz_replay.prune_fixed([path], workers=1) == 0
    assert path.is_file()
    assert path.with_suffix(".json").is_file()


def test_replay_does_not_hide_arbitrary_failure(monkeypatch, tmp_path):
    path = tmp_path / "false_accept" / "case.CR6"
    path.parent.mkdir()
    path.write_text("BeginProg\nEndProg\n")
    path.with_suffix(".json").write_text(json.dumps({"logger": "cr6", "class": "false_accept"}))
    monkeypatch.setattr(
        fuzz_replay.fuzz, "run_mutant",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("implementation bug")),
    )
    with pytest.raises(ValueError, match="implementation bug"):
        fuzz_replay.replay_one(path)


def test_replay_json_report_shape(tmp_path):
    path = tmp_path / "reports" / "replay.json"
    rows = [_replay_row(None), _replay_row("false_reject"), _replay_row("false_reject")]
    fuzz_replay.write_json_report(path, rows)
    report = json.loads(path.read_text())
    assert report["summary"] == {"total": 3, "counts": {"exact": 1, "false_reject": 2}}
    assert report["rows"] == rows


def test_difftest_groups_exclude_historical_examples():
    assert set(difftest.GROUPS) == {"repo", "chm"}


def test_difftest_rejects_historical_examples_group():
    result = subprocess.run(
        [sys.executable, str(Path(difftest.__file__)), "--group", "examples"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "invalid choice: 'examples'" in result.stderr


def test_difftest_display_path_accepts_outside_repo(tmp_path):
    path = tmp_path / "case.CR6"
    assert difftest.display_path(path) == str(path.resolve())
