import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = ["illegal_dimension_echo", "dims_not_declared",
            "alias_consttable_cascade", "alias_undeclared_cascade",
            "arity_name_order",
            "block_closer_suppression",
            "calltable_first_only", "calltable_hidden", "dims_state"]
CASES = [c for name in FIXTURES for c in json.loads(
    (Path(__file__).parent / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))["cases"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_cr3xx_cascade_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
