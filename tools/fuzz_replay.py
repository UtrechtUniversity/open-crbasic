#!/usr/bin/env python3
"""Re-check every saved fuzz reproducer against opencrbasic and the real compiler.

    python3 tools/fuzz_replay.py [--logger cr6] [tests/corpus/fuzz/cr6/false_accept/*.CR6 ...]

For each `tests/corpus/fuzz/<logger>/<class>/<hash>.CR6` (or explicit paths given on the command
line), reads the sibling `<hash>.json` for its recorded class, recompiles the file exactly as
`tools/fuzz.py` did when it was saved (copying `crbasic/`'s current contents alongside it first
when the file's first line carries the `' fuzz-needs-siblings: crbasic` marker), and reports
whether it still reproduces that class, has become exact (fixed), or now reproduces a different
class (the checker changed shape, not just got better). Intended for the agents fixing
`src/opencrbasic/**` to confirm a fix without re-running the fuzzer.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

from opencrbasic import profiles  # noqa: E402
import fuzz  # noqa: E402

FUZZ_CORPUS = ROOT / "tests" / "corpus" / "fuzz"


def find_reproducers(logger: str | None, paths: list[Path]) -> list[Path]:
    if paths:
        return paths
    loggers = [logger] if logger else [p.name for p in FUZZ_CORPUS.iterdir() if p.is_dir()]
    out = []
    for lg in loggers:
        d = FUZZ_CORPUS / lg
        if d.is_dir():
            out += sorted(d.glob("*/*.CR6"))
    return out


def replay_one(cr6_path: Path, use_recorded_theirs: bool = False) -> dict:
    cr6_path = cr6_path.resolve()
    meta_path = cr6_path.with_suffix(".json")
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    logger = meta.get("logger") or cr6_path.parent.parent.name
    recorded_class = meta.get("class") or cr6_path.parent.name
    profile = profiles.get(logger)

    text = cr6_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    needs_siblings = bool(lines) and lines[0].startswith(fuzz.SIBLINGS_MARKER)
    siblings = fuzz.CRBASIC_DIR if needs_siblings else None
    seed = fuzz.Seed("replay", cr6_path, siblings)

    # --prune-fixed trusts the recorded real-compiler verdict instead of re-invoking Wine: it
    # only needs to know whether *our* output still matches that recorded verdict, and re-running
    # a program through Wine ~1840 times just to re-derive text that is already on disk is pure
    # overhead this mode is meant to avoid.
    if use_recorded_theirs and "their_exit" in meta and "theirs" in meta:
        with tempfile.TemporaryDirectory(prefix="fuzz-replay-") as tmp:
            path = fuzz.materialise(seed, lines, Path(tmp), cr6_path.name)
            our_exit, our_out = fuzz.run_ours(path, profile)
        their_exit, their_out = meta["their_exit"], meta["theirs"]
    else:
        our_exit, our_out, their_exit, their_out = fuzz.run_mutant(seed, lines, profile, name=cr6_path.name)
    now_class = fuzz.classify(our_exit, our_out, their_exit, their_out)

    if now_class is None:
        status = "FIXED (now exact)"
    elif now_class == recorded_class:
        status = f"still {now_class}"
    else:
        status = f"changed: {recorded_class} -> {now_class}"

    return {
        "file": str(cr6_path.relative_to(ROOT)),
        "logger": logger,
        "recorded_class": recorded_class,
        "now_class": now_class,
        "status": status,
        "our_exit": our_exit,
        "their_exit": their_exit,
        "ours": our_out,
        "theirs": their_out,
    }


def prune_fixed(files: list[Path], workers: int) -> int:
    """Delete each reproducer's .CR6/.json pair once opencrbasic now matches the *recorded*
    real-compiler output exactly, without re-invoking the real compiler (see `replay_one`'s
    `use_recorded_theirs`)."""
    before = Counter((f.parent.parent.name, f.parent.name) for f in files)

    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(lambda p: replay_one(p, use_recorded_theirs=True), files))

    after = Counter(before)
    removed = 0
    for r in results:
        print(f"{r['status']:<32} {r['file']}")
        if r["now_class"] is None:
            cr6_path = ROOT / r["file"]
            cr6_path.unlink(missing_ok=True)
            cr6_path.with_suffix(".json").unlink(missing_ok=True)
            after[(r["logger"], r["recorded_class"])] -= 1
            removed += 1

    kept = len(results) - removed
    print(f"\n{len(results)} reproducers checked: {removed} now exact and pruned, {kept} kept")
    print(f"\n{'logger/class':<28} {'before':>7} {'after':>7}")
    for key in sorted(before):
        print(f"{key[0] + '/' + key[1]:<28} {before[key]:>7} {after[key]:>7}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logger", default=None, help="restrict to one logger's reproducers")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--prune-fixed", action="store_true",
                     help="delete each reproducer's .CR6/.json pair once opencrbasic now matches "
                          "the recorded real-compiler output exactly, without re-invoking the "
                          "real compiler")
    ap.add_argument("paths", nargs="*", type=Path)
    args = ap.parse_args()

    files = find_reproducers(args.logger, args.paths)
    if not files:
        print("no reproducers found", file=sys.stderr)
        return 1

    if args.prune_fixed:
        return prune_fixed(files, args.workers)

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(replay_one, files))

    still = fixed = changed = 0
    for r in results:
        if r["now_class"] is None:
            fixed += 1
        elif r["now_class"] == r["recorded_class"]:
            still += 1
        else:
            changed += 1
        print(f"{r['status']:<32} {r['file']}")

    print(f"\n{len(results)} reproducers: {still} still reproduce, {fixed} fixed, "
          f"{changed} changed class")
    return 0


if __name__ == "__main__":
    sys.exit(main())
