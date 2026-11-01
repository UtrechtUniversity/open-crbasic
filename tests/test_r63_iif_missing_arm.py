"""IIF omitted-final-arm type diagnostics and boundary controls (round 63)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

CASES = json.loads((Path(__file__).parent / "fixtures" / "r63_iif_missing_arm.json").read_text())["cases"]


@pytest.mark.parametrize("case", [
    pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
    if "not_modelled" in case else case for case in CASES], ids=lambda case: case["id"])
def test_iif_missing_arm_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    ours = "\n".join(result.result_lines()).encode("latin-1", "replace").decode("utf-8", "replace")
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert ours == case["expected_stdout"]
