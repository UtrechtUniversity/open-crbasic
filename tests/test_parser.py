"""Unit tests for `opencrbasic.parser`."""

from opencrbasic import parser
from opencrbasic import syntax as S


def parse(text: str):
    prog, diags = parser.parse_text(text, "t.cr6")
    return prog, diags


def stmts(text: str):
    prog, diags = parse(text)
    assert not any(isinstance(s, S.Unparsed) for s in prog.statements), [
        (s.line, s.text, s.reason) for s in prog.statements if isinstance(s, S.Unparsed)
    ]
    return prog.statements


def one(text: str):
    ss = stmts(text)
    assert len(ss) == 1, ss
    return ss[0]


# ------------------------------------------------------------------ declarations


def test_public_simple():
    d = one("Public X\n")
    assert isinstance(d, S.Declaration)
    assert d.kind == "PUBLIC"
    assert d.vars[0].name == "X"


def test_public_multi_with_types_and_array():
    d = one("Public a, b(10), c(2,3) As Long, s As String * 32, x = 5\n")
    assert [v.name for v in d.vars] == ["a", "b", "c", "s", "x"]
    assert len(d.vars[1].dims) == 1
    assert len(d.vars[2].dims) == 2 and d.vars[2].type == "LONG"
    assert d.vars[3].type == "STRING"
    assert isinstance(d.vars[3].string_size, S.Number) and d.vars[3].string_size.value == 32
    assert isinstance(d.vars[4].init[0], S.Number) and d.vars[4].init[0].value == 5


def test_public_array_literal_init():
    d = one("Public arr(3) = {1,2,3}\n")
    v = d.vars[0]
    assert [e.value for e in v.init] == [1, 2, 3]


def test_dim_public_is_public():
    d = one("Dim Public X\n")
    assert d.kind == "PUBLIC"


def test_const_typed_and_array():
    c = one('Const A As Long = 9999\n')
    assert isinstance(c, S.ConstDecl) and c.type == "LONG"
    prog, _ = parse("Const A = {1,2,3}\n")
    c2 = prog.statements[0]
    assert isinstance(c2.value, S.ArrayLiteral)
    assert [e.value for e in c2.value.items] == [1, 2, 3]


def test_alias_multi_target():
    i = one("Alias Array = FrontRoom, BedRoom, GreatRoom(4), Laundry\n")
    assert isinstance(i, S.Instruction) and i.keyword == "ALIAS"
    assert isinstance(i.args[0], S.Name) and i.args[0].name == "Array"
    assert [a.name if isinstance(a, S.Name) else a.name for a in i.args[1:]] == [
        "FrontRoom", "BedRoom", "GreatRoom", "Laundry",
    ]


def test_units_label_is_raw_text():
    i = one("Units Acclima_BulkEC()=uS/cm\n")
    assert i.keyword == "UNITS"
    assert isinstance(i.args[0], S.Index) and i.args[0].name == "Acclima_BulkEC"
    assert isinstance(i.args[1], S.String) and i.args[1].value == "uS/cm"


def test_units_percent_label_not_lexed():
    i = one("Units Acclima_VWC()=%\n")
    assert i.args[1].value == "%"


def test_units_combined_via_colon():
    ss = stmts("Public TC(120) : Units TC = C\n")
    assert len(ss) == 2
    assert isinstance(ss[0], S.Declaration)
    assert isinstance(ss[1], S.Instruction) and ss[1].keyword == "UNITS"
    assert ss[1].args[1].value == "C"


# ------------------------------------------------------------------ expressions


def test_assignment_vs_relational_ambiguity():
    # `=` is both assignment and equality; the parser must not swallow the assignment's `=` as a
    # relational operator when parsing the lvalue (found by corpus testing; see docs/frontend.md).
    a = one("X = Y\n")
    assert isinstance(a, S.Assignment)
    assert isinstance(a.target, S.Name) and a.target.name == "X"
    assert isinstance(a.value, S.Name) and a.value.name == "Y"


def test_compound_assignment_desugars_to_binary():
    a = one("X += 2\n")
    assert isinstance(a, S.Assignment)
    assert isinstance(a.value, S.Binary) and a.value.op == "+"
    assert isinstance(a.value.left, S.Name) and a.value.left.name == "X"


def test_array_element_and_member_access():
    a = one("X = Status.SkippedScan\n")
    assert isinstance(a.value, S.Member)
    assert a.value.member == "SkippedScan"
    assert a.value.args is None

    a2 = one("X = Status.Battery(1)\n")
    assert isinstance(a2.value, S.Member) and a2.value.member == "Battery"
    assert [e.value for e in a2.value.args] == [1]

    a3 = one("X = Acclima_VWC(2)\n")
    assert isinstance(a3.value, S.Index) and a3.value.name == "Acclima_VWC"


