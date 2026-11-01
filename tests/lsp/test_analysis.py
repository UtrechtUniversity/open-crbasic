
import subprocess
import sys
from pathlib import Path

import pytest

from opencrbasic import profiles
from opencrbasic.analysis import API_VERSION, AnalysisRequest, analyze
from opencrbasic.compiler import compile_file

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT / "tests" / "corpus" / "firnmelt").resolve()
CHM = sorted((ROOT / "tests" / "corpus" / "chm").glob("*.CR6"))

LIB = b"Public LibVar\n"
MAIN = (b'Include("CPU:Lib.crb")\n\nPublic X\n\nBeginProg\n  X = LibVar\n  Scan(1,Sec,0,0)\n'
        b'  NextScan\nEndProg\n')


def _key(d):
    return (d.severity, d.line, d.message, d.file)


def _direct(entry, logger, signature=True):
    result = compile_file(entry, profiles.get(logger), warn_unused=True)
    items = sorted(result.diagnostics.items, key=result._sort_key)
    return (result.ok, result.mode, [_key(d) for d in items],
            result.result_lines(show_signature=signature))


def _siblings(entry):
    return {p: p.read_bytes() for p in entry.parent.iterdir() if p.is_file()}


def _check_equal(entry, logger, overlays, memo):
    ok, mode, diags, lines = _direct(entry, logger)
    for ov in ({}, overlays):
        request = AnalysisRequest(entry, logger, ov, warn_unused=True, signature=True)
        res = analyze(request) if ov else memo(request)
        assert res.api_version == API_VERSION == 2
        assert (res.ok, res.mode, [_key(d) for d in res.diagnostics], list(res.rendered_lines)) == (
            ok, mode, diags, lines), entry


@pytest.mark.parametrize("entry", sorted(CRBASIC.glob("*.CR6")), ids=lambda p: p.name)
def test_crbasic_overlay_equals_disk(entry, memo_analyze):
    _check_equal(entry, "cr6", _siblings(entry), memo_analyze)


@pytest.mark.slow
@pytest.mark.skipif(not CHM, reason="CRBasic help examples not extracted; see tests/corpus/README.md")
def test_chm_corpus_overlay_equals_disk_cr6(memo_analyze):
    assert CHM
    for entry in CHM:
        _check_equal(entry, "cr6", {entry: entry.read_bytes()}, memo_analyze)


