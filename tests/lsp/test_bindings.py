"""LSP-05a: the read-only binding index (`opencrbasic.bindings`). Scope policies and their evidence:
docs/lsp-api.md "Binding index (LSP-05a)"."""

from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from opencrbasic import bindings, parser, profiles
from opencrbasic import syntax as S
from opencrbasic.analysis import AnalysisRequest, analyze

from .assertions import required

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT / "tests" / "corpus" / "station").resolve()
CHM = sorted((ROOT / "tests" / "corpus" / "chm").glob("*.CR6"))


def index(src: str, logger: str = "cr6"):
    prog, _ = parser.parse_text(src, "t.cr6", profile=profiles.get(logger))
    return bindings.build(prog, logger)


def decls(ix, name, kind=None):
    return [d for d in ix.declarations_named(name) if kind is None or d.kind == kind]


def refs(ix, name, role=None):
    return [r for r in ix.references if r.name.upper() == name.upper() and (role is None or r.role == role)]


def targets(ref):
    return [(d.kind, d.scope, d.line) for d in ref.resolved]


def body(text: str) -> str:
    return text + "BeginProg\nEndProg\n"


# ------------------------------------------------------------- declarations


def test_globals_preserve_spelling_and_resolve_case_insensitively():
    ix = index(
        "Public Temp_C, Arr(3) As Long\nConst Limit = 5\nDim Work\n"
        "BeginProg\n  temp_c = arr(1) + LIMIT + work\nEndProg\n"
    )
    assert [(d.name, d.kind, d.scope, d.line) for d in ix.declarations if d.kind in ("public", "const", "dim")] == [
        ("Temp_C", "public", "global", 1),
        ("Arr", "public", "global", 1),
        ("Limit", "const", "global", 2),
        ("Work", "dim", "global", 3),
    ]
    use = {r.name: r for r in ix.references if r.line == 5}
    assert use["temp_c"].role == "write" and use["temp_c"].is_write
    assert use["temp_c"].status == "resolved" and use["temp_c"].resolved[0].name == "Temp_C"
    assert use["arr"].resolved[0].name == "Arr" and use["arr"].resolved[0].type == "LONG"
    assert use["LIMIT"].resolved[0].kind == "const" and use["work"].resolved[0].kind == "dim"
    assert ix.failures == ()


def test_local_dim_and_parameter_shadow_global():
    ix = index("Public X, P\nSub Foo(P)\n  Dim X\n  X = P\nEndSub\nBeginProg\n  X = 1\n  P = 2\nEndProg\n")
    inside = [r for r in ix.references if r.line == 4 and r.role in ("write", "read")]
    assert [targets(r) for r in inside] == [[("dim", "routine", 3)], [("param", "routine", 2)]]
    outside = [r for r in ix.references if r.line in (7, 8)]
    assert [targets(r) for r in outside] == [[("public", "global", 1)]] * 2
    local = decls(ix, "X", "dim")[0]
    assert isinstance(local.routine, S.SubDef) and local.routine.name == "Foo"


def test_same_name_parameter_in_two_routines_is_distinct():
    ix = index(body("Sub A(N)\n  N = 1\nEndSub\nSub B(N)\n  N = 2\nEndSub\n"))
    pa, pb = decls(ix, "N", "param")
    assert pa is not pb and pa.routine.name == "A" and pb.routine.name == "B"
    wa, wb = refs(ix, "N", "write")
    assert wa.resolved == (pa,) and wb.resolved == (pb,)
    assert wa.status == wb.status == "resolved"


def test_const_used_in_sub_resolves_to_global_const():
    ix = index("Sub Foo(Q)\n  Q = N\nEndSub\nConst N = 2\nBeginProg\nEndProg\n")
    (use,) = refs(ix, "N", "read")
    assert targets(use) == [("const", "global", 4)]
    assert use.forward  # the Const is declared after the Sub (docs: sub_param_const.json)


def test_parameter_named_like_later_const_is_local():
    ix = index("Public X\nSub Foo(N)\n  X = N\nEndSub\nConst N = 2\nBeginProg\n  X = N\nEndProg\n")
    inside, outside = refs(ix, "N", "read")
    assert targets(inside) == [("param", "routine", 2)]
    assert targets(outside) == [("const", "global", 5)]


