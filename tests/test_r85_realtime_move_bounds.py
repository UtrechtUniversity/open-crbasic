"""Reference verdicts for typed RealTime and Move destinations."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURE = Path(__file__).parent / "fixtures" / "r85_realtime_move_bounds.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_destination_bounds(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"])
    result = compile_file(program, profiles.get(case["logger"]))
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
