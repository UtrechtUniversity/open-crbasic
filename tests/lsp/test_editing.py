"""Editing-feature logic (LSP-03b-core): outline, folding, include links, completion."""

from pathlib import Path

import pytest

from opencrbasic import editing, parser, profiles
from opencrbasic import syntax as S
from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.catalog import InstructionCatalog
from opencrbasic.editing import (
    FoldRange, Span, completion_candidates, declared_names, document_symbols, folding_ranges,
    include_links,
)

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT.parent / "crbasic").resolve()
CHM = sorted((ROOT / "tests" / "corpus" / "chm").glob("*.CR6"))
MAIN = CRBASIC / "Main.CR6"


def _write(tmp_path: Path, name: str, text: str, newline: str = "\n") -> Path:
    path = tmp_path / name
    path.write_bytes(text.replace("\n", newline).encode("latin-1"))
    return path


def _analyze(path: Path):
    return analyze(AnalysisRequest(path))


def _folds(text: str):
    return [(f.start_line, f.end_line) for f in folding_ranges(text) if f.kind == "region"]


def _flat(symbols):
    for s in symbols:
        yield s
        yield from _flat(s.children)


# ------------------------------------------------------------------ block words

EXPECTED_CR6_PAIRS = {
    "FOR": "NEXT", "DO": "LOOP", "WHILE": "WEND", "DATATABLE": "ENDTABLE", "BEGINPROG": "ENDPROG",
    "SCAN": "NEXTSCAN", "SUBSCAN": "NEXTSUBSCAN", "SLOWSEQUENCE": "ENDSEQUENCE", "IF": "ENDIF",
    "SELECT": "ENDSELECT", "PROCHIPRI": "ENDPROCHIPRI", "CONSTTABLE": "ENDCONSTTABLE",
    "SUB": "ENDSUB", "SUBMENU": "ENDSUBMENU", "FUNCTION": "ENDFUNCTION",
    "DIALSEQUENCE": "ENDDIALSEQUENCE", "MODEMHANGUP": "ENDMODEMHANGUP",
    "APPLYANDRESTARTSEQUENCE": "ENDAPPLYANDRESTARTSEQUENCE", "STRUCTURETYPE": "ENDSTRUCTURETYPE",
    "OBJECTTYPE": "ENDOBJECTTYPE", "DISPLAYMENU": "ENDMENU", "WEBPAGEBEGIN": "WEBPAGEEND",
    "SHUTDOWNBEGIN": "SHUTDOWNEND", "BEGINBURSTTRIGGER": "ENDBURSTTRIGGER",
}

# the statements the parser makes of each opener (words not listed parse as a bare instruction)
OPENER_SNIPPETS = {
    "IF": "If x Then", "FOR": "For i = 1 To 3", "DO": "Do", "SUB": "Sub F()",
    "FUNCTION": "Function F() As Long", "SELECT": "Select Case x", "DATATABLE": "DataTable(T,True,1)",
    "CONSTTABLE": "ConstTable(T)", "WHILE": "While x < 3",
}


def test_block_pairs_cr6_is_pinned():
    # a change in the instruction table that adds or removes a block shows up here
    assert editing.block_pairs("cr6") == EXPECTED_CR6_PAIRS


@pytest.mark.parametrize("profile", ["cr6", "cr1000x", "cr300", "cr350"])
def test_block_words_are_instruction_rows(profile):
    cat = InstructionCatalog.for_profile(profile)
    pairs = editing.block_pairs(profile)
    assert {"IF", "FOR", "SUB", "BEGINPROG", "SCAN", "DATATABLE"} <= set(pairs)
    for opener, closer in pairs.items():
        assert cat.get(opener, "instruction") and cat.get(closer, "instruction"), (opener, closer)
    # every irregular pair the profile has is there; a derived one is `End` + opener
    for opener, closer in editing._IRREGULAR_PAIRS.items():
        if opener in cat and closer in cat:
            assert pairs[opener] == closer


@pytest.mark.parametrize("opener,closer", sorted(EXPECTED_CR6_PAIRS.items()))
def test_parser_reads_block_words_as_the_pair_says(opener, closer):
    roles = editing._roles("cr6")
    snippet = OPENER_SNIPPETS.get(opener, opener)
    # The openers sit in a context where the parser keeps them as statements; a bare word is a
    # generic instruction row.
    program = parser.parse_text(snippet + "\n", "t.CR6")[0]
    assert len(program.statements) == 1, (opener, program.statements)
    stmt = program.statements[0]
    assert not isinstance(stmt, S.Unparsed), (opener, stmt)
    assert editing._ast_event(stmt, roles) == ("open", opener)
    forms = [closer]
    if closer.startswith("END"):
        forms.append("End " + closer[3:])
    if closer.startswith("NEXT") and closer != "NEXT":
        forms.append("Next " + closer[4:])
    for form in forms:
        program = parser.parse_text(form + "\n", "t.CR6")[0]
        assert len(program.statements) == 1, form
        assert not isinstance(program.statements[0], S.Unparsed), form
        assert editing._ast_event(program.statements[0], roles) == ("close", opener), form


def test_inline_if_is_not_a_block_to_the_parser():
    program = parser.parse_text("If x Then y = 1\n", "t.CR6")[0]
    assert editing._ast_event(program.statements[0], editing._roles("cr6")) is None


def test_case_and_else_are_not_block_words():
    roles = editing._roles("cr6")
    for word in ("CASE", "ELSE", "ELSEIF", "EXIT", "RETURN", "CALL", "END", "THEN"):
        assert editing._classify(word, None, roles) is None, word


# ------------------------------------------------------------------ folding


