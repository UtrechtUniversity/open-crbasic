"""Port-terminal argument errors. Expected lines are verbatim oracle output (real compilers,
2026-10-01) for the shared port converter."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(instruction: str, logger: str) -> list[str]:
    text = (
        "Public V, VA(10), L As Long, LA(10) As Long\nBeginProg\n  Scan (1,Sec,0,0)\n"
        f"    {instruction}\n  NextScan\nEndProg\n"
    )
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


def invalid_port(n: int | str) -> str:
    return f"line 4: Invalid port {n}, use C1..C4 or U1..U12."


def invalid_port_x(n: int | str) -> str:
    return f"line 4: Invalid port {n}, use C1..C8."


SPI_PORT = "line 4: SPI Port must be C1, U1, U5, or U9."
I2C_PORT = "line 4: I2C port must be 1,3,5,7..."
TIMER_START = "line 4: TimerInput starting channel must be C1 or U1."
SDI12_PORT = "line 4: Invalid SDI-12 port, must be C1,C3 or U1,U3...U11"
AM25T_START = "line 4: Invalid start channel for Differential Measurement. Must be U1, U3, U5 ..."

CR6_CASES = [
    ("SPIOpen(3,100000,&H60)", [invalid_port(3)]),
    ("SPIOpen(117,100000,&H60)", [invalid_port(117)]),
    ("SPIOpen(213,100000,&H60)", [invalid_port(213)]),
    ("SPIOpen(0,100000,&H60)", [invalid_port(0)]),
    ("SPIOpen(104,100000,&H60)", [SPI_PORT]),
    ("SPIOpen(212,100000,&H60)", [SPI_PORT]),
    ("SPIOpen(101,100000,&H60)", []),
    ("SPIOpen(105,100000,&H60)", []),
    ("SPIOpen(109,100000,&H60)", []),
    ("SPIOpen(201,100000,&H60)", []),
    ("PortPairConfig(3,2)", [invalid_port(3)]),
    ("PortPairConfig((1+2),2)", [invalid_port(3)]),
    ("PWM(V,3,200,uSec)", [invalid_port(3)]),
    ("I2COpen(3,1000)", [invalid_port(3)]),
    ("I2COpen(102,1000)", [I2C_PORT]),
    ("I2COpen(105,1000)", []),
    ("MuxSelect(C1,3,20,1,1)", [invalid_port(3)]),
    ("MuxSelect(3,C2,20,1,1)", [invalid_port(3)]),
    ("PulsePort(117,5000)", [invalid_port(117)]),
    ("PulseCount(V,1,3,2,0,1,0)", [invalid_port(3)]),
    ("WaitDigTrig(3,0)", [invalid_port(3)]),
    ("TimerInput(V,3,0,2,0,usec)", [invalid_port(3)]),
    ("TimerInput(V,202,0,2,0,usec)", [TIMER_START]),
    ("TimerInput(V,105,0,2,0,usec)", []),
    ("TimerInput(V,201,0,2,0,usec)", []),
    ("PortSet(105,1)", []),
    ("PortSet(109,1)", []),
    ("PortGet(V,105)", []),
    ("CS616(VA(1),1,U1,109,1,1,0)", []),
    ("PWM(V,105,200,uSec)", []),
    ("WaitDigTrig(105,1)", []),
    ("PortSet(117,1)", [invalid_port(117)]),
    (
        "AM25T(VA(),2,mV200C,1,1,TypeT,V,3,C2,U1,True,0,60,1,0)",
        ["line 4: Invalid channel 1, must use U1..U12.", invalid_port(3)],
    ),
    ("AM25T(VA(),2,mV200C,1,U2,TypeT,V,C1,C2,U3,True,0,60,1,0)", [AM25T_START]),
    (
        "AM25T(VA(),2,mV200C,1,U1,TypeT,V,C1,C2,3,True,0,60,1,0)",
        ["line 4: Invalid channel 3, must use U1..U12."],
    ),
    ("CWB100RSSI(102)", [SDI12_PORT]),
    ("CWB100RSSI(105)", [SDI12_PORT]),
    ("SDI12SensorSetup(4,102,0,4)", [SDI12_PORT]),
    ("CWB100RSSI(C1)", []),
    ("SDI12SensorSetup(4,C1,0,4)", []),
    ("Quadrature(VA(),C1,0,1)", []),
]

CR1000X_CASES = [
    ("SPIOpen(3,100000,&H60)", [invalid_port_x(3), SPI_PORT]),
    ("SPIOpen(109,100000,&H60)", [invalid_port_x(109), SPI_PORT]),
    ("SPIOpen(104,100000,&H60)", [SPI_PORT]),
    ("SPIOpen(101,100000,&H60)", []),
    ("SPIOpen(105,100000,&H60)", []),
    ("I2COpen(3,1000)", [invalid_port_x(3), I2C_PORT]),
    ("I2COpen(102,1000)", [I2C_PORT]),
    ("PortGet(V,3)", ["line 4: Invalid port."]),
    ("PortGet(V,109)", ["line 4: Invalid port."]),
    ("PortSet(109,1)", [invalid_port_x(109)]),
    ("PortPairConfig(3,2)", [invalid_port_x(3)]),
    (
        "TimerInput(V,3,0,2,0,usec)",
        [invalid_port_x(3), "line 4: TimerInput starting channel must be C1."],
    ),
    ("TimerInput(V,105,0,2,0,usec)", ["line 4: TimerInput starting channel must be C1."]),
    (
        "WaitDigTrig(300,0)",
        [
            invalid_port_x(300),
            "line 4: WaitDigTrig must use edge triggering in Scan..NextScan.",
        ],
    ),
    (
        "Quadrature(VA(),3,0,1)",
        [invalid_port_x(3), "line 4: Quadrature must use C1, C3, C5, or C7"],
    ),
    (
        "PulseCount(V,1,3,2,0,1,0)",
        ["line 4: Invalid PulseCount channel, must be P1, P2, or C1..C8."],
    ),
    (
        "PulseCount(V,1,109,2,0,1,0)",
        ["line 4: Invalid PulseCount channel, must be P1, P2, or C1..C8."],
    ),
    ("AM25T(VA(),2,mV200C,1,1,TypeT,V,3,C2,Vx1,True,0,60,1,0)", [invalid_port_x(3)]),
    ("MuxSelect(3,C2,20,1,1)", [invalid_port_x(3)]),
    ("CWB100RSSI(102)", ["line 4: Invalid SDI-12 port, must be C1, C3, C5, or C7"]),
    ("CWB100RSSI(105)", []),
    ("AM25T(VA(),2,mV200C,1,1,TypeT,V,C1,C2,Vx1,True,0,60,1,0)", []),
    ("PWM(V,105,200,uSec)", []),
]


@pytest.mark.parametrize("instruction,expected", CR6_CASES, ids=[c[0] for c in CR6_CASES])
def test_cr6_port_terminals(instruction, expected):
    assert lines(instruction, "cr6") == expected


@pytest.mark.parametrize("instruction,expected", CR1000X_CASES, ids=[c[0] for c in CR1000X_CASES])
def test_cr1000x_port_terminals(instruction, expected):
    assert lines(instruction, "cr1000x") == expected
