"""Instruction-specific measurement/I-O semantic checks that are not the channel/port *terminal*
range model (`terminals.py`, `data/<logger>/terminals.json`). Value-set and value-range checks for
`Range` codes, `SW12` ports, `Delay`, `CS616`, `TimerInput` and `ExciteV`/`ExciteI`, per logger,
driven by `data/<logger>/measure.json`. Pass 2 (`docs/checker.md` "Two-pass compilation"): called
from `checker/__init__.py` alongside `instructions.check`, inside the `if not diags.errors:` gate,
so (matching every existing pass-2 check) it never fires when the file has an earlier, unrelated
error. All rules below are oracle-confirmed per logger this round; see `docs/instructions.md`
"Measurement/I-O instruction checks" for the evidence and each logger's coverage.
"""

from __future__ import annotations

import re

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from ..profiles import Profile


def check(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    measure = profile.measure
    if not measure:
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw in measure.get("range_code_instructions", {}):
            _check_range_code(stmt, measure, measure["range_code_instructions"][kw], env, diags)
        if kw == "SW12" and "sw12_ports" in measure:
            _check_sw12(stmt, measure, env, diags)
        if kw == "DELAY" and "delay" in measure:
            _check_delay(stmt, measure["delay"], env, diags)
        if kw == "CS616" and "cs616_min_reps_pos" in measure:
            _check_cs616_reps(stmt, measure, env, diags)
        if kw in ("EXCITEV", "EXCITEI") and "excite_max_mv" in measure:
            _check_excite_magnitude(stmt, measure, env, diags)


def _const_value(arg: S.Expr | None, env: consteval.Env):
    """The folded constant value of `arg`, or `None` if it is missing, a variable, or otherwise
    not resolvable at compile time (in every case, some *other* check's concern, not this one's --
    matching `terminals.py`'s own convention)."""
    if arg is None or not consteval.is_constant(arg, env):
        return None
    try:
        return consteval.value_of(arg, env)
    except consteval.NotConstant:
        return None


def _int_value(arg: S.Expr | None, env: consteval.Env) -> int | None:
    v = _const_value(arg, env)
    return int(v) if isinstance(v, (int, float)) else None


# --- Range codes: `Invalid range code, valid options are: mV5000, mV1000, mV200, or AutoRange.`
# (CR6/CR1000X only; CR300/CR350 have no such check -- their own `mv5000`/`mv200` etc. are simply
# not symbols at all, an unrelated, already-handled undefined-symbol rejection, `docs/
# instructions.md`). Fires for `VoltSe`/`VoltDiff`/`TCSe`/`TCDiff`/`CurrentSe`'s own `Range`
# argument (position 3 on all five) when the folded value is not one of the CHM's documented
# "mV5000/mV1000/mV200/AutoRange" family, including their open-detection `...C` siblings.
def _check_range_code(stmt: S.Instruction, measure: dict, pos: int, env: consteval.Env, diags: Diagnostics) -> None:
    idx = pos - 1
    if idx >= len(stmt.args):
        return
    value = _const_value(stmt.args[idx], env)
    if value is None:
        return
    valid = measure["range_codes"]
    if int(value) not in valid:
        diags.error(stmt.line, measure["range_code_message"], stmt.file)


# --- `SW12`: `Invalid SW12 port %d.` -- an exact, small, per-logger set of valid ports (both a
# plain-integer slot number and the named `SW12_n` constant resolve to different raw values but
# both compile; every other value in the probed neighbourhood fails), `docs/instructions.md`.
def _check_sw12(stmt: S.Instruction, measure: dict, env: consteval.Env, diags: Diagnostics) -> None:
    if not stmt.args:
        return
    value = _int_value(stmt.args[0], env)
    if value is None:
        return
    if value not in measure["sw12_ports"]:
        diags.error(stmt.line, f"Invalid SW12 port {value}.", stmt.file)


# --- `Delay`: `Delay(Option, Interval, Units)`. Three checks, oracle-confirmed per logger
# (`docs/instructions.md`):
#   - `Illegal option %d` (all four loggers) when `Option` is not 0, 1 or 2 -- shown value is the
#     folded number truncated to an unsigned byte (oracle: `Option=-1` shows as `255`).
#   - `Delay interval cannot be of type String.` (all four loggers, any `Option`) when `Interval`
#     folds to a string constant.
#   - Depending on `Option` (0 = measurement-task-sequence delay, 1 = processing delay, 2 = SDM
#     delay): `Option=0` needs `Interval` to itself be a compile-time constant
#     (`Measurement delay must have constant interval.`) and, once resolved, at most 60 minutes
#     (`Measurement Delay Exceeds 60 min.`) -- both CR6/CR1000X only, oracle-confirmed absent on
#     CR300/CR350 (`Delay(0,61,min)` compiles clean there); `Option=1` needs the resolved interval
#     to be strictly less than 24 hours (`Max Delay is 24 Hours`, all four loggers); `Option=2`
#     needs `Interval` to be a compile-time constant (`SDM Delay interval must be constant.`, all
#     four loggers).
_DELAY_US_PER_UNIT = {0: 1, 1: 1_000, 2: 1_000_000, 3: 60_000_000, 4: 3_600_000_000, 5: 86_400_000_000}


def _check_delay(stmt: S.Instruction, cfg: dict, env: consteval.Env, diags: Diagnostics) -> None:
    args = stmt.args
    option_arg = args[0] if len(args) > 0 else None
    interval_arg = args[1] if len(args) > 1 else None
    units_arg = args[2] if len(args) > 2 else None

    option = _int_value(option_arg, env)
    if option is not None and option not in (0, 1, 2):
        diags.error(stmt.line, f"Illegal option {option % 256}", stmt.file)

    interval_value = _const_value(interval_arg, env)
    if isinstance(interval_value, str):
        diags.error(stmt.line, "Delay interval cannot be of type String.", stmt.file)
        return  # oracle: the string-type rejection is the only diagnostic for a string interval.

    if option == 0 and cfg.get("option0_limit"):
        if interval_arg is not None and not consteval.is_constant(interval_arg, env):
            diags.error(stmt.line, "Measurement delay must have constant interval.", stmt.file)
            return
        us = _delay_interval_us(interval_arg, units_arg, env)
        if us is not None and us > cfg["option0_limit"]:
            diags.error(stmt.line, "Measurement Delay Exceeds 60 min.", stmt.file)
    elif option == 1 and cfg.get("option1_limit"):
        us = _delay_interval_us(interval_arg, units_arg, env)
        if us is not None and us >= cfg["option1_limit"]:
            diags.error(stmt.line, "Max Delay is 24 Hours", stmt.file)
    elif option == 2 and cfg.get("option2_constant", True):
        if interval_arg is not None and not consteval.is_constant(interval_arg, env):
            diags.error(stmt.line, "SDM Delay interval must be constant.", stmt.file)


def _delay_interval_us(interval_arg, units_arg, env: consteval.Env) -> float | None:
    value = _const_value(interval_arg, env)
    unit = _const_value(units_arg, env)
    if not isinstance(value, (int, float)) or not isinstance(unit, (int, float)) or value < 0:
        return None
    us_per_unit = _DELAY_US_PER_UNIT.get(int(unit))
    return value * us_per_unit if us_per_unit is not None else None


# --- `CS616`: `Invalid reps %d, must be > 0` for a non-positive `Reps` (CR6 only, oracle-
# confirmed; not chased on CR1000X/CR300/CR350 this round, `docs/instructions.md`).
def _check_cs616_reps(stmt: S.Instruction, measure: dict, env: consteval.Env, diags: Diagnostics) -> None:
    pos = measure["cs616_min_reps_pos"]
    idx = pos - 1
    if idx >= len(stmt.args):
        return
    value = _int_value(stmt.args[idx], env)
    if value is not None and value <= 0:
        diags.error(stmt.line, f"Invalid reps {value}, must be > 0", stmt.file)


# --- `ExciteV`/`ExciteI`: `Excitation exceeds 2500.000000.` when `|ExmV|`/`|ExmI|` > 2500 (CR6
# only, oracle-confirmed; CR1000X accepts at least 2501 mV with no error, so this is deliberately
# not applied there -- `docs/instructions.md`). `ExciteI` does not exist on CR1000X/CR300/CR350 at
# all (`docs/loggers.md`), so this only ever matches on CR6 regardless.
def _check_excite_magnitude(stmt: S.Instruction, measure: dict, env: consteval.Env, diags: Diagnostics) -> None:
    pos = measure["excite_magnitude_pos"]
    idx = pos - 1
    if idx >= len(stmt.args):
        return
    value = _const_value(stmt.args[idx], env)
    if isinstance(value, (int, float)) and abs(value) > measure["excite_max_mv"]:
        diags.error(stmt.line, f"Excitation exceeds {float(measure['excite_max_mv']):.6f}.", stmt.file)


_UNIT_NAMES = {1: "mSec", 2: "Sec", 3: "Min"}
_OPENERS = frozenset({"SELECT", "WHILE"})
_CLOSERS = frozenset({"ENDIF", "ENDSELECT", "NEXTSUBSCAN", "WEND"})


def check_scan_delay_budget(program: S.Program, profile: Profile, diags: Diagnostics,
                            env: consteval.Env) -> None:
    """CR6/CR1000X: measurement delays (`Delay(0,...)`) in the main `Scan` that add up to its
    interval -> `Scan interval too short for measurement by %d %s.` at `NextScan`, in the scan's
    own unit, rounded up past the small per-scan overhead (oracle, 2026-10-01: `Scan(1,Sec)` with
    `Delay(0,1,Sec)` -> `by 1 Sec`, `Scan(50,mSec)` with `Delay(0,1,Sec)` -> `by 951 mSec`; a delay
    in `SubScan(0,0,5)` counts five times)."""
    if profile.name not in ("cr6", "cr1000x"):
        return
    from .instr_rules import _US_PER_UNIT
    for _scan, end, (value, unit), total in scan_delay_totals(program, env):
        interval = value * _US_PER_UNIT[unit]
        if interval > 0 and total >= interval:
            diags.error(end.line, f"Scan interval too short for measurement by "
                        f"{(total - interval) // _US_PER_UNIT[unit] + 1} {_UNIT_NAMES[unit]}.",
                        end.file)


def scan_delay_totals(program: S.Program, env: consteval.Env):
    """`(Scan, NextScan, (interval, unit), delay_us)` for each main-sequence `Scan` with a constant
    mSec/Sec/Min interval: the measurement delays (`Delay(0,...)`) it contains, repeated per
    `SubScan(0,0,n)` pass. Delays inside a loop, an `If` block or a `Select` are not counted, so
    the total is a lower bound of the real compiler's estimate."""
    from .instr_rules import _US_PER_UNIT, _scan_raw
    slow = False
    scan: S.Instruction | None = None
    raw: tuple[int, int] | None = None
    frames: list[int | None] = []  # per open block: SubScan repeat count, None when not counted
    total = 0
    for stmt in program.statements:
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        text = stmt.text or ""
        if kw == "NEXT" and re.match(r"(?i)\s*Next\s+Scan\b", text):
            kw = "NEXTSCAN"
        elif kw == "NEXT" and re.match(r"(?i)\s*Next\s+SubScan\b", text):
            kw = "NEXTSUBSCAN"
        if kw == "SLOWSEQUENCE":
            slow = True
        elif kw == "SCAN" and not slow:
            raw = _scan_raw(stmt, env)
            scan = stmt if raw and raw[1] in _UNIT_NAMES else None
            frames, total = [], 0
        elif scan is None:
            continue
        elif kw == "NEXTSCAN":
            if not frames:
                yield scan, stmt, raw, total
            scan = None
        elif kw == "SUBSCAN":
            args = [_value(a, env) for a in stmt.args]
            count = args[2] if len(args) == 3 and args[0] == 0 else None
            frames.append(count if isinstance(count, int) and not isinstance(count, bool)
                          and count > 0 else None)
        elif (isinstance(stmt, S.For) or (isinstance(stmt, S.DoLoop) and stmt.keyword == "DO")
              or (isinstance(stmt, S.If) and stmt.inline is None) or kw in _OPENERS):
            frames.append(None)
        elif (kw == "NEXT" or (isinstance(stmt, S.DoLoop) and stmt.keyword == "LOOP")
              or kw in _CLOSERS or (kw == "END" and re.match(r"(?i)\s*End\s+(If|Select)\b", text))):
            if not frames:
                scan = None
            else:
                frames.pop()
        elif kw == "DELAY" and None not in frames and len(stmt.args) == 3:
            option, value, unit = (_value(a, env) for a in stmt.args)
            if option == 0 and isinstance(value, (int, float)) and not isinstance(value, bool) \
                    and value > 0 and unit in _US_PER_UNIT and not isinstance(unit, bool):
                repeat = 1
                for count in frames:
                    repeat *= count
                total += int(value * _US_PER_UNIT[unit]) * repeat


def _value(expr: S.Expr | None, env: consteval.Env) -> object | None:
    if expr is None or not consteval.is_constant(expr, env):
        return None
    try:
        return consteval.value_of(expr, env)
    except consteval.NotConstant:
        return None


# `Reps` (the second argument) of measurement instructions, oracle-probed 2026-10-01 (the decompiled
# CR6 check `FUN_005163b0` is shared by 23 instructions; only the ones probed on a logger are
# listed). CR6/CR1000X: `Invalid reps %d, must be > 0` (no period) for Reps <= 0, and `Variable %s
# out of bounds.` when the Float destination has fewer elements from its start than Reps (any
# negative Reps). CR300/CR350: `Invalid reps 0, must be > 0.` for Reps 0 only.
_REPS_CHECKED = {
    "cr6": frozenset({"VOLTSE", "VOLTDIFF", "TCDIFF", "TCSE", "BRHALF"}),
    "cr1000x": frozenset({"VOLTSE", "VOLTDIFF", "TCDIFF", "TCSE", "CURRENTSE", "PERIODAVG",
                          "PULSECOUNT", "BRHALF"}),
    "cr300": frozenset({"THERM107", "THERM108", "THERM109", "PULSECOUNT"}),
    "cr350": frozenset({"THERM107", "THERM108", "THERM109", "PULSECOUNT"}),
}
_REPS_DEST_SIZE = frozenset({"VOLTSE", "VOLTDIFF", "TCDIFF", "TCSE", "CURRENTSE", "PERIODAVG",
                             "BRHALF"})  # positive overflow probed; PulseCount has its own message


def check_reps(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    checked = _REPS_CHECKED.get(profile.name, frozenset())
    if not checked:
        return
    from . import symbols
    dims = symbols.declared_all_dims(program, env)
    floats = {v.name.upper() for st in program.statements
              if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM")
              for v in st.vars if (v.type or "FLOAT").upper() == "FLOAT"}
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() not in checked or \
                len(stmt.args) < 2:
            continue
        reps = _int_value(stmt.args[1], env)
        if reps is None and profile.single_pass and isinstance(_const_value(stmt.args[1], env), str):
            reps = 0  # `Therm107(T,"fuzz",...)` (oracle, CR300)
        if reps is None:
            continue
        if profile.single_pass:
            if reps == 0:
                # before this line's fnotch warning (oracle, CR300, fuzz `84bd80cc420c`)
                at = next((i for i, d in enumerate(diags.items)
                           if (d.line, d.file) == (stmt.line, stmt.file)), len(diags.items))
                diags.items.insert(at, Diagnostic("error", stmt.line,
                                                  "Invalid reps 0, must be > 0.", stmt.file))
            continue
        if reps <= 0:
            diags.error(stmt.line, f"Invalid reps {reps}, must be > 0", stmt.file)
        dest = stmt.args[0]
        if reps == 0 or not isinstance(dest, (S.Name, S.Index)) or \
                dest.name.upper() not in floats or dest.name.upper() not in dims:
            continue
        if reps > 0 and stmt.keyword.upper() not in _REPS_DEST_SIZE:
            continue
        shape = dims[dest.name.upper()]
        offset = _flat_offset(dest, shape)
        if offset is None:
            continue
        total = 1
        for n in shape:
            total *= n
        if reps < 0 or reps > total - offset:
            diags.error(stmt.line, f"Variable {dest.name} out of bounds.", stmt.file)


def _flat_offset(dest: S.Expr, shape: tuple[int, ...]) -> int | None:
    """0-based row-major offset of the destination's first element, or None if not literal."""
    if isinstance(dest, S.Name) or not dest.args or all(a is None for a in dest.args):
        return 0
    if not all(isinstance(a, S.Number) and isinstance(a.value, int) for a in dest.args):
        return None
    index = [a.value for a in dest.args]
    if len(index) == 1:
        return index[0] - 1 if 1 <= index[0] else None
    if len(index) != len(shape) or any(not 1 <= k <= n for k, n in zip(index, shape)):
        return None
    offset = 0
    for k, n in zip(index, shape):
        offset = offset * n + (k - 1)
    return offset
