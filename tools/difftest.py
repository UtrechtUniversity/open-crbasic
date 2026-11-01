#!/usr/bin/env python3
"""Differential test: compile programs with opencrbasic and with the real compiler, compare.

    python3 tools/difftest.py [--logger cr6] [--group repo|chm|geus|all] [--show N] [paths...]

Each program is classified as
  exact    identical stdout after normalisation,
  verdict  both accept or both reject, but the text differs,
  mismatch one accepts and the other rejects,
  implementation_exception a swallowed opencrbasic implementation failure.
Results go to stdout and to `difftest-<logger>.json` (ignored by git). The real compiler runs
through tools/oracle.py (cached), so the first run of a large corpus is slow.
"""

import argparse
import concurrent.futures
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import fuzz  # noqa: E402
import impact  # noqa: E402
import oracle  # noqa: E402

from opencrbasic import profiles  # noqa: E402
from opencrbasic.compiler import compile_file  # noqa: E402

GROUPS = {
    "repo": (REPO / "tests" / "corpus" / "firnmelt", "*.CR6"),
    "chm": (ROOT / "tests" / "corpus" / "chm", "*.CR6"),
}
# GEUS station programs (human decision 2026-10-24, `PLAN.md` "GEUS corpus"): every program with
# the profile's extension, Archive/Testing/Development included, compiled beside the files of its
# own directory
GEUS = REPO / "examples" / "Logger Programs"
_GEUS_SIBLINGS = (".crb", ".dld", ".cr1x", ".cr6", ".txt")


def display_path(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def gate_failed(rows: list[dict], gate: str) -> bool:
    if not rows:
        return True
    if any(row["class"] == "oracle_error" for row in rows):
        return True
    if gate == "report":
        return False
    if any(row["class"] == "implementation_exception" for row in rows):
        return True
    if gate == "exact":
        return any(row["status"] != "exact" for row in rows)
    return any(row["class"] in {"false_reject", "crash", "hang"} for row in rows)


def ours(path: Path, profile) -> tuple[int, str]:
    try:
        result = compile_file(path, profile, provenance=False)
        if result.implementation_exceptions:
            return 4, "IMPLEMENTATION EXCEPTION: " + " | ".join(result.implementation_exceptions)
        return (0 if result.ok else 1), oracle.as_read_back("\n".join(result.result_lines()))
    except Exception as exc:  # a crash is a result, not a harness failure
        return 2, f"CRASH {type(exc).__name__}: {exc}"


def theirs(path: Path, profile, siblings: Path | None) -> tuple[int, str]:
    exe = REPO / "compiler" / profile.exe_name
    r = oracle.run_oracle(path, siblings, compiler_path=exe)
    return r["exit"], r["stdout"]


def normalise(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def compare_one(item: tuple[str, Path, Path | None], profile) -> dict:
    group, path, siblings = item
    if group == "geus":
        with tempfile.TemporaryDirectory() as tmp:
            sib = Path(tmp) / "sib"
            sib.mkdir()
            for f in path.parent.iterdir():
                if f.is_file() and f.suffix.lower() in _GEUS_SIBLINGS:
                    shutil.copy2(f, sib / f.name)
            staged = Path(tmp) / re.sub(r"[^A-Za-z0-9_.-]", "_", path.name)
            shutil.copy2(path, staged)
            for f in sib.iterdir():
                if not (Path(tmp) / f.name).exists():
                    shutil.copy2(f, Path(tmp) / f.name)
            row = compare_one(("geus-staged", staged, sib), profile)
        row.update(group="geus", file=display_path(path))
        return row
    try:
        their_exit, their_out = theirs(path, profile, siblings)
    except oracle.OracleError as exc:
        # Keep the usual row fields for report consumers. `theirs` describes harness failure; it
        # is not reference-compiler diagnostic output and must never enter semantic classification.
        return {"group": group, "file": display_path(path), "status": "oracle_error",
                "class": "oracle_error", "ours": "", "theirs": str(exc),
                "our_exit": None, "their_exit": None, "logger": getattr(profile, "name", ""),
                "impact": impact.OPERATIONAL}

    our_exit, our_out = ours(path, profile)
    cls = fuzz.classify(our_exit, our_out, their_exit, their_out)
    if cls is None:
        status = "exact"
    elif cls == "implementation_exception":
        status = cls
    elif our_exit == their_exit:
        status = "verdict"
    else:
        status = "mismatch"
    row = {"group": group, "file": display_path(path), "status": status, "class": cls,
           "ours": our_out, "theirs": their_out, "our_exit": our_exit,
           "their_exit": their_exit, "logger": getattr(profile, "name", "")}
    row["impact"] = impact.impact_class(row)
    return row


def summarise(rows: list[dict]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = summary.setdefault(
            row["group"], {"exact": 0, "verdict": 0, "mismatch": 0, "oracle_error": 0,
                           "implementation_exception": 0}
        )
        counts[row["status"]] += 1
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logger", default="cr6")
    ap.add_argument("--group", choices=["repo", "chm", "geus", "all"], default="all",
                    help="`all` is repo and chm; geus runs alone")
    ap.add_argument("--gate", choices=["report", "no-regressions", "exact"], default="report",
                    help="exit nonzero for regressions or any non-exact result")
    ap.add_argument("--show", type=int, default=15, help="mismatches to print")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("paths", nargs="*", type=Path)
    args = ap.parse_args()
    profile = profiles.get(args.logger)

    work: list[tuple[str, Path, Path | None]] = []
    if args.paths:
        work = [("cli", p.resolve(), p.resolve().parent) for p in args.paths]
    elif args.group == "geus":
        ext = {"cr6": ".CR6", "cr1000x": ".CR1X"}.get(args.logger)
        work = [("geus", p, None) for p in sorted(GEUS.rglob("*"))
                if ext and p.is_file() and p.suffix.upper() == ext]
    else:
        for group, (directory, pattern) in GROUPS.items():
            if args.group in ("all", group):
                siblings = directory if group != "chm" else None
                work += [(group, p, siblings) for p in sorted(directory.glob(pattern))]

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(lambda w: compare_one(w, profile), work))

    summary = summarise(rows)
    for group, s in summary.items():
        total = sum(s.values())
        print(f"{args.logger} {group}: {total} programs, exact {s['exact']}, verdict {s['verdict']}, "
              f"mismatch {s['mismatch']}, oracle_error {s['oracle_error']}, "
              f"implementation_exception {s['implementation_exception']}")
    print(impact.table(rows))
    shown = 0
    for r in rows:
        if r["status"] != "exact" and shown < args.show:
            shown += 1
            print(f"--- {r['status']}: {r['file']}")
            print("  ours:   " + normalise(r["ours"]).replace("\n", "\n          ")[:600])
            print("  theirs: " + normalise(r["theirs"]).replace("\n", "\n          ")[:600])
    (ROOT / f"difftest-{args.logger}.json").write_text(json.dumps(rows, indent=1))
    return 1 if gate_failed(rows, args.gate) else 0


if __name__ == "__main__":
    sys.exit(main())
