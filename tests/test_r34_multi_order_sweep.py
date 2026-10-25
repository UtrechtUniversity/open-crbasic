"""Round-34 reference observations (r34_multi_order_sweep): undeclared names at instruction positions outside the multi-report order, SDMSIO4 value counts, TimedControl constants."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "r34_multi_order_sweep.json").read_text())


@pytest.mark.parametrize("case", [
    pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
    if "not_modelled" in case else case for case in FIXTURE["cases"]], ids=lambda case: case["id"])
def test_r34_multi_order_sweep_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    for name, text in case.get("siblings", {}).items():
        (tmp_path / name).write_text(text, encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
