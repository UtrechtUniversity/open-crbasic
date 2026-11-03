"""Source positions (LSP-02a): provenance pieces, reported-line map, LSP position conversion."""

from pathlib import Path

import pytest

from opencrbasic import lexer, parser, positions, profiles, source
from opencrbasic.analysis import AnalysisRequest, analyze

from .assertions import required

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT / "tests" / "corpus" / "station").resolve()
CHM = sorted((ROOT / "tests" / "corpus" / "chm").glob("*.CR6"))

LOGGERS = [("cr6", "t.CR6"), ("cr300", "t.CR300")]


def _parse(text: str, name: str = "t.CR6", logger: str | None = None):
    return parser.parse_text(text, name, profile=profiles.get(logger) if logger else None)[0]


def _load(text: str, logger: str = "cr6", name: str | None = None, directory=None):
    name = name or dict(LOGGERS)[logger]
    own = {"cr6": "CR6"}.get(logger)
    exts = {"cr300": ("CR300", "CR310")}.get(logger)
    return source.load_text(text, name, directory, own, True, exts)[0]


def _slices(lines: list[bytes], ranges):
    return ["".join(lines[r.line - 1].decode("latin-1")[r.start_col : r.end_col]) for r in ranges]


def _find(ll, needle):
    i = ll.text.index(needle)
    return i, i + len(needle)


# ------------------------------------------------------------------ continuation


def test_continuation_two_lines_with_comment_and_blank():
    text = "x = 1 + _ ' note\n   2 ' tail\n\nBeginProg\n"
    ll = _load(text)[0]
    assert ll.text == "x = 1 +    2 "
    assert ll.line == 2 and ll.phys == (1, 2)
    assert [(p.start, p.end, p.line, p.col) for p in ll.pieces] == [(0, 8, 1, 0), (8, 13, 2, 0)]
    plines = positions.physical_lines(text.encode())
    two = positions.logical_ranges(ll, *_find(ll, "2"))
    assert two == [positions.PhysicalRange("t.CR6", 0, 2, 3, 4)]
    assert _slices(plines, two) == ["2"]
    spanning = positions.logical_ranges(ll, 4, 12)  # "1 +    2" crosses the join
    assert [(r.line, r.start_col, r.end_col) for r in spanning] == [(1, 4, 8), (2, 0, 4)]
    assert positions.exact_range(ll, 4, 12) is None


def test_continuation_three_lines_blank_and_lone_underscore():
    text = "Public A, _\n   B, _\n   C\nPublic D\n"
    ll = _load(text)[0]
    assert ll.phys == (1, 3) and ll.line == 3
    plines = positions.physical_lines(text.encode())
    for name, line in (("A", 1), ("B", 2), ("C", 3)):
        (r,) = positions.logical_ranges(ll, *_find(ll, name))
        assert r.line == line and _slices(plines, [r]) == [name]
    lone = _load("x = 1 _\n_\n+ 2\n")[0]
    assert lone.phys == (1, 3)
    (r,) = positions.logical_ranges(lone, *_find(lone, "2"))
    assert r.line == 3


def test_continuation_at_end_of_file_drops_the_statement():
    # the real compilers read nothing of a statement whose last line ends with `_` at the end of
    # the file (oracle, 2026-10-12, tests/fixtures/r15_eof_continuation.json)
    assert _load("x = 1 _") == []
    assert _load("x = 1 _\n") == []
    assert [ll.text for ll in _load("x = 1 _\n\n")] == ["x = 1 "]


# ------------------------------------------------------------------ colon segments


def test_colon_segments_map_to_the_same_physical_line():
    text = "  x = 1 : y = _\n   2 : z = 3\n"
    ll = _load(text)[0]
    plines = positions.physical_lines(text.encode())
    expect = {"x": 1, "y": 1, "2": 2, "z": 2}
    for name, line in expect.items():
        start = ll.text.index(name) if name != "2" else ll.text.rindex("2")
        r = positions.exact_range(ll, start, start + 1)
        assert r is not None and r.line == line and _slices(plines, [r]) == [name]
    toks = [t for t in lexer.tokenize(ll.text) if t.text == ":"]
    assert len(toks) == 2
    assert [required(positions.exact_range(ll, t.col, t.col + 1)).line for t in toks] == [1, 2]


# ------------------------------------------------------------------ pulled initializers


