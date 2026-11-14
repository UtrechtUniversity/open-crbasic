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

import math
import re
import struct

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import context, instr_rules

_FUNCTIONS = frozenset({"TIMEINTOINTERVAL", "IFTIME"})
# Functions whose integer-typed constant arguments draw `Parameter must be integral.`, with the
# argument positions checked (`docs/checker.md` "Integral parameters").
_INTEGRAL_ARGS = {"TIMEINTOINTERVAL": (0, 1, 2), "IFTIME": (0, 1, 2), "SERIALOPEN": (1,), "TIMER": (0,)}
_MONTH = 6
# The trailing `TimeOut` of `HTTPPut` (9th argument) and `FTPClient` (11th): see `timeout_gate`.
_TIMEOUT_ARG = {"HTTPPUT": 8, "FTPCLIENT": 10}
_MODE_RE = re.compile(r"(?i)\b(IfTime|TimeIntoInterval|Timer|SerialOpen|HTTPPut|FTPClient)\b\s*\(")
_LITERAL_RE = re.compile(r"\"[^\"]*\"|'.*$")
NOT_INTEGRAL = "Parameter must be integral."
_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1
_F32_MAX = 3.4028234663852886e38


def gate_text(stmt) -> str | None:
    """The text `timeout_gate` reads for `stmt` in a flattened (`_with_inline`) statement list: a
    one-line `If` gives only its condition, its bodies follow as statements of their own, so the
    mode advances once per source occurrence (oracle, round 19, all four loggers)."""
    text = getattr(stmt, "text", None)
    if isinstance(stmt, S.If) and stmt.inline is not None and text:
        m = re.search(r"(?i)\bThen\b", _LITERAL_RE.sub('""', text))
        return _LITERAL_RE.sub('""', text)[: m.start()] if m else text
    return text


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
    from . import expressions, instr_data

    wide = wide_constants(program, profile)
    known = context.of(program, profile).known_names
    positions = {id(st): i for i, st in enumerate(program.statements)}
    in_table = blocked_table = False
    scan: tuple[int, int] | None = None
    seen = False
    for stmt in program.statements:
        gate, seen = timeout_gate(seen, getattr(stmt, "text", None))
        _check_timeout_value(stmt, diags, env, profile, wide, program)
        if gate:
            _check_timeout(stmt, diags, env, profile, wide)
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        if isinstance(stmt, S.Instruction) and (kw == "DATATABLE"):
            in_table = True
            blocked_table = any(expressions._undefined_names(a, known) for a in stmt.args[1:])
        elif kw == "ENDTABLE":
            in_table = blocked_table = False
        if isinstance(stmt, S.Instruction) and (kw == "SCAN"):
            scan = _scan_step(stmt, env)
            continue
        if kw in ("NEXTSCAN", "SUBSCAN", "NEXTSUBSCAN") or isinstance(stmt, S.SubDef):
            scan = None
        if not profile.single_pass:
            for call in _calls(stmt, {"FTPCLIENT"}):
                if (option := _put_get_option(call, env)) is not None:
                    diags.error(stmt.line, f"Invalid PutGetOption: {option}", stmt.file)
        for call in _calls(stmt, _INTEGRAL_ARGS):
            if isinstance(call, S.Call) and call.explicit:
                continue
            if call.name.upper() == "TIMER":
                owner, before = _timer_context(stmt, call, known, profile.single_pass)
                table_only = (
                    isinstance(owner, S.Instruction) and owner.keyword.upper() in instr_data.TABLE_ONLY_INSTRUCTIONS
                )
                if (
                    before
                    or blocked_table
                    or (table_only and not in_table)
                    or (not profile.single_pass and _timer_shadowed(program, stmt, positions, diags))
                ):
                    continue
            bad = [
                i
                for i in _INTEGRAL_ARGS[call.name.upper()][: len(call.args)]
                if not_integral(call.args[i], env, profile, wide) or _nonfinite_literal(call.args[i], env)
            ]
            for _ in bad:
                diags.error(stmt.line, NOT_INTEGRAL, stmt.file)
            if not bad and call.name.upper() in _FUNCTIONS:
                _check_call(call, scan, stmt, diags, env, any(d.line < stmt.line for d in diags.errors))


