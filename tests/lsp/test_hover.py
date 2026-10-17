"""Hover and signature-help logic tests. Help text in these tests is synthetic (written here); the
optional real-help tests run only when ../extracted_chm exists and assert structure, not text."""

from pathlib import Path

import pytest

from opencrbasic import hover as H
from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.helpdocs import HelpProvider, InstructionHelp, ParamHelp

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT.parent / "crbasic").resolve()
CHM = ROOT.parent / "extracted_chm"


def _at(text: str, marker: str = "|"):
    """(text without marker, line, column) of the single marker."""
    before, after = text.split(marker)
    return before + after, before.count("\n"), len(before) - (before.rfind("\n") + 1)


def ctx(text: str):
    t, line, col = _at(text)
    return H.call_context(t, line, col)


class FakeHelp:
    """Duck-typed HelpProvider with synthetic pages keyed by upper-case name."""

    def __init__(self, **pages: InstructionHelp):
        self.pages = {k.upper(): v for k, v in pages.items()}

    def lookup(self, name: str):
        return self.pages.get(name.upper())


def page(name: str, params: list[tuple[str, str]], summary: str = "Synthetic summary.",
         syntax: str | None = None, optional: tuple[str, ...] = ()) -> InstructionHelp:
    ps = tuple(ParamHelp(n, None, d, n in optional) for n, d in params)
    return InstructionHelp(name.upper(), (name.upper(),), f"{name} (Synthetic Title)",
                           syntax or f"{name} ( {', '.join(n for n, _ in params)} )", ps, summary,
                           Path("/help/Instructions") / f"{name.lower()}.htm")


VOLT = page("VoltSe", [("Dest", "Where to store."), ("Reps", "How many."), ("Range", "Range code."),
                       ("SEChan", "Channel."), ("MeasOff", "Offset."), ("SettlingTime", "Delay."),
                       ("fN1", "Notch."), ("Mult", "Multiplier."), ("Offset", "Offset value.")])
SAMPLE3 = page("Sample", [("Reps", "r"), ("Source", "s"), ("DataType", "d")])
LEFT = page("Left", [("SearchString", "The text."), ("NumChars", "How many.")])
HELP = FakeHelp(voltse=VOLT, sample=SAMPLE3, left=LEFT)


# ---------------------------------------------------------------- call_context


def test_simple_calls_and_active_index():
    assert ctx("VoltSe(|") == H.CallContext("VoltSe", 0, 0, 0)
    assert ctx("VoltSe(a|") .active == 0
    assert ctx("VoltSe(a,|").active == 1
    assert ctx("VoltSe(a, b|, c)").active == 1
    assert ctx("VoltSe(a, b, c|)").active == 2
    assert ctx("VoltSe(a, b, c)|") is None
    assert ctx("|VoltSe(a, b)") is None
    assert ctx("VoltSe|(a)") is None          # on the name, before the paren
    assert ctx("  Scan (1, Sec|, 0)").name == "Scan"    # blank between name and paren
    assert ctx("  Scan (1, Sec|, 0)").column == 2


def test_nested_calls():
    src = "A(B(1,2),3)"
    cases = {2: ("A", 0), 4: ("B", 0), 5: ("B", 0), 6: ("B", 1), 7: ("B", 1), 8: ("A", 0),
             9: ("A", 1), 10: ("A", 1), 11: None}
    for col, want in cases.items():
        got = H.call_context(src, 0, col)
        assert (got and (got.name, got.active)) == want, col
    assert H.call_context(src, 0, 6).column == 2      # B starts at column 2


def test_grouping_parentheses_and_braces_are_transparent():
    assert (ctx("A((1+|2), 3)").name, ctx("A((1+|2), 3)").active) == ("A", 0)
    assert ctx("A((1,2)|, 3)").active == 0           # the comma inside the group is not A's
    assert ctx("A({1,2,|3}, 4)").active == 0
    assert ctx("A({1,2}, 4|)").active == 1
    assert ctx("If (a And |b) Then") is None         # grammar words are not calls
    assert ctx("x = a And (b|)") is None
    assert ctx("x = f(a And (b|), c)").name == "f"


