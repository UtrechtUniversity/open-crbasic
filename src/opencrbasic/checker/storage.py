"""CR300/CR350 data-table storage limit (`docs/tdf-rules.md` "Storage limit").

After a clean parse the real compiler sizes every table in one pool (`cr300comp.exe` /
`cr350comp.exe`): up to 26 passes, then `Unable to allocate enough data table space to manage
flash write cycle limits.` and, when the last pass left the pool negative, `Not enough storage
area for DataTables.` This module models only the part that is exact and cannot reject a program
the real compiler accepts:

- every called table of a fixed size whose `CallTable` sits lexically inside a `Scan` is charged
  `roundup4096(a + int(R * (N / k)) + 0x2000)` (all float32), raised to the flash-wear minimum
  record count of a no-`DataInterval` table in a known-interval `Scan`; the sum is compared with
  the pool less 16 KB per declared table (`_pool_pages`);
- a called table whose size literal is beyond +-2^31 (as float32) fails whenever another called
  table is auto-sized.

Auto-sized tables, `DataEvent`/`CardOut` tables, tables called only from a `Sub`, and pool
consumers this module does not know (it only ever shrinks the real pool) are never charged, so
the model can miss a rejection but not invent one. The constants and rules come from the
allocator's decompilation and were confirmed with oracle threshold sweeps (docs).
"""

from __future__ import annotations

import math
import struct

from .. import syntax as S

_POOL_PAGES = {"cr300": 2560, "cr350": 12800}   # 0xA00000 / 0x3200000 bytes, in 4 KiB pages
_PAGE = 4096
_BLOCK_BYTES = 496                              # a DataInterval table's block: 496 // bytes per record
_INT_MIN = -(1 << 31)

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


def _min_records(interval: float | None, scan: float | None, flagged: bool, k: int) -> int:
    """Flash-wear minimum record count at the last (26th) pass, `FUN_005b50c0`: a negative-size
    table is written once a day, a table with a `DataInterval` of a second or more every whole
    second of it, any other table every `Scan` interval; 0 when unknown."""
    if flagged:
        period_ms = 86400000.0
    elif interval is not None and interval >= 1:
        period_ms = float(int(interval) * 1000)
    elif scan is not None:
        period_ms = _f32(_f32(scan) * 1000.0)
    else:
        return 0
    wear = _f32(_f32(_f32(_f32(3600000.0 / period_ms) * 24.0) / _f32(float(k)))
                / _f32(_f32(100000.0) / _f32(365.0)))
    return _cvt_int(wear)


def _charge(n: int, nbytes: int, lapses: int, flagged: bool, interval: float | None,
            scan: float | None) -> int:
    """Bytes (a multiple of 4096) one fixed-size called table takes from the pool."""
    a1 = (lapses * 16) // nbytes + 1 if lapses else 0
    k = 1
    if lapses:
        k = min(_BLOCK_BYTES // nbytes if nbytes else 0, a1 + n)
        if k < 2:
            k, lapses = 1, 0
    rec = k * nbytes + 16
    a2 = (lapses * 16) // nbytes + 1 if lapses else 0
    used = _wrap32(a2 + _cvt_int(_f32(_f32(float(rec)) * _f32(_f32(float(n)) / _f32(float(k))))) + 0x2000)
    lo = _min_records(interval, scan, flagged, k)
    # C `(int)((float)used / (float)rec)` truncates toward zero
    if int(_f32(_f32(float(used)) / _f32(float(rec)))) < lo:
        used = lo * rec
    return -(-used // _PAGE) * _PAGE if used > 0 else 0


def check(program: S.Program, profile, result, diags) -> None:
    """Reports the storage-limit pair of messages (pass 2, only on an otherwise clean program)."""
    if profile.name not in _POOL_PAGES or diags.errors:
        return
    from .. import tables as T
    declared = [t for t in (result.tables or []) if hasattr(t, "_storage")]
    if not declared:
        return
    scanned = T._scan_program(program)
    extra = (1 if scanned["publics"] else 0) + 1     # `Public` and `DataTableInfo`, a table each
    extra -= 1                                       # (the pool is 16 KB per table beyond the first)
    extra += sum(1 for c in scanned["const_tables"] if c.name)   # a named `ConstTable` is a table too
    pool = (_POOL_PAGES[profile.name] - 4 * (len(declared) + extra)) * _PAGE
    classes = []
    used = 0
    for t in declared:
        m = t._storage
        if not m["called"]:
            continue
        cls = T.size_class(m["size_raw"])
        if cls is not None and cls[0] in ("edge", "inf"):   # +-2^31 / +inf: INT32_MAX, -2^31 flagged
            cls = ("fixed", 2147483647, cls[0] == "edge" and cls[1] < 0)
        classes.append((t, m, cls))
    autos = [m for _, m, cls in classes if cls == ("auto",) and m["scans"]]
    if autos and any(cls == ('toxic',) and m["scans"] for _, m, cls in classes):
        diags.error(0, MSG_ALLOC)
        diags.error(0, MSG_STORAGE)
        return
    for t, m, cls in classes:
        if cls is None or cls[0] != "fixed" or not m["scans"]:
            continue
        if m["has_card"] or m["has_event"] or m["zero_interval"] or not m["bytes_per_record"]:
            continue
        lapses = m["lapses"] if m["interval_seconds"] else 0
        scan = None
        known = [s for s in m["scans"] if s]
        if len(known) == len(m["scans"]):
            scan = max(known)
        used += _charge(cls[1], m["bytes_per_record"], lapses, cls[2], m["interval_seconds"], scan)
    if used > pool:
        diags.error(0, MSG_ALLOC)
        diags.error(0, MSG_STORAGE)
