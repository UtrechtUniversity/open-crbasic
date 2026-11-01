"""The jobs that run inside a spawned worker process: an analysis, and the compiler output of the
`opencrbasic.showCompilerOutput` command. Imports only the core compiler."""

import os
import pickle
import time
from dataclasses import dataclass
from pathlib import Path

CRASH_ENV = "OPENCRBASIC_LSP_TEST_CRASH"
SLEEP_ENV = "OPENCRBASIC_LSP_TEST_SLEEP"


@dataclass(frozen=True)
class JobResult:
    """Plain picklable data: no AST, no dataclasses from the facade."""

    ok: bool
    # (severity, message, line, resolved path or None, compiler file name,
    #  physical (first, last) line in that path's file or None)
    diagnostics: tuple[tuple[str, str, int, str | None, str, tuple[int, int] | None], ...]
    failures: tuple[str, ...]
    reads: tuple[tuple[str, str | None], ...]  # path -> sha256 or None (missing)
    listings: tuple[str, ...]  # directories whose listing was read
    program: bytes | None = None  # pickled parsed program (None: none or unpicklable)
    oversized: tuple[tuple[str, int], ...] = ()  # files over max_file_bytes (read as missing)
    # (channel, code, severity, message, line, resolved path or None, compiler file name, span, link)
    findings: tuple[tuple[str, str, str, str, int, str | None, str, tuple[int, int] | None, str], ...] = ()
    config_error: str | None = None  # invalid `opencrbasic.toml` (no checks ran)


def _pickle_program(program: object) -> bytes | None:
    if program is None:
        return None
    try:
        return pickle.dumps(program, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:  # RecursionError, an unpicklable node: features fall back to text
        return None


@dataclass(frozen=True)
class OutputResult:
    """`commands.CompilerOutput` as plain picklable data."""

    text: str
    ok: bool
    oversized: tuple[tuple[str, int], ...] = ()  # files over max_file_bytes (not read; no text)


def _test_hooks(entry: str) -> None:
    """Test hooks (docs/lsp-api.md "Test hooks"): only ever act on a substring of the entry path."""
    crash = os.environ.get(CRASH_ENV)
    if crash and crash in entry:
        os._exit(3)
    sleep = os.environ.get(SLEEP_ENV)
    if sleep:
        needle, _, seconds = sleep.rpartition(":")
        if needle and needle in entry:
            time.sleep(float(seconds))


def run_compiler_output(
    entry: str, logger: str, overlays: tuple[tuple[str, bytes], ...], max_file_bytes: int | None = None
) -> OutputResult:
    _test_hooks(entry)
    from .. import commands

    out = commands.compiler_output(Path(entry), logger, {Path(p): data for p, data in overlays}, max_file_bytes)
    return OutputResult(out.text, out.ok, tuple((str(p), size) for p, size in out.oversized.items()))


def run_analysis(
    entry: str,
    logger: str,
    overlays: tuple[tuple[str, bytes], ...],
    max_file_bytes: int | None = None,
    checks: tuple[tuple[str, str], ...] = (),
    notices: bool = False,
    config_root: str | None = None,
) -> JobResult:
    _test_hooks(entry)
    from .. import analysis

    result = analysis.analyze(
        analysis.AnalysisRequest(
            Path(entry),
            logger,
            {Path(p): data for p, data in overlays},
            max_file_bytes=max_file_bytes,
            checks=dict(checks),
            notices=notices,
            config_root=None if config_root is None else Path(config_root),
        )
    )
    return JobResult(
        result.ok,
        tuple(
            (
                d.severity,
                d.message,
                d.line,
                None if d.path is None else str(d.path),
                d.file,
                None if d.span is None else (int(d.span[0]), int(d.span[1])),
            )
            for d in result.diagnostics
        ),
        tuple(result.implementation_failures),
        tuple((str(p), h) for p, h in result.dependencies.reads.items()),
        tuple(str(p) for p in result.dependencies.listings),
        _pickle_program(result.program),
        tuple((str(p), size) for p, size in result.dependencies.oversized.items()),
        tuple(
            (
                f.channel,
                f.code,
                f.severity,
                f.message,
                f.line,
                None if f.path is None else str(f.path),
                f.file,
                None if f.span is None else (int(f.span[0]), int(f.span[1])),
                f.link,
            )
            for f in result.findings
        ),
        result.config_error,
    )
