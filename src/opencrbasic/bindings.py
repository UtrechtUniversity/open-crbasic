"""Binding index: declarations, references and their resolution over a parsed program.

Read-only. `build(program, profile)` walks the AST that `analysis.analyze` returns
(`AnalysisResult.program`; also `parser.parse_file`) and records every declaration and every name
use with the scope rules the compiler fixtures establish. It changes no compiler behaviour and
no checker uses it. Policies, evidence and limits: `docs/lsp-api.md` "Binding index (LSP-05a)".

A name is never resolved by searching source text. Resolution works on AST names; the only
re-lexing is `name_tokens`/`token_of`, which tokenizes a statement's own text so that LSP-05b can
find a record's column (string tokens are never name tokens).
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NamedTuple

from . import consteval, lexer, profiles, tables
from . import syntax as S
from .catalog import CatalogEntry, InstructionCatalog

# Reference roles (as far as the AST tells; see docs/lsp-api.md).
READ, WRITE, CALL, MEMBER_BASE, MEMBER = "read", "write", "call", "member_base", "member"
ALIAS_TARGET, UNITS_TARGET, TABLE_REF, ARGUMENT = "alias_target", "units_target", "table_ref", "argument"
TYPE, INSTRUCTION = "type", "instruction"

# Names the parser/expression layer treats as predefined although no extracted table lists them
# (checker/expressions.py `_EXTRA_PREDEFINED_SYMBOLS`).
_EXTRA_PREDEFINED = ("NAN", "INF")
# System tables: addressable as `Table.Field` in every program (checker/symbols.py).
_SYSTEM_TABLES = ("STATUS", "PUBLIC", "SETTINGS")
_TABLE_KINDS = ("datatable", "consttable")
_STRUCT_KINDS = ("structuretype", "objecttype")
_ROUTINE_KINDS = ("sub", "function")
_VARIABLE_KINDS = ("public", "dim", "static")
_TABLE_PROPERTIES = {n.upper(): n for n in tables.TABLE_PROPERTIES}


@dataclass(eq=False)
class Declaration:
    """One declared name. Identity (not value) distinguishes declarations: the same spelling in
    two routines, two includes of one file, or two statements gives distinct objects."""

    name: str  # source spelling
    kind: str  # public dim static const sub function param alias datatable
    #                                    consttable structuretype objecttype structure_member label
    #                                    field system_table external
    scope: str  # "global" | "routine" | "structure" | "table" | "predefined"
    routine: S.SubDef | None  # the owning Sub/Function (scope "routine"; params too)
    container: Declaration | None  # StructureType owning a member; ConstTable owning a Const;
    #                                    DataTable/system table owning a `field`
    file: str  # compiler display name
    line: int  # compiler-reported line
    node: object  # VarDecl | ConstDecl | Param | SubDef | Instruction | Label
    stmt: S.Stmt | None  # innermost statement holding the declaration
    unit: S.Stmt | None  # outermost statement of the source segment (owns the text)
    index: int  # position of `unit` in `program.statements` (-1: predefined)
    occurrence: int | None = None  # nth same-name token of `unit.text` (see `token_of`)
    type: str | None = None  # upper-case declared type, if any
    quoted: bool = False  # a DataTable/ConstTable name written as a string
    external: CatalogEntry | None = None
    alias_target: Reference | None = None
    unverified_scope: bool = False  # scope inside a routine is not established by evidence
    tok_key: tuple = ()
    modelled: bool = False  # table/system_table: every field name is known (`fields_of`)
    members: dict | None = None  # StructureType: upper member name -> [Declaration]
    site: str = ""  # `field`: "fieldnames" (the FieldNames string token is the
    #                                    site, `quoted`), "instruction" (name derived from the Source;
    #                                    only stmt/line locate it, no token) or "property" (a
    #                                    `Table.Output`-style member, predefined)

    def __repr__(self) -> str:
        return f"Declaration({self.kind} {self.name!r} {self.file}:{self.line} {self.scope})"


@dataclass(eq=False)
class Reference:
    name: str  # source spelling
    role: str
    file: str
    line: int
    resolved: tuple = ()  # candidate declarations; () unresolved; >1 ambiguous
    ambiguity: str = ""  # non-empty: why the candidates are not a proven single target
    container: tuple = ()  # member reference: the table/structure declarations searched
    routine: S.SubDef | None = None  # routine the reference occurs in
    node: object = None  # Name | Index | Member | Call | Instruction | Param | VarDecl
    stmt: S.Stmt | None = None
    unit: S.Stmt | None = None
    index: int = 0
    occurrence: int | None = None
    base: Reference | None = None  # member reference: the reference of its base
    is_write: bool = False
    forward: bool = False  # every candidate is declared in a later statement
    tok_key: tuple = ()

    @property
    def status(self) -> str:
        if self.ambiguity or len(self.resolved) > 1:
            return "ambiguous"
        if self.resolved:
            return "resolved"
        if self.role == MEMBER and any(
            c.kind in ("datatable", "system_table") and not c.modelled for c in self.container
        ):
            return "unmodelled"  # a table whose field names the index cannot list
        return "unresolved"

    def __repr__(self) -> str:
        return f"Reference({self.role} {self.name!r} {self.file}:{self.line} {self.status})"


@dataclass(frozen=True)
class IndexFailure:
    index: int  # statement position, -1 for the whole build
    file: str
    line: int
    message: str


class Resolution(NamedTuple):
    candidates: tuple
    ambiguity: str


class BindingIndex:
    def __init__(self, profile: profiles.Profile):
        self.profile = profile
        self.declarations: tuple[Declaration, ...] = ()
        self.references: tuple[Reference, ...] = ()
        self.failures: tuple[IndexFailure, ...] = ()
        self.unparsed: tuple[S.Unparsed, ...] = ()  # statements the parser could not take apart
        self._by_name: dict[str, list[Declaration]] = {}
        self._refs_to: dict[int, list[Reference]] = {}
        self._by_unit: dict[int, list] = {}
        self._glob: dict[str, list[Declaration]] = {}
        self._local: dict[int, dict[str, list[Declaration]]] = {}
        self._loose: dict[str, list[Declaration]] = {}
        self._catalog = InstructionCatalog.for_profile(profile.name)
        self._external: dict[tuple, Declaration] = {}
        self._system: dict[str, Declaration] = {}
        self._tokens: dict[int, list | None] = {}
        self._fields: dict[int, list[Declaration]] = {}  # id(DataTable decl) -> fields, in order
        self._field_names: dict[tuple, list[Declaration]] = {}  # (id(table), UPPER name) -> fields
        self._sys_names: dict[str, dict[str, str] | None] = {}  # system table -> upper -> spelling
        self._sys_fields: dict[tuple, Declaration] = {}
        self._slow_seq = 0

    # ------------------------------------------------------------ queries

    def fields_of(self, table: Declaration) -> tuple[Declaration, ...]:
        """The fields of a user DataTable declaration (kind `field`, in TDF order) as
        `tables.py` derives them. Fields are not in `declarations_named`; `()` for any other
        declaration or a table whose fields could not be derived (`table.modelled` is False)."""
        return tuple(self._fields.get(id(table), ()))

    def declarations_named(self, name: str) -> tuple[Declaration, ...]:
        return tuple(self._by_name.get(name.upper(), ()))

    def references_to(self, decl: Declaration) -> tuple[Reference, ...]:
        """References that list `decl` among their candidates (ambiguous ones included)."""
        return tuple(self._refs_to.get(id(decl), ()))

    def records_in(self, stmt: S.Stmt) -> tuple:
        """Declarations and references, in source order, of one top-level statement."""
        return tuple(self._by_unit.get(id(stmt), ()))

    def counts(self) -> dict:
        refs = defaultdict(int)
        for r in self.references:
            refs[r.status] += 1
        kinds = defaultdict(int)
        for d in self.declarations:
            kinds[d.kind] += 1
        return {
            "declarations": dict(kinds),
            "references": dict(refs),
            "failures": len(self.failures),
            "unparsed": len(self.unparsed),
        }

    def lookup(self, name: str, routine: S.SubDef | None = None, role: str = READ) -> Resolution:
        """Candidates for a name used in `routine` (None: outside every routine)."""
        return self._resolve_name(name.upper(), routine, role)

    def token_of(self, record: Declaration | Reference) -> lexer.Token | None:
        """The lexer token (a `col` into `record.unit.text`) of the record's name, or None when the
        statement text cannot be tokenized or has fewer such tokens than `occurrence` says."""
        if record.unit is None or record.occurrence is None:
            return None
        toks = self._unit_tokens(record.unit)
        if toks is None:
            return None
        kind = record.tok_key[1]
        pool = [t for t, k in toks if k == kind and _tok_name(t, k) == record.tok_key[0]]
        return pool[record.occurrence] if record.occurrence < len(pool) else None

    def _unit_tokens(self, unit: S.Stmt):
        key = id(unit)
        if key not in self._tokens:
            self._tokens[key] = classified_tokens(unit)
        return self._tokens[key]

    # --------------------------------------------------------- resolution

    def _ext(self, entry: CatalogEntry) -> Declaration:
        key = (entry.name.upper(), entry.kind)
        decl = self._external.get(key)
        if decl is None:
            decl = Declaration(
                entry.name,
                "external",
                "predefined",
                None,
                None,
                "",
                0,
                None,
                None,
                None,
                -1,
                type=entry.kind,
                external=entry,
            )
            self._external[key] = decl
        return decl

    def _predefined(self, upper: str, kinds: tuple[str, ...]) -> tuple[Declaration, ...]:
        for kind in kinds:
            entry = self._catalog.get(upper, kind)
            if entry is not None:
                return (self._ext(entry),)
        if upper in _EXTRA_PREDEFINED and "symbol" in kinds:
            entry = CatalogEntry(upper, "symbol", self.profile.name, "parser:extra-predefined")
            return (self._ext(entry),)
        return ()

    def _system_table(self, upper: str) -> Declaration | None:
        if upper == "DATATABLEINFO" and not any(d.kind == "datatable" for d in self.declarations):
            return None
        if upper not in _SYSTEM_TABLES + ("DATATABLEINFO",):
            return None
        decl = self._system.get(upper)
        if decl is None:
            decl = Declaration(
                upper.capitalize() if upper != "DATATABLEINFO" else "DataTableInfo",
                "system_table",
                "predefined",
                None,
                None,
                "",
                0,
                None,
                None,
                None,
                -1,
            )
            self._system[upper] = decl
        return decl

    def _resolve_name(self, upper: str, routine, role: str) -> Resolution:
        profile = self.profile
        if role == INSTRUCTION:
            # The parser made this token an instruction: only the catalog answers, never a user
            # declaration of the same spelling (docs/lsp-api.md, "Scope policies and evidence").
            return Resolution(self._predefined(upper, ("instruction", "keyword")), "")
        locals_ = self._local.get(id(routine), {}).get(upper, []) if routine is not None else []
        if locals_:
            if len(locals_) > 1:
                return Resolution(tuple(locals_), "duplicate declarations in one routine")
            local = locals_[0]
            if profile.name in ("cr300", "cr350") and upper in profile.functions and local.kind in ("dim", "param"):
                fn = self._predefined(upper, ("function",))
                if fn:
                    return Resolution(
                        fn + (local,),
                        f"{profile.name}: a local Dim/parameter does not shadow a "
                        "built-in function (docs/checker.md, function-named "
                        "assignment targets)",
                    )
            return Resolution((local,), "")
        glob = self._glob.get(upper, [])
        if glob:
            if len(glob) > 1:
                return Resolution(tuple(glob), _duplicate_reason(glob))
            return Resolution((glob[0],), "")
        loose = self._loose.get(upper, [])
        if loose:
            return Resolution(
                tuple(loose),
                "declared inside a routine; its visibility outside that routine is not established by evidence",
            )
        if role == CALL:
            kinds = ("instruction", "function", "symbol")
        elif role == TYPE:
            kinds = ("symbol",)
        else:
            kinds = ("symbol", "function")
        pre = self._predefined(upper, kinds)
        if not pre and role == ARGUMENT:
            pre = self._predefined(upper, ("instruction",))
        return Resolution(pre, "")

    def _members_of(self, cand: Declaration, member_upper: str):
        """(containers, member declarations, ambiguity note) for one base candidate."""
        if cand.kind == "consttable":
            found = [d for d in self._glob.get(member_upper, []) if d.container is cand]
            return [cand], found
        if cand.kind == "system_table":
            if cand.name.upper() == "PUBLIC":
                found = [d for d in self._glob.get(member_upper, []) if d.kind in ("public", "alias")]
                return [cand], found
            sysf = self._system_field(cand, member_upper) or self._table_property(cand, member_upper)
            return [cand], [sysf] if sysf is not None else []
        if cand.kind == "datatable":
            found = list(self._field_names.get((id(cand), member_upper), ()))
            if not found:
                prop = self._table_property(cand, member_upper)
                found = [prop] if prop is not None else []
            return [cand], found
        if cand.kind in _TABLE_KINDS:
            return [cand], []
        tname = (cand.type or "").rstrip("!")
        struct = next((d for d in self._glob.get(tname, []) if d.kind in _STRUCT_KINDS), None)
        if struct is not None and struct.members is not None:
            return [struct], list(struct.members.get(member_upper, []))
        return [], []

    def _system_names(self, table_upper: str) -> dict[str, str] | None:
        """Upper -> spelling of the fields the profile data lists for a system table, or None.
        Status and DataTableInfo come from the CR6 TDF derivation of `tables.py` (CR6 only);
        Settings from the profile's `settings_fields.json` `settings` list. A name outside the
        list stays `unmodelled`: the lists are not proven complete (the project uses
        `Status.CardBytesFree`, which is in neither Status list)."""
        if table_upper not in self._sys_names:
            names = None
            try:
                if table_upper == "SETTINGS":
                    names = {n.upper(): n for n in self.profile.settings_fields["settings"]}
                elif self.profile.name == "cr6" and table_upper == "STATUS":
                    names = {f.name.upper(): f.name for f in tables._status_table(self._slow_seq).fields}
                    for n in self.profile.settings_fields["status"]:
                        names.setdefault(n.upper(), n)
                elif self.profile.name == "cr6" and table_upper == "DATATABLEINFO":
                    names = {f.name.upper(): f.name for f in tables._datatableinfo_table(1).fields}
            except Exception:
                names = None
            self._sys_names[table_upper] = names
        return self._sys_names[table_upper]

    def _table_property(self, table: Declaration, member_upper: str) -> Declaration | None:
        if member_upper not in _TABLE_PROPERTIES or table.name.upper() in ("SETTINGS", "PUBLIC"):
            return None
        key = (id(table), member_upper)
        decl = self._sys_fields.get(key)
        if decl is None:
            decl = Declaration(
                _TABLE_PROPERTIES[member_upper],
                "field",
                "predefined",
                None,
                table,
                "",
                0,
                None,
                None,
                None,
                -1,
                site="property",
            )
            self._sys_fields[key] = decl
        return decl

    def _system_field(self, table: Declaration, member_upper: str) -> Declaration | None:
        names = self._system_names(table.name.upper())
        if not names or member_upper not in names:
            return None
        key = (table.name.upper(), member_upper)
        decl = self._sys_fields.get(key)
        if decl is None:
            decl = Declaration(names[member_upper], "field", "predefined", None, table, "", 0, None, None, None, -1)
            self._sys_fields[key] = decl
        return decl

    def _resolve_member(self, ref: Reference) -> None:
        base = ref.base
        cands = [c for c in (base.resolved if base is not None else ()) if c.kind != "external"]
        containers: list[Declaration] = []
        found: list[Declaration] = []
        for cand in cands:
            c, f = self._members_of(cand, ref.name.upper())
            containers += c
            found += f
        ref.container = tuple(containers)
        ref.resolved = tuple(found)
        if len(found) > 1:
            ref.ambiguity = "duplicate member declarations"
        elif base is not None and base.ambiguity and found:
            ref.ambiguity = "base reference is ambiguous"

    def _finish_reference(self, ref: Reference) -> None:
        if ref.role == MEMBER:
            self._resolve_member(ref)
        else:
            res = self._resolve_name(ref.name.upper(), ref.routine, ref.role)
            if not res.candidates and ref.role == MEMBER_BASE:
                sysd = self._system_table(ref.name.upper())
                if sysd is not None:
                    res = Resolution((sysd,), "")
            ref.resolved, ref.ambiguity = res.candidates, res.ambiguity
            if (
                ref.role in (READ, ARGUMENT)
                and isinstance(ref.node, S.Index)
                and ref.resolved
                and all(_is_callable(d) for d in ref.resolved)
            ):
                ref.role = CALL
            if ref.role in (READ, ARGUMENT) and ref.resolved and all(d.kind in _TABLE_KINDS for d in ref.resolved):
                ref.role = TABLE_REF
        user = [
            d
            for d in ref.resolved
            if d.index >= 0 and d.kind not in _ROUTINE_KINDS + _TABLE_KINDS and d.kind not in _STRUCT_KINDS
        ]
        ref.forward = bool(user) and len(user) == len(ref.resolved) and all(d.index > ref.index for d in user)


def _duplicate_reason(cands: list[Declaration]) -> str:
    if len({(d.file, d.line) for d in cands}) == 1:
        return "the same declaration is included more than once"
    tables = [d.kind in _TABLE_KINDS for d in cands]
    if any(tables) and not all(tables):
        return (
            "a table and another declaration share this spelling; whether tables and "
            "variables are separate namespaces is not established here"
        )
    return "duplicate global declarations"


def _is_callable(d: Declaration) -> bool:
    if d.kind in _ROUTINE_KINDS:
        return True
    return d.kind == "external" and d.external is not None and d.external.kind in ("function", "instruction")


# ------------------------------------------------------------- token helpers


def closes_routine(stmt: S.Stmt) -> bool:
    """True when `stmt` ends a Sub or Function: `EndSub` / `EndFunction`, or the two-word `End Sub` /
    `End Function`, which the parser gives as `Instruction(keyword="END", args=[Name("Sub")])`.
    Shared by the binding index, hover and the routine scope they track."""
    return S.ends_routine(stmt)


def classified_tokens(stmt: S.Stmt):
    """`[(token, kind)]` of `stmt.text`, kind `"id"` (identifier not after `.`), `"member"`
    (identifier after `.`) or `"string"`; None when the text cannot be tokenized. A `Units`
    statement is cut at its first `=` (the label is free text, as in the parser)."""
    text = getattr(stmt, "text", "") or ""
    if not text:
        return None
    if re.match(r"\s*Units\b", text, re.I):
        eq = text.find("=")
        if eq != -1:
            text = text[:eq]
    try:
        toks = lexer.tokenize(text, stmt.line)
    except lexer.LexError:
        return None
    out = []
    prev = None
    for t in toks:
        if t.kind == "IDENT":
            out.append((t, "member" if prev is not None and prev.kind == "OP" and prev.text == "." else "id"))
        elif t.kind == "STRING":
            out.append((t, "string"))
        prev = t
    return out


def _alias_junk(st: S.Instruction, n_items: int) -> list[list[tuple]]:
    """Per right-hand item of `Alias A = X junk, Y` the `(name, kind)` keys of the identifier and
    string tokens after its name (and index) that the parser skips; the last list holds those of
    every item after the parsed ones. Empty lists when the text cannot be tokenized."""
    empty: list[list[tuple]] = [[] for _ in range(n_items + 1)]
    try:
        toks = lexer.tokenize(st.text or "", st.line)
    except lexer.LexError:
        return empty
    depth, items, cur, prev, seen_eq = 0, [], [], None, False
    for t in toks:
        op = t.kind == "OP"
        if op and t.text == "(":
            depth += 1
        elif op and t.text == ")":
            depth = max(0, depth - 1)
        if not seen_eq:
            seen_eq = op and t.text == "=" and depth == 0
        elif op and t.text == "," and depth == 0:
            items.append(cur)
            cur = []
        else:
            cur.append((t, prev))
        prev = t
    items.append(cur)
    out: list[list[tuple]] = []
    for j, item in enumerate(items):
        skip = 0 if j >= n_items else 1
        if skip and len(item) > 1 and item[1][0].kind == "OP" and item[1][0].text == "(":
            d, skip = 0, len(item)
            for idx in range(1, len(item)):
                t = item[idx][0]
                d += (t.text == "(") - (t.text == ")") if t.kind == "OP" else 0
                if d == 0:
                    skip = idx + 1
                    break
        keys = []
        for t, pv in item[skip:]:
            if t.kind == "IDENT":
                k = "member" if pv is not None and pv.kind == "OP" and pv.text == "." else "id"
            elif t.kind == "STRING":
                k = "string"
            else:
                continue
            keys.append((_tok_name(t, k), k))
        out.append(keys)
    tail = [k for keys in out[n_items:] for k in keys]
    return (out[:n_items] + [[] for _ in range(max(0, n_items - len(out)))])[:n_items] + [tail]


def skipped_keys(unit: S.Stmt) -> list[tuple]:
    """Keys of the tokens of `unit` the index counts but has no record for (see `_alias_junk`)."""
    if isinstance(unit, S.Instruction) and unit.keyword.upper() == "ALIAS":
        return [k for keys in _alias_junk(unit, len(unit.args) - 1) for k in keys]
    return []


def _tok_name(t: lexer.Token, kind: str) -> str:
    return str(t.value).upper() if kind == "string" else t.upper


def name_tokens(stmt: S.Stmt, name: str, *, member: bool = False, string: bool = False) -> list:
    """Tokens of `stmt.text` spelling `name` (case-insensitive): identifiers not after `.` by
    default, identifiers after `.` with `member`, string tokens whose decoded value is `name` with
    `string`. Columns are offsets into `stmt.text`; add the statement's offset in the logical line
    (`segment_starts`) for a logical column. String contents are never matched as identifiers."""
    toks = classified_tokens(stmt)
    if toks is None:
        return []
    kind = "string" if string else ("member" if member else "id")
    up = name.upper()
    return [t for t, k in toks if k == kind and _tok_name(t, k) == up]


def segment_starts(logical_text: str) -> list[tuple[int, str]]:
    """`(offset, segment text)` of the top-level `:`-separated segments of a logical line, as the
    parser splits them. `Stmt.text` of a segment's statement equals the segment text (stripped
    for `Units`); an inline `If` body's text is a slice of its segment."""
    from .parser import _split_text_segments

    out, pos = [], 0
    for seg in _split_text_segments(logical_text):
        out.append((pos, seg))
        pos += len(seg) + 1
    return out


