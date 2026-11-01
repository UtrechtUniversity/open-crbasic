"""Round-64 reference observations: leading-operator statements, overflowing WindVector Reps,
user Function calls of any arity and unterminated strings in them."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [case for name in ("r64_leading_op", "r64_windvector_reps", "r64_function_args",
                           "r64_function_string", "r64_sdm_delay")
         for case in json.loads((FIXTURES / f"{name}.json").read_text())["cases"]]


@pytest.mark.parametrize("case", [
    pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
    if "not_modelled" in case else case for case in CASES], ids=lambda case: case["id"])
def test_round64_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_bytes(case["source"].encode("utf-8"))
    result = compile_file(program, profiles.get(case["logger"]))
    ours = "\n".join(result.result_lines()).encode("latin-1", "replace").decode("utf-8", "replace")
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert ours == case["expected_stdout"]
