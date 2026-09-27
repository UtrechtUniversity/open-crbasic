"""Unit tests for `checker/instr_rules.py`'s `check_precision_variable` (`AddPrecise`/
`MovePrecise`'s own `PrecisionVariable` argument). Rules and oracle evidence:
`docs/instructions.md`.
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


PROG = """Public Precise
Dim d(2) As Long
Const K = 5
BeginProg
{body}EndProg
"""


def wrapped(body: str) -> str:
    return PROG.format(body=body)


# ------------------------------------------------------------------ rejected: no genuine variable
# reference anywhere in the argument


def test_bare_literal_is_rejected():
    result = compile_text(wrapped("  AddPrecise(1, .000000111111)\n"))
    assert messages_of(result) == ["Invalid Variable or Array: 1."]


def test_parenthesised_constant_expression_is_rejected():
    result = compile_text(wrapped("  AddPrecise((1+2), .000000111111)\n"))
    assert messages_of(result) == ["Invalid Variable or Array: (1+2)."]


def test_unary_negated_literal_is_rejected():
    result = compile_text(wrapped("  AddPrecise(-1, .1)\n"))
    assert messages_of(result) == ["Invalid Variable or Array: -1."]


def test_declared_const_name_is_rejected():
    result = compile_text(wrapped("  AddPrecise(K, .1)\n"))
    assert messages_of(result) == ["Invalid Variable or Array: K."]


def test_nan_literal_is_rejected():
    result = compile_text(wrapped("  AddPrecise(NAN, .1)\n"))
    assert messages_of(result) == ["Invalid Variable or Array: NAN."]


def test_moveprecise_shares_the_same_rule():
    result = compile_text(wrapped("  MovePrecise((1+2), .1)\n"))
    assert messages_of(result) == ["Invalid Variable or Array: (1+2)."]


# ------------------------------------------------------------------ accepted: a genuine variable
# reference (declared or undeclared) anywhere in the argument


def test_bare_declared_variable_is_accepted():
    result = compile_text(wrapped("  AddPrecise(Precise, .000000111111)\n"))
    assert messages_of(result) == []


def test_unary_negated_variable_is_accepted():
    result = compile_text(wrapped("  AddPrecise(-Precise, .1)\n"))
    assert messages_of(result) == []


def test_parenthesised_variable_is_accepted():
    result = compile_text(wrapped("  AddPrecise((Precise), .1)\n"))
    assert messages_of(result) == []


def test_array_element_is_accepted():
    result = compile_text(wrapped("  AddPrecise(d(1), .1)\n"))
    assert messages_of(result) == []


def test_variable_mixed_with_literals_is_accepted():
    result = compile_text(wrapped("  AddPrecise(1+2+Precise, .1)\n"))
    assert messages_of(result) == []


def test_undeclared_name_is_accepted():
    # Saved fuzz false-reject reproducer shape (`tests/corpus/fuzz/cr6/false_reject/
    # fac2798b6778.CR6`): an undeclared name in this position compiles clean and is not flagged as
    # an undefined symbol either (`expressions.py`'s own `_UNDEFINED_CHECK_EXEMPT_POSITIONS`).
    result = compile_text(wrapped("  AddPrecise(UndeclaredFuzzName123, .1)\n"))
    assert messages_of(result) == []


def test_x_argument_has_no_such_restriction():
    result = compile_text(wrapped("  AddPrecise(Precise, (1+2))\n"))
    assert messages_of(result) == []


# ------------------------------------------------------------------ pass-1 tier


def test_fires_alongside_an_unrelated_earlier_pass1_error():
    # Same shape as `test_function_arity.py`'s own equivalent test: a bare `Public` (`Variable not
    # named.`) is a genuine, earlier pass-1 declaration error.
    text = "Public\n" + wrapped("  AddPrecise(1, .1)\n")
    result = compile_text(text)
    msgs = messages_of(result)
    assert "Variable not named." in msgs
    assert "Invalid Variable or Array: 1." in msgs


def test_suppresses_an_unrelated_pass2_finding_elsewhere_in_the_file():
    result = compile_text(wrapped("  AddPrecise(1, .1)\n  Randomize(BazQux)\n"))
    assert messages_of(result) == ["Invalid Variable or Array: 1."]


# ------------------------------------------------------------------ CR1000X byte-identical; not
# implemented at all on CR300/CR350 (`AddPrecise`/`MovePrecise` are absent from their own
# `instructions.json`)


def test_cr1000x_matches_cr6_message():
    result = compile_text(wrapped("  AddPrecise((1+2), .1)\n"), profile=CR1000X)
    assert messages_of(result) == ["Invalid Variable or Array: (1+2)."]


def test_cr300_does_not_know_addprecise_at_all_so_never_flags_it():
    result = compile_text(wrapped("  AddPrecise((1+2), .1)\n"), profile=CR300)
    assert messages_of(result) == []
