"""CR300/CR350 data-table allocator (`docs/tdf-rules.md` "Storage limit" and "Auto-sized tables").

After a clean parse the real compiler sizes every table in one pool (`cr300comp.exe` /
`cr350comp.exe`): up to 26 passes, then `Unable to allocate enough data table space to manage
flash write cycle limits.` and, when the last pass left the pool negative, `Not enough storage
area for DataTables.` `allocate` is a port of that allocator (decompiled in rounds 15 and 16,
confirmed with oracle threshold sweeps on both loggers), 32-bit wraparound and float32 arithmetic
included. It does three things at once:

- charges every called table of a fixed size `roundup4096(a + int(R * (N / k)) + 0x2000)`, raised
  to the flash-wear minimum record count, against the pool less 16 KB per declared table
  (`pool_bytes`);
- sizes the auto (`-1`) tables from what is left (`tables.build_tables` takes the TDF record
  counts from here), and a table called only from a `Sub`/`AcceptDataRecords` is charged its auto
  share instead of its own `N`;
- fails (both messages) when no pass fits, which also covers a size literal beyond +-2^31
  (`INT_MIN`, whose wrapped charge sinks the pool whenever another table is auto-sized).

Pool consumers this module does not know (GOES/PakBus send buffers, `DAT_006ef00c`) only ever
shrink the real pool, so the model can miss a rejection or size an auto table too large but not
invent a rejection.
"""

from __future__ import annotations

import math
import struct

from .. import syntax as S

_POOL_PAGES = {"cr300": 2560, "cr350": 12800}   # 0xA00000 / 0x3200000 bytes, in 4 KiB pages
_PAGE = 4096
_BLOCK_BYTES = 496                              # a DataInterval table's block: 496 // bytes per record
_INT_MIN = -(1 << 31)
_BASE = 0x600000                                 # start of the table area

MSG_ALLOC = "Unable to allocate enough data table space to manage flash write cycle limits."
MSG_STORAGE = "Not enough storage area for DataTables."


