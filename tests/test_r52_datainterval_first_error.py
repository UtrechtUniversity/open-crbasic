"""Round-52 reference observations: negative DataInterval ahead of a table Reps error, constant destinations first."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [
    case
    for name in ("r52_datainterval_first_error.json", "r87_table_interval_names.json")
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
def test_r52_datainterval_first_error_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    for name, text in case.get("siblings", {}).items():
        (tmp_path / name).write_text(text, encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
