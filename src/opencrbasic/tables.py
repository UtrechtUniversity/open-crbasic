"""Derive `-z` (TDF) and `-l` (table list) output from a parsed `Program`.

Rules are reverse-engineered black-box against `CR6Comp.exe`; see `docs/tdf-rules.md` for the
evidence and confidence tags. Section references below (`§N`) are to that document.

Entry points:
    build_tables(program, profile, consts) -> (tables, table_list_text, diagnostics_or_None)
    render_table_list(tables) -> str            # the `-l`/`-c` text, given tables in TDF order
"""

import dataclasses
import json
import math
import struct
from dataclasses import dataclass, field
from typing import TypedDict
from fractions import Fraction
from pathlib import Path

from . import consteval
from . import syntax as S
from .tdf import TdfField, TdfTable

# `TableName.<property>` members of a data (or Status) table that are not fields
# (extracted_chm/Info/datatableaccess.htm "Data Table Access").
TABLE_PROPERTIES = ("BytesPerRecord", "EventCount", "EventEnd", "Output", "Record", "TableBytes",
                    "TableFull", "TableSize", "TimeStamp")

DATA = Path(__file__).resolve().parent / "data"

# ---------------------------------------------------------------- type codes (§1, §8)

# TDF field type byte. Confirmed by oracle: 2 UINT2, 6 LONG, 7 FP2, 9 IEEE4, 11 ASCII, 14 NSEC,
# 17 BOOL8, 18 IEEE8, 28 BOOLEAN. UINT4=3 oracle-confirmed, UINT1 a guess (never produced; see
# docs/tdf-rules.md open question 12).
TYPE_CODES = {
    "FLOAT": 9, "IEEE4": 9, "FP2": 7, "LONG": 6, "BOOLEAN": 28, "BOOL8": 17,
    "STRING": 11, "DOUBLE": 18, "IEEE8": 18, "UINT2": 2, "NSEC": 14,
    "UINT1": 1, "UINT4": 3,
}


def _dtype_code(expr: S.Expr | None, env: consteval.Env) -> int:
    """TDF type code of an output instruction's DataType argument. A constant expression or named
    `Const` resolves through its value (the predefined data-type symbols equal the TDF codes)."""
    if isinstance(expr, S.Name) and expr.name.upper() in TYPE_CODES:
        return TYPE_CODES[expr.name.upper()]
    v = _fold(expr, env, None)
    return int(v) if isinstance(v, (int, float)) and v in TYPE_CODES.values() else 9

_UNIT_SECONDS = {"USEC": 1e-6, "MSEC": 1e-3, "SEC": 1, "MIN": 60, "HR": 3600, "DAY": 86400}
_UNIT_NS = {"USEC": 1_000, "MSEC": 1_000_000, "SEC": 1_000_000_000,
            "MIN": 60_000_000_000, "HR": 3_600_000_000_000, "DAY": 86_400_000_000_000}
# Numeric Scan units (`checker/scan_time.UNIT_NAMES`), keyed by the predefined symbol value.
_UNIT_NAME_BY_VALUE = {0: "USEC", 1: "MSEC", 2: "SEC", 3: "MIN", 4: "HR", 5: "DAY"}

_STATUS_JSON = json.loads((DATA / "status_table.json").read_text())

# Auto-size (§10): one finalisation algorithm for every table mix (`docs/tdf-rules.md` §10, rewritten
# 2026-10-10 from a re-read of the finalisation loop). Free memory is the compiler's own malloc probe,
# a binary search that returns 2**22 - 1 whenever every allocation succeeds. All table arithmetic is
# 32-bit float (`_f32`), as in the real compiler.
_FREE_MEMORY = 4_194_303
_DEFAULT_PROBE_BUFFER_BYTES = 1024
_DEFAULT_PROBE_OVERHEAD = 16
_TABLE_INIT_RESERVED = 4096


def _f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def _f32_safe(x) -> float:
    """float32 rounding that overflows to +-inf (CPython 3.13 raises on some builds)."""
    try:
        return _f32(float(x))
    except OverflowError:
        return float("inf") if x > 0 else float("-inf")


_INT32_MAX = 2147483647
_TWO31 = 2147483648.0


