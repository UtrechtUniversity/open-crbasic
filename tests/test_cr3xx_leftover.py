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


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
@pytest.mark.parametrize("body,expected", [
    ("    (X,Y)", ["line 4: Invalid, or out of place expression: X,Y."]),
    ("    (X+1)", ["line 4: Invalid, or out of place expression: X+1."]),
    ("    (Nope,Y)", ["line 4: Nope is not defined.",
                      "line 4: Invalid, or out of place expression: Nope,Y."]),
])
def test_parenthesised_line_echoes_its_content(body, expected, logger):
    # oracle, CR300, 2026-10-01
    text = f"Public X, Y\nBeginProg\n  Scan (1,Sec,0,0)\n{body}\n  NextScan\nEndProg\n"
    assert compile_lines(text, logger) == expected


@pytest.mark.parametrize("stmt,expected", [
    ("Data foo bar", ["line 5: foo is not defined.", "line 5: bar is not defined.",
                      "line 5: Incomplete expression: foo bar\n.",
                      "line 5: Illegal Data parameter foo bar\n."]),
    ("Data Tables and Store Data", ["line 5: Tables is not defined.", "line 5: Store is not defined.",
                                    "line 5: Data is not defined.",
                                    "line 5: Incomplete expression: Tables and Store Data\n.",
                                    "line 5: Illegal Data parameter Tables and Store Data\n."]),
])
def test_data_with_juxtaposed_words(stmt, expected):
    # oracle, CR300, 2026-10-01
    text = f"Public X\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\n{stmt}\nEndProg\n"
    assert compile_lines(text, "cr300") == expected


@pytest.mark.parametrize("logger", ["cr6", "cr300"])
def test_data_is_never_flagged_unreachable(logger):
    text = "Public X\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nData 1,2\nEndProg\n"
    assert compile_lines(text, logger) == []


@pytest.mark.parametrize("stmt,expected", [
    ("foo Battery", ["line 4: foo is not defined.", "line 4: Battery is not defined."]),
    ("foo Next", ["line 4: foo is not defined.", "line 4: Next is not defined."]),
    ("foo Sin", ["line 4: foo is not defined."]),
    ("foo Scan", ["line 4: foo is not defined."]),
])
def test_instruction_keywords_in_prose_are_undefined(stmt, expected):
    # oracle, CR300, 2026-10-01: instruction-only keywords are not values
    text = f"Public X\nBeginProg\n  Scan (1,Sec,0,0)\n{stmt}\n  NextScan\nEndProg\n"
    got = [line for line in compile_lines(text, "cr300") if "is not defined" in line]
    assert got == expected


@pytest.mark.parametrize("stmt,expected", [
    ("PortSet(C4,1,1)", ["line 4: C4 is not defined.", "line 4: Variable illegal in Parameter: C4."]),
    ("PortSet(X,1,1)", ["line 4: Variable illegal in Parameter: X."]),
    ("Delay(Nope,1,Sec,9)", ["line 4: Nope is not defined.",
                             "line 4: Variable illegal in Parameter: Nope."]),
    ("Nope+=1", ["line 4: Nope is not defined."]),
])
def test_argument_errors_end_the_list(stmt, expected):
    # oracle, CR300, 2026-10-01: no genuine `Too many parameters` after `Variable illegal`
    text = f"Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    {stmt}\n  NextScan\nEndProg\n"
    assert compile_lines(text, "cr300") == expected


@pytest.mark.parametrize("stmt,expected", [
    ("PortSet(C4,1)", ["line 5: C4 is not defined.", "line 5: Variable illegal in Parameter: C4."]),
    ("PortSet(Nope+1,1)", ["line 5: Nope is not defined.",
                           "line 5: Variable illegal in Parameter: Nope+1."]),
])
def test_cascade_constant_first_argument(stmt, expected):
    # oracle, CR300, 2026-10-01: under the cascade, instead of `Too many parameters`
    text = f"Dim Z banana\nPublic X\nBeginProg\n  Scan (1,Sec,0,0)\n    {stmt}\n  NextScan\nEndProg\n"
    assert [l for l in compile_lines(text, "cr300") if l.startswith("line 5")] == expected


@pytest.mark.parametrize("stmt,expected", [
    ("PRT sensor using the", ["line 4: sensor is not defined.", "line 4: using is not defined.",
                              "line 4: Incomplete expression: sensor using the."]),
    ("PortSet sensor using the", ["line 4: sensor is not defined.", "line 4: using is not defined.",
                                  "line 4: Incomplete expression: sensor using the.",
                                  "line 4: Variable illegal in Parameter: sensor using the."]),
    ("Battery foo and bar baz", ["line 4: foo is not defined.", "line 4: bar is not defined.",
                                 "line 4: baz is not defined.",
                                 "line 4: Incomplete expression: foo and bar baz."]),
    ("Battery foo, bar", ["line 4: foo is not defined.", "line 4: Too many parameters:  bar."]),
])
def test_instruction_followed_by_prose(stmt, expected):
    # oracle, CR300, 2026-10-01
    text = f"Public X\nBeginProg\n  Scan (1,Sec,0,0)\n{stmt}\n  NextScan\nEndProg\n"
    assert compile_lines(text, "cr300") == expected
