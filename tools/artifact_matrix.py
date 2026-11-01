#!/usr/bin/env python3
"""Artifact matrix (M5): every output of the reference compiler and of `crbc` per profile, option
set and program, compared byte for byte: exit status, stdout, the message file, and every file an
option writes (TDF, table lists, preprocessed source, CDM image), including whether it is written
at all or written empty.

    uv run python tools/artifact_matrix.py --logger cr6 --jobs 4
    uv run python tools/artifact_matrix.py --logger cr6 --baseline tests/artifact_baseline.json --gate no-regressions
    uv run python tools/artifact_matrix.py --logger cr6 --write-baseline tests/artifact_baseline.json

Reference runs are cached in `.oracle-cache/matrix/` by the compiler bytes, the arguments and the
input files; a runner failure is never cached or counted. Classes and the support table:
`docs/artifacts.md`.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
import oracle  # noqa: E402

from opencrbasic import profiles  # noqa: E402

CACHE = oracle.CACHE_DIR / "matrix"
CRBASIC = ROOT / "tests" / "corpus" / "firnmelt"
CHM = ROOT / "tests" / "corpus" / "chm"
# option set -> arguments; `P` is the program
OPTIONS = {
    "plain": ["P"],
    "message-file": ["P", "msg.txt"],
    "signature": ["-s", "P"],
    "timing": ["-t", "P"],
    "unused": ["-u", "P"],
    "tables": ["-z", "o.tdf", "-l", "o.lst", "-c", "o.cl", "P"],
    "preprocess": ["-p", "o.pre", "P"],
    "include": ["-i", "o.inc", "P"],
    "cdm": ["-b", "o.cdm", "P"],
    "combined": ["-st", "-z", "o.tdf", "P"],
}
# programs the reference rejects, for the outputs of a failed compile
FAILING = {
    "fail_decl.CR6": "Public 1bad\nBeginProg\n  Scan(1,Sec,0,0)\n  NextScan\nEndProg\n",
    "fail_table.CR6": (
        "Public X\nDataTable(T,True,-1)\n  Sample(1,Nope,FP2)\nEndTable\nBeginProg\n"
        "  Scan(1,Sec,0,0)\n    CallTable T\n  NextScan\nEndProg\n"
    ),
    "fail_include.CR6": 'Include "CPU:missing.crb"\nBeginProg\n  Scan(1,Sec,0,0)\n  NextScan\nEndProg\n',
    "fail_scan.CR6": "Public X\nBeginProg\n  Scan(1,Sec,0,0)\n    X = \nEndProg\n",
    "fail_section.CR6": (
        'Public X\nIncludeSection("Defs","CPU:missing.crb")\nBeginProg\n  Scan(1,Sec,0,0)\n  NextScan\nEndProg\n'
    ),
}


SIBLING_SUFFIXES = (".cr6", ".crb", ".cr1x", ".cr300", ".cr350", ".csv")


def corpus(stride: int) -> list[tuple[str, dict[str, bytes]]]:
    """(program name, every file staged next to it) per program."""
    siblings = {
        f.name: f.read_bytes()
        for f in sorted(CRBASIC.iterdir())
        if f.is_file() and f.suffix.lower() in SIBLING_SUFFIXES
    }
    out = [(p.name, siblings) for p in sorted(CRBASIC.glob("*.CR6"))]
    out += [(p.name, {p.name: p.read_bytes()}) for p in sorted(CHM.glob("*.CR6"))[::stride]]
    out += [(name, {name: text.replace("\n", "\r\n").encode()}) for name, text in FAILING.items()]
    return out


def lines(data: bytes) -> bytes:
    """Output bytes with line endings normalised (Wine doubles the CR of a piped stdout) and
    trailing blank lines dropped; every other byte is kept."""
    return re.sub(rb"\r*\n|\r+", b"\n", data).rstrip(b"\n")


def run(argv: list[str], name: str, files: dict[str, bytes], env=None) -> dict:
    with tempfile.TemporaryDirectory(prefix="matrix-") as d:
        tmp = Path(d)
        for f, data in files.items():
            (tmp / f).write_bytes(data)
        proc = subprocess.run(
            [name if a == "P" else a for a in argv], cwd=tmp, capture_output=True, timeout=120, env=env
        )
        return {
            "exit": proc.returncode,
            "stdout": base64.b64encode(lines(proc.stdout)).decode(),
            "files": {
                f.name: base64.b64encode(f.read_bytes()).decode()
                for f in sorted(tmp.iterdir())
                if f.is_file() and f.name not in files
            },
        }


def stdout_text(result: dict) -> str:
    return base64.b64decode(result["stdout"]).decode("latin-1")


def reference(exe: Path, option: str, name: str, files: dict[str, bytes]) -> dict | None:
    h = hashlib.sha256(exe.read_bytes())
    h.update(json.dumps(OPTIONS[option]).encode() + b"\0" + name.encode())
    for k in sorted(files):
        h.update(b"\0" + k.encode() + b"\0" + hashlib.sha256(files[k]).digest())
    path = CACHE / f"{h.hexdigest()}.json"
    if path.exists():
        return json.loads(path.read_text())
    prefix, env = oracle._runner_command(exe)
    try:
        result = run(prefix + OPTIONS[option], name, files, env)
    except OSError, subprocess.TimeoutExpired:
        return None
    if result["exit"] not in (0, 1) or f"{name} -- " not in stdout_text(result):
        return None  # a runner failure, not a compiler verdict
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result))
    return result


def ours(logger: str, option: str, name: str, files: dict[str, bytes]) -> dict:
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    env.pop("OPENCRBASIC_STRICT", None)
    return run([sys.executable, "-m", "opencrbasic", "--logger", logger, *OPTIONS[option]], name, files, env)


def classify(theirs: dict | None, mine: dict) -> dict[str, str]:
    """`{"reference failure": ""}`, `{"exact": ""}`, or each output that differs -> the sha256 of
    opencrbasic's bytes for it (`exit`, `stdout`, `<file> differs`, `<file> missing` (the reference
    wrote it, we did not), `<file> extra`), so the gate notices a different wrong output too."""
    if theirs is None:
        return {"reference failure": ""}

    def sha(b64: str) -> str:
        return hashlib.sha256(base64.b64decode(b64)).hexdigest()[:16]

    out: dict[str, str] = {}
    if theirs["exit"] != mine["exit"]:
        out["exit"] = str(mine["exit"])
    if theirs["stdout"] != mine["stdout"]:
        out["stdout"] = sha(mine["stdout"])
    for f in sorted(set(theirs["files"]) | set(mine["files"])):
        if f not in mine["files"]:
            out[f"{f} missing"] = ""
        elif f not in theirs["files"]:
            out[f"{f} extra"] = sha(mine["files"][f])
        elif theirs["files"][f] != mine["files"][f]:
            out[f"{f} differs"] = sha(mine["files"][f])
    return out or {"exact": ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--logger", default="cr6", choices=list(profiles.PROFILES))
    ap.add_argument("--stride", type=int, default=10, help="every Nth help example (default 10)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--gate", choices=["report", "no-regressions"], default="report")
    ap.add_argument("--write-baseline", type=Path)
    ap.add_argument("--show", type=int, default=10)
    args = ap.parse_args()

    exe = ROOT / "compiler" / profiles.get(args.logger).exe_name
    cells = [(option, prog) for prog in corpus(args.stride) for option in OPTIONS]

    def one(cell):
        option, (name, files) = cell
        return option, name, classify(reference(exe, option, name, files), ours(args.logger, option, name, files))

    with ThreadPoolExecutor(args.jobs) as pool:
        results = list(pool.map(one, cells))
    table = {f"{option}|{name}": classes for option, name, classes in results}

    print(f"{args.logger}: {len({n for _, n, _ in results})} programs x {len(OPTIONS)} option sets")
    by_option: dict[str, Counter] = {o: Counter() for o in OPTIONS}
    for option, _, classes in results:
        by_option[option][
            "exact" if "exact" in classes else "reference failure" if "reference failure" in classes else "differs"
        ] += 1
    for option, c in by_option.items():
        print(
            f"  {option:13} exact {c['exact']:4}  differs {c['differs']:4}  reference failure {c['reference failure']:3}"
        )
    outputs = Counter(k for _, _, classes in results for k in classes if k not in ("exact", "reference failure"))
    if outputs:
        print("  differences by output: " + ", ".join(f"{k} {v}" for k, v in sorted(outputs.items())))
    shown = [(o, n, c) for o, n, c in results if "exact" not in c and "reference failure" not in c]
    for option, name, classes in shown[: args.show]:
        print(f"  --- {option} {name}: {', '.join(classes)}")

    if args.write_baseline:
        base = json.loads(args.write_baseline.read_text()) if args.write_baseline.exists() else {}
        base[args.logger] = dict(sorted(table.items()))
        args.write_baseline.write_text(json.dumps(base, indent=1, sort_keys=True) + "\n")
    status = 0
    if args.gate == "no-regressions" and not args.baseline:
        ap.error("--gate no-regressions needs --baseline")
    if args.baseline:
        everything = json.loads(args.baseline.read_text())
        if args.logger not in everything:
            ap.error(f"{args.baseline} has no {args.logger} cells")
        base = everything[args.logger]
        # a regression: a cell not in the baseline, or a difference (with its output fingerprint)
        # or a reference failure the baseline does not have; losing a difference is an improvement
        regressions = [
            k
            for k, classes in table.items()
            if k not in base or ("exact" not in classes and not classes.items() <= base[k].items())
        ]
        missing = sorted(set(base) - set(table))
        improved = [k for k, classes in table.items() if k in base and k not in regressions and classes != base[k]]
        for k in regressions:
            print(f"  REGRESSION {k}: {base.get(k, 'not in the baseline')} -> {table[k]}")
        for k in missing:
            print(f"  NOT RUN {k}")
        for k in improved:
            print(f"  IMPROVED {k}: {base[k]} -> {table[k]} (refresh the baseline)")
        if args.gate == "no-regressions" and (regressions or missing):
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
