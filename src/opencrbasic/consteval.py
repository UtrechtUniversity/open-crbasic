"""Constant-expression evaluation: numbers, strings, predefined symbols, earlier `Const`s,
operators (CRBasic precedence, built by `parser.py`) and a handful of pure builtin functions.

Used for `#If`/`#ElseIf` conditions (which must be constant) and by `mode.py`'s `is_constant`/
`value_of` callbacks.

Operator precedence, loosest to tightest (built into `parser.py`'s expression parser, not this
module, which only walks whatever tree it is given): ``IMP EQV OR XOR AND`` (one left-associative
tier -- see docs/frontend.md: the oracle refutes the textbook "AND binds tighter than OR" rule
for CRBasic), ``NOT`` (unary), relational (``= <> < > <= >=``), ``<< >>``, ``& + -``,
``MOD``, ``\\``, ``* /``, unary ``- +``, ``^`` (left-associative; confirmed `2^3^2 = 64`, and
unary minus binds looser than `^`: `-2^2 = -4`).

``TRUE`` = -1, ``FALSE`` = 0 (from the symbol table); comparisons yield -1/0.
"""

import math
import sys
from dataclasses import dataclass, field
from typing import TypeGuard

from . import syntax as S
from .profiles import Profile


class NotConstant(Exception):
    """Raised when an expression (or a name within it) has no compile-time value."""


class DivideByZero(NotConstant):
    """A `/` whose divisor folds to 0; `numerator` is the folded dividend. Every folded value is a
    float, so a caller that must tell `1/0` from `1/0.0` looks at the source text."""

    numerator: object = None


class CFloat(float):
    """A `Const` value known to be float-typed."""


@dataclass
class Env:
    """Constants known so far (definition order doesn't matter; last `define` wins) plus the
    logger profile's predefined symbols. `section` is the `Section` pseudo-value bound while
    walking a line that came from an `IncludeSection()` call (see `source.py`); referencing
    `Section` outside that context is an ordinary undefined-symbol error (verified with the
    oracle: `Invalid symbol in expression: Section = "Foo" .`).
    """

    profile: Profile
    consts: dict[str, S.ConstValue] = field(default_factory=dict)
    section: str | None = None

    def define(self, name: str, value: S.ConstValue, is_float: bool | None = None) -> None:
        """`is_float` (see `is_float`) travels with the stored value, so the `dict(consts)` copies
        the checker makes keep it: `CFloat` is float-typed, an `int` integer-typed, a plain float
        not established."""
        if is_float and isinstance(value, float):
            value = CFloat(value)
        elif (
            is_float is False
            and isinstance(value, float)
            and value == value
            and abs(value) < 2**63
            and value == int(value)
        ):
            value = int(value)
        self.consts[name.upper()] = value

    def child(self, section: str | None) -> Env:
        """A view with a different `Section` binding but the same (mutable, shared) consts."""
        return Env(self.profile, self.consts, section)


# Pure builtins usable in constant expressions (see mathematicalexpressions.htm / the CHM function
# pages for CHR/ABS/INT/SQR/LEN). Each takes already-evaluated argument values.
def _fn_chr(args: list[S.ConstValue]) -> str:
    # any numeric argument is accepted by the real compilers (CHR(-1), CHR(1e300) probed 2026-10-10).
    # A NUL byte adds nothing: CHR(256), CHR(0)+"A"+CHR(0)+"B" are "", "AB" (round 10, see checker.md)
    v = _num(args[0])
    code = int(v) & 0xFF if v == v and abs(v) != float("inf") else 0
    return chr(code) if code else ""


def _fn_abs(args: list[S.ConstValue]) -> float:
    return abs(_num(args[0]))


def _fn_int(args: list[S.ConstValue]) -> float:
    v = _num(args[0])
    import math

    return float(math.floor(v))


def _fn_sqr(args: list[S.ConstValue]) -> float:
    root = _num(args[0]) ** 0.5
    if isinstance(root, complex):
        raise NotConstant("SQR() of a negative constant is not folded")
    return root


def _fn_len(args: list[S.ConstValue]) -> int:
    v = args[0]
    if not isinstance(v, str):
        raise NotConstant("LEN() of a non-string constant")
    return len(v)


