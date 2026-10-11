"""The analysis job that runs inside a spawned worker process. Imports only the core compiler."""

from __future__ import annotations

import os
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
    reads: tuple[tuple[str, str | None], ...]      # path -> sha256 or None (missing)
    listings: tuple[str, ...]                      # directories whose listing was read


def run_analysis(entry: str, logger: str, overlays: tuple[tuple[str, bytes], ...]) -> JobResult:
    # Test hooks (docs/lsp-api.md "Test hooks"): only ever act on a substring of the entry path.
    crash = os.environ.get(CRASH_ENV)
    if crash and crash in entry:
        os._exit(3)
    sleep = os.environ.get(SLEEP_ENV)
    if sleep:
        needle, _, seconds = sleep.rpartition(":")
        if needle and needle in entry:
            time.sleep(float(seconds))
    from .. import analysis

    result = analysis.analyze(analysis.AnalysisRequest(
        Path(entry), logger, {Path(p): data for p, data in overlays}))
    return JobResult(
        result.ok,
        tuple((d.severity, d.message, d.line, None if d.path is None else str(d.path), d.file,
               None if d.span is None else (int(d.span[0]), int(d.span[1])))
              for d in result.diagnostics),
        tuple(result.implementation_failures),
        tuple((str(p), h) for p, h in result.dependencies.reads.items()),
        tuple(str(p) for p in result.dependencies.listings))
