#!/usr/bin/env python3
"""Differential test for the `-t` `[Times]` section: real compiler vs opencrbasic.

    python3 tools/timingtest.py [--logger cr6] [--group repo|chm|all] [--show N] [paths...]

For each program the real exe is run as `<exe> -t <program> out.msg` (Wine; cached under
`.oracle-cache/times/`), and `opencrbasic -t` writes its own message file. The text between the
`[Times]` header and the result line (`<name> -- Compiled|Compile Failed!`) is compared.

  exact         identical `[Times]` text,
  mismatch      different entries (or one side has none),
  oracle_error  the real compiler gave no verdict (e.g. a crash); no verdict, never a mismatch.

Results go to stdout and to `timingtest-<logger>.json` (ignored by git).
"""

import argparse
import concurrent.futures
import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import oracle  # noqa: E402

from opencrbasic import cli, profiles  # noqa: E402

CACHE_DIR = ROOT / ".oracle-cache" / "times"
GROUPS = {
    "repo": (REPO / "tests" / "corpus" / "station", "*.CR6"),
    "chm": (ROOT / "tests" / "corpus" / "chm", "*.CR6"),
}


def extract_times(message: str, program_name: str) -> str | None:
    """The `[Times]` entries of a message file as `\\n`-joined text; `None` without a header.

    The header has no newline of its own, so with zero entries it runs straight into the result
    line (`[Times]prog.CR6 -- Compile Failed!`) and the section is empty."""
    text = message.replace("\r\n", "\n")
    if "[Times]" not in text:
        return None
    body = text.split("[Times]", 1)[1]
    end = body.find(program_name + " -- ")
    if end >= 0:
        body = body[:end]
    return "\n".join(line for line in body.split("\n") if line.strip())


def classify(ours: str | None, theirs: str | None) -> str:
    return "exact" if ours == theirs else "mismatch"


def first_difference(ours: str | None, theirs: str | None) -> str:
    a = (ours or "").split("\n")
    b = (theirs or "").split("\n")
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else "<none>"
        y = b[i] if i < len(b) else "<none>"
        if x != y:
            return f"entry {i}: ours={x!r} theirs={y!r}"
    return ""


def _siblings_key(siblings: Path | None):
    if siblings is None:
        return []
    return sorted((e.relative_to(siblings).as_posix(), e.read_bytes()) for e in siblings.rglob("*") if e.is_file())


def theirs_times(path: Path, siblings: Path | None, exe: Path) -> str | None:
    h = hashlib.sha256()
    h.update(hashlib.sha256(exe.read_bytes()).digest())
    h.update(path.name.encode())
    h.update(path.read_bytes())
    for name, data in _siblings_key(siblings):
        h.update(name.encode())
        h.update(data)
    cache = CACHE_DIR / f"{h.hexdigest()}.json"
    if cache.is_file():
        rec = json.loads(cache.read_text())
        return rec["times"]
    with tempfile.TemporaryDirectory(prefix="timingtest-") as tmp:
        tmp_dir = Path(tmp)
        if siblings is not None:
            for entry in siblings.rglob("*"):
                if entry.is_file():
                    dest = tmp_dir / entry.relative_to(siblings)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(entry, dest)
        shutil.copy2(path, tmp_dir / path.name)
        argv, env = oracle._runner_command(exe)
        try:
            proc = subprocess.run(
                argv + ["-t", path.name, "out.msg"], cwd=tmp_dir, env=env, capture_output=True, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise oracle.OracleError(f"runner failed: {exc}") from exc
        msg_file = tmp_dir / "out.msg"
        message = oracle._decode_and_normalise(msg_file.read_bytes()) if msg_file.is_file() else ""
        stdout = oracle._decode_and_normalise(proc.stdout)
        if not oracle._valid_result({"exit": proc.returncode, "stdout": stdout}, path.name):
            raise oracle.OracleError(f"exit {proc.returncode}: {(stdout or 'no verdict')[:200]}")
    times = extract_times(message, path.name)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"times": times}))
    return times


def ours_times(path: Path, logger: str) -> str | None:
    with tempfile.TemporaryDirectory(prefix="timingtest-ours-") as tmp:
        msg = Path(tmp) / "out.msg"
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["-t", "--logger", logger, str(path), str(msg)])
        return extract_times(msg.read_bytes().decode("latin-1"), path.name)


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def summarise(rows: list[dict]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for row in rows:
        counts = summary.setdefault(row["group"], {"exact": 0, "mismatch": 0, "oracle_error": 0})
        counts[row["status"]] += 1
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logger", default="cr6")
    ap.add_argument("--group", choices=["repo", "chm", "all"], default="all")
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("paths", nargs="*", type=Path)
    args = ap.parse_args()
    profile = profiles.get(args.logger)
    exe = REPO / "compiler" / profile.exe_name

    work: list[tuple[str, Path, Path | None]] = []
    if args.paths:
        work = [("cli", p.resolve(), p.resolve().parent) for p in args.paths]
    else:
        for group, (directory, pattern) in GROUPS.items():
            if args.group in ("all", group):
                siblings = directory if group == "repo" else None
                work += [(group, p, siblings) for p in sorted(directory.glob(pattern))]

    def real(item):
        try:
            return theirs_times(item[1], item[2], exe), None
        except oracle.OracleError as exc:
            return None, str(exc)

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        reals = list(pool.map(real, work))

    rows = []
    for (group, path, _), (theirs, error) in zip(work, reals):
        if error is not None:
            rows.append(
                {"group": group, "file": display_path(path), "status": "oracle_error", "ours": None, "theirs": error}
            )
            continue
        ours = ours_times(path, args.logger)
        rows.append(
            {
                "group": group,
                "file": display_path(path),
                "status": classify(ours, theirs),
                "ours": ours,
                "theirs": theirs,
            }
        )

    for group, s in summarise(rows).items():
        print(
            f"{args.logger} {group}: {sum(s.values())} programs, exact {s['exact']}, "
            f"mismatch {s['mismatch']}, oracle_error {s['oracle_error']}"
        )
    shown = 0
    for r in rows:
        if r["status"] != "exact" and shown < args.show:
            shown += 1
            print(f"--- {r['status']}: {r['file']}")
            if r["status"] == "mismatch":
                print("  " + first_difference(r["ours"], r["theirs"]))
            else:
                print("  " + str(r["theirs"]))
    (ROOT / f"timingtest-{args.logger}.json").write_text(json.dumps(rows, indent=1))
    return 1 if (not rows or any(r["status"] != "exact" for r in rows)) else 0


if __name__ == "__main__":
    sys.exit(main())