def test_empty_array_all_elements():
    # `Foo()` with nothing inside the parens means "the whole array" (Sample(1,Acclima_VWC(),FP2)
    # in the real corpus); check the empty-args Index shape directly.
    a = one("X = Acclima_VWC()\n")
    assert isinstance(a.value, S.Index) and a.value.name == "Acclima_VWC" and a.value.args == []


def test_pointer_dereference_and_address_of():
    a = one("temp = !val1\n")
    assert isinstance(a.value, S.Unary) and a.value.op == "!"
    a2 = one("!val1 = !val2\n")
    assert isinstance(a2.target, S.Unary) and a2.target.op == "!"
    a3 = one("ptr3 = @s1\n")
    assert isinstance(a3.value, S.Unary) and a3.value.op == "@"


def test_pointer_type_suffix():
    d = one("Dim pointer_float As Float!\n")
    assert d.vars[0].type == "FLOAT!"


def test_array_dim_after_type():
    d = one("Public floatArr As Float(5) = {100, 2, 55, 371, 244}\n")
    v = d.vars[0]
    assert v.type == "FLOAT" and len(v.dims) == 1
    assert [e.value for e in v.init] == [100, 2, 55, 371, 244]


def test_structure_member_declaration():
    ss = stmts("StructureType VWSensor\n  Frequency As Float\n  ReadOnly Frequency\nEndStructureType\n")
    assert isinstance(ss[0], S.Instruction) and ss[0].keyword == "STRUCTURETYPE"
    member = ss[1]
    assert isinstance(member, S.Declaration) and member.kind == "STRUCTUREMEMBER"
    assert member.vars[0].name == "Frequency" and member.vars[0].type == "FLOAT"
    assert isinstance(ss[2], S.Instruction) and ss[2].keyword == "READONLY"
    assert isinstance(ss[3], S.Instruction) and ss[3].keyword == "ENDSTRUCTURETYPE"


# ------------------------------------------------------------------ If / Select / loops


def test_if_block_form():
    ss = stmts("If X > 1 Then\n  Y = 1\nEndIf\n")
    node = ss[0]
    assert isinstance(node, S.If) and node.inline is None
    assert isinstance(ss[1], S.Assignment)
    assert isinstance(ss[-1], S.Instruction) and ss[-1].keyword == "ENDIF"


def test_if_then_optional_for_block_form():
    # Confirmed valid CRBasic with the oracle (checkport_1.CR6: `If Flag` with no `Then`).
    ss = stmts("If Flag\n  X = 1\nElse\n  X = 2\nEndIf\n")
    assert isinstance(ss[0], S.If) and ss[0].inline is None


def test_if_inline_no_else():
    node = one("If NOT DebugTrace Then ExitFunction\n")
    assert isinstance(node, S.If)
    assert len(node.inline) == 1
    assert isinstance(node.inline[0], S.Instruction) and node.inline[0].keyword == "EXITFUNCTION"
    assert node.inline_else is None


def test_if_inline_with_else():
    node = one("If X > 10 Then Y = 1 Else Y = 2\n")
    assert isinstance(node.inline[0], S.Assignment)
    assert isinstance(node.inline_else[0], S.Assignment)


def test_elseif():
    ss = stmts("If X < 10 Then\n  Y = 1\nElseIf X < 100 Then\n  Y = 2\nElse\n  Y = 3\nEndIf\n")
    kinds = [type(s).__name__ for s in ss]
    assert kinds == ["If", "Assignment", "ElseIf", "Assignment", "Instruction", "Assignment", "Instruction"]
    assert ss[4].keyword == "ELSE"
    assert ss[6].keyword == "ENDIF"


def test_end_if_two_word_form():
    ss = stmts("If X Then\n  Y = 1\nEnd If\n")
    end = ss[-1]
    assert isinstance(end, S.Instruction) and end.keyword == "END"
    assert isinstance(end.args[0], S.Name) and end.args[0].name == "If"


def test_select_case():
    ss = stmts(
        "Select Case X\n"
        "Case 1, 2 To 5, Is >= 7\n"
        "  Y = 1\n"
        "Case Else\n"
        "  Y = 2\n"
        "EndSelect\n"
    )
    sel = ss[0]
    assert isinstance(sel, S.Instruction) and sel.keyword == "SELECT"
    assert isinstance(sel.args[0], S.Name) and sel.args[0].name == "X"
    case1 = ss[1]
    assert isinstance(case1, S.Case) and not case1.is_else
    assert case1.items[0] == ("value", case1.items[0][1])
    assert case1.items[1][0] == "range"
    assert case1.items[2][0] == "is" and case1.items[2][1] == ">="
    case_else = ss[3]
    assert isinstance(case_else, S.Case) and case_else.is_else
    assert isinstance(ss[-1], S.Instruction) and ss[-1].keyword == "ENDSELECT"


