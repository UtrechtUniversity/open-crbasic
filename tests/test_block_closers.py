"""Unmatched `EndIf`/`EndSelect`/`Next`/`Loop` (`checker/structure.py` `check_block_closers`).
Oracle probes, CR6 and CR300 identical, 2026-09-27 (`docs/checker.md` "Unmatched block ends")."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

ILLEGAL_IF = "IF or SELECT statement illegally has SCAN without NEXT SCAN."
NO_IF = "END IF without IF or END SELECT without SELECT."


def messages(text: str, logger: str = "cr6") -> list[tuple[int, str]]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return [(d.line, d.message) for d in sorted(diags.errors, key=lambda d: d.line)]


def scan(body: str) -> str:
    return f"Public X, I\nBeginProg\n  Scan (1,Sec,0,0)\n{body}  NextScan\nEndProg\n"


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize("body,expected", [
    ("    X = 1\n    EndIf\n", [(5, ILLEGAL_IF), (5, NO_IF)]),
    ("    Next X\n", [(4, "FOR NEXT statement illegally has SCAN without NEXT SCAN."),
                    (4, "NEXT without FOR.")]),
    ("    Loop\n", [(4, "LOOP statement illegally has SCAN without NEXT SCAN."),
                  (4, "LOOP without DO.")]),
    ("    EndSelect\n", [(4, "EndSelect w/o Select")]),
    ("    For I = 1 To 2\n      EndIf\n    Next\n",
     [(5, NO_IF), (6, "FOR NEXT statement illegally has SCAN without NEXT SCAN.")]),
    ("    If X Then\n      Next\n    EndIf\n", [(5, "NEXT without FOR."), (6, ILLEGAL_IF)]),
    ("    EndIf\n    EndIf\n", [(4, ILLEGAL_IF), (4, NO_IF), (5, NO_IF)]),
])
def test_unmatched_closers(body, expected, logger):
    assert messages(scan(body), logger) == expected


def test_outside_scan_only_the_without_message():
    text = "Public X\nBeginProg\n  EndIf\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    assert messages(text) == [(3, NO_IF)]


def test_is_pass2():
    text = "Public X\nDim Z banana\nBeginProg\n  Scan (1,Sec,0,0)\n    EndIf\n  NextScan\nEndProg\n"
    assert messages(text) == [(2, "Invalid Variable declaration banana.")]


def test_balanced_blocks_are_silent():
    body = ("    If X Then\n      For I = 1 To 2\n        Do\n        Loop Until X\n      Next\n"
            "    EndIf\n    Select Case X\n      Case 1\n    EndSelect\n")
    assert messages(scan(body)) == []
