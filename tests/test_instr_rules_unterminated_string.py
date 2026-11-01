"""Unit tests for `checker/instr_rules.py`'s `_check_unterminated_string_param`: a required
`expr_or_name` (type code 26) argument whose opening `"` never closes before the physical line
ends. Rule and oracle evidence: `docs/checker.md` "Required parameter missing" (the
`_UNTERMINATED_STRING_KINDS` addition).

Oracle-confirmed against `CR6Comp.exe` (`WINEPREFIX=$HOME/.wine-cr6 wine .../CR6Comp.exe t.cr6`,
no CLI flags), scratch probes only, not saved as `knowledge/test_results/` captures since this is
`opencrbasic`, not the CRBasic library work `AGENTS.md` governs:

    Public Commands As String * 10
    BeginProg
      HTTPOut("<title>CR1000X
    Datalogger</title>")
    EndProg

    -> t.cr6 -- Compile Failed!
       line 3: Invalid or missing parameter.

(one diagnostic only -- the continuation line 4, itself a second lex failure, gets nothing, which
this test also locks in.)
"""

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

PROFILE = profiles.get("cr6")


def messages(text: str) -> list[tuple[int, str]]:
    program, diags = parser.parse_text(text, "t.CR6")
    result = CompileResult("t.CR6", PROFILE, diags)
    checker.check(program, PROFILE, diags, result)
    return [(d.line, d.message) for d in diags.errors]


def test_unterminated_string_in_httpout_reports_missing_parameter():
    text = 'Public Commands As String * 10\nBeginProg\n  HTTPOut("<title>CR1000X\nDatalogger</title>")\nEndProg\n'
    assert messages(text) == [(3, "Invalid or missing parameter.")]


def test_unterminated_string_repeats_once_per_occurrence():
    # `httpout_1.CR6`/`webpagebeginwebpageend_1.CR6` (CHM corpus) shape: several independent
    # unterminated `HTTPOut` calls in one file, each its own line, each its own diagnostic, the
    # continuation line of each pair silent throughout (`checker/__init__.py`'s pass-1/pass-2 gate
    # already covers the rest of the file once any pass-1 error exists, so no other line needs
    # its own suppression here).
    text = (
        "Public Commands As String * 10\n"
        "BeginProg\n"
        '  HTTPOut("<title>A\n'
        'Cont</title>")\n'
        '  HTTPOut("<title>B\n'
        'Cont</title>")\n'
        "EndProg\n"
    )
    assert messages(text) == [
        (3, "Invalid or missing parameter."),
        (5, "Invalid or missing parameter."),
    ]


def test_unterminated_string_in_bool_expr_position_not_flagged_as_missing_parameter():
    # Oracle-confirmed *different* real message for this shape (`call_1.CR6`, CHM corpus):
    # `String without end quote`, not `Invalid or missing parameter.` -- `IF`'s own condition is
    # type code 7 (`bool_expr`), not 26 (`expr_or_name`), so `_UNTERMINATED_STRING_KINDS`
    # deliberately excludes it. Implementing `String without end quote` itself is out of scope
    # here (no oracle evidence collected for its own rule); this test only locks in that the new
    # check does not over-fire onto this shape.
    text = 'Public X As Float\nBeginProg\n  IF X <= 29 Then X = "TEMP\nWATCH"\nEndProg\n'
    assert all(msg != "Invalid or missing parameter." for _, msg in messages(text))


def test_unterminated_string_leading_word_not_a_known_instruction_not_flagged():
    # The leading word must resolve against `profile.instructions` -- ordinary prose text that
    # happens to contain an unterminated string is not itself grounds for this message.
    text = 'Public X As Float\nBeginProg\n  This is "not a real instruction\nEndProg\n'
    assert all(msg != "Invalid or missing parameter." for _, msg in messages(text))
