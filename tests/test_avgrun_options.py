"""AvgRun optional Count and instance parameters. Expected lines are verbatim oracle output
(real compilers, 2026-10-01)."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

LOGGERS = ["cr6", "cr1000x", "cr300", "cr350"]
FULL = ["cr6", "cr1000x"]


def lines(body: str | list[str], logger: str) -> list[str]:
    if isinstance(body, str):
        body = [body, "A1 = 1"]
    body_text = "".join(f"    {b}\n" for b in body)
    text = (
        "Public A1, R As Boolean, CF, CS As String, CL As Long, CB As Boolean, "
        "LA(3) As Long, FA(3)\nConst K = 2\nBeginProg\n  Scan (1,Sec,0,0)\n"
        f"{body_text}  NextScan\nEndProg\n"
    )
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


COUNT_TYPE = "line 5: Optional Count parameter must be type long."
INSTANCES_CONSTANT = "line 5: Optional number of distinct instances must be a constant of type long."
MISSING_INSTANCE = "line 5: Missing instance number"


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("count", ["CF", "CS", "FA(2)"])
def test_count_must_be_long(count, logger):
    assert lines(f"AvgRun (A1,1,CF,100,R,{count})", logger) == [COUNT_TYPE]


@pytest.mark.parametrize("logger", LOGGERS)
def test_count_and_condition_both_float(logger):
    assert lines("AvgRun (A1,1,CF,100,CF,CF)", logger) == [COUNT_TYPE]


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("count", ["CB", "5", "CL", "LA(2)", "CL+1"])
def test_count_accepted(count, logger):
    assert lines(f"AvgRun (A1,1,CF,100,R,{count})", logger) == []


@pytest.mark.parametrize("logger", FULL)
@pytest.mark.parametrize("instances", ["2", "K", "2.5"])
def test_instances_without_instance_number_full(instances, logger):
    assert lines(f"AvgRun (A1,1,CF,100,R,CL,{instances})", logger) == [MISSING_INSTANCE]


@pytest.mark.parametrize("logger", FULL)
@pytest.mark.parametrize("instances", ["0", "CL"])
def test_instances_not_a_positive_constant_full(instances, logger):
    assert lines(f"AvgRun (A1,1,CF,100,R,CL,{instances})", logger) == [
        INSTANCES_CONSTANT,
        MISSING_INSTANCE,
    ]


@pytest.mark.parametrize("logger", FULL)
def test_count_and_instances_errors_accumulate_full(logger):
    assert lines("AvgRun (A1,1,CF,100,R,CF,0)", logger) == [
        COUNT_TYPE,
        INSTANCES_CONSTANT,
        MISSING_INSTANCE,
    ]


@pytest.mark.parametrize("logger", FULL)
@pytest.mark.parametrize("tail", ["2,1", "2,CL", "K,FA(1)"])
def test_instances_with_instance_number_full(tail, logger):
    assert lines(f"AvgRun (A1,1,CF,100,R,CL,{tail})", logger) == []


@pytest.mark.parametrize("logger", LOGGERS)
def test_running_average_must_be_positive(logger):
    assert lines(["AvgRun (A1,0,CF,5)", "AvgRun (A1,1,CF,-3)"], logger) == [
        "line 5: number of averages 5 and reps 0 in RunningAvg must be > 0.",
        "line 6: number of averages -3 and reps 1 in RunningAvg must be > 0.",
    ]


@pytest.mark.parametrize("logger", FULL)
def test_pass_count_type_before_symbol_error_full(logger):
    assert lines(["AvgRun (A1,1,CF,100,R,CF)", "Randomize(Nope)"], logger) == [
        COUNT_TYPE,
        "line 6: Invalid symbol in expression: Nope.",
    ]


@pytest.mark.parametrize("logger,channel", [("cr6", "U1..U12"), ("cr1000x", "1..16")])
def test_pass_count_type_before_channel_error_full(logger, channel):
    assert lines(
        ["AvgRun (A1,1,CF,100,R,CF)", "VoltSe(A1,1,mV1000,99,True,0,15000,1,0)"], logger
    ) == [
        COUNT_TYPE,
        f"line 6: Invalid channel 99, must use {channel}.",
    ]


@pytest.mark.parametrize("logger", FULL)
def test_pass_running_average_before_symbol_error_full(logger):
    assert lines(["AvgRun (A1,1,CF,0,R,CL)", "Randomize(Nope)"], logger) == [
        "line 5: number of averages 0 and reps 1 in RunningAvg must be > 0.",
        "line 6: Invalid symbol in expression: Nope.",
    ]
