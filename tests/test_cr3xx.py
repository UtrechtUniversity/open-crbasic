"""Unit tests for `opencrbasic.checker.cr3xx`. Oracle evidence: `docs/loggers.md` "Round 8" and
"Too many parameters cascade, widened".

`check_unknown_line_words` and `check_too_many_cascade` are both wired into the shared checker
pipeline (`checker/__init__.py`); the tests below call the ordinary `checker.check` pipeline (via
`apply_hook`/`compile_result_for_profile`), which is equivalent and keeps each test focused on one
hook's own effect.
"""

from __future__ import annotations

from opencrbasic import checker, parser, profiles
from opencrbasic.checker import cr3xx
from opencrbasic.compiler import CompileResult

PROFILE = profiles.get("cr300")


def compile_result(text: str) -> CompileResult:
    program, diags = parser.parse_text(text, "t.cr300", profile=PROFILE)
    result = CompileResult("t.cr300", PROFILE, diags)
    checker.check(program, PROFILE, diags, result)
    return program, diags, result


def apply_hook(text: str) -> list[str]:
    """Run the ordinary checker (which now applies `check_unknown_line_words` itself, from
    `checker/__init__.py`) and return the resulting error message list."""
    _, diags, _ = compile_result(text)
    return [d.message for d in diags.errors]


# ---------------------------------------------------------- `_leading_unknown_words` pure walk


def test_single_word():
    assert cr3xx._leading_unknown_words("foo", set()) == ["foo"]


def test_two_bare_words_both_flagged():
    assert cr3xx._leading_unknown_words("foo bar", set()) == ["foo", "bar"]


def test_three_or_more_bare_words_caps_at_the_one_trailing_peek():
    assert cr3xx._leading_unknown_words("foo bar baz", set()) == ["foo", "bar"]
    assert cr3xx._leading_unknown_words("foo bar baz qux", set()) == ["foo", "bar"]


def test_operator_joined_chain_is_not_capped():
    # `alpha-beta-gamma` (no spaces) is one valid operator chain -- every operand in it is
    # checked, then exactly one more (`delta`) as the trailing peek.
    assert cr3xx._leading_unknown_words("alpha-beta-gamma delta", set()) == [
        "alpha", "beta", "gamma", "delta",
    ]


def test_comma_breaks_the_chain_with_no_trailing_peek():
    assert cr3xx._leading_unknown_words("foo, bar", set()) == ["foo"]


def test_peek_requires_a_bare_word_not_a_number():
    assert cr3xx._leading_unknown_words("Part 1.", set()) == ["Part"]


def test_single_word_before_a_period_with_nothing_else():
    assert cr3xx._leading_unknown_words("NOTE.", set()) == ["NOTE"]


def test_known_word_is_walked_but_not_reported():
    # `Is` is a real CR300 token (`data/cr300/functions.json`); it still consumes the trailing
    # peek slot (nothing after it is ever reached), it is just not itself reported.
    assert cr3xx._leading_unknown_words("This is not defined test", {"IS"}) == ["This"]


def test_known_word_leading_the_whole_statement_does_not_suppress_the_next_ones():
    assert cr3xx._leading_unknown_words("Double foo", {"DOUBLE"}) == ["foo"]


def test_known_word_mid_chain_still_ends_scanning():
    assert cr3xx._leading_unknown_words("foo Double bar", {"DOUBLE"}) == ["foo"]


def test_no_leading_word_at_all():
    assert cr3xx._leading_unknown_words(" 1.1 will be a value.", set()) == []


def test_empty_text():
    assert cr3xx._leading_unknown_words("", set()) == []


# ---------------------------------------------------------------- `check_unknown_line_words`


def test_hook_prepends_one_line_per_word_before_the_generic_fallback():
    # No trailing "Expr illegally occurs before BeginProg." here: `checker/structure.py`'s own
    # `S.Unparsed`-`else` branch carves that follow-up/stop out for CR300/CR350 (`docs/loggers.md`
    # "Round 8" Finding 2, oracle-confirmed) -- the statement is before `BeginProg`, but the real
    # compilers do not stop for this shape.
    messages = apply_hook("foo bar baz qux\n\nBeginProg\nEndProg\n")
    assert messages == [
        "foo is not defined.",
        "bar is not defined.",
        # CR300/CR350 echo the raw line terminator before the closing `.`
        # (`Profile.fallback_echo_newline`).
        "Invalid, or out of place expression: foo bar baz qux\n.",
    ]


