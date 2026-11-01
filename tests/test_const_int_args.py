"""`Illegal parameter %s, expecting constant integer.` at type-code-4 positions
(`instr_rules.check_const_int_args`). Oracle, 2026-09-28 (`docs/type-codes.md`, code 4)."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(body: str, logger: str) -> list[str]:
    text = f"Public X, S\nBeginProg\n  Scan (1,Sec,0,0)\n{body}  NextScan\nEndProg\n"
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def illegal(text: str, line: int = 4) -> str:
    return f"line {line}: Illegal parameter {text}, expecting constant integer."


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize(
    "body,expected",
    [
        ("    SW12(1,1,Nope)\n", [illegal("Nope")]),
        ("    SW12(1,1,X)\n", [illegal("X")]),
        ("    SW12(1,1,Nope+1)\n", [illegal("Nope+1")]),
        ("    SW12(1,1,0.5)\n", [illegal("0.5")]),
        ('    SW12(1,1,"a")\n', [illegal('"a"')]),
        ("    PortSet(C1,1,Nope)\n", [illegal("Nope")]),
        ("    PortGet(X,C1,Nope)\n", [illegal("Nope")]),
        ("    SW12(1,1,Nope)\n    X = Q\n", [illegal("Nope")]),
        ("    X = Q\n    SW12(1,1,Nope)\n", [illegal("Nope", 5)]),
    ],
)
def test_const_int_args(body, expected, logger):
    assert lines(body, logger) == expected


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
def test_calibrate(logger):
    assert lines("    Calibrate(X,Nope)\n", logger) == [illegal("Nope")]


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
def test_swvx(logger):
    assert lines("    SwVx(1,1,1,Nope)\n", logger) == [illegal("Nope")]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize(
    "arg,echo",
    [
        ("1+2", "1+2"),
        ("(1+2)", "1+2"),
        ("K+1", "K+1"),
        ("1.0", "1.0"),
        ("1E0", "1E0"),
        ("- 1", "-"),
        ("K", None),
        ("(1)", None),
        ("-1", None),
        ("True", None),
        ("&H1", None),
        ("2", None),
    ],
)
def test_integer_token_forms(arg, echo, logger):
    text = f"Public X, S\nConst K = 1\nBeginProg\n  Scan (1,Sec,0,0)\n    PortSet(C1,1,{arg})\n  NextScan\nEndProg\n"
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    got = result.result_lines()[2:] if not result.ok else result.result_lines()[1:]
    assert got == ([illegal(echo, 5)] if echo else [])
