"""Types for loader metadata attached after parsing, without adding runtime defaults."""

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .source import LineSpan, Occurrence


class LoadProvenance:
    """Annotations only: loaders populate these attributes when the corresponding data exists."""
    pull_log: list[tuple[tuple[int, ...], tuple[int, ...]]]
    pull_site: dict[str, tuple[int, ...]]
    include_paths: dict[str, Path]
    line_map: dict[str, dict[int, list[LineSpan]]]
    occurrences: list[Occurrence]
