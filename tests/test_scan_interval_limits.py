"""`DataTable interval ... not integrally divisible ...` units and `Maximum Scan interval is 1 Day.`
(`checker/instr_rules.py` `_divisibility_message`, `check_scan_max_interval`). Oracle, all four
loggers, 2026-09-27 (`docs/instructions.md` "Scan and table interval limits")."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

LOGGERS = ["cr6", "cr1000x", "cr300", "cr350"]


def lines(scan: str, interval: str, logger: str) -> list[str]:
    text = (f"Public X\nDataTable (T,1,-1)\n  DataInterval (0,{interval},10)\n  Sample (1,X,FP2)\n"
            f"EndTable\nBeginProg\n  Scan ({scan},1,0)\n    CallTable T\n  NextScan\nEndProg\n")
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def divisible(dt: str, scan: str) -> str:
    return f"line 8: DataTable interval {dt} not integrally divisible by scan interval {scan}."


MAXIMUM = "line 7: Maximum Scan interval is 1 Day."


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("scan,interval,expected", [
    ("7,mSec", "1,Sec", [divisible("1000000 uSec", "7000 uSec")]),
    ("7,Sec", "1,Hr", [divisible("3600000000 uSec", "7000000 uSec")]),
    ("1,Day", "1,Hr", [divisible("3600000000 uSec", "86400000000 uSec")]),
    ("2,Hr", "3,Hr", [divisible("10800 Sec", "7200 Sec")]),
    ("4295,Sec", "1440,Min", [divisible("86400 Sec", "4295 Sec")]),
    ("86400,Sec", "1,Day", []),
    ("86401,Sec", "1,Day", [MAXIMUM, divisible("86400 Sec", "86401 Sec")]),
    ("1441,Min", "1,Day", [MAXIMUM, divisible("86400 Sec", "86460 Sec")]),
    ("2,Day", "1,Day", [MAXIMUM, divisible("86400 Sec", "172800 Sec")]),
])
def test_interval_limits(scan, interval, expected, logger):
    assert lines(scan, interval, logger) == expected


@pytest.mark.parametrize("logger,expected", [
    ("cr6", ["line 7: Scan Interval parameter must be a non-negative integer"]),
    ("cr350", []),
])
def test_interval_past_int32_is_not_checked(logger, expected):
    assert lines("999999999999,Sec", "1440,Min", logger) == expected
