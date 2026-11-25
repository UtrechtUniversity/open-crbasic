"""Routine parameters and Consts used before their declaration (round 71)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [
    case
    for name in ("r71_const_order.json", "r76_sub_parameter_scope.json", "r78_sub_values.json")
    for case in json.loads((FIXTURES / name).read_text())["cases"]
]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
        if "not_modelled" in case
        else case
        for case in CASES
    ],
    ids=lambda case: case["id"],
)
def test_const_order_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")
    result = compile_file(program, profiles.get(case["logger"]))
    ours = "\n".join(result.result_lines()).encode("latin-1", "replace").decode("utf-8", "replace")
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert ours == case["expected_stdout"]
