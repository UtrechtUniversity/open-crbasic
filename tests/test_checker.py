"""Unit tests for `opencrbasic.checker`. Rules and oracle evidence: `docs/checker.md`."""

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

PROFILE = profiles.get("cr6")


def compile_text(text: str):
    program, diags = parser.parse_text(text, "t.CR6")
    result = CompileResult("t.CR6", PROFILE, diags)
    checker.check(program, PROFILE, diags, result)
    return result


def messages(text: str) -> list[str]:
    return [d.message for d in compile_text(text).diagnostics.errors]


PROG = """Public X As Float
BeginProg
  Scan (1,Sec,3,1)
{body}  NextScan
EndProg
"""


def wrapped(body: str) -> str:
    return PROG.format(body=body)


def wrapped_bare(body: str) -> str:
    """`wrapped` without its own `Public X` line, for tests that declare `X` themselves (a second
    `Public X` is itself an error: `X already declared as a variable.`)."""
    return PROG.split("\n", 1)[1].format(body=body)


# ------------------------------------------------------------------ structure.py


def test_no_beginprog():
    result = compile_text("Public X As Float\n")
    assert result.diagnostics.errors[-1].message == "No BeginProg in Program."
    assert result.diagnostics.errors[-1].line == 1


def test_scan_without_nextscan():
    result = compile_text("Public X As Float\nBeginProg\n  Scan (1,Sec,3,1)\n    X = 1\nEndProg\n")
    assert "Scan without NextScan." in messages_of(result)


def test_nextscan_with_no_scan():
    result = compile_text("Public X As Float\nBeginProg\n  NextScan\nEndProg\n")
    assert "NextScan with no Scan." in messages_of(result)


def test_if_without_endif():
    result = compile_text(wrapped("    If X > 1 Then\n      X = 2\n"))
    assert "IF or SELECT without EndIf or EndSelect." in messages_of(result)


def test_select_without_endselect_is_the_same_message():
    result = compile_text(wrapped("    Select Case X\n      Case 1\n        X = 2\n"))
    assert "IF or SELECT without EndIf or EndSelect." in messages_of(result)


def test_well_formed_program_has_no_structural_errors():
    result = compile_text(wrapped("    X = 1\n"))
    assert result.diagnostics.errors == []


# ---- nested/unclosed `DataTable` (docs/checker.md "DataTable/EndTable pairing", oracle-confirmed
# identical on CR6/CR1000X/CR300) -----------------------------------------------------------------


def test_nested_datatable_reports_datatable_without_endtable():
    # A second `DataTable` opened while the first is still open is a pass-1 error at the *second*
    # `DataTable`'s own line (4), regardless of how many `EndTable`s eventually follow -- both are
    # properly referenced here, yet the nesting itself is still rejected.
    text = (
        "Public X As Float\n"
        "DataTable (A,True,-1)\n"
        "  Sample (1,X,FP2)\n"
        "DataTable (B,True,-1)\n"
        "  Sample (1,X,FP2)\n"
        "EndTable\n"
        "EndTable\n"
    ) + wrapped_bare("    CallTable A\n    CallTable B\n")
    result = compile_text(text)
    assert messages_of(result) == ["DataTable without EndTable."]
    assert result.diagnostics.errors[0].line == 4


def test_nested_datatable_extra_endtable_reports_only_one_error():
    # An extra, third `EndTable` beyond the nested pair adds no second diagnostic -- the nesting
    # error alone is reported, same as the balanced-closure shape above.
    text = (
        "Public X As Float\n"
        "DataTable (A,True,-1)\n"
        "  Sample (1,X,FP2)\n"
        "DataTable (B,True,-1)\n"
        "  Sample (1,X,FP2)\n"
        "EndTable\n"
        "EndTable\n"
        "EndTable\n"
    ) + wrapped_bare("    CallTable A\n    CallTable B\n")
    result = compile_text(text)
    assert messages_of(result) == ["DataTable without EndTable."]


def test_orphan_endtable_with_no_datatable_at_all():
    text = "Public X As Float\nEndTable\n" + wrapped_bare("")
    result = compile_text(text)
    assert messages_of(result) == ["EndTable without DataTable."]
    assert result.diagnostics.errors[0].line == 2


