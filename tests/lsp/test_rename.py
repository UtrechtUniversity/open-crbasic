"""LSP-07a: protocol-independent rename (`opencrbasic.rename`). Policies:
docs/lsp-api.md "Rename (LSP-07a)".

The semantic-preservation sweep renames program-declared names of `../crbasic` to fresh names in
tmp copies, compiles the affected entries and compares them with the unrenamed compile. By default
a stride sample runs; `RENAME_SWEEP=full` runs every declaration and prints the counts."""

import dataclasses
import hashlib
import os
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic import rename as R
from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.compiler import compile_file

from .assertions import required

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT / "tests" / "corpus" / "firnmelt").resolve()


def write(tmp_path: Path, name: str, text: str, newline: str = "\n") -> Path:
    p = tmp_path / name
    p.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return p


def run(entry: Path, **kw):
    return analyze(AnalysisRequest(entry, **kw))


def pos(path: Path, needle: str, nth: int = 0, offset: int = 0) -> tuple[int, int]:
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


def apply(data: bytes, edits) -> bytes:
    lines = data.split(b"\n")
    for e in sorted(edits, key=lambda e: (e.line, e.start), reverse=True):
        line = lines[e.line]
        lines[e.line] = line[:e.start] + e.new_text.encode("latin-1") + line[e.end:]
    return b"\n".join(lines)


def spots(res: R.RenameResult, path: Path):
    return [(e.line, e.start, e.end) for e in res.edits.get(path, ())]


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


# ------------------------------------------------------------------ accepted renames

def test_global_rename_skips_shadowing_local(sample):
    p, r = sample
    line, _col = pos(p, "a", 0, 7 - 0)[0], 7
    res = R.rename([r], p, 0, 7, "Alpha")
    assert res.ok and res.refusal is None
    assert spots(res, p) == [(0, 7, 8), (8, 4, 5), (9, 8, 9)]       # not the Dim a / `a = p + K`
    assert all(e.new_text == "Alpha" for e in res.edits[p])
    assert res.versions == {p: hashlib.sha256(p.read_bytes()).hexdigest()}
    out = apply(p.read_bytes(), res.edits[p]).decode()
    assert "Public Alpha, Arr(3)" in out and "  Dim a\n  a = p + K" in out
    assert "    Alpha = Arr(1) + K\n    Foo(Alpha)" in out
    assert line == 0


def test_local_parameter_and_constant(sample):
    p, r = sample
    res = R.rename([r], p, 2, 8, "q")                  # parameter p
    assert spots(res, p) == [(2, 8, 9), (4, 6, 7)] or spots(res, p) == [(2, 8, 9), (4, 6, 7)]
    res = R.rename([r], p, 1, 6, "Kay")                # Const K
    assert spots(res, p) == [(1, 6, 7), (4, 10, 11), (8, 17, 18)]
    res = R.rename([r], p, 3, 6, "loc")                # local Dim a: only the Sub's uses
    assert spots(res, p) == [(3, 6, 7), (4, 2, 3)]


def test_reference_position_and_just_after_name(sample):
    p, r = sample
    res = R.rename([r], p, 8, 5, "Alpha")              # just after the `a` in BeginProg
    assert spots(res, p) == [(0, 7, 8), (8, 4, 5), (9, 8, 9)]
    pr = R.prepare_rename([r], p, 9, 8)
    assert pr.ok and (pr.path, pr.line, pr.start, pr.end, pr.placeholder) == (p, 9, 8, 9, "a")


def test_case_only_change_is_allowed(sample):
    p, r = sample
    res = R.rename([r], p, 1, 6, "k")
    assert res.ok and len(res.edits[p]) == 3


def test_sub_function_and_table_rename(tmp_path):
    m = write(tmp_path, "m.CR6", """Public x
Function Twice(v)
  Twice = v * 2
EndFunction
DataTable(Hist,True,-1)
  Sample(1,x,FP2)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    x = Twice(2)
    CallTable Hist
    x = Hist.Foo
  NextScan
EndProg
""")
    r = run(m)
    res = R.rename([r], m, 1, 10, "Dbl")
    assert res.ok and spots(res, m) == [(1, 9, 14), (2, 2, 7), (9, 8, 13)]
    res = R.rename([r], m, 4, 11, "Series")
    assert res.ok and spots(res, m) == [(4, 10, 14), (10, 14, 18), (11, 8, 12)]


