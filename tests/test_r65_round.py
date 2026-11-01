"""Round-65 reference observations: String-typed operands, FTPClient PutGetOption and GetRecord
destination size."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file

FIXTURES = Path(__file__).parent / "fixtures"
CASES = [case for name in ("r65_string_operands", "r65_ftpclient_option", "r65_getrecord_size")
         for case in json.loads((FIXTURES / f"{name}.json").read_text())["cases"]]


@pytest.mark.parametrize("case", [
    pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
    if "not_modelled" in case else case for case in CASES], ids=lambda case: case["id"])
def test_round65_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_bytes(case["source"].encode("utf-8"))
    result = compile_file(program, profiles.get(case["logger"]))
    ours = "\n".join(result.result_lines()).encode("latin-1", "replace").decode("utf-8", "replace")
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert ours == case["expected_stdout"]
