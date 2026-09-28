"""Display menu structure (`instr_data.check_table_and_menu_placement`, the end-of-program
`DisplayMenu without EndMenu.`). Expected lines are verbatim CR6 oracle output, 2026-09-28; CR1000X
matched the same probes (`docs/checker.md` "Display menus")."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


CASES = [
    ('n1', 'Public X, Y\nDisplayMenu ("M",-1)\n  MenuItem ("X",X)\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 7: DisplayMenu without EndMenu.']),
    ('n10', 'Public X, Y\nDisplayMenu ("M",-1)\n  MenuPick (1,2)\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n    X = Q\n  NextScan\nEndProg\n',
     ['line 3: MenuPick must follow a MenuItem instruction.', 'line 4: Empty DisplayMenu .. EndMenu.', 'line 7: Invalid symbol in expression:     X = Q.']),
    ('n11', 'Public X, Y\nDisplayMenu ("M",-1)\n  SubMenu ("S")\n    MenuItem ("X",X)\n  EndSubMenu\n  MenuPick (1,2)\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 6: MenuPick must follow a MenuItem instruction.']),
    ('n12', 'Public X, Y\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 2: EndMenu must be used within DisplayMenu..EndMenu.']),
    ('n2', 'Public X, Y\n  MenuItem ("X",X)\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 2: MenuItem must be used within DisplayMenu..EndMenu.', 'line 3: EndMenu must be used within DisplayMenu..EndMenu.']),
    ('n3', 'Public X, Y\nDisplayMenu ("M",-1)\n  MenuPick (1,2)\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 3: MenuPick must follow a MenuItem instruction.', 'line 4: Empty DisplayMenu .. EndMenu.']),
    ('n4', 'Public X, Y\nDisplayMenu ("M",-1)\n  MenuItem ("X",X)\n  MenuPick (1,2)\n  MenuPick (3,4)\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     []),
    ('n5', 'Public X, Y\nDisplayMenu ("M",-1)\n  SubMenu ("S")\n  MenuItem ("X",X)\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 5: SubMenu with no EndSubMenu.']),
    ('n6', 'Public X, Y\nDisplayMenu ("M",-1)\n  MenuItem ("X",X)\n  EndSubMenu\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 4: EndSubMenu without SubMenu.']),
    ('n7', 'Public X, Y\nDisplayMenu ("M",-1)\n  SubMenu ("S")\n  EndSubMenu\n  MenuPick (1,2)\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 4: Empty SubMenu ... EndSubMenu.', 'line 5: MenuPick must be used within DisplayMenu..EndMenu.', 'line 6: EndMenu must be used within DisplayMenu..EndMenu.']),
    ('n8', 'Public X, Y\nDisplayMenu ("M",-1)\n  MenuItem ("X",X)\n  DisplayValue ("Y",Y)\n  MenuPick (1,2)\nEndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
     ['line 5: MenuPick must follow a MenuItem instruction.']),
    ('n9', 'Public X, Y\nDisplayMenu ("M",-1)\n  MenuItem ("X",X)\nBeginProg\n  Scan (1,Sec,0,0)\n    X = Q\n  NextScan\nEndProg\n',
     ['line 6: Invalid symbol in expression:     X = Q.']),
]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("name,text,expected", CASES, ids=[c[0] for c in CASES])
def test_display_menus(name, text, expected, logger):
    assert lines(text, logger) == expected


MENU = ('Public A, D\nDisplayMenu("Config",-3)\n{body}EndMenu\nBeginProg\n  Scan (1,Sec,0,0)\n'
        '  NextScan\nEndProg\n')


@pytest.mark.parametrize("body", [
    '  MenuItem("A-Pick",A)\n  MenuPick(1,2)\n  MenuRecompile("Apply",D)\n  MenuPick(TRUE,FALSE)\n',
])
def test_menupick_after_menurecompile_is_accepted(body):
    assert lines(MENU.format(body=body), "cr6") == []