def test_end_select_two_word_is_pipeline_relevant():
    # docs/modes.md: the two-word `End Select` (unlike one-word `EndSelect`) does not close the
    # conditional block for mode purposes. Just check the shape here; mode.py owns the semantics.
    ss = stmts("Select Case X\nCase 1\n  Y=1\nEnd Select\n")
    end = ss[-1]
    assert isinstance(end, S.Instruction) and end.keyword == "END"
    assert end.args[0].name == "Select"


def test_for_next():
    ss = stmts("For I = 1 To 10 Step 2\n  X = I\nNext I\n")
    f = ss[0]
    assert isinstance(f, S.For)
    assert f.var.name == "I"
    assert f.start.value == 1 and f.end.value == 10
    assert f.step.value == 2
    nxt = ss[-1]
    assert isinstance(nxt, S.Instruction) and nxt.keyword == "NEXT"
    assert nxt.args[0].name == "I"


def test_next_scan_two_word():
    ss = stmts("Scan (1,Sec,3,0)\n  X = 1\nNext Scan\n")
    nxt = ss[-1]
    assert nxt.keyword == "NEXT" and nxt.args[0].name == "Scan"
    ss2 = stmts("Scan (1,Sec,3,0)\n  X = 1\nNextScan\n")
    assert ss2[-1].keyword == "NEXTSCAN" and ss2[-1].args == []


def test_do_loop_forms():
    ss = stmts("Do While Condition\n  X += 1\nLoop\n")
    do = ss[0]
    assert isinstance(do, S.DoLoop) and do.keyword == "DO" and do.cond_kind == "WHILE"
    loop = ss[-1]
    assert isinstance(loop, S.DoLoop) and loop.keyword == "LOOP" and loop.cond_kind is None

    ss2 = stmts("Do\n  X += 1\nLoop Until X = 10\n")
    loop2 = ss2[-1]
    assert loop2.cond_kind == "UNTIL"


def test_while_wend():
    ss = stmts("While X < 10\n  X += 1\nWend\n")
    assert isinstance(ss[0], S.Instruction) and ss[0].keyword == "WHILE"
    assert isinstance(ss[-1], S.Instruction) and ss[-1].keyword == "WEND"


def test_exit_two_word_and_one_word():
    ss = stmts("Sub Foo()\n  Exit Sub\nEndSub\n")
    exit_stmt = ss[1]
    assert isinstance(exit_stmt, S.Instruction) and exit_stmt.keyword == "EXIT"
    assert exit_stmt.args[0].name == "Sub"

    ss2 = stmts("Sub Foo()\n  ExitSub\nEndSub\n")
    assert ss2[1].keyword == "EXITSUB" and ss2[1].args == []

    ss3 = stmts("BeginProg\n  If X Then Exit\nEndProg\n")
    if_stmt = [s for s in ss3 if isinstance(s, S.If)][0]
    inline_exit = if_stmt.inline[0]
    assert isinstance(inline_exit, S.Instruction) and inline_exit.keyword == "EXIT" and inline_exit.args == []


# ------------------------------------------------------------------ Sub / Function


def test_sub_params():
    s = one("Sub ConvertCtoF (Tmp, TmpF)\n")
    assert isinstance(s, S.SubDef) and s.keyword == "SUB" and s.name == "ConvertCtoF"
    assert [p.name for p in s.params] == ["Tmp", "TmpF"]


def test_function_return_type_and_optional_param():
    s = one("Function xPlusy (x, y, Optional z=0) As Long\n")
    assert s.return_type == "LONG"
    assert [p.name for p in s.params] == ["x", "y", "z"]
    assert isinstance(s.params[2].default, S.Number) and s.params[2].default.value == 0


def test_function_no_parens_with_return_type():
    s = one("Function DayName As String\n")
    assert s.params == [] and s.return_type == "STRING"


def test_sub_string_param_with_array():
    s = one("Sub Splitdata (InString as string * 2000, strdata(25) as string, realdata(25))\n")
    assert s.params[0].type == "STRING"
    assert s.params[1].dims == 1


# ------------------------------------------------------------------ calls


def test_call_explicit_no_parens():
    c = one("Call InitPowerProfile\n")
    assert isinstance(c, S.Call) and c.explicit and c.name == "InitPowerProfile" and c.args == []


