"""Pass-1 checks inside `Public`/`Dim`/`Alias` declarations (`checker/structure.py`
`_check_decl_exprs`/`_check_alias_index_names`). Oracle probes, 2026-09-27, `docs/checker.md`
"Declaration expressions"."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def messages(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


def program(decl: str) -> str:
    return f"Public X(5), Y\n{decl}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"


CASES = [
    ("Public A(3, Nope)", ["Invalid symbol in expression: Nope."]),
    ("Dim A( Nope )", ["Invalid symbol in expression: Nope ."]),
    ("Dim A(Nope+1, Nope2)", ["Invalid symbol in expression: Nope+1.", "Invalid symbol in expression: Nope2."]),
    ("Public A(2), B(Nope), C(3)", ["Invalid symbol in expression: Nope."]),
    ("Alias X( Nope ) = Q", ["Invalid symbol in expression:  Nope .", "Alias is illegally dimensioned."]),
    (
        "Public B As Long = Nope * 2",
        ["Invalid symbol in expression: Nope * 2.", "Variable initialization of B has bad syntax."],
    ),
    (
        "Public B(2) = {1, Nope}",
        ["Invalid symbol in expression: Nope.", "Variable initialization of B has bad syntax."],
    ),
    ("Dim A(Y)", ["Variable illegal in Parameter: Y."]),
    ("Public A As StringX * 50", ["Variable Type StringX not supported"]),
    ("Dim A(3) As Flot", ["Variable Type Flot not supported"]),
    ("Public A As Long, B As Bogus, C", ["Variable Type Bogus not supported"]),
]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("decl,expected", CASES)
def test_declaration_messages(decl, expected, logger):
    assert messages(program(decl), logger) == expected


def test_cr300_undefined_dimension_pairs_with_variable_illegal():
    assert messages(program("Public A(3, Nope)"), "cr300")[:2] == [
        "Nope is not defined.",
        "Variable illegal in Parameter: Nope.",
    ]


@pytest.mark.parametrize("type_", ["Float", "Long", "Boolean", "String * 10", "Double", "UInt1"])
def test_supported_types_compile(type_):
    assert messages(program(f"Dim A As {type_}")) == []


def test_structure_type_is_a_supported_type():
    text = (
        "StructureType Pt\n  x As Float\nEndStructureType\nPublic P As Pt\nPublic Q As pt\n"
        "BeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    )
    assert messages(text) == []