def test_sub_forward_reference_and_call_roles():
    ix = index(
        "Public X\nSub Early\n  Later(1)\n  X = Fn(2)\nEndSub\nSub Later(A)\nEndSub\n"
        "Function Fn(B)\n  Fn = B\nEndFunction\nBeginProg\n  Call Early\nEndProg\n"
    )
    later = refs(ix, "Later", "call")[0]
    assert targets(later) == [("sub", "global", 6)] and not later.forward  # Sub order is free
    fn_read, fn_write = refs(ix, "Fn")[0], refs(ix, "Fn")[1]
    assert fn_read.role == "call" and targets(fn_read) == [("function", "global", 8)]
    assert fn_write.role == "write" and targets(fn_write) == [("function", "global", 8)]
    assert targets(refs(ix, "Early", "call")[0]) == [("sub", "global", 2)]


def test_sub_declared_after_endprog_is_not_read():
    ix = index("Public X\nBeginProg\n  Late\nEndProg\nSub Late\nEndSub\n")
    assert not decls(ix, "Late") and refs(ix, "Late")[0].status == "unresolved"


def test_alias_read_and_write():
    ix = index("Public Arr(3), Y\nAlias Arr(1) = Ta, Tb\nBeginProg\n  Ta = 1\n  Y = Tb + Ta\nEndProg\n")
    ta, tb = decls(ix, "Ta", "alias")[0], decls(ix, "Tb", "alias")[0]
    assert ta.alias_target is tb.alias_target
    assert ta.alias_target.role == "alias_target"
    assert targets(ta.alias_target) == [("public", "global", 1)]
    w = refs(ix, "Ta", "write")
    assert w and w[0].resolved == (ta,)
    assert [r.resolved for r in refs(ix, "Tb", "read")] == [(tb,)]


def test_units_target_references_variable_and_alias():
    ix = index("Public X, Arr(2)\nAlias Arr(1) = Ta\nUnits X = degC\nUnits Ta = V\nBeginProg\nEndProg\n")
    assert ix is not None
    ux, ut = refs(ix, "X", "units_target")[0], refs(ix, "Ta", "units_target")[0]
    assert targets(ux) == [("public", "global", 1)]
    assert targets(ut) == [("alias", "global", 2)]
    assert required(ix.token_of(ux)).col == len("Units ")


def test_datatable_name_and_member_reference():
    ix = index(
        "Public X\nDataTable(Tab,True,1)\n  Sample(1,X,FP2)\nEndTable\n"
        "BeginProg\n  Scan(1,Sec,0,0)\n    X = Tab.X\n    CallTable Tab\n  NextScan\nEndProg\n"
    )
    tab = decls(ix, "Tab", "datatable")[0]
    assert tab.line == 2 and tab.scope == "global" and tab.node.name == "Tab"
    base = refs(ix, "Tab", "member_base")[0]
    assert base.resolved == (tab,)
    member = refs(ix, "X", "member")[0]
    assert member.container == (tab,) and member.base is base
    (field,) = ix.fields_of(tab)
    assert member.resolved == (field,) and member.status == "resolved"
    assert (field.name, field.kind, field.scope, field.container) == ("X", "field", "table", tab)
    assert refs(ix, "Tab", "table_ref")[0].resolved == (tab,)
    assert targets(refs(ix, "Sec", "argument")[0]) == [("external", "predefined", 0)]


def test_quoted_table_name_is_declared_but_strings_are_not_references():
    ix = index(
        'Public X\nDataTable ("Hist",True,1)\n  Sample(1,X,FP2)\nEndTable\n'
        'BeginProg\n  Scan(1,Sec,0,0)\n    CallTable("Hist")\n  NextScan\nEndProg\n'
    )
    assert ix is not None
    (tab,) = decls(ix, "Hist")
    assert tab.quoted and tab.kind == "datatable"
    assert not refs(ix, "Hist")
    assert required(ix.token_of(tab)).kind == "STRING"


