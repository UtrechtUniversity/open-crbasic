"""Unit tests for the `Units`/`Alias` "target parsed, no '=' on this line" echo quirk.

Oracle-confirmed (`docs/checker.md` "Echo quirk", scratch probes under Wine against
`CR6Comp.exe`, 2026-09-27): when a `Units`/`Alias` target parses as a valid lvalue but the `=`
itself runs off the end of that physical line (a CHM line-wrap with no `_` continuation), the
real compiler's echo embeds that line's own trailing newline before the closing `.`, so the
message visually spans two output lines:

    Public x
    foo bar baz
    Units x
    BeginProg
    EndProg
    -> line 3: Need '=' in units definition: x
       .

    Public rTime(9)
    Alias rTime(1)
    = Year
    BeginProg
    EndProg
    -> line 2: Need '=' in alias definition: rTime(1)
       .

This is distinct from the fully-empty-target case (`Units`/`Alias` alone on its own line, no
target at all), which gets the same message with a plain empty echo and no embedded newline
(already covered by `test_checker.py`'s `test_units_without_eq`/`test_alias_without_eq`; see
`docs/checker.md` for the corpus evidence, e.g. `am25t_1.CR6`, `ProgramStructure_1.CR6`).
"""

from opencrbasic import checker, parser, profiles
from opencrbasic import syntax as S
from opencrbasic.compiler import CompileResult

PROFILE = profiles.get("cr6")


def compile_text(text: str):
    program, diags = parser.parse_text(text, "t.CR6")
    result = CompileResult("t.CR6", PROFILE, diags)
    checker.check(program, PROFILE, diags, result)
    return result


def messages(text: str) -> list[str]:
    return [d.message for d in compile_text(text).diagnostics.errors]


# ------------------------------------------------------------------ Units


def test_units_target_present_no_eq_embeds_newline_before_period():
    # Oracle probe (`t.cr6` under Wine): `Public x` / `foo bar baz` / `Units x` / `BeginProg` /
    # `EndProg` -> `line 3: Need '=' in units definition: x\n.` (line 2 never reported -- pass 1
    # only fires the `Units` error here, matching the existing "ran off the end of the line"
    # family's own reporting shape).
    text = "Public x\nfoo bar baz\nUnits x\n" "BeginProg\nEndProg\n"
    assert messages(text) == ["Need '=' in units definition: x\n."]


def test_units_fully_empty_target_has_no_embedded_newline():
    text = "Public X As Float\nUnits\nX = Volts\nBeginProg\nEndProg\n"
    assert messages(text) == ["Need '=' in units definition: ."]


def test_units_parser_reason_distinguishes_target_present():
    prog, diags = parser.parse_text("Units x\n", "t.cr6")
    (s,) = prog.statements
    assert isinstance(s, S.Unparsed) and s.reason == "Units target with no '='"
    assert s.text == "x"


# ------------------------------------------------------------------ Alias


def test_alias_target_present_no_eq_embeds_newline_before_period():
    # Oracle probe (`t2.cr6` under Wine): `Public rTime(9)` / `Alias rTime(1)` / `= Year` /
    # `BeginProg` / `EndProg` -> `line 2: Need '=' in alias definition: rTime(1)\n.` (matches
    # `tests/corpus/chm/realtime_1.CR6`'s own first diagnostic, oracle-confirmed identical).
    text = "Public rTime(9)\nAlias rTime(1)\n= Year\nBeginProg\nEndProg\n"
    assert messages(text)[0] == "Need '=' in alias definition: rTime(1)\n."


def test_alias_fully_empty_target_has_no_embedded_newline():
    result = compile_text("Public X As Float\nAlias\nX = Y\nBeginProg\nEndProg\n")
    assert "Need '=' in alias definition: ." in [d.message for d in result.diagnostics.errors]


def test_alias_parser_reason_distinguishes_target_present():
    prog, diags = parser.parse_text("Alias rTime(1)\n", "t.cr6")
    (s,) = prog.statements
    assert isinstance(s, S.Unparsed) and s.reason == "Alias target with no '='"


def test_alias_target_indexed_array_element():
    # A dimensioned alias target (`gps_data(1)`, not just a bare name) parses the same way.
    text = "Public gps_data(15)\nAlias gps_data(1)\n= latitude_a\nBeginProg\nEndProg\n"
    assert messages(text)[0] == "Need '=' in alias definition: gps_data(1)\n."


# ------------------------------------------------------------------ corpus regression

def test_realtime_1_first_message_matches_oracle():
    # `tests/corpus/chm/realtime_1.CR6` line 4 is `Alias rTime(1)` with `= Year` on line 5 --
    # this was the corpus case that first surfaced the quirk (`docs/checker.md`).
    from pathlib import Path

    corpus = Path(__file__).parent / "corpus" / "chm" / "realtime_1.CR6"
    program, diags = parser.parse_file(corpus)
    result = CompileResult(corpus.name, PROFILE, diags)
    checker.check(program, PROFILE, diags, result)
    first = result.diagnostics.errors[1]  # [0] is the unrelated line-1 "Variable not named."
    assert first.line == 4
    assert first.message == "Need '=' in alias definition: rTime(1)\n."
