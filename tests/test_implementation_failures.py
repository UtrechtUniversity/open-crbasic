
import pytest

from opencrbasic import mode, profiles, signature
from opencrbasic.checker import structure
from opencrbasic.compiler import compile_file


def _program(tmp_path):
    path = tmp_path / "t.CR6"
    path.write_text("BeginProg\nEndProg\n", encoding="utf-8")
    return path


def test_checker_pass_failure_rejects_the_compile(monkeypatch, tmp_path):
    def broken(*args):
        raise RuntimeError("test failure")

    broken.__module__ = structure.__name__
    monkeypatch.setattr(structure, "check_pass1", broken)
    monkeypatch.setenv("OPENCRBASIC_STRICT", "0")
    result = compile_file(_program(tmp_path), profiles.get("cr6"))

    assert not result.ok
    assert result.implementation_exceptions == [
        "opencrbasic.checker.structure.broken: RuntimeError: test failure"
    ]
    assert result.result_lines() == [
        "t.CR6 -- Compile Failed!",
        "",
        "Internal error: unable to compile program.",
    ]


def test_checker_pass_failure_still_raises_in_strict_mode(monkeypatch, tmp_path):
    def broken(*args):
        raise RuntimeError("test failure")

    monkeypatch.setattr(structure, "check_pass1", broken)
    monkeypatch.setenv("OPENCRBASIC_STRICT", "1")

    with pytest.raises(RuntimeError, match="test failure"):
        compile_file(_program(tmp_path), profiles.get("cr6"))


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("mode", "opencrbasic.mode.decide: RuntimeError: mode failure"),
        ("signature", "signature.program_signature: RuntimeError: signature failure"),
    ],
)
def test_late_implementation_failure_rejects_the_compile(monkeypatch, tmp_path, target, expected):
    def broken(*args):
        raise RuntimeError(f"{target} failure")

    if target == "mode":
        broken.__module__ = mode.__name__
        broken.__name__ = "decide"
        monkeypatch.setattr(mode, "decide", broken)
    else:
        monkeypatch.setattr(signature, "program_signature", broken)
    monkeypatch.setenv("OPENCRBASIC_STRICT", "0")

    result = compile_file(_program(tmp_path), profiles.get("cr6"))

    assert not result.ok
    assert result.implementation_exceptions == [expected]
    assert result.result_lines()[-1] == "Internal error: unable to compile program."


@pytest.mark.parametrize("target", ["mode", "signature"])
def test_late_implementation_failure_raises_in_strict_mode(monkeypatch, tmp_path, target):
    def broken(*args):
        raise RuntimeError(f"{target} failure")

    if target == "mode":
        monkeypatch.setattr(mode, "decide", broken)
    else:
        monkeypatch.setattr(signature, "program_signature", broken)

    with pytest.raises(RuntimeError, match=f"{target} failure"):
        compile_file(_program(tmp_path), profiles.get("cr6"))
