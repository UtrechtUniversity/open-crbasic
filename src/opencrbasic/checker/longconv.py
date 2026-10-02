"""Constant arguments converted to the compiler's 32-bit `long` (oracle, 2026-10-02,
`docs/checker.md` "Option arguments"), shared by the `SplitStr`, `IPRoute` and `Matrix` option
checks.

An integer-typed constant expression (integer literals, whole-valued names, `+ - * MOD \\`) wraps
at 32 bits (`100000*100000` -> 1410065408, `2147483647+1` -> -2147483648). A float-typed one (a
decimal point or exponent, `/`, `^`, a literal beyond 32 bits, a name declared from one) is
truncated toward zero. Beyond the 32-bit range a float becomes -2147483648 on CR6/CR1000X; on
CR300/CR350 it is first rounded to a 32-bit float, becoming -2147483648 past +-2^31 and otherwise
saturating at +-2147483647. A string or NAN is -2147483648. Hex literals wrap to 32 bits.
"""

from __future__ import annotations

import math
import struct

from .. import consteval
from .. import syntax as S

INT_MIN = -2**31
_OPS = ("+", "-", "*", "/", "^", "\\", "MOD")


def float_names(program: S.Program) -> frozenset[str]:
    """`Const` names whose declaration is float-typed (resolved in declaration order)."""
    names: set[str] = set()
    for st in program.statements:
        if isinstance(st, S.ConstDecl) and _float_typed(st.value, names):
            names.add(st.name.upper())
    return frozenset(names)


def _float_typed(expr: S.Expr | None, names) -> bool:
    if isinstance(expr, S.Number):
        text = expr.text.upper()
        if text.startswith("&"):
            return False
        return "." in text or "E" in text or not INT_MIN <= expr.value < 2**31
    if isinstance(expr, S.Name):
        return expr.name.upper() in names
    if isinstance(expr, S.Unary):
        return _float_typed(expr.operand, names)
    if isinstance(expr, S.Binary):
        return expr.op in ("/", "^") or _float_typed(expr.left, names) or \
            _float_typed(expr.right, names)
    return False


def _plain(expr: S.Expr | None) -> bool:
    if isinstance(expr, (S.Number, S.Name)):
        return True
    if isinstance(expr, S.Unary):
        return expr.op in ("-", "+") and _plain(expr.operand)
    if isinstance(expr, S.Binary):
        return expr.op in _OPS and _plain(expr.left) and _plain(expr.right)
    return False


def long_value(arg: S.Expr | None, env: consteval.Env, profile, names=frozenset()) -> int | None:
    """The 32-bit `long` the compiler makes of constant `arg`, or None when `arg` is missing, not
    constant, or in a form this model does not cover (hex beyond 8 digits, function calls)."""
    if arg is None:
        return None
    if isinstance(arg, S.String):
        return INT_MIN
    if isinstance(arg, S.Name) and arg.name.upper() in ("NAN", "INF") and \
            arg.name.upper() not in env.consts:
        return INT_MIN
    if not _plain(arg):
        return None
    try:
        value = consteval.value_of(arg, env)
    except consteval.NotConstant:
        return None
    if isinstance(value, str):
        return INT_MIN
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return INT_MIN
    if isinstance(arg, S.Number) and arg.text.startswith("&"):
        if value > 0xFFFFFFFF:
            return None
        return value - 2**32 if value >= 2**31 else int(value)
    if _hex_inside(arg):
        return None
    if not (_float_typed(arg, names) or value != int(value)):
        return _wrap(int(value))
    if INT_MIN < value < 2**31:
        return int(value)
    if profile.name in ("cr300", "cr350"):
        return _float32_long(value)
    return INT_MIN


def _hex_inside(expr: S.Expr | None) -> bool:
    if isinstance(expr, S.Number):
        return expr.text.startswith("&")
    if isinstance(expr, S.Unary):
        return _hex_inside(expr.operand)
    if isinstance(expr, S.Binary):
        return _hex_inside(expr.left) or _hex_inside(expr.right)
    return False


def _wrap(value: int) -> int:
    value = int(value) & 0xFFFFFFFF
    return value - 2**32 if value >= 2**31 else value


def _float32_long(value: float) -> int:
    f = struct.unpack("<f", struct.pack("<f", float(value)))[0] if abs(value) < 3.4e38 \
        else math.copysign(math.inf, value)
    if not -2**31 <= f <= 2**31:
        return INT_MIN
    return max(-2**31 + 1, min(2**31 - 1, int(f)))