def size_class(raw):
    """How the real compiler reads a `DataTable` size argument (oracle, all four loggers,
    `docs/tdf-rules.md` "DataTable size argument"): the constant is rounded to float32 and
    truncated toward zero (an integer constant that fits in 32 bits is kept exactly). Returns `("auto",)` for -1, `("fixed", N, flagged)` for any other
    integer (`flagged`: it was negative, `N = -size`; zero is 1), `("edge", sign)` when the
    float32 is exactly +-2^31, `("inf",)` for `+inf`, `("toxic",)` beyond it (and `-inf`), `None`
    when the value is not a number."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if isinstance(raw, int) and -_TWO31 <= raw < _TWO31:
        v = raw                                  # an integer literal that fits is kept exactly
    else:
        f = _f32_safe(raw)
        if f != f:
            return None
        if f == float("inf"):
            return ("inf",)
        if f == _TWO31:
            return ("edge", 1)
        if f == -_TWO31:
            return ("edge", -1)
        if abs(f) > _TWO31:
            return ("toxic",)
        v = int(f)
    if v == -1:
        return ("auto",)
    if v == 0:
        return ("fixed", 1, False)
    return ("fixed", min(-v, _INT32_MAX), True) if v < -1 else ("fixed", v, False)


def declared_size(raw, cr3xx: bool, bytes_per_record: int) -> int | None:
    """The TDF size of a called table with this size argument, or `None` when it is auto-sized
    (the caller computes it). CR6/CR1000X auto-size every negative or out-of-range size and keep
    only `+inf` as `INT32_MAX`; CR300/CR350 keep `-N` as a fixed `N`, saturate +-2^31, and give
    a beyond-range size ("toxic", `checker/storage.py`) the minimum a table can get, three pages
    less the first block: `(12288 // R) - (4096 // R + 1)` records, `R = bytes + 16`."""
    c = size_class(raw)
    if c is None or c[0] == "auto":
        return None
    if c[0] == "inf":
        return _INT32_MAX
    if cr3xx:
        if c[0] == "edge":
            return _INT32_MAX
        if c[0] == "toxic":
            rec = bytes_per_record + 16
            return max(12288 // rec - (4096 // rec + 1), 1)
        return c[1]
    if c[0] in ("edge", "toxic") or c[2]:
        return None
    return c[1]


def _fold(expr: S.Expr | None, env: consteval.Env, default=None):
    if expr is None:
        return default
    try:
        return consteval.value_of(expr, env)
    except consteval.NotConstant:
        return default


def _unit_value(expr: S.Expr | None, env: consteval.Env) -> int | None:
    """Units argument of `Scan` or `DataInterval` as an integer: the predefined name (`Min`), its
    number (`3`) and a `Const` agree, and a fraction is truncated toward zero (`1.9` is mSec, `-0.5`
    is uSec; oracle, round 20 W5, `docs/tdf-rules.md` "Numeric and Const units"). `None` when the
    argument does not fold to a finite number."""
    v = _fold(expr, env, None)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return int(v)


def _scan_unit_name(expr: S.Expr | None, env: consteval.Env) -> str:
    """`_UNIT_NS` key of a Scan units argument (`_unit_value`). A non-constant reads as seconds. A
    value outside 0..5 reads as uSec, the unit the real compiler leaves no table charge for."""
    v = _unit_value(expr, env)
    if v is None:
        return "SEC"
    return _UNIT_NAME_BY_VALUE.get(v, "USEC")


def _as_int(v, default=0) -> int:
    if v is None:
        return default
    try:
        return int(round(float(v)))
    except (TypeError, ValueError, OverflowError):
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
    struct: dict | None = None                         # StructureType body when typed as one
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


class _Scanned(TypedDict):
    uses_ch400: bool
    struct_types: dict[str, dict]
    uses_cpi: bool
    publics: list[S.VarDecl]
    all_vars: list[S.VarDecl]
    const_tables: list[ConstTableDecl]
    data_tables: list[DataTableDecl]
    alias_stmts: list[S.Instruction]
    units_stmts: list[S.Instruction]
    readonly_names: set[str]
    slow_seq_count: int
    called: set[str]


def _scan_program(program: S.Program) -> _Scanned:
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
    struct_types: dict[str, dict] = {}   # NAME -> {"members": [VarDecl], "units": {}, "ro": set}

    stmts = program.statements
    n = len(stmts)
    i = 0
    while i < n:
        s = stmts[i]
        kw = _kw(s)
        if isinstance(s, S.Instruction) and (kw == "STRUCTURETYPE"):
            st = {"members": [], "units": {}, "ro": set()}
            if s.args and isinstance(s.args[0], S.Name):
                struct_types[s.args[0].name.upper()] = st
            i += 1
            while i < n and _kw(stmts[i]) != "ENDSTRUCTURETYPE":
                m = stmts[i]
                if isinstance(m, S.Declaration):
                    st["members"].extend(m.vars)
                elif isinstance(m, S.Instruction) and (_kw(m) == "UNITS" and len(m.args) > 1 and isinstance(m.args[1], S.String)):
                    st["units"][getattr(m.args[0], "name", "").upper()] = m.args[1].value
                elif isinstance(m, S.Instruction) and (_kw(m) == "READONLY"):
                    st["ro"].update(a.name.upper() for a in m.args if isinstance(a, S.Name))
                i += 1
            i += 1
            continue
        if isinstance(s, S.Declaration) and s.kind in ("PUBLIC", "DIM", "STATIC"):
            all_vars.extend(s.vars)
            if s.kind == "PUBLIC":
                publics.extend(s.vars)
            i += 1
            continue
        if isinstance(s, S.Instruction) and (kw == "CONSTTABLE"):
            args = s.args
            name = args[0].name if args and isinstance(args[0], S.Name) else None
            hidden = _as_int(args[1].value, 0) if len(args) > 1 and isinstance(args[1], S.Number) else 0
            j = i + 1
            const_body: list[S.ConstDecl] = []
            while j < n and _kw(stmts[j]) != "ENDCONSTTABLE":
                candidate = stmts[j]
                if isinstance(candidate, S.ConstDecl):
                    const_body.append(candidate)
                j += 1
            const_tables.append(ConstTableDecl(name, hidden, const_body, s.line))
            i = j + 1
            continue
        if isinstance(s, S.Instruction) and (kw == "DATATABLE"):
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
        if isinstance(s, S.Instruction) and (kw == "ALIAS"):
            alias_stmts.append(s)
            i += 1
            continue
        if isinstance(s, S.Instruction) and (kw == "UNITS"):
            units_stmts.append(s)
            i += 1
            continue
        if isinstance(s, S.Instruction) and (kw == "READONLY"):
            for arg in s.args:
                if isinstance(arg, S.Name):
                    readonly_names.add(arg.name.upper())
            i += 1
            continue
        i += 1

    called: set[str] = set()

    for s in _flat(stmts):
        kw2 = _kw(s)
        if kw2 == "CALLTABLE" and s.args and isinstance(s.args[0], S.Name):
            called.add(s.args[0].name.upper())
        elif kw2 == "ACCEPTDATARECORDS" and len(s.args) > 2 and isinstance(s.args[2], S.Name):
            # AcceptDataRecords(PakBusAddr, TableNo, DestTableName): populates DestTableName
            # without a `CallTable` (oracle-confirmed, acceptdatarecords_1.CR6: a fixed-size
            # table named only here still gets its declared size, not the uncalled `size=1`).
            called.add(s.args[2].name.upper())

    publics = [v for v in publics if (v.type or "").upper() not in struct_types]
    uses_ch400 = any(_kw(s) == "CH400STATUS" for s in stmts)
    uses_cpi = any(_kw(s).startswith("CDM_") or _kw(s) == "CPIFILESEND" for s in stmts)

    return _Scanned(uses_ch400=uses_ch400, struct_types=struct_types, uses_cpi=uses_cpi, publics=publics, all_vars=all_vars, const_tables=const_tables,
                data_tables=data_tables, alias_stmts=alias_stmts, units_stmts=units_stmts,
                readonly_names=readonly_names, slow_seq_count=slow_seq_count, called=called)


def _flat(ss):
    """`ss` with the statements of every inline `If c Then ... Else ...` spliced in after it (an
    inline `If c Then CallTable T` calls T too)."""
    for x in ss:
        yield x
        yield from _flat((getattr(x, "inline", None) or []) + (getattr(x, "inline_else", None) or []))


def _scan_weights(stmts: list[S.Stmt], env: consteval.Env) -> dict[str, float]:
    """Maps each `CallTable`d table name (upper) to the interval, in seconds, of the nearest
    enclosing `Scan` block (a top-level `Scan` or a `SlowSequence`'s own `Scan`) that contains its
    `CallTable` statement. Used as the auto-size weight for a table with no `DataInterval` (see
    `_build_data_table`/`_resolve_auto_sizes` and docs/tdf-rules.md §10 "scan-interval weighting",
    oracle-confirmed against `FinApp_tester.CR6`: real weight is the enclosing scan's interval, not
    a flat 1 second). `SubScan` is transparent (does not change the enclosing weight) -- untested,
    matches §4's "SubScan has no effect" finding for the Status table.
    """
    stack: list[float] = []
    weights: dict[str, float] = {}
    for s in stmts:
        kw = _kw(s)
        if isinstance(s, S.Instruction) and (kw == "SCAN"):
            secs = None
            if s.args:
                val = _fold(s.args[0], env, None)
                # `_fold` legitimately returns a non-numeric Python value (e.g. a string literal's
                # own text) for a `Scan` interval the real compiler itself rejects elsewhere with
                # its own specific message (`Scan Interval parameter must be a non-negative
                # integer`, an instruction-specific value check, not this module's concern, see
                # `docs/fuzz-findings.md` "Crash"). This function unconditionally runs for every
                # `Scan` block regardless of whether that check ran or found anything, so it must
                # tolerate the value being un-usable here rather than assume `float()` succeeds.
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    unit_name = _scan_unit_name(s.args[1], env) if len(s.args) > 1 else "SEC"
                    secs = float(val) * _UNIT_SECONDS.get(unit_name, 1)
            stack.append(secs if secs else 1.0)
        elif kw == "NEXTSCAN":
            if stack:
                stack.pop()
        elif isinstance(s, S.Instruction) and (kw == "CALLTABLE" and s.args and isinstance(s.args[0], S.Name) and stack):
            weights[s.args[0].name.upper()] = stack[-1]
    return weights


def _scan_calls(stmts: list[S.Stmt], env: consteval.Env) -> dict[str, list[float | None]]:
    """Maps each table name (upper) to the interval (seconds, None when not a plain constant) of
    every enclosing `Scan` of each of its `CallTable` statements that is lexically inside one;
    a call in a `Sub` or outside any `Scan` adds nothing (`checker/storage.py`)."""
    stack: list[float | None] = []
    calls: dict[str, list[float | None]] = {}
    for s in _flat(stmts):
        kw = _kw(s)
        if kw == "SCAN":
            secs = None
            if s.args:
                val = _fold(s.args[0], env, None)
                if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                    unit_name = _scan_unit_name(s.args[1], env) if len(s.args) > 1 else "SEC"
                    secs = float(val) * _UNIT_SECONDS.get(unit_name, 1)
            stack.append(secs)
        elif kw == "NEXTSCAN":
            if stack:
                stack.pop()
        elif kw == "CALLTABLE" and s.args and isinstance(s.args[0], S.Name) and stack:
            calls.setdefault(s.args[0].name.upper(), []).append(stack[-1])
    return calls


def _last_call_interval(stmts: list[S.Stmt], env: consteval.Env) -> dict[str, float | None]:
    """The call interval (seconds) the real compiler leaves on each table: that of the last
    `CallTable` in source order (the enclosing `Scan`'s interval; `None` for a call outside every
    `Scan`, e.g. in a `Sub`, or one whose interval is not a plain constant). `checker/storage.py`."""
    stack: list[float | None] = []
    last: dict[str, float | None] = {}
    for s in _flat(stmts):
        kw = _kw(s)
        if kw == "SCAN":
            secs = None
            if s.args:
                val = _fold(s.args[0], env, None)
                if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                    unit_name = _scan_unit_name(s.args[1], env) if len(s.args) > 1 else "SEC"
                    secs = float(val) * _UNIT_SECONDS.get(unit_name, 1)
            stack.append(secs)
        elif kw == "NEXTSCAN":
            if stack:
                stack.pop()
        elif kw == "CALLTABLE" and s.args and isinstance(s.args[0], S.Name):
            last[s.args[0].name.upper()] = stack[-1] if stack else None
    return last


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


def _build_struct_tables(all_vars: list[S.VarDecl], struct_types: dict, env: consteval.Env):
    """docs/tdf-rules.md §2: every Public/Dim variable of a StructureType is a table named after
    the variable (size = product of its dims, one field per member), in reverse declaration order."""
    out = []
    for v in reversed(all_vars):
        st = struct_types.get((v.type or "").upper())
        if st is None:
            continue
        size = 1
        for d in _var_shape(v, env):
            size *= d
        fields = []
        for m in st["members"]:
            info = _make_var_info(m, env)
            dim, subdims = info.collapsed()
            fields.append(TdfField(info.type_code, m.name.upper() in st["ro"], m.name, [], "",
                                    st["units"].get(m.name.upper(), ""), "", 1, dim, subdims))
        out.append(TdfTable(v.name, size, 14, (0, 0), (0, 0), fields))
    return out


_DEG_C = "\xef\xbf\xbdC"
# CH400Status instruction table (docs/tdf-rules.md §2): (name, units); None = 28-byte string. The
# temperature units are the bytes EF BF BD 'C' (a UTF-8 replacement character, then C) -- reproduced
# as latin-1 text.
_CH400_FIELDS = (
    ("ChargeAVoltage", 'Volts'),
    ("ChargeACurrent", 'Amps'),
    ("ChargeBVoltage", 'Volts'),
    ("ChargeBCurrent", 'Amps'),
    ("InputChargeStatus", None),
    ("InputCurrentLimit", 'Amps'),
    ("BattAVoltage", 'Volts'),
    ("BattACurrent", 'Amps'),
    ("BattAChargeStatus", None),
    ("BattASetVoltage", 'Volts'),
    ("BattACurrentLimit", 'Amps'),
    ("BattADepthofDischarge", '%'),
    ("BattBVoltage", 'Volts'),
    ("BattBCurrent", 'Amps'),
    ("BattBChargeStatus", None),
    ("BattBSetVoltage", 'Volts'),
    ("BattBCurrentLimit", 'Amps'),
    ("BattBDepthofDischarge", '%'),
    ("SystemVoltage", 'Volts'),
    ("SystemCurrent", 'Amps'),
    ("AlwaysOn12Voltage", 'Volts'),
    ("AlwaysOn12Current", 'Amps'),
    ("Switched12Voltage", 'Volts'),
    ("Switched12Current", 'Amps'),
    ("Switched24Voltage", 'Volts'),
    ("Switched24Current", 'Amps'),
    ("PowerSwitchState", None),
    ("MaxBoardTemp", _DEG_C),
    ("MinBoardTemp", _DEG_C),
    ("PanelTemp", _DEG_C),
    ("BattAProbeTemp", _DEG_C),
    ("BattBProbeTemp", _DEG_C),
)
def _ch400status_table() -> TdfTable:
    fields = [TdfField(11 if u is None else 9, True, n, [], "", u or "", "", 1,
                        28 if u is None else 1, [28] if u is None else [])
              for n, u in _CH400_FIELDS]
    return TdfTable("CH400Status", 1, 14, (0, 0), (0, 0), fields)


def _cpistatus_table() -> TdfTable:
    """docs/tdf-rules.md §2: present iff a `CDM_*` instruction or `CPIFileSend` is used."""
    def f(t: int, ro: bool, name: str, dim: int = 1, sub: tuple[int, ...] = ()) -> TdfField:
        return TdfField(t, ro, name, [], "", "", "", 1, dim, list(sub))
    fields = [f(9, True, "BusLoad")] + [f(6, False, n) for n in (
        "ModuleReportCount", "ActiveModules", "BuffErr", "RxErrMax", "TxErrMax", "FrameErr")]
    fields.append(f(11, False, "ModuleInfo", 6400, (50, 128)))
    return TdfTable("CPIStatus", 1, 14, (0, 0), (0, 0), fields)


# ---------------------------------------------------------------- Public table (§8)


def _resolve_alias_target(expr: S.Expr | None, var_info: dict | None = None) -> tuple[str, int | None]:
    """`Var` -> (Var, None); `Var(i)` -> (Var, i) for a single constant index; `Var(i,j)` of a
    declared multi-dimensional array -> its row-major flat 1-based index."""
    if isinstance(expr, S.Name):
        return expr.name.upper(), None
    if isinstance(expr, S.Index) and len(expr.args) > 1 and var_info is not None:
        info = var_info.get(expr.name.upper())
        numbers = [a for a in expr.args if isinstance(a, S.Number)]
        if info is not None and len(info.shape) == len(expr.args) == len(numbers):
            flat = 0
            for k, a in enumerate(numbers):
                stride = 1
                for d in info.shape[k + 1:]:
                    stride *= d
                flat += (_as_int(a.value) - 1) * stride
            return expr.name.upper(), flat + 1
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
        base, idx = _resolve_alias_target(a.args[0], var_info)
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
        if elements and info.shape:
            array_size = 1
            for d in info.shape:
                array_size *= d
            full_shape = info.full_shape()
            group: list[TdfField] = []
            pos = 1
            es = info.stored_len or 1   # String elements are stored_len bytes (docs/tdf-rules.md §9a)
            while pos <= array_size:
                if pos in elements:
                    f = TdfField(info.type_code, ro, elements[pos], [], "", "", "", 1, es,
                                 [es] if info.stored_len else [])
                    f.alias_elem = True  # type: ignore[attr-defined]
                    f.alias_of = (v.name, pos)  # type: ignore[attr-defined]
                    group.append(f)
                    pos += 1
                else:
                    run_start = pos
                    while pos <= array_size and pos not in elements:
                        pos += 1
                    run_len = pos - run_start
                    f = TdfField(info.type_code, ro, v.name, [], "", "", "",
                                 (run_start - 1) * es + 1, run_len * es, list(full_shape))
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
            if nm in by_base:
                targets = by_base[nm]   # Units on the declared name covers every alias split too
            elif nm in by_display:
                targets = [by_display[nm]]
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

    fields = [] if n == 0 else [
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


def _source_info(expr: S.Expr | None, var_info: dict[str, VarInfo]) -> VarInfo | None:
    if isinstance(expr, (S.Name, S.Index)):
        return var_info.get(expr.name.upper())
    if isinstance(expr, S.Member) and isinstance(expr.base, (S.Name, S.Index)):
        return var_info.get(f"{expr.base.name}.{expr.member}".upper())
    return None


def _source_name(expr: S.Expr | None, var_info: dict[str, VarInfo] | None = None) -> str:
    if var_info is not None:
        info = _source_info(expr, var_info)
        if info is not None and info.name:
            return info.name  # declared spelling; CRBasic is case-insensitive at the use site
    if isinstance(expr, (S.Name, S.Index)):
        return expr.name
    return expr.member if isinstance(expr, S.Member) else "Anonymous"


def _is_anonymous_source(expr: S.Expr | None, var_info: dict, env: consteval.Env) -> bool:
    """docs/tdf-rules.md §9c: a Source that is not a variable (literal, `Const`, `NAN`/`True`, any
    expression, a function call) is named `Anonymous<n>`; a `Table.Field` is not (it takes the
    referenced field's name)."""
    if isinstance(expr, S.Index):
        return expr.name.upper() not in var_info
    if isinstance(expr, S.Name):
        return expr.name.upper() not in var_info and (
            _fold(expr, env, None) is not None or expr.name.upper() == "NAN")
    return expr is not None and not isinstance(expr, S.Member)


def _anonymous_string_len(expr: S.Expr | None, var_info: dict, env: consteval.Env) -> int:
    """Character count that sizes a non-variable String source (§9c): a literal is its own length,
    a String variable its stored length, `+`/`&` of those the sum. String functions propagate a
    temporary buffer length, not the length of their runtime result. Unknown/numeric sources
    retain the 20-character conversion default (stored 24)."""
    def known(e):
        v = _fold(e, env, None)
        if isinstance(v, str):
            return len(v)
        info = _source_info(e, var_info)
        if info is not None:
            return info.stored_len
        if isinstance(e, (S.Name, S.Index)):
            fn = env.profile.functions.get(e.name.upper())
            if isinstance(e, S.Index) and fn and fn["kind"] == 312:
                code = fn["code"]
                if code == 229:  # Chr / Chr$
                    return 2
                if code == 243:  # Hex
                    return 0
                if code == 254:  # IPInfo
                    return 104
                if code in (239, 251) and e.args:
                    n = known(e.args[-1])
                    return (24 if n is None else n) + 60
                if code == 252 and e.args:  # FormatLongLong's single variable/string argument
                    return known(e.args[0])
                if code == 250 and len(e.args) == 3:  # Replace
                    first, last = known(e.args[0]), known(e.args[2])
                    return (24 if first is None else first) - 1 + (24 if last is None else last)
                if code in (230, 237, 238, 245, 246, 247, 248, 249) and e.args:
                    n = known(e.args[0])
                    return 24 if n is None else n
            return None
        if isinstance(e, S.Binary) and e.op in ("+", "&"):
            l, r = known(e.left), known(e.right)
            return None if l is None or r is None else l + r
        return None
    n = known(expr)
    return 20 if n is None else n


def _number_anonymous(fields: list, src: S.Expr | None, var_info: dict, env: consteval.Env,
                      anon: list[int] | None) -> None:
    """Give the fields derived from a non-variable Source its `Anonymous<n>` name; `anon[0]` is the
    program-wide count so far (one number per such Source, FieldNames or not)."""
    if anon is None or not _is_anonymous_source(src, var_info, env):
        return
    anon[0] += 1
    raw = _source_name(src, var_info)
    for f in fields:
        if f.name.startswith(raw):
            f.name = f"Anonymous{anon[0]}" + f.name[len(raw):]


def _source_shape(expr: S.Expr | None, var_info: dict[str, VarInfo], env: consteval.Env,
                   reps: int) -> tuple[int, list[int]]:
    """(begin, subdims) for an output field's Source, per §3: subdims are the *declared* shape of the
    source array whatever `reps` is; begin is the 1-based row-major flat index of the first element."""
    if isinstance(expr, S.Member) and isinstance(expr.base, (S.Name, S.Index)):
        info = var_info.get(f"{expr.base.name}.{expr.member}".upper())
        if info is not None:
            return 1, list(info.shape)
    if isinstance(expr, (S.Index, S.Name)):
        info = var_info.get(expr.name.upper())
        if isinstance(expr, S.Index) and info is None and (
                len(expr.args) != 1 or isinstance(expr.args[0], S.String)):
            # The source-name parser accepts a single integer subscript, even on a function,
            # but not a comma-separated argument list or a quoted string (§9c).
            return 1, []
        array_shape = list(info.shape) if info and info.shape else ([reps] if isinstance(expr, S.Index) else [])
        if array_shape == [1]:
            array_shape = []  # §3 collapse rule: a one-element array has no subdims
        begin = 1
        if isinstance(expr, S.Index) and expr.args:
            idx = [_as_int(_fold(a, env, 1), 1) for a in expr.args]
            if info and info.shape and len(idx) == len(info.shape) > 1:
                if not all(isinstance(a, (S.Number, S.Name)) for a in expr.args):
                    return 1, array_shape  # docs/tdf-rules.md §9b: expression subscripts give begin=1
                begin = 0
                for k, v in enumerate(idx):
                    stride = 1
                    for d in info.shape[k + 1:]:
                        stride *= d
                    begin += (v - 1) * stride
                begin += 1
            else:
                begin = idx[0]
        return begin, array_shape
    return 1, []


def _units_for_source(expr: S.Expr | None, var_info: dict[str, VarInfo],
                       by_display: dict[str, TdfField]) -> str:
    if isinstance(expr, S.Member) and isinstance(expr.base, (S.Name, S.Index)):
        info = var_info.get(expr.base.name.upper())
        if info is not None and info.struct is not None:
            return info.struct["units"].get(expr.member.upper(), "")
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
    # Duplicate-field-name bookkeeping only (`checker/instr_data.check_duplicate_fields`):
    # `renamed` marks a name set by `FieldNames`; `name_only` fields (FFT, CH400Status source)
    # take part in that check but are never written to the TDF or counted in the record size;
    # `placeholder` distinguishes a generated Anonymous label from a user field of that name;
    # `rng` is the element range `(lo, hi)` the field covers (`None`: never conflicts by range).
    renamed: bool = False
    name_only: bool = False
    ch400: bool = False
    placeholder: bool = False
    rng: tuple[int, int] | None = None
    unit_list: list | None = None   # per-element units when a FieldNames split must differ
    elem: int = 1  # bytes per element when splittable (a stored String element is stored_len)
    splittable: bool = False  # dim == Reps * element size: follows the Source's alias decomposition
    struct_members: int = 0   # >0: field of a StructureType source (members per element)
    member: str = ""


_FFT_UNBOUNDED = 1 << 30


def _element_range(src: S.Expr | None, begin: int, dim: int, env: consteval.Env):
    if isinstance(src, S.Index) and len(src.args) > 1:
        if dim != 1:
            return None
        flat = 0
        for k, a in enumerate(src.args):
            v = _fold(a, env, None)
            if not isinstance(v, (int, float)):
                return None
            flat += int(v) << (20 * k)
        return flat, flat
    return begin, begin + max(dim, 1) - 1


_TYPE_BYTES = {2: 2, 6: 4, 7: 2, 9: 4, 11: 0, 14: 8, 17: 1, 18: 8, 28: 4, 1: 1, 4: 4}


def _type_bytes(type_code: int, dim_for_string: int = 0) -> int:
    if type_code == 11:
        return dim_for_string
    return _TYPE_BYTES.get(type_code, 4)


def _elem_size(f: _OutField, info: VarInfo) -> int:
    return info.stored_len if (info.stored_len and f.type_code == 11) else 1


def _split_by_alias(fields: list[_OutField], src, var_info: dict, by_base: dict,
                    env: consteval.Env) -> list[_OutField]:
    """docs/tdf-rules.md §9a: an output field over an array that has element `Alias`es follows the
    Public table's field decomposition: one scalar field per aliased element (alias name + the
    instruction suffix, the alias' units), one field per un-aliased run (source name, `begin` =
    run start in the array). Only for fields with `dim == Reps * element size` (a `Covariance(6,A,...)`
    output of 21 values is one field named after the alias at its first element, unsplit); a
    `FieldNames`-named field is never split."""
    if not isinstance(src, (S.Name, S.Index)):
        return fields
    base = src.name.upper()
    groups = by_base.get(base)
    if not groups or not any(getattr(g, "alias_elem", False) for g in groups):
        return fields
    out = []
    declared = var_info[base].name
    for f in fields:
        if f.name_only or f.renamed or not f.name.startswith(declared):
            out.append(f)
            continue
        suffix = f.name[len(declared):].lstrip("0123456789")  # drop the flat-index digits (_idx_suffix)
        es = _elem_size(f, var_info[base])
        lo, hi = f.begin, f.begin + f.dim - 1
        if not f.splittable:
            # unsplit: named after the alias of the Source's first element, if it has one
            first = (_source_shape(src, var_info, env, 1)[0] - 1) * es + 1
            pos = 1
            for g in groups:
                n = es if getattr(g, "alias_elem", False) else g.dimension
                if pos <= first < pos + n:
                    if getattr(g, "alias_elem", False):
                        f.name = g.name + suffix
                    break
                pos += n
            out.append(f)
            continue
        pos = 1
        for g in groups:
            if getattr(g, "alias_elem", False):
                glo, ghi = pos, pos + es - 1
            else:
                glo, ghi = g.begin_index, g.begin_index + g.dimension - 1
            pos = ghi + 1
            if ghi < lo or glo > hi:
                continue
            if getattr(g, "alias_elem", False):
                out.append(_OutField(g.name + suffix, f.type_code, f.read_only, f.processing, 1, es,
                                      list(g.subdims), g.units, rng=(glo, ghi)))
            else:
                a, b = max(lo, glo), min(hi, ghi)
                out.append(_OutField(f.name, f.type_code, f.read_only, f.processing, a, b - a + 1,
                                      list(g.subdims), f.units, rng=(a, b)))
    return out


def _fft_field(s: S.Instruction, var_info: dict, by_display: dict, env: consteval.Env,
               anon: list[int] | None = None) -> _OutField:
    """`FFT(Source, DataType, N, Tint, Units, Option)` (docs/tdf-rules.md §9b): one read-only field
    `<Source>[<flat element index if > 1>]_FFT`; layout by `Option mod 10`, description `FFT,N,<Tint seconds %.4e>,<Option mod 100>`.
    A `Table.Field` Source this module cannot resolve stays a name-only placeholder (no TDF field);
    any other non-variable Source is a field named `Anonymous<n>_FFT` (§9c)."""
    a = s.args
    src = a[0] if a else None
    placeholder = src is None or (isinstance(src, S.Member) and not var_info.get(
        f"{getattr(src.base, 'name', '')}.{src.member}".upper()))
    if placeholder:
        return _OutField("Anonymous", 9, True, "FFT", 1, _FFT_UNBOUNDED, [], name_only=True,
                          placeholder=True, rng=(1, _FFT_UNBOUNDED))
    n = _as_int(_fold(a[2], env, 1), 1) if len(a) > 2 else 1
    tint = _fold(a[3], env, 0) if len(a) > 3 else 0
    unit = str(getattr(a[4], "name", "")).upper() if len(a) > 4 else "SEC"
    opt = _as_int(_fold(a[5], env, 0), 0) if len(a) > 5 else 0
    half = n // 2 + 1
    k = opt % 10
    if k == 5:
        dim, subdims = n, [n]
    elif k in (1, 3, 4):
        dim, subdims = half, [half]
    elif k in (0, 2):
        dim, subdims = 2 * half, [half, 2]
    else:
        dim, subdims = 2 * half, [half]
    first, shape = _source_shape(src, var_info, env, 1)
    if dim == 1:
        subdims = shape
    proc = "FFT,%d,%.4e,%d" % (n, float(tint) * _UNIT_SECONDS.get(unit, 1), opt % 100)
    out = _OutField(_source_name(src, var_info) + (str(first) if first > 1 else "") + "_FFT", (_dtype_code(a[1], env) if len(a) > 1 else 9), True,
                    proc, 1, dim, subdims, _units_for_source(src, var_info, by_display),
                    rng=(1, _FFT_UNBOUNDED))
    _number_anonymous([out], src, var_info, env, anon)
    return out


def _idx_suffix(src: S.Expr | None, var_info: dict, env: consteval.Env) -> str:
    """Flat first-element index appended to the source name when the Source is an array element
    other than the first (FFT, Covariance, Histogram, WindVector; docs/tdf-rules.md §9b)."""
    first, _ = _source_shape(src, var_info, env, 1)
    return str(first) if first > 1 else ""


def _alias_source(instr: S.Instruction, var_info: dict, by_display: dict, by_base: dict | None):
    if (by_base and len(instr.args) > 1 and isinstance(instr.args[1], S.Name)
            and instr.args[1].name.upper() not in var_info):
        # a Source that names an element Alias is that element of its array (docs/tdf-rules.md §9a)
        target = by_display.get(instr.args[1].name.upper())
        of = getattr(target, "alias_of", None)
        if of is not None:
            a = instr.args[1]
            idx = S.Number(a.line, a.file, value=of[1], text=str(of[1]))
            args = list(instr.args)
            args[1] = S.Index(a.line, a.file, name=of[0], args=[idx])
            instr = dataclasses.replace(instr, args=args)
    return instr


def _emit_output_fields(instr: S.Instruction, var_info: dict, by_display: dict,
                         env: consteval.Env, by_base: dict | None = None,
                         anon: list[int] | None = None) -> list[_OutField]:
    instr = _alias_source(instr, var_info, by_display, by_base)
    fields = _emit_struct_fields(instr, var_info, env)
    if fields is None:
        fields = _emit_output_fields_raw(instr, var_info, by_display, env)
        if len(instr.args) > 1 and isinstance(instr.args[1], (S.Name, S.Index)):
            info = var_info.get(instr.args[1].name.upper())
            reps = _fold(instr.args[0], env, None)
            if info is not None and isinstance(reps, (int, float)):
                for f in fields:
                    f.elem = _elem_size(f, info)
                    f.splittable = f.dim == int(reps) * f.elem
    src = instr.args[1] if len(instr.args) > 1 else None
    if instr.keyword.upper() == "SAMPLE" and isinstance(src, S.Name) \
            and src.name.upper() == "CH400STATUS" and "CH400STATUS" not in var_info:
        for f in fields:
            f.ch400 = True
    for f in fields:
        if f.rng is None and not f.ch400:
            f.rng = _element_range(src, f.begin, f.dim, env)
    if instr.keyword.upper() != "HISTOGRAM":
        _number_anonymous(fields, src, var_info, env, anon)
    return fields


_STRUCT_SUFFIX = {"SAMPLE": ("", "Smp"), "AVERAGE": ("_Avg", "Avg"), "TOTALIZE": ("_Tot", "Tot"),
                  "MAXIMUM": ("_Max", "Max"), "MINIMUM": ("_Min", "Min")}


def _emit_struct_fields(instr: S.Instruction, var_info: dict, env: consteval.Env):
    """docs/tdf-rules.md §9a: an output instruction whose Source is a StructureType variable emits
    one field per member per element (reps elements); None when not applicable."""
    kw = instr.keyword.upper()
    a = instr.args
    if kw not in _STRUCT_SUFFIX or len(a) < 2 or not isinstance(a[1], (S.Name, S.Index)):
        return None
    info = var_info.get(a[1].name.upper())
    if info is None or info.struct is None:
        return None
    if kw in ("MAXIMUM", "MINIMUM") and len(a) > 4 and str(getattr(a[4], "name", "")).upper() == "TRUE":
        return None  # time-of-extreme fields of a structure source: not probed
    reps = _as_int(_fold(a[0], env, 1), 1)
    if env.profile.name in ("cr300", "cr350"):
        reps = 1  # CR300/CR350 expand one element whatever Reps says (oracle, 2026-10-12)
    suffix, tag = _STRUCT_SUFFIX[kw]
    st = info.struct
    out = []
    for _ in range(reps):
        for m in st["members"]:
            mi = _make_var_info(m, env)
            dim, subdims = mi.collapsed()
            out.append(_OutField(m.name + suffix, mi.type_code, m.name.upper() in st["ro"], tag, 1,
                                  dim, subdims, st["units"].get(m.name.upper(), ""),
                                  struct_members=len(st["members"]), member=m.name))
    return out


def _emit_output_fields_raw(instr: S.Instruction, var_info: dict, by_display: dict,
                             env: consteval.Env) -> list[_OutField]:
    kw = instr.keyword.upper()
    a = instr.args

    def type_of(idx):
        return _dtype_code(a[idx], env) if idx < len(a) else 9

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
        info = _source_info(src, var_info)
        dim = reps
        if info is not None and info.stored_len is not None and tcode == 11:
            # String source (docs/tdf-rules.md §9a): element = stored_len bytes
            ln = info.stored_len
            dim = reps * ln
            subdims = info.full_shape()
            if isinstance(src, S.Index) and src.args:
                begin = (begin - 1) * ln + 1
        elif tcode == 11 and _is_anonymous_source(src, var_info, env):
            # a non-variable String source is as wide as its value (docs/tdf-rules.md §9c)
            dim = _string_stored_len(_anonymous_string_len(src, var_info, env))
            subdims = [dim]
            begin = ((begin - 1) * dim + 1) & 0xffffffff
            return [_OutField(_source_name(src, var_info), tcode, True, "Smp", begin, dim, subdims,
                              units, placeholder=True, elem=dim, splittable=True)]
        return [_OutField(_source_name(src, var_info), tcode, True, "Smp", begin, dim, subdims,
                          units, placeholder=not isinstance(src, (S.Name, S.Index)) and info is None)]

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
                                  [] if reps == 1 else [reps], units))
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
        return [_OutField(_source_name(src, var_info) + _idx_suffix(src, var_info, env) + "_Cov", tcode, True, "Cov", 1, n_cov,
                           [] if n_cov == 1 else [n_cov], units)]

    if kw == "WINDVECTOR":
        speed = a[1] if len(a) > 1 else None
        tcode = type_of(3)
        output_opt = _as_int(_fold(a[7], env, 0), 0) if len(a) > 7 else 0
        dim = {0: 3, 1: 2, 2: 4, 3: 1, 4: 2}.get(output_opt, 3)
        units = _units_for_source(speed, var_info, by_display)
        # per-element units by Output option (docs/tdf-rules.md §9a): speed units, then "Deg"
        ulist = {0: [units, "Deg", "Deg"], 1: [units, "Deg"], 2: [units, units, "Deg", "Deg"],
                 3: ["Deg"], 4: ["Deg", "Deg"]}.get(output_opt, [units] * dim)
        f = _OutField(_source_name(speed, var_info) + _idx_suffix(speed, var_info, env) + "_WVc", tcode, True, "WVc", 1, dim,
                      [] if dim == 1 else [dim], ulist[0])
        f.unit_list = ulist
        return [f]

    if kw == "HISTOGRAM":
        bin_select = a[0] if a else None
        tcode = type_of(1)
        bins = _as_int(_fold(a[3], env, 1), 1) if len(a) > 3 else 1
        wtval = _fold(a[5], env, 0.0) if len(a) > 5 else 0.0
        lolim = _fold(a[6], env, 0.0) if len(a) > 6 else 0.0
        uplim = _fold(a[7], env, 0.0) if len(a) > 7 else 0.0
        tag = f"Hst,{bins},{float(wtval):.4e},{float(lolim):.4e},{float(uplim):.4e}"
        units = _units_for_source(bin_select, var_info, by_display)
        return [_OutField(_source_name(bin_select, var_info) + _idx_suffix(bin_select, var_info, env) + "_Hst", tcode, True, tag, 1, bins,
                           [] if bins == 1 else [bins], units)]

    return []


