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
   by it the way the *rest* of `expressions.py`'s pass-2 checks would be). `expressions.
   check_data_params` -- `Data`/`DataLong`'s own parameters, each required to be a constant
   expression with no name reference at all, defined or not (`Invalid symbol in expression: %s.`/
   `Illegal Data parameter %s.`, `docs/checker.md`) -- runs right after it, also ungated pass 1,
   the same way.
3. `cr3xx.check` -- CR300/CR350 only (a no-op on every other logger): `SerialOpen`'s own `ComPort`
   argument and every other `Serial*` function's own first argument (`docs/loggers.md`). Also
   ungated, oracle-confirmed the same way as step 2.
4. `instr_rules.check` -- a required-parameter position with no value at all ("Invalid or missing
   parameter.", `checker/instr_rules.py`, owned alongside this package). Also pass 1 (oracle
   evidence, `docs/checker.md`: a file with several earlier "Variable not named." errors still
   gets its own, later "Invalid or missing parameter." reported). `structure.
   check_missing_param_followup` runs right after it, also ungated: the physical line the
   shortfall's own wrapped argument list spills onto gets its own "Invalid, or out of place
   expression: %s." when every name it references resolves (`docs/checker.md` "Missing-parameter
   followup"). `instr_rules.check_too_many` --
   the mirror image, an argument list running past the declared count ("Too many parameters:  %s.",
   round 5, `docs/checker.md`) -- runs alongside it, also pass 1 (oracle-confirmed the same way).
   `expressions.check_function_arity` -- the same shortfall/overflow shape, but for a *built-in
   function* call inside an expression rather than an instruction's own argument list ("Expression
   expects , : %s."/"No Matching, or out of place ) in expression: %s.", `docs/checker.md`); also
   pass 1, oracle-confirmed the same way.
5. `warnings.check_early` -- reachability/fnotch; ungated, fires even when the file has other
   errors elsewhere (`docs/instructions.md`). `warnings.check_background_calibration` is a
   related but separately-gated check, run at step 11 below instead (round 6, `docs/checker.md`).

If, and only if, steps 1-5 found nothing:

6. `structure.check_pass2` -- the generic "Expr"/implicit-`Call` fallback and the before-`BeginProg`
   placement check (returns the cutoff line, if any).
7. `expressions.check` -- a bare identifier or compound expression used at an instruction argument
   position that resolves nowhere, and the constant-only-family "rejects a variable" check
   (deliberately narrow; see that module's docstring). `instr_data.check_unclosed_calltable` runs
   alongside it, then `cr3xx.check_unknown_line_words` (CR300/CR350 only, one leading-word `%s is
   not defined.` line per `S.Unparsed` statement's own generic
   fallback, `docs/loggers.md` "Round 8").
8. `instructions.check` -- per-instruction argument checks layered on top of the generic type-code
   parsing (the analog-channel/port terminal range checks, `terminals.py`); `instr_data.
   check_table_structure` (table-structure placement) and `instr_measure.check` (`Range`/`SW12`/
   `Delay`/`CS616`/`TimerInput`/`ExciteV`/`ExciteI` value checks, `docs/instructions.md`) run
   alongside it.
9. Every error step 6-8 added is truncated to step 6's cutoff line, if it returned one.

Then, regardless of errors so far:

9b. `cr3xx.check_too_many_cascade` (CR300/CR350 only, a no-op on every other logger): once any
    undefined-name-class or genuine `Too many parameters:` finding has fired anywhere above, in
    any tier, every following two-plus-argument recognised instruction to end of file gets its own
    spurious `Too many parameters:  %s.` in place of whatever it would otherwise have found
    (`checker/cr3xx.py`'s own module comment for the oracle evidence). Run here, unconditionally,
    rather than inside step 7's own tier-B block, because the taint can originate from a tier-A
    finding that skips tier B/C entirely (`docs/loggers.md`).
10. `mode.decide` -- Sequential/PipelineMode (`mode.py`, reusing the parser's own folded `Const`
    values via `consteval.Env`, as the previous placeholder checker did).
11. When the compile has no errors so far: `warnings.check_background_calibration` (round 6, see
    that function's own docstring for why it is not part of step 5 above), then `tables_hook.
    attach`, when the module is available (it is written by a different concurrent workstream,
    `PLAN.md`; guarded so this checker still runs standalone while that module is in progress),
    then `warnings.check` ("table declared but never called") and
    `instr_data.check_duplicate_fields`.

Full instruction-signature checks (the rest of `instructions.py`) and a general expression-tree
name/type resolution (the rest of `expressions.py`) are future work; see `docs/checker.md` and
`PROGRESS.md`'s phase-3 row for current coverage and the oracle evidence behind each rule above.
"""

from __future__ import annotations

import dataclasses
import os
from contextvars import ContextVar

from .. import consteval, mode
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile
from . import (
    cr3xx, ess, expressions, instr_data, instr_measure, instr_rules, instructions, intervals,
    structure, var_types, warnings,
)


_current_result: ContextVar = ContextVar("opencrbasic_checker_result", default=None)


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
    except Exception as exc:
        _record_exception(fn, exc)
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise


def _with_inline(program: S.Program) -> S.Program:
    """A copy of `program` whose statement list also holds each one-line `If`'s statements, right
    after the `If`."""
    statements: list[S.Stmt] = []
    for stmt in program.statements:
        statements.append(stmt)
        if isinstance(stmt, S.If):
            statements.extend((stmt.inline or []) + (stmt.inline_else or []))
    return dataclasses.replace(program, statements=statements)


def _run_pass_capture(fn, *args):
    """Like `_run_pass`, but returns `fn`'s own return value (or `None` on an exception) -- for
    `structure.check_pass2`, whose return value (the placement-error cutoff line) the caller needs.
    """
    try:
        return fn(*args)
    except Exception as exc:
        _record_exception(fn, exc)
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise
        return None


def _record_exception(fn, exc: Exception) -> None:
    result = _current_result.get()
    if result is not None:
        result.implementation_exceptions.append(
            f"{fn.__module__}.{fn.__name__}: {type(exc).__name__}: {exc}"
        )


def check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    token = _current_result.set(result)
    try:
        _check(program, profile, diags, result)
    finally:
        _current_result.reset(token)


def _check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    env = consteval.Env(profile, dict(program.consts))

    _run_pass(structure.check_pass1, program, profile, diags, env)
    # Also pass 1 (unconditional -- oracle-confirmed, `docs/loggers.md`): a `Const`'s own value
    # expression is folded eagerly at declaration time, so an undefined name inside it is
    # reported alongside pass-1's other declaration errors, not deferred to pass 2's gated
    # `expressions.check` below.
    _run_pass(expressions.check_consts, program, profile, diags)
    # CR6/CR1000X: constant-family instruction arguments resolve in pass 1 too.
    _run_pass(expressions.check_const_family_args, program, profile, diags)
    # CR6/CR1000X: `Range` code and `fN1` value checks, same pass (`docs/instructions.md`).
    _run_pass(instr_measure.check_pass1_values, program, profile, diags, env)
    # `TimeIntoInterval`/`IfTime` constant arguments, a parser check (`checker/intervals.py`).
    _run_pass(intervals.check, program, profile, diags, env)
    _run_pass(instr_rules.check_const_int_args, program, profile, diags, env)
    _run_pass(expressions.check_function_call_counts, program, profile, diags)
    _run_pass(expressions.check_recursive_functions, program, profile, diags)
    # `Data`/`DataLong` parameter constant-only checking (`expressions.py`'s own docstring for the
    # oracle evidence). Also ungated pass 1, same reasoning as `check_consts` just above.
    _run_pass(expressions.check_data_params, program, profile, diags)
    # CR300/CR350 only (a no-op on every other logger, `checker/cr3xx.py`): `SerialOpen`'s own
    # `ComPort` argument, and every other `Serial*` function's own first argument. Also ungated
    # (oracle-confirmed, `docs/loggers.md`).
    _run_pass(cr3xx.check, program, profile, diags)
    _run_pass(instr_rules.check, program, profile, diags)
    # The leftover line a required-parameter shortfall's own wrapped argument list spills onto
    # (`checker/structure.py`'s `check_missing_param_followup` docstring for the oracle evidence):
    # also ungated, since it fires precisely because the call just above already found something.
    _run_pass(structure.check_missing_param_followup, program, profile, diags)
    _run_pass(instr_rules.check_too_many, program, profile, diags)
    _run_pass(instr_rules.check_serialin_destination, program, profile, diags)
    # `AddPrecise`/`MovePrecise`'s own `PrecisionVariable` argument (`instr_rules.py`'s own
    # `_PRECISION_VARIABLE_POSITIONS` docstring for the oracle evidence). Also pass 1,
    # unconditional -- oracle-confirmed the same way as the two calls just above.
    _run_pass(instr_rules.check_precision_variable, program, profile, diags)
    # Built-in function call arity (`Expression expects , : %s.` / `No Matching, or out of place )
    # in expression: %s.`, `checker/expressions.py`'s `check_function_arity`, own module docstring
    # for the oracle evidence and the safe allow-lists). Also pass 1, unconditional -- oracle-
    # confirmed the same way as the two calls just above (`docs/checker.md`).
    _run_pass(var_types.check_ctype, program, profile, diags)
    _run_pass(expressions.check_function_arity, program, profile, diags)
    # Ungated on CR6/CR1000X (unlike step 2 below): reachability/background-calibration/fnotch all
    # oracle-confirmed to fire even when the file has other errors elsewhere (`checker/warnings.py`,
    # `docs/instructions.md`). **Not** shared by CR300/CR350 (`profile.early_warnings_gated`,
    # `docs/loggers.md`): there, this same call waits until tier B and the table `Reps` checks are
    # clean.
    if not profile.early_warnings_gated:
        _run_pass(warnings.check_early, program, profile, diags, env)

    bounds = Diagnostics()
    fields = Diagnostics()   # `SetSetting`/`SetStatus` field-name findings, see below
    # CR6/CR1000X `X not allowed outside of Scan..NextScan in PipeLine Mode.` and `DO .. LOOP
    # cannot span across sequences.`: pass 2 errors that do not hide tier C (oracle: printed
    # together with the instruction's own `Invalid channel`/`Invalid port` and `Scan interval too
    # short` errors), merged ahead of the tier-C findings below.
    pipe = Diagnostics()
    links = structure.CallLinks()
    pass1_errors = bool(diags.errors)
    if not diags.errors:
        # CR6/CR1000X instruction checks between pass 1 and pass 2's expression tier; an error
        # here skips tier B (`instr_data.check_stage2a`).
        _run_pass(instr_data.check_stage2a, program, profile, diags, env)
    if not diags.errors or profile.single_pass:
        # Pass 2, tier B (see this module's docstring): the generic "Expr"/implicit-`Call`
        # fallback (`structure.check_pass2`) and the undefined-symbol/constant-only-family checks
        # on a *recognised* instruction's own arguments (`expressions.check`) freely accumulate
        # with each other -- oracle-confirmed, `docs/checker.md` "Pass model": `Randomize(FooBar)`
        # (an `expressions.check` finding) followed by a separate bare `qux quux` (a
        # `structure.check_pass2` finding) reports *both*. `structure.check_pass2` also reports
        # where (if anywhere) tier B should be considered cut off, matching the real compiler's own
        # "stop entirely at the first placement error" behaviour.
        pass2_start = len(diags.items)
        # Block-closer messages come before a same-line placement error (`EndIf illegally
        # occurs before BeginProg.` follows `END IF without IF...`).
        _run_pass(structure.check_block_closers, program, profile, diags)
        # Tier B also checks the statements of a one-line `If` (oracle: `If X Then Y = 1` ->
        # `Invalid, or out of place expression: Y = 1.`).
        flat = _with_inline(program)
        cutoff = _run_pass_capture(structure.check_pass2, flat, profile, diags, env, links)
        stray_stop = _run_pass_capture(structure.check_stray_controls, program, profile, diags)
        if stray_stop is not None and (cutoff is None or stray_stop < cutoff):
            cutoff = stray_stop
        _run_pass(structure.check_pipeline_task_placement, program, profile, pipe)
        _run_pass(structure.check_slow_sequence_blocks, program, profile, pipe)
        menu_stop = _run_pass_capture(instr_data.check_table_and_menu_placement, program, profile,
                                      diags, env)
        if menu_stop is not None and (cutoff is None or menu_stop < cutoff):
            cutoff = menu_stop
        _run_pass(expressions.check, flat, profile, diags, env)
        if not profile.single_pass:
            _run_pass(instr_data.check_eventcount_tables, flat, profile, diags)
        _run_pass(ess.check, flat, profile, diags, env)
        # Collected aside: reported with pass 2 but not hiding the tier-C checks (oracle:
        # `PortSet(1,A(9))` gives both `Invalid port 1 ...` and `Variable A out of bounds.`).
        if not profile.single_pass:
            _run_pass(expressions.check_instruction_index_bounds, flat, profile, bounds, env)
            _run_pass(instr_rules.check_avgrun_options, flat, profile, bounds, env)
            _run_pass(instr_rules.check_option_values, flat, profile, bounds, env)
        if cutoff is not None:
            bounds.items = [d for d in bounds.items if d.line <= cutoff]
            pipe.items = [d for d in pipe.items if d.line <= cutoff]
        _run_pass(instr_rules.check_sdm_address, program, profile, diags, env)
        # `Could not find DataTable %s.` (round 6, `checker/instr_data.py`'s
        # `check_unclosed_calltable` docstring): a `CallTable` referencing a `DataTable` that was
        # declared but never closed with `EndTable`. Also tier B, oracle-confirmed the same way as
        # the other two calls just above (accumulates with an unrelated `expressions.check` finding
        # rather than being suppressed by it).
        _run_pass(instr_data.check_unclosed_calltable, program, diags)
        # `SetSetting`/`SetStatus` field names: collected aside like the index bounds, so a finding
        # neither hides tier C nor is hidden by tier-B findings (CR6/CR1000X; CR300/CR350 run it
        # below, only when nothing else was found). `docs/instructions.md`.
        if not profile.single_pass:
            _run_pass(instr_rules.check_settings_fields, flat, profile, diags, fields, env)
            if cutoff is not None:
                fields.items = [d for d in fields.items if d.line <= cutoff]
        # CR300/CR350 only (a no-op on every other logger, `checker/cr3xx.py`): one leading-word
        # `%s is not defined.` line per bare word of an `S.Unparsed` statement's own source text,
        # spliced in immediately ahead of `structure.check_pass2`'s generic "Invalid, or out of
        # place expression" fallback for it (`docs/loggers.md` "Round 8"). Reacts to `diags.items`
        # already emitted above, the same after-the-fact pattern `check_too_many_cascade` below
        # also uses.
        _run_pass(cr3xx.check_unknown_line_words, flat, profile, diags)
        # CR300/CR350 only (a no-op on every other logger, `checker/cr3xx.py`): one `%s is not
        # defined.` line per undefined bare-name argument of an `S.Call` that itself resolved to
        # nothing, spliced in between that call's own `{name} is not defined.` line (`structure.
        # _check_call_pass2`) and the generic fallback for it (`docs/loggers.md` "CDM_* argument
        # names"). Same after-the-fact `diags.items` pattern as `check_unknown_line_words` above.
        _run_pass(cr3xx.check_undefined_call_args, flat, profile, diags)
        _run_pass(cr3xx.check_float_destinations, flat, profile, diags)
        _run_pass(cr3xx.check_undefined_arg_aborts, flat, profile, diags)
        _run_pass(cr3xx.drop_too_many_after_variable_illegal, flat, profile, diags)
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

    if profile.single_pass:
        _run_pass(structure.commit_deferred_sub_error, links, profile, diags)

    late_start = len(diags.items)
    if profile.single_pass and all(d.message.startswith("Could not find DataTable ")
                                   for d in diags.errors):
        # CR300/CR350: runs unless an earlier finding other than an unknown `CallTable` table
        # exists (`docs/instructions.md`, SetSetting and SetStatus field names).
        _run_pass(instr_rules.check_settings_fields, _with_inline(program), profile, diags,
                  fields, env)
        _run_pass(instr_rules.check_option_values, _with_inline(program), profile, bounds, env)
    if not diags.errors:
        # After passes 1 and 2 are clean: CR300/CR350 table `Reps`/bounds, then unclosed
        # `Scan`/`For`/`Do`/`If` at end of program.
        if profile.single_pass:
            _run_pass(expressions.check_instruction_index_bounds, program, profile, bounds, env)
            _run_pass(instr_rules.check_avgrun_options, program, profile, bounds, env)
            _run_pass(instr_data.check_eventcount_tables, program, profile, bounds)
        _run_pass(instr_data.check_table_reps_late, program, profile, diags, env)
    if not diags.errors and profile.early_warnings_gated:
        # CR300/CR350: the early warnings follow the table `Reps` checks and precede `CallTable`
        # placement and the unclosed-block checks (oracle: `Illegal Reps 0` hides an fnotch
        # warning; `DO without LOOP.` and a `CallTable` placement error do not).
        _run_pass(warnings.check_early, program, profile, diags, env)
        stop = _run_pass_capture(structure.check_scan_placement_late, program, profile, diags)
        if stop is not None:
            # The walk ends there, so later warnings are never printed (oracle: `SetSetting`
            # before `BeginProg` hides an fnotch warning further down).
            diags.items = [d for d in diags.items if d.severity != "warning" or
                           d.file != stop[0] or d.line <= stop[1]]
    if not diags.errors and not fields.items and not pipe.items:
        _run_pass(structure.check_end_blocks, program, diags)
    # CR300/CR350: these late errors do not start the `Too many parameters` cascade (oracle:
    # `Sample (1,Nope,FP2)` gives only `Nope is not defined.`).
    diags.items[late_start:] = [dataclasses.replace(d, cascade=False) for d in diags.items[late_start:]]
    tier_c_start = len(diags.items)
    if not diags.errors:
        # Pass 2, tier C: the real compiler never reaches an instruction's own channel/range/
        # table/value validation once *either* tier A or tier B recorded any error anywhere in the
        # file -- oracle-confirmed, `docs/checker.md` "Two-pass compilation"/"Pass model":
        # `ExciteV(Vx1,...)` (an `expressions.check` finding, tier B) on an earlier line suppresses
        # a later, otherwise-invalid `TCDiff` channel argument entirely -- no accumulation across
        # this boundary, unlike within tier B itself above.
        _run_pass(instr_measure.check_reps, program, profile, diags, env)
        _run_pass(instr_measure.check_dest_bounds, program, profile, diags, env)
        _run_pass(var_types.check, program, profile, diags, env)
        _run_pass(instructions.check, program, profile, diags, env)
        # Also pass 2 (oracle-confirmed the same way, `docs/instructions.md`): output-processing
        # instructions placed outside `DataTable...EndTable`, and an orphan `EndTable`.
        _run_pass(instr_data.check_table_structure, program, profile, diags)
        _run_pass(instr_data.check_units_targets, program, profile, diags)
        # Also pass 2 (oracle-confirmed the same way, `docs/instructions.md`): `Range`/`SW12`/
        # `Delay`/`CS616`/`TimerInput`/`ExciteV`/`ExciteI` value-set and value-range checks.
        _run_pass(instr_measure.check, program, profile, diags, env)
        _run_pass(instr_measure.check_scan_delay_budget, program, profile, diags, env)
    diags.items[tier_c_start:tier_c_start] = pipe.items

    diags.items += bounds.items + fields.items
    # `Call`s naming no declared `Sub` (`structure.finalize_call_links`): a link-step message
    # after every other error, so it does not gate the tier-C checks above.
    _run_pass(structure.finalize_call_links, links, profile, diags, pass1_errors)

    # CR300/CR350 only (a no-op on every other logger, `checker/cr3xx.py`): once any undefined-
    # name-class or genuine-`Too many parameters:` finding has fired anywhere above -- tier A
    # (ungated: `cr3xx.check`'s own `SerialOpen`/`ComPort` finding, `instr_rules.check_too_many`'s
    # own genuine finding), tier B, or tier C -- every following two-plus-argument recognised
    # instruction, to end of file, gets its own spurious `Too many parameters:  %s.` in place of
    # whatever it would otherwise have found (`checker/cr3xx.py`'s own module-level comment for the
    # oracle evidence). Run unconditionally, after every other diagnostic-producing pass, since the
    # taint can originate from a tier-A finding that would otherwise skip tier B/C entirely.
    _run_pass(cr3xx.check_too_many_cascade, program, profile, diags)
    _run_pass(cr3xx.drop_compound_target_repeat, program, profile, diags)

    def is_constant(expr: S.Expr | None) -> bool:
        return consteval.is_constant(expr, env)

    def value_of(expr: S.Expr | None):
        return consteval.value_of(expr, env)

    try:
        result.mode = mode.decide(program.statements, profile, is_constant, value_of)
    except Exception:
        result.mode = None

    if not diags.errors:
        # `When Main Sequence Scan %d is running, background calibration will be disabled.`:
        # round 6, moved here from the unconditional `warnings.check_early` call above -- oracle-
        # confirmed *not* ungated the way `_check_reachability`/`_check_fnotch` are, unlike this
        # module's own docstring previously claimed for all of `check_early` (`checker/warnings.py`'s
        # `check_background_calibration` docstring has the oracle evidence). Independent of
        # `tables_hook`'s own availability, so called ahead of that guard rather than inside it.
        _run_pass(warnings.check_background_calibration, program, profile, diags, env)
        try:
            from .. import tables_hook
        except ImportError:
            pass
        else:
            _run_pass(tables_hook.attach, program, profile, result)
            # `Duplicate field name %s in DataTable %s.` -- needs `result.tables`, just attached
            # above (`checker/instr_data.py`, `docs/checker.md` "Duplicate field names"). Its
            # errors suppress the "declared but never called" warning.
            _run_pass(instr_data.check_duplicate_fields, program, result, diags)
            if not diags.errors:
                _run_pass(warnings.check, program, result, diags)
