"""`For` header expression errors. Expected lines are verbatim oracle output (real compilers,
2026-10-01); CR6 and CR1000X are identical, CR350 is identical to CR300."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(for_stmt: str, next_stmt: str, logger: str) -> list[str]:
    text = (
        "Public X\nBeginProg\n  Scan (1,Sec,0,0)\n"
        f"    {for_stmt}\n    {next_stmt}\n  NextScan\nEndProg\n"
    )
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


E = "Invalid symbol in expression:"

ROWS = [
    ("For j = 1 to 10", "Next j",
     [f"line 5: {E} j ."],
     ["line 4: j is not defined."]),
    ("For j = 1 to N", "Next",
     [f"line 4: {E} N.", f"line 5: {E} j ."],
     ["line 4: j is not defined.", "line 4: N is not defined."]),
    ("For X = 1 to N Step S", "Next",
     [f"line 4: {E} N.", f"line 4: {E} S."],
     ["line 4: N is not defined.", "line 4: S is not defined."]),
    ("For j(1) = 1 to 2", "Next",
     [f"line 5: {E} j(1) ."],
     ["line 4: j is not defined.", "line 4: Dimensions not declared: j(1) ."]),
    ("For X = A to 2", "Next",
     [f"line 4: {E}  A."],
     ["line 4: A is not defined."]),
    ("For X=A+1 To 2", "Next",
     [f"line 4: {E} A+1."],
     ["line 4: A is not defined."]),
    ("For j=A To B Step C", "Next",
     [f"line 4: {E} A.", f"line 4: {E} B.", f"line 4: {E} C.", f"line 5: {E} j."],
     ["line 4: j is not defined.", "line 4: A is not defined.",
      "line 4: B is not defined.", "line 4: C is not defined."]),
    ("For X = 1 to N   'c", "Next",
     [f"line 4: {E} N   ."],
     ["line 4: N is not defined."]),
    ("For X = 1 to N+M", "Next",
     [f"line 4: {E} N+M."],
     ["line 4: N is not defined.", "line 4: M is not defined."]),
    ("For X = 1 to 2", "Next", [], []),
]


@pytest.mark.parametrize("for_stmt,next_stmt,cr6,cr300", ROWS)
def test_for_header(for_stmt, next_stmt, cr6, cr300):
    assert lines(for_stmt, next_stmt, "cr6") == cr6
    assert lines(for_stmt, next_stmt, "cr1000x") == cr6
    assert lines(for_stmt, next_stmt, "cr300") == cr300
    assert lines(for_stmt, next_stmt, "cr350") == cr300
