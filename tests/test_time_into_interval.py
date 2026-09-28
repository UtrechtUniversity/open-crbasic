"""`TimeIntoInterval`/`IfTime` constant-argument checks (`checker/intervals.py`). Expected lines
are verbatim oracle output, CR6 and CR300, 2026-09-28 (`docs/checker.md` "TimeIntoInterval
arguments"); CR1000X and CR350 matched the `t*` probes."""

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
    ('t1', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(3,1,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.']}),
    ('t10', 'Public X\nBeginProg\n  Scan (2,Sec,0,0)\n    If TimeIntoInterval(0,10,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr300': []}),
    ('t11', 'Public X\nBeginProg\n  Scan (3,Sec,0,0)\n    If TimeIntoInterval(0,1,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr300': []}),
    ('t12', 'Public X\nBeginProg\n  Scan (7,Sec,0,0)\n    If TimeIntoInterval(0,1,Hr) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Scan interval must be integrally divisible into interval.'], 'cr300': ['line 4: Scan interval must be integrally divisible into interval.']}),
    ('t13', 'Public X\nBeginProg\n  Scan (500,mSec,0,0)\n    If TimeIntoInterval(0,1,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr300': []}),
    ('t14', 'Public X\nBeginProg\n  Scan (300,mSec,0,0)\n    If TimeIntoInterval(0,1,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Scan interval must be integrally divisible into interval.'], 'cr300': ['line 4: Scan interval must be integrally divisible into interval.']}),
    ('t15', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(-1,5,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr300': []}),
    ('t16', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(2,1,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (2) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (2) must be < Interval.']}),
    ('t18', 'Public X\nBeginProg\n  Scan (7,Sec,0,0)\n    If TimeIntoInterval(0,1,Day) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Scan interval must be integrally divisible into interval.'], 'cr300': ['line 4: Scan interval must be integrally divisible into interval.']}),
    ('t2', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(0,0,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (0) must be > 0 and TimeInto Interval (0) must be < Interval.'], 'cr300': ['line 4: Interval (0) must be > 0 and TimeInto Interval (0) must be < Interval.']}),
    ('t3', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(60,1,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (60) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (60) must be < Interval.']}),
    ('t4', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(59,1,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (59) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (59) must be < Interval.']}),
    ('t5', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If IfTime(3,1,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.']}),
    ('t6', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If IfTime(1,1,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (1) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (1) must be < Interval.']}),
    ('t7', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(1,1,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (1) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (1) must be < Interval.']}),
    ('t8', 'Public X\nBeginProg\n  Scan (7,Sec,0,0)\n    If TimeIntoInterval(0,15,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Scan interval must be integrally divisible into interval.'], 'cr300': ['line 4: Scan interval must be integrally divisible into interval.']}),
    ('t9', 'Public X\nBeginProg\n  Scan (2,Sec,0,0)\n    If TimeIntoInterval(1,10,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Scan interval must be integrally divisible into interval.'], 'cr300': ['line 4: Scan interval must be integrally divisible into interval.']}),
    ('u1', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(0,1500,mSec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Scan interval must be integrally divisible into interval.'], 'cr300': ['line 4: Scan interval must be integrally divisible into interval.']}),
    ('u10', 'Public X\nConst K = 1\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(3,K,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 5: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.'], 'cr300': ['line 5: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.']}),
    ('u2', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(0,3601500,mSec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Illegal sub seconds in time into and/or interval when interval > 3600 secs.'], 'cr300': ['line 4: Illegal sub seconds in time into and/or interval when interval > 3600 secs.']}),
    ('u3', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(500,7200000,mSec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Illegal sub seconds in time into and/or interval when interval > 3600 secs.'], 'cr300': ['line 4: Illegal sub seconds in time into and/or interval when interval > 3600 secs.']}),
    ('u4', 'Public X\nDim Z banana\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(3,1,Min) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 2: Invalid Variable declaration banana.', 'line 5: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.'], 'cr300': ['line 2: Invalid Variable declaration banana.', 'line 4: Too many parameters:  Sec.', 'line 5: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.']}),
    ('u5', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(3,1,Min) Then X = 1\n    X = Q\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (3) must be < Interval.', 'line 5: Q is not defined.']}),
    ('u6', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(-500,1,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1) must be > 0 and TimeInto Interval (-500) must be < Interval.'], 'cr300': ['line 4: Interval (1) must be > 0 and TimeInto Interval (-500) must be < Interval.']}),
    ('u7', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(-1500,1000,mSec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Interval (1000) must be > 0 and TimeInto Interval (-1500) must be < Interval.', 'line 4: Scan interval must be integrally divisible into interval.'], 'cr300': ['line 4: Interval (1000) must be > 0 and TimeInto Interval (-1500) must be < Interval.', 'line 4: Scan interval must be integrally divisible into interval.']}),
    ('u8', 'Public X\nBeginProg\n  Scan (250,mSec,0,0)\n    If TimeIntoInterval(100,1000,mSec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': ['line 4: Scan interval must be integrally divisible into interval.'], 'cr300': ['line 4: Scan interval must be integrally divisible into interval.']}),
    ('u9', 'Public X\nBeginProg\n  Scan (1,Sec,0,0)\n    If TimeIntoInterval(0,7201,Sec) Then X = 1\n  NextScan\nEndProg\n',
     {'cr6': [], 'cr300': []}),
]


@pytest.mark.parametrize("name,text,expected", CASES, ids=[c[0] for c in CASES])
def test_time_into_interval(name, text, expected):
    assert lines(text, "cr6") == expected["cr6"]
    assert lines(text, "cr300") == expected["cr300"]
