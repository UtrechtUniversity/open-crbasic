"""Declared-name collection: `Sub`/`Function` names, used by `structure.py` to decide whether a
bare (no-`Call`-keyword) name is a legal statement. Rules and oracle evidence: `docs/checker.md`.
"""

from __future__ import annotations

import re

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics


def declared_subs(program: S.Program) -> set[str]:
    """Upper-case names of every `Sub`/`Function` declared anywhere in the program.

    A bare identifier (or `Name(args)` with no `Call` keyword) is only a legal CRBasic statement
    when it names an already-declared `Sub`/`Function` (oracle-confirmed, `docs/frontend.md`
    "Statements"; re-confirmed for the parenthesised-argument form in `docs/checker.md`). Building
    this set from the whole program (not just statements seen so far) is deliberately lenient
    about forward references: we would rather under-report ("verdict", not "exact") than reject a
    program the real compiler accepts (AGENTS.md priority order).
    """
    return {s.name.upper() for s in program.statements if isinstance(s, S.SubDef)}


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


def system_table_names(program: S.Program) -> set[str]:
    """System table names a bare `Name`/`Member` base may legally reference, beyond the
    program's own declared `DataTable`s (already covered by `declared_names`).

    `DataTableInfo` only resolves once the program declares at least one user `DataTable`
    (oracle-confirmed, all four loggers: `DataTableInfo.SkippedRecord(1)` alone in an otherwise
    table-less program fails `Could not find DataTableInfo.`/`Could not find DataTable
    DataTableInfo.`; the same reference compiles once any `DataTable(...)...EndTable` exists
    anywhere in the program, `docs/checker.md`). `Status`/`Public`/`Settings` have no such
    precondition. Checked against `program.statements` (post-`#If`-resolution, so an inactive
    `DataTable` inside a false `#If` branch correctly does not count, matching what the real
    compiler would also not see).
    """
    names = set(_ALWAYS_KNOWN_SYSTEM_TABLES)
    if any(isinstance(s, S.Instruction) and s.keyword.upper() == "DATATABLE" for s in program.statements):
        names.add("DATATABLEINFO")
    return names


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


def ess_variable_names(program: S.Program) -> set[str]:
    """Names this checker treats as resolved once the program declares `ESSVariables` (`docs/
    checker.md` "Round 5" -- a fuzz-found false reject): `ESSVariables Dim`/`ESSVariables Public`
    implicitly declares a whole predefined family of NTCIP `ess...`-prefixed variables
    (`essNtcipSiteDescription`, `essBatteryStatus`, ...), oracle-confirmed accepted even though none
    of them is a `Public`/`Dim`/`Const` `declared_names` would ever see (`ESSVariables Dim` /
    `essNtcipSiteDescription = "Main Street"` compiles on `CR6Comp.exe`).

    This project has not extracted the actual, complete ESS-variable name list from the binary (no
    table this project has decoded enumerates it, unlike a `DataTable`'s own fields), so rather than
    guess at it, this returns every name *actually referenced anywhere in this specific program*
    that starts with the documented `ess` prefix (case-insensitive, matching the CHM's own spelling
    convention) when -- and only when -- the program declares `ESSVariables` somewhere. Deliberately
    conservative both ways: a genuinely misspelled `ess`-prefixed name is silently accepted too
    (under-reporting, the safe side of AGENTS.md's priority), and this costs nothing when the
    program never uses `ESSVariables` at all (the overwhelmingly common case -- returns empty
    immediately).
    """
    if not any(isinstance(s, S.Instruction) and s.keyword.upper() == "ESSVARIABLES"
               for s in program.statements):
        return set()
    names: set[str] = set()
    for stmt in program.statements:
        exprs: list[S.Expr | None] = []
        if isinstance(stmt, S.Assignment):
            exprs = [stmt.target, stmt.value]
        elif isinstance(stmt, S.Instruction):
            exprs = list(stmt.args)
        for expr in exprs:
            for name in _walk_names(expr):
                if name.upper().startswith("ESS"):
                    names.add(name.upper())
    return names


def declared_variable_types(program: S.Program) -> dict[str, str]:
    """Upper-case name -> declared type (`FLOAT` when none) of every Public/Dim/Static variable;
    a name declared with two different types is left out."""
    types: dict[str, str] = {}
    clash: set[str] = set()
    for st in program.statements:
        if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC"):
            for v in st.vars:
                name, kind = v.name.upper(), (v.type or "FLOAT").upper()
                if v.paren_dim:  # `Public A((1+2)) As Long` is a Float array (oracle, docs/checker.md)
                    kind = "FLOAT"
                if types.setdefault(name, kind) != kind:
                    clash.add(name)
    for name in clash:
        del types[name]
    return types


