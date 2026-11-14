"""Shared AST name/expression walkers, declaration-kind tables, and name-collision checks.

Program-scoped symbol, dimension, and scope tables now live in `checker/context.py`.
Rules and oracle evidence: `docs/checker.md`.
"""

import re

from .. import syntax as S
from ..diagnostics import Diagnostics

# Instructions whose first argument is an identifier this checker treats as declaring a name a
# bare `Name` may later reference: a `DataTable`'s own name (referenced by `CallTable`/`Read`/...),
# or a `StructureType`/`ObjectType`'s own name (referenced as a `Sample`/`Average`/... `DataType`
# argument for a structured measurement, oracle-confirmed regression this session found,
# `structuretype_1.CR6`/`structuretype_2.CR6`: `Sample(3,Piezometer,VWSensor)` where `VWSensor` is
# the `StructureType` declared just above, not a fixed `data/symbols.json` `DataType` constant).
_NAME_DECLARING_INSTRUCTIONS = ("DATATABLE", "STRUCTURETYPE", "OBJECTTYPE", "CONSTTABLE")


# System tables always addressable as `Table.Field` (`Status.SerialNumber`, `Public.X`,
# `Settings.PakBusAddress`), oracle-confirmed accepted on all four loggers (CR6/CR1000X/CR300/
# CR350) with no program-shape precondition -- unlike `DataTableInfo` below, these three exist in
# every program regardless of whether any `DataTable`/`ConstTable`/`SlowSequence` is declared
# (`docs/checker.md`). Not sourced from any `data/<logger>/symbols.json` (that table is the
# compiler's *named-constant* table, `docs/type-codes.md`; a table name resolves through a
# different mechanism entirely, oracle-confirmed by `Status`/`Public`/`Settings` never appearing
# in any of the four extracted `symbols.json` files despite compiling cleanly).
_ALWAYS_KNOWN_SYSTEM_TABLES = frozenset({"STATUS", "PUBLIC", "SETTINGS"})


def _walk_names(expr: S.Expr | None):
    """Yields every `Name`/`Index`/`Member`-base's own bare name, in source order, from anywhere
    inside `expr` -- the same recursion shape `expressions._undefined_names` walks for a different
    purpose (undefined-name detection), duplicated here rather than imported so `symbols.py` does
    not depend on `expressions.py` (the reverse dependency already holds, `expressions.py` imports
    this module)."""
    if expr is None:
        return
    if isinstance(expr, S.Name):
        yield expr.name
    elif isinstance(expr, S.Unary):
        yield from _walk_names(expr.operand)
    elif isinstance(expr, S.Binary):
        yield from _walk_names(expr.left)
        yield from _walk_names(expr.right)
    elif isinstance(expr, S.Index):
        yield expr.name
        for a in expr.args:
            yield from _walk_names(a)
    elif isinstance(expr, S.Member):
        yield from _walk_names(expr.base)
    elif isinstance(expr, S.ArrayLiteral):
        for item in expr.items:
            yield from _walk_names(item)


def _stmt_exprs(stmt: S.Stmt):
    """Every expression a statement holds (declaration dimensions/initialisers, `Const` value,
    assignment, condition, loop bounds, `Case` items, call/instruction arguments, parameter
    defaults, and those of a one-line `If`'s bodies)."""
    if isinstance(stmt, S.Declaration):
        for v in stmt.vars:
            yield from v.dims
            yield from v.init or ()
            yield v.string_size
    elif isinstance(stmt, S.ConstDecl):
        yield stmt.value
    elif isinstance(stmt, S.Assignment):
        yield stmt.target
        yield stmt.value
    elif isinstance(stmt, (S.Instruction, S.Call)):
        yield from stmt.args
    elif isinstance(stmt, S.If):
        yield stmt.cond
        for s2 in [*(stmt.inline or ()), *(stmt.inline_else or ())]:
            yield from _stmt_exprs(s2)
    elif isinstance(stmt, (S.ElseIf, S.DoLoop)):
        yield stmt.cond
    elif isinstance(stmt, S.For):
        yield from (stmt.var, stmt.start, stmt.end, stmt.step)
    elif isinstance(stmt, S.Case):
        for item in stmt.items:
            yield from (part for part in item if isinstance(part, S.Expr))
    elif isinstance(stmt, S.SubDef):
        for p in stmt.params:
            yield p.default
            yield p.string_size


def _name_nodes(expr: S.Expr | None):
    """The `Name`/`Index` nodes and `Member` bases inside `expr` that `expressions._undefined_names`
    looks a bare name up for."""
    if expr is None:
        return
    if isinstance(expr, (S.Name, S.Index)):
        yield expr
        if isinstance(expr, S.Index):
            for a in expr.args:
                yield from _name_nodes(a)
    elif isinstance(expr, S.Unary):
        yield from _name_nodes(expr.operand)
    elif isinstance(expr, S.Binary):
        yield from _name_nodes(expr.left)
        yield from _name_nodes(expr.right)
    elif isinstance(expr, S.Member):
        yield from _name_nodes(expr.base)
        for a in getattr(expr, "args", None) or ():
            yield from _name_nodes(a)
    elif isinstance(expr, S.ArrayLiteral):
        for item in expr.items:
            yield from _name_nodes(item)


_SNMP_VARIABLE_RE = re.compile(r"(?i)\s*SNMPVariable\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)")


def is_reserved_predefined_name(name: str, profile) -> bool:
    """Declaration namespace, docs/checker.md#predefined-constant-name-collisions."""
    key = name.upper()
    return key in profile.symbols and key not in profile.declarable_predefined_names


def report_predefined_name_collision(stmt: S.Stmt, name: str, profile, diags: Diagnostics) -> bool:
    if not is_reserved_predefined_name(name, profile):
        return False
    diags.error(stmt.line, f"{name} is a predefined CONST.", stmt.file)
    return True


def report_function_name_collision(stmt: S.Stmt, name: str, profile, diags: Diagnostics) -> bool:
    if name.upper() not in profile.functions:
        return False
    diags.error(stmt.line, f"{name} already defined as an internal function.", stmt.file)
    return True


def check_local_name_collision(stmt: S.Stmt, profile, diags: Diagnostics) -> None:
    """Routine namespace checks in declaration order; local Dim/parameters shadow functions."""
    if isinstance(stmt, S.ConstDecl):
        names = [stmt.name]
        shadows_functions = False
    elif isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM"):
        names = [v.name for v in stmt.vars]
        shadows_functions = stmt.kind == "DIM"
    elif isinstance(stmt, S.SubDef):
        names = [p.name for p in stmt.params]
        shadows_functions = True
    else:
        return
    for index, name in enumerate(names):
        collision = not shadows_functions and report_function_name_collision(stmt, name, profile, diags)
        collision = collision or report_predefined_name_collision(stmt, name, profile, diags)
        if collision and profile.single_pass:
            if index + 2 < len(names):
                diags.error(stmt.line, f"Too many parameters:  {names[index + 2]}.", stmt.file)
            break
