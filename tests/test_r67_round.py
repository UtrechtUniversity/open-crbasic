"""Round-67 reference observations: CType typing, FTPClient overflow, nested built-ins and quotes in
user Function calls, measurement Reps overflow, String Const table names and block structure."""

import json
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.compiler import compile_file


FIXTURES = Path(__file__).parent / "fixtures"
CASES = [case for name in ("r67_ctype_typing", "r67_ftpclient_overflow", "r67_nested_builtin",
                           "r67_function_tail", "r67_measurement_reps", "r67_calltable_const",
                           "r67_block_structure", "r67_string_size_junk")
         for case in json.loads((FIXTURES / f"{name}.json").read_text())["cases"]]


@pytest.mark.parametrize("case", [
    pytest.param(case, marks=pytest.mark.xfail(reason=case["not_modelled"], strict=True))
    if "not_modelled" in case else case for case in CASES], ids=lambda case: case["id"])
def test_round67_observations(case, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_bytes(case["source"].encode("utf-8"))
    result = compile_file(program, profiles.get(case["logger"]))
    ours = "\n".join(result.result_lines()).encode("latin-1", "replace").decode("utf-8", "replace")
    assert not result.implementation_exceptions
    assert (0 if result.ok else 1) == case["expected_exit"]
    assert ours == case["expected_stdout"]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("sign", ["", "+", "-"])
def test_ftpclient_huge_integer_no_implementation_exception(logger, sign, tmp_path):
    program = tmp_path / "t.CR6"
    option = sign + "1" + "0" * 309
    program.write_text('Public X\nBeginProg\nX = FTPClient("h","u","p","f","g",'
                       + option + ')\nEndProg\n')
    result = compile_file(program, profiles.get(logger))
    assert not result.implementation_exceptions


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("kind", ["Sub", "Function"])
@pytest.mark.parametrize("tail", ["", ", B As Long"])
def test_routine_string_size_bang_pass1(logger, kind, tail, tmp_path):
    program = tmp_path / "t.CR6"
    program.write_text(f"{kind} S(A As String * 16!{tail})\nEnd{kind}\nBeginProg\nEndProg\n")
    result = compile_file(program, profiles.get(logger))
    assert not result.implementation_exceptions
    assert result.pass1_errors
    assert [d.message for d in result.diagnostics.errors] == ["Incomplete expression: 16!."]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("nested", ["Abs(X Y)", "Round(X Y, 1)"])
def test_function_adjacent_operand_locator_skips_unary_not(logger, nested, tmp_path):
    program = tmp_path / "t.CR6"
    prefix = ("Public X, Y, N As Long\nFunction G(A As Long, B As Long) As Long\n"
              "Return 1\nEndFunction\nBeginProg\n")
    messages = []
    for first in ("X", "Not X"):
        program.write_text(prefix + f"N = G({first}, {nested})\nEndProg\n")
        result = compile_file(program, profiles.get(logger))
        assert not result.implementation_exceptions
        messages.append([d.message.replace("Not X", "X") for d in result.diagnostics.errors])
    assert messages[0]
    assert messages[0] == messages[1]