def _put_get_option(call, env: consteval.Env) -> int | None:
    """CR6/CR1000X: an `FTPClient` constant PutGetOption outside -29..29 after truncation, as the
    Long the message prints (above the Long range -2147483648, above float32 2147483647; below the
    Long range it compiles); None when valid or not a numeric constant (oracle, rounds 65 and 67,
    docs/instructions.md#ftpclient-putgetoption)."""
    if len(call.args) < 6:
        return None
    try:
        value = consteval.value_of(call.args[5], env)
    except consteval.NotConstant:
        return None
    if (
        isinstance(value, (bool, str))
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
    ):
        return None  # (a huge integer literal is a Python int: `math.isfinite` would overflow)
    if value < _INT_MIN:
        return None  # below the Long range: compiles (`-2147483649`, `-1e39`; oracle, round 67)
    if value > _F32_MAX:
        option = _INT_MAX  # beyond float32 (+inf): 2147483647 (oracle, round 67)
    else:
        option = int(value) if value <= _INT_MAX else _INT_MIN
    return option if abs(option) >= 30 else None


def _timer_context(stmt, call, known, single_pass):
    """Owner and an earlier unresolved name in the expression that contains this call."""
    from . import expressions, symbols

    if stmt is call:
        return stmt, False
    before = False
    for expr in expressions._stmt_exprs(stmt):
        if not (single_pass and isinstance(stmt, S.Call)):
            before = False
        for node in symbols._name_nodes(expr):
            if node is call:
                return stmt, before
            before |= node.name.upper() not in known or id(node) in getattr(known, "out_of_scope", ())
    for inner in (getattr(stmt, "inline", None) or []) + (getattr(stmt, "inline_else", None) or []):
        if any(c is call for c in _calls(inner, {"TIMER"})):
            return _timer_context(inner, call, known, single_pass)
    return stmt, False


def _timer_shadowed(program, stmt, positions, diags):
    """CR6/CR1000X resolve earlier visible storage named Timer before the built-in."""

    for decl in context.of(program).scoped_view(stmt).statements:
        if positions[id(decl)] > positions[id(stmt)]:
            continue
        if isinstance(decl, S.SubDef) and any(p.name.upper() == "TIMER" for p in decl.params):
            return True
        if isinstance(decl, S.Declaration) and decl.kind == "DIM" and any(v.name.upper() == "TIMER" for v in decl.vars):
            return True
        if (
            isinstance(decl, S.Instruction)
            and decl.keyword.upper() == "ALIAS"
            and not any(d.line == decl.line and d.file == decl.file for d in diags.errors)
            and any(isinstance(a, (S.Name, S.Index)) and a.name.upper() == "TIMER" for a in decl.args[1:])
        ):
            return True
    return False


def _nonfinite_literal(expr: S.Expr | None, env) -> bool:
    # NAN/INF are expression literals outside consteval's symbol table. The integral-argument
    # resolver reads them as such even beside aliases or parameters of that name (round 46).
    while isinstance(expr, S.Unary) and expr.op in ("+", "-"):
        expr = expr.operand
    return isinstance(expr, S.Name) and expr.name.upper() in ("NAN", "INF") and expr.name.upper() not in env.consts


def _check_timeout_value(stmt: S.Stmt, diags: Diagnostics, env, profile, wide, program) -> None:
    """A constant integer `TimeOut` whose 32-bit product `10 * TimeOut` is negative is `Parameter
    TimeOut cannot be negative` on all four loggers, gated or not (oracle, round 19, probes
    `scratch/r19/W8`). Only the forms in `timeout_value` are modelled. CR6/CR1000X: no such line when
    another argument of the same call is an undefined name (`Invalid symbol in expression` alone).
    CR300/CR350: the line follows the same-line `... is not defined.` lines (`order_timeout_negative`).
    After an already reported error arithmetic is not folded: `fold_timeout_expressions`."""
    for call in _calls(stmt, _TIMEOUT_ARG):
        if _negative_timeout(call, env, profile, wide, program):
            diags.error(stmt.line, _TIMEOUT_NEGATIVE, stmt.file, stmt=stmt)


def _negative_timeout(call, env, profile, wide, program) -> bool:
    """Whether `call` draws `Parameter TimeOut cannot be negative` when nothing was reported before."""
    if isinstance(call, S.Call) and call.explicit:
        return False
    i = _TIMEOUT_ARG[call.name.upper()]
    if i >= len(call.args) or call.args[i] is None:
        return False
    value = timeout_value(call.args[i], env, profile, wide)
    if value is None or value[0] != "int" or _signed32(10 * value[1]) >= 0:
        return False
    return profile.single_pass or not _undefined_sibling(call, i, program, profile)


def _undefined_sibling(call: S.Index, i: int, program: S.Program, profile) -> bool:
    """Whether another argument of `call` (not position `i`) names an undefined symbol."""
    from . import expressions  # lazy: `expressions` imports this module's package

    known = context.of(program, profile).known_names
    return any(a is not None and expressions._undefined_names(a, known) for j, a in enumerate(call.args) if j != i)