def test_system_tables_and_public_members():
    ix = index("Public Z\nBeginProg\n  Z = Status.Battery + Public.Z\nEndProg\n")
    assert refs(ix, "Status", "member_base")[0].resolved[0].kind == "system_table"
    assert targets(refs(ix, "Battery", "member")[0]) == [("field", "predefined", 0)]
    pz = refs(ix, "Z", "member")[0]
    assert targets(pz) == [("public", "global", 1)]
    # DataTableInfo only exists once a DataTable does
    assert (
        refs(index("Public Z\nBeginProg\n  Z = DataTableInfo.X\nEndProg\n"), "DataTableInfo")[0].status == "unresolved"
    )


FIELD_SRC = """Public X, TC(2), A(3), S As String * 8
Alias A(2) = Mid
DataTable(Tab,True,10)
  DataInterval(0,1,Min,10)
  Average(1,X,FP2,False)
  Sample(2,TC(),FP2)
  FieldNames("One:desc,Two")
  Maximum(1,X,FP2,False,True)
  Sample(1,S,String)
  Totalize(3,TC(),FP2,False)
EndTable
DataTable(Other,True,5)
  Sample(1,Tab.X_Avg,FP2)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    X = Tab.X_Avg + Tab.One + Tab.Two(1,1) + Tab.Nope + Tab.TC_Tot(1,2) + Tab.Record(1,1)
    X = Tab.x_max + Tab.X_TMx + Tab.S + Other.X_Avg
    CallTable Tab
  NextScan
EndProg
"""


def field_names(ix, table):
    (tab,) = decls(ix, table, "datatable")
    return [f.name for f in ix.fields_of(tab)]


def test_table_fields_are_named_as_the_tdf_derivation_names_them():
    ix = index(FIELD_SRC)
    assert ix.failures == ()
    assert field_names(ix, "Tab") == ["X_Avg", "One", "Two", "X_Max", "X_TMx", "S", "TC_Tot"]
    assert field_names(ix, "Other") == ["X_Avg"]
    assert decls(ix, "Tab", "datatable")[0].modelled


def test_table_field_references_resolve_by_table_and_case_insensitively():
    ix = index(FIELD_SRC)
    fields = {
        (required(f.container).name, f.name): f
        for d in ix.declarations
        if d.kind == "datatable"
        for f in ix.fields_of(d)
    }
    want = {
        "X_Avg": [("Tab", "X_Avg"), ("Tab", "X_Avg"), ("Other", "X_Avg")],
        "One": [("Tab", "One")],
        "Two": [("Tab", "Two")],
        "TC_Tot": [("Tab", "TC_Tot")],
        "x_max": [("Tab", "X_Max")],
        "X_TMx": [("Tab", "X_TMx")],
        "S": [("Tab", "S")],
    }
    for name, keys in want.items():
        got = [r for r in refs(ix, name, "member")]
        assert [r.resolved for r in got] == [(fields[k],) for k in keys], name
    assert all(r.status == "resolved" for r in ix.references if r.role == "member" and r.name != "Nope")


def test_table_field_unknown_member_is_unresolved_but_table_properties_resolve():
    ix = index(FIELD_SRC)
    nope = refs(ix, "Nope", "member")[0]
    assert nope.status == "unresolved" and nope.resolved == ()
    rec = refs(ix, "Record", "member")[0]
    assert [(d.kind, d.scope, d.site, d.container.name) for d in rec.resolved] == [
        ("field", "predefined", "property", "Tab")
    ]


def test_table_field_sites_fieldnames_string_token_is_quoted_and_instruction_has_none():
    ix = index(FIELD_SRC)
    (tab,) = decls(ix, "Tab", "datatable")
    by = {f.name: f for f in ix.fields_of(tab)}
    for name in ("One", "Two"):
        f = by[name]
        assert (f.site, f.quoted, f.line) == ("fieldnames", True, 7)
        tok = ix.token_of(f)
        assert tok is not None
        assert tok.kind == "STRING" and tok.value == "One:desc,Two"
        assert isinstance(f.node, S.Instruction) and f.node.keyword.upper() == "SAMPLE"
    f = by["X_Avg"]
    assert (f.site, f.quoted, f.line, ix.token_of(f)) == ("instruction", False, 5, None)
    assert isinstance(f.node, S.Instruction) and f.node.keyword.upper() == "AVERAGE" and f.stmt is f.node
    assert ix.references_to(by["X_Avg"]) and all(r.name == "X_Avg" for r in ix.references_to(f))
    assert "ONE" not in [d.name.upper() for d in ix.declarations_named("One")]  # not in the name table


