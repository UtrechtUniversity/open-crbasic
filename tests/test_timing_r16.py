"""`-t` [Times] text per logger against real-compiler observations (tests/fixtures/timing_r16.json)."""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from opencrbasic import cli

CASES = json.loads((Path(__file__).parent / "fixtures" / "timing_r16.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_times(case, tmp_path):
    src = tmp_path / "t.CR6"
    src.write_bytes(case["source"].replace("\n", "\r\n").encode("latin-1"))
    msg = tmp_path / "out.msg"
    with redirect_stdout(io.StringIO()):
        cli.main(["-t", "--logger", case["logger"], str(src), str(msg)])
    text = msg.read_bytes().decode("latin-1").replace("\r\n", "\n")
    got = text.split("[Times]", 1)[1].split("t.CR6 -- ", 1)[0]
    got = "\n".join(l for l in got.split("\n") if l.strip())
    assert got == (case["expected_times"] or "")
