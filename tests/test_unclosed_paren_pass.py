"""Pass membership of an unclosed `(` statement on CR6/CR1000X.

Oracle probes (`docs/checker.md` "Pass-1 fallback: statements that start with a known word"):
first token known and every name resolves -> pass-1 `No Matching, or out of place ) in
expression: %s.` (printed next to other pass-1 errors, and it suppresses pass 2); first token
unknown -> pass-2 generic fallback; a later unknown name -> pass-2 `Invalid symbol in expression`.
"""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

LOGGERS = ["cr6", "cr1000x"]


def messages(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


def with_pass1_error(line: str) -> str:
    return f"Public Pi, X\nDim Z banana\nBeginProg\n{line}\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"


def with_pass2_error(line: str) -> str:
    return f"Public Pi, X\nBeginProg\n  X = Q\n{line}\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("line", ["  Pi = 4 * ATN( 1", "  Pi = (1"])
def test_known_first_word_is_pass1(line, logger):
    no_match = f"No Matching, or out of place ) in expression: {line}."
    assert messages(with_pass1_error(line), logger) == ["Invalid Variable declaration banana.", no_match]
    assert messages(with_pass2_error(line), logger) == [no_match]


@pytest.mark.parametrize("logger", LOGGERS)
def test_unknown_first_word_is_pass2_fallback(logger):
    line = "  foo = 4 * ATN( 1"
    assert messages(with_pass1_error(line), logger) == ["Invalid Variable declaration banana."]
    assert messages(with_pass2_error(line), logger) == [
        "Invalid symbol in expression:   X = Q.",
        f"Invalid, or out of place expression: {line}.",
    ]


@pytest.mark.parametrize("logger", LOGGERS)
def test_later_unknown_name_is_pass2_invalid_symbol(logger):
    line = "  Pi = foo(1"
    assert messages(with_pass1_error(line), logger) == ["Invalid Variable declaration banana."]
    assert messages(with_pass2_error(line), logger) == [
        "Invalid symbol in expression:   X = Q.",
        f"Invalid symbol in expression: {line}.",
    ]