def test_strings_comments_and_incomplete_calls():
    assert ctx('A("x,y", |2)').active == 1
    assert ctx('A("a)b|c", 2)').active == 0          # inside a string: still A, argument 0
    assert ctx('A("a""b,|c", 2)').active == 0        # "" is an escaped quote
    assert ctx('A("a"",b", |2)').active == 1
    assert ctx('A(1, "unterminated|').active == 1
    assert ctx("A(1, 2,|").active == 2               # trailing comma
    assert ctx("A(B(1,|").name == "B"                # nothing closed
    assert ctx("A(1) ' note (x, y|)") is None        # comment
    assert ctx("A(1 ' note, |") is None
    assert ctx("A(1, ' c\n|") is None                # next line does not continue the call


def test_continuation_lines_and_crlf():
    assert ctx("A(1, _\n   2, |_\n   3)").active == 2
    assert ctx("A(1, _\n   2|, _\n   3)").active == 1
    got = ctx("A(1, _\n   2, |_\n   3)")
    assert (got.line, got.column) == (0, 0)
    assert ctx("A(1, _\n   2, _\n   3|)").active == 2
    assert ctx("A(1,\n|2)") is None                  # no `_`: a new statement
    t, line, col = _at("A(1, _\r\n 2, |3)")
    assert H.call_context(t, line, col).active == 2
    t, line, col = _at("A(1, 2,\r\n  B(3,|4)")
    assert H.call_context(t, line, col).name == "B"
    # a comment ends the line, so a `_` inside it does not continue
    assert ctx("A(1) ' _\n  B(|2)").name == "B"


def test_dotted_names_and_dollar_names():
    assert ctx("Status.Battery(|1)").name == "Status.Battery"
    assert ctx("x = Chr$(|65)").name == "Chr$"
    assert ctx("Tbl.Field(2,|3)").active == 1


def test_call_context_positions_out_of_range():
    assert H.call_context("A(1)", 5, 0) is None
    assert H.call_context("A(1)", -1, 0) is None
    assert H.call_context("A(1", 0, -2) is None
    assert H.call_context("A(1", 0, 99).active == 0      # past the end clamps
    assert H.call_context("", 0, 0) is None


def test_colon_statements():
    assert ctx("x = 1 : A(1, |2)").name == "A"
    assert ctx("A(1) : x = |2") is None


# ---------------------------------------------------------------- signature_help


def test_signature_paired_from_help():
    s = H.signature_help("voltse", 2, "cr6", HELP)
    assert s.name == "VoltSe" and s.kind == "instruction" and s.paired and s.note == ""
    assert s.label == "VoltSe(Dest, Reps, Range, SEChan, MeasOff, SettlingTime, fN1, Mult, Offset)"
    assert [p.label for p in s.parameters][:3] == ["Dest", "Reps", "Range"]
    assert s.parameters[2].documentation == "Range code."
    assert s.active_parameter == 2 and s.catalog_count == s.help_count == 9
    assert "Synthetic summary." in s.documentation and "/help/Instructions".split("/")[-1] in s.documentation
    assert "`Instructions/voltse.htm`" in s.documentation
    assert s.help_source == Path("/help/Instructions/voltse.htm")


def test_signature_label_spans_slice_the_label():
    s = H.signature_help("VoltSe", 0, "cr6", HELP)
    for p in s.parameters:
        a, b = p.label_span
        assert s.label[a:b].startswith(p.label)
    s = H.signature_help("Sample", 0, "cr6", None)
    for p in s.parameters:
        a, b = p.label_span
        assert s.label[a:b].startswith(p.label)
        assert ("(optional)" in s.label[a:b]) == p.optional


def test_signature_type_codes_and_kinds():
    s = H.signature_help("VoltSe", 0, "cr6", None)
    assert [p.label for p in s.parameters] == [f"arg{i}" for i in range(1, 10)]
    assert s.parameters[0].type_code == 8 and s.parameters[0].kind == "variable_or_array"
    assert s.parameters[1].kind == "const_expr"
    s = H.signature_help("Sample", 0, "cr6", None)
    assert s.parameters[3].type_code >= 100 and s.parameters[3].optional
    assert s.parameters[3].kind == "const_expr"      # code 101 is the optional form of 1
    assert not s.parameters[0].optional


