"""Instructions, calls and assignments before `BeginProg` (`checker/structure.py`,
`profile.before_beginprog`). Oracle, all four loggers, 2026-09-27 (`docs/checker.md` "Instructions
before BeginProg")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

LOGGERS = ["cr6", "cr1000x", "cr300", "cr350"]
FNOTCH = "Warning: Invalid fnotch value, must be 50,60 400 or 4000"


def lines(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def prog(before: str, body: str = "") -> str:
    return (f"Public X, PTemp\nSub Foo\nEndSub\n{before}\nBeginProg\n  Scan (1,Sec,0,0)\n{body}"
            "  NextScan\nEndProg\n")


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("before,name", [
    ("Delay (0,1,Sec)", "Delay"), ("Randomize (1)", "Randomize"), ("PanelTemp (X,60)", "PanelTemp"),
    ("Battery (X)", "Battery"), ("PortSet (C1,1)", "PortSet"), ("Erase (X)", "Erase"),
    ("X = 1", "Expr"), ("Sin(1)", "Expr"), ("Foo", "Call"), ("Call Foo", "Call"),
])
def test_placement_error(before, name, logger):
    assert lines(prog(before), logger) == [f"line 4: {name} illegally occurs before BeginProg."]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
def test_cr6_placement_ends_pass2(logger):
    assert lines(prog("Randomize (1)", "    X = Q\n"), logger) == [
        "line 4: Randomize illegally occurs before BeginProg."]


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
@pytest.mark.parametrize("before", ["Randomize (1)", "Delay (0,1,Sec)", "X = 1", "Sin(1)", "Foo",
                                    "If X Then\nEndIf"])
def test_cr300_placement_is_late(before, logger):
    assert lines(prog(before, "    X = Q\n"), logger) == ["line 7: Q is not defined."
                                                          if "\n" not in before else
                                                          "line 8: Q is not defined."]


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
def test_cr300_placement_drops_later_warnings(logger):
    assert lines(prog("Randomize (1)", "    PanelTemp (PTemp,15000)\n"), logger) == [
        "line 4: Randomize illegally occurs before BeginProg."]


@pytest.mark.parametrize("before,loggers", [("SetStatus (\"USRDriveSize\",0)", ["cr6", "cr1000x"]),
                                            ("StationName (Test)", LOGGERS)])
def test_allowed_before_beginprog(before, loggers):
    for logger in loggers:
        assert lines(prog(before), logger) == []


@pytest.mark.parametrize("logger", LOGGERS)
def test_calltable_before_beginprog(logger):
    text = ("Public X, PTemp\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nCallTable T\n"
            "BeginProg\n  Scan (1,Sec,0,0)\n    PanelTemp (PTemp,15000)\n  NextScan\nEndProg\n")
    assert lines(text, logger) == [
        "line 5: Cannot autoallocate memory for DataTable T called outside of Scan, e.g., called by Sub.",
        "line 5: CallTable illegally occurs before BeginProg."]
