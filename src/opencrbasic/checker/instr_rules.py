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


# --- Too many parameters: `Too many parameters:  %s.` (round 5, new this round) -----------------

# `docs/type-codes.md` `kind`s eligible for `check_too_many` below: every `kind` whose own argument
# is always exactly one ordinary, comma-delimited token compiled through the generic per-code
# expression/constant/keyword resolver -- the same shared assumption `expressions.py`'s
# `_RESOLVED_KINDS` makes for a *different* purpose (name resolution), deliberately narrower here.
# **Not** included, each for its own oracle/corpus-evidenced reason a corpus safety scan (`docs/
# checker.md` "Round 5") found necessary:
#
# - `identifier` (6), `deferred_text` (11), `raw_line_text` (18), `expr_marker` (37), `type_name`
#   (45): raw-text-capturing codes whose own instruction (`Units`, `ConstTable`, `MenuPick`, ...)
#   may swallow embedded commas as part of *one* declared slot's own text (`Units X = Volts` is one
#   code-6 argument to the real compiler, never comma-split) -- comparing this checker's own,
#   always-comma-split `stmt.args` length against the declared code count for these would flag
#   real, oracle-accepted programs as having "too many" parameters purely from this checker's own
#   parsing convention, not a real excess (found as 631 `Alias`/534 `Units`/35 `MenuPick`/17
#   `ConstTable` false hits on this project's own *accepted* `crbasic/`+`chm` corpus before this
#   exclusion was added).
# - `sub_params` (12/35), `for_header` (3), `expr_list` (13): declaration/loop-header/call-argument
#   grammars with their own, already-implemented parsing (`Sub`/`Function`/`FOR`/`Call`), never
#   reached via a plain `S.Instruction`'s `args` list at all (`Call` parses to `S.Call`, `Sub`/
#   `Function` to `S.SubDef`, `FOR` to `S.For` -- `checker/structure.py`'s own AST node types), so
#   excluding them here is defensive rather than corpus-evidenced.
_TOO_MANY_SAFE_KINDS = frozenset({
    "const_expr", "expr", "const_expr_only", "bool_expr", "variable_or_array", "expr_or_name",
    "expr_typed", "expr_special", "expr_pair", "datatype_kw", "const_int_kw", "next_kw",
    "end_exit_kw",
})

# `FUN_004baf00`'s own "end of a simple token" delimiter set (`docs/type-codes.md` section 1,
# `FUN_004ba440`): comma, colon, both parens, space, `=`, newline, tab, `>`. The "too many
# parameters" message's own echo uses *this* flat tokenizer, not the depth-aware one a normal
# argument boundary is parsed with (`FUN_004b9700`) -- oracle-confirmed, `docs/checker.md` "Round
# 5": an extra, fully-parenthesised argument (`(1+2)`) echoes as `1+2`, its own leading `(`
# consumed as a delimiter (the *skip-leading-delimiters* step) and its first inner `)` ending the
# token, the nested nesting being coincidental rather than honoured; `Battery(X,X,X)` (two bare
# extra `X`s) echoes only the *first* one, `X` -- confirming the echo is a single next-token
# lookup, not the whole remaining comma list, matching `docs/type-codes.md`'s own worked example.
_TOO_MANY_DELIMS = frozenset(",:() =\n\t>")


def _next_simple_token(text: str) -> str:
    i, n = 0, len(text)
    while i < n and text[i] in _TOO_MANY_DELIMS:
        i += 1
    start = i
    while i < n and text[i] not in _TOO_MANY_DELIMS:
        i += 1
    return text[start:i]


def _too_many_eligible(params: list[int]) -> bool:
    """`params` (the raw, un-filtered declared code list) is eligible for `check_too_many` only
    when it is non-empty and every code's `kind` is either `_TOO_MANY_SAFE_KINDS` or `none`
    (`_real_params` already strips `none`-kind codes for the actual count, but a `none` code does
    not itself disqualify the surrounding instruction). An **empty** `params` list (`Alias`,
    `Units`, `Data`, `EssVariables`, oracle/corpus-evidenced above) is never eligible: it means this
    project's own extracted table has no per-code model for the instruction's arguments at all --
    real, custom parsing the generic per-code loop this check models does not apply to.
    """
    if not params:
        return False
    kinds = _type_code_kinds()
    for code in params:
        base = code - 100 if code >= 100 else code
        kind = kinds.get(base)
        if kind != "none" and kind not in _TOO_MANY_SAFE_KINDS:
            return False
    return True


