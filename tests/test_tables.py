"""Tests for `tables.py`: TDF (`-z`) and table-list (`-l`) generation against the real compiler.

For the 16 `crbasic/*.CR6` entry programs this uses cached oracle fixtures (`tests/fixtures/tdf/`,
refreshed by `python3 tools/tdftest.py --group repo --write-fixtures`) so the tests run without
Wine. When Wine *is* available (`tools.tdftest.run_oracle_tdf`), the same fixtures are re-verified
live and refreshed. Auto-allocated (`Size=-1`) table sizes are known-incomplete (see
`docs/tdf-rules.md` section 10 and section 13's "mixed regime" note) -- programs whose only
difference from the oracle is such a size are reported as an expected, separate category rather
than a hard failure, matching the task's acceptance criterion.
"""

import base64
import json
import sys
from pathlib import Path

import pytest

OPENCRBASIC_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = OPENCRBASIC_ROOT.parent
CRBASIC_DIR = REPO_ROOT / "crbasic"
FIXTURES_DIR = OPENCRBASIC_ROOT / "tests" / "fixtures" / "tdf"

sys.path.insert(0, str(OPENCRBASIC_ROOT / "tools"))

from opencrbasic import parser, profiles, tdf  # noqa: E402
from opencrbasic.tables import build_tables  # noqa: E402
from opencrbasic.tables_hook import attach  # noqa: E402

CRBASIC_PROGRAMS = sorted(CRBASIC_DIR.glob("*.CR6")) if CRBASIC_DIR.is_dir() else []


def _try_oracle():
    try:
        import tdftest
    except ImportError:
        return None
    if sys.platform != "win32":
        import shutil

        wine = (
            Path.home() / ".local" / "wine" / "Wine Devel.app" / "Contents" / "Resources"
            / "wine" / "bin" / "wine"
        )
        if not wine.is_file() and not shutil.which("wine"):
            return None
    return tdftest


TDFTEST = _try_oracle()


def _expected(path: Path):
    """(tdf_bytes, lst_bytes) from a live oracle run (cached) or a committed fixture."""
    if TDFTEST is not None:
        try:
            result = TDFTEST.run_oracle_tdf(path, CRBASIC_DIR)
        except Exception:
            result = None
        if result is not None and result["ok"] and result["tdf"] is not None:
            return result["tdf"], result["lst"] or b""
    fixture = FIXTURES_DIR / f"repo_{path.stem}.json"
    if fixture.is_file():
        rec = json.loads(fixture.read_text())
        return base64.b64decode(rec["tdf_b64"]), base64.b64decode(rec["lst_b64"])
    return None, None


@pytest.mark.parametrize("path", CRBASIC_PROGRAMS, ids=lambda p: p.name)
def test_table_list_byte_identical(path):
    """The `-l` text: deterministic (no auto-size ambiguity affects it), so this must match
    exactly for every program the real compiler accepts."""
    theirs_tdf, theirs_lst = _expected(path)
    if theirs_tdf is None:
        pytest.skip("no oracle and no cached fixture for this program")

    program, diags = parser.parse_file(path)
    assert not diags.errors, diags.errors
    profile = profiles.get("cr6")
    tables, table_list, _ = build_tables(program, profile, dict(program.consts))
    assert table_list.encode("latin-1", "replace") == theirs_lst


@pytest.mark.parametrize("path", CRBASIC_PROGRAMS, ids=lambda p: p.name)
def test_tdf_byte_identical_or_only_autosize_differs(path):
    """The `-z` TDF: byte-identical is the target; a difference confined to an auto-allocated
    (`Size=-1`) table's `size` field is reported (xfail) rather than failing outright, per
    docs/tdf-rules.md section 10/13 (`_MIXED_POOL_BY_EVENT_COUNT` is a best-effort approximation,
    not a derived formula)."""
    theirs_tdf, _ = _expected(path)
    if theirs_tdf is None:
        pytest.skip("no oracle and no cached fixture for this program")

    program, diags = parser.parse_file(path)
    assert not diags.errors, diags.errors
    profile = profiles.get("cr6")
    tables, _, _ = build_tables(program, profile, dict(program.consts))
    ours_tdf = tdf.serialize(tables)

    if ours_tdf == theirs_tdf:
        return

    _, theirs_tables = tdf.parse(theirs_tdf)
    only_size_diffs = True
    for t1, t2 in zip(theirs_tables, tables):
        if t1.name != t2.name:
            only_size_diffs = False
            break
        t2_saved_size = t2.size
        t2.size = t1.size  # neutralise the size field, compare everything else
        if tdf.serialize([t1]) != tdf.serialize([t2]):
            only_size_diffs = False
        t2.size = t2_saved_size
        if not only_size_diffs:
            break
    if only_size_diffs:
        pytest.xfail("auto-allocated table size(s) differ; see docs/tdf-rules.md section 10/13")
    assert ours_tdf == theirs_tdf


def test_attach_sets_result_tables_and_table_list():
    """`tables_hook.attach` (what `checker.py` is meant to call) sets both `CompileResult`
    fields, for at least one real program."""
    from opencrbasic.compiler import CompileResult
    from opencrbasic.diagnostics import Diagnostics

    path = CRBASIC_DIR / "Main.CR6"
    if not path.is_file():
        pytest.skip("crbasic/Main.CR6 not present")
    program, diags = parser.parse_file(path)
    profile = profiles.get("cr6")
    result = CompileResult("Main.CR6", profile, Diagnostics())
    attach(program, profile, result)
    assert result.tables and any(t.name == "Status" for t in result.tables)
    assert result.table_list and "Table:" in result.table_list


# ------------------------------------------------------------------ robustness
#
# `docs/fuzz-findings.md` "Crash"/"Hang": neither reproducer should ever reach `build_tables`
# (`checker/structure.py`'s array-dimension check rejects the first before `tables_hook.attach`
# would run at all, and `checker/instructions.py`'s -- not this module's -- own `Scan` value check
# is the intended fix for the second), but `tables.py` itself must still never crash or hang if
# either ever *did* reach it, e.g. through some other path this project has not found yet.


def test_scan_weights_never_crashes_on_a_non_numeric_interval():
    # tests/corpus/fuzz/cr6/crash/ce0f811dd89e.CR6, minimized: `Scan("fuzz",Sec,0,0)` used to raise
    # `ValueError: could not convert string to float: 'fuzz'` inside `_scan_weights`, reachable for
    # *any* `Scan` block regardless of whether a table is even called inside it.
    text = (
        'DataTable (T,-1,-1)\n  Sample (1,X,FP2)\nEndTable\n'
        'Public X As Float\nBeginProg\n'
        '  Scan("fuzz",Sec,0,0)\n    CallTable T\n  NextScan\n'
        'EndProg\n'
    )
    program, diags = parser.parse_text(text, "t.CR6")
    tables, table_list_text, _ = build_tables(program, profiles.get("cr6"), dict(program.consts))
    assert any(t.name == "T" for t in tables)


def test_explode_indices_is_bounded():
    # `docs/fuzz-findings.md` "Hang": `_explode_indices`/`render_table_list` must never try to
    # enumerate an unbounded number of index tuples, regardless of how large a field's `dimension`
    # claims to be -- defense in depth alongside `checker/structure.py`'s declaration-time check,
    # for any `TdfField` this module derives a huge `dimension` for by some other path.
    from opencrbasic.tables import _explode_indices, _MAX_EXPLODE_COUNT

    out = list(_explode_indices([999_999_999_999], False, 1, 999_999_999_999))
    assert len(out) == _MAX_EXPLODE_COUNT
