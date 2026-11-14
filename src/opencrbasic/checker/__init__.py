"""Semantic checker: `check(program, profile, diags, result)`.

After cutting off `#...` trailing statements, rotating any slow-sequence opener via
`structure.open_program_at_slowsequence`, and building one shared `context.Context` per compile,
diagnostic positions and owning phases are populated automatically via `diagnostics._ACTIVE_POSITIONS`
and `diagnostics._ACTIVE_PHASE`.

Compilation runs in explicit phases whose findings go either to `diags.items` (gating/core flow) or
`diags.asides` (deferred/ordered side buckets merged at the end):

1. `pass1`: ungated parse/declaration/shape/const/parameter/arity checks (`structure.check_pass1`,
   `expressions`, `intervals`, `var_types`, `instr_rules`, `instr_measure`, `cr3xx`, `storage`,
   `instr_data`; plus `structs.check_members` on CR6/CR1000X when clean). Multi-index member errors
   emitted by `expressions.check_function_arity` carry `phase="held"` and wait until after `early`.
2. `early` (when `not profile.early_warnings_gated`): `warnings.check_early` and
   `instr_measure.check_scan_delay_budget`. Warnings remain on `diags.items`; errors move to aside
   `phase="early"` (dropped if pass 1 recorded any error).
3. `stage2a` (when pass 1 recorded no errors): `instr_data.check_stage2a`. Its `wind_subinterval`
   finding moves to aside `phase="soft"`; any other stage-2a error suppresses unreached warnings and
   all `early` errors except an earlier `datainterval_non_negative`.
4. `tier_b` / `tier_b_generic` / `tier_b_calltable` (when pass 1 + stage 2a are clean, or
   unconditionally on `profile.single_pass`): block closers, `structure.check_pass2` (`nested_scan`
   moves to aside `early_scan` on CR6/CR1000X), stray-control and table/menu placement,
   expression/symbol/member resolution, `ess`, `sdm_address`, unclosed `CallTable`/table refs,
   CR300/CR350 Tier-B passes, and CR6/CR1000X aside buckets (`pipe`, `bounds`, `fields`, `ranges`,
   `claims`, `leading`). Errors past the earliest placement/table stop are cut off; on CR6/CR1000X,
   `dimensions_not_declared` promotes to a gating `tier_b` error and clears `invalid_symbol` errors
   and asides.
5. `late` / `late_scan`: CR300/CR350 deferred `Sub` calls, executable `DataTable` checks, late
   settings/option/bounds/table-reps checks, gated early warnings, and `check_scan_placement_late`.
   Surviving `held` errors are activated here (suppressing same-statement/line undefined-symbol
   errors), and `late*`/`held` findings are marked `cascade=False`.
6. `tier_c` / `tier_c_apply` / `tier_c_tail`: runs when no gating error blocks Tier C (`pass1`,
   `stage2a`, `late*`, non-generic `tier_b`, plus `tier_b_generic`/`held`/`TABLE_NAME_KINDS` on
   `single_pass`; `tier_b_calltable` `could_not_find_table` never blocks Tier C). Runs `instr_measure`,
   `var_types.check`, `terminals.check`, `instr_data`, and `structure.check_apply_restart_sequence`.
7. `structs_late` (CR300/CR350 when clean), `end_blocks` (clean and no `fields`/`pipe`/`early`
   asides), and `units` (clean and no active blocking asides).

`_resolve` applies all remaining cutoffs (`out_of_memory`, generic/unknown-table Tier-C bounds,
`table_stop`), stably sorts Tier B + Tier C errors by expanded position when both appear, merges and
orders `tier_c_apply`, `pipe`, `bounds` (`_place_index_bounds`), `fields`, `ranges`, `early_scan`,
`early`, `soft`, `claims` (`sdi12_conflict`), and `leading` in one pass over `diags`, then
`link` (`structure.finalize_call_links`), `cr3xx` single-pass post-passes, `intervals`, `mode.decide`,
and error-free post-compile checks (`warnings`, `instr_measure.check_cpi_time`, `tables_hook.attach`,
`instr_data.check_duplicate_fields`, `storage.check`) run. See `docs/checker.md` and `docs/loggers.md`.
"""

import dataclasses
import os
from collections import Counter
from contextvars import ContextVar

from .. import consteval, mode, tables_hook
from .. import syntax as S
from ..diagnostics import (
    _ACTIVE_PHASE,
    COULD_NOT_FIND_KINDS,
    INVALID_PORT_KINDS,
    PLACEMENT_MENU_KINDS,
    TABLE_NAME_KINDS,
    Diagnostic,
    Diagnostics,
)
from ..profiles import Profile
from . import (
    context,
    cr3xx,
    ess,
    expressions,
    instr_data,
    instr_measure,
    instr_rules,
    intervals,
    storage,
    structs,
    structure,
    terminals,
    var_types,
    warnings,
)