def check_too_many(program: S.Program, profile, diags: Diagnostics) -> None:
    """`Too many parameters:  %s.` (note the two spaces before `%s`, oracle-confirmed verbatim,
    `docs/type-codes.md`) when an `Instruction` statement's parsed argument list runs *past* its
    declared parameter count -- the mirror image of `check` above (a shortfall), and, like it, pass
    1 (unconditional -- oracle-confirmed this round, `docs/checker.md` "Round 5": fires alongside an
    earlier, unrelated `Variable not named.` in the same file, the same "ungated" shape `check`
    already has). Deliberately restricted to instructions whose declared codes are *all*
    `_too_many_eligible` -- see that function and `_TOO_MANY_SAFE_KINDS` for the corpus-evidenced
    reasoning; this is the check's actual safety margin, not the echo logic below.

    The echoed text is the *next* token by the real compiler's own flat, non-depth-aware tokenizer
    (`_next_simple_token`) starting at the first excess argument's own raw source position
    (`Instruction.arg_spans`) -- not a re-derived or whole-remaining-text form. A missing span
    (an empty/skipped argument slot with no captured position) is left unflagged rather than
    guessed at.
    """
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record or not _too_many_eligible(record["params"]):
            continue
        max_count = len(_real_params(record["params"]))
        if len(stmt.args) <= max_count:
            continue
        spans = getattr(stmt, "arg_spans", None)
        if not spans or max_count >= len(spans) or spans[max_count] is None:
            continue
        start = spans[max_count][0]
        token = _next_simple_token(stmt.text[start:])
        if not token:
            continue
        diags.error(stmt.line, f"Too many parameters:  {token}.", stmt.file)


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
    non-negative integer`), a number whose value truncated toward zero is negative, or one that
    overflows a signed 32-bit int. Python's `int()` on a float already truncates toward zero,
    matching the oracle's own boundary exactly: `-0.5` (`int(-0.5) == 0`) passes this check and is
    accepted (`docs/instructions.md`), `-1` fails it. **Round 5 addition, oracle-confirmed**
    (`docs/checker.md` "Round 5", a fuzz-found false accept): the same message also covers a
    positive value too large to fit `INT32_MAX` (`2147483647`) -- `Scan(1,Sec,3,2147483647)`
    compiles, `Scan(1,Sec,3,2147483648)` fails with this exact text, boundary-swept on both `Count`
    and `Interval`; `DataInterval`'s own sibling check (`check_data_interval` below, same shared
    predicate) shares the identical boundary (`DataInterval(2147483648,10,Sec,10)` ->
    `DataInterval TintoInt and Interval parameters must be non-negative`). An unrecognised constant
    type (never observed; `consteval.value_of` only ever returns `int`/`float`/`str`) is
    deliberately treated as valid -- AGENTS.md: never guess into a false rejection.
    """
    if isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return int(value) < 0 or int(value) > 2147483647
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


def _divisible_interval_us(interval_arg: S.Expr | None, units_arg: S.Expr | None,
                            env: consteval.Env) -> float | None:
    """`_interval_us`, but `None` (never trackable for the divisibility check below) when the
    interval's own raw declared value is not a whole number -- oracle-confirmed this round
    (`docs/checker.md` "Round 5", a fuzz-found false reject): a fractional `Interval` on *either*
    side (`DataInterval`'s own or the enclosing `Scan`'s) silences `DataTable interval ... not
    integrally divisible ...` entirely, at every fractional value swept (`1.1`..`2.9` in `0.1`
    steps against a `1`-second `Scan`, and a `1.5`-second `Scan` against a `10`-second
    `DataInterval`) -- `DataInterval(0,1.5,Sec,10)` inside `Scan(1,Sec,...)` compiles clean on
    `CR6Comp.exe` despite `1.5` not being an integer multiple of `1`, while `DataInterval(0,5,...)`
    (an integer) against the same `2`-second `Scan` still correctly fires (`5000000 uSec not
    integrally divisible by 2000000 uSec.`) -- so this is not "the check never fires for a
    non-integer `Scan`/`DataInterval` combination", only "a *fractional-valued* interval argument
    on either side is exempt". A whole-number **float** literal (`5.0`) still counts as an integer
    here and the check still fires for it (oracle-confirmed, `5.0` against a `2`-second `Scan`) --
    this is about the folded *value*, not whether the source token has a decimal point.
    """
    us = _interval_us(interval_arg, units_arg, env)
    if us is None:
        return None
    try:
        value = consteval.value_of(interval_arg, env)
    except consteval.NotConstant:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        return None
    return us


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
            if _is_negative_or_invalid(value):
                diags.error(stmt.line, "DataInterval TintoInt and Interval parameters must be non-negative", stmt.file)
                break
        interval_us = _divisible_interval_us(interval, units, env)
        if interval_us is not None:
            declared_interval_us[current_table] = (interval_us, stmt.line)

    scan_interval_us: float | None = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "SCAN":
            args = stmt.args
            scan_interval_us = _divisible_interval_us(
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