def test_fold_every_block_kind():
    text = "\n".join([
        "Public x",                                    # 0
        "Sub A()",                                     # 1
        "  If x Then",                                 # 2
        "    x = 1",                                   # 3
        "  Else",                                      # 4
        "    x = 2",                                   # 5
        "  EndIf",                                     # 6
        "EndSub",                                      # 7
        "Function B() As Long",                        # 8
        "  Select Case x",                             # 9
        "  Case 1",                                    # 10
        "    x = 3",                                   # 11
        "  EndSelect",                                 # 12
        "  Return 1",                                  # 13
        "EndFunction",                                 # 14
        "DataTable(T,True,-1)",                        # 15
        "  Sample(1,x,FP2)",                           # 16
        "EndTable",                                    # 17
        "BeginProg",                                   # 18
        "  Scan(1,Sec,0,0)",                           # 19
        "    For i = 1 To 3",                          # 20
        "      x = i",                                 # 21
        "    Next",                                    # 22
        "    Do While x < 5",                          # 23
        "      x = x + 1",                             # 24
        "    Loop",                                    # 25
        "    While x < 9",                             # 26
        "      x = x + 1",                             # 27
        "    Wend",                                    # 28
        "    SubScan(1,Sec,3)",                        # 29
        "      x = 0",                                 # 30
        "    NextSubScan",                             # 31
        "  NextScan",                                  # 32
        "  SlowSequence",                              # 33
        "  Scan(5,Sec,0,0)",                           # 34
        "    x = 1",                                   # 35
        "  NextScan",                                  # 36
        "EndProg",                                     # 37
        "",
    ])
    assert _folds(text) == [
        (1, 6), (2, 5), (8, 13), (9, 11), (15, 16), (18, 36), (19, 31), (20, 21), (23, 24),
        (26, 27), (29, 30), (33, 36), (34, 35),
    ]


def test_fold_kinds_and_two_word_closers():
    text = "If x Then\n  y = 1\nEnd If\nScan(1,Sec,0,0)\n  y = 2\nNext Scan\n"
    assert folding_ranges(text) == [FoldRange(0, 1, "region"), FoldRange(3, 4, "region")]


def test_fold_is_case_insensitive():
    assert _folds("sub a()\n  x = 1\nendsub\nIF x THEN\n y\nENDIF\n") == [(0, 1), (3, 4)]


def test_fold_one_line_if_has_no_block():
    text = "If x Then y = 1\nIf x Then y = 1 Else y = 2\nIf x Then : y = 1\nz = 1\n"
    assert folding_ranges(text) == []
    # a block If whose Then ends the line
    assert _folds("If x Then\n y = 1\nEndIf\n") == [(0, 1)]
    # a one-line If followed by more lines does not swallow the later EndIf of another block
    assert _folds("Sub A()\n If x Then y = 1\n y = 2\nEndSub\n") == [(0, 2)]


def test_fold_words_in_strings_and_comments_are_ignored():
    text = ('Public s As String * 20\n'
            'BeginProg\n'
            '  s = "If x Then"\n'
            "  ' Sub Fake()\n"
            '  s = "EndSub"   \' EndIf\n'
            '  If s = "Then" Then\n'
            '    s = "x"\n'
            '  EndIf\n'
            'EndProg\n')
    assert _folds(text) == [(1, 7), (5, 6)]


def test_fold_comment_runs():
    text = "' a\n' b\n' c\nx = 1\n' lone\ny = 2\n  ' d\n\t' e\n"
    assert [(f.start_line, f.end_line) for f in folding_ranges(text) if f.kind == "comment"] == [
        (0, 2), (6, 7)]


def test_fold_directives_including_inactive_branches():
    text = ("#If A Then\n"           # 0
            "Public x\n"             # 1
            "#ElseIf B Then\n"       # 2
            "Public y\n"             # 3
            "#Else\n"                # 4
            "Public z\n"             # 5
            "#EndIf\n"               # 6
            "Const A = 1\n")
    assert _folds(text) == [(0, 5)]
    nested = "#If A\n#If B\nx\n#EndIf\n#EndIf\n"
    assert _folds(nested) == [(0, 3), (1, 2)]


def test_fold_inactive_code_is_still_folded():
    text = "#If False\nSub Dead()\n  x = 1\nEndSub\n#EndIf\n"
    assert _folds(text) == [(0, 3), (1, 2)]


def test_fold_continuation_lines():
    text = "Sub A(a As Long, _\n      b As Long)\n  x = 1\nEndSub\n"
    assert _folds(text) == [(0, 2)]
    # a one-line body has nothing to hide
    assert _folds("Sub A()\nEndSub\n") == []
    assert _folds("Sub A(a, _\n b)\nEndSub\n") == []


def test_fold_crlf():
    text = "Sub A()\r\n  x = 1\r\n  y = 2\r\nEndSub\r\n' c1\r\n' c2\r\n"
    assert folding_ranges(text) == [FoldRange(0, 2, "region"), FoldRange(4, 5, "comment")]


@pytest.mark.parametrize("text,expected", [
    ("Sub A()\n  x = 1\n", []),                                    # unclosed Sub
    ("Sub A()\n  If x Then\n    y = 1\n  EndIf\n", [(1, 2)]),     # inner block still folds
    ("Sub A()\n  If x Then\n    y = 1\nEndSub\n", [(0, 2)]),      # missing EndIf: Sub still closes
    ('Sub A()\n  s = "abc\n  x = 1\nEndSub\n', [(0, 2)]),         # unterminated string
    ("EndSub\nEndIf\nNext\nLoop\n", []),                          # closers with no opener
    ("Sub A()\nSub B()\n x = 1\nEndSub\n", [(1, 2)]),             # only the matched pair
    ("", []),
    ("\n\n", []),
    ("Public", []),
    ('"', []),
    ("If", []),
])
def test_fold_incomplete_text(text, expected):
    assert _folds(text) == expected


