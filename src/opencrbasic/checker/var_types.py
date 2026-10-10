"""Variable-data-type argument checks (pass 2, tier C). Rules, fixtures and oracle evidence:
`docs/instructions.md` "Variable data types" (`tests/fixtures/var_types.json`).

Not a uniform per-type-code rule: each instruction applies its own requirement, so this module is
a small table of them. Unknown names, functions and operand mixes the oracle was not probed on
have no type (`None`), and a check that needs a type it does not have stays silent.
"""

from __future__ import annotations

import math
import re

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import symbols

_NUMERIC = ("FLOAT", "LONG")


def _types(program: S.Program) -> tuple[dict[str, str], dict[str, str]]:
    """Declared variable types and `Alias` name -> target variable name (upper case)."""
    types = symbols.declared_variable_types(program)
    alias: dict[str, str] = {}
    for st in program.statements:
        if isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS" and st.args and \
                isinstance(st.args[0], (S.Name, S.Index)):
            for a in st.args[1:]:
                if isinstance(a, S.Name):
                    alias[a.name.upper()] = st.args[0].name.upper()
    return types, alias


class _Typer:
    def __init__(self, program: S.Program):
        self.types, self.alias = _types(program)
        self.consts = {st.name.upper(): st for st in program.statements
                       if isinstance(st, S.ConstDecl)}

    def base(self, name: str) -> str:
        return self.alias.get(name.upper(), name.upper())

    def variable(self, expr: S.Expr | None) -> str | None:
        """Upper-case declared-variable name an expression refers to, or None."""
        if isinstance(expr, (S.Name, S.Index)):
            name = self.base(expr.name)
            return name if name in self.types else None
        return None

    def of(self, expr: S.Expr | None, depth: int = 0) -> str | None:
        """FLOAT / LONG / STRING / BOOLEAN / DOUBLE, or None when not known."""
        if isinstance(expr, S.Number):
            text = expr.text.upper()
            if text.startswith("&"):
                return "LONG"
            return "FLOAT" if "." in text or "E" in text else "LONG"
        if isinstance(expr, S.String):
            return "STRING"
        var = self.variable(expr)
        if var is not None:
            return self.types[var]
        if isinstance(expr, S.Name):
            if expr.name.upper() in ("TRUE", "FALSE"):
                return "LONG"
            if expr.name.upper() == "NAN":
                return "FLOAT"
            const = self.consts.get(expr.name.upper())
            if const is None or depth > 8:
                return None
            if const.type:
                return const.type.upper()
            return self.of(const.value, depth + 1)
        if isinstance(expr, S.Unary) and expr.op in ("-", "+"):
            return self.of(expr.operand, depth + 1) if \
                self.of(expr.operand, depth + 1) in _NUMERIC else None
        if isinstance(expr, S.Binary) and expr.op in ("+", "-", "*", "/"):
            left, right = self.of(expr.left, depth + 1), self.of(expr.right, depth + 1)
            if left == right == "STRING" and expr.op == "+":
                return "STRING"
            if left in _NUMERIC and right in _NUMERIC:
                return "LONG" if left == right == "LONG" and expr.op != "/" else "FLOAT"
        return None


# SNMPVariable: Type keywords a declared variable type may not be given (oracle, CR6/CR1000X).
# A Float variable takes only Float, a String one String or Float, the rest anything but String.
_SNMP_TYPES = {"INTEGER", "COUNTER", "STRING", "TIMETICKS", "OPAQUE", "GAUGE", "FLOAT"}
_SNMP_RE = re.compile(r"\s*SNMPVARIABLE\s*\(\s*(\w+)\s*,\s*[\d.]+\s*,\s*(\w+)", re.I)
_SNMP_ALLOWED = {"FLOAT": {"FLOAT"}, "STRING": {"STRING", "FLOAT"}}


# Operators that reject a String operand (oracle, all four loggers): the logical and shift
# operators with one shared message, `* / MOD` with one message per String side.
_STRING_ILLEGAL_OPS = {"AND", "OR", "XOR", "IMP", "EQV", "<<", ">>"}
_STRING_ARITH_OPS = {"*", "/", "MOD"}


