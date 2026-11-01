"""`-t` `[Times]` output against CR6Comp.exe observations (tests/fixtures/timing.json, rules in
docs/cli.md "Timing output (-t)"). `expected_times` is the text between `[Times]` and the result
line of the real message file."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "timing_pass1_gate.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_times_observations(case, tmp_path):
    name = case.get("file", "t.CR6")
    (tmp_path / name).write_bytes(case["source"].encode("latin-1"))
    subprocess.run(
        [sys.executable, "-m", "opencrbasic", "-t", name, "out.msg"],
        cwd=tmp_path,
        capture_output=True,
        env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    msg = (tmp_path / "out.msg").read_bytes().decode("latin-1")
    got = msg.split("[Times]", 1)[1]
    got = got[: got.index(name + " -- Compile")]
    assert got == case["expected_times"]
