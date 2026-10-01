"""Unit tests for `opencrbasic.checker.cr3xx`. Oracle evidence: `docs/loggers.md` "Round 8" and
"Too many parameters cascade, widened".

`check_unknown_line_words` and `check_too_many_cascade` are both wired into the shared checker
pipeline (`checker/__init__.py`); the tests below call the ordinary `checker.check` pipeline (via
`apply_hook`/`compile_result_for_profile`), which is equivalent and keeps each test focused on one
hook's own effect.
"""

from __future__ import annotations

import pytest

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


def test_single_word_with_nothing_else():
    # `NOTE:` echoes as `NOTE.`; the final `.` is the message's own, not the source's.
    assert cr3xx._leading_unknown_words("NOTE", set()) == ["NOTE"]


def test_word_before_a_period_is_a_table_reference():
    # `instruction.` and `two words.` (oracle, CR300, 2026-10-01): `word.` is never reported.
    assert cr3xx._leading_unknown_words("instruction.\n", set()) == []
    assert cr3xx._leading_unknown_words("two words.\n", set()) == ["two"]


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


def test_undefined_call_skips_a_resolving_argument():
    # `GoodVar` (a declared `Public`) is skipped and `BadName2` (argument 4) is still reported
    # (oracle, CR300, 2026-10-01).
    text = "Public GoodVar\n\nBeginProg\n  FooBar(BadName1,1,GoodVar,BadName2)\nEndProg\n"
    messages = apply_hook(text)
    assert messages == [
        "FooBar is not defined.",
        "BadName1 is not defined.",
        "BadName2 is not defined.",
        "Invalid, or out of place expression:   FooBar(BadName1,1,GoodVar,BadName2)\n.",
    ]


def test_undefined_call_examines_only_four_arguments():
    # `t3.cr6`: literals are skipped, and `BadName3` (argument 7) is past the four examined.
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