def test_structure_member_and_type(tmp_path):
    m = write(tmp_path, "m.CR6", """StructureType Pt
  Lat As Float
  Lon As Float
EndStructureType
Public p As Pt
BeginProg
  Scan(1,Sec,0,0)
    p.Lat = 1
    p.Lon = p.Lat + 1
  NextScan
EndProg
""")
    r = run(m)
    res = R.rename([r], m, 1, 3, "North")
    assert res.ok and spots(res, m) == [(1, 2, 5), (7, 6, 9), (8, 14, 17)]
    res = R.rename([r], m, 0, 16, "Point")
    assert res.ok and spots(res, m) == [(0, 14, 16), (4, 12, 14)]
    res = R.rename([r], m, 1, 3, "Lon")                # collides with a member of the same type
    assert res is not None
    assert required(res.refusal).code == "collision"
    assert R.rename([r], m, 1, 3, "Lat2").ok


def test_crlf_and_overlay_versions(tmp_path):
    m = write(tmp_path, "m.CR6", SAMPLE, newline="\r\n")
    r = run(m)
    res = R.rename([r], m, 0, 7, "Alpha")
    assert spots(res, m) == [(0, 7, 8), (8, 4, 5), (9, 8, 9)]
    out = apply(m.read_bytes().replace(b"\r\n", b"\n"), res.edits[m]).replace(b"\n", b"\r\n")
    assert b"Public Alpha, Arr(3)\r\n" in out
    buf = SAMPLE.replace("Public a,", "Public a ,").encode()           # unsaved buffer
    r2 = run(m, overlays={m: buf})
    res2 = R.rename([r2], m, 0, 7, "Alpha")
    assert res2.ok and res2.versions == {m: hashlib.sha256(buf).hexdigest()}
    assert spots(res2, m) == [(0, 7, 8), (8, 4, 5), (9, 8, 9)]


def test_stale_file_is_refused(sample):
    p, r = sample
    p.write_bytes(p.read_bytes() + b"\n")
    res = R.rename([r], p, 0, 7, "Alpha")
    assert res is not None
    assert not res.ok and required(res.refusal).code == "stale"
    assert required(R.prepare_rename([r], p, 0, 7).refusal).code == "stale"


def test_unedited_dependency_that_changed_is_stale(tmp_path):      # review R2 finding 1
    lib = write(tmp_path, "Lib.crb", "Public otherName\n")
    m = write(tmp_path, "M.CR6", 'Include "Lib.crb"\nPublic oldName\nBeginProg\noldName=1\nEndProg\n')
    r = run(m)
    assert r.ok
    lib.write_bytes(b"Public newName\n")
    assert run(m).ok
    res = R.rename([r], m, 1, 8, "newName")
    assert res is not None
    assert code(res) == "stale" and "Lib.crb" in required(res.refusal).message
    assert code(R.prepare_rename([r], m, 1, 8)) == "stale"
    lib.write_bytes(b"Public otherName\n")                 # restored: current again
    assert R.rename([r], m, 1, 8, "newName").ok
    lib.unlink()                                             # a vanished dependency is stale too
    assert code(R.rename([r], m, 1, 8, "newName")) == "stale"


def test_unedited_dependency_read_through_given_source(tmp_path):
    from opencrbasic.sources import DiskSource, OverlaySource
    lib = write(tmp_path, "Lib.crb", "Public otherName\n")
    m = write(tmp_path, "M.CR6", 'Include "Lib.crb"\nPublic oldName\nBeginProg\noldName=1\nEndProg\n')
    r = run(m)
    buf = OverlaySource(DiskSource(), {lib: b"Public newName\n"})      # unsaved buffer differs
    assert code(R.rename([r], m, 1, 8, "newName", source=buf)) == "stale"
    assert R.rename([r], m, 1, 8, "newName", source=OverlaySource(DiskSource())).ok


