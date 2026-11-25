"""A number beyond the float range (a 309-digit integer literal, `1e309`) never raises inside the
checker (M1: no implementation exception; round 69). The reference gives no usable verdict for
such literals, so only the absence of an implementation exception is asserted."""

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

HUGE = "1" + "0" * 309
SCAN = [
    "Delay({0},1,mSec)",
    "Delay(1,{0},Sec)",
    "Delay(1,1,{0})",
    "ExciteV({0},1,1)",
    "PortSet({0},1)",
    "VoltSe(V,1,mV5000,1,1,0,{0},1.0,0)",
    "PanelTemp(V,{0})",
    "SerialOpen(ComC1,1,1,{0},1)",
    'X = FTPClient("h","u","p","f","g",{0})',
    "SubScan({0},mSec,1)\n    X = 1\n  NextSubScan",
]
TABLE = [
    "Sample({0},V,FP2)",
    "WindVector({0},V,V,FP2,False,1,0,1)",
    "DataInterval({0},1,Min,1)",
    "DataInterval(1,{0},Min,1)",
]


def _sources(value):
    for body in SCAN:
        yield (
            "Public V(4), X\nDataTable(T,True,-1)\n  Sample(1,X,FP2)\nEndTable\nBeginProg\n"
            f"  Scan(1,Sec,0,0)\n    {body.format(value)}\n    CallTable T\n  NextScan\nEndProg\n"
        )
    for row in TABLE:
        yield (
            f"Public V(4), X\nDataTable(T,True,-1)\n  {row.format(value)}\nEndTable\nBeginProg\n"
            "  Scan(1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n"
        )
    yield f"Dim A({value})\nConst K = {value}\nPublic X\nBeginProg\n  Scan({value},Sec,0,0)\n    X = K\n  NextScan\nEndProg\n"


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize("value", [HUGE, "-" + HUGE, "1e309", "-1e309"])
def test_no_implementation_exception(tmp_path, logger, value):
    program = tmp_path / "t.CR6"
    for source in _sources(value):
        program.write_text(source, newline="")
        result = compile_file(program, profiles.get(logger))
        assert not result.implementation_exceptions, (source, result.implementation_exceptions)
