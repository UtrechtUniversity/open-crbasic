"""Warnings: post-table "declared but never called" (needs the whole post-`#If`-resolution
statement list, `tables_hook`-gated, pass 2) plus a small set of *ungated* warnings that the real
compiler emits regardless of whether the file has other errors (oracle-confirmed per warning
below, and generally in `docs/instructions.md`'s "Two of these run even on a failed compile"
section) -- reachability ("never reached at run time"), the CR6/CR1000X fast-scan
background-calibration note, and the CR300/CR350 fixed-set `fnotch` value check. Rules and oracle
evidence: `docs/instructions.md`; the pre-existing table-never-called rule's own evidence stays in
`docs/checker.md`/`docs/tdf-rules.md` section 13.
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile
from . import instr_rules


def _table_arg_name(stmt: S.Instruction) -> str | None:
    if stmt.args and isinstance(stmt.args[0], S.Name):
        return stmt.args[0].name
    return None


def check(program: S.Program, result, diags: Diagnostics) -> None:
    # User `DataTable(...)...EndTable` blocks only -- deliberately *not* `result.tables` (which
    # also carries the logger's own system tables, `Status`/`DataTableInfo`/`GlobalConfigTable`/
    # the `Public` table, none of which are ever `CallTable`d and must not get this warning).
    declared: dict[str, str] = {}
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "DATATABLE":
            name = _table_arg_name(stmt)
            if name:
                declared[name.upper()] = name

    # "Called" is deliberately not just `CallTable`: `AcceptDataRecords`' own TableName argument
    # also counts (oracle-confirmed regression, `acceptdatarecords_1.CR6`: `AcceptDataRecords
    # (3000,32769,CR1000XTable)` with no `CallTable` at all still gets no warning). Rather than
    # enumerate every instruction with a table-name argument, any bare-name argument anywhere
    # that matches a declared table counts as a use -- a table name is not reused as an ordinary
    # variable, so this is a safe superset of the real "called" set.
    called = {
        arg.name.upper()
        for stmt in program.statements
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() != "DATATABLE"
        for arg in stmt.args
        if isinstance(arg, S.Name) and arg.name.upper() in declared
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


def check_early(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    _check_reachability(program, profile, diags, env)
    _check_fnotch(program, profile, diags, env)
    # Numeric `Scan`/`DataInterval` value checks: also ungated (oracle-confirmed,
    # `docs/instructions.md`), implemented in `instr_rules.py` alongside this module's other
    # required-parameter check since they are instruction-argument-shaped, not warnings.
    instr_rules.check_scan_params(program, profile, diags, env)
    instr_rules.check_scan_min_interval(program, profile, diags, env)
    instr_rules.check_scan_max_interval(program, profile, diags, env)
    instr_rules.check_data_interval(program, profile, diags, env)


def _kw(stmt: S.Stmt) -> str:
    if isinstance(stmt, S.Instruction):
        return stmt.keyword.upper()
    if isinstance(stmt, S.SubDef):
        return stmt.keyword.upper()  # SUB / FUNCTION
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
})


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
        elif kw == "EXITSCAN":
            has_exit = True
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
    statements = program.statements
    n = len(statements)
    dead = False
    open_depth = 0
    sub_depth = 0
    i = 0
    while i < n:
        stmt = statements[i]
        kw = _kw(stmt)

        if sub_depth > 0:
            if kw in ("SUB", "FUNCTION"):
                sub_depth += 1
            elif kw in ("ENDSUB", "ENDFUNCTION"):
                sub_depth -= 1
            i += 1
            continue
        if kw in ("SUB", "FUNCTION"):
            sub_depth = 1
            i += 1
            continue

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

        if kw == "SCAN":
            if dead:
                diags.warning(stmt.line, f"{_display_name(stmt, profile)} never reached at run time.", stmt.file)
                return
            j, has_exit = _scan_span(statements, i)
            count_is_zero = _scan_count_is_zero(stmt, env)
            dead = bool(count_is_zero) and not has_exit
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
            )
            if not is_declaration:
                diags.warning(stmt.line, f"{_display_name(stmt, profile)} never reached at run time.", stmt.file)
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

    in_slow = False
    main_scan_index = 0
    for stmt in statements:
        kw = _kw(stmt)
        if kw == "SLOWSEQUENCE":
            in_slow = True
            continue
        if kw != "SCAN" or in_slow:
            continue
        main_scan_index += 1
        interval_ms = _scan_interval_ms(stmt, env)
        if interval_ms is not None and interval_ms < threshold_ms:
            diags.warning(
                last_line,
                f"When Main Sequence Scan {main_scan_index} is running, "
                "background calibration will be disabled.",
            )


# `Scan`'s `Units` argument folds (via `consteval`, `profile.symbols`) to this 0..5 code
# (`data/symbols.json`: `usec`=0 .. `day`=5); milliseconds per unit, used only to test against
# `background_calibration.json`'s whole-millisecond threshold.
_MS_PER_UNIT = {0: 0.001, 1: 1, 2: 1000, 3: 60000, 4: 3_600_000, 5: 86_400_000}


def _scan_interval_ms(stmt: S.Instruction, env: consteval.Env) -> float | None:
    args = stmt.args
    if len(args) < 2 or args[0] is None or args[1] is None:
        return None
    try:
        value = consteval.value_of(args[0], env)
        unit = consteval.value_of(args[1], env)
    except consteval.NotConstant:
        return None
    ms_per_unit = _MS_PER_UNIT.get(unit)
    if ms_per_unit is None or not isinstance(value, (int, float)):
        return None
    return value * ms_per_unit


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
    if not data:
        return
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
