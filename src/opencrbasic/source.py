"""Load a CRBasic program: physical lines -> logical lines, with comments stripped, line
continuation joined, and `Include`/`IncludeSection` expanded.

Quirks verified against the real compiler (`tools/oracle.py`); see `docs/frontend.md`:

- A logical line built from `_` continuation reports CR6Comp's error line as the *last* physical
  line it spans, not the first (`LogicalLine.line` is set accordingly; there is no evidence CR6Comp
  tracks a finer position than "the whole logical line").
- Code pulled in by `Include`/`IncludeSection` is reported at *its own* line number within the
  included file, never the line of the `Include`/`IncludeSection` call, and the main program's own
  line numbers are unaffected by an inclusion (each file keeps its own line counter).
- `IncludeSection("Name", "Device:File")` splices in the *entire* file's text (like `Include`);
  `Name` only matters because the file's own `#If Section = "..."` directives compare against it
  (`LogicalLine.section`). Referencing `Section` anywhere else is an ordinary undefined-symbol
  error, confirmed with the oracle (`Invalid symbol in expression: Section = "Foo" .`).
- A missing `Include`/`IncludeSection` target is only a *warning* (`Cannot open include file  %s.`,
  the double space is the real message text) and the inclusion contributes nothing; compilation
  continues.
- `Include` nested inside an already-included file is a hard error in CR6Comp (`Include file
  within an Include file is illegal.`); we do not expand a second level of bare `Include` and
  record an error instead of recursing. `IncludeSection` is not observed to have this restriction
  and nothing in this repository's corpus nests it, so it is expanded recursively.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .diagnostics import Diagnostics

_INCLUDE_RE = re.compile(r'^\s*Include\s*\(?\s*"([^"]*)"\s*\)?\s*$', re.IGNORECASE)
_INCLUDE_SECTION_RE = re.compile(
    r'^\s*IncludeSection\s*\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)\s*$', re.IGNORECASE
)
_DRIVE_RE = re.compile(r'^(CPU|USR|CRD|USB):', re.IGNORECASE)


@dataclass
class LogicalLine:
    text: str                 # joined, comment-stripped source text
    line: int                 # physical line to report (see module docstring)
    file: str                 # originating file's name, for our own richer diagnostics
    section: str | None = None  # the Name argument of the enclosing IncludeSection(), if any


def _split_physical_lines(raw: str) -> list[str]:
    return raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _strip_comment(line: str) -> str:
    """Remove a trailing `'` comment, respecting `"..."` strings (with `""` escapes)."""
    in_string = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_string:
            if ch == '"':
                if i + 1 < n and line[i + 1] == '"':
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch == "'":
            return line[:i]
        i += 1
    return line


def _is_continuation(line: str) -> tuple[bool, str]:
    """`(True, prefix)` when `line` ends with whitespace + `_`; `prefix` has it removed."""
    t = line.rstrip(" \t")
    if t.endswith("_") and (len(t) == 1 or t[-2] in " \t"):
        return True, t[:-1].rstrip(" \t")
    return False, line


def _resolve_include_path(spec: str, directory: Path | None) -> Path | None:
    name = _DRIVE_RE.sub("", spec.strip())
    if directory is None or not name:
        return None
    if not directory.is_dir():
        return None
    for entry in directory.iterdir():
        if entry.is_file() and entry.name.lower() == name.lower():
            return entry
    return None


class _Loader:
    """Turns one file's raw text into `LogicalLine`s, expanding includes as it goes."""

    def __init__(self, directory: Path | None, diags: Diagnostics):
        self.directory = directory
        self.diags = diags
        self.files: list[bytes] = []    # every file read, in order (ProgSignature input)

    def load_file(self, path: Path, *, section: str | None, in_include: bool) -> list[LogicalLine]:
        try:
            data = path.read_bytes()
        except OSError:
            return []
        self.files.append(data)
        return self.load_text(data.decode("latin-1"), path.name, section=section, in_include=in_include)

    def load_text(
        self, raw: str, filename: str, *, section: str | None, in_include: bool
    ) -> list[LogicalLine]:
        physical = _split_physical_lines(raw)
        out: list[LogicalLine] = []
        i = 0
        n = len(physical)
        while i < n:
            start_line = i + 1
            text = _strip_comment(physical[i])
            last_line = start_line
            while True:
                cont, prefix = _is_continuation(text)
                if not cont or i + 1 >= n:
                    text = prefix if cont else text
                    break
                i += 1
                last_line = i + 1
                text = prefix + " " + _strip_comment(physical[i]).lstrip(" \t")
            if text.strip() == "":
                i += 1
                continue
            out.extend(
                self._expand_or_keep(text, last_line, filename, section, in_include)
            )
            i += 1
        return out

    def _expand_or_keep(
        self, text: str, line: int, filename: str, section: str | None, in_include: bool
    ) -> list[LogicalLine]:
        m = _INCLUDE_SECTION_RE.match(text)
        if m:
            name, spec = m.group(1), m.group(2)
            target = _resolve_include_path(spec, self.directory)
            if target is None:
                self.diags.warning(line, f"Cannot open include file  {spec}.", filename)
                return []
            return self.load_file(target, section=name, in_include=in_include)
        m = _INCLUDE_RE.match(text)
        if m:
            if in_include:
                self.diags.error(line, "Include file within an Include file is illegal.", filename)
                return []
            spec = m.group(1)
            target = _resolve_include_path(spec, self.directory)
            if target is None:
                self.diags.warning(line, f"Cannot open include file  {spec}.", filename)
                return []
            return self.load_file(target, section=None, in_include=True)
        return [LogicalLine(text, line, filename, section)]


def load_program(path: Path) -> tuple[list[LogicalLine], Diagnostics]:
    """Load `path` and every file it `Include`s/`IncludeSection`s, as one logical-line stream."""
    path = Path(path)
    diags = Diagnostics()
    loader = _Loader(path.parent, diags)
    lines = loader.load_file(path, section=None, in_include=False)
    return lines, diags


def load_text(text: str, name: str = "t.cr6", directory: Path | None = None) -> tuple[list[LogicalLine], Diagnostics]:
    """Like `load_program`, for source text that may not be on disk (`directory` resolves includes)."""
    diags = Diagnostics()
    loader = _Loader(directory, diags)
    lines = loader.load_text(text, name, section=None, in_include=False)
    return lines, diags
