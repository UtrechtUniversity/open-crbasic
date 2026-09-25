"""Semantic checker: `check(program, profile, diags, result)`.

Runs, in order:

This checker's own two-pass split mirrors an oracle-confirmed real-compiler architecture (see
"Two-pass compilation" in `docs/checker.md`): pass 1 (steps 1-3 below) parses and resolves every
statement in the file, accumulating *every* such error regardless of what came before or after;
pass 2 (step 4, plus `tables_hook`/`warnings.check`) is deeper semantic validation that the real
compiler runs only when pass 1 found nothing wrong anywhere in the file -- confirmed with the
oracle by placing an unrelated pass-1 error before, and separately after, an otherwise-invalid
channel argument: the channel check fired in neither case, only when the file had no other error at
all. `tables_hook`/`warnings.check` were already gated this way before this session; step 4 below
now matches.

1. `structure.check` -- `BeginProg`/`EndProg` presence, `Scan`/`NextScan` and `If`/`Select` block
   matching, undefined explicit `Call` targets, and the generic "not a valid statement" fallback.
2. `expressions.check` -- a bare identifier used as a direct instruction argument that resolves
   nowhere (deliberately narrow; see that module's docstring).
3. `instr_rules.check` -- a required-parameter position with no value at all ("Invalid or missing
   parameter.", `checker/instr_rules.py`, owned alongside this package; instruction-specific
   channel/port checks are a separate, concurrently-owned module, `instructions.py`, `PLAN.md`).
   Pass 1, like 1-2 above: fires regardless of earlier errors (oracle evidence, `docs/checker.md`:
   a file with several earlier "Variable not named." errors still gets its own, later "Invalid or
   missing parameter." reported).
4. `instructions.check` -- per-instruction argument checks layered on top of the generic
   type-code parsing (currently the analog-channel `U1..U12` range check); pass 2, run only when
   steps 1-3 found nothing (see above).
5. `mode.decide` -- Sequential/PipelineMode (`mode.py`, reusing the parser's own folded `Const`
   values via `consteval.Env`, as the previous placeholder checker did).
6. `tables_hook.attach`, when the compile has no errors so far and the module is available (it is
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
from . import expressions, instr_rules, instructions, structure, warnings


def check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    structure.check(program, profile, diags)
    expressions.check(program, profile, diags)
    instr_rules.check(program, profile, diags)

    env = consteval.Env(profile, dict(program.consts))
    if not diags.errors:
        # Pass 2 (see this module's docstring): the real compiler never reaches an instruction's
        # own channel/range validation once pass 1 recorded any error anywhere in the file.
        instructions.check(program, profile, diags, env)

    def is_constant(expr: S.Expr | None) -> bool:
        return consteval.is_constant(expr, env)

    def value_of(expr: S.Expr | None):
        return consteval.value_of(expr, env)

    result.mode = mode.decide(program.statements, profile, is_constant, value_of)

    if not diags.errors:
        try:
            from .. import tables_hook
        except ImportError:
            pass
        else:
            tables_hook.attach(program, profile, result)
            warnings.check(program, result, diags)
