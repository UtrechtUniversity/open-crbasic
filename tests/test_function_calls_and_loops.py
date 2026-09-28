"""User `Function` argument counts in expressions (`expressions.check_function_call_counts`) and
undefined names in `Do`/`Loop` conditions. Expected lines are verbatim oracle output, CR6 and
CR300, 2026-09-28; CR1000X and CR350 matched the `f*` probes (`docs/checker.md` "Function argument
counts")."""

from __future__ import annotations

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
    ('f1', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    X = One()\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression:     X = One().'], 'cr300': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression:     X = One()', '.']}),
    ('f2', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    X = One() + 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression:     X = One() + 1.'], 'cr300': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression:     X = One() + 1', '.']}),
    ('f3', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    X = Two(1)\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression:     X = Two(1).'], 'cr300': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression:     X = Two(1)', '.']}),
    ('f4', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    X = One(1,2)\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: No Matching, or out of place ) in expression:     X = One(1,2).'], 'cr300': ['line 10: No Matching, or out of place ) in expression:     X = One(1,2)', '.']}),
    ('f5', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    X = Two(1,2,3)\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: No Matching, or out of place ) in expression:     X = Two(1,2,3).'], 'cr300': ['line 10: No Matching, or out of place ) in expression:     X = Two(1,2,3)', '.']}),
    ('f6', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    If One() > 1 Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression: One() > 1 .'], 'cr300': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression: One() > 1 .']}),
    ('f7', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    X = One()\n    X = Q\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression:     X = One().'], 'cr300': ['line 10: Call argument required.', 'line 10: Invalid, or out of place expression:     X = One()', '.', 'line 11: Q is not defined.']}),
    ('f8', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    Do\n    Loop Until (Nope <> 1)\n  NextScan\nEndProg\n',
     {'cr6': ['line 11: Invalid symbol in expression: (Nope <> 1).'], 'cr300': ['line 11: Nope is not defined.']}),
    ('f9', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    Do While Nope\n    Loop\n  NextScan\nEndProg\n',
     {'cr6': ['line 10: Invalid symbol in expression: Nope.'], 'cr300': ['line 10: Nope is not defined.']}),
    ('f11', 'Public X\nFunction One (A)\n  Return A\nEndFunction\nFunction Two (A, B)\n  Return A + B\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n    X = One\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr300': []}),
    ('g1', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    Do\n    Loop Until (Nope <> 1)   \n  NextScan\nEndProg\n',
     {'cr6': ['line 5: Invalid symbol in expression: (Nope <> 1)   .'], 'cr300': ['line 5: Nope is not defined.']}),
    ('g2', "Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    Do While Nope  'c\n    Loop\n  NextScan\nEndProg\n",
     {'cr6': ['line 4: Invalid symbol in expression: Nope  .'], 'cr300': ['line 4: Nope is not defined.']}),
]


@pytest.mark.parametrize("name,text,expected", CASES, ids=[c[0] for c in CASES])
def test_oracle_case(name, text, expected):
    assert lines(text, "cr6") == expected["cr6"]
    assert lines(text, "cr300") == expected["cr300"]