_current_result: ContextVar = ContextVar("opencrbasic_checker_result", default=None)
_ASIDE = object()
_ASIDE_CUTOFF_PHASES = frozenset({"bounds", "pipe", "leading", "claims", "ranges", "fields"})
_TIER_C_PHASES = frozenset({"tier_c", "tier_c_apply", "tier_c_tail"})
_EARLY_PHASES = frozenset({"early", "early_scan"})
_PASS2_ORDER_PHASES = frozenset(
    {
        "tier_b",
        "tier_b_generic",
        "tier_b_calltable",
        "late",
        "late_scan",
        "held",
        "tier_c",
        "tier_c_apply",
        "tier_c_tail",
    }
)
_POST_TIER_C_PHASES = frozenset({"structs_late", "end_blocks", "units"})


def _is_unreached(d: Diagnostic) -> bool:
    return d.severity == "warning" and d.kind == "unreached"


def _pos_or_line(d: Diagnostic) -> int:
    return d.pos if d.pos >= 0 else d.line


def _before_cutoff(d: Diagnostic, stop_at: int | None) -> bool:
    if stop_at is None:
        return True
    return d.pos < 0 or d.pos <= stop_at


def _blocks_tier_c(d: Diagnostic, single_pass: bool) -> bool:
    if d.phase == "tier_b_calltable" and d.kind == "could_not_find_table":
        return False
    if not single_pass and (d.phase in ("tier_b_generic", "held") or d.kind in TABLE_NAME_KINDS):
        return False
    return True


def _is_tier_b_generic(d: Diagnostic) -> bool:
    return d.phase in ("tier_b_generic", "held") or (d.phase == "tier_b_calltable" and d.kind == "could_not_find_table")


def _run_pass(fn, *args, phase: str = "") -> None:
    """Never let one checker pass's bug take down the whole compile (AGENTS.md: a crash is worse
    than a wrong-but-returned answer; `docs/fuzz-findings.md` "Hang"/"Crash"). A pass that raises
    is treated as having found nothing further this run: whatever diagnostics it already recorded
    stand, and every other pass still runs.
    """
    token = _ACTIVE_PHASE.set(phase) if phase else None
    try:
        fn(*args)
    except Exception as exc:
        _record_exception(fn, exc)
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise
    finally:
        if token is not None:
            _ACTIVE_PHASE.reset(token)


def _run_aside(phase: str, diags: Diagnostics, fn, *args) -> None:
    """Run an aside pass, stamping `phase` and recording findings into `diags.asides`."""
    bucket = Diagnostics()
    call_args = tuple(bucket if arg is _ASIDE else arg for arg in args)
    token = _ACTIVE_PHASE.set(phase)
    try:
        fn(*call_args)
    except Exception as exc:
        _record_exception(fn, exc)
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise
    finally:
        _ACTIVE_PHASE.reset(token)
        diags.asides.extend(bucket.items)


# Instructions whose compile function rejects placement (`TimerInput must be used within a
# Scan...NextScan.`, `MenuItem must be used within DisplayMenu..EndMenu.`) before resolving their own
# arguments: an undeclared name in them is not reported (oracle, CR6/CR1000X, 2026-10-12, `docs/checker.md`
# "Round 14 placement before arguments"). `PulseCount` resolves its arguments first and is not listed.
_PLACEMENT_FIRST_KINDS = {
    "TIMERINPUT": frozenset({"placement_scan"}),
    "MENUITEM": PLACEMENT_MENU_KINDS,
    "MENURECOMPILE": PLACEMENT_MENU_KINDS,
}


def _placement_rejected(program: S.Program, profile, diags: Diagnostics) -> frozenset:
    """`id()`s of the `_PLACEMENT_FIRST_KINDS` statements that already carry their placement error."""
    if profile.single_pass:
        return frozenset()
    found = set()
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kinds = _PLACEMENT_FIRST_KINDS.get(stmt.keyword.upper())
        if kinds is None:
            continue
        if any(
            d.severity == "error" and d.line == stmt.line and d.file == stmt.file and d.kind in kinds
            for d in diags.items
        ):
            found.add(id(stmt))
    return frozenset(found)


def _run_pass_capture(fn, *args, phase: str = ""):
    """Like `_run_pass`, but returns `fn`'s own return value (or `None` on an exception)."""
    token = _ACTIVE_PHASE.set(phase) if phase else None
    try:
        return fn(*args)
    except Exception as exc:
        _record_exception(fn, exc)
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise
        return None
    finally:
        if token is not None:
            _ACTIVE_PHASE.reset(token)


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


def _reorder_scan_placement_after_symbols(items: list[Diagnostic]) -> list[Diagnostic]:
    """`PulseCount must be used within a Scan...NextScan.` follows its line's symbol errors (round 40)."""
    out = list(items)
    for d in [d for d in out if d.phase.startswith("tier_b") and d.kind == "placement_scan"]:
        after = [
            i
            for i, o in enumerate(out)
            if o.phase.startswith("tier_b") and (o.file, o.line) == (d.file, d.line) and o.kind == "invalid_symbol"
        ]
        if after and after[-1] > out.index(d):
            out.remove(d)
            out.insert(after[-1], d)
    return out


