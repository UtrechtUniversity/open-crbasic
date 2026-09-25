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

Requires Wine (see PROGRESS.md); `--no-oracle` skips real-compiler runs and only reports on
already-cached results (used by the pytest tests when Wine is unavailable).
"""

from __future__ import annotations

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
    import os
    import shlex

    env = dict(os.environ)
    override = os.environ.get("CR6COMP_RUNNER")
    if override:
        return shlex.split(override) + [str(exe_path)], env
    if sys.platform == "win32":
        return [str(exe_path)], env
    macos_wine = (Path.home() / ".local" / "wine" / "Wine Devel.app" / "Contents" / "Resources"
                  / "wine" / "bin" / "wine")
    wine = os.environ.get("CR6_WINE") or (str(macos_wine) if macos_wine.is_file() else None) \
        or shutil.which("wine")
    if not wine:
        raise RuntimeError("no wine runner found")
    env.setdefault("WINEPREFIX", str(Path.home() / ".wine-cr6"))
    env.setdefault("WINEDEBUG", "-all")
    return [wine, str(exe_path)], env


def run_oracle_tdf(program_path: Path, siblings_dir: Path, compiler_path: Path = DEFAULT_COMPILER,
                    use_cache: bool = True):
    """Compile with the real exe under Wine; return {"ok", "stdout", "tdf": bytes|None,
    "lst": bytes|None}, cached under TDF_CACHE_DIR."""
    key = _cache_key(compiler_path, program_path, siblings_dir)
    cache_path = TDF_CACHE_DIR / f"{key}.json"
    if use_cache and cache_path.is_file():
        rec = json.loads(cache_path.read_text())
        return {
            "ok": rec["ok"], "stdout": rec["stdout"],
            "tdf": base64.b64decode(rec["tdf"]) if rec["tdf"] else None,
            "lst": base64.b64decode(rec["lst"]) if rec["lst"] else None,
        }

    with tempfile.TemporaryDirectory(prefix="tdftest-") as tmp:
        tmp_dir = Path(tmp)
        for entry in _iter_sibling_files(siblings_dir):
            shutil.copy2(entry, tmp_dir / entry.name)
        dest = tmp_dir / program_path.name
        shutil.copy2(program_path, dest)
        argv_prefix, env = _runner_command(compiler_path)
        proc = subprocess.run(argv_prefix + ["-z", "out.tdf", "-l", "out.lst", dest.name],
                               cwd=tmp_dir, env=env, capture_output=True, text=False, timeout=30)
        stdout = proc.stdout.decode("utf-8", errors="replace")
        tdf_path, lst_path = tmp_dir / "out.tdf", tmp_dir / "out.lst"
        tdf_bytes = tdf_path.read_bytes() if tdf_path.is_file() and tdf_path.stat().st_size else None
        lst_bytes = lst_path.read_bytes() if lst_path.is_file() and lst_path.stat().st_size else None
        ok = proc.returncode == 0

    TDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "ok": ok, "stdout": stdout,
        "tdf": base64.b64encode(tdf_bytes).decode("ascii") if tdf_bytes else None,
        "lst": base64.b64encode(lst_bytes).decode("ascii") if lst_bytes else None,
    }))
    return {"ok": ok, "stdout": stdout, "tdf": tdf_bytes, "lst": lst_bytes}


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
    ap.add_argument("--write-fixtures", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    profile = profiles.get("cr6")
    programs = _programs(args.group, args.limit)

    compiled_ok = 0
    tdf_match = 0
    lst_match = 0
    mismatches: list[tuple[str, str, str]] = []
    skipped = 0

    for group, path, siblings in programs:
        try:
            oracle_result = run_oracle_tdf(path, siblings, use_cache=not args.no_oracle
                                            or True)
        except RuntimeError as exc:
            print(f"oracle unavailable: {exc}", file=sys.stderr)
            return 2
        if not oracle_result["ok"] or oracle_result["tdf"] is None:
            skipped += 1
            continue
        compiled_ok += 1

        our_tdf, our_lst, err = ours_tdf(path, profile)
        if err is not None:
            mismatches.append((group, path.name, f"ours failed: {err}"))
            continue

        theirs_tdf, theirs_lst = oracle_result["tdf"], oracle_result["lst"] or b""
        if our_tdf == theirs_tdf:
            tdf_match += 1
        else:
            diff = _first_tdf_diff(theirs_tdf, our_tdf)
            mismatches.append((group, path.name, f"TDF: {diff}"))

        if our_lst == theirs_lst:
            lst_match += 1
        elif our_tdf == theirs_tdf:
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
            fixture = FIXTURES_DIR / f"{group}_{path.stem}.json"
            fixture.write_text(json.dumps({
                "program": path.name, "group": group,
                "tdf_b64": base64.b64encode(theirs_tdf).decode("ascii"),
                "lst_b64": base64.b64encode(theirs_lst).decode("ascii"),
            }))

    total = len(programs)
    print(f"programs: {total}  compiled by real exe: {compiled_ok}  skipped (real exe rejected "
          f"or no TDF): {skipped}")
    print(f"TDF byte-identical: {tdf_match}/{compiled_ok}")
    print(f"table-list byte-identical: {lst_match}/{compiled_ok}")
    if mismatches:
        print(f"\n{len(mismatches)} mismatches:")
        for group, name, diff in mismatches:
            print(f"  [{group}] {name}: {diff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
