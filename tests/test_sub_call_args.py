"""Undefined names in `Sub` call arguments (`structure._report_call_arg_names`). Expected lines are
verbatim oracle output, CR6 and CR300, 2026-09-28; CR1000X and CR350 matched the same probes
(`docs/checker.md` "Sub call arguments")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

HEAD = ("Public X\nSub Foo (A)\nEndSub\nSub Two (A, B)\nEndSub\nSub Msg (S As String)\nEndSub\n"
        "BeginProg\n  Scan (1,Sec,0,0)\n")


def lines(body: str, logger: str) -> list[str]:
    text = f"{HEAD}{body}  NextScan\nEndProg\n"
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


ROWS = [
    ('    Call Foo(Q)\n',
     ['line 10: Invalid symbol in expression: Q.'],
     ['line 10: Q is not defined.']),
    ('    Foo(X,1)\n',
     ['line 10: Call to Foo with 2 parameters, requires 1 arguments.'],
     ['line 10: Call to Foo with 2 parameters, requires 1 arguments.']),
    ('    Foo(Q)\n    X = Q2\n',
     ['line 10: Invalid symbol in expression: Q.', 'line 11: Invalid symbol in expression:     X = Q2.'],
     ['line 10: Q is not defined.', 'line 11: Q2 is not defined.']),
    ('    Two(Q, Q2)\n',
     ['line 10: Invalid symbol in expression: Q.', 'line 10: Invalid symbol in expression: Q2.'],
     ['line 10: Q is not defined.', 'line 10: Q2 is not defined.']),
    ('    Foo(Q)\n',
     ['line 10: Invalid symbol in expression: Q.'],
     ['line 10: Q is not defined.']),
    ('    Foo(Q+1)\n',
     ['line 10: Invalid symbol in expression: Q+1.'],
     ['line 10: Q is not defined.']),
    ('    Foo(Sin(Q))\n',
     ['line 10: Invalid symbol in expression: Sin(Q).'],
     ['line 10: Q is not defined.']),
    ('    Two(X, Q)\n',
     ['line 10: Invalid symbol in expression: Q.'],
     ['line 10: Q is not defined.']),
    ('    Foo(Nope(1))\n',
     ['line 10: Invalid symbol in expression: Nope(1).'],
     ['line 10: Nope is not defined.', 'line 10: Dimensions not declared: Nope(1).']),
    ('    Msg("a" + Nope)\n',
     ['line 10: Invalid symbol in expression: "a" + Nope.'],
     ['line 10: Nope is not defined.']),
    ('    Msg("a","b")\n',
     ['line 10: Call to Msg with 2 parameters, requires 1 arguments.'],
     ['line 10: Call to Msg with 2 parameters, requires 1 arguments.']),
    ('    Call Msg("a","b")\n',
     ['line 10: Call to Msg with 2 parameters, requires 1 arguments.'],
     ['line 10: Call to Msg with 2 parameters, requires 1 arguments.']),
]


@pytest.mark.parametrize("body,cr6,cr300", ROWS)
def test_sub_call_args(body, cr6, cr300):
    assert lines(body, "cr6") == cr6
    assert lines(body, "cr1000x") == cr6
    assert lines(body, "cr300") == cr300
    assert lines(body, "cr350") == cr300
