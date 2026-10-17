"""`NextScan` without an open `Scan`, and `CallTable` outside `Scan..NextScan`
(`checker/structure.py` `check_pass2`, `_check_calltable_outside_scan`, `check_end_blocks`).
Oracle, CR6 and CR300, 2026-09-27 (`docs/checker.md` "Scan placement")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

TABLE = "Public X\nDataTable (T,1,{size})\n{interval}  Sample (1,X,FP2)\nEndTable\n"
DATA_INTERVAL = "  DataInterval(0,1,Min,10)\n"
AUTOALLOC = "Cannot autoallocate memory for DataTable T called outside of Scan, e.g., called by Sub."


def lines(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def table(size: str = "-1", interval: str = "") -> str:
    return TABLE.format(size=size, interval=interval)


LOGGERS = ["cr6", "cr1000x", "cr300", "cr350"]


@pytest.mark.parametrize("logger", LOGGERS)
def test_calltable_before_scan(logger):
    text = table(interval=DATA_INTERVAL) + "BeginProg\n  CallTable T\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    assert lines(text, logger) == [
        "line 7: Table with DataInterval must be called within Scan..NextScan.", f"line 7: {AUTOALLOC}"]
    text = table() + "BeginProg\n  CallTable T\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    assert lines(text, logger) == [f"line 6: {AUTOALLOC}"]


@pytest.mark.parametrize("logger", LOGGERS)
def test_fixed_size_table_outside_scan_is_fine(logger):
    text = table("100") + "BeginProg\n  CallTable T\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    assert lines(text, logger) == []


@pytest.mark.parametrize("logger", LOGGERS)
def test_calltable_in_sub(logger):
    text = (table() + "Sub S\n  CallTable T\nEndSub\n"
            "BeginProg\n  Scan (1,Sec,0,0)\n    Call S\n  NextScan\nEndProg\n")
    assert lines(text, logger) == [f"line 6: {AUTOALLOC}"]


@pytest.mark.parametrize("logger", LOGGERS)
def test_calltable_after_nextscan(logger):
    text = table() + "BeginProg\n  Scan (1,Sec,0,0)\n  NextScan\n  CallTable T\nEndProg\n"
    assert lines(text, logger) == [f"line 8: {AUTOALLOC}",
                                   "line 8: Warning: CallTable never reached at run time."]


@pytest.mark.parametrize("logger", LOGGERS)
def test_only_first_stray_nextscan_is_reported(logger):
    text = "Public X\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\n  NextScan\n  NextScan\nEndProg\n"
    assert lines(text, logger) == ["line 5: NextScan with no Scan."]


@pytest.mark.parametrize("logger,expected", [
    ("cr6", ["line 5: NextScan with no Scan."]),
    ("cr1000x", ["line 5: NextScan with no Scan."]),
    ("cr300", ["line 6: Q is not defined."]),
    ("cr350", ["line 6: Q is not defined."]),
])
def test_stray_nextscan_stops_pass2_on_cr6_and_is_late_on_cr300(logger, expected):
    text = "Public X\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\n  NextScan\n  X = Q\nEndProg\n"
    assert lines(text, logger) == expected


def test_stray_nextscan_is_suppressed_by_pass1_error():
    text = "Public X\nDim Z banana\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\n  NextScan\nEndProg\n"
    assert lines(text) == ["line 2: Invalid Variable declaration banana."]


FNOTCH = "Warning: Invalid fnotch value, must be 50,60 400 or 4000"


def fnotch_program(reps: str = "1", before: str = "", body: str = "", after: str = "") -> str:
    return (f"Public X, PTemp\nDataTable (T,1,-1)\n  Sample ({reps},X,FP2)\nEndTable\nBeginProg\n"
            f"{before}  Scan (1,Sec,0,0)\n    PanelTemp(PTemp,15000)\n{body}  NextScan\n{after}EndProg\n")


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
@pytest.mark.parametrize("kwargs,expected", [
    ({"reps": "0"}, ["line 3: Illegal Reps 0"]),
    ({"body": "    Do\n"}, [f"line 7: {FNOTCH}", "line 10: DO without LOOP."]),
    ({"after": "  NextScan\n"}, [f"line 7: {FNOTCH}", "line 9: NextScan with no Scan."]),
    ({"before": "  CallTable T\n"}, [f"line 6: {AUTOALLOC}", f"line 8: {FNOTCH}"]),
    ({"before": "  CallTable T\n", "body": "    Do\n"}, [f"line 6: {AUTOALLOC}", f"line 8: {FNOTCH}"]),
    ({"reps": "0", "before": "  CallTable T\n"}, ["line 3: Illegal Reps 0"]),
])
def test_cr300_late_order(kwargs, expected, logger):
    assert lines(fnotch_program(**kwargs), logger) == expected


@pytest.mark.parametrize("logger,expected", [
    ("cr6", [f"line 6: {AUTOALLOC}", "line 8: Invalid symbol in expression:     X = Q."]),
    ("cr300", ["line 8: Q is not defined."]),
])
def test_calltable_error_with_unrelated_pass2_error(logger, expected):
    text = table() + "BeginProg\n  CallTable T\n  Scan (1,Sec,0,0)\n    X = Q\n  NextScan\nEndProg\n"
    assert lines(text, logger) == expected


@pytest.mark.parametrize("logger", LOGGERS)
def test_calltable_error_and_stray_nextscan_together(logger):
    text = table() + "BeginProg\n  CallTable T\n  Scan (1,Sec,0,0)\n  NextScan\n  NextScan\nEndProg\n"
    assert lines(text, logger) == [f"line 6: {AUTOALLOC}", "line 9: NextScan with no Scan."]


MEASURE = {"PulseCount": "PulseCount (R,1,C1,2,0,0.01,0)",
           "Quadrature": "Quadrature (Q2(1),C1,0,1.0)",
           "TimerInput": "TimerInput (T(1),C1,0,2,0,usec)"}


def measure_program(sub: str = "", body: str = "", after: str = "") -> str:
    return (f"Public X, R, Q2(2), T(2)\nSub Foo\n{sub}EndSub\nBeginProg\n  Scan (1,Sec,0,0)\n{body}"
            f"  NextScan\n{after}EndProg\n")


@pytest.mark.parametrize("name,loggers", [("PulseCount", LOGGERS), ("Quadrature", LOGGERS),
                                          ("TimerInput", ["cr6", "cr1000x"])])
def test_measurement_outside_scan(name, loggers):
    message = f"{name} must be used within a Scan...NextScan."
    for logger in loggers:
        assert lines(measure_program(after=f"  {MEASURE[name]}\n"), logger) == [f"line 7: {message}"]
        assert lines(measure_program(sub=f"  {MEASURE[name]}\n", body="    Call Foo\n"),
                     logger) == [f"line 3: {message}"]


@pytest.mark.parametrize("logger,expected", [
    ("cr6", ["line 7: PulseCount must be used within a Scan...NextScan."]),
    ("cr300", ["line 8: Q is not defined."]),
])
def test_measurement_outside_scan_ends_pass2(logger, expected):
    text = measure_program(after=f"  {MEASURE['PulseCount']}\n  X = Q\n")
    assert lines(text, logger) == expected
