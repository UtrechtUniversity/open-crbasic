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

import re
import struct

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import instr_rules

_FUNCTIONS = frozenset({"TIMEINTOINTERVAL", "IFTIME"})
# Functions whose integer-typed constant arguments draw `Parameter must be integral.`, with the
# argument positions checked (`docs/checker.md` "Integral parameters").
_INTEGRAL_ARGS = {"TIMEINTOINTERVAL": (0, 1, 2), "IFTIME": (0, 1, 2), "SERIALOPEN": (1,)}
_MONTH = 6
# The trailing `TimeOut` of `HTTPPut` (9th argument) and `FTPClient` (11th): see `timeout_gate`.
_TIMEOUT_ARG = {"HTTPPUT": 8, "FTPCLIENT": 10}
_MODE_RE = re.compile(r"(?i)\b(IfTime|TimeIntoInterval|Timer|SerialOpen|HTTPPut|FTPClient)\b\s*\(")
_LITERAL_RE = re.compile(r"\"[^\"]*\"|'.*$")
NOT_INTEGRAL = "Parameter must be integral."
_INT_MIN, _INT_MAX = -2**31, 2**31 - 1


def timeout_gate(seen: bool, text: str | None) -> tuple[bool | None, bool]:
    """`(gate, seen)` for one statement. `seen` is the parser's persistent integer-argument mode:
    `IfTime`/`TimeIntoInterval` set it and nothing restores it until a `Timer` or `SerialOpen`
    (oracle, round 17, CR6 and CR350: an earlier `IfTime` in a `Sub`, another `Scan` or earlier in
    the same statement; `Timer`/`SerialOpen` between them switches it off). `gate` is the mode at
    the statement's first `HTTPPut`/`FTPClient` (`True`/`False`), `None` when an `IfTime` follows
    that call in the same statement (not modelled). Gates the `TimeOut` rule (round 17 notes in
    `docs/instructions.md`)."""
    code = _LITERAL_RE.sub('""', text or "")
    mode, gate, queried = seen, False, False
    for m in _MODE_RE.finditer(code):
        word = m.group(1).upper()
        if word in ("HTTPPUT", "FTPCLIENT"):
            if not queried:
                gate, queried = mode, True
        elif word in ("IFTIME", "TIMEINTOINTERVAL"):
            if queried and not mode:
                gate = None
            mode = True
        else:
            mode = False
    return gate, mode


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    wide = wide_constants(program, profile)
    scan: tuple[int, int] | None = None
    seen = False
    for stmt in program.statements:
        gate, seen = timeout_gate(seen, getattr(stmt, "text", None))
        if gate:
            _check_timeout(stmt, diags, env, profile, wide)
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        if kw == "SCAN":
            scan = _scan_step(stmt, env)
            continue
        if kw in ("NEXTSCAN", "SUBSCAN", "NEXTSUBSCAN") or isinstance(stmt, S.SubDef):
            scan = None
        for call in _calls(stmt, _INTEGRAL_ARGS):
            if isinstance(call, S.Call) and call.explicit:
                continue
            bad = [i for i in _INTEGRAL_ARGS[call.name.upper()][:len(call.args)]
                   if not_integral(call.args[i], env, profile, wide)]
            for _ in bad:
                diags.error(stmt.line, NOT_INTEGRAL, stmt.file)
            if not bad and call.name.upper() in _FUNCTIONS:
                _check_call(call, scan, stmt, diags, env,
                            any(d.line < stmt.line for d in diags.errors))


def _check_timeout(stmt: S.Stmt, diags: Diagnostics, env, profile, wide) -> None:
    """After an earlier `IfTime`/`TimeIntoInterval`, a string `TimeOut` is `Warning: Parameter will
    truncate to the nearest integer.`; a float-typed constant or `NAN` is `Parameter must be
    integral.` (not a constant-integer error; oracle, all four loggers, round 17)."""
    for call in _calls(stmt, _TIMEOUT_ARG):
        if isinstance(call, S.Call) and call.explicit:
            continue
        i = _TIMEOUT_ARG[call.name.upper()]
        if i >= len(call.args) or call.args[i] is None:
            continue
        arg = call.args[i]
        if isinstance(arg, S.String):
            diags.warning(stmt.line, "Parameter will truncate to the nearest integer.", stmt.file)
        elif (isinstance(arg, S.Name) and arg.name.upper() == "NAN") or (
                not_integral(arg, env, profile, wide)
                and (not profile.single_pass or _float_typed(arg, env, wide))):
            diags.error(stmt.line, NOT_INTEGRAL, stmt.file)


def wide_constants(program: S.Program, profile) -> frozenset[str]:
    """`Const` names declared as one positive numeric literal beyond the converted range: they
    carry the literal's float type into every expression that uses them (oracle, 2026-10-02:
    `Const KB = 999999999999` -> `KB`, `-KB` and `KB-1` all draw the error, while the computed
    `Const KC = 2147483647+1` and `Const KM = -2147483649` wrap silently)."""
    return frozenset(
        st.name.upper() for st in program.statements
        if isinstance(st, S.ConstDecl) and isinstance(st.value, S.Number)
        and not st.value.text.startswith("&") and _out_of_range(st.value.value, profile))


def _out_of_range(value, profile) -> bool:
    if profile.name in ("cr300", "cr350"):
        rounded = struct.unpack("<f", struct.pack("<f", float(value)))[0] \
            if abs(value) < 3.4e38 else float("inf")
        return not _INT_MIN <= rounded <= 2**31
    return not _INT_MIN <= value <= _INT_MAX


