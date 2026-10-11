"""Indentation formatter (LSP-07b): rules, refusals, idempotence and the semantic-preservation
sweep over `crbasic/` and the CHM corpus."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from opencrbasic import compiler, profiles, tdf
from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.formatting import (
    TextEdit, apply_edits, format_indentation, format_range, format_refusal,
)

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT.parent / "crbasic").resolve()
CHM = sorted((ROOT / "tests" / "corpus" / "chm").glob("*.CR6"))
BLANKS = re.compile(r"\s+")


def fmt(text: str, indent: str = "  ") -> str:
    return apply_edits(text, format_indentation(text, "cr6", indent))


def lines(text: str) -> list[str]:
    return text.split("\n")


# ------------------------------------------------------------------ rules


def test_blocks_case_else_continuations_comments():
    src = """Public x
BeginProg
Scan(1,Sec,0,0)
If x Then
x=1
ElseIf x=2 Then
x=2
Else
x=3
EndIf
Select Case x
Case 1
x=1
Case Else
x=2
EndSelect
Call Foo(1, _
2, _
3)
' comment before code
x = 1 'trailing
NextScan
EndProg
"""
    assert fmt(src) == """Public x
BeginProg
  Scan(1,Sec,0,0)
    If x Then
      x=1
    ElseIf x=2 Then
      x=2
    Else
      x=3
    EndIf
    Select Case x
      Case 1
        x=1
      Case Else
        x=2
    EndSelect
    Call Foo(1, _
      2, _
      3)
    ' comment before code
    x = 1 'trailing
  NextScan
EndProg
"""


def test_two_word_closers_loops_subs_tables_and_inline_if():
    src = """Sub S
For i = 1 To 3
Do While i > 0
If i Then x = 1
Loop
Next i
End Sub
DataTable (T,1,-1)
Sample (1,x,FP2)
EndTable
"""
    assert fmt(src) == """Sub S
  For i = 1 To 3
    Do While i > 0
      If i Then x = 1
    Loop
  Next i
End Sub
DataTable (T,1,-1)
  Sample (1,x,FP2)
EndTable
"""


def test_comment_follows_next_code_line_and_directives_are_column_zero():
    src = """BeginProg
  #If A Then
    ' about the else
x = 1
  #Else
x = 2
#EndIf
    ' end
    EndProg
"""
    assert fmt(src) == """BeginProg
#If A Then
  ' about the else
  x = 1
#Else
  x = 2
