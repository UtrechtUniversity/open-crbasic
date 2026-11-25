"""Reference verdicts for indexed constants (round 79)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [
    case
    for name in ("r79_indexed_constants.json", "r82_indexed_declarations.json")
    for case in json.loads((FIXTURES / name).read_text())["cases"]
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_reference_verdict(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], newline="")
    result = compile_file(program, profiles.get(case["logger"]))
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
