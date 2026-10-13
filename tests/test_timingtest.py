"""Pure comparison logic of tools/timingtest.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import timingtest as tt  # noqa: E402


def test_extract_entries():
    msg = "[Times]\r\nline 5: Scan: 0\r\nline 6: PanelTemp = 1100\r\np.CR6 -- Compiled in SequentialMode.\r\n"
    assert tt.extract_times(msg, "p.CR6") == "line 5: Scan: 0\nline 6: PanelTemp = 1100"


def test_extract_empty_header_runs_into_result_line():
    assert tt.extract_times("[Times]p.CR6 -- Compile Failed!\r\n\nline 1: x\r\n", "p.CR6") == ""


def test_extract_blank_separator_dropped_and_missing_header():
    assert tt.extract_times("[Times]\nline 1: Scan: 0\n\nline 9: Scan: 1 (Subroutine)\np.CR6 -- Compiled", "p.CR6") \
        == "line 1: Scan: 0\nline 9: Scan: 1 (Subroutine)"
    assert tt.extract_times("p.CR6 -- Compiled", "p.CR6") is None


def test_classify_and_first_difference():
    assert tt.classify("a", "a") == "exact"
    assert tt.classify("a", "") == "mismatch"
    assert tt.first_difference("a\nb", "a\nc") == "entry 1: ours='b' theirs='c'"
    assert tt.first_difference("a", "a\nb") == "entry 1: ours='<none>' theirs='b'"