def test_edited_file_changed_after_prepare_is_stale(tmp_path):     # review R2 finding 2
    text = "Public oldName\nBeginProg\noldName=1\nEndProg\n"
    p = write(tmp_path, "M.CR6", text)
    r = run(p)
    assert R.prepare_rename([r], p, 0, 8).ok
    p.write_bytes(("' insert before analysed text\n" + text).encode())
    assert code(R.rename([r], p, 0, 8, "newName")) == "stale"
    assert code(R.prepare_rename([r], p, 0, 8)) == "stale"


def test_public_member_use_of_new_name_is_a_capture(tmp_path):     # review R2 finding 4
    p = write(tmp_path, "M.CR6", "Public oldName,out\nBeginProg\nout=Public.newName\nEndProg\n")
    r = run(p)
    res = R.rename([r], p, 0, 8, "newName")
    assert res is not None
    assert code(res) == "collision" and "3" in required(res.refusal).message
    assert R.rename([r], p, 0, 8, "fresh").ok


def test_public_member_use_of_the_target_is_renamed_and_other_members_are_captures(tmp_path):
    p = write(tmp_path, "M.CR6", "Public oldName,out\nBeginProg\nout=Public.oldName\nEndProg\n")
    res = R.rename([run(p)], p, 0, 8, "fresh")
    assert res.ok and (2, 11, 18) in spots(res, p)
    p2 = write(tmp_path, "N.CR6", "Public a,b\nBeginProg\nb=Public.zed\nEndProg\n")
    assert code(R.rename([run(p2)], p2, 0, 7, "zed")) == "collision"
    p3 = write(tmp_path, "K.CR6", "Const c = 1\nPublic a,b\nBeginProg\nb=Public.c\nEndProg\n")
    assert code(R.rename([run(p3)], p3, 1, 7, "c")) == "collision"


def test_member_use_of_other_container_is_not_a_collision(tmp_path):
    p = write(tmp_path, "M.CR6", "Public oldName,out\nPublic Other As Long\nBeginProg\n"
              "out=Status.newName\nEndProg\n")
    r = run(p)
    assert R.rename([r], p, 0, 8, "newName").ok


# ------------------------------------------------------------------ refusals: the position

def code(res) -> str:
    assert not res.ok and res.refusal is not None
    assert res.refusal.code in R.REASONS and res.refusal.message
    return res.refusal.code


def test_position_refusals(tmp_path):
    m = write(tmp_path, "m.CR6", """Public a, st$ As String * 4  ' a comment a
Const K = 5

BeginProg
  Scan(1,Sec,0,0)
    a = K + Status.Battery(1) + 1
    PanelTemp(a, 60)
    a = Len("a")
  NextScan
EndProg
""")
    r = run(m)
    assert code(R.rename([r], m, 0, 40, "X")) == "not_a_name"          # in the comment
    assert code(R.rename([r], m, 7, 14, "X")) == "not_a_name"          # in the string
    assert code(R.rename([r], m, 5, 13, "X")) == "external"             # Status
    assert code(R.rename([r], m, 6, 5, "X")) == "external"             # instruction PanelTemp
    assert code(R.rename([r], m, 7, 9, "X")) == "external"            # function Len
    assert not R.prepare_rename([r], m, 99, 0).ok
    assert code(R.rename([], m, 0, 7, "X")) == "no_results"


def test_unresolved_and_unmodelled_and_quoted(tmp_path):
    m = write(tmp_path, "m.CR6", """Public a
DataTable("Quoted",True,-1)
  Sample(1,a,FP2)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    a = missing + 1
    a = Quoted.a
  NextScan
EndProg
""")
    r = run(m)
    assert code(R.rename([r], m, 6, 9, "X")) == "unresolved"
    assert code(R.rename([r], m, 1, 12, "X")) == "quoted"
    # `Quoted.a`: the table is a quoted declaration, the field name is not modelled
    assert code(R.rename([r], m, 7, 15, "X")) == "unmodelled"
    # the variable `a` is also a table field name that `Quoted.a` may use
    assert code(R.rename([r], m, 0, 7, "X")) == "table_field"


def test_ambiguous_duplicate_declarations(tmp_path):
    m = write(tmp_path, "m.CR6", """Public a
Public a
BeginProg
  Scan(1,Sec,0,0)
    a = 1
  NextScan
EndProg
""")
    r = run(m)
    assert code(R.rename([r], m, 4, 4, "X")) == "ambiguous"
    assert code(R.rename([r], m, 0, 7, "X")) == "ambiguous"            # its reference is ambiguous


