"""Derive `-z` (TDF) and `-l` (table list) output from a parsed `Program`.

Rules are reverse-engineered black-box against `CR6Comp.exe`; see `docs/tdf-rules.md` for the
evidence and confidence tags. Section references below (`§N`) are to that document.

Entry points:
    build_tables(program, profile, consts) -> (tables, table_list_text, diagnostics_or_None)
    render_table_list(tables) -> str            # the `-l`/`-c` text, given tables in TDF order
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import consteval
from . import syntax as S
from .tdf import TdfField, TdfTable

DATA = Path(__file__).resolve().parent / "data"

# ---------------------------------------------------------------- type codes (§1, §8)

# TDF field type byte. Confirmed by oracle: 2 UINT2, 6 LONG, 7 FP2, 9 IEEE4, 11 ASCII, 14 NSEC,
# 17 BOOL8, 18 IEEE8, 28 BOOLEAN. UINT1/UINT4 are guesses (never produced in an experiment; see
# docs/tdf-rules.md open question 12).
TYPE_CODES = {
    "FLOAT": 9, "IEEE4": 9, "FP2": 7, "LONG": 6, "BOOLEAN": 28, "BOOL8": 17,
    "STRING": 11, "DOUBLE": 18, "IEEE8": 18, "UINT2": 2, "NSEC": 14,
    "UINT1": 1, "UINT4": 4,
}

_UNIT_SECONDS = {"USEC": 1e-6, "MSEC": 1e-3, "SEC": 1, "MIN": 60, "HR": 3600, "DAY": 86400}
_UNIT_NS = {"USEC": 1_000, "MSEC": 1_000_000, "SEC": 1_000_000_000,
            "MIN": 60_000_000_000, "HR": 3_600_000_000_000, "DAY": 86_400_000_000_000}

_STATUS_JSON = json.loads((DATA / "status_table.json").read_text())

# Auto-size (§10) constants. POOL1/DECREMENT are oracle-fitted (see docs/tdf-rules.md §10);
# everything under _mixed_pool_estimate is a low-confidence curve fit, not a derived formula.
_POOL1 = 4_119_534
_PER_TABLE_DECREMENT = 5198
_CARDOUT_BONUS_BYTES = 160
_EVENT_ALONE_OVERHEAD = 16
_EVENT_ALONE_POOL2 = 4_188_120
# Exact oracle measurements for a single no-DataInterval/DataEvent auto table, alone in the
# program (docs/tdf-rules.md §10): no flat `overhead` value reconciles all four simultaneously
# (checked exhaustively), so known byte-widths are looked up directly and only an unseen width
# falls back to the floor(Pool2/(bytes+16)) approximation.
_EVENT_ALONE_SIZES = {2: 232_674, 4: 209_406, 6: 190_368, 8: 174_504}
# Measured Pool (records * rate, low bound) for 1 DataInterval(1s) participant + e DataEvent/
# no-interval participants, all FP2 (2 bytes/record) -- docs/tdf-rules.md §10 "mixed regime".
_MIXED_POOL_BY_EVENT_COUNT = {1: 835_244, 2: 659_568, 3: 596_616, 4: 564_160, 5: 544_320}


def _fold(expr: S.Expr | None, env: consteval.Env, default=None):
    if expr is None:
        return default
    try:
        return consteval.value_of(expr, env)
    except consteval.NotConstant:
        return default


def _as_int(v, default=0) -> int:
    if v is None:
        return default
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- const int/float inference (§7)


def _const_is_int(expr: S.Expr | None, env: consteval.Env, int_names: dict) -> bool:
    """Whether a ConstTable `Const` expression folds to CRBasic-integer (-> LONG) rather than
    float (-> IEEE4). Oracle-confirmed (docs/tdf-rules.md §7, exp `consttypes`/`negconst`):
    integer literals, hex/binary literals, unary +/- of an int, True/False and predefined symbols,
    and +/-/* of two ints stay LONG; `/` is always IEEE4 even when exact; other operators are
    treated as float unless both operands are known int (best-effort, not exhaustively tested).
    """
    if isinstance(expr, S.Number):
        return isinstance(expr.value, int)
    if isinstance(expr, S.Name):
        upper = expr.name.upper()
        if upper in int_names:
            return int_names[upper]
        sym = env.profile.symbols.get(upper)
        if sym is not None:
            return isinstance(sym.get("value"), int)
        return True  # unknown name (already resolved as constant elsewhere): assume int
    if isinstance(expr, S.Unary):
        if expr.op == "NOT":
            return True
        return _const_is_int(expr.operand, env, int_names)
    if isinstance(expr, S.Binary):
        if expr.op == "/":
            return False
        if expr.op in ("+", "-", "*"):
            return (_const_is_int(expr.left, env, int_names)
                    and _const_is_int(expr.right, env, int_names))
        if expr.op in ("\\", "MOD", "AND", "OR", "XOR", "=", "<>", "<", ">", "<=", ">=",
                       "EQV", "IMP", "<<", ">>"):
            return True
        return False  # "^" and anything else: unverified, assume float
    return True


# ---------------------------------------------------------------- variable registry


@dataclass
class VarInfo:
    """A declared Public/Dim variable: its TDF-relevant shape, for Public-table fields and for
    resolving DataTable output instructions' `Source` arrays (§3, §8, §9)."""

    type_code: int
    shape: list[int] = field(default_factory=list)   # declared array dims, e.g. [10] or [2,3]
    stored_len: int | None = None                     # STRING only: rounded storage length
    units: str = ""
    name: str = ""                                     # declared spelling (CRBasic is
    # case-insensitive; a `Source` reference may use different casing than the declaration, but
    # the default output field name follows the declaration -- oracle-confirmed, ftpclient_4.CR6:
    # `Public Count As Long` + `Sample(1,count,LONG)` -> field name `Count`, not `count`).

    def full_shape(self) -> list[int]:
        s = list(self.shape)
        if self.stored_len is not None:
            s.append(self.stored_len)
        return s

    def collapsed(self) -> tuple[int, list[int]]:
        """(dim, subdims) per the §3 collapse rule."""
        shape = self.full_shape()
        if not shape:
            return 1, []
        if len(shape) == 1 and shape[0] == 1:
            return 1, []
        dim = 1
        for n in shape:
            dim *= n
        return dim, shape


def _string_stored_len(n: int) -> int:
    """§8: smallest multiple of 4 strictly greater than n."""
    return (n // 4 + 1) * 4


def _var_shape(v: S.VarDecl, env: consteval.Env) -> list[int]:
    dims = []
    for d in v.dims:
        n = _as_int(_fold(d, env), default=1)
        dims.append(max(n, 1))
    return dims


def _var_type_code(v: S.VarDecl) -> int:
    t = (v.type or "FLOAT").upper()
    return TYPE_CODES.get(t, 9)


def _string_size_expr(v: S.VarDecl) -> S.Expr | None:
    """`v.string_size`, working around a parser quirk: `As String * 60 = "x"` currently parses
    the size and the initializer together as one `Binary(op='=', left=60, right="x")` node
    instead of splitting them; the real size is the left side."""
    e = v.string_size
    if isinstance(e, S.Binary) and e.op == "=":
        return e.left
    return e


def _make_var_info(v: S.VarDecl, env: consteval.Env) -> VarInfo:
    type_code = _var_type_code(v)
    shape = _var_shape(v, env)
    stored_len = None
    if (v.type or "").upper() == "STRING":
        # A bare `As String` with no `* n` defaults to n=20 (oracle-confirmed: formatlong_1.CR6's
        # `FormatString As String` and ctype_1.CR6's `Type(6) As String` both round to dim=24 per
        # element, matching n=20 -- `(20 div 4 + 1) * 4`).
        n = _as_int(_fold(_string_size_expr(v), env), default=20)
        stored_len = _string_stored_len(max(n, 1))
    return VarInfo(type_code, shape, stored_len, name=v.name)


# ---------------------------------------------------------------- statement block scanning


def _kw(s: S.Stmt) -> str:
    return getattr(s, "keyword", "").upper() if isinstance(s, S.Instruction) else ""


@dataclass
class ConstTableDecl:
    name: str | None
    hidden: int
    consts: list[S.ConstDecl]
    line: int


@dataclass
class DataTableDecl:
    name: str
    trig: S.Expr | None
    size_expr: S.Expr | None
    body: list[S.Stmt]
    line: int


def _scan_program(program: S.Program):
    """One pass over the flat statement list: collects Public/Dim declarations (in order),
    ConstTable blocks, DataTable blocks, Alias/Units statements and the SlowSequence count.
    DataTable and ConstTable bodies do not nest (CRBasic), so simple non-nested pairing suffices.
    """
    publics: list[S.VarDecl] = []
    all_vars: list[S.VarDecl] = []          # Public + Dim (+ Static), for Source lookups
    const_tables: list[ConstTableDecl] = []
    data_tables: list[DataTableDecl] = []
    alias_stmts: list[S.Instruction] = []
    units_stmts: list[S.Instruction] = []
    readonly_names: set[str] = set()
    slow_seq_count = 0

    stmts = program.statements
    n = len(stmts)
    i = 0
    while i < n:
        s = stmts[i]
        kw = _kw(s)
        if isinstance(s, S.Declaration) and s.kind in ("PUBLIC", "DIM", "STATIC"):
            all_vars.extend(s.vars)
            if s.kind == "PUBLIC":
                publics.extend(s.vars)
            i += 1
            continue
        if kw == "CONSTTABLE":
            args = s.args
            name = args[0].name if args and isinstance(args[0], S.Name) else None
            hidden = _as_int(args[1].value, 0) if len(args) > 1 and isinstance(args[1], S.Number) else 0
            j = i + 1
            body: list[S.ConstDecl] = []
            while j < n and _kw(stmts[j]) != "ENDCONSTTABLE":
                if isinstance(stmts[j], S.ConstDecl):
                    body.append(stmts[j])
                j += 1
            const_tables.append(ConstTableDecl(name, hidden, body, s.line))
            i = j + 1
            continue
        if kw == "DATATABLE":
            args = s.args
            name = args[0].name if args and isinstance(args[0], S.Name) else ""
            trig = args[1] if len(args) > 1 else None
            size_expr = args[2] if len(args) > 2 else None
            j = i + 1
            body: list[S.Stmt] = []
            while j < n and _kw(stmts[j]) != "ENDTABLE":
                body.append(stmts[j])
                j += 1
            data_tables.append(DataTableDecl(name, trig, size_expr, body, s.line))
            i = j + 1
            continue
        if kw == "SLOWSEQUENCE":
            slow_seq_count += 1
            i += 1
            continue
        if kw == "ALIAS":
            alias_stmts.append(s)
            i += 1
            continue
        if kw == "UNITS":
            units_stmts.append(s)
            i += 1
            continue
        if kw == "READONLY":
            for arg in s.args:
                if isinstance(arg, S.Name):
                    readonly_names.add(arg.name.upper())
            i += 1
            continue
        i += 1

    called: set[str] = set()
    for s in stmts:
        kw2 = _kw(s)
        if kw2 == "CALLTABLE" and s.args and isinstance(s.args[0], S.Name):
            called.add(s.args[0].name.upper())
        elif kw2 == "ACCEPTDATARECORDS" and len(s.args) > 2 and isinstance(s.args[2], S.Name):
            # AcceptDataRecords(PakBusAddr, TableNo, DestTableName): populates DestTableName
            # without a `CallTable` (oracle-confirmed, acceptdatarecords_1.CR6: a fixed-size
            # table named only here still gets its declared size, not the uncalled `size=1`).
            called.add(s.args[2].name.upper())

    return dict(publics=publics, all_vars=all_vars, const_tables=const_tables,
                data_tables=data_tables, alias_stmts=alias_stmts, units_stmts=units_stmts,
                readonly_names=readonly_names, slow_seq_count=slow_seq_count, called=called)


# ---------------------------------------------------------------- Status table (§4)


_STATUS_INSERTIONS = [
    ("SkippedSystemScan", "SkippedSlowScan", 6, False, ""),
    ("LastSystemScan", "LastSlowScan", 14, True, ""),
    ("SystemProcTime", "SlowProcTime", 6, True, "usec"),
    ("MaxSystemProcTime", "MaxSlowProcTime", 6, False, "usec"),
]


def _status_table(slow_seq_count: int) -> TdfTable:
    fields = []
    for f in _STATUS_JSON["fields"]:
        fields.append(TdfField(f["type"], f["read_only"], f["name"], list(f["aliases"]),
                                f["processing"], f["units"], f["description"], f["begin"],
                                f["dim"], list(f["subdims"])))
    if slow_seq_count >= 1:
        if slow_seq_count == 1:
            dim, subdims = 1, []
        else:
            dim, subdims = slow_seq_count, [slow_seq_count]
        for after, name, type_code, ro, units in _STATUS_INSERTIONS:
            new_field = TdfField(type_code, ro, name, [], "", units, "", 1, dim, list(subdims))
            idx = next(k for k, existing in enumerate(fields) if existing.name == after)
            fields.insert(idx + 1, new_field)
    return TdfTable("Status", 1, _STATUS_JSON["time_type"], tuple(_STATUS_JSON["time_into"]),
                     tuple(_STATUS_JSON["interval"]), fields)


# ---------------------------------------------------------------- Public table (§8)


def _resolve_alias_target(expr: S.Expr | None) -> tuple[str, int | None]:
    """`Var` -> (Var, None); `Var(i)` -> (Var, i) for a single constant index."""
    if isinstance(expr, S.Name):
        return expr.name.upper(), None
    if isinstance(expr, S.Index) and expr.args:
        idx = expr.args[0]
        if isinstance(idx, S.Number):
            return expr.name.upper(), _as_int(idx.value)
    if isinstance(expr, S.Index):
        return expr.name.upper(), None
    return "", None


def _build_public_table(publics: list[S.VarDecl], var_info: dict[str, VarInfo],
                         alias_stmts: list[S.Instruction], units_stmts: list[S.Instruction],
                         readonly_names: set[str]):
    """Returns (TdfTable('Public') or None, display_name -> TdfField, base_name -> [TdfField])."""
    if not publics:
        return None, {}, {}

    # base_name(upper) -> list of aliased element indices (1-based) -> alias display name
    aliased_elements: dict[str, dict[int, str]] = {}
    whole_alias: dict[str, str] = {}
    for a in alias_stmts:
        if len(a.args) < 2 or not isinstance(a.args[1], S.Name):
            continue
        base, idx = _resolve_alias_target(a.args[0])
        alias_name = a.args[1].name
        if idx is None:
            whole_alias[base] = alias_name
        else:
            aliased_elements.setdefault(base, {})[idx] = alias_name

    fields: list[TdfField] = []
    by_display: dict[str, TdfField] = {}
    by_base: dict[str, list[TdfField]] = {}

    for v in publics:
        base = v.name.upper()
        info = var_info[base]
        display_name = whole_alias.get(base, v.name)
        ro = base in readonly_names

        elements = aliased_elements.get(base)
        if elements and info.shape and len(info.shape) == 1:
            array_size = info.shape[0]
            full_shape = info.full_shape()
            group: list[TdfField] = []
            pos = 1
            while pos <= array_size:
                if pos in elements:
                    f = TdfField(info.type_code, ro, elements[pos], [], "", "", "", 1, 1, [])
                    group.append(f)
                    pos += 1
                else:
                    run_start = pos
                    while pos <= array_size and pos not in elements:
                        pos += 1
                    run_len = pos - run_start
                    f = TdfField(info.type_code, ro, v.name, [], "", "", "", run_start,
                                 run_len, list(full_shape))
                    group.append(f)
            fields.extend(group)
            for f in group:
                by_display[f.name.upper()] = f
            by_base[base] = group
            continue

        dim, subdims = info.collapsed()
        f = TdfField(info.type_code, ro, display_name, [], "", "", "", 1, dim, subdims)
        fields.append(f)
        by_display[display_name.upper()] = f
        by_base[base] = [f]

    for u in units_stmts:
        if len(u.args) < 2 or not isinstance(u.args[1], S.String):
            continue
        target, label = u.args[0], u.args[1].value
        targets: list[TdfField] = []
        if isinstance(target, S.Index) and not target.args:
            targets = by_base.get(target.name.upper(), [])
        elif isinstance(target, (S.Name, S.Index)):
            nm = target.name.upper()
            if nm in by_display:
                targets = [by_display[nm]]
            else:
                targets = by_base.get(nm, [])
        for f in targets:
            f.units = label

    return TdfTable("Public", 1, 14, (0, 0), (0, 0), fields), by_display, by_base


# ---------------------------------------------------------------- ConstTable (§7)


def _build_const_tables(const_tables: list[ConstTableDecl], env: consteval.Env) -> list[TdfTable]:
    tables = []
    int_names: dict[str, bool] = {}
    for ct in const_tables:
        fields = []
        for c in ct.consts:
            is_str = isinstance(c.value, S.String)
            if c.type:
                # An explicit `Const X As <Type> = ...` (oracle-confirmed,
                # mqttpublishconsttable_1.CR6: `Const myDouble As Double = 987.654321` -> IEEE8,
                # not the IEEE4 the literal alone would infer) overrides literal-based inference.
                type_code = TYPE_CODES.get(c.type.upper(), TYPE_CODES["IEEE4"])
                is_ascii = type_code == TYPE_CODES["STRING"]
                dim, subdims = (80, [80]) if is_ascii else (1, [])
                fields.append(TdfField(type_code, False, c.name, [], "", "", "", 1, dim, subdims))
                int_names[c.name.upper()] = type_code == TYPE_CODES["LONG"]
            elif is_str:
                fields.append(TdfField(TYPE_CODES["STRING"], False, c.name, [], "", "", "",
                                        1, 80, [80]))
                int_names[c.name.upper()] = True
            else:
                is_int = _const_is_int(c.value, env, int_names)
                int_names[c.name.upper()] = is_int
                type_code = TYPE_CODES["LONG"] if is_int else TYPE_CODES["IEEE4"]
                fields.append(TdfField(type_code, False, c.name, [], "", "", "", 1, 1, []))
        if ct.name is None:
            continue  # bare ConstTable: no TDF table (§7)
        fields.append(TdfField(TYPE_CODES["BOOLEAN"], False, "ApplyAndRestart", [], "", "", "",
                                1, 1, []))
        tables.append(TdfTable(ct.name, 1, 14, (0, 0), (0, 0), fields))
    return tables


# ---------------------------------------------------------------- DataTableInfo (§6)


def _datatableinfo_table(n: int) -> TdfTable:
    def dims(n):
        return [] if n == 1 else [n]

    fields = [
        TdfField(11, True, "DataTableName", [], "", "", "", 1, 24 * n, [n, 24]),
        TdfField(6, False, "SkippedRecord", [], "", "", "", 1, n, dims(n)),
        TdfField(6, True, "DataRecordSize", [], "", "records", "", 1, 2 * n, [n, 2]),
        TdfField(9, True, "SecsPerRecord", [], "", "", "", 1, n, dims(n)),
        TdfField(9, True, "DataFillDays", [], "", "days", "", 1, 2 * n, [n, 2]),
        TdfField(11, True, "DataFilled", [], "", "", "", 1, 64 * n, [n, 2, 32]),
        TdfField(14, True, "LastPublish", [], "", "", "", 1, n, dims(n)),
    ]
    return TdfTable("DataTableInfo", 1, 14, (0, 0), (0, 0), fields)


# ---------------------------------------------------------------- DataTable output fields (§9)


def _source_name(expr: S.Expr | None, var_info: dict[str, VarInfo] | None = None) -> str:
    raw = None
    if isinstance(expr, (S.Name, S.Index)):
        raw = expr.name
    if raw is None:
        return "Anonymous"
    if var_info is not None:
        info = var_info.get(raw.upper())
        if info is not None and info.name:
            return info.name  # declared spelling; CRBasic is case-insensitive at the use site
    return raw


def _source_shape(expr: S.Expr | None, var_info: dict[str, VarInfo], env: consteval.Env,
                   reps: int) -> tuple[int, list[int]]:
    """(begin, subdims) for an output field's Source, per §3."""
    if isinstance(expr, S.Index):
        info = var_info.get(expr.name.upper())
        array_shape = list(info.shape) if info and info.shape else [reps]
        begin = 1
        if expr.args:
            begin = _as_int(_fold(expr.args[0], env, 1), 1)
        return begin, array_shape
    return 1, []


def _units_for_source(expr: S.Expr | None, var_info: dict[str, VarInfo],
                       by_display: dict[str, TdfField]) -> str:
    name = _source_name(expr, var_info).upper()
    f = by_display.get(name)
    if f is not None:
        return f.units
    info = var_info.get(name)
    return info.units if info else ""


@dataclass
class _OutField:
    name: str
    type_code: int
    read_only: bool
    processing: str
    begin: int
    dim: int
    subdims: list[int]
    units: str = ""
    bytes: int = 0


_TYPE_BYTES = {2: 2, 6: 4, 7: 2, 9: 4, 11: 0, 14: 8, 17: 1, 18: 8, 28: 4, 1: 1, 4: 4}


def _type_bytes(type_code: int, dim_for_string: int = 0) -> int:
    if type_code == 11:
        return dim_for_string
    return _TYPE_BYTES.get(type_code, 4)


def _emit_output_fields(instr: S.Instruction, var_info: dict, by_display: dict,
                         env: consteval.Env) -> list[_OutField]:
    kw = instr.keyword.upper()
    a = instr.args

    def type_of(idx):
        name = getattr(a[idx], "name", None) if idx < len(a) else None
        return TYPE_CODES.get((name or "").upper(), 9)

    def reps_of():
        return _as_int(_fold(a[0], env, 1), 1) if a else 1

    def shaped(src, reps):
        begin, shape = _source_shape(src, var_info, env, reps)
        subdims = shape if shape else ([] if reps == 1 else [reps])
        units = _units_for_source(src, var_info, by_display)
        return begin, subdims, units

    if kw == "SAMPLE":
        reps = reps_of()
        src = a[1] if len(a) > 1 else None
        tcode = type_of(2)
        begin, subdims, units = shaped(src, reps)
        return [_OutField(_source_name(src, var_info), tcode, True, "Smp", begin, reps, subdims, units)]

    if kw in ("AVERAGE", "TOTALIZE", "STDDEV"):
        reps = reps_of()
        src = a[1] if len(a) > 1 else None
        tcode = type_of(2)
        begin, subdims, units = shaped(src, reps)
        suffix, tag = {"AVERAGE": ("_Avg", "Avg"), "TOTALIZE": ("_Tot", "Tot"),
                       "STDDEV": ("_Std", "Std")}[kw]
        name = _source_name(src, var_info) + suffix
        return [_OutField(name, tcode, True, tag, begin, reps, subdims, units)]

    if kw in ("MAXIMUM", "MINIMUM"):
        reps = reps_of()
        src = a[1] if len(a) > 1 else None
        tcode = type_of(2)
        begin, subdims, units = shaped(src, reps)
        time_flag = False
        if len(a) > 4:
            time_flag = str(getattr(a[4], "name", "")).upper() == "TRUE"
        base = _source_name(src, var_info)
        vsuf, vtag, tsuf, ttag = (("_Max", "Max", "_TMx", "TMx") if kw == "MAXIMUM"
                                   else ("_Min", "Min", "_TMn", "TMn"))
        out = [_OutField(base + vsuf, tcode, True, vtag, begin, reps, subdims, units)]
        if time_flag:
            out.append(_OutField(base + tsuf, 14, True, ttag, 1, reps,
                                  [] if reps == 1 else [reps], ""))
        return out

    if kw == "MEDIAN":
        reps = reps_of()
        src = a[1] if len(a) > 1 else None
        tcode = type_of(3)
        begin, subdims, units = shaped(src, reps)
        return [_OutField(_source_name(src, var_info) + "_Med", tcode, True, "Med", begin, reps, subdims, units)]

    if kw == "MOMENT":
        reps = reps_of()
        src = a[1] if len(a) > 1 else None
        tcode = type_of(3)
        begin, subdims, units = shaped(src, reps)
        return [_OutField(_source_name(src, var_info) + "_MMT", tcode, True, "MMT", begin, reps, subdims, units)]

    if kw == "SAMPLEMAXMIN":
        reps = reps_of()
        src = a[1] if len(a) > 1 else None
        tcode = type_of(2)
        begin, subdims, units = shaped(src, reps)
        return [_OutField(_source_name(src, var_info) + "_SMM", tcode, True, "SMM", begin, reps, subdims, units)]

    if kw == "COVARIANCE":
        src = a[1] if len(a) > 1 else None
        tcode = type_of(2)
        n_cov = _as_int(_fold(a[4], env, 1), 1) if len(a) > 4 else 1
        units = _units_for_source(src, var_info, by_display)
        return [_OutField(_source_name(src, var_info) + "_Cov", tcode, True, "Cov", 1, n_cov,
                           [] if n_cov == 1 else [n_cov], units)]

    if kw == "WINDVECTOR":
        speed = a[1] if len(a) > 1 else None
        tcode = type_of(3)
        output_opt = _as_int(_fold(a[7], env, 0), 0) if len(a) > 7 else 0
        dim = {0: 3, 1: 2, 2: 4, 3: 1, 4: 2}.get(output_opt, 3)
        units = _units_for_source(speed, var_info, by_display)
        return [_OutField(_source_name(speed, var_info) + "_WVc", tcode, True, "WVc", 1, dim,
                           [] if dim == 1 else [dim], units)]

    if kw == "HISTOGRAM":
        bin_select = a[0] if a else None
        tcode = type_of(1)
        bins = _as_int(_fold(a[3], env, 1), 1) if len(a) > 3 else 1
        wtval = _fold(a[5], env, 0.0) if len(a) > 5 else 0.0
        lolim = _fold(a[6], env, 0.0) if len(a) > 6 else 0.0
        uplim = _fold(a[7], env, 0.0) if len(a) > 7 else 0.0
        tag = f"Hst,{bins},{float(wtval):.4e},{float(lolim):.4e},{float(uplim):.4e}"
        units = _units_for_source(bin_select, var_info, by_display)
        return [_OutField(_source_name(bin_select, var_info) + "_Hst", tcode, True, tag, 1, bins,
                           [] if bins == 1 else [bins], units)]

    return []


def _apply_fieldnames(fields: list[_OutField], arg: str) -> list[_OutField]:
    parts = [p.strip() for p in arg.split(",")]
    if len(fields) == 1 and len(parts) == 1 and fields[0].dim == 1:
        name, _, desc = parts[0].partition(":")
        fields[0].name = name.strip()
        if desc:
            fields[0].processing += ":" + desc.strip()
        return fields
    if len(fields) == 1 and fields[0].dim > 1:
        # One name per repetition (§9, exp #33/#37): split a single Reps>1 field into scalar
        # fields. Fewer names than Reps (oracle-confirmed, SbdPayload_tester.CR6:
        # `Average(2,FinApp_Neutrons(),...) FieldNames("Neutrons")` on a 2-element array) renames
        # only the first len(parts) elements individually and leaves the rest as one field under
        # the original default name, keeping the source array's full declared subdims.
        base = fields[0]
        out = []
        n_named = min(len(parts), base.dim)
        for i in range(n_named):
            name, _, desc = parts[i].partition(":")
            proc = base.processing + (":" + desc.strip() if desc else "")
            out.append(_OutField(name.strip(), base.type_code, base.read_only, proc,
                                  base.begin + i, 1, [], base.units))
        remaining = base.dim - n_named
        if remaining > 0:
            out.append(_OutField(base.name, base.type_code, base.read_only, base.processing,
                                  base.begin + n_named, remaining, base.subdims, base.units))
        return out
    for f, part in zip(fields, parts):
        name, _, desc = part.partition(":")
        f.name = name.strip()
        if desc:
            f.processing += ":" + desc.strip()
    return fields


# ---------------------------------------------------------------- user DataTable header (§5)

_TIME_KEYWORDS = {"DATAINTERVAL", "DATAEVENT"}
_OUTPUT_KEYWORDS = {"SAMPLE", "AVERAGE", "MAXIMUM", "MINIMUM", "TOTALIZE", "STDDEV", "MEDIAN",
                    "MOMENT", "SAMPLEMAXMIN", "HISTOGRAM", "WINDVECTOR", "COVARIANCE"}


def _interval_from_args(args: list, env: consteval.Env, into_idx: int, val_idx: int, unit_idx: int):
    unit_name = (getattr(args[unit_idx], "name", "") or "SEC").upper() if len(args) > unit_idx else "SEC"
    into_val = _fold(args[into_idx], env, 0) if len(args) > into_idx else 0
    interval_val = _fold(args[val_idx], env, 0) if len(args) > val_idx else 0
    ns_per_unit = _UNIT_NS.get(unit_name, _UNIT_NS["SEC"])
    into_ns = int(round(float(into_val or 0) * ns_per_unit))
    interval_ns = int(round(float(interval_val or 0) * ns_per_unit))
    into = (into_ns // 1_000_000_000, into_ns % 1_000_000_000)
    interval = (interval_ns // 1_000_000_000, interval_ns % 1_000_000_000)
    return into, interval, float(interval_val or 0), unit_name


def _build_data_table(dt: DataTableDecl, env: consteval.Env, var_info: dict,
                       by_display: dict, called: set[str]) -> tuple[TdfTable, dict]:
    """Returns (TdfTable, meta) where meta carries auto-sizing inputs (bytes_per_record,
    interval_seconds, has_interval, has_card) for tables whose declared Size is -1.
    """
    into = (0, 0)
    interval = (0, 0)
    interval_seconds = None
    display_interval = None  # (value, unit_text) for the -l "Interval:" line
    has_card = False

    out_fields: list[_OutField] = []
    pending: list[_OutField] = []

    for s in dt.body:
        kw = _kw(s)
        if kw == "DATAINTERVAL":
            into, interval, ival, unit = _interval_from_args(s.args, env, 0, 1, 2)
            interval_seconds = ival * (_UNIT_SECONDS.get(unit, 1))
            display_interval = (ival, unit)
        elif kw == "DATAEVENT":
            pass  # interval/into stay (0,0) (§5, oracle-confirmed)
        elif kw in ("CARDOUT", "TABLEFILE"):
            has_card = True
        elif kw == "FIELDNAMES":
            if s.args and isinstance(s.args[0], S.String) and pending:
                pending = _apply_fieldnames(pending, s.args[0].value)
                out_fields.extend(pending)
                pending = []
        elif kw in _OUTPUT_KEYWORDS:
            if pending:
                out_fields.extend(pending)
            pending = _emit_output_fields(s, var_info, by_display, env)
    if pending:
        out_fields.extend(pending)

    fields = []
    for of in out_fields:
        fields.append(TdfField(of.type_code, of.read_only, of.name, [], of.processing, of.units,
                                of.processing, of.begin, of.dim, of.subdims))

    size_val = _fold(dt.size_expr, env, -1)
    size_val = _as_int(size_val, -1)

    bytes_per_record = sum(_field_record_bytes(of) for of in out_fields)

    meta = dict(size_val=size_val, bytes_per_record=bytes_per_record,
                interval_seconds=interval_seconds, has_card=has_card,
                display_interval=display_interval, called=dt.name.upper() in called)

    table = TdfTable(dt.name, 1, 14, into, interval, fields)
    if display_interval is not None:
        table._interval_display = display_interval  # type: ignore[attr-defined]
    return table, meta


def _field_record_bytes(of: "_OutField") -> int:
    """Per-record byte cost of one output field: for ASCII (type 11) `dim` already IS the byte
    length; for numeric types it is `dim` (element count) times the type's own byte width."""
    if of.type_code == 11:
        return of.dim
    return of.dim * _TYPE_BYTES.get(of.type_code, 4)


# ---------------------------------------------------------------- auto-size (§10)


def _resolve_auto_sizes(tables_meta: list[tuple[TdfTable, dict]]):
    autos = [(t, m) for t, m in tables_meta if m["size_val"] == -1 and m["called"]]
    if not autos:
        return
    interval_ones = [(t, m) for t, m in autos if m["interval_seconds"]]
    event_ones = [(t, m) for t, m in autos if not m["interval_seconds"]]
    n = len(autos)

    rate = 0.0
    for t, m in autos:
        secs = m["interval_seconds"] or 1.0
        rate += m["bytes_per_record"] / secs

    if event_ones and interval_ones:
        e = len(event_ones)
        if e in _MIXED_POOL_BY_EVENT_COUNT:
            pool = _MIXED_POOL_BY_EVENT_COUNT[e]
        else:
            # extrapolate past the measured e=1..5 range using the last observed delta (low
            # confidence -- docs/tdf-rules.md §10 "mixed regime" is not reconciled to a formula)
            keys = sorted(_MIXED_POOL_BY_EVENT_COUNT)
            last, prev = _MIXED_POOL_BY_EVENT_COUNT[keys[-1]], _MIXED_POOL_BY_EVENT_COUNT[keys[-2]]
            delta = last - prev
            pool = max(last - delta * (e - keys[-1]), 1)
        if len(interval_ones) > 1:
            pool = _POOL1 - _PER_TABLE_DECREMENT * (n - 1)  # unverified combination; fallback
    elif event_ones and not interval_ones:
        if n == 1:
            t, m = event_ones[0]
            b = m["bytes_per_record"]
            records = _EVENT_ALONE_SIZES.get(b, _EVENT_ALONE_POOL2 // (b + _EVENT_ALONE_OVERHEAD))
            t.size = records
            return
        pool = _POOL1 - _PER_TABLE_DECREMENT * (n - 1)
    else:
        pool = _POOL1 - _PER_TABLE_DECREMENT * (n - 1)

    duration = pool / rate if rate else 0
    for t, m in autos:
        secs = m["interval_seconds"] or 1.0
        records = int(duration / secs)
        if m["has_card"] and m["bytes_per_record"]:
            records += _CARDOUT_BONUS_BYTES // m["bytes_per_record"]
        t.size = max(records, 1)


def _apply_sizes(tables_meta: list[tuple[TdfTable, dict]]):
    for t, m in tables_meta:
        if not m["called"]:
            # A table with no `CallTable` anywhere in the compiled program never gets its
            # DataInterval/DataEvent finalised either (oracle-confirmed, docs/tdf-rules.md §13):
            # `size=1` *and* `interval`/`into` reset to (0,0), as if declared with neither.
            t.size = 1
            t.interval = (0, 0)
            t.time_into = (0, 0)
        elif m["size_val"] != -1:
            t.size = m["size_val"]
    _resolve_auto_sizes(tables_meta)


# ---------------------------------------------------------------- top-level


def build_tables(program: S.Program, profile, consts: dict):
    env = consteval.Env(profile, dict(consts))
    scanned = _scan_program(program)

    var_info: dict[str, VarInfo] = {}
    for v in scanned["all_vars"]:
        var_info[v.name.upper()] = _make_var_info(v, env)

    # Build the Public table first (source-variable units lookups for output fields need its
    # display-name map), but keep its position in the TDF (after DataTableInfo/ConstTables, §2).
    public_table, by_display, by_base = _build_public_table(
        scanned["publics"], var_info, scanned["alias_stmts"], scanned["units_stmts"],
        scanned["readonly_names"])

    tables: list[TdfTable] = [_status_table(scanned["slow_seq_count"])]

    data_table_metas = []
    for dt in scanned["data_tables"]:
        table, meta = _build_data_table(dt, env, var_info, by_display, scanned["called"])
        tables.append(table)
        data_table_metas.append((table, meta))

    if scanned["data_tables"]:
        tables.append(_datatableinfo_table(len(scanned["data_tables"])))

    tables.extend(_build_const_tables(scanned["const_tables"], env))

    if public_table is not None:
        tables.append(public_table)

    _apply_sizes(data_table_metas)

    table_list_text = render_table_list(tables)
    return tables, table_list_text, None


# ---------------------------------------------------------------- `-l` table list (docs/cli.md)


def _explode_indices(subdims: list[int], drop_last: bool, begin: int = 1, dim: int = 1):
    """Row-major (last dim fastest) index tuples for the `dim` stored elements starting at the
    1-based linear position `begin` within `subdims` (after dropping the trailing string-storage
    dim for ASCII fields, per §3/§8's alias-prefix/suffix fields and §3's Reps<array-size case:
    both need the *slice*, not every valid coordinate of the full declared shape)."""
    dims = subdims[:-1] if drop_last and subdims else subdims
    if not dims:
        yield ()
        return
    total = 1
    for d in dims:
        total *= d
    strides = [1] * len(dims)
    acc = 1
    for i in range(len(dims) - 1, -1, -1):
        strides[i] = acc
        acc *= dims[i]
    start = max(min(begin - 1, total - 1), 0)
    count = max(min(dim, total - start), 0)
    for lin in range(start, start + count):
        coord = []
        rem = lin
        for d, stride in zip(dims, strides):
            coord.append(rem // stride + 1)
            rem %= stride
        yield tuple(coord)


def render_table_list(tables: list[TdfTable]) -> str:
    lines = []
    for t in tables:
        if t.name == "Status":
            continue
        lines.append("-" * 52)
        lines.append(f"Table: {t.name}")
        disp = getattr(t, "_interval_display", None)
        if disp is not None:
            value, unit = disp
            value = int(value) if float(value).is_integer() else value
            lines.append(f"Interval: {value} {unit}")
        elif t.interval != (0, 0):
            # Fallback when the original declared unit wasn't carried on the table (shouldn't
            # happen for a DataInterval-bearing table built via _build_data_table).
            secs = t.interval[0]
            value, unit = _seconds_to_display(secs)
            lines.append(f"Interval: {value} {unit}")
        lines.append("Fields:")
        for f in t.fields:
            drop_last = f.type == 11 and bool(f.subdims)
            dims_after_drop = f.subdims[:-1] if drop_last else f.subdims
            if f.type == 11 and len(dims_after_drop) >= 2:
                # Oracle-confirmed quirk (DataTableInfo's DataFilled at n=1,2,7; a 2D Public
                # String array, filelist_1.CR6's `Files(3,10) As String * 25`): an ASCII field
                # with 2+ non-string dims does NOT get the plain Cartesian product every other
                # multi-dim field gets (contrast DataRecordSize/DataFillDays, same [n,2] shape but
                # type LONG/IEEE4, which *do* show the full product): the `-l` lister instead
                # computes both displayed coordinates via `divmod` by the *string storage length*
                # (`stored_len` = `subdims[-1]`) rather than by the true adjacent declared
                # dimension -- confirmed exactly for `Files(3,10)` (`stored_len`=28, wrong stride
                # vs. the true 10): entries 1..28 show as `(1,1)..(1,28)`, then `(2,1)`, `(2,2)`
                # for the last 2 of its 30 true elements, not `(1,1)..(1,10)` then `(2,1)..`.
                # Total entry count is still correct (`dim / stored_len`). A field with 3+
                # non-string dims is unverified; this always yields exactly 2 coordinates like
                # the confirmed cases, which may not generalise. Mechanism not understood (a
                # legacy quirk in the exe's own lister); reproduced, not derived.
                stored_len = f.subdims[-1]
                total = f.dimension // stored_len if stored_len else 0
                explode_iter = ((lin // stored_len + 1, lin % stored_len + 1)
                                 for lin in range(total))
            else:
                explode_iter = _explode_indices(f.subdims, drop_last, f.begin_index, f.dimension)
            for idx in explode_iter:
                suffix = "(" + ",".join(str(i) for i in idx) + ")" if idx else ""
                line = f" {f.name}{suffix}"
                if f.units:
                    line += f"\tUnits: {f.units}"
                lines.append(line)
        lines.append("")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def _seconds_to_display(secs: int) -> tuple[int, str]:
    for unit, mult in (("DAY", 86400), ("HR", 3600), ("MIN", 60)):
        if secs % mult == 0 and secs // mult > 0:
            return secs // mult, unit
    return secs, "SEC"