def _place_index_bounds(
    bounds_items: list[Diagnostic], resolved: list[Diagnostic], program: S.Program, single_pass: bool = False
) -> tuple[list[Diagnostic], list[Diagnostic]]:
    """CR6/CR1000X `Variable %s out of bounds.` from the index check (`bounds`), appended after tier C
    with two corrections (oracle, round 9, docs/checker.md "Index bounds placement"): suppress
    duplicates already printed on the line, and for `PortGet` insert ahead of `Invalid port ...`.
    CR300/CR350 (`single_pass`): only the duplicate suppression (round 31)."""
    portget = (
        set()
        if single_pass
        else {
            (st.file, st.line)
            for st in program.statements
            if isinstance(st, S.Instruction) and st.keyword.upper() == "PORTGET"
        }
    )
    out = list(resolved)
    printed = Counter(out)
    seen_bounds: Counter[Diagnostic] = Counter()
    kept = []
    for d in bounds_items:
        if seen_bounds[d] < printed[d]:
            seen_bounds[d] += 1
            continue  # the destination check printed it
        seen_bounds[d] += 1
        at = next(
            (i for i, o in enumerate(out) if (o.file, o.line) == (d.file, d.line) and o.kind in INVALID_PORT_KINDS),
            None,
        )
        if at is not None and (d.file, d.line) in portget:
            out.insert(at, d)
        else:
            kept.append(d)
    return out, kept


def _resolve(
    diags: Diagnostics,
    program: S.Program,
    flat: S.Program,
    profile: Profile,
    *,
    stop_at: int | None,
    table_stop_pos: int | None,
) -> None:
    """Apply all cutoffs, cross-phase ordering, aside merging, and per-line placement in one place."""
    resolved = list(diags.items)
    asides = list(diags.asides)
    for extra in (d for d in asides if d.phase == "tier_c_apply"):
        at = next(
            (
                i
                for i, d in enumerate(resolved)
                if (d.phase == "tier_c" and d.severity == "error" and d.line > extra.line)
                or d.phase in ("tier_c_tail", *_POST_TIER_C_PHASES)
            ),
            len(resolved),
        )
        resolved.insert(at, extra)

    oom_pair = next(
        ((i, d) for i, d in enumerate(resolved) if d.phase in _TIER_C_PHASES and d.kind == "out_of_memory"), None
    )
    if oom_pair is not None:
        # A failed builder allocation ends the compile there (CSAT3, round 31): later statements do not print.
        oom_index, oom = oom_pair
        at = _pos_or_line(oom)
        resolved = [
            d
            for i, d in enumerate(resolved)
            if (
                d.phase in _TIER_C_PHASES
                and (
                    d.severity != "error"
                    or not d.line
                    or _pos_or_line(d) < at
                    or (d.line == oom.line and i <= oom_index)
                )
            )
            or (
                d.phase not in _TIER_C_PHASES
                and (d.phase in _POST_TIER_C_PHASES or d.severity != "warning" or not d.line or _pos_or_line(d) < at)
            )
        ]
        asides = [d for d in asides if d.phase not in ("ranges", "bounds") or _pos_or_line(d) < at]

    has_generic = any(_is_tier_b_generic(d) for d in resolved)
    if has_generic:
        resolved = [
            d
            for d in resolved
            if d.phase not in _TIER_C_PHASES or d.severity != "error" or not d.line or _before_cutoff(d, stop_at)
        ]
    if profile.single_pass:
        unknown_tables = [d for d in resolved if d.phase == "tier_b_calltable" and d.kind == "could_not_find_table"]
        if unknown_tables:
            end_pos = min(_pos_or_line(d) for d in unknown_tables)
            resolved = [
                d
                for d in resolved
                if d.phase not in _TIER_C_PHASES or d.severity != "error" or not d.line or _pos_or_line(d) <= end_pos
            ]

    if has_generic and any(d.phase in _TIER_C_PHASES for d in resolved):
        # Tier-C findings print in source order among the generic pass-2 ones (oracle, CR6/CR1000X, 2026-10-04).
        slots = [
            i for i, d in enumerate(resolved) if d.phase in _PASS2_ORDER_PHASES and d.severity == "error" and d.line
        ]
        ordered = sorted((resolved[i] for i in slots), key=_pos_or_line)
        for i, d in zip(slots, ordered):
            resolved[i] = d
    if table_stop_pos is not None:
        resolved = [
            d
            for d in resolved
            if d.phase not in _TIER_C_PHASES or d.severity != "error" or d.pos < 0 or d.pos <= table_stop_pos
        ]

    # Merge asides. `pipe` sits at the tier-C boundary (or right after a same-line argument error).
    tier_c_boundary = next(
        (i for i, d in enumerate(resolved) if d.phase in _TIER_C_PHASES or d.phase in _POST_TIER_C_PHASES),
        len(resolved),
    )
    argument_error_first = structure.pipeline_argument_error_first_locations(program, profile)
    ordinary_pipe = []
    delayed_pipe = []
    for d in asides:
        if d.phase != "pipe":
            continue
        location = (d.file, d.line)
        if location in argument_error_first and any(
            item.phase in _TIER_C_PHASES and item.severity == "error" and (item.file, item.line) == location
            for item in resolved
        ):
            delayed_pipe.append(d)
        else:
            ordinary_pipe.append(d)
    resolved[tier_c_boundary:tier_c_boundary] = ordinary_pipe
    for d in delayed_pipe:
        location = (d.file, d.line)
        at = (
            min(i for i, item in enumerate(resolved) if item.severity == "error" and (item.file, item.line) == location)
            + 1
        )
        resolved.insert(at, d)

    early_items = [d for d in asides if d.phase == "early_scan"] + [d for d in asides if d.phase == "early"]
    if early_items:
        early_items = [d for d in early_items if _before_cutoff(d, stop_at)]
        if table_stop_pos is not None:
            early_items = [d for d in early_items if (0 if d.pos < 0 else d.pos) <= table_stop_pos]

    bounds_items = [d for d in asides if d.phase == "bounds"]
    if bounds_items:
        resolved, bounds_items = _place_index_bounds(bounds_items, resolved, flat, profile.single_pass)
    fields_items = [d for d in asides if d.phase == "fields"]
    ranges_items = [d for d in asides if d.phase == "ranges"]
    resolved.extend(bounds_items + fields_items + ranges_items + early_items)

    soft_items = [d for d in asides if d.phase == "soft"]
    for d in reversed(soft_items):  # before errors of its own and later lines (oracle, round 58)
        here = (_pos_or_line(d), d.line)
        at = next(
            (i for i, o in enumerate(resolved) if o.severity == "error" and (_pos_or_line(o), o.line) >= here),
            len(resolved),
        )
        resolved.insert(at, d)

    # Terminal conflicts hide nothing and print first on their line. An SDI-12 conflict is reported only
    # when no error (another conflict included) precedes it (oracle, CR6/CR1000X, round 37).
    leading_items = [d for d in asides if d.phase == "leading"]
    claims_items = [d for d in asides if d.phase == "claims"]
    others = [_pos_or_line(d) for d in resolved + leading_items + claims_items if d.severity == "error" and d.line]
    leading_items.extend(
        d for d in claims_items if d.kind != "sdi12_conflict" or not any(p < _pos_or_line(d) for p in others)
    )
    last_leading: dict[tuple[str, int], Diagnostic] = {}
    for d in leading_items:
        key = (d.file, d.line)
        if key in last_leading:
            at = next(i for i, o in enumerate(resolved) if o is last_leading[key]) + 1
        else:
            at = next((i for i, o in enumerate(resolved) if (o.file, o.line) == key), len(resolved))
        resolved.insert(at, d)
        last_leading[key] = d

    diags.items[:] = resolved
    diags.asides.clear()