def test_fold_slow_sequence_without_end_sequence():
    text = ("BeginProg\n Scan(1,Sec,0,0)\n  x = 1\n NextScan\n SlowSequence\n Scan(2,Sec,0,0)\n"
            "  x = 2\n NextScan\n SlowSequence\n Scan(3,Sec,0,0)\n  x = 3\n NextScan\nEndProg\n")
    folds = _folds(text)
    assert (4, 7) in folds and (8, 11) in folds       # each runs up to the next / to EndProg
    assert (0, 11) in folds
    explicit = "SlowSequence\n Scan(2,Sec,0,0)\n  x = 2\n NextScan\nEndSequence\n"
    assert (0, 3) in _folds(explicit)


def test_fold_long_line_does_not_hide_the_rest():
    long_line = "x = " + "1 + " * 200 + "1"
    assert len(long_line) > 510
    text = f"Sub A()\n  x = 1\nEndSub\n{long_line}\nSub B()\n  y = 1\n  z = 2\nEndSub\n"
    assert _folds(text) == [(0, 1), (4, 6)]


def test_fold_profiles():
    text = "Sub A()\n x = 1\nEndSub\n"
    assert folding_ranges(text, "cr300") == [FoldRange(0, 1, "region")]
    # CR300 reads `If c Then : y` as a block If; CR6 as a one-line If
    one = "If x Then : y = 1\n z = 2\nEndIf\n"
    assert _folds(one) == []
    assert folding_ranges(one, "cr300") == [FoldRange(0, 1, "region")]


def test_fold_main_cr6_matches_outline():
    text = MAIN.read_bytes().decode("latin-1")
    result = _analyze(MAIN)
    folds = {(f.start_line, f.end_line) for f in folding_ranges(text) if f.kind == "region"}
    assert folds
    blocks = [s for s in _flat(document_symbols(result, MAIN))
              if s.kind in ("sub", "function", "datatable", "consttable", "beginprog", "scan")]
    assert blocks
    for s in blocks:
        start, end = s.range.start_line, s.range.end_line - 1      # closer line stays visible
        if end > start:
            assert (start, end) in folds, (s.name, start, end)
    lines = text.split("\n")
    for start, end in folds:
        assert end > start
        assert lines[start].lstrip()[:1] != "'"
    # every fold is a pair of opener .. line before closer
    pairs = editing.block_pairs("cr6")
    openers = set(pairs) | {"#IF", "#IF_NO_REMOVE"}
    for start, end in folds:
        first = lines[start].split("(")[0].split()[0].upper() if lines[start].split() else ""
        assert first in openers or first.startswith("#"), lines[start]


# ------------------------------------------------------------------ outline


def test_outline_main_cr6():
    result = _analyze(MAIN)
    assert result.ok
    symbols = document_symbols(result, MAIN)
    top = {s.name: s for s in symbols}
    text_lines = MAIN.read_text(encoding="latin-1").split("\n")

    prog = top["BeginProg"]
    assert prog.kind == "beginprog"
    assert text_lines[prog.range.start_line].strip() == "BeginProg"
    assert text_lines[prog.range.end_line].strip() == "EndProg"
    assert prog.selection == Span(prog.range.start_line, 0, prog.range.start_line, None)
    scans = [c for c in prog.children if c.kind == "scan"]
    assert scans and scans[0].detail.startswith("Scan")
    # Main.CR6 has four SlowSequence blocks; the fourth is inside an inactive `#If`
    assert [c.kind for c in prog.children].count("slowsequence") == 3
    seq = [c for c in prog.children if c.kind == "slowsequence"][0]
    assert [c.kind for c in seq.children] == ["scan"]

    functions = [s for s in symbols if s.kind == "function"]
    assert functions
    fn = functions[0]
    assert text_lines[fn.range.start_line].lstrip().lower().startswith("function " + fn.name.lower())
    assert text_lines[fn.range.end_line].strip().lower() == "endfunction"
    table = top["StationStatus"]
    assert table.kind == "datatable"
    assert [c.kind for c in table.children] and all(c.kind == "output" for c in table.children)
    assert "Sample" in [c.name for c in table.children]
    ct = top["GlobalConfigTable"]
    assert ct.kind == "consttable" and ct.children and all(c.kind == "const" for c in ct.children)
    publics = [s for s in symbols if s.kind == "public"]
    assert {"PTemp", "Batt_volt"} <= {s.name for s in publics}
    # included files' symbols are not in this file's outline
    assert "StationProfile" not in {s.name for s in _flat(symbols)}
    # no overlapping top-level ranges
    flat = sorted(symbols, key=lambda s: (s.range.start_line, s.range.end_line))
    for a, b in zip(flat, flat[1:]):
        assert a.range.end_line < b.range.start_line or (
            a.range.start_line == b.range.start_line == a.range.end_line == b.range.end_line)


def test_outline_of_an_include_file():
    result = _analyze(MAIN)
    lib = CRBASIC / "PowerProfileLib.crb"
    symbols = document_symbols(result, lib)
    assert symbols
    names = {s.name for s in _flat(symbols)}
    assert "SeqTickSecs" in names
    assert len({s.occurrence for s in symbols}) == 1
    lines = lib.read_text(encoding="latin-1").split("\n")
    for s in _flat(symbols):
        assert 0 <= s.range.start_line <= s.range.end_line < len(lines)