# ------------------------------------------------------------------- builder


class _Builder:
    def __init__(self, index: BindingIndex):
        self.ix = index
        self.decls: list[Declaration] = []
        self.refs: list[Reference] = []
        self.order: list[tuple] = []  # (unit, record) in emission order
        self.routine: S.SubDef | None = None
        self.struct: Declaration | None = None
        self.consttable: Declaration | None = None
        self.unit: S.Stmt | None = None
        self.idx = 0
        self.counts: dict = {}

    # -- emission --------------------------------------------------------

    def _occ(self, key: tuple) -> int:
        n = self.counts.get(key, 0)
        self.counts[key] = n + 1
        return n

    def _skip_tokens(self, keys) -> None:
        """Count the tokens the parser ignored (`Alias A = X junk, Y`), so that the ordinals of the
        records after them still point at their own token."""
        for key in keys:
            self._occ(key)

    def decl(
        self,
        stmt,
        name,
        kind,
        *,
        node=None,
        scope=None,
        routine="default",
        container=None,
        type=None,
        quoted=False,
        unverified=False,
        count=True,
    ) -> Declaration:
        if routine == "default":
            routine = self.routine
        if scope is None:
            scope = "routine" if routine is not None else "global"
        key = (name.upper(), "string" if quoted else "id")
        owner = routine if scope == "routine" and isinstance(routine, S.SubDef) else None
        d = Declaration(
            name,
            kind,
            scope,
            owner,
            container,
            stmt.file,
            stmt.line,
            node,
            stmt,
            self.unit,
            self.idx,
            self._occ(key) if count else None,
            type,
            quoted,
            unverified_scope=unverified,
            tok_key=key,
        )
        if kind in _STRUCT_KINDS:
            d.members = {}
        self.decls.append(d)
        self.order.append((self.unit, d))
        return d

    def ref(self, stmt, name, role, node, *, member=False, base=None, write=False) -> Reference:
        key = (name.upper(), "member" if member else "id")
        r = Reference(
            name,
            role,
            stmt.file,
            stmt.line,
            routine=self.routine,
            node=node,
            stmt=stmt,
            unit=self.unit,
            index=self.idx,
            occurrence=self._occ(key),
            base=base,
            is_write=write,
            tok_key=key,
        )
        self.refs.append(r)
        self.order.append((self.unit, r))
        return r

    # -- expressions -----------------------------------------------------

    def expr(self, e, stmt, role=READ, *, write=False, skip=None) -> Reference | None:
        """Walk `e` in source order. Returns the reference of the outermost name (the root of an
        `a(i).b.c` chain)."""
        if e is None or e is skip or isinstance(e, (S.Number, S.String)):
            return None
        if isinstance(e, S.Name):
            return self.ref(stmt, e.name, WRITE if write else role, e, write=write)
        if isinstance(e, S.Index):
            r = self.ref(stmt, e.name, WRITE if write else role, e, write=write)
            for a in e.args:
                self.expr(a, stmt, READ)
            return r
        if isinstance(e, S.Member):
            root = self.expr(e.base, stmt, MEMBER_BASE)
            base_ref = self._last_chain_ref(e.base, root)
            self.ref(stmt, e.member, MEMBER, e, member=True, base=base_ref, write=write)
            for a in e.args or []:
                self.expr(a, stmt, READ)
            return root
        if isinstance(e, S.Unary):
            return self.expr(e.operand, stmt, role, write=write)
        if isinstance(e, S.Binary):
            self.expr(e.left, stmt, READ, skip=skip)
            self.expr(e.right, stmt, READ, skip=skip)
            return None
        if isinstance(e, S.ArrayLiteral):
            for item in e.items:
                self.expr(item, stmt, READ)
            return None
        for f in getattr(e, "__dataclass_fields__", {}):  # unknown node: walk its fields
            v = getattr(e, f)
            for x in v if isinstance(v, list) else [v]:
                if isinstance(x, S.Expr):
                    self.expr(x, stmt, READ)
        return None

    def _last_chain_ref(self, base_expr, root):
        """The reference standing for `base_expr`: its member reference for a nested `a.b`, else the
        root name's reference."""
        if isinstance(base_expr, S.Member):
            for r in reversed(self.refs):
                if r.node is base_expr:
                    return r
        return root

    def args(self, args, stmt, role=ARGUMENT):
        for a in args or []:
            self.expr(a, stmt, role)

    def type_ref(self, stmt, vtype, node):
        if vtype:
            name = vtype.rstrip("!")
            self.ref(stmt, name, TYPE, node)

    # -- statements ------------------------------------------------------

    def statement(self, st: S.Stmt) -> None:
        if isinstance(st, S.Declaration):
            self.declaration(st)
        elif isinstance(st, S.ConstDecl):
            self.const(st)
        elif isinstance(st, S.Assignment):
            self.expr(st.target, st, WRITE, write=True)
            self.expr(st.value, st, READ, skip=st.target if st.compound else None)
        elif isinstance(st, S.Instruction):
            self.instruction(st)
        elif isinstance(st, S.If):
            self.expr(st.cond, st)
            for sub in (st.inline or []) + (st.inline_else or []):
                self.statement(sub)
        elif isinstance(st, S.ElseIf):
            self.expr(st.cond, st)
        elif isinstance(st, S.For):
            self.expr(st.var, st, WRITE, write=True)
            for e in (st.start, st.end, st.step):
                self.expr(e, st)
        elif isinstance(st, S.DoLoop):
            self.expr(st.cond, st)
        elif isinstance(st, S.Case):
            for item in st.items:
                for e in item[1:]:
                    if isinstance(e, S.Expr):
                        self.expr(e, st)
        elif isinstance(st, S.SubDef):
            self.subdef(st)
        elif isinstance(st, S.Call):
            self.ref(st, st.name, CALL, st)
            self.args(st.args, st)
            self.expr(st.tail_expr, st)
            for e in st.juxtaposed or []:
                self.expr(e, st)
        elif isinstance(st, S.Label):
            self.decl(st, st.name, "label", node=st, unverified=self.routine is not None)

    def declaration(self, st: S.Declaration) -> None:
        for v in st.vars:
            if st.kind == "STRUCTUREMEMBER":
                d = self.decl(
                    st,
                    v.name,
                    "structure_member",
                    node=v,
                    scope="structure",
                    routine=None,
                    container=self.struct,
                    type=v.type,
                )
                if self.struct is not None and self.struct.members is not None:
                    self.struct.members.setdefault(v.name.upper(), []).append(d)
            else:
                self.decl(
                    st,
                    v.name,
                    st.kind.lower(),
                    node=v,
                    type=v.type,
                    unverified=st.kind == "PUBLIC" and self.routine is not None,
                )
            for dim in v.dims:
                self.expr(dim, st)
            self.type_ref(st, v.type, v)
            self.expr(v.string_size, st)
            for e in v.init or []:
                self.expr(e, st)

    def const(self, st: S.ConstDecl) -> None:
        self.decl(
            st, st.name, "const", node=st, type=st.type, container=self.consttable if self.routine is None else None
        )
        self.type_ref(st, st.type, st)
        self.expr(st.value, st)

    def subdef(self, st: S.SubDef) -> None:
        kind = "function" if st.keyword == "FUNCTION" else "sub"
        self.decl(st, st.name, kind, node=st, scope="global", routine=None, type=st.return_type)
        for p in st.params:
            self.decl(st, p.name, "param", node=p, scope="routine", routine=st, type=p.type)
            for dim in p.dim_exprs:  # `p(sz)`: a read in the routine's scope
                self.expr(dim, st)
            self.type_ref(st, p.type, p)
            self.expr(p.string_size, st)
            self.expr(p.default, st)
        self.type_ref(st, st.return_type, st)

    def instruction(self, st: S.Instruction) -> None:
        kw = st.keyword.upper()
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*\$?)", st.text or "")
        spelling = m.group(1) if m and m.group(1).upper() == kw else kw
        self.ref(st, spelling, INSTRUCTION, st)
        if kw == "ALIAS" and st.args:
            target = self.expr(st.args[0], st, ALIAS_TARGET)
            junk = _alias_junk(st, len(st.args) - 1)
            for j, rhs in enumerate(st.args[1:]):
                name = rhs.name if isinstance(rhs, (S.Name, S.Index)) else None
                if name is None:
                    self.expr(rhs, st)
                    continue
                d = self.decl(st, name, "alias", node=rhs, unverified=self.routine is not None)
                d.alias_target = target
                if isinstance(rhs, S.Index):
                    for a in rhs.args:
                        self.expr(a, st)
                self._skip_tokens(junk[j])
            self._skip_tokens(junk[-1])
        elif kw == "UNITS" and st.args:
            self.expr(st.args[0], st, UNITS_TARGET)
        elif kw in ("DATATABLE", "CONSTTABLE", "STRUCTURETYPE", "OBJECTTYPE") and st.args:
            first = st.args[0]
            dkind = kw.lower()
            if isinstance(first, S.Name):
                d = self.decl(st, first.name, dkind, node=first, scope="global", routine=None)
            elif isinstance(first, S.String) and first.value and kw in ("DATATABLE", "CONSTTABLE"):
                d = self.decl(st, first.value, dkind, node=first, scope="global", routine=None, quoted=True)
            else:
                d = None
                self.expr(first, st, ARGUMENT)
            if d is not None and kw == "STRUCTURETYPE":
                self.struct = d
            if d is not None and kw == "CONSTTABLE":
                self.consttable = d
            self.args(st.args[1:], st)
        else:
            self.args(st.args, st)
        if kw == "ENDSTRUCTURETYPE":
            self.struct = None
        elif kw == "ENDCONSTTABLE":
            self.consttable = None


