"""Unclosed blocks at end of program (`checker/structure.py` `check_end_blocks`) and the
reachability rule for an `If` left open at `NextScan`. Oracle, CR6 and CR300, 2026-09-27
(`docs/checker.md` "Unclosed blocks")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def prog(body: str) -> str:
    return f"Public X, I\nBeginProg\n  Scan (1,Sec,0,0)\n{body}"


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300"])
@pytest.mark.parametrize("body,expected", [
    ("    For I = 1 To 2\n      X = I\n  NextScan\nEndProg\n", ["line 7: FOR without NEXT."]),
    ("    Do\n      X = I\n  NextScan\nEndProg\n", ["line 7: DO without LOOP."]),
    ("    For I = 1 To 2\n    Do\n  NextScan\nEndProg\n",
     ["line 7: FOR without NEXT.", "line 7: DO without LOOP."]),
    ("    If X Then\n    For I = 1 To 2\n  NextScan\nEndProg\n",
     ["line 7: FOR without NEXT.", "line 7: IF or SELECT without EndIf or EndSelect."]),
    ("    For I = 1 To 2\n    If X Then\nEndProg\n",
     ["line 6: Scan without NextScan.", "line 6: FOR without NEXT.",
      "line 6: IF or SELECT without EndIf or EndSelect."]),
])
def test_unclosed_blocks(body, expected, logger):
    assert lines(prog(body), logger) == expected


@pytest.mark.parametrize("body,expected", [
    ("    If X Then\n  NextScan\n  X = Q\nEndProg\n",
     ["line 6: Invalid symbol in expression:   X = Q."]),
    ("    If X Then\n      X = Q\n  NextScan\nEndProg\n",
     ["line 5: Invalid symbol in expression:       X = Q."]),
])
def test_pass2_error_suppresses_unclosed_block_messages(body, expected):
    assert lines(prog(body)) == expected


def test_unclosed_scan_after_pass2_error_is_silent():
    assert lines("Public X\nBeginProg\n  X = Q\n  Scan (1,Sec,0,0)\nEndProg\n") == [
        "line 3: Invalid symbol in expression:   X = Q."]


def test_if_open_at_nextscan_hides_inner_statements_from_reachability():
    assert lines(prog("    If X Then\n  NextScan\n  X = Q\nEndProg\n")) == [
        "line 6: Invalid symbol in expression:   X = Q."]


def test_statement_closing_the_open_if_is_flagged():
    assert lines(prog("    If X Then\n  NextScan\n    EndIf\nEndProg\n")) == [
        "line 6: Warning: EndIf never reached at run time."]