def _subexpressions(expr: S.Expr | None):
    if expr is None:
        return
    yield expr
    if isinstance(expr, S.Binary):
        yield from _subexpressions(expr.left)
        yield from _subexpressions(expr.right)
    elif isinstance(expr, S.Unary):
        yield from _subexpressions(expr.operand)
    elif isinstance(expr, (S.Index, S.Member)):
        for a in expr.args or []:
            yield from _subexpressions(a)
    elif isinstance(expr, S.ArrayLiteral):
        for a in expr.items:
            yield from _subexpressions(a)


def _statement_expressions(stmt: S.Stmt):
    if isinstance(stmt, S.Assignment):
        yield stmt.value
    elif isinstance(stmt, (S.Instruction, S.Call)):
        yield from stmt.args
    elif isinstance(stmt, (S.If, S.ElseIf)):
        yield stmt.cond
        for inner in (getattr(stmt, "inline", None) or []) + (getattr(stmt, "inline_else", None) or []):
            yield from _statement_expressions(inner)


def _string_operand_messages(typer: "_Typer", stmt: S.Stmt, every: bool = False) -> list[str]:
    """The messages for the one String-operand error a statement holds; none when it holds
    several (their number and order is not established), except `every`: several
    `String is illegal here`, one each (oracle, 2026-10-10: `"a" and "b" and "c" .`)."""
    found: list[list[str]] = []
    for top in _statement_expressions(stmt):
        for node in _subexpressions(top):
            if not isinstance(node, S.Binary):
                continue
            left, right = typer.of(node.left) == "STRING", typer.of(node.right) == "STRING"
            if node.op == "TO":  # a String on the right only (`S To 3` compiles); round 9
                if right:
                    found.append(["String is illegal here"])
            elif node.op in _STRING_ILLEGAL_OPS and (left or right):
                found.append(["String is illegal here"])
            elif node.op in _STRING_ARITH_OPS and (left or right):
                found.append((["Illegal String operand"] if left else []) +
                             (["Illegal operand of type STRING"] if right else []))
    if every and found and all(f == ["String is illegal here"] for f in found):
        return [f[0] for f in found]
    return found[0] if len(found) == 1 else []


def _snmp_mismatch(typer: "_Typer", name: S.Expr | None, snmp_type: str) -> bool:
    var = typer.variable(name)
    return var is not None and snmp_type.upper() in _SNMP_TYPES and \
        snmp_type.upper() not in _SNMP_ALLOWED.get(typer.types[var], _SNMP_TYPES - {"STRING"})


def _same_type(dest: str, source: str) -> bool:
    return dest == source or (dest == "STRING" and source == "FLOAT")