def test_signature_count_mismatch_uses_catalog_count_and_argn():
    s = H.signature_help("Sample", 1, "cr6", HELP)       # help has 3, catalog 4
    assert not s.paired and s.help_count == 3 and s.catalog_count == 4
    assert [p.label for p in s.parameters] == ["arg1", "arg2", "arg3", "arg4"]
    assert all(p.documentation == "" for p in s.parameters)
    assert "help lists 3 parameters, catalog has 4" in s.note
    assert "Synthetic summary." in s.documentation and "Help syntax: `Sample ( Reps, Source, DataType )`" in s.documentation
    assert "not applied" in s.documentation


def test_signature_help_optional_word_from_help():
    h = FakeHelp(voltse=page("VoltSe", [(f"p{i}", "") for i in range(9)], optional=("p8",)))
    s = H.signature_help("VoltSe", 0, "cr6", h)
    assert s.parameters[8].optional and s.label.endswith("p8 (optional))")


def test_active_parameter_is_clamped():
    assert H.signature_help("VoltSe", -3, "cr6", None).active_parameter == 0
    assert H.signature_help("VoltSe", 99, "cr6", None).active_parameter == 8
    assert H.signature_help("VoltSe", 4, "cr6", None).active_parameter == 4


def test_signature_zero_parameters():
    s = H.signature_help("EndProg", 3, "cr6", None)
    assert s.parameters == () and s.active_parameter is None and s.label == "ENDPROG()"


def test_signature_for_functions():
    s = H.signature_help("left", 1, "cr6", HELP)
    assert s.kind == "function" and s.name == "LEFT" and s.paired
    assert s.label == "LEFT(SearchString, NumChars)" and s.active_parameter == 1
    assert s.parameters[0].type_code is None and s.parameters[0].kind is None
    s = H.signature_help("Left", 7, "cr6", None)
    assert s.label == "LEFT(arg1, arg2)" and s.active_parameter == 1
    s = H.signature_help("Chr$", 0, "cr6", None)
    assert s.name == "CHR$" and s.label == "CHR$(arg1)"
    assert H.signature_help("Chr", 0, "cr6", None).name == "CHR"     # $ is part of the name: a different row


def test_signature_none_cases():
    assert H.signature_help("NoSuchThing", 0, "cr6", HELP) is None
    assert H.signature_help("Status.Battery", 0, "cr6", None) is None
    assert H.signature_help("TRUE", 0, "cr6", None) is None      # predefined constant
    assert H.signature_help("Then", 0, "cr6", None) is None      # keyword only
    assert H.signature_help("NOT", 0, "cr6", None) is None       # operator without argument count


def test_profile_object_and_unknown_profile():
    from opencrbasic import profiles
    assert H.signature_help("VoltSe", 0, profiles.get("cr6"), None).label.startswith("VoltSe(")
    with pytest.raises(ValueError):
        H.signature_help("VoltSe", 0, "nope", None)


# ---------------------------------------------------------------- declarations and hover

PROGRAM = """\
Public Fvar(3) As Float, Name As String * 20, Plain
Const K = 2*3
Const Label As String = "x"
Alias Fvar(1) = First
Units Fvar = degC
Units Plain = mm
Sub Foo(a, b As Long)
  Dim loc
EndSub
Function Bar(x) As Long
  Dim loc As Long
EndFunction
DataTable(Tbl,True,-1)
  Sample(1,Fvar(1),FP2)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    Fvar(1) = Chr(65) ' Fvar in a comment
    Plain = Len("Fvar") + 5 + &H1F
    Tbl.Fvar_1 = 1
  NextScan
EndProg
"""


@pytest.fixture(scope="module")
def prog(tmp_path_factory):
    path = tmp_path_factory.mktemp("hover") / "Main.CR6"
    path.write_text(PROGRAM)
    return analyze(AnalysisRequest(path)), PROGRAM


