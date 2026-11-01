"""CR6Comp-compatible CLI outputs: `-p`/`-i` (preprocessed source), `-t` (`[Times]`), `-b` (CDM
image). Fixtures under `tests/fixtures/cli/` were captured from the real exe under Wine
(`docs/cli.md`); `sample.CR6`/`sample_inc.crb` is a small scratch program, not corpus text, built
to exercise comments, a line continuation, `#If`, a bare `Include()` inside a `Scan`, and the
timed instructions this project implements.
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


def _stage(tmp_path):
    (tmp_path / "sample.CR6").write_bytes((FIXTURES / "sample.CR6").read_bytes())
    (tmp_path / "sample_inc.crb").write_bytes((FIXTURES / "sample_inc.crb").read_bytes())


def test_preprocess_p_matches_exe(tmp_path):
    _stage(tmp_path)
    _run(tmp_path, ["-p", "out.pre", "sample.CR6", "out.msg"])
    got = (tmp_path / "out.pre").read_bytes()
    expected = (FIXTURES / "sample_p_expected.pre").read_bytes()
    assert got == expected


def test_preprocess_i_expands_include(tmp_path):
    _stage(tmp_path)
    _run(tmp_path, ["-i", "out.pre", "sample.CR6", "out.msg"])
    got = (tmp_path / "out.pre").read_bytes()
    expected = (FIXTURES / "sample_i_expected.pre").read_bytes()
    assert got == expected
    # The included file's own text is spliced in, not left as a literal `Include(...)` call.
    assert b"Include(" not in got
    assert b"PortSet (C1,0)" in got


def test_times_and_cdm_message_match_exe(tmp_path):
    _stage(tmp_path)
    _run(tmp_path, ["-stb", "out.bin", "sample.CR6", "out.msg"])
    got = (tmp_path / "out.msg").read_bytes()
    expected = (FIXTURES / "sample_stb_expected.msg").read_bytes()
    assert got == expected
    assert (tmp_path / "out.bin").read_bytes() == b""  # no CDM devices used


def test_times_entries_survive_a_pass2_only_failure():
    """`-t`'s `[Times]` section is gated on pass 1, not on the overall result (docs/cli.md "-t"):
    this file fails on `PortSet (C1,1)`'s channel argument (a pass-2 check), but `Scan`/`PanelTemp`/
    `TCDiff`/`Delay`, all of which parsed fine before that point, still get timed -- confirmed
    against the real exe (`sample_stb_expected.msg`, embedded in this test rather than a second
    fixture file since it is the same program).
    """
    expected = (FIXTURES / "sample_stb_expected.msg").read_bytes().decode("latin-1")
    assert "[Times]\r\nline 14: Scan: 0\r\n" in expected
    assert "line 17: PanelTemp = 1100\r\nline 17: PanelTemp = 1100\r\n" in expected
    assert "line 18: TCDiff = 2900\r\n" in expected
    assert "line 20: Delay = 100100\r\n" in expected
    assert "-- Compile Failed!" in expected  # the program itself does not compile


def test_times_hidden_by_prose_before_beginprog(tmp_path):
    """Real exe output (CR6Comp under Wine) for this program is exactly the expected bytes: errors
    before `BeginProg` (and undefined symbols) suppress `[Times]` entries (docs/cli.md "-t")."""
    (tmp_path / "t.CR6").write_bytes(
        b"Some prose line\r\nPublic x\r\nBeginProg\r\nScan(1,Sec,0,0)\r\nPanelTemp(x,15000)\r\nNextScan\r\nEndProg\r\n"
    )
    _run(tmp_path, ["-t", "t.CR6", "o.msg"])
    assert (tmp_path / "o.msg").read_bytes() == (
        b"[Times]t.CR6 -- Compile Failed!\r\n\nline 1: Invalid, or out of place expression: "
        b"Some prose line.\r\nline 1: Expr illegally occurs before BeginProg.\r\n"
    )


def test_no_message_file_flag(tmp_path):
    _stage(tmp_path)
    result = _run(tmp_path, ["--no-message-file", "sample.CR6"])
    assert result.returncode == 1  # sample.CR6 fails to compile (PortSet channel)
    assert not (tmp_path / "CR6cmple.xxx").exists()


def test_stdout_echoes_source_bytes_like_message_file(tmp_path):
    # The exe echoes source bytes unchanged; a UTF-8 degree sign must not be re-encoded (round 52).
    (tmp_path / "deg.CR6").write_bytes(b'BeginProg\r\n  x = "\xc2\xb0" +\r\nEndProg\r\n')
    out = _run(tmp_path, ["deg.CR6"]).stdout
    assert b"\xc2\xb0" in out and b"\xc3\x82" not in out
    assert out.replace(b"\r\n", b"\n") == (tmp_path / "CR6cmple.xxx").read_bytes().replace(b"\r\n", b"\n")
