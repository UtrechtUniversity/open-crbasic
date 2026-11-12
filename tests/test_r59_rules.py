"""Round-59 reference observations: realistic first errors of the M2 review (constant
destinations in pass 1, bridge Range codes, declarations, routine headers, `!`, nested tables,
dotted bases; docs/checker.md, round-59 sections)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = (
    "r59_destinations",
    "r59_measurement",
    "r59_declarations",
    "r59_tables_names",
    "r59_conditions",
    "r95_optional_params",
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
def test_r59_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_bytes(case["source"].encode("utf-8"))

    result = compile_file(program, profiles.get(case["logger"]))

    # the CLI writes Latin-1, the fixture holds the reference's bytes read as UTF-8
    ours = "\n".join(result.result_lines()).encode("latin-1", "replace").decode("utf-8", "replace")
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert ours == case["expected_stdout"]