def test_extra_endtable_after_a_properly_closed_table():
    text = (
        "DataTable (T,True,-1)\n  Sample (1,X,FP2)\nEndTable\nEndTable\n"
        + wrapped("    CallTable T\n")
    )
    result = compile_text(text)
    assert messages_of(result) == ["EndTable without DataTable."]


def test_explicit_call_to_undefined_sub():
    result = compile_text(wrapped("    Call NoSuchSub\n"))
    assert "Sub NoSuchSub not defined." in messages_of(result)


def test_implicit_call_to_declared_sub_is_fine():
    text = ("Public X As Float\nSub MySub(a As Float)\n  X = a\nEndSub\n" +
            wrapped_bare("    MySub(5)\n"))
    result = compile_text(text)
    assert result.diagnostics.errors == []


def test_implicit_call_to_builtin_function_is_fine():
    # `Timer`, `SerialOpen`, ... are `data/functions.json` entries (not the 410-entry instruction
    # table) called for a side effect with the return value discarded -- real code in this
    # repository's own `crbasic/Main.CR6` does this throughout.
    result = compile_text(wrapped("    Timer (1,Sec,2)\n"))
    assert result.diagnostics.errors == []


def test_undefined_bare_call_is_invalid_expression():
    result = compile_text("NOTE\n" + wrapped(""))
    assert "Invalid, or out of place expression: NOTE." in messages_of(result)


def test_known_parser_gap_is_not_flagged():
    # `docs/frontend.md` "Known residual gaps": still oracle-accepted despite not parsing.
    result = compile_text(wrapped("    WS_ms=WS_ms+=1\n"))
    assert result.diagnostics.errors == []


def test_echo_preserves_leading_indentation_and_is_not_trimmed():
    # round 2: the real compiler's echo is the raw physical-line text, comment-stripped only
    # (`udpsocketopen_1.CR6` etc., `docs/checker.md` "Verbatim echo text") -- not the token-trimmed
    # text an earlier version of `parser.py`'s `_segment_text` produced.
    result = compile_text(wrapped("    ThisIsBogus stuff here\n"))
    assert "Invalid, or out of place expression:     ThisIsBogus stuff here." in messages_of(result)


# ---- "Need closing )" shapes (docs/checker.md "Need closing ) shapes", oracle-confirmed CR6) --

def test_unclosed_paren_in_assignment_rhs():
    result = compile_text(wrapped("  x = Sin(1\n"))
    assert "No Matching, or out of place ) in expression:   x = Sin(1." in messages_of(result)


def test_unclosed_bare_grouping_paren_in_assignment_rhs():
    result = compile_text(wrapped("  x = (1+2\n"))
    assert "No Matching, or out of place ) in expression:   x = (1+2." in messages_of(result)


def test_unclosed_paren_in_instruction_call_still_compiles():
    # Unlike a general expression, an instruction's own argument list left open to end-of-line is
    # tolerated (pre-existing `expect_close` behaviour, unchanged) -- oracle-confirmed `PortSet(1,1`
    # alone fails on `PortSet`'s own channel check, never on the missing `)`.
    result = compile_text(wrapped("  PortSet(1,1\n"))
    assert messages_of(result) == ["Invalid port 1, use C1..C4 or U1..U12."]


def test_unclosed_paren_in_bare_call_still_compiles():
    # A bare call (no `Call` keyword) is just as tolerant of its own outermost unclosed paren as
    # the explicit `Call`/instruction forms -- oracle-confirmed.
    result = compile_text("Sub Foo(a,b,c)\nEndSub\n" + wrapped("  Foo(1,2,3\n"))
    assert result.diagnostics.errors == []


def test_extra_close_paren_in_assignment():
    result = compile_text(wrapped("  x = 1+2))\n"))
    assert "Incomplete expression:   x = 1+2))." in messages_of(result)


def test_unclosed_declaration_dimension_reports_missing_close_paren():
    result = compile_text("Public x(5\n" + wrapped(""))
    assert messages_of(result) == ["x(5 is missing the ) character."]
    assert result.diagnostics.errors[0].line == 1


def test_unclosed_declaration_dimension_multi_dim():
    result = compile_text("Dim x(5,3\n" + wrapped(""))
    assert "x(5,3 is missing the ) character." in messages_of(result)


def test_unclosed_sub_param_list_still_compiles():
    # A `Sub`'s own parameter list, unlike a `Public`/`Dim` dimension list, is oracle-confirmed
    # still tolerant of running off the end of the line.
    result = compile_text("Sub Foo(a,b\nEndSub\n" + wrapped(""))
    assert result.diagnostics.errors == []