def test_table_field_names_come_from_the_tdf_derivation_on_crbasic_and_chm():
    from opencrbasic import tables

    checked = 0
    for path in sorted(CRBASIC.glob("*.CR6")) + CHM[::5]:
        prog, _ = parser.parse_file(path, profiles.get("cr6"))
        ix = bindings.build(prog, "cr6")
        assert ix.failures == ()
        try:
            tdf_tables, _, _ = tables.build_tables(prog, profiles.get("cr6"), dict(prog.consts))
        except Exception:
            continue
        tdf = {t.name: [f.name for f in t.fields] for t in tdf_tables}
        for d in ix.declarations:
            if d.kind == "datatable" and d.modelled and not d.quoted and d.name in tdf:
                assert [f.name for f in ix.fields_of(d)] == tdf[d.name], (path.name, d.name)
                checked += 1
    assert checked > 20


def test_system_table_members_come_from_profile_data():
    ix = index(
        "Public Z\nDataTable(T,True,1)\n  Sample(1,Z,FP2)\nEndTable\nBeginProg\n"
        "  Z = Status.Battery + Status.SkippedScan + Settings.StationName + Status.Nope"
        " + Settings.Nope + DataTableInfo.SkippedRecord + Status.TimeStamp\nEndProg\n"
    )
    got = {r.name: r for r in ix.references if r.role == "member"}
    for name in ("Battery", "SkippedScan", "StationName", "SkippedRecord", "TimeStamp"):
        assert got[name].status == "resolved", name
    assert [(d.name, d.container.name) for d in got["StationName"].resolved] == [("StationName", "Settings")]
    assert got["TimeStamp"].resolved[0].site == "property"
    assert got["Nope"].status == "unmodelled" and got["Nope"].resolved == ()
    assert [r.status for r in ix.references if r.name == "Nope"] == ["unmodelled", "unmodelled"]


def test_status_fields_are_only_modelled_for_cr6():
    src = "Public Z\nBeginProg\n  Z = Status.Battery\nEndProg\n"
    assert refs(index(src, "cr6"), "Battery", "member")[0].status == "resolved"
    assert refs(index(src, "cr1000x"), "Battery", "member")[0].status == "unmodelled"


def test_structure_member_and_instance_type():
    ix = index(
        "StructureType Pt\n  Lat As Float\n  Lon As Float\nEndStructureType\n"
        "Public P As Pt\nBeginProg\n  P.Lat = 1\n  P.lon = P.Lat\n  P.Nope = 1\nEndProg\n"
    )
    members = {d.name: d for d in ix.declarations if d.kind == "structure_member"}
    assert set(members) == {"Lat", "Lon"} and members["Lat"].scope == "structure"
    assert members["Lat"].container is decls(ix, "Pt", "structuretype")[0]
    ty = refs(ix, "Pt", "type")[0]
    assert targets(ty) == [("structuretype", "global", 1)]
    lat = refs(ix, "Lat", "member")
    assert [r.resolved for r in lat] == [(members["Lat"],)] * 2
    assert refs(ix, "lon", "member")[0].resolved == (members["Lon"],)
    assert refs(ix, "Nope", "member")[0].status == "unresolved"
    assert lat[0].is_write and not lat[1].is_write
    assert refs(ix, "P", "member_base")[0].resolved[0].type == "PT"


def test_const_table_members():
    ix = index("ConstTable(CT)\n  Const A = 1\nEndConstTable\nPublic X\nBeginProg\n  X = CT.A\n  X = A\nEndProg\n")
    a = decls(ix, "A", "const")[0]
    assert a.container is decls(ix, "CT", "consttable")[0]
    assert refs(ix, "A", "member")[0].resolved == (a,)
    assert refs(ix, "A", "read")[0].resolved == (a,)


