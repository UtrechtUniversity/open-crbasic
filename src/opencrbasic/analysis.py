"""Analysis facade for editor tooling: one call that runs the CLI's compile driver over a program
whose files may have unsaved edits, and returns structured diagnostics plus the file dependencies
the result was computed from. No second pipeline: `analyze` is `compiler.compile_file` with a
`SourceProvider`. See `docs/lsp-api.md` "Analysis facade (API_VERSION 1)".
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from . import checkconfig, checks, positions, profiles
from .compiler import compile_file
from .sources import DiskSource, LimitedSource, OverlaySource, RecordingSource, SourceProvider

API_VERSION = 2   # 2: findings, notices, config_error, AnalysisRequest.checks (M6)
DOCS = "https://github.com/UtrechtUniversity/firnmelt-datalogger/blob/main/opencrbasic/"


def _no_overlays() -> Mapping[Path, bytes]:
    return MappingProxyType({})


def _no_sizes() -> Mapping[Path, int]:
    return MappingProxyType({})


@dataclass(frozen=True)
class AnalysisRequest:
    entry: Path                         # the entry program (made absolute, not symlink-resolved)
    logger: str = "cr6"                 # profile name, never inferred from the extension
    overlays: Mapping[Path, bytes] = field(default_factory=_no_overlays)  # unsaved buffers, encoded
    warn_unused: bool = False
    signature: bool = False             # compute ProgSignature (reads every included file again)
    max_file_bytes: int | None = None   # a larger file reads as missing and is listed in `oversized`
    checks: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))  # LSP severities by code
    notices: bool = False               # also the coverage notices (`checks.notices`)
    config_root: Path | None = None     # `opencrbasic.toml` is not searched above this directory


@dataclass(frozen=True)
class AnalysisDiagnostic:
    severity: str                       # "error" | "warning"
    message: str                        # compiler text, unchanged
    line: int                           # compiler-reported line (0: none)
    file: str                           # compiler display name ("" or entry name: the entry)
    path: Path | None                   # resolved file; None when the file cannot be resolved
    rendered: str                       # `Diagnostic.cr6comp()` text
    span: tuple[int, int] | None = None  # physical first/last line in `path`'s file; None: unknown


@dataclass(frozen=True)
class AnalysisFinding:
    """An opencrbasic check (`channel` "check") or a coverage notice ("notice"); never a compiler
    diagnostic (`PLAN.md` "Three diagnostic channels")."""
    channel: str                        # "check" | "notice"
    code: str                           # `OCB...`, or "notice"
    severity: str                       # check: "error" | "warning" | "info"; notice: "hint"
    message: str
    line: int
    file: str                           # display name as for `AnalysisDiagnostic.file`
    path: Path | None
    link: str                           # URL of the evidence
    span: tuple[int, int] | None = None


@dataclass(frozen=True)
class Dependencies:
    reads: Mapping[Path, str | None]                    # path -> sha256 hex of the bytes, None: missing
    listings: Mapping[Path, tuple[str, ...] | None]     # directory -> sorted file names, None: no dir
    oversized: Mapping[Path, int] = field(default_factory=_no_sizes)  # path -> size, over the limit


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
    findings: tuple[AnalysisFinding, ...] = ()    # checks, then notices if requested; only when `ok`
    config_error: str | None = None               # an invalid `opencrbasic.toml` (no checks ran)


def _config(entry: Path, source, root: Path | None) -> tuple[checkconfig.Config, str | None]:
    """`opencrbasic.toml` as `checkconfig.find` finds it, but not above `root`, read through
    `source` so unsaved text is used and every probed path (found or missing) is a recorded
    dependency."""
    parents = entry.resolve().parents
    if root is not None and root.resolve() in parents:
        parents = parents[:parents.index(root.resolve()) + 1]
    for directory in parents:
        path = directory / checkconfig.FILE_NAME
        data = source.read(path)
        if data is None:
            continue
        try:
            return checkconfig.loads(data.decode("utf-8"), path, checks.DEFAULT_SEVERITY), None
        except UnicodeDecodeError as e:
            return checkconfig.Config(), f"{path}: cannot read: {e}"
        except checkconfig.ConfigError as e:
            return checkconfig.Config(), str(e)
    return checkconfig.Config(), None


def analyze(request: AnalysisRequest) -> AnalysisResult:
    entry = Path(request.entry).absolute()
    overlay = OverlaySource(DiskSource(), request.overlays)
    limited = None if request.max_file_bytes is None else LimitedSource(overlay, request.max_file_bytes)
    inner: SourceProvider = overlay if limited is None else limited
    recorder = RecordingSource(inner)
    result = compile_file(entry, profiles.get(request.logger), warn_unused=request.warn_unused,
                          source=recorder, signature=request.signature)
    includes = getattr(result.program_ast, "include_paths", None) or {}
    lowered = {name.lower(): path for name, path in includes.items()}
    drive = result.profile.include_drive_prefix
    line_map = getattr(result.program_ast, "line_map", None)

    def where(file: str, line: int) -> tuple[Path | None, tuple[int, int] | None]:
        if not file or file.lower() == result.program.lower():
            path = entry
        else:
            path = includes.get(file) or lowered.get(file.lower())
        return path, positions.line_span(line_map, file or result.program, line) if line else None

    diagnostics = []
    for d in sorted(result.diagnostics.items, key=result._sort_key):
        path, span = where(d.file, d.line)
        diagnostics.append(AnalysisDiagnostic(d.severity, d.message, d.line, d.file, path,
                                              d.cr6comp(result.program, drive), span))
    findings: list[AnalysisFinding] = []
    config_error = None
    if result.ok:   # checks are sound only on a program the reference accepts
        config, config_error = _config(entry, recorder, request.config_root)
        if config_error is None:
            checks.analyze(result, entry, config, lsp=dict(request.checks))
        for f in result.findings:
            path, span = where(f.file, f.line)
            findings.append(AnalysisFinding("check", f.code, f.severity, f.message, f.line, f.file, path,
                                            DOCS + checks.EVIDENCE[f.code], span))
        for n in checks.notices(result.program_ast, result.profile) if request.notices else ():
            path, span = where(n["file"], n["line"])
            rules = n["rules"]
            findings.append(AnalysisFinding(
                "notice", "notice", "hint",
                f"opencrbasic does not model {len(rules)} reference rule{'s' * (len(rules) != 1)} "
                f"of {n['instruction']}: " + "; ".join(rules), n["line"], n["file"], path,
                DOCS + checks.EVIDENCE["notice"], span))
    return AnalysisResult(
        API_VERSION, request, result.ok, result.mode, tuple(diagnostics),
        tuple(result.result_lines(show_signature=request.signature)),
        tuple(result.implementation_exceptions),
        Dependencies(dict(recorder.reads), dict(recorder.listings),
                     {} if limited is None else dict(limited.oversized)),
        result.program_ast, tuple(findings), config_error)