def test_outline_sub_parameters_and_details(tmp_path):
    entry = _write(tmp_path, "p.CR6",
                   "Public x As Long\n"
                   "Const K = 3\n"
                   "Alias x = ax\n"
                   "Sub Add(a As Long, b)\n"
                   "  Dim local\n"
                   "  x = a + b\n"
                   "EndSub\n"
                   "Function Twice(v As Float) As Float\n"
                   "  Return v * 2\n"
                   "EndFunction\n"
                   "StructureType Pair\n"
                   "  A As Long\n"
                   "  B As Float\n"
                   "EndStructureType\n"
                   "BeginProg\n"
                   "  Scan(1,Sec,0,0)\n"
                   "    Call Add(1, 2)\n"
                   "  NextScan\n"
                   "EndProg\n")
    result = _analyze(entry)
    symbols = document_symbols(result, entry)
    by_name = {s.name: s for s in symbols}
    assert by_name["x"].kind == "public" and by_name["x"].detail == "Long"
    assert by_name["K"].kind == "const"
    assert by_name["ax"].kind == "alias"
    add = by_name["Add"]
    assert add.kind == "sub"
    assert (add.range.start_line, add.range.end_line) == (3, 6)
    assert add.detail == "(a As Long, b)"
    assert [(c.name, c.kind) for c in add.children] == [("a", "parameter"), ("b", "parameter")]
    assert "local" not in {s.name for s in _flat(symbols)}       # routine locals are not listed
    twice = by_name["Twice"]
    assert twice.kind == "function" and twice.detail == "(v As Float) As Float"
    pair = by_name["Pair"]
    assert pair.kind == "structuretype"
    assert [(c.name, c.kind) for c in pair.children] == [("A", "member"), ("B", "member")]
    assert (pair.range.start_line, pair.range.end_line) == (10, 13)
    assert by_name["BeginProg"].range.end_line == 18


def test_outline_inactive_if_has_no_symbols(tmp_path):
    entry = _write(tmp_path, "p.CR6",
                   "#If False\nPublic hidden\nSub Dead()\nEndSub\n#Else\nPublic shown\n#EndIf\n"
                   "BeginProg\nEndProg\n")
    names = {s.name for s in _flat(document_symbols(_analyze(entry), entry))}
    assert "shown" in names and "hidden" not in names and "Dead" not in names


def test_outline_unclosed_block_has_its_header_only(tmp_path):
    entry = _write(tmp_path, "p.CR6", "Public x\nSub Open()\n  x = 1\n")
    symbols = document_symbols(_analyze(entry), entry)
    sub = next(s for s in symbols if s.name == "Open")
    assert sub.range == sub.selection == Span(1, 0, 1, None)


def test_outline_continuation_header_spans_all_physical_lines(tmp_path):
    entry = _write(tmp_path, "p.CR6",
                   "Public a, _\n  b\nSub F(p, _\n      q)\nEndSub\nBeginProg\nEndProg\n")
    symbols = document_symbols(_analyze(entry), entry)
    by_name = {s.name: s for s in symbols}
    assert by_name["a"].range == Span(0, 0, 1, None)
    assert by_name["F"].selection == Span(2, 0, 3, None)
    assert (by_name["F"].range.start_line, by_name["F"].range.end_line) == (2, 4)


def test_outline_file_included_twice_has_one_entry_per_occurrence(tmp_path):
    _write(tmp_path, "lib.crb", "Sub Shared()\nEndSub\nConst C = 1\n")
    entry = _write(tmp_path, "p.CR6",
                   'IncludeSection("A","CPU:lib.crb")\nIncludeSection("B","CPU:lib.crb")\n'
                   "BeginProg\nEndProg\n")
    result = _analyze(entry)
    lib = tmp_path / "lib.crb"
    symbols = document_symbols(result, lib)
    assert [s.name for s in symbols].count("Shared") == 2
    occurrences = [s.occurrence for s in symbols if s.name == "Shared"]
    assert len(set(occurrences)) == 2
    same = [s for s in symbols if s.name == "Shared"]
    assert same[0].range == same[1].range == Span(0, 0, 1, None)
    assert [s.name for s in symbols].count("C") == 2
    assert [s.name for s in document_symbols(result, entry)] == ["BeginProg"]


def test_outline_unknown_path_and_no_program(tmp_path):
    result = _analyze(MAIN)
    assert document_symbols(result, tmp_path / "nothing.crb") == []
    entry = _write(tmp_path, "bad.CR6", "")
    assert document_symbols(_analyze(entry), entry) == []


def test_outline_crlf(tmp_path):
    entry = _write(tmp_path, "p.CR6", "Public x\nSub S()\n x = 1\nEndSub\nBeginProg\nEndProg\n",
                   newline="\r\n")
    by_name = {s.name: s for s in document_symbols(_analyze(entry), entry)}
    assert (by_name["S"].range.start_line, by_name["S"].range.end_line) == (1, 3)


# ------------------------------------------------------------------ include links


def test_include_links_main_cr6():
    result = _analyze(MAIN)
    links = include_links(result)
    assert links
    main_lines = MAIN.read_bytes().decode("latin-1").split("\n")
    in_main = [l for l in links if l.file == MAIN or editing._same_path(l.file, MAIN)]
    assert in_main
    for link in in_main:
        assert link.keyword == "IncludeSection"
        line = main_lines[link.range.start_line]
        assert link.range.start_line == link.range.end_line
        assert line[link.range.start_col:link.range.end_col] == link.spec
        assert link.target is not None and link.target.name == link.spec.split(":")[-1]
        assert link.target.is_file()
    assert {l.spec for l in in_main} >= {"CPU:StationConfig.crb", "CPU:PowerProfileLib.crb"}


def test_include_links_missing_and_forms(tmp_path):
    _write(tmp_path, "present.crb", "Public p\n")
    entry = _write(tmp_path, "p.CR6",
                   'Include("CPU:Present.crb")\n'
                   '  includesection ( "Sect" , "CPU:absent.crb" )  \' trailing\n'
                   "IncludeSection(\"S2\", _\n   \"CPU:present.crb\")\n"
                   'Public q\n'
                   "BeginProg\nEndProg\n")
    result = _analyze(entry)
    links = include_links(result)
    lines = entry.read_text().split("\n")
    assert [(l.keyword, l.spec) for l in links] == [
        ("Include", "CPU:Present.crb"), ("includesection", "CPU:absent.crb"),
        ("IncludeSection", "CPU:present.crb")]
    for l in links:
        assert lines[l.range.start_line][l.range.start_col:l.range.end_col] == l.spec
    assert links[0].target == tmp_path / "present.crb"       # resolved case-insensitively
    assert links[1].target is None                          # missing
    assert links[2].range.start_line == 3                   # on the continuation line
    assert links[2].target == tmp_path / "present.crb"


