"""Subscripts beyond a variable's declared dimensions (`expressions._over_indexed`,
`symbols.declared_dim_counts`). Expected lines are verbatim oracle output, CR6 and CR300,
2026-09-28; CR1000X and CR350 matched the same probes (`docs/checker.md` "Subscript counts")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

HEAD = "Public X, R(5), M(5,5), C(2,2,2)\nBeginProg\n  Scan (1,Sec,0,0)\n"


def lines(body: str, logger: str, head: str = HEAD) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(f"{head}{body}  NextScan\nEndProg\n", "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    out = result.result_lines()[2:] if not result.ok else result.result_lines()[1:]
    return [part for line in out for part in line.split("\n") if part.strip()]


ROWS = [
    ('    R(2,1) = 1\n',
     [],
     ['line 4: Invalid, or out of place expression:     R(2,1) = 1', '.']),
    ('    X = M(2)\n',
     [],
     []),
    ('    X = X(1)\n',
     [],
     ['line 4: Dimensions not declared:     X = X(1)', '.']),
    ('    R(2,1) += 1\n',
     [],
     ['line 4: Invalid, or out of place expression:     R(2,1) += 1', '.']),
    ('    M(2) = 1\n',
     [],
     []),
    ('    X(1) = 1\n',
     [],
     ['line 4: Invalid, or out of place expression:     X(1) = 1', '.']),
    ('    X = R(2,1)\n',
     [],
     ['line 4: Dimensions not declared:     X = R(2,1)', '.']),
    ('    R(2,"fuzz") = 1\n',
     [],
     ['line 4: Invalid, or out of place expression:     R(2,"fuzz") = 1', '.']),
    ('    C(1,1,1,1) = 1\n',
     ['line 4: Invalid, or out of place expression:     C(1,1,1,1) = 1.'],
     ['line 4: Invalid, or out of place expression:     C(1,1,1,1) = 1', '.']),
    ('    R(2,1) = 1\n    X = Q\n',
     ['line 5: Invalid symbol in expression:     X = Q.'],
     ['line 4: Invalid, or out of place expression:     R(2,1) = 1', '.', 'line 5: Q is not defined.']),
    ('    X = Q\n    R(2,1) = 1\n',
     ['line 4: Invalid symbol in expression:     X = Q.'],
     ['line 4: Q is not defined.', 'line 5: Invalid, or out of place expression:     R(2,1) = 1', '.']),
    ('    Battery(R(2,1))\n',
     [],
     ['line 4: Dimensions not declared: R(2,1).']),
]


@pytest.mark.parametrize("body,cr6,cr300", ROWS)
def test_subscript_counts(body, cr6, cr300):
    assert lines(body, "cr6") == cr6
    assert lines(body, "cr1000x") == cr6
    assert lines(body, "cr300") == cr300
    assert lines(body, "cr350") == cr300


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
def test_string_character_subscript_is_not_counted(logger):
    head = "Public X As Long, B As String * 20\nBeginProg\n  Scan (1,Sec,0,0)\n"
    assert lines("    X = ASCII(B(1,1,2))\n", logger, head) == []
