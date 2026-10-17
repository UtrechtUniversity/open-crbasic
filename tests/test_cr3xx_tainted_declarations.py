"""CR300/CR350: declarations after the `Too many parameters` cascade starts are not registered
(`cr3xx._report_unregistered`). Expected lines are verbatim oracle output, 2026-09-28
(`docs/loggers.md` "Tainted declarations")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    out = result.result_lines()[2:] if not result.ok else result.result_lines()[1:]
    return [part for line in out for part in line.split("\n") if part.strip()]


CASES = [
    ('z1', 'Public X\nDim Z banana\nPublic A\nBeginProg\n  Scan (1,Sec,0,0)\n    X = A\n  NextScan\nEndProg\n',
     {'cr300': ['line 2: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.', 'line 6: A is not defined.'], 'cr350': ['line 2: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.', 'line 6: A is not defined.']}),
    ('z2', 'Public X\nPublic A\nDim Z banana\nBeginProg\n  Scan (1,Sec,0,0)\n    X = A\n  NextScan\nEndProg\n',
     {'cr300': ['line 3: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.'], 'cr350': ['line 3: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.']}),
    ('z3', 'Public X\nDim Z banana\nPublic A, B\nBeginProg\n  Scan (1,Sec,0,0)\n    X = B\n    X = A\n  NextScan\nEndProg\n',
     {'cr300': ['line 2: Invalid Variable declaration banana.', 'line 3: Too many parameters:  B.', 'line 5: Too many parameters:  Sec.', 'line 6: B is not defined.', 'line 7: A is not defined.'], 'cr350': ['line 2: Invalid Variable declaration banana.', 'line 3: Too many parameters:  B.', 'line 5: Too many parameters:  Sec.', 'line 6: B is not defined.', 'line 7: A is not defined.']}),
    ('z4', 'Public X\nDim Z banana\nFunction F (x)\n  Return x + 1\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    X = F(1)\n  NextScan\nEndProg\n',
     {'cr300': ['line 2: Invalid Variable declaration banana.', 'line 7: Too many parameters:  Sec.', 'line 8: No Matching, or out of place ) in expression:     X = F(1)', '.'], 'cr350': ['line 2: Invalid Variable declaration banana.', 'line 7: Too many parameters:  Sec.', 'line 8: No Matching, or out of place ) in expression:     X = F(1)', '.']}),
    ('z5', 'Public X\nDim Z banana\nDim A(3)\nBeginProg\n  Scan (1,Sec,0,0)\n    A(1) = 2\n    X = A(2)\n  NextScan\nEndProg\n',
     {'cr300': ['line 2: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.', 'line 6: A is not defined.', 'line 6: Invalid, or out of place expression:     A(1) = 2', '.', 'line 7: A is not defined.', 'line 7: Dimensions not declared:     X = A(2)', '.'], 'cr350': ['line 2: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.', 'line 6: A is not defined.', 'line 6: Invalid, or out of place expression:     A(1) = 2', '.', 'line 7: A is not defined.', 'line 7: Dimensions not declared:     X = A(2)', '.']}),
    ('z6', 'Public X\nDim Z banana\nPublic A\nBeginProg\n  Scan (1,Sec,0,0)\n    If A > 1 Then X = 1\n  NextScan\nEndProg\n',
     {'cr300': ['line 2: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.', 'line 6: A is not defined.'], 'cr350': ['line 2: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.', 'line 6: A is not defined.']}),
    ('z7', 'Public X\nDim Z banana\nConst K = 2\nBeginProg\n  Scan (1,Sec,0,0)\n    X = K\n  NextScan\nEndProg\n',
     {'cr300': ['line 2: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.'], 'cr350': ['line 2: Invalid Variable declaration banana.', 'line 5: Too many parameters:  Sec.']}),
    ('z8', 'Public X\nDim Z banana\nSub S (a)\n  X = a\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    Call S(1)\n  NextScan\nEndProg\n',
     {'cr300': ['line 2: Invalid Variable declaration banana.', 'line 4: a is not defined.', 'line 7: Too many parameters:  Sec.'], 'cr350': ['line 2: Invalid Variable declaration banana.', 'line 4: a is not defined.', 'line 7: Too many parameters:  Sec.']}),
]


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
@pytest.mark.parametrize("name,text,expected", CASES, ids=[c[0] for c in CASES])
def test_tainted_declarations(name, text, expected, logger):
    assert lines(text, logger) == expected[logger]
