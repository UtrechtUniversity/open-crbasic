"""Semantic checker: `check(program, profile, diags, result)`.

Two passes, mirroring the real compiler's own architecture (`docs/checker.md` "Pass model",
oracle-confirmed): **pass 1** parses every statement into a record and is lenient about *shape* --
an unrecognised line becomes an `Expr`/`Call` record without error at this stage. Pass-1 errors are
parsing and declaration errors (parameter-count/shape by type code, `Variable not named.`,
`Units`/`Const`/`Alias` truncation, array-dimension limits, block matching); every one in the file
is reported regardless of what else is wrong. **Pass 2** runs only when pass 1 reported nothing at
all, and compiles the records in order: this is where the generic "Expr"/implicit-`Call` fallback
(`Invalid, or out of place expression: %s.`, `Sub %s not defined.`) and every instruction's own
semantic handler (`expressions.py`'s undefined-symbol/constant-only checks, `instructions.py`'s
channel/port checks, `instr_data.py`'s table-structure checks, `instr_measure.py`'s value checks)
live. Pass 2 stops entirely at the first **placement** error (a statement occurring somewhere it
structurally cannot, e.g. before `BeginProg`) -- `structure.check_pass2` is both the "Expr" fallback
*and* the only place that currently detects a placement error (the before-`BeginProg` case; two
other real placement shapes are not yet implemented, `docs/checker.md`), so its return value (the
placement error's own line, or `None`) is what `check` below uses to truncate every other pass-2
module's diagnostics to the same cutoff, without those modules needing to coordinate it themselves.

Runs, in order:

1. `structure.check_pass1` -- `BeginProg`/`EndProg` presence, `Scan`/`NextScan` and `If`/`Select`
   block matching, declaration/array-dimension errors, `Units`/`Const`/`Alias` truncation.
2. `expressions.check_consts` -- an undefined name inside a `Const`'s own value expression
   (`<name> is not defined.`/`Invalid symbol in expression: %s.`, per-logger). Also pass 1 (oracle
   evidence, `docs/loggers.md`: `Const` folding happens eagerly at declaration time, so this
   accumulates alongside an unrelated, earlier `Variable not named.` rather than being suppressed
   by it the way the *rest* of `expressions.py`'s pass-2 checks would be).
3. `instr_rules.check` -- a required-parameter position with no value at all ("Invalid or missing
   parameter.", `checker/instr_rules.py`, owned alongside this package). Also pass 1 (oracle
   evidence, `docs/checker.md`: a file with several earlier "Variable not named." errors still
   gets its own, later "Invalid or missing parameter." reported).
4. `warnings.check_early` -- reachability/background-calibration/fnotch; ungated, fires even when
   the file has other errors elsewhere (`docs/instructions.md`).

If, and only if, steps 1-4 found nothing:

5. `structure.check_pass2` -- the generic "Expr"/implicit-`Call` fallback and the before-`BeginProg`
   placement check (returns the cutoff line, if any).
6. `expressions.check` -- a bare identifier or compound expression used at an instruction argument
   position that resolves nowhere, and the constant-only-family "rejects a variable" check
   (deliberately narrow; see that module's docstring).
7. `instructions.check` -- per-instruction argument checks layered on top of the generic type-code
   parsing (the analog-channel/port terminal range checks, `terminals.py`); `instr_data.
   check_table_structure` (table-structure placement) and `instr_measure.check` (`Range`/`SW12`/
   `Delay`/`CS616`/`TimerInput`/`ExciteV`/`ExciteI` value checks, `docs/instructions.md`) run
   alongside it.
8. Every error step 5-7 added is truncated to step 5's cutoff line, if it returned one.

Then, regardless of errors so far:

9. `mode.decide` -- Sequential/PipelineMode (`mode.py`, reusing the parser's own folded `Const`
   values via `consteval.Env`, as the previous placeholder checker did).
10. `tables_hook.attach`, when the compile has no errors so far and the module is available (it is
    written by a different concurrent workstream, `PLAN.md`; guarded so this checker still runs
    standalone while that module is in progress), then `warnings.check` ("table declared but never
    called") and `instr_data.check_duplicate_fields`.

Full instruction-signature checks (the rest of `instructions.py`) and a general expression-tree
name/type resolution (the rest of `expressions.py`) are future work; see `docs/checker.md` and
`PROGRESS.md`'s phase-3 row for current coverage and the oracle evidence behind each rule above.
"""

from __future__ import annotations

from .. import consteval, mode
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile
from . import expressions, instr_data, instr_measure, instr_rules, instructions, structure, warnings


def _run_pass(fn, *args) -> None:
    """Never let one checker pass's bug take down the whole compile (AGENTS.md: a crash is worse
    than a wrong-but-returned answer; `docs/fuzz-findings.md` "Hang"/"Crash" -- both found by
    fuzzing, both in code this session does not exhaustively re-verify). A pass that raises is
    treated as having found nothing further this run: whatever diagnostics it already recorded
    (via `diags`, mutated in place) stand, and every other pass still runs. No oracle text can ever
    exist for "opencrbasic itself broke", so this never invents a diagnostic to paper over it --
    the caller is responsible for still returning a usable `CompileResult` either way.
    """
    try:
        fn(*args)
    except Exception:
        pass


