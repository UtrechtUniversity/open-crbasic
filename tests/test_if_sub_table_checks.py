"""`If` conditions, `Sub` call argument counts, table sources and string `Alias` indices.
Oracle probes, 2026-09-27 (`docs/checker.md` "If conditions, Sub arguments, table sources")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult

HDR = ("Public X, W(3), S As String * 20\nSub Log2(A As String, B As Long)\nEndSub\n"
       "Sub Log1(A As String)\nEndSub\nDataTable (T,1,-1)\n{table}\nEndTable\n")
BODY = "BeginProg\n  Scan (1,Sec,0,0)\n    CallTable T\n{body}\n  NextScan\nEndProg\n"


def messages(table: str, body: str, logger: str = "cr6", pass1_error: bool = False) -> list[str]:
    text = (HDR + BODY).format(table=table, body=body)
    if pass1_error:
        text = text.replace("Public X, W(3)", "Public X, W(3)\nDim Z banana", 1)
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in sorted(diags.errors, key=lambda d: d.line)]


SAMPLE = "  Sample (1,X,FP2)"


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
@pytest.mark.parametrize("body,expected", [
    ("    If Foo(X) > 20 Then X = 1", "Invalid symbol in expression: Foo(X) > 20 ."),
    ("    If Nope Then\n    EndIf", "Invalid symbol in expression: Nope ."),
    ("    If X > 1 And Nope Then\n    EndIf", "Invalid symbol in expression: X > 1 And Nope ."),
])
def test_if_condition_undefined_name(body, expected, logger):
    assert messages(SAMPLE, body, logger) == [expected]
    assert messages(SAMPLE, body, logger, pass1_error=True) == ["Invalid Variable declaration banana."]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
def test_if_condition_arity_is_pass1(logger):
    body = "    If TimeIsBetween(0,15,60,sec,1) Then\n    EndIf"
    msg = "No Matching, or out of place ) in expression: TimeIsBetween(0,15,60,sec,1) ."
    assert messages(SAMPLE, body, logger) == [msg]
    if logger in ("cr6", "cr1000x"):
        assert messages(SAMPLE, body, logger, pass1_error=True) == [
            "Invalid Variable declaration banana.", msg]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize("body,expected", [
    ('    Log2("a")', "Call argument required."),
    ('    Log1("a","b")', "Call to Log1 with 2 parameters, requires 1 arguments."),
    ("    Log1()", "Call argument required."),
    ('    Call Log1("a","b")', "Call to Log1 with 2 parameters, requires 1 arguments."),
    ("    Log1", "Call argument required."),
])
def test_sub_call_argument_count(body, expected, logger):
    assert messages(SAMPLE, body, logger) == [expected]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x"])
def test_undefined_table_source_stops_pass2(logger):
    table = "  Sample (1,Nope,FP2)\n  Average (1,Nope2,FP2,False)"
    assert messages(table, "    X = Q", logger) == ["Invalid symbol in expression: Nope."]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300"])
def test_string_alias_index(logger):
    table = SAMPLE + '\nEndTable\nAlias W("fuzz") = WF\nDataTable (T2,1,-1)\n' + SAMPLE
    assert messages(table, "    X = 1", logger)[0] == "Illegally dimensioned aliased name"
