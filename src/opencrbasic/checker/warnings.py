"""Warnings: post-table "declared but never called" (needs the whole post-`#If`-resolution
statement list, `tables_hook`-gated, pass 2) plus a small set of *ungated* warnings that the real
compiler emits regardless of whether the file has other errors (oracle-confirmed per warning
below, and generally in `docs/instructions.md`'s "Two of these run even on a failed compile"
section) -- reachability ("never reached at run time"), the CR6/CR1000X fast-scan
background-calibration note, and the CR300/CR350 fixed-set `fnotch` value check. Rules and oracle
evidence: `docs/instructions.md`; the pre-existing table-never-called rule's own evidence stays in
`docs/checker.md`/`docs/tdf-rules.md` section 13.
"""

import re

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from ..profiles import Profile
from . import instr_rules, structure, symbols


def _table_arg_name(stmt: S.Instruction) -> str | None:
    if stmt.args and isinstance(stmt.args[0], S.Name):
        return stmt.args[0].name
    return None


def _with_inline_branches(statements):
    """The statements plus those in every single-line `If ... Then stmt [Else stmt]` branch, at any
    depth. A `CallTable` there counts as a use even when the branch is never taken (oracle,
    CR6/CR1000X, 2026-10-11: `If Batt > 1 Then CallTable (Msgs)`, `If False Then CallTable (Msgs)`,
    `If Batt > 1 Then Battery (Batt) Else CallTable (Msgs)`, `If ... Then CallTable (Msgs) : Battery`
    -- `emailrecv_1.CR6` has its only call this way, and no `Table ... never called` warning)."""
    for stmt in statements:
        yield stmt
        if isinstance(stmt, S.If):
            yield from _with_inline_branches(stmt.inline or ())
            yield from _with_inline_branches(stmt.inline_else or ())


def check(program: S.Program, result, diags: Diagnostics) -> None:
    # User `DataTable(...)...EndTable` blocks only -- deliberately *not* `result.tables` (which
    # also carries the logger's own system tables, `Status`/`DataTableInfo`/`GlobalConfigTable`/
    # the `Public` table, none of which are ever `CallTable`d and must not get this warning).
    declared: dict[str, str] = {}
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "DATATABLE":
            name = _table_arg_name(stmt)
            if name:
                from .instr_data import _const_table_name
                name = _const_table_name(name, program)
                declared[name.upper()] = name

    # "Called" is deliberately not just `CallTable`: `AcceptDataRecords`' own TableName argument
    # also counts (oracle-confirmed regression, `acceptdatarecords_1.CR6`: `AcceptDataRecords
    # (3000,32769,CR1000XTable)` with no `CallTable` at all still gets no warning). Rather than
    # enumerate every instruction with a table-name argument, any bare-name argument anywhere
    # that matches a declared table counts as a use -- a table name is not reused as an ordinary
    # variable, so this is a safe superset of the real "called" set.
    from .instr_data import _const_table_name
    called = {
        name
        for stmt in _with_inline_branches(program.statements)
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() != "DATATABLE"
        for arg in stmt.args if isinstance(arg, S.Name)
        # (a String `Const` names the table its value spells, round 67)
        for name in (arg.name.upper(), _const_table_name(arg.name, program).upper())
        if name in declared
    }
    for key, name in declared.items():
        if key not in called:
            # oracle-confirmed (docs/tdf-rules.md #13, `fixeduncalled`): fires whenever no
            # `CallTable`/`CallTable(...)` for this table survives `#If` resolution, regardless
            # of `DataInterval`/fixed size; `Main.CR6`'s `Shunt_Data` is the repo's own example
            # (its only `CallTable` is inside a false-by-default `#If`).
            diags.warning(0, f"Table {name}, is declared but never called.")


# --- Ungated warnings (checker/__init__.py calls `check_early` regardless of pass-1 errors; see
# module docstring and docs/instructions.md for the oracle evidence that these fire even on an
# otherwise-failed compile) -------------------------------------------------------------------


def _check_uint1_dims(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    """CR6/CR1000X: a UINT1 variable whose element count is not a multiple of 4 is padded, one
    warning per variable on its declaration line, also in a failed compile (oracle, round 56)."""
    if profile.name not in ("cr6", "cr1000x"):
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Declaration):
            continue
        for v in stmt.vars:
            if v.type != "UINT1":
                continue
            count = 1
            for d in v.dims:
                try:
                    size = consteval.value_of(d, env)
                except consteval.NotConstant:
                    break
                if not isinstance(size, int) or isinstance(size, bool) or size < 1:
                    break
                count *= size
            else:
                if count % 4:
                    diags.warning(stmt.line, "Adjusting dimensionality of UINT1 so number of bytes "
                                             "evenly divisible by 4", stmt.file)


