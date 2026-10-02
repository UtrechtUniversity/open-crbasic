from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import oracle


@pytest.fixture
def staged(tmp_path, monkeypatch):
    program = tmp_path / "case.CR6"
    compiler = tmp_path / "CR6Comp.exe"
    program.write_text("BeginProg\nEndProg\n")
    compiler.write_bytes(b"compiler")
    cache = tmp_path / "cache"
    monkeypatch.setattr(oracle, "CACHE_DIR", cache)
    monkeypatch.setattr(oracle, "_runner_command", lambda path: (["runner", str(path)], {}))
    return program, compiler, cache


def result(returncode: int, stdout: bytes, stderr: bytes = b""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_builtin_wine_runner_disables_gui_drivers(monkeypatch, tmp_path):
    compiler = tmp_path / "CR6Comp.exe"
    monkeypatch.delenv("CR6COMP_RUNNER", raising=False)
    monkeypatch.delenv("WINEDLLOVERRIDES", raising=False)
    monkeypatch.setattr(oracle.sys, "platform", "darwin")
    monkeypatch.setattr(oracle, "_find_wine", lambda: "/test/wine")

    argv, env = oracle._runner_command(compiler)

    assert argv == ["/test/wine", str(compiler)]
    assert env["WINEDLLOVERRIDES"] == "winemac.drv,winex11.drv,winewayland.drv=d"


def test_builtin_wine_runner_preserves_explicit_gui_override(monkeypatch, tmp_path):
    compiler = tmp_path / "CR6Comp.exe"
    monkeypatch.delenv("CR6COMP_RUNNER", raising=False)
    monkeypatch.setenv("WINEDLLOVERRIDES", "custom=value")
    monkeypatch.setattr(oracle.sys, "platform", "linux")
    monkeypatch.setattr(oracle, "_find_wine", lambda: "/test/wine")

    _, env = oracle._runner_command(compiler)

    assert env["WINEDLLOVERRIDES"] == "custom=value"


def test_explicit_runner_does_not_inject_wine_gui_environment(monkeypatch, tmp_path):
    compiler = tmp_path / "CR6Comp.exe"
    monkeypatch.setenv("CR6COMP_RUNNER", "custom-runner --flag")
    monkeypatch.delenv("WINEDLLOVERRIDES", raising=False)
    monkeypatch.setattr(oracle.sys, "platform", "darwin")

    argv, env = oracle._runner_command(compiler)

    assert argv == ["custom-runner", "--flag", str(compiler)]
    assert "WINEDLLOVERRIDES" not in env


def test_native_windows_runner_does_not_inject_wine_gui_environment(monkeypatch, tmp_path):
    compiler = tmp_path / "CR6Comp.exe"
    monkeypatch.delenv("CR6COMP_RUNNER", raising=False)
    monkeypatch.delenv("WINEDLLOVERRIDES", raising=False)
    monkeypatch.setattr(oracle.sys, "platform", "win32")

    argv, env = oracle._runner_command(compiler)

    assert argv == [str(compiler)]
    assert "WINEDLLOVERRIDES" not in env


@pytest.mark.parametrize(
    "record",
    [
        {"exit": 0, "stdout": "case.CR6 -- Compiled in SequentialMode."},
        {"exit": 1, "stdout": "case.CR6 -- Compile Failed!\n\nline 1: bad"},
    ],
)
def test_valid_result_accepts_real_success_and_failure(record):
    assert oracle._valid_result(record, "case.CR6")


@pytest.mark.parametrize(
    "record",
    [
        {"exit": 1, "stdout": ""},
        {"exit": 5, "stdout": "case.CR6 -- Compile Failed!"},
        {"exit": 0, "stdout": "other.CR6 -- Compiled OK."},
    ],
)
def test_valid_result_rejects_runner_failures(record):
    assert not oracle._valid_result(record, "case.CR6")


@pytest.mark.parametrize(
    "completed",
    [
        result(1, b"", b"runner unavailable"),
        result(5, b"WineDbg attached", b"debugger failure"),
    ],
)
def test_invalid_runner_result_raises_and_is_not_cached(staged, monkeypatch, completed):
    program, compiler, cache = staged
    monkeypatch.setattr(oracle.subprocess, "run", lambda *args, **kwargs: completed)

    with pytest.raises(oracle.OracleError, match="no usable compiler verdict"):
        oracle.run_oracle(program, compiler_path=compiler)

    assert not list(cache.glob("*.json"))


def test_invalid_old_cache_is_rerun_and_replaced(staged, monkeypatch):
    program, compiler, cache = staged
    cache.mkdir()
    cache_path = cache / f"{oracle._cache_key(compiler, program, None)}.json"
    cache_path.write_text(json.dumps({"file": program.name, "exit": 1, "stdout": "", "cmple": None}))
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return result(0, b"case.CR6 -- Compiled OK.\r\n", b"runner note\r\n")

    monkeypatch.setattr(oracle.subprocess, "run", run)
    record = oracle.run_oracle(program, compiler_path=compiler)

    assert len(calls) == 1
    assert record["exit"] == 0
    assert record["stderr"] == "runner note\n"
    assert json.loads(cache_path.read_text()) == record


def test_valid_old_cache_is_reused_without_runner(staged, monkeypatch):
    program, compiler, cache = staged
    cache.mkdir()
    cache_path = cache / f"{oracle._cache_key(compiler, program, None)}.json"
    cached = {"file": program.name, "exit": 1,
              "stdout": "case.CR6 -- Compile Failed!\n\nline 1: bad", "cmple": None}
    cache_path.write_text(json.dumps(cached))

    def unexpected(*args, **kwargs):
        raise AssertionError("valid cache should avoid the runner")

    monkeypatch.setattr(oracle.subprocess, "run", unexpected)
    assert oracle.run_oracle(program, compiler_path=compiler) == cached


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(["runner"], 60),
        OSError("cannot execute runner"),
    ],
)
def test_subprocess_failures_become_oracle_error(staged, monkeypatch, failure):
    program, compiler, cache = staged

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(oracle.subprocess, "run", fail)
    with pytest.raises(oracle.OracleError, match="reference compiler runner failed"):
        oracle.run_oracle(program, compiler_path=compiler)
    assert not list(cache.glob("*.json"))


def test_stderr_is_retained(staged, monkeypatch):
    program, compiler, _ = staged
    monkeypatch.setattr(
        oracle.subprocess,
        "run",
        lambda *args, **kwargs: result(
            1, b"case.CR6 -- Compile Failed!\r\n\r\nline 1: bad\r\n", b"wine warning\r\n"
        ),
    )
    record = oracle.run_oracle(program, compiler_path=compiler)
    assert record["stderr"] == "wine warning\n"
