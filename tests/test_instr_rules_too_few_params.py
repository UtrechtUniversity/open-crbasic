"""Unit tests for `checker/instr_rules.py`'s `check`: a required parameter missing entirely
reports `Too few parameters in instruction.` when its own `type_codes.json` `kind` is
`identifier` (6) or `deferred_text` (11), and the existing `Invalid or missing parameter.`
otherwise. Rule and oracle evidence: `docs/checker.md` "`Too few parameters in instruction.` vs
`Invalid or missing parameter.`".

Oracle-confirmed against `CR6Comp.exe` (`WINEPREFIX=$HOME/.wine-cr6 wine .../CR6Comp.exe t.cr6`,
no CLI flags), scratch probes only, not saved as `knowledge/test_results/` captures since this is
`opencrbasic`, not the CRBasic library work `AGENTS.md` governs:

    Public X
    BeginProg
      Scan(1,Sec,0,0)
        Battery
      NextScan
    EndProg
    -> line 5: Invalid or missing parameter.        (code 8, variable_or_array)

    Public X
    DataTable(T,1,100)
      Sample(1,X,FP2)
    EndTable
    BeginProg
      Scan(1,Sec,0,0)
        CallTable
      NextScan
    EndProg
    -> line 9: Too few parameters in instruction.    (code 6, identifier)

    Public X
    DataTable(T,1,100)
      Histogram
    EndTable
    BeginProg
      Scan(1,Sec,0,0)
      NextScan
    EndProg
    -> line 4: Too few parameters in instruction.    (code 11, deferred_text)

and cross-checked against the CHM corpus's own `openinterval_1.CR6` (bare `DataTable`, code 6),
`etsz_1.CR6` (bare `ETsz`, code 11) and `writeio_1.CR6` (bare `CallTable`, code 6).
"""

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

PROFILE = profiles.get("cr6")


def messages(text: str) -> list[tuple[int, str]]:
    program, diags = parser.parse_text(text, "t.CR6")
    result = CompileResult("t.CR6", PROFILE, diags)
    checker.check(program, PROFILE, diags, result)
    return [(d.line, d.message) for d in diags.errors]


def test_bare_battery_missing_variable_or_array_reports_invalid_or_missing_parameter():
    text = "Public X\nBeginProg\n  Scan(1,Sec,0,0)\n    Battery\n  NextScan\nEndProg\n"
    assert messages(text) == [(4, "Invalid or missing parameter.")]


def test_bare_calltable_missing_identifier_reports_too_few_parameters():
    text = (
        "Public X\n"
        "DataTable(T,1,100)\n"
        "  Sample(1,X,FP2)\n"
        "EndTable\n"
        "BeginProg\n"
        "  Scan(1,Sec,0,0)\n"
        "    CallTable\n"
        "  NextScan\n"
        "EndProg\n"
    )
    assert messages(text) == [(7, "Too few parameters in instruction.")]


def test_bare_histogram_missing_deferred_text_reports_too_few_parameters():
    text = "Public X\nDataTable(T,1,100)\n  Histogram\nEndTable\nBeginProg\n  Scan(1,Sec,0,0)\n  NextScan\nEndProg\n"
    assert messages(text) == [(3, "Too few parameters in instruction.")]


def test_bare_datatable_missing_identifier_reports_too_few_parameters():
    # CHM corpus shape (`openinterval_1.CR6`): `DataTable` alone on its own physical line, its
    # `(Name, Trig, Size)` argument list on the next -- our own tolerant cross-line parsing
    # produces a short argument list, same as the other cases this module already handles.
    text = (
        "Public X\n"
        "DataTable\n"
        "(T,1,100)\n"
        "  Sample(1,X,FP2)\n"
        "EndTable\n"
        "BeginProg\n"
        "  Scan(1,Sec,0,0)\n"
        "  NextScan\n"
        "EndProg\n"
    )
    assert (2, "Too few parameters in instruction.") in messages(text)
