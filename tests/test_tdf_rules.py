"""Fixture-backed TDF/table-list rules (docs/tdf-rules.md); fixtures from tools/tdf_fixture_gen.py."""

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from opencrbasic import parser, profiles, tdf  # noqa: E402
from opencrbasic.tables import build_tables  # noqa: E402

CASES = json.loads((ROOT / "tests" / "fixtures" / "tdf_rules.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_tdf_rules(case, tmp_path):
    p = tmp_path / "t.CR6"
    p.write_text(case["source"], newline="")
    program, diags = parser.parse_file(p)
    tables, lst, _ = build_tables(program, profiles.get("cr6"), dict(program.consts))
    assert tdf.serialize(tables) == base64.b64decode(case["tdf"])
    assert lst.encode("latin-1") == base64.b64decode(case["lst"])
