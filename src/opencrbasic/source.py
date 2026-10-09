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
# A third (or later) argument is tolerated and ignored, matching the real compiler
# (oracle-confirmed, `docs/fuzz-findings.md` "False reject": `IncludeSection("Rail",
# "CPU:BenchLib.crb",(1+2))` compiles -- `(1+2)` is accepted and has no effect). `(?:,.*)?` is
# greedy and the trailing `\)\s*$` still anchors the match to the statement's own closing paren,
# so a parenthesised extra argument (nested parens included) does not break the match.
_INCLUDE_SECTION_RE = re.compile(
    r'^\s*IncludeSection\s*\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*(?:,.*)?\)\s*$', re.IGNORECASE
)
_DRIVE_RE = re.compile(r'^(CPU|USR|CRD|USB):', re.IGNORECASE)


@dataclass
class LogicalLine:
    text: str                 # joined, comment-stripped source text
    line: int                 # physical line to report (see module docstring)
    file: str                 # originating file's name, for our own richer diagnostics
    section: str | None = None  # the Name argument of the enclosing IncludeSection(), if any
    had_trailing_comment: bool = False  # the *last* physical line contributing to `text` had a
    # `'` comment stripped off it -- so `text` stops short of that physical line's real end, rather
    # than reaching it. Lets the generic "Invalid, or out of place expression" fallback's own
    # CR300/CR350 newline quirk (`checker/structure.py`'s `_out_of_place`) tell a statement that
    # truly runs to the physical end of line from one a trailing comment cut short (oracle-
    # confirmed, `docs/loggers.md` "Generic fallback echo on CR300/CR350").
    kind: str = "code"       # code | overflow | include_begin | include_end | include_warning | include_error


def _split_physical_lines(raw: str) -> list[str]:
    return raw.replace("\r\n", "\n").split("\n")


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


_DECL_START_RE = re.compile(r"^\s*(?:Public|Dim)\b", re.IGNORECASE)


def _mask_strings(text: str) -> str:
    return re.sub(r'"[^"]*"', lambda m: "s" * len(m.group()), text)


def _split_top_commas(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for k, ch in enumerate(_mask_strings(text)):
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:k])
            start = k + 1
    parts.append(text[start:])
    return parts


def _pull_decl_initializer(text: str, physical: list[str], i: int):
    """CR6/CR1000X `Public`/`Dim` initializer line pulling (oracle, 2026-10-10, `docs/checker.md`
    "Round 7 CR6/CR1000X help-corpus families"): a declaration ending in `=` pulls the next line
    into itself, and a brace list ending in `,` or `{` keeps pulling; every pulled line is
    consumed without advancing the reported line counter. Returns `(text, i, pulls, bad)`; `bad`
    is `(message, variable)` when the pulled line opens with `{` after a trailing `=` on an array."""
    pulls = 0
    n = len(physical)

    def next_text() -> tuple[str, str] | None:
        if i + pulls + 1 >= n:
            return None
        raw = physical[i + pulls + 1]
        stripped = _strip_comment(raw)
        return None if stripped.strip() == "" else (raw, stripped)

    masked = _mask_strings(text).rstrip()
    if masked.endswith("=") and not masked.endswith(("<=", ">=")):
        nxt = next_text()
        if nxt is None:
            return text, i, 0, None
        raw, stripped = nxt
        head = text.rstrip()[:-1].rstrip()
        body = stripped.lstrip(" \t")
        names = _split_top_commas(head)
        first = names[-1]
        if len(names) == 1:
            first = re.sub(r"^\s*(?:Public|Dim)\b", "", first, count=1, flags=re.I)
        m = re.match(r"\s*(\w+)\s*(\(?)", first)
        if body.startswith("{") and m and m.group(2):
            seg = _split_top_commas(body)
            echo = seg[0] + ("\n" if len(seg) == 1 and len(stripped) == len(raw) else "")
            kind = ("Invalid, or out of place expression" if seg[0].strip() == "{"
                    else "Incomplete expression")
            return head, i + 1, 1, (f"{kind}: {echo}.", m.group(1))
        return head + " = " + body, i + 1, 1, None
    while True:
        m = _mask_strings(text).rstrip()
        after = m[m.rfind("=") + 1:] if "=" in m else ""
        if not (after.count("{") > after.count("}") and m.endswith((",", "{"))):
            break
        nxt = next_text()
        if nxt is None:
            break
        text = text.rstrip() + " " + nxt[1].lstrip(" \t")
        pulls += 1
    return text, i + pulls, pulls, None


