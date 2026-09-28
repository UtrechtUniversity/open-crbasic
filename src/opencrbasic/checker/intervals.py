"""`TimeIntoInterval`/`IfTime` argument checks, made by the parser (pass 1) on all four loggers.

Decompiled `FUN_00494780`: with constant `TintoInt`, `Interval` and `Units` (not month), both
values become whole seconds plus microseconds. A negative `TintoInt` wraps into the interval. Then:

- `Interval (%d) must be > 0 and TimeInto Interval (%d) must be < Interval.` (the raw values)
  unless `0 <= TintoInt < Interval`;
- inside a `Scan`, `Scan interval must be integrally divisible into interval.` unless both are
  multiples of the scan interval (100 uSec units, or whole seconds once the interval passes
  3600 s, where sub-second parts give `Illegal sub seconds in time into and/or interval when
  interval > 3600 secs.`).

Oracle probes, 2026-09-28 (`docs/checker.md` "TimeIntoInterval arguments").
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import instr_rules

_FUNCTIONS = frozenset({"TIMEINTOINTERVAL", "IFTIME"})
_MONTH = 6


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    scan: tuple[int, int] | None = None
    for stmt in program.statements:
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        if kw == "SCAN":
            raw = instr_rules._scan_raw(stmt, env)
            scan = _split(raw[0] * instr_rules._US_PER_UNIT[raw[1]]) if raw else None
            continue
        if kw in ("NEXTSCAN", "SUBSCAN", "NEXTSUBSCAN") or isinstance(stmt, S.SubDef):
            scan = None
        for call in _calls(stmt):
            _check_call(call, scan, stmt, diags, env)


def _check_call(call: S.Index, scan, stmt: S.Stmt, diags: Diagnostics, env) -> None:
    values = [_int(a, env) for a in call.args[:3]]
    if len(call.args) != 3 or None in values or values[2] == _MONTH or \
            values[2] not in instr_rules._US_PER_UNIT:
        return
    tinto_raw, interval_raw, unit = values
    t_sec, t_us = _split(tinto_raw * instr_rules._US_PER_UNIT[unit])
    i_sec, i_us = _split(interval_raw * instr_rules._US_PER_UNIT[unit])
    if t_us < 0:
        t_sec, t_us = i_sec - 1 + t_sec, t_us + 1_000_000
    elif t_sec < 0:
        t_sec += i_sec
    if i_sec < 0 or i_us < 0 or t_sec < 0 or i_sec < t_sec or (t_sec == i_sec and i_us <= t_us):
        diags.error(stmt.line, f"Interval ({interval_raw}) must be > 0 and TimeInto Interval "
                    f"({tinto_raw}) must be < Interval.", stmt.file)
    if scan is None:
        return
    if i_sec < 3601:
        interval, tinto = i_sec * 10000 + _cdiv(i_us, 100), t_sec * 10000 + _cdiv(t_us, 100)
        step = scan[0] * 10000 + _cdiv(scan[1], 100)
    else:
        interval, tinto, step = i_sec, t_sec, scan[0]
        if i_us or t_us:
            diags.error(stmt.line, "Illegal sub seconds in time into and/or interval when "
                        "interval > 3600 secs.", stmt.file)
    if step and (_cmod(interval, step) or _cmod(tinto, step)):
        diags.error(stmt.line, "Scan interval must be integrally divisible into interval.",
                    stmt.file)


def _split(total_us: int) -> tuple[int, int]:
    """Whole seconds and microseconds, both truncated toward zero like the compiler's C code."""
    sec = _cdiv(total_us, 1_000_000)
    return sec, total_us - sec * 1_000_000


def _cdiv(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def _cmod(a: int, b: int) -> int:
    return a - b * _cdiv(a, b)


def _int(expr: S.Expr | None, env: consteval.Env) -> int | None:
    if expr is None or not consteval.is_constant(expr, env):
        return None
    try:
        value = consteval.value_of(expr, env)
    except consteval.NotConstant:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        return None
    return int(value)


def _calls(stmt: S.Stmt):
    """Every `TimeIntoInterval`/`IfTime` call in `stmt`, including a one-line `If`'s statements."""
    exprs: list[S.Expr | None] = []
    if isinstance(stmt, (S.If, S.ElseIf, S.DoLoop)):
        exprs.append(stmt.cond)
    if isinstance(stmt, S.If):
        for inner in (stmt.inline or []) + (stmt.inline_else or []):
            yield from _calls(inner)
    elif isinstance(stmt, S.Assignment):
        exprs += [stmt.target, stmt.value]
    elif isinstance(stmt, (S.Instruction, S.Call)):
        exprs += list(stmt.args)
    elif isinstance(stmt, S.For):
        exprs += [stmt.start, stmt.end, stmt.step]
    stack = [e for e in exprs if e is not None]
    while stack:
        expr = stack.pop(0)
        if isinstance(expr, S.Index):
            if expr.name.upper() in _FUNCTIONS:
                yield expr
            stack += [a for a in expr.args if a is not None]
        elif isinstance(expr, S.Unary):
            stack.append(expr.operand)
        elif isinstance(expr, S.Binary):
            stack += [expr.left, expr.right]
        elif isinstance(expr, S.Member):
            stack += [a for a in (expr.args or []) if a is not None]