def check_early(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    _check_uint1_dims(program, profile, diags, env)
    _check_reachability(program, profile, diags, env)
    _check_fnotch(program, profile, diags, env)
    # Numeric `Scan`/`DataInterval` value checks: also ungated (oracle-confirmed,
    # `docs/instructions.md`), implemented in `instr_rules.py` alongside this module's other
    # required-parameter check since they are instruction-argument-shaped, not warnings.
    instr_rules.check_scan_params(program, profile, diags, env)
    instr_rules.check_scan_max_interval(program, profile, diags, env)
    instr_rules.check_data_interval(program, profile, diags, env)


def _kw(stmt: S.Stmt) -> str:
    if isinstance(stmt, S.Instruction):
        return stmt.keyword.upper()
    if isinstance(stmt, S.SubDef):
        return stmt.keyword.upper()  # SUB / FUNCTION
    if isinstance(stmt, S.Unparsed):
        word = re.match(r"\s*(DataLong|Data)\s", stmt.text or "", re.I)
        if word:
            return word.group(1).upper()  # `Data foo bar`: still a `Data` line
    return type(stmt).__name__.upper()


# Instruction keywords that are declarations/structural markers, never "executed" and so never
# flagged by `_check_reachability` even when they sit right after unreachable code (oracle:
# `docs/instructions.md` -- `Dim`/`Public`/`Const`/`ConstTable`/`Alias`/`Units`/`DataTable`/
# `StructureType`/`ObjectType` bodies are all silent; `BeginProg`/`EndProg` themselves were never
# observed flagged either). `SLOWSEQUENCE` is handled separately (it *resets* reachability, see
# below, rather than merely being skipped).
_NON_EXECUTABLE_KEYWORDS = frozenset({
    "BEGINPROG", "ENDPROG", "DATATABLE", "ENDTABLE", "ALIAS", "UNITS",
    "CONSTTABLE", "ENDCONSTTABLE", "STRUCTURE", "ENDSTRUCTURE",
    "STRUCTURETYPE", "ENDSTRUCTURETYPE", "OBJECTTYPE", "ENDOBJECTTYPE",
    "DATA", "DATALONG",  # (oracle, 2026-10-01: `Data 1,2` after `NextScan` is not flagged)
    "SEQUENTIALMODE", "PIPELINEMODE",  # (oracle, CR6, 2026-10-02: not flagged after `NextScan`)
})


def _is_program_end(stmt: S.Stmt) -> bool:
    """Bare `End` and `End Prog` (any whitespace between the words) are never flagged as unreachable
    and do not stop the search (oracle, all four loggers, 2026-10-12: `End` then `a = 1` after an
    endless `Scan` warns only for `a = 1`)."""
    if not (isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "END"):
        return False
    return not stmt.args or (len(stmt.args) == 1 and isinstance(stmt.args[0], S.Name)
                             and stmt.args[0].name.upper() == "PROG")


def _display_name(stmt: S.Stmt, profile: Profile) -> str:
    """The name the real compiler echoes in `%s never reached at run time.` -- an `Instruction`'s
    canonical table spelling (`Delay`, `Scan`, proper-cased; oracle:
    `tests/corpus/fuzz/cr6/mode_diff/19a402de98a4.CR6`/`cr1000x/mode_diff/cf78b8596121.json`), or
    `Expr`, the compiler's own generic bare-expression-statement pseudo-instruction (`data/
    instructions.json` index 5; already used the same way by `structure.py`'s "before BeginProg"
    rule) for anything else -- a plain assignment (oracle: `Expr never reached`, this session's own
    minimal probe) or a statement that failed to parse as a recognised instruction at all (oracle:
    `tests/corpus/fuzz/cr6/message_diff/caf5c15cd16b.CR6`, a mutated `EndProg` that fails to parse
    gets both `Invalid, or out of place expression:` *and* `Expr never reached at run time.`)."""
    if isinstance(stmt, S.Instruction):
        record = profile.instructions.get(stmt.keyword.upper())
        return record["name"] if record else stmt.keyword
    if isinstance(stmt, (S.DoLoop, S.For)):
        # oracle: `Do never reached`, `Loop never reached`, `FOR never reached` (table spelling)
        keyword = stmt.keyword.upper() if isinstance(stmt, S.DoLoop) else "FOR"
        record = profile.instructions.get(keyword)
        return record["name"] if record else keyword
    if isinstance(stmt, S.Call):
        return "Call"   # `Call S(1)`, `S(1)`: oracle, CR6/CR300, 2026-10-08 (docs/fuzz-findings.md)
    return "Expr"


def _open_conditional(statements: list[S.Stmt], start: int, end: int) -> int:
    """How many `If`/`Select` blocks opened inside `statements[start:end]` are still open at its
    end. Statements after `NextScan` inside them are not flagged; the one that closes the last of
    them is (oracle, CR6: `If X Then` / `NextScan` / `X = Q` gives no warning, `If X Then` /
    `NextScan` / `EndIf` gives `EndIf never reached at run time.`)."""
    depth = 0
    for stmt in statements[start:end]:
        kw = _kw(stmt)
        if (kw == "IF" and getattr(stmt, "inline", None) is None) or kw == "SELECT":
            depth += 1
        elif kw in ("ENDIF", "ENDSELECT") and depth:
            depth -= 1
    return depth


def _scan_span(statements: list[S.Stmt], start: int) -> tuple[int, bool]:
    """`statements[start]` is a top-level `SCAN`. Returns (index right after its matching
    `NEXTSCAN`, whether an `EXITSCAN` appears anywhere in between). `SubScan`'s own block uses the
    distinct `SUBSCAN`/`NEXTSUBSCAN` keywords (`data/instructions.json`), so a same-keyword depth
    counter here never confuses the two."""
    depth = 1
    has_exit = False
    j = start + 1
    n = len(statements)
    while j < n and depth > 0:
        kw = _kw(statements[j])
        if kw == "SCAN":
            depth += 1
        elif kw == "NEXTSCAN":
            depth -= 1
        elif kw == "EXITSCAN" or any(
                _kw(x) == "EXITSCAN" for x in (getattr(statements[j], "inline", None) or [])
                + (getattr(statements[j], "inline_else", None) or [])):
            has_exit = True     # (also in a one-line `If X Then ExitScan`, oracle 2026-10-10)
        j += 1
    return j, has_exit


def _scan_count_is_zero(stmt: S.Instruction, env: consteval.Env) -> bool | None:
    """The `Scan(Interval,Units,BufferOption,Count)` `Count` argument's constant value compared to
    0 (`True`/`False`), or `None` when it is missing or not a compile-time constant -- in which
    case the caller must *not* treat the scan as provably infinite (AGENTS.md: never guess into a
    false positive)."""
    args = stmt.args
    if len(args) < 4 or args[3] is None:
        return None
    try:
        return consteval.value_of(args[3], env) == 0
    except consteval.NotConstant:
        return None


def _check_reachability(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`%s never reached at run time.` -- oracle-confirmed (`docs/instructions.md`) to fire for the
    *first* executable top-level statement that textually follows a `Scan(...,0)` (loop forever)
    with no `ExitScan` anywhere in its body, up to (but not including) the next `SlowSequence`
    (which starts a genuinely separate, concurrently-scheduled task, oracle-confirmed *not*
    flagged) or the end of the statement list. Fires even when the file has unrelated errors
    elsewhere (unlike `instructions.py`'s pass-2 checks) and, once found, this checker stops
    looking for a second one -- every corpus/oracle example found only ever shows one, matching
    this project's other "one diagnostic, then silence" findings (`docs/checker.md`).

    A `Sub`/`Function` body is skipped entirely (its own statements are reachable whenever the sub
    is called, independent of where its definition sits in the file).
    """
    if any(d.message.startswith("Illegal END or EXIT ") for d in diags.items):
        return  # a pass-1 error of this kind hides the warning (oracle, CR6, 2026-10-03)
    statements = program.statements
    n = len(statements)
    dead = False
    open_depth = 0
    sub_depth = 0
    begun = False
    tasks = profile.pipeline_tasks["tasks"]
    pipeline_delays = {id(st) for st, _ in structure.pipeline_task_errors(program, profile)
                       if _kw(st) == "DELAY" or tasks.get(_kw(st), {}).get("hides_unreached")}
    i = 0
    while i < n:
        stmt = statements[i]
        kw = _kw(stmt)

        if sub_depth > 0:
            if kw in ("SUB", "FUNCTION"):
                sub_depth += 1
            elif S.ends_routine(stmt):
                sub_depth -= 1
            i += 1
            continue
        if kw in ("SUB", "FUNCTION"):
            sub_depth = 1
            i += 1
            continue

        if kw == "BEGINPROG":
            begun = True
        if kw == "SLOWSEQUENCE":
            # A `SlowSequence` starts a genuinely separate, concurrently-scheduled task (oracle:
            # `docs/instructions.md` -- a `SlowSequence`/`Scan`/`NextScan` right after an infinite,
            # no-`ExitScan` main scan is never itself flagged). This checker has no oracle evidence
            # either way for reachability *within* a slow sequence's own scan chain (a second,
            # chained slow `Scan...NextScan` the way the main sequence supports, `docs/checker.md`'s
            # own CHM "Scan Loop #1/#2" example) or for its own `EndSequence` terminator, which a
            # false positive here (10 corpus regressions during development, all real, oracle-
            # accepted programs, e.g. `ftpclient_1.CR6`'s `EndSequence`) showed is *not* flagged
            # either. Rather than guess at slow-sequence-internal reachability, stop this analysis
            # entirely once a `SlowSequence` is seen -- AGENTS.md: never guess into a false warning.
            return

        if kw == "SCAN" and isinstance(stmt, S.Instruction):
            if dead:
                diags.warning(stmt.line, f"{_display_name(stmt, profile)} never reached at run time.", stmt.file)
                return
            j, has_exit = _scan_span(statements, i)
            count_is_zero = _scan_count_is_zero(stmt, env)
            dead = bool(count_is_zero) and not has_exit
            if not begun and profile.name in ("cr6", "cr1000x"):
                dead = False   # `Scan illegally occurs before BeginProg.`: the scan is not
                # compiled, so nothing after it is unreachable (oracle, 2026-10-12, fuzz R15)
            closer = statements[j - 1] if j <= n and j > i + 1 else None
            if dead and closer is not None and hasattr(closer, "hoisted_last"):
                # `If C Then NextScan` closes the endless `Scan` from inside a one-line `If`: the
                # `If`'s own end is the first thing after it (oracle, all four loggers, 2026-10-12);
                # more statements after the `NextScan` in the body silence the check
                if closer.hoisted_last and not (j < n and (statements[j].line, statements[j].file) == (closer.line, closer.file)):
                    diags.warning(closer.line, "EndIf never reached at run time.", closer.file)
                return
            open_depth = _open_conditional(statements, i, j)
            i = j
            continue

        if dead and open_depth:
            if kw in ("ENDIF", "ENDSELECT"):
                open_depth -= 1
            elif (isinstance(stmt, S.If) and stmt.inline is None) or kw == "SELECT":
                open_depth += 1
            if open_depth:
                i += 1
                continue
        if dead and id(stmt) in pipeline_delays:
            i += 1  # its pipeline-mode error replaces its own warning (oracle); later ones follow
            continue
        if dead and (kw in structure.SCAN_ONLY or kw == "SUBSCAN"):
            return  # its compile error ends pass 2 before the reachability check (oracle; a `SubScan` after the `NextScan` is outside any `Scan`)
        if dead and (isinstance(stmt, S.If) or kw in ("SELECT", "NEXTSCAN")):
            # An unreachable `If`/`Select` (block or one-line), or a stray `NextScan`, silences the
            # check for the rest of the file (oracle, CR6: `If X Then` / `EndIf` / `X = 2` after a `NextScan` gives no
            # warning at all; `docs/instructions.md`).
            return
        if dead:
            # An `ExitScan` outside any `Scan` compiles to nothing (oracle: `ExitScan not within a
            # Scan.` and no warning), unlike `ContinueScan`.
            is_declaration = (
                isinstance(stmt, (S.Declaration, S.ConstDecl, S.Label))
                or kw in _NON_EXECUTABLE_KEYWORDS or kw == "EXITSCAN"
                or _is_program_end(stmt)
            )
            if not is_declaration:
                name = _display_name(stmt, profile)
                if isinstance(stmt, S.Call) and not stmt.explicit and not stmt.tail_ignored and \
                        profile.name in ("cr6", "cr1000x"):
                    # a bare `Nope`/`Nope(x)`/`Abs(x)` that is no Sub or Function is `Expr`; a
                    # variable gives no warning at all (oracle, CR6/CR1000X, 2026-10-12, fuzz R15)
                    routines = {st.name.upper() for st in statements if isinstance(st, S.SubDef)}
                    if stmt.name.upper() not in routines:
                        if stmt.name.upper() in symbols.declared_variable_names(program):
                            return
                        name = "Expr"
                diags.warning(stmt.line, f"{name} never reached at run time.", stmt.file)
                return
        i += 1


def check_background_calibration(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`When Main Sequence Scan %d is running, background calibration will be disabled.` --
    oracle-confirmed (`docs/instructions.md`) to fire once per *main-sequence* `Scan` (not one
    declared under `SlowSequence`, whose own minimum interval, 100mSec, is far above every
    threshold found) whose folded `Interval`/`Units` is below a logger-specific threshold in whole
    milliseconds (`profile.background_calibration`, empty for CR300/CR350: oracle-probed down to a
    100us scan interval with no such warning). Reported at the *last* statement's line, like
    `structure.py`'s `No BeginProg in Program.` (same oracle-confirmed convention, independently
    re-derived here since `structure.py` is a different workstream's owned file, `PLAN.md`).

    **Round 6, gating correction**: unlike `_check_reachability`/`_check_fnotch` (still called from
    `check_early`, genuinely ungated -- oracle-confirmed, `docs/instructions.md` "Two ungated call
    sites"), this warning is **not** ungated on CR6/CR1000X either, despite living in the same
    `check_early` function until this round and being covered by that same doc section's blanket
    claim. Oracle-confirmed (a fresh sweep this round, an unrelated undefined-symbol error placed
    both *before* and, separately, *after* an otherwise-calibration-warning-triggering `Scan(1,
    mSec,...)` in the same file): the warning is suppressed **entirely** whenever the file has any
    error anywhere, regardless of that error's own position relative to the `Scan` -- unlike
    `_check_reachability`, which still fires on a later statement even with an earlier, unrelated
    `Invalid symbol in expression` error present (`Delay never reached at run time.` alongside it,
    same probe file). A warning-only file (no errors, e.g. a reachability warning with no error
    anywhere) still gets *both* warnings together, confirming this is specifically errors, not "any
    diagnostic", that gates it. Moved to `checker/__init__.py`'s own final `if not diags.errors:`
    tier (alongside `warnings.check`/`tables_hook.attach`, the same "only on an otherwise-clean
    compile" gate already used there) instead of `check_early`'s unconditional call site -- this is
    the only change; the rule and message above are unchanged. Found as a side effect of developing
    `instr_rules.check_scan_min_interval` (this round): dozens of pre-existing `false_accept`/
    `message_diff` fuzz reproducers (`tests/corpus/fuzz/cr6/message_diff/ba333c2ee146.CR6` and
    siblings) already showed this exact extra, spurious warning line alongside an unrelated real
    error -- a genuine, previously-undocumented false-accept-shaped bug independent of this round's
    own `Scan`-interval work, not a regression it introduced.
    """
    data = profile.background_calibration
    if not data:
        return
    threshold_ms = data["threshold_ms"]
    statements = program.statements
    last_line = statements[-1].line if statements else 1

    # A scan with measurement time warns when its slack (interval minus time) is under
    # `busy_slack` units of 100 us, or under its longest integration time plus a per-logger constant
    # (oracle, 2026-10-10: CR6 `Scan(100,mSec)` + `Delay(0,97700,uSec)` warns, 97600 does not;
    # `VoltSe(...,250)` alone warns up to Scan(10,mSec), not 11; CR1000X delay boundary
    # 98000/97900). Such a scan never uses the empty-scan interval threshold above.
    from . import scan_time
    from .instr_measure import is_sequential
    # Not in SequentialMode (oracle, CR6, 2026-10-10: not even for `Scan(1,mSec)`).
    if is_sequential(program, profile, env):
        return
    busy_slack = data.get("busy_slack", 0)
    busy, timed = set(), set()
    for b in scan_time.budgets(program, env, profile):
        if b.total:
            timed.add(id(b.scan))
            # a scan whose only timed instructions are PulseCounts never warns (oracle, CR6,
            # 2026-10-10: Scan(1..9,mSec) + PulseCount)
            gap = b.max_integ + scan_time.MODELS[profile.name].busy_extra if b.max_integ > 0 else 0
            if b.interval - b.total < max(busy_slack, gap) and not b.pulse_only:
                busy.add(id(b.scan))
    in_slow = False
    main_scan_index = 0
    for stmt in statements:
        kw = _kw(stmt)
        if kw == "SLOWSEQUENCE":
            in_slow = True
            continue
        if kw != "SCAN" or in_slow or not isinstance(stmt, S.Instruction):
            continue
        main_scan_index += 1
        interval_ms = _scan_interval_ms(stmt, env)
        if id(stmt) in busy or (id(stmt) not in timed and interval_ms is not None
                                and interval_ms < threshold_ms):
            diags.warning(
                last_line,
                f"When Main Sequence Scan {main_scan_index} is running, "
                "background calibration will be disabled.",
            )


def _value_in(args: list, position: int, values: list, env) -> bool:
    """Whether 1-based argument `position` is a constant among `values`."""
    try:
        return position <= len(args) and consteval.value_of(args[position - 1], env) in values
    except consteval.NotConstant:
        return False


def check_slow_scan_skips(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`Slow Seq %d, Scan %d, may skip scans if running with Scan %d.` (round 54, oracle CR6 and
    CR1000X, `docs/checker.md` "Task instructions in PipeLine Mode"): at the last statement's line,
    for every slow-sequence `Scan` that holds a measurement instruction of
    `pipeline_tasks["slow_scan_tasks"]` and every main-sequence `Scan` whose constant interval is not
    shorter than the slow one's; nothing in SequentialMode or with errors elsewhere. Order: slow
    sequence, slow scan, main scan."""
    tasks = profile.pipeline_tasks.get("slow_scan_tasks")
    if not tasks:
        return
    from .instr_measure import is_sequential
    if is_sequential(program, profile, env):
        return
    rules = profile.pipeline_tasks["tasks"]
    mains: list[float | None] = []
    slows: list[list] = []   # [sequence, scan, interval ms or None, has a task]
    seq = scan_no = 0
    cur: list | None = None
    for stmt in _with_inline_branches(program.statements):
        kw = _kw(stmt)
        if kw == "NEXT" and re.match(r"(?i)\s*Next\s+Scan\b", stmt.text or ""):
            kw = "NEXTSCAN"
        if kw == "SLOWSEQUENCE":
            seq, scan_no = seq + 1, 0
        elif kw == "SCAN" and isinstance(stmt, S.Instruction):
            interval = _scan_interval_ms(stmt, env)
            if seq == 0:
                mains.append(interval)
            else:
                scan_no += 1
                cur = [seq, scan_no, interval, False]
                slows.append(cur)
        elif kw == "NEXTSCAN":
            cur = None
        elif cur is not None and kw in tasks and isinstance(stmt, S.Instruction):
            constants = rules.get(kw, {}).get("constant_values", {})   # (`Delay` option 0 only)
            # (`Delay(0,0,...)` and `PortSet(...,1)` start no task, oracle round 55)
            no_task = profile.pipeline_tasks.get("slow_scan_no_task", {}).get(kw, {})
            try:
                cur[3] = cur[3] or all(consteval.value_of(stmt.args[int(p) - 1], env) in allowed
                                       for p, allowed in constants.items()) and not any(
                    _value_in(stmt.args, int(p), vals, env) for p, vals in no_task.items())
            except (consteval.NotConstant, IndexError):
                pass
    last_line = program.statements[-1].line if program.statements else 1
    for seq_no, scan_no, interval, has_task in slows:
        for main_no, main_interval in enumerate(mains, 1):
            if has_task and interval is not None and main_interval is not None and interval <= main_interval:
                diags.warning(last_line, f"Slow Seq {seq_no}, Scan {scan_no}, may skip scans if "
                                         f"running with Scan {main_no}.")


def _scan_interval_ms(stmt: S.Instruction, env: consteval.Env) -> float | None:
    """The `Scan` interval in milliseconds as the compiler holds it (100 us units from the 32-bit
    seconds/microseconds of `instr_rules.scan_split`, so a wrapped `Scan(2147483647,Min,..)` is
    negative and counts as shorter than any threshold)."""
    split = instr_rules.scan_split(stmt, env)
    if split is None:
        return None
    units = split[0] * 10000 + (abs(split[1]) // 100) * (1 if split[1] >= 0 else -1)
    units &= 0xFFFFFFFF
    return (units - 2**32 if units >= 2**31 else units) / 10


def _check_fnotch(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`Invalid fnotch value, must be 50,60 400 or 4000` (CR300/CR350 only, no trailing period --
    verbatim oracle text, `docs/instructions.md`) -- every CR300/CR350 instruction with a `fN1`
    filter-frequency argument (found exhaustively: every instruction whose `data/<logger>/
    instructions.json` `params` contains type code 20, `checker/warnings.py`'s sibling
    `docs/instructions.md` cross-checks each one's argument position against its own CHM `Syntax`
    line) restricts it to exactly `{50, 60, 400, 4000}`; CR6/CR1000X accept any value in a
    continuous range instead (oracle: `PanelTemp(...,15000)` compiles clean on both, `15000` is
    even the CHM's own example value) so `profile.fnotch` is empty there and this is a no-op.
    Fires even when the file has unrelated errors elsewhere (oracle: `docs/instructions.md`), so
    (like `_check_reachability`/`_check_background_calibration`) this is called ungated.
    """
    data = profile.fnotch
    if not data or "valid" not in data:
        return  # CR6/CR1000X carry a minimum-frequency error instead (`instr_measure.check_pass1_values`)
    valid = set(data["valid"])
    message = data["message"]
    positions: dict[str, int] = data["instructions"]

    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        pos = positions.get(stmt.keyword.upper())
        if pos is None:
            continue
        idx = pos - 1
        if idx >= len(stmt.args) or stmt.args[idx] is None:
            continue
        arg = stmt.args[idx]
        if not consteval.is_constant(arg, env):
            continue  # a variable here is a different, unrelated check's concern (not ours).
        try:
            value = consteval.value_of(arg, env)
        except consteval.NotConstant:
            continue
        if value not in valid:
            diags.warning(stmt.line, message, stmt.file)


_TRUNCATE_CALLS = {"IFTIME": 3, "TIMEINTOINTERVAL": 3}
_TRUNCATE_CALL_RE = re.compile(r"(?i)\b(IfTime|TimeIntoInterval)\s*\(")
_ARITY_ERROR_PREFIXES = ("Expression expects , :", "No Matching, or out of place )", "Invalid, or out of place expression:")


def _call_args(text: str, start: int) -> list[str] | None:
    """The top-level argument texts of the call whose `(` ends just before `start`."""
    depth, args, cur, i = 1, [], "", start
    while i < len(text):
        ch = text[i]
        if ch == '"':
            j = text.find('"', i + 1)
            if j < 0:
                return None
            cur += text[i:j + 1]
            i = j + 1
            continue
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
            if depth == 0:
                args.append(cur)
                return [x for x in args] if any(x.strip() for x in args) else []
        elif ch == "," and depth == 1:
            args.append(cur)
            cur = ""
            i += 1
            continue
        cur += ch
        i += 1
    return None


def add_truncate_warnings(program: S.Program, profile, diags: Diagnostics) -> None:
    """All four loggers (oracle, 2026-10-12, `docs/checker.md` "Round 14 truncate warnings"): an
    `IfTime`/`TimeIntoInterval` call that fails its argument count prints `Warning: Parameter will
    truncate to the nearest integer.` once per missing argument and once per argument that holds a
    variable, at its line before the arity error (`Expression expects , :`, `No Matching, or out of
    place )`, or the empty-call `Invalid, or out of place expression:`). Not modelled: the same warnings
    of a valid call with a variable argument (printed twice, in both passes)."""
    variables = symbols.declared_variable_names(program)
    for stmt in program.statements:
        text = getattr(stmt, "text", None) or ""
        m = _TRUNCATE_CALL_RE.search(text)
        if not m:
            continue
        args = _call_args(text, m.end())
        if args is None:
            continue
        need = _TRUNCATE_CALLS[m.group(1).upper()]
        if len(args) == need:
            continue
        with_vars = sum(1 for a in args if any(w.upper() in variables for w in re.findall(r"[A-Za-z_]\w*", re.sub(r'"[^"]*"', "", a))))
        count = max(need - len(args), 0) + with_vars
        key = (stmt.file, stmt.line)
        at = next((j for j, d in enumerate(diags.items) if (d.file, d.line) == key and d.severity == "error"
                   and d.message.startswith(_ARITY_ERROR_PREFIXES) and m.group(1).lower() in d.message.lower()), None)
        if at is None or not count:
            continue
        diags.items[at:at] = [Diagnostic("warning", stmt.line, "Parameter will truncate to the nearest integer.", stmt.file)
                              for _ in range(count)]