@pytest.mark.parametrize("logger,name", LOGGERS)
def test_pull_reported_line_differs_from_physical(logger, name):
    text = "Public A = \n  5\nPublic B\nBeginProg\n  B = 1\nEndProg\n"
    lines = _load(text, logger)
    a = lines[0]
    assert a.text == "Public A = 5" and a.line == 1 and a.phys == (1, 2) and a.pulls == 1
    plines = positions.physical_lines(text.encode())
    (r5,) = positions.logical_ranges(a, a.text.index("5"), a.text.index("5") + 1)
    assert r5.line == 2 and r5.start_col == 2 and _slices(plines, [r5]) == ["5"]
    # the inserted " = " has no piece: the range crossing it splits in two
    parts = positions.logical_ranges(a, a.text.index("A"), len(a.text))
    assert [(r.line, r.start_col, r.end_col) for r in parts] == [(1, 7, 8), (2, 2, 3)]
    assert positions.logical_ranges(a, 11, 11) == [positions.PhysicalRange(name, 0, 2, 2, 2)]
    assert positions.logical_ranges(a, 10, 10) == []  # inside the inserted " = "
    # physical line 3 is reported at line 2; line 5 at 4
    prog = _parse(text, name, logger)
    assert positions.reported_spans(prog.line_map, name, 2) == [source.LineSpan(3, 3, 0)]
    assert positions.line_span(prog.line_map, name, 4) == (5, 5)
    assert positions.line_span(prog.line_map, name.lower(), 1) == (1, 2)
    assert positions.line_span(prog.line_map, name, 99) is None


@pytest.mark.parametrize("logger,name", LOGGERS)
def test_pull_brace_list_continues_and_lines_after_pull_are_shifted(logger, name):
    text = "Public A(3) = {1,\n 2,\n 3}\nPublic B\n"
    a = _load(text, logger)[0]
    assert a.pulls == 2 and a.phys == (1, 3)
    plines = positions.physical_lines(text.encode())
    for ch, line in (("1", 1), ("2", 2), ("3", 3)):
        i = a.text.rindex(ch)
        (r,) = positions.logical_ranges(a, i, i + 1)
        assert r.line == line and _slices(plines, [r]) == [ch]
    prog = _parse(text, name, logger)
    assert positions.line_span(prog.line_map, name, 2) == (4, 4)


def test_unread_pull_in_inactive_if_keeps_physical_lines_mappable():
    text = "#If False\nPublic A = \n 5\nPublic C\n#EndIf\nPublic B\n"
    prog = _parse(text)
    spans = prog.line_map["t.CR6"]
    covered = sorted({n for ss in spans.values() for s in ss for n in range(s.first, s.last + 1)})
    assert covered == [1, 2, 3, 4, 5, 6]
    assert positions.line_span(prog.line_map, "t.CR6", 2) == (2, 2)  # unread: not pulled
    assert positions.line_span(prog.line_map, "t.CR6", 6) == (6, 6)  # no shift after it


# ------------------------------------------------------------------ includes


LIB = 'Public LibVar\n#If Section = "One"\nPublic OnlyOne\n#EndIf\n'


def _write(tmp_path, entry: str, **files: str) -> Path:
    for fname, content in files.items():
        (tmp_path / fname).write_bytes(content.encode())
    path = tmp_path / "m.CR6"
    path.write_bytes(entry.encode())
    return path


def test_include_lines_have_their_own_physical_lines(tmp_path):
    path = _write(tmp_path, 'Include("CPU:Lib.crb")\nPublic X\nBeginProg\nEndProg\n', **{"Lib.crb": LIB})
    prog, _ = parser.parse_file(path)
    assert [o.file for o in prog.occurrences] == ["m.CR6", "CPU:Lib.crb"]
    lib = prog.occurrences[1]
    assert lib.parent == 0 and lib.site == (1, 1) and lib.path == tmp_path / "Lib.crb"
    assert positions.line_span(prog.line_map, "CPU:Lib.crb", 1) == (1, 1)
    assert positions.line_span(prog.line_map, "m.CR6", 2) == (2, 2)
    assert positions.reported_spans(prog.line_map, "m.CR6", 1) == [source.LineSpan(1, 1, 0)]


def test_double_include_section_keeps_distinct_occurrences(tmp_path):
    entry = (
        'IncludeSection("One", "CPU:Lib.crb")\n\nIncludeSection("Two", "CPU:Lib.crb")\nPublic X\nBeginProg\nEndProg\n'
    )
    path = _write(tmp_path, entry, **{"Lib.crb": LIB})
    prog, _ = parser.parse_file(path)
    libs = [o for o in prog.occurrences if o.file == "CPU:Lib.crb"]
    assert [o.id for o in libs] == [1, 2] and [o.site for o in libs] == [(1, 1), (3, 3)]
    assert all(o.parent == 0 for o in libs)
    spans = positions.reported_spans(prog.line_map, "CPU:Lib.crb", 1)
    assert {s.occurrence for s in spans} == {1, 2} and {(s.first, s.last) for s in spans} == {(1, 1)}
    # the inactive branch of each load is still mappable
    assert {s.occurrence for s in positions.reported_spans(prog.line_map, "CPU:Lib.crb", 3)} == {1, 2}
    lines = source.load_program(path, "CR6", True)[0]
    pieces = {p.occurrence for ll in lines for p in ll.pieces if p.file == "CPU:Lib.crb"}
    assert pieces == {1, 2}


