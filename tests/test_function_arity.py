"""Unit tests for `checker/expressions.py`'s `check_function_arity` (built-in function call
arity inside expressions). Rules and oracle evidence: `docs/checker.md`.
"""

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


# ------------------------------------------------------------------ too few (Round needs 2 args)


def test_too_few_args_in_assignment():
    result = compile_text(wrapped("    X = Round(X)\n"))
    assert messages_of(result) == ["Expression expects , :     X = Round(X)."]


def test_too_few_args_in_bare_call():
    result = compile_text(wrapped("    Round(X)\n"))
    assert messages_of(result) == ["Expression expects , :     Round(X)."]


def test_too_few_args_echoes_only_the_argument_not_the_whole_instruction():
    # `Randomize` is a real `Instruction`, so the echo is that argument's own verbatim span, not
    # the whole statement (oracle-confirmed, `docs/checker.md`) -- unlike the bare-`Call` case.
    result = compile_text(wrapped("    Randomize(Round(X))\n"))
    assert messages_of(result) == ["Expression expects , : Round(X)."]


def test_too_few_args_nested_inside_a_bare_call_echoes_the_whole_statement():
    # `WatchdogTimer` is a bare `Call` (not a recognised `Instruction`), so the echo is the whole
    # statement, even though the arity problem is on the *nested* `Round(X)` call (oracle-confirmed
    # `WatchdogTimer(0,Round(X))`, `docs/checker.md`).
    result = compile_text(wrapped("    WatchdogTimer(0,Round(X))\n"))
    assert messages_of(result) == ["Expression expects , :     WatchdogTimer(0,Round(X))."]


# ------------------------------------------------------------------ too many


def test_too_many_args_in_bare_call():
    result = compile_text(wrapped("    Round(X,1,2)\n"))
    assert messages_of(result) == ["No Matching, or out of place ) in expression:     Round(X,1,2)."]


def test_too_many_args_in_assignment():
    result = compile_text(wrapped("    X = Round(X,1,2)\n"))
    assert messages_of(result) == [
        "No Matching, or out of place ) in expression:     X = Round(X,1,2)."
    ]


# ------------------------------------------------------------------ pass-1 tier


def test_arity_error_fires_alongside_an_unrelated_earlier_pass1_error():
    # Oracle-confirmed (`docs/checker.md` "Pass model"): pass-1 errors all accumulate regardless of
    # what else is wrong in the file.
    text = "Public\n" + wrapped("    Round(X,1,2)\n")
    result = compile_text(text)
    assert "Variable not named." in messages_of(result)
    assert "No Matching, or out of place ) in expression:     Round(X,1,2)." in messages_of(result)


def test_arity_error_suppresses_an_unrelated_pass2_finding_elsewhere_in_the_file():
    # Oracle-confirmed (`docs/checker.md`): once pass 1 finds anything anywhere in the file, pass 2
    # (here, `expressions.check`'s own undefined-symbol check on a different, otherwise-clean
    # statement) does not run at all.
    result = compile_text(wrapped("    Randomize(BazQux)\n    X = Round(X)\n"))
    assert messages_of(result) == ["Expression expects , :     X = Round(X)."]


# ------------------------------------------------------------------ at most one per statement


def test_only_the_first_bad_position_is_reported_per_instruction():
    # The second statement has *two* independent bad positions (0 and 1); only the first (0) is
    # reported -- oracle-confirmed, `docs/checker.md`. One diagnostic per statement, so the second
    # statement's own single diagnostic happens to have the same text as the first statement's.
    result = compile_text(wrapped("    WriteIO(1,Round(X))\n    WriteIO(Round(X),Round(Y))\n"))
    msgs = messages_of(result)
    assert msgs == ["Expression expects , : Round(X)."] * 2


# ------------------------------------------------------------------ never rejects a genuinely
# accepted count (`_ARITY_MIN_SAFE`/`_ARITY_MAX_SAFE`'s own safety margin)


def test_serialopen_with_its_real_but_undocumented_sixth_argument_is_not_flagged():
    # Oracle-confirmed (`docs/checker.md`): `SerialOpen` genuinely accepts 6 arguments on CR6/
    # CR1000X even though `data/functions.json` lists `nargs=5` -- must never be flagged as "too
    # many".
    result = compile_text(wrapped("    SerialOpen(ComC1,9600,0,0,1000,1)\n"))
    assert messages_of(result) == []


def test_serialopen_exact_nargs_is_not_flagged_on_cr350_despite_its_own_table_nargs_of_6():
    # `docs/checker.md`: CR350's own `functions.json` gives `SerialOpen` `nargs=6` (it already bakes
    # in the 6th argument CR6/CR1000X's table omits), so the ordinary 5-argument form must not be
    # flagged as "too few" there -- this is exactly the false reject a name-only allow list caused
    # during development (`tests/corpus/chm/serialflush_1.CR6`), fixed by keying `_ARITY_MIN_SAFE`/
    # `_ARITY_MAX_SAFE` on the tested `nargs` value too, not just the function's name.
    result = compile_text(wrapped("    SerialOpen(ComRS232,9600,0,0,1000)\n"), profile=CR350)
    assert messages_of(result) == []


def test_uniform_argtypes_math_function_too_many_args():
    # `Floor`/`Sin`/... have `argtypes: null` (a uniform single-float argument, `docs/binary.md`)
    # rather than a decoded `argtypes` list, but the same oracle sweep found the rule holds for
    # them too (`docs/checker.md`).
    result = compile_text(wrapped("    X = Floor(X,1)\n"))
    assert messages_of(result) == ["No Matching, or out of place ) in expression:     X = Floor(X,1)."]


def test_abs_too_many_args_uses_its_own_incomplete_expression_message():
    # Oracle-confirmed exception (`docs/checker.md`): `Abs(x,y)` does not use the ordinary arity
    # message, but is still a pass-1 error.
    result = compile_text(wrapped("    X = Abs(X,1)\n"))
    assert messages_of(result) == ["Incomplete expression:     X = Abs(X,1)."]


def test_sprintf_variadic_argument_counts_are_never_flagged():
    # Oracle-confirmed genuinely variadic (2..9 arguments all compile clean, `docs/checker.md`) --
    # deliberately absent from both allow-lists.
    result = compile_text(wrapped('    X = Sprintf("%d %d %d",1,"x",1,1,1)\n'))
    assert messages_of(result) == []


# ------------------------------------------------------------------ CR300/CR350 message shape


def test_cr300_echoes_with_a_trailing_newline_before_the_period():
    # Oracle-confirmed (`docs/checker.md`): CR300/CR350 put the closing `.` alone on the next line,
    # unlike CR6/CR1000X.
    result = compile_text(wrapped("    X = Round(X)\n"), profile=CR300)
    assert messages_of(result) == ["Expression expects , :     X = Round(X)\n."]


def test_cr1000x_matches_cr6_message_shape():
    result = compile_text(wrapped("    X = Round(X)\n"), profile=CR1000X)
    assert messages_of(result) == ["Expression expects , :     X = Round(X)."]
