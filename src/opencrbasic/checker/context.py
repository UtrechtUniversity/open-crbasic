"""Shared per-compilation analysis context for `checker`.

One `Context` is built per checked `S.Program` and bound for the duration of `checker.check()`
via a `ContextVar`, so every pass reads cached facts instead of rescanning declarations, scopes,
dimension tables, or known-name sets. Variant programs derived during checking (the inline-
flattened `ctx.flat`, prefixes, cascade-rewritten programs) are cached as child contexts on the
active root so scope tables can still share work when inline bodies introduce no declarations or
routine boundaries.
"""

from __future__ import annotations

import dataclasses
import functools
import re
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, cast

from .. import consteval, diagnostics
from .. import syntax as S
from ..source import _split_top_commas
from . import symbols

if TYPE_CHECKING:
    from ..profiles import Profile
    from .expressions import DimsState, KnownNames


class DimCounts(dict[str, int]):
    """`Context.dim_counts`'s result; `.strings` names the String variables it includes."""

    strings: frozenset[str] = frozenset()


class ScopeTables:
    """`dims` (upper-case name -> full dimension tuple), `types` (name -> type, arrays always and
    scalars only when `scalars`) and `arrays` (names declared with dimensions), for the declarations
    visible at one statement."""

    __slots__ = ("dims", "types", "arrays")

    def __init__(self, dims: dict[str, tuple[int, ...]], types: dict[str, str], arrays: set[str]):
        self.dims, self.types, self.arrays = dims, types, arrays


@dataclasses.dataclass(frozen=True, slots=True)
class _ScopeBase:
    dims: dict[str, tuple[int, ...]]
    arrays: set[str]
    array_types: dict[str, str]
    scalar_types: dict[str, str]


@dataclasses.dataclass(frozen=True, slots=True)
class _RoutineLayout:
    """Routine ownership of every statement in `program.statements`, computed in one pass.

    - `owners` resets on routine-ending statements before recording the closer (matching
      `scoped_view` / `scope_tables` / `_enclosing_sub`).
    - `groups` keeps a routine closer in its routine's group before starting the next top-level
      group (matching `out_of_scope_nodes` and `var_types._routine_scopes`).
    """

    owners: dict[int, S.SubDef | None]
    groups: list[tuple[S.SubDef | None, list[S.Stmt]]]
    global_stmts: list[S.Stmt]
    routine_stmts: dict[int, list[S.Stmt]]
    all_public_in_routines: list[tuple[S.SubDef, S.Stmt]]


def _with_inline(program: S.Program) -> S.Program:
    """A copy of `program` whose statement list also holds each one-line `If`'s statements, right
    after the `If`."""
    statements: list[S.Stmt] = []
    flat_at: list[int] = []  # raw statement index -> index in the flattened list
    for stmt in program.statements:
        flat_at.append(len(statements))
        statements.append(stmt)
        if isinstance(stmt, S.If):
            statements.extend((stmt.inline or []) + (stmt.inline_else or []))
    flat_at.append(len(statements))
    # (`include_error_at` indexes the raw list: re-index it, or each earlier one-line `If` body
    # would shift the CR300/CR350 cascade start too early)
    return dataclasses.replace(
        program,
        statements=statements,
        include_error_at=[flat_at[min(i, len(flat_at) - 1)] for i in program.include_error_at],
    )


def _names_through_bad_as_clause(stmt: S.Unparsed) -> set[str]:
    """`Public Q, Flag A s Boolean`: the variables up to and including the one with the invalid
    `As` clause are registered, later ones are not (oracle, CR300, 2026-10-08)."""
    m = re.match(r"(?i)\s*(?:Public|Dim|Static)\s+(.*)$", stmt.text or "", re.S)
    if not m:
        return set()
    bad = re.escape(stmt.reason[len("Invalid As clause: ") :].strip())
    out: set[str] = set()
    for item in _split_top_commas(m.group(1)):  # not a comma inside an array's dimensions
        name = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", item)
        if not name:
            break
        out.add(name.group(1).upper())
        if bad and re.search(rf"\b{bad}\b", item[name.end() :]):
            break
    return out