_BUILTINS = {"CHR": _fn_chr, "CHR$": _fn_chr, "ABS": _fn_abs, "INT": _fn_int, "SQR": _fn_sqr, "LEN": _fn_len}


def _num(v: object) -> float:
    if isinstance(v, bool):
        return -1.0 if v else 0.0
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except OverflowError as exc:
            # Huge integer literals have no recorded oracle value; stop folding rather than
            # crash (round-67 FTPClient review, docs/checker.md#round-67-review-fixes).
            raise NotConstant("number outside float range") from exc
    raise NotConstant("expected a number")


def _bool(v: object) -> bool:
    return _num(v) != 0


def to_long(value: int | float | str) -> int:
    """The C `(long)` conversion applied to a constant parameter (oracle, 2026-10-02): truncated
    toward zero (`0.5` -> 0, `-1.9` -> -1); a string, NaN or a value outside Long range gives
    `-2147483648`."""
    if isinstance(value, str) or value != value:
        return -2147483648
    try:
        n = int(value)
    except OverflowError:
        return -2147483648
    return n if -2147483648 <= n <= 2147483647 else -2147483648


def is_finite_number(value: object) -> TypeGuard[int | float]:
    """Usable by integer-converting rules; infinities stay constants in `value_of`
    (no reference verdict: docs/checker.md#round-67-review-fixes)."""
    return isinstance(value, (int, float)) and (not isinstance(value, float) or math.isfinite(value))


def _truth(v: bool) -> int:
    return -1 if v else 0


def value_of(expr: S.Expr | None, env: Env) -> S.ConstValue:
    """The constant value of `expr` (int/float/str), or raise `NotConstant`. An integer beyond the
    float range (a literal of 309 digits) is not a constant: the reference gives no usable verdict
    for it, and the numeric rules convert to float. Infinities stay values: `String * 1e999` has
    recorded verdicts (rounds 29 and 54; round 69, docs/checker.md#round-67-review-fixes)."""
    value = _value_of(expr, env)
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > _FLOAT_MAX_INT:
        raise NotConstant("number outside the float range")
    return value


_FLOAT_MAX_INT = int(sys.float_info.max)


def _value_of(expr: S.Expr | None, env: Env) -> S.ConstValue:
    if expr is None:
        raise NotConstant("empty expression")
    if isinstance(expr, S.Number):
        return expr.value
    if isinstance(expr, S.String):
        return expr.value
    if isinstance(expr, S.Name):
        return _lookup(expr.name, env)
    if isinstance(expr, S.Unary):
        v = value_of(expr.operand, env)
        if expr.op == "-":
            return -_num(v)
        if expr.op == "+":
            return _num(v)
        if expr.op == "NOT":
            return _truth(not _bool(v))
        raise NotConstant(f"unsupported unary operator {expr.op!r}")
    if isinstance(expr, S.Binary):
        left, right = value_of(expr.left, env), value_of(expr.right, env)
        try:
            return _binary(expr.op, left, right)
        except (ZeroDivisionError, OverflowError) as exc:
            # not foldable; only `/` is modelled (the real compiler reports it where an instruction
            # argument is folded, `checker/expressions.py` `check_splitstr_whole_array`)
            err = (
                DivideByZero(f"{expr.op!r} cannot be folded: {exc}")
                if expr.op == "/" and isinstance(exc, ZeroDivisionError)
                else NotConstant(f"{expr.op!r}: {exc}")
            )
            if isinstance(err, DivideByZero):
                err.numerator = left
            raise err from exc
    if isinstance(expr, S.Index):
        fn = _BUILTINS.get(expr.name.upper())
        if fn is None:
            raise NotConstant(f"{expr.name} is not a constant-foldable function")
        if len(expr.args) != 1:
            raise NotConstant(f"{expr.name} takes one argument")
        args = [value_of(a, env) for a in expr.args]
        return fn(args)
    raise NotConstant(f"{type(expr).__name__} has no constant value")