def _f32(x: float) -> float:
    try:
        return struct.unpack("f", struct.pack("f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def _cvt_int(x: float) -> int:
    """`cvttss2si`: truncation, with the out-of-range / NaN result `INT_MIN`."""
    if x != x or not (-2147483648.0 <= x < 2147483648.0):
        return _INT_MIN
    return int(x)


def _wrap32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


_I32 = (-(1 << 31), (1 << 31) - 1)


def _i32(v: int) -> int:
    return _wrap32(v)


def _min_recs(sp: dict, k: int, cc: int) -> int:
    """Flash-wear minimum record count of pass `cc`, `FUN_005b50c0` (also the `W == -1` and
    `W == 0` branches). `sp` carries `flagged`, `day`, `own_ms`, `W` (call interval in seconds, `None`
    when the call interval is unknown) and `B`'s block `k`."""
    if sp["flagged"]:
        period = 86400000.0
    elif sp["day"]:
        period = 2592000000.0
    elif sp["own_ms"]:
        period = _f32(float(sp["own_ms"]))
    elif sp["W"] is None:
        if cc == 0:
            period = 50.0
        elif cc < 5:
            period = float(cc * 100)
        elif cc < 10:
            period = float(cc * 1000)
        else:
            period = float(cc * 5000)
    elif sp["W"] == 0:
        period = 50.0
        for lim, val in ((2, 2000.0), (4, 5000.0), (8, 10000.0), (12, 20000.0), (16, 30000.0),
                         (20, 60000.0), (23, 120000.0)):
            if cc > lim:
                period = val
    else:
        period = _f32(_f32(sp["W"]) * 1000.0)
    den = _f32(max(float(10 - cc), 1.0))
    try:
        wear = _f32(_f32(_f32(_f32(3600000.0 / period) * 24.0) / _f32(float(k)))
                    / _f32(_f32(100000.0) / _f32(den * 365.0)))
    except ZeroDivisionError:
        return _INT_MIN
    return _cvt_int(wear)


def _round_page(v: int) -> int:
    """`FUN_005d4660`: round an unsigned 32-bit address up to 4096."""
    return _wrap32((-(-(v & 0xFFFFFFFF) // _PAGE)) * _PAGE)


def allocate(specs: list[dict], pool: int) -> dict:
    """Port of the table allocator (`cr300comp` `FUN_005ae6e0`, 26 passes; docs/tdf-rules.md
    "Storage limit" and "Auto-sized tables"). `specs` are the user tables in declaration order:
    `called`, `n` (record count argument; -1 auto, INT_MIN for a size beyond 32 bits), `flagged`,
    `B` (bytes per record), `lapses`, `card`, `own_ms` (DataInterval in ms, 0 none), `W` (call
    interval in seconds or None), `fillstop`, `kone`, `day`. Returns `{"sizes": [...], "msg1": bool,
    "msg2": bool}` (sizes are the final record counts of the last pass)."""
    st = []
    for sp in specs:
        st.append({"N": sp["n"], "k": 0, "R": 0, "lapses": sp["lapses"], **{
            x: sp[x] for x in ("called", "flagged", "B", "card", "own_ms", "W", "fillstop", "kone", "day")}})
    e4 = pool
    ok_exit = False
    for cc in range(26):
        e4 = pool
        reserve = 0
        rate = 0.0
        d8 = False
        ptr = _BASE
        for t in st:
            if not t["called"]:
                t["N"] = 1
                reserve = _i32(reserve + t["B"])
                continue
            if (t["N"] < 0 or t["W"] is None) and not t["flagged"]:
                _blockfix(t, 512)
                R, k = t["R"], t["k"]
                c1 = (1 if R < 512 else 3) + (1024 // R) + (1 if k == 1 else 0)
                c1 = max(c1 * R, 0x3000)
                reserve = _i32(reserve + c1)
                if not t["card"]:
                    reserve = _i32(reserve + _lap(t) * t["B"])
                w = 1.0 if t["W"] is None else _f32(t["W"])
                rate = _f32(_f32(float(R) / _f32(float(k) * w)) + rate)
            else:
                a1 = _lap(t)
                _blockfix(t, _i32(a1 + t["N"]))
                R, k = t["R"], t["k"]
                a2 = _lap(t)
                u = _i32(a2 + _cvt_int(_f32(_f32(float(R)) * _f32(_f32(float(t["N"])) / _f32(float(k))))) + 0x2000)
                if not t["fillstop"]:
                    lo = _min_recs(t, k, cc)
                    if _cvt_int(_f32(_f32(float(u)) / _f32(float(R)))) < lo:
                        u = _i32(lo * R)
                ptr, used = _alloc(ptr, u)
                e4 = _i32(e4 - used)
        dur = 0.0
        if rate != 0.0:
            dur = _f32(float(_i32(_i32(e4 - reserve) - 0x1000))) / rate if rate else 0.0
            dur = _f32(dur)
            if dur <= 0.0:
                d8 = True
        for t in st:
            if t["N"] < 0 or (t["W"] is None and not t["flagged"]):
                if t["called"] and t["B"]:
                    R, k = t["R"], t["k"]
                    w = 1.0 if t["W"] is None else _f32(t["W"])
                    n = _cvt_int(_f32(dur / w)) if w else _INT_MIN
                    u = _cvt_int(_f32(_f32(float(R)) * _f32(_f32(float(n)) / _f32(float(k)))))
                    u = _i32(u + _lap(t) + 0x2000)
                    if u < 0x3000:
                        u = 0x3000
                    if not t["fillstop"]:
                        lo = _min_recs(t, k, cc)
                        if _tdiv(u, R) < lo:
                            d8 = True
                    ptr, used = _alloc(ptr, u)
                    b4 = _tdiv(used, R)
                    if t["N"] < 0:
                        t["N"] = _i32((b4 - (4096 // R + 1)) * k)
                    e4 = _i32(e4 - used)
                if t["N"] < 1:
                    t["N"] = 1
                if t["N"] < t["k"]:
                    t["N"] = t["k"]
        if e4 < 0:
            d8 = True
        if not d8:
            ok_exit = True
            break
    return {"sizes": [t["N"] for t in st], "msg1": not ok_exit, "msg2": e4 < 0}


def _lap(t: dict) -> int:
    """Records kept for the Lapses count: `Lapses * 16` wraps to 32 bits before the division."""
    return _tdiv(_i32(t["lapses"] * 16), t["B"]) + 1 if t["lapses"] else 0


def _tdiv(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _alloc(ptr: int, u: int) -> tuple[int, int]:
    """One table's span from the running pointer: start and end rounded up to a page."""
    start = _round_page(ptr)
    end = _round_page(_i32(start + u))
    return end, _i32(end - start)


def _blockfix(t: dict, arg: int) -> None:
    """`FUN_005b7d00`: the record block (`k` records of `R = k * B + 16` bytes), fixed the first time."""
    if t["R"]:
        return
    if t["lapses"] == 0:
        t["k"] = 1
    else:
        t["k"] = min(_BLOCK_BYTES // t["B"], arg)
        if t["k"] < 2:
            t["k"], t["lapses"] = 1, 0
        if t["kone"]:
            t["k"], t["lapses"] = 1, 0
    t["R"] = t["k"] * t["B"] + 16


def pool_bytes(profile_name: str, n_declared: int, has_public: bool, n_named_const: int) -> int:
    """The pool left for the user's tables: 16 KB per table beyond the first (`DataTableInfo`, a
    `Public` table and a named `ConstTable` are tables too)."""
    extra = (1 if has_public else 0) + n_named_const
    return (_POOL_PAGES[profile_name] - 4 * (n_declared + extra)) * _PAGE


def spec_of(m: dict) -> dict:
    """The allocator input of one data table from its `tables._build_data_table` meta."""
    from .. import tables as T
    cls = T.size_class(m["size_raw"])
    flagged = False
    if cls is None:
        n = 1
    elif cls[0] == "auto":
        n = -1
    elif cls[0] in ("edge", "inf"):
        n = 2147483647
        flagged = cls[0] == "edge" and cls[1] < 0
    elif cls[0] == "toxic":
        n, flagged = _INT_MIN, True
    else:
        n, flagged = cls[1], cls[2]
    di = bool(m["interval_seconds"]) or m["zero_interval"]
    return {"called": m["called"], "n": n, "flagged": flagged, "B": m["bytes_per_record"],
            "lapses": (m["lapses"] if m["lapses"] < 1 << 31 else _INT_MIN) if di else 0, "card": False,   # TableFile does not skip the Lapses term here
            "own_ms": int(round(m["interval_seconds"] * 1000)) if m["interval_seconds"] else 0,
            "W": m["call_w"], "fillstop": m.get("fillstop", False), "kone": False, "day": False,
            "zero_interval": m["zero_interval"]}


def run(metas: list[dict], pool: int) -> dict | None:
    """Runs the allocator on the data tables' metas (`None` when a table has no record bytes)."""
    specs = [spec_of(m) for m in metas]
    for sp in specs:
        if sp["zero_interval"] and sp["W"]:
            sp["own_ms"] = int(round(sp["W"] * 1000))
    if any(not sp["B"] for sp in specs):
        return None
    return allocate(specs, pool)


_MAX_ALLOC = 0x8000000                   # CR6/CR1000X: largest single allocation request, 128 MiB
MSG_OOM = "Out of memory."


def cr6_table_oom(m: dict) -> bool:
    """CR6/CR1000X: a fixed-size table's single block request exceeds `_MAX_ALLOC` (docs/
    tdf-rules.md "CR6/CR1000X Out of memory"). Only a plain fixed size is modelled; an auto,
    negative or beyond-range size is left alone (not probed)."""
    from .. import tables as T
    cls = T.size_class(m["size_raw"])
    if cls is None:
        n = 1
    elif cls[0] == "fixed" and not cls[2]:
        n = cls[1]
    else:
        return False
    b = m["bytes_per_record"]
    if not b or not m["called"]:
        return False
    lap = (m["lapses"] if m["lapses"] < 1 << 31 else _INT_MIN) if m["display_interval"] else 0
    a = _tdiv(_wrap32(abs(lap) * 16 if lap != _INT_MIN else 0), b) + 1 if lap else 0
    mm = _wrap32(n + a)
    k = 1
    if lap:
        k = min(1008 // b, mm)
        if k < 2:
            k = 1
    r = k * b + 16
    if n == 1:
        c = r
    else:
        c = _wrap32((2048 // r + _tdiv(mm, k) + (1 if r < 1024 else 3)) * r)
    return c > _MAX_ALLOC or c < 0      # a wrapped negative request fails the host allocation


def _var_words(program: S.Program, env, kinds: tuple) -> list[tuple[int, int]]:
    """(elements, 4-byte words per element) of every `kinds` declaration (a Float/Long/Boolean
    is one word, a Double two, a String its padded length / 4; any other type is not counted, so
    the total can only be too small)."""
    from .. import tables as T
    out = []
    for st in program.statements:
        if not isinstance(st, S.Declaration) or st.kind not in kinds:
            continue
        for v in st.vars:
            t = (v.type or "FLOAT").upper()
            if t not in ("FLOAT", "LONG", "BOOLEAN", "DOUBLE", "STRING"):
                continue
            dims = [T._as_int(T._fold(d, env), default=1) for d in v.dims]
            if len(dims) > 3 or any(not 1 <= d <= 2147483647 for d in dims):
                continue                    # `Illegally dimension`: never allocated
            info = T._make_var_info(v, env)
            n = 1
            for d in dims:
                n *= d
            out.append((n, 2 if t == "DOUBLE" else info.stored_len // 4 if t == "STRING" else 1))
    return out


def _segment_oom(words: list[tuple[int, int]]) -> bool:
    """One variable segment (`Dim` or `Public`) starts at 4 words and is requested as
    `wrap32(words * 4)` bytes; the request fails above 128 MiB or when it wraps negative (zero
    is a null request that crashes the compiler: not modelled)."""
    total = 4
    for n, w in words:
        total = _wrap32(total + _wrap32(_wrap32(n) * w))
    req = _wrap32(total * 4)
    return req > _MAX_ALLOC or req < 0


def variables_oom(program: S.Program, profile, env) -> bool:
    """CR6/CR1000X end of pass 1: a `Dim` or a `Public` segment too large to allocate (reported at
    the program's last statement, ends the compile before pass 2)."""
    return profile.name in ("cr6", "cr1000x") and any(
        _segment_oom(_var_words(program, env, (k,))) for k in ("DIM", "PUBLIC"))


def check_pass1(program: S.Program, profile, diags, env) -> None:
    if variables_oom(program, profile, env):
        diags.error(program.last_line, MSG_OOM)


def public_table_oom(program: S.Program, env) -> bool:
    """The `Public` table is one record of the `Public` variables' bytes; its block request is
    `2 * bytes + 32`, so it fails above 67,108,848 bytes."""
    words = _var_words(program, env, ("PUBLIC",))
    if any(n >= 1 << 31 for n, _ in words):
        return False
    return sum(n * w for n, w in words) * 4 > (_MAX_ALLOC - 32) // 2


def check(program: S.Program, profile, result, diags) -> None:
    """Reports the storage-limit pair of messages (pass 2, only on an otherwise clean program)."""
    if profile.name in ("cr6", "cr1000x") and not diags.errors:
        from .. import consteval
        if public_table_oom(program, consteval.Env(profile, dict(program.consts))) or any(
                cr6_table_oom(t._storage) for t in (result.tables or []) if hasattr(t, "_storage")):
            diags.error(0, MSG_OOM)
        return
    if profile.name not in _POOL_PAGES or diags.errors:
        return
    declared = [t for t in (result.tables or []) if hasattr(t, "_storage")]
    if not declared:
        return
    alloc = declared[0]._storage.get("alloc")
    if alloc and alloc["msg1"]:
        diags.error(0, MSG_ALLOC)
        if alloc["msg2"]:
            diags.error(0, MSG_STORAGE)
