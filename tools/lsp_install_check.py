#!/usr/bin/env python3
"""Install check for the opencrbasic package (LSP-08c).

    uv run --no-sync python tools/lsp_install_check.py [--keep]

Builds the sdist and wheel with `uv build`, then:

1. checks the wheel and sdist: every profile data file under src/opencrbasic/data is present,
   the console entry points and the `lsp` extra are declared, no tests/scratch/cache entries;
2. installs the wheel with the `lsp` extra into a fresh virtual environment; runs `crbc --help`
   and compiles a copy of ../crbasic (Main.CR6 and its includes) with `crbc`; the same compile is
   then run from the checkout (PYTHONPATH=src) and stdout, stderr, exit status and every written
   file must match;
3. starts `crbc-lsp` from that environment and performs initialize, initialized, shutdown, exit
   over stdio; the startup banner must name the installed package, not the checkout;
4. installs the wheel without the extra into a second fresh environment; `import opencrbasic.cli`
   must work and `pygls` must be absent.

Everything written (build outputs, environments, copies) goes under a directory made with
tempfile.mkdtemp(prefix="lsp-install-") and removed on exit unless --keep. The repository is not
written to. Exit status is 1 when any check fails.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # opencrbasic/
CRBASIC = ROOT.parent / "crbasic"                   # the checkout's CRBasic sources
SRC = ROOT / "src"
DATA = SRC / "opencrbasic" / "data"
DROP_ENV = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
BANNER = re.compile(r"opencrbasic-lsp: opencrbasic=(?P<path>.+) api_version=(?P<api>\d+) "
                    r"git=(?P<git>\S+)")
COMPILE_RUNS = (
    ("compile", ["-s", "-t", "-z", "out.tdf", "-l", "out.lst", "-p", "out.pre",
                 "Main.CR6", "msg1.txt"]),
    ("reload", ["-u", "-i", "out.pre", "Main.CR6", "msg2.txt"]),
)
CONSOLE_SCRIPTS = ("crbc = opencrbasic.cli:main", "crbc-lsp = opencrbasic.lsp.server:main")
LSP_EXTRA = re.compile(r"""extra\s*==\s*['"]lsp['"]""")
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


def clean_env(**extra: str) -> dict[str, str]:
    """The inherited environment without the checkout on the import path."""
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    env.update(extra)
    return env


def sh(argv: list, *, cwd: Path, env: dict[str, str], timeout: float = 900):
    return subprocess.run([str(a) for a in argv], cwd=cwd, env=env, capture_output=True,
                          timeout=timeout)


def text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def tail(data: bytes, lines: int = 25) -> str:
    return "\n".join(text(data).splitlines()[-lines:])


