#!/usr/bin/env python3
"""Extract CR6Comp.exe's built-in data tables into src/opencrbasic/data/*.json.

    uv run tools/extract_tables.py [--exe path/to/CR6Comp.exe] [--out DIR]

Needs `pefile` (in the uv `dev` group: `uv sync`). The exe is located by --exe, $CR6COMP_EXE, or
../compiler/CR6Comp.exe relative to the repository. Table locations and record layouts are
documented in docs/binary.md; the addresses below are for CR6.14.2.0 (2025-03-04) and are
checked by content before use.
"""

import argparse
import json
import os
import re
import struct
import sys
from pathlib import Path

import pefile

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "src" / "opencrbasic" / "data"
DEFAULT_EXE = HERE.parents[1] / "compiler" / "CR6Comp.exe"

IDENT = re.compile(rb"^[A-Za-z_][A-Za-z0-9_$]*$")


class Image:
    def __init__(self, path: Path):
        self.pe = pefile.PE(str(path))
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.d = self.pe.__data__

    def section(self, va: int):
        rva = va - self.base
        for s in self.pe.sections:
            if s.VirtualAddress <= rva < s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData):
                return s
        return None

    def cstr(self, va: int) -> str | None:
        """NUL-terminated string at va; '' for zero-initialised (bss) memory."""
        s = self.section(va)
        if s is None:
            return None
        rel = va - self.base - s.VirtualAddress
        if rel >= s.SizeOfRawData:
            return ""
        off = s.PointerToRawData + rel
        raw = self.d[off:self.d.find(b"\0", off)]
        if len(raw) < 200 and all(32 <= c < 127 for c in raw):
            return raw.decode()
        return None

    def off(self, va: int) -> int:
        return self.pe.get_offset_from_rva(va - self.base)

    def va(self, off: int) -> int:
        return self.pe.get_rva_from_offset(off) + self.base

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.d, off)[0]


def find_string(img: Image, text: str) -> int:
    needle = b"\0" + text.encode() + b"\0"
    off = img.d.find(needle)
    if off < 0:
        raise SystemExit(f"string {text!r} not found; unsupported CR6Comp.exe version?")
    return off + 1


def instruction_table(img: Image) -> list[dict]:
    """16-byte records {char *name; char *param_codes; void *compile; void *runtime}."""
    anchor_va = img.va(find_string(img, "SDI12Recorder"))
    ref = img.d.find(struct.pack("<I", anchor_va))
    start = end = ref

    def valid(off: int) -> bool:
        name = img.cstr(img.u32(off))
        codes = img.cstr(img.u32(off + 4))
        return bool(name) and name[0].isalpha() and codes is not None and img.section(img.u32(off + 8)) is not None

    while valid(start - 16):
        start -= 16
    while valid(end + 16):
        end += 16
    rows = []
    for index, off in enumerate(range(start, end + 16, 16)):
        codes = img.cstr(img.u32(off + 4)) or ""
        rows.append({
            "index": index,
            "name": img.cstr(img.u32(off)),
            "params": [int(c) for c in codes.split(",") if c],
            "compile_fn": hex(img.u32(off + 8)),
            "runtime_fn": hex(img.u32(off + 12)) if img.u32(off + 12) else None,
        })
    return rows


def function_table(img: Image) -> list[dict]:
    """112-byte records {char name[96]; int code; int kind; int argspec; void *fn}."""
    stride = 0x70
    anchor = find_string(img, "CSGN")

    def name(off: int) -> str | None:
        raw = img.d[off:off + 96].split(b"\0")[0]
        return raw.decode() if raw and all(32 < c < 127 for c in raw) else None

    start = end = anchor
    while name(start - stride):
        start -= stride
    while name(end + stride):
        end += stride
    rows = []
    for off in range(start, end + stride, stride):
        code, kind, argspec, fn = struct.unpack_from("<iiiI", img.d, off + 96)
        row = {"name": name(off), "code": code, "kind": kind, "argspec": argspec}
        if kind in (295, 309, 311, 312):
            nargs = argspec & 0xF if kind != 295 else None
            if kind == 295:
                row["nargs"] = argspec if argspec < 16 else argspec & 0xF
                row["argtypes"] = None if argspec < 16 else [(argspec >> (4 + 2 * i)) & 3 for i in range(argspec & 0xF)]
            else:
                row["nargs"] = nargs
                row["argtypes"] = [(argspec >> (4 + 2 * i)) & 3 for i in range(nargs)]
        rows.append(row)
    return rows


def symbol_table(img: Image) -> list[dict]:
    """Records {char name[40]; float value; int value; int extra; ...}: 52 bytes on CR6/CR1000X,
    64 bytes on CR300/CR350. The stride is measured between the adjacent `SCAN` and `SUBSCAN`."""
    anchor = find_string(img, "SUBSCAN")
    stride = anchor - img.d.rfind(b"\0SCAN\0", 0, anchor) - 1

    def name(off: int) -> str | None:
        raw = img.d[off:off + 36]
        text = raw.split(b"\0")[0]
        return text.decode() if IDENT.match(text) and not any(raw[len(text):]) else None

    start = end = anchor
    while name(start - stride):
        start -= stride
    while name(end + stride):
        end += stride
    rows = []
    for off in range(start, end + stride, stride):
        fval, ival, extra = struct.unpack_from("<fii", img.d, off + 40)
        rows.append({"name": name(off), "value": ival, "fvalue": fval, "extra": extra})
    return rows


def ess_table(img: Image) -> list[dict]:
    """The `ESSVariables` table (CR6: `DAT_005fd008`, 0xd9 records of 0x14 bytes): type byte (0xb
    String, 0xff a Long `count x 16` array, otherwise Long), element count (0: not checked) and
    name pointer at +4. Found from the pointer to its `essTypeofStation` name."""
    va = img.va(find_string(img, "essTypeofStation"))
    start = img.d.find(struct.pack("<I", va)) - 4

    def name(off: int) -> str | None:
        n = img.cstr(img.u32(off + 4)) if off >= 0 else None
        return n if n and IDENT.match(n.encode()) else None

    while name(start - 20):
        start -= 20
    rows = []
    off = start
    while (n := name(off)) is not None:
        kind = {0x0B: "STRING", 0xFF: "LONG_ARRAY16"}.get(img.d[off], "LONG")
        rows.append({"name": n, "type": kind, "count": img.d[off + 1]})
        off += 20
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--exe", type=Path, default=Path(os.environ.get("CR6COMP_EXE", DEFAULT_EXE)))
    parser.add_argument("--out", type=Path, default=DATA, help="output directory (default: package data)")
    args = parser.parse_args()
    img = Image(args.exe)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    outputs = {
        "instructions.json": instruction_table(img),
        "functions.json": function_table(img),
        "symbols.json": symbol_table(img),
        "ess_variables.json": ess_table(img),
    }
    for name, rows in outputs.items():
        (out / name).write_text(json.dumps(rows, indent=1) + "\n")
        print(f"{name}: {len(rows)} records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
