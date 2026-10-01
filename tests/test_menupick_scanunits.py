"""MenuPick placement/constant errors and Scan interval-unit errors. Expected lines are verbatim
oracle output (real compilers, 2026-10-01)."""

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


def menu_program(body: str) -> str:
    items = "".join(f"  {part}\n" for part in body.split(" / "))
    return (
        "Public X, Y\nConstTable\n  Const C = 1\n  Const D = 2\nEndConstTable\n"
        'DisplayMenu ("M",-1)\n' + items + "EndMenu\n"
        "BeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    )


MUST_FOLLOW = "MenuPick must follow a MenuItem instruction."
CONST_FIRST = (
    "When using a ConstTable constant in MenuPick, the constant name must be the first parameter."
)

MENU_ROWS = [
    ('MenuItem ("C",C) / MenuPick (C,1,2) / MenuPick (D)', [f"line 9: {MUST_FOLLOW}"]),
    ('MenuItem ("C",C) / MenuPick (1,2) / MenuPick (D)', [f"line 8: {CONST_FIRST}"]),
    ('MenuItem ("C",C) / MenuPick (D)', []),
    ('MenuItem ("C",C) / MenuPick (C) / MenuPick (C)', [f"line 9: {MUST_FOLLOW}"]),
    ('MenuItem ("C",C) / MenuPick (D,1,2)', []),
    ('MenuItem ("X",X) / MenuPick (C,1) / MenuPick (D)', []),
    ('MenuItem ("X",X) / MenuPick (1,2) / MenuPick (3,4) / MenuPick (5,6)', []),
    ('MenuItem ("X",X) / MenuPick (Y) / MenuPick (3,4)', ["line 8: Y must be declared as a Const."]),
    ('MenuItem ("X",X) / MenuPick (1,2) / MenuPick (Y)', ["line 9: Y must be declared as a Const."]),
]


@pytest.mark.parametrize("body,expected", MENU_ROWS)
def test_menupick(body, expected):
    text = menu_program(body)
    assert compile_lines(text, "cr6") == expected
    assert compile_lines(text, "cr1000x") == expected


SCAN_ARGS = ['1,"fuzz",0,0', "1,99,0,0", '1,"2",0,0', "1,7,0,0", "1,-1,0,0", '5,"",3,0']


@pytest.mark.parametrize("args", SCAN_ARGS)
def test_scan_units(args):
    text = f"Public X\nBeginProg\n  Scan({args})\n  NextScan\nEndProg\n"
    bad = ["line 3: Scan 0uSec invalid must be at least 1mSec."]
    assert compile_lines(text, "cr6") == bad
    assert compile_lines(text, "cr1000x") == bad
    assert compile_lines(text, "cr300") == []
