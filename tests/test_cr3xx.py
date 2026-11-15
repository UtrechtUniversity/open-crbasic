"""Unit tests for `opencrbasic.checker.cr3xx`. Oracle evidence: `docs/loggers.md` "Round 8".

`check_unknown_line_words` is wired into the shared checker pipeline (`checker/__init__.py`); the
tests below call the ordinary `checker.check` pipeline (via `apply_hook`/`compile_result_for_profile`),
which keeps each test focused on its own rule.
"""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic import syntax as S
from opencrbasic.checker import cr3xx
from opencrbasic.compiler import CompileResult
from opencrbasic.diagnostics import Diagnostics

PROFILE = profiles.get("cr300")


def compile_result(text: str) -> tuple[S.Program, Diagnostics, CompileResult]:
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
        "alpha",
        "beta",
        "gamma",
        "delta",
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


def test_leading_number_then_one_peeked_word():
    # oracle, CR300, 2026-10-01: ` 1.1 will be a value.` -> `will is not defined.`
    assert cr3xx._leading_unknown_words(" 1.1 will be a value.", set()) == ["will"]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("foo and bar baz qux", ["foo", "bar", "baz"]),
        ("foo to bar baz qux", ["foo", "bar", "baz"]),
        ("foo AND bar AND baz qux zap", ["foo", "bar", "baz", "qux"]),
        ("foo and 5 bar baz", ["foo", "bar"]),
        ("5 foo+bar baz", ["foo"]),
        ("instruction to measure two differential thermocouples.", ["instruction", "measure", "two"]),
    ],
)
def test_keyword_operators_join_the_chain(text, expected):
    # oracle, CR300, 2026-10-01 (`Is`/`Not` do not join it)
    assert cr3xx._leading_unknown_words(text, set()) == expected


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
        "foo bar baz qux\n\nBeginProg\nEndProg\n", profiles.get("cr6"), "t.cr6"
    )
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


_DEST = "Illegal destination variable type, expecting Float."
_FLOAT_DEST_FRAME = (
    "Public X, L As Long, St As String, D As Double\nBeginProg\n  Scan (1,Sec,0,0)\n{body}\n  NextScan\nEndProg\n"
)


def _float_dest_body(*lines):
    return "\n".join("    " + line for line in lines)


@pytest.mark.parametrize(
    "profile_name,body,expected",
    [
        pytest.param("cr300", ("PanelTemp(L,60)",), [(4, _DEST), (4, "Too many parameters:  60.")], id="long"),
        pytest.param("cr300", ("PanelTemp(X+1,60)",), [(4, _DEST), (4, "Too many parameters:  60.")], id="expression"),
        pytest.param("cr300", ("PanelTemp(D,60)",), [(4, _DEST), (4, "Too many parameters:  60.")], id="double"),
        pytest.param(
            "cr300", ("VoltSe(L,1,mV1000,1,0,0,60,1,0)",), [(4, _DEST), (4, "Too many parameters:  1.")], id="voltse"
        ),
        pytest.param(
            "cr300",
            ("X = Nope2", "PanelTemp(L,60)", "X = Nope"),
            [(4, "Nope2 is not defined."), (5, _DEST), (5, "Too many parameters:  60."), (6, "Nope is not defined.")],
            id="surrounding_lines",
        ),
        pytest.param(
            "cr300",
            ("PanelTemp(L,60)", "Battery(L)"),
            [(4, _DEST), (4, "Too many parameters:  60."), (5, _DEST)],
            id="following_call",
        ),
        pytest.param(
            "cr300",
            ("PanelTemp(Nope,60)",),
            [(4, "Nope is not defined."), (4, "Too many parameters:  60.")],
            id="undefined_name",
        ),
        pytest.param(
            "cr300",
            ("PanelTemp(L,60)", "PortSet(1,1)"),
            [(4, _DEST), (4, "Too many parameters:  60.")],
            id="following_portset",
        ),
        pytest.param(
            "cr300",
            ("VoltSe(Nope,1,mV1000,1,0,0,60,1,0)",),
            [(4, "Nope is not defined."), (4, "Too many parameters:  1.")],
            id="voltse_undefined",
        ),
        pytest.param("cr350", ("PanelTemp(L,60)",), [(4, _DEST), (4, "Too many parameters:  60.")], id="cr350"),
    ],
)
def test_float_destination_first_argument(profile_name, body, expected):
    # Oracle (CR300) 2026-10-01: the first argument of a cascade-rewritten call is checked
    # for a Float destination, in order with the other findings.
    text = _FLOAT_DEST_FRAME.format(body=_float_dest_body(*body))
    _, diags, _ = compile_result_for_profile(text, profiles.get(profile_name), "t.cr300")
    assert [(d.line, d.message) for d in diags.errors] == expected


