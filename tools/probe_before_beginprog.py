#!/usr/bin/env python3
"""Regenerate `data/**/before_beginprog.json`: which instructions the real compiler rejects with
`%s illegally occurs before BeginProg.`.

    uv run tools/probe_before_beginprog.py --logger cr6 --out /tmp/bp-cr6

For each chm example the oracle accepts (`difftest-<logger>.json`, so run `tools/difftest.py`
first), the first two single-line uses of each instruction inside `BeginProg` are copied to just
before `BeginProg` and compiled with the oracle. An instruction is listed only when every probe of
it reports that message; any probe that compiles cleanly or fails differently keeps it out
(`SetSetting` with a constant value is accepted, so it is not listed). `EndIf` and `NEXT` are left
to the control-statement check. Pass `--write` to update the logger's data file.
"""

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import oracle

from opencrbasic import parser, profiles
from opencrbasic import syntax as S

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT


def probes(logger: str, out: Path) -> dict[str, list[str]]:
    profile = profiles.get(logger)
    accepted = [r["file"] for r in json.loads((ROOT / f"difftest-{logger}.json").read_text())
                if r["their_exit"] == 0 and "/chm/" in r["file"]]
    found: dict[str, list[str]] = {}
    for rel in accepted:
        path = REPO / rel
        text = path.read_bytes().decode("latin-1")
        if re.search(r"(?im)^\s*include", text):
            continue
        program, _ = parser.parse_file(path, profile=profile)
        begin = next((s.line for s in program.statements if isinstance(s, S.Instruction)
                      and s.keyword.upper() == "BEGINPROG"), None)
        if begin is None:
            continue
        lines = text.split("\n")
        for stmt in program.statements:
            if stmt.line <= begin or not isinstance(stmt, S.Instruction):
                continue
            kw = stmt.keyword.upper()
            if kw not in profile.instructions or kw in ("SCAN", "NEXTSCAN", "ENDPROG", "BEGINPROG"):
                continue
            src = lines[stmt.line - 1]
            if not re.match(r"\s*" + re.escape(stmt.keyword) + r"\b", src, re.I) or \
                    src.rstrip().endswith("_") or len(found.get(kw, [])) >= 2:
                continue
            name = f"{kw.lower()}_{len(found.get(kw, []))}.CR6"
            (out / name).write_bytes("\n".join(lines[:begin - 1] + [src] + lines[begin - 1:])
                                     .encode("latin-1"))
            found.setdefault(kw, []).append(name)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logger", default="cr6", choices=["cr6", "cr1000x", "cr300", "cr350"])
    ap.add_argument("--out", type=Path, required=True, help="directory for the probe programs")
    ap.add_argument("--write", action="store_true", help="update the logger's data file")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    profile = profiles.get(args.logger)
    exe = REPO / "compiler" / profile.exe_name
    found = probes(args.logger, args.out)

    def rejected(item: tuple[str, str]) -> tuple[str, bool]:
        kw, name = item
        stdout = oracle.run_oracle(args.out / name, compiler_path=exe)["stdout"]
        return kw, f"{profile.instructions[kw]['name']} illegally occurs before BeginProg." in stdout

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(rejected, [(kw, n) for kw, names in found.items() for n in names]))
    listed = sorted({kw for kw, bad in results if bad} - {kw for kw, bad in results if not bad}
                    - {"ENDIF", "NEXT"})
    print(f"{args.logger}: {len(found)} instructions probed, {len(listed)} rejected before BeginProg")
    if args.write:
        target = profile.data_dir / "before_beginprog.json"
        target.write_text(json.dumps({"instructions": listed}, indent=1) + "\n")
        print(f"wrote {target.relative_to(ROOT)}")
    else:
        print(" ".join(listed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