def snapshot(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def profile_files() -> list[Path]:
    return sorted(p for p in DATA.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def describe_environment(uv: str) -> list[str]:
    clean = clean_env()
    rev = text(sh(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], cwd=ROOT,
                  env=clean).stdout).strip()
    dirty = text(sh(["git", "-C", ROOT, "status", "--porcelain", "--", "src"], cwd=ROOT,
                    env=clean).stdout).strip()
    uv_version = text(sh([uv, "--version"], cwd=ROOT, env=clean).stdout).strip()
    return [
        f"host: {platform.node()} {platform.platform()}",
        f"python: {platform.python_version()} ({sys.executable})",
        f"uv: {uv_version}",
        f"revision: {rev or 'unknown'}; src/ tree {'has uncommitted changes' if dirty else 'clean'}",
        f"checkout: {ROOT}",
    ]


def build(uv: str, tmp: Path, report: Report) -> tuple[Path, Path] | None:
    dist = tmp / "dist"
    proc = sh([uv, "build", "--sdist", "--wheel", "--out-dir", dist, ROOT], cwd=tmp,
              env=clean_env(UV_PROJECT_ENVIRONMENT=str(tmp / "project-env")))
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if proc.returncode != 0 or len(wheels) != 1 or len(sdists) != 1:
        report.record("uv build produces one sdist and one wheel", False,
                      f"exit {proc.returncode}\n{tail(proc.stdout)}\n{tail(proc.stderr)}")
        return None
    detail = "\n".join(line for line in text(proc.stderr).splitlines() if "Successfully" in line)
    report.record("uv build produces one sdist and one wheel", True,
                  f"{wheels[0].name}, {sdists[0].name}\n{detail}")
    return wheels[0], sdists[0]


def check_wheel(report: Report, wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        meta = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        entry = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
        meta_text = text(z.read(meta)) if meta else ""
        entry_text = text(z.read(entry)) if entry else ""
    files = [n for n in names if not n.endswith("/")]
    present = set(files)
    expected = [p.relative_to(SRC).as_posix() for p in profile_files()]
    missing = [n for n in expected if n not in present]
    report.record("wheel contains every profile data file",
                  bool(expected) and not missing,
                  f"{len(expected)} profile files in src/opencrbasic/data; missing: {missing}")
    missing_scripts = [s for s in CONSOLE_SCRIPTS if s not in entry_text]
    report.record("wheel declares crbc and crbc-lsp entry points", not missing_scripts,
                  f"missing: {missing_scripts}" if missing_scripts else "\n".join(CONSOLE_SCRIPTS))
    lsp_requires = [line for line in meta_text.splitlines()
                    if line.startswith("Requires-Dist") and "pygls" in line
                    and LSP_EXTRA.search(line)]
    report.record("wheel declares pygls under the lsp extra", bool(lsp_requires),
                  "\n".join(lsp_requires) or "no such Requires-Dist line")
    stray = [n for n in files if any(part in ("tests", "scratch", "__pycache__", ".venv")
                                     for part in n.split("/"))]
    report.record("wheel holds no tests, scratch, cache or venv entries", not stray,
                  f"stray: {stray[:20]}")
    size = wheel.stat().st_size
    report.record("wheel size and file count", True,
                  f"{wheel.name}: {size} bytes ({size / 1024:.1f} KiB), {len(files)} files")


def check_sdist(report: Report, sdist: Path) -> None:
    with tarfile.open(sdist) as t:
        members = {m.name.split("/", 1)[1] for m in t.getmembers()
                   if m.isfile() and "/" in m.name}
    expected = [p.relative_to(ROOT).as_posix() for p in profile_files()]
    missing = [n for n in expected if n not in members]
    report.record("sdist contains every profile data file", bool(expected) and not missing,
                  f"{sdist.name}: {len(members)} files; missing: {missing}")


def make_venv(uv: str, tmp: Path, name: str, requirement: str, report: Report,
              label: str) -> Path | None:
    venv = tmp / name
    env = clean_env(UV_PROJECT_ENVIRONMENT=str(tmp / "project-env"))
    created = sh([uv, "venv", venv], cwd=tmp, env=env)
    python = venv / "bin" / "python"
    installed = sh([uv, "pip", "install", "--python", python, requirement], cwd=tmp, env=env)
    if created.returncode != 0 or installed.returncode != 0:
        report.record(f"fresh venv: {label}", False,
                      f"venv exit {created.returncode}, install exit {installed.returncode}\n"
                      f"{tail(created.stderr)}\n{tail(installed.stderr)}")
        return None
    version = text(sh([python, "-c", "import sys; print(sys.version.split()[0])"], cwd=tmp,
                      env=clean_env()).stdout).strip()
    packages = [line.strip()[2:] for line in text(installed.stderr).splitlines()
                if line.strip().startswith("+ ")]
    report.record(f"fresh venv: {label}", True,
                  f"python {version}; installed: {', '.join(packages)}")
    return venv


def compile_under(argv_prefix: list, base: Path, env: dict[str, str]) -> dict:
    """Copy ../crbasic to a fixed path under `base` and run the compile commands there."""
    if base.exists():
        shutil.rmtree(base)
    work = base / "crbasic"
    shutil.copytree(CRBASIC, work)
    before = snapshot(work)
    runs = []
    for label, args in COMPILE_RUNS:
        proc = sh([*argv_prefix, *args], cwd=work, env=env)
        runs.append({"run": label, "rc": proc.returncode,
                     "stdout": text(proc.stdout), "stderr": text(proc.stderr)})
    after = snapshot(work)
    written = {k: v for k, v in after.items() if before.get(k) != v}
    return {"runs": runs, "written": written}


def compare_compiles(a: dict, b: dict) -> list[str]:
    diffs = []
    for ra, rb in zip(a["runs"], b["runs"]):
        for key in ("rc", "stdout", "stderr"):
            if ra[key] != rb[key]:
                detail = ""
                if key != "rc":
                    detail = "\n".join(list(difflib.unified_diff(
                        ra[key].splitlines(), rb[key].splitlines(), "installed", "checkout",
                        lineterm=""))[:20])
                diffs.append(f"{ra['run']} {key} differs: {ra[key] if key == 'rc' else ''}"
                             f" vs {rb[key] if key == 'rc' else ''}\n{detail}")
    if a["written"] != b["written"]:
        keys = set(a["written"]) ^ set(b["written"])
        keys |= {k for k in a["written"] if k in b["written"] and a["written"][k] != b["written"][k]}
        diffs.append(f"written files differ: {sorted(keys)}")
    return diffs


def check_compile(report: Report, venv: Path, tmp: Path) -> None:
    help_env = clean_env()
    crbc = venv / "bin" / "crbc"
    installed_help = sh([crbc, "--help"], cwd=tmp, env=help_env)
    checkout_help = sh([sys.executable, "-m", "opencrbasic", "--help"], cwd=tmp,
                       env=clean_env(PYTHONPATH=str(SRC)))
    same_help = (installed_help.returncode == checkout_help.returncode == 0
                 and installed_help.stdout == checkout_help.stdout
                 and installed_help.stderr == checkout_help.stderr)
    report.record("crbc --help exits 0 and matches the checkout's", same_help,
                  f"exit {installed_help.returncode} (installed), "
                  f"{checkout_help.returncode} (checkout)\n{tail(installed_help.stdout, 5)}")

    installed = compile_under([crbc], tmp / "compile", clean_env())
    rcs = [r["rc"] for r in installed["runs"]]
    report.record("installed crbc compiles a copy of ../crbasic (Main.CR6)",
                  all(rc == 0 for rc in rcs),
                  f"exit statuses {rcs}; {len(installed['written'])} files written\n"
                  + "\n".join(f"{k}  {v[:12]}" for k, v in sorted(installed["written"].items())))
    checkout_import = sh([sys.executable, "-c",
                          "import opencrbasic, pathlib; "
                          "print(pathlib.Path(opencrbasic.__file__).resolve())"],
                         cwd=tmp, env=clean_env(PYTHONPATH=str(SRC)))
    checkout_path = text(checkout_import.stdout).strip()
    checkout = compile_under([sys.executable, "-m", "opencrbasic"], tmp / "compile",
                             clean_env(PYTHONPATH=str(SRC)))
    diffs = compare_compiles(installed, checkout)
    report.record("installed crbc output equals the checkout's (stdout, stderr, files)",
                  not diffs and checkout_path.startswith(str(SRC.resolve())),
                  f"checkout package: {checkout_path}\n" + "\n".join(diffs))


def read_frames(stream, sink: queue.Queue) -> None:
    """Parse LSP frames from stdout into `sink` as (kind, payload) pairs."""
    try:
        while True:
            length = None
            while True:
                line = stream.readline()
                if not line:
                    sink.put(("eof", None))
                    return
                line = line.strip()
                if not line:
                    break
                name, sep, value = line.decode("ascii", "replace").partition(":")
                if not sep:
                    sink.put(("error", f"non-header line on stdout: {line!r}"))
                    return
                if name.strip().lower() == "content-length":
                    length = int(value)
            if length is None:
                sink.put(("error", "frame without Content-Length"))
                return
            sink.put(("msg", json.loads(stream.read(length).decode("utf-8"))))
    except Exception as exc:  # a protocol fault is a failed check, never a hang
        sink.put(("error", f"{type(exc).__name__}: {exc}"))


def lsp_session(exe: Path, cwd: Path, env: dict[str, str], timeout: float = 60) -> dict:
    proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=cwd, env=env)
    frames: queue.Queue = queue.Queue()
    stderr_lines: list[str] = []
    notes: list[dict] = []
    faults: list[str] = []
    result: dict = {}

    def drain_stderr() -> None:
        for raw in proc.stderr:
            stderr_lines.append(text(raw).rstrip("\r\n"))

    threading.Thread(target=read_frames, args=(proc.stdout, frames), daemon=True).start()
    err_thread = threading.Thread(target=drain_stderr, daemon=True)
    err_thread.start()

    def send(msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        proc.stdin.flush()

    def await_response(rid: int) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no response to request id {rid}")
            try:
                kind, payload = frames.get(timeout=left)
            except queue.Empty:
                raise TimeoutError(f"no response to request id {rid}") from None
            if kind != "msg":
                raise RuntimeError(f"stdout {kind}: {payload or ''}")
            if "method" in payload and "id" in payload:     # server request: answer null
                send({"jsonrpc": "2.0", "id": payload["id"], "result": None})
            elif "method" in payload:
                notes.append(payload)
            elif payload.get("id") == rid:
                return payload
            else:
                faults.append(f"unexpected message: {payload}")

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"processId": None, "rootUri": None, "capabilities": {},
                         "workspaceFolders": None}})
        result["init"] = await_response(1)
        send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "shutdown"})
        result["shutdown"] = await_response(2)
        send({"jsonrpc": "2.0", "method": "exit"})
        result["rc"] = proc.wait(timeout=timeout)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        err_thread.join(timeout=5)
    while True:                                  # notifications that arrived last
        try:
            kind, payload = frames.get_nowait()
        except queue.Empty:
            break
        if kind == "msg" and "method" in payload and "id" not in payload:
            notes.append(payload)
    result.update(notes=notes, stderr=stderr_lines, faults=faults)
    return result


