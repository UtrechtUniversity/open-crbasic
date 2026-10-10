"""Auto-allocated table sizes (`Size = -1`) against sizes read from the real CR6Comp.exe's TDF."""

import json
from pathlib import Path

import pytest

from opencrbasic import parser, profiles, tables

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "auto_size.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_auto_size_matches_real_compiler(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")
    parsed, _ = parser.parse_file(program)
    built, _, _ = tables.build_tables(parsed, profiles.get("cr6"), dict(parsed.consts))
    assert {t.name: t.size for t in built if t.name.startswith("T")} == case["expected_sizes"]
