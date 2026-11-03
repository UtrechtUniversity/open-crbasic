#!/usr/bin/env python3
"""Self-differential gate: opencrbasic's own CLI outputs before and after a change.

    python3 tools/selfdiff.py --save scratch/selfdiff-base.json        # record a baseline
    python3 tools/selfdiff.py --compare scratch/selfdiff-base.json     # exit 1 on any difference

Unlike `tools/difftest.py`, this needs no reference compiler: it checks that a refactor leaves
every observable `crbc` output unchanged. Each case runs the CLI in-process twice:
`-s -t -z TDF -l LIST -p PRE <file> <msgfile>` and `-u -i PRE <file> <msgfile>`, recording exit
status, stdout, and the bytes of every written file. Cases: `../crbasic/*.CR6` and
`tests/corpus/chm/*.CR6` on all four loggers, and every fuzz reproducer on its own logger.

It establishes output equivalence of two opencrbasic revisions only, never agreement with the
real compilers.
"""

import argparse
import concurrent.futures
import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT
sys.path.insert(0, str(ROOT / "src"))

LOGGERS = ("cr6", "cr1000x", "cr300", "cr350")
CRBASIC_DIR = REPO / "tests" / "corpus" / "station"
CHM_DIR = ROOT / "tests" / "corpus" / "chm"
FUZZ_DIR = ROOT / "tests" / "corpus" / "fuzz"
SIBLINGS_MARKER = "' fuzz-needs-siblings: crbasic"
RUNS = (
    ("a", ["-s", "-t", "-z", "{tmp}/out.tdf", "-l", "{tmp}/out.lst", "-p", "{tmp}/out.pre"]),
    ("b", ["-u", "-i", "{tmp}/out.pre"]),
)


def cases() -> list[tuple[str, str, bool]]:
    """(path, logger, needs crbasic siblings) for every case, in a stable order."""
    out = []
    for group in (sorted(CRBASIC_DIR.glob("*.CR6")), sorted(CHM_DIR.glob("*.CR6"))):
        for path in group:
            for logger in LOGGERS:
                out.append((str(path), logger, False))
    for path in sorted(FUZZ_DIR.glob("*/*/*.CR6")):
        first = path.read_bytes().split(b"\n", 1)[0].decode("latin-1")
        out.append((str(path), path.parent.parent.name, first.startswith(SIBLINGS_MARKER)))
    return out


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def run_case(case: tuple[str, str, bool]) -> tuple[str, dict]:
    from opencrbasic import cli

    path_s, logger, siblings = case
    path = Path(path_s)
    key = f"{logger}:{path.relative_to(REPO)}"
    record = {}
    with tempfile.TemporaryDirectory(prefix="selfdiff-") as tmp:
        work = Path(tmp) / "src"
        work.mkdir()
        if siblings:
            for entry in sorted(CRBASIC_DIR.iterdir()):
                if entry.is_file():
                    shutil.copy2(entry, work / entry.name)
            target = work / path.name
            shutil.copy2(path, target)
        else:
            target = path
        for run, options in RUNS:
            for name in ("out.tdf", "out.lst", "out.pre", "msg.txt"):
                (Path(tmp) / name).unlink(missing_ok=True)
            argv = ["--logger", logger] + [o.format(tmp=tmp) for o in options]
            argv += [str(target), f"{tmp}/msg.txt"]
            stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout):
                    code = cli.main(argv)
            except Exception as exc:  # recorded, so a new exception is a difference
                code = f"exception {type(exc).__name__}: {exc}"
            files = {
                name: _digest((Path(tmp) / name).read_bytes())
                for name in ("out.tdf", "out.lst", "out.pre", "msg.txt")
                if (Path(tmp) / name).is_file()
            }
            out = stdout.getvalue().replace(str(target), "<program>")
            record[run] = {"exit": code, "stdout": out, "files": files}
    return key, record


def collect(workers: int) -> dict:
    todo = cases()
    with concurrent.futures.ProcessPoolExecutor(workers) as pool:
        return dict(pool.map(run_case, todo, chunksize=8))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--save", type=Path, help="write the current outputs as a baseline")
    mode.add_argument("--compare", type=Path, help="compare current outputs with a baseline")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--show", type=int, default=10, help="differences to print")
    args = ap.parse_args()
    current = collect(args.workers)
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
        print(f"selfdiff: saved {len(current)} cases to {args.save}")
        return 0
    base = json.loads(args.compare.read_text())
    differ = sorted(k for k in base.keys() | current.keys() if base.get(k) != current.get(k))
    for key in differ[: args.show]:
        print(
            f"DIFF {key}\n  base:    {json.dumps(base.get(key))[:600]}\n  current: {json.dumps(current.get(key))[:600]}"
        )
    print(f"selfdiff: {len(current)} cases, {len(differ)} differ from {args.compare}")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