def _declared_names_in_statements(statements: Iterable[S.Stmt]) -> set[str]:
    """Upper-cased names declared across `statements` (same rules as `Context.declared_names`)."""
    names: set[str] = set()
    for stmt in statements:
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
            if kw in symbols._NAME_DECLARING_INSTRUCTIONS and stmt.args and isinstance(stmt.args[0], S.Name):
                names.add(stmt.args[0].name.upper())
            elif kw == "ALIAS":
                for arg in stmt.args[1:]:
                    # `Alias X = Y(2)` registers `Y` (round 30)
                    if isinstance(arg, S.Name) or (isinstance(arg, S.Index) and len(arg.args) == 1):
                        names.add(arg.name.upper())
        # `SNMPVariable(MyTemp, 1.3.6.1..., Float, RONLY)` declares `MyTemp` (oracle, all four
        # loggers, 2026-09-28; a CR350 fuzz false reject). Its dotted OID does not parse here.
        m = symbols._SNMP_VARIABLE_RE.match(getattr(stmt, "text", "") or "")
        if m:
            names.add(m.group(1).upper())
        if isinstance(stmt, S.Unparsed) and stmt.reason.startswith("Invalid As clause: "):
            names.update(_names_through_bad_as_clause(stmt))
    return names


def _declared_all_dims(statements: Iterable[S.Stmt], env: consteval.Env) -> dict[str, tuple[int, ...]]:
    """Upper-case variable name -> full tuple of declared constant dimensions across `statements`."""
    dims: dict[str, tuple[int, ...]] = {}
    for stmt in statements:
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
                if (
                    not isinstance(val, (int, float))
                    or isinstance(val, bool)
                    or val != val
                    or val in (float("inf"), float("-inf"))
                ):
                    break
                sizes.append(int(val))
            else:
                dims[v.name.upper()] = tuple(sizes)
    return dims


class _ScopeLookup:
    """Callable (`tables(stmt)`) and `.for_statement(stmt)` view onto one `(scalars, parameters)` variant."""

    __slots__ = ("_ctx", "_scalars", "_parameters")

    def __init__(self, ctx: Context, scalars: bool, parameters: bool):
        self._ctx = ctx
        self._scalars = scalars
        self._parameters = parameters

    def __call__(self, stmt: S.Stmt) -> ScopeTables:
        return self._ctx._lookup_scope(stmt, self._scalars, self._parameters)

    def for_statement(self, stmt: S.Stmt) -> ScopeTables:
        return self._ctx._lookup_scope(stmt, self._scalars, self._parameters)