def hv(prog, line, col, help_provider=None, **kw):
    result, text = prog
    return H.hover(result, text, line, col, "cr6", help_provider, **kw)


def find(text, needle, nth=0):
    idx = -1
    for _ in range(nth + 1):
        idx = text.index(needle, idx + 1)
    before = text[:idx]
    return before.count("\n"), idx - (before.rfind("\n") + 1)


def test_declarations_table(prog):
    d = H.declarations(prog[0].program)
    assert [x.kind for x in d["FVAR"]] == ["Public"]
    v = d["FVAR"][0]
    assert v.dims == ("3",) and v.type == "FLOAT" and v.units == "degC" and v.line == 1 and v.file == "Main.CR6"
    assert v.physical_line == 1
    assert d["NAME"][0].string_size == "20" and d["PLAIN"][0].units == "mm"
    assert d["K"][0].kind == "Const" and d["K"][0].value == "6"
    assert d["LABEL"][0].type is not None
    assert d["FIRST"][0].kind == "Alias" and d["FIRST"][0].detail == "Fvar(1)"
    assert d["FOO"][0].kind == "Sub" and d["FOO"][0].detail == "a, b As LONG"
    assert d["BAR"][0].kind == "Function" and d["BAR"][0].type == "LONG"
    assert d["TBL"][0].kind == "DataTable"
    locs = d["LOC"]
    assert [x.scope for x in locs] == ["Foo", "Bar"] and locs[1].type == "LONG"
    assert H.declarations(None) == {}


def test_hover_declared_variable(prog):
    line, col = find(prog[1], "Fvar", 0)
    h = hv(prog, line, col + 2)
    assert (h.line, h.start_column, h.end_column) == (line, col, col + 4)
    for part in ("**Fvar** (Public)", "Public Fvar(3) As FLOAT", "Dimensions: 3", "Units: degC",
                 "Declared in `Main.CR6`, line 1."):
        assert part in h.markdown
    # the same word in code further down hovers the same declaration
    line, col = find(prog[1], "Fvar", 4)       # `Fvar(1) = Chr(65)` in the Scan
    assert "Public Fvar(3)" in hv(prog, line, col).markdown


def test_hover_cursor_at_word_end(prog):
    line, col = find(prog[1], "Plain", 0)
    assert hv(prog, line, col + 5) is not None
    assert hv(prog, line, col + 6) is None or hv(prog, line, col + 6).start_column != col


def test_hover_const_sub_table_alias_and_local(prog):
    text = prog[1]
    assert "Const K = 6" in hv(prog, *find(text, "K =")).markdown
    assert "Const Label = " in hv(prog, *find(text, "Label")).markdown
    assert "Sub Foo(a, b As LONG)" in hv(prog, *find(text, "Foo")).markdown
    assert "DataTable(Tbl, ...)" in hv(prog, *find(text, "Tbl")).markdown
    md = hv(prog, *find(text, "First")).markdown
    assert "Alias Fvar(1) = First" in md
    md = hv(prog, *find(text, "loc")).markdown
    assert "Declared more than once" in md and "local to `Foo`" in md and "local to `Bar`" in md


def test_hover_nothing_in_comments_strings_numbers_members(prog):
    text = prog[1]
    assert hv(prog, *find(text, "Fvar in a comment")) is None
    line, col = find(text, '"Fvar"')
    assert hv(prog, line, col + 2) is None           # inside the string
    line, col = find(text, "&H1F")
    assert hv(prog, line, col + 2) is None           # hex literal digits are not a word
    assert hv(prog, line, col + 1) is None
    line, col = find(text, "Fvar_1")
    assert hv(prog, line, col + 2) is None           # member after `.`
    assert hv(prog, 0, 400) is None and hv(prog, 99, 0) is None and hv(prog, -1, 0) is None
    assert hv(prog, 0, -1) is None
    line, col = find(text, "+ 5")
    assert hv(prog, line, col + 2) is None           # number


