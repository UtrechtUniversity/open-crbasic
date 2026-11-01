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

import argparse
import concurrent.futures
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import fuzz  # noqa: E402
import impact  # noqa: E402

from opencrbasic import profiles  # noqa: E402

FUZZ_CORPUS = ROOT / "tests" / "corpus" / "fuzz"
SIBLINGS_DIGEST = fuzz.siblings_digest()


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


def rerecord(cr6_path: Path) -> bool:
    """Re-run the reference for a stale `needs_siblings` reproducer against the current
    `crbasic/` files and store its result and their digest (the reproducer itself is kept).
    Returns whether the recording changed."""
    meta_path = cr6_path.with_suffix(".json")
    meta = json.loads(meta_path.read_text())
    profile = profiles.get(meta.get("logger") or cr6_path.parent.parent.name)
    lines = cr6_path.read_text(encoding="utf-8", errors="replace").splitlines()
    with tempfile.TemporaryDirectory(prefix="fuzz-rerecord-") as tmp:
        path = fuzz.materialise(fuzz.Seed("replay", cr6_path, fuzz.CRBASIC_DIR), lines, Path(tmp), cr6_path.name)
        their_exit = their_out = None
        for attempt in range(3):
            try:
                their_exit, their_out = fuzz.run_theirs(path, profile, Path(tmp))
                break
            except fuzz.oracle.OracleError:
                if attempt == 2:
                    return False  # no verdict: the row stays stale
    changed = (their_exit, their_out) != (meta.get("their_exit"), meta.get("theirs"))
    meta.update(their_exit=their_exit, theirs=their_out, siblings_sha256=SIBLINGS_DIGEST)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return changed


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
    try:
        if use_recorded_theirs and "their_exit" in meta and "theirs" in meta:
            if needs_siblings and meta.get("siblings_sha256") != SIBLINGS_DIGEST:
                # recorded against other `crbasic/` files: no verdict until `--rerecord-stale`
                return {
                    "file": fuzz._display_path(cr6_path),
                    "logger": logger,
                    "recorded_class": recorded_class,
                    "now_class": "stale_recording",
                    "status": "stale recording",
                    "our_exit": None,
                    "their_exit": None,
                    "ours": "",
                    "theirs": "crbasic/ changed since this was recorded",
                }
            with tempfile.TemporaryDirectory(prefix="fuzz-replay-") as tmp:
                path = fuzz.materialise(seed, lines, Path(tmp), cr6_path.name)
                our_exit, our_out = fuzz.run_ours(path, profile)
            their_exit, their_out = meta["their_exit"], meta["theirs"]
            if not fuzz.oracle._valid_result({"exit": their_exit, "stdout": their_out}, cr6_path.name):
                # a runner failure saved as the reference result is no verdict
                return {
                    "file": fuzz._display_path(cr6_path),
                    "logger": logger,
                    "recorded_class": recorded_class,
                    "now_class": "recorded_runner_failure",
                    "status": "recorded runner failure",
                    "our_exit": our_exit,
                    "their_exit": None,
                    "ours": our_out,
                    "theirs": their_out,
                }
        else:
            our_exit, our_out, their_exit, their_out = fuzz.run_mutant(seed, lines, profile, name=cr6_path.name)
    except fuzz.oracle.OracleError as exc:
        return {
            "file": fuzz._display_path(cr6_path),
            "logger": logger,
            "recorded_class": recorded_class,
            "now_class": "oracle_error",
            "status": f"oracle error: {exc}",
            "our_exit": None,
            "their_exit": None,
            "ours": "",
            # Harness failure description retained for triage; this is not compiler diagnostics.
            "theirs": str(exc),
        }
    now_class = fuzz.classify(our_exit, our_out, their_exit, their_out)

    if now_class is None:
        status = "FIXED (now exact)"
    elif now_class == recorded_class:
        status = f"still {now_class}"
    else:
        status = f"changed: {recorded_class} -> {now_class}"

    return {
        "file": fuzz._display_path(cr6_path),
        "logger": logger,
        "recorded_class": recorded_class,
        "now_class": now_class,
        "status": status,
        "our_exit": our_exit,
        "their_exit": their_exit,
        "ours": our_out,
        "theirs": their_out,
    }