def test_nested_include_section_is_not_loaded(tmp_path):
    path = _write(
        tmp_path,
        'IncludeSection("A", "CPU:A.crb")\nBeginProg\nEndProg\n',
        **{"A.crb": '\nIncludeSection("B", "CPU:B.crb")\n', "B.crb": "Public V\n"},
    )
    prog, diags = parser.parse_file(path)
    assert [o.file for o in prog.occurrences if o.file.startswith("CPU:")] == ["CPU:A.crb"]
    assert any("within an Include file" in d.message for d in diags.errors)


def test_analysis_diagnostic_span_in_include_and_entry(tmp_path):
    path = _write(
        tmp_path,
        'Include("CPU:Lib.crb")\nPublic A = \n 1\nPublic Q\nBeginProg\n  Q = Nope\nEndProg\n',
        **{"Lib.crb": "\n\nPublic Z\nBeginProg\n"},
    )
    res = analyze(AnalysisRequest(path))
    by_msg = {d.message: d for d in res.diagnostics}
    entry_err = next(d for d in res.diagnostics if "Nope" in d.message)
    assert entry_err.line == 5 and entry_err.span == (6, 6) and entry_err.path == path
    assert all(isinstance(d.span, (tuple, type(None))) for d in by_msg.values())


@pytest.mark.parametrize("logger,name", [("cr6", "m.CR6"), ("cr300", "m.CR300")])
def test_analysis_span_pull_reported_differs(tmp_path, logger, name):
    path = tmp_path / name
    path.write_bytes(b"Public A = \n 1\nPublic Q\nBeginProg\n  Q = Nope\n  Q = Nope2 + _\n   1\nEndProg\n")
    res = analyze(AnalysisRequest(path, logger))
    spans = {("Nope2" if "Nope2" in d.message else "Nope"): (d.line, d.span) for d in res.diagnostics}
    assert spans["Nope"] == (4, (5, 5))
    assert spans["Nope2"] == (6, (6, 7))


def test_analysis_diagnostic_span_none_for_unknown(tmp_path):
    path = tmp_path / "m.CR6"
    path.write_bytes(b"Public X\n")
    res = analyze(AnalysisRequest(path))
    assert res.diagnostics and all(d.span is None for d in res.diagnostics if d.line == 0)


# ------------------------------------------------------------------ inactive #If


def test_inactive_if_branch_tokens_map():
    text = "#If 1 = 2\nPublic Hidden, _\n  Other\n#Else\nPublic Shown\n#EndIf\n"
    prog = _parse(text)
    assert positions.line_span(prog.line_map, "t.CR6", 3) == (2, 3)
    assert positions.line_span(prog.line_map, "t.CR6", 1) == (1, 1)
    ll = next(l for l in _load(text) if "Hidden" in l.text)
    (r,) = positions.logical_ranges(ll, *_find(ll, "Other"))
    assert (r.line, r.start_col, r.end_col) == (3, 2, 7)
    assert not any(getattr(s, "name", None) == "Hidden" for s in prog.statements)


def test_lines_after_endprog_are_still_mapped():
    text = "BeginProg\nEndProg\n\nthis is ignored\n"
    prog = _parse(text)
    assert positions.line_span(prog.line_map, "t.CR6", 4) == (4, 4)


# ------------------------------------------------------------------ CRLF and encodings


def test_crlf_and_lf_give_identical_provenance_and_positions():
    lf = "Public A = \n é\nx = 1 _\n + 2 ' c\n"
    crlf = lf.replace("\n", "\r\n")
    a, b = _load(lf), _load(crlf)
    assert [(l.text, l.line, l.phys, l.pieces) for l in a] == [(l.text, l.line, l.phys, l.pieces) for l in b]
    assert positions.physical_lines(lf.encode()) == positions.physical_lines(crlf.encode())
    for enc in positions.POSITION_ENCODINGS:
        assert positions.lsp_position(lf.encode(), 3, 4, encoding=enc) == positions.lsp_position(
            crlf.encode(), 3, 4, encoding=enc
        )


