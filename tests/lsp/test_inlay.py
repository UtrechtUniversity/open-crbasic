"""LSP-09a: parameter-name inlay hints (`opencrbasic.inlay`). Policies: docs/lsp-api.md "Inlay hints
(LSP-09a)". Help text in these tests is synthetic; the sweep over the real help runs only when
../extracted_chm exists."""

import time
from pathlib import Path

import pytest

from opencrbasic import inlay as I
from opencrbasic import positions
from opencrbasic.analysis import AnalysisRequest, analyze
from opencrbasic.helpdocs import HelpProvider, InstructionHelp, ParamHelp

ROOT = Path(__file__).resolve().parents[2]
CRBASIC = (ROOT / "tests" / "corpus" / "firnmelt").resolve()
CHM = ROOT / "extracted_chm"


class FakeHelp:
    def __init__(self, **pages: InstructionHelp):
        self.pages = {k.upper(): v for k, v in pages.items()}

    def lookup(self, name: str):
        return self.pages.get(name.upper())


def page(name: str, params: list[str]) -> InstructionHelp:
    ps = tuple(ParamHelp(n, None, "d") for n in params)
    return InstructionHelp(
        name.upper(),
        (name.upper(),),
        name,
        f"{name}({', '.join(params)})",
        ps,
        "s",
        Path("/help/Instructions") / f"{name.lower()}.htm",
    )


HELP = FakeHelp(
    voltse=page("VoltSe", ["Dest", "Reps", "Range", "SEChan", "MeasOff", "SettlingTime", "fN1", "Mult", "Offset"]),
    left=page("Left", ["SearchString", "NumChars"]),
    sample=page("Sample", ["Reps", "Source", "DataType", "Extra"]),
    right=page("Right", ["SearchString", "NumChars"]),
)


def run(tmp_path: Path, text: str, name: str = "p.CR6"):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return p, analyze(AnalysisRequest(p, "cr6"))


def hints(tmp_path, text, help=HELP, start=0, end=10_000):
    p, res = run(tmp_path, text)
    return [(h.line, h.column, h.label) for h in I.parameter_hints(res, p, start, end, "cr6", help)]


def test_voltse_every_argument(tmp_path):
    src = "Public T\nBeginProg\n  Scan(1,Sec,0,0)\n    VoltSe(T,1,mV200,1,False,0,250,1,0)\n  NextScan\nEndProg\n"
    got = hints(tmp_path, src)
    names = ["Dest", "Reps", "Range", "SEChan", "MeasOff", "SettlingTime", "fN1", "Mult", "Offset"]
    # `T` for Dest is a different name; Dest: gets a hint
    line = 3
    text = "    VoltSe(T,1,mV200,1,False,0,250,1,0)"
    cols = [text.index(a) for a in ("T,", "1,", "mV200", "1,F", "False", "0,2", "250", "1,0", "0)")]
    assert got == [(line, c, n + ":") for c, n in zip(cols, names)]


def test_argument_equal_to_name_and_empty_skipped(tmp_path):
    src = (
        "Public Reps, T\nBeginProg\n  Scan(1,Sec,0,0)\n"
        "    VoltSe(T,reps,mV200,1,False,0,250,1,0)\n    Sample(1,T,FP2)\n  NextScan\nEndProg\n"
    )
    got = hints(tmp_path, src)
    assert (3, 11, "Dest:") in got and not any(l == "Reps:" and ln == 3 for ln, _, l in got)
    # Sample(1,T,FP2): fewer arguments than the four parameters
    assert [l for ln, _, l in got if ln == 4] == ["Reps:", "Source:", "DataType:"]


def test_empty_argument_gets_no_hint(tmp_path):
    src = "Public s As String * 8\nBeginProg\n  s = Left(s,)\nEndProg\n"
    assert hints(tmp_path, src) == [(2, 11, "SearchString:")]


def test_nested_call_and_fewer_arguments(tmp_path):
    src = "Public s As String * 8, t As String * 8\nBeginProg\n  s = Left(Right(t,2),3)\n  t = Left(s)\nEndProg\n"
    got = hints(tmp_path, src)
    line = "  s = Left(Right(t,2),3)"
    assert [(ln, c, l) for ln, c, l in got if ln == 2] == [
        (2, line.index("Right"), "SearchString:"),
        (2, line.index("t,2"), "SearchString:"),
        (2, line.index("2)"), "NumChars:"),
        (2, line.index("3)"), "NumChars:"),
    ]
    assert [(c, l) for ln, c, l in got if ln == 3] == [(len("  t = Left("), "SearchString:")]


def test_colon_statement(tmp_path):
    src = "Public s As String * 8, t\nBeginProg\n  t = 1 : s = Left(s,2)\nEndProg\n"
    got = hints(tmp_path, src)
    line = "  t = 1 : s = Left(s,2)"
    assert [c for _, c, _ in got] == [line.index("s,2"), line.index("2)")]


def test_continuation_line(tmp_path):
    src = "Public s As String * 8\nBeginProg\n  s = Left(s, _\n    2)\nEndProg\n"
    got = hints(tmp_path, src)
    assert got == [(2, 11, "SearchString:"), (3, 4, "NumChars:")]


