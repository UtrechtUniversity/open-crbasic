"""Seed 49 fixes: constant-expression errors, constant-integer parameters, variable-parameter
errors, member arity, and EventCount. Expected lines are verbatim oracle output (real compilers,
2026-10-01). CR6 and CR1000X give the CR6 list; CR300 and CR350 give the CR300 list."""

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


B = "BeginProg\n  Scan (1,Sec,0,0)\n"
E = "  NextScan\nEndProg\n"

CR6_LOGGERS = ("cr6", "cr1000x")
CR300_LOGGERS = ("cr300", "cr350")
ALL_LOGGERS = CR6_LOGGERS + CR300_LOGGERS

EXPR = "Invalid, or out of place expression:"
SCAN = "line 4: Too many parameters:  Sec."
SERIAL_A = '    SerialOpen("a",115200,0,0,10000)'
PORT = "line 4: ComPort 0 illegal."
CONST_INT = "Parameter must be a constant integer:"
VARIABLE = "Invalid Variable or Array:"
PAREN = "No Matching, or out of place ) in expression:"

# (id, program text, cr6 expected, cr300 expected)
ROWS = [
    (
        "const_expr_comment",
        "Const Pi = 4*ATN() 'c\nPublic X\n" + B + E,
        [f"line 1: {EXPR} 4*ATN() ."],
        [f"line 1: {EXPR} 4*ATN() .", SCAN],
    ),
    (
        "const_expr_plain",
        "Const Pi = 4*ATN()\nPublic X\n" + B + E,
        [f"line 1: {EXPR} 4*ATN()."],
        [f"line 1: {EXPR} 4*ATN().", SCAN],
    ),
    (
        "serialopen_string_comport_then_undefined",
        "Public X\n" + B + SERIAL_A + "\n    X = Nope\n" + E,
        [PORT, f"line 4: {CONST_INT} {SERIAL_A}."],
        [PORT, f"line 4: {CONST_INT} {SERIAL_A}\n.", "line 5: Nope is not defined."],
    ),
    (
        "realtime_constant_then_undefined",
        "Public X, A(9)\n" + B + "    RealTime(5)\n    X = Nope\n" + E,
        [f"line 4: {VARIABLE} 5."],
        [f"line 4: {VARIABLE} 5.", "line 5: Nope is not defined."],
    ),
    (
        "readio_paren_expression",
        "Public X, A(9)\n" + B + "    ReadIO((1+2),&B1)\n" + E,
        [f"line 4: {VARIABLE} (1+2)."],
        [f"line 4: {VARIABLE} (1+2).", "line 4: Too many parameters:  &B1."],
    ),
]


@pytest.mark.parametrize("text,cr6,cr300", [r[1:] for r in ROWS], ids=[r[0] for r in ROWS])
def test_seed49_rows(text, cr6, cr300):
    for logger in CR6_LOGGERS:
        assert lines(text, logger) == cr6, logger
    for logger in CR300_LOGGERS:
        assert lines(text, logger) == cr300, logger


def test_serialopen_variable_comport():
    text = "Public X\n" + B + "    SerialOpen(X,115200,0,0,10000)\n" + E
    expected = [PORT, f"line 4: {CONST_INT}     SerialOpen(X,115200,0,0,10000)."]
    for logger in CR6_LOGGERS:
        assert lines(text, logger) == expected, logger


def test_serialopen_comment_all_loggers():
    text = "Public X\n" + B + SERIAL_A + " 'c\n" + E
    expected = [PORT, f"line 4: {CONST_INT} {SERIAL_A} ."]
    for logger in ALL_LOGGERS:
        assert lines(text, logger) == expected, logger


CR300_ONLY = [
    (
        "realtime_then_portset",
        "Public X, A(9)\n" + B + "    RealTime(5)\n    PortSet(C1,1)\n" + E,
        [f"line 4: {VARIABLE} 5.", "line 5: Too many parameters:  1."],
    ),
    (
        "battery_paren_then_portset",
        "Public X, A(9)\n" + B + "    Battery((1+2))\n    PortSet(C1,1)\n" + E,
        [
            "line 4: Illegal destination variable type, expecting Float.",
            "line 5: Too many parameters:  1.",
        ],
    ),
]


@pytest.mark.parametrize("text,expected", [r[1:] for r in CR300_ONLY],
                         ids=[r[0] for r in CR300_ONLY])
def test_cr300_only(text, expected):
    for logger in CR300_LOGGERS:
        assert lines(text, logger) == expected, logger


# Member arity.
ARITY_PROGRAM = (
    "Public X, T As Long, V(3)\nDataTable (Tb,1,-1)\n  Sample (1,X,FP2)\nEndTable\n"
    + B + "    CallTable Tb\n    {stmt}\n" + E
)

ARITY_BAD = [
    "T = Tb.Timestamp(1,1,1)",
    "X = Tb.X(1,1,1)",
    "X = Tb.Record(1,1,1)",
    "X = Public.V(1,1,1)",
    "X = Status.Battery(1,1,1)",
    "X = Tb.Output(1,1,1)",
]

ARITY_OK = [
    "T = Tb.Timestamp(1,1)",
    "X = Tb.X(1)",
    "X = Public.X(1,1)",
    "X = Status.Battery(1,1)",
]


@pytest.mark.parametrize("stmt", ARITY_BAD)
def test_member_arity_bad(stmt):
    text = ARITY_PROGRAM.format(stmt=stmt)
    cr6 = [f"line 8: {PAREN}     {stmt}."]
    cr300 = [f"line 8: {PAREN}     {stmt}\n."]
    for logger in CR6_LOGGERS:
        assert lines(text, logger) == cr6, logger
    for logger in CR300_LOGGERS:
        assert lines(text, logger) == cr300, logger


@pytest.mark.parametrize("stmt", ARITY_OK)
def test_member_arity_ok(stmt):
    text = ARITY_PROGRAM.format(stmt=stmt)
    for logger in ALL_LOGGERS:
        assert lines(text, logger) == [], logger


# EventCount. With no DataEvent line, CallTable is line 7 and BODY starts on line 8.
def event_program(data_event: str, body: str) -> str:
    de = f"{data_event}\n" if data_event else ""
    return (
        "Public X\nDataTable (Tb,1,-1)\n" + de + "  Sample (1,X,FP2)\nEndTable\n"
        + B + "    CallTable Tb\n" + body + "\n" + E
    )


EVENT_MSG = "DataTable Tb must have DataEvent() to request EventCount."


def test_eventcount_without_dataevent_then_error():
    text = event_program("", "    X = Tb.EventCount(1,1)\n    X = Nope")
    cr6 = [f"line 8: {EVENT_MSG}", "line 9: Invalid symbol in expression:     X = Nope."]
    cr300 = ["line 9: Nope is not defined."]
    for logger in CR6_LOGGERS:
        assert lines(text, logger) == cr6, logger
    for logger in CR300_LOGGERS:
        assert lines(text, logger) == cr300, logger


def test_eventcount_in_if_without_dataevent():
    text = event_program("", "    If Tb.EventCount(1,1) > 0 Then X = 1")
    for logger in ALL_LOGGERS:
        assert lines(text, logger) == [f"line 8: {EVENT_MSG}"], logger


def test_eventcount_with_dataevent():
    text = event_program("  DataEvent(0,X>1,X<1,0)", "    X = Tb.EventCount(1,1)")
    for logger in ALL_LOGGERS:
        assert lines(text, logger) == [], logger
