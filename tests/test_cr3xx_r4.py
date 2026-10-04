"""Real-compiler fixtures for the CR300/CR350 round-4 cascade rules (docs/checker.md "Round 4")."""
import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

NAMES = ["alias_dropped_decl", "dims_assign_target", "alias_need_closing", "decl_first_bad"]
CASES = [c for n in NAMES for c in json.loads(
    (Path(__file__).parent / "fixtures" / f"{n}.json").read_text(encoding="utf-8"))["cases"]]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_cr3xx_r4_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