def test_public_with_no_name_reports_variable_not_named():
    # `docs/checker.md` "`Public`/`Dim` with no name at all": a CHM line-wrap shape (`Public` /
    # `Y, Z` split across two physical lines with no `_` continuation).
    result = compile_text("Public\nY, Z\n" + wrapped(""))
    assert "Variable not named." in messages_of(result)


def test_public_with_no_name_suppresses_only_the_next_line():
    result = compile_text("Public\nY, Z\n" + wrapped(""))
    msgs = messages_of(result)
    assert msgs == ["Variable not named."]  # the stranded "Y, Z" line is not separately reported


def test_units_without_eq():
    result = compile_text("Public X As Float\nUnits\nX = Volts\n" + wrapped(""))
    assert "Need '=' in units definition: ." in messages_of(result)


def test_const_without_eq():
    result = compile_text("Const\nPI = 3.14159\n" + wrapped(""))
    assert "Const with no assignment." in messages_of(result)


def test_alias_without_eq():
    result = compile_text("Public X As Float\nAlias\nX = Y\n" + wrapped(""))
    assert "Need '=' in alias definition: ." in messages_of(result)


# `docs/checker.md` "Duplicate Alias": two independent oracle-confirmed duplicate checks, both
# pass 1 (`_check_alias_dims`, `structure.py`), keyed on opposite sides of the same `Alias
# <source> = <name>` statement.


def test_duplicate_alias_name_two_sources_same_target():
    # The target name `Foo` is reused by a second, unrelated `Alias` (a different declared
    # source, `Y`) -- oracle-confirmed `Duplicate Alias Name: <name>.` on the *second* statement's
    # own line, all four loggers (`t.cr6` probes, `CR6Comp.exe`/`cr1000xcomp.exe`/`cr300comp.exe`/
    # `cr350comp.exe`).
    result = compile_text(
        "Public X As Float\nPublic Y As Float\nAlias X = Foo\nAlias Y = Foo\n" + wrapped_bare(""))
    assert messages_of(result) == ["Duplicate Alias Name: Foo."]
    assert result.diagnostics.errors[0].line == 4


def test_duplicate_alias_same_source_two_names():
    # The *source* `X` is aliased twice under two different names -- oracle-confirmed plain
    # `Duplicate Alias` (no name, no trailing period) on the second statement's own line, same on
    # all four loggers.
    result = compile_text("Public X As Float\nAlias X = Foo\nAlias X = Bar\n" + wrapped_bare(""))
    assert messages_of(result) == ["Duplicate Alias"]
    assert result.diagnostics.errors[0].line == 3


def test_alias_name_equal_to_declared_variable_is_a_different_message():
    # Aliasing to a name that is *itself* already a declared variable is oracle-confirmed to give
    # a third, distinct message (`%s already declared as a variable.`, not in this project's
    # scope -- `docs/messages.tsv`'s neighbouring `FUN_004b8e70` row) rather than either duplicate-
    # alias check above; this checker does not implement that third message (under-reports, no
    # false reject), so it must not misreport one of the two implemented ones here either.
    result = compile_text("Public X As Float\nPublic Y As Float\nAlias X = Y\n" + wrapped(""))
    assert "Duplicate Alias Name: Y." not in messages_of(result)
    assert "Duplicate Alias" not in messages_of(result)


# ------------------------------------------------------------------ malformed `As` clause
#
# `docs/checker.md` "Invalid Variable declaration", `docs/fuzz-findings.md` "5. Message diff":
# well evidenced by 6 independent fuzz mutants, all a corrupted spelling of the `As` keyword.


def test_malformed_as_clause_reports_invalid_variable_declaration():
    result = compile_text("Public X asX Long\n" + wrapped(""))
    assert messages_of(result) == ["Invalid Variable declaration asX."]


def test_malformed_as_clause_with_no_as_keyword_at_all():
    result = compile_text("Public X garbage\n" + wrapped(""))
    assert messages_of(result) == ["Invalid Variable declaration garbage."]


def test_malformed_as_clause_stops_the_whole_declaration():
    # oracle-confirmed: a valid first variable followed by a malformed second one reports only
    # the second's own message and parses no further variable in the same comma list.
    result = compile_text("Public pppSocket As Long,pppIP As_Mut String\n" + wrapped(""))
    assert messages_of(result) == ["Invalid Variable declaration As_Mut."]


