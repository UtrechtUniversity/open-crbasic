"""The round-35 realistic mutation operators of `tools/fuzz.py`: each changes one line or gives
up with None."""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import fuzz  # noqa: E402

LINES = ["Public Battery_V, Temp(3)", "BeginProg", "  Scan(5,Sec,0,0)", "    Battery(Battery_V)",
         "  NextScan", "EndProg"]


def test_realistic_operators():
    for op in (fuzz.op_typo_identifier, fuzz.op_neighbour_number, fuzz.op_swap_arguments):
        changed = [o for o in (op(LINES, random.Random(k)) for k in range(20)) if o is not None]
        assert changed
        for out in changed:
            assert len(out) == len(LINES) and sum(a != b for a, b in zip(out, LINES)) == 1
    assert fuzz.op_typo_identifier(["BeginProg", "EndProg"], random.Random(0)) is None