class _Loader:
    """Turns one file's raw text into `LogicalLine`s, expanding includes as it goes."""

    def __init__(self, directory: Path | None, diags: Diagnostics, own_ext: str | None = None,
                 pull_init: bool = False):
        self.directory = directory
        self.pull_init = pull_init
        self.diags = diags
        self.own_ext = own_ext  # CR6/CR1000X only: the logger's own program extension
        self.files: list[bytes] = []    # every file read, in order (ProgSignature input)

    def load_file(self, path: Path, *, section: str | None, in_include: bool,
                  display: str | None = None) -> list[LogicalLine]:
        """`display` is an included file's name as written in the `Include`, drive included
        (`CPU:Lib.crb`); `diagnostics.Diagnostic.cr6comp` renders it per logger."""
        try:
            data = path.read_bytes()
        except OSError:
            return []
        self.files.append(data)
        return self.load_text(data.decode("latin-1"), display or path.name, section=section,
                              in_include=in_include)

    def load_text(
        self, raw: str, filename: str, *, section: str | None, in_include: bool
    ) -> list[LogicalLine]:
        physical = _split_physical_lines(raw)
        out: list[LogicalLine] = []
        i = 0
        n = len(physical)
        shift = 0
        while i < n:
            start_line = i + 1
            raw_line = physical[i]
            buffer_text = raw_line
            # The buffer includes an LF when present; see docs/frontend.md#source-line-buffer.
            if len(buffer_text) + (i < n - 1) > 510:
                out.append(LogicalLine("", start_line - shift, filename, section, kind="overflow"))
                break
            text = _strip_comment(raw_line)
            had_comment = len(text) != len(raw_line)
            last_line = start_line
            while True:
                cont, prefix = _is_continuation(text)
                if not cont or i + 1 >= n:
                    text = prefix if cont else text
                    break
                buffer_text = _strip_comment(buffer_text).rstrip(" \t")[:-1]
                i += 1
                last_line = i + 1
                raw_line = physical[i]
                buffer_text += raw_line
                if len(buffer_text) + (i < n - 1) > 510:
                    out.append(LogicalLine("", last_line - shift, filename, section, kind="overflow"))
                    return out
                stripped = _strip_comment(raw_line)
                had_comment = len(stripped) != len(raw_line)
                text = prefix + " " + stripped.lstrip(" \t")
            if text.strip() == "":
                i += 1
                continue
            pulls = 0
            if self.pull_init and _DECL_START_RE.match(text):
                text, i, pulls, bad = _pull_decl_initializer(text, physical, i)
                if pulls:
                    had_comment = len(_strip_comment(physical[i])) != len(physical[i])
                if bad:
                    self.diags.error(last_line - shift, bad[0], filename)
                    self.diags.error(last_line - shift,
                                     f"Variable initialization of {bad[1]} has bad syntax.", filename)
            out.extend(
                self._expand_or_keep(text, last_line - shift, filename, section, in_include,
                                     had_comment)
            )
            shift += pulls
            i += 1
        return out

    def _expand_or_keep(
        self, text: str, line: int, filename: str, section: str | None, in_include: bool,
        had_comment: bool = False,
    ) -> list[LogicalLine]:
        m = _INCLUDE_SECTION_RE.match(text)
        if m:
            name, spec = m.group(1), m.group(2)
            if self._bad_extension(spec):
                return [self._extension_error(spec, line, filename, section)]
            target = _resolve_include_path(spec, self.directory)
            if target is None:
                return [self._missing(spec, line, filename, section)]
            included = self.load_file(target, section=name, in_include=in_include,
                                      display=spec.strip())
            return self._include_lines(included, line, filename, section)
        m = _INCLUDE_RE.match(text)
        if m:
            if in_include:
                self.diags.error(line, "Include file within an Include file is illegal.", filename)
                return []
            spec = m.group(1)
            if self._bad_extension(spec):
                return [self._extension_error(spec, line, filename, section)]
            target = _resolve_include_path(spec, self.directory)
            if target is None:
                return [self._missing(spec, line, filename, section)]
            included = self.load_file(target, section=None, in_include=True, display=spec.strip())
            return self._include_lines(included, line, filename, section)
        return [LogicalLine(text, line, filename, section, had_trailing_comment=had_comment)]

    def _bad_extension(self, spec: str) -> bool:
        """CR6/CR1000X: an include name whose text after the last `.` (none: empty) starts with
        neither `crb` nor the logger's own extension is an error whether or not the file exists, and
        an existing file contributes nothing (oracle, 2026-10-08/10)."""
        return self.own_ext is not None and not spec.rpartition(".")[2].upper().startswith(
            ("CRB", self.own_ext))

    @staticmethod
    def _extension_error(spec: str, line: int, filename: str, section: str | None) -> LogicalLine:
        return LogicalLine(f"Include file {spec} must have valid program file extension.", line,
                           filename, section, kind="include_error")

    @staticmethod
    def _missing(spec: str, line: int, filename: str, section: str | None) -> LogicalLine:
        """A missing include is a marker the parser reports only if it reads that far: text after
        `EndProg` or in an inactive `#If` branch is never looked at (oracle, 2026-10-10)."""
        return LogicalLine(f"Cannot open include file  {spec}.", line, filename, section,
                           kind="include_warning")

    @staticmethod
    def _include_lines(included: list[LogicalLine], line: int, filename: str,
                       section: str | None) -> list[LogicalLine]:
        return [LogicalLine("", line, filename, section, kind="include_begin"), *included,
                LogicalLine("", line, filename, section, kind="include_end")]


def load_program(path: Path, own_ext: str | None = None,
                 pull_init: bool = False) -> tuple[list[LogicalLine], Diagnostics]:
    """Load `path` and every file it `Include`s/`IncludeSection`s, as one logical-line stream."""
    path = Path(path)
    diags = Diagnostics()
    loader = _Loader(path.parent, diags, own_ext, pull_init)
    lines = loader.load_file(path, section=None, in_include=False)
    return lines, diags


def load_text(text: str, name: str = "t.cr6", directory: Path | None = None,
              own_ext: str | None = None, pull_init: bool = False) -> tuple[list[LogicalLine], Diagnostics]:
    """Like `load_program`, for source text that may not be on disk (`directory` resolves includes)."""
    diags = Diagnostics()
    loader = _Loader(directory, diags, own_ext, pull_init)
    lines = loader.load_text(text, name, section=None, in_include=False)
    return lines, diags
