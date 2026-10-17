"""`SNMPVariable(MyTemp, OID, ...)` declares `MyTemp` (`checker/symbols.py`). Oracle, all four
loggers, 2026-09-28: the program below compiles."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

TEXT = ("Public X\nBeginProg\n  SNMPVariable(MyTemp,1.3.6.1.4.1.43592.55.1.4,Float,RONLY)\n"
        "  Scan (1,Sec,0,0)\n    MyTemp = X + 1\n  NextScan\nEndProg\n")


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
def test_snmp_variable_declares_its_name(logger):
    profile = profiles.get(logger)
    program, diags = parser.parse_text(TEXT, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    assert result.ok and not diags.items
