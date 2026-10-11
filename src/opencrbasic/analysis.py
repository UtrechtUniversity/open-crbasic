"""Analysis facade for editor tooling: one call that runs the CLI's compile driver over a program
whose files may have unsaved edits, and returns structured diagnostics plus the file dependencies
the result was computed from. No second pipeline: `analyze` is `compiler.compile_file` with a
`SourceProvider`. See `docs/lsp-api.md` "Analysis facade (API_VERSION 1)".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from . import profiles
from .compiler import compile_file
from .sources import DiskSource, OverlaySource, RecordingSource

API_VERSION = 1


def _no_overlays() -> Mapping[Path, bytes]:
    return MappingProxyType({})


@dataclass(frozen=True)
class AnalysisRequest:
    entry: Path                         # the entry program (made absolute, not symlink-resolved)
    logger: str = "cr6"                 # profile name, never inferred from the extension
    overlays: Mapping[Path, bytes] = field(default_factory=_no_overlays)  # unsaved buffers, encoded
    warn_unused: bool = False
    signature: bool = False             # compute ProgSignature (reads every included file again)


@dataclass(frozen=True)
class AnalysisDiagnostic:
    severity: str                       # "error" | "warning"
    message: str                        # compiler text, unchanged
    line: int                           # compiler-reported line (0: none)
    file: str                           # compiler display name ("" or entry name: the entry)
    path: Path | None                   # resolved file; None when the file cannot be resolved
    rendered: str                       # `Diagnostic.cr6comp()` text


@dataclass(frozen=True)
class Dependencies:
    reads: Mapping[Path, str | None]                    # path -> sha256 hex of the bytes, None: missing
    listings: Mapping[Path, tuple[str, ...] | None]     # directory -> sorted file names, None: no dir


@dataclass(frozen=True)
class AnalysisResult:
    api_version: int
    request: AnalysisRequest
    ok: bool
    mode: str | None
    diagnostics: tuple[AnalysisDiagnostic, ...]   # `result_lines` order, not cut by the 4096-byte buffer
    rendered_lines: tuple[str, ...]               # == CompileResult.result_lines(show_signature=...)
    implementation_failures: tuple[str, ...]
    dependencies: Dependencies
    program: object                               # parsed AST (None when parsing failed); unstable


def analyze(request: AnalysisRequest) -> AnalysisResult:
    entry = Path(request.entry).absolute()
    recorder = RecordingSource(OverlaySource(DiskSource(), request.overlays))
    result = compile_file(entry, profiles.get(request.logger), warn_unused=request.warn_unused,
                          source=recorder, signature=request.signature)
    includes = getattr(result.program_ast, "include_paths", None) or {}
    lowered = {name.lower(): path for name, path in includes.items()}
    drive = result.profile.include_drive_prefix
    diagnostics = []
    for d in sorted(result.diagnostics.items, key=result._sort_key):
        if not d.file or d.file.lower() == result.program.lower():
            path = entry
        else:
            path = includes.get(d.file) or lowered.get(d.file.lower())
        diagnostics.append(AnalysisDiagnostic(d.severity, d.message, d.line, d.file, path,
                                              d.cr6comp(result.program, drive)))
    return AnalysisResult(
        API_VERSION, request, result.ok, result.mode, tuple(diagnostics),
        tuple(result.result_lines(show_signature=request.signature)),
        tuple(result.implementation_exceptions),
        Dependencies(dict(recorder.reads), dict(recorder.listings)), result.program_ast)