def gate_failed(results: list[dict], gate: str, baseline: list[dict] | None = None) -> bool:
    if any(r["now_class"] == "oracle_error" for r in results):
        return True
    if gate == "report":
        return False
    if baseline is not None and gate == "no-regressions":
        found = impact.regressions(baseline, results)
        for line in found:
            print("REGRESSION " + line)
        if found:
            return True
    if any(r["now_class"] == "implementation_exception" for r in results):
        return True
    if gate == "exact":
        return any(r["now_class"] is not None for r in results)
    return any(r["now_class"] in {"false_reject", "crash", "hang"} for r in results)


def write_json_report(path: Path, results: list[dict]) -> None:
    counts = Counter(r["now_class"] or "exact" for r in results)
    for r in results:
        r.setdefault("impact", impact.impact_class(r))
    impacts = Counter(r["impact"] for r in results)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "total": len(results),
                    "counts": dict(sorted(counts.items())),
                    "impact": {c: impacts[c] for c in impact.CLASSES if impacts[c]},
                },
                "rows": results,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


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
    ap.add_argument(
        "--prune-fixed",
        action="store_true",
        help="delete each reproducer's .CR6/.json pair once opencrbasic now matches "
        "the recorded real-compiler output exactly, without re-invoking the "
        "real compiler",
    )
    ap.add_argument(
        "--gate",
        choices=["report", "no-regressions", "exact"],
        default="report",
        help="exit nonzero for regressions or any non-exact result",
    )
    ap.add_argument("--json", type=Path, help="write summary counts and result rows as JSON")
    ap.add_argument(
        "--recorded",
        action="store_true",
        help="compare with the recorded reference output instead of rerunning it (no Wine needed)",
    )
    ap.add_argument(
        "--baseline",
        type=Path,
        help="a previous --json report; with --gate no-regressions, fail on a new "
        "CR6/CR1000X false accept, false reject, mode difference or first-error "
        "regression (tools/impact.py)",
    )
    ap.add_argument(
        "--save-baseline", type=Path, help="write the compact (logger, file, impact) baseline that --baseline reads"
    )
    ap.add_argument(
        "--rerecord-stale",
        action="store_true",
        help="re-run the reference for reproducers whose crbasic/ siblings changed "
        "since they were recorded, store the new result, then replay",
    )
    ap.add_argument("paths", nargs="*", type=Path)
    args = ap.parse_args()

    files = find_reproducers(args.logger, args.paths)
    if not files:
        print("no reproducers found", file=sys.stderr)
        return 1

    if args.prune_fixed:
        return prune_fixed(files, args.workers)

    if args.rerecord_stale:
        stale = [
            p
            for p in files
            if p.read_text(encoding="utf-8", errors="replace").startswith(fuzz.SIBLINGS_MARKER)
            and json.loads(p.with_suffix(".json").read_text()).get("siblings_sha256") != SIBLINGS_DIGEST
        ]
        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            changed = sum(pool.map(rerecord, stale))
        print(f"re-recorded {len(stale)} stale reproducers, {changed} with a different reference result")

    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda p: replay_one(p, use_recorded_theirs=args.recorded), files))
    for r in results:
        r["impact"] = impact.impact_class(r)
    baseline = None
    if args.baseline:
        baseline = json.loads(args.baseline.read_text())["rows"]
        for r in baseline:
            r.setdefault("impact", impact.impact_class(r))

    if args.json:
        write_json_report(args.json, results)
    if args.save_baseline:
        args.save_baseline.write_text(
            json.dumps({"rows": [{k: r[k] for k in ("logger", "file", "impact")} for r in results]}, indent=0) + "\n"
        )

    still = fixed = changed = oracle_errors = 0
    for r in results:
        if r["now_class"] == "oracle_error":
            oracle_errors += 1
        elif r["now_class"] is None:
            fixed += 1
        elif r["now_class"] == r["recorded_class"]:
            still += 1
        else:
            changed += 1
        print(f"{r['status']:<32} {r['file']}")

    print(
        f"\n{len(results)} reproducers: {still} still reproduce, {fixed} fixed, "
        f"{changed} changed class, {oracle_errors} oracle errors"
    )
    print("\n" + impact.table(results))
    return 1 if gate_failed(results, args.gate, baseline) else 0


if __name__ == "__main__":
    sys.exit(main())