def test_hook_is_a_noop_on_cr6():
    program, diags, _ = compile_result_for_profile(
        "foo bar baz qux\n\nBeginProg\nEndProg\n", profiles.get("cr6"), "t.cr6")
    before = [d.message for d in diags.errors]
    cr3xx.check_unknown_line_words(program, profiles.get("cr6"), diags)
    after = [d.message for d in diags.errors]
    assert before == after


def compile_result_for_profile(text: str, profile, filename: str):
    program, diags = parser.parse_text(text, filename, profile=profile)
    result = CompileResult(filename, profile, diags)
    checker.check(program, profile, diags, result)
    return program, diags, result


def test_hook_does_not_double_up_the_already_handled_bare_call_shape():
    # A bare unresolved call (`NOTE` with args/parens) is already handled by
    # `checker/structure.py`'s own CR300/CR350 branch (`profile.undefined_call_message`), not by
    # this hook -- it must not add a second `NOTE is not defined.` line.
    messages = apply_hook("NOTE(1)\n\nBeginProg\nEndProg\n")
    assert messages.count("NOTE is not defined.") == 1


def test_hook_is_noop_with_no_unparsed_statements():
    messages = apply_hook("Public X As Float\n\nBeginProg\nEndProg\n")
    assert messages == []


# ------------------------------------------------------------------ `check_too_many_cascade`
# Oracle evidence: scratch probes `t.cr6`/`t2.cr6`/`t3.cr6`/`t4.cr6` against `cr300comp.exe` and
# `cr350comp.exe` (identical on both), plus the corpus's own `dewpoint_1.CR6`/`runprogram_1.CR6`/
# `filemanage_1.CR6`/`goesdata_1.CR6`/`acceptdatarecords_1.CR6` (`checker/cr3xx.py`'s own module
# comment above `check_too_many_cascade` has the full transcript).

_CASCADE_PROGRAM = """\
Public A, B, C

BeginProg
  Scan(1,Sec,3,0)
    Battery(BadVar)
    PortSet(C1,1)
    PortSet(C1,2)
    PortSet(C1,3)
    Delay(0,10,mSec)
  NextScan
EndProg
"""


def test_cascade_chains_to_every_qualifying_statement_not_just_the_first():
    messages = apply_hook(_CASCADE_PROGRAM)
    assert messages == [
        "BadVar is not defined.",
        "Too many parameters:  1.",
        "Too many parameters:  2.",
        "Too many parameters:  3.",
        "Too many parameters:  10.",
    ]


def test_cascade_replaces_a_tainted_statements_own_undefined_name_line():
    # `PortSet(C1,BadVar2)`: BadVar2 is itself undefined, but once tainted a statement's own
    # checks never run at all on CR300/CR350 -- oracle: only the cascade line, never a second
    # `BadVar2 is not defined.` of its own (`t2.cr6`).
    text = ("Public A\n\nBeginProg\n  Scan(1,Sec,3,0)\n    Battery(BadVar)\n"
            "    PortSet(C1,BadVar2)\n    PortSet(C1,2)\n  NextScan\nEndProg\n")
    messages = apply_hook(text)
    assert messages == [
        "BadVar is not defined.",
        "Too many parameters:  BadVar2.",
        "Too many parameters:  2.",
    ]


def test_cascade_does_not_touch_the_first_tainting_statement_itself():
    # The statement that *first* goes trigger-shaped keeps its own real diagnostic untouched;
    # only statements strictly after it are swallowed by the chain (`dewpoint_1.CR6`: the first of
    # two identically-shaped `VoltSE` calls keeps its real `mv5000C is not defined.` pair).
    messages = apply_hook(_CASCADE_PROGRAM)
    assert "Too many parameters:  BadVar." not in messages


def test_cascade_is_a_noop_on_cr6():
    # CR6 has no cascade at all (`checker/cr3xx.py`'s own module comment): only `BadVar`'s own,
    # ordinary undefined-name finding, never a "Too many parameters" line for anything after it.
    program, diags, _ = compile_result_for_profile(_CASCADE_PROGRAM, profiles.get("cr6"), "t.cr6")
    messages = [d.message for d in diags.errors]
    assert len(messages) == 1
    assert "BadVar" in messages[0]
    assert not any(m.startswith("Too many parameters:") for m in messages)


def test_cascade_skips_statements_with_fewer_than_two_arguments():
    # `Battery(BattV)` (one argument) is passed over -- not itself flagged, and not a stop.
    text = ("Public A, BattV\n\nBeginProg\n  Scan(1,Sec,3,0)\n    Battery(BadVar)\n"
            "    Battery(BattV)\n    PortSet(C1,2)\n  NextScan\nEndProg\n")
    messages = apply_hook(text)
    assert messages == [
        "BadVar is not defined.",
        "Too many parameters:  2.",
    ]
