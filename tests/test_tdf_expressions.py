"""Reference-compiler diagnostics and exact user-table TDF bytes for expression sources."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles, tdf
from opencrbasic.compiler import compile_file

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tdf_expressions.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_expression_source_observations(case, tmp_path):
    path = tmp_path / "t.CR6"
    path.write_text(case["source"], encoding="utf-8")
    result = compile_file(path, profiles.get(case["logger"]))
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
    if "expected_tdf" in case:
        tables = [table for table in result.tables if table.name.upper() in case["table_names"]]
        assert tdf.serialize(tables).hex() == case["expected_tdf"]
