"""Where program text comes from: the disk, or the disk plus unsaved editor buffers.

The loader (`source.py`) asks a `SourceProvider` for file bytes and directory listings instead of
touching the filesystem itself, so the language-server facade (`analysis.py`) can analyse a
program whose files have unsaved edits through the same compile driver the CLI uses. Keys are
absolute, non-lowercased, non-symlink-resolved paths (`Path.absolute()`). `DiskSource` is exactly
the loader's historical behaviour. See `docs/lsp-api.md`.
"""

from __future__ import annotations

import hashlib
import stat
from pathlib import Path
from typing import Mapping, Protocol


class SourceProvider(Protocol):
    def read(self, path: Path) -> bytes | None:
        """The file's bytes; None when it is missing or unreadable."""

    def list_dir(self, directory: Path) -> list[str] | None:
        """Names of the files (not subdirectories) in `directory`; None when it is not a directory."""


class DiskSource:
    def read(self, path: Path) -> bytes | None:
        try:
            return Path(path).read_bytes()
        except OSError:
            return None

    def list_dir(self, directory: Path) -> list[str] | None:
        directory = Path(directory)
        if not directory.is_dir():
            return None
        return [entry.name for entry in directory.iterdir() if entry.is_file()]


class OverlaySource:
    """`base` with `overlays` (path -> bytes) on top. An overlay file also appears in its
    directory's listing, even when `base` has no such file or no such directory."""

    def __init__(self, base: SourceProvider, overlays: Mapping[Path, bytes] | None = None):
        self.base = base
        self.overlays: dict[Path, bytes] = {Path(p).absolute(): bytes(b)
                                            for p, b in (overlays or {}).items()}

    def read(self, path: Path) -> bytes | None:
        data = self.overlays.get(Path(path).absolute())
        return data if data is not None else self.base.read(path)

    def list_dir(self, directory: Path) -> list[str] | None:
        directory = Path(directory).absolute()
        names = self.base.list_dir(directory)
        extra = sorted(p.name for p in self.overlays if p.parent == directory)
        if names is None:
            return extra or None
        have = set(names)
        return names + [n for n in extra if n not in have]


class LimitedSource:
    """`inner` without the files larger than `limit` bytes: such a file reads as missing (None)
    and is recorded in `oversized` (path -> size). The size of an overlay is its length; a disk
    file is checked with `stat` before it is read, so an oversized file is never read."""

    def __init__(self, inner: OverlaySource, limit: int):
        self.inner = inner
        self.limit = limit
        self.oversized: dict[Path, int] = {}

    def read(self, path: Path) -> bytes | None:
        data = self.inner.overlays.get(Path(path).absolute())
        if data is None:
            try:
                info = Path(path).stat()
            except OSError:
                info = None
            if info is not None and stat.S_ISREG(info.st_mode) and info.st_size > self.limit:
                self.oversized[Path(path)] = info.st_size
                return None
            data = self.inner.read(path)
        if data is not None and len(data) > self.limit:       # an overlay, or a file that grew
            self.oversized[Path(path)] = len(data)
            return None
        return data

    def list_dir(self, directory: Path) -> list[str] | None:
        return self.inner.list_dir(directory)


class RecordingSource:
    """Wraps `inner` and records what was asked: `reads` maps path -> sha256 hex of the bytes (None
    for a miss), `listings` maps directory -> tuple of sorted names (None for a non-directory)."""

    def __init__(self, inner: SourceProvider):
        self.inner = inner
        self.reads: dict[Path, str | None] = {}
        self.listings: dict[Path, tuple[str, ...] | None] = {}

    def read(self, path: Path) -> bytes | None:
        data = self.inner.read(path)
        self.reads[Path(path)] = None if data is None else hashlib.sha256(data).hexdigest()
        return data

    def list_dir(self, directory: Path) -> list[str] | None:
        names = self.inner.list_dir(directory)
        self.listings[Path(directory)] = None if names is None else tuple(sorted(names))
        return names
