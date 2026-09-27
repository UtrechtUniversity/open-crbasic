"""CR300/CR350: declaration errors start the `Too many parameters` cascade, and a tainted
declaration's own error is dropped. Oracle, both compilers identical, 2026-09-27
(`docs/loggers.md` "Declaration errors taint")."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def messages(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


BODY = "BeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
@pytest.mark.parametrize("decls,first", [
    ("Public id As Long\nPublic id As Long\n", "id already declared as a variable."),
    ("Const A = 1\nPublic A\n", "A is already declared as a CONST."),
    ("Public X\nPublic A As StringX * 50\nDim Z banana\n", "Variable Type StringX not supported"),
])
def test_declaration_error_taints_scan(decls, first, logger):
    assert messages(decls + BODY, logger) == [first, "Too many parameters:  Sec."]


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
def test_no_pass_gate(logger):
    # Oracle: pass-2 findings are still reported after a pass-1 error on CR300/CR350.
    text = "Public X\nDim Z banana\nBeginProg\n  X = Q\n  foo bar\n  X = 1 +\nEndProg\n"
    assert messages(text, logger) == [
        "Invalid Variable declaration banana.", "Q is not defined.", "foo is not defined.",
        "bar is not defined.", "Invalid, or out of place expression:   foo bar\n.",
        "Invalid, or out of place expression:   X = 1 +\n."]


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
@pytest.mark.parametrize("line,expected", [
    ("Nope = Q", ["Nope is not defined.", "Q is not defined."]),
    ("Nope = 1", ["Nope is not defined."]),
    ("Nope(2) = X", ["Nope is not defined.", "Invalid, or out of place expression: Nope(2) = X\n."]),
])
def test_undeclared_assignment_target(line, expected, logger):
    text = f"Public X\n{line}\nBeginProg\n  X = 1\n  Y2 = 3\nEndProg\n"
    assert messages(text, logger) == expected + ["Y2 is not defined."]