def test_call_bare_with_parens():
    c = one("LogDebug(\"\")\n")
    assert isinstance(c, S.Call) and not c.explicit and c.name == "LogDebug"


def test_bare_call_no_parens_no_args():
    c = one("Zork\n")
    assert isinstance(c, S.Call) and not c.explicit and c.name == "Zork" and c.args == []


def test_calltable_bare_arg():
    i = one("CallTable StationStatus\n")
    assert isinstance(i, S.Instruction) and i.keyword == "CALLTABLE"
    assert i.args[0].name == "StationStatus"


# ------------------------------------------------------------------ colon / two-statement lines


def test_colon_multi_statement():
    ss = stmts("X = 1 : Y = 2\n")
    assert len(ss) == 2
    assert all(isinstance(s, S.Assignment) for s in ss)


# ------------------------------------------------------------------ implicit close / EndProg cutoff


def test_unclosed_paren_implicitly_closes_at_eol():
    c = one("Call Foo(1,2,3\n")
    assert isinstance(c, S.Call) and [a.value for a in c.args] == [1, 2, 3]


def test_endprog_stops_parsing():
    prog, diags = parse("Public X\nBeginProg\nEndProg\nThis $$$ is not even lexable CRBasic\n")
    assert not diags.errors
    assert [type(s).__name__ for s in prog.statements] == ["Declaration", "Instruction", "Instruction"]


# ------------------------------------------------------------------ line-wrapped declarations
# (`docs/checker.md` "`Public`/`Dim` with no name at all" / "`Units`/`Const`/`Alias` truncated"):
# a CHM corpus example wrapped a declaration's own required content onto the next physical line
# with no `_` continuation. The checker (`structure.py`) turns these into specific messages;
# these tests only cover the parser's own shape.


def test_public_with_nothing_following_is_an_empty_declaration():
    d = one("Public\n")
    assert isinstance(d, S.Declaration) and d.kind == "PUBLIC" and d.vars == []


def test_dim_with_nothing_following_is_an_empty_declaration():
    d = one("Dim\n")
    assert isinstance(d, S.Declaration) and d.kind == "DIM" and d.vars == []


def test_const_with_nothing_following_is_unparsed_with_specific_reason():
    prog, diags = parse("Const\n")
    (s,) = prog.statements
    assert isinstance(s, S.Unparsed) and s.reason == "Const without '='"


def test_const_truncated_after_as_is_the_same_reason():
    # `sprintf_4.CR6`: `Const ToAddr As` / `String = "..."` -- truncated further into the grammar,
    # same message either way (oracle-confirmed, `docs/checker.md`).
    prog, diags = parse("Const ToAddr As\n")
    (s,) = prog.statements
    assert isinstance(s, S.Unparsed) and s.reason == "Const without '='"


def test_alias_with_nothing_following_is_unparsed_with_specific_reason():
    prog, diags = parse("Alias\n")
    (s,) = prog.statements
    assert isinstance(s, S.Unparsed) and s.reason == "Alias without '='"


def test_alias_target_present_but_no_eq_is_a_distinct_reason():
    # `realtime_1.CR6`: `Alias rTime(1)` / `= Year` on the next line, no `_` continuation -- a
    # target *is* present and parses fine, unlike the fully-empty case above. Distinguished so
    # `checker/structure.py` can echo the oracle-confirmed embedded-newline shape (see
    # `test_checker.py`'s echo test), not the plain empty one. See `docs/checker.md` "Echo quirk".
    prog, diags = parse("Alias rTime(1)\n")
    (s,) = prog.statements
    assert isinstance(s, S.Unparsed) and s.reason == "Alias target with no '='"


# ------------------------------------------------------------------ conditional compilation


def test_conditional_compilation_skipped_branch_defines_nothing():
    prog, diags = parse(
        "Const Flag = False\n"
        "#If Flag Then\n"
        "Const OnlyIfFlag = 1\n"
        "#Else\n"
        "Const OnlyIfNotFlag = 2\n"
        "#EndIf\n"
    )
    assert prog.consts.get("ONLYIFNOTFLAG") == 2
    assert "ONLYIFFLAG" not in prog.consts


def test_static_assert_pattern_never_parsed_when_skipped():
    # `ERROR__STATIC_ASSERT__...` alone is not valid CRBasic syntax (confirmed with the oracle:
    # `Invalid, or out of place expression`), but a skipped #If branch is never even tokenised,
    # so it never reaches the parser. See docs/frontend.md.
    prog, diags = parse(
        "Const Ok = True\n"
        "#If Ok Then\n"
        "#Else\n"
        "ERROR__STATIC_ASSERT__unreachable\n"
        "#EndIf\n"
        "Public X\n"
    )
    assert not diags.errors
    assert [type(s).__name__ for s in prog.statements] == ["ConstDecl", "Declaration"]