def test_predefined_instruction_and_symbol_targets():
    ix = index(
        "Public T\nBeginProg\n  Scan(1,Sec,0,0)\n    PanelTemp(T,15000)\n    T = Sin(T) + NAN\n  NextScan\nEndProg\n"
    )
    kinds = {
        r.name: r.resolved[0].external.kind for r in ix.references if r.resolved and r.resolved[0].kind == "external"
    }
    assert kinds["PanelTemp"] == "instruction" and kinds["Sec"] == "symbol"
    assert kinds["Sin"] == "function" and kinds["NAN"] == "symbol"
    assert refs(ix, "Sin")[0].role == "call"
    ext = refs(ix, "PanelTemp", "instruction")[0].resolved[0]
    assert ext.external.name == "PanelTemp" and ext.index == -1


# ------------------------------------------------------- unresolved / ambiguous


def test_unresolved_name():
    ix = index("Public X\nBeginProg\n  X = Zork + 1\n  Zork2 = 3\nEndProg\n")
    assert [(r.name, r.status) for r in ix.references if r.name.startswith("Zork")] == [
        ("Zork", "unresolved"),
        ("Zork2", "unresolved"),
    ]
    assert ix.counts()["references"]["unresolved"] == 2


def test_duplicate_global_is_ambiguous_not_guessed():
    ix = index("Public X\nDim X\nBeginProg\n  X = 1\nEndProg\n")
    (r,) = refs(ix, "X", "write")
    assert r.status == "ambiguous" and len(r.resolved) == 2
    assert r.ambiguity == "duplicate global declarations"


def test_table_and_variable_sharing_a_name_is_ambiguous():
    # the vendor sample avgrun_1.CR6 declares `Public TCTemp` and `DataTable (TCTemp,...)`
    ix = index(
        "Public TCTemp\nDataTable(TCTemp,True,1)\n  Sample(1,TCTemp,FP2)\nEndTable\n"
        "BeginProg\n  Scan(1,Sec,0,0)\n    CallTable TCTemp\n  NextScan\nEndProg\n"
    )
    call = refs(ix, "TCTemp", "argument") + refs(ix, "TCTemp", "table_ref")
    assert call and all(r.status == "ambiguous" for r in call)
    assert {d.kind for d in call[0].resolved} == {"public", "datatable"}
    assert "separate namespaces" in call[0].ambiguity


LOCAL_EXP = "Sub S\n  Dim Exp\n  Exp = 1\nEndSub\nBeginProg\nEndProg\n"


def test_local_dim_shadows_function_name_on_cr6_and_cr1000x():
    for logger in ("cr6", "cr1000x"):
        (r,) = refs(index(LOCAL_EXP, logger), "Exp", "write")
        assert r.status == "resolved" and r.resolved[0].kind == "dim", logger


def test_local_dim_does_not_shadow_function_on_cr300_and_cr350():
    for logger in ("cr300", "cr350"):
        ix = index(LOCAL_EXP, logger)
        (r,) = refs(ix, "Exp", "write")
        assert r.status == "ambiguous", logger
        assert [d.kind for d in r.resolved] == ["external", "dim"]
        assert logger in r.ambiguity


def test_public_declared_in_routine_is_not_guessed_global():
    ix = index("Sub S\n  Public Inner\n  Inner = 1\nEndSub\nPublic X\nBeginProg\n  X = Inner\nEndProg\n")
    inside, outside = refs(ix, "Inner")
    assert inside.status == "resolved" and inside.resolved[0].unverified_scope
    assert outside.status == "ambiguous" and "not established" in outside.ambiguity


# -------------------------------------------------------- strings, #If, includes


def test_string_literal_is_never_a_reference_or_token():
    src = 'Public X, S As String * 10\nBeginProg\n  S = "X Foo"\n  X = 1 : S = "X"\nEndProg\n'
    ix = index(src)
    assert ix is not None
    assert not refs(ix, "Foo")
    xs = refs(ix, "X")
    assert [r.line for r in xs] == [4]
    toks = bindings.name_tokens(xs[0].unit, "X")
    assert [t.kind for t in toks] == ["IDENT"] and required(ix.token_of(xs[0])).col == xs[0].unit.text.index("X")
    assert bindings.name_tokens(xs[0].unit, "X", string=True) == []
    str_stmt = next(s for s in parser.parse_text(src)[0].statements if getattr(s, "text", "").strip() == 'S = "X Foo"')
    assert bindings.name_tokens(str_stmt, "X") == []
    assert [t.kind for t in bindings.name_tokens(str_stmt, "X Foo", string=True)] == ["STRING"]


