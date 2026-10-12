import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
# Multi-file fixtures (cases with "siblings") run in test_include_validation.py.
_FILES = [json.loads(f.read_text(encoding="utf-8"))["cases"] for f in sorted(FIXTURES.glob("r15_*.json"))]
CASES = [c for cases in _FILES if not any("siblings" in c for c in cases) for c in cases]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_r15_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