GRIN = "\U0001f600"  # non-BMP: 4 UTF-8 bytes, 2 UTF-16 units, 1 code point


def test_non_ascii_comment_and_string_positions():
    text = f"' café {GRIN}\nx = \"é{GRIN}\" + y ' é\n"
    data = text.encode("utf-8")
    ll = _load(data.decode("latin-1"))[0]
    assert ll.phys == (2, 2)
    (r,) = positions.logical_ranges(ll, ll.text.index("y"), ll.text.index("y") + 1)
    assert r.line == 2
    # decoded columns are bytes: e-acute 2, emoji 4
    assert positions.lsp_position(data, 2, r.start_col) == (1, len('x = "') + 1 + 2 + len('" + '))
    assert positions.lsp_position(data, 2, r.start_col, encoding="utf-32") == (1, len('x = "') + 2 + len('" + '))
    assert positions.lsp_position(data, 2, r.start_col, encoding="utf-8") == (1, r.start_col)
    assert positions.lsp_range(data, r) == ((1, 12), (1, 13))
    # the comment before it: a column inside the emoji snaps to its start
    assert positions.lsp_position(data, 1, len("' caf") + 2 + 1 + 2) == (0, len("' caf") + 1 + 1 + 0)
    assert positions.lsp_position(data, 1, len(data.split(b"\n")[0])) == (0, len("' café ") + 2)


def test_lsp_position_other_editor_encoding_and_bounds():
    data = "x = é;\n".encode("cp1252")
    assert positions.lsp_position(data, 1, 6, editor_encoding="cp1252") == (0, 6)
    assert positions.lsp_position(data, 1, 6) == (0, 6)  # utf-8 reading: 0xE9 is replaced by U+FFFD
    assert positions.lsp_position(data, 1, 5) == (0, 4)  # ... and an incomplete tail is dropped
    assert positions.lsp_position(data, 1, 99, encoding="utf-32", editor_encoding="cp1252") == (0, 6)
    with pytest.raises(IndexError):
        positions.lsp_position(data, 5, 0)
    with pytest.raises(ValueError):
        positions.lsp_position(data, 1, 0, encoding="utf-9")


# ------------------------------------------------------------------ no behaviour change


def test_unpulled_lines_carry_provenance():
    ll = _load("Public A = \n 5\n")[0]
    assert ll.unpulled and [u.phys for u in ll.unpulled] == [(1, 1), (2, 2)]
    assert ll.unpulled[1].pieces == (source.Piece(0, 2, "t.CR6", 2, 0, 0),)


def test_logicalline_constructor_compatible():
    ll = source.LogicalLine("x", 1, "f")
    assert ll.pieces == () and ll.phys is None and ll.occurrence == 0


# ------------------------------------------------------------------ property test


def _bytes_by_occurrence(program) -> dict[int, list[bytes]]:
    out = {}
    for o in program.occurrences:
        if o.path is not None and Path(o.path).exists():
            out[o.id] = positions.physical_lines(Path(o.path).read_bytes())
    return out


def _check_program(path: Path, logger: str = "cr6") -> int:
    profile = profiles.get(logger)
    prog, _ = parser.parse_file(path, profile)
    files = _bytes_by_occurrence(prog)
    lines, _ = source.load_program(path, "CR6", True)
    exact = 0
    for ll in lines:
        if ll.kind != "code" or not ll.pieces:
            continue
        plines = files[ll.occurrence]
        whole = positions.logical_ranges(ll, 0, len(ll.text))
        covered = "".join(ll.text[p.start : p.end] for p in ll.pieces)
        assert "".join(_slices(plines, whole)) == covered, (path, ll.line)
        try:
            tokens = lexer.tokenize(ll.text)
        except lexer.LexError:
            continue
        for t in tokens[:-1]:
            r = positions.exact_range(ll, t.col, t.col + len(t.text))
            if r is None:
                continue
            exact += 1
            assert _slices(plines, [r]) == [t.text], (path, ll.line, t)
            assert r.file == ll.file and r.occurrence == ll.occurrence
    return exact


@pytest.mark.parametrize("entry", sorted(CRBASIC.glob("*.CR6")), ids=lambda p: p.name)
def test_property_crbasic_tokens_map_to_their_text(entry):
    assert _check_program(entry) > 0


@pytest.mark.skipif(not CHM, reason="CRBasic help examples not extracted; see tests/corpus/README.md")
def test_property_chm_corpus_sample():
    assert CHM
    total = sum(_check_program(p) for p in CHM[:: max(1, len(CHM) // 150)])
    assert total > 0
