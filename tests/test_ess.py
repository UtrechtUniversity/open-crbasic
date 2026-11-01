"""ESSVariables declaration checks. Expected lines are oracle-confirmed (2026-10-01)."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.checker import ess
from opencrbasic.compiler import CompileResult

TAIL = "Public BattV\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"


def lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
def test_not_string(logger):
    text = "Public essNtcipSiteDescription As Long\nESSVariables Dim\n" + TAIL
    assert lines(text, logger) == ["line 2: ESS variable essNtcipSiteDescription must be declared as String"]


def test_string_size():
    text = "Public essNtcipSiteDescription As String * 16\nESSVariables Dim\n" + TAIL
    assert lines(text, "cr6") == ["line 2: ESS variable of type string must be of size 255"]


def test_second_dim_dimensions():
    text = "Public essNtcipSiteDescription As String * 255\nESSVariables Dim\nESSVariables Dim\n" + TAIL
    assert lines(text, "cr6") == [
        "line 3: ESS variable moduleDeviceNode must be dimensioned as 20",
        "line 3: ESS variable dayPlanActionNumberOID must be dimensioned as 10",
        "line 3: ESS variable eventConfigCompareOID must be dimensioned as 10",
        "line 3: ESS variable eventConfigLogOID must be dimensioned as 10",
    ]


def test_declared_after():
    text = "ESSVariables Dim\nPublic essBatteryStatus As Long\n" + TAIL
    assert lines(text, "cr6") == ["line 2: essBatteryStatus already declared as a variable."]


def test_declared_before():
    text = "Public essBatteryStatus As Long\nESSVariables Dim\n" + TAIL
    assert lines(text, "cr6") == []


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
def test_cr300_cascade(logger):
    text = "Public essBatteryStatus As Float\nESSVariables Dim\n" + TAIL
    out = lines(text, logger)
    assert out[0] == "line 2: ESS variable essBatteryStatus must be declared as Long"
    assert "line 5: Too many parameters:  Sec." in out


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
def test_data_loads(logger):
    rows = ess._table(str(profiles.get(logger).data_dir))
    assert len(rows) == 217
    row = next(r for r in rows if r["name"] == "moduleDeviceNode")
    assert row["type"] == "LONG_ARRAY16"
    assert row["count"] == 20
