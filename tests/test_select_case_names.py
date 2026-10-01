"""Undefined names in `Select Case`, `Case` and `ElseIf` expressions. Expected lines are verbatim
oracle output (real compilers, 2026-10-01); CR6 and CR1000X are identical, CR350 is identical to
CR300."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(body: str, logger: str) -> list[str]:
    body_text = "".join(f"    {line}\n" for line in body.split(" / "))
    text = f"Public X\nBeginProg\n  Scan (1,Sec,0,0)\n{body_text}  NextScan\nEndProg\n"
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


E = "Invalid symbol in expression:"
OUTSIDE = "line 5: Case outside Select."

ROWS = [
    ("Select Case Nope / Case 1 / EndSelect",
     [f"line 4: {E}  Nope."],
     ["line 4: Nope is not defined.", "line 5: Case outside Select.",
      "line 6: EndSelect w/o Select"]),
    ("Select Case X / Case Nope / EndSelect",
     ["line 5: Case outside Select.", f"line 5: {E} Nope."],
     ["line 5: Nope is not defined."]),
    ("Select Case X / Case 1 To Nope / EndSelect",
     [f"line 5: {E} 1 To Nope."],
     ["line 5: Nope is not defined."]),
    ("Select Case X / Case Is > Nope / EndSelect",
     [f"line 5: {E} Is > Nope."],
     ["line 5: Nope is not defined."]),
    ("Select Case X / Case 1, 2, Nope / EndSelect",
     [OUTSIDE, OUTSIDE, OUTSIDE, f"line 5: {E} 1, 2, Nope."],
     ["line 5: Nope is not defined."]),
    ("Select Case X / Case Nope, Nope2 / EndSelect",
     ["line 5: Case outside Select.", f"line 5: {E} Nope, Nope2."],
     ["line 5: Nope is not defined.", "line 5: Nope2 is not defined."]),
    ("Select Case X / Case Is < Nope, 2 / EndSelect",
     [f"line 5: {E} Is < Nope, 2."],
     ["line 5: Nope is not defined."]),
    ("If X Then / ElseIf Nope Then / EndIf",
     [f"line 5: {E} Nope ."],
     ["line 5: Nope is not defined."]),
    ("If X Then / ElseIf Nope+1 > 2 Then / EndIf",
     [f"line 5: {E} Nope+1 > 2 ."],
     ["line 5: Nope is not defined."]),
    ("Select Case  X+Nope / Case 1 / EndSelect",
     [f"line 4: {E}   X+Nope."],
     ["line 4: Nope is not defined.", "line 5: Case outside Select.",
      "line 6: EndSelect w/o Select"]),
    ("Select Case X / Case 1, 2 / EndSelect", [], []),
]


@pytest.mark.parametrize("body,cr6,cr300", ROWS)
def test_select_case_names(body, cr6, cr300):
    assert lines(body, "cr6") == cr6
    assert lines(body, "cr1000x") == cr6
    assert lines(body, "cr300") == cr300
    assert lines(body, "cr350") == cr300
