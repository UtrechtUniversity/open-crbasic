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


# ------------------------------------------ CR300/CR350 fallback echo newline (docs/loggers.md
# "Generic fallback echo on CR300/CR350"): the closing `.` moves to its own output line only when
# the echoed text truly reaches the physical end of its source line. Oracle-confirmed (scratch
# probes on cr300comp.exe, no option flags): `Zzq1(1,2)` alone on a line -> newline before `.`;
# `Zzq2(3,4) 'comment` -> no newline, `.` right after the trailing space a stripped comment left
# behind; `Zzq3(5,6):` (trailing `:`, nothing after) -> no newline either, `.` right after the
# text a `:` split cut short.


def test_trailing_comment_suppresses_the_newline():
    messages = apply_hook("BeginProg\n  Zzq2(3,4) 'comment\nEndProg\n")
    assert messages == [
        "Zzq2 is not defined.",
        "Invalid, or out of place expression:   Zzq2(3,4) .",
    ]


def test_trailing_colon_segment_suppresses_the_newline():
    messages = apply_hook("BeginProg\n  Zzq3(5,6):\nEndProg\n")
    assert messages == [
        "Zzq3 is not defined.",
        "Invalid, or out of place expression:   Zzq3(5,6).",
    ]


def test_reaching_the_true_end_of_line_still_gets_the_newline():
    messages = apply_hook("BeginProg\n  Zzq1(1,2)\nEndProg\n")
    assert messages == [
        "Zzq1 is not defined.",
        "Invalid, or out of place expression:   Zzq1(1,2)\n.",
    ]


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


# ------------------------------------------------------------------ `check_undefined_call_args`
# Oracle evidence: `docs/loggers.md` "CDM_* argument names" -- corpus (`cdmexcitei_1.CR6`,
# `cdmexcitev_1.CR6`, `cdmbrhalf3w_1.CR6`, `cdmfftfilt_1.CR6`, `cdmmuxselect_1.CR6`, ...) plus
# scratch probe `t3.cr6` against `cr300comp.exe`/`cr350comp.exe` (identical on both).


def test_undefined_call_flags_each_undefined_argument_past_a_skipped_literal():
    # `FooBar(BadName1,1,BadName2)`: the undefined call's own name, then each undefined bare-name
    # argument in order, the literal `1` in between silently skipped over (neither flagged nor a
    # stop) -- `cdmexcitei_1.CR6`'s own `CDM_ExciteI(CDM_A108,1,X1,ExmA,10)` shape.
    messages = apply_hook("BeginProg\n  FooBar(BadName1,1,BadName2)\nEndProg\n")
    assert messages == [
        "FooBar is not defined.",
        "BadName1 is not defined.",
        "BadName2 is not defined.",
        "Invalid, or out of place expression:   FooBar(BadName1,1,BadName2)\n.",
    ]


def test_undefined_call_stops_at_the_first_resolving_argument():
    # `FooBar(BadName1,1,GoodVar,BadName2)`: `GoodVar` (a declared `Public`) stops the scan for
    # good -- `BadName2` after it is never reached, even though it is itself undefined
    # (`cdmbrhalf3w_1.CR6`'s own `CDM_BrHalf3W(CDM_A108,1,HBr3W,1,mv200,1,X1,...)` shape, `t3.cr6`).
    text = "Public GoodVar\n\nBeginProg\n  FooBar(BadName1,1,GoodVar,BadName2)\nEndProg\n"
    messages = apply_hook(text)
    assert messages == [
        "FooBar is not defined.",
        "BadName1 is not defined.",
        "Invalid, or out of place expression:   FooBar(BadName1,1,GoodVar,BadName2)\n.",
    ]


def test_undefined_call_stops_past_two_skipped_literals():
    # `t3.cr6`: two literals (`1`, `2`) in a row between the two undefined names are both skipped
    # over without breaking the chain, and a resolving argument after the second undefined name
    # still stops the scan before the final, otherwise-undefined `BadName3`.
    text = ("Public GoodKnownVar\n\nBeginProg\n"
            "  FooBar(BadName1,1,2,BadName2,3,GoodKnownVar,BadName3)\nEndProg\n")
    messages = apply_hook(text)
    assert messages == [
        "FooBar is not defined.",
        "BadName1 is not defined.",
        "BadName2 is not defined.",
        "Invalid, or out of place expression:   "
        "FooBar(BadName1,1,2,BadName2,3,GoodKnownVar,BadName3)\n.",
    ]


def test_undefined_call_index_head_resolving_stops_the_scan():
    # `Meas(i)`: only the `Index`'s own head name (`Meas`) is checked, not its own sub-argument
    # (`i`) -- `cdmmuxselect_1.CR6`'s own `CDM_VoltDiff(CDM_A108,1,Meas(i),1,mV5000,...)` shape,
    # where a resolving `Meas` stops the scan before the genuinely-undefined `mV5000` is reached.
    text = "Public Meas(5), i\n\nBeginProg\n  FooBar(BadName1,Meas(i),BadName2)\nEndProg\n"
    messages = apply_hook(text)
    assert messages == [
        "FooBar is not defined.",
        "BadName1 is not defined.",
        "Invalid, or out of place expression:   FooBar(BadName1,Meas(i),BadName2)\n.",
    ]


def test_undefined_call_with_no_undefined_arguments_adds_no_lines():
    # `CDM_MuxSelect(CDM_A108,1,1,2,10,4,1)`-shaped: a single undefined name at argument 0, every
    # other argument a literal -- nothing left to flag past it.
    messages = apply_hook("BeginProg\n  FooBar(BadName1,1,2,3)\nEndProg\n")
    assert messages == [
        "FooBar is not defined.",
        "BadName1 is not defined.",
        "Invalid, or out of place expression:   FooBar(BadName1,1,2,3)\n.",
    ]


def test_undefined_call_args_is_a_noop_on_cr6():
    program, diags, _ = compile_result_for_profile(
        "BeginProg\n  FooBar(BadName1,1,BadName2)\nEndProg\n", profiles.get("cr6"), "t.cr6")
    before = [d.message for d in diags.errors]
    cr3xx.check_undefined_call_args(program, profiles.get("cr6"), diags)
    after = [d.message for d in diags.errors]
    assert before == after
