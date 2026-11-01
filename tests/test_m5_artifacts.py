"""The outputs of `crbc` against saved reference runs (M5, `docs/artifacts.md`): exit status,
stdout, and every file a run writes, including whether it is written at all."""

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "fixtures" / "m5_artifacts.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_m5_artifacts(case, tmp_path):
    for name, text in {case["program"]: case["source"], **case.get("siblings", {})}.items():
        (tmp_path / name).write_bytes(text.replace("\n", "\r\n").encode())
    args = [case["program"] if a == "P" else a for a in case["args"]]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    env.pop("OPENCRBASIC_STRICT", None)
    run = subprocess.run(
        [sys.executable, "-m", "opencrbasic", "--logger", case["logger"], *args],
        cwd=tmp_path,
        capture_output=True,
        env=env,
    )
    stdout = re.sub(r"\r+", "\r", run.stdout.decode("latin-1")).replace("\r\n", "\n").rstrip("\n")
    assert (run.returncode, stdout) == (case["expected_exit"], case["expected_stdout"])
    written = {
        f.name: base64.b64encode(f.read_bytes()).decode()
        for f in tmp_path.iterdir()
        if f.name not in {case["program"], *case.get("siblings", {})}
    }
    assert written == case["expected_files"]
