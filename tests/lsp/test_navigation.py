"""LSP-05b-core: protocol-independent navigation (`opencrbasic.navigation`). Policies:
docs/lsp-api.md "Navigation (LSP-05b-core)"."""

from .assertions import required
import shutil
from pathlib import Path

import pytest

from opencrbasic import navigation as N
from opencrbasic import positions
from opencrbasic.analysis import AnalysisRequest, analyze

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT.parent / "crbasic").resolve()


def write(tmp_path: Path, name: str, text: str, newline: str = "\n") -> Path:
    p = tmp_path / name
    p.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return p


def run(entry: Path, logger: str = "cr6"):
    return analyze(AnalysisRequest(entry, logger))


def pos(path: Path, needle: str, nth: int = 0, offset: int = 0) -> tuple[int, int]:
    """(0-based line, decoded column) of the `nth` occurrence of `needle` in the file's bytes."""
    lines = path.read_bytes().replace(b"\r\n", b"\n").split(b"\n")
    n = needle.encode("utf-8")
    seen = 0
    for i, line in enumerate(lines):
        start = 0
        while (j := line.find(n, start)) != -1:
            if seen == nth:
                return i, j + offset
            seen += 1
            start = j + 1
    raise AssertionError(f"{needle!r} #{nth} not found")


def where(locs):
    return [(l.path.name if l.path else None, l.line, l.start, l.end) for l in locs]


SAMPLE = """Public a, Arr(3)
Const K = 5
Sub Foo(p As Long)
  Dim a
  a = p + K
EndSub
BeginProg
  Scan(1,Sec,0,0)
    a = Arr(1) + K
    Foo(a)
  NextScan
EndProg
"""


@pytest.fixture
def sample(tmp_path):
    p = write(tmp_path, "m.CR6", SAMPLE)
    return p, run(p)


def test_global_local_shadowing(sample):
    p, r = sample
    glob_decl = pos(p, "a", 0, 0)                     # "Public a"
    # reference in BeginProg resolves to the global
    line, col = pos(p, "    a = Arr")[0], 4
    d = N.definition(r, p, line, col)
    assert where(d) == [("m.CR6", 0, 7, 8)]
    # inside the Sub, `a = p + K` is the local Dim
    d = N.definition(r, p, 4, 2)
    assert where(d) == [("m.CR6", 3, 6, 7)]
    refs = N.references(r, p, 0, 7)
    assert (3, 6, 7) not in [(l.line, l.start, l.end) for l in refs]
    assert [(l.line, l.start) for l in refs] == [(0, 7), (8, 4), (9, 8)]
    refs = N.references(r, p, 3, 6, include_declaration=False)
    assert [(l.line, l.start) for l in refs] == [(4, 2)]
    assert glob_decl == (0, 7)


def test_parameter_and_constant(sample):
    p, r = sample
    assert where(N.definition(r, p, 4, 6)) == [("m.CR6", 2, 8, 9)]            # p -> parameter
    refs = N.references(r, p, 1, 6)                                            # Const K
    assert [(l.line, l.start) for l in refs] == [(1, 6), (4, 10), (8, 17)]
    hl = N.document_highlights(r, p, 1, 6)
    assert [(h.line, h.start, h.kind) for h in hl] == [(1, 6, "write"), (4, 10, "read"), (8, 17, "read")]


def test_highlight_kinds(sample):
    p, r = sample
    hl = N.document_highlights(r, p, 8, 4)                                     # global a
    kinds = {(h.line, h.start): h.kind for h in hl}
    assert kinds[(0, 7)] == "write" and kinds[(8, 4)] == "write"
    assert kinds[(9, 8)] in ("read", "text")


def test_position_just_after_name_and_off_name(sample):
    p, r = sample
    assert where(N.definition(r, p, 8, 5)) == [("m.CR6", 0, 7, 8)]            # end of `a`
    assert N.definition(r, p, 8, 6) == []                                      # on the space/`=`


def test_semantic_tokens_legend(sample):
    p, r = sample
    toks = N.semantic_tokens(r, p)
    by = {(t.line, t.start): t for t in toks}
    assert by[(0, 7)].type == "variable" and by[(0, 7)].modifiers == ("declaration",)
    assert by[(1, 6)].modifiers == ("declaration", "readonly")
    assert by[(2, 4)].type == "function" and by[(2, 8)].type == "parameter"
    assert toks == sorted(toks)
    for t in toks:
        assert t.type in N.TOKEN_TYPES and set(t.modifiers) <= set(N.TOKEN_MODIFIERS)