def test_include_links_use_overlays(tmp_path):
    entry = _write(tmp_path, "p.CR6", "BeginProg\nEndProg\n")
    edited = b'Include("CPU:new.crb")\nBeginProg\nEndProg\n'
    result = analyze(AnalysisRequest(entry, overlays={entry: edited}))
    assert [l.spec for l in include_links(result)] == ["CPU:new.crb"]


def test_include_links_file_included_twice_is_listed_once(tmp_path):
    _write(tmp_path, "lib.crb", "Public z\n")
    entry = _write(tmp_path, "p.CR6",
                   'Include("CPU:lib.crb")\nInclude("CPU:lib.crb")\nBeginProg\nEndProg\n')
    links = include_links(_analyze(entry))
    assert [(l.range.start_line, l.spec) for l in links] == [(0, "CPU:lib.crb"), (1, "CPU:lib.crb")]


def test_include_links_crlf_and_latin1(tmp_path):
    entry = _write(tmp_path, "p.CR6", '\' \xe9\xe8\nInclude("CPU:\xe9.crb")\nBeginProg\nEndProg\n',
                   newline="\r\n")
    (link,) = include_links(_analyze(entry))
    assert link.spec == "CPU:\xe9.crb" and link.target is None
    line = entry.read_bytes().decode("latin-1").split("\r\n")[1]
    assert line[link.range.start_col:link.range.end_col] == link.spec


# ------------------------------------------------------------------ completion


def _complete(text, line, col, result=None, **kw):
    return completion_candidates(result, text, line, col, "cr6", **kw)


def _labels(c):
    return [i.label for i in c.items]


def test_completion_prefix_is_case_insensitive_with_profile_spelling():
    c = _complete("  volt", 0, 6)
    labels = _labels(c)
    assert "VoltSe" in labels and "VoltDiff" in labels
    assert all(l.lower().startswith("volt") for l in labels)
    assert c.replace == Span(0, 2, 0, 6) and c.prefix == "volt"
    item = next(i for i in c.items if i.label == "VoltSe")
    assert (item.kind, item.source) == ("instruction", "catalog")
    assert item.param_count == len(InstructionCatalog.for_profile("cr6").get("VoltSe").params) > 0


def test_completion_keyword_beats_instruction_row():
    c = _complete("EndI", 0, 4)
    kinds = {i.label: i.kind for i in c.items}
    assert kinds["EndIf"] == "keyword"
    assert _complete("Sel", 0, 3).items[0].kind in ("keyword", "instruction")
    sel = next(i for i in _complete("Sel", 0, 3).items if i.label.lower() == "select")
    assert sel.kind == "keyword"


def test_completion_functions_symbols_and_counts():
    c = _complete("x = Abs", 0, 7)
    abs_item = next(i for i in c.items if i.label.lower() == "abs")
    assert abs_item.kind == "function" and abs_item.param_count == 1
    c = _complete("Public x As Lo", 0, 14)
    assert "long" in [l.lower() for l in _labels(c)]
    sym = next(i for i in _complete("Volt10", 0, 6).items if i.kind == "symbol")
    assert sym.param_count is None


def test_completion_positions_in_the_middle_and_at_boundaries():
    c = _complete("Volt", 0, 2)                       # the prefix is what precedes the cursor
    assert c.prefix == "Vo" and c.replace == Span(0, 0, 0, 2)
    assert "VoltSe" in _labels(c) and all(l.lower().startswith("vo") for l in _labels(c))
    c = _complete("x = 1 + ", 0, 8)
    assert c.prefix == "" and c.replace == Span(0, 8, 0, 8) and len(c.items) > 100
    assert _complete("12ab", 0, 4).prefix == ""        # `ab` is the end of a number, not a name
    # out-of-range positions are clamped
    assert _complete("Vol", 5, 99).replace == Span(5, 0, 5, 0)
    assert _complete("Vol", 0, 99).prefix == "Vol"


def test_completion_none_inside_strings_and_comments():
    assert _complete('x = "Volt', 0, 9).items == ()
    assert _complete('x = "a""Vol', 0, 11).items == ()
    assert _complete("x = 1 ' Vol", 0, 11).items == ()
    assert _complete("' Vol", 0, 5).items == ()
    # after the string closed, or the quote inside a comment later on the line
    assert _complete('x = "a" & Vol', 0, 13).items
    assert _complete('x = Vol \' "', 0, 7).items


def test_completion_dollar_names():
    labels = _labels(_complete("s = Chr", 0, 7))
    assert any(l.lower().startswith("chr") for l in labels)


def test_completion_program_names_scoped_to_the_routine(tmp_path):
    src = ("Public Volcano As Long\n"          # 0
           "Const VoltLimit = 3\n"             # 1
           "Sub Mix(volume As Long)\n"         # 2
           "  Dim volLocal\n"                  # 3
           "  vol\n"                           # 4
           "EndSub\n"                          # 5
           "Sub Other(volOther)\n"             # 6
           "  vol\n"                           # 7
           "EndSub\n"                          # 8
           "BeginProg\n"                       # 9
           "  vol\n"                           # 10
           "EndProg\n")
    entry = _write(tmp_path, "p.CR6", src)
    result = _analyze(entry)
    kinds = {i.label: i.kind for i in _complete(src, 4, 5, result).items if i.source == "program"}
    assert kinds == {"Volcano": "variable", "VoltLimit": "constant", "volume": "parameter",
                     "volLocal": "variable"}
    names4 = {i.label for i in _complete(src, 4, 5, result).items if i.source == "program"}
    names7 = {i.label for i in _complete(src, 7, 5, result).items if i.source == "program"}
    names10 = {i.label for i in _complete(src, 10, 5, result).items if i.source == "program"}
    assert names4 == {"Volcano", "VoltLimit", "volume", "volLocal"}
    assert names7 == {"Volcano", "VoltLimit", "volOther"}
    assert names10 == {"Volcano", "VoltLimit"}
    # program names are listed before catalog names
    items = _complete(src, 10, 5, result).items
    assert [i.source for i in items] == sorted((i.source for i in items), key=lambda s: s != "program")
    # sub and function names are global
    all_names = {i.label for i in _complete(src, 10, 0, result).items if i.source == "program"}
    assert {"Mix", "Other"} <= all_names


