"""`-u`: `%s declared but not used.` (`checker/unused.py`, `docs/cli.md` "-u").

Fixtures under `tests/fixtures/cli/` were captured from the real exe under Wine, the same way as
`test_cli.py`'s. `unused_sample.CR6` is a small scratch program exercising: an unused `Public`
scalar/array/`Dim`, an aliased `Public` that *is* used only via its alias name, an unused `Sub`
parameter and an unused `Sub`-local `Dim`, an uncalled `Sub` and an uncalled `Function` (both
flagged identically), a `Const` (never flagged), and a fixed-size `DataTable` that is never
`CallTable`d (the pre-existing "table declared but never called" warning, to check it still
prints *after* every `-u` warning). `unused_multi/` exercises `Include`d files: two library files,
each with one unused `Public` and one uncalled `Sub`, to check the cross-file ordering fix in
`compiler.CompileResult.result_lines` (grouped by `Include` order, ascending by each file's own
line numbers within a group -- not a single global line sort, which does not make sense once
different files restart their own line numbering at 1).
"""

import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "cli"


def _run(tmp_path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "opencrbasic", *args],
        cwd=tmp_path,
        capture_output=True,
        text=False,
        env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )


def test_u_matches_exe_on_sample_program(tmp_path):
    (tmp_path / "unused_sample.CR6").write_bytes((FIXTURES / "unused_sample.CR6").read_bytes())
    _run(tmp_path, ["-u", "unused_sample.CR6", "out.msg"])
    got = (tmp_path / "out.msg").read_bytes()
    expected = (FIXTURES / "unused_sample_expected.msg").read_bytes()
    assert got == expected


def test_u_omitted_adds_no_warnings(tmp_path):
    """`-u` is opt-in (`docs/cli.md`): without it, none of `unused_sample.CR6`'s declarations are
    flagged, even though every one of them is genuinely unused."""
    (tmp_path / "unused_sample.CR6").write_bytes((FIXTURES / "unused_sample.CR6").read_bytes())
    _run(tmp_path, ["unused_sample.CR6", "out.msg"])
    got = (tmp_path / "out.msg").read_bytes()
    assert b"declared but not used" not in got
    assert b"Table NeverCalled" in got  # the *other* warning still fires unconditionally


def test_u_multi_file_matches_exe_include_order(tmp_path):
    d = FIXTURES / "unused_multi"
    for name in ("main_multi.CR6", "libA.crb", "libB.crb"):
        (tmp_path / name).write_bytes((d / name).read_bytes())
    _run(tmp_path, ["-u", "main_multi.CR6", "out.msg"])
    got = (tmp_path / "out.msg").read_bytes()
    expected = (d / "main_multi_expected.msg").read_bytes()
    assert got == expected


def test_u_suppressed_by_any_compile_error(tmp_path):
    """Oracle-confirmed (`checker/unused.py`'s docstring): a single unrelated error anywhere in
    the file removes every `-u` warning, not just the ones after it."""
    (tmp_path / "bad.CR6").write_text(
        "Public UnusedVar As Float\n"
        "BeginProg\n"
        "  UnusedVar = ThisNameIsNotDefinedAnywhere\n"
        "  Scan (1,Sec,0,0)\n"
        "  NextScan\n"
        "EndProg\n"
    )
    result = _run(tmp_path, ["-u", "bad.CR6", "out.msg"])
    assert result.returncode == 1
    got = (tmp_path / "out.msg").read_bytes()
    assert b"declared but not used" not in got


def test_unused_check_unit_alias_and_table_source():
    """Direct unit coverage of `checker.unused.check`'s two subtler rules, without needing Wine:
    a variable referenced only as a `DataTable` output source counts as used, and aliasing merges
    the alias name and the original into one usage identity (`docs/tdf-rules.md` §8, re-derived
    for `-u` by oracle probing, see `checker/unused.py`'s own docstring)."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
    from opencrbasic import parser
    from opencrbasic.checker import unused
    from opencrbasic.diagnostics import Diagnostics

    text = (
        "Public TableSource As Float\n"
        "Public AliasBase As Float\n"
        "Alias AliasBase = AliasTarget\n"
        "DataTable(T,1,-1)\n"
        "  Sample(1,TableSource,FP2)\n"
        "EndTable\n"
        "BeginProg\n"
        "  AliasTarget = 1\n"
        "  Scan (1,Sec,0,0)\n"
        "    CallTable T\n"
        "  NextScan\n"
        "EndProg\n"
    )
    program, diags = parser.parse_text(text, "t.CR6")
    assert not diags.errors
    out = Diagnostics()
    unused.check(program, out)
    assert out.items == []  # neither TableSource nor AliasBase is flagged


def test_unused_check_flags_genuinely_unused_names():
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
    from opencrbasic import parser
    from opencrbasic.checker import unused
    from opencrbasic.diagnostics import Diagnostics

    text = (
        "Public NeverUsed As Float\n"
        "Const StillIgnored = 1\n"
        "BeginProg\n"
        "  Scan (1,Sec,0,0)\n"
        "  NextScan\n"
        "EndProg\n"
    )
    program, diags = parser.parse_text(text, "t.CR6")
    assert not diags.errors
    out = Diagnostics()
    unused.check(program, out)
    messages = [d.message for d in out.items]
    assert messages == ["NeverUsed declared but not used."]