def _add_data_table_fields(ix: BindingIndex, program, statements, decls, failures) -> None:
    """Declare the fields of every user DataTable. Names, order and renaming come from
    `tables.py` (private helpers `_scan_program`, `_emit_output_fields`, `_apply_fieldnames`,
    `_split_by_alias`, `_build_public_table`; not modified): the loop below mirrors
    `_build_data_table`'s grouping of output instructions and `FieldNames`, to know which
    statement declares which field, and its names are checked against `_build_data_table`'s own
    field list (a table that differs is left unmodelled)."""
    tbl_decls = {id(d.stmt): d for d in decls if d.kind == "datatable" and d.scope == "global"}
    if not tbl_decls:
        return
    env = consteval.Env(ix.profile, dict(getattr(program, "consts", None) or {}))
    scanned = tables._scan_program(program)
    ix._slow_seq = scanned["slow_seq_count"]
    var_info: dict = {}
    for v in scanned["all_vars"]:
        var_info[v.name.upper()] = tables._make_var_info(v, env)
        var_info[v.name.upper()].struct = scanned["struct_types"].get((v.type or "").upper())
    _, by_display, by_base = tables._build_public_table(
        scanned["publics"], var_info, scanned["alias_stmts"], scanned["units_stmts"], scanned["readonly_names"]
    )
    pos = {id(st): i for i, st in enumerate(statements)}
    added: list[Declaration] = []
    anon_groups, anon_build = [0], [0]  # two passes over the tables, each with its own `Anonymous<n>` count
    for dt in scanned["data_tables"]:
        head = statements[pos[id(dt.body[0])] - 1] if dt.body and id(dt.body[0]) in pos else None
        table = tbl_decls.get(id(head))
        try:
            groups = _field_groups(dt, env, var_info, by_display, by_base, anon_groups)
            tdf, _ = tables._build_data_table(dt, env, var_info, by_display, scanned["called"], by_base, anon_build)
            for f in tdf.fields:  # a later `Sample(1, Table.Field, ...)` source
                if f.type != 11:
                    var_info[f"{dt.name}.{f.name}".upper()] = tables.VarInfo(
                        f.type, list(f.subdims) or ([f.dimension] if f.dimension > 1 else []), name=f.name
                    )
            derived = [of.name for _, _, fs in groups for of in fs]
            if table is None or derived != [of.name for of in getattr(tdf, "_dup_fields") if not of.name_only]:
                continue
        except Exception as e:  # never raise out of the index
            failures.append(IndexFailure(-1, "", dt.line, f"fields of {dt.name}: {type(e).__name__}: {e}"))
            continue
        out = ix._fields.setdefault(id(table), [])
        table.modelled = True
        for instr, fn, fields in groups:
            for of in fields:
                if fn is not None:
                    arg = fn.args[0]
                    unit, tok_key, occ, site = fn, (str(arg.value).upper(), "string"), 0, "fieldnames"
                else:
                    unit, tok_key, occ, site = instr, (), None, "instruction"
                d = Declaration(
                    of.name,
                    "field",
                    "table",
                    None,
                    table,
                    unit.file,
                    unit.line,
                    instr,
                    unit,
                    unit,
                    pos[id(unit)],
                    occ,
                    None,
                    fn is not None,
                    tok_key=tok_key,
                    site=site,
                )
                added.append(d)
                out.append(d)
                ix._field_names.setdefault((id(table), of.name.upper()), []).append(d)
    ix.declarations += tuple(added)


