"""Lines cut short by an unterminated string, and `ESSInitialize`'s raw argument
(`instr_rules._check_unterminated_string_param`, `parser._parse_text_segment`). Expected lines
are verbatim oracle output on all four loggers, 2026-09-28 (`docs/checker.md` "Unterminated
strings")."""

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def lines(text: str, logger: str) -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    result = CompileResult("t.CR6", profile, diags)
    checker.check(program, profile, diags, result)
    out = result.result_lines()[2:] if not result.ok else result.result_lines()[1:]
    # the CR300/CR350 echo ends with a newline before its `.`; the oracle lines are split on it
    return [part for line in out for part in line.split("\n") if part.strip()]


CASES = [
    (
        "s1",
        'ESSVariables Dim\nPublic X, S As String\nBeginProg\n  ESSInitialize(public")\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
        {"cr6": [], "cr1000x": [], "cr300": [], "cr350": []},
    ),
    (
        "s2",
        'ESSVariables Dim\nPublic X, S As String\nBeginProg\n  ESSInitialize("public)\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
        {"cr6": [], "cr1000x": [], "cr300": [], "cr350": []},
    ),
    (
        "s3",
        'ESSVariables Dim\nPublic X, S As String\nBeginProg\n  S = "abc\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
        {
            "cr6": ["line 4: String without end quote", 'line 4: Invalid, or out of place expression:   S = "abc.'],
            "cr1000x": ["line 4: String without end quote", 'line 4: Invalid, or out of place expression:   S = "abc.'],
            "cr300": [
                "line 4: String without end quote",
                'line 4: Invalid, or out of place expression:   S = "abc',
                ".",
            ],
            "cr350": [
                "line 4: String without end quote",
                'line 4: Invalid, or out of place expression:   S = "abc',
                ".",
            ],
        },
    ),
    (
        "s7",
        'ESSVariables Dim\nPublic X, S As String\nBeginProg\n  ESSInitialize("public")\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n',
        {"cr6": [], "cr1000x": [], "cr300": [], "cr350": []},
    ),
    (
        "v1",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    S = "abc\n  NextScan\nEndProg\n',
        {
            "cr6": ["line 6: String without end quote", 'line 6: Invalid, or out of place expression:     S = "abc.'],
            "cr1000x": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc.',
            ],
            "cr300": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc',
                ".",
            ],
            "cr350": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc',
                ".",
            ],
        },
    ),
    (
        "v2",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    Battery("abc\n  NextScan\nEndProg\n',
        {
            "cr6": ["line 6: Invalid or missing parameter."],
            "cr1000x": ["line 6: Invalid or missing parameter."],
            "cr300": ["line 6: Invalid or missing parameter."],
            "cr350": ["line 6: Invalid or missing parameter."],
        },
    ),
    (
        "v3",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    If X Then S = "abc\n  NextScan\nEndProg\n',
        {
            "cr6": ["line 6: String without end quote", 'line 6: Invalid, or out of place expression: S = "abc:endif.'],
            "cr1000x": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression: S = "abc:endif.',
            ],
            "cr300": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression: S = "abc:endif.',
            ],
            "cr350": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression: S = "abc:endif.',
            ],
        },
    ),
    (
        "v4",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    Call Foo("abc\n  NextScan\nEndProg\n',
        {
            "cr6": ["line 6: Invalid or malformed function argument."],
            "cr1000x": ["line 6: Invalid or malformed function argument."],
            "cr300": ["line 6: Invalid or malformed function argument."],
            "cr350": ["line 6: Invalid or malformed function argument."],
        },
    ),
    (
        "v5",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    S = "abc" + "def\n  NextScan\nEndProg\n',
        {
            "cr6": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc" + "def.',
            ],
            "cr1000x": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc" + "def.',
            ],
            "cr300": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc" + "def',
                ".",
            ],
            "cr350": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc" + "def',
                ".",
            ],
        },
    ),
    (
        "v6",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    S = "abc\n    X = Q\n  NextScan\nEndProg\n',
        {
            "cr6": ["line 6: String without end quote", 'line 6: Invalid, or out of place expression:     S = "abc.'],
            "cr1000x": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc.',
            ],
            "cr300": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc',
                ".",
                "line 7: Q is not defined.",
            ],
            "cr350": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc',
                ".",
                "line 7: Q is not defined.",
            ],
        },
    ),
    (
        "v7",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    X = Q\n    S = "abc\n  NextScan\nEndProg\n',
        {
            "cr6": ["line 7: String without end quote", 'line 7: Invalid, or out of place expression:     S = "abc.'],
            "cr1000x": [
                "line 7: String without end quote",
                'line 7: Invalid, or out of place expression:     S = "abc.',
            ],
            "cr300": [
                "line 6: Q is not defined.",
                "line 7: String without end quote",
                'line 7: Invalid, or out of place expression:     S = "abc',
                ".",
            ],
            "cr350": [
                "line 6: Q is not defined.",
                "line 7: String without end quote",
                'line 7: Invalid, or out of place expression:     S = "abc',
                ".",
            ],
        },
    ),
    (
        "v8",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    Foo("abc\n  NextScan\nEndProg\n',
        {
            "cr6": ["line 6: Invalid or malformed function argument."],
            "cr1000x": ["line 6: Invalid or malformed function argument."],
            "cr300": ["line 6: Invalid or malformed function argument."],
            "cr350": ["line 6: Invalid or malformed function argument."],
        },
    ),
    (
        "v9",
        'Public X, S As String\nSub Foo (A As String)\nEndSub\nBeginProg\n  Scan (1,Sec,0,0)\n    S = "abc   \n  NextScan\nEndProg\n',
        {
            "cr6": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc   .',
            ],
            "cr1000x": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc   .',
            ],
            "cr300": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc   ',
                ".",
            ],
            "cr350": [
                "line 6: String without end quote",
                'line 6: Invalid, or out of place expression:     S = "abc   ',
                ".",
            ],
        },
    ),
]


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize("name,text,expected", CASES, ids=[c[0] for c in CASES])
def test_unterminated_string(name, text, expected, logger):
    assert lines(text, logger) == expected[logger]
