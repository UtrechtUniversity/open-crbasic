"""Unit tests for user `Function` argument counts in bare (statement-form) calls
(`expressions.py`'s `check_function_call_counts`). Expected lines are verbatim oracle output,
all four loggers (2026-10-02)."""

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


PROG = """Public X
Function F0 ()
  Return 1
EndFunction
Function F1 (A)
  Return A
EndFunction
Function F2 (A, B)
  Return A + B
EndFunction
BeginProg
  Scan (1,Sec,0,0)
{stmt}
  NextScan
EndProg
"""


def prog(stmt: str) -> str:
    return PROG.format(stmt=stmt)


# ------------------------------------------------------------------ too many arguments


def test_bare_call_too_many_args_f1_takes_1():
    text = prog("    F1(1,2)\n")
    assert lines(text, "cr6") == ["line 13: No Matching, or out of place ) in expression:     F1(1,2)."]
    assert lines(text, "cr300") == ["line 13: No Matching, or out of place ) in expression:     F1(1,2)", "."]
    assert lines(text, "cr1000x") == ["line 13: No Matching, or out of place ) in expression:     F1(1,2)."]
    assert lines(text, "cr350") == ["line 13: No Matching, or out of place ) in expression:     F1(1,2)", "."]


def test_bare_call_too_many_args_f0_takes_0():
    text = prog("    F0(1)\n")
    assert lines(text, "cr6") == ["line 13: No Matching, or out of place ) in expression:     F0(1)."]
    assert lines(text, "cr300") == ["line 13: No Matching, or out of place ) in expression:     F0(1)", "."]
    assert lines(text, "cr1000x") == ["line 13: No Matching, or out of place ) in expression:     F0(1)."]


def test_bare_call_too_many_args_f2_takes_2():
    text = prog("    F2(1,2,3)\n")
    assert lines(text, "cr6") == ["line 13: No Matching, or out of place ) in expression:     F2(1,2,3)."]
    assert lines(text, "cr300") == ["line 13: No Matching, or out of place ) in expression:     F2(1,2,3)", "."]


# ------------------------------------------------------------------ too few arguments


def test_bare_call_too_few_args_f1_needs_1():
    text = prog("    F1()\n")
    assert lines(text, "cr6") == [
        "line 13: Call argument required.",
        "line 13: Invalid, or out of place expression:     F1()."
    ]
    assert lines(text, "cr300") == [
        "line 13: Call argument required.",
        "line 13: Invalid, or out of place expression:     F1()",
        "."
    ]
    assert lines(text, "cr1000x") == [
        "line 13: Call argument required.",
        "line 13: Invalid, or out of place expression:     F1()."
    ]


def test_bare_call_too_few_args_f2_needs_2():
    text = prog("    F2(1)\n")
    assert lines(text, "cr6") == [
        "line 13: Call argument required.",
        "line 13: Invalid, or out of place expression:     F2(1)."
    ]
    assert lines(text, "cr300") == [
        "line 13: Call argument required.",
        "line 13: Invalid, or out of place expression:     F2(1)",
        "."
    ]


# ------------------------------------------------------------------ correct argument counts


def test_bare_call_correct_args_f0():
    text = prog("    F0()\n")
    # F0 takes 0 args, F0() is correct
    assert lines(text, "cr6") == []
    assert lines(text, "cr300") == []


def test_bare_call_correct_args_f0_no_parens():
    text = prog("    F0\n")
    # F0 bare reference (no parens) is valid (same as F0() for 0-arg function)
    assert lines(text, "cr6") == []
    assert lines(text, "cr300") == []


def test_bare_call_correct_args_f1():
    text = prog("    F1(1)\n")
    # F1 takes 1 arg, F1(1) is correct
    assert lines(text, "cr6") == []
    assert lines(text, "cr300") == []


def test_bare_call_correct_args_f2():
    text = prog("    F2(1,2)\n")
    # F2 takes 2 args, F2(1,2) is correct
    assert lines(text, "cr6") == []
    assert lines(text, "cr300") == []
