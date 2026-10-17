"""Trace the measurement-time accounting of CR6Comp (the 100 us units added to a Scan's budget).

Same patch technique as `trace_errors.py` (a cave in the zero slack of .text, never touching
`compiler/`), hooked at `FUN_00522e50(units)` -- the function every instruction's code generator
calls to add its time to the open Scan (`docs/binary.md`). Prints, in call order, the added units
and the return address into the caller; `--json` also gives the frames.

    python tools/trace_times.py program.CR6 [--json]
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pefile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trace_errors as te  # noqa: E402

EXE = "CR6Comp.exe"
TARGET_VA = 0x522E50
NDISP = 10  # push ebp; mov ebp,esp; cmp dword [0x66d76c],0


def build() -> Path:
    out = te.CACHE / "CR6Comp.times.exe"
    src = te.COMPILERS / EXE
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    te.CACHE.mkdir(parents=True, exist_ok=True)
    data = bytearray(src.read_bytes())
    pe = pefile.PE(data=bytes(data))
    base = pe.OPTIONAL_HEADER.ImageBase
    text = next(s for s in pe.sections if s.Name.startswith(b".text\0"))
    iat = {i.name: i.address for e in pe.DIRECTORY_ENTRY_IMPORT for i in e.imports
           if i.name in (b"GetStdHandle", b"WriteFile")}
    slack_off = text.PointerToRawData + text.Misc_VirtualSize
    cave_va = base + text.VirtualAddress + text.Misc_VirtualSize
    foff = text.PointerToRawData + (TARGET_VA - base - text.VirtualAddress)
    displaced = bytes(data[foff : foff + NDISP])
    assert displaced[:3] == b"\x55\x8b\xec", displaced.hex()
    stub = te._assemble(cave_va, iat[b"GetStdHandle"], iat[b"WriteFile"], TARGET_VA + NDISP, displaced)
    assert data[slack_off : slack_off + len(stub)] == bytes(len(stub)), "slack not empty"
    data[slack_off : slack_off + len(stub)] = stub
    data[foff : foff + NDISP] = b"\xe9" + struct.pack("<i", cave_va - (TARGET_VA + 5)) + b"\x90" * (NDISP - 5)
    vs_off = text.get_file_offset() + 8
    data[vs_off : vs_off + 4] = struct.pack("<I", text.Misc_VirtualSize + len(stub))
    out.write_bytes(data)
    return out


def trace(program: Path) -> dict:
    exe = build()
    with tempfile.TemporaryDirectory(prefix="times-") as tmp:
        tmp = Path(tmp)
        shutil.copy2(program, tmp / program.name)
        shutil.copy2(exe, tmp / exe.name)
        env = dict(os.environ, WINEDEBUG="-all", WINEPREFIX=os.environ.get("WINEPREFIX", str(Path.home() / ".wine-cr6")))
        env.setdefault("WINEDLLOVERRIDES", "winemac.drv,winex11.drv,winewayland.drv=d")
        r = subprocess.run([str(te.WINE), exe.name, program.name], cwd=tmp, env=env, capture_output=True, timeout=120)
    recs = []
    for m in re.finditer(re.escape(struct.pack("<I", te.MAGIC)), r.stderr):
        blk = r.stderr[m.start() : m.start() + 48]
        if len(blk) < 48:
            continue
        w = struct.unpack("<12I", blk)
        recs.append({"units": struct.unpack("<i", struct.pack("<I", w[1]))[0], "ret": hex(w[3]),
                     "frames": [hex(x) for x in w[4:] if x]})
    out = re.sub(r"\r+", "\r", r.stdout.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", "\n")
    return {"stdout": out.rstrip("\n"), "records": recs}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("program", type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = trace(a.program.resolve())
    if a.json:
        print(json.dumps(res, indent=1))
        return
    print(res["stdout"])
    print("--- time added (100 us units), caller")
    for i, rec in enumerate(res["records"]):
        print(f"{i} {rec['units']:8} {rec['ret']} {' '.join(rec['frames'][:3])}")


if __name__ == "__main__":
    sys.exit(main())