def _check(program: S.Program, profile: Profile, diags: Diagnostics, result) -> None:
    # `Unknown instruction: #...` ends the compile on all four loggers: nothing after that
    # statement is reported (oracle, 2026-10-10: `#a` then `#b` -> only `#a`)
    stop = next((i for i, st in enumerate(program.statements) if S.hash_statement(st)), None)
    if stop is not None:
        program = dataclasses.replace(
            program, statements=[*program.statements[:stop], S.cut_after_hash(program.statements[stop])]
        )
        diags.trim(program.hash_cut)  # parser findings of later lines
    program = structure.open_program_at_slowsequence(program, profile)
    env = consteval.Env(profile, dict(program.consts))
    ctx = context.Context(program, profile, env)
    with context.activate(ctx):
        # Populate `pos` and `kind` on pre-existing parser diagnostics now that `ctx.positions` is active.
        diags.items[:] = [dataclasses.replace(d) for d in diags.items]
        _check_with_context(program, profile, diags, result, env, ctx)


def _check_with_context(
    program: S.Program, profile: Profile, diags: Diagnostics, result, env: consteval.Env, ctx: context.Context
) -> None:
    expressions.set_arity_context(program, profile, ctx.declared_variable_names)

    _run_pass(structure.check_pass1, program, profile, diags, env, phase="pass1")
    # Also pass 1 (unconditional -- oracle-confirmed, `docs/loggers.md`): `Const` folding happens at
    # declaration time, so undefined names inside `Const` accumulate alongside other pass-1 errors.
    _run_pass(expressions.check_consts, program, profile, diags, phase="pass1")
    _run_pass(instr_data.check_constant_destinations, program, profile, diags, env, phase="pass1")
    _run_pass(expressions.check_const_family_args, ctx.flat, profile, diags, phase="pass1")
    _run_pass(instr_measure.check_pass1_values, program, profile, diags, env, phase="pass1")
    _run_pass(var_types.check_string_operands, program, profile, diags, phase="pass1")
    _run_pass(intervals.check, program, profile, diags, env, phase="pass1")
    _run_pass(instr_rules.check_const_int_args, program, profile, diags, env, phase="pass1")
    _run_pass(expressions.check_function_call_counts, program, profile, diags, phase="pass1")
    _run_pass(expressions.check_recursive_functions, program, profile, diags, phase="pass1")
    _run_pass(expressions.check_data_params, program, profile, diags, phase="pass1")
    _run_pass(cr3xx.check, program, profile, diags, phase="pass1")
    _run_pass(cr3xx.check_dropped_tail, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check, ctx.flat, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_return_placement, program, profile, diags, phase="pass1")
    _run_pass(structure.check_missing_param_followup, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_too_many, ctx.flat, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_essinitialize_too_many, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_snmpvariable_too_many, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_snmp_oid, program, profile, diags, env, phase="pass1")
    _run_pass(instr_rules.check_modemcallback_baud, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_serialin_destination, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_serialin_comport, ctx.flat, profile, diags, env, phase="pass1")
    _run_pass(instr_rules.check_emailrecv_destination, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_tcpopen_connections, program, profile, diags, env, phase="pass1")
    _run_pass(instr_rules.check_tcpopen_handle_type, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_precision_variable, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_vibrating_wire_diagnostics, program, profile, diags, phase="pass1")
    _run_pass(instr_rules.check_iif_types, ctx.flat, profile, diags, phase="pass1")
    _run_pass(var_types.check_ctype, program, profile, diags, phase="pass1")
    _run_pass(var_types.check_pointer_deref, program, profile, diags, phase="pass1")
    _run_pass(expressions.check_function_arity, ctx.flat, profile, diags, phase="pass1")

    if not profile.single_pass and not diags.errors:
        _run_pass(structs.check_members, program, profile, diags, ctx.flat, phase="pass1")

    if not profile.early_warnings_gated:
        errors_before_early = len(diags.errors)
        _run_pass(warnings.check_early, program, profile, diags, env, phase="early")
        if not profile.single_pass and not errors_before_early:
            _run_pass(instr_measure.check_scan_delay_budget, program, profile, diags, env, True, phase="early")
        # `early` errors neither gate pass 2 nor tier C, but end past a placement stop and move aside.
        diags.asides.extend(d for d in diags.items if d.phase == "early" and d.severity == "error")
        diags.items[:] = [d for d in diags.items if not (d.phase == "early" and d.severity == "error")]

    # Multi-index member errors are tagged `phase="held"` inside `check_function_arity`: they wait for
    # pass 2 / late unless any pass-1 error already exists.
    held_items = [d for d in diags.items if d.phase == "held"]
    if held_items:
        diags.items[:] = [d for d in diags.items if d.phase != "held"]
        if not diags.errors:
            diags.asides.extend(held_items)

    _run_pass(storage.check_pass1, program, profile, diags, env, phase="pass1")
    _run_pass(instr_data.drop_unreached_destinations, program, profile, diags, env, phase="pass1")
    pass1_errors = bool(diags.errors)
    result.pass1_errors = pass1_errors
    pass1_items = diags.errors
    if pass1_errors:
        if not profile.single_pass:
            diags.items[:] = [d for d in diags.items if not _is_unreached(d)]
        diags.asides[:] = [d for d in diags.asides if d.phase != "early"]

    if not diags.errors:
        # CR6/CR1000X instruction checks between pass 1 and pass 2's expression tier.
        _run_pass(instr_data.check_stage2a, program, profile, diags, env, phase="stage2a")
        diags.asides.extend(d for d in diags.items if d.phase == "soft")
        diags.items[:] = [d for d in diags.items if d.phase != "soft"]
        if diags.errors:
            diags.items[:] = [d for d in diags.items if not _is_unreached(d)]
            # Stage-2a errors hide earlier warnings and `early` errors except a negative `DataInterval`
            # strictly preceding the first stage-2a error (round 52).
            order = {(st.file, st.line): i for i, st in enumerate(program.statements)}
            first = min(order.get((d.file, d.line), 0) for d in diags.errors)
            diags.asides[:] = [
                d
                for d in diags.asides
                if d.phase != "early"
                or (d.kind == "datainterval_non_negative" and order.get((d.file, d.line), 0) < first)
            ]

    stop_at: int | None = None
    flat = program
    links = structure.CallLinks(positions=ctx.positions)
    if not diags.errors or profile.single_pass:
        _run_aside("leading", diags, instr_rules.check_spi_open_order, ctx.flat, profile, _ASIDE)
        _run_aside("ranges", diags, terminals.check_i2c_open_order, ctx.flat, profile, _ASIDE, env)
        if profile.single_pass:
            _run_aside("leading", diags, structure.check_block_closers, program, profile, _ASIDE)
        else:
            _run_pass(structure.check_block_closers, program, profile, diags, phase="tier_b")

        flat = ctx.flat
        stop_line = _run_pass_capture(structure.check_pass2, flat, profile, diags, env, links, phase="tier_b_generic")
        if not profile.single_pass:
            diags.asides.extend(d for d in diags.items if d.phase == "early_scan")
            diags.items[:] = [d for d in diags.items if d.phase != "early_scan"]

        stops = [links.cutoff] if stop_line is not None and links.cutoff else []
        unreached_off: set[tuple[str, int]] = set()
        stray_stop = _run_pass_capture(structure.check_stray_controls, program, profile, diags, phase="tier_b")
        if stray_stop is not None:
            stops.append(stray_stop)
        _run_aside("pipe", diags, structure.check_pipeline_task_placement, program, profile, _ASIDE)
        _run_aside("pipe", diags, structure.check_slow_sequence_blocks, program, profile, _ASIDE)
        menu_stop = _run_pass_capture(
            instr_data.check_table_and_menu_placement, program, profile, diags, env, phase="tier_b"
        )
        if menu_stop is not None:
            stops.append(menu_stop)

        if not profile.single_pass:
            for stmt, _name in instr_data.unknown_calltables(program):
                stops.append((stmt.file, stmt.line))
                unreached_off.add((stmt.file, stmt.line))
            for stmt, _message, ends in instr_data.unknown_table_references(program, profile):
                if ends:
                    stops.append((stmt.file, stmt.line))
            early_table_stop = _run_pass_capture(instr_data.check_table_structure, ctx.flat, profile, Diagnostics())
            if early_table_stop is not None:
                stops.append(early_table_stop)
        stop_at = min((ctx.positions[k] for k in stops if k in ctx.positions), default=None)

        diags.items[:] = [
            d
            for d in diags.items
            if not (_is_unreached(d) and (not _before_cutoff(d, stop_at) or (d.file, d.line) in unreached_off))
        ]

        _run_pass(
            expressions.check,
            flat,
            profile,
            diags,
            env,
            _placement_rejected(flat, profile, diags),
            phase="tier_b_generic",
        )
        _run_pass(expressions.check_timedcontrol_constants, flat, profile, diags, phase="tier_b_generic")
        _run_pass(expressions.check_table_members, flat, profile, diags, phase="tier_b_generic")
        _run_pass(expressions.check_digit_members, flat, profile, diags, phase="tier_b_generic")
        _run_pass(expressions.check_undefined_dotted_bases, flat, profile, diags, phase="tier_b_generic")
        _run_pass(instr_rules.check_debug_args, flat, profile, diags, env, phase="tier_b_generic")
        _run_aside("leading", diags, instr_rules.check_serialbrk_comport, flat, profile, _ASIDE, env)
        if not profile.single_pass:
            diags.items[:] = [
                dataclasses.replace(d, show_file=False)
                if d.phase.startswith("tier_b") and d.kind == "invalid_symbol"
                else d
                for d in diags.items
            ]
            _run_pass(instr_data.move_fieldnames_symbol_errors, flat, diags, phase="tier_b_generic")
            diags.items[:] = _reorder_scan_placement_after_symbols(diags.items)

        _run_pass(instr_rules.check_mqtt_publish_payload, flat, profile, diags, phase="tier_b")
        if not profile.single_pass:
            _run_pass(instr_data.check_eventcount_tables, flat, profile, diags, phase="tier_b")
        _run_pass(ess.check, flat, profile, diags, env, phase="tier_b")

        if not profile.single_pass:
            _run_aside("bounds", diags, expressions.check_instruction_index_bounds, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_avgrun_options, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_data.check_output_datatype, flat, profile, _ASIDE, env)
            _run_aside("leading", diags, instr_data.check_odd_pair_reps, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_option_values, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_serialinrecord, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_splitstr_whole_array, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_splitstr_element_reps, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_sdmsio4_count, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_calibrate_size, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_subcall_element_bounds, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_fieldcal_variable_args, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_pulsecount_channels, flat, profile, _ASIDE)
            _run_aside("claims", diags, instr_rules.check_terminal_conflicts, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_sdi12_nan_fill, flat, profile, _ASIDE, env)
            _run_aside("leading", diags, instr_rules.check_dnp_update, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_portbridge_array, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_cdm_address_types, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_avw200_scan_time, flat, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_epi_scan_buffers, flat, profile, _ASIDE, env)

        _run_pass(instr_rules.check_sdm_address, program, profile, diags, env, phase="tier_b")
        if not profile.single_pass:
            _run_aside("ranges", diags, instr_measure.check_const_ranges, flat.statements, profile, _ASIDE, env)
        _run_pass(instr_data.check_unclosed_calltable, program, diags, profile, phase="tier_b_calltable")
        if not profile.single_pass:
            _run_aside("fields", diags, instr_rules.check_settings_fields, flat, profile, diags, _ASIDE, env)

        _run_pass(cr3xx.check_unknown_line_words, flat, profile, diags, phase="tier_b")
        _run_pass(cr3xx.check_for_header, flat, profile, diags, phase="tier_b")
        _run_pass(cr3xx.check_undefined_call_args, flat, profile, diags, phase="tier_b")
        _run_pass(cr3xx.check_float_destinations, flat, profile, diags, phase="tier_b")
        _run_pass(cr3xx.check_undefined_arg_aborts, flat, profile, diags, phase="tier_b")
        _run_pass(cr3xx.drop_too_many_after_variable_illegal, flat, profile, diags, phase="tier_b")
        _run_pass(cr3xx.drop_calltable_error_with_other_errors, program, profile, diags, phase="tier_b")

        diags.items[:] = [
            d
            for d in diags.items
            if not d.phase.startswith("tier_b") or d.severity != "error" or not d.line or _before_cutoff(d, stop_at)
        ]
        diags.asides[:] = [d for d in diags.asides if d.phase not in _ASIDE_CUTOFF_PHASES or _before_cutoff(d, stop_at)]
        if not profile.single_pass and any(
            d.phase.startswith("tier_b") and d.severity == "error" and d.kind == "dimensions_not_declared"
            for d in diags.items
        ):
            # Over-subscripted arrays behave as a pass-1 error on CR6/CR1000X (round 40):
            # promote out of `tier_b_generic` and suppress symbol errors, asides, and tier C.
            diags.items[:] = [
                dataclasses.replace(d, phase="tier_b")
                if d.phase.startswith("tier_b") and d.kind == "dimensions_not_declared"
                else d
                for d in diags.items
                if not (d.phase.startswith("tier_b") and d.kind == "invalid_symbol")
            ]
            diags.asides[:] = [d for d in diags.asides if d.phase not in (_ASIDE_CUTOFF_PHASES | _EARLY_PHASES)]

    if profile.single_pass:
        _run_pass(structure.commit_deferred_sub_error, links, profile, diags, phase="tier_b")
    if profile.single_pass and not diags.errors:
        _run_pass(structure.check_data_table_placement, program, profile, diags, phase="tier_b")

    late_stop = None
    if profile.single_pass and all(d.kind in COULD_NOT_FIND_KINDS for d in diags.errors):
        _run_aside("fields", diags, instr_rules.check_settings_fields, ctx.flat, profile, diags, _ASIDE, env)
        _run_aside("bounds", diags, instr_rules.check_option_values, ctx.flat, profile, _ASIDE, env)
    if not diags.errors:
        if profile.single_pass:
            _run_aside("bounds", diags, expressions.check_instruction_index_bounds, program, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_data.check_output_datatype, program, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_status_member_reads_late, program, profile, _ASIDE)
            _run_aside("bounds", diags, expressions.check_splitstr_whole_array, program, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_splitstr_element_reps, program, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_calibrate_size, program, profile, _ASIDE, env)
            _run_aside("bounds", diags, expressions.check_subcall_element_bounds, program, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_fieldcal_variable_args, program, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_rules.check_matrix_destination, program, profile, _ASIDE)
            _run_aside("bounds", diags, instr_rules.check_avgrun_options, program, profile, _ASIDE, env)
            _run_aside("bounds", diags, instr_data.check_eventcount_tables, program, profile, _ASIDE)
        _run_pass(instr_data.check_table_reps_late, program, profile, diags, env, phase="late")
        if profile.single_pass:
            _run_pass(instr_rules.check_dnp_update, ctx.flat, profile, diags, env, phase="late")
            _run_pass(instr_rules.check_portbridge_array, ctx.flat, profile, diags, env, phase="late")
    if (
        not diags.errors or (profile.single_pass and all(d.kind in COULD_NOT_FIND_KINDS for d in diags.errors))
    ) and profile.early_warnings_gated:
        _run_pass(warnings.check_early, program, profile, diags, env, phase="late")
        stop = _run_pass_capture(structure.check_scan_placement_late, program, profile, diags, phase="late_scan")
        ref_lines = [st.line for st, _m, ends in instr_data.unknown_table_references(program, profile) if ends]
        if profile.single_pass:
            ref_lines += [st.line for st, _name in instr_data.unknown_calltables(program)]
        if ref_lines:
            first_ref = min(ref_lines)
            diags.items[:] = [
                d for d in diags.items if d.phase != "late_scan" or d.severity != "error" or d.line <= first_ref
            ]
            if profile.single_pass:
                diags.items[:] = [d for d in diags.items if not (_is_unreached(d) and d.line >= first_ref)]
        if profile.single_pass and stop is not None and ref_lines and stop[1] < min(ref_lines):
            diags.items[:] = [
                d
                for d in diags.items
                if not (
                    d.severity == "error"
                    and d.kind in ("could_not_find_table", "could_not_find")
                    and d.file == stop[0]
                    and d.line > stop[1]
                )
            ]
        late_stop = stop
        if stop is not None:
            diags.items[:] = [
                d for d in diags.items if d.severity != "warning" or d.file != stop[0] or d.line <= stop[1]
            ]

    held_active = [d for d in diags.asides if d.phase == "held"]
    diags.asides[:] = [d for d in diags.asides if d.phase != "held"]
    if profile.single_pass:
        held_active = [d for d in held_active if late_stop is None or d.line <= late_stop[1]]
        if any(d.kind != "before_beginprog" for d in diags.errors):
            held_active = []
    else:
        held_active = [d for d in held_active if _before_cutoff(d, stop_at)]
    if held_active:
        own = {(d.file, d.line) for d in held_active}
        own_stmts = {d.stmt for d in held_active if d.stmt}
        diags.items[:] = [
            d
            for d in diags.items
            if not (
                ((d.stmt in own_stmts) if d.stmt else (d.file, d.line) in own)
                and d.severity == "error"
                and d.kind in ("invalid_symbol", "not_defined")
            )
        ]
        diags.items.extend(held_active)
    diags.items[:] = [
        dataclasses.replace(d, cascade=False) if d.phase.startswith("late") or d.phase == "held" else d
        for d in diags.items
    ]

    table_stop = None
    if not any(_blocks_tier_c(d, profile.single_pass) for d in diags.errors):
        _run_pass(instr_measure.check_reps, program, profile, diags, env, phase="tier_c")
        _run_pass(instr_measure.check_dest_bounds, program, profile, diags, env, phase="tier_c")
        _run_pass(var_types.check, program, profile, diags, env, phase="tier_c")
        if profile.single_pass:
            _run_pass(instr_measure.check_const_ranges, ctx.flat.statements, profile, diags, env, phase="tier_c")
        _run_pass(terminals.check, program, profile, diags, env, phase="tier_c")
        table_stop = _run_pass_capture(instr_data.check_table_structure, ctx.flat, profile, diags, phase="tier_c")
        _run_pass(instr_data.check_fieldnames, program, profile, diags, env, phase="tier_c")
        _run_pass(instr_data.check_fieldclassify, program, profile, diags, env, phase="tier_c")
        _run_aside("tier_c_apply", diags, structure.check_apply_restart_sequence, program, profile, _ASIDE)
        _run_pass(instr_rules.check_samplefieldcal, ctx.flat, profile, diags, phase="tier_c_tail")
        _run_pass(instr_measure.check, program, profile, diags, env, phase="tier_c_tail")
        _run_pass(instr_measure.check_scan_delay_budget, program, profile, diags, env, phase="tier_c_tail")

    aside_phases = {d.phase for d in diags.asides}
    has_errors = bool(diags.errors) or any(d.phase == "tier_c_apply" and d.severity == "error" for d in diags.asides)
    if profile.single_pass and not has_errors:
        flat_program = ctx.flat
        _run_pass(structs.check_members, program, profile, diags, flat_program, phase="structs_late")
        _run_pass(structs.check_struct_assignment, program, profile, diags, flat_program, phase="structs_late")
        has_errors = bool(diags.errors) or has_errors

    if not has_errors and not (aside_phases & {"fields", "pipe", "early", "early_scan"}):
        _run_pass(structure.check_end_blocks, program, diags, phase="end_blocks")
        has_errors = bool(diags.errors) or has_errors
    if not has_errors and not (aside_phases & {"early", "early_scan", "pipe", "fields", "bounds", "ranges", "leading"}):
        _run_pass(instr_data.check_units_targets, program, profile, diags, phase="units")

    table_stop_pos = ctx.positions[table_stop] if table_stop is not None and table_stop in ctx.positions else None
    _resolve(diags, program, flat, profile, stop_at=stop_at, table_stop_pos=table_stop_pos)

    _run_pass(structure.finalize_call_links, links, profile, diags, pass1_errors, phase="link")

    _run_pass(cr3xx.drop_block_closer_errors_after_errors, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.order_names_around_arity, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.order_names_before_call_arity, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.order_email_destination, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.drop_shortfall_with_names, program, profile, diags, phase="cr3xx")
    if profile.single_pass:
        _run_pass(instr_data.finalize_illegal_table_names, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.check_tcpopen_handle_early, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.check_too_many_cascade, ctx.flat, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.drop_compound_target_repeat, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.order_block_keyword_calls, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.fix_then_operand, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.order_invalid_assignments, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.order_target_name_first, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.order_unterminated_string, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.check_tcpopen_handle, program, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.add_string_illegal, program, profile, diags, phase="cr3xx")
    _run_pass(warnings.add_truncate_warnings, ctx.flat, profile, diags, phase="cr3xx")
    _run_pass(cr3xx.semicolon_headers, program, profile, diags, phase="cr3xx")
    _run_pass(intervals.fold_timeout_expressions, ctx.flat, profile, diags, env, pass1_items, phase="cr3xx")
    _run_pass(intervals.order_timeout_negative, diags, profile, program, phase="cr3xx")
    _run_pass(cr3xx.drop_after_alias_collision, program, profile, diags, phase="cr3xx")

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
        _run_pass(warnings.check_background_calibration, program, profile, diags, env, phase="post")
        _run_pass(warnings.check_slow_scan_skips, program, profile, diags, env, phase="post")
        _run_pass(instr_measure.check_cpi_time, program, profile, diags, env, phase="post")
        _run_pass(tables_hook.attach, program, profile, result, phase="post")
        _run_pass(instr_data.check_duplicate_fields, program, result, diags, phase="post")
        if not diags.errors:
            _run_pass(storage.check, program, profile, result, diags, phase="post")
        if not diags.errors:
            _run_pass(warnings.check, program, result, diags, phase="post")
