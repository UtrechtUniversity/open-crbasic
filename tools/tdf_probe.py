#!/usr/bin/env python3
"""One-shot TDF/table-list probe: compile a CRBasic snippet with the real CR6Comp.exe and print
the resulting TDF dump plus compiler messages, so RE experiments are a single command.

    python3 tools/tdf_probe.py snippet.cr6
    python3 tools/tdf_probe.py - <<'EOF'
    Public X As Float
    BeginProg
      Scan(1,Sec,0,0)
      NextScan
    EndProg
    EOF
    python3 tools/tdf_probe.py snippet.cr6 --keep   # print the temp dir instead of deleting it
    python3 tools/tdf_probe.py snippet.cr6 --raw    # also print the list-file text (-l)

Wraps the snippet body as-is (no auto BeginProg/EndProg; the snippet must be a full program) in a
fresh temp directory, runs::

    CR6Comp.exe -z out.tdf -l out.lst t.cr6

under the same Wine setup ``tools/oracle.py`` uses, then prints:
  - stdout (the compiler's result/error lines)
  - the table-list (-l) text, if requested with --raw
  - the TDF (-z) dump via ``opencrbasic.tdf.dump``

Exits nonzero if the compile failed (matching CR6Comp.exe's own exit status), after still
printing whatever the compiler produced.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
OPENCRBASIC_ROOT = TOOLS_DIR.parent
REPO_ROOT = OPENCRBASIC_ROOT
DEFAULT_COMPILER = REPO_ROOT / "compiler" / "CR6Comp.exe"
SRC_DIR = OPENCRBASIC_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(TOOLS_DIR))
import oracle  # noqa: E402

DEFAULT_MACOS_WINE = (
    Path.home() / ".local" / "wine" / "Wine Devel.app" / "Contents" / "Resources" / "wine" / "bin" / "wine"
)
DEFAULT_WINEPREFIX = Path.home() / ".wine-cr6"


def _find_wine() -> str:
    override = os.environ.get("CR6_WINE")
    if override:
        return override
    if DEFAULT_MACOS_WINE.is_file():
        return str(DEFAULT_MACOS_WINE)
    found = shutil.which("wine")
    if found:
        return found
    raise SystemExit("no wine runner found: install wine, or set CR6_WINE")


def _runner_command(exe_path: Path) -> tuple[list[str], dict[str, str]]:
    return oracle._runner_command(exe_path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("snippet", help="path to a .cr6/.CR6 file, or '-' to read stdin")
    ap.add_argument("--exe", type=Path, default=DEFAULT_COMPILER)
    ap.add_argument("--name", default="t.cr6", help="program filename inside the temp dir")
    ap.add_argument("--keep", action="store_true", help="print the temp dir path instead of deleting it")
    ap.add_argument("--raw", action="store_true", help="also print the -l table-list text verbatim")
    ap.add_argument("--extra", action="append", default=[], help="extra CR6Comp.exe option(s), e.g. --extra -s")
    ap.add_argument("--sibling", action="append", default=[], metavar="PATH",
                     help="extra file to copy into the temp dir (e.g. an Include'd .crb); repeatable")
    args = ap.parse_args()

    if args.snippet == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.snippet).read_text()

    if not args.exe.is_file():
        raise SystemExit(f"compiler not found: {args.exe}")

    tmpdir = Path(tempfile.mkdtemp(prefix="tdf_probe_"))
    prog = tmpdir / args.name
    prog.write_text(text)
    for sib in args.sibling:
        sp = Path(sib)
        shutil.copy(sp, tmpdir / sp.name)

    tdf_path = tmpdir / "out.tdf"
    lst_path = tmpdir / "out.lst"
    argv_prefix, env = _runner_command(args.exe)
    cmd = argv_prefix + ["-z", "out.tdf", "-l", "out.lst", *args.extra, args.name]
    proc = subprocess.run(cmd, cwd=tmpdir, env=env, capture_output=True, text=True, timeout=30)

    print("=== stdout ===")
    print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.stderr.strip():
        print("=== stderr ===")
        print(proc.stderr)

    if args.raw and lst_path.is_file() and lst_path.stat().st_size:
        print("=== table list (-l) ===")
        print(lst_path.read_text(errors="replace"))

    if tdf_path.is_file() and tdf_path.stat().st_size:
        from opencrbasic.tdf import dump
        print("=== TDF dump (-z) ===")
        print(dump(tdf_path.read_bytes()))
    else:
        print("=== TDF dump (-z) === (no/empty TDF written)")

    if args.keep:
        print(f"=== temp dir kept: {tmpdir} ===")
    else:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
