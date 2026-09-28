"""Duplicate `Units` (`instr_data.check_units_targets`) and the missing-include warning's drive
prefix (`parser._include_warning_drives`). Oracle, all four loggers, 2026-09-28
(`docs/checker.md` "Duplicate Units, missing includes")."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

LOGGERS = ["cr6", "cr1000x", "cr300", "cr350"]


def lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def units(decls: str, body: str = "") -> str:
    return (f"Public X, Y(3)\n{decls}BeginProg\n  Scan (1,Sec,0,0)\n{body}  NextScan\nEndProg\n")


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("decls,body,expected", [
    ("Units X = C\nUnits X = K\n", "",
     ["line 2: X already assigned Units C.", "line 3: X already assigned Units K."]),
    ("Units Y = C\nUnits Y = K\n", "",
     ["line 2: Y already assigned Units C.", "line 3: Y already assigned Units K."]),
    ("Units x = C\nUnits X = K\n", "",
     ["line 2: x already assigned Units C.", "line 3: X already assigned Units K."]),
    ("Units X = deg C\nUnits X = K\n", "",
     ["line 2: X already assigned Units deg C.", "line 3: X already assigned Units K."]),
    ("Units X = C\nUnits Y = K\nUnits X = m\nUnits X = s\n", "",
     ["line 2: X already assigned Units C."] * 2 + ["line 4: X already assigned Units m."] * 2
     + ["line 5: X already assigned Units s."] * 2),
])
def test_duplicate_units(decls, body, expected, logger):
    assert lines(units(decls, body), logger) == expected


@pytest.mark.parametrize("logger", ["cr6", "cr300"])
def test_duplicate_units_is_late(logger):
    expected = {"cr6": ["line 6: Invalid symbol in expression:     X = Q."],
                "cr300": ["line 6: Q is not defined."]}[logger]
    assert lines(units("Units X = C\nUnits X = K\n", "    X = Q\n"), logger) == expected


@pytest.mark.parametrize("logger,expected", [
    ("cr6", "NoSuch.crb"), ("cr1000x", "NoSuch.crb"),
    ("cr300", "CPU:NoSuch.crb"), ("cr350", "CPU:NoSuch.crb"),
])
def test_missing_include_drive(logger, expected):
    text = 'Public X\nInclude "CPU:NoSuch.crb"\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n'
    assert lines(text, logger) == [f"line 2: Warning: Cannot open include file  {expected}."]