@pytest.mark.parametrize("logger", ["cr1000x", "cr300", "cr350"])
def test_chm_sample_overlay_equals_disk_other_loggers(logger, memo_analyze):
    for entry in CHM[:: max(1, len(CHM) // 30)]:
        _check_equal(entry, logger, {entry: entry.read_bytes()}, memo_analyze)


def _project(tmp_path, lib=LIB, main=MAIN):
    (tmp_path / "Lib.crb").write_bytes(lib)
    (tmp_path / "Main.CR6").write_bytes(main)
    return tmp_path / "Main.CR6"


def test_clean_project(tmp_path):
    res = analyze(AnalysisRequest(_project(tmp_path)))
    assert res.ok and res.diagnostics == () and res.implementation_failures == ()
    assert res.dependencies.reads[tmp_path / "Lib.crb"] is not None
    assert tmp_path in res.dependencies.listings


def test_unsaved_include_edit_is_attributed_to_include(tmp_path):
    entry = _project(tmp_path)
    inc = tmp_path / "Lib.crb"
    assert analyze(AnalysisRequest(entry)).ok
    res = analyze(AnalysisRequest(entry, overlays={inc: b"Public LibVar\nBogus Statement Here\n"}))
    assert not res.ok
    assert inc.read_bytes() == LIB  # nothing written
    errs = [d for d in res.diagnostics if d.severity == "error"]
    assert errs and all(d.path == inc and d.file.lower() == "cpu:lib.crb" for d in errs)
    assert errs[0].line == 2
    assert any("Compile Failed" in line for line in res.rendered_lines)


def test_edit_that_breaks_entry_use_of_include(tmp_path):
    entry = _project(tmp_path)
    res = analyze(AnalysisRequest(entry, overlays={tmp_path / "Lib.crb": b"Public Other\n"}))
    assert not res.ok
    assert all(d.path == entry for d in res.diagnostics if d.severity == "error")


def test_entry_overlay_wins_over_disk(tmp_path):
    entry = _project(tmp_path)
    res = analyze(AnalysisRequest(entry, overlays={entry: b"BeginProg\n  Nope\nEndProg\n"}))
    assert not res.ok
    assert all(d.path == entry for d in res.diagnostics)


def test_unsaved_new_include_resolves(tmp_path):
    entry = tmp_path / "Main.CR6"
    entry.write_bytes(MAIN)
    inc = tmp_path / "Lib.crb"
    assert not inc.exists()
    missing = analyze(AnalysisRequest(entry))
    assert not missing.ok
    res = analyze(AnalysisRequest(entry, overlays={inc: LIB}))
    assert res.ok and not inc.exists()
    assert res.dependencies.reads[inc] is not None
    assert "Lib.crb" in (res.dependencies.listings[tmp_path] or [])


def test_missing_include_is_recorded_as_miss(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCRBASIC_STRICT")
    entry = tmp_path / "Main.CR6"
    entry.write_bytes(b'Include("CPU:Gone.crb")\nBeginProg\nEndProg\n')
    res = analyze(AnalysisRequest(entry))
    assert any("Cannot open include file" in d.message and d.severity == "warning"
               for d in res.diagnostics)
    assert res.dependencies.listings[tmp_path] == ("Main.CR6",)
    assert tmp_path / "Gone.crb" not in res.dependencies.reads  # the miss is in the listing
    gone = tmp_path / "nodir"
    entry2 = gone / "Main.CR6"
    res2 = analyze(AnalysisRequest(entry2))
    assert not res2.ok
    assert res2.dependencies.reads[entry2] is None


def test_include_case_insensitive_through_overlay(tmp_path):
    entry = tmp_path / "Main.CR6"
    entry.write_bytes(MAIN)
    inc = tmp_path / "LIB.CRB"
    res = analyze(AnalysisRequest(entry, overlays={inc: LIB}))
    assert res.ok
    assert res.dependencies.reads[inc] is not None


def test_signature_flag_only_changes_signature(tmp_path):
    entry = _project(tmp_path)
    off = analyze(AnalysisRequest(entry))
    on = analyze(AnalysisRequest(entry, signature=True))
    assert off.diagnostics == on.diagnostics and off.ok and on.ok
    assert not any("ProgSignature" in line for line in off.rendered_lines)
    assert any("ProgSignature" in line for line in on.rendered_lines)


def test_diagnostics_not_cut_by_message_buffer(tmp_path):
    entry = tmp_path / "Main.CR6"
    body = "".join(f"  Undefined{i}Name = 1\n" for i in range(150))
    entry.write_text("BeginProg\n" + body + "EndProg\n")
    res = analyze(AnalysisRequest(entry))
    assert len([d for d in res.diagnostics if d.severity == "error"]) == 150
    assert len(res.rendered_lines) < 150


def test_unreadable_entry_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCRBASIC_STRICT")
    res = analyze(AnalysisRequest(tmp_path / "Nope.CR6"))
    assert not res.ok


@pytest.mark.parametrize("module", ["opencrbasic.cli", "opencrbasic.compiler", "opencrbasic.analysis",
                                    "opencrbasic.sources"])
def test_core_imports_without_pygls(module):
    code = ("import sys\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'pygls' or name.startswith(('pygls.', 'lsprotocol')):\n"
            "            raise ImportError('blocked ' + name)\n"
            "sys.meta_path.insert(0, Block())\n"
            f"import {module}\n"
            "assert not any(m.split('.')[0] in ('pygls', 'lsprotocol') for m in sys.modules)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")})
    assert out.returncode == 0, out.stderr
