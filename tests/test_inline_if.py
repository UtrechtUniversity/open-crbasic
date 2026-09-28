"""Statements of a one-line `If` get the ordinary tier-B checks, echoing their own verbatim text
(`checker/__init__.py` `_with_inline`, `parser.py` `_parse_stmt_list`). Expected lines are
verbatim oracle output, CR6 and CR300, 2026-09-28 (`docs/checker.md` "One-line If statements")."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(body: str, logger: str) -> list[str]:
    text = f"Public X\nBeginProg\n  Scan (1,Sec,0,0)\n{body}  NextScan\nEndProg\n"
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


ROWS = [
    ('    If X Then Y = 1\n',
     ['line 4: Invalid, or out of place expression: Y = 1.'],
     ['line 4: Y is not defined.']),
    ('    If X Then X = Q\n',
     ['line 4: Invalid symbol in expression: X = Q.'],
     ['line 4: Q is not defined.']),
    ('    If X Then Battery(Q)\n',
     ['line 4: Invalid symbol in expression: Q.'],
     ['line 4: Q is not defined.']),
    ('    If X Then foo bar\n',
     ['line 4: Invalid, or out of place expression: foo bar.'],
     ['line 4: foo is not defined.', 'line 4: bar is not defined.', 'line 4: Invalid, or out of place expression: foo bar.']),
    ('    If X Then X = 1 Else Y = 2\n',
     ['line 4: Invalid, or out of place expression: Y = 2.'],
     ['line 4: Y is not defined.']),
    ('    If X Then Battery(X) Else X = Q\n',
     ['line 4: Invalid symbol in expression: X = Q.'],
     ['line 4: Q is not defined.']),
    ('    If X Then Y = 1\n    X = Q2\n',
     ['line 4: Invalid, or out of place expression: Y = 1.', 'line 5: Invalid symbol in expression:     X = Q2.'],
     ['line 4: Y is not defined.', 'line 5: Q2 is not defined.']),
    ('    If X Then X = Q\n    X = Q2\n',
     ['line 4: Invalid symbol in expression: X = Q.', 'line 5: Invalid symbol in expression:     X = Q2.'],
     ['line 4: Q is not defined.', 'line 5: Q2 is not defined.']),
    ('    If X > 1 Then X=Q+1\n',
     ['line 4: Invalid symbol in expression: X=Q+1.'],
     ['line 4: Q is not defined.']),
    ('    If X Then Y(1) = 1\n',
     ['line 4: Invalid, or out of place expression: Y(1) = 1.'],
     ['line 4: Y is not defined.', 'line 4: Invalid, or out of place expression: Y(1) = 1.']),
    ('    If X Then Battery(Q) Else Battery(Q2)\n',
     ['line 4: Invalid symbol in expression: Q.', 'line 4: Invalid symbol in expression: Q2.'],
     ['line 4: Q is not defined.', 'line 4: Q2 is not defined.']),
]


@pytest.mark.parametrize("body,cr6,cr300", ROWS)
def test_inline_if(body, cr6, cr300):
    assert lines(body, "cr6") == cr6
    assert lines(body, "cr1000x") == cr6
    assert lines(body, "cr300") == cr300
    assert lines(body, "cr350") == cr300
