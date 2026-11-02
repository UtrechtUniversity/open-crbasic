"""`#Define`/`#Macro` expansion (`macros.py`) and `crbc --expand`."""

import subprocess
import sys
from pathlib import Path

import pytest

from opencrbasic.macros import MacroError, expand

PROGRAM = """\
Public DebugMsg As String * 64, X
#Define LOGLEVEL 1 ' on
#Define LOGD(msg) DebugMsg = msg
#Define NOLOG(msg)
#Macro SETBOTH(a, b)
  X = a
  LOGD(b)
#EndMacro
DataTable(Debug,True,-1)
  Sample(1,DebugMsg,String)
EndTable
BeginProg
  Scan(1,Sec,0,0)
    #If LOGLEVEL Then
      LOGD("logd, (msg)") ' LOGD stays in comments
    #EndIf
    NOLOG("gone")
    SETBOTH(ABS(-2), "b")
    CallTable Debug
  NextScan
EndProg
"""


def test_expand():
    out = expand(PROGRAM).splitlines()
    assert out[1] == "'#Define LOGLEVEL 1 ' on"
    assert out[4:8] == ["'#Macro SETBOTH(a, b)", "'  X = a", "'  LOGD(b)", "'#EndMacro"]
    assert out[13] == "    #If 1 Then"
    assert out[14] == """      DebugMsg = "logd, (msg)" ' LOGD stays in comments"""
    assert out[16] == "    "
    assert out[17:19] == ["    X = ABS(-2)", '    DebugMsg = "b"']


def test_expand_keeps_text_without_macros():
    text = 'Public X\r\nX = &HFF ' + "' X\r\n"
    assert expand(text) == text


@pytest.mark.parametrize(
    "text, line",
    [
        ("#Define F(a) a\nX = F(1, 2)\n", 2),
        ("#Define F(a) a\nX = F(1\n", 2),
        ("#Define F 1\n#Define f 2\n", 2),
        ("#Macro M(a)\nX = a\n", 1),
        ("#Define F(a, a) a\n", 1),
    ],
)
def test_expand_errors(text, line):
    with pytest.raises(MacroError) as e:
        expand(text)
    assert e.value.line == line


def test_self_reference_does_not_recurse():
    assert expand("#Define X X + 1\nY = X\n").splitlines()[1] == "Y = X + 1"


def test_cli_expand_compiles_the_expanded_file(tmp_path):
    (tmp_path / "Main.CR6").write_text(PROGRAM)
    run = subprocess.run(
        [sys.executable, "-m", "opencrbasic", "--no-message-file", "--expand", "Out.CR6", "Main.CR6"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )
    assert run.stdout.startswith("Out.CR6 -- Compiled in"), run.stdout
    assert (tmp_path / "Out.CR6").read_text() == expand(PROGRAM)


def test_editor_analysis_expands_on_source_lines(tmp_path):
    from opencrbasic.analysis import AnalysisRequest, analyze

    entry = tmp_path / "Main.CR6"
    entry.write_text(PROGRAM)
    assert analyze(AnalysisRequest(entry)).ok
    lines = PROGRAM.splitlines()
    lines[16] = "    SETBOTH(Nope, 1)"  # an error inside a multi-line expansion
    lines[17] = "    X = Nope2"  # an error after it
    res = analyze(AnalysisRequest(entry, overlays={entry: "\n".join(lines).encode() + b"\n"}))
    assert [(d.line, d.span) for d in res.diagnostics if d.severity == "error"] == [(17, (17, 17)), (18, (18, 18))]
    entry.write_text("#Macro M(a)\nX = a\n")
    assert [(d.line, d.message) for d in analyze(AnalysisRequest(entry)).diagnostics][0] == (
        1,
        "#Macro M without #EndMacro.",
    )
