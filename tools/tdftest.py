#!/usr/bin/env python3
"""Differential test for `tables.py`'s `-z` (TDF) and `-l` (table list) generation.

For each program in `crbasic/*.CR6` (siblings: `crbasic/`) and `tests/corpus/chm/*.CR6`
(siblings: its own directory) that the real compiler accepts, this:

  1. Runs the real `CR6Comp.exe -z out.tdf -l out.lst <program>` under Wine (cached by
     sha256(compiler bytes, program bytes, sorted sibling bytes) under
     `opencrbasic/.oracle-cache/tdf/<hash>.json`, base64-encoded `tdf`/`lst` bytes).
  2. Parses the program with `parser.parse_file` and derives tables with `tables.build_tables`,
     serialises with `tdf.serialize` and `tables.render_table_list`.
  3. Compares byte-for-byte and reports: TDF-identical count, table-list-identical count, and
     the first difference (table/field/attribute) for each mismatch.

Usage (from `opencrbasic/`):

    python3 tools/tdftest.py                     # crbasic/ + tests/corpus/chm/
    python3 tools/tdftest.py --group repo         # crbasic/ only
    python3 tools/tdftest.py --group chm --limit 50
    python3 tools/tdftest.py --write-fixtures     # also refresh tests/fixtures/tdf/*.json
    python3 tools/tdftest.py --logger cr300       # same, against cr300comp.exe (cr6|cr1000x|cr300|cr350)

Requires Wine (see PROGRESS.md); `--no-oracle` skips real-compiler runs and only reports on
already-cached results (used by the pytest tests when Wine is unavailable).
"""

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "src"))

from opencrbasic import parser, profiles, tdf  # noqa: E402
from opencrbasic.tables import build_tables  # noqa: E402
import oracle  # noqa: E402

DEFAULT_COMPILER = REPO / "compiler" / "CR6Comp.exe"
TDF_CACHE_DIR = ROOT / ".oracle-cache" / "tdf"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "tdf"

GROUPS = {
    "repo": (REPO / "crbasic", "*.CR6", REPO / "crbasic"),
    "chm": (ROOT / "tests" / "corpus" / "chm", "*.CR6", ROOT / "tests" / "corpus" / "chm"),
}


def _iter_sibling_files(siblings_dir: Path):
    for entry in sorted(siblings_dir.iterdir()):
        if entry.is_file():
            yield entry


def _cache_key(compiler_path: Path, program_path: Path, siblings_dir: Path) -> str:
    h = hashlib.sha256()
    h.update(hashlib.sha256(compiler_path.read_bytes()).digest())
    h.update(program_path.name.encode("utf-8"))
    h.update(program_path.read_bytes())
    for entry in _iter_sibling_files(siblings_dir):
        h.update(entry.name.encode("utf-8"))
        h.update(entry.read_bytes())
    return h.hexdigest()


def _runner_command(exe_path: Path):
    return oracle._runner_command(exe_path)


class NotCached(RuntimeError):
    """Raised by `run_oracle_tdf(..., cache_only=True)` for a program with no cached result."""