def test_trailing_text_after_a_complete_as_clause_is_silently_ignored():
    # oracle-confirmed quirk: once a variable's own `As Type` has been fully parsed, anything
    # before the next comma (or end of line) is discarded, not rejected -- `Y` is still declared
    # normally afterwards.
    result = compile_text(
        "Public X As Long asX String, Y As Float\n" + wrapped_bare("    Y = 1\n")
    )
    assert result.diagnostics.errors == []


# ------------------------------------------------------------------ array dimension limits
#
# `docs/checker.md` "Array dimension limits", `docs/fuzz-findings.md` "Hang": oracle-confirmed
# against a fuzz-found reproducer where an oversized `Public` array dimension made
# `tables.py`'s `-l` renderer enumerate ~10^12 indices and never return.


def test_oversized_array_dimension_is_rejected():
    # tests/corpus/fuzz/cr6/hang/dd9cc650482d.CR6, minimized: >2147483647 (INT32_MAX).
    result = compile_text("Public X(999999999999) As Long\n" + wrapped_bare(""))
    assert messages_of(result) == [
        "Illegally dimension: Public X(999999999999) As Long\n"
    ]


def test_zero_array_dimension_is_rejected():
    result = compile_text("Public X(0) As Long\n" + wrapped_bare(""))
    assert messages_of(result) == ["Illegally dimension: Public X(0) As Long\n"]


def test_negative_array_dimension_is_rejected():
    result = compile_text("Public X(-5) As Long\n" + wrapped_bare(""))
    assert messages_of(result) == ["Illegally dimension: Public X(-5) As Long\n"]


def test_max_int32_array_dimension_is_accepted():
    # The boundary itself (INT32_MAX) is not this checker's concern (the real compiler crashes on
    # it, `docs/checker.md`); a comfortably-legal size stays accepted.
    result = compile_text("Public X(2000000) As Long\n" + wrapped_bare(""))
    assert result.diagnostics.errors == []


def test_illegally_dimension_echoes_the_whole_declaration_verbatim():
    # oracle-confirmed: the echoed text is the *whole* declaration statement, not just the
    # offending variable, and even a fully-valid sibling variable on the same line doesn't change
    # that -- `docs/checker.md`.
    result = compile_text(
        "Public A As Long, X(0) As Long, B As Float\n" + wrapped_bare("")
    )
    assert messages_of(result) == [
        "Illegally dimension: Public A As Long, X(0) As Long, B As Float\n"
    ]


def test_illegally_dimension_fires_once_per_bad_dimension():
    # oracle-confirmed: two illegal dimensions on the same variable report the message twice.
    result = compile_text("Public X(0,-1) As Long\n" + wrapped_bare(""))
    assert messages_of(result) == [
        "Illegally dimension: Public X(0,-1) As Long\n",
        "Illegally dimension: Public X(0,-1) As Long\n",
    ]


def test_array_dimension_count_over_three_is_rejected():
    # oracle-confirmed: more than 3 dimensions, named with the variable plus its *first*
    # dimension's own raw source text -- independent of which later dimension would itself be
    # out of range (`Public X(1,1,1,2147483648)` still names the first dimension, "1").
    result = compile_text("Dim Arr(2,2,2,2) As Long\n" + wrapped(""))
    assert messages_of(result) == ["Variable Arr(2 dimension exceeds max of 3."]


def test_array_dimension_count_echoes_first_dimension_raw_text():
    result = compile_text("Public X(1 + 1,2,2,2) As Long\n" + wrapped_bare(""))
    assert messages_of(result) == ["Variable X(1 + 1 dimension exceeds max of 3."]


def test_array_dimension_count_checked_before_value():
    # oracle-confirmed: a 4th dimension's own value being illegal doesn't change the message --
    # the dimension-count check runs first and wins.
    result = compile_text("Public X(1,1,1,2147483648) As Long\n" + wrapped_bare(""))
    assert messages_of(result) == ["Variable X(1 dimension exceeds max of 3."]


def test_array_dimension_count_reports_once_per_over_dim_variable():
    result = compile_text(
        "Public X(1,1,1,1) As Long, Y(2,2,2,2) As Long\n" + wrapped_bare("")
    )
    assert messages_of(result) == [
        "Variable X(1 dimension exceeds max of 3.",
        "Variable Y(2 dimension exceeds max of 3.",
    ]