def _needs_integer_fold(arg) -> bool:
    """Arithmetic needs the parser's error-free fold; a unary plus alone does not.

    See `docs/instructions.md`, round 23. The value classifier bounds the supported operators
    and operands; runtime expressions and unobserved forms are left to their existing checks.
    """
    while isinstance(arg, S.Unary) and arg.op in ("-", "+"):
        if arg.op == "-":
            return True
        arg = arg.operand
    return isinstance(arg, S.Binary) and arg.op in ("+", "-", "*")


def fold_timeout_expressions(program: S.Program, profile, diags: Diagnostics, env, reported) -> None:
    """The compiler folds integer arithmetic only while its reported-error count is zero (warnings
    do not count, `:` and newlines do not reset it). After any reported error, a supported arithmetic
    TimeOut is `Parameter must be a constant integer: <statement>.` instead of the sign check,
    whatever its value; in the gated state (`timeout_gate`) it is silent. Oracle rounds 21 and 23,
    fixtures `r21_negfold.json` and `r23_timeout_expressions.json`. `program` is the `_with_inline`
    list. What counts as reported
    before the call: an error on an earlier line, an error of an earlier statement of the same line
    (any other error of the line when the call has an earlier segment), a previous `TimeOut` finding of
    this rule, and on CR300/CR350 an undefined name in an earlier argument of the call. CR6/CR1000X
    count only `reported`, the pass-1 errors (a name left to pass 2 does not count). A converted
    finding goes after the other errors of its line.
    """
    from . import expressions  # lazy: `expressions` imports this module's package

    stmts = program.statements
    neg_lines: dict = {}
    for d in diags.items:
        if d.severity == "error" and d.message == _TIMEOUT_NEGATIVE:
            neg_lines.setdefault((d.file, d.line), []).append(d)
    wide = wide_constants(program, profile)
    lines: dict = {}
    first: dict = {}
    for i, st in enumerate(stmts):
        lines.setdefault(st.file or "", []).append((st.line, i))
        first.setdefault((st.file or "", st.line), i)
    for v in lines.values():
        v.sort()
    import bisect

    def position(d) -> int:
        entries = lines.get(d.file or "")
        if not entries:
            return -1
        k = bisect.bisect_left(entries, (d.line, -1))
        return entries[k][1] if k < len(entries) else len(stmts)

    known = None
    seen = False
    family_error = False
    added: list = []
    for k, stmt in enumerate(stmts):
        gate, seen = timeout_gate(seen, gate_text(stmt))
        inline = {
            id(c)
            for inner in (getattr(stmt, "inline", None) or []) + (getattr(stmt, "inline_else", None) or [])
            for c in _calls(inner, _TIMEOUT_ARG)
        }
        for call in _calls(stmt, _TIMEOUT_ARG):
            if id(call) in inline or (isinstance(call, S.Call) and call.explicit):
                continue
            i = _TIMEOUT_ARG[call.name.upper()]
            if i >= len(call.args) or call.args[i] is None:
                continue
            arg = call.args[i]
            key = (stmt.file, stmt.line)
            own = None
            if _negative_timeout(call, env, profile, wide, program):
                own = (neg_lines.get(key) or [None]).pop(0)
            value = timeout_value(arg, env, profile, wide)
            if _needs_integer_fold(arg) and value is not None and value[0] == "int":
                line_first = first[(stmt.file or "", stmt.line)]
                counted = [
                    d
                    for d in diags.items
                    if d.severity == "error"
                    and d.line
                    and d.message != _TIMEOUT_NEGATIVE
                    and not any(d is a for a in added)
                    and (profile.single_pass or any(d is r for r in reported))
                ]
                before = (
                    family_error
                    or any(position(d) < line_first for d in counted)
                    or (k > line_first and any(position(d) == line_first for d in counted))
                )
                if not before and profile.single_pass:
                    known = known or context.of(program, profile).known_names
                    before = any(a is not None and expressions._undefined_names(a, known) for a in call.args[:i])
                if before and gate is not None:
                    if own is not None:
                        diags.items.remove(own)
                    if not gate:
                        diags.error(
                            stmt.line,
                            expressions._arity_message(
                                "constint", stmt.text, profile, stmt.at_eol, whole_statement=True
                            ),
                            stmt.file,
                        )
                        added.append(diags.items[-1])
                        family_error = True
                    continue
            if own is not None:
                family_error = True


