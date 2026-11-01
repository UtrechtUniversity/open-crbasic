"""`SlowSequence` with an unterminated string (`SlowSequence(")`, `SlowSequence(A,")`), before and
inside `BeginProg` (`parser._parse_text_segment`, `checker/structure.py` `check_pass2`). Expected
lines are verbatim oracle output on all four loggers, 2026-10-16 (`docs/checker.md`, "Optional first
parameter"; fixture `r18_slowseq_timeout.json`)."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "r18_slowseq_timeout.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_slowseq_unterminated_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]