def test_unindexed_code_is_refused(tmp_path):
    m = write(tmp_path, "m.CR6", """Const Mode = 1
Public a
#If Mode = 1
Public b
#EndIf
BeginProg
  Scan(1,Sec,0,0)
    a = Mode
  NextScan
EndProg
""")
    r = run(m)
    assert code(R.rename([r], m, 0, 6, "Choice")) == "unindexed_code"     # #If Mode
    m2 = write(tmp_path, "n.CR6", """Public a
Public b
#If 1 = 0
b = a
#EndIf
BeginProg
  Scan(1,Sec,0,0)
    a = 1
  NextScan
EndProg
""")
    r2 = run(m2)
    assert code(R.rename([r2], m2, 0, 7, "X")) == "unindexed_code"        # inactive branch uses a
    assert required(R.rename([r2], m2, 1, 7, "X").refusal).code == "unindexed_code"   # b too
    m3 = write(tmp_path, "o.CR6", "Public a\nBeginProg\n  a = 1\nEndProg\na = 2\n")
    assert code(R.rename([run(m3)], m3, 0, 7, "X")) == "unindexed_code"     # after EndProg


def test_name_inside_a_string_is_refused(tmp_path):
    m = write(tmp_path, "m.CR6", """ConstTable(Cfg)
  Const Secs = 60
EndConstTable
Public a
BeginProg
  Scan(1,Sec,0,0)
    a = Secs + Len("Cfg.Secs")
  NextScan
EndProg
""")
    r = run(m)
    assert code(R.rename([r], m, 1, 8, "Ticks")) == "string_mention"        # Secs
    assert code(R.rename([r], m, 0, 12, "Config")) == "string_mention"      # Cfg
    assert R.rename([r], m, 3, 7, "b").ok
    assert code(R.rename([r], m, 3, 7, "Secs")) == "collision"
    m2 = write(tmp_path, "n.CR6", 'Public a\nBeginProg\n  a = Len("fresh")\nEndProg\n')
    assert code(R.rename([run(m2)], m2, 0, 7, "fresh")) == "string_mention"


# ------------------------------------------------------------------ refusals: the new name

def test_new_name_refusals(sample):
    p, r = sample
    for bad in ("", "1x", "a b", "x$", "x-y", "x.y", "é"):
        assert code(R.rename([r], p, 1, 6, bad)) == "invalid_name", bad
    for word in ("Public", "If", "Scan", "PanelTemp", "Len", "Status", "Settings", "NAN", "inf",
                 "Long", "Sec", "As", "then"):
        assert code(R.rename([r], p, 1, 6, word)) == "reserved_name", word
    assert code(R.rename([r], p, 1, 6, "K")) == "unchanged"
    assert code(R.rename([r], p, 1, 6, "arr")) == "collision"          # global Arr, other case
    assert code(R.rename([r], p, 1, 6, "Foo")) == "collision"          # a Sub
    assert code(R.rename([r], p, 0, 7, "p")) == "collision"            # a parameter somewhere
    assert code(R.rename([r], p, 2, 8, "K")) == "collision"            # local param vs global Const
    assert code(R.rename([r], p, 3, 6, "Arr")) == "collision"          # local Dim vs global
    assert code(R.rename([r], p, 2, 8, "a")) == "collision"            # param vs the Sub's own Dim a
    assert R.rename([r], p, 2, 8, "fresh").ok


def test_new_name_used_by_other_reference(tmp_path):
    m = write(tmp_path, "m.CR6", """Public a
BeginProg
  Scan(1,Sec,0,0)
    a = undeclared + 1
  NextScan
EndProg
""")
    r = run(m)
    assert code(R.rename([r], m, 0, 7, "undeclared")) == "collision"


# ------------------------------------------------------------------ several entries

LIB = """Public shared
Sub Bump()
  shared = shared + 1
EndSub
"""


def entry(tmp_path, name, extra=""):
    return write(tmp_path, name, f'''Include "lib.crb"
{extra}BeginProg
  Scan(1,Sec,0,0)
    Bump()
  NextScan
EndProg
''')


