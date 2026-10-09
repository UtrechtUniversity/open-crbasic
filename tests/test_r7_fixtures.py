import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [c for f in sorted(FIXTURES.glob("r7_*.json")) for c in json.loads(f.read_text(encoding="utf-8"))["cases"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_r7_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
