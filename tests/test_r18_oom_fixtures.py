import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [
    c
    for name in ("r18_inline_calltable", "r18_oom_auto")
    if (FIXTURES / f"{name}.json").exists()
    for c in json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["cases"]
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_r18_oom_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
