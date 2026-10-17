"""Constant-index bounds errors. Expected lines are verbatim oracle output (real compilers,
2026-10-01)."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

LOGGERS = ["cr6", "cr1000x", "cr300", "cr350"]
FULL = ["cr6", "cr1000x"]
SMALL = ["cr300", "cr350"]


def lines(body: str, logger: str) -> list[str]:
    body_text = "".join(f"    {b}\n" for b in body.split(" / "))
    text = (
        "Public X, A(5), B(2,3), L(4) As Long\nBeginProg\n  Scan (1,Sec,0,0)\n"
        f"{body_text}  NextScan\nEndProg\n"
    )
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    return result.result_lines()[2:] if not result.ok else result.result_lines()[1:]


A_OOB = ["line 4: Variable A out of bounds."]
B_OOB = ["line 4: Variable B out of bounds."]


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("body", [
    "Battery(A(9))",
    "Battery(A(0))",
    "Randomize A(&HFF)",
    "Move(A(1),2,A(6),1)",
    "DewPoint(X,A(9),1)",
])
def test_a_out_of_bounds(body, logger):
    assert lines(body, logger) == A_OOB


@pytest.mark.parametrize("logger", LOGGERS)
def test_delay_out_of_bounds(logger):
    assert "line 4: Variable A out of bounds." in lines("Delay(0,A(9),Sec)", logger)


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("body", ["Battery(B(3,1))", "Battery(B(1,4))", "Battery(B(7))"])
def test_b_out_of_bounds(body, logger):
    assert lines(body, logger) == B_OOB


@pytest.mark.parametrize("logger", LOGGERS)
@pytest.mark.parametrize("body", [
    "Battery(A(5))",
    "Battery(A(-1))",
    "Battery(A(6)+1)",
    "X = A(6)",
    "Battery(L(5))",
    "WriteIO(1,A(9))",
    "AddPrecise(X,A(9))",
])
def test_not_bounds_checked(body, logger):
    # some of these have other (oracle-matching) errors on CR300/CR350; none is out of bounds
    assert not any("out of bounds" in line for line in lines(body, logger))


@pytest.mark.parametrize("logger,terminal", [("cr6", "U1"), ("cr1000x", "VX1"), ("cr300", "VX1")])
def test_excitation_destination_is_checked(logger, terminal):
    assert lines(f"ExciteV({terminal},A(9),0)", logger) == A_OOB


def test_excitei_is_checked_on_cr6():
    assert lines("ExciteI(U1,A(9),0)", "cr6") == A_OOB


@pytest.mark.parametrize("logger", FULL)
def test_pass_placement_full(logger):
    assert lines("Battery(A(9)) / X = Nope", logger) == [
        "line 4: Variable A out of bounds.",
        "line 5: Invalid symbol in expression:     X = Nope.",
    ]


@pytest.mark.parametrize("logger", SMALL)
def test_pass_placement_small(logger):
    assert lines("Battery(A(9)) / X = Nope", logger) == ["line 5: Nope is not defined."]


@pytest.mark.parametrize("logger", FULL)
@pytest.mark.parametrize("body", [
    "Battery(A(1,2))",
    "Battery(X(2))",
    "Battery(X(0))",
    "Battery(B(1,1,2))",
    "Battery(A(1,0))",
])
def test_extra_subscripts_are_size_one_dimensions_full(body, logger):
    # a subscript beyond the declared rank counts as a size-1 dimension; a scalar is shape (1,)
    name = body.split("(")[1]
    assert lines(body, logger) == [f"line 4: Variable {name} out of bounds."]


@pytest.mark.parametrize("logger", FULL)
def test_scalar_destination_out_of_bounds_full(logger):
    assert lines("PanelTemp(X(1,2),60)", logger) == ["line 4: Variable X out of bounds."]


@pytest.mark.parametrize("logger", FULL)
@pytest.mark.parametrize("body", [
    "Battery(A(2,1))",
    "Battery(A(5,1))",
    "Battery(X(1))",
    "Battery(X(1,1))",
    "Battery(B(1,1,1))",
    "Battery(A(1,1,1))",
])
def test_extra_subscripts_in_bounds_full(body, logger):
    assert lines(body, logger) == []


@pytest.mark.parametrize("logger", SMALL)
@pytest.mark.parametrize("body,name", [
    ("Battery(X(2))", "X(2)"),
    ("Battery(A(1,2))", "A(1,2)"),
    ("Battery(L(1,2))", "L(1,2)"),
])
def test_over_indexing_is_dimensions_not_declared_small(body, name, logger):
    # a Long destination must not also report "Illegal destination variable type"
    assert lines(body, logger) == [f"line 4: Dimensions not declared: {name}."]
