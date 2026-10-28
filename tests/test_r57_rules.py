"""Round-57 reference observations: task-memory overflow of long delays, `SerialOpen` format and
CommsMode, and alias bounds (docs/checker.md, round-57 sections)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file


FIXTURES = ("r57_task_memory_delays", "r57_serialopen_format_commsmode", "r57_alias_bounds")
CASES = [case for name in FIXTURES
         for case in json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text())["cases"]]


@pytest.mark.parametrize("case", [
    pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
    if "not_modelled" in case else case for case in CASES], ids=lambda case: case["id"])
def test_r57_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
