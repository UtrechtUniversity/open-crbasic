"""Round-58 reference observations: the table walk, WindVector options, declaration dimensions,
`SerialOpen` formats and recovery, never-resolved arguments (docs/checker.md, round-58 sections)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = (
    "r58_table_walk",
    "r58_windvector_options",
    "r58_declaration_dims",
    "r58_serialopen_and_names",
    "r58_inline_tier_c",
)
CASES = [
    case
    for name in FIXTURES
    for case in json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text())["cases"]
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
def test_r58_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")

    result = compile_file(program, profiles.get(case["logger"]))

    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
