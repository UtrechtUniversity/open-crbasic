"""CR6/CR1000X pass-1 statement scan (`checker/structure.py` `_pass1_scan`).

Oracle probes, CR6 (`docs/checker.md` "Pass-1 fallback: statements that start with a known
word"): a syntax error found before any unknown name is a pass-1 error (printed next to the
pass-1 `Invalid Variable declaration banana.`, and it suppresses pass 2); an unknown name found
first leaves the statement for pass 2.
"""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

DECLS = "Public Pi, X, S As String\nConst K = 3\nAlias X = AX\n"
PASS1_ERROR = "Invalid Variable declaration banana."


def messages(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


def program(line: str, pass1_error: bool) -> str:
    dim = "Dim Z banana\n" if pass1_error else ""
    return f"{DECLS}{dim}BeginProg\n{line}\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"


PASS1 = [
    ("  Pi", "Invalid, or out of place expression:   Pi."),
    ("  Pi bar", "Invalid, or out of place expression:   Pi bar."),
    ("  X banana split", "Invalid, or out of place expression:   X banana split."),
    ("  AX bar", "Invalid, or out of place expression:   AX bar."),
    ("  K bar", "Invalid, or out of place expression:   K bar."),
    ("  S bar", "Invalid, or out of place expression:   S bar."),
    ("  True bar", "Invalid, or out of place expression:   True bar."),
    ("  NAN bar", "Invalid, or out of place expression:   NAN bar."),
    ("  To bar", "Invalid, or out of place expression:   To bar."),
    ("  And bar", "Invalid, or out of place expression:   And bar."),
    ("  Mod bar", "Invalid, or out of place expression:   Mod bar."),
    ('  "abc" bar', 'Invalid, or out of place expression:   "abc" bar.'),
    ("  -1 bar", "Invalid, or out of place expression:   -1 bar."),
    ("  1 + 2", "Invalid, or out of place expression:   1 + 2."),
    ("  1 + (2", "Invalid, or out of place expression:   1 + (2."),
    ("  Pi 1", "Invalid, or out of place expression:   Pi 1."),
    ("  Pi = 1 +", "Invalid, or out of place expression:   Pi = 1 +."),
    ("  Pi = * 2", "Invalid, or out of place expression:   Pi = * 2."),
    ("  Pi = 1 bar", "Incomplete expression:   Pi = 1 bar."),
    ("  Pi = Not 1 2", "Incomplete expression:   Pi = Not 1 2."),
    ("  Pi = (1))", "Incomplete expression:   Pi = (1))."),
    ("  Pi = Sin(1) bar", "Incomplete expression:   Pi = Sin(1) bar."),
    ("  Not 1 2", "Incomplete expression:   Not 1 2."),
    ("(-1,Second,Sunday)", "Invalid, or out of place expression: (-1,Second,Sunday)."),
    ("(Pi,1,2)", "Invalid, or out of place expression: (Pi,1,2)."),
    ("Pi, 1, 2", "Invalid, or out of place expression: Pi, 1, 2."),
    ("(1)", "Invalid, or out of place expression: (1)."),
    ("(Pi + 1) 2", "Invalid, or out of place expression: (Pi + 1) 2."),
    ("Pi = (1, 2)", "No Matching, or out of place ) in expression: Pi = (1, 2)."),
    ("  Sin bar", "Expression expects ( :   Sin bar."),
    ("  X = Sin bar", "Expression expects ( :   X = Sin bar."),
]

PASS2 = [
    ("  foo bar", "Invalid, or out of place expression:   foo bar."),
    ("  Pi + Q", "Invalid, or out of place expression:   Pi + Q."),
    ("  Pi = Q bar", "Invalid symbol in expression:   Pi = Q bar."),
    ("  Pi = Not bar", "Invalid symbol in expression:   Pi = Not bar."),
    ("  Pi = Sin(Q) bar", "Invalid symbol in expression:   Pi = Sin(Q) bar."),
    ("  Pi = Q + 1 +", "Invalid symbol in expression:   Pi = Q + 1 +."),
    ("(Q,1,2)", "Invalid, or out of place expression: (Q,1,2)."),
    ("Q, 1, 2", "Invalid, or out of place expression: Q, 1, 2."),
]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("line,message", PASS1)
def test_pass1(line, message, logger):
    assert messages(program(line, True), logger) == [PASS1_ERROR, message]
    assert messages(program(line, False), logger) == [message]


@pytest.mark.parametrize("line,message", PASS2)
def test_pass2(line, message):
    assert messages(program(line, True)) == [PASS1_ERROR]
    assert messages(program(line, False)) == [message]


def test_pass1_error_suppresses_earlier_pass2_errors():
    text = f"{DECLS}BeginProg\n  X = Q\n  foo bar\n  Pi bar\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    assert messages(text) == ["Invalid, or out of place expression:   Pi bar."]


@pytest.mark.parametrize("line", ["  MySub bar", "  Tab bar", "  Then bar", "  Not bar"])
def test_unknown_to_pass1_words_stay_pass2(line):
    text = (f"{DECLS}Dim Z banana\nDataTable (Tab,1,-1)\nSample (1,Pi,FP2)\nEndTable\n"
            f"Sub MySub\nEndSub\nBeginProg\n{line}\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n")
    assert messages(text) == [PASS1_ERROR]
