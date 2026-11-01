import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

CASES = [
    c
    for f in ("r8_hash_cut",)
    if (Path(__file__).parent / "fixtures" / f"{f}.json").exists()
    for c in json.loads((Path(__file__).parent / "fixtures" / f"{f}.json").read_text(encoding="utf-8"))["cases"]
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_r8_hash_cut_observations(case, tmp_path):
    for name, text in case["siblings"].items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
