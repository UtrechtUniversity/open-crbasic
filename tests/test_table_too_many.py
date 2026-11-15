"""Excess arguments on table-output instructions and `SendTableDef`
(`instr_rules._TOO_MANY_EXTRA`). Expected lines are verbatim oracle output on all four loggers,
2026-09-28."""

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
    (
        "t1",
        'Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2,False,"fuzz")\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n',
        {
            "cr6": ['line 3: Too many parameters:  "fuzz".'],
            "cr1000x": ['line 3: Too many parameters:  "fuzz".'],
            "cr300": ['line 3: Too many parameters:  "fuzz".'],
            "cr350": ['line 3: Too many parameters:  "fuzz".'],
        },
    ),
    (
        "t10",
        "Public X, Y\nDataTable (T,1,-1)\n  Median (1,X,100,FP2,False,1)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n",
        {
            "cr6": ["line 3: Too many parameters:  1."],
            "cr1000x": ["line 3: Too many parameters:  1."],
            "cr300": ["line 3: Too many parameters:  1."],
            "cr350": ["line 3: Too many parameters:  1."],
        },
    ),
    (
        "t2",
        'Public X, Y\nDataTable (T,1,-1)\n  Average (1,X,FP2,False,"fuzz")\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n',
        {
            "cr6": ['line 3: Too many parameters:  "fuzz".'],
            "cr1000x": ['line 3: Too many parameters:  "fuzz".'],
            "cr300": ['line 3: Too many parameters:  "fuzz".'],
            "cr350": ['line 3: Too many parameters:  "fuzz".'],
        },
    ),
    (
        "t3",
        'Public X, Y\nDataTable (T,1,-1)\n  Maximum (1,X,FP2,False,False,"fuzz")\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n',
        {
            "cr6": ['line 3: Too many parameters:  "fuzz".'],
            "cr1000x": ['line 3: Too many parameters:  "fuzz".'],
            "cr300": ['line 3: Too many parameters:  "fuzz".'],
            "cr350": ['line 3: Too many parameters:  "fuzz".'],
        },
    ),
    (
        "t4",
        "Public X, Y\nDataTable (T,1,-1)\n  Minimum (1,X,FP2,False,False,(1+2))\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n",
        {
            "cr6": ["line 3: Too many parameters:  1+2."],
            "cr1000x": ["line 3: Too many parameters:  1+2."],
            "cr300": ["line 3: Too many parameters:  1+2."],
            "cr350": ["line 3: Too many parameters:  1+2."],
        },
    ),
    (
        "t5",
        "Public X, Y\nDataTable (T,1,-1)\n  Totalize (1,X,FP2,False,Y)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n",
        {
            "cr6": ["line 3: Too many parameters:  Y."],
            "cr1000x": ["line 3: Too many parameters:  Y."],
            "cr300": ["line 3: Too many parameters:  Y."],
            "cr350": ["line 3: Too many parameters:  Y."],
        },
    ),
    (
        "t6",
        "Public X, Y\nDataTable (T,1,-1)\n  StdDev (1,X,FP2,False,1)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n",
        {
            "cr6": ["line 3: Too many parameters:  1."],
            "cr1000x": ["line 3: Too many parameters:  1."],
            "cr300": ["line 3: Too many parameters:  1."],
            "cr350": ["line 3: Too many parameters:  1."],
        },
    ),
    (
        "t7",
        "Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2,False,1)\n  Sample (1,Y,FP2)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n",
        {
            "cr6": ["line 3: Too many parameters:  1."],
            "cr1000x": ["line 3: Too many parameters:  1."],
            "cr300": ["line 3: Too many parameters:  1."],
            "cr350": ["line 3: Too many parameters:  1."],
        },
    ),
    (
        "t8",
        "Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2,False,1)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    X = Q\n  NextScan\nEndProg\n",
        {
            "cr6": ["line 3: Too many parameters:  1."],
            "cr1000x": ["line 3: Too many parameters:  1."],
            "cr300": [
                "line 3: Too many parameters:  1.",
                "line 8: Q is not defined.",
            ],
            "cr350": [
                "line 3: Too many parameters:  1.",
                "line 8: Q is not defined.",
            ],
        },
    ),
    (
        "t9",
        "Public X, Y\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nBeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n    SendTableDef(ComRS232,0,4094,T,(1+2))\n  NextScan\nEndProg\n",
        {
            "cr6": ["line 8: Too many parameters:  1+2."],
            "cr1000x": ["line 8: Too many parameters:  1+2."],
            "cr300": ["line 8: Too many parameters:  1+2."],
            "cr350": ["line 8: Too many parameters:  1+2."],
        },
    ),
]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize("name,text,expected", CASES, ids=[c[0] for c in CASES])
def test_table_too_many(name, text, expected, logger):
    assert lines(text, logger) == expected[logger]