def _indexed_past_first(typer: "_Typer", dest: S.Expr) -> bool:
    """`RoutersNeighbors` takes the whole array: a subscript that is a variable, a binary
    expression or a literal of 2 or more is `Destination variable cannot be indexed` (oracle,
    2026-10-08: `L(1)`, `L(1.5)`, `L(-1)` and `(1,1)` compile, `L(0)` adds an out-of-bounds line
    and is not modelled). A subscript that is a `Const` name, or a parenthesised literal, is not
    probed (the parser drops the parentheses, so `(1)` reads as `1`)."""
    if not isinstance(dest, S.Index):
        return False
    for a in dest.args:
        if isinstance(a, S.Binary) or (isinstance(a, S.Name) and typer.variable(a) is not None) \
                or (isinstance(a, S.Number) and a.value >= 2):
            return True
    return False


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    typer = _Typer(program)
    dims = None
    for stmt in program.statements:
        for text in _string_operand_messages(typer, stmt):
            diags.error(stmt.line, text, stmt.file)
        if isinstance(stmt, S.Unparsed):  # a dotted OID is not an expression
            m = _SNMP_RE.match(stmt.text)
            if m and "SNMPVARIABLE" in profile.instructions and \
                    _snmp_mismatch(typer, S.Name(stmt.line, name=m.group(1)), m.group(2)):
                diags.error(stmt.line, "Miss match of types", stmt.file)
            continue
        if not isinstance(stmt, S.Instruction):
            continue
        kw, args = stmt.keyword.upper(), stmt.args
        if kw not in profile.instructions:
            continue
        message = None
        extra = None
        if kw == "ROUTERSNEIGHBORS" and args:
            kind = "STRING" if isinstance(args[0], S.String) else typer.of(args[0])
            if typer.variable(args[0]) is not None or isinstance(args[0], S.String):
                if kind != "LONG":
                    message = "Destination variable must be type LONG"
                elif _indexed_past_first(typer, args[0]):
                    message = "Destination variable cannot be indexed"
        elif kw == "ARGOSERROR" and args:
            if typer.variable(args[0]) is not None and typer.of(args[0]) not in (None, "STRING"):
                message = "A string variable destination is required"
        elif kw == "SORTSPAINDEXED" and len(args) > 2:
            dest, source = typer.of(args[0]), typer.of(args[2])
            if typer.variable(args[0]) is not None and dest and source and \
                    not _same_type(dest, source):
                message = "Source and Dest types must be the same."
        elif kw == "SAMPLE" and len(args) > 2 and isinstance(args[2], S.Name) and \
                args[2].name.upper() == "NSEC":
            source = typer.of(args[1])
            if source in ("STRING", "BOOLEAN", "DOUBLE"):
                message = "Source type must be either LONG or FLOAT or NSEC for data type NSEC"
            elif source == "FLOAT" and not any((d.line, d.file) == (stmt.line, stmt.file)
                                              for d in diags.errors):
                if dims is None:
                    dims = symbols.declared_all_dims(program, env)
                size = _array_size(typer, dims, args[1])
                try:
                    reps = consteval.value_of(args[0], env)
                except consteval.NotConstant:
                    reps = None
                if size is not None and isinstance(reps, int) and reps >= 1 and size < 7 * reps:
                    message = "Float to NSEC expects source array size >= 7"
        elif kw in ("SDMCD16MASK", "SDMCD16AC") and args:
            if kw == "SDMCD16MASK" and any(typer.types.get(typer.variable(a), "") == "BOOLEAN"
                                           for a in args[:2]):
                message = "Source and Mask type must be LONG."
            if isinstance(args[0], S.Number) or (isinstance(args[0], S.Name) and
                                                 args[0].name.upper() in typer.consts):
                extra = "Invalid Source."
        elif kw == "SNMPVARIABLE" and len(args) > 2 and isinstance(args[2], S.Name) and \
                _snmp_mismatch(typer, args[0], args[2].name):
            message = "Miss match of types"
        elif kw == "MENURECOMPILE" and len(args) > 1 and \
                isinstance(args[1], (S.Name, S.Index)) and typer.of(args[1]) == "STRING":
            message = "String type variable not allowed."
        elif kw == "SDMGENERIC" and len(args) > 6 and \
                typer.of(args[0]) == typer.of(args[4]) == "STRING" and \
                consteval.is_constant(args[6], env):
            try:
                bytes_per_value = consteval.value_of(args[6], env)
            except consteval.NotConstant:
                bytes_per_value = None
            if bytes_per_value != 1:
                message = "If source and destination are of type STRING, bytes per value must be 1"
        for text in (message, extra):
            if text:
                diags.error(stmt.line, text, stmt.file)


def _array_size(typer: _Typer, dims: dict[str, tuple[int, ...]], expr: S.Expr | None) -> int | None:
    """Element count of the variable behind `expr`; 1 for an expression that is no variable."""
    if isinstance(expr, (S.Name, S.Index)) and typer.variable(expr) is not None:
        shape = dims.get(typer.variable(expr))
        return None if shape is None else math.prod(shape)
    return 1


def _ctype_illegal(typer: "_Typer", node: S.Index) -> bool:
    """`CType(x)` (no type) or `CType(x,Boolean|UINT2|<Const>|<constant number expression>)`:
    `Illegal type`, a pass-1 finding. Other bad second arguments (a variable, an undefined name, a
    string) add further messages and are left alone."""
    args = node.args
    if None in args:
        return False
    if len(args) == 1:
        return True
    if len(args) != 2:
        return False
    kind = args[1]
    if isinstance(kind, S.Name):
        return kind.name.upper() in ("BOOLEAN", "UINT2") or kind.name.upper() in typer.consts
    if isinstance(kind, (S.Unary, S.Binary)):
        return typer.of(kind) in _NUMERIC and not any(
            isinstance(n, (S.Name, S.Index)) for n in _subexpressions(kind))
    return False


def check_ctype(program: S.Program, profile, diags: Diagnostics) -> None:
    if "CTYPE" not in profile.functions:
        return
    typer = _Typer(program)
    for stmt in program.statements:
        for top in _statement_expressions(stmt):
            for node in _subexpressions(top):
                if isinstance(node, S.Index) and node.name.upper() == "CTYPE" and \
                        _ctype_illegal(typer, node):
                    diags.error(stmt.line, "Illegal type", stmt.file)