def const_table_fields(program: S.Program) -> dict[str, set[str]]:
    """Closed ConstTable name -> Const names declared in its body."""
    fields: dict[str, set[str]] = {}
    current: str | None = None
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "CONSTTABLE":
            current = stmt.args[0].name.upper() if stmt.args and isinstance(stmt.args[0], S.Name) \
                else None
            if current is not None:
                fields.setdefault(current, set())
        elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ENDCONSTTABLE":
            current = None
        elif current is not None and isinstance(stmt, S.ConstDecl):
            fields[current].add(stmt.name.upper())
    return fields


def declared_variable_names(program: S.Program) -> set[str]:
    """Names that are genuine runtime storage: `Public`/`Dim`/`Static` variables and `Sub`/
    `Function` parameters -- deliberately excluding `Const`/`ConstTable`, `DataTable`/
    `StructureType`/`ObjectType` names, `Alias` targets, and `Label`s, none of which are a live
    variable a compile-time-constant-only argument could illegally reference.

    Used only by the constant-only-family check below (`_CONST_ONLY_FAMILY_KINDS`): a narrower
    purpose than `declared_names`' whole-program symbol-exists check, so this is its own function
    rather than a filter over that one's result (`declared_names` does not tag *which kind* of
    name each entry is).
    """
    names: set[str] = set()
    for stmt in program.statements:
        if isinstance(stmt, S.Declaration):
            for v in stmt.vars:
                names.add(v.name.upper())
        elif isinstance(stmt, S.SubDef):
            for p in stmt.params:
                names.add(p.name.upper())
    return names