def test_array_dimension_named_by_a_const_is_accepted():
    result = compile_text("Const N = 5\nPublic X(N) As Long\n" + wrapped_bare(""))
    assert result.diagnostics.errors == []


def test_non_constant_array_dimension_is_not_flagged():
    # No oracle evidence for this shape; AGENTS.md's priority (never reject an accepted program)
    # rules out guessing at it, so a dimension that doesn't fold to a constant is left alone.
    result = compile_text(wrapped("") + "Sub S(N As Long)\n  Dim X(N) As Long\nEndSub\n")
    assert result.diagnostics.errors == []


# ------------------------------------------------------------------ instr_rules.py


def test_missing_required_parameter():
    # `docs/checker.md` "Required parameter missing": `Battery` (`params=[8]`) with no argument at
    # all -- oracle-confirmed (`docs/type-codes.md` section 3).
    result = compile_text(wrapped("    Battery\n"))
    assert "Invalid or missing parameter." in messages_of(result)


def test_bare_do_loop_is_not_flagged_as_missing_parameter():
    # code 4 (`Do`/`Loop`'s own optional `While`/`Until` marker) is exempt: `_TOLERANT_CODES`.
    result = compile_text(wrapped("    Do\n      X = 1\n    Loop\n"))
    assert result.diagnostics.errors == []


def test_bare_endsub_is_not_flagged_as_missing_parameter():
    # code 14 (`EndSub`'s own `none`-kind marker) is not a real argument slot: `_real_params`.
    text = "Public X As Float\nSub MySub()\nEndSub\n" + wrapped_bare("")
    result = compile_text(text)
    assert result.diagnostics.errors == []


# ---------------------------------------- structure.py: missing-parameter followup (docs/checker.md)


def test_missing_parameter_followup_reports_leftover_continuation_line():
    # `average_1.CR6` (`docs/checker.md` "Missing-parameter followup"): `Average` alone on one
    # line (required-parameter shortfall), its own wrapped argument list on the next -- the real
    # compiler reports both, oracle-confirmed.
    text = ("Public TCTemp As Float\nBeginProg\n  Scan (1,Sec,3,1)\n"
            "    Average\n(1,TCTemp,FP2,False)\n  NextScan\nEndProg\n")
    assert printed_messages_of(compile_text(text)) == [
        "Invalid or missing parameter.",
        "Invalid, or out of place expression: (1,TCTemp,FP2,False).",
    ]


def test_missing_parameter_followup_suppressed_when_first_name_undeclared():
    # `getdatarecord_3.CR6` vs `getdatarecord_1.CR6` (`docs/checker.md`): the real compiler stays
    # silent about the leftover line when an unknown name comes before any syntax error. Oracle,
    # 2026-09-27: `(Undeclared,1,FP2,False)` silent, but `(1,Undeclared,FP2,False)` is reported
    # (the comma after `1` is the syntax error; `docs/checker.md` "The scan").
    text = ("Public TCTemp As Float\nBeginProg\n  Scan (1,Sec,3,1)\n"
            "    Average\n(Undeclared,1,FP2,False)\n  NextScan\nEndProg\n")
    assert messages_of(compile_text(text)) == ["Invalid or missing parameter."]
    text = text.replace("(Undeclared,1,", "(1,Undeclared,")
    assert printed_messages_of(compile_text(text)) == [
        "Invalid or missing parameter.",
        "Invalid, or out of place expression: (1,Undeclared,FP2,False).",
    ]


def test_missing_parameter_followup_checks_only_the_first_name():
    # `cdmresistance_1.CR6` (`docs/checker.md`): a *later* undeclared name in the same leftover
    # does not suppress the followup -- only the first one gates it.
    text = ("Public TCTemp As Float\nBeginProg\n  Scan (1,Sec,3,1)\n"
            "    Average\n(1,TCTemp,FP2,Undeclared)\n  NextScan\nEndProg\n")
    assert printed_messages_of(compile_text(text)) == [
        "Invalid or missing parameter.",
        "Invalid, or out of place expression: (1,TCTemp,FP2,Undeclared).",
    ]


