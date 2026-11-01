"""Pass-1 constant-family instruction arguments, failed `Const` cascades, and include ordering.

Oracle probes, 2026-09-27 (`docs/checker.md` "Pass-1 instruction arguments").
"""

from pathlib import Path

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult, compile_file


def messages(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


def body(line: str) -> str:
    return f"Public X, S As String * 20\nDim Z banana\nBeginProg\n  Scan (1,Sec,0,0)\n{line}\n  NextScan\nEndProg\n"


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize(
    "line,message",
    [
        ('    SDI12Recorder(X, Nope, "0", "M!", 1, 0)', "Invalid symbol in expression: Nope."),
        ("    PanelTemp(X, Nope)", "Invalid symbol in expression: Nope."),
        ("    VoltSe(X,1,Nope,1,1,0,15000,1,0)", "Invalid symbol in expression: Nope."),
        ("    VoltSe(Nope2,1,mV5000,Nope,1,0,15000,1,0)", "Invalid symbol in expression: Nope."),
        ("    PortSet(X,1)", "Variable illegal in Parameter: X."),
    ],
)
def test_const_family_argument_is_pass1(line, message, logger):
    assert messages(body(line), logger) == ["Invalid Variable declaration banana.", message]


@pytest.mark.parametrize(
    "line", ["    VoltSe(Nope,1,mV5000,1,1,0,15000,1,0)", "    Randomize(Nope)", "    Delay(0,Nope,mSec)"]
)
def test_general_argument_is_pass2(line):
    assert messages(body(line)) == ["Invalid Variable declaration banana."]


CASCADE = (
    "Public X\nConst P = Nope\nConst R = P + 1\nBeginProg\n  Scan (1,Sec,0,0)\n"
    "    PortSet(R,1)\n    PanelTemp(X, P)\n  NextScan\nEndProg\n"
)


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
def test_failed_const_is_undeclared(logger):
    assert messages(CASCADE, logger) == [
        "Invalid symbol in expression: Nope.",
        "Invalid symbol in expression: P + 1.",
        "Invalid symbol in expression: R.",
        "Invalid symbol in expression: P.",
    ]


def test_failed_const_cascade_cr300():
    assert messages(CASCADE, "cr300")[:2] == ["Nope is not defined.", "P is not defined."]


def test_include_messages_print_in_expansion_order(tmp_path: Path):
    (tmp_path / "Lib.crb").write_text("Const Port = U5\nSub Go\n  PortSet(Port,1)\nEndSub\n")
    main = tmp_path / "t.CR1X"
    main.write_text(
        'Public X\nInclude "Lib.crb"\nBeginProg\n  Scan (1,Sec,0,0)\n    PortSet(Port,1)\n  NextScan\nEndProg\n'
    )
    result = compile_file(main, profiles.get("cr1000x"))
    assert result.result_lines()[2:] == [
        "<Lib.crb> line 1: Invalid symbol in expression: U5.",
        "<Lib.crb> line 3: Invalid symbol in expression: Port.",
        "line 5: Invalid symbol in expression: Port.",
    ]