def test_inactive_if_branch_is_not_indexed():
    src = "#If 0\nPublic Hidden\n#Else\nPublic Shown\n#EndIf\nBeginProg\n  Hidden = 1\n  Shown = 1\nEndProg\n"
    ix = index(src)
    assert not decls(ix, "Hidden") and [d.line for d in decls(ix, "Shown")] == [4]
    assert refs(ix, "Hidden")[0].status == "unresolved" and refs(ix, "Shown")[0].status == "resolved"


def _write(tmp: Path, name: str, text: str) -> Path:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    return path


def test_cross_include_declaration_and_reference(tmp_path):
    _write(tmp_path, "Lib.crb", "Public LibVar\nSub LibSub\n  LibVar = 1\nEndSub\n")
    entry = _write(tmp_path, "m.CR6", 'Include("CPU:Lib.crb")\nPublic X\nBeginProg\n  LibSub\n  X = libvar\nEndProg\n')
    result = analyze(AnalysisRequest(entry, "cr6"))
    ix = bindings.build(result.program, "cr6")
    (lv,) = decls(ix, "LibVar")
    assert lv.file == "CPU:Lib.crb" and lv.line == 1
    use = refs(ix, "libvar", "read")[0]
    assert use.file == "m.CR6" and use.resolved == (lv,)
    assert targets(refs(ix, "LibSub", "call")[0]) == [("sub", "global", 2)]
    assert [r.file for r in refs(ix, "LibVar", "write")] == ["CPU:Lib.crb"]
    assert refs(ix, "LibVar", "write")[0].resolved == (lv,)


def test_same_file_included_twice_is_not_merged(tmp_path):
    _write(tmp_path, "Lib.crb", "Public LibVar\n")
    entry = _write(
        tmp_path, "m.CR6", 'Include("CPU:Lib.crb")\nInclude("CPU:Lib.crb")\nBeginProg\n  LibVar = 1\nEndProg\n'
    )
    ix = bindings.build(parser.parse_file(entry)[0], "cr6")
    first, second = decls(ix, "LibVar")
    assert first is not second and first.file == second.file and first.line == second.line == 1
    assert first.unit is not second.unit and first.index < second.index
    (use,) = refs(ix, "LibVar", "write")
    assert set(use.resolved) == {first, second}
    assert use.status == "ambiguous" and "included more than once" in use.ambiguity
    assert ix.references_to(first) == ix.references_to(second) == (use,)


# ------------------------------------------------------ statement forms, tokens


def test_compound_assignment_and_inline_if_tokens():
    ix = index("Public X, Y\nBeginProg\n  X += 1 : If X > 0 Then Y = X Else Y = 2\nEndProg\n")
    assert ix is not None
    xs = [r for r in refs(ix, "X") if r.line == 3]
    assert [r.role for r in xs] == ["write", "read", "read"]  # no synthetic copy of the target
    assert [required(ix.token_of(r)).col for r in xs] == [2, 4, 19]  # per unit text: `X += 1`, `If X > 0 ...`
    assert xs[1].unit is xs[2].unit is not xs[0].unit
    assert all(r.unit.text[ix.token_of(r).col] == "X" for r in xs)
    ys = [r for r in refs(ix, "Y") if r.line == 3]
    assert [r.is_write for r in ys] == [True, True]


def test_every_record_token_matches_its_name_on_crbasic_sources():
    mismatches = 0
    for path in sorted(CRBASIC.glob("*.CR6")):
        prog, _ = parser.parse_file(path, profiles.get("cr6"))
        ix = bindings.build(prog, "cr6")
        for rec in list(ix.declarations) + list(ix.references):
            if rec.unit is None or getattr(rec, "kind", "") == "field":  # token: FieldNames string
                continue
            tok = ix.token_of(rec)
            if tok is None or rec.name.upper().rstrip("!") != (
                str(tok.value).upper() if tok.kind == "STRING" else tok.upper
            ):
                mismatches += 1
    assert mismatches == 0


