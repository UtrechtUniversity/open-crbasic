"""Instruction-specific checks that are not channel/port related (that logic lives in
`checker/instructions.py`, a different workstream, `PLAN.md`). A required argument position with
no corresponding value at all (`check`, pass-1, gated normally through `checker/__init__.py`), plus
a small set of *ungated* `Scan`/`DataInterval` numeric-value checks (`check_scan_params`,
`check_data_interval`, called from `checker/warnings.py`'s `check_early` -- oracle-confirmed to
fire even on an otherwise-failed compile, same as that module's own ungated warnings; see its
module docstring). Rules and oracle evidence: `docs/checker.md` (the required-parameter check) and
`docs/instructions.md` (the numeric-value checks).
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from .expressions import _type_code_kinds

# `type_codes.json` codes exempt from the "missing required parameter" error: when the
# statement's own physical line runs out of tokens right at one of these, `FUN_004baf00` silently
# fills it (and everything declared after it) with a default/sentinel value and ends that
# statement's own parameter parsing with no diagnostic at all, rather than reporting anything --
# `docs/type-codes.md` code 4's row ("this code (and 16, 17, 36, 37, 45, 46) is exempt from the
# loop-level `Too few parameters in instruction.` check"); this is what lets a bare `Do`/`Loop`/
# `Else` (code 4, no trailing `While`/`Until` condition), bare `End`/`Exit` (16), and bare `Next`
# (17) all compile with zero declared arguments even though their own table row lists the code as
# non-optional (code < 100). 36/45/46 are listed here too for documentation fidelity to that
# passage, but never reach this check in practice: `_real_params` below already drops them (they
# are `none`-kind, not real argument slots at all).
_TOLERANT_CODES = frozenset({4, 16, 17, 36, 37, 45, 46})


def _real_params(params: list[int]) -> list[int]:
    """`params` with every `none`-kind code removed (14 `EndSub`/`EndFunction`, 32
    `RainFlowSample`'s first slot, 36 `EndSelect`, 46 `EndStructureType`/`EndObjectType`,
    `docs/type-codes.md`) -- these are structural markers in the raw per-instruction table that
    consume no user-written token at all, so counting them as "required" would flag a legitimately
    argument-less statement (`EndSub` alone), and leaving one in the list at all would shift every
    later position out of alignment with the parsed `stmt.args` (found as a regression while
    developing this check: bare `EndSub`, `params=[14]`, was flagged as one required argument
    short; `RainFlowSample`'s real first user argument is its own table position 1, code 11, after
    the position-0 no-op marker, code 32)."""
    kinds = _type_code_kinds()
    return [c for c in params if kinds.get(c - 100 if c >= 100 else c) != "none"]


def _required_count(params: list[int]) -> int:
    """How many leading `params` entries are non-optional (code < 100). `FUN_004baf00`'s own
    rule -- "All arguments after the first optional parameter must be optional." -- means the
    first optional (code >= 100) entry marks where the required prefix ends; the CHM param tables
    never observed one out of order, and `data/instructions.json` is table-driven so this holds by
    construction."""
    for i, code in enumerate(params):
        if code >= 100:
            return i
    return len(params)


def check(program: S.Program, profile, diags: Diagnostics) -> None:
    """Flags an `Instruction` statement whose declared required-parameter prefix runs past the end
    of the parsed argument list -- almost always a CHM corpus example whose instruction call was
    wrapped across two physical lines with no `_` continuation (`docs/checker.md`: `Average` alone
    on one line, `(1,TCTemp,FP2,False)` on the next -- our own tolerant "auto-close an unclosed `(`
    at end of line" parsing, `docs/frontend.md`, silently produces a *short* argument list rather
    than a syntax error, unlike the real compiler, which reports the gap and abandons the rest of
    that statement's own parsing there).

    Deliberately checks only a *shortfall in count* (`len(stmt.args) < required`), never a `None`
    gap from a skipped comma in the middle of the list -- no oracle evidence this session found
    supports flagging that shape, and AGENTS.md's priority (never reject an accepted program) rules
    out guessing.

    This is pass-1 work (`checker/__init__.py`'s docstring): it fires regardless of what other
    errors the file already has. It does *not* need to separately suppress
    `checker/instructions.py`'s own per-instruction checks (channel ranges etc.) for the statement
    it flags -- `checker/__init__.py` already gates all of pass 2 behind "no pass-1 error anywhere
    in the file" (oracle evidence there), which covers this case for free.
    """
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        params = _real_params(record["params"])
        required = _required_count(params)
        if len(stmt.args) >= required:
            continue
        missing_code = params[len(stmt.args)]
        if missing_code in _TOLERANT_CODES:
            continue
        diags.error(stmt.line, "Invalid or missing parameter.", stmt.file)


# --- Ungated `Scan`/`DataInterval` numeric-value checks (called from `checker/warnings.py`'s
# `check_early`; see this module's docstring and `docs/instructions.md` for the oracle evidence
# that both fire even when the file has other errors elsewhere) ------------------------------

# CR300/CR350 do not perform the `Scan`/`DataInterval` non-negative-value validation at all --
# oracle-confirmed a genuine per-logger difference, not a shared check with a different message
# (`docs/instructions.md`): `Scan("fuzz",Sec,3,0)`, `Scan(-1,Sec,3,0)` and `DataInterval(0,-3,
# Sec,0)` all compile clean on `cr300comp.exe` (found as a **regression** during development --
# the corpus's own `hex_2.CR6`, `Scan(1,Sec,3,-1)`, is real CR300/CR350-accepted code, a false
# reject once this went live logger-unconditionally). The `DataTable`-interval-vs-`Scan`-interval
# divisibility check (`check_data_interval`'s other half) is *not* gated this way -- oracle-
# confirmed identical, message and all, on `cr300comp.exe` too.
_SCAN_VALUE_CHECK_LOGGERS = frozenset({"cr6", "cr1000x"})


def _is_negative_or_invalid(value: object) -> bool:
    """`True` when a folded constant `value` is what the real compiler's shared `Scan`/
    `DataInterval` parameter validator (`FUN_00468900`/`FUN_004f11d0`) rejects: a non-numeric
    constant (a string, oracle: `Scan("fuzz",Sec,1,0)` -> `Scan Interval parameter must be a
    non-negative integer`), or a number whose value truncated toward zero is negative. Python's
    `int()` on a float already truncates toward zero, matching the oracle's own boundary exactly:
    `-0.5` (`int(-0.5) == 0`) passes this check and is accepted (`docs/instructions.md`), `-1`
    fails it. An unrecognised constant type (never observed; `consteval.value_of` only ever
    returns `int`/`float`/`str`) is deliberately treated as *not* negative -- AGENTS.md: never
    guess into a false rejection.
    """
    if isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return int(value) < 0
    return False


def check_scan_params(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`Scan Interval/BufferOption/Count parameter must be a non-negative integer` (no trailing
    period -- verbatim oracle/`docs/messages.tsv` text, function `FUN_00468900`) for `Scan`'s 1st,
    3rd and 4th arguments respectively (`Scan(Interval,Units,BufferOption,Count)`, `docs/
    instructions.md`). Only checked when the argument is already a compile-time constant (a
    variable there is a different check's concern, matching `checker/terminals.py`'s own
    convention); this is deliberately narrower than the real compiler's full `Scan` validation
    (minimum-interval, maximum-interval and multiple-of-a-logger-granularity checks all exist too,
    oracle-confirmed present but with boundaries this project could not pin down precisely enough
    to implement without risking a false reject on a borderline-valid interval -- `docs/
    instructions.md` "Known gaps"). This closes the fuzz-found crash in `tables.py`'s
    `_scan_weights` (a non-numeric `Scan` interval, `docs/fuzz-findings.md` "2. Crash") with a real
    diagnostic instead of an unhandled exception, and the corpus's own `hex_2.CR6`
    (`Scan Count parameter must be a non-negative integer`, `docs/checker.md` "Known remaining
    gaps"). CR6/CR1000X only -- see `_SCAN_VALUE_CHECK_LOGGERS`.
    """
    if profile.name not in _SCAN_VALUE_CHECK_LOGGERS:
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() != "SCAN":
            continue
        for idx, label in ((0, "Interval"), (2, "BufferOption"), (3, "Count")):
            if idx >= len(stmt.args) or stmt.args[idx] is None:
                continue
            arg = stmt.args[idx]
            if not consteval.is_constant(arg, env):
                continue
            try:
                value = consteval.value_of(arg, env)
            except consteval.NotConstant:
                continue
            if _is_negative_or_invalid(value):
                diags.error(stmt.line, f"Scan {label} parameter must be a non-negative integer", stmt.file)


# `Scan`/`DataInterval`'s `Units` argument folds (via `consteval`, `profile.symbols`) to this 0..5
# code (`data/symbols.json`: `usec`=0 .. `day`=5); microseconds per unit.
_US_PER_UNIT = {0: 1, 1: 1_000, 2: 1_000_000, 3: 60_000_000, 4: 3_600_000_000, 5: 86_400_000_000}


def _interval_us(interval_arg: S.Expr | None, units_arg: S.Expr | None, env: consteval.Env) -> float | None:
    if interval_arg is None or units_arg is None:
        return None
    if not (consteval.is_constant(interval_arg, env) and consteval.is_constant(units_arg, env)):
        return None
    try:
        value = consteval.value_of(interval_arg, env)
        unit = consteval.value_of(units_arg, env)
    except consteval.NotConstant:
        return None
    us_per_unit = _US_PER_UNIT.get(unit)
    if us_per_unit is None or not isinstance(value, (int, float)) or value < 0:
        return None
    return value * us_per_unit


def check_data_interval(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """Two `DataInterval` checks, both oracle-confirmed ungated (`docs/instructions.md`):

    - `DataInterval TintoInt and Interval parameters must be non-negative` (function
      `FUN_004f11d0`, verbatim, no trailing period) when either of `DataInterval`'s first two
      arguments (`TintoInt`, `Interval`) is a negative compile-time-constant number, reported at
      the `DataInterval` statement's own line. CR6/CR1000X only -- oracle-confirmed a genuine
      per-logger difference, not shared with CR300/CR350 (`_SCAN_VALUE_CHECK_LOGGERS`; found the
      same way as `check_scan_params`'s own CR300/CR350 gate, same session).
    - `DataTable interval %d uSec not integrally divisible by scan interval %d uSec.` (function
      `FUN_004f6540`) -- project knowledge (`knowledge/crbasic/lessons.md` "`DataInterval` versus
      `Scan`": "a table interval must be at least the scan interval and an exact integer multiple
      of it", human-verified against `CR6Comp.exe`), reported at each `CallTable <name>` site whose
      table has a `DataInterval` and which sits directly inside a `Scan...NextScan` block (main
      sequence or `SlowSequence`, both accepted per the CHM) with its own resolvable constant
      interval, when the two aren't an exact multiple. `Interval = 0` (CHM: "the data storage
      interval is the scan interval") trivially always divides evenly, so it needs no special case.

      **Deliberately conservative** (AGENTS.md: never falsely reject a program the real compiler
      accepts) -- this only tracks the *textually innermost* `Scan`/`SlowSequence` `Scan` a
      `CallTable` sits inside (no depth stack beyond "currently open or not": `Scan` never nests
      with itself, only `SubScan`, which uses distinct `SUBSCAN`/`NEXTSCAN` -- wait, distinct
      `SUBSCAN`/`NEXTSUBSCAN` keywords, so it never resets this tracker). It does **not** attempt:
      a table called indirectly through a `Sub` (the call site's enclosing `Scan` is not visible at
      the `Sub`'s own definition), a table called from more than one differently-interval'd `Scan`
      (each call site is still checked independently against whatever `Scan` textually contains
      it, which is correct for the common case but under-covers a table called from *outside* any
      `Scan` at all, e.g. via `AcceptDataRecords` -- silently skipped), or the CHM's documented
      `GetDataRecord`-destination-table exception (not implemented; a false reject there is
      possible but not found in this project's corpus/fuzz evidence, `docs/instructions.md`).
    """
    declared_interval_us: dict[str, tuple[float, int]] = {}  # table name -> (interval_us, line)
    current_table: str | None = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "DATATABLE":
            current_table = stmt.args[0].name.upper() if stmt.args and isinstance(stmt.args[0], S.Name) else None
            continue
        if kw == "ENDTABLE":
            current_table = None
            continue
        if kw != "DATAINTERVAL" or not current_table:
            continue
        args = stmt.args
        tinto = args[0] if len(args) > 0 else None
        interval = args[1] if len(args) > 1 else None
        units = args[2] if len(args) > 2 else None
        for arg in (tinto, interval) if profile.name in _SCAN_VALUE_CHECK_LOGGERS else ():
            if arg is None or not consteval.is_constant(arg, env):
                continue
            try:
                value = consteval.value_of(arg, env)
            except consteval.NotConstant:
                continue
            if isinstance(value, (int, float)) and value < 0:
                diags.error(stmt.line, "DataInterval TintoInt and Interval parameters must be non-negative", stmt.file)
                break
        interval_us = _interval_us(interval, units, env)
        if interval_us is not None:
            declared_interval_us[current_table] = (interval_us, stmt.line)

    scan_interval_us: float | None = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "SCAN":
            args = stmt.args
            scan_interval_us = _interval_us(
                args[0] if args else None, args[1] if len(args) > 1 else None, env
            )
            continue
        if kw == "NEXTSCAN":
            scan_interval_us = None
            continue
        if kw != "CALLTABLE" or not scan_interval_us or not stmt.args:
            continue
        name_arg = stmt.args[0]
        if not isinstance(name_arg, S.Name):
            continue
        entry = declared_interval_us.get(name_arg.name.upper())
        if entry is None:
            continue
        dt_us, _ = entry
        if dt_us and int(dt_us) % int(scan_interval_us) != 0:
            diags.error(
                stmt.line,
                f"DataTable interval {int(dt_us)} uSec not integrally divisible by "
                f"scan interval {int(scan_interval_us)} uSec.",
                stmt.file,
            )