def order_timeout_negative(diags: Diagnostics, profile, program: S.Program) -> None:
    """CR300/CR350 only: a `Parameter TimeOut cannot be negative` line is printed after the
    `... is not defined.` lines of its own call and of the earlier statements of its line, before
    those of later ones (oracle, round 19: `HTTPPut(...,NoSuchVar,8,214748365)` gives `NoSuchVar is
    not defined.` then the TimeOut line; round 21: `HTTPPut(...,-1) : Result(4)=Nope+1` the other way
    round). Moved after the last such line."""
    if not profile.single_pass:
        return
    items = diags.items
    for d in [d for d in items if d.severity == "error" and d.message == _TIMEOUT_NEGATIVE]:
        at = next(i for i, o in enumerate(items) if o is d)
        same = [st for st in program.statements if (st.file, st.line) == (d.file, d.line)]
        mine = next((n for n, st in enumerate(same) if id(st) == d.stmt), len(same))
        after = []
        for j, o in enumerate(items):
            if (
                o is d
                or o.severity != "error"
                or (o.file, o.line) != (d.file, d.line)
                or not o.message.endswith(" is not defined.")
            ):
                continue
            name = re.compile(r"(?i)\b" + re.escape(o.message[: -len(" is not defined.")]) + r"\b")
            owner = next((n for n, st in enumerate(same) if name.search(st.text or "")), 0)
            if owner <= mine:
                after.append(j)
        if after and max(after) > at:
            items.pop(at)
            items.insert(max(after), d)


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
        elif (
            (isinstance(arg, S.Name) and arg.name.upper() == "NAN")
            or (not_integral(arg, env, profile, wide) and (not profile.single_pass or _float_typed(arg, env, wide)))
            or (timeout_value(arg, env, profile, wide) == ("wide", 0))
        ):
            diags.error(stmt.line, NOT_INTEGRAL, stmt.file)


def wide_constants(program: S.Program, profile) -> frozenset[str]:
    """`Const` names declared as one positive numeric literal beyond the converted range: they
    carry the literal's float type into every expression that uses them (oracle, 2026-10-02:
    `Const KB = 999999999999` -> `KB`, `-KB` and `KB-1` all draw the error, while the computed
    `Const KC = 2147483647+1` and `Const KM = -2147483649` wrap silently)."""
    return frozenset(
        st.name.upper()
        for st in program.statements
        if isinstance(st, S.ConstDecl)
        and isinstance(st.value, S.Number)
        and not st.value.text.startswith("&")
        and _out_of_range(st.value.value, profile)
    )


def _out_of_range(value, profile) -> bool:
    if profile.name in ("cr300", "cr350"):
        rounded = struct.unpack("<f", struct.pack("<f", float(value)))[0] if abs(value) < 3.4e38 else float("inf")
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
        return expr.op in ("+", "-", "*", "/", "^", "\\", "MOD") and _plain(expr.left) and _plain(expr.right)
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
        return expr.op in ("/", "^") or _float_typed(expr.left, env, wide) or _float_typed(expr.right, env, wide)
    return False


_TIMEOUT_NEGATIVE = "Parameter TimeOut cannot be negative"


def _signed32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 2**32 if x >= 2**31 else x


def timeout_value(arg, env, profile, wide=frozenset()):
    """Classify a constant `TimeOut` argument: `("wide", 0)` when it is float-typed (a float literal,
    or a decimal integer literal past the logger's 32-bit range, see `_decimal`), `("int", v)` for an
    integer constant whose 32-bit value is `v`, or `None` when not modelled. Integer `+`, `-`,
    `*` and unary signs on named constants are folded with 32-bit wrapping; other operators and
    out-of-range `Const` values on CR6/CR1000X remain outside this classifier.
    """
    if isinstance(arg, S.Unary) and arg.op in ("-", "+"):
        signs, lit = [], arg
        while isinstance(lit, S.Unary) and lit.op in ("-", "+"):
            signs.append(lit.op)
            lit = lit.operand
        sign = -1 if signs.count("-") % 2 else 1
        if isinstance(lit, S.Number):
            return _timeout_literal(lit, sign, profile)
        value = timeout_value(lit, env, profile, wide)
        if value is not None and value[0] == "int":
            return ("int", _signed32(sign * value[1]))
        return value
    if isinstance(arg, S.Binary) and arg.op in ("+", "-", "*"):
        left = timeout_value(arg.left, env, profile, wide)
        right = timeout_value(arg.right, env, profile, wide)
        if left is None or right is None or left[0] != "int" or right[0] != "int":
            return None
        a, b = left[1], right[1]
        value = a + b if arg.op == "+" else a - b if arg.op == "-" else a * b
        return ("int", _signed32(value))
    if isinstance(arg, S.Number):
        return _timeout_literal(arg, 1, profile)
    if isinstance(arg, S.Name):
        name = arg.name.upper()
        if name in ("TRUE", "FALSE"):
            return ("int", -1 if name == "TRUE" else 0)
        if name in wide:
            return ("wide", 0)
        if env is None:
            return None
        try:
            value = consteval.value_of(arg, env)
        except consteval.NotConstant:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if _INT_MIN <= value <= _INT_MAX:
            return ("int", value)
        return _decimal(abs(value), -1 if value < 0 else 1, profile, literal=False)
    return None


