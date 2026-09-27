"""`Could not find DataTable %s.` for any unknown `CallTable` target, and global redeclaration
errors. Oracle probes, CR6 and CR300 identical, 2026-09-27 (`docs/checker.md`)."""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def messages(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


TABLE = ("Public X\nDataTable (T,1,-1)\n  Sample (1,X,FP2)\nEndTable\nBeginProg\n"
         "  Scan (1,Sec,0,0)\n    CallTable T\n{line}\n  NextScan\nEndProg\n")


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize("line,name", [
    ("    CallTable Nope", "Nope"), ('    CallTable("fuzz")', "fuzz"),
    ("    CallTable((1+2))", "1+2"), ("    CallTable X", "X"), ("    CallTable Status", "Status"),
])
def test_unknown_calltable_target(line, name, logger):
    assert messages(TABLE.format(line=line), logger) == [f"Could not find DataTable {name}."]


def test_calltable_is_case_insensitive():
    assert messages(TABLE.format(line="    CallTable t")) == []


def test_quoted_table_name_is_found():
    text = ('Public X\nDataTable ("Q",1,-1)\n  Sample (1,X,FP2)\nEndTable\nBeginProg\n'
            "  Scan (1,Sec,0,0)\n    CallTable Q\n  NextScan\nEndProg\n")
    assert messages(text) == []


BODY = "BeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"


# CR300/CR350 print the same line, then their latching `Too many parameters:  Sec.` cascade on
# `Scan` (not modelled for this message yet).
@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("decls,expected", [
    ("Public id As Long\nPublic id As Long\n", ["id already declared as a variable."]),
    ("Public A\nDim a\n", ["a already declared as a variable."]),
    ("Public A, B, A\n", ["A already declared as a variable."]),
    ("Const A = 1\nPublic A\n", ["A is already declared as a CONST."]),
    ("Public A\nConst A = 1\n", ["A already declared as a variable."]),
    ("Public A(2)\nAlias A(1) = B\nPublic B\n", ["B already declared as a variable."]),
])
def test_redeclaration(decls, expected, logger):
    assert messages(decls + BODY, logger) == expected


@pytest.mark.parametrize("decls", ["Public A\nSub S(A)\nEndSub\n",
                                   "Public A\nSub S\n  Dim A\nEndSub\n"])
def test_sub_scope_may_shadow(decls):
    assert messages(decls + BODY) == []


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("decls,expected", [
    ("Const K = 1\nConst K = 2\n", ["K is already declared as a CONST."]),
    ("Public X\nReadOnly Nope\n", ["Nope not yet declared."]),
    ("Public X\nReadOnly X, Nope2\n", ["Nope2 not yet declared."]),
    ("Public A() As Long\n", ["Invalid symbol in expression:  As."]),
    ("Public A() As String * 10\n", ["Invalid symbol in expression:  As."]),
    ("Public A()\n", []),
])
def test_more_declaration_rules(decls, expected, logger):
    assert messages(decls + BODY, logger) == expected


def test_readonly_inside_structure_names_members():
    text = ("StructureType ST\n  a As Float\n  ReadOnly a\nEndStructureType\nPublic V As ST\n" + BODY)
    assert messages(text) == []


@pytest.mark.parametrize("logger", ["cr300", "cr350"])
def test_cr300_empty_dims_as(logger):
    assert messages("Public A() As Long\n" + BODY, logger) == [
        "As is not defined.", "Variable illegal in Parameter:  As.", "Too many parameters:  Sec."]