def run_oracle_tdf(program_path: Path, siblings_dir: Path, compiler_path: Path = DEFAULT_COMPILER,
                    use_cache: bool = True, cache_only: bool = False):
    """Compile with the real exe under Wine; return {"ok", "stdout", "tdf": bytes|None,
    "lst": bytes|None}, cached under TDF_CACHE_DIR. `cache_only=True` never invokes Wine, raising
    `NotCached` instead of a cache miss (used to report on a partially-warmed cache quickly)."""
    key = _cache_key(compiler_path, program_path, siblings_dir)
    cache_path = TDF_CACHE_DIR / f"{key}.json"
    if (use_cache or cache_only) and cache_path.is_file():
        rec = json.loads(cache_path.read_text())
        exit_code = rec.get("exit", 0 if rec.get("ok") else 1)
        validity = {"exit": exit_code, "stdout": rec.get("stdout", "")}
        if oracle._valid_result(validity, program_path.name):
            return {
                "ok": exit_code == 0, "exit": exit_code, "stdout": rec["stdout"],
                "stderr": rec.get("stderr", ""),
                "tdf": base64.b64decode(rec["tdf"]) if rec["tdf"] else None,
                "lst": base64.b64decode(rec["lst"]) if rec["lst"] else None,
            }
    if cache_only:
        raise NotCached(str(program_path))

    with tempfile.TemporaryDirectory(prefix="tdftest-") as tmp:
        tmp_dir = Path(tmp)
        for entry in _iter_sibling_files(siblings_dir):
            shutil.copy2(entry, tmp_dir / entry.name)
        dest = tmp_dir / program_path.name
        shutil.copy2(program_path, dest)
        argv_prefix, env = _runner_command(compiler_path)
        try:
            proc = subprocess.run(argv_prefix + ["-z", "out.tdf", "-l", "out.lst", dest.name],
                                  cwd=tmp_dir, env=env, capture_output=True, text=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise oracle.OracleError(f"reference compiler runner failed: {exc}") from exc
        stdout = oracle._decode_and_normalise(proc.stdout)
        stderr = oracle._decode_and_normalise(proc.stderr)
        tdf_path, lst_path = tmp_dir / "out.tdf", tmp_dir / "out.lst"
        tdf_bytes = tdf_path.read_bytes() if tdf_path.is_file() and tdf_path.stat().st_size else None
        lst_bytes = lst_path.read_bytes() if lst_path.is_file() and lst_path.stat().st_size else None
        ok = proc.returncode == 0
        if not oracle._valid_result({"exit": proc.returncode, "stdout": stdout}, program_path.name):
            detail = stderr.strip() or stdout or "no compiler result line"
            raise oracle.OracleError(
                f"{compiler_path.name} returned no usable compiler verdict "
                f"(exit {proc.returncode}): {detail[:1000]}"
            )

    TDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "ok": ok, "exit": proc.returncode, "stdout": stdout, "stderr": stderr,
        "tdf": base64.b64encode(tdf_bytes).decode("ascii") if tdf_bytes else None,
        "lst": base64.b64encode(lst_bytes).decode("ascii") if lst_bytes else None,
    }))
    return {"ok": ok, "exit": proc.returncode, "stdout": stdout, "stderr": stderr,
            "tdf": tdf_bytes, "lst": lst_bytes}


def gate_failed(gate: str, compared: int, mismatches: int, missing_artifacts: int,
                oracle_errors: int) -> bool:
    if oracle_errors or compared == 0:
        return True
    return gate == "exact" and bool(mismatches or missing_artifacts)


def ours_tdf(program_path: Path, profile):
    program, diags = parser.parse_file(program_path)
    if diags.errors:
        return None, None, f"parse errors: {diags.errors[:3]}"
    try:
        tables, table_list, _ = build_tables(program, profile, dict(program.consts))
        return tdf.serialize(tables), table_list.encode("latin-1", "replace"), None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"CRASH {type(exc).__name__}: {exc}"


def _first_tdf_diff(a: bytes, b: bytes) -> str:
    try:
        _, ta = tdf.parse(a)
        _, tb = tdf.parse(b)
    except Exception as exc:  # noqa: BLE001
        return f"(unparseable: {exc}) byte offset {_first_byte_diff(a, b)}"
    names_a = [t.name for t in ta]
    names_b = [t.name for t in tb]
    if names_a != names_b:
        return f"table order/set: theirs={names_a} ours={names_b}"
    for t1, t2 in zip(ta, tb):
        if t1.size != t2.size:
            return f"table {t1.name}: size theirs={t1.size} ours={t2.size}"
        if (t1.time_type, t1.time_into, t1.interval) != (t2.time_type, t2.time_into, t2.interval):
            return (f"table {t1.name}: header theirs="
                    f"{(t1.time_type, t1.time_into, t1.interval)} ours="
                    f"{(t2.time_type, t2.time_into, t2.interval)}")
        fn1 = [f.name for f in t1.fields]
        fn2 = [f.name for f in t2.fields]
        if fn1 != fn2:
            return f"table {t1.name}: field names theirs={fn1} ours={fn2}"
        for f1, f2 in zip(t1.fields, t2.fields):
            for attr in ("type", "read_only", "aliases", "processing", "units", "description",
                         "begin_index", "dimension", "subdims"):
                if getattr(f1, attr) != getattr(f2, attr):
                    return (f"table {t1.name} field {f1.name}.{attr}: theirs="
                            f"{getattr(f1, attr)!r} ours={getattr(f2, attr)!r}")
    return "(bytes differ but no structural diff found)"


def _user_sizes(blob: bytes) -> dict[str, int] | None:
    """`{table name: record count}` of every table but `Status` (CR300/CR350 Status is not
    modelled, so byte identity says nothing about their data tables)."""
    try:
        return {t.name: t.size for t in tdf.parse(blob)[1] if t.name != "Status"}
    except Exception:  # noqa: BLE001
        return None