def _field_groups(dt, env, var_info, by_display, by_base, anon=None):
    """`[(output instruction, its FieldNames statement | None, [_OutField])]` for one table, the
    fields exactly as `tables._build_data_table` builds them (name_only fields dropped)."""
    groups: list = []
    pending: list = []
    pending_src = None
    instr = None

    def flush(fnames):
        nonlocal pending, pending_src, instr
        fields = pending
        if pending_src is not None and by_base:
            fields = tables._split_by_alias(pending, pending_src, var_info, by_base, env)
        groups.append((instr, fnames, [of for of in fields if not of.name_only]))
        pending, pending_src, instr = [], None, None

    for s in dt.body:
        kw = tables._kw(s)
        if kw == "FIELDNAMES":
            if s.args and isinstance(s.args[0], S.String) and pending:
                pending = tables._apply_fieldnames(pending, s.args[0].value)
                flush(s)
        elif kw in tables._OUTPUT_KEYWORDS:
            if pending:
                flush(None)
            s2 = tables._alias_source(s, var_info, by_display, by_base)
            pending = tables._emit_output_fields(s2, var_info, by_display, env, by_base, anon)
            pending_src = s2.args[1] if len(s2.args) > 1 and not any(f.struct_members for f in pending) else None
            instr = s
        elif kw == "FFT":
            if pending:
                flush(None)
            pending, pending_src, instr = [tables._fft_field(s, var_info, by_display, env, anon)], None, s
    if pending:
        flush(None)
    return groups


