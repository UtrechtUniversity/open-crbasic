#!/usr/bin/env python3
"""List every error/warning format string CR6Comp.exe can emit, from a Ghidra decompile dump.

    python3 tools/message_catalog.py /opt/re/out/decomp.c > docs/messages.tsv

Errors are reported through FUN_005089c0 (error) and FUN_00508a00 (warning); see docs/binary.md.
Output columns: kind, function containing the call, format string.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_tables import DEFAULT_EXE, Image  # noqa: E402

CALL = re.compile(r"(?:thunk_)?FUN_(005089c0|00508a00)\((s_\w+_([0-9a-f]{8})|PTR_\w+|&DAT_([0-9a-f]{8}))")
FUNC = re.compile(r"^// ==== (\S+) @ ([0-9a-f]+)")


def main() -> int:
    img = Image(DEFAULT_EXE)
    current = "?"
    seen = set()
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        m = FUNC.match(line)
        if m:
            current = m.group(1)
            continue
        for m in CALL.finditer(line):
            kind = "error" if m.group(1) == "005089c0" else "warning"
            addr = m.group(3) or m.group(4)
            text = img.cstr(int(addr, 16)) if addr else None
            if text is None:
                text = f"<{m.group(2)}>"
            key = (kind, current, text)
            if key in seen:
                continue
            seen.add(key)
            print(f"{kind}\t{current}\t{text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
