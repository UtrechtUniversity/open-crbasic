"""Control statements outside their block (`checker/structure.py` `stray_controls`, the
`Case outside Select.`/`EndSelect w/o Select` pass-1 checks). Expected lines are verbatim oracle
output, CR6 and CR300, 2026-09-27 (`docs/checker.md` "Stray control statements")."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def prog(sub: str, body: str, after: str) -> str:
    return (f"Public X, I\nSub Foo\n{sub}EndSub\nBeginProg\n  Scan (1,Sec,0,0)\n{body}  NextScan\n"
            f"{after}EndProg\n")


ROWS = [
    ('', '    Else\n', '',
     ['line 6: Else with no If.'],
     ['line 6: Else with no If.']),
    ('', '    ElseIf X Then\n', '',
     ['line 6: Case with no Select.'],
     ['line 6: Case with no Select.']),
    ('', '    Exit Do\n', '',
     ['line 6: Exit Do without DO.'],
     ['line 6: Exit Do without DO.']),
    ('', '    Exit For\n', '',
     ['line 6: Exit For without FOR.'],
     ['line 6: Exit For without FOR.']),
    ('  ExitScan\n', '', '',
     ['line 3: ExitScan not within a Scan.'],
     ['line 3: ExitScan not within a Scan.']),
    ('', '', '  ContinueScan\n',
     ['line 7: ContinueScan not within a Scan.', 'line 7: Warning: ContinueScan never reached at run time.'],
     ['line 7: ContinueScan not within a Scan.', 'line 7: Warning: ContinueScan never reached at run time.']),
    ('', '    Else\n    X = Q\n', '',
     ['line 6: Else with no If.'],
     ['line 7: Q is not defined.']),
    ('  ExitScan\n', '    X = Q\n', '',
     ['line 3: ExitScan not within a Scan.', 'line 7: Invalid symbol in expression:     X = Q.'],
     ['line 7: Q is not defined.']),
    ('', '    Exit Do\n    X = Q\n', '',
     ['line 6: Exit Do without DO.', 'line 7: Invalid symbol in expression:     X = Q.'],
     ['line 7: Q is not defined.']),
    ('', '    Case 1\n', '',
     ['line 6: Case outside Select.'],
     ['line 6: Case outside Select.']),
    ('', '    Exit Sub\n', '',
     ['line 6: Exit Sub without Sub or nested subs.'],
     ['line 6: Exit Sub without Sub or nested subs.']),
    ('', '', '  ExitScan\n',
     ['line 7: ExitScan not within a Scan.'],
     ['line 7: ExitScan not within a Scan.']),
    ('', '    X = Q\n    Else\n', '',
     ['line 6: Invalid symbol in expression:     X = Q.', 'line 7: Else with no If.'],
     ['line 6: Q is not defined.']),
    ('', '    Case Else\n', '',
     ['line 6: Else with no If.'],
     ['line 6: Else with no If.']),
    ('', '    Case 1\n    X = Q\n', '',
     ['line 6: Case outside Select.'],
     ['line 6: Case outside Select.', 'line 7: Q is not defined.']),
    ('', '    ElseIf X Then\n    X = Q\n', '',
     ['line 6: Case with no Select.'],
     ['line 7: Q is not defined.']),
    ('', '    Exit For\n    X = Q\n', '',
     ['line 6: Exit For without FOR.', 'line 7: Invalid symbol in expression:     X = Q.'],
     ['line 7: Q is not defined.']),
    ('', '    If X Then\n    Else\n    Else\n    EndIf\n    X = Q\n', '',
     ['line 8: If statement with duplicate Else.', 'line 10: Invalid symbol in expression:     X = Q.'],
     ['line 10: Q is not defined.']),
    ('', '    Else\n    Exit Do\n', '',
     ['line 6: Else with no If.'],
     ['line 6: Else with no If.']),
    ('', '    Exit Do\n    Else\n', '',
     ['line 6: Exit Do without DO.', 'line 7: Else with no If.'],
     ['line 6: Exit Do without DO.', 'line 7: Else with no If.']),
    ('', '    Exit Do\n    Exit For\n    Case 1\n', '',
     ['line 8: Case outside Select.'],
     ['line 8: Case outside Select.']),
    ('', '    Exit Sub\n    Exit Do\n', '',
     ['line 6: Exit Sub without Sub or nested subs.', 'line 7: Exit Do without DO.'],
     ['line 6: Exit Sub without Sub or nested subs.', 'line 7: Exit Do without DO.']),
    ('  ExitScan\n', '    Else\n', '',
     ['line 3: ExitScan not within a Scan.', 'line 7: Else with no If.'],
     ['line 3: ExitScan not within a Scan.', 'line 7: Else with no If.']),
    ('', '    If X Then\n    Else\n    Else\n    EndIf\n    Exit Do\n', '',
     ['line 8: If statement with duplicate Else.', 'line 10: Exit Do without DO.'],
     ['line 8: If statement with duplicate Else.', 'line 10: Exit Do without DO.']),
    ('', '    X = Q\n    ElseIf X Then\n', '',
     ['line 6: Invalid symbol in expression:     X = Q.', 'line 7: Case with no Select.'],
     ['line 6: Q is not defined.']),
    ('', '    X = Q\n    Case 1\n', '',
     ['line 7: Case outside Select.'],
     ['line 6: Q is not defined.', 'line 7: Case outside Select.']),
]


@pytest.mark.parametrize("sub,body,after,cr6,cr300", ROWS)
def test_stray_controls(sub, body, after, cr6, cr300):
    text = prog(sub, body, after)
    assert lines(text, "cr6") == cr6
    assert lines(text, "cr1000x") == cr6
    assert lines(text, "cr300") == cr300
    assert lines(text, "cr350") == cr300


@pytest.mark.parametrize("case,expected", [
    ('    Case Else = "x"\n', ["line 4: Else with no If."]),
    ("    Case Elsewhere\n", ["line 4: Else with no If."]),
    ("    Select Case X\n    Case Else\n    Case Else = 3\n    EndSelect\n",
     ["line 6: If statement with duplicate Else."]),
])
def test_case_else_is_a_prefix(case, expected):
    text = f"Public X, I\nBeginProg\n  Scan (1,Sec,0,0)\n{case}  NextScan\nEndProg\n"
    assert lines(text, "cr6") == expected
    assert lines(text, "cr350") == expected


@pytest.mark.parametrize("body", ["    Else\n    EndSelect\n", "    EndSelect\n    X = Q\n",
                                  "    Exit Do\n    EndSelect\n"])
def test_endselect_without_select_is_pass1(body):
    text = (f"Public X, I\nFunction Fn\nEndFunction\nBeginProg\n  Scan (1,Sec,0,0)\n{body}"
            "  NextScan\nEndProg\n")
    line = 6 if body.startswith("    EndSelect") else 7
    assert lines(text, "cr6") == [f"line {line}: EndSelect w/o Select"]
