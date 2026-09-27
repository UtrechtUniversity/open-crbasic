"""Empty-parentheses calls, `DataTable` too many parameters, duplicate `DataInterval`, menu
placement and a stray `NextSubScan`. Oracle probes, 2026-09-27 (`docs/checker.md` "One-off
rules")."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

TABLE = "DataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\n"


def messages(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


def prog(line: str, table: str = TABLE) -> str:
    return (f"Public X, Y\n{table}BeginProg\n  Scan (1,Sec,0,0)\n{line}\n    CallTable T\n"
            "  NextScan\nEndProg\n")


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("line,expected", [
    ("    X = Int()", "Invalid, or out of place expression:     X = Int()."),
    ("    X = Sin() + 1", "Invalid, or out of place expression:     X = Sin() + 1."),
    ("    MenuRecompile(\"a\",X)", "MenuRecompile must be used within DisplayMenu..EndMenu."),
    ("    NextSubScan", "Next SubScan without SubScan."),
])
def test_statement_rules(line, expected, logger):
    assert messages(prog(line), logger) == [expected]


def test_cr300_empty_parens_keeps_the_newline():
    assert messages(prog("    X = Int()"), "cr300") == [
        "Invalid, or out of place expression:     X = Int()\n."]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300"])
def test_datatable_too_many_parameters(logger):
    table = 'DataTable (T,1,-1,"fuzz")\n  Sample (1,X,FP2)\nEndTable\n'
    assert messages(prog("    X = 1", table), logger)[0] == 'Too many parameters:  "fuzz".'


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300"])
def test_second_datainterval(logger):
    table = ("DataTable (T,1,-1)\n  DataInterval(0,1,sec,1)\n  DataInterval(0,1,sec,1)\n"
             "  Sample (1,X,FP2)\nEndTable\n")
    assert messages(prog("    X = 1", table), logger) == [
        "Only one DataInterval allowed within DataTable..EndTable."]


def test_menupick_outside_displaymenu():
    text = "Public X, Y\nMenuPick(TRUE,FALSE)\n" + TABLE + \
        "BeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n"
    assert messages(text) == ["MenuPick must be used within DisplayMenu..EndMenu."]


@pytest.mark.parametrize("alias", ["Alias CD16(29) = Spare2_1  3", "Alias CD16(29) = Sp x y",
                                   "Alias CD16(29) = Sp + 1", "Alias CD16(29) = Sp, Q2",
                                   "Alias Q = Sp As Long"])
def test_alias_ignores_text_after_the_name(alias):
    text = f"Public CD16(32), Q\n{alias}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    assert messages(text) == []


def test_alias_comma_names_are_all_declared():
    text = ("Public CD16(32)\nAlias CD16(1) = A1, B1\nBeginProg\n  Scan (1,Sec,0,0)\n    B1 = 2\n"
            "  NextScan\nEndProg\n")
    assert messages(text) == []


def test_alias_to_a_number():
    text = "Public CD16(32)\nAlias CD16(29) = 3\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    assert messages(text) == ["Variable name 3 illegal, starts with a number."]


@pytest.mark.parametrize("text,line", [("Public X\n\n\n", 3), ("' c\n\n", 2), ("Public X\n", 1)])
def test_no_beginprog_on_the_last_physical_line(text, line):
    profile = profiles.get("cr6")
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    assert [(d.line, d.message) for d in diags.errors] == [(line, "No BeginProg in Program.")]


def test_no_beginprog_does_not_hide_pass2_errors():
    assert messages("Public X\nY=EXP()\n") == [
        "Invalid, or out of place expression: Y=EXP().", "Expr illegally occurs before BeginProg."]