class Context:
    """Cached per-program analysis context shared across checker passes."""

    def __init__(
        self,
        program: S.Program,
        profile: Profile | None = None,
        env: consteval.Env | None = None,
        *,
        _routine_bases: dict[int, _ScopeBase] | None = None,
        _scope_cache: dict[tuple[int, bool, bool], ScopeTables] | None = None,
    ):
        self.program = program
        self.profile = profile
        self._env = env
        self._variants: dict[int, Context] = {}
        self._routine_bases: dict[int, _ScopeBase] = _routine_bases if _routine_bases is not None else {}
        self._scope_cache: dict[tuple[int, bool, bool], ScopeTables] = _scope_cache if _scope_cache is not None else {}
        self._scoped_views: dict[tuple[int, bool], S.Program] = {}

    @functools.cached_property
    def env(self) -> consteval.Env:
        """Compile-time constant evaluator environment for `self.program`."""
        if self._env is not None:
            return self._env
        assert self.profile is not None
        return consteval.Env(self.profile, dict(self.program.consts))

    @functools.cached_property
    def flat(self) -> S.Program:
        """`self.program` with one-line `If` statements flattened inline, plus its child `Context`."""
        flat_prog = _with_inline(self.program)
        has_inline_scope_change = any(
            isinstance(st, S.If)
            and any(
                isinstance(inl, (S.Declaration, S.SubDef)) or S.ends_routine(inl)
                for inl in (st.inline or []) + (st.inline_else or [])
            )
            for st in self.program.statements
        )
        flat_ctx = Context(
            flat_prog,
            self.profile,
            self._env,
            _routine_bases=None if has_inline_scope_change else self._routine_bases,
            _scope_cache=None if has_inline_scope_change else self._scope_cache,
        )
        self._variants[id(flat_prog)] = flat_ctx
        return flat_prog

    @functools.cached_property
    def positions(self) -> dict[tuple[str, int], int]:
        """Expanded-source position of each `(file, line)` in `self.flat`."""
        pos: dict[tuple[str, int], int] = {}
        for index, stmt in enumerate(self.flat.statements):
            pos.setdefault((stmt.file, stmt.line), index)
        return pos

    @functools.cached_property
    def _routine_layout(self) -> _RoutineLayout:
        groups: list[tuple[S.SubDef | None, list[S.Stmt]]] = [(None, [])]
        owners: dict[int, S.SubDef | None] = {}
        global_stmts: list[S.Stmt] = []
        routine_stmts: dict[int, list[S.Stmt]] = {}
        all_public_in_routines: list[tuple[S.SubDef, S.Stmt]] = []
        cur: S.SubDef | None = None
        for st in self.program.statements:
            is_sub = isinstance(st, S.SubDef)
            is_end = False if is_sub else S.ends_routine(st)
            if is_sub:
                assert isinstance(st, S.SubDef)
                cur = st
                groups.append((st, []))
                routine_stmts.setdefault(id(st), [])
            groups[-1][1].append(st)
            if is_end:
                cur = None
                groups.append((None, []))
            owners[id(st)] = cur
            if cur is None:
                global_stmts.append(st)
            else:
                routine_stmts[id(cur)].append(st)
                if isinstance(st, S.Declaration) and st.kind == "PUBLIC":
                    all_public_in_routines.append((cur, st))
        return _RoutineLayout(owners, groups, global_stmts, routine_stmts, all_public_in_routines)

    def enclosing_sub(self, stmt: S.Stmt) -> S.SubDef | None:
        """The `Sub`/`Function` whose body holds `stmt`, if any."""
        return self._routine_layout.owners.get(id(stmt))

    @functools.cached_property
    def declared_subs(self) -> frozenset[str]:
        """Upper-case names of every `Sub`/`Function` declared anywhere in `self.program`."""
        return frozenset(s.name.upper() for s in self.program.statements if isinstance(s, S.SubDef))

    @functools.cached_property
    def system_table_names(self) -> frozenset[str]:
        """System table names addressable in `self.program` beyond declared `DataTable`s."""
        names = set(symbols._ALWAYS_KNOWN_SYSTEM_TABLES)
        if any(isinstance(s, S.Instruction) and s.keyword.upper() == "DATATABLE" for s in self.program.statements):
            names.add("DATATABLEINFO")
        return frozenset(names)

    @functools.cached_property
    def ess_variable_names(self) -> frozenset[str]:
        """NTCIP `ess...`-prefixed names referenced when `ESSVariables` is declared."""
        if not any(
            isinstance(s, S.Instruction) and s.keyword.upper() == "ESSVARIABLES" for s in self.program.statements
        ):
            return frozenset()
        names: set[str] = set()
        for stmt in self.program.statements:
            exprs: list[S.Expr | None] = []
            if isinstance(stmt, S.Assignment):
                exprs = [stmt.target, stmt.value]
            elif isinstance(stmt, S.Instruction):
                exprs = list(stmt.args)
            for expr in exprs:
                for name in symbols._walk_names(expr):
                    if name.upper().startswith("ESS"):
                        names.add(name.upper())
        return frozenset(names)

    @functools.cached_property
    def _variable_types_pair(self) -> tuple[dict[str, str], dict[str, str]]:
        first: dict[str, str] = {}
        clash: set[str] = set()
        last: dict[str, str] = {}
        for st in self.program.statements:
            if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC"):
                for v in st.vars:
                    name, kind = v.name.upper(), (v.type or "FLOAT").upper()
                    if v.paren_dim:  # `Public A((1+2)) As Long` is a Float array (oracle, docs/checker.md)
                        kind = "FLOAT"
                    last[name] = kind
                    if first.setdefault(name, kind) != kind:
                        clash.add(name)
        for name in clash:
            del first[name]
        return first, last

    def variable_types(self, *, prefer_last: bool = False) -> dict[str, str]:
        """Fresh dict of upper-case name -> declared type (`FLOAT` when none)."""
        first, last = self._variable_types_pair
        return dict(last if prefer_last else first)

    @functools.cached_property
    def const_table_fields(self) -> dict[str, frozenset[str]]:
        """Closed `ConstTable` name -> `Const` names declared in its body."""
        fields: dict[str, set[str]] = {}
        current: str | None = None
        for stmt in self.program.statements:
            if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "CONSTTABLE":
                current = stmt.args[0].name.upper() if stmt.args and isinstance(stmt.args[0], S.Name) else None
                if current is not None:
                    fields.setdefault(current, set())
            elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ENDCONSTTABLE":
                current = None
            elif current is not None and isinstance(stmt, S.ConstDecl):
                fields[current].add(stmt.name.upper())
        return {k: frozenset(v) for k, v in fields.items()}

    @functools.cached_property
    def declared_variable_names(self) -> frozenset[str]:
        """Upper-case names of genuine runtime storage (`Public`/`Dim`/`Static` and routine params)."""
        names: set[str] = set()
        for stmt in self.program.statements:
            if isinstance(stmt, S.Declaration):
                for v in stmt.vars:
                    names.add(v.name.upper())
            elif isinstance(stmt, S.SubDef):
                for p in stmt.params:
                    if p.name.upper() not in self.program.consts:
                        names.add(p.name.upper())  # (a parameter named like a later `Const` is local)
        return frozenset(names)

    @functools.cached_property
    def declared_names(self) -> frozenset[str]:
        """Upper-case names declared anywhere in `self.program` (`Public`/`Dim`/`Const`/etc.)."""
        return frozenset(_declared_names_in_statements(self.program.statements))

    @functools.cached_property
    def statement_scopes(self) -> dict[int, tuple[frozenset[str], frozenset[str]]]:
        """`id(stmt)` -> (routine parameters in scope, `Const` names not yet declared)."""
        assert self.profile is not None
        consts = {st.name.upper() for st in self.program.statements if isinstance(st, S.ConstDecl)} - set(
            self.profile.symbols
        )
        pending = set(consts)
        params: frozenset[str] = frozenset()
        out: dict[int, tuple[frozenset[str], frozenset[str]]] = {}
        for st in self.program.statements:
            if isinstance(st, S.SubDef):
                # (a parameter named like an earlier `Const` is `already declared as a CONST`)
                params = frozenset(p.name.upper() for p in st.params) - (consts - pending)
            elif S.ends_routine(st):
                params = frozenset()
            elif isinstance(st, S.ConstDecl):
                pending.discard(st.name.upper())
            out[id(st)] = (params, frozenset(pending))
        return out

    @functools.cached_property
    def not_yet_declared(self) -> dict[int, frozenset[str]]:
        """Per top-level `ConstDecl` (`id(stmt)`), names declared only later at top level."""
        assert self.profile is not None
        stmts = self.program.statements
        variables = self.declared_variable_names
        base = set(self.profile.symbols) | set(self.profile.functions) | set(self.profile.instructions)
        seen: set[str] = set()
        out: dict[int, frozenset[str]] = {}
        consts_after: dict[int, set[str]] = {}
        pending: set[str] = set()
        for i in range(len(stmts) - 1, -1, -1):
            if isinstance(stmts[i], S.ConstDecl):
                pending = pending | {cast(S.ConstDecl, stmts[i]).name.upper()}
            consts_after[i] = pending
        in_routine = False
        for i, st in enumerate(stmts):
            if isinstance(st, S.SubDef):
                in_routine = True
            elif S.ends_routine(st):
                in_routine = False
            elif in_routine:
                continue  # routine-local names stay invisible at top level, its own Consts keep the old rule
            elif isinstance(st, S.ConstDecl):
                later = (consts_after[i] | (variables - seen)) - seen - base
                out[id(st)] = frozenset(later)
                seen.add(st.name.upper())
            elif isinstance(st, S.Declaration):
                seen.update(v.name.upper() for v in st.vars)
        return out

    @functools.cached_property
    def routine_scopes(self) -> dict[int, dict[str, tuple[str, str]]]:
        """`id(stmt)` -> `{UPPER name: (declared name, type)}` for variables visible at `stmt`."""

        def declarations(stmts: Iterable[S.Stmt]) -> dict[str, tuple[str, str]]:
            out: dict[str, tuple[str, str]] = {}
            clash: set[str] = set()
            for st in stmts:
                if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC"):
                    for v in st.vars:
                        kind = "FLOAT" if v.paren_dim else (v.type or "FLOAT").upper()
                        key = v.name.upper()
                        if out.setdefault(key, (v.name, kind))[1] != kind:
                            clash.add(key)
            return {k: v for k, v in out.items() if k not in clash}

        groups = self._routine_layout.groups
        glob = declarations(st for routine, stmts in groups if routine is None for st in stmts)
        scopes: dict[int, dict[str, tuple[str, str]]] = {}
        for routine, stmts in groups:
            if routine is None:
                scope = glob
            else:
                scope = dict(glob)
                scope.update(declarations(stmts))
                for prm in routine.params:
                    scope[prm.name.upper()] = (prm.name, (prm.type or "FLOAT").upper())
                if routine.keyword.upper() == "FUNCTION":
                    scope.setdefault(routine.name.upper(), (routine.name, ""))
            for st in stmts:
                scopes[id(st)] = scope
        return scopes

    @functools.cached_property
    def declared_dim0_sizes(self) -> dict[str, int]:
        """Every `Public`/`Dim`/`Static` variable's upper-case name -> its first constant dimension."""
        sizes: dict[str, int] = {}
        for stmt in self.program.statements:
            if not (isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC")):
                continue
            for v in stmt.vars:
                if not v.dims:
                    sizes[v.name.upper()] = 1
                    continue
                try:
                    val = consteval.value_of(v.dims[0], self.env)
                except consteval.NotConstant:
                    continue
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    sizes[v.name.upper()] = int(val)
        return sizes

    @functools.cached_property
    def declared_all_dims(self) -> dict[str, tuple[int, ...]]:
        """Every `Public`/`Dim`/`Static` variable's upper-case name -> full constant dimension tuple."""
        return _declared_all_dims(self.program.statements, self.env)

    @functools.cached_property
    def _dim_counts_pair(self) -> tuple[DimCounts, DimCounts]:
        def build(strings: bool) -> DimCounts:
            counts: dict[str, int] = {}
            ambiguous: set[str] = set()
            strings_seen: set[str] = set()
            for stmt in self.program.statements:
                if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
                    for v in stmt.vars:
                        name = v.name.upper()
                        if (v.type or "").upper() == "STRING":
                            strings_seen.add(name)
                        if ((v.type or "").upper() == "STRING" and not strings) or re.search(
                            r"(?i)\b" + re.escape(v.name) + r"\s*\(\s*\)" + (r"\s*As\b" if strings else ""), stmt.text
                        ):
                            ambiguous.add(name)
                        elif counts.setdefault(name, len(v.dims)) != len(v.dims):
                            ambiguous.add(name)
            out = DimCounts({k: v for k, v in counts.items() if k not in ambiguous})
            out.strings = frozenset(strings_seen)
            return out

        return build(False), build(True)

    def dim_counts(self, *, strings: bool = False) -> DimCounts:
        """Every `Public`/`Dim`/`Static` variable's upper-case name -> declared dimension count."""
        return self._dim_counts_pair[1] if strings else self._dim_counts_pair[0]

    def scoped_view(self, stmt: S.Stmt, *, publics: bool = False) -> S.Program:
        """`self.program` restricted to the declarations visible at `stmt`."""
        layout = self._routine_layout
        owner = layout.owners.get(id(stmt))
        cache_key = (id(owner), publics)
        cached = self._scoped_views.get(cache_key)
        if cached is not None:
            return cached
        stmts: list[S.Stmt] = list(layout.global_stmts)
        if owner is not None:
            stmts.extend(layout.routine_stmts[id(owner)])
        if publics:
            stmts = [st for r, st in layout.all_public_in_routines if r is not owner] + stmts
        view = S.Program("", stmts)
        self._scoped_views[cache_key] = view
        return view

    def _build_scope(self, routine: S.SubDef | None) -> _ScopeBase:
        """Build the un-parameterized base tables once per routine scope (`id(routine)`)."""
        layout = self._routine_layout
        stmts = layout.global_stmts if routine is None else layout.global_stmts + layout.routine_stmts[id(routine)]
        dims = _declared_all_dims(stmts, self.env)
        array_types: dict[str, str] = {}
        scalar_types: dict[str, str] = {}
        arrays: set[str] = set()
        for st in stmts:
            if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM"):
                for v in st.vars:
                    name = v.name.upper()
                    kind = "FLOAT" if v.paren_dim else (v.type or "FLOAT").upper()
                    scalar_types[name] = kind
                    if v.dims:
                        array_types[name] = kind
                        arrays.add(name)
        if routine is not None:
            for prm in routine.params:
                n = prm.name.upper()
                dims.pop(n, None)
                array_types.pop(n, None)
                scalar_types.pop(n, None)
                arrays.discard(n)
        return _ScopeBase(dims, arrays, array_types, scalar_types)

    def _lookup_scope(self, stmt: S.Stmt, scalars: bool, parameters: bool) -> ScopeTables:
        routine = self._routine_layout.owners.get(id(stmt))
        key = (id(routine), scalars, parameters)
        cached = self._scope_cache.get(key)
        if cached is not None:
            return cached
        routine_id = id(routine)
        base = self._routine_bases.get(routine_id)
        if base is None:
            base = self._build_scope(routine)
            self._routine_bases[routine_id] = base
        base_types = base.scalar_types if scalars else base.array_types
        if not parameters or routine is None or not routine.params:
            table = ScopeTables(base.dims, base_types, base.arrays)
        else:
            dims = dict(base.dims)
            types = dict(base_types)
            for prm in routine.params:
                n = prm.name.upper()
                try:
                    shape = tuple(consteval.value_of(d, self.env) for d in prm.dim_exprs) or (1,)
                except consteval.NotConstant:
                    continue
                if all(
                    isinstance(d, (int, float)) and not isinstance(d, bool) and 1 <= d < float("inf") for d in shape
                ):
                    dims[n], types[n] = tuple(int(d) for d in shape), (prm.type or "FLOAT").upper()
            table = ScopeTables(dims, types, base.arrays)
        self._scope_cache[key] = table
        return table

    def scope_tables(self, *, scalars: bool = False, parameters: bool = False) -> _ScopeLookup:
        """`stmt -> ScopeTables` lookup for one `(scalars, parameters)` variant."""
        return _ScopeLookup(self, scalars, parameters)

    def out_of_scope_nodes(self, resolvable: frozenset[str] | set[str] = frozenset()) -> frozenset[int]:
        """`id`s of `Name`/`Index` nodes referencing another routine's local `Dim` or parameter."""
        locals_: dict[int, set[str]] = {}
        owners: list[tuple[S.Stmt, S.SubDef | None]] = []
        outer_stmts: list[S.Stmt] = []
        for routine, stmts in self._routine_layout.groups:
            if routine is not None:
                local_names = locals_.setdefault(id(routine), {p.name.upper() for p in routine.params})
                for st in stmts:
                    owners.append((st, routine))
                    if st is routine:
                        if routine.keyword.upper() != "SUB":
                            outer_stmts.append(dataclasses.replace(routine, params=[]))
                    else:
                        if isinstance(st, S.Declaration) and st.kind == "DIM":
                            local_names.update(v.name.upper() for v in st.vars)
                        else:
                            outer_stmts.append(st)
            else:
                for st in stmts:
                    owners.append((st, None))
                    outer_stmts.append(st)
        every = set().union(*locals_.values()) if locals_ else set()
        if not every:
            return frozenset()
        outer = _declared_names_in_statements(outer_stmts)
        hidden_all = every - outer - set(resolvable)
        if not hidden_all:
            return frozenset()
        out: set[int] = set()
        for st, r in owners:
            hidden = hidden_all - (locals_[id(r)] if r is not None else set())
            if r is st:
                continue  # the header itself
            for e in symbols._stmt_exprs(st):
                for node in symbols._name_nodes(e):
                    if node.name.upper() in hidden:
                        out.add(id(node))
        return frozenset(out)

    @functools.cached_property
    def known_names(self) -> KnownNames:
        """Every name a checked expression position may reference in `self.program`."""
        from . import expressions

        return expressions._build_known_names(self)

    @functools.cached_property
    def dims_state(self) -> DimsState:
        """Dimension-count state tracking across statements in `self.program`."""
        from . import expressions

        return expressions._build_dims_state(self)

    @functools.cached_property
    def float_names(self) -> frozenset[str]:
        """Float-typed declaration names in `self.program` for `longconv` coercion."""
        from . import longconv

        return longconv.float_names(self.program.statements)

    @functools.cached_property
    def literal_names(self) -> frozenset[str]:
        """Float-typed declaration names preserving literal coercions in `longconv`."""
        from . import longconv

        return longconv.literal_names(self.program.statements)

    @functools.cached_property
    def string_consts(self) -> frozenset[str]:
        """Upper-case names of `Const` declarations holding strings or joined strings."""
        out: set[str] = set()
        for st in self.program.statements:
            if isinstance(st, S.ConstDecl) and _stringish_const(st.value, out):
                out.add(st.name.upper())
        return frozenset(out)

    @functools.cached_property
    def alias_names(self) -> frozenset[str]:
        """Upper-case names introduced as `Alias` targets in `self.program`."""
        return frozenset(
            a.name.upper()
            for st in self.program.statements
            if isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS"
            for a in st.args[1:]
            if isinstance(a, S.Name)
        )

    @functools.cached_property
    def _serialin_types_map(self) -> dict[str, str]:
        assert self.profile is not None
        types = self.variable_types()
        for stmt in self.program.statements:
            if isinstance(stmt, S.ConstDecl):
                types[stmt.name.upper()] = "CONST"
            elif (
                isinstance(stmt, S.Instruction)
                and stmt.keyword.upper() == "ALIAS"
                and stmt.args
                and isinstance(stmt.args[0], (S.Name, S.Index))
            ):
                kind = types.get(stmt.args[0].name.upper())
                if kind is not None:
                    for alias in stmt.args[1:]:
                        if isinstance(alias, S.Name):
                            types[alias.name.upper()] = kind
        for name in self.profile.functions:
            types.setdefault(name, "FUNCTION")
        for name in self.profile.symbols:  # `SerialIn(ComRS232,...)` (oracle, CR6/CR1000X, round 59)
            types.setdefault(name, "CONST")
        return types

    def serialin_types(self) -> dict[str, str]:
        """Fresh `name -> type/role` dict for string-destination and argument checks."""
        return dict(self._serialin_types_map)

    @functools.cached_property
    def measurement_aliases(self):
        """Cached `Alias` storage lookup callable `(stmt, dest, allow_bare_index=False)`."""
        from . import instr_measure

        return instr_measure._build_measurement_aliases(self.program, self.scope_tables(scalars=True), self.env)


def _stringish_const(expr: S.Expr | None, str_consts: set[str] | frozenset[str]) -> bool:
    if isinstance(expr, S.String):
        return True
    if isinstance(expr, S.Name):
        return expr.name.upper() in str_consts
    if isinstance(expr, S.Binary) and expr.op in ("&", "+"):
        return _stringish_const(expr.left, str_consts) or _stringish_const(expr.right, str_consts)
    return False


_CURRENT: ContextVar[Context | None] = ContextVar("opencrbasic_checker_context", default=None)


@contextmanager
def activate(ctx: Context) -> Generator[Context]:
    """Bind `ctx` as the active checker context for `of(...)` lookups."""
    token = _CURRENT.set(ctx)
    pos_token = diagnostics._ACTIVE_POSITIONS.set(ctx.positions)
    try:
        yield ctx
    finally:
        diagnostics._ACTIVE_POSITIONS.reset(pos_token)
        _CURRENT.reset(token)


def of(
    program: S.Program,
    profile: Profile | None = None,
    env: consteval.Env | None = None,
) -> Context:
    """Return the active `Context` for `(program, profile, env)`, or build a standalone one."""
    active = _CURRENT.get()
    if active is None:
        return Context(program, profile, env)
    if (
        program is active.program
        and (profile is None or profile is active.profile)
        and (env is None or active._env is None or env is active._env)
    ):
        if env is not None and active._env is None:
            active._env = env
        return active
    eff_profile = profile if profile is not None else active.profile
    eff_env = env
    if eff_env is None and active._env is not None and program.consts is active.program.consts:
        eff_env = active._env
    key = id(program)
    cached = active._variants.get(key)
    if (
        cached is not None
        and cached.program is program
        and (profile is None or cached.profile is eff_profile)
        and (env is None or cached._env is None or env is cached._env)
    ):
        if env is not None and cached._env is None:
            cached._env = env
        return cached
    variant = Context(program, eff_profile, eff_env)
    active._variants[key] = variant
    return variant
