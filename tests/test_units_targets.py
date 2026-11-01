"""`Can't assign units to %s, ...` (`checker/instr_data.py` `check_units_targets`). Oracle probes,
CR6 and CR300 identical, 2026-09-27 (`docs/checker.md` "Units targets")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

DECLS = (
    "Public P, PA(3)\nDim D, DA(3)\nConst K = 3\nAlias P = AP\n"
    "StructureType ST\n  a As Float\n  Units a = m\nEndStructureType\nPublic V As ST\n"
    "Sub S(Arg)\nEndSub\n"
)


def messages(units: str, logger: str = "cr6") -> list[str]:
    text = f"{DECLS}{units}\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in diags.errors]


def cannot(name: str) -> str:
    return f"Can't assign units to {name}, it must be declared Public or referenced as a source in a Data Table."


@pytest.mark.parametrize("units", ["Units P = m", "Units D = m", "Units AP = m", "Units DA = m", "Units PA(1) = m"])
def test_declared_variables_and_aliases_accept_units(units):
    assert messages(units) == []


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize(
    "units,name",
    [
        ("Units K = m", "K"),
        ("Units Nope = m", "Nope"),
        ("Units nope(1) = m", "nope"),
        ("Units Arg = m", "Arg"),
        ("Units V = m", "V"),
    ],
)
def test_other_targets_are_rejected(units, name, logger):
    assert messages(units, logger) == [cannot(name)]


def test_suppressed_by_an_earlier_error():
    assert messages("Units Nope = m\nDim Z banana") == ["Invalid Variable declaration banana."]
