import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

_FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = {
    "cases": [
        c
        for name in ("struct_index", "struct_index_call")
        for c in json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["cases"]
    ]
}


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_struct_index_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
