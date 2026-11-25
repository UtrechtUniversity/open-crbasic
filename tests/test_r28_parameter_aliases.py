"""Round-28 reference observations: routine-parameter alias declarations."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "r28_parameter_aliases.json").read_text())


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
        if "not_modelled" in case
        else case
        for case in FIXTURE["cases"]
    ],
    ids=lambda case: case["id"],
)
def test_parameter_alias_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")

    result = compile_file(program, profiles.get(case["logger"]))

    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in FIXTURE["cases"]
        if case["id"].endswith(("-forward_const", "-forward_expr", "-after_prog_const"))
    ],
    ids=lambda case: case["id"],
)
def test_forward_parameter_dimension_stays_rejected(case, tmp_path):
    # Keep the rejection mandatory while exact header diagnostics remain expected failures.
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")
    result = compile_file(program, profiles.get(case["logger"]))

    assert not result.implementation_exceptions
    assert not result.ok