def is_float(expr: S.Expr | None, env: Env) -> bool | None:
    """Whether a constant expression is float-typed (a decimal literal, `Const` of one, or any
    `+ - * \\ MOD` with a float operand; `/`, `^` and `Int()` are always float, `Abs` keeps its
    operand's type -- oracle, CR6, 2026-10-10, SDM address probes). `None`: not established."""
    if isinstance(expr, S.Number):
        return isinstance(expr.value, float)
    if isinstance(expr, S.Name):
        upper = expr.name.upper()
        if upper in env.consts:
            v = env.consts[upper]
            return True if isinstance(v, CFloat) else False if isinstance(v, int) else None
        sym = env.profile.symbols.get(upper)
        return None if sym is None else isinstance(sym["value"], float)
    if isinstance(expr, S.Unary) and expr.op in ("-", "+"):
        return is_float(expr.operand, env)
    if isinstance(expr, S.Binary) and expr.op in ("/", "^"):
        return True
    if isinstance(expr, S.Binary) and expr.op in ("+", "-", "*", "\\", "MOD"):
        sides = [is_float(expr.left, env), is_float(expr.right, env)]
        return True if True in sides else None if None in sides else False
    if isinstance(expr, S.Index) and expr.name.upper() == "INT":
        return True
    if isinstance(expr, S.Index) and expr.name.upper() == "ABS" and len(expr.args) == 1:
        return is_float(expr.args[0], env)
    return None


def truthy(v: object) -> bool:
    """CRBasic truthiness of an already-evaluated constant (0 / 0.0 / "" are false)."""
    if isinstance(v, str):
        return v != ""
    return _num(v) != 0


def is_constant(expr: S.Expr | None, env: Env) -> bool:
    try:
        value_of(expr, env)
        return True
    except NotConstant:
        return False


def _lookup(name: str, env: Env) -> S.ConstValue:
    upper = name.upper()
    if upper == "SECTION" and env.section is not None:
        return env.section
    if upper in env.consts:
        return env.consts[upper]
    sym = env.profile.symbols.get(upper)
    if sym is not None:
        return sym["value"]
    raise NotConstant(f"{name} is not a constant")


def _as_str(v: object) -> str:
    if isinstance(v, str):
        return v
    n = _num(v)
    return str(int(n)) if n == int(n) else str(n)


def _binary(op: str, left: S.ConstValue, right: S.ConstValue) -> S.ConstValue:
    if op == "TO":  # `Scan(1 To 2,..)` is interval 0, `PortGet(X,5 To 2)` port 0 (oracle, round 9).
        # A string left operand is accepted too: `Const K = "a" To 3` gives K=0; a string right is not
        _num(right)
        return 0
    if op == "&":
        return _as_str(left) + _as_str(right)
    if op == "+":
        if isinstance(left, str) or isinstance(right, str):
            return _as_str(left) + _as_str(right)
        return _num(left) + _num(right)
    if op == "-":
        return _num(left) - _num(right)
    if op == "*":
        return _num(left) * _num(right)
    if op == "/":
        return _num(left) / _num(right)
    if op == "\\":
        return float(int(_num(left)) // int(_num(right)))
    if op == "^":
        power = _num(left) ** _num(right)
        if isinstance(power, complex):
            raise NotConstant("a negative base to a fractional power is not folded")
        return power
    if op == "MOD":
        return float(int(_num(left)) % int(_num(right)))
    if op == "<<":
        return float(int(_num(left)) << int(_num(right)))
    if op == ">>":
        return float(int(_num(left)) >> int(_num(right)))
    if op == "AND":
        return float(int(_num(left)) & int(_num(right)))
    if op == "OR":
        return float(int(_num(left)) | int(_num(right)))
    if op == "XOR":
        return float(int(_num(left)) ^ int(_num(right)))
    if op == "EQV":
        return _truth(_bool(left) == _bool(right))
    if op == "IMP":
        return _truth((not _bool(left)) or _bool(right))
    if op in ("=", "<>", "<", ">", "<=", ">="):
        if isinstance(left, str) or isinstance(right, str):
            return _truth(_compare(op, _as_str(left), _as_str(right)))
        return _truth(_compare(op, _num(left), _num(right)))
    raise NotConstant(f"unsupported operator {op!r}")


def _compare[T: (str, float)](op: str, left: T, right: T) -> bool:
    return {
        "=": left == right,
        "<>": left != right,
        "<": left < right,
        ">": left > right,
        "<=": left <= right,
        ">=": left >= right,
    }[op]