def check_lsp(report: Report, venv: Path, tmp: Path) -> None:
    cwd = tmp / "lsp-cwd"
    cwd.mkdir()
    res = lsp_session(venv / "bin" / "crbc-lsp", cwd, clean_env())
    problems: list[str] = []
    if "error" in res:
        problems.append(res["error"])
    if res.get("rc") not in (None, 0):
        problems.append(f"exit status {res['rc']} (expected 0)")
    if res.get("rc") is None and "error" not in res:
        problems.append("no exit status")
    problems += res["faults"]
    stderr = res["stderr"]
    first = stderr[0] if stderr else ""
    match = BANNER.fullmatch(first)
    banner_path = match["path"] if match else None
    if not match:
        problems.append(f"first stderr line is not the startup banner: {first!r}")
    else:
        where = Path(banner_path)
        if not where.resolve().is_relative_to(venv.resolve()):
            problems.append(f"banner names {where}, not a package under {venv}")
        if where.resolve().is_relative_to(ROOT.resolve()):
            problems.append(f"banner names the checkout: {where}")
    caps = (res.get("init") or {}).get("result", {}).get("capabilities")
    if not isinstance(caps, dict) or "textDocumentSync" not in caps:
        problems.append(f"initialize response has no textDocumentSync: {res.get('init')}")
    shut = res.get("shutdown") or {}
    if "error" in shut or "result" not in shut:
        problems.append(f"shutdown response: {shut}")
    logs = [n.get("params", {}).get("message", "") for n in res["notes"]
            if n.get("method") == "window/logMessage"]
    if not (banner_path and any("opencrbasic=" + banner_path in m for m in logs)):
        problems.append(f"no window/logMessage carries the banner; logMessages: {logs}")
    detail = [first, f"initialize capabilities: {sorted(caps) if isinstance(caps, dict) else caps}",
              f"logMessages after initialized: {logs}", f"stderr lines: {len(stderr)}"]
    detail += [f"problem: {p}" for p in problems]
    report.record("crbc-lsp answers initialize/shutdown/exit with the installed banner",
                  not problems, "\n".join(detail))