def test_undefined_call_resolving_index_continues_the_scan():
    # A declared `Meas(i)` is walked and skipped (oracle, CR300, 2026-10-01).
    text = "Public Meas(5), i\n\nBeginProg\n  FooBar(BadName1,Meas(i),BadName2)\nEndProg\n"
    messages = apply_hook(text)
    assert messages == [
        "FooBar is not defined.",
        "BadName1 is not defined.",
        "BadName2 is not defined.",
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


_CALL_ARG_PROGRAM = (
    "Public X(2), I\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nBeginProg\n"
    "  Scan (1,Sec,0,0)\n    CallTable T\n    Foo({args})\n  NextScan\nEndProg\n")


@pytest.mark.parametrize("profile_name, args, names", [
    ("cr300", "B0, B1, B2, B3, B4, B5", "B0 B1 B2 B3"),
    ("cr350", "B0, B1, B2, B3, B4, B5", "B0 B1 B2 B3"),
    ("cr300", "1, 2, 3, 4, B4", ""),
    ("cr300", "B0(1), B1, B2, B3, B4", "B0"),
    ("cr300", "X+B0, B1, B2, B3, B4", "B0 B1 B2 B3"),
    ("cr300", "B0, B1(1), B2", "B0 B1"),
    ("cr300", "B0(B9), B1", "B0 B9"),
    ("cr300", "B0+B5+B6, B1", "B0 B5 B6 B1"),
    ("cr300", "B0, -B1, B2", "B0 B1 B2"),
    ("cr300", "B0, B1+1, B2", "B0 B1 B2"),
    ("cr300", "B0, B1, B2, B3+B4, B5", "B0 B1 B2 B3"),
    ("cr300", "1, 2, 3, B3+B4", "B3"),
    ("cr300", "B0, 1, X(I), B3", "B0 B3"),
    ("cr300", "B0+B1, B2+B3, B4+B5", "B0 B1 B2 B3 B4 B5"),
    ("cr300", "B0, X(B1), B2", "B0 B1 B2"),
    ("cr300", "X, 0, B0, 2, B1", "B0"),
    ("cr300", "B0, 1, X, 1, B1", "B0"),
    ("cr300", "B0, X, B1, 1, B2", "B0 B1"),
    ("cr300", "T, B1", "T B1"),
])
def test_undefined_call_argument_scan_matches_oracle(profile_name, args, names):
    # Oracle probes 2026-10-01; rule in the comment above `_call_arg_undefined_names`.
    profile = profiles.get(profile_name)
    _, diags, _ = compile_result_for_profile(
        _CALL_ARG_PROGRAM.format(args=args), profile, "t." + profile_name)
    got = [d.message for d in diags.errors if d.line == 8
           and d.message.endswith("is not defined.")]
    assert got == ["Foo is not defined."] + [f"{n} is not defined." for n in names.split()]


def test_cdmvoltse_corpus_declaration_cascade_order():
    from pathlib import Path
    path = Path(__file__).parent / "corpus" / "chm" / "cdmvoltse_1.CR6"
    program, diags = parser.parse_text(path.read_text(errors="replace"), path.name, profile=PROFILE)
    result = CompileResult(path.name, PROFILE, diags)
    checker.check(program, PROFILE, diags, result)
    by_line: dict[int, list[str]] = {}
    for d in diags.errors:
        by_line.setdefault(d.line, []).append(d.message)
    assert by_line[29].count("Bar_inHg is not defined.") == 2
    l23 = by_line[23]
    assert l23[:3] == ["CDM_Battery is not defined.", "CDM_A108 is not defined.",
                       "Batt_Volt is not defined."]
    assert l23[3].startswith("Invalid, or out of place expression")


_CASCADE_HEAD = "Dim Z banana\nPublic PTemp\nBeginProg\n  Scan (1,Sec,0,0)\n    {call}\n  NextScan\nEndProg\n"
_BANANA = (1, "Invalid Variable declaration banana.")
_SEC = (4, "Too many parameters:  Sec.")


@pytest.mark.parametrize("text, expected", [
    pytest.param(
        _CASCADE_HEAD.format(call="PanelTemp(PTemp,15000)"),
        [_BANANA, _SEC, (5, "PTemp is not defined."), (5, "Too many parameters:  15000.")],
        id="c1"),
    pytest.param(
        _CASCADE_HEAD.format(call="PanelTemp(Nope,15000)"),
        [_BANANA, _SEC, (5, "Nope is not defined."), (5, "Too many parameters:  15000.")],
        id="c2"),
    pytest.param(
        "Public PTemp\nDim Z banana\nBeginProg\n  Scan (1,Sec,0,0)\n"
        "    PanelTemp(PTemp,15000)\n  NextScan\nEndProg\n",
        [(2, "Invalid Variable declaration banana."), _SEC,
         (5, "Too many parameters:  15000.")],
        id="c3"),
    pytest.param(
        "Dim Z banana\nPublic A(3), I\nBeginProg\n  Scan (1,Sec,0,0)\n"
        "    PanelTemp(A(I),15000)\n  NextScan\nEndProg\n",
        [_BANANA, (2, "Too many parameters:  I."), _SEC, (5, "A is not defined."),
         (5, "I is not defined."), (5, "Dimensions not declared: A(I)."),
         (5, "Too many parameters:  15000.")],
        id="c4"),
    pytest.param(
        _CASCADE_HEAD.format(call="PanelTemp(PTemp(),15000)"),
        [_BANANA, _SEC, (5, "PTemp is not defined."), (5, "Too many parameters:  15000.")],
        id="c6"),
    pytest.param(
        _CASCADE_HEAD.format(call="VoltSe(PTemp,1,Nope,1,0,0,15000,1,0)"),
        [_BANANA, _SEC, (5, "PTemp is not defined."), (5, "Too many parameters:  1.")],
        id="c7"),
])
def test_cascade_rewrite_checks_first_argument(text, expected):
    # Oracle (CR300) 2026-10-01: after an invalid declaration, the first argument of the
    # cascade-rewritten call is checked for definition, in order with the other findings.
    _, diags, _ = compile_result(text)
    assert [(d.line, d.message) for d in diags.errors] == expected


def test_cascade_rewrite_skips_first_argument_that_declares_a_name():
    text = ("Dim Z banana\nPublic X\nDataTable (Table1,True,-1)\n  Sample (1,X,FP2)\nEndTable\n"
            "BeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n")
    _, diags, _ = compile_result(text)
    line3 = [d.message for d in diags.errors if d.line == 3]
    assert not any("is not defined" in m for m in line3)
    assert "Too many parameters:  True." in line3


_DEST = "Illegal destination variable type, expecting Float."
_FLOAT_DEST_FRAME = ("Public X, L As Long, St As String, D As Double\nBeginProg\n"
                     "  Scan (1,Sec,0,0)\n{body}\n  NextScan\nEndProg\n")


def _float_dest_body(*lines):
    return "\n".join("    " + line for line in lines)


@pytest.mark.parametrize("profile_name,body,expected", [
    pytest.param("cr300", ("PanelTemp(L,60)",),
                 [(4, _DEST), (4, "Too many parameters:  60.")], id="long"),
    pytest.param("cr300", ("PanelTemp(X+1,60)",),
                 [(4, _DEST), (4, "Too many parameters:  60.")], id="expression"),
    pytest.param("cr300", ("PanelTemp(D,60)",),
                 [(4, _DEST), (4, "Too many parameters:  60.")], id="double"),
    pytest.param("cr300", ("VoltSe(L,1,mV1000,1,0,0,60,1,0)",),
                 [(4, _DEST), (4, "Too many parameters:  1.")], id="voltse"),
    pytest.param("cr300", ("X = Nope2", "PanelTemp(L,60)", "X = Nope"),
                 [(4, "Nope2 is not defined."), (5, _DEST), (5, "Too many parameters:  60."),
                  (6, "Nope is not defined.")], id="surrounding_lines"),
    pytest.param("cr300", ("PanelTemp(L,60)", "Battery(L)"),
                 [(4, _DEST), (4, "Too many parameters:  60."), (5, _DEST)],
                 id="following_call"),
    pytest.param("cr300", ("PanelTemp(Nope,60)",),
                 [(4, "Nope is not defined."), (4, "Too many parameters:  60.")],
                 id="undefined_name"),
    pytest.param("cr300", ("PanelTemp(L,60)", "PortSet(1,1)"),
                 [(4, _DEST), (4, "Too many parameters:  60."),
                  (5, "Too many parameters:  1.")], id="following_portset"),
    pytest.param("cr300", ("VoltSe(Nope,1,mV1000,1,0,0,60,1,0)",),
                 [(4, "Nope is not defined."), (4, "Too many parameters:  1.")],
                 id="voltse_undefined"),
    pytest.param("cr350", ("PanelTemp(L,60)",),
                 [(4, _DEST), (4, "Too many parameters:  60.")], id="cr350"),
])
def test_float_destination_first_argument(profile_name, body, expected):
    # Oracle (CR300) 2026-10-01: the first argument of a cascade-rewritten call is checked
    # for a Float destination, in order with the other findings.
    text = _FLOAT_DEST_FRAME.format(body=_float_dest_body(*body))
    _, diags, _ = compile_result_for_profile(text, profiles.get(profile_name), "t.cr300")
    assert [(d.line, d.message) for d in diags.errors] == expected


def test_float_destination_first_argument_accepts_float():
    # Oracle (CR300) 2026-10-01: a Float first argument raises no destination error.
    text = _FLOAT_DEST_FRAME.format(
        body=_float_dest_body("VoltSe(X,1,mV1000,1,0,0,60,1,0)"))
    _, diags, _ = compile_result(text)
    assert _DEST not in [d.message for d in diags.errors]


_PROSE_F = "Public X\nBeginProg\n  Scan (1,Sec,0,0)\n"
_PROSE_END = "  NextScan\nEndProg\n"
_PROSE_SEC = "Too many parameters:  Sec."


@pytest.mark.parametrize("text, expected", [
    pytest.param(
        "The program\ninstruction.\n" + _PROSE_F + _PROSE_END,
        [(1, "The is not defined."), (1, "program is not defined."),
         (1, "Invalid, or out of place expression: The program."), (5, _PROSE_SEC)],
        id="the-program-instruction"),
    pytest.param(
        "The program\ntwo words.\n" + _PROSE_F + _PROSE_END,
        [(1, "The is not defined."), (1, "program is not defined."),
         (1, "Invalid, or out of place expression: The program."),
         (2, "two is not defined."),
         (2, "Invalid, or out of place expression: two words.."), (5, _PROSE_SEC)],
        id="the-program-two-words"),
    pytest.param(
        "two words.\n" + _PROSE_F + _PROSE_END,
        [(1, "two is not defined."),
         (1, "Invalid, or out of place expression: two words.."), (4, _PROSE_SEC)],
        id="two-words"),
    pytest.param(
        _PROSE_F + "instruction.\n" + _PROSE_END,
        [(4, "Could not find DataTable instruction."),
         (4, "Invalid, or out of place expression: instruction..")],
        id="body-instruction"),
    pytest.param(
        _PROSE_F + "X = Nope\ninstruction.\n" + _PROSE_END,
        [(4, "Nope is not defined.")],
        id="undefined-then-instruction"),
    pytest.param(
        _PROSE_F + "foo.bar\n" + _PROSE_END,
        [(4, "Could not find DataTable foo."),
         (4, "Invalid, or out of place expression: foo.bar.")],
        id="foo-dot-bar"),
    pytest.param(
        _PROSE_F + "instruction.\n    PortSet(1,1)\n" + _PROSE_END,
        [(4, "Could not find DataTable instruction."),
         (4, "Invalid, or out of place expression: instruction..")],
        id="instruction-then-call"),
    pytest.param(
        _PROSE_F + "foo bar.baz\n" + _PROSE_END,
        [(4, "foo is not defined."),
         (4, "Invalid, or out of place expression: foo bar.baz.")],
        id="foo-bar-dot-baz"),
    pytest.param(
        _PROSE_F + "X.\n" + _PROSE_END,
        [(4, "Could not find DataTable X."),
         (4, "Invalid, or out of place expression: X..")],
        id="x-dot"),
])
def test_table_shaped_prose_lines(text, expected):
    # Oracle (CR300) 2026-10-01. The echo ends at end-of-line with "\n" before the final ".";
    # messages are compared with the newline removed.
    _, diags, _ = compile_result(text)
    assert [(d.line, d.message.replace("\n", "")) for d in diags.errors] == expected
