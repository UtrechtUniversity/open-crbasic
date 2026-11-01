import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "r13_logical_assign_targets.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_logical_assign_targets_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