def test_hover_instruction_with_and_without_help(prog):
    text = prog[1]
    line, col = find(text, "Sample")
    none = hv(prog, line, col, None).markdown
    assert "**Sample** (instruction)" in none and "Sample(arg1, arg2, arg3, arg4 (optional))" in none
    assert "Help: not configured (No help directory configured)." in none and "const_expr" in none
    missing = hv(prog, line, col, FakeHelp()).markdown
    assert "Help: still to do (No help page for this name)." in missing
    got = hv(prog, line, col, HELP).markdown
    assert "**Sample (Synthetic Title)**" in got and "Synthetic summary." in got
    assert "Help lists 3 parameters, catalog has 4" in got and "Source: help page `Instructions/sample.htm`" in got
    line, col = find(text, "Chr")
    fn = hv(prog, line, col, None).markdown
    assert "**CHR** (function)" in fn or "(function)" in fn
    assert "CHR(arg1)" in fn or "Chr(arg1)" in fn.replace("CHR", "Chr")


def test_hover_paired_instruction_lists_parameters():
    text = "BeginProg\n  VoltSe(x, 1, mV5000, 1, False, 0, 250, 1, 0)\nEndProg\n"
    h = H.hover(None, text, 1, 4, "cr6", HELP)
    assert "**VoltSe (Synthetic Title)**" in h.markdown
    assert "- `Dest` (variable_or_array): Where to store." in h.markdown
    assert "- `Reps` (const_expr): How many." in h.markdown
    assert "not applied" not in h.markdown
    assert h.markdown.index("```crbasic") < h.markdown.index("Synthetic summary.")


def test_hover_constants_keywords_and_function_without_count():
    t = "x = TRUE\nIf x Then\ny = a And b\nz = FOO\n"
    h = H.hover(None, t, 0, 5, "cr6", None)
    assert "(predefined constant)" in h.markdown and "TRUE = " in h.markdown
    assert (h.start_column, h.end_column) == (4, 8)
    h = H.hover(None, t, 1, 7, "cr6", None)
    assert h.markdown == "**Then** (CRBasic keyword)"
    h = H.hover(None, t, 2, 7, "cr6", None)          # And: function row, no argument count
    assert "AND" in h.markdown.upper() and "no argument count" in h.markdown
    assert H.hover(None, t, 3, 5, "cr6", None) is None   # unknown word, no analysis
    h = H.hover(None, t, 1, 0, "cr6", None)          # If: instruction row, symbol and keyword
    assert h is not None and "If" in h.markdown and "Also a predefined constant" in h.markdown


def test_hover_dollar_names():
    t = "x = Chr$(65)\ny = Chr(65)\n"
    h = H.hover(None, t, 0, 6, "cr6", None)
    assert h.markdown.startswith("**CHR$**") and (h.start_column, h.end_column) == (4, 8)
    h = H.hover(None, t, 1, 5, "cr6", None)
    assert h is not None and not h.markdown.startswith("**CHR$**")


def test_hover_injected_declarations_and_no_result():
    t = "Public Z\nZ = 1\n"
    assert H.hover(None, t, 1, 0, "cr6", None) is None
    d = H.Declaration("Z", "Public", type="LONG", file="X.CR6", line=7)
    h = H.hover(None, t, 1, 0, "cr6", None, declared={"Z": [d]})
    assert "Public Z As LONG" in h.markdown and "line 7" in h.markdown


def test_declared_name_wins_over_catalog():
    d = H.Declaration("Sample", "Public", file="X.CR6", line=1)
    h = H.hover(None, "Sample = 1\n", 0, 2, "cr6", HELP, declared={"SAMPLE": [d]})
    assert h.markdown.startswith("**Sample** (Public)")


def test_markdown_is_escaped_and_single_line_descriptions():
    h = FakeHelp(voltse=page("VoltSe", [("Dest", "line one\nline two with <angle> and *star*")] * 9,
                             summary="Has _under_ score."))
    s = H.signature_help("VoltSe", 0, "cr6", h)
    assert s.parameters[0].documentation == r"line one line two with \<angle\> and \*star\*"
    md = H.hover(None, "VoltSe(", 0, 2, "cr6", h).markdown
    assert r"Has \_under\_ score." in md and "\n" not in s.parameters[0].documentation


