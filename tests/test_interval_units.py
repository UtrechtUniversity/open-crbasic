import json
from pathlib import Path

import pytest

from opencrbasic import parser, profiles
from opencrbasic.compiler import compile_file
from opencrbasic.tables import build_tables

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "r20_interval_units.json").read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_interval_units_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]


@pytest.mark.parametrize("case", [c for c in CASES if "expected_interval_lines" in c], ids=lambda c: c["id"])
def test_interval_units_table_list(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    parsed, _ = parser.parse_file(program)
    tables, table_list, _ = build_tables(parsed, profiles.get(case["logger"]), dict(parsed.consts))

    assert [line for line in table_list.splitlines() if line.startswith("Interval:")] == case["expected_interval_lines"]
    test_table = next(t for t in tables if t.name == "Test")
    assert test_table.size == case["expected_test_records"]