def _first_byte_diff(a: bytes, b: bytes) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def _programs(group: str, limit: int | None):
    out = []
    for name, (dirpath, pattern, siblings) in GROUPS.items():
        if group != "all" and name != group:
            continue
        if not dirpath.is_dir():
            continue
        for p in sorted(dirpath.glob(pattern)):
            out.append((name, p, siblings))
    if limit:
        out = out[:limit]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", choices=["repo", "chm", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-oracle", action="store_true", help="only use cached oracle results")
    ap.add_argument("--gate", choices=["report", "exact"], default="report")
    ap.add_argument("--logger", choices=["cr6", "cr1000x", "cr300", "cr350"], default="cr6",
                    help="logger whose compiler and profile are compared (default cr6)")
    ap.add_argument("--write-fixtures", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    profile = profiles.get(args.logger)
    compiler = REPO / "compiler" / profile.exe_name
    programs = _programs(args.group, args.limit)

    compiled_ok = 0
    tdf_match = 0
    size_match = 0
    lst_match = 0
    mismatches: list[tuple[str, str, str]] = []
    skipped = 0
    missing_artifacts = 0
    oracle_errors = 0

    not_cached = 0
    for group, path, siblings in programs:
        try:
            oracle_result = run_oracle_tdf(path, siblings, compiler, cache_only=args.no_oracle)
        except NotCached:
            not_cached += 1
            continue
        except oracle.OracleError as exc:
            oracle_errors += 1
            print(f"  [{group}] {path.name}: oracle error: {exc}", file=sys.stderr)
            continue
        if not oracle_result["ok"]:
            skipped += 1
            continue
        if oracle_result["tdf"] is None or oracle_result["lst"] is None:
            missing_artifacts += 1
            continue
        compiled_ok += 1

        our_tdf, our_lst, err = ours_tdf(path, profile)
        if err is not None:
            mismatches.append((group, path.name, f"ours failed: {err}"))
            continue

        theirs_tdf, theirs_lst = oracle_result["tdf"], oracle_result["lst"] or b""
        theirs_sizes = _user_sizes(theirs_tdf)
        if theirs_sizes is not None and theirs_sizes == _user_sizes(our_tdf):
            size_match += 1
        elif args.logger in ("cr300", "cr350"):
            mismatches.append((group, path.name, f"sizes: theirs={theirs_sizes} ours={_user_sizes(our_tdf)}"))
        if our_tdf == theirs_tdf:
            tdf_match += 1
        elif args.logger in ("cr6", "cr1000x"):    # CR300/CR350: Status/-l are unmodelled, see sizes
            diff = _first_tdf_diff(theirs_tdf, our_tdf)
            mismatches.append((group, path.name, f"TDF: {diff}"))

        if our_lst == theirs_lst:
            lst_match += 1
        elif our_tdf == theirs_tdf and args.logger in ("cr6", "cr1000x"):
            # TDF matched but list text didn't: show a line-level diff.
            a = theirs_lst.decode("latin-1").splitlines()
            b = our_lst.decode("latin-1").splitlines()
            first = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]),
                         min(len(a), len(b)))
            ta = a[first] if first < len(a) else "(missing)"
            tb = b[first] if first < len(b) else "(missing)"
            mismatches.append((group, path.name, f"LST line {first}: theirs={ta!r} ours={tb!r}"))

        if args.write_fixtures:
            FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
            fixture = FIXTURES_DIR / (f"{group}_{path.stem}.json" if args.logger == "cr6"
                                      else f"{args.logger}_{group}_{path.stem}.json")
            fixture.write_text(json.dumps({
                "program": path.name, "group": group,
                "tdf_b64": base64.b64encode(theirs_tdf).decode("ascii"),
                "lst_b64": base64.b64encode(theirs_lst).decode("ascii"),
            }))

    total = len(programs)
    print(f"programs: {total}  compared: {compiled_ok}  rejected by real exe: {skipped}"
          + (f"  not yet cached (--no-oracle): {not_cached}" if args.no_oracle else ""))
    print(f"missing expected artifacts: {missing_artifacts}  oracle errors: {oracle_errors}")
    print(f"TDF byte-identical: {tdf_match}/{compiled_ok}")
    print(f"table sizes identical (all tables but Status): {size_match}/{compiled_ok}")
    print(f"table-list byte-identical: {lst_match}/{compiled_ok}")
    if mismatches:
        print(f"\n{len(mismatches)} mismatches:")
        for group, name, diff in mismatches:
            print(f"  [{group}] {name}: {diff}")
    return 1 if gate_failed(args.gate, compiled_ok, len(mismatches), missing_artifacts,
                            oracle_errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