def not_integral(expr: S.Expr | None, env: consteval.Env, profile, wide=frozenset()) -> bool:
    """Whether a constant argument of an integer-typed function parameter draws `Parameter must be
    integral.` (oracle, 2026-10-02): the expression is float-typed (a literal with a decimal point
    or exponent, a `/` or `^`, a named constant whose value is not whole, or arithmetic with any
    of those) or its value is outside the converted range: a 32-bit integer on CR6/CR1000X, a
    32-bit float rounded into [-2^31, 2^31] on CR300/CR350. A named constant otherwise counts as
    an integer (`Const K = 60.0` passes). Hex and binary literals wrap to 32 bits and are never
    flagged. Variables and function calls are left alone."""
    if expr is None or not _plain(expr):
        return False
    try:
        value = consteval.value_of(expr, env)
    except consteval.NotConstant:
        return False
    if isinstance(value, (str, bool)) or not isinstance(value, (int, float)):
        return False
    if _float_typed(expr, env, wide) or value != int(value):
        return True
    literal = expr.operand if isinstance(expr, S.Unary) and expr.op == "-" else expr
    if not isinstance(literal, S.Number) or literal.text.startswith("&"):
        return False  # (a computed or named value wraps or saturates silently, rule not fitted)
    return _out_of_range(value, profile)


def _plain(expr: S.Expr | None) -> bool:
    if isinstance(expr, (S.Number, S.Name)):
        return True
    if isinstance(expr, S.Unary):
        return expr.op in ("-", "+") and _plain(expr.operand)
    if isinstance(expr, S.Binary):
        return expr.op in ("+", "-", "*", "/", "^", "\\", "MOD") and _plain(expr.left) and \
            _plain(expr.right)
    return False


def _float_typed(expr: S.Expr | None, env: consteval.Env, wide) -> bool:
    if isinstance(expr, S.Number):
        text = expr.text.upper()
        return not text.startswith("&") and ("." in text or "E" in text)
    if isinstance(expr, S.Name):
        if expr.name.upper() in wide:
            return True
        try:
            value = consteval.value_of(expr, env)
        except consteval.NotConstant:
            return False
        return isinstance(value, float) and value != int(value)
    if isinstance(expr, S.Unary):
        return _float_typed(expr.operand, env, wide)
    if isinstance(expr, S.Binary):
        return expr.op in ("/", "^") or _float_typed(expr.left, env, wide) or \
            _float_typed(expr.right, env, wide)
    return False


def _check_call(call: S.Index, scan, stmt: S.Stmt, diags: Diagnostics, env,
                after_error: bool = False) -> None:
    values = [_int(a, env) for a in call.args[:3]]
    if len(call.args) != 3 or None in values or values[2] == _MONTH or \
            values[2] not in instr_rules._US_PER_UNIT or _INT_MIN in values[:2]:
        return
    if after_error and min(values[:2]) < 0:
        return  # a negative argument is only reported while the file is still clean
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
        step = _wrap32(scan[0] * 10000 + _cdiv(scan[1], 100))
    else:
        interval, tinto, step = i_sec, t_sec, scan[0]
        if i_us or t_us:
            diags.error(stmt.line, "Illegal sub seconds in time into and/or interval when "
                        "interval > 3600 secs.", stmt.file)
    if step and (_cmod(interval, step) or _cmod(tinto, step)):
        diags.error(stmt.line, "Scan interval must be integrally divisible into interval.",
                    stmt.file)


def _scan_step(stmt: S.Instruction, env: consteval.Env) -> tuple[int, int] | None:
    """Seconds and microseconds the compiler keeps of a constant `Scan` interval, valid or not
    (`instr_rules.scan_split`; oracle, all four loggers, 2026-10-10: `Scan("fuzz",Sec,...)` is
    -2^31 s, whose step in 100 us wraps to 0 and so passes `IfTime(0,5,Min)` but not `(0,24,Hr)`)."""
    return instr_rules.scan_split(stmt, env)


def _wrap32(n: int) -> int:
    n &= 0xFFFFFFFF
    return n - 2**32 if n >= 2**31 else n


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
    if isinstance(expr, S.Number) and expr.text.startswith("&"):
        if int(value) > 0xFFFFFFFF:
            return None  # (real: silent for more than 8 hex digits; rule not fitted)
        value = int(value) & 0xFFFFFFFF
        return value - 2**32 if value >= 2**31 else value
    inner = expr.operand if isinstance(expr, S.Unary) and expr.op == "-" else expr
    if env.profile.name in ("cr300", "cr350") and isinstance(inner, S.Number):
        return max(-_INT_MAX, min(_INT_MAX, int(value)))  # a single literal saturates
    return int(value) if _INT_MIN <= value <= _INT_MAX else None  # computed: wraps, silent


def _calls(stmt: S.Stmt, names=_FUNCTIONS):
    """Every call of `names` in `stmt`, including a one-line `If`'s statements."""
    exprs: list[S.Expr | None] = []
    if isinstance(stmt, (S.If, S.ElseIf, S.DoLoop)):
        exprs.append(stmt.cond)
    if isinstance(stmt, S.Call) and stmt.name.upper() in names:
        yield stmt
    if isinstance(stmt, S.If):
        for inner in (stmt.inline or []) + (stmt.inline_else or []):
            yield from _calls(inner, names)
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
            if expr.name.upper() in names:
                yield expr
            stack += [a for a in expr.args if a is not None]
        elif isinstance(expr, S.Unary):
            stack.append(expr.operand)
        elif isinstance(expr, S.Binary):
            stack += [expr.left, expr.right]
        elif isinstance(expr, S.Member):
            stack += [a for a in (expr.args or []) if a is not None]
