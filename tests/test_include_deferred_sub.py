import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.checker.structure import CallLinks, commit_deferred_sub_error
from opencrbasic.compiler import compile_file
from opencrbasic.diagnostics import Diagnostics

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "include_deferred_sub.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_include_deferred_sub_observations(case, tmp_path):
    for name, text in case["siblings"].items():
        (tmp_path / name).write_text(text, encoding="utf-8", newline="")
    program = tmp_path / "t.CR6"
    program.write_text(case["source"], encoding="utf-8", newline="")

    result = compile_file(program, profiles.get(case["logger"]))

    assert (0 if result.ok else 1) == case["expected_exit"]
    assert "\n".join(result.result_lines()) == case["expected_stdout"]


def test_deferred_sub_cutoff_uses_expanded_position():
    links = CallLinks(
        first=(11, "lib.crb", "Sub Nope not defined."),
        positions={("t.CR6", 9): 4, ("lib.crb", 11): 5, ("t.CR6", 1): 6},
    )
    diags = Diagnostics()
    diags.error(9, "NextScan with no Scan.", "t.CR6")
    diags.error(1, "NextScan with no Scan.", "t.CR6")

    commit_deferred_sub_error(links, profiles.get("cr300"), diags)

    assert [(d.line, d.file, d.message) for d in diags.errors] == [
        (9, "t.CR6", "NextScan with no Scan."),
        (11, "lib.crb", "Sub Nope not defined."),
    ]
