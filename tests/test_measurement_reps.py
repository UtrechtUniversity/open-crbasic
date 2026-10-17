"""Measurement-instruction `Reps` and destination bounds. Expected lines are verbatim oracle
output (real compilers, 2026-10-01)."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(stmt: str, logger: str) -> list[str]:
    text = (
        "Public X, A(4), B(2,3), L(4) As Long, P\n"
        f"BeginProg\n  Scan (1,Sec,0,0)\n    {stmt}\n  NextScan\nEndProg\n"
    )
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


VOLT = "mV1000,U1,0,0,60,1,0"

CR6_ROWS = [
    (f"VoltSe(A(),4,{VOLT})", []),
    (f"VoltSe(A(),5,{VOLT})", ["line 4: Variable A out of bounds."]),
    (f"VoltSe(A(2),3,{VOLT})", []),
    (f"VoltSe(A(2),4,{VOLT})", ["line 4: Variable A out of bounds."]),
    (f"VoltSe(A,5,{VOLT})", ["line 4: Variable A out of bounds."]),
    (f"VoltSe(X,2,{VOLT})", ["line 4: Variable X out of bounds."]),
    (f"VoltSe(B(),6,{VOLT})", []),
    (f"VoltSe(B(),7,{VOLT})", ["line 4: Variable B out of bounds."]),
    (f"VoltSe(B(2,1),3,{VOLT})", []),
    (f"VoltSe(B(2,1),4,{VOLT})", ["line 4: Variable B out of bounds."]),
    (f"VoltSe(X,0,{VOLT})", ["line 4: Invalid reps 0, must be > 0"]),
    (f"VoltSe(X,-1,{VOLT})",
     ["line 4: Invalid reps -1, must be > 0", "line 4: Variable X out of bounds."]),
    ("TCDiff(A(),5,mV200C,U1,TypeT,X,True,0,60,1.0,0)",
     ["line 4: Variable A out of bounds."]),
    (f"VoltSe(L(),5,{VOLT})", []),
    ("VoltSe(X,0,mV1000,1,0,0,60,1,0)",
     ["line 4: Invalid reps 0, must be > 0", "line 4: Invalid channel 1, must use U1..U12."]),
]


@pytest.mark.parametrize("stmt,expected", CR6_ROWS)
def test_cr6(stmt, expected):
    assert lines(stmt, "cr6") == expected


CR1000X_ROWS = [
    ("VoltSe(X,0,mV1000,1,0,0,60,1,0)", ["line 4: Invalid reps 0, must be > 0"]),
    ("VoltSe(X,-1,mV1000,1,0,0,60,1,0)",
     ["line 4: Invalid reps -1, must be > 0", "line 4: Variable X out of bounds."]),
    ("VoltSe(X,3,mV1000,1,0,0,60,1,0)", ["line 4: Variable X out of bounds."]),
    ("PulseCount(X,0,P1,0,0,1,0)", ["line 4: Invalid reps 0, must be > 0"]),
    ("PulseCount(X,-1,P1,0,0,1,0)",
     ["line 4: Invalid reps -1, must be > 0", "line 4: Variable X out of bounds."]),
]


@pytest.mark.parametrize("stmt,expected", CR1000X_ROWS)
def test_cr1000x(stmt, expected):
    assert lines(stmt, "cr1000x") == expected


REPS0 = ["line 4: Invalid reps 0, must be > 0."]

CR300_ROWS = [
    ("Therm107(X,0,1,VX1,0,60,1,0)", REPS0),
    ('Therm107(X,"fuzz",1,VX1,0,60,1,0)', REPS0),
    ("Therm109(X,0,1,VX1,0,60,1,0)", REPS0),
    ("Therm108(X,0,1,VX1,0,60,1,0)", REPS0),
]


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
@pytest.mark.parametrize("stmt,expected", CR300_ROWS)
def test_cr300(logger, stmt, expected):
    assert lines(stmt, logger) == expected
