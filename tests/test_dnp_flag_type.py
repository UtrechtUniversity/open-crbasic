import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "r98_dnp_flag_type.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_dnp_flag_type_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    actual = "\n".join(result.result_lines())
    if reason := case.get("not_modelled"):
        assert actual != case["expected_stdout"], f"Remove the expected failure for {case['id']}"
        pytest.xfail(reason)
    assert actual == case["expected_stdout"]
