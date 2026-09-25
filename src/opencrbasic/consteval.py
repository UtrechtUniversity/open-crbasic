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

from __future__ import annotations

from dataclasses import dataclass, field

from . import syntax as S


class NotConstant(Exception):
    """Raised when an expression (or a name within it) has no compile-time value."""


@dataclass
class Env:
    """Constants known so far (definition order doesn't matter; last `define` wins) plus the
    logger profile's predefined symbols. `section` is the `Section` pseudo-value bound while
    walking a line that came from an `IncludeSection()` call (see `source.py`); referencing
    `Section` outside that context is an ordinary undefined-symbol error (verified with the
    oracle: `Invalid symbol in expression: Section = "Foo" .`).
    """

    profile: object
    consts: dict[str, object] = field(default_factory=dict)
    section: str | None = None

    def define(self, name: str, value: object) -> None:
        self.consts[name.upper()] = value

    def child(self, section: str | None) -> "Env":
        """A view with a different `Section` binding but the same (mutable, shared) consts."""
        return Env(self.profile, self.consts, section)


# Pure builtins usable in constant expressions (see mathematicalexpressions.htm / the CHM function
# pages for CHR/ABS/INT/SQR/LEN). Each takes already-evaluated argument values.
def _fn_chr(args):
    return chr(int(_num(args[0])))


def _fn_abs(args):
    return abs(_num(args[0]))


def _fn_int(args):
    v = _num(args[0])
    import math
    return float(math.floor(v))


def _fn_sqr(args):
    return _num(args[0]) ** 0.5


def _fn_len(args):
    v = args[0]
    if not isinstance(v, str):
        raise NotConstant("LEN() of a non-string constant")
    return len(v)


_BUILTINS = {"CHR": _fn_chr, "CHR$": _fn_chr, "ABS": _fn_abs, "INT": _fn_int, "SQR": _fn_sqr,
             "LEN": _fn_len}


def _num(v: object) -> float:
    if isinstance(v, bool):
        return -1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    raise NotConstant("expected a number")


def _bool(v: object) -> bool:
    return _num(v) != 0


def _truth(v: bool) -> int:
    return -1 if v else 0


def value_of(expr: S.Expr | None, env: Env) -> object:
    """The constant value of `expr` (int/float/str), or raise `NotConstant`."""
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
        return _binary(expr.op, value_of(expr.left, env), value_of(expr.right, env))
    if isinstance(expr, S.Index):
        fn = _BUILTINS.get(expr.name.upper())
        if fn is None:
            raise NotConstant(f"{expr.name} is not a constant-foldable function")
        args = [value_of(a, env) for a in expr.args]
        return fn(args)
    raise NotConstant(f"{type(expr).__name__} has no constant value")


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


def _lookup(name: str, env: Env) -> object:
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


def _binary(op: str, left: object, right: object) -> object:
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
        return _num(left) ** _num(right)
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
            l, r = _as_str(left), _as_str(right)
        else:
            l, r = _num(left), _num(right)
        result = {"=": l == r, "<>": l != r, "<": l < r, ">": l > r,
                  "<=": l <= r, ">=": l >= r}[op]
        return _truth(result)
    raise NotConstant(f"unsupported operator {op!r}")