def test_channel_check_suppressed_when_file_has_an_earlier_pass1_error():
    # `docs/checker.md` "Two-pass compilation": pass 2 (the channel-range check) never runs once
    # pass 1 recorded any error anywhere in the file.
    text = wrapped(
        "    ExciteV (Vx1,2500,0)\n"
        "    TCDiff (X,1,mv200C,1,TypeT,X,True,0,15000,1.0,0)\n"
    )
    msgs = messages_of(compile_text(text))
    assert "Invalid symbol in expression: Vx1." in msgs
    assert "Invalid channel 1, must use U1..U12." not in msgs


def test_channel_check_runs_when_file_is_otherwise_clean():
    result = compile_text(wrapped("    TCDiff (X,1,mv200C,1,TypeT,X,True,0,15000,1.0,0)\n"))
    assert "Invalid channel 1, must use U1..U12." in messages_of(result)


# ------------------------------------------------------------------ expressions.py


def test_undefined_symbol_in_channel_argument():
    # `Vx1`/`P1`/`C5`.. are CR1000X-only terminals, absent from `data/cr6/symbols.json`.
    result = compile_text(wrapped("    TCDiff (X,1,mv200C,Vx1,TypeT,X,True,0,15000,1.0,0)\n"))
    assert "Invalid symbol in expression: Vx1." in messages_of(result)


def test_datatable_name_position_is_not_checked():
    # code 6 (identifier): the table's own name is captured verbatim, not symbol-resolved.
    text = "DataTable (SomeTable,True,-1)\n  Sample (1,X,FP2)\nEndTable\n" + wrapped(
        "    CallTable (SomeTable)\n"
    )
    result = compile_text(text)
    assert result.diagnostics.errors == []


def test_structuretype_name_used_as_datatype_is_not_flagged():
    # regression: `Sample`'s `DataType` argument may be a user `StructureType` name.
    text = (
        "StructureType VWSensor\n  Frequency As Float\nEndStructureType\nPublic S As VWSensor\n"
        "DataTable (T,True,-1)\n  Sample (1,S,VWSensor)\nEndTable\n" + wrapped("")
    )
    result = compile_text(text)
    assert result.diagnostics.errors == []


# ---- `Data`/`DataLong` parameters (docs/checker.md "Data parameters", oracle-confirmed CR6) -----


def test_data_param_undefined_symbol_reports_both_lines():
    result = compile_text(wrapped("    Data SomeVar\n"))
    msgs = messages_of(result)
    assert "Invalid symbol in expression: SomeVar\n." in msgs
    assert "Illegal Data parameter SomeVar\n." in msgs


def test_data_param_defined_variable_is_illegal_but_not_undefined():
    # `X` is declared (`PROG`'s own `Public X As Float`): illegal as a `Data` parameter (it is not
    # a constant), but with no undefined-symbol line -- the name itself resolves fine.
    result = compile_text(wrapped("    Data X\n"))
    msgs = messages_of(result)
    assert "Illegal Data parameter X\n." in msgs
    assert not any(m.startswith("Invalid symbol in expression") for m in msgs)


def test_data_param_literal_constants_are_legal():
    result = compile_text(wrapped("    Data 1,2,3\n"))
    assert result.diagnostics.errors == []


def test_data_param_not_last_on_line_has_no_embedded_newline():
    result = compile_text(wrapped("    Data 1,SomeVar,3\n"))
    msgs = messages_of(result)
    assert "Invalid symbol in expression: SomeVar." in msgs
    assert "Illegal Data parameter SomeVar." in msgs


def test_datalong_param_undefined_symbol_same_as_data():
    result = compile_text(wrapped("    DataLong SomeVar\n"))
    msgs = messages_of(result)
    assert "Invalid symbol in expression: SomeVar\n." in msgs
    assert "Illegal Data parameter SomeVar\n." in msgs


def test_data_unparsed_multiword_line_reports_illegal_data_parameter():
    # The common CHM-corpus shape (`brfull6w_1.CR6` etc.): a `'`-less comment-continuation line
    # the parser can only partially consume as `Data`'s own bare argument list, so the whole
    # statement becomes `S.Unparsed` rather than a clean `Instruction`.
    result = compile_text(wrapped("    Data Tables and Store Data\n"))
    msgs = messages_of(result)
    assert "Invalid symbol in expression: Tables and Store Data\n." in msgs
    assert "Illegal Data parameter Tables and Store Data\n." in msgs


# ------------------------------------------------------------------ instructions.py


def test_invalid_channel_argument():
    result = compile_text(wrapped("    TCDiff (X,1,mv200C,1,TypeT,X,True,0,15000,1.0,0)\n"))
    assert "Invalid channel 1, must use U1..U12." in messages_of(result)