def declared_dim0_sizes(program: S.Program, env: consteval.Env) -> dict[str, int]:
    """Every `Public`/`Dim`/`Static` variable's own upper-case name -> its *first* dimension's own
    declared constant size, for `structure.py`'s `Illegally dimensioned aliased name` check
    (round 6, `docs/checker.md`). A dimension that does not fold to an `int`/`float` constant is
    skipped (no entry -- the caller must then leave that name unchecked, `structure.py`'s own
    `check_array_dims` makes the same conservative choice for the same reason, AGENTS.md: never
    guess into a false reject).

    A variable with **no** declared dimensions at all (a plain scalar) maps to `1`, not omitted --
    oracle-confirmed (`docs/checker.md`): `Alias y(2) = Ts` on a scalar `Public y` fails the exact
    same way an out-of-range array index does, while `Alias y(1) = Ts` compiles clean, matching a
    size-1 array rather than "no array at all, so anything goes".
    """
    sizes: dict[str, int] = {}
    for stmt in program.statements:
        if not (isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC")):
            continue
        for v in stmt.vars:
            if not v.dims:
                sizes[v.name.upper()] = 1
                continue
            try:
                val = consteval.value_of(v.dims[0], env)
            except consteval.NotConstant:
                continue
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                sizes[v.name.upper()] = int(val)
    return sizes


def declared_dim_counts(program: S.Program, strings: bool = False) -> dict[str, int]:
    """Every `Public`/`Dim`/`Static` variable's upper-case name -> its number of declared
    dimensions (0 for a scalar). A name declared twice with different counts is left out, and so
    is a String unless `strings` (CR300/CR350 have no character subscript: `S(1,2)` for
    `Public S(5) As String` is `Dimensions not declared`, oracle 2026-10-01)."""
    counts: dict[str, int] = {}
    ambiguous: set[str] = set()
    strings_seen: set[str] = set()
    for stmt in program.statements:
        if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
            for v in stmt.vars:
                name = v.name.upper()
                if (v.type or "").upper() == "STRING":
                    strings_seen.add(name)
                if ((v.type or "").upper() == "STRING" and not strings) or \
                        re.search(r"(?i)\b" + re.escape(v.name) + r"\s*\(\s*\)", stmt.text):
                    # a String takes a character subscript (`Buf(1,1,I)`); `A()` is its own error
                    ambiguous.add(name)
                elif counts.setdefault(name, len(v.dims)) != len(v.dims):
                    ambiguous.add(name)
    out = DimCounts({k: v for k, v in counts.items() if k not in ambiguous})
    out.strings = frozenset(strings_seen)
    return out


class DimCounts(dict):
    """`declared_dim_counts`'s result; `.strings` names the String variables it includes."""
    strings: frozenset = frozenset()


def declared_all_dims(program: S.Program, env: consteval.Env) -> dict[str, tuple[int, ...]]:
    """Every `Public`/`Dim`/`Static` variable's own upper-case name -> its **full** tuple of
    declared dimension sizes, `(1,)` for a scalar (matching `declared_dim0_sizes`'s own "behaves
    like a size-1 array" convention) -- for `structure.py`'s `Illegally dimensioned aliased name`
    check's own row-major flat-offset computation (round 7, `docs/checker.md`). Unlike
    `declared_dim0_sizes` (which only needs the *first* dimension to fold), this one needs *every*
    declared dimension to fold to a positive int/float constant, or the variable is omitted
    entirely (no entry) -- the caller must then leave that name's own flat-offset check unrun
    (AGENTS.md: never guess into a false reject; `structure.py`'s own `_check_array_dims` already
    reports a real error for a declaration whose own dims don't fold this way, so a well-formed
    declaration this checker reaches at all should already have every dim foldable in practice).
    """
    dims: dict[str, tuple[int, ...]] = {}
    for stmt in program.statements:
        if not (isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC")):
            continue
        for v in stmt.vars:
            if not v.dims:
                dims[v.name.upper()] = (1,)
                continue
            sizes: list[int] = []
            for d in v.dims:
                try:
                    val = consteval.value_of(d, env)
                except consteval.NotConstant:
                    break
                if not isinstance(val, (int, float)) or isinstance(val, bool):
                    break
                sizes.append(int(val))
            else:
                dims[v.name.upper()] = tuple(sizes)
    return dims


def declared_names(program: S.Program) -> set[str]:
    """Every name a bare `Name` expression may legally reference: declared `Public`/`Dim`/`Const`/
    `ConstTable` variables, `Sub`/`Function` names and their parameters, `DataTable` and
    `StructureType`/`ObjectType` names, `Alias` targets, and `Label`s (goto targets). A `For`
    loop does not declare its variable (oracle, 2026-10-01: `For j = 1 To 2` -> `j is not
    defined.`). Deliberately global (not scope- or order-checked): a name declared anywhere in the
    program, including inside a `Sub` it is not used in, is accepted everywhere, matching this
    checker's general stance of preferring under-reporting (AGENTS.md's "never reject an accepted
    program" priority) over a full scope analysis this project has not verified against the real
    compiler.
    """
    names: set[str] = set()
    for stmt in program.statements:
        if isinstance(stmt, S.Declaration):
            for v in stmt.vars:
                names.add(v.name.upper())
        elif isinstance(stmt, S.ConstDecl):
            names.add(stmt.name.upper())
        elif isinstance(stmt, S.SubDef):
            names.add(stmt.name.upper())
            for p in stmt.params:
                names.add(p.name.upper())
        elif isinstance(stmt, S.Label):
            names.add(stmt.name.upper())
        elif isinstance(stmt, S.Instruction):
            kw = stmt.keyword.upper()
            if kw in _NAME_DECLARING_INSTRUCTIONS and stmt.args and isinstance(stmt.args[0], S.Name):
                names.add(stmt.args[0].name.upper())
            elif kw == "ALIAS":
                for arg in stmt.args[1:]:
                    if isinstance(arg, S.Name):
                        names.add(arg.name.upper())
        # `SNMPVariable(MyTemp, 1.3.6.1..., Float, RONLY)` declares `MyTemp` (oracle, all four
        # loggers, 2026-09-28; a CR350 fuzz false reject). Its dotted OID does not parse here.
        m = _SNMP_VARIABLE_RE.match(getattr(stmt, "text", "") or "")
        if m:
            names.add(m.group(1).upper())
    return names


_SNMP_VARIABLE_RE = re.compile(r"(?i)\s*SNMPVariable\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)")


def is_reserved_predefined_name(name: str, profile) -> bool:
    """Declaration namespace, docs/checker.md#predefined-constant-name-collisions."""
    key = name.upper()
    return key in profile.symbols and key not in profile.declarable_predefined_names


def report_predefined_name_collision(stmt: S.Stmt, name: str, profile,
                                     diags: Diagnostics) -> bool:
    if not is_reserved_predefined_name(name, profile):
        return False
    diags.error(stmt.line, f"{name} is a predefined CONST.", stmt.file)
    return True


def report_function_name_collision(stmt: S.Stmt, name: str, profile,
                                    diags: Diagnostics) -> bool:
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
        collision = (not shadows_functions and
                     report_function_name_collision(stmt, name, profile, diags))
        collision = collision or report_predefined_name_collision(stmt, name, profile, diags)
        if collision and profile.single_pass:
            if index + 2 < len(names):
                diags.error(stmt.line, f"Too many parameters:  {names[index + 2]}.", stmt.file)
            break
