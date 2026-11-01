"""Map CR6 instructions to the decompiled functions that call a given function.

Each instruction's `compile_fn` (`src/opencrbasic/data/instructions.json`) points at an
incremental-link thunk; the thunk chain is followed through the PE image to the builder, and every
call of FUNCTION inside a decompiled builder body is printed with its arguments. A caller that is no
builder prints `?`: run the tool again on that helper to find its builders.

    uv run tools/builder_map.py FUN_005077e0          # shared destination resolver
    uv run tools/builder_map.py FUN_005163b0          # shared Reps validator

Reads `scratch/re/CR6Comp.decomp.c` (regenerate with `tools/ghidra/DumpAll.java`) and
`../compiler/CR6Comp.exe`. Used in round 24 (`docs/checker.md` "Builder destination counts").
"""

import argparse
import bisect
import json
import re
from pathlib import Path

import pefile

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("function", help="decompiled function name, e.g. FUN_005077e0")
    ap.add_argument("--decomp", type=Path, default=ROOT / "scratch" / "re" / "CR6Comp.decomp.c")
    ap.add_argument("--exe", type=Path, default=ROOT / "compiler" / "CR6Comp.exe")
    args = ap.parse_args()

    pe = pefile.PE(str(args.exe), fast_load=True)
    base = getattr(pe.OPTIONAL_HEADER, "ImageBase")

    def target(va: int) -> int:
        code = pe.get_data(va - base, 5)
        while code[0] == 0xE9:  # jmp rel32
            va += 5 + int.from_bytes(code[1:5], "little", signed=True)
            code = pe.get_data(va - base, 5)
        return va

    src = args.decomp.read_text(errors="replace")
    heads = [(m.start(), int(m.group(1), 16)) for m in re.finditer(r"^// ==== \S+ @ ([0-9a-f]{8})\n", src, re.M)]
    starts = [h[0] for h in heads]
    builders: dict[int, list[str]] = {}
    for record in json.loads((ROOT / "src" / "opencrbasic" / "data" / "instructions.json").read_text()):
        builders.setdefault(target(int(record["compile_fn"], 16)), []).append(record["name"])
    own = int(args.function.rsplit("_", 1)[-1], 16)
    calls = set()
    for m in re.finditer(r"(?:thunk_)?" + re.escape(args.function) + r"\(([^;]*)\);", src):
        owner = heads[bisect.bisect_right(starts, m.start()) - 1][1]
        if owner == own:  # its own definition
            continue
        calls.add((owner, " ".join(m.group(1).split())))
    for owner, call_args in sorted(calls):
        names = ",".join(builders.get(owner, ["?"]))
        print(f"{owner:#010x} {names:30} {call_args}")


if __name__ == "__main__":
    main()
