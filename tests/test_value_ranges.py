"""Oracle-backed instruction-value and GPS-structure range observations."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [
    case
    for name in ("value_ranges.json", "r86_paneltemp_channels.json", "r88_event_pulse_ranges.json")
    for case in json.loads((FIXTURES / name).read_text())["cases"]
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_value_range_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"])
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
