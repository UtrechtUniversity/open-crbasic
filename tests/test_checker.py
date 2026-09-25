"""Unit tests for `opencrbasic.checker`. Rules and oracle evidence: `docs/checker.md`."""

from __future__ import annotations

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

PROFILE = profiles.get("cr6")


def compile_text(text: str):
    program, diags = parser.parse_text(text, "t.CR6")
    result = CompileResult("t.CR6", PROFILE, diags)
    checker.check(program, PROFILE, diags, result)
    return result


def messages(text: str) -> list[str]:
    return [d.message for d in compile_text(text).diagnostics.errors]


PROG = """Public X As Float
BeginProg
  Scan (1,Sec,3,1)
{body}  NextScan
EndProg
"""


def wrapped(body: str) -> str:
    return PROG.format(body=body)


# ------------------------------------------------------------------ structure.py


def test_no_beginprog():
    result = compile_text("Public X As Float\n")
    assert result.diagnostics.errors[-1].message == "No BeginProg in Program."
    assert result.diagnostics.errors[-1].line == 1


def test_scan_without_nextscan():
    result = compile_text("Public X As Float\nBeginProg\n  Scan (1,Sec,3,1)\n    X = 1\nEndProg\n")
    assert "Scan without NextScan." in messages_of(result)


def test_nextscan_with_no_scan():
    result = compile_text("Public X As Float\nBeginProg\n  NextScan\nEndProg\n")
    assert "NextScan with no Scan." in messages_of(result)


def test_if_without_endif():
    result = compile_text(wrapped("    If X > 1 Then\n      X = 2\n"))
    assert "IF or SELECT without EndIf or EndSelect." in messages_of(result)


def test_select_without_endselect_is_the_same_message():
    result = compile_text(wrapped("    Select Case X\n      Case 1\n        X = 2\n"))
    assert "IF or SELECT without EndIf or EndSelect." in messages_of(result)


def test_well_formed_program_has_no_structural_errors():
    result = compile_text(wrapped("    X = 1\n"))
    assert result.diagnostics.errors == []


def test_explicit_call_to_undefined_sub():
    result = compile_text(wrapped("    Call NoSuchSub\n"))
    assert "Sub NoSuchSub not defined." in messages_of(result)


def test_implicit_call_to_declared_sub_is_fine():
    text = ("Public X As Float\nSub MySub(a As Float)\n  X = a\nEndSub\n" +
            wrapped("    MySub(5)\n"))
    result = compile_text(text)
    assert result.diagnostics.errors == []


def test_implicit_call_to_builtin_function_is_fine():
    # `Timer`, `SerialOpen`, ... are `data/functions.json` entries (not the 410-entry instruction
    # table) called for a side effect with the return value discarded -- real code in this
    # repository's own `crbasic/Main.CR6` does this throughout.
    result = compile_text(wrapped("    Timer (1,Sec,2)\n"))
    assert result.diagnostics.errors == []


def test_undefined_bare_call_is_invalid_expression():
    result = compile_text("NOTE\n" + wrapped(""))
    assert "Invalid, or out of place expression: NOTE." in messages_of(result)


def test_known_parser_gap_is_not_flagged():
    # `docs/frontend.md` "Known residual gaps": still oracle-accepted despite not parsing.
    result = compile_text(wrapped("    WS_ms=WS_ms+=1\n"))
    assert result.diagnostics.errors == []


# ------------------------------------------------------------------ expressions.py


def test_undefined_symbol_in_channel_argument():
    # `Vx1`/`P1`/`C5`.. are CR1000X-only terminals, absent from `data/cr6/symbols.json`.
    result = compile_text(wrapped("    TCDiff (X,1,mv200C,Vx1,TypeT,X,True,0,15000,1.0,0)\n"))
    assert "Invalid symbol in expression: Vx1." in messages_of(result)


def test_datatable_name_position_is_not_checked():
    # code 6 (identifier): the table's own name is captured verbatim, not symbol-resolved.
    text = "DataTable (SomeTable,True,-1)\n  Sample (1,X,FP2)\nEndTable\n" + wrapped(
        "    CallTable (SomeTable)\n"
    )
    result = compile_text(text)
    assert result.diagnostics.errors == []


def test_structuretype_name_used_as_datatype_is_not_flagged():
    # regression: `Sample`'s `DataType` argument may be a user `StructureType` name.
    text = (
        "StructureType VWSensor\n  Frequency As Float\nEndStructureType\n"
        "DataTable (T,True,-1)\n  Sample (1,S,VWSensor)\nEndTable\n" + wrapped("")
    )
    result = compile_text(text)
    assert result.diagnostics.errors == []


# ------------------------------------------------------------------ instructions.py


def test_invalid_channel_argument():
    result = compile_text(wrapped("    TCDiff (X,1,mv200C,1,TypeT,X,True,0,15000,1.0,0)\n"))
    assert "Invalid channel 1, must use U1..U12." in messages_of(result)


def test_valid_u_channel_argument():
    result = compile_text(wrapped("    TCDiff (X,1,mv200C,U1,TypeT,X,True,0,15000,1.0,0)\n"))
    assert result.diagnostics.errors == []


# ------------------------------------------------------------------ warnings.py


def test_table_declared_but_never_called():
    text = "DataTable (T,True,-1)\n  Sample (1,X,FP2)\nEndTable\n" + wrapped("")
    result = compile_text(text)
    assert any(d.message == "Table T, is declared but never called." for d in result.diagnostics.warnings)


def test_table_called_is_not_warned():
    text = "DataTable (T,True,-1)\n  Sample (1,X,FP2)\nEndTable\n" + wrapped("    CallTable (T)\n")
    result = compile_text(text)
    assert result.diagnostics.warnings == []


def messages_of(result) -> list[str]:
    return [d.message for d in result.diagnostics.errors]
