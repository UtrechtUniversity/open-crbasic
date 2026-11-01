"""Oracle harness: run the real CR6Comp.exe (under Wine, PowerShell, or natively on Windows)
and return its normalised result, cached by content hash.

Module use:

    from oracle import run_oracle
    result = run_oracle(Path("Main.CR6"), siblings_dir=Path("crbasic"))

CLI use (from opencrbasic/):

    python3 tools/oracle.py file.CR6 [--siblings DIR] [--json]

Runner selection:
    - env CR6COMP_RUNNER: an explicit command prefix (shell-split), run as
      ``<CR6COMP_RUNNER...> <exe> <program> [<program>...]``. Overrides everything else.
    - else on win32: the exe is run directly.
    - else (Linux/macOS): ``wine`` is used. env CR6_WINE overrides the wine binary path;
      otherwise the first of ``$HOME/.local/wine/Wine Devel.app/Contents/Resources/wine/bin/wine``
      (the macOS install documented in openloggernet/README.md) or ``wine`` on PATH is used.

Results are cached in ``opencrbasic/.oracle-cache/<sha256>.json``, keyed by a hash of the compiler
exe bytes, the program name and bytes, and the sorted (name, bytes) of every sibling file, so a
corpus re-run only re-invokes the real compiler for programs that actually changed.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

OPENCRBASIC_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = OPENCRBASIC_ROOT
DEFAULT_COMPILER = REPO_ROOT / "compiler" / "CR6Comp.exe"
CACHE_DIR = OPENCRBASIC_ROOT / ".oracle-cache"

DEFAULT_MACOS_WINE = (
    Path.home() / ".local" / "wine" / "Wine Devel.app" / "Contents" / "Resources" / "wine" / "bin" / "wine"
)
DEFAULT_WINEPREFIX = Path.home() / ".wine-cr6"


class OracleError(RuntimeError):
    """No working CR6Comp.exe runner could be found."""


def _valid_result(record: dict, program_name: str) -> bool:
    """A runner failure is not a compiler verdict, including in older caches."""
    return record.get("exit") in (0, 1) and any(
        line.startswith(f"{program_name} -- ")
        and ("Compiled" in line or "Compile Failed!" in line)
        for line in record.get("stdout", "").splitlines()
    )


def _find_wine() -> str:
    override = os.environ.get("CR6_WINE")
    if override:
        if not Path(override).is_file():
            raise OracleError(f"CR6_WINE={override!r} does not exist")
        return override
    if DEFAULT_MACOS_WINE.is_file():
        return str(DEFAULT_MACOS_WINE)
    found = shutil.which("wine")
    if found:
        return found
    raise OracleError(
        "no wine runner found: install wine, or set CR6_WINE, or set CR6COMP_RUNNER"
    )


def _runner_command(exe_path: Path) -> tuple[list[str], dict[str, str]]:
    """The argv prefix (before the program name) and environment to invoke `exe_path` with."""
    env = dict(os.environ)
    override = os.environ.get("CR6COMP_RUNNER")
    if override:
        return shlex.split(override) + [str(exe_path)], env
    if sys.platform == "win32":
        return [str(exe_path)], env
    wine = _find_wine()
    env.setdefault("WINEPREFIX", str(DEFAULT_WINEPREFIX))
    env.setdefault("WINEDEBUG", "-all")
    env.setdefault("WINEDLLOVERRIDES", "winemac.drv,winex11.drv,winewayland.drv=d")
    return [wine, str(exe_path)], env


def _decode_and_normalise(data: bytes) -> str:
    """Decode raw output bytes and collapse all line endings to a single ``\\n``.

    Wine's console layer doubles the exe's own CR when stdout is captured through a pipe
    (observed: ``"...Mode.\\r\\r\\nWarning: ..."``), so runs of CR are first collapsed to one
    before the usual CRLF/CR/LF normalisation; this also matches the (mostly plain CRLF, but
    occasionally bare LF for a blank separator line) bytes written to CR6cmple.xxx.
    """
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"\r+", "\r", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def as_read_back(text: str) -> str:
    """opencrbasic's diagnostic text as the CLI writes it (Latin-1) and this module reads the
    reference's bytes back (UTF-8), so a non-ASCII echo such as `°` compares byte for byte."""
    return text.encode("latin-1", "replace").decode("utf-8", errors="replace")


