"""Unit tests for `opencrbasic.checker.cr3xx`. Oracle evidence: `docs/loggers.md` "Round 8".

`check_unknown_line_words` is now wired into the shared checker pipeline (`checker/__init__.py`,
right after `cr3xx.check_expr_cascade`); the tests below still call it directly (via `apply_hook`)
after running the ordinary `checker.check` pipeline, which is equivalent and keeps each test
focused on this one hook's own effect.
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
