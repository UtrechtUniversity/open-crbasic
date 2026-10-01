"""Scan interval too short for measurement (Delay budget). Expected lines are verbatim oracle
output (real compilers, 2026-10-01); CR300 has no such check."""

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


def simple(scan: str, delay: str) -> str:
    return (
        "Public X\nBeginProg\n"
        f"  Scan ({scan},0,0)\n    Delay(0,{delay})\n  NextScan\nEndProg\n"
    )


def short(by: str, line: int = 5) -> list[str]:
    return [f"line {line}: Scan interval too short for measurement by {by}."]


ROWS = [
    ("1,Sec", "1,Sec", short("1 Sec")),
    ("1,Sec", "2,Sec", short("2 Sec")),
    ("1,Sec", "1000,mSec", short("1 Sec")),
    ("100,mSec", "100,mSec", short("1 mSec")),
    ("100,mSec", "150,mSec", short("51 mSec")),
    ("50,mSec", "1,Sec", short("951 mSec")),
    ("1,Min", "1,Min", short("1 Min")),
    ("2,Sec", "1,Sec", []),
    ("2,Sec", "1990,mSec", []),
]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("scan,delay,expected", ROWS)
def test_scan_delay_budget(logger, scan, delay, expected):
    assert compile_lines(simple(scan, delay), logger) == expected


def test_cr300_has_no_check():
    assert compile_lines(simple("1,Sec", "1,Sec"), "cr300") == []


def test_full_program_delay():
    text = "Public X\nBeginProg\n  Scan (1,Sec,3,0)\n    Delay(0,1,Sec)\n  Next Scan\nEndProg\n"
    assert compile_lines(text, "cr6") == short("1 Sec")


def test_subscan_delay():
    text = (
        "Public X\nBeginProg\n  Scan (1,Sec,3,0)\n    SubScan (0,0,5)\n"
        "      Delay(0,1,Sec)\n    NextSubScan\n  NextScan\nEndProg\n"
    )
    assert compile_lines(text, "cr6") == short("5 Sec", 7)


def test_delay_in_if_not_counted():
    text = (
        "Public X\nBeginProg\n  Scan (1,Sec,3,0)\n    If X Then\n"
        "      Delay(0,1,Sec)\n    EndIf\n  NextScan\nEndProg\n"
    )
    assert not [x for x in compile_lines(text, "cr6") if "too short" in x]


CALIBRATION = ["line 6: Warning: When Main Sequence Scan 1 is running, background calibration "
               "will be disabled."]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("delay,expected", [
    ("98,mSec", CALIBRATION), ("99,mSec", CALIBRATION), ("97,mSec", []), ("92,mSec", []),
])
def test_little_spare_time_disables_background_calibration(delay, expected, logger):
    # oracle, 2026-10-01: under 3 mSec spare in a 100 mSec scan
    assert compile_lines(simple("100,mSec", delay), logger) == expected