# ---------------------------------------------------------------- real help (optional)


@pytest.fixture(scope="module")
def prov():
    return HelpProvider(CHM)


@pytest.mark.skipif(not CHM.is_dir(), reason="extracted_chm not present")
class TestRealHelp:
    def test_voltse_pairs(self, prov):
        s = H.signature_help("VoltSe", 3, "cr6", prov)
        assert s.paired and s.catalog_count == s.help_count == 9
        assert s.parameters[3].label == "SEChan" and s.parameters[3].documentation
        assert s.documentation and s.help_source.name == "voltse.htm"

    def test_hover_has_provenance_and_parameters(self, prov):
        h = H.hover(None, "VoltSe(a)", 0, 2, "cr6", prov)
        assert "Source: help page `Instructions/voltse.htm`" in h.markdown
        assert "- `Dest` (variable_or_array)" in h.markdown

    def test_mismatch_still_gives_signature(self, prov):
        s = H.signature_help("Sample", 0, "cr6", prov)
        assert s is not None and len(s.parameters) == s.catalog_count
        if not s.paired:
            assert all(p.label.startswith("arg") for p in s.parameters)

    def test_function_help(self, prov):
        s = H.signature_help("Left", 0, "cr6", prov)
        assert s.kind == "function" and len(s.parameters) == 2

    def test_every_catalog_callable_builds(self, prov):
        from opencrbasic.catalog import InstructionCatalog
        for e in InstructionCatalog.for_profile("cr6"):
            if e.kind in ("instruction", "function"):
                s = H.signature_help(e.name, 99, "cr6", prov)
                if s is not None:
                    assert s.active_parameter in (None, len(s.parameters) - 1)
                    assert s.label.upper().startswith(e.name.upper() + "(")


# ---------------------------------------------------------------- never raises


