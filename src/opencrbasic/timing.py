"""`-t`: the `[Times]` section CR6Comp writes to the message file -- one `line N: <label>` per
timed statement. Every rule, its evidence and the instructions still unmodelled (AM25T) are in `docs/cli.md` "Timing output
(-t)"; coverage is only what oracle probing established, anything else prints no entry.
"""

from __future__ import annotations

import re

from . import consteval, mode
from . import syntax as S
from .profiles import Profile


def _const(expr: S.Expr | None, env: consteval.Env) -> object | None:
    try:
        return consteval.value_of(expr, env)
    except consteval.NotConstant:
        return None


def _num(v: object | None) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _round_half_up(x: float) -> int:
    # C's usual `(int)(x + 0.5)` for a non-negative `x` -- avoids Python's round-half-to-even,
    # which would silently disagree with the exe at an exact `.5` (docs/cli.md "-t").
    return int(x + 0.5)


def _flatten(stmts):
    for stmt in stmts:
        yield stmt
        if isinstance(stmt, S.If):
            yield from _flatten(stmt.inline or [])
            yield from _flatten(stmt.inline_else or [])


def times_lines(program: S.Program, profile: Profile, main_file: str) -> list[str]:
    """The `[Times]` section's entries (without the `[Times]` header itself), in program order.

    `main_file` is accepted for backwards compatibility but no longer used to filter statements
    (see the module docstring's last paragraph); kept so callers do not need to change.
    """
    del main_file
    env = consteval.Env(profile, dict(program.consts))
    try:
        pipeline = mode.decide(program.statements, profile, lambda e: consteval.is_constant(e, env),
                               lambda e: consteval.value_of(e, env)) == "Pipeline"
    except Exception:
        pipeline = False
    lines: list[str] = []
    scan_count = 0
    inside = False       # within a Scan(...)'s own span (see module docstring)
    in_sub = False
    prev_kw = ""
    for stmt in _flatten(program.statements):
        if isinstance(stmt, S.SubDef):
            in_sub = True
            continue
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword
        if kw in ("ENDSUB", "ENDFUNCTION"):
            in_sub = False
        if kw == "SCAN":
            if prev_kw != "SLOWSEQUENCE":
                if scan_count:
                    lines.append("")
                lines.append(f"line {stmt.line}: Scan: {scan_count}" + (" (Subroutine)" if in_sub else ""))
                scan_count += 1
                inside = True
            else:
                inside = False
            prev_kw = kw
            continue
        if kw == "NEXTSCAN":
            inside = False
            prev_kw = kw
            continue
        prev_kw = kw
        if not inside:
            continue
        if kw == "PORTSET" and len(stmt.args) == 2:
            lines.append(f"line {stmt.line}: PortSet = 100")
        elif kw == "SW12":
            lines.append(f"line {stmt.line}: Sw12 = 100")
        elif kw == "PANELTEMP":
            _paneltemp(stmt, env, lines)
        elif kw == "DELAY":
            _delay(stmt, env, lines, pipeline)
        elif kw in _MEASURE:
            _measure(stmt, env, lines)
        elif kw in _THERM:
            _therm(stmt, env, lines)
        elif kw in _BRIDGE:
            _bridge(stmt, env, lines)
        elif kw in _CLOSED:
            _closed(stmt, env, lines, pipeline)
    return lines


def _paneltemp(stmt: S.Instruction, env: consteval.Env, lines: list[str]) -> None:
    if len(stmt.args) < 2:
        return
    timeout = _num(_const(stmt.args[1], env))
    if not timeout or timeout <= 0:
        return
    cost = (10 + _round_half_up(10000.0 / timeout)) * 100
    lines.append(f"line {stmt.line}: PanelTemp = {cost}")
    lines.append(f"line {stmt.line}: PanelTemp = {cost}")


_UNIT_US = {"USEC": 1, "MSEC": 1000, "SEC": 1000000, "MIN": 60000000}


