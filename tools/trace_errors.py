"""Trace which code path emits each compiler message, in emission order.

Patches a COPY of a real compiler (never compiler/) so that its shared error/warning formatter
first writes one binary record to stderr, then runs normally. Each record holds the message
kind (1 error, 0 warning), the format-string address, and the return addresses of the calling
frames (frame 0 = the code that called the reporter). Uses no debugger: winedbg on macOS Wine
disables a breakpoint after its first hit, so it cannot trace more than one message.

    python tools/trace_errors.py <cr6|cr1000x|cr300|cr350> program.CR6 [--siblings DIR] [--json]

The patched exes are cached in scratch/re/. Patch = 5-byte jmp at the formatter entry to a cave in
the zero slack at the end of .text; the cave saves all registers, writes the record via
GetStdHandle(-12)/WriteFile, restores them, re-executes the displaced prologue and jumps back.
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

ROOT = Path(__file__).resolve().parent.parent
COMPILERS = ROOT.parent / "compiler"
CACHE = ROOT / "scratch" / "re"
WINE = Path.home() / ".local/wine/Wine Devel.app/Contents/Resources/wine/bin/wine"
MAGIC = 0xDEADBEEF
FRAMES = 8

# logger -> (exe, formatter VA, displaced prologue bytes)
TARGETS = {
    "cr6": ("CR6Comp.exe", 0x50AEF0, 6),
    "cr1000x": ("cr1000xcomp.exe", 0x50AFB0, 6),
    "cr300": ("cr300comp.exe", 0x5C3B40, 9),
    "cr350": ("cr350comp.exe", 0x5C2E20, 9),
}


def _assemble(cave_va: int, iat_get: int, iat_write: int, back_va: int, displaced: bytes) -> bytes:
    """Hand-encoded x86-32 stub (see module docstring)."""
    c = bytearray()
    c += b"\x9c\x60"  # pushfd; pushad          -> [esp+36]=ret [esp+40]=kind [esp+44]=fmt
    c += b"\x83\xec\x34"  # sub esp, 52
    c += b"\xc7\x04\x24" + struct.pack("<I", MAGIC)  # [esp]=magic
    c += b"\x8b\x44\x24\x5c\x89\x44\x24\x04"  # kind
    c += b"\x8b\x44\x24\x60\x89\x44\x24\x08"  # fmt
    c += b"\x8b\x44\x24\x58\x89\x44\x24\x0c"  # return address into the reporter
    c += b"\x89\xeb"  # mov ebx, ebp
    c += b"\x8d\x7c\x24\x10"  # lea edi, [esp+16]
    c += b"\xb9" + struct.pack("<I", FRAMES)  # mov ecx, FRAMES
    loop = len(c)
    c += b"\x31\xc0\x89\xda\x29\xe2"  # xor eax,eax; mov edx,ebx; sub edx,esp
    c += b"\x81\xfa\x00\x00\x10\x00"  # cmp edx, 0x100000
    c += b"\x73\x05"  # jae store
    c += b"\x8b\x43\x04\x8b\x1b"  # mov eax,[ebx+4]; mov ebx,[ebx]
    c += b"\xab"  # store: stosd
    c += bytes([0xE2, (loop - (len(c) + 2)) & 0xFF])  # loop
    c += b"\x6a\xf4\xff\x15" + struct.pack("<I", iat_get)  # GetStdHandle(-12)
    c += b"\x6a\x00\x8d\x4c\x24\x34\x51\x6a\x30\x8d\x4c\x24\x0c\x51\x50"
    c += b"\xff\x15" + struct.pack("<I", iat_write)  # WriteFile(h, buf, 48, &n, 0)
    c += b"\x83\xc4\x34\x61\x9d"  # add esp,52; popad; popfd
    c += displaced
    jmp_at = cave_va + len(c)
    c += b"\xe9" + struct.pack("<i", back_va - (jmp_at + 5))
    return bytes(c)


def build(logger: str) -> Path:
    exe, fmt_va, ndisp = TARGETS[logger]
    out = CACHE / f"{Path(exe).stem}.trace.exe"
    src = COMPILERS / exe
    if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    CACHE.mkdir(parents=True, exist_ok=True)
    data = bytearray(src.read_bytes())
    pe = pefile.PE(data=bytes(data))
    base = pe.OPTIONAL_HEADER.ImageBase
    text = next(s for s in pe.sections if s.Name.startswith(b".text\0"))
    iat = {}
    for e in pe.DIRECTORY_ENTRY_IMPORT:
        for i in e.imports:
            if i.name in (b"GetStdHandle", b"WriteFile"):
                iat[i.name] = i.address
    # stub length is fixed (about 110 bytes); it lives in the raw slack after the section's virtual size
    slack_off = text.PointerToRawData + text.Misc_VirtualSize
    cave_va = base + text.VirtualAddress + text.Misc_VirtualSize
    foff = text.PointerToRawData + (fmt_va - base - text.VirtualAddress)
    displaced = bytes(data[foff : foff + ndisp])
    assert displaced[:3] == b"\x55\x8b\xec", displaced.hex()
    stub = _assemble(cave_va, iat[b"GetStdHandle"], iat[b"WriteFile"], fmt_va + ndisp, displaced)
    assert text.SizeOfRawData - text.Misc_VirtualSize >= len(stub), "no slack"
    assert data[slack_off : slack_off + len(stub)] == bytes(len(stub)), "slack not empty"
    data[slack_off : slack_off + len(stub)] = stub
    data[foff : foff + ndisp] = b"\xe9" + struct.pack("<i", cave_va - (fmt_va + 5)) + b"\x90" * (ndisp - 5)
    vs_off = text.get_file_offset() + 8
    data[vs_off : vs_off + 4] = struct.pack("<I", text.Misc_VirtualSize + len(stub))
    out.write_bytes(data)
    return out


def _cstr(pe: pefile.PE, raw: bytes, va: int) -> str:
    try:
        off = pe.get_offset_from_rva(va - pe.OPTIONAL_HEADER.ImageBase)
    except Exception:
        return ""
    end = raw.index(b"\0", off)
    return raw[off:end].decode("latin1")


def trace(logger: str, program: Path, siblings: Path | None = None) -> dict:
    exe = build(logger)
    raw = (COMPILERS / TARGETS[logger][0]).read_bytes()
    pe = pefile.PE(data=raw)
    with tempfile.TemporaryDirectory(prefix="trace-") as tmp:
        tmp = Path(tmp)
        if siblings:
            for f in siblings.iterdir():
                if f.is_file():
                    shutil.copy2(f, tmp / f.name)
        shutil.copy2(program, tmp / program.name)
        shutil.copy2(exe, tmp / exe.name)
        env = dict(os.environ, WINEDEBUG="-all", WINEPREFIX=os.environ.get("WINEPREFIX", str(Path.home() / ".wine-cr6")))
        env.setdefault("WINEDLLOVERRIDES", "winemac.drv,winex11.drv,winewayland.drv=d")
        r = subprocess.run([str(WINE), exe.name, program.name], cwd=tmp, env=env, capture_output=True, timeout=120)
    recs = []
    err = r.stderr
    magic = struct.pack("<I", MAGIC)
    for m in re.finditer(re.escape(magic), err):
        blk = err[m.start() : m.start() + 48]
        if len(blk) < 48:
            continue
        w = struct.unpack("<12I", blk)
        frames = []
        for x in w[4:]:
            if not x:
                break
            frames.append(hex(x))
        recs.append({"kind": "error" if w[1] == 1 else "warning" if w[1] == 0 else str(w[1]),
                     "fmt": _cstr(pe, raw, w[2]), "reporter_ret": hex(w[3]), "frames": frames})
    out = re.sub(r"\r+", "\r", r.stdout.decode("utf-8", "replace")).replace("\r\n", "\n").replace("\r", "\n")
    return {"stdout": out.rstrip("\n"), "records": recs}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("logger", choices=TARGETS)
    ap.add_argument("program", type=Path)
    ap.add_argument("--siblings", type=Path)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = trace(a.logger, a.program.resolve(), a.siblings)
    if a.json:
        print(json.dumps(res, indent=1))
        return
    print(res["stdout"])
    print("--- trace (emission order; frames[0] = caller of the reporter)")
    for i, rec in enumerate(res["records"]):
        print(f"{i} {rec['kind']:7} {' '.join(rec['frames'][:4])}  {rec['fmt']!r}")


if __name__ == "__main__":
    sys.exit(main())
