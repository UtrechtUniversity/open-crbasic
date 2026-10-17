
import argparse
import json

import pytest

import fuzz


def test_load_seeds_collects_only_oracle_error(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    chm = tmp_path / "chm"
    repo.mkdir(); chm.mkdir()
    good = repo / "good.CR6"; bad = chm / "bad.CR6"
    good.write_text("good"); bad.write_text("bad")
    monkeypatch.setattr(fuzz, "CRBASIC_DIR", repo)
    monkeypatch.setattr(fuzz, "CHM_DIR", chm)

    def accepted(path, siblings, exe):
        if path == bad:
            raise fuzz.oracle.OracleError("reference crashed")
        return True

    monkeypatch.setattr(fuzz, "_accepted", accepted)
    errors = []
    seeds = fuzz.load_seeds(object(), tmp_path / "exe", 1, errors)
    assert [s.path for s in seeds] == [good]
    assert errors == [{
        "stage": "seed", "seed": "chm/bad.CR6", "source": str(bad),
        "message": "reference crashed",
    }]


def test_load_seeds_default_still_propagates(monkeypatch, tmp_path):
    chm = tmp_path / "chm"; chm.mkdir()
    (chm / "bad.CR6").write_text("bad")
    monkeypatch.setattr(fuzz, "CRBASIC_DIR", tmp_path / "missing")
    monkeypatch.setattr(fuzz, "CHM_DIR", chm)
    monkeypatch.setattr(
        fuzz, "_accepted",
        lambda *args: (_ for _ in ()).throw(fuzz.oracle.OracleError("failure")),
    )
    with pytest.raises(fuzz.oracle.OracleError, match="failure"):
        fuzz.load_seeds(object(), tmp_path / "exe", 1)


def test_run_logger_records_mutant_oracle_error_and_fails(monkeypatch, tmp_path):
    exe = tmp_path / "compiler" / "compiler.exe"; exe.parent.mkdir(); exe.write_bytes(b"exe")
    seed_path = tmp_path / "seed.CR6"; seed_path.write_text("BeginProg\nEndProg\n")
    profile = type("Profile", (), {"exe_name": exe.name, "name": "cr6"})()
    monkeypatch.setattr(fuzz.profiles, "get", lambda logger: profile)
    monkeypatch.setattr(fuzz, "REPO", tmp_path)
    monkeypatch.setattr(fuzz, "load_seeds", lambda *args: [fuzz.Seed("chm", seed_path, None)])
    monkeypatch.setattr(
        fuzz, "run_mutant",
        lambda *args, **kwargs: (_ for _ in ()).throw(fuzz.oracle.OracleError("runner died")),
    )
    args = argparse.Namespace(count=1, workers=1, seed=7, shrink_budget_total=0,
                              shrink_budget=0, max_per_signature=1, out=tmp_path / "out")
    assert not fuzz.run_logger("cr6", args)
    summary = json.loads((args.out / "summary-cr6.json").read_text())
    assert summary["counts"]["oracle_error"] == 1
    assert summary["saved"] == 0
    assert summary["oracle_errors"][0]["stage"] == "mutant"
    assert summary["oracle_errors"][0]["seed"] == "chm/seed.CR6"
    assert summary["oracle_errors"][0]["message"] == "runner died"


def test_run_logger_does_not_hide_arbitrary_mutant_failure(monkeypatch, tmp_path):
    exe = tmp_path / "compiler" / "compiler.exe"; exe.parent.mkdir(); exe.write_bytes(b"exe")
    seed_path = tmp_path / "seed.CR6"; seed_path.write_text("x")
    profile = type("Profile", (), {"exe_name": exe.name, "name": "cr6"})()
    monkeypatch.setattr(fuzz.profiles, "get", lambda logger: profile)
    monkeypatch.setattr(fuzz, "REPO", tmp_path)
    monkeypatch.setattr(fuzz, "load_seeds", lambda *args: [fuzz.Seed("chm", seed_path, None)])
    monkeypatch.setattr(
        fuzz, "run_mutant", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bug")),
    )
    args = argparse.Namespace(count=1, workers=1, seed=1, shrink_budget_total=0,
                              shrink_budget=0, max_per_signature=1, out=tmp_path / "out")
    with pytest.raises(ValueError, match="bug"):
        fuzz.run_logger("cr6", args)


@pytest.mark.parametrize("stage", ["shrink", "save"])
def test_run_logger_records_late_oracle_errors(monkeypatch, tmp_path, stage):
    exe = tmp_path / "compiler" / "compiler.exe"; exe.parent.mkdir(); exe.write_bytes(b"exe")
    seed_path = tmp_path / "seed.CR6"; seed_path.write_text("x")
    profile = type("Profile", (), {"exe_name": exe.name, "name": "cr6"})()
    monkeypatch.setattr(fuzz.profiles, "get", lambda logger: profile)
    monkeypatch.setattr(fuzz, "REPO", tmp_path)
    monkeypatch.setattr(fuzz, "load_seeds", lambda *args: [fuzz.Seed("chm", seed_path, None)])
    monkeypatch.setattr(fuzz, "run_mutant", lambda *args, **kwargs: (0, "accepted", 1, "rejected"))
    failure = lambda *args, **kwargs: (_ for _ in ()).throw(fuzz.oracle.OracleError("late failure"))
    monkeypatch.setattr(fuzz, "save_reproducer" if stage == "save" else "shrink", failure)
    args = argparse.Namespace(count=1, workers=1, seed=1, shrink_budget_total=1,
                              shrink_budget=1 if stage == "shrink" else 0,
                              max_per_signature=1, out=tmp_path / "out")
    assert not fuzz.run_logger("cr6", args)
    summary = json.loads((args.out / "summary-cr6.json").read_text())
    assert summary["oracle_errors"][0]["stage"] == stage
    assert summary["saved"] == 0