def _signed32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x >= 1 << 31 else x


def _floor100(x: int) -> int:
    """`x` rounded down to a multiple of 100 in unsigned 32-bit arithmetic, shown signed (a Delay
    of -5 mSec prints -4996; docs/cli.md)."""
    return _signed32((x & 0xFFFFFFFF) // 100 * 100)


def _delay(stmt: S.Instruction, env: consteval.Env, lines: list[str], pipeline: bool) -> None:
    # Timed only for RepDelay == 0 in PipelineMode; units case-insensitive (docs/cli.md).
    if len(stmt.args) < 3 or not pipeline:
        return
    value = _num(_const(stmt.args[1], env))
    units = stmt.args[2]
    if _num(_const(stmt.args[0], env)) != 0 or not value:
        return
    if not isinstance(units, S.Name) or units.name.upper() not in _UNIT_US:
        return
    cost = _floor100(int(value * _UNIT_US[units.name.upper()]) + 100)
    lines.append(f"line {stmt.line}: Delay = {cost}")


# (reps, range, channel, flag, settle, integ) argument positions; `flag_adds` is True when the
# flag (MeasOff / RevDiff) adds one measurement per rep to a single-ended / doubles a differential.
_MEASURE = {
    "VOLTSE": (1, 2, 3, 4, 5, 6, False),
    "VOLTDIFF": (1, 2, 3, 4, 5, 6, True),
    "TCSE": (1, 2, 3, 6, 7, 8, False),
    "TCDIFF": (1, 2, 3, 6, 7, 8, True),
}
_PLAIN_RANGES = {800, 802, 804}
_C_RANGES = {820, 822, 824}
_AUTO_RANGES = {810, 830}   # AutoRange, AutoRangeC


def _measure(stmt: S.Instruction, env: consteval.Env, lines: list[str]) -> None:
    """VoltSe/VoltDiff/TCSe/TCDiff (docs/cli.md "Voltage and thermocouple cost"). Not modelled
    (no entry): AutoRange, non-constant arguments, negative settle/integ."""
    ri, gi, ci, fi, si, ii, doubling = _MEASURE[stmt.keyword]
    if len(stmt.args) <= ii:
        return
    reps, rng, flag, settle, integ = (_num(_const(stmt.args[k], env)) for k in (ri, gi, fi, si, ii))
    chan = stmt.args[ci]
    um = re.fullmatch(r"U(\d+)", chan.name.upper()) if isinstance(chan, S.Name) else None
    u_chan = um is not None and (not doubling or int(um.group(1)) % 2 == 1)  # even U is no differential pair
    if um is not None and not u_chan:
        chan = S.Number(chan.line, value=0)
    if not u_chan and _num(_const(chan, env)) is None:
        return
    if None in (reps, rng, flag, settle, integ) or reps < 1 or settle < 0 or integ < 0:
        return
    auto = rng in _AUTO_RANGES
    if rng not in _PLAIN_RANGES and rng not in _C_RANGES and not auto:
        return
    n = reps * (2 if flag else 1) if doubling else reps + (1 if flag else 0)
    extra = 0
    thirds_u = 0
    if rng in _C_RANGES or rng == 830:
        extra = 50 * reps
        if u_chan:
            thirds_u = 160
    cost = _meas_cost(n, settle, integ, extra, thirds_u, (n if doubling else reps) if auto else 0)
    lines.append(f"line {stmt.line}: {_LABEL[stmt.keyword]} = {cost}")


def _meas_cost(n: float, settle: float, integ: float, extra: int = 0, thirds_u: int = 0, auto_reps: int = 0) -> int:
    ks = int((24 * (settle or 500) + 127) // 256)
    kx = 24 if integ == 0 else max(1, int((24e6 / integ + 128) // 256))
    settle_part = -(-(32 * ks + thirds_u) // 3)
    # AutoRange adds one settle-only pass per rep, 1 us cheaper than a measurement's (docs/cli.md).
    return int(140 + extra + n * (816 + settle_part + 32 * kx // 3) + auto_reps * (815 + settle_part)) // 100 * 100


# Therm107/108/109(Dest, Reps, SEChan, VxChan, Settle, Integ, Mult, Offset): two measurements per
# rep, plain range, no channel dependence (docs/cli.md "Thermistor cost").
_THERM = {"THERM107": "Therm107", "THERM108": "Therm108", "THERM109": "Therm109"}


def _therm(stmt: S.Instruction, env: consteval.Env, lines: list[str]) -> None:
    a = _args(stmt, env, 1, 4, 5)
    if a is None or a[0] < 1 or a[1] < 0 or a[2] < 0:
        return
    lines.append(f"line {stmt.line}: {_THERM[stmt.keyword]} = {_meas_cost(2 * a[0], a[1], a[2])}")


_LABEL = {"VOLTSE": "VoltSe", "VOLTDIFF": "VoltDiff", "TCSE": "TCSe", "TCDIFF": "TCDiff"}


# Bridge instructions: (label, first range, second range or None, channel, MeasPEx, RevDiff or
# None, RevEx, settle, integ, base measurements per rep, single-ended, no range dependence,
# entry pairs). Argument positions after `Dest` (docs/cli.md "Bridge cost").
_BRIDGE = {
    "BRHALF": ("BrHalf", 2, None, 3, 5, None, 7, 8, 9, 1, True, False),
    "BRHALF3W": ("BrHalf3W", 2, None, 3, 5, None, 7, 8, 9, 2, True, False),
    "BRHALF4W": ("BrHalf4W", 2, 3, 4, 6, 8, 9, 10, 11, 2, False, False),
    "BRFULL": ("BrFull", 2, None, 3, 5, 7, 8, 9, 10, 1, False, False),
    "BRFULL6W": ("BrFull6W", 2, 3, 4, 6, 8, 9, 10, 11, 2, False, False),
    "RESISTANCE": ("Resistance", 2, None, 3, 5, 7, 8, 9, 10, 1, False, True),
}


def _bridge(stmt: S.Instruction, env: consteval.Env, lines: list[str]) -> None:
    label, g1, g2, ci, mpi, rdi, rei, si, ii, base, single, resistance = _BRIDGE[stmt.keyword]
    if len(stmt.args) <= ii:
        return
    idx = [1, g1, mpi, rei, si, ii] + ([g2] if g2 else []) + ([rdi] if rdi else [])
    vals = [_num(_const(stmt.args[k], env)) for k in idx]
    if None in vals:
        return
    reps, rng, mp, rev_ex, settle, integ = vals[:6]
    rest = vals[6:]
    rng2 = rest.pop(0) if g2 else rng
    rev_diff = rest.pop(0) if rdi else 0
    chan = stmt.args[ci]
    if not _chan_is_u(chan) and _num(_const(chan, env)) is None:
        return
    if reps < 1 or reps > 1000 or settle < 0 or integ < 0:
        return
    c_range = False
    for r in (rng, rng2):
        if r not in _PLAIN_RANGES and r not in _C_RANGES:
            return
        c_range |= r in _C_RANGES
    if resistance:
        c_range = False
        if mp < 1:
            return
    u = 0
    if c_range and _chan_is_u(chan) and (single or int(re.fullmatch(r"U(\d+)", chan.name.upper()).group(1)) % 2 == 1):
        u = 160
    per = base * (2 if rev_diff else 1) * (2 if rev_ex else 1)
    group = max(int(mp), 1)
    left = int(reps)
    while left > 0:
        g = min(group, left)
        left -= g
        if resistance:
            lines.append(f"line {stmt.line}: {label} = {_meas_cost(per, settle, integ)}")
        extra = 50 * g * base if c_range else 0
        lines.append(f"line {stmt.line}: {label} = {_meas_cost(g * per, settle, integ, extra, u)}")


def _args(stmt: S.Instruction, env: consteval.Env, *idx: int) -> list[float] | None:
    if len(stmt.args) <= max(idx):
        return None
    vals = [_num(_const(stmt.args[i], env)) for i in idx]
    return None if None in vals else vals


def _chan_is_u(expr: S.Expr | None) -> bool:
    return isinstance(expr, S.Name) and re.fullmatch(r"U\d+", expr.name.upper()) is not None


def _closed(stmt: S.Instruction, env: consteval.Env, lines: list[str], pipeline: bool) -> None:
    """Instructions whose cost is a closed form of one or two constant arguments
    (docs/cli.md "Closed forms"); no entry when an argument is not constant."""
    kw = stmt.keyword
    out: list[int] = []
    label = _CLOSED[kw]
    if kw == "PULSEPORT":
        a = _args(stmt, env, 1)
        out = [_floor100(2 * max(int(a[0]), 0) + 100)] if a else []
    elif kw == "EXCITEV":
        a = _args(stmt, env, 2)
        out = [_floor100(max(int(a[0]), 0) + 100)] if a else []
    elif kw == "PULSECOUNT":
        out = [100] if _args(stmt, env, 1) else []
    elif kw == "CALIBRATE":
        out = [2000, 7700] * 3
    elif kw == "CS616":
        a = _args(stmt, env, 1)
        out = [200 + 500 * int(a[0])] if a else []
    elif kw == "PERIODAVG":
        a = _args(stmt, env, 1, 7)
        out = [_signed32(int(a[0]) * int(a[1]) * 1000 + 200)] if a else []
    elif kw == "MUXSELECT":
        a = _args(stmt, env, 2, 3, 4)
        if a and pipeline:
            base = 100 if a[2] == 0 else 13000
            out = [_floor100(base + int(a[1]) * (2000 * int(a[0]) + 50))]
    elif kw == "ACPOWER":
        out = _acpower(stmt, env)
    lines.extend(f"line {stmt.line}: {label} = {v}" for v in out)


def _acpower(stmt: S.Instruction, env: consteval.Env) -> list[int]:
    # LineFrq 50/60 are special; any other whole-number frequency (a fraction is truncated) follows
    # the closed form of docs/cli.md "ACPower"; below 1 Hz the real exe reports an error.
    a = _args(stmt, env, 1, 2, 9)
    if not a or len(stmt.args) < 10 or not 1 <= a[1] < 1e6:
        return []
    for k in (3, 6):
        if not _chan_is_u(stmt.args[k]) and _num(_const(stmt.args[k], env)) is None:
            return []
    f = int(a[1])
    if f in (50, 60):
        u_cost, n_cost = {50: (41100, 278300), 60: (34500, 231900)}[f]
        head = 61200 if f == 50 else 51200
    else:
        q = 1000 // f
        u_cost, n_cost = (1003 * q + 2171) // 100 * 100, (2021 * q + 2160) // 100 * 100
        head = 1200 + 1000 * (2000 // f)
    volt, cur = (u_cost if _chan_is_u(stmt.args[k]) else n_cost for k in (3, 6))
    cfg = int(a[0])
    seq = {2: [volt, cur, cur], 3: [volt, cur] * 3, 1: [volt] + [cur] * max(int(a[2]), 0)}.get(cfg, [volt, cur])
    return [1000] + [v for x in seq for v in (head, x)]


_CLOSED = {"PULSEPORT": "PulsePort", "EXCITEV": "ExciteV", "PULSECOUNT": "Pulsecount",
           "CALIBRATE": "Calibrate", "CS616": "CS616", "PERIODAVG": "PeriodAvg",
           "MUXSELECT": "MuxSelect", "ACPOWER": "ACPower"}
