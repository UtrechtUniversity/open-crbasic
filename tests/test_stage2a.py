"""CR6/CR1000X checks between pass 1 and pass 2's expression tier (`instr_data.check_stage2a`),
and string array dimensions. Oracle probes, 2026-09-27 (`docs/checker.md` "Stage 2a")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def messages(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


def prog(table: str, body: str = "") -> str:
    return (f"Public X, Y, A(5)\nDataTable (T,1,-1)\n{table}\nEndTable\nBeginProg\n"
            f"  Scan (1,Sec,0,0)\n{body}    CallTable T\n  NextScan\nEndProg\n")


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("table,expected", [
    ("  Sample (0,X,FP2)", ["Illegal Reps 0"]),
    ('  Sample ("fuzz",X,FP2)', ["Illegal Reps -2147483648"]),
    ("  Maximum (999999999999,X,FP2,False,False)", ["Illegal Reps -2147483648"]),
    ("  Sample (3,X,FP2)", ["Variable X out of bounds.", "Variable X, index 1, reps 3, out of bounds."]),
    ("  Sample (3,A(4),FP2)", ["Variable A out of bounds.", "Variable A, index 4, reps 3, out of bounds."]),
    ("  Sample (5,A(),FP2)", []),
    ("  Sample (0,X,FP2)\n  Sample (3,A(4),FP2)", ["Illegal Reps 0"]),
])
def test_table_reps_and_bounds(table, expected, logger):
    assert messages(prog(table), logger) == expected


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
def test_constant_destination(logger):
    body = "    Battery(5)\n    PanelTemp(3,15000)\n    Battery(X+1)\n"
    assert messages(prog("  Sample (1,X,FP2)", body), logger) == [
        "Invalid Variable or Array: 5.", "Invalid Variable or Array: 3."]


def test_stage2a_suppresses_earlier_pass2_expression_errors():
    body = "    X = Q\n    Battery((1+2))\n"
    assert messages(prog("  Sample (1,X,FP2)", body)) == ["Invalid Variable or Array: (1+2)."]


def test_stage2a_is_suppressed_by_pass1_errors():
    text = prog("  Sample (0,X,FP2)").replace("Public X, Y, A(5)", "Public X, Y, A(5)\nDim Z banana")
    assert messages(text) == ["Invalid Variable declaration banana."]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
def test_string_array_dimension(logger):
    text = prog("  Sample (1,X,FP2)").replace("Public X, Y, A(5)", 'Public X, Y, A(5)\nPublic Q("fuzz")')
    assert messages(text, logger)[0] == 'Illegally dimension: Public Q("fuzz")\n'


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
def test_cr300_table_checks_run_late(logger):
    assert messages(prog("  Sample (0,X,FP2)\n  Sample (3,A(4),FP2)"), logger) == ["Illegal Reps 0"]
    assert messages(prog("  Sample (3,A(4),FP2)"), logger) == [
        "Variable A out of bounds.", "Variable A, index 4, reps 3, out of bounds."]
    assert messages(prog("  Sample (0,X,FP2)", "    X = Q\n"), logger) == ["Q is not defined."]
