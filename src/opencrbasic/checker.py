"""Minimal semantic checker.

Full semantic checking (BeginProg/Scan structure, instruction signatures, table/terminal
validation) is future work (see `PLAN.md`'s phase 3). For now this module only computes
SequentialMode/PipelineMode (`mode.py`), which is enough for `python3 -m opencrbasic <file>` to
run end to end and to report a compiled program's mode line. It reuses the `Const` values the
parser already resolved (`program.consts`) for `mode.py`'s `is_constant`/`value_of` callbacks
instead of re-walking the program.
"""

from __future__ import annotations

from . import consteval, mode
from . import syntax as S
from .diagnostics import Diagnostics
from .profiles import Profile


def check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    env = consteval.Env(profile, dict(program.consts))

    def is_constant(expr: S.Expr | None) -> bool:
        return consteval.is_constant(expr, env)

    def value_of(expr: S.Expr | None):
        return consteval.value_of(expr, env)

    result.mode = mode.decide(program.statements, is_constant, value_of)
