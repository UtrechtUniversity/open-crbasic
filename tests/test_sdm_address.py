"""`Invalid SDM Address: %d, must be 0 - 14.` (`instr_rules.check_sdm_address`). Oracle, CR6 and
CR1000X, 2026-09-28 (`docs/instructions.md` "SDM addresses")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(body: str, logger: str) -> list[str]:
    text = f"Public X, V(20)\nBeginProg\n  Scan (1,Sec,0,0)\n{body}  NextScan\nEndProg\n"
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize(
    "body,expected",
    [
        ("    CSAT3B(V(),0,99,0)\n", ["line 4: Invalid SDM Address: 99, must be 0 - 14."]),
        ("    CSAT3BMonitor(V(),0,99)\n", ["line 4: Invalid SDM Address: 99, must be 0 - 14."]),
        ("    SDMX50 (15,4)\n", ["line 4: Invalid SDM Address: 15, must be 0 - 14."]),
        ("    SDMX50 (-1,4)\n", ["line 4: Invalid SDM Address: 255, must be 0 - 14."]),
        ("    SDMX50 (14,4)\n", []),
        ("    SDMX50 (256,4)\n", []),
        ("    SDMX50 (X,4)\n", ["line 4: Variable illegal in Parameter: X."]),
        (
            "    SDMX50 (99,4)\n    SDMX50 (98,4)\n",
            ["line 4: Invalid SDM Address: 99, must be 0 - 14.", "line 5: Invalid SDM Address: 98, must be 0 - 14."],
        ),
        (
            "    X = Q\n    SDMX50 (99,4)\n",
            ["line 4: Invalid symbol in expression:     X = Q.", "line 5: Invalid SDM Address: 99, must be 0 - 14."],
        ),
    ],
)
def test_sdm_address(body, expected, logger):
    assert lines(body, logger) == expected


import json
from pathlib import Path

from opencrbasic.compiler import compile_file

FLOAT_TYPING = json.loads((Path(__file__).parent / "fixtures" / "r7_sdm_float_typing.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FLOAT_TYPING["cases"], ids=lambda case: case["id"])
def test_sdm_float_typing_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")
    result = compile_file(program, profiles.get(case["logger"]))
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
