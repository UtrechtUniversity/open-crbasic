"""Source positions for editor tooling: compiler-reported lines and logical columns -> physical
file ranges -> LSP positions. Read-only over the provenance the loader records (`source.Piece`,
`source.LineSpan`, `source.Occurrence`); it changes no compiler output. See `docs/lsp-api.md`
"Source positions".

Three levels:

- `reported_spans` / `line_span`: a compiler-reported (file, line) -> the physical line span(s) of
  the logical line(s) reported there (`Program.line_map`).
- `logical_ranges`: a column range of a `LogicalLine.text` -> physical (line, column) ranges,
  through continuation joins and initializer pulls.
- `lsp_position` / `lsp_range`: a physical column counted in decoded (Latin-1) characters, i.e. a
  byte offset in the line, -> an LSP `Position` for the editor's file encoding and the negotiated
  position encoding.
"""

from __future__ import annotations

import codecs
from typing import Mapping, NamedTuple, Sequence

from .source import LineSpan, LogicalLine, Occurrence, Piece, _split_physical_lines

__all__ = ["LineSpan", "Occurrence", "Piece", "PhysicalRange", "reported_spans", "line_span",
           "logical_ranges", "exact_range", "physical_lines", "lsp_position", "lsp_range",
           "POSITION_ENCODINGS"]

POSITION_ENCODINGS = ("utf-16", "utf-8", "utf-32")

LineMap = Mapping[str, Mapping[int, Sequence[LineSpan]]]


class PhysicalRange(NamedTuple):
    """`columns [start_col, end_col)` of physical line `line` (1-based) of `file`'s `occurrence`.
    Columns are 0-based decoded (Latin-1) characters, i.e. byte offsets into the line."""
    file: str
    occurrence: int
    line: int
    start_col: int
    end_col: int


def reported_spans(line_map: LineMap | None, file: str, line: int) -> list[LineSpan]:
    """Every physical span whose logical line the compiler reports at `line` of `file` (a display
    name; the empty name is not resolved here). Several logical lines can share a reported line
    (after initializer pulls) and a file loaded twice contributes one span per occurrence."""
    if not line_map:
        return []
    by_line = line_map.get(file)
    if by_line is None:
        lowered = file.lower()
        by_line = next((m for name, m in line_map.items() if name.lower() == lowered), None)
    return list(by_line.get(line, ())) if by_line else []


def line_span(line_map: LineMap | None, file: str, line: int) -> tuple[int, int] | None:
    """The covering physical (first, last) line of `reported_spans`, or None when unknown."""
    spans = reported_spans(line_map, file, line)
    if not spans:
        return None
    return min(s.first for s in spans), max(s.last for s in spans)


def logical_ranges(ll: LogicalLine, start: int, end: int) -> list[PhysicalRange]:
    """Physical ranges for `ll.text[start:end]`, in order. A range that crosses a continuation
    join, or a loader-inserted `" = "`/`" "`, comes back as several ranges (inserted characters
    map to nothing); an empty `start == end` gives one empty range, or none when that offset is
    inserted text. Characters with no piece are skipped."""
    out: list[PhysicalRange] = []
    for p in ll.pieces:
        lo, hi = max(start, p.start), min(end, p.end)
        if start == end:
            if p.start <= start <= p.end:
                return [PhysicalRange(p.file, p.occurrence, p.line, p.col + start - p.start,
                                      p.col + start - p.start)]
            continue
        if lo >= hi:
            continue
        piece = PhysicalRange(p.file, p.occurrence, p.line, p.col + lo - p.start,
                              p.col + hi - p.start)
        if out and out[-1][:3] == piece[:3] and out[-1].end_col == piece.start_col:
            piece = piece._replace(start_col=out.pop().start_col)
        out.append(piece)
    return out


def exact_range(ll: LogicalLine, start: int, end: int) -> PhysicalRange | None:
    """`logical_ranges` when the whole of `ll.text[start:end]` is one contiguous physical slice
    (a token from a single piece), else None."""
    ranges = logical_ranges(ll, start, end)
    if len(ranges) != 1:
        return None
    r = ranges[0]
    return r if start == end or r.end_col - r.start_col == end - start else None


def physical_lines(data: bytes) -> list[bytes]:
    """`data` split into physical lines exactly as the loader does (CRLF and LF; a lone CR stays
    inside its line, which an editor would split -- see the limits in `docs/lsp-api.md`)."""
    return data.replace(b"\r\n", b"\n").split(b"\n")


def lsp_position(data: bytes | Sequence[bytes], line: int, col: int, *,
                 encoding: str = "utf-16", editor_encoding: str = "utf-8") -> tuple[int, int]:
    """LSP `(line, character)` for decoded column `col` of physical `line` (1-based).

    `data` is the file's bytes (or `physical_lines(data)`); `col` counts the compiler's Latin-1
    characters, so it is a byte offset into that line. The prefix `line_bytes[:col]` is decoded with
    `editor_encoding` (errors replaced; an incomplete trailing sequence is dropped, so a column
    inside a multi-byte character snaps back to that character's start) and its length is counted
    in `encoding` units: UTF-16 code units, UTF-8 bytes, or UTF-32 code points. Only ASCII-
    compatible editor encodings are meaningful. A column past the line end is clamped to it."""
    if encoding not in POSITION_ENCODINGS:
        raise ValueError(f"unsupported position encoding {encoding!r}")
    lines = physical_lines(data) if isinstance(data, (bytes, bytearray)) else data
    if not 1 <= line <= len(lines):
        raise IndexError(f"line {line} outside 1..{len(lines)}")
    prefix = lines[line - 1][:max(col, 0)]
    decoder = codecs.getincrementaldecoder(editor_encoding)(errors="replace")
    text = decoder.decode(prefix, final=False)
    if encoding == "utf-16":
        return line - 1, len(text.encode("utf-16-le")) // 2
    if encoding == "utf-8":
        return line - 1, len(text.encode("utf-8", errors="replace"))
    return line - 1, len(text)


def lsp_range(data: bytes | Sequence[bytes], r: PhysicalRange, *, encoding: str = "utf-16",
              editor_encoding: str = "utf-8") -> tuple[tuple[int, int], tuple[int, int]]:
    """`((line, character), (line, character))` for a `PhysicalRange` of the same file."""
    lines = physical_lines(data) if isinstance(data, (bytes, bytearray)) else data
    return (lsp_position(lines, r.line, r.start_col, encoding=encoding, editor_encoding=editor_encoding),
            lsp_position(lines, r.line, r.end_col, encoding=encoding, editor_encoding=editor_encoding))