def test_cross_include_definition_and_references(tmp_path):
    inc = write(tmp_path, "inc.CR6", "Public shared\nSub Helper()\nEndSub\n")
    m = write(tmp_path, "m.CR6",
              'Include "inc.CR6"\nBeginProg\n  Scan(1,Sec,0,0)\n    shared = 1\n    Helper()\n'
              '  NextScan\nEndProg\n')
    r = run(m)
    d = N.definition(r, m, 3, 6)
    assert where(d) == [("inc.CR6", 0, 7, 13)]
    assert d[0].path == inc
    refs = N.references(r, inc, 0, 8)
    assert where(refs) == [("inc.CR6", 0, 7, 13), ("m.CR6", 3, 4, 10)]
    assert where(N.definition(r, m, 4, 5)) == [("inc.CR6", 1, 4, 10)]
    hl = N.document_highlights(r, inc, 0, 8)                                   # same file only
    assert [(h.line, h.start) for h in hl] == [(0, 7)]
    assert [(t.line, t.start, t.type) for t in N.semantic_tokens(r, inc)] == [
        (0, 7, "variable"), (1, 4, "function")]


def test_alias_and_table_and_structure(tmp_path):
    m = write(tmp_path, "m.CR6", """StructureType Pt
  X As Float
  Y As Float
EndStructureType
Public v As Pt
Public raw
Alias raw = nice
Dim idx
DataTable(Tab,True,-1)
  Sample(1,v.X,FP2)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    v.X = 1
    nice = 3
    CallTable Tab
  NextScan
EndProg
""")
    r = run(m)
    assert where(N.definition(r, m, 14, 4)) == [("m.CR6", 6, 12, 16)]         # alias declared name
    refs = N.references(r, m, 6, 13, include_declaration=False)
    assert [(l.line, l.start) for l in refs] == [(14, 4)]
    assert where(N.definition(r, m, 15, 15)) == [("m.CR6", 8, 10, 13)]         # table
    assert where(N.definition(r, m, 13, 6)) == [("m.CR6", 1, 2, 3)]            # member X
    assert [(l.line, l.start) for l in N.references(r, m, 1, 2)] == [(1, 2), (9, 13), (13, 6)]
    types = {(t.line, t.start): t.type for t in N.semantic_tokens(r, m)}
    assert types[(0, 14)] == "struct" and types[(1, 2)] == "property"
    assert types[(8, 10)] == "namespace" and types[(4, 7)] == "variable"
    # catalog names have no location
    assert N.definition(r, m, 15, 6) == []                                     # CallTable
    assert N.definition(r, m, 12, 4) == []                                     # Scan


def test_alias_junk_token_is_not_the_declaration(tmp_path):
    m = write(tmp_path, "m.CR6", "Public arr(2)\nAlias arr(1) = a1 a2, a2\nBeginProg\n"
              "  Scan(1,Sec,0,0)\n    a2 = 1\n  NextScan\nEndProg\n")
    r = run(m)
    assert where(N.definition(r, m, *pos(m, "a2 = 1"))) == [("m.CR6", 1, 22, 24)]
    assert [(h.line, h.start) for h in N.document_highlights(r, m, *pos(m, "a2 = 1"))] == \
        [(1, 22), (4, 4)]


def test_continued_line(tmp_path):
    m = write(tmp_path, "m.CR6", "Public alpha, _\n  beta, _\n  gamma\nBeginProg\n  Scan(1,Sec,0,0)\n"
              "    gamma = beta + _\n      alpha\n  NextScan\nEndProg\n")
    r = run(m)
    assert where(N.definition(r, m, 5, 4)) == [("m.CR6", 2, 2, 7)]
    assert where(N.definition(r, m, 5, 12)) == [("m.CR6", 1, 2, 6)]
    assert where(N.definition(r, m, 6, 6)) == [("m.CR6", 0, 7, 12)]
    assert [(l.line, l.start) for l in N.references(r, m, 6, 6)] == [(0, 7), (6, 6)]