def test_completion_unclosed_routine_scope_follows_the_text(tmp_path):
    entry = _write(tmp_path, "p.CR6", "Sub Mix(volume)\n  x = 1\nEndSub\nBeginProg\nEndProg\n")
    result = _analyze(entry)
    edited = "Sub Mix(volume)\n  vol\n"          # EndSub deleted: the result is stale
    assert "volume" in _labels(_complete(edited, 1, 5, result))
    assert "volume" not in _labels(_complete("Sub Mix(volume)\nEndSub\nvol\n", 2, 3, result))


def test_completion_declared_argument_replaces_the_ast_walk():
    c = _complete("vol", 0, 3, None, declared=[("volA", "variable", None), ("volB", "parameter", "R"),
                                              ("volC", "variable", "r")])
    program = [(i.label, i.kind) for i in c.items if i.source == "program"]
    assert program == [("volA", "variable")]
    c = _complete("Sub R()\n vol\nEndSub\n", 1, 4, None,
                  declared=[("volA", "variable", None), ("volB", "parameter", "R"),
                            ("volC", "variable", "other")])
    assert [i.label for i in c.items if i.source == "program"] == ["volA", "volB"]


def test_completion_limit_and_is_incomplete_flag():
    full = editing._catalog_items("cr6")
    assert len(full) > editing.COMPLETION_LIMIT
    c = _complete("x = 1 + ", 0, 8)                    # empty prefix: every catalog name matches
    assert len(c.items) == editing.COMPLETION_LIMIT and c.is_incomplete
    assert c.items == full[:editing.COMPLETION_LIMIT]   # the first names in completion order
    narrow = _complete("Volt", 0, 4)
    assert not narrow.is_incomplete and len(narrow.items) < editing.COMPLETION_LIMIT
    assert _complete("x = \"", 0, 5).is_incomplete is False   # inside a string: nothing, complete


def test_completion_limit_keeps_declared_names_first():
    declared = [(f"Volume{k:03d}", "variable", None) for k in range(250)]
    c = _complete("x = Vol", 0, 7, declared=declared)
    assert len(c.items) == editing.COMPLETION_LIMIT and c.is_incomplete
    assert all(i.source == "program" for i in c.items)  # 250 declared names fill the list
    c = _complete("x = 1 + ", 0, 8, declared=declared[:3])
    assert [i.source for i in c.items[:3]] == ["program"] * 3
    assert c.is_incomplete and all(i.source == "catalog" for i in c.items[3:])


def test_completion_limit_applies_to_members(tmp_path):
    # one declaration per line: a physical line is limited to 510 characters
    count = editing.COMPLETION_LIMIT + 5
    decls = "".join(f"Public Var{k:03d}\n" for k in range(count))

    def members(tail):
        src = decls + f"BeginProg\n  {tail}\nEndProg\n"
        return _complete(src, count + 1, 2 + len(tail), _analyze(_write(tmp_path, "p.CR6", src)))

    c = members("Public.")
    assert c.member_of == "Public" and len(c.items) == editing.COMPLETION_LIMIT and c.is_incomplete
    c = members("Public.Var")                           # 205 names match `Var`
    assert c.prefix == "Var" and len(c.items) == editing.COMPLETION_LIMIT and c.is_incomplete
    c = members("Public.Var20")                         # Var200 .. Var204
    assert c.prefix == "Var20" and not c.is_incomplete
    assert [i.label for i in c.items] == [f"Var{k}" for k in range(200, 205)]


def test_catalog_items_are_cached_per_profile():
    assert editing._catalog_items("cr6") is editing._catalog_items("cr6")
    assert isinstance(editing._catalog_items("cr6"), tuple)
    labels = [(i.label.lower(), i.label) for i in editing._catalog_items("cr6")]
    assert labels == sorted(labels)


def test_completion_without_a_result_is_catalog_only():
    c = _complete("Vol", 0, 3, None)
    assert c.items and all(i.source == "catalog" for i in c.items)


def test_completion_members(tmp_path):
    src = ("Public Batt, Temp(2)\n"
           "DataTable(Hourly,True,-1)\n"
           "  Sample(1,Batt,FP2)\n"
           "  Average(1,Temp(1),FP2,False)\n"
           "EndTable\n"
           "BeginProg\n"
           "  Scan(1,Sec,0,0)\n"
           "    Hourly.\n"
           "    Status.Bat\n"
           "    Public.Te\n"
           "    Nothing.\n"
           "    x = 1.\n"
           "  NextScan\n"
           "EndProg\n")
    entry = _write(tmp_path, "p.CR6", src)
    result = _analyze(entry)
    c = _complete(src, 7, 11, result)
    assert c.member_of == "Hourly" and c.prefix == ""
    assert {"Batt", "Temp_Avg"} <= set(_labels(c))
    assert all(i.kind == "field" and i.source == "member" for i in c.items)
    c = _complete(src, 8, 14, result)
    assert c.member_of == "Status" and _labels(c) == ["Battery"]
    assert c.replace == Span(8, 11, 8, 14)
    c = _complete(src, 9, 13, result)
    assert c.member_of == "Public" and _labels(c) == ["Temp"]
    assert _complete(src, 10, 12, result).items == ()          # unknown table
    assert _complete(src, 11, 11, result).items == ()          # a number, not a name
    # members need the analysed program
    assert _complete(src, 7, 11, None).items == ()