#EndIf
' end
EndProg
"""


def test_if_branches_with_different_open_blocks_stop_formatting():
    src = "#If A Then\nSub A\n#Else\nIf x Then\n#EndIf\nx = 1\nEndSub\n"
    assert fmt(src) == src


def test_if_branches_that_open_the_same_block():
    src = "#If A Then\nSub A\n#Else\nSub B\n#EndIf\nx = 1\nEndSub\n"
    assert fmt(src) == "#If A Then\nSub A\n#Else\nSub B\n#EndIf\n  x = 1\nEndSub\n"


def test_slowsequence_without_endsequence():
    src = "BeginProg\nScan(1,Sec,0,0)\nx=1\nNextScan\nSlowSequence\nScan(1,Sec,0,0)\nx=1\nNextScan\nSlowSequence\nx=2\nEndProg\n"
    assert fmt(src) == ("BeginProg\n  Scan(1,Sec,0,0)\n    x=1\n  NextScan\n  SlowSequence\n"
                        "    Scan(1,Sec,0,0)\n      x=1\n    NextScan\n  SlowSequence\n    x=2\nEndProg\n")


def test_indent_option_tabs_and_validation():
    assert fmt("Sub S\nx=1\nEndSub\n", "\t") == "Sub S\n\tx=1\nEndSub\n"
    for bad in ("", "x", " a"):
        with pytest.raises(ValueError):
            format_indentation("x\n", "cr6", bad)


def test_crlf_blank_lines_and_trailing_text_untouched():
    src = "Sub S\r\n   \r\n\tx=1 \r\n\r\n   EndSub\r\n"
    out = fmt(src)
    assert out == "Sub S\r\n   \r\n  x=1 \r\n\r\nEndSub\r\n"
    assert len(lines(out)) == len(lines(src))


def test_lines_inside_strings_are_left_alone():
    # a string that runs over a continuation, and an unterminated string
    src = 'Sub S\nx = "abc _\n   def" + "q"\ny = 1\nEndSub\n'
    assert lines(fmt(src))[2] == "   def\" + \"q\""
    src = 'Sub S\n   x = "never closed\ny = 1\nEndSub\n'
    assert lines(fmt(src))[1] == '   x = "never closed'
    assert lines(fmt(src))[2] == "  y = 1"


def test_continued_units_label_is_left_alone(tmp_path):
    # review F4: the label is verbatim text, so re-indenting its continuation changes the TDF
    src = ('Public T\nUnits T = Deg _\n        C\nDataTable(Tab,True,-1)\nSample(1,T,FP2)\n'
           'EndTable\nBeginProg\nScan(1,Sec,0,0)\nCallTable Tab\nNextScan\nEndProg\n')
    assert lines(fmt(src))[:3] == ["Public T", "Units T = Deg _", "        C"]
    assert lines(fmt(src))[4] == "  Sample(1,T,FP2)"          # the rest is still formatted
    f = tmp_path / "u.CR6"
    f.write_bytes(fmt(src).encode("latin-1"))
    assert [x.units for t in compiler.compile_file(f, profiles.get("cr6"), signature=False).tables
            for x in t.fields if x.name == "T"][0] == "Deg         C"


def test_unclosed_block_leaves_the_rest_alone():
    src = "Sub S\nIf x Then\ny = 1\nEndSub\n   z = 2\n"
    out = lines(fmt(src))
    assert out[0] == "Sub S"
    assert out[1] == "  If x Then"          # the opener itself is formatted
    assert out[2] == "y = 1" and out[4] == "   z = 2"
    assert fmt("  Sub S\nx = 1\n   y = 2\n") == "Sub S\nx = 1\n   y = 2\n"       # no EndSub


def test_closer_without_opener_stops_formatting_there():
    out = lines(fmt("Sub S\nx=1\nEndSub\nEndSub\n   y=2\n"))
    assert out == ["Sub S", "  x=1", "EndSub", "EndSub", "   y=2", ""]


def test_nothing_after_the_top_level_endprog_is_touched():
    src = "BeginProg\nx=1\nEndProg\n   prose ### here\n  Sub X\n"
    assert fmt(src) == "BeginProg\n  x=1\nEndProg\n   prose ### here\n  Sub X\n"


def test_buffer_limit_refuses_the_growing_line():
    def text(n: int) -> str:
        return "BeginProg\nx = 1 '" + "a" * (n - len("x = 1 '")) + "\ny = 2\nEndProg\n"
    fits = fmt(text(507))      # 507 + 2 blanks + LF = 510: the loader still accepts it
    assert lines(fits)[1].startswith("  x = 1") and len(lines(fits)[1]) == 509
    refused = fmt(text(508))   # would need 511 with the LF: refused, the other lines are formatted
    assert lines(refused)[1].startswith("x = 1") and lines(refused)[2] == "  y = 2"


def test_buffer_limit_counts_a_continued_statement_together():
    first = "x = 1 + _"
    second = "2 '" + "a" * 496           # 499 characters: 8 + 499 ... joined buffer near the limit
    src = f"BeginProg\n{first}\n{second}\nEndProg\n"
    out = fmt(src)
    from opencrbasic import source
    loaded, _ = source.load_text(out, "t")
    assert not any(ll.kind == "overflow" for ll in loaded)
    loaded, _ = source.load_text(src, "t")
    assert not any(ll.kind == "overflow" for ll in loaded)


def test_text_after_overflow_is_not_touched():
    long = "x = 1 '" + "a" * 600
    src = f"BeginProg\n{long}\n   y = 1\nEndProg\n"
    assert fmt(src) == f"BeginProg\n{long}\n   y = 1\nEndProg\n"


def test_range_formats_only_the_lines_asked_for():
    src = "Sub S\nx=1\ny=2\nz=3\nEndSub\n"
    edits = format_range(src, 2, 3)
    assert [e.line for e in edits] == [2, 3]
    assert apply_edits(src, edits) == "Sub S\nx=1\n  y=2\n  z=3\nEndSub\n"
    assert format_range(src, 0, 0) == []


def test_idempotent_and_edits_touch_leading_whitespace_only():
    src = "Sub S\n\tIf x Then\n   y=1 \t'c\nElse\n  y=2\n  EndIf\n   EndSub\n"
    once = fmt(src)
    assert fmt(once) == once
    for e in format_indentation(src):
        old = lines(src)[e.line]
        assert e.start == 0 and not old[:e.end].strip(" \t") and e.new_text.strip(" \t") == ""
    assert [ln.lstrip(" \t") for ln in lines(once)] == [ln.lstrip(" \t") for ln in lines(src)]


def test_format_refusal(tmp_path):
    good = tmp_path / "g.CR6"
    good.write_bytes(b"BeginProg\nEndProg\n")
    bad = tmp_path / "b.CR6"
    bad.write_bytes(b"BeginProg\nNoSuchThing 1\nEndProg\n")
    assert format_refusal(analyze(AnalysisRequest(good))) is None
    assert "errors" in format_refusal(analyze(AnalysisRequest(bad)))


# ------------------------------------------------------------------ semantic preservation


def _outcome(path: Path):
    r = compiler.compile_file(path, profiles.get("cr6"), signature=False)
    return (r.ok, r.mode, tuple(r.result_lines()), tdf.serialize(r.tables) if r.tables else b"",
            r.table_list, tuple((d.severity, d.message) for d in r.diagnostics.items))


def _format_bytes(data: bytes, indent: str = "  ") -> bytes:
    text = data.decode("latin-1")
    return apply_edits(text, format_indentation(text, "cr6", indent)).encode("latin-1")


@pytest.mark.slow
@pytest.mark.parametrize("indent", ["  ", "\t", "    "])
def test_crbasic_programs_compile_identically_after_formatting(tmp_path, indent):
    edited = 0
    for f in CRBASIC.iterdir():
        if not f.is_file():
            continue
        data = f.read_bytes()
        if f.suffix.upper() in (".CR6", ".CRB"):
            edited += data != _format_bytes(data, indent)
            data = _format_bytes(data, indent)
            assert _format_bytes(data, indent) == data, f.name
        (tmp_path / f.name).write_bytes(data)
    if indent != "  ":      # crbasic/ is already two-space formatted
        assert edited > 0, "a no-op formatter must not pass the sweep"
    entries = sorted(CRBASIC.glob("*.CR6"))
    assert len(entries) > 10
    for entry in entries:
        assert _outcome(tmp_path / entry.name) == _outcome(entry), entry.name


@pytest.mark.slow
def test_chm_programs_compile_identically_after_formatting(tmp_path):
    """Every CHM program. Accepted: identical `ok`, mode, diagnostics, TDF bytes, table list.
    Rejected: only the blanks echoed in messages may differ (one program differs in more)."""
    accepted = rejected = echo = other = changed = 0
    odd = []
    for f in CHM:
        data = f.read_bytes()
        new = _format_bytes(data)
        changed += new != data
        assert _format_bytes(new) == new, f.name
        g = tmp_path / f.name
        g.write_bytes(new)
        before, after = _outcome(f), _outcome(g)
        if before[0]:
            accepted += 1
            assert after == before, f.name
            continue
        rejected += 1
        echo += after != before
        norm = lambda o: (o[0], o[1], tuple(BLANKS.sub(" ", x) for x in o[2]), o[3], o[4],
                          tuple(BLANKS.sub(" ", m) for _, m in o[5]))
        if norm(after) != norm(before):
            other += 1
            odd.append(f.name)
    print(f"chm: accepted {accepted}, rejected {rejected}, rejected differing {echo}, "
          f"differing beyond blanks {other} {odd}")
    assert accepted > 100
    assert changed > 0, "a no-op formatter must not pass the sweep"
    assert set(odd) <= {"xmlparse_1.CR6"}        # a prose line parsed differently with its indent