def _strip_trailing_blank_lines(text: str) -> str:
    lines = text.split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _iter_sibling_files(siblings_dir: Path):
    for entry in sorted(siblings_dir.rglob("*")):
        if entry.is_file():
            yield entry


def _cache_key(exe_path: Path, program_path: Path, siblings_dir: Path | None) -> str:
    h = hashlib.sha256()
    h.update(hashlib.sha256(exe_path.read_bytes()).digest())
    h.update(program_path.name.encode("utf-8"))
    h.update(program_path.read_bytes())
    if siblings_dir is not None:
        siblings = sorted(
            (entry.relative_to(siblings_dir).as_posix(), entry.read_bytes())
            for entry in _iter_sibling_files(siblings_dir)
        )
        for name, data in siblings:
            h.update(name.encode("utf-8"))
            h.update(data)
    return h.hexdigest()


def run_oracle(
    program_path: Path | str,
    siblings_dir: Path | str | None = None,
    compiler_path: Path | str = DEFAULT_COMPILER,
    use_cache: bool = True,
) -> dict:
    """Compile `program_path` with the real CR6Comp.exe and return its normalised result.

    Returns {"file": name, "exit": int, "stdout": str, "cmple": str | None}.
    """
    program_path = Path(program_path).resolve()
    compiler_path = Path(compiler_path).resolve()
    siblings_dir = Path(siblings_dir).resolve() if siblings_dir is not None else None

    if not program_path.is_file():
        raise FileNotFoundError(program_path)
    if not compiler_path.is_file():
        raise FileNotFoundError(compiler_path)

    cache_path = None
    if use_cache:
        key = _cache_key(compiler_path, program_path, siblings_dir)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = CACHE_DIR / f"{key}.json"
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except ValueError:
                cached = None
            if cached is not None and _valid_result(cached, program_path.name):
                return cached

    with tempfile.TemporaryDirectory(prefix="oracle-") as tmp:
        tmp_dir = Path(tmp)
        if siblings_dir is not None:
            for entry in _iter_sibling_files(siblings_dir):
                dest = tmp_dir / entry.relative_to(siblings_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, dest)
        dest_program = tmp_dir / program_path.name
        shutil.copy2(program_path, dest_program)

        argv_prefix, env = _runner_command(compiler_path)
        try:
            result = subprocess.run(
                argv_prefix + [dest_program.name],
                cwd=tmp_dir,
                env=env,
                capture_output=True,
                text=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OracleError(f"reference compiler runner failed: {exc}") from exc

        stdout = _strip_trailing_blank_lines(_decode_and_normalise(result.stdout))

        cmple_path = tmp_dir / "CR6cmple.xxx"
        cmple: str | None = None
        if cmple_path.is_file():
            cmple = _decode_and_normalise(cmple_path.read_bytes())

        record = {
            "file": program_path.name,
            "exit": result.returncode,
            "stdout": stdout,
            "cmple": cmple,
            "stderr": _decode_and_normalise(result.stderr),
        }
        if not _valid_result(record, program_path.name):
            detail = record["stderr"].strip() or stdout or "no compiler result line"
            raise OracleError(
                f"{compiler_path.name} returned no usable compiler verdict "
                f"(exit {result.returncode}): {detail[:1000]}"
            )

    if cache_path is not None:
        partial = cache_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        partial.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, cache_path)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the real CR6Comp.exe oracle on one program.")
    parser.add_argument("program", help="path to the .CR6 file to compile")
    parser.add_argument("--siblings", help="directory of sibling files to copy alongside it")
    parser.add_argument("--json", action="store_true", help="print the full JSON result")
    parser.add_argument("--no-cache", action="store_true", help="bypass the result cache")
    args = parser.parse_args(argv)

    try:
        result = run_oracle(
            args.program,
            siblings_dir=args.siblings,
            use_cache=not args.no_cache,
        )
    except (OracleError, FileNotFoundError) as exc:
        print(f"oracle error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["stdout"])
    return result["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