def _timeout_literal(lit: S.Number, sign: int, profile):
    text = lit.text.upper()
    if text.startswith(("&H", "&B")):
        digits = text[2:]
        base, limit, alphabet = (16, 8, "0123456789ABCDEF") if text[1] == "H" else (2, 32, "01")
        if not digits or len(digits) > limit or any(c not in alphabet for c in digits):
            return None
        return ("int", _signed32(sign * _signed32(int(digits, base))))
    if text.startswith("&"):
        return None
    if "." in text or "E" in text:
        return ("wide", 0)  # a float literal
    if not text.isdigit():
        return None
    return _decimal(int(text), sign, profile, literal=True)


def _decimal(magnitude: int, sign: int, profile, literal: bool):
    """A decimal magnitude with its sign. CR300/CR350 (single pass): the float32-rounded magnitude
    past 2147483648 is a float, otherwise the digits saturate at 2147483647 before the sign.
    CR6/CR1000X: past 2147483647 is a float literal (except the exact literal -2147483648, integer
    INT_MIN), while an out-of-range `Const` value is not modelled (`None`)."""
    if profile.single_pass:
        if magnitude >= 2**32:
            return ("wide", 0)
        rounded = struct.unpack("<f", struct.pack("<f", float(magnitude)))[0]
        if rounded > 2**31:
            return ("wide", 0)
        return ("int", sign * min(magnitude, _INT_MAX))
    if magnitude > _INT_MAX:
        if not literal:
            return None
        if sign < 0 and magnitude == 2**31:
            return ("int", _INT_MIN)
        return ("wide", 0)
    return ("int", sign * magnitude)


def _check_call(call: S.Index | S.Call, scan, stmt: S.Stmt, diags: Diagnostics, env, after_error: bool = False) -> None:
    values = [v for a in call.args[:3] if (v := _int(a, env)) is not None]
    if len(call.args) != 3 or len(values) != 3 or values[2] == _MONTH or _INT_MIN in values[:2]:
        return
    if after_error and min(values[:2]) < 0:
        return  # a negative argument is only reported while the file is still clean
    tinto_raw, interval_raw, unit = values
    # an unknown unit scales both to 0, so the check always fails (oracle, round 55)
    per_unit = instr_rules._US_PER_UNIT.get(unit, 0)
    t_sec, t_us = _split(tinto_raw * per_unit)
    i_sec, i_us = _split(interval_raw * per_unit)
    if t_us < 0:
        t_sec, t_us = i_sec - 1 + t_sec, t_us + 1_000_000
    elif t_sec < 0:
        t_sec += i_sec
    if i_sec < 0 or i_us < 0 or t_sec < 0 or i_sec < t_sec or (t_sec == i_sec and i_us <= t_us):
        diags.error(
            stmt.line,
            f"Interval ({interval_raw}) must be > 0 and TimeInto Interval ({tinto_raw}) must be < Interval.",
            stmt.file,
        )
    if scan is None:
        return
    if i_sec < 3601:
        interval, tinto = i_sec * 10000 + _cdiv(i_us, 100), t_sec * 10000 + _cdiv(t_us, 100)
        step = _wrap32(scan[0] * 10000 + _cdiv(scan[1], 100))
    else:
        interval, tinto, step = i_sec, t_sec, scan[0]
        if i_us or t_us:
            diags.error(
                stmt.line, "Illegal sub seconds in time into and/or interval when interval > 3600 secs.", stmt.file
            )
    if step and (_cmod(interval, step) or _cmod(tinto, step)):
        diags.error(stmt.line, "Scan interval must be integrally divisible into interval.", stmt.file)


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
        elif isinstance(expr, S.Unary) and expr.operand is not None:
            stack.append(expr.operand)
        elif isinstance(expr, S.Binary):
            stack += [expr.left, expr.right]
        elif isinstance(expr, S.Member):
            stack += [a for a in (expr.args or []) if a is not None]