def test_segment_starts_follow_the_parser_split():
    text = 'Public X : X = 1 : Y = "a:b"'
    starts = bindings.segment_starts(text)
    assert [seg for _, seg in starts] == ["Public X ", " X = 1 ", ' Y = "a:b"']
    assert all(text[off : off + len(seg)] == seg for off, seg in starts)


# --------------------------------------------------------------- robustness


def test_unparsed_statements_are_counted_not_indexed():
    ix = index("Public X\nStatic Q\nBeginProg\n  X = 1\nEndProg\n")
    assert [u.text.strip() for u in ix.unparsed][:1] == ["Static Q"]  # the parser has no Static
    assert not decls(ix, "Q")


def test_build_with_no_program_and_garbage():
    assert bindings.build(None, "cr6").declarations == ()
    ix = bindings.build(S.Program(name="x", statements=[cast(S.Stmt, object()), S.Unparsed(1, "f", reason="r")]), "cr6")
    assert ix.failures == () and len(ix.unparsed) == 1
    with pytest.raises(ValueError):
        bindings.build(None, "no-such-logger")


def test_internal_failure_is_recorded_and_rolled_back():
    class Boom(S.Assignment):
        @property
        def value(self):
            raise RuntimeError("boom")

        @value.setter
        def value(self, v):
            pass

    good = S.Declaration(1, "f", kind="PUBLIC", vars=[S.VarDecl("A")], text="Public A")
    bad = Boom(2, "f", target=S.Name(2, "f", "A"), text="A = 1")
    after = S.Assignment(3, "f", target=S.Name(3, "f", "A"), value=S.Number(3, "f", 1, "1"), text="A = 1")
    ix = bindings.build(S.Program(name="f", statements=[good, bad, after]), "cr6")
    assert [(f.index, f.line) for f in ix.failures] == [(1, 2)] and "boom" in ix.failures[0].message
    assert [r.line for r in ix.references if r.role == "write"] == [3]
    assert all(r.line != 2 for r in ix.references)
    assert refs(ix, "A", "write")[0].status == "resolved"


def test_build_never_raises_on_malformed_sources():
    samples = [
        "",
        "Public\nBeginProg\nEndProg\n",
        "Public X(\nBeginProg\n",
        "Sub\nEndSub\n",
        "Alias\nAlias X\nUnits\nUnits X\nBeginProg\n",
        'S = "unterminated\n',
        "DataTable(\nEndTable\nStructureType\nEndStructureType\n",
        "Sub A(B,\n",
        "If X Then Y = Z Else\nSelect Case\nCase Is >\nFor\nDo While\n",
        "Public X.Y.Z\nX.Y.Z.W(1).V = 1\nBeginProg\n!p = @q\nEndProg\n",
    ]
    for logger in ("cr6", "cr1000x", "cr300", "cr350"):
        for src in samples:
            ix = index(src, logger)
            assert ix.failures == (), (logger, src)


# ------------------------------------------------------------------- corpora


def _corpus_counts(files, logger="cr6"):
    total: Counter = Counter()
    failures = []
    prof = profiles.get(logger)
    for path in files:
        prog, _ = parser.parse_file(path, prof)
        ix = bindings.build(prog, prof)
        total.update({"files": 1, "declarations": len(ix.declarations), "unparsed": len(ix.unparsed)})
        for status, n in ix.counts()["references"].items():
            total[status] += n
        failures += [(path.name, f) for f in ix.failures]
    return total, failures


def test_build_never_raises_on_crbasic_directory_and_reports_unresolved():
    files = sorted(CRBASIC.glob("*.CR6"))
    assert files
    total, failures = _corpus_counts(files)
    assert failures == []
    print("crbasic/*.CR6 (cr6):", dict(total))
    assert total["resolved"] > 0


