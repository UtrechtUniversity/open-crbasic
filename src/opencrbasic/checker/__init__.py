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
   placement check (returns the stop, if any).
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
9. Every error step 6-8 added is truncated to the earliest stop (expanded-source position) of steps 6-8.

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
from ..diagnostics import Diagnostic, Diagnostics
from ..profiles import Profile
from . import (
    cr3xx, ess, expressions, instr_data, instr_measure, instr_rules, instructions, intervals,
    structs, structure, var_types, warnings,
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
        result.record_implementation_failure(f"{fn.__module__}.{fn.__name__}", exc)


def check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    token = _current_result.set(result)
    try:
        _check(program, profile, diags, result)
    finally:
        _current_result.reset(token)


def _place_index_bounds(bounds: Diagnostics, diags: Diagnostics, program: S.Program) -> None:
    """CR6/CR1000X `Variable %s out of bounds.` from the index check (`bounds`), which is appended
    after tier C, with two corrections (oracle, round 9, docs/checker.md "Index bounds placement"):
    a message the destination check already printed on the line is not printed twice
    (`VoltSe(A(11),1,..,13,..)`), and for `PortGet` the destination (argument 1) comes before the
    port (argument 2), so it is inserted ahead of `Invalid port ...` (`PortSet(1,A(11))` keeps the
    port first)."""
    portget = {(st.file, st.line) for st in program.statements
               if isinstance(st, S.Instruction) and st.keyword.upper() == "PORTGET"}
    kept = []
    printed = list(diags.items)  # before any insertion below
    for n, d in enumerate(bounds.items):
        same = lambda o: (o.file, o.line, o.message) == (d.file, d.line, d.message)  # noqa: E731
        if sum(map(same, bounds.items[:n])) < sum(map(same, printed)):
            continue  # the destination check printed it
        at = next((i for i, o in enumerate(diags.items) if (o.file, o.line) == (d.file, d.line)
                   and o.message.startswith("Invalid port")), None)
        if at is not None and (d.file, d.line) in portget:
            diags.items.insert(at, d)
        else:
            kept.append(d)
    bounds.items = kept


def _check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    # `Unknown instruction: #...` ends the compile on all four loggers: nothing after that
    # statement is reported (oracle, 2026-10-10: `#a` then `#b` -> only `#a`)
    stop = next((i for i, st in enumerate(program.statements) if S.hash_statement(st)), None)
    if stop is not None:
        program = dataclasses.replace(program, statements=[*program.statements[:stop],
                                                           S.cut_after_hash(program.statements[stop])])
        if program.hash_cut is not None:
            del diags.items[program.hash_cut:]  # parser findings of later lines
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
    _run_pass(instr_rules.check_return_placement, program, profile, diags)
    # The leftover line a required-parameter shortfall's own wrapped argument list spills onto
    # (`checker/structure.py`'s `check_missing_param_followup` docstring for the oracle evidence):
    # also ungated, since it fires precisely because the call just above already found something.
    _run_pass(structure.check_missing_param_followup, program, profile, diags)
    _run_pass(instr_rules.check_too_many, program, profile, diags)
    _run_pass(instr_rules.check_essinitialize_too_many, program, profile, diags)
    _run_pass(instr_rules.check_snmpvariable_too_many, program, profile, diags)
    _run_pass(instr_rules.check_modemcallback_baud, program, profile, diags)
    _run_pass(instr_rules.check_serialin_destination, program, profile, diags)
    _run_pass(instr_rules.check_emailrecv_destination, program, profile, diags)
    _run_pass(instr_rules.check_tcpopen_connections, program, profile, diags, env)
    # `AddPrecise`/`MovePrecise`'s own `PrecisionVariable` argument (`instr_rules.py`'s own
    # `_PRECISION_VARIABLE_POSITIONS` docstring for the oracle evidence). Also pass 1,
    # unconditional -- oracle-confirmed the same way as the two calls just above.
    _run_pass(instr_rules.check_precision_variable, program, profile, diags)
    # Built-in function call arity (`Expression expects , : %s.` / `No Matching, or out of place )
    # in expression: %s.`, `checker/expressions.py`'s `check_function_arity`, own module docstring
    # for the oracle evidence and the safe allow-lists). Also pass 1, unconditional -- oracle-
    # confirmed the same way as the two calls just above (`docs/checker.md`).
    _run_pass(var_types.check_ctype, program, profile, diags)
    held_member: list = []  # compile-stage `Member` index errors, see `pass1_errors` below
    _run_pass(expressions.check_function_arity, _with_inline(program), profile, diags, held_member)
    # Ungated on CR6/CR1000X (unlike step 2 below): reachability/background-calibration/fnotch all
    # oracle-confirmed to fire even when the file has other errors elsewhere (`checker/warnings.py`,
    # `docs/instructions.md`). **Not** shared by CR300/CR350 (`profile.early_warnings_gated`,
    # `docs/loggers.md`): there, this same call waits until tier B and the table `Reps` checks are
    # clean.
    if not profile.single_pass and not diags.errors:
        # CR6/CR1000X: an unknown structure member is a pass-1 error (`checker/structs.py`).
        _run_pass(structs.check_members, program, profile, diags, _with_inline(program))
    early = Diagnostics()  # CR6/CR1000X `Scan`/`DataInterval` value errors, see below
    if not profile.early_warnings_gated:
        early_start = len(diags.items)
        errors_before_early = len(diags.errors)
        _run_pass(warnings.check_early, program, profile, diags, env)
        if not profile.single_pass and not errors_before_early:
            _run_pass(instr_measure.check_scan_delay_budget, program, profile, diags, env, True)
        # Pass-2 findings that neither gate pass 2 nor tier C (oracle, CR6/CR1000X, 2026-10-03:
        # `Scan(0,Sec,1,0)` plus a later `z = Q`, a bad channel or an orphan `Sample` reports
        # both) but end it past a placement stop, and hide the unclosed-block checks.
        early.items = [d for d in diags.items[early_start:] if d.severity == "error"]
        diags.items[early_start:] = [d for d in diags.items[early_start:] if d.severity != "error"]

    bounds = Diagnostics()
    fields = Diagnostics()   # `SetSetting`/`SetStatus` field-name findings, see below
    # CR6/CR1000X `X not allowed outside of Scan..NextScan in PipeLine Mode.` and `DO .. LOOP
    # cannot span across sequences.`: pass 2 errors that do not hide tier C (oracle: printed
    # together with the instruction's own `Invalid channel`/`Invalid port` and `Scan interval too
    # short` errors), merged ahead of the tier-C findings below.
    pipe = Diagnostics()

    ranges = Diagnostics()   # `measure.json` `const_ranges` findings, see below
    leading = Diagnostics()  # pass-2 findings that print ahead of their line's other findings
    links = structure.CallLinks()
    def before_cutoff(diagnostic) -> bool:  # tier B (below) redefines it once a stop is known
        return True

    held_items: list = []
    if held_member:
        # A table/status field with 3+ indices fails in the statement's compile (pass 2), not in
        # pass 1: it neither stops pass 2 nor hides the placement error, but any pass-1 error hides
        # it, and it is only reached up to the first placement stop (oracle, all four loggers,
        # 2026-10-10; message tracer: pass-1 frames for `Sin(1,2)`, compile frames for these).
        held_items = [d for d in diags.items if any(d is h for h in held_member)]
        diags.items[:] = [d for d in diags.items if not any(d is h for h in held_member)]
        if diags.errors:
            held_items = []
    pass1_errors = bool(diags.errors)
    result.pass1_errors = pass1_errors
    generic_errors: list = []  # pass-2 argument/expression findings (CR6/CR1000X: no tier-C gate)
    if pass1_errors:
        early.items = []  # hidden by any pass-1 error (oracle, CR6/CR1000X, 2026-10-03)
    if not diags.errors:
        # CR6/CR1000X instruction checks between pass 1 and pass 2's expression tier; an error
        # here skips tier B (`instr_data.check_stage2a`).
        _run_pass(instr_data.check_stage2a, program, profile, diags, env)
        if diags.errors:
            early.items = []  # hidden by a stage-2a error too (oracle, CR6/CR1000X, 2026-10-04:
            # `PanelTemp(1,15000)` under `Scan(0,Sec,3,0)` reports only `Invalid Variable or Array`)
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
        _run_pass(instr_rules.check_spi_open_order, _with_inline(program), profile, leading)
        # Block-closer messages come before a same-line placement error (`EndIf illegally
        # occurs before BeginProg.` follows `END IF without IF...`).
        # CR300/CR350: collected aside in `leading` (printed first on their line, but they do not
        # count as errors for the placement stop; `cr3xx.drop_block_closer_errors_after_errors`).
        _run_pass(structure.check_block_closers, program, profile,
                  leading if profile.single_pass else diags)
        # Tier B also checks the statements of a one-line `If` (oracle: `If X Then Y = 1` ->
        # `Invalid, or out of place expression: Y = 1.`).
        flat = _with_inline(program)
        n_generic = len(diags.items)
        stop_line = _run_pass_capture(structure.check_pass2, flat, profile, diags, env, links)
        generic_errors += diags.items[n_generic:]
        # Every stop is one expanded-source position (`links.positions`), the earliest wins; an
        # included file has its own line numbers, so a physical line cannot order them.
        # Like the `Scan` value errors, a nested `Scan` neither gates tier C nor survives past a
        # placement stop (oracle, CR6/CR1000X, 2026-10-03: a later bad channel is still reported).
        if not profile.single_pass:
            early.items[:0] = [d for d in diags.items[pass2_start:]
                               if d.message == structure.NESTED_SCAN]
            diags.items[pass2_start:] = [d for d in diags.items[pass2_start:]
                                         if d.message != structure.NESTED_SCAN]
        stops = [links.cutoff] if stop_line is not None and links.cutoff else []
        stray_stop = _run_pass_capture(structure.check_stray_controls, program, profile, diags)
        if stray_stop is not None:
            stops.append(stray_stop)
        _run_pass(structure.check_pipeline_task_placement, program, profile, pipe)
        _run_pass(structure.check_slow_sequence_blocks, program, profile, pipe)
        menu_stop = _run_pass_capture(instr_data.check_table_and_menu_placement, program, profile,
                                      diags, env)
        if menu_stop is not None:
            stops.append(menu_stop)
        # An unknown `CallTable` target ends pass 2 there like a placement error (oracle, all four
        # loggers: a later `NextScan with no Scan.`/`x = 1` is not reported).
        # (CR300/CR350 order these differently, `check_scan_placement_late`: not modelled.)
        if not profile.single_pass:
            for stmt, _name in instr_data.unknown_calltables(program):
                stops.append((stmt.file, stmt.line))
        # An orphan `EndTable`/empty table ends pass 2 too (reported in tier C, `check_table_
        # structure`), cutting a later placement error.
        if not profile.single_pass:
            early_table_stop = _run_pass_capture(instr_data.check_table_structure, program,
                                                 profile, Diagnostics())
            if early_table_stop is not None:
                stops.append(early_table_stop)
        stop_at = min((links.positions[k] for k in stops if k in links.positions), default=None)

        def before_cutoff(diagnostic) -> bool:
            if stop_at is None:
                return True
            return links.positions.get((diagnostic.file, diagnostic.line), stop_at) <= stop_at

        n_generic = len(diags.items)
        _run_pass(expressions.check, flat, profile, diags, env)
        _run_pass(expressions.check_table_members, flat, profile, diags)
        _run_pass(expressions.check_digit_members, flat, profile, diags)
        generic_errors += diags.items[n_generic:]
        _run_pass(instr_rules.check_mqtt_publish_payload, flat, profile, diags)
        if not profile.single_pass:
            _run_pass(instr_data.check_eventcount_tables, flat, profile, diags)
        _run_pass(ess.check, flat, profile, diags, env)
        # Collected aside: reported with pass 2 but not hiding the tier-C checks (oracle:
        # `PortSet(1,A(9))` gives both `Invalid port 1 ...` and `Variable A out of bounds.`).
        if not profile.single_pass:
            _run_pass(expressions.check_instruction_index_bounds, flat, profile, bounds, env)
            _run_pass(instr_rules.check_avgrun_options, flat, profile, bounds, env)
            _run_pass(instr_rules.check_option_values, flat, profile, bounds, env)
            _run_pass(expressions.check_splitstr_whole_array, flat, profile, bounds, env)
            _run_pass(expressions.check_subcall_element_bounds, flat, profile, bounds, env)
            _run_pass(instr_rules.check_fieldcal_variable_args, flat, profile, bounds)
            _run_pass(instr_rules.check_pulsecount_channels, flat, profile, bounds)
            _run_pass(instr_rules.check_dnp_update, flat, profile, leading, env)
            _run_pass(instr_rules.check_portbridge_array, flat, profile, bounds, env)
            _run_pass(instr_rules.check_cdm_address_types, flat, profile, bounds, env)
            _run_pass(instr_rules.check_avw200_scan_time, flat, profile, bounds, env)
            _run_pass(instr_rules.check_epi_scan_buffers, flat, profile, bounds, env)
        bounds.items = [d for d in bounds.items if before_cutoff(d)]
        pipe.items = [d for d in pipe.items if before_cutoff(d)]
        leading.items = [d for d in leading.items if before_cutoff(d)]
        _run_pass(instr_rules.check_sdm_address, program, profile, diags, env)
        # Collected aside like the index bounds: a finding neither hides tier C (oracle: a bad
        # `SemaphoreGet` beside a bad `VoltDiff` channel reports both) nor is hidden by tier B.
        if not profile.single_pass:
            _run_pass(instr_measure.check_const_ranges, flat.statements, profile, ranges, env)
            ranges.items = [d for d in ranges.items if before_cutoff(d)]
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
            fields.items = [d for d in fields.items if before_cutoff(d)]
        # CR300/CR350 only (a no-op on every other logger, `checker/cr3xx.py`): one leading-word
        # `%s is not defined.` line per bare word of an `S.Unparsed` statement's own source text,
        # spliced in immediately ahead of `structure.check_pass2`'s generic "Invalid, or out of
        # place expression" fallback for it (`docs/loggers.md` "Round 8"). Reacts to `diags.items`
        # already emitted above, the same after-the-fact pattern `check_too_many_cascade` below
        # also uses.
        _run_pass(cr3xx.check_unknown_line_words, flat, profile, diags)
        _run_pass(cr3xx.check_for_header, flat, profile, diags)  # (`For ... To 1 Step-1`)
        # CR300/CR350 only (a no-op on every other logger, `checker/cr3xx.py`): one `%s is not
        # defined.` line per undefined bare-name argument of an `S.Call` that itself resolved to
        # nothing, spliced in between that call's own `{name} is not defined.` line (`structure.
        # _check_call_pass2`) and the generic fallback for it (`docs/loggers.md` "CDM_* argument
        # names"). Same after-the-fact `diags.items` pattern as `check_unknown_line_words` above.
        _run_pass(cr3xx.check_undefined_call_args, flat, profile, diags)
        _run_pass(cr3xx.check_float_destinations, flat, profile, diags)
        _run_pass(cr3xx.check_undefined_arg_aborts, flat, profile, diags)
        _run_pass(cr3xx.drop_too_many_after_variable_illegal, flat, profile, diags)
        _run_pass(cr3xx.drop_calltable_error_with_other_errors, program, profile, diags)
        # Every pass-2 error past the placement-error's own line is dropped, reproducing the
        # real compiler's "stop entirely" behaviour without any of the modules above needing
        # to coordinate the cutoff themselves (`docs/checker.md` "Pass model"). Warnings and
        # anything already present before this pass (pass-1 errors -- none, since `not
        # diags.errors` just gated this whole block, but defensive) are left untouched.
        diags.items = diags.items[:pass2_start] + [
            d for d in diags.items[pass2_start:]
            if d.severity != "error" or not d.line or before_cutoff(d)
        ]

    if profile.single_pass:
        _run_pass(structure.commit_deferred_sub_error, links, profile, diags)

    if profile.single_pass and not diags.errors:
        # CR300/CR350 reject an executable `DataTable` before the table-field checks that would
        # otherwise diagnose its body (`structure.check_data_table_placement`).
        _run_pass(structure.check_data_table_placement, program, profile, diags)

    late_start = len(diags.items)
    late_stop = None
    if profile.single_pass and all(d.message.startswith("Could not find ")
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
            _run_pass(expressions.check_splitstr_whole_array, program, profile, bounds, env)
            _run_pass(expressions.check_subcall_element_bounds, program, profile, bounds, env)
            _run_pass(instr_rules.check_fieldcal_variable_args, program, profile, bounds)
            _run_pass(instr_rules.check_matrix_destination, program, profile, bounds)
            _run_pass(instr_rules.check_avgrun_options, program, profile, bounds, env)
            _run_pass(instr_data.check_eventcount_tables, program, profile, bounds)
        _run_pass(instr_data.check_table_reps_late, program, profile, diags, env)
        if profile.single_pass:
            _run_pass(instr_rules.check_dnp_update, _with_inline(program), profile, diags, env)
            _run_pass(instr_rules.check_portbridge_array, _with_inline(program), profile, diags, env)
    if (not diags.errors or (profile.single_pass and all(
            d.message.startswith("Could not find ") for d in diags.errors))) and \
            profile.early_warnings_gated:
        # CR300/CR350: the early warnings follow the table `Reps` checks and precede `CallTable`
        # placement and the unclosed-block checks (oracle: `Illegal Reps 0` hides an fnotch
        # warning; `DO without LOOP.` and a `CallTable` placement error do not).
        _run_pass(warnings.check_early, program, profile, diags, env)
        stop = _run_pass_capture(structure.check_scan_placement_late, program, profile, diags)
        late_stop = stop
        if stop is not None:
            # The walk ends there, so later warnings are never printed (oracle: `SetSetting`
            # before `BeginProg` hides an fnotch warning further down).
            diags.items = [d for d in diags.items if d.severity != "warning" or
                           d.file != stop[0] or d.line <= stop[1]]
    if profile.single_pass:
        held_items = [d for d in held_items if late_stop is None or d.line <= late_stop[1]]
        if any(not d.message.endswith("illegally occurs before BeginProg.") for d in diags.errors):
            held_items = []
    else:
        held_items = [d for d in held_items if before_cutoff(d)]
    # (the failed statement's own undefined-symbol findings are not reported: the index error
    # aborts its compile -- `LoggerID=Status.StationName(1,1,Nope)`, oracle, cr6)
    own = {(d.file, d.line) for d in held_items}
    own_stmts = {d.stmt for d in held_items if d.stmt}  # tagged: exact statement identity
    diags.items[:] = [d for d in diags.items if not (
        ((d.stmt in own_stmts) if d.stmt else (d.file, d.line) in own)
        and d.severity == "error" and (
            d.message.startswith("Invalid symbol in expression") or d.message.endswith("is not defined.")))]
    diags.items.extend(held_items)
    generic_errors += held_items
    # CR300/CR350: these late errors do not start the `Too many parameters` cascade (oracle:
    # `Sample (1,Nope,FP2)` gives only `Nope is not defined.`).
    diags.items[late_start:] = [dataclasses.replace(d, cascade=False) for d in diags.items[late_start:]]
    tier_c_start = len(diags.items)
    table_stop = None
    gate = [d for d in diags.errors if profile.single_pass or not any(d is g for g in generic_errors)]
    if not gate:
        # Pass 2, tier C: the real compiler never reaches an instruction's own channel/range/
        # table/value validation once *either* tier A or tier B recorded any error anywhere in the
        # file -- oracle-confirmed, `docs/checker.md` "Two-pass compilation"/"Pass model":
        # `ExciteV(Vx1,...)` (an `expressions.check` finding, tier B) on an earlier line suppresses
        # a later, otherwise-invalid `TCDiff` channel argument entirely -- no accumulation across
        # this boundary, unlike within tier B itself above.
        _run_pass(instr_measure.check_reps, program, profile, diags, env)
        _run_pass(instr_measure.check_dest_bounds, program, profile, diags, env)
        _run_pass(var_types.check, program, profile, diags, env)

        if profile.single_pass:
            _run_pass(instr_measure.check_const_ranges, _with_inline(program).statements, profile, diags, env)
        _run_pass(instructions.check, program, profile, diags, env)
        # Also pass 2 (oracle-confirmed the same way, `docs/instructions.md`): output-processing
        # instructions placed outside `DataTable...EndTable`, and an orphan `EndTable`.
        table_stop = _run_pass_capture(instr_data.check_table_structure, program, profile, diags)
        _run_pass(instr_rules.check_samplefieldcal, _with_inline(program), profile, diags)
        if not early.items and not generic_errors:  # hidden by a `Scan` value error (oracle, CR6/CR1000X, 2026-10-03)
            _run_pass(instr_data.check_units_targets, program, profile, diags)
        # Also pass 2 (oracle-confirmed the same way, `docs/instructions.md`): `Range`/`SW12`/
        # `Delay`/`CS616`/`TimerInput`/`ExciteV`/`ExciteI` value-set and value-range checks.
        _run_pass(instr_measure.check, program, profile, diags, env)
        _run_pass(instr_measure.check_scan_delay_budget, program, profile, diags, env)
    if generic_errors:
        diags.items[tier_c_start:] = [d for d in diags.items[tier_c_start:]
                                      if d.severity != "error" or not d.line or before_cutoff(d)]
    if generic_errors and len(diags.items) > tier_c_start:
        # Tier-C findings print in source order among the generic pass-2 ones (oracle, CR6/
        # CR1000X, 2026-10-04: a bad channel on an earlier line precedes a later `foo bar`).
        tail = diags.items[pass2_start:]
        slots = [i for i, d in enumerate(tail) if d.severity == "error" and d.line]
        ordered = sorted((tail[i] for i in slots),
                         key=lambda d: links.positions.get((d.file, d.line), d.line))
        for i, d in zip(slots, ordered):
            tail[i] = d
        diags.items[pass2_start:] = tail
    if table_stop is not None and table_stop in links.positions:
        late_pos = links.positions[table_stop]
        diags.items[tier_c_start:] = [
            d for d in diags.items[tier_c_start:]
            if d.severity != "error" or links.positions.get((d.file, d.line), late_pos) <= late_pos]
    if profile.single_pass and not diags.errors:
        # CR300/CR350: structure member/assignment errors; they do not hide the early warnings
        # (oracle: `Invalid fnotch value` still printed) and are sorted in by line.
        flat_program = _with_inline(program)
        _run_pass(structs.check_members, program, profile, diags, flat_program)
        _run_pass(structs.check_struct_assignment, program, profile, diags, flat_program)
    if not diags.errors and not fields.items and not pipe.items and not early.items:
        # After tier C, not before (oracle, all four loggers: an unclosed `Scan`/`If`/`For` or a
        # missing `BeginProg` plus a tier-C finding reports only the tier-C finding).
        _run_pass(structure.check_end_blocks, program, diags)
    argument_error_first = structure.pipeline_argument_error_first_locations(program, profile)
    ordinary_pipe = []
    delayed_pipe = []
    for diagnostic in pipe.items:
        location = (diagnostic.file, diagnostic.line)
        if location in argument_error_first and any(
                item.severity == "error" and (item.file, item.line) == location
                for item in diags.items[tier_c_start:]):
            delayed_pipe.append(diagnostic)
        else:
            ordinary_pipe.append(diagnostic)
    diags.items[tier_c_start:tier_c_start] = ordinary_pipe
    for diagnostic in delayed_pipe:
        location = (diagnostic.file, diagnostic.line)
        index = min(
            i for i, item in enumerate(diags.items)
            if item.severity == "error" and (item.file, item.line) == location
        ) + 1
        diags.items.insert(index, diagnostic)

    if early.items:
        early.items = [d for d in early.items if before_cutoff(d)]
        if table_stop is not None and table_stop in links.positions:
            early.items = [d for d in early.items
                           if links.positions.get((d.file, d.line), 0) <= links.positions[table_stop]]
    if not profile.single_pass and bounds.items:
        _place_index_bounds(bounds, diags, flat)
    diags.items += bounds.items + fields.items + ranges.items + early.items
    last: dict[tuple, Diagnostic] = {}  # (file, line) -> its latest leading finding
    for d in leading.items:
        # Aside like `bounds` (tier C still runs), but printed first on its own line, in their own
        # order (oracle: `SPIOpen must precede SPIWrite.` precedes `SPIWrite illegally occurs
        # before BeginProg.`).
        key = (d.file, d.line)
        if key in last:
            at = next(i for i, o in enumerate(diags.items) if o is last[key]) + 1
        else:
            at = next((i for i, o in enumerate(diags.items) if (o.file, o.line) == key),
                      len(diags.items))
        diags.items.insert(at, d)
        last[key] = d
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
    _run_pass(cr3xx.drop_block_closer_errors_after_errors, program, profile, diags)
    _run_pass(cr3xx.order_names_around_arity, program, profile, diags)
    _run_pass(cr3xx.order_names_before_call_arity, program, profile, diags)
    _run_pass(cr3xx.order_email_destination, program, profile, diags)
    _run_pass(cr3xx.drop_shortfall_with_names, program, profile, diags)
    if profile.single_pass:
        _run_pass(instr_data.finalize_illegal_table_names, program, profile, diags)
    _run_pass(cr3xx.check_tcpopen_handle_early, program, profile, diags)
    _run_pass(cr3xx.check_too_many_cascade, _with_inline(program), profile, diags)
    _run_pass(cr3xx.drop_compound_target_repeat, program, profile, diags)
    _run_pass(cr3xx.order_block_keyword_calls, program, profile, diags)
    _run_pass(cr3xx.fix_then_operand, program, profile, diags)
    _run_pass(cr3xx.order_invalid_assignments, program, profile, diags)
    _run_pass(cr3xx.order_target_name_first, program, profile, diags)
    _run_pass(cr3xx.order_unterminated_string, program, profile, diags)
    _run_pass(cr3xx.check_tcpopen_handle, program, profile, diags)
    _run_pass(cr3xx.add_string_illegal, program, profile, diags)

    def is_constant(expr: S.Expr | None) -> bool:
        return consteval.is_constant(expr, env)

    def value_of(expr: S.Expr | None):
        return consteval.value_of(expr, env)

    try:
        result.mode = mode.decide(program.statements, profile, is_constant, value_of)
    except Exception as exc:
        _record_exception(mode.decide, exc)
        result.mode = None
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise

    if not diags.errors:
        # `When Main Sequence Scan %d is running, background calibration will be disabled.`:
        # round 6, moved here from the unconditional `warnings.check_early` call above -- oracle-
        # confirmed *not* ungated the way `_check_reachability`/`_check_fnotch` are, unlike this
        # module's own docstring previously claimed for all of `check_early` (`checker/warnings.py`'s
        # `check_background_calibration` docstring has the oracle evidence). Independent of
        # `tables_hook`'s own availability, so called ahead of that guard rather than inside it.
        _run_pass(warnings.check_background_calibration, program, profile, diags, env)
        _run_pass(instr_measure.check_cpi_time, program, profile, diags, env)
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
