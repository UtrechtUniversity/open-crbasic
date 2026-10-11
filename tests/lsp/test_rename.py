"""LSP-07a: protocol-independent rename (`opencrbasic.rename`). Policies:
docs/lsp-api.md "Rename (LSP-07a)".

The semantic-preservation sweep renames program-declared names of `../crbasic` to fresh names in
tmp copies, compiles the affected entries and compares them with the unrenamed compile. By default
a stride sample runs; `RENAME_SWEEP=full` runs every declaration and prints the counts."""

from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT.parent / "crbasic").resolve()


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
    line, col = pos(p, "a", 0, 7 - 0)[0], 7
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
    assert res.refusal.code == "collision"
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
    assert not res.ok and res.refusal.code == "stale"
    assert R.prepare_rename([r], p, 0, 7).refusal.code == "stale"


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
    assert R.rename([r2], m2, 1, 7, "X").refusal.code == "unindexed_code"   # b too
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
    shutil.copytree(CRBASIC, work, dirs_exist_ok=True)
    entries = sorted(work.glob("*.CR6"))
    results = [run(e) for e in entries]
    base = {e: _facts(e) for e in entries}
    original = {p: p.read_bytes() for p in work.iterdir() if p.is_file()}
    targets = {}
    for r in results:
        nav = R._state(r).nav
        nav._build()
        for d in nav.index.declarations:
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
        old_name = targets[(path, line, col)].name
        res = R.rename(affected, path, line, col, fresh)
        if not res.ok:
            refused[res.refusal.code] += 1
            if verbose:
                print("refused", path.name, line + 1, old_name, res.refusal.code, res.refusal.message)
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


def test_sweep_semantic_preservation(tmp_path):
    full = os.environ.get("RENAME_SWEEP") == "full"
    accepted, refused, problems = sweep(1 if full else 23, 0, verbose=full, workdir=tmp_path / "w")
    print(f"\nRENAME SWEEP accepted={accepted} refused={sum(refused.values())} "
          f"by_reason={dict(refused)} problems={len(problems)}")
    for p in problems[:20]:
        print("PROBLEM", p)
    assert accepted > 0
    assert problems == []