def test_colon_statement(tmp_path):
    m = write(tmp_path, "m.CR6", "Public a : Public b : Const c = 2\nBeginProg\n  a = 1 : b = c : a = b\nEndProg\n")
    r = run(m)
    assert where(N.definition(r, m, 2, 14)) == [("m.CR6", 0, 28, 29)]
    assert where(N.definition(r, m, 2, 18)) == [("m.CR6", 0, 7, 8)]
    assert where(N.definition(r, m, 2, 22)) == [("m.CR6", 0, 18, 19)]
    assert [(l.line, l.start) for l in N.references(r, m, 0, 18)] == [(0, 18), (2, 10), (2, 22)]


def test_ambiguous_returns_all_candidates(tmp_path):
    m = write(tmp_path, "m.CR6", "Public dup\nPublic dup\nBeginProg\n  dup = 1\nEndProg\n")
    r = run(m)
    d = N.definition(r, m, 3, 3)
    assert where(d) == [("m.CR6", 0, 7, 10), ("m.CR6", 1, 7, 10)]
    refs = N.references(r, m, 3, 3, include_declaration=False)
    assert where(refs) == [("m.CR6", 3, 2, 5)]


def test_file_included_twice_distinguishes_occurrences(tmp_path):
    inc = write(tmp_path, "inc.CR6", "Public twice\n")
    m = write(tmp_path, "m.CR6", 'Include "inc.CR6"\nInclude "inc.CR6"\nBeginProg\n  twice = 1\nEndProg\n')
    r = run(m)
    d = N.definition(r, m, 3, 3)
    assert where(d) == [("inc.CR6", 0, 7, 12)]            # one editor range, two loads
    assert d[0].occurrences == (1, 2)
    refs = N.references(r, inc, 0, 8)
    assert where(refs) == [("inc.CR6", 0, 7, 12), ("m.CR6", 3, 2, 7)]
    toks = N.semantic_tokens(r, inc)
    assert [(t.line, t.start) for t in toks] == [(0, 7)]


def test_inactive_if_name_gives_nothing(tmp_path):
    m = write(tmp_path, "m.CR6", "Public a\n#If False\n  Public hidden\n  hidden = a\n#EndIf\nBeginProg\n"
              "  a = 1\nEndProg\n")
    r = run(m)
    assert N.definition(r, m, 2, 9) == []
    assert N.definition(r, m, 3, 4) == []
    assert N.definition(r, m, 3, 13) == []                # `a` in the inactive branch: not indexed
    assert N.definition(r, m, 6, 2) != []


def test_unresolved_and_nonsense_positions(sample):
    p, r = sample
    for line, col in ((-1, 0), (500, 3), (0, 999), (0, -4), (1, 0)):
        assert N.definition(r, p, line, col) == []
        assert N.references(r, p, line, col) == []
        assert N.document_highlights(r, p, line, col) == []
    assert N.semantic_tokens(r, p.with_name("nothere.CR6")) == []


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_crlf_and_non_ascii_before_token(tmp_path, newline):
    m = write(tmp_path, "m.CR6",
              'Public s As String * 8\nPublic caf\nBeginProg\n  s = "é" : caf = 1 + caf\nEndProg\n',
              newline)
    r = run(m)
    line, col = pos(m, "caf", 1)                           # first `caf` in the code line
    assert (line, col) == (3, 13)                          # byte column: `é` is two bytes
    assert where(N.definition(r, m, line, col)) == [("m.CR6", 1, 7, 10)]
    refs = N.references(r, m, line, col)
    assert [(l.line, l.start) for l in refs] == [(1, 7), (3, 13), (3, 23)]
    data = m.read_bytes()
    (l0, c0), (l1, c1) = positions.lsp_range(data, positions.PhysicalRange("m.CR6", 0, 4, 13, 16))
    assert (l0, c0, c1) == (3, 12, 15)                     # editor column is one less than the byte column


def test_no_program_returns_empty(tmp_path):
    m = write(tmp_path, "m.CR6", "Public a\nBeginProg\nEndProg\n")
    r = run(m)
    nav = N.Navigation(r)
    assert nav.definition(m, 0, 7)
    broken = type("R", (), {"program": None, "request": r.request})()
    nav = N.Navigation(broken)
    assert nav.definition(m, 0, 7) == [] and nav.semantic_tokens(m) == []


