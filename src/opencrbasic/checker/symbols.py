"""Declared-name collection: `Sub`/`Function` names, used by `structure.py` to decide whether a
bare (no-`Call`-keyword) name is a legal statement. Rules and oracle evidence: `docs/checker.md`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from types import SimpleNamespace

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..source import _split_top_commas


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


def out_of_scope_nodes(program: S.Program, resolvable: frozenset | set = frozenset()) -> frozenset[int]:
    """`id`s of the `Name`/`Index` nodes that name a `Dim` or parameter local to some other `Sub`/
    `Function` and nothing visible where they stand: the real compilers report them undefined (oracle, all
    four loggers, 2026-10-12: `Dim Y` in a Sub, `Y = 2` in main code or in another Sub ->
    `Invalid, or out of place expression`, CR300/CR350 `Y is not defined.`). A name that is also
    declared outside a routine, or `Public`/`Const` inside one (both global), stays visible, and so
    does one that resolves without any declaration (`resolvable`: a predefined symbol, function...,
    so a refused `Dim Sec` in a Sub leaves every `Sec` valid)."""
    locals_: dict[int, set[str]] = {}
    owners: list[tuple[S.Stmt, S.SubDef | None]] = []
    cur = None
    for st in program.statements:
        if isinstance(st, S.SubDef):
            cur = st
            locals_[id(cur)] = {p.name.upper() for p in st.params}
        owners.append((st, cur))
        if S.ends_routine(st):
            cur = None
        elif cur is not None and isinstance(st, S.Declaration) and st.kind == "DIM":
            locals_[id(cur)].update(v.name.upper() for v in st.vars)
    every = set().union(*locals_.values()) if locals_ else set()
    if not every:
        return frozenset()
    outer = declared_names(SimpleNamespace(statements=[
        replace(st, params=[]) if isinstance(st, S.SubDef) else st for st, r in owners
        if not (r is not None and isinstance(st, S.Declaration) and st.kind == "DIM")]))
    hidden_all = every - outer - set(resolvable)
    if not hidden_all:
        return frozenset()
    out: set[int] = set()
    for st, r in owners:
        hidden = hidden_all - (locals_[id(r)] if r is not None else set())
        if r is st:
            continue  # the header itself
        for e in _stmt_exprs(st):
            for node in _name_nodes(e):
                if node.name.upper() in hidden:
                    out.add(id(node))
    return frozenset(out)


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
                if p.name.upper() not in program.consts:
                    names.add(p.name.upper())  # (a parameter named like a later `Const` is local)
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
                        re.search(r"(?i)\b" + re.escape(v.name) + r"\s*\(\s*\)" + (r"\s*As\b" if strings else ""),
                           stmt.text):
                    # a String takes a character subscript (`Buf(1,1,I)`); `A()` is its own error
                    # on CR6/CR1000X, a scalar to CR300/CR350 (`Public A()`, `X = A(1)` ->
                    # `Dimensions not declared`, oracle 2026-10-10) unless a failed `A() As Long`
                    ambiguous.add(name)
                elif counts.setdefault(name, len(v.dims)) != len(v.dims):
                    ambiguous.add(name)
    out = DimCounts({k: v for k, v in counts.items() if k not in ambiguous})
    out.strings = frozenset(strings_seen)
    return out


class DimCounts(dict):
    """`declared_dim_counts`'s result; `.strings` names the String variables it includes."""
    strings: frozenset = frozenset()


def scoped_view(program: S.Program, stmt: S.Stmt) -> S.Program:
    """`program` restricted to the declarations visible at `stmt`: everything outside `Sub`/`Function`
    bodies plus `stmt`'s own routine's body. Other routines' local `Dim`s are dropped, and the
    routine's own sit later in the list so a local shadows a global (last declaration wins)."""
    tagged, owner, cur = [], None, None
    for st in program.statements:
        if isinstance(st, S.SubDef):
            cur = st
        elif S.ends_routine(st):
            cur = None
        if st is stmt:
            owner = cur
        tagged.append((st, cur))
    stmts = [st for st, c in tagged if c is None] + [st for st, c in tagged if c is not None and c is owner]
    return SimpleNamespace(statements=stmts)


class ScopeTables:
    """`dims` (upper-case name -> full dimension tuple, as `declared_all_dims`), `types` (name -> type,
    arrays always, scalars only when `scalars`) and `arrays` (names declared with dimensions), for the
    declarations visible at one statement."""
    __slots__ = ("dims", "types", "arrays")

    def __init__(self, dims, types, arrays):
        self.dims, self.types, self.arrays = dims, types, arrays


def scope_tables(program: S.Program, env: consteval.Env, scalars: bool = False):
    """`stmt -> ScopeTables` looking array shapes up by the scope of the use site: a `Sub`/`Function`
    body sees its own local `Dim`s over the globals (a local shadows a global of the same name, oracle
    2026-10-12: `Dim P(5)` in one Sub, `S1(P(15))` against `Public P(20)` in main code compiles, and
    `P(6)` inside that Sub is out of bounds), main code sees only the globals. A routine's own
    parameters hide the name (no entry: the caller skips it). Cached per routine."""
    owner: dict[int, S.SubDef | None] = {}
    cur = None
    for st in program.statements:
        if isinstance(st, S.SubDef):
            cur = st
        elif isinstance(st, S.Instruction) and st.keyword.upper() in ("ENDSUB", "ENDFUNCTION"):
            cur = None
        owner[id(st)] = cur
    cache: dict[int, ScopeTables] = {}

    def build(routine, probe) -> ScopeTables:
        view = scoped_view(program, probe)
        dims = declared_all_dims(view, env)
        types: dict[str, str] = {}
        arrays: set[str] = set()
        for st in view.statements:
            if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM"):
                for v in st.vars:
                    if v.dims or scalars:
                        types[v.name.upper()] = (v.type or "FLOAT").upper()
                    if v.dims:
                        arrays.add(v.name.upper())
        if routine is not None:
            for prm in routine.params:
                n = prm.name.upper()
                dims.pop(n, None)
                types.pop(n, None)
                arrays.discard(n)
        return ScopeTables(dims, types, arrays)

    def lookup(stmt) -> ScopeTables:
        routine = owner.get(id(stmt))
        key = id(routine)
        if key not in cache:
            cache[key] = build(routine, stmt)
        return cache[key]

    return lookup


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
        if isinstance(stmt, S.Unparsed) and stmt.reason.startswith("Invalid As clause: "):
            names.update(_names_through_bad_as_clause(stmt))
    return names


def _names_through_bad_as_clause(stmt: S.Unparsed) -> set[str]:
    """`Public Q, Flag A s Boolean`: the variables up to and including the one with the invalid
    `As` clause are registered, later ones are not (oracle, CR300, 2026-10-08)."""
    m = re.match(r"(?i)\s*(?:Public|Dim|Static)\s+(.*)$", stmt.text or "", re.S)
    if not m:
        return set()
    bad = re.escape(stmt.reason[len("Invalid As clause: "):].strip())
    out: set[str] = set()
    for item in _split_top_commas(m.group(1)):  # not a comma inside an array's dimensions
        name = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", item)
        if not name:
            break
        out.add(name.group(1).upper())
        if bad and re.search(rf"\b{bad}\b", item[name.end():]):
            break
    return out


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
