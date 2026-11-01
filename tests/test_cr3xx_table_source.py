"""CR300/CR350: an undefined name in a table-output source (`instr_data._report_undefined_source`).
Oracle, 2026-09-27 (`docs/loggers.md` "Undefined table sources")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(table: str, body: str, logger: str) -> list[str]:
    text = (
        f"Public X\nDataTable (T,1,-1)\n{table}EndTable\nBeginProg\n  Scan (1,Sec,0,0)\n"
        f"    CallTable T\n{body}  NextScan\nEndProg\n"
    )
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
@pytest.mark.parametrize(
    "table,body,expected",
    [
        ("  Sample (1,Nope,FP2)\n", "", ["line 3: Nope is not defined."]),
        ("  Sample (1,X+Nope,FP2)\n", "", ["line 3: Nope is not defined."]),
        ("  Maximum (1,Nope,FP2,False,False)\n", "", ["line 3: Nope is not defined."]),
        (
            "  Sample (1,Nope(2),FP2)\n",
            "",
            ["line 3: Nope is not defined.", "line 3: Dimensions not declared: Nope(2)."],
        ),
        ("  Sample (1,Nope,FP2)\n  Average (1,Nope2,FP2,False)\n", "", ["line 3: Nope is not defined."]),
        ("  Sample (1,Nope,FP2)\n  Sample (1,X,FP2)\n", "", ["line 3: Nope is not defined."]),
        ("  Sample (1,Nope,FP2)\n", "    X = Q\n", ["line 8: Q is not defined."]),
    ],
)
def test_undefined_table_source(table, body, expected, logger):
    assert lines(table, body, logger) == expected
