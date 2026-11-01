"""A clean `SlowSequence` before any `BeginProg` opens the program, so `SlowSequence` / `EndProg` compiles
(`checker/structure.open_program_at_slowsequence`; `docs/checker.md`). Expected lines are verbatim oracle output on all
four loggers, 2026-10-16 (fixture `r18_slowseq_endprog.json`)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "r18_slowseq_endprog.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_slowseq_endprog_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
