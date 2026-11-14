"""Reference verdicts recorded while fixing the review findings of rounds 76-149 (round 150)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
NAMES = (
    "r150_const_initializer_arguments.json",
    "r150_serialopen_non_finite.json",
    "r150_settings_table_properties.json",
)
CASES = [
    pytest.param(case, id=case["id"], marks=[pytest.mark.xfail(reason=case["not_modelled"], strict=True)])
    if "not_modelled" in case
    else pytest.param(case, id=case["id"])
    for name in NAMES
    for case in json.loads((FIXTURES / name).read_text(encoding="utf-8"))["cases"]
]


@pytest.mark.parametrize("case", CASES)
def test_reference_verdict(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
