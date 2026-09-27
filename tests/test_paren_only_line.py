"""A statement made only of `(`, `)` and `,` compiles silently on all four loggers.

Oracle probes (`docs/checker.md` "Pass-1 fallback: statements that start with a known word"):
`)`, `))`, `(`, `((((`, `)(`, `( )`, `),`, `) ) )` and `)  'comment`, in a `BeginProg` body or
before `BeginProg`, give `Compiled in PipelineMode.` (CR6/CR1000X) / `Compiled OK.` (CR300/CR350).
`) X banana`, `) + 3` and `,x` still get the generic fallback.
"""

from __future__ import annotations

import pytest

from opencrbasic import checker, parser, profiles
from opencrbasic.compiler import CompileResult


def messages(text: str, logger: str = "cr6") -> list[str]:
    profile = profiles.get(logger)
    program, diags = parser.parse_text(text, "t.CR6", profile=profile)
    checker.check(program, profile, diags, CompileResult("t.CR6", profile, diags))
    return [d.message for d in diags.errors]


def body(line: str) -> str:
    return f"Public Pi, X\nBeginProg\n{line}\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n"


@pytest.mark.parametrize("logger", ["cr6", "cr1000x", "cr300", "cr350"])
@pytest.mark.parametrize("line", ["  )", "  ))", "  (", "  ((((", "  )(", "  ( )", "  ),",
                                  "  ) ) )", "  )  'comment"])
def test_paren_only_line_is_silent(line, logger):
    assert messages(body(line), logger) == []


def test_paren_only_line_before_beginprog_is_silent():
    assert messages("Public Pi, X\n)\nBeginProg\n  Scan (1,Sec,0,0)\n  NextScan\nEndProg\n") == []


@pytest.mark.parametrize("line", ["  ) X banana", "  ) + 3", "  ,x"])
def test_paren_then_other_tokens_still_falls_back(line):
    assert messages(body(line)) == [f"Invalid, or out of place expression: {line}."]