@pytest.mark.slow
def test_never_raises_over_crbasic(tmp_path):
    files = sorted(CRBASIC.glob("*.CR6"))
    assert files
    work = tmp_path / "crbasic"
    shutil.copytree(CRBASIC, work, ignore=shutil.ignore_patterns("*.dat", "*.log"))
    resolved = declared = 0
    for f in files:
        p = work / f.name
        r = run(p)
        nav = N.Navigation(r)
        for t in nav.semantic_tokens(p):
            declared += 1
            locs = nav.definition(p, t.line, t.start)
            assert any(l.path == p and (l.line, l.start) == (t.line, t.start) for l in locs)
            nav.references(p, t.line, t.start + 1)
            nav.document_highlights(p, t.line, t.start)
        for s in nav._build():
            if s.path == p and not isinstance(s.rec, N.bindings.Declaration) and s.rec.resolved:
                resolved += 1
                got = nav.definition(p, s.line, s.start)
                assert got or all(d.scope == "predefined" or id(d) not in nav._site_of
                                  for d in s.rec.resolved)
                # the range holds the record's own spelling
                text = p.read_bytes().replace(b"\r\n", b"\n").split(b"\n")[s.line]
                assert text[s.start:s.end].decode("latin-1").upper() == s.rec.name.upper() or \
                    s.rec.name.upper() in ("", )
    assert declared > 100 and resolved > 100


@pytest.mark.parametrize("end", ["EndSub", "End Sub"])
def test_two_word_end_sub_scopes_the_main_program_name(tmp_path, end):
    src = (f"Public x\nSub S()\n Dim x\n x=1\n{end}\nPublic z\n"
           "BeginProg\n Scan(1,Sec,0,0)\n  x=2\n NextScan\nEndProg\n")
    p = write(tmp_path, "m.CR6", src)
    r = run(p)
    assert where(N.definition(r, p, 8, 2)) == [("m.CR6", 0, 7, 8)]


def test_instruction_token_has_no_user_definition(tmp_path):
    p = write(tmp_path, "m.CR6", "Public PanelTemp,T\nBeginProg\n Scan(1,Sec,0,0)\n"
                                 "  PanelTemp(T,15000)\n NextScan\nEndProg\n")
    assert N.definition(run(p), p, 3, 3) == []


# ------------------------------------------------------------------ across entries (LSP-R4a)

SHARED_USE = 'Include "Lib.crb"\nBeginProg\nsharedValue = 1\nEndProg\n'


def test_references_across_entries_of_a_shared_library(tmp_path):
    """Host H-08: from A's use, B's use or the declaration, every entry's uses are listed."""
    lib = write(tmp_path, "Lib.crb", "Public sharedValue\n")
    a = write(tmp_path, "A.CR6", SHARED_USE)
    b = write(tmp_path, "B.CR6", SHARED_USE)
    navs = [N.Navigation(run(a)), N.Navigation(run(b))]
    want = [("A.CR6", 2, 0, 11), ("B.CR6", 2, 0, 11), ("Lib.crb", 0, 7, 18)]
    for path, line, col in ((a, 2, 3), (b, 2, 3), (lib, 0, 9)):
        assert where(N.references_across(navs, path, line, col)) == want
        assert where(N.references_across(list(reversed(navs)), path, line, col)) == want
        assert where(N.references_across(navs, path, line, col, False)) == want[:2]
    assert where(N.references_across(navs[:1], b, 2, 3)) == []             # A does not read B
    assert where(N.references(navs[0].result, a, 2, 3)) == [want[0], want[2]]  # one entry: unchanged
    sites = N.declaration_sites(navs, a, 2, 3)
    assert [(required(s.path).name, s.line, s.start, s.end) for s in sites] == [("Lib.crb", 0, 7, 18)]


def test_references_across_entries_with_their_own_declarations(tmp_path):
    """A library use that resolves to a different declaration in each entry lists both."""
    lib = write(tmp_path, "Lib.crb", "Sub Bump\n  v = v + 1\nEndSub\n")
    body = 'Public v\nInclude "Lib.crb"\nBeginProg\nv = 1\nBump\nEndProg\n'
    a = write(tmp_path, "A.CR6", body)
    b = write(tmp_path, "B.CR6", body)
    navs = [N.Navigation(run(a)), N.Navigation(run(b))]
    got = where(N.references_across(navs, lib, 1, 2))
    assert ("A.CR6", 0, 7, 8) in got and ("B.CR6", 0, 7, 8) in got
    assert ("A.CR6", 3, 0, 1) in got and ("B.CR6", 3, 0, 1) in got
    assert [g for g in got if g[0] == "Lib.crb"] == [("Lib.crb", 1, 2, 3), ("Lib.crb", 1, 6, 7)]