def _run_pass_capture(fn, *args):
    """Like `_run_pass`, but returns `fn`'s own return value (or `None` on an exception) -- for
    `structure.check_pass2`, whose return value (the placement-error cutoff line) the caller needs.
    """
    try:
        return fn(*args)
    except Exception:
        return None


def check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    env = consteval.Env(profile, dict(program.consts))

    _run_pass(structure.check_pass1, program, profile, diags, env)
    # Also pass 1 (unconditional -- oracle-confirmed, `docs/loggers.md`): a `Const`'s own value
    # expression is folded eagerly at declaration time, so an undefined name inside it is
    # reported alongside pass-1's other declaration errors, not deferred to pass 2's gated
    # `expressions.check` below.
    _run_pass(expressions.check_consts, program, profile, diags)
    _run_pass(instr_rules.check, program, profile, diags)
    # Ungated on CR6/CR1000X (unlike step 2 below): reachability/background-calibration/fnotch all
    # oracle-confirmed to fire even when the file has other errors elsewhere (`checker/warnings.py`,
    # `docs/instructions.md`). **Not** shared by CR300/CR350 (`profile.early_warnings_gated`,
    # `docs/loggers.md`): there, this same call is deferred to the tier-C gate below instead of
    # running here unconditionally.
    if not profile.early_warnings_gated:
        _run_pass(warnings.check_early, program, profile, diags, env)

    if not diags.errors:
        # Pass 2, tier B (see this module's docstring): the generic "Expr"/implicit-`Call`
        # fallback (`structure.check_pass2`) and the undefined-symbol/constant-only-family checks
        # on a *recognised* instruction's own arguments (`expressions.check`) freely accumulate
        # with each other -- oracle-confirmed, `docs/checker.md` "Pass model": `Randomize(FooBar)`
        # (an `expressions.check` finding) followed by a separate bare `qux quux` (a
        # `structure.check_pass2` finding) reports *both*. `structure.check_pass2` also reports
        # where (if anywhere) tier B should be considered cut off, matching the real compiler's own
        # "stop entirely at the first placement error" behaviour.
        pass2_start = len(diags.items)
        cutoff = _run_pass_capture(structure.check_pass2, program, profile, diags, env)
        _run_pass(expressions.check, program, profile, diags, env)
        if cutoff is not None:
            # Every pass-2 error past the placement-error's own line is dropped, reproducing the
            # real compiler's "stop entirely" behaviour without any of the modules above needing
            # to coordinate the cutoff themselves (`docs/checker.md` "Pass model"). Warnings and
            # anything already present before this pass (pass-1 errors -- none, since `not
            # diags.errors` just gated this whole block, but defensive) are left untouched.
            diags.items = diags.items[:pass2_start] + [
                d for d in diags.items[pass2_start:]
                if d.severity != "error" or not d.line or d.line <= cutoff
            ]

    if not diags.errors:
        # Pass 2, tier C: the real compiler never reaches an instruction's own channel/range/
        # table/value validation once *either* tier A or tier B recorded any error anywhere in the
        # file -- oracle-confirmed, `docs/checker.md` "Two-pass compilation"/"Pass model":
        # `ExciteV(Vx1,...)` (an `expressions.check` finding, tier B) on an earlier line suppresses
        # a later, otherwise-invalid `TCDiff` channel argument entirely -- no accumulation across
        # this boundary, unlike within tier B itself above.
        if profile.early_warnings_gated:
            # CR300/CR350 only (see the step-4 comment above): `check_early` itself is gated the
            # same way tier C is, not run unconditionally.
            _run_pass(warnings.check_early, program, profile, diags, env)
        _run_pass(instructions.check, program, profile, diags, env)
        # Also pass 2 (oracle-confirmed the same way, `docs/instructions.md`): output-processing
        # instructions placed outside `DataTable...EndTable`, and an orphan `EndTable`.
        _run_pass(instr_data.check_table_structure, program, profile, diags)
        # Also pass 2 (oracle-confirmed the same way, `docs/instructions.md`): `Range`/`SW12`/
        # `Delay`/`CS616`/`TimerInput`/`ExciteV`/`ExciteI` value-set and value-range checks.
        _run_pass(instr_measure.check, program, profile, diags, env)

    def is_constant(expr: S.Expr | None) -> bool:
        return consteval.is_constant(expr, env)

    def value_of(expr: S.Expr | None):
        return consteval.value_of(expr, env)

    try:
        result.mode = mode.decide(program.statements, profile, is_constant, value_of)
    except Exception:
        result.mode = None

    if not diags.errors:
        try:
            from .. import tables_hook
        except ImportError:
            pass
        else:
            _run_pass(tables_hook.attach, program, profile, result)
            _run_pass(warnings.check, program, result, diags)
            # `Duplicate field name %s in DataTable %s.` -- needs `result.tables`, just attached
            # above (`checker/instr_data.py`, `docs/instructions.md`).
            _run_pass(instr_data.check_duplicate_fields, program, result, diags)
