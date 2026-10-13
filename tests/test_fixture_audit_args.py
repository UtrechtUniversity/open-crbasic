import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fixture_audit  # noqa: E402


def test_defaults_use_cache_single_job():
    a = fixture_audit.parse_args(["a.json", "b.json"])
    assert a.fixtures == ["a.json", "b.json"] and not a.no_cache and a.jobs == 1


def test_flags():
    a = fixture_audit.parse_args(["--no-cache", "--jobs", "4", "a.json"])
    assert a.no_cache and a.jobs == 4


def test_differs_ignores_trailing_newlines():
    case = {"expected_exit": 1, "expected_stdout": "x\n"}
    assert not fixture_audit.differs(case, {"exit": 1, "stdout": "x"})
    assert fixture_audit.differs(case, {"exit": 0, "stdout": "x"})