def _columns(line: str) -> set[int]:
    return {0, len(line) // 2, len(line), len(line) + 3} | {i for i, c in enumerate(line) if c in "(),\"' "}


@pytest.mark.slow
def test_sweep_over_project_programs():
    files = sorted(CRBASIC.glob("*.CR6")) + sorted(CRBASIC.glob("*.crb"))
    assert files
    checked = 0
    for f in files:
        text = f.read_bytes().decode("latin-1")
        for ln, line in enumerate(text.replace("\r\n", "\n").split("\n")):
            for col in _columns(line):
                H.call_context(text, ln, col)
                H.hover(None, text, ln, col, "cr6", HELP)
                checked += 1
    assert checked > 1000


@pytest.mark.slow
def test_sweep_with_analysis_and_real_help(memo_analyze):
    path = CRBASIC / "Main.CR6"
    if not path.exists():
        pytest.skip("no Main.CR6")
    text = path.read_bytes().decode("latin-1")
    result = memo_analyze(AnalysisRequest(path))
    prov = HelpProvider(CHM) if CHM.is_dir() else HELP
    hits = 0
    for ln, line in enumerate(text.replace("\r\n", "\n").split("\n")):
        for col in {0, 2, len(line) // 2, len(line)}:
            if H.hover(result, text, ln, col, "cr6", prov) is not None:
                hits += 1
    assert hits > 10


def test_hostile_text():
    for t in ["", "(", ")))", '"', "'", "A(" * 500, "{" * 300 + "A(1,2", "\x00(\x00,", "_", "A(1, _\n_\n_\n"]:
        n = t.count("\n") + 1
        for ln in range(n + 1):
            for col in (0, 1, 5, 1000):
                H.call_context(t, ln, col)
                H.hover(None, t, ln, col, "cr6", None)


@pytest.mark.parametrize("end", ["EndSub", "End Sub"])
def test_two_word_end_sub_closes_hover_scope(tmp_path, end):
    path = tmp_path / "Main.CR6"
    path.write_text(f"Public x\nSub S()\n Dim x\nx=1\n{end}\nPublic z\n")
    d = H.declarations(analyze(AnalysisRequest(path)).program)
    assert [x.scope for x in d["X"]] == [None, "S"]
    assert d["Z"][0].scope is None


# ---------------------------------------------------------------- function arity from the checker

def _ftp_help(count: int) -> FakeHelp:
    names = [f"P{i}" for i in range(1, count + 1)]
    return FakeHelp(ftpclient=page("FTPClient", [(n, "d") for n in names]))


def test_function_accepted_counts_pair_help_names_with_optional_tail():
    # FTPCLIENT (table nargs 6) takes 6, 10 or 11 arguments (checker `_ARITY_ALLOWED`); 11 help names.
    s = H.signature_help("FTPClient", 0, "cr6", _ftp_help(11))
    assert s.paired and s.catalog_count == s.help_count == 11 and s.note == ""
    assert s.label == "FTPCLIENT(P1, P2, P3, P4, P5, P6, P7 (optional), P8 (optional), P9 (optional), " \
                      "P10 (optional), P11 (optional))"
    assert [p.optional for p in s.parameters] == [False] * 6 + [True] * 5
    assert s.parameters[0].documentation == "d"
    assert "Accepted argument counts (checker): 6, 10 or 11." in s.documentation
    assert s.active_parameter == 0
    assert H.signature_help("FTPClient", 20, "cr6", _ftp_help(11)).active_parameter == 10


def test_function_help_count_not_an_accepted_maximum_keeps_argn():
    # MQTTSUBSCRIBE (table nargs 4): the checker has a lower bound only, so no largest count; help has 8.
    s = H.signature_help("MQTTSubscribe", 0, "cr6", FakeHelp(mqttsubscribe=page("MQTTSubscribe", [(f"P{i}", "d") for i in range(1, 9)])))
    assert not s.paired and s.label == "MQTTSUBSCRIBE(arg1, arg2, arg3, arg4)"
    assert all(p.documentation == "" and not p.optional for p in s.parameters)
    assert "Accepted argument counts" not in s.documentation


def test_function_allowed_counts_pair_when_help_matches_the_maximum():
    # EMAILSEND (table nargs 6) takes 9, 13 or 14 arguments (checker `_ARITY_ALLOWED`); help names 14.
    s = H.signature_help("EmailSend", 0, "cr6", FakeHelp(emailsend=page("EmailSend", [(f"P{i}", "d") for i in range(1, 15)])))
    assert s.paired and s.catalog_count == s.help_count == 14
    assert "Accepted argument counts (checker): 9, 13 or 14." in s.documentation


def test_function_range_counts_pair_when_help_matches_the_maximum():
    # NETWORKTIMEPROTOCOL (nargs 5) takes 3 or 4 arguments (checker `_ARITY_RANGE`); help lists 4.
    help4 = FakeHelp(networktimeprotocol=page("NetworkTimeProtocol",
                                              [("Server", "a"), ("Offset", "b"), ("Max", "c"), ("Wait", "d")]))
    s = H.signature_help("NetworkTimeProtocol", 0, "cr6", help4)
    assert s.paired and s.label == "NETWORKTIMEPROTOCOL(Server, Offset, Max, Wait (optional))"
    assert "Accepted argument counts (checker): 3 or 4." in s.documentation


def test_function_without_help_or_with_other_count_is_unchanged():
    assert H.signature_help("FTPClient", 0, "cr6", None).label == "FTPCLIENT(arg1, arg2, arg3, arg4, arg5, arg6)"
    s = H.signature_help("FTPClient", 0, "cr6", _ftp_help(9))
    assert not s.paired and s.label == "FTPCLIENT(arg1, arg2, arg3, arg4, arg5, arg6)"


def test_accepted_counts_helper():
    assert H._accepted_counts("FTPClient", 6) == (6, 10, 11)
    assert H._accepted_counts("ftpclient", 6) == (6, 10, 11)
    assert H._accepted_counts("HTTPPut", 5) == (4, 8, 9)
    assert H._accepted_counts("NetworkTimeProtocol", 5) == (3, 4)
    assert H._accepted_counts("Left", 2) == (2,)         # both bounds are nargs in the checker tables
    assert H._accepted_counts("EmailSend", 6) == (9, 13, 14)
    assert H._accepted_counts("MQTTSubscribe", 4) is None    # minimum only in the checker tables
    assert H._accepted_counts("NoSuchFunction", 3) is None


def test_declarations_include_quoted_datatable_and_consttable_names(tmp_path):
    path = tmp_path / "Main.CR6"
    path.write_text('Public x\nDataTable("Hist",1,-1)\nConstTable("Ct")\nEndTable\nEndProg\n')
    d = H.declarations(analyze(AnalysisRequest(path)).program)
    assert d["HIST"][0].kind == "DataTable" and d["HIST"][0].name == "Hist"
    assert d["CT"][0].kind == "ConstTable" and d["CT"][0].name == "Ct"
    assert d["X"][0].kind == "Public"


def test_no_help_placeholders_in_signature_documentation():
    # Help directory not configured: "not configured"; configured but no page: "still to do".
    off = H.signature_help("Left", 0, "cr6", None)
    assert off.help_count is None and off.label == "LEFT(arg1, arg2)"
    assert "Help: not configured (No help directory configured)." in off.documentation
    assert "still to do" not in off.documentation
    todo = H.signature_help("FTPClient", 0, "cr6", FakeHelp())
    assert "Help: still to do (No help page for this name)." in todo.documentation
    assert "not configured" not in todo.documentation
    # With help, no placeholder is shown.
    assert "Help: " not in H.signature_help("left", 0, "cr6", HELP).documentation


def test_no_help_placeholders_in_hover_markdown(prog):
    from opencrbasic.hover import _callable_markdown
    from opencrbasic import hover as HH
    cat = HH._catalog("cr6")
    entry = cat.get("Sample", "instruction")
    assert entry is not None
    off = _callable_markdown(entry, None, False, ())
    assert "Help: not configured (No help directory configured)." in off
    todo = _callable_markdown(entry, None, True, ())
    assert "Help: still to do (No help page for this name)." in todo


# ---------------------------------------------------------------- LSP-R3d (review F9, F10)


def stack(text: str):
    t, line, col = _at(text)
    return [(c.name, c.active) for c in H.call_stack(t, line, col)]


def test_call_stack_is_innermost_first_and_context_is_its_head():
    assert stack("Sample(1, Temp(|1), FP2)") == [("Temp", 0), ("Sample", 1)]
    assert stack("VoltSe(Temp(1), |") == [("VoltSe", 1)]
    assert stack("VoltSe(x, MySub(1, |") == [("MySub", 1), ("VoltSe", 1)]
    assert stack("x = (a + (b|))") == []
    assert stack("A(1) ' B(|") == []
    assert ctx("Sample(1, Temp(|1), FP2)").name == "Temp"


def test_signature_help_falls_through_an_array_index_to_the_instruction():
    from opencrbasic.lsp import features
    from opencrbasic.lsp.features import Ctx
    from lsprotocol import types
    path = Path("/nowhere/x.cr6")
    for text, col, name in (("Sample(1, Temp(1), FP2)", 15, "Sample"),
                            ("VoltSe(Temp(1), ", 16, "VoltSe"),
                            ("VoltSe(x, MySub(1, ", 19, "VoltSe")):
        c = Ctx("cr6", "utf-16", "utf-8", {path: text.encode()}, {})
        got = features.signature_help(c, path, types.Position(0, col))
        assert got is not None and got.signatures[0].label.startswith(name + "("), text
    c = Ctx("cr6", "utf-16", "utf-8", {path: b"x = Temp(1)"}, {})
    assert features.signature_help(c, path, types.Position(0, 9)) is None


def test_string_goes_on_across_a_continuation():
    assert ctx('x = Foo("a, _\n     b", |2)').active == 1
    t, line, col = _at('x = Foo("a, _\n     b|", 2)')
    assert H.call_context(t, line, col).active == 0
    # a word inside the continued string is not hover material
    t, line, col = _at('s = "a _\n  Volt|age"')
    assert H._word_at(H._lines(t), line, col) is None
    t, line, col = _at('s = "a" _\n  + Volt|age')
    assert H._word_at(H._lines(t), line, col) is not None