def test_two_entries_deduplicate(tmp_path):
    lib = write(tmp_path, "lib.crb", LIB)
    a = entry(tmp_path, "a.CR6", "Public aa\n")
    b = entry(tmp_path, "b.CR6", "Public bb\n")
    ra, rb = run(a), run(b)
    res = R.rename([ra, rb], lib, 0, 8, "total")
    assert res.ok and list(res.edits) == [lib]
    assert spots(res, lib) == [(0, 7, 13), (2, 2, 8), (2, 11, 17)]
    assert res.versions == {lib: hashlib.sha256(lib.read_bytes()).hexdigest()}


def test_collision_in_one_entry_refuses_all(tmp_path):
    lib = write(tmp_path, "lib.crb", LIB)
    ra = run(entry(tmp_path, "a.CR6", "Public aa\n"))
    rb = run(entry(tmp_path, "b.CR6", "Public total\n"))
    res = R.rename([ra, rb], lib, 0, 8, "total")
    assert code(res) == "collision"
    assert R.rename([ra], lib, 0, 8, "total").ok


def test_reference_in_entry_file_and_unrelated_entry_ignored(tmp_path):
    lib = write(tmp_path, "lib.crb", LIB)
    a = entry(tmp_path, "a.CR6", "Public aa\n")
    other = write(tmp_path, "c.CR6", "Public shared\nBeginProg\n  shared = 1\nEndProg\n")
    ra, rc = run(a), run(other)
    res = R.rename([ra, rc], lib, 0, 8, "total")            # c does not depend on lib.crb
    assert res.ok and list(res.edits) == [lib]
    res = R.rename([ra, rc], other, 0, 8, "total")
    assert res.ok and list(res.edits) == [other]


def test_entries_disagree_on_declaration_set(tmp_path):
    lib = write(tmp_path, "lib.crb", "Sub Bump()\n  v = v + 1\nEndSub\n")
    ra = run(entry(tmp_path, "a.CR6", "Public v\n"))
    rb = run(entry(tmp_path, "b.CR6", "Public pad\nPublic v\n"))
    ra2 = run(tmp_path / "a.CR6")
    assert code(R.rename([ra, rb], lib, 1, 2, "w")) == "entries_disagree"
    a = tmp_path / "a.CR6"                     # one entry: its own declaration decides
    res = R.rename([ra2], lib, 1, 2, "w")
    assert res.ok and set(res.edits) == {a, lib}


def test_included_twice_same_range(tmp_path):
    lib = write(tmp_path, "lib.crb", "Sub Bump()\n  Dim t\n  t = 1\nEndSub\n")
    m = write(tmp_path, "m.CR6", 'Include "lib.crb"\nInclude "lib.crb"\nBeginProg\nEndProg\n')
    r = run(m)
    res = R.rename([r], lib, 1, 6, "tmp2")
    assert res.ok and spots(res, lib) == [(1, 6, 7), (2, 2, 3)]
    # the Sub itself is declared twice: a reference would be ambiguous, the declaration alone is not
    res = R.rename([r], lib, 0, 5, "Bump2")
    assert not res.ok or spots(res, lib) == [(0, 4, 8)]


SHARED_ENTRY = 'Include "Lib.crb"\n{extra}BeginProg\nsharedValue = 1\nEndProg\n'


def shared(tmp_path, b_extra=""):
    """LSP-R3a (review A1): `Public sharedValue` in a library both A and B include and use."""
    lib = write(tmp_path, "Lib.crb", "Public sharedValue\n")
    a = write(tmp_path, "A.CR6", SHARED_ENTRY.format(extra=""))
    b = write(tmp_path, "B.CR6", SHARED_ENTRY.format(extra=b_extra))
    return lib, a, b