def test_valid_u_channel_argument():
    result = compile_text(wrapped("    TCDiff (X,1,mv200C,U1,TypeT,X,True,0,15000,1.0,0)\n"))
    assert result.diagnostics.errors == []


# ------------------------------------------------------------------ warnings.py


def test_table_declared_but_never_called():
    text = "DataTable (T,True,-1)\n  Sample (1,X,FP2)\nEndTable\n" + wrapped("")
    result = compile_text(text)
    assert any(d.message == "Table T, is declared but never called." for d in result.diagnostics.warnings)


def test_table_called_is_not_warned():
    text = "DataTable (T,True,-1)\n  Sample (1,X,FP2)\nEndTable\n" + wrapped("    CallTable (T)\n")
    result = compile_text(text)
    assert result.diagnostics.warnings == []


def messages_of(result) -> list[str]:
    return [d.message for d in result.diagnostics.errors]


def printed_messages_of(result) -> list[str]:
    """Error messages in printed order (by line), as `CompileResult.result_lines` sorts them."""
    return [d.message for d in sorted(result.diagnostics.errors, key=lambda d: d.line)]


# ------------------------------------------------------------------ file-origin prefix (diagnostics.py)
#
# `docs/checker.md` "Errors from an included file": an error whose statement came from an
# `Include`d/`IncludeSection`ed file is printed as `<file> line N: message`, except for two
# oracle-confirmed message classes that never get it. These use `compiler.compile_file` (not
# `compile_text`) so the main program has a real name to compare each diagnostic's own `file`
# against.


def _compile_with_include(tmp_path, lib_text: str, main_text: str):
    from opencrbasic.compiler import compile_file

    (tmp_path / "Lib.crb").write_text(lib_text)
    main = tmp_path / "main.CR6"
    main.write_text(main_text)
    return compile_file(main, PROFILE)


def test_error_from_an_included_file_gets_the_file_prefix(tmp_path):
    result = _compile_with_include(
        tmp_path,
        '#If Section = "Defs" Then\nPublic LibArr(0) As Long\n#EndIf\n',
        'IncludeSection("Defs", "CPU:Lib.crb")\n' + wrapped(""),
    )
    assert messages_of(result) == ["Illegally dimension: Public LibArr(0) As Long\n"]
    assert result.result_lines() == [
        "main.CR6 -- Compile Failed!",
        "",
        "<Lib.crb> line 2: Illegally dimension: Public LibArr(0) As Long",
    ]


def test_error_in_the_main_file_itself_gets_no_prefix(tmp_path):
    result = _compile_with_include(
        tmp_path,
        '#If Section = "Defs" Then\nPublic LibVar As Long\n#EndIf\n',
        'IncludeSection("Defs", "CPU:Lib.crb")\nPublic X(0) As Long\n' + wrapped(""),
    )
    assert "<main.CR6>" not in result.result_lines()[2]
    assert result.result_lines()[2] == "line 2: Illegally dimension: Public X(0) As Long\n"


def test_sub_not_defined_never_gets_the_file_prefix(tmp_path):
    # Oracle-confirmed exception (`docs/checker.md`): unlike every other message class tested,
    # `Sub %s not defined.` never carries the `<file>` prefix even when the undefined `Call` is
    # itself inside the included file.
    result = _compile_with_include(
        tmp_path,
        '#If Section = "Body" Then\n  Call NoSuchLibSub\n#EndIf\n',
        'BeginProg\n  Scan (1,Sec,3,1)\n'
        '    IncludeSection("Body", "CPU:Lib.crb")\n  NextScan\nEndProg\n',
    )
    assert messages_of(result) == ["Sub NoSuchLibSub not defined."]
    assert "<Lib.crb>" not in result.result_lines()[2]


def test_bare_unresolved_statement_never_gets_the_file_prefix(tmp_path):
    result = _compile_with_include(
        tmp_path,
        '#If Section = "Body" Then\nNotBogusButUndefined\n#EndIf\n',
        'BeginProg\n  Scan (1,Sec,3,1)\n'
        '    IncludeSection("Body", "CPU:Lib.crb")\n  NextScan\nEndProg\n',
    )
    assert messages_of(result) == ["Invalid, or out of place expression: NotBogusButUndefined."]
    assert "<Lib.crb>" not in result.result_lines()[2]
