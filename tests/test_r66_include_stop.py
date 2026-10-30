"""An `IncludeSection` extension error stops every later include on CR6/CR1000X (round 66)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file


CASES = json.loads((Path(__file__).parent / "fixtures" / "r66_include_stop.json").read_text())["cases"]


@pytest.mark.parametrize("case", [
    pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
    if "not_modelled" in case else case for case in CASES], ids=lambda case: case["id"])
def test_include_stop_observations(case, tmp_path):
    for name, text in case["files"].items():
        (tmp_path / name).write_bytes(text.encode("utf-8"))
    program = tmp_path / "t.CR6"
    program.write_bytes(case["source"].encode("utf-8"))
    result = compile_file(program, profiles.get(case["logger"]))
    ours = "\n".join(result.result_lines()).encode("latin-1", "replace").decode("utf-8", "replace")
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert ours == case["expected_stdout"]
