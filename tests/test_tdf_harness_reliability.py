
import base64
import json
from types import SimpleNamespace

import oracle
import pytest
import tdf_probe
import tdftest


@pytest.fixture
def staged(tmp_path, monkeypatch):
    program = tmp_path / "case.CR6"
    compiler = tmp_path / "CR6Comp.exe"
    siblings = tmp_path / "siblings"
    program.write_text("BeginProg\nEndProg\n")
    compiler.write_bytes(b"compiler")
    siblings.mkdir()
    monkeypatch.setattr(tdftest, "TDF_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(tdftest, "_runner_command", lambda exe: (["runner", str(exe)], {"TOKEN": "yes"}))
    return program, compiler, siblings


def cache_path(program, compiler, siblings):
    return tdftest.TDF_CACHE_DIR / f"{tdftest._cache_key(compiler, program, siblings)}.json"


def test_valid_historical_cache_infers_success_exit(staged, monkeypatch):
    program, compiler, siblings = staged
    path = cache_path(program, compiler, siblings)
    path.parent.mkdir()
    path.write_text(json.dumps({
        "ok": True, "stdout": "case.CR6 -- Compiled OK.",
        "tdf": base64.b64encode(b"tdf").decode(), "lst": base64.b64encode(b"lst").decode(),
    }))
    monkeypatch.setattr(tdftest.subprocess, "run", lambda *a, **k: pytest.fail("runner called"))
    got = tdftest.run_oracle_tdf(program, siblings, compiler)
    assert got["exit"] == 0 and got["tdf"] == b"tdf" and got["lst"] == b"lst"


def test_invalid_old_cache_is_not_usable_cache_only(staged):
    program, compiler, siblings = staged
    path = cache_path(program, compiler, siblings)
    path.parent.mkdir()
    path.write_text(json.dumps({"ok": False, "stdout": "", "tdf": None, "lst": None}))
    with pytest.raises(tdftest.NotCached):
        tdftest.run_oracle_tdf(program, siblings, compiler, cache_only=True)


def test_invalid_old_cache_reruns_and_replaces_with_exit_stderr(staged, monkeypatch):
    program, compiler, siblings = staged
    path = cache_path(program, compiler, siblings)
    path.parent.mkdir()
    path.write_text(json.dumps({"ok": False, "stdout": "", "tdf": None, "lst": None}))

    def run(*args, **kwargs):
        (kwargs["cwd"] / "out.tdf").write_bytes(b"tdf")
        (kwargs["cwd"] / "out.lst").write_bytes(b"lst")
        assert kwargs["env"] == {"TOKEN": "yes"}
        return SimpleNamespace(returncode=0, stdout=b"case.CR6 -- Compiled OK.\r\n", stderr=b"note\r\n")

    monkeypatch.setattr(tdftest.subprocess, "run", run)
    got = tdftest.run_oracle_tdf(program, siblings, compiler)
    saved = json.loads(path.read_text())
    assert got["exit"] == saved["exit"] == 0
    assert got["stderr"] == saved["stderr"] == "note\n"


def test_invalid_new_result_raises_and_is_not_cached(staged, monkeypatch):
    program, compiler, siblings = staged
    monkeypatch.setattr(tdftest.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=5, stdout=b"", stderr=b"Wine debugger"
    ))
    with pytest.raises(oracle.OracleError, match="no usable compiler verdict"):
        tdftest.run_oracle_tdf(program, siblings, compiler)
    assert not cache_path(program, compiler, siblings).exists()


@pytest.mark.parametrize("gate", ["report", "exact"])
def test_no_compares_fails_every_gate(gate):
    assert tdftest.gate_failed(gate, compared=0, mismatches=0, missing_artifacts=0,
                               oracle_errors=0)


def test_exact_gate_checks_mismatch_and_missing_artifact():
    assert not tdftest.gate_failed("report", 1, 1, 1, 0)
    assert tdftest.gate_failed("exact", 1, 1, 0, 0)
    assert tdftest.gate_failed("exact", 1, 0, 1, 0)
    assert tdftest.gate_failed("report", 1, 0, 0, 1)


def test_tdf_probe_passes_runner_environment(tmp_path, monkeypatch):
    compiler = tmp_path / "compiler.exe"
    snippet = tmp_path / "snippet.CR6"
    compiler.write_bytes(b"exe")
    snippet.write_text("BeginProg\nEndProg\n")
    seen = {}
    monkeypatch.setattr(tdf_probe, "_runner_command", lambda exe: (["runner"], {"SPECIAL": "env"}))

    def run(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(tdf_probe.subprocess, "run", run)
    monkeypatch.setattr("sys.argv", ["tdf_probe.py", str(snippet), "--exe", str(compiler)])
    assert tdf_probe.main() == 0
    assert seen["env"] == {"SPECIAL": "env"}