def _apply_fieldnames(fields: list[_OutField], arg: str, profile_name: str = "") -> list[_OutField]:
    parts = arg.split(",")
    n_members = fields[0].struct_members if fields else 0
    if n_members and profile_name in ("cr300", "cr350"):
        return fields  # `Fieldnames` after a structure source renames nothing there (oracle, 2026-10-12)
    if n_members:
        # structure source: name k prefixes every member of element k (`<name>_<member>`), the
        # instruction's own suffix (`_Avg`...) is dropped; unnamed elements keep default names
        raw = arg.split(",")
        for k, f in enumerate(fields):
            if k // n_members < len(raw):
                nm, _, desc = raw[k // n_members].partition(":")
                f.name = nm + "_" + f.member
                if desc:
                    f.processing += ":" + desc
                f.renamed = True
        return fields
    if len(fields) == 1 and len(parts) == 1 and fields[0].dim == 1:
        name, _, desc = parts[0].partition(":")
        fields[0].name = name.strip()
        fields[0].renamed = True
        fields[0].placeholder = False
        fields[0].begin, fields[0].subdims = 1, []
        if desc:
            fields[0].processing += ":" + desc
        return fields
    if any(f.dim > 1 and f.splittable for f in fields) or (len(fields) == 1 and fields[0].dim > 1):
        # One name per repetition across the instruction's output fields in order (§9, exp #33/#37;
        # `Minimum(2,A,...,True)` + "M1,M2,T1,T2" names the Min then the TMn elements): each named
        # element is its own field and a field the names run out in keeps the remainder under the
        # original default name with the source array's full declared subdims (oracle-confirmed,
        # SbdPayload_tester.CR6: `Average(2,FinApp_Neutrons(),...) FieldNames("Neutrons")`).
        out = []
        names = list(parts)
        for base in fields:
            if not names:
                out.append(base)
                continue
            if not (base.dim > 1 and (base.splittable or len(fields) == 1)):
                name, _, desc = names.pop(0).partition(":")
                base.name = name.strip()
                base.renamed = True
                base.placeholder = False
                if desc:
                    base.processing += ":" + desc
                out.append(base)
                continue
            es = base.elem if base.splittable else 1
            n_named = min(len(names), base.dim // es)
            for i in range(n_named):
                name, _, desc = names.pop(0).partition(":")
                proc = base.processing + (":" + desc if desc else "")
                rng = None if base.rng is None else (base.rng[0] + i * es, base.rng[0] + (i + 1) * es - 1)
                u = base.unit_list[i] if base.unit_list and i < len(base.unit_list) else base.units
                out.append(_OutField(name.strip(), base.type_code, base.read_only, proc,
                                      1, es, [es] if es > 1 else [], u, renamed=True,
                                      name_only=base.name_only, placeholder=False, rng=rng))
            remaining = base.dim - n_named * es
            if remaining > 0:
                rng = None if base.rng is None else (base.rng[0] + n_named * es, base.rng[1])
                out.append(_OutField(base.name, base.type_code, base.read_only, base.processing,
                                      base.begin + n_named * es, remaining, base.subdims, base.units,
                                      name_only=base.name_only, placeholder=base.placeholder, rng=rng,
                                      splittable=base.splittable, elem=base.elem))
        return out
    for f, part in zip(fields, parts):
        name, _, desc = part.partition(":")
        f.name = name.strip()
        f.renamed = True
        f.placeholder = False
        if desc:
            f.processing += ":" + desc
    return fields


# ---------------------------------------------------------------- user DataTable header (§5)

_TIME_KEYWORDS = {"DATAINTERVAL", "DATAEVENT"}
_OUTPUT_KEYWORDS = {"SAMPLE", "AVERAGE", "MAXIMUM", "MINIMUM", "TOTALIZE", "STDDEV", "MEDIAN",
                    "MOMENT", "SAMPLEMAXMIN", "HISTOGRAM", "WINDVECTOR", "COVARIANCE"}


_UNIT_MON = 6  # predefined `MON`; a DataInterval unit only (`Scan` has no month)


def _interval_from_args(args: list, env: consteval.Env, into_idx: int, val_idx: int, unit_idx: int):
    """(TintoInt, Interval) as (sec, nsec) TDF pairs, the Interval value, and its unit name for the
    `-l` line. The units argument reads by value (`_unit_value`): 0..5 are the named units, 6 is MON
    (both TDF pairs (0,0) and TintoInt dropped, oracle round 20 W4), and any other value is invalid
    (Interval 1 Sec, TintoInt dropped; oracle round 20 W4). A non-constant units argument reads as
    seconds."""
    unit_val = _unit_value(args[unit_idx], env) if len(args) > unit_idx else None
    into_val = _fold(args[into_idx], env, 0) if len(args) > into_idx else 0
    interval_val = _fold(args[val_idx], env, 0) if len(args) > val_idx else 0
    # A String `TintoInt`/`Interval` (`DataInterval("x",15,Sec,10)`) compiles on CR300/CR350
    # (oracle, 2026-10-12): it is taken as 0 here rather than crashing in `float()`.
    into_val = into_val if isinstance(into_val, (int, float)) else 0
    interval_val = interval_val if isinstance(interval_val, (int, float)) else 0
    if unit_val == _UNIT_MON:
        return (0, 0), (0, 0), float(interval_val or 0), "MON"
    if unit_val is not None and unit_val not in _UNIT_NAME_BY_VALUE:
        into_val, interval_val, unit_val = 0, 1, 2
    unit_name = "SEC" if unit_val is None else _UNIT_NAME_BY_VALUE.get(unit_val, "SEC")
    ns_per_unit = _UNIT_NS[unit_name]
    into_ns = int(round(float(into_val or 0) * ns_per_unit))
    interval_ns = int(round(float(interval_val or 0) * ns_per_unit))
    into = (into_ns // 1_000_000_000, into_ns % 1_000_000_000)
    interval = (interval_ns // 1_000_000_000, interval_ns % 1_000_000_000)
    return into, interval, float(interval_val or 0), unit_name


def _build_data_table(dt: DataTableDecl, env: consteval.Env, var_info: dict,
                       by_display: dict, called: set[str], by_base: dict | None = None,
                       anon: list[int] | None = None) -> tuple[TdfTable, dict]:
    """Returns (TdfTable, meta) where meta carries auto-sizing inputs (bytes_per_record,
    interval_seconds, has_interval, has_card) for tables whose declared Size is -1.
    """
    into = (0, 0)
    interval = (0, 0)
    interval_seconds = None
    display_interval = None  # (value, unit_text) for the -l "Interval:" line
    zero_interval = False
    has_card = False
    lapses = 10
    own_weight = None  # the DataInterval's own interval in seconds, kept even when the header is cleared

    out_fields: list[_OutField] = []
    pending: list[_OutField] = []
    pending_src = None   # Source of `pending` when it is eligible for alias splitting (after FieldNames)

    def split_pending():
        if pending_src is not None and by_base:
            return _split_by_alias(pending, pending_src, var_info, by_base, env)
        return pending

    for s in dt.body:
        kw = _kw(s)
        if isinstance(s, S.Instruction) and (kw == "DATAINTERVAL"):
            into, interval, ival, unit = _interval_from_args(s.args, env, 0, 1, 2)
            interval_seconds = ival * (_UNIT_SECONDS.get(unit, 1))
            display_interval = (ival, unit)
            zero_interval = interval_seconds == 0
            if unit == "MON":
                # no `-l` line for a zero MON interval, no enclosing-Scan weight, and no interval
                # for the auto-size arithmetic (oracle round 20 W5: Size -1 gives 232674 records for
                # 1, 2, 15 and 100 MON, the size of a table with no interval)
                zero_interval = False
                display_interval = (ival, unit) if ival else None
                interval_seconds = None
            if zero_interval:
                interval_seconds = None
            own_weight = interval_seconds
            if len(s.args) > 3:
                lapses = abs(_as_int(_fold(s.args[3], env, 10), 10))
            if len(s.args) > 3 and _fold(s.args[3], env, None) == 0:
                # Lapses = 0: TDF header and sizing as for a table with no DataInterval; only the
                # `-l` "Interval:" line remains (docs/tdf-rules.md §5)
                into, interval, interval_seconds = (0, 0), (0, 0), None
        elif kw == "DATAEVENT":
            pass  # interval/into stay (0,0) (§5, oracle-confirmed)
        elif kw in ("CARDOUT", "TABLEFILE"):
            has_card = True
        elif isinstance(s, S.Instruction) and (kw == "FIELDNAMES"):
            if s.args and isinstance(s.args[0], S.String) and pending:
                pending = _apply_fieldnames(pending, s.args[0].value, env.profile.name)
                out_fields.extend(split_pending())
                pending, pending_src = [], None
        elif kw in _OUTPUT_KEYWORDS and isinstance(s, S.Instruction):
            if pending:
                out_fields.extend(split_pending())
            s = _alias_source(s, var_info, by_display, by_base)
            pending = _emit_output_fields(s, var_info, by_display, env, by_base, anon)
            pending_src = s.args[1] if len(s.args) > 1 and not any(f.struct_members for f in pending) else None
        elif isinstance(s, S.Instruction) and (kw == "FFT"):
            if pending:
                out_fields.extend(split_pending())
            pending, pending_src = [_fft_field(s, var_info, by_display, env, anon)], None
        elif kw in ("ETSZ", "ETCLEARSKY"):
            if pending:
                out_fields.extend(split_pending())
            pending, pending_src = [_OutField(n, 7, True, n, 1, 1, [], name_only=True, rng=(1, _FFT_UNBOUNDED))
                                    for n in ("ETrs", "Rso")], None   # name-only: the TDF fields are not derived
    if pending:
        out_fields.extend(split_pending())

    fields = []
    for of in out_fields:
        if of.name_only:
            continue
        fields.append(TdfField(of.type_code, of.read_only, of.name, [], of.processing, of.units,
                                of.processing, of.begin, of.dim, of.subdims))

    size_raw = _fold(dt.size_expr, env, -1)

    bytes_per_record = sum(_field_record_bytes(of) for of in out_fields if not of.name_only)
    size_val = declared_size(size_raw, env.profile.name in ("cr300", "cr350"), bytes_per_record)
    size_val = -1 if size_val is None else size_val

    if interval_seconds is not None and 2 * bytes_per_record > _DEFAULT_PROBE_BUFFER_BYTES - _DEFAULT_PROBE_OVERHEAD:
        # fewer than 2 records fit the 1008-byte probe buffer: sized and stamped like a no-interval table
        into, interval, interval_seconds = (0, 0), (0, 0), None

    meta = dict(own_weight=own_weight, lapses=lapses, zero_interval=zero_interval, size_val=size_val, size_raw=size_raw,
                bytes_per_record=bytes_per_record,
                has_event=any(_kw(s) == "DATAEVENT" for s in dt.body),
                fillstop=any(_kw(s) == "FILLSTOP" for s in dt.body),
                interval_seconds=interval_seconds, has_card=has_card,
                display_interval=display_interval, called=dt.name.upper() in called)

    table = TdfTable(dt.name, 1, 14, into, interval, fields)
    table._dup_fields = out_fields  # type: ignore[attr-defined]
    if display_interval is not None:
        table._interval_display = display_interval  # type: ignore[attr-defined]
    return table, meta


def _field_record_bytes(of: _OutField) -> int:
    """Per-record byte cost of one output field: for ASCII (type 11) `dim` already IS the byte
    length; for numeric types it is `dim` (element count) times the type's own byte width."""
    if of.type_code == 11:
        return of.dim
    return of.dim * _TYPE_BYTES.get(of.type_code, 4)


# ---------------------------------------------------------------- auto-size (§10)


def _table_default_min(bytes_per_record: int) -> int:
    """`floor((1024-16)/bytes)`: a table's own default record count (probe buffer)."""
    if bytes_per_record <= 0:
        return 1
    return max((_DEFAULT_PROBE_BUFFER_BYTES - _DEFAULT_PROBE_OVERHEAD) // bytes_per_record, 1)


def _resolve_auto_sizes(tables_meta: list[tuple[TdfTable, dict]], scan_weights: dict[str, float]):
    autos = [(t, m) for t, m in tables_meta if m["size_val"] == -1 and m["called"]]
    if not autos:
        return
    reserved = _TABLE_INIT_RESERVED
    rate = 0.0
    for t, m in autos:
        # weight: a DataInterval table's own interval; a DataEvent/no-interval table's enclosing
        # Scan/SlowSequence interval (§10 "scan-interval weighting"), 1 s if none was found.
        w = _f32(m["own_weight"] or scan_weights.get(t.name.upper(), 1.0))
        b = m["bytes_per_record"]
        lapses = m["lapses"] if m["interval_seconds"] else 0
        dflt = _table_default_min(b) if lapses else 1
        if dflt < 2:
            dflt, lapses = 1, 0
        t8 = dflt * b + _DEFAULT_PROBE_OVERHEAD
        m.update(_w=w, _dflt=dflt, _t8=t8, _lapses=lapses)
        m["_reserve"] = ((2 * _DEFAULT_PROBE_BUFFER_BYTES) // t8 + (1 if dflt == 1 else 0)
                         + (1 if t8 < _DEFAULT_PROBE_BUFFER_BYTES else 3)) * t8
        m["_rate"] = _f32(_f32(t8) / _f32(_f32(dflt) * w))
        # a CardOut/TableFile table (nonzero card size) skips the Lapses-buffer term
        reserved += m["_reserve"]
        if lapses and b and not m["has_card"]:
            if 0 < lapses < 1 << 31:    # `Lapses * 16` wraps to 32 bits before the division (CR6/CR1000X `Out of memory.`)
                w16 = (lapses * 16 + (1 << 31)) % (1 << 32) - (1 << 31)
                reserved += (int(w16 / b) + 1) * b
            else:
                reserved += ((lapses * 16) // b + 1) * b
        rate = _f32(m["_rate"] + rate)
    duration = _f32(_f32(_FREE_MEMORY - reserved) / rate) if rate else float("inf")
    if rate:
        # tables whose interval is not shorter than the duration get 2 records and leave the pool
        for t, m in autos:
            if duration > 0 and duration <= m["_w"]:
                m["_forced"] = True
                rate = _f32(rate - m["_rate"])
                reserved -= m["_reserve"]
                duration = _f32(_f32(_FREE_MEMORY - reserved) / rate) if rate else float("inf")
    for t, m in autos:
        if m.get("_forced"):
            t.size = 2
            continue
        records = int(_f32(duration / m["_w"])) if m["bytes_per_record"] else 0
        m["_n_raw"] = records     # before the minimum: the count `checker/storage.cr6_table_oom` charges
        t.size = max(records, 2, m["_dflt"])


def _apply_sizes(tables_meta: list[tuple[TdfTable, dict]], scan_weights: dict[str, float],
                 resolve_auto: bool = True):
    for t, m in tables_meta:
        w = scan_weights.get(t.name.upper())
        if (m["zero_interval"] and m["called"] and w
                and 2 * m["bytes_per_record"] <= _DEFAULT_PROBE_BUFFER_BYTES - _DEFAULT_PROBE_OVERHEAD):
            # DataInterval(0,0,...): the interval is the enclosing Scan's (docs/tdf-rules.md §5)
            ns = round(w * 1_000_000_000)
            t.interval = (ns // 1_000_000_000, ns % 1_000_000_000)
            m["interval_seconds"] = w
            unit = m["display_interval"][1]
            t._interval_display = (w / _UNIT_SECONDS.get(unit, 1), unit)  # type: ignore[attr-defined]
        if not m["called"]:
            # A table with no `CallTable` anywhere in the compiled program never gets its
            # DataInterval/DataEvent finalised either (oracle-confirmed, docs/tdf-rules.md §13):
            # `size=1` *and* `interval`/`into` reset to (0,0), as if declared with neither.
            t.size = 1
            t.interval = (0, 0)
            t.time_into = (0, 0)
        elif m["size_val"] != -1:
            t.size = m["size_val"]
    if resolve_auto:
        _resolve_auto_sizes(tables_meta, scan_weights)


# ---------------------------------------------------------------- top-level


def _register_table_sources(table: TdfTable, var_info: dict[str, VarInfo]) -> None:
    """Descriptors for `Table.Field` sources (§9b/§9c), without inheriting table-field units."""
    for f in table.fields:
        shape = list(f.subdims)
        stored_len = None
        if f.type == 11:
            stored_len = shape.pop() if shape else f.dimension
        elif not shape and f.dimension > 1:
            shape = [f.dimension]
        var_info[f"{table.name}.{f.name}".upper()] = VarInfo(f.type, shape, stored_len, name=f.name)


def build_tables(program: S.Program, profile, consts: dict):
    env = consteval.Env(profile, dict(consts))
    scanned = _scan_program(program)
    scan_weights = _scan_weights(program.statements, env)
    scan_calls = _scan_calls(program.statements, env)

    var_info: dict[str, VarInfo] = {}
    for v in scanned["all_vars"]:
        var_info[v.name.upper()] = _make_var_info(v, env)
        var_info[v.name.upper()].struct = scanned["struct_types"].get((v.type or "").upper())

    # Build the Public table first (source-variable units lookups for output fields need its
    # display-name map), but keep its position in the TDF (after DataTableInfo/ConstTables, §2).
    public_table, by_display, by_base = _build_public_table(
        scanned["publics"], var_info, scanned["alias_stmts"], scanned["units_stmts"],
        scanned["readonly_names"])

    tables: list[TdfTable] = [_status_table(scanned["slow_seq_count"])]
    struct_tables = _build_struct_tables(scanned["all_vars"], scanned["struct_types"], env)
    tables.extend(struct_tables)
    if scanned["uses_ch400"]:
        struct_tables = struct_tables + [_ch400status_table()]
        tables.append(struct_tables[-1])
    for table in tables:
        _register_table_sources(table, var_info)

    data_table_metas = []
    anon = [0]   # program-wide `Anonymous<n>` counter (docs/tdf-rules.md §9c)
    for dt in scanned["data_tables"]:
        table, meta = _build_data_table(dt, env, var_info, by_display, scanned["called"], by_base, anon)
        tables.append(table)
        data_table_metas.append((table, meta))
        _register_table_sources(table, var_info)

    if scanned["data_tables"] or struct_tables:
        tables.append(_datatableinfo_table(len(scanned["data_tables"])))

    if scanned["uses_cpi"]:
        tables.append(_cpistatus_table())

    tables.extend(_build_const_tables(scanned["const_tables"], env))

    if public_table is not None:
        tables.append(public_table)

    cr3xx = profile.name in ("cr300", "cr350")
    if cr3xx:
        last_call = _last_call_interval(program.statements, env)
        for table, meta in data_table_metas:
            meta["call_w"] = meta["own_weight"] or last_call.get(table.name.upper())
    _apply_sizes(data_table_metas, scan_weights, resolve_auto=not cr3xx)
    alloc = None
    if cr3xx and data_table_metas:
        from .checker import storage
        pool = storage.pool_bytes(profile.name, len(data_table_metas), bool(scanned["publics"]),
                                  sum(1 for c in scanned["const_tables"] if c.name)) \
            - _preserve_deduction(program, env, scanned, var_info)
        metas = [m for _, m in data_table_metas]
        alloc = storage.run(metas, pool)
        if alloc:
            for (table, m), n in zip(data_table_metas, alloc["sizes"]):
                if m["called"] and (m["size_val"] == -1 or n > table.size):
                    table.size = n     # a fixed size is raised to the record block `k` (Sub-called)
        else:
            for table, m in data_table_metas:
                if m["called"] and m["size_val"] == -1:
                    table.size = 1
    for table, meta in data_table_metas:
        if alloc:
            meta["alloc"] = alloc
        table._storage = dict(meta, scans=scan_calls.get(table.name.upper(), []))  # type: ignore[attr-defined]

    table_list_text = render_table_list(tables)
    return tables, table_list_text, None


def _preserve_deduction(program: S.Program, env, scanned, var_info) -> int:
    """CR300/CR350 pool bytes taken before the tables by `PreserveVariables` and
    `PreserveOneVariable` (docs/tdf-rules.md "Preserved variables"; `checker/storage.py`)."""
    from .checker import storage
    stmts = program.statements
    kws = [_kw(s) for s in stmts]
    if "PRESERVEVARIABLES" not in kws and "PRESERVEONEVARIABLE" not in kws:
        return 0
    ticks = None          # smallest main-sequence Scan; SlowSequence scans only lift the 60 s cap
    slow = False
    for s in stmts:
        if _kw(s) == "SLOWSEQUENCE":
            slow = True
        elif isinstance(s, S.Instruction) and (_kw(s) == "SCAN"):
            if slow:
                ticks = 600000 if ticks is None else ticks
            elif s.args:
                val = _fold(s.args[0], env, None)
                if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                    unit = _scan_unit_name(s.args[1], env) if len(s.args) > 1 else "SEC"
                    t = int(val * _UNIT_NS.get(unit, _UNIT_NS["SEC"]) // 100_000)
                    ticks = t if ticks is None else min(ticks, t)
    ticks = 100000 if ticks is None else min(ticks, 600000)

    def size(v: S.VarDecl) -> tuple[int, int]:
        """(element width, element count) of a declaration; a `Double` is 8 bytes."""
        info = _make_var_info(v, env)
        return info.stored_len or (8 if (v.type or "").upper() == "DOUBLE" else 4), math.prod(info.shape)

    total = 0
    if "PRESERVEVARIABLES" in kws:
        pub = 16      # every Public, Dim and Sub-level Dim, plus each StructureType member once
        decls = [v for v in scanned["all_vars"] if (v.type or "").upper() not in scanned["struct_types"]]
        decls += [m for st in scanned["struct_types"].values() for m in st["members"]]
        pub += sum(w * n for w, n in map(size, decls))
        total += storage.preserve_charge(64, 60, 0, 100000, ticks, pub)
    by_name = {v.name.upper(): v for v in scanned["all_vars"]}
    payloads = []
    for s in stmts:
        if _kw(s) != "PRESERVEONEVARIABLE" or not isinstance(s, S.Instruction) or not s.args:
            continue
        a = s.args[0]
        v = by_name.get(getattr(a, "name", "").upper())
        if v is None:
            continue
        w, n = size(v)
        whole = not isinstance(a, S.Index) or not any(x is not None for x in a.args)   # `V()` is the whole array
        payloads.append(w * n if whole else w)
    if payloads:
        top = max(payloads)
        total += storage.preserve_charge(top + 78, top, len(payloads), 20000, ticks, 0)
    return total


# ---------------------------------------------------------------- `-l` table list (docs/cli.md)

# Defensive cap on how many index tuples a single field's `-l` listing will ever enumerate.
# `checker/structure.py`'s array-dimension check (`docs/checker.md` "Array dimension limits")
# rejects the specific fuzz-found case that made this hang outright (a `Public`/`Dim` dimension
# above `INT32_MAX`, `docs/fuzz-findings.md` "Hang"), so `build_tables`/`tables_hook.attach` is
# only ever reached for a declaration this checker considers legal -- but that still allows a
# dimension anywhere up to `INT32_MAX` through to here, and the real compiler itself rejects most
# of that range with `Out of memory.` (`checker/storage.py`, not this module). This is therefore pure robustness, not a claim about a
# verified real-compiler limit: `_MAX_EXPLODE_COUNT` is comfortably above the largest single-field
# element count this session could get the real compiler to actually accept (2,000,000, oracle-
# confirmed), so no known-exact `-l` output is affected, and it keeps this generator from ever
# building tens or hundreds of millions of index tuples for a declaration this checker did not
# (or, for some other field this module derives a huge `dimension` for, cannot) reject.
_MAX_EXPLODE_COUNT = 2_000_000


def _explode_indices(subdims: list[int], drop_last: bool, begin: int = 1, dim: int = 1):
    """Row-major (last dim fastest) index tuples for the `dim` stored elements starting at the
    1-based linear position `begin` within `subdims` (after dropping the trailing string-storage
    dim for ASCII fields, per §3/§8's alias-prefix/suffix fields and §3's Reps<array-size case:
    both need the *slice*, not every valid coordinate of the full declared shape). Bounded by
    `_MAX_EXPLODE_COUNT` regardless of how large `dim`/`subdims` claim to be; see that constant."""
    dims = subdims[:-1] if drop_last and subdims else subdims
    if drop_last and subdims and dim % subdims[-1] == 0 and begin % subdims[-1] == 1 % subdims[-1]:
        # string field: begin/dim are in bytes (§9a), convert to elements
        begin, dim = (begin - 1) // subdims[-1] + 1, dim // subdims[-1]
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
    if len(dims) == 1 and begin + dim - 1 > total and not drop_last:
        # a rank-1 field longer than its subdim (FFT Option mod 10 = 6..9: dim 2*(N/2+1) over
        # subdims [N/2+1]) lists its index running on past the subdim (docs/tdf-rules.md §9b)
        start, count = max(begin - 1, 0), min(dim, _MAX_EXPLODE_COUNT)
        for lin in range(start, start + count):
            yield (lin + 1,)
        return
    count = max(min(dim, total - start), 0)
    count = min(count, _MAX_EXPLODE_COUNT)
    for lin in range(start, start + count):
        coord = []
        rem = lin
        for d, stride in zip(dims, strides):
            coord.append(rem // stride + 1)
            rem %= stride
        yield tuple(coord)


def render_table_list(tables: list[TdfTable]) -> str:
    lines: list[str] = []
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
                total = min(total, _MAX_EXPLODE_COUNT)
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
