"""Typed dimensions (`Public A As Float(Nope)`), index names in `Status.TimeStamp(...)`,
initializer bounds warnings, `CallTable` excess arguments and the CR300/CR350 `Sub` cascade
rewrite. Expected lines are verbatim oracle output on all four loggers, 2026-09-28
(`docs/checker.md` "Declaration details and CallTable arguments")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


CASES = [
    ('ct1', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable(T,X,Y)\n  NextScan\nEndProg\n',
     {'cr6': ['line 7: Too many parameters:  Y.'], 'cr1000x': ['line 7: Too many parameters:  Y.'], 'cr300': ['line 7: Too many parameters:  Y.'], 'cr350': ['line 7: Too many parameters:  X.']}),
    ('ct2', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T,X,(1+2)\n  NextScan\nEndProg\n',
     {'cr6': ['line 7: Too many parameters:  1+2.'], 'cr1000x': ['line 7: Too many parameters:  1+2.'], 'cr300': ['line 7: Too many parameters:  1+2.'], 'cr350': ['line 7: Too many parameters:  X.']}),
    ('ct3', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable(T,X)\n    X = Q\n  NextScan\nEndProg\n',
     {'cr6': ['line 8: Invalid symbol in expression:     X = Q.'], 'cr1000x': ['line 8: Invalid symbol in expression:     X = Q.'], 'cr300': ['line 8: Q is not defined.'], 'cr350': ['line 7: Too many parameters:  X.', 'line 8: Q is not defined.']}),
    ('d1', 'Public X, Y\nPublic A As Float(Nope)\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n',
     {'cr6': ['line 2: Invalid symbol in expression: Nope.'], 'cr1000x': ['line 2: Invalid symbol in expression: Nope.'], 'cr300': ['line 2: Nope is not defined.', 'line 2: Variable illegal in Parameter: Nope.', 'line 3: Too many parameters:  1.', 'line 4: Too many parameters:  X.', 'line 6: Too many parameters:  B.', 'line 9: Too many parameters:  Sec.'], 'cr350': ['line 2: Nope is not defined.', 'line 2: Variable illegal in Parameter: Nope.', 'line 3: Too many parameters:  1.', 'line 4: Too many parameters:  X.', 'line 6: Too many parameters:  B.', 'line 9: Too many parameters:  Sec.']}),
    ('d10', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    CallTable(T,X)\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr1000x': [], 'cr300': [], 'cr350': ['line 10: Too many parameters:  X.']}),
    ('d2', 'Public X, Y\nPublic A As Float(3)\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    A(2) = 1\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr1000x': [], 'cr300': [], 'cr350': []}),
    ('d3', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    X = Status.TimeStamp(Nope,1)\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Invalid symbol in expression:     X = Status.TimeStamp(Nope,1).'], 'cr1000x': ['line 10: Invalid symbol in expression:     X = Status.TimeStamp(Nope,1).'], 'cr300': ['line 10: Nope is not defined.'], 'cr350': ['line 10: Nope is not defined.']}),
    ('d4', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    CallTable(T,(1+2))\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr1000x': [], 'cr300': [], 'cr350': ['line 10: Too many parameters:  1+2.']}),
    ('d5', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    Foo(@X)\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Call argument required.'], 'cr1000x': ['line 10: Call argument required.'], 'cr300': ['line 10: Call argument required.'], 'cr350': ['line 10: Call argument required.']}),
    ('d6', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    Call Foo(X)\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Call argument required.'], 'cr1000x': ['line 10: Call argument required.'], 'cr300': ['line 10: Call argument required.'], 'cr350': ['line 10: Call argument required.']}),
    ('d7', 'Public X, Y\nPublic A As Float(Nope) = {1,2}\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n',
     {'cr6': ['line 2: Invalid symbol in expression: Nope.', 'line 2: Warning: Attempt to initialize variable out of bounds.'], 'cr1000x': ['line 2: Invalid symbol in expression: Nope.', 'line 2: Warning: Attempt to initialize variable out of bounds.'], 'cr300': ['line 2: Nope is not defined.', 'line 2: Variable illegal in Parameter: Nope.', 'line 2: Warning: Attempt to initialize variable out of bounds.', 'line 3: Too many parameters:  1.', 'line 4: Too many parameters:  X.', 'line 6: Too many parameters:  B.', 'line 9: Too many parameters:  Sec.'], 'cr350': ['line 2: Nope is not defined.', 'line 2: Variable illegal in Parameter: Nope.', 'line 2: Warning: Attempt to initialize variable out of bounds.', 'line 3: Too many parameters:  1.', 'line 4: Too many parameters:  X.', 'line 6: Too many parameters:  B.', 'line 9: Too many parameters:  Sec.']}),
    ('d8', 'Public X, Y\nDim A As Long(2,Nope)\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n',
     {'cr6': ['line 2: Invalid symbol in expression: Nope.'], 'cr1000x': ['line 2: Invalid symbol in expression: Nope.'], 'cr300': ['line 2: Nope is not defined.', 'line 2: Variable illegal in Parameter: Nope.', 'line 3: Too many parameters:  1.', 'line 4: Too many parameters:  X.', 'line 6: Too many parameters:  B.', 'line 9: Too many parameters:  Sec.'], 'cr350': ['line 2: Nope is not defined.', 'line 2: Variable illegal in Parameter: Nope.', 'line 3: Too many parameters:  1.', 'line 4: Too many parameters:  X.', 'line 6: Too many parameters:  B.', 'line 9: Too many parameters:  Sec.']}),
    ('d9', 'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nSub Foo (A, B)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    X = Status.TimeStamp(1,Nope)\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Invalid symbol in expression:     X = Status.TimeStamp(1,Nope).'], 'cr1000x': ['line 10: Invalid symbol in expression:     X = Status.TimeStamp(1,Nope).'], 'cr300': ['line 10: Nope is not defined.'], 'cr350': ['line 10: Nope is not defined.']}),
    ('w1', 'Public A(2) = {1,2,3}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr1000x': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr300': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr350': ['line 1: Warning: Attempt to initialize variable out of bounds.']}),
    ('w10', 'Public A(Nope) = 5\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Invalid symbol in expression: Nope.'], 'cr1000x': ['line 1: Invalid symbol in expression: Nope.'], 'cr300': ['line 1: Nope is not defined.', 'line 1: Variable illegal in Parameter: Nope.', 'line 3: Too many parameters:  Sec.'], 'cr350': ['line 1: Nope is not defined.', 'line 1: Variable illegal in Parameter: Nope.', 'line 3: Too many parameters:  Sec.']}),
    ('w11', 'Public A(2) = {1,2,3}\nPublic X = Q\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Warning: Attempt to initialize variable out of bounds.', 'line 2: Invalid symbol in expression: Q.', 'line 2: Variable initialization of X has bad syntax.'], 'cr1000x': ['line 1: Warning: Attempt to initialize variable out of bounds.', 'line 2: Invalid symbol in expression: Q.', 'line 2: Variable initialization of X has bad syntax.'], 'cr300': ['line 1: Warning: Attempt to initialize variable out of bounds.', 'line 2: Q is not defined.', 'line 2: Variable illegal in Parameter: Q.', 'line 2: Variable initialization of X has bad syntax.', 'line 4: Too many parameters:  Sec.'], 'cr350': ['line 1: Warning: Attempt to initialize variable out of bounds.', 'line 2: Q is not defined.', 'line 2: Variable illegal in Parameter: Q.', 'line 2: Variable initialization of X has bad syntax.', 'line 4: Too many parameters:  Sec.']}),
    ('w2', 'Public A(2) = {1,2}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr1000x': [], 'cr300': [], 'cr350': []}),
    ('w3', 'Public A = {1,2}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr1000x': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr300': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr350': ['line 1: Warning: Attempt to initialize variable out of bounds.']}),
    ('w4', 'Public A(Nope) = {1}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Invalid symbol in expression: Nope.'], 'cr1000x': ['line 1: Invalid symbol in expression: Nope.'], 'cr300': ['line 1: Nope is not defined.', 'line 1: Variable illegal in Parameter: Nope.', 'line 3: Too many parameters:  Sec.'], 'cr350': ['line 1: Nope is not defined.', 'line 1: Variable illegal in Parameter: Nope.', 'line 3: Too many parameters:  Sec.']}),
    ('w5', 'Dim A(2,2) = {1,2,3,4,5}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr1000x': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr300': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr350': ['line 1: Warning: Attempt to initialize variable out of bounds.']}),
    ('w6', 'Public A(3) As Long = {1,2,3,4}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr1000x': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr300': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr350': ['line 1: Warning: Attempt to initialize variable out of bounds.']}),
    ('w7', 'Const N = 2\nPublic A(N) = {1,2,3}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 2: Warning: Attempt to initialize variable out of bounds.'], 'cr1000x': ['line 2: Warning: Attempt to initialize variable out of bounds.'], 'cr300': ['line 2: Warning: Attempt to initialize variable out of bounds.'], 'cr350': ['line 2: Warning: Attempt to initialize variable out of bounds.']}),
    ('w8', 'Public A(2) = {1,2,3}, B(1) = {1,2}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Warning: Attempt to initialize variable out of bounds.', 'line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr1000x': ['line 1: Warning: Attempt to initialize variable out of bounds.', 'line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr300': ['line 1: Warning: Attempt to initialize variable out of bounds.', 'line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr350': ['line 1: Warning: Attempt to initialize variable out of bounds.', 'line 1: Warning: Attempt to initialize variable out of bounds.']}),
    ('w9', 'Public S(2) As String = {"a","b","c"}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     {'cr6': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr1000x': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr300': ['line 1: Warning: Attempt to initialize variable out of bounds.'], 'cr350': ['line 1: Warning: Attempt to initialize variable out of bounds.']}),
]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize("name,text,expected", CASES, ids=[c[0] for c in CASES])
def test_oracle_case(name, text, expected, logger):
    assert lines(text, logger) == expected[logger]
