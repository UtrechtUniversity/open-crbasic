
import contextlib
import io
import shutil
from pathlib import Path

import pytest

from opencrbasic import cli, profiles
from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.commands import (CompilerOutput, compiler_output, render_table_fields,
                                  table_fields)
from opencrbasic.tables import build_tables

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT.parent / "crbasic").resolve()
CHM = sorted((ROOT / "tests" / "corpus" / "chm").glob("*.CR6"))
CRBASIC_PROGRAMS = sorted(CRBASIC.glob("*.CR6"))


def _cli_stdout(path: Path, logger: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["--logger", logger, "--no-message-file", str(path)])
    return code, buf.getvalue()


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob("*")}


@pytest.fixture(scope="module")
def crbasic_copy(tmp_path_factory):
    dst = tmp_path_factory.mktemp("crbasic")
    for p in CRBASIC.iterdir():
        if p.is_file():
            shutil.copy2(p, dst / p.name)
    return dst


@pytest.mark.parametrize("program", CRBASIC_PROGRAMS, ids=lambda p: p.name)
def test_equals_cli_stdout_for_crbasic(program, crbasic_copy, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, expected = _cli_stdout(crbasic_copy / program.name, "cr6")
    before_cwd, before_src = _tree(tmp_path), _tree(CRBASIC)
    out = compiler_output(program, "cr6")
    assert isinstance(out, CompilerOutput)
    assert out.text == expected
    assert out.ok == (code == 0)
    assert out.program_name == program.name
    assert _tree(tmp_path) == before_cwd and _tree(CRBASIC) == before_src


@pytest.mark.parametrize("sample", CHM[::25], ids=lambda p: p.name)
def test_equals_cli_stdout_for_chm_sample(sample, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    shutil.copy2(sample, work / sample.name)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    code, expected = _cli_stdout(work / sample.name, "cr6")
    before = _tree(tmp_path)
    out = compiler_output(sample, "cr6")
    assert out.text == expected
    assert out.ok == (code == 0)
    assert _tree(tmp_path) == before


def test_unsaved_overlay_matches_cli_on_a_saved_copy(crbasic_copy, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    name = "Main.CR6"
    original = (CRBASIC / name).read_bytes()
    broken = original.replace(b"BeginProg", b"BeginProg\n  Nope = Undefined1", 1)
    assert broken != original
    work = tmp_path / "work"
    shutil.copytree(crbasic_copy, work)
    (work / name).write_bytes(broken)
    code, expected = _cli_stdout(work / name, "cr6")
    assert code == 1 and "line " in expected
    disk_before = (CRBASIC / name).read_bytes()
    out = compiler_output(CRBASIC / name, "cr6", {CRBASIC / name: broken})
    assert out.text == expected and not out.ok
    assert (CRBASIC / name).read_bytes() == disk_before
    assert compiler_output(CRBASIC / name, "cr6").text != expected


def test_overlay_only_entry_and_missing_entry(tmp_path):
    entry = tmp_path / "Unsaved.CR6"
    src = b"Public X\n\nBeginProg\n  Scan(1,Sec,0,0)\n    X = 1\n  NextScan\nEndProg\n"
    out = compiler_output(entry, "cr6", {entry: src})
    assert out.ok and out.text.startswith("Unsaved.CR6 -- Compiled in ")
    assert not entry.exists() and not list(tmp_path.iterdir())
    missing = compiler_output(entry, "cr6")
    assert not missing.ok and missing.text.endswith("No Program File\n")


def test_max_file_bytes_refuses_the_entry_and_its_includes(tmp_path):
    entry, lib = tmp_path / "E.CR6", tmp_path / "Lib.crb"
    entry.write_text('Public x\nInclude("CPU:Lib.crb")\nBeginProg\nx = lib_v\nEndProg\n')
    lib.write_text("Public lib_v\n" + "' pad\n" * 30)
    plain = compiler_output(entry, "cr6")
    assert compiler_output(entry, "cr6", max_file_bytes=10_000) == plain and not plain.oversized
    over = compiler_output(entry, "cr6", max_file_bytes=100)
    assert over.text == "" and not over.ok and over.oversized == {lib: lib.stat().st_size}
    tiny = compiler_output(entry, "cr6", max_file_bytes=10)
    assert tiny.text == "" and tiny.oversized == {entry: entry.stat().st_size}
    small = {lib: b"Public lib_v\n"}                   # an overlay under the limit replaces it
    assert compiler_output(entry, "cr6", small, 100) == compiler_output(entry, "cr6", small)


def test_other_logger_matches_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = CHM[0]
    work = tmp_path / "w"
    work.mkdir()
    shutil.copy2(sample, work / sample.name)
    _, expected = _cli_stdout(work / sample.name, "cr300")
    assert compiler_output(sample, "cr300").text == expected


@pytest.mark.parametrize("program", CRBASIC_PROGRAMS, ids=lambda p: p.name)
def test_table_fields_match_tdf_derivation(program):
    res = analyze(AnalysisRequest(program, "cr6"))
    tables = table_fields(res)
    if not res.ok:
        assert tables == []
        return
    tdf_tables, _, _ = build_tables(res.program, profiles.get("cr6"), dict(res.program.consts))
    assert [t.name for t in tables] == [t.name for t in tdf_tables]
    for info, ref in zip(tables, tdf_tables):
        assert [f.name for f in info.fields] == [f.name for f in ref.fields]
        assert [f.units for f in info.fields] == [f.units or None for f in ref.fields]
        assert [f.process for f in info.fields] == [f.processing or None for f in ref.fields]
    assert tables and tables[0].name == "Status"


def test_table_fields_of_a_small_program_and_markdown():
    src = (b"Public Temp, Batt\nUnits Temp = degC\n\nDataTable (Hourly, True, -1)\n"
           b"  DataInterval (0, 5, Min, 10)\n  Average (1, Temp, FP2, False)\n"
           b"  Minimum (1, Batt, FP2, False, False)\nEndTable\n\n"
           b"BeginProg\n  Scan (1, Sec, 0, 0)\n    Temp = 1\n    CallTable Hourly\n  NextScan\nEndProg\n")
    entry = Path("/nonexistent-dir/Small.CR6")
    res = analyze(AnalysisRequest(entry, "cr6", {entry: src}))
    assert res.ok
    tables = {t.name: t for t in table_fields(res)}
    hourly = tables["Hourly"]
    assert hourly.interval == "5 MIN"
    assert [(f.name, f.units, f.process) for f in hourly.fields] == [
        ("Temp_Avg", "degC", "Avg"), ("Batt_Min", None, "Min")]
    assert "Public" in tables and "Status" in tables
    md = render_table_fields(list(tables.values()))
    assert "## Hourly (interval 5 MIN)" in md
    assert "| Temp_Avg | FP2 | degC | Avg |" in md


def test_table_fields_empty_for_failed_compile():
    entry = Path("/nonexistent-dir/Bad.CR6")
    res = analyze(AnalysisRequest(entry, "cr6", {entry: b"BeginProg\n  X = Nope\nEndProg\n"}))
    assert not res.ok and table_fields(res) == []
    assert "does not compile" in render_table_fields([])