def test_float_destination_first_argument_accepts_float():
    # Oracle (CR300) 2026-10-01: a Float first argument raises no destination error.
    text = _FLOAT_DEST_FRAME.format(body=_float_dest_body("VoltSe(X,1,mV1000,1,0,0,60,1,0)"))
    _, diags, _ = compile_result(text)
    assert _DEST not in [d.message for d in diags.errors]


_PROSE_F = "Public X\nBeginProg\n  Scan (1,Sec,0,0)\n"
_PROSE_END = "  NextScan\nEndProg\n"


@pytest.mark.parametrize(
    "text, expected",
    [
        pytest.param(
            "The program\ninstruction.\n" + _PROSE_F + _PROSE_END,
            [
                (1, "The is not defined."),
                (1, "program is not defined."),
                (1, "Invalid, or out of place expression: The program."),
                (2, "Could not find DataTable instruction."),
                (2, "Invalid, or out of place expression: instruction.."),
                (2, "Expr illegally occurs before BeginProg."),
            ],
            id="the-program-instruction",
        ),
        pytest.param(
            "The program\ntwo words.\n" + _PROSE_F + _PROSE_END,
            [
                (1, "The is not defined."),
                (1, "program is not defined."),
                (1, "Invalid, or out of place expression: The program."),
                (2, "two is not defined."),
                (2, "Invalid, or out of place expression: two words.."),
            ],
            id="the-program-two-words",
        ),
        pytest.param(
            "two words.\n" + _PROSE_F + _PROSE_END,
            [(1, "two is not defined."), (1, "Invalid, or out of place expression: two words..")],
            id="two-words",
        ),
        pytest.param(
            _PROSE_F + "instruction.\n" + _PROSE_END,
            [(4, "Could not find DataTable instruction."), (4, "Invalid, or out of place expression: instruction..")],
            id="body-instruction",
        ),
        pytest.param(
            _PROSE_F + "X = Nope\ninstruction.\n" + _PROSE_END,
            [
                (4, "Nope is not defined."),
                (5, "Could not find DataTable instruction."),
                (5, "Invalid, or out of place expression: instruction.."),
            ],
            id="undefined-then-instruction",
        ),
        pytest.param(
            _PROSE_F + "foo.bar\n" + _PROSE_END,
            [(4, "Could not find DataTable foo."), (4, "Invalid, or out of place expression: foo.bar.")],
            id="foo-dot-bar",
        ),
        pytest.param(
            _PROSE_F + "instruction.\n    PortSet(1,1)\n" + _PROSE_END,
            [(4, "Could not find DataTable instruction."), (4, "Invalid, or out of place expression: instruction..")],
            id="instruction-then-call",
        ),
        pytest.param(
            _PROSE_F + "foo bar.baz\n" + _PROSE_END,
            [(4, "foo is not defined."), (4, "Invalid, or out of place expression: foo bar.baz.")],
            id="foo-bar-dot-baz",
        ),
        pytest.param(
            _PROSE_F + "X.\n" + _PROSE_END,
            [(4, "Could not find DataTable X."), (4, "Invalid, or out of place expression: X..")],
            id="x-dot",
        ),
    ],
)
def test_table_shaped_prose_lines(text, expected):
    # Oracle (CR300) 2026-10-01. The echo ends at end-of-line with "\n" before the final ".";
    # messages are compared with the newline removed.
    _, diags, _ = compile_result(text)
    assert [(d.line, d.message.replace("\n", "")) for d in sorted(diags.errors, key=lambda d: d.line)] == expected


@pytest.mark.parametrize(
    "stmt,expected",
    [
        (
            "    Y = 2 * ASin( 1",
            [(4, "Y is not defined."), (4, "No Matching, or out of place ) in expression:     Y = 2 * ASin( 1\n.")],
        ),
        ("    X = 2 * ASin( 1", [(4, "No Matching, or out of place ) in expression:     X = 2 * ASin( 1\n.")]),
        ("    Y = (1", [(4, "Y is not defined."), (4, "No Matching, or out of place ) in expression:     Y = (1\n.")]),
    ],
)
def test_unclosed_paren_assignment(stmt, expected):
    # Oracle, CR300, 2026-10-01: end-of-line echo newline; an undefined target is reported first.
    _, diags, _ = compile_result(f"Public X\nBeginProg\n  Scan (1,Sec,0,0)\n{stmt}\n  NextScan\nEndProg\n")
    assert [(d.line, d.message) for d in diags.errors] == expected


def test_inline_if_scan_keeps_later_prose_lines_checkable():
    # An unreadable one-line `If` condition followed by a prose line: the inline scan once
    # replaced the first-declaration map, and the later line crashed the checker (round 47).
    _, diags, _ = compile_result(
        "Public Y\nBeginProg\n  Scan (1,Sec,0,0)\n    If Y Z Then Y = 1\n    foo bar baz\n  NextScan\nEndProg\n"
    )
    assert "Internal error: unable to compile program." not in [d.message for d in diags.items]
