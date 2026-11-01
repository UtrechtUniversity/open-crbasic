"""Shared LSP test fixtures: a memoised analysis for unmodified repository files (LSP-08d).

`memo_analyze` returns the same immutable `AnalysisResult` for a repeated request. The cache key
is the request (entry, logger, warn_unused, signature) and a hit is honoured only while every file
and directory listing the stored result depends on still has the content hash it was computed
from; otherwise the analysis is recomputed. It refuses overlays and paths outside the repository
sources (`crbasic/`, `tests/corpus/`), so a test that writes or overlays files cannot use it.
The cache lives per process (each xdist worker has its own)."""

import hashlib
from pathlib import Path

import pytest

from opencrbasic.analysis import AnalysisRequest, AnalysisResult, analyze

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = ((ROOT / "tests" / "corpus" / "firnmelt").resolve(), (ROOT / "tests" / "corpus").resolve())


class MemoAnalyze:
    def __init__(self, allowed: tuple[Path, ...] = ALLOWED) -> None:
        self.allowed = allowed
        self._store: dict[tuple, AnalysisResult] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _fresh(result: AnalysisResult) -> bool:
        deps = result.dependencies
        for path, digest in deps.reads.items():
            try:
                data = Path(path).read_bytes()
            except OSError:
                data = None
            if (None if data is None else hashlib.sha256(data).hexdigest()) != digest:
                return False
        for directory, names in deps.listings.items():
            directory = Path(directory)
            now = tuple(sorted(e.name for e in directory.iterdir() if e.is_file())) if directory.is_dir() else None
            if now != names:
                return False
        return True

    def __call__(self, request: AnalysisRequest) -> AnalysisResult:
        entry = Path(request.entry).absolute()
        if request.overlays:
            raise ValueError("memo_analyze is for unmodified disk files: no overlays")
        if not any(entry.resolve().is_relative_to(base) for base in self.allowed):
            raise ValueError(f"memo_analyze is for repository sources only, not {entry}")
        key = (entry, request.logger, request.warn_unused, request.signature)
        stored = self._store.get(key)
        if stored is not None and self._fresh(stored):
            self.hits += 1
            return stored
        self.misses += 1
        result = analyze(request)
        self._store[key] = result
        return result


_MEMO = MemoAnalyze()


@pytest.fixture
def memo_analyze() -> MemoAnalyze:
    return _MEMO