def _scope_tables(ix: BindingIndex, decls: Iterable[Declaration]) -> None:
    for d in decls:
        ix._by_name.setdefault(d.name.upper(), []).append(d)
        key = d.name.upper()
        if d.scope == "global":
            ix._glob.setdefault(key, []).append(d)
        elif d.scope == "routine" and d.routine is not None:
            ix._local.setdefault(id(d.routine), {}).setdefault(key, []).append(d)
            if d.unverified_scope:
                ix._loose.setdefault(key, []).append(d)


def build(program, profile) -> BindingIndex:
    """Index `program` (a `syntax.Program`, e.g. `AnalysisResult.program`) for `profile` (a
    profile name or a `Profile`). Never raises for any program; a statement the indexer cannot
    handle is recorded in `BindingIndex.failures` and contributes no records. `ValueError` for an
    unknown profile name, as `profiles.get`."""
    prof = profile if isinstance(profile, profiles.Profile) else profiles.get(profile)
    ix = BindingIndex(prof)
    try:
        _build(ix, program)
    except Exception as e:  # never raise out of the index
        ix.failures += (IndexFailure(-1, "", 0, f"internal error: {e!r}"),)
    return ix


def _build(ix: BindingIndex, program) -> None:
    statements = list(getattr(program, "statements", None) or [])
    b = _Builder(ix)
    failures: list[IndexFailure] = []
    unparsed: list = []
    for i, st in enumerate(statements):
        b.idx, b.unit, b.counts = i, st, {}
        d0, r0, o0 = len(b.decls), len(b.refs), len(b.order)
        saved = (b.routine, b.struct, b.consttable)
        try:
            if isinstance(st, S.Unparsed):
                unparsed.append(st)
            else:
                if isinstance(st, S.SubDef):
                    b.routine = st
                b.statement(st)
        except Exception as e:
            del b.decls[d0:], b.refs[r0:], b.order[o0:]
            b.routine, b.struct, b.consttable = saved
            failures.append(
                IndexFailure(i, getattr(st, "file", ""), getattr(st, "line", 0), f"{type(e).__name__}: {e}")
            )
        if closes_routine(st):
            b.routine = None
    ix.declarations = tuple(b.decls)
    ix.references = tuple(b.refs)
    ix.unparsed = tuple(unparsed)
    _scope_tables(ix, b.decls)
    try:
        _add_data_table_fields(ix, program, statements, b.decls, failures)
    except Exception as e:
        failures.append(IndexFailure(-1, "", 0, f"table fields: {type(e).__name__}: {e}"))
    for unit, rec in b.order:
        ix._by_unit.setdefault(id(unit), []).append(rec)
    for ref in b.refs:  # emission order: bases before members
        try:
            ix._finish_reference(ref)
        except Exception as e:
            ref.resolved, ref.ambiguity, ref.container = (), "", ()
            failures.append(IndexFailure(ref.index, ref.file, ref.line, f"resolve {ref.name}: {type(e).__name__}: {e}"))
        for d in ref.resolved:
            ix._refs_to.setdefault(id(d), []).append(ref)
    ix.failures = tuple(failures)