def test_nested_conditional_compilation():
    prog, diags = parse(
        "Const A = 1\n"
        "#If A = 1 Then\n"
        "  #If A > 0 Then\n"
        "Const Inner = 1\n"
        "  #Else\n"
        "Const InnerElse = 1\n"
        "  #EndIf\n"
        "#EndIf\n"
    )
    assert prog.consts.get("INNER") == 1
    assert "INNERELSE" not in prog.consts


# ------------------------------------------------------------------ include / includesection


def test_include_expands_and_reports_own_line_numbers(tmp_path):
    (tmp_path / "Inc1.CR6").write_text("'l1\n'l2\nIncVar = 1\n")
    text = 'Public IncVar\nBeginProg\n  Include "CPU:Inc1.CR6"\nEndProg\n'
    prog, diags = parser.parse_text(text, "t.cr6", directory=tmp_path)
    assign = [s for s in prog.statements if isinstance(s, S.Assignment)][0]
    assert assign.line == 3  # Inc1.CR6's own line 3, not the include site's line
    assert assign.file == "CPU:Inc1.CR6"  # the name as written; diagnostics render it per logger


def test_includesection_section_binding(tmp_path):
    (tmp_path / "Lib.crb").write_text(
        '#If Section = "ConstTable" Then\nConst FromCT = 1\n#EndIf\n'
        '#If Section = "Definitions" Then\nConst FromDefs = 2\n#EndIf\n'
    )
    text = (
        'IncludeSection("ConstTable", "CPU:Lib.crb")\n'
        'IncludeSection("Definitions", "CPU:Lib.crb")\n'
    )
    prog, diags = parser.parse_text(text, "t.cr6", directory=tmp_path)
    assert prog.consts.get("FROMCT") == 1
    assert prog.consts.get("FROMDEFS") == 2


def test_includesection_tolerates_a_trailing_argument(tmp_path):
    # Regression, `docs/fuzz-findings.md` "False reject" #3: the real compiler accepts (and
    # ignores) an `IncludeSection` call with a third argument
    # (`IncludeSection("Rail", "CPU:BenchLib.crb",(1+2))`, oracle-confirmed,
    # `tests/corpus/fuzz/cr6/false_reject/28b79b4c2e87.CR6`) -- `source.py`'s regex used to require
    # the call to end right after the two strings it uses, so a trailing argument (however
    # harmless) silently fell through to being parsed as an ordinary, meaningless statement
    # instead of expanding the include at all.
    (tmp_path / "Lib.crb").write_text(
        '#If Section = "Rail" Then\nConst FromRail = 1\n#EndIf\n'
    )
    text = 'IncludeSection("Rail", "CPU:Lib.crb",(1+2))\n'
    prog, diags = parser.parse_text(text, "t.cr6", directory=tmp_path)
    assert prog.consts.get("FROMRAIL") == 1
    assert not diags.errors


def test_missing_include_is_a_warning_not_a_crash(tmp_path):
    text = 'Public X\nBeginProg\n  Include "CPU:DoesNotExist.CR6"\nEndProg\n'
    prog, diags = parser.parse_text(text, "t.cr6", directory=tmp_path)
    assert not diags.errors
    assert any("Cannot open include file" in w.message for w in diags.warnings)


# ------------------------------------------------------------------ never raises


def test_unparsed_statement_never_raises():
    prog, diags = parse("1 + 2\n")  # not a valid statement start
    assert isinstance(prog.statements[0], S.Unparsed)


def test_lex_error_never_raises():
    prog, diags = parse("X = %\n")
    assert isinstance(prog.statements[0], S.Unparsed)
    assert "lex error" in prog.statements[0].reason


def test_structuretype_bare_members_and_statements():
    from opencrbasic import parser, syntax as S
    prog, _ = parser.parse_text(
        "StructureType gps_t\n  Lat\n  Lon As Double\n  A, B, C\n  S(2) As String * 110\n"
        "  Units Lat = deg\n  ReadOnly Lat, Lon\nEndStructureType\n")
    members = [v.name for st in prog.statements if isinstance(st, S.Declaration)
               and st.kind == "STRUCTUREMEMBER" for v in st.vars]
    assert members == ["Lat", "Lon", "A", "B", "C", "S"]
    assert not any(isinstance(st, S.Unparsed) for st in prog.statements)