def test_completion_member_names_ignore_case_and_spaces(tmp_path):
    src = "Public Batt\nBeginProg\n  status . bat\nEndProg\n"
    entry = _write(tmp_path, "p.CR6", src)
    c = _complete(src, 2, 14, _analyze(entry))
    assert _labels(c) == ["Battery"]


def test_completion_crlf_text(tmp_path):
    src = "Public Volcano\r\nBeginProg\r\n  Vol\r\nEndProg\r\n"
    entry = _write(tmp_path, "p.CR6", src)
    c = _complete(src, 2, 5, _analyze(entry))
    assert c.replace == Span(2, 2, 2, 5)
    assert "Volcano" in _labels(c)


def test_declared_names_from_the_ast():
    program = parser.parse_text(
        "Public a, b(2)\nDim c\nConst K = 1\nAlias a = al\nStructureType T\n m As Long\n"
        "EndStructureType\nDataTable(Tab,True,1)\nEndTable\nConstTable(CT)\nEndConstTable\n"
        "Sub S(p)\n Dim loc\nEndSub\nFunction F(q) As Long\nEndFunction\n", "t.CR6")[0]
    names = {(n.name, n.kind, n.scope) for n in declared_names(program)}
    assert names >= {("a", "variable", None), ("b", "variable", None), ("c", "variable", None),
                     ("K", "constant", None), ("al", "alias", None), ("T", "type", None),
                     ("Tab", "table", None), ("CT", "table", None), ("S", "sub", None),
                     ("p", "parameter", "S"), ("loc", "variable", "S"),
                     ("F", "user_function", None), ("q", "parameter", "F")}
    assert declared_names(None) == []


# ------------------------------------------------------------------ never raises


