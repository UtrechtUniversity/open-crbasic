"""Parenthesis and expression-shape errors. Expected lines are verbatim oracle output (real
compilers, 2026-10-01); CR6 and CR1000X are identical, CR350 is identical to CR300."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


BODY = "BeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
PAREN = "No Matching, or out of place ) in expression:"

ALIAS_CR6 = [f"line 2: {PAREN} (1+2.", "line 2: Alias is illegally dimensioned."]

ROWS = [
    (
        "Public V(4)\nAlias V((1+2))=WatchDog\n" + BODY,
        ALIAS_CR6,
        ALIAS_CR6 + ["line 4: Too many parameters:  Sec."],
    ),
    (
        "Public V(4,4)\nAlias V(2,(1+2)) = W\n" + BODY,
        ALIAS_CR6,
        ALIAS_CR6 + ["line 4: Too many parameters:  Sec."],
    ),
    (
        "Public V(4)\nAlias V((1))=W\n" + BODY,
        [f"line 2: {PAREN} (1.", "line 2: Alias is illegally dimensioned."],
        [f"line 2: {PAREN} (1.", "line 2: Alias is illegally dimensioned.",
         "line 4: Too many parameters:  Sec."],
    ),
    (
        "Const CRLF = CHR(13,1) + CHR(10)\n" + BODY,
        [f"line 1: {PAREN} CHR(13,1) + CHR(10)."],
        [f"line 1: {PAREN} CHR(13,1) + CHR(10).", "line 3: Too many parameters:  Sec."],
    ),
    (
        "Public X\nBeginProg\n  X = ASin( 1\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n",
        None,
        [f"line 3: {PAREN}   X = ASin( 1\n.", "line 4: Too many parameters:  Sec."],
    ),
    (
        "Public X\nBeginProg\n  X = ATN2(1)\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n",
        None,
        ["line 3: Expression expects , :   X = ATN2(1)\n.",
         "line 4: Too many parameters:  Sec."],
    ),
    (
        "Public X\nBeginProg\n  X = (1))\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n",
        None,
        ["line 3: Incomplete expression:   X = (1))\n.", "line 4: Too many parameters:  Sec."],
    ),
    (
        "Public X\nBeginProg\n  X = Sin\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n",
        None,
        ["line 3: Expression expects ( :   X = Sin\n.", "line 4: Too many parameters:  Sec."],
    ),
]


@pytest.mark.parametrize("text,cr6,cr300", ROWS)
def test_paren_errors(text, cr6, cr300):
    if cr6 is not None:
        assert lines(text, "cr6") == cr6
        assert lines(text, "cr1000x") == cr6
    assert lines(text, "cr300") == cr300
    assert lines(text, "cr350") == cr300
