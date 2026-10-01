"""Leftover-text errors on CR300/CR350 (statement fragments and the `Too many parameters`
cascade). Expected lines are verbatim oracle output (CR300, 2026-10-01); CR350 is identical."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def compile_lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def program_a(left: str) -> str:
    return (
        "Public X, Y\nBeginProg\n  Scan (1,Sec,0,0)\n    VoltSe\n"
        f"{left}\n    PortSet(C1,1)\n  NextScan\nEndProg\n"
    )


def program_b(stmt: str) -> str:
    return (
        "Public X, Y\nBeginProg\n  Scan (1,Sec,0,0)\n"
        f"{stmt}\n    PortSet(C1,1)\n  NextScan\nEndProg\n"
    )


IMP = "Invalid, or out of place expression:"
INV = "line 4: Invalid or missing parameter."
TOO = "line 6: Too many parameters:  1."
ARGS = "X,1,mV1000,1,0,0,60,1,0"
A_FULL = [INV, f"line 5: {IMP} {ARGS}.", TOO]

A_ROWS = [
    (f"({ARGS})", A_FULL),
    (f"  ({ARGS})  ", A_FULL),
    (f"({ARGS}) 'c", A_FULL),
    (f"({ARGS}", [INV, f"line 5: {IMP} {ARGS}\n.", TOO]),
    ("(X)", [INV, f"line 5: {IMP} X.", TOO]),
]


@pytest.mark.parametrize("left,expected", A_ROWS)
def test_program_a(left, expected):
    text = program_a(left)
    assert compile_lines(text, "cr300") == expected
    assert compile_lines(text, "cr350") == expected


def test_program_a_cr6_no_cascade():
    out = compile_lines(program_a(f"({ARGS})"), "cr6")
    assert not any("Too many parameters" in line for line in out)


TOO5 = "line 5: Too many parameters:  1."

B_ROWS = [
    ("    VoltSe", ["line 4: Invalid or missing parameter.", TOO5]),
    ("    X = 1 +", [f"line 4: {IMP}     X = 1 +\n.", TOO5]),
    ("    , X", [f"line 4: {IMP}     , X\n.", TOO5]),
    ("    instruction.", [
        "line 4: Could not find DataTable instruction.",
        f"line 4: {IMP}     instruction.\n.",
    ]),
]


@pytest.mark.parametrize("stmt,expected", B_ROWS)
def test_program_b(stmt, expected):
    text = program_b(stmt)
    assert compile_lines(text, "cr300") == expected
    assert compile_lines(text, "cr350") == expected