@pytest.mark.skipif(not CHM, reason="CRBasic help examples not extracted; see tests/corpus/README.md")
def test_build_never_raises_on_chm_corpus_and_reports_unresolved():
    assert CHM
    total, failures = _corpus_counts(CHM)
    assert failures == []
    print("tests/corpus/chm (cr6):", dict(total))
    assert total["resolved"] > total["unresolved"]


@pytest.mark.parametrize("logger", ["cr1000x", "cr300", "cr350"])
@pytest.mark.skipif(not CHM, reason="CRBasic help examples not extracted; see tests/corpus/README.md")
def test_build_never_raises_on_chm_sample_other_loggers(logger):
    total, failures = _corpus_counts(CHM[::7], logger)
    assert failures == [] and total["files"] > 0


# ------------------------------------- LSP-R1: two-word closers, instruction tokens


@pytest.mark.parametrize("end", ["EndSub", "End Sub"])
def test_two_word_end_sub_closes_routine_scope(end):
    ix = index(
        f"Public x\nSub S()\n Dim x\n x=1\n{end}\nPublic z\nBeginProg\n Scan(1,Sec,0,0)\n  x=2\n NextScan\nEndProg\n"
    )
    assert ix.failures == ()
    assert [(d.kind, d.scope, d.line) for d in decls(ix, "z")] == [("public", "global", 6)]
    assert [targets(r) for r in refs(ix, "x") if r.line == 4] == [[("dim", "routine", 3)]]
    assert [targets(r) for r in refs(ix, "x") if r.line == 9] == [[("public", "global", 1)]]


def test_instruction_keyword_resolves_to_catalog_before_a_user_declaration():
    ix = index("Public PanelTemp,T\nBeginProg\n Scan(1,Sec,0,0)\n  PanelTemp(T,15000)\n NextScan\nEndProg\n")
    assert ix.failures == ()
    ref = refs(ix, "PanelTemp", "instruction")[0]
    assert ref.status == "resolved"
    assert [(d.kind, d.external and d.external.kind) for d in ref.resolved] == [("external", "instruction")]
    public = decls(ix, "PanelTemp", "public")[0]
    assert not any(r.role == "instruction" for r in ix.references_to(public))


def test_instruction_keyword_resolves_to_catalog_before_a_routine_local():
    ix = index("Sub S(T)\n Dim PanelTemp\n PanelTemp(T,15000)\nEndSub\nBeginProg\nEndProg\n")
    ref = refs(ix, "PanelTemp", "instruction")[0]
    assert [(d.kind, d.external and d.external.kind) for d in ref.resolved] == [("external", "instruction")]


# ------------------------------------------- LSP-R3b: array-parameter dimensions (review F1)


def test_parameter_dimension_expressions_are_read_references_and_keep_ordinals():
    ix = index("Const N = 3\nSub Fill(p(sz) As Float, sz As Long, q(N) As Float)\nEndSub\nBeginProg\nEndProg\n")
    assert ix is not None
    sz = decls(ix, "sz", "param")[0]
    assert sz.occurrence == 1 and required(ix.token_of(sz)).col == sz.unit.text.index("sz As")
    dim = refs(ix, "sz")[0]
    assert (dim.role, dim.occurrence, required(ix.token_of(dim)).col) == ("read", 0, sz.unit.text.index("sz)"))
    assert dim.resolved == (sz,)  # resolved in the routine's scope
    n = refs(ix, "N")[0]
    assert n.role == "read" and targets(n) == [("const", "global", 1)]


def test_param_dim_exprs_is_additive():
    prog, _ = parser.parse_text(
        "Sub F(a(2, k) As Long, b() As Long, c)\nEndSub\nBeginProg\nEndProg\n", "t.cr6", profile=profiles.get("cr6")
    )
    sub = next(s for s in prog.statements if isinstance(s, S.SubDef))
    assert [p.dims for p in sub.params] == [1, 1, 0]
    assert [len(p.dim_exprs) for p in sub.params] == [2, 0, 0]
    assert isinstance(sub.params[0].dim_exprs[1], S.Name)
    assert "dim_exprs" not in repr(sub.params[0])
    assert sub.params[0] == S.Param(name="a", dims=1, type="LONG")  # not part of equality