def check_core(report: Report, venv: Path, tmp: Path) -> None:
    python = venv / "bin" / "python"
    imported = sh([python, "-c", "import opencrbasic.cli as c, pathlib; "
                   "print(pathlib.Path(c.__file__).resolve())"], cwd=tmp, env=clean_env())
    path = text(imported.stdout).strip()
    inside = bool(path) and Path(path).is_relative_to(venv.resolve())
    report.record("import opencrbasic.cli without the lsp extra",
                  imported.returncode == 0 and inside,
                  f"exit {imported.returncode}; {path}\n{tail(imported.stderr, 10)}")
    probe = sh([python, "-c", "import pygls"], cwd=tmp, env=clean_env())
    absent = probe.returncode != 0 and b"ModuleNotFoundError" in probe.stderr
    report.record("pygls is absent from the core environment", absent,
                  f"exit {probe.returncode}; {tail(probe.stderr, 2)}")


def run_checks(uv: str, tmp: Path, report: Report) -> None:
    for line in describe_environment(uv):
        print(line, flush=True)
    print(f"temporary directory: {tmp}", flush=True)
    built = build(uv, tmp, report)
    if built is None:
        return
    wheel, sdist = built
    check_wheel(report, wheel)
    check_sdist(report, sdist)
    lsp_venv = make_venv(uv, tmp, "venv-lsp", f"opencrbasic[lsp] @ {wheel.as_uri()}", report,
                         "wheel with the lsp extra")
    if lsp_venv is not None:
        check_compile(report, lsp_venv, tmp)
        check_lsp(report, lsp_venv, tmp)
    core_venv = make_venv(uv, tmp, "venv-core", f"opencrbasic @ {wheel.as_uri()}", report,
                          "wheel without the extra")
    if core_venv is not None:
        check_core(report, core_venv, tmp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary directory (builds, environments, copies)")
    args = parser.parse_args(argv)
    uv = shutil.which("uv")
    if uv is None:
        print("uv is not on PATH", file=sys.stderr)
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="lsp-install-"))
    report = Report()
    try:
        run_checks(uv, tmp, report)
    except Exception as exc:
        report.record("install check completed", False, f"{type(exc).__name__}: {exc}")
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
