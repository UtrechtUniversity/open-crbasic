#!/usr/bin/env python3
"""VSIX packaging check for the VS Code client (LSP-08f).

    export UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/opencrbasic PYTHONPATH=$PWD/src
    uv run --no-sync python tools/vsix_check.py [--keep]

Needs `npm ci` in ../vscode first (it uses the pinned node_modules/.bin/vsce). Then:

1. runs `npm run compile` in vscode/ and `vsce package` into a directory made with
   tempfile.mkdtemp(prefix="vsix-check-"); nothing is written to the repository;
2. lists the archive and checks the include and exclude rules: every compiled `out/*.js` (not
   tests), `syntaxes/`, `language-configuration.json`, `package.json`, the README and the runtime
   dependency closure are present; `src/`, tests, `.vscode/`, maps, dev tools, `extracted_chm`
   and any other top-level entry are absent;
3. checks that the packaged files equal the sources byte for byte, and that the packaged
   `package.json` matches the source one for name, version, engines, main, activation events,
   contributes (languages, grammars, commands, configuration) and dependencies;
4. reports the size and file count.

Exit status is 1 when any check fails. The .vsix is removed on exit unless --keep.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # opencrbasic/
VSCODE = ROOT / "vscode"
VSCE = VSCODE / "node_modules" / ".bin" / "vsce"
DROP_ENV = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
TOP_LEVEL_ALLOWED = {"package.json", "readme.md", "language-configuration.json", "out",
                     "syntaxes", "node_modules"}
OUT_EXCLUDED = re.compile(r"(^|/)test/|\.test\.js$|\.map$")
MAX_DETAIL_LINES = 40


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool]] = []

    def record(self, name: str, passed: bool, detail: str = "") -> bool:
        self.results.append((name, passed))
        print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)
        lines = detail.splitlines()
        for line in lines[:MAX_DETAIL_LINES]:
            print(f"     {line}", flush=True)
        if len(lines) > MAX_DETAIL_LINES:
            print(f"     ... {len(lines) - MAX_DETAIL_LINES} more lines", flush=True)
        return passed


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run(argv: list, *, cwd: Path, timeout: float = 900) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in argv], cwd=cwd, env=clean_env(), capture_output=True,
                          text=True, timeout=timeout)


def first_line(proc: subprocess.CompletedProcess) -> str:
    out = (proc.stdout or "").strip().splitlines()
    return out[0] if out else f"exit {proc.returncode}"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def environment_lines() -> list[str]:
    rev = run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], cwd=ROOT).stdout.strip()
    dirty = run(["git", "-C", ROOT, "status", "--porcelain", "--", "vscode", "tools"],
                cwd=ROOT).stdout.strip()
    npm = shutil.which("npm")
    node = shutil.which("node")
    return [
        f"host: {platform.node()} {platform.platform()}",
        f"node: {first_line(run([node, '--version'], cwd=VSCODE)) if node else 'missing'}",
        f"npm: {first_line(run([npm, '--version'], cwd=VSCODE)) if npm else 'missing'}",
        f"vsce: {first_line(run([VSCE, '--version'], cwd=VSCODE)) if VSCE.exists() else 'missing'}",
        f"revision: {rev or 'unknown'}; vscode/ and tools/ "
        f"{'have uncommitted changes' if dirty else 'clean'}",
        f"checkout: {ROOT}",
    ]


def source_outputs() -> dict[str, bytes]:
    """Compiled out/ files that belong in the package (tests excluded), keyed by package path."""
    out = VSCODE / "out"
    files = {}
    for p in sorted(out.rglob("*")):
        if p.is_file():
            rel = p.relative_to(out).as_posix()
            if not OUT_EXCLUDED.search(rel):
                files[f"out/{rel}"] = p.read_bytes()
    return files


def runtime_closure() -> set[str]:
    """Names of every package in the production dependency tree, as npm resolves it."""
    npm = shutil.which("npm")
    proc = run([npm, "ls", "--omit=dev", "--all", "--json"], cwd=VSCODE)
    tree = json.loads(proc.stdout or "{}")
    names: set[str] = set()

    def walk(node: dict) -> None:
        for name, child in (node.get("dependencies") or {}).items():
            names.add(name)
            walk(child)

    walk(tree)
    return names


def package_names(entries: list[str]) -> set[str]:
    """Package names installed under extension/node_modules, nested ones included."""
    names: set[str] = set()
    for entry in entries:
        for found in re.finditer(r"(?:^|/)node_modules/((?:@[^/]+/)?[^/]+)", entry):
            names.add(found.group(1))
    return names


def build(tmp: Path, report: Report) -> Path | None:
    compiled = run(["npm", "run", "compile"], cwd=VSCODE)
    if compiled.returncode != 0:
        report.record("npm run compile", False, compiled.stdout[-2000:] + compiled.stderr[-2000:])
        return None
    report.record("npm run compile", True, "tsc -p ./ exit 0")
    target = tmp / "opencrbasic-package.vsix"
    proc = run([VSCE, "package", "--allow-missing-repository", "--skip-license",
               "--out", target], cwd=VSCODE)
    text = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0 or not target.exists():
        report.record("vsce package builds a VSIX", False, f"exit {proc.returncode}\n{text[-3000:]}")
        return None
    report.record("vsce package builds a VSIX", True, f"exit 0\n{text.splitlines()[-1]}")
    return target


def check_entries(report: Report, vsix: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(vsix) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        contents = {i.filename: z.read(i) for i in infos}
    names = sorted(contents)
    report.record("archive lists its entries", bool(names),
                  f"{len(names)} files; top level: "
                  f"{sorted({n.split('/', 1)[0] for n in names})}")

    top = {n[len("extension/"):].split("/", 1)[0] for n in names if n.startswith("extension/")}
    bad_top = sorted(top - TOP_LEVEL_ALLOWED)
    report.record("extension/ holds only the allowed top-level entries", not bad_top,
                  f"unexpected: {bad_top}" if bad_top else f"allowed: {sorted(top)}")

    banned = [n for n in names if re.search(r"(^|/)(src|test|tests|\.vscode|extracted_chm)/", n)
              or re.search(r"\.(map|vsix|tsbuildinfo)$|\.test\.js$|tsconfig|\.ts$", n)
              or n.startswith("extension/node_modules/@types/")
              or n.startswith("extension/node_modules/typescript/")
              or n.startswith("extension/node_modules/@vscode/")
              or re.search(r"\.DS_Store$", n)]
    report.record("no sources, tests, dev tools, maps or help text in the package", not banned,
                  f"found: {banned[:40]}" if banned else "none found")

    expected = source_outputs()
    packaged_out = {n[len("extension/"):]: contents[n] for n in names
                    if n.startswith("extension/out/")}
    missing = sorted(set(expected) - set(packaged_out))
    extra = sorted(set(packaged_out) - set(expected))
    differ = sorted(k for k in set(expected) & set(packaged_out) if expected[k] != packaged_out[k])
    report.record("out/ holds exactly the compiled non-test files, byte-identical",
                  not (missing or extra or differ),
                  f"compiled: {len(expected)}; missing {missing}; extra {extra}; differ {differ}")

    singles = {
        "syntaxes/crbasic.tmLanguage.json": VSCODE / "syntaxes" / "crbasic.tmLanguage.json",
        "language-configuration.json": VSCODE / "language-configuration.json",
    }
    mismatched = []
    for rel, src in singles.items():
        if contents.get(f"extension/{rel}") != src.read_bytes():
            mismatched.append(rel)
    report.record("syntaxes and language configuration are the source files",
                  not mismatched, f"differ: {mismatched}" if mismatched else
                  ", ".join(f"{k} {sha(v.read_bytes())[:12]}" for k, v in singles.items()))

    readme = [n for n in names if n.lower() == "extension/readme.md"]
    readme_ok = bool(readme) and contents[readme[0]] == (VSCODE / "README.md").read_bytes()
    report.record("README is packaged unchanged", readme_ok,
                  f"{readme[0] if readme else 'missing'}")

    return contents


def check_manifest(report: Report, contents: dict[str, bytes]) -> None:
    raw = contents.get("extension/package.json")
    if raw is None:
        report.record("packaged package.json present", False, "extension/package.json missing")
        return
    packaged = json.loads(raw)
    source = json.loads((VSCODE / "package.json").read_text(encoding="utf-8"))
    keys = ["name", "version", "publisher", "engines", "main", "activationEvents", "dependencies"]
    diffs = [k for k in keys if packaged.get(k) != source.get(k)]
    contributes = packaged.get("contributes") or {}
    src_contributes = source.get("contributes") or {}
    for part in ("languages", "grammars", "commands"):
        if contributes.get(part) != src_contributes.get(part):
            diffs.append(f"contributes.{part}")
    p_conf = (contributes.get("configuration") or {}).get("properties") or {}
    s_conf = (src_contributes.get("configuration") or {}).get("properties") or {}
    if set(p_conf) != set(s_conf):
        diffs.append("contributes.configuration keys")
    elif p_conf != s_conf:
        diffs.append("contributes.configuration values")
    report.record("packaged package.json matches the source", not diffs,
                  f"{packaged.get('name')}@{packaged.get('version')}; "
                  f"{len(p_conf)} configuration keys; differs: {diffs}")

    names = [n for n in contents if n.startswith("extension/node_modules/")]
    installed = package_names(names)
    closure = runtime_closure()
    required = {"vscode-languageclient"}
    not_runtime = sorted(installed - closure)
    missing = sorted((closure - installed) | (required - installed))
    report.record("bundled node_modules are exactly the runtime dependency closure",
                  not not_runtime and not missing,
                  f"runtime closure {len(closure)} packages, bundled {len(installed)}; "
                  f"bundled but not runtime: {not_runtime}; runtime but not bundled: {missing}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--keep", action="store_true", help="keep the temporary directory")
    args = parser.parse_args(argv)
    for line in environment_lines():
        print(line, flush=True)
    if not VSCE.exists():
        print(f"{VSCE} is missing: run `npm ci` in {VSCODE} first", file=sys.stderr)
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="vsix-check-"))
    print(f"temporary directory: {tmp}", flush=True)
    report = Report()
    try:
        vsix = build(tmp, report)
        if vsix is not None:
            size = vsix.stat().st_size
            with zipfile.ZipFile(vsix) as z:
                count = sum(1 for i in z.infolist() if not i.is_dir())
            report.record("package size and file count", True,
                          f"{size} bytes ({size / 1024:.1f} KiB), {count} files")
            contents = check_entries(report, vsix)
            check_manifest(report, contents)
    except Exception as exc:
        report.record("vsix check completed", False, f"{type(exc).__name__}: {exc}")
    finally:
        if args.keep:
            print(f"kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    failed = [name for name, ok in report.results if not ok]
    print(f"SUMMARY: {len(report.results) - len(failed)} of {len(report.results)} checks passed")
    for name in failed:
        print(f"FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