def test_rename_from_any_entry_edits_every_entry_of_the_declaration(tmp_path):
    lib, a, b = shared(tmp_path)
    results = [run(a), run(b)]
    want = {lib: [(0, 7, 18)], a: [(2, 0, 11)], b: [(2, 0, 11)]}
    for path, line in ((a, 2), (b, 2), (lib, 0)):
        col = 3 if path != lib else 9
        res = R.rename(results, path, line, col, "renamedValue")
        assert res.ok, res.refusal
        assert {p: spots(res, p) for p in res.edits} == want
        assert set(res.versions) == set(want)
        pr = R.prepare_rename(results, path, line, col)
        assert pr.ok and pr.files == frozenset(want)
    res = R.rename(results, a, 2, 3, "renamedValue")
    for p, edits in res.edits.items():
        p.write_bytes(apply(p.read_bytes(), edits))
    assert all(run(e).ok for e in (a, b))


def test_collision_only_in_the_other_entry_is_refused(tmp_path):
    lib, a, b = shared(tmp_path, b_extra="Public renamedValue\n")
    results = [run(a), run(b)]
    for path, line, col in ((a, 2, 3), (b, 3, 3), (lib, 0, 9)):
        assert code(R.rename(results, path, line, col, "renamedValue")) == "collision"
    assert R.rename(results[:1], a, 2, 3, "renamedValue").ok       # A alone has no collision


def test_entry_using_an_edited_line_without_the_declaration_is_refused(tmp_path):
    lib, a, _ = shared(tmp_path)
    write(tmp_path, "Use.crb", "Sub Touch()\n  sharedValue = 2\nEndSub\n")
    a.write_text('Include "Lib.crb"\nInclude "Use.crb"\nBeginProg\nsharedValue = 1\nEndProg\n')
    c = write(tmp_path, "C.CR6", 'Public sharedValue\nInclude "Use.crb"\nBeginProg\nEndProg\n')
    ra, rc = run(a), run(c)
    assert R.rename([ra], lib, 0, 9, "renamedValue").ok
    assert code(R.rename([ra, rc], lib, 0, 9, "renamedValue")) == "entries_disagree"
    assert code(R.rename([ra, rc], a, 3, 3, "renamedValue")) == "entries_disagree"


# ------------------------------------------------------------------ semantic preservation sweep

_BOUND = "(?<![A-Za-z0-9])"


def _norm(text: str, name: str) -> str:
    return re.sub(f"{_BOUND}{re.escape(name)}(?![A-Za-z0-9])", "\x00", text, flags=re.I)


def _facts(path: Path) -> dict:
    res = compile_file(path, profiles.get("cr6"), signature=False)
    tables = []
    for t in res.tables:
        tables.append(repr(dataclasses.replace(t, fields=[])))
        tables.extend(repr(f) for f in t.fields)
    return {"ok": res.ok, "mode": res.mode, "lines": list(res.result_lines()), "tables": tables,
            "list": (res.table_list or "").split("\n")}


def _same_facts(base: dict, new: dict, old_name: str, new_name: str) -> list[str]:
    """Differences between the compile of the original and of the renamed program. Texts are equal
    when they match after replacing the old name (in the original) and the new name (in the
    renamed program) by one placeholder, or when they are equal as written (names that come from
    strings or from fixed names such as the `Status` table do not follow the rename)."""
    bad = []
    for k in ("ok", "mode"):
        if base[k] != new[k]:
            bad.append(k)
    for k in ("lines", "tables", "list"):
        if len(base[k]) != len(new[k]):
            bad.append(k)
            continue
        for b, n in zip(base[k], new[k]):
            if b != n and _norm(b, old_name) != _norm(n, new_name):
                bad.append(k)
                break
    return bad


