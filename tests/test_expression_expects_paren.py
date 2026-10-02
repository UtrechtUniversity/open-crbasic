"""Unit tests for `checker/expressions.py`'s bare-function-reference check inside
`check_function_arity` (`Expression expects ( : %s.`). Rules and oracle evidence:
`docs/checker.md`, "Expression expects ( (bare function name)".
"""

from __future__ import annotations

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

CR6 = profiles.get("cr6")
CR1000X = profiles.get("cr1000x")
CR300 = profiles.get("cr300")
CR350 = profiles.get("cr350")


def compile_text(text: str, profile=CR6):
    program, diags = parser.parse_text(text, "t.CR6")
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result


def messages_of(result) -> list[str]:
    return [d.message for d in result.diagnostics.errors]


PROG = """Public X As Float
Public Y As Float
BeginProg
  Scan (1,Sec,3,1)
{body}  NextScan
EndProg
"""


def wrapped(body: str) -> str:
    return PROG.format(body=body)


# ------------------------------------------------------------------ Assignment RHS


def test_bare_name_in_assignment_triggers():
    # Oracle: `t_sin_plain.cr6`, `X = Sin` alone -> `Expression expects ( :   X = Sin.` on CR6/
    # CR1000X (this session's own oracle probe, `docs/checker.md`).
    result = compile_text(wrapped("    X = Sin\n"))
    assert messages_of(result) == ["Expression expects ( :     X = Sin."]


def test_bare_name_in_assignment_split_across_lines():
    # `findspa_1.CR6` shape: the argument list is split onto the next physical line, so the
    # Assignment's own value is a bare `Name`, not an `Index` -- oracle: `FindResult=FindSpa` ->
    # `Expression expects ( :     FindResult=FindSpa.` (leading whitespace kept in the echo).
    text = wrapped("    X = FindSpa\n(Y,Y,Y,Y)\n")
    result = compile_text(text)
    assert "Expression expects ( :     X = FindSpa." in messages_of(result)


def test_zero_arg_function_bare_in_assignment_does_not_trigger():
    # `Rnd` is genuinely zero-argument (`argspec` also 0, unlike `EmailRelay` below) -- oracle
    # probe: `X = Rnd` compiles clean on all four loggers (`docs/checker.md`).
    result = compile_text(wrapped("    X = Rnd\n"))
    assert messages_of(result) == []


def test_zero_required_arg_function_with_optional_args_still_triggers():
    # `EmailRelay`: `nargs=0` (no *required* argument) but `argspec` non-zero (it does take
    # optional ones) -- the sole such outlier in `data/functions.json`, oracle-confirmed via the
    # `emailrelay_1.CR6` help corpus example: `email_tx_success = EmailRelay` ->
    # `Expression expects ( :          email_tx_success = EmailRelay.`.
    result = compile_text(wrapped("    X = EmailRelay\n"))
    assert messages_of(result) == ["Expression expects ( :     X = EmailRelay."]


def test_local_variable_shadowing_a_function_name_is_not_flagged():
    # CR6 observation: scratch/builtin-local-shadow-probes.json.
    text = """Public X As Float
Sub Test
Dim exp As Long
exp = 2
X = exp + 1
EndSub
BeginProg
Scan(1,Sec,3,1)
Call Test
NextScan
EndProg
"""
    result = compile_text(text)
    assert messages_of(result) == []


# ------------------------------------------------------------------ bare Call statement


def test_bare_name_as_a_call_statement_triggers():
    # `timer_1.CR6` shape: `Timer` alone, its own argument list on the next physical line ->
    # `Expression expects ( :     Timer.` (oracle, `docs/checker.md`).
    text = wrapped("    Timer\n(1,uSec,2)\n")
    result = compile_text(text)
    assert "Expression expects ( :     Timer." in messages_of(result)


def test_bare_name_as_a_call_statement_alone_on_its_own_line():
    result = compile_text(wrapped("    TCPOpen\n"))
    assert messages_of(result) == ["Expression expects ( :     TCPOpen."]


def test_call_statement_with_explicit_empty_parens_is_the_generic_fallback():
    # `TCPOpen()` (real parens, zero args) is not a bare reference; oracle, CR6, 2026-09-27:
    # `Invalid, or out of place expression:     TCPOpen().` (same for `SerialFlush()`, `X = Sin()`).
    result = compile_text(wrapped("    TCPOpen()\n"))
    assert messages_of(result) == ["Invalid, or out of place expression:     TCPOpen()."]


# ------------------------------------------------------------------ Instruction arguments


def test_bare_name_as_an_instruction_argument_echoes_only_that_argument():
    # `Randomize(Sin)`: oracle probe (this session, `docs/checker.md`) ->
    # `Expression expects ( : Sin.` -- the argument's own text, not the whole statement, matching
    # the existing arity check's own echo convention for `Instruction`.
    result = compile_text(wrapped("    Randomize(Sin)\n"))
    assert messages_of(result) == ["Expression expects ( : Sin."]


# ------------------------------------------------------------------ cross-logger message shape


def test_cr1000x_matches_cr6():
    result = compile_text(wrapped("    X = Sin\n"), profile=CR1000X)
    assert messages_of(result) == ["Expression expects ( :     X = Sin."]


def test_cr300_echoes_with_a_trailing_newline_before_the_period():
    # Oracle-confirmed (`docs/checker.md`): CR300/CR350 put the closing `.` alone on the next
    # line, same convention `_arity_message` already uses for the other arity messages.
    result = compile_text(wrapped("    X = Sin\n"), profile=CR300)
    assert messages_of(result) == ["Expression expects ( :     X = Sin\n."]


def test_cr350_echoes_with_a_trailing_newline_before_the_period():
    result = compile_text(wrapped("    X = Sin\n"), profile=CR350)
    assert messages_of(result) == ["Expression expects ( :     X = Sin\n."]


def test_cr300_instruction_argument_matches_cr6():
    result = compile_text(wrapped("    Randomize(Sin)\n"), profile=CR300)
    assert messages_of(result) == ["Expression expects ( : Sin."]
