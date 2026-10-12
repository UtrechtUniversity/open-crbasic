"""TDF size of a single called FP2 `DataTable` (`-z`) for every kind of size argument.

Expected values are what the real compilers wrote (`tools/tdf_probe.py --exe`, 2026-10-12):
the size is read as float32 (an integer constant that fits in 32 bits is kept exactly) and truncated toward zero; CR6/CR1000X make every negative or
out-of-range size auto-sized, CR300/CR350 keep `-N` as `N`, saturate +-2^31 and give a size
beyond it the minimum. Before round 15 a negative or beyond-32-bit size crashed `tdf.serialize`.
"""
import pytest

from opencrbasic import profiles, tdf
from opencrbasic.compiler import compile_file

SRC = """Public X
DataTable (T,True,{size})
  Sample (1,X,FP2)
EndTable
BeginProg
  Scan (1,Sec,0,0)
    CallTable T
  NextScan
EndProg
"""

CR3XX = {  # literal -> TDF size of T (CR300 and CR350 alike where both were observed)
    "3000000000": 454, "-999999999999": 454, "-2147483777": 454, "99999999999": 454,
    "-5000000000": 454, "4294967296": 454, "4294967297": 454, "1e12": 454, "-1e39": 454,
    "2147483777": 454, "2147483648": 2147483647, "-2147483648": 2147483647,
    "2147483776": 2147483647, "1e39": 2147483647, "-3": 3, "-70000": 70000, "100": 100,
    "100.7": 100, "100.5": 100, "2.5": 2, "-2.5": 2, "-0.5": 1, "0": 1, "0.5": 1, "1.5": 1,
    "-100.7": 100, "-2": 2, "2147483000": 2147483000, "1000000000": 1000000000,
    "578673.99": 578674, "100.9999": 100, "100.99999999": 101, "99.99999": 99,
    "2147483520": 2147483520, "2147483647": 2147483647,
}
CR6X = {  # CR6 and CR1000X
    "3000000000": 232674, "-999999999999": 232674, "-2147483777": 232674,
    "2147483648": 232674, "99999999999": 232674, "-2147483648": 232674,
    "-5000000000": 232674, "4294967296": 232674, "4294967297": 232674, "1e12": 232674,
    "-1": 232674, "-3": 232674, "-70000": 232674, "100": 100, "100.7": 100, "100.5": 100,
    "2.5": 2, "-2.5": 232674, "-1.5": 232674, "-0.5": 1, "0": 1, "0.5": 1, "1.5": 1,
    "-100.7": 232674, "-2": 232674, "1e39": 2147483647, "-1e39": 232674,
    "578673.99": 578674, "100.9999": 100, "100.99999999": 101, "99.99999": 99,
    "2147483583": 2147483583, "2147483647": 2147483647,
}
CASES = [(lg, lit, want) for lg in ("cr300", "cr350") for lit, want in CR3XX.items()]
CASES += [(lg, lit, want) for lg in ("cr6", "cr1000x") for lit, want in CR6X.items()]


@pytest.mark.parametrize("logger,literal,want", CASES, ids=lambda v: str(v))
def test_declared_size(tmp_path, logger, literal, want):
    program = tmp_path / "t.CR6"
    program.write_text(SRC.format(size=literal), encoding="utf-8")
    result = compile_file(program, profiles.get(logger))
    assert result.ok, result.result_lines()
    table = next(t for t in result.tables if t.name == "T")
    assert table.size == want
    tdf.serialize(result.tables)       # no struct.error for any size
