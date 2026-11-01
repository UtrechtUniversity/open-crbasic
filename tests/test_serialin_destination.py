import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "serialin_destination.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_serialin_destination_observations(case, tmp_path):
    program = tmp_path / case["program_filename"]
    program.write_bytes(case["source"].encode("ascii"))

    result = compile_file(program, profiles.get(case["logger"]))
    actual_exit = 0 if result.ok else 1
    actual_stdout = "\n".join(result.result_lines())

    assert actual_exit == case["expected_exit"]
    assert actual_stdout == case["expected_stdout"]