def sweep(stride: int = 1, offset: int = 0, verbose: bool = False, workdir: Path | None = None):
    """Rename every `stride`-th program-declared name of `../crbasic/*.CR6` (and the libraries
    they include) and compile the affected entries. Returns (accepted, refused Counter, problems)."""
    work = workdir
    assert work is not None
    shutil.copytree(CRBASIC, work, dirs_exist_ok=True)
    entries = sorted(work.glob("*.CR6"))
    results = [run(e) for e in entries]
    base = {e: _facts(e) for e in entries}
    original = {p: p.read_bytes() for p in work.iterdir() if p.is_file()}
    targets = {}
    for r in results:
        nav = R._state(r).nav
        assert nav is not None
        nav._build()
        for d in required(nav.index).declarations:
            site = nav._site_of.get(id(d))
            if site is None or site.path is None:
                continue
            targets.setdefault((site.path, site.line, site.start), d)
    ordered = sorted(targets, key=lambda k: (str(k[0]), k[1], k[2]))[offset::stride]
    accepted = 0
    refused: Counter = Counter()
    problems = []
    for n, (path, line, col) in enumerate(ordered):
        affected = [r for r in results if any(Path(q) == path for q in r.dependencies.reads)]
        fresh = f"Rn{n}x"
        old_name = required(targets[(path, line, col)]).name
        res = R.rename(affected, path, line, col, fresh)
        assert res is not None
        if not res.ok:
            refused[required(res.refusal).code] += 1
            if verbose:
                print("refused", path.name, line + 1, old_name, required(res.refusal).code, required(res.refusal).message)
            continue
        accepted += 1
        try:
            for p, edits in res.edits.items():
                p.write_bytes(apply(original[p], edits))
            for r in affected:
                e = r.request.entry
                bad = _same_facts(base[e], _facts(e), old_name, fresh)
                if bad:
                    problems.append((path.name, line + 1, old_name, e.name, bad))
        finally:
            for p in res.edits:
                p.write_bytes(original[p])
    return accepted, refused, problems


@pytest.mark.slow
def test_sweep_semantic_preservation(tmp_path):
    full = os.environ.get("RENAME_SWEEP") == "full"
    accepted, refused, problems = sweep(1 if full else 23, 0, verbose=full, workdir=tmp_path / "w")
    print(f"\nRENAME SWEEP accepted={accepted} refused={sum(refused.values())} "
          f"by_reason={dict(refused)} problems={len(problems)}")
    for p in problems[:20]:
        print("PROBLEM", p)
    assert accepted > 0
    assert problems == []


# ------------------------------------------------- LSP-R3b: token-exact rename (review round 3)


F1_TEXT = """Const N = 3
Public arr(3)
Sub Fill(p(sz) As Float, sz As Long, q(N) As Float)
  Dim i
  For i = 1 To sz
    p(i) = i
  Next
EndSub
BeginProg
  Scan(1,Sec,0,0)
    Fill(arr, 3, arr)
  NextScan
EndProg
"""


def test_r3b_f1_parameter_named_in_an_array_dimension(tmp_path):
    p = write(tmp_path, "M.CR6", F1_TEXT)
    r = run(p)
    assert r.ok
    res = R.rename([r], p, *pos(p, "To sz", offset=3), "cnt")
    assert res.ok, res.refusal
    assert spots(res, p) == [(2, 11, 13), (2, 25, 27), (4, 15, 17)]
    p.write_bytes(apply(p.read_bytes(), res.edits[p]))
    assert b"Sub Fill(p(cnt) As Float, cnt As Long" in p.read_bytes()
    assert run(p).ok


def test_r3b_f1_const_used_as_a_parameter_dimension_is_renamed(tmp_path):
    p = write(tmp_path, "M.CR6", F1_TEXT)
    r = run(p)
    res = R.rename([r], p, *pos(p, "Const N", offset=6), "M3")
    assert res.ok, res.refusal
    assert spots(res, p) == [(0, 6, 7), (2, 39, 40)]
    p.write_bytes(apply(p.read_bytes(), res.edits[p]))
    assert run(p).ok


def test_r3b_f2_alias_junk_token_does_not_shift_ordinals(tmp_path):
    p = write(tmp_path, "M.CR6", "Public arr(2)\nAlias arr(1) = a1 a2, a2\nBeginProg\n"
              "  Scan(1,Sec,0,0)\n    a2 = 1\n  NextScan\nEndProg\n")
    r = run(p)
    assert r.ok
    res = R.rename([r], p, *pos(p, "a2 = 1"), "b2")     # the declaration, not the ignored `a2`
    assert res.ok and spots(res, p) == [(1, 22, 24), (4, 4, 6)]
    p.write_bytes(apply(p.read_bytes(), res.edits[p]))
    assert run(p).ok
    res = R.rename([run(p)], p, *pos(p, "a1"), "b1")
    assert res.ok and spots(res, p) == [(1, 15, 17)]


