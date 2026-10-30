"""OCB002-OCB006 (`checks.py`): each form the reference accepts on all four loggers fires, each
accepted control does not (`tests/fixtures/m3_check_evidence.json`, oracle 2026-10-30)."""

import json
from pathlib import Path

import pytest

from opencrbasic import checks, profiles
from opencrbasic.compiler import compile_file

CASES = json.loads((Path(__file__).parent / "fixtures" / "m3_check_evidence.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['logger']}-{c['code'] or 'control'}-{c['body']}")
def test_check_evidence(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_bytes(case["source"].encode("utf-8"))
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"] == 0   # the reference accepts the form
    codes = [f.code for f in checks.run(result.program_ast, result.profile)]
    assert codes == ([case["code"]] if case["code"] else [])


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
def test_firnmelt_programs_have_no_findings(logger):
    """Each firing on `crbasic/` programs is fixed or justified (PLAN M3): there are none."""
    root = Path(__file__).resolve().parents[2] / "crbasic"
    for path in sorted(root.glob("*.CR6")):
        result = compile_file(path, profiles.get(logger), display_name=path.name, provenance=False)
        if result.ok:
            assert [f for f in checks.run(result.program_ast, result.profile)] == [], path.name


def test_default_severities_are_documented():
    assert checks.DEFAULT_SEVERITY == {"OCB001": "error", "OCB002": "warning", "OCB003": "warning",
                                       "OCB004": "warning", "OCB005": "info",
                                       "OCB006": "info"}
