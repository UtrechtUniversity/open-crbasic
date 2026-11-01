"""Block-structure and placement observations (real-compiler output, docs/checker.md "Block placement
order"): unknown `CallTable` stop, table placement before end-of-program checks, `DataTable` body
expressions, `Scan` value errors as non-gating pass-2 findings, nested `Scan`, stale closer
slots, and `Return` placement."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

NAMES = (
    "unknown_calltable_stop",
    "table_structure_order",
    "datatable_body_expr",
    "scan_value_errors",
    "nested_scan",
    "stale_closer",
    "return_placement",
)
CASES = [
    case
    for name in NAMES
    for case in json.loads((Path(__file__).parent / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))["cases"]
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_block_placement_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
