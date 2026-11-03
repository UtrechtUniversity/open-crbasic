"""The memoised analysis fixture (tests/lsp/conftest.py): keys, invalidation and refusals."""

import pytest

from opencrbasic.analysis import AnalysisRequest, analyze

from .conftest import ROOT, MemoAnalyze

MAIN = ROOT / "tests" / "corpus" / "station" / "Main.CR6"


def _key(result):
    return (
        result.ok,
        result.mode,
        [(d.severity, d.line, d.message) for d in result.diagnostics],
        result.rendered_lines,
    )


def test_repeat_request_returns_the_same_result(memo_analyze):
    first = memo_analyze(AnalysisRequest(MAIN))
    again = memo_analyze(AnalysisRequest(MAIN))
    assert again is first
    assert _key(first) == _key(analyze(AnalysisRequest(MAIN)))


def test_request_fields_are_part_of_the_key(memo_analyze):
    plain = memo_analyze(AnalysisRequest(MAIN))
    assert memo_analyze(AnalysisRequest(MAIN, warn_unused=True)) is not plain
    assert memo_analyze(AnalysisRequest(MAIN, "cr1000x")) is not plain
    assert memo_analyze(AnalysisRequest(MAIN, signature=True)) is not plain


def test_a_changed_dependency_invalidates_the_entry(tmp_path):
    (tmp_path / "Lib.crb").write_bytes(b"Public LibVar\n")
    entry = tmp_path / "Main.CR6"
    entry.write_bytes(
        b'Include("CPU:Lib.crb")\nPublic X\nBeginProg\n Scan(1,Sec,0,0)\n  X = LibVar\n NextScan\nEndProg\n'
    )
    memo = MemoAnalyze(allowed=(tmp_path.resolve(),))
    first = memo(AnalysisRequest(entry))
    assert first.ok and memo(AnalysisRequest(entry)) is first
    (tmp_path / "Lib.crb").write_bytes(b"Public Other\n")  # an include changes
    second = memo(AnalysisRequest(entry))
    assert second is not first and not second.ok
    (tmp_path / "Extra.crb").write_bytes(b"")  # a directory listing changes
    assert memo(AnalysisRequest(entry)) is not second
    assert (memo.hits, memo.misses) == (1, 3)


def test_overlays_and_foreign_paths_are_refused(memo_analyze, tmp_path):
    with pytest.raises(ValueError):
        memo_analyze(AnalysisRequest(MAIN, overlays={MAIN: MAIN.read_bytes()}))
    other = tmp_path / "x.CR6"
    other.write_bytes(b"BeginProg\nEndProg\n")
    with pytest.raises(ValueError):
        memo_analyze(AnalysisRequest(other))
