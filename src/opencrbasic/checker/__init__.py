"""Semantic checker: `check(program, profile, diags, result)`.

Runs, in order:

1. `structure.check` -- `BeginProg`/`EndProg` presence, `Scan`/`NextScan` and `If`/`Select` block
   matching, undefined explicit `Call` targets, and the generic "not a valid statement" fallback.
2. `expressions.check` -- a bare identifier used as a direct instruction argument that resolves
   nowhere (deliberately narrow; see that module's docstring).
3. `instructions.check` -- per-instruction argument checks layered on top of the generic
   type-code parsing (currently the analog-channel `U1..U12` range check).
4. `mode.decide` -- Sequential/PipelineMode (`mode.py`, reusing the parser's own folded `Const`
   values via `consteval.Env`, as the previous placeholder checker did).
5. `tables_hook.attach`, when the compile has no errors so far and the module is available (it is
   written by a different concurrent workstream, `PLAN.md`; guarded so this checker still runs
   standalone while that module is in progress), then `warnings.check` ("table declared but never
   called").

Full instruction-signature checks (the rest of `instructions.py`) and a general expression-tree
name/type resolution (the rest of `expressions.py`) are future work; see `docs/checker.md` and
`PROGRESS.md`'s phase-3 row for current coverage and the oracle evidence behind each rule above.
"""

from __future__ import annotations

from .. import consteval, mode
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile
from . import expressions, instructions, structure, warnings


def check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    structure.check(program, profile, diags)
    expressions.check(program, profile, diags)

    env = consteval.Env(profile, dict(program.consts))
    instructions.check(program, profile, diags, env)

    def is_constant(expr: S.Expr | None) -> bool:
        return consteval.is_constant(expr, env)

    def value_of(expr: S.Expr | None):
        return consteval.value_of(expr, env)

    result.mode = mode.decide(program.statements, is_constant, value_of)

    if not diags.errors:
        try:
            from .. import tables_hook
        except ImportError:
            pass
        else:
            tables_hook.attach(program, profile, result)
            warnings.check(program, result, diags)