STRUCT_TEXT = """StructureType Pt
  Lat As Float
  Lon As Float
EndStructureType
Public P As Pt
Public out
DataTable(T,True,-1)
  Sample(1,P,Pt)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    P.Lat = 1
    out = T.Lat
    CallTable T
  NextScan
EndProg
"""


def test_r3b_f3_structure_member_named_by_a_table_field_is_refused(tmp_path):
    p = write(tmp_path, "M.CR6", STRUCT_TEXT)
    r = run(p)
    res = R.rename([r], p, *pos(p, "Lat As Float"), "Y")
    assert res is not None
    assert not res.ok and required(res.refusal).code == "table_field"
    res = R.rename([r], p, *pos(p, "Lon As Float"), "Lat2")
    assert res.ok, res.refusal                       # no `T.Lon` use
    p2 = write(tmp_path, "N.CR6", STRUCT_TEXT.replace("out = T.Lat", "out = T.Q_Avg"))
    r2 = run(p2)
    res = R.rename([r2], p2, *pos(p2, "Lon As Float"), "Q")     # new name derives T.Q_Avg
    assert res is not None
    assert not res.ok and required(res.refusal).code == "table_field"


def test_r3b_f14a_case_only_rename_from_a_differently_spelled_reference(tmp_path):
    p = write(tmp_path, "M.CR6", "Public total\nBeginProg\n  Scan(1,Sec,0,0)\n    TOTAL = 1\n"
              "  NextScan\nEndProg\n")
    r = run(p)
    res = R.rename([r], p, *pos(p, "TOTAL"), "total")
    assert res.ok, res.refusal
    assert [e.new_text for e in res.edits[p]] == ["total", "total"]
    res = R.rename([r], p, *pos(p, "TOTAL"), "TOTAL")
    assert res.ok                                    # the declaration is spelled `total`
    p.write_bytes(p.read_bytes().replace(b"TOTAL", b"total"))
    r = run(p)
    res = R.rename([r], p, *pos(p, "total", nth=1), "total")
    assert res is not None
    assert not res.ok and required(res.refusal).code == "unchanged"


def test_r3b_f14b_case_only_rename_is_not_a_collision_with_a_local(tmp_path):
    p = write(tmp_path, "M.CR6", "Public a\nSub S\n  Dim a\n  a = 2\nEndSub\nBeginProg\n"
              "  Scan(1,Sec,0,0)\n    a = 1\n    S\n  NextScan\nEndProg\n")
    r = run(p)
    res = R.rename([r], p, *pos(p, "Public a", offset=7), "A")
    assert res.ok, res.refusal
    assert spots(res, p) == [(0, 7, 8), (7, 4, 5)]
    res = R.rename([r], p, *pos(p, "Public a", offset=7), "S")
    assert res is not None
    assert not res.ok and required(res.refusal).code == "collision"


def test_r3b_f16_new_name_in_an_unparsed_statement_is_refused(tmp_path):
    p = write(tmp_path, "M.CR6", "Public x\nStatic y\nBeginProg\n  Scan(1,Sec,0,0)\n    x = 1\n"
              "  NextScan\nEndProg\n")
    r = run(p)
    res = R.rename([r], p, *pos(p, "Public x", offset=7), "y")
    assert res is not None
    assert not res.ok and required(res.refusal).code == "unparsed"
    res = R.rename([r], p, *pos(p, "Public x", offset=7), "z")
    assert res.ok, res.refusal


def test_click_in_a_comment_line_or_inactive_branch_is_not_a_name(tmp_path):
    """LSP-R4a (host H-10): the file is read, the line is not code any entry parses."""
    m = write(tmp_path, "m.CR6", """' sharedValue in a comment
Public a
#If 1 = 0
Public z
#EndIf
BeginProg
  a = 1
EndProg
""")
    r = run(m)
    for line, col in ((0, 4), (3, 8)):
        res = R.prepare_rename([r], m, line, col)
        assert res is not None
        assert code(res) == "not_a_name"
        assert "not in code any entry parses" in required(res.refusal).message
    other = write(tmp_path, "o.CR6", "Public q\nBeginProg\nEndProg\n")
    res = R.prepare_rename([run(other)], m, 0, 4)                   # an entry not reading m
    assert res is not None
    assert code(res) == "no_results" and "depends on the file" in required(res.refusal).message