def _sweep_text(text: str) -> None:
    folding_ranges(text)
    folding_ranges(text, "cr300")
    lines = text.split("\n")
    for n in range(0, len(lines), max(1, len(lines) // 12)):
        for col in (0, 1, len(lines[n]) // 2, len(lines[n])):
            completion_candidates(None, text, n, col)


@pytest.mark.parametrize("path", sorted(CRBASIC.glob("*.CR6")) + sorted(CRBASIC.glob("*.crb")),
                         ids=lambda p: p.name)
def test_never_raises_on_crbasic(path, memo_analyze):
    text = path.read_bytes().decode("latin-1")
    _sweep_text(text)
    _sweep_text(text.replace("\n", "\r\n"))
    step = max(1, len(text) // 25)
    for cut in range(0, len(text), step):
        _sweep_text_light(text[:cut])
    if path.suffix.upper() == ".CR6":
        result = memo_analyze(AnalysisRequest(path))
        document_symbols(result, path)
        include_links(result)
        for n in range(0, text.count("\n"), 40):
            completion_candidates(result, text, n, 3)
            completion_candidates(result, text, n, 3, "cr6")


def _sweep_text_light(text: str) -> None:
    folding_ranges(text)
    lines = text.split("\n")
    completion_candidates(None, text, len(lines) - 1, len(lines[-1]))


def test_never_raises_on_chm_corpus():
    assert CHM
    for path in CHM:
        text = path.read_bytes().decode("latin-1")
        folds = folding_ranges(text)
        for f in folds:
            assert 0 <= f.start_line < f.end_line < text.count("\n") + 1
        lines = text.split("\n")
        mid = len(lines) // 2
        completion_candidates(None, text, mid, min(4, len(lines[mid])))


def test_chm_corpus_outline_and_links_sample(memo_analyze):
    for path in CHM[:: max(1, len(CHM) // 60)]:
        result = memo_analyze(AnalysisRequest(path))
        symbols = document_symbols(result, path)
        total = len(path.read_bytes().split(b"\n"))
        for s in _flat(symbols):
            assert 0 <= s.selection.start_line <= s.selection.end_line < total, path
            assert s.range.start_line <= s.range.end_line < total, path
        for link in include_links(result):
            assert link.range.start_line == link.range.end_line
        completion_candidates(result, path.read_bytes().decode("latin-1"), 2, 2)


def test_chm_folds_equal_outline_blocks_where_the_program_compiles(memo_analyze):
    checked = 0
    for path in CHM[:: max(1, len(CHM) // 80)]:
        result = memo_analyze(AnalysisRequest(path))
        if not result.ok:
            continue
        text = path.read_bytes().decode("latin-1")
        folds = {(f.start_line, f.end_line) for f in folding_ranges(text) if f.kind == "region"}
        for s in _flat(document_symbols(result, path)):
            if s.kind in ("sub", "function", "datatable", "consttable") and s.range.end_line > s.range.start_line + 1:
                assert (s.range.start_line, s.range.end_line - 1) in folds, (path.name, s.name)
                checked += 1
    assert checked > 10


def test_quoted_datatable_name_is_listed_declared_and_completed(tmp_path):
    src = ('Public x\nDataTable("Hist",True,1)\n Sample(1,x,FP2)\nEndTable\n'
           'BeginProg\n Scan(1,Sec,0,0)\n CallTable("Hist")\n NextScan\nEndProg\n')
    entry = _write(tmp_path, "q.CR6", src)
    result = _analyze(entry)
    assert result.ok
    assert [(s.kind, s.name) for s in document_symbols(result, entry) if s.kind == "datatable"] == [
        ("datatable", "Hist")]
    assert ("Hist", "table", None) in {(n.name, n.kind, n.scope) for n in declared_names(result.program)}
    assert [i.label for i in _complete("Hi", 0, 2, result).items if i.source == "program"] == ["Hist"]


@pytest.mark.parametrize("end", ["EndSub", "End Sub"])
def test_declared_names_close_routine_scope_for_two_word_end(tmp_path, end):
    entry = _write(tmp_path, "s.CR6", f"Public x\nSub S()\n Dim x\n x=1\n{end}\nPublic z\n"
                                      "BeginProg\nEndProg\n")
    names = {(n.name, n.scope) for n in declared_names(_analyze(entry).program)}
    assert ("z", None) in names and ("x", None) in names and ("x", "S") in names



# ------------------------------------------------------------------ scan cache (LSP-03d)


def _uncached_scan(monkeypatch):
    """Route `editing._scan` to the uncached function for the duration of a test."""
    monkeypatch.setattr(editing, "_scan", lambda text, profile: tuple(
        editing._scan_uncached(text, profile)))


def test_scan_cache_returns_the_uncached_result_on_main_cr6():
    text = MAIN.read_bytes().decode("latin-1")
    editing._scan.cache_clear()
    cold = editing._scan(text, "cr6")
    assert cold == tuple(editing._scan_uncached(text, "cr6"))
    assert editing._scan(text, "cr6") is cold           # the second call is a cache hit


def test_scan_cache_is_keyed_by_text_and_profile():
    text = "Sub A()\nEndSub\n"
    editing._scan.cache_clear()
    assert editing._scan(text, "cr6") != editing._scan(text + "Sub B()\n", "cr6")
    assert editing._scan(text, "cr6") == tuple(editing._scan_uncached(text, "cr6"))
    assert editing._scan(text, "cr300") == tuple(editing._scan_uncached(text, "cr300"))
    assert editing._scan.cache_info().currsize == 3


def test_scan_cache_is_bounded():
    editing._scan.cache_clear()
    for k in range(editing._SCAN_CACHE_SIZE + 5):
        editing._scan(f"Sub S{k}()\nEndSub\n", "cr6")
    assert editing._scan.cache_info().currsize == editing._SCAN_CACHE_SIZE


def test_scan_cache_follows_an_edited_buffer():
    # the same routine with its closer, then without: a stale hit would keep the old scope
    closed = "Public x\nSub S()\n Dim y\nEndSub\nBeginProg\n x = y\nEndProg\n"
    open_ = "Public x\nSub S()\n Dim y\nBeginProg\n x = y\nEndProg\n"
    assert folding_ranges(closed) != folding_ranges(open_)
    assert folding_ranges(closed) == folding_ranges(closed)
    declared = [("x", "variable", None), ("y", "variable", "S")]   # the routine's local `y`
    assert [i.label for i in _complete(closed, 5, 6, declared=declared).items
            if i.source == "program"] == []
    assert [i.label for i in _complete(open_, 4, 6, declared=declared).items
            if i.source == "program"] == ["y"]


@pytest.mark.slow
def test_completion_on_main_cr6_cached_equals_uncached(monkeypatch, memo_analyze):
    # every line (at its end) and every fifth line (mid-line): the candidates through the cache
    # must equal those through the uncached scan, position by position
    text = MAIN.read_bytes().decode("latin-1")
    result = memo_analyze(AnalysisRequest(MAIN))
    lines = text.split("\n")
    positions = [(n, len(l)) for n, l in enumerate(lines)]
    positions += [(n, len(l) // 2) for n, l in enumerate(lines) if n % 5 == 0 and len(l) > 1]
    editing._scan.cache_clear()
    cached = [_complete(text, n, c, result) for n, c in positions]
    assert editing._scan.cache_info().hits > 0
    _uncached_scan(monkeypatch)
    uncached = [_complete(text, n, c, result) for n, c in positions]
    assert cached == uncached


# ------------------------------------------------------------------ LSP-R3d (review F10, F11, F13)


def test_completion_string_state_goes_on_across_a_continuation():
    src = 's = "a, _\n     b" + Vol'
    assert _complete(src, 1, len('     b" + Vol')).items
    assert _complete('s = "a, _\n     Vol', 1, len("     Vol")).items == ()
    assert _complete('s = "a, _\n     \' Vol', 1, len("     ' Vol")).items == ()


def test_catalog_items_are_identifiers_only():
    import re
    for profile in ("cr6", "cr300"):
        for item in editing._catalog_items(profile):
            assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\$?", item.label), item.label
    labels = _labels(_complete("", 0, 0))
    assert "<<" not in labels and ">>" not in labels


def test_completion_routine_local_shadows_global_once():
    src = "Public x\nSub Foo\n  Dim x\n  \nEndSub\n"
    declared = [("x", "variable", None), ("X", "variable", "Foo"), ("y", "variable", None)]
    got = [i for i in _complete(src, 3, 2, declared=declared).items if i.source == "program"]
    assert [i.label for i in got] == ["X", "y"]
    # outside the routine only the global is visible
    assert [i.label for i in _complete(src, 0, 0, declared=declared).items
            if i.source == "program"] == ["x", "y"]


def test_completion_members_from_a_broken_or_stale_program(tmp_path):
    # LSP-R4b: an incomplete `x = Status.` still parses to a program whose tables give members,
    # and a stale program of the valid text serves the edited text; only no program gives none
    valid = "Public Temp\nBeginProg\n  Scan(1,Sec,0,0)\n    Temp = 1\n  NextScan\nEndProg\n"
    for name, member in (("Status", "SkippedScan"), ("Public", "Temp")):
        broken = valid.replace("    Temp = 1\n", f"    Temp = 1\n    x = {name}.\n")
        col = len(f"    x = {name}.")
        own = _analyze(_write(tmp_path, "b.CR6", broken))
        assert own.program is not None
        stale = _analyze(_write(tmp_path, "v.CR6", valid))
        for result in (own, stale):
            c = _complete(broken, 4, col, result)
            assert c.member_of == name and member in _labels(c)
        assert _complete(broken, 4, col, None).items == ()
