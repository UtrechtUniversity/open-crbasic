"""Editor commands (LSP-09b): the compiler output `crbc` would print, and the data tables a
program defines. No second pipeline: `compiler_output` is `compiler.compile_file` over a
`SourceProvider` and `table_fields` is `tables.build_tables`, the call `tables_hook.attach` makes.
Neither writes a file. See `docs/lsp-api.md` "Commands (LSP-09b)".
"""

from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping

from . import profiles
from .compiler import compile_file
from .sources import DiskSource, LimitedSource, OverlaySource, SourceProvider


@dataclass(frozen=True)
class CompilerOutput:
    text: str                   # exactly what `crbc` prints to stdout (newline-terminated)
    ok: bool
    program_name: str           # the name in the header line (the entry's file name)
    # files over `max_file_bytes` (path -> size): when not empty, `text` is "" (no output)
    oversized: Mapping[Path, int] = field(default_factory=dict)


@dataclass(frozen=True)
class FieldInfo:
    name: str
    type: str
    units: str | None
    process: str | None         # the TDF processing text (`Average`, `Sample`, ...); None: empty
    dimension: int = 1


@dataclass(frozen=True)
class TableInfo:
    name: str
    fields: tuple[FieldInfo, ...]
    interval: str | None        # as `-l` prints it ("5 MIN"); None: no interval


def compiler_output(entry: Path, logger: str = "cr6",
                    overlays: Mapping[Path, bytes] | None = None,
                    max_file_bytes: int | None = None) -> CompilerOutput:
    """The text `crbc [--logger LOGGER] --no-message-file ENTRY` prints, with `overlays` (path ->
    encoded unsaved buffer) on top of the disk. An entry that neither exists nor is overlaid gives
    the CLI's "No Program File" text. With `max_file_bytes`, a file larger than that (the entry or
    one it includes) is not read (`sources.LimitedSource`); the result then has no text and lists
    the files in `oversized`."""
    entry = Path(entry).absolute()
    profile = profiles.get(logger)
    overlay = OverlaySource(DiskSource(), overlays)
    limited = None if max_file_bytes is None else LimitedSource(overlay, max_file_bytes)
    source: SourceProvider = overlay if limited is None else limited
    if source.read(entry) is None:
        if limited is not None and limited.oversized:
            return CompilerOutput("", False, entry.name, dict(limited.oversized))
        lines = [f"{entry} -- Compile Failed!", "", "No Program File"]
        return CompilerOutput("\n".join(lines) + "\n", False, entry.name)
    result = compile_file(entry, profile, display_name=entry.name, source=source, signature=False)
    if limited is not None and limited.oversized:
        return CompilerOutput("", False, result.program, dict(limited.oversized))
    return CompilerOutput("\n".join(result.result_lines()) + "\n", result.ok, result.program)


_TYPE_NAMES = {1: "UINT1", 2: "UINT2", 3: "UINT4", 6: "LONG", 7: "FP2", 9: "IEEE4", 11: "String",
               14: "NSEC", 17: "BOOL8", 18: "IEEE8", 28: "Boolean"}


def table_fields(result) -> list[TableInfo]:
    """The tables the compiler would define for `result` (an `AnalysisResult`), in TDF order, the
    `Status`, `DataTableInfo` and `Public` tables included. Empty when the program did not
    compile or has no parsed program (the compiler builds tables only after a clean check)."""
    program = result.program
    if program is None or not result.ok:
        return []
    from .tables import _seconds_to_display, build_tables
    profile = profiles.get(result.request.logger)
    try:
        tables, _, _ = build_tables(program, profile, dict(program.consts))
    except Exception:
        return []
    out = []
    for t in tables:
        interval = None
        shown = getattr(t, "_interval_display", None)
        if shown is not None:
            value, unit = shown
            interval = f"{int(value) if float(value).is_integer() else value} {unit}"
        elif t.interval != (0, 0):
            value, unit = _seconds_to_display(t.interval[0])
            interval = f"{value} {unit}"
        fields = tuple(FieldInfo(f.name, _TYPE_NAMES.get(f.type, f"type {f.type}"),
                                 f.units or None, f.processing or None, f.dimension)
                       for f in t.fields)
        out.append(TableInfo(t.name, fields, interval))
    return out


def render_table_fields(tables: list[TableInfo]) -> str:
    """Markdown: a heading per table, then a field table."""
    if not tables:
        return "No tables (the program does not compile).\n"
    lines = []
    for t in tables:
        lines.append(f"## {t.name}" + (f" (interval {t.interval})" if t.interval else ""))
        lines.append("")
        lines.append("| Field | Type | Units | Process |")
        lines.append("|---|---|---|---|")
        for f in t.fields:
            name = f.name + (f"({f.dimension})" if f.dimension > 1 and f.type != "String" else "")
            lines.append(f"| {name} | {f.type} | {f.units or ''} | {f.process or ''} |")
        lines.append("")
    return "\n".join(lines)
