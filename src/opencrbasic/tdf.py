"""Table definition file (TDF) reading and writing.

The `-z` output of CR6Comp.exe is the PakBus table-definition structure (as returned by the
logger's `.TDF` file): a version byte (1), then per table: name (NUL-terminated), table size
(u32 BE), time type (u8), time-into (NSec: s32 + s32 BE), interval (NSec), fields, and a 0 byte
ending the field list. Each field: type byte (0x80 flag = read-only), name, alias list
(NUL-terminated strings ending in an empty string), processing, units, description, begin index
(u32), dimension (u32), sub-dimensions (u32 list ending in 0).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


@dataclass
class TdfField:
    type: int
    read_only: bool
    name: str
    aliases: list[str] = field(default_factory=list)
    processing: str = ""
    units: str = ""
    description: str = ""
    begin_index: int = 1
    dimension: int = 1
    subdims: list[int] = field(default_factory=list)


@dataclass
class TdfTable:
    name: str
    size: int
    time_type: int
    time_into: tuple[int, int]
    interval: tuple[int, int]
    fields: list[TdfField] = field(default_factory=list)


def _cstr(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\0", pos)
    return data[pos:end].decode("latin-1"), end + 1


def parse(data: bytes) -> tuple[int, list[TdfTable]]:
    version = data[0]
    pos = 1
    tables = []
    while pos < len(data):
        name, pos = _cstr(data, pos)
        size, time_type = struct.unpack_from(">IB", data, pos)
        pos += 5
        into = struct.unpack_from(">ii", data, pos)
        interval = struct.unpack_from(">ii", data, pos + 8)
        pos += 16
        table = TdfTable(name, size, time_type, into, interval)
        while data[pos] != 0:
            ftype = data[pos]
            pos += 1
            fname, pos = _cstr(data, pos)
            aliases = []
            while True:
                alias, pos = _cstr(data, pos)
                if not alias:
                    break
                aliases.append(alias)
            processing, pos = _cstr(data, pos)
            units, pos = _cstr(data, pos)
            description, pos = _cstr(data, pos)
            begin, dim = struct.unpack_from(">II", data, pos)
            pos += 8
            subdims = []
            while True:
                (sd,) = struct.unpack_from(">I", data, pos)
                pos += 4
                if sd == 0:
                    break
                subdims.append(sd)
            table.fields.append(TdfField(ftype & 0x7F, bool(ftype & 0x80), fname, aliases, processing,
                                         units, description, begin, dim, subdims))
        pos += 1
        tables.append(table)
    return version, tables


def serialize(tables: list[TdfTable], version: int = 1) -> bytes:
    out = bytearray([version])
    for t in tables:
        out += t.name.encode("latin-1") + b"\0"
        out += struct.pack(">IB", t.size, t.time_type)
        out += struct.pack(">ii", *t.time_into) + struct.pack(">ii", *t.interval)
        for f in t.fields:
            out.append(f.type | (0x80 if f.read_only else 0))
            out += f.name.encode("latin-1") + b"\0"
            for a in f.aliases:
                out += a.encode("latin-1") + b"\0"
            out += b"\0"
            for s in (f.processing, f.units, f.description):
                out += s.encode("latin-1") + b"\0"
            out += struct.pack(">II", f.begin_index, f.dimension)
            for sd in f.subdims:
                out += struct.pack(">I", sd)
            out += b"\0\0\0\0"
        out.append(0)
    return bytes(out)


def dump(data: bytes) -> str:
    version, tables = parse(data)
    lines = [f"version {version}"]
    for t in tables:
        lines.append(f"table {t.name} size={t.size} timetype={t.time_type} into={t.time_into} interval={t.interval}")
        for f in t.fields:
            ro = " ro" if f.read_only else ""
            extra = []
            if f.aliases:
                extra.append(f"aliases={f.aliases}")
            for key in ("processing", "units", "description"):
                if getattr(f, key):
                    extra.append(f"{key}={getattr(f, key)!r}")
            lines.append(f"  {f.name} type={f.type}{ro} begin={f.begin_index} dim={f.dimension} "
                         f"subdims={f.subdims} {' '.join(extra)}".rstrip())
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    print(dump(open(sys.argv[1], "rb").read()))