def test_no_help_gives_no_hints(tmp_path):
    src = "Public s As String * 8\nBeginProg\n  s = Left(s,2)\nEndProg\n"
    assert hints(tmp_path, src, help=None) == []
    assert hints(tmp_path, src, help=FakeHelp()) == []


def test_unpaired_help_gives_no_argn_hints(tmp_path):
    bad = FakeHelp(left=page("Left", ["Only"]))
    src = "Public s As String * 8\nBeginProg\n  s = Left(s,2)\nEndProg\n"
    assert hints(tmp_path, src, help=bad) == []


def test_range_filter(tmp_path):
    src = "Public s As String * 8\nBeginProg\n  s = Left(s,2)\n  s = Left(s,3)\nEndProg\n"
    assert {ln for ln, _, _ in hints(tmp_path, src, start=3, end=3)} == {3}
    assert hints(tmp_path, src, start=0, end=1) == []


def test_non_ascii_before_call_uses_decoded_columns(tmp_path):
    src = 'Public s As String * 8, t As String * 8\nBeginProg\n  t = "éé" : s = Left(s,2)\nEndProg\n'
    p, res = run(tmp_path, src)
    got = I.parameter_hints(res, p, 0, 10, "cr6", HELP)
    data = p.read_bytes()
    line = data.split(b"\n")[2]
    assert [h.column for h in got] == [line.index(b"s,2"), line.index(b"2)")]
    assert got[0].column > len(line.decode().split("Left(")[0]) + 5  # decoded > editor chars
    ln, ch = positions.lsp_position(data, 3, got[0].column)
    assert ch == len('  t = "éé" : s = Left(')


def test_string_argument_and_comma_in_string(tmp_path):
    src = 'Public s As String * 8\nBeginProg\n  s = Left("a,b",2)\nEndProg\n'
    got = hints(tmp_path, src)
    assert [(c, l) for _, c, l in got] == [(11, "SearchString:"), (17, "NumChars:")]


def test_declared_name_shadows_catalog(tmp_path):
    src = "Public Left(3), x\nBeginProg\n  x = Left(1)\nEndProg\n"
    assert hints(tmp_path, src) == []


def test_never_raises_on_garbage():
    class R:
        program = object()

    assert I.parameter_hints(R(), "x", 0, 5, "cr6", HELP) == []
    assert I.parameter_hints(None, "x", 0, 5, "cr6", HELP) == []


PROGRAMS = sorted(CRBASIC.glob("*.CR6"))


@pytest.fixture(scope="module")
def analyses():
    return {p: analyze(AnalysisRequest(p, "cr6")) for p in PROGRAMS}


def test_sweep_never_raises_and_voltse_in_crbasic(analyses):
    total = 0
    seen_voltse = False
    for p, res in analyses.items():
        got = I.parameter_hints(res, p, 0, 100_000, "cr6", HELP)
        total += len(got)
        lines = p.read_bytes().split(b"\n")
        for h in got:
            assert 0 <= h.line < len(lines) and 0 <= h.column <= len(lines[h.line])
            assert h.label.endswith(":") and h.kind == "parameter" and h.padding_right
        seen_voltse = seen_voltse or any(h.label == "Dest:" and b"VoltSe" in lines[h.line][: h.column] for h in got)
    assert total > 0 and seen_voltse


@pytest.mark.skipif(not CHM.is_dir(), reason="no extracted_chm")
def test_sweep_with_real_help_and_timing(analyses):
    real = HelpProvider(CHM)
    main = CRBASIC / "Main.CR6"
    t0 = time.perf_counter()
    got = I.parameter_hints(analyses[main], main, 0, 100_000, "cr6", real)
    elapsed = time.perf_counter() - t0
    assert got
    assert not any(h.label.startswith("arg") for h in got)
    assert elapsed < 10, elapsed
    for p, res in analyses.items():
        I.parameter_hints(res, p, 0, 100_000, "cr6", real)


UNITS_STATEMENT = (
    'Public Value As String * 8\nUnits Value = "abc"\nDataTable(T,True,1)\n'
    '  Sample(1,Value,String)\nEndTable\nBeginProg\n  Value = Left("abc",2)\nEndProg\n'
)
UNITS_PREFIX = 'Public UnitsValue As String * 8\nBeginProg\n  UnitsValue = Left("abc",2)\nEndProg\n'


def test_units_prefixed_name_still_gets_hints(tmp_path):
    assert [h[2] for h in hints(tmp_path, UNITS_PREFIX)] == ["SearchString:", "NumChars:"]


def test_plain_name_baseline_gets_hints(tmp_path):
    assert [h[2] for h in hints(tmp_path, UNITS_PREFIX.replace("UnitsValue", "Value"))] == [
        "SearchString:",
        "NumChars:",
    ]


def test_units_statement_is_excluded_but_later_calls_are_hinted(tmp_path):
    got = hints(tmp_path, UNITS_STATEMENT.replace('"abc"\nDataTable', '"Left(1,2)"\nDataTable'))
    assert {h[0] for h in got} == {3, 6}, got  # the Sample and Left lines, not the Units line
