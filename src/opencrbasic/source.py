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
- An `Include` or `IncludeSection` inside an already-included file (either kind, any depth, a file
  including itself too) is a hard error in CR6Comp on all four loggers: `Include file within an
  Include file is illegal.`, reported at that statement's line in the included file. The nested
  file is not loaded (`_nested_include`; oracle, 2026-10-12, `docs/frontend.md`).
"""

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .diagnostics import Diagnostics
from .sources import DiskSource, SourceProvider

_INCLUDE_RE = re.compile(r'^\s*Include\s*\(?\s*"([^"]*)"\s*\)?\s*$', re.IGNORECASE)
# A third (or later) argument is tolerated and ignored, matching the real compiler
# (oracle-confirmed, `docs/fuzz-findings.md` "False reject": `IncludeSection("Rail",
# "CPU:BenchLib.crb",(1+2))` compiles -- `(1+2)` is accepted and has no effect). `(?:,.*)?` is
# greedy and the trailing `\)\s*$` still anchors the match to the statement's own closing paren,
# so a parenthesised extra argument (nested parens included) does not break the match.
_INCLUDE_SECTION_RE = re.compile(r'^\s*IncludeSection\s*\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*(?:,.*)?\)\s*$', re.IGNORECASE)
# An `Include`/`IncludeSection` statement as the real compiler recognises it inside an included
# file, well formed or not (oracle, 2026-10-12, `docs/frontend.md`): the keyword followed by
# blank, `(`, `=` or the end of the line. `IncludeVar`, `Includes`, `Include"x"` are other tokens;
# a lone `IncludeSection` crashes CR6Comp (no verdict) and is left alone.
_NESTED_INCLUDE_RE = re.compile(r"^\s*(?:Include(?:Section)?(?=[\s(=])|Include\s*$)", re.IGNORECASE)
_DRIVE_RE = re.compile(r"^(CPU|USR|CRD|USB):", re.IGNORECASE)


class Piece(NamedTuple):
    """Provenance: `text[start:end]` of a `LogicalLine` is the decoded characters of physical line
    `line` (1-based, the true line in its file) from character `col` (0-based) on. Characters of
    `text` that no piece covers were inserted by the loader (the `" = "`/`" "` joins of a pull)."""

    start: int
    end: int
    file: str  # the file's display name, as `LogicalLine.file`
    line: int
    col: int
    occurrence: int  # which load of that file (`Occurrence.id`)


class Occurrence(NamedTuple):
    """One load of a file: a file included twice has two. `parent` is the occurrence whose
    `Include`/`IncludeSection` statement loaded it (None: the entry) and `site` the physical line
    span (first, last) of that statement in the parent."""

    id: int
    file: str
    path: Path | None
    parent: int | None
    site: tuple[int, int] | None


class LineSpan(NamedTuple):
    """The physical lines (first, last) of a logical line reported at some line of its file."""

    first: int
    last: int
    occurrence: int


def _clip(pieces: list[Piece], n: int) -> list[Piece]:
    out = []
    for p in pieces:
        if p.start >= n:
            break
        out.append(p if p.end <= n else p._replace(end=n))
    return out


@dataclass
class LogicalLine:
    text: str  # joined, comment-stripped source text
    line: int  # physical line to report (see module docstring)
    file: str  # originating file's name, for our own richer diagnostics
    section: str | None = None  # the Name argument of the enclosing IncludeSection(), if any
    had_trailing_comment: bool = False  # the *last* physical line contributing to `text` had a
    # `'` comment stripped off it -- so `text` stops short of that physical line's real end, rather
    # than reaching it. Lets the generic "Invalid, or out of place expression" fallback's own
    # CR300/CR350 newline quirk (`checker/structure.py`'s `_out_of_place`) tell a statement that
    # truly runs to the physical end of line from one a trailing comment cut short (oracle-
    # confirmed, `docs/loggers.md` "Generic fallback echo on CR300/CR350").
    kind: str = "code"  # code | overflow | include_begin | include_end | include_warning | include_error
    pulls: int = 0  # CR6/CR1000X initializer pull: physical lines folded into `text`
    unpulled: list[LogicalLine] | None = None  # the lines as written, for when `text` is not read
    bad_init: tuple[str, str] | None = None  # (message, variable) reported only when read
    # Provenance (positions only; nothing in the compiler reads these): see `positions.py`.
    pieces: tuple[Piece, ...] = ()  # where each character of `text` came from, in order
    phys: tuple[int, int] | None = None  # physical lines (first, last) the statement spans
    occurrence: int = 0  # `Occurrence.id` of the load this line belongs to
    stops_includes: bool = False  # an `IncludeSection` extension error (`parser`, round 66)


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


def _resolve_include_path(spec: str, directory: Path | None, source: SourceProvider | None = None) -> Path | None:
    """The file `spec` names, searched from the main program's directory. `/` and `\\` both
    separate directories; `.` and empty parts are skipped and `..` cancels the part before it, even a missing one (oracle, 2026-10-12,
    all four loggers: `CPU:sub/Lib.crb`, `CPU:sub\\Lib.crb`, `./`, `..`, `a/b\\Lib.crb` and
    `sub//Lib.crb` open the file, `docs/checker.md` "Include paths with a directory"). A spec
    starting with a separator, or naming a drive-like part, is not found. Names match
    case-insensitively."""
    name = _DRIVE_RE.sub("", spec.strip())
    if directory is None or not name or name[0] in "/\\":
        return None
    source = source or DiskSource()
    *folders, leaf = name.replace("\\", "/").split("/")
    parts: list[str] = []
    for part in folders:
        if part in ("", "."):
            continue
        if ":" in part:
            return None
        if part == "..":  # lexical: `nosub/../Lib.crb` opens `Lib.crb` (oracle, 2026-10-12)
            if parts:
                parts.pop()
            else:
                directory = directory.absolute().parent
        else:
            parts.append(part)
    for part in parts:
        directory = _child_directory(directory, part, source)
        if directory is None:
            return None
    if leaf in ("", ".", "..") or ":" in leaf:
        return None
    names = source.list_dir(directory)
    if names is None:
        return None
    for entry in names:
        if entry.lower() == leaf.lower():
            return directory / entry
    return None


def _child_directory(directory: Path, part: str, source: SourceProvider) -> Path | None:
    """`directory/part`, matched case-insensitively when the provider can list subdirectories."""
    exact = directory / part
    if source.list_dir(exact) is not None:
        return exact
    subdirs = getattr(source, "list_subdirs", None)
    for entry in (subdirs(directory) if subdirs else None) or ():
        if entry.lower() == part.lower():
            return directory / entry
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


def _pull_decl_initializer(text: str, physical: list[str], i: int, trace: list | None = None):
    """CR6/CR1000X `Public`/`Dim` initializer line pulling (oracle, 2026-10-10, `docs/checker.md`
    "Round 7 CR6/CR1000X help-corpus families"): a declaration ending in `=` pulls the next line
    into itself, and a brace list ending in `,` or `{` keeps pulling; every pulled line is
    consumed without advancing the reported line counter. Returns `(text, i, pulls, bad)`; `bad`
    is `(message, variable)` when the pulled line opens with `{` after a trailing `=` on an array.
    `trace`, when given, receives the provenance edits in order: `("cut", n)` keeps the first `n`
    characters of `text`; `("add", gap, index, col, length)` appends `gap` inserted characters and
    then `length` characters of physical line index `index` from column `col`."""
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
        if trace is not None:
            trace.append(("cut", len(head)))
        body = stripped.lstrip(" \t")
        names = _split_top_commas(head)
        first = names[-1]
        if len(names) == 1:
            first = re.sub(r"^\s*(?:Public|Dim)\b", "", first, count=1, flags=re.I)
        m = re.match(r"\s*(\w+)\s*(\(?)", first)
        unbal = body.count("{") > body.count("}") and _mask_strings(body).rstrip().endswith((",", "{"))
        if body.startswith("{") and m and (m.group(2) or unbal):
            seg = _split_top_commas(body)
            echo = seg[0] + ("\n" if len(seg) == 1 and len(stripped) == len(raw) else "")
            kind = "Invalid, or out of place expression" if seg[0].strip() == "{" else "Incomplete expression"
            return head, i + 1, 1, (f"{kind}: {echo}.", m.group(1))
        if not body.startswith("{"):
            body = _split_top_commas(body)[0]  # the rest of the pulled line is dropped, unread
        if trace is not None:
            trace.append(("add", 3, i + 1, len(stripped) - len(stripped.lstrip(" \t")), len(body)))
        return head + " = " + body, i + 1, 1, None
    while True:
        m = _mask_strings(text).rstrip()
        after = m[m.rfind("=") + 1 :] if "=" in m else ""
        if not (after.count("{") > after.count("}") and m.endswith((",", "{"))):
            break
        nxt = next_text()
        if nxt is None:
            break
        if trace is not None:
            trace.append(("cut", len(text.rstrip())))
            trace.append(("add", 1, i + pulls + 1, len(nxt[1]) - len(nxt[1].lstrip(" \t")), len(nxt[1].lstrip(" \t"))))
        text = text.rstrip() + " " + nxt[1].lstrip(" \t")
        pulls += 1
    return text, i + pulls, pulls, None


def bad_include_extension(spec: str, own_ext: str | None, cr3xx_exts: tuple[str, ...] | None) -> bool:
    """Shared by literal includes in the loader and folded include names in the parser."""
    if cr3xx_exts is not None:
        ext = spec.rpartition(".")[2].upper() if "." in spec else ""
        return (len(ext) == 3 and ext != "CRB") or (len(ext) == 5 and ext.startswith("CR3") and ext not in cr3xx_exts)
    return own_ext is not None and not spec.rpartition(".")[2].upper().startswith(("CRB", own_ext))


class _Loader:
    """Turns one file's raw text into `LogicalLine`s, expanding includes as it goes."""

    def __init__(
        self,
        directory: Path | None,
        diags: Diagnostics,
        own_ext: str | None = None,
        pull_init: bool = False,
        cr3xx_exts: tuple[str, ...] | None = None,
        source: SourceProvider | None = None,
        provenance: bool = True,
    ):
        self.provenance = provenance  # False: skip `LogicalLine.pieces` (only the language server reads them)
        self.source = source or DiskSource()
        self.includes: dict[str, Path] = {}  # include display name -> the path it resolved to
        self.directory = directory
        self.pull_init = pull_init
        self.pull_limit: tuple[int, ...] | None = None  # pulls only at positions below this (CR300/CR350)
        self.pull_log: list[tuple[tuple[int, ...], tuple[int, ...]]] = []  # (position, end position) of pulls
        self.site: dict[str, tuple[int, ...]] = {}  # included file -> position of its Include line
        self.diags = diags
        self.own_ext = own_ext  # CR6/CR1000X only: the logger's own program extension
        self.cr3xx_exts = cr3xx_exts  # CR300/CR350 only: the extensions an include may load
        self.files: list[bytes] = []  # every file read, in order (ProgSignature input)
        self.occurrences: list[Occurrence] = []  # every load of a file, in order (provenance)
        self._cur: int | None = None  # the occurrence being loaded (provenance)
        self._path: Path | None = None  # path of the file `load_file` is about to load
        self._site_phys: tuple[int, int] | None = None  # physical span of the include being expanded

    def load_file(
        self, path: Path, *, section: str | None, in_include: bool, display: str | None = None
    ) -> list[LogicalLine]:
        """`display` is an included file's name as written in the `Include`, drive included
        (`CPU:Lib.crb`); `diagnostics.Diagnostic.cr6comp` renders it per logger."""
        data = self.source.read(path)
        if data is None:
            return []
        self.files.append(data)
        saved = self._cur
        self._path = path
        try:
            return self.load_text(data.decode("latin-1"), display or path.name, section=section, in_include=in_include)
        finally:
            self._cur = saved

    def load_text(self, raw: str, filename: str, *, section: str | None, in_include: bool) -> list[LogicalLine]:
        physical = _split_physical_lines(raw)
        prov = self.provenance
        occ = len(self.occurrences)
        self.occurrences.append(
            Occurrence(occ, filename, self._path, self._cur, self._site_phys if self._cur is not None else None)
        )
        self._cur, self._path = occ, None
        out: list[LogicalLine] = []
        i = 0
        n = len(physical)
        # the empty piece after a final newline is no line: a continuation into it joins nothing
        real_n = n - 1 if n > 1 and physical[-1] == "" else n
        shift = 0
        while i < n:
            start_line = i + 1
            raw_line = physical[i]
            buffer_text = raw_line
            # The buffer includes an LF when present; see docs/frontend.md#source-line-buffer.
            if len(buffer_text) + (i < n - 1) > 510:
                out.append(
                    LogicalLine(
                        "",
                        start_line - shift,
                        filename,
                        section,
                        kind="overflow",
                        phys=(start_line, start_line),
                        occurrence=occ,
                    )
                )
                break
            text = _strip_comment(raw_line)
            had_comment = len(text) != len(raw_line)
            pieces = [Piece(0, len(text), filename, start_line, 0, occ)] if text and prov else []
            last_line = start_line
            while True:
                cont, prefix = _is_continuation(text)
                if cont and i + 1 >= real_n:
                    text = ""  # a continuation into the end of the file: the statement is not read
                    pieces = []
                    break
                if not cont or i + 1 >= n:
                    text = prefix if cont else text
                    pieces = _clip(pieces, len(text)) if prov else pieces
                    break
                buffer_text = _strip_comment(buffer_text).rstrip(" \t")[:-1]
                i += 1
                last_line = i + 1
                raw_line = physical[i]
                buffer_text += raw_line
                if len(buffer_text) + (i < n - 1) > 510:
                    out.append(
                        LogicalLine(
                            "",
                            last_line - shift,
                            filename,
                            section,
                            kind="overflow",
                            phys=(start_line, last_line),
                            occurrence=occ,
                        )
                    )
                    return out
                stripped = _strip_comment(raw_line)
                had_comment = len(stripped) != len(raw_line)
                # the text before the `_` with its blanks, then the next line verbatim, no
                # separator (oracle, 2026-10-10, `docs/checker.md` "Round 7"; after a text part
                # too: `foo bar _` / `      baz` echoes `foo bar       baz`)
                lone = (
                    not prefix.strip() and _is_continuation(stripped)[0] and not _is_continuation(stripped)[1].strip()
                )
                joined = len(text.rstrip(" \t")) - 1
                text = stripped if lone else text.rstrip(" \t")[:-1] + stripped
                pieces = [] if lone or not prov else _clip(pieces, joined)
                if stripped and prov:
                    pieces.append(Piece(0 if lone else joined, len(text), filename, last_line, 0, occ))
            if text.strip() == "":
                i += 1
                continue
            pulls, bad = 0, None
            orig = text
            orig_pieces = pieces
            pos = (*self.site.get(filename, ()), start_line - shift)
            if self.pull_init and _DECL_START_RE.match(text) and (self.pull_limit is None or pos < self.pull_limit):
                trace: list = []
                text, i, pulls, bad = _pull_decl_initializer(text, physical, i, trace)
                size = len(orig)
                for edit in trace if prov else ():
                    if edit[0] == "cut":
                        pieces, size = _clip(pieces, edit[1]), edit[1]
                    else:
                        _, gap, index, col, length = edit
                        pieces = pieces + [Piece(size + gap, size + gap + length, filename, index + 1, col, occ)]
                        size += gap + length
                if pulls:
                    self.pull_log.append((pos, (*self.site.get(filename, ()), last_line - shift)))
                if pulls:
                    had_comment = len(_strip_comment(physical[i])) != len(physical[i])
            self._site_phys = (start_line, i + 1)
            new = self._expand_or_keep(text, last_line - shift, filename, section, in_include, had_comment)
            for made in new:
                if made.phys is None:
                    made.phys, made.occurrence = (start_line, i + 1), occ
            if len(new) == 1 and new[0].kind == "code":
                new[0].pieces = tuple(pieces)
            if pulls and len(new) == 1 and new[0].kind == "code":
                # the parser decides whether this text is read (not in an inactive #If, not after
                # EndProg; oracle, 2026-10-10) and otherwise restores the lines as written
                base = last_line - shift - pulls
                new[0].pulls, new[0].bad_init = pulls, bad
                new[0].unpulled = [
                    LogicalLine(
                        orig,
                        base,
                        filename,
                        section,
                        pieces=tuple(orig_pieces),
                        phys=(start_line, last_line),
                        occurrence=occ,
                    )
                ] + [
                    LogicalLine(
                        _strip_comment(physical[last_line - 1 + k]),
                        base + k,
                        filename,
                        section,
                        pieces=(self._whole(physical[last_line - 1 + k], filename, last_line + k, occ) if prov else ()),
                        phys=(last_line + k, last_line + k),
                        occurrence=occ,
                    )
                    for k in range(1, pulls + 1)
                ]
            out.extend(new)
            shift += pulls
            i += 1
        return out

    @staticmethod
    def _whole(raw: str, filename: str, line: int, occ: int) -> tuple[Piece, ...]:
        n = len(_strip_comment(raw))
        return (Piece(0, n, filename, line, 0, occ),) if n else ()

    def _expand_or_keep(
        self,
        text: str,
        line: int,
        filename: str,
        section: str | None,
        in_include: bool,
        had_comment: bool = False,
    ) -> list[LogicalLine]:
        if in_include and _NESTED_INCLUDE_RE.match(text):
            return [self._nested_include(line, filename, section)]
        m = _INCLUDE_SECTION_RE.match(text)
        if m:
            name, spec = m.group(1), m.group(2)
            if self._bad_extension(spec):
                return [dataclasses.replace(self._extension_error(spec, line, filename, section), stops_includes=True)]
            target = None if self._unloadable(spec) else _resolve_include_path(spec, self.directory, self.source)
            if target is None:
                return [self._missing(spec, line, filename, section)]
            self.site.setdefault(spec.strip(), (*self.site.get(filename, ()), line))
            self.includes.setdefault(spec.strip(), target)
            included = self.load_file(target, section=name, in_include=True, display=spec.strip())
            return self._include_lines(included, line, filename, section)
        m = _INCLUDE_RE.match(text)
        if m:
            spec = m.group(1)
            if self._bad_extension(spec):
                return [self._extension_error(spec, line, filename, section)]
            target = None if self._unloadable(spec) else _resolve_include_path(spec, self.directory, self.source)
            if target is None:
                return [self._missing(spec, line, filename, section)]
            self.site.setdefault(spec.strip(), (*self.site.get(filename, ()), line))
            self.includes.setdefault(spec.strip(), target)
            included = self.load_file(target, section=None, in_include=True, display=spec.strip())
            return self._include_lines(included, line, filename, section)
        return [LogicalLine(text, line, filename, section, had_trailing_comment=had_comment)]

    def _bad_extension(self, spec: str) -> bool:
        """CR6/CR1000X: an include name whose text after the last `.` (none: empty) starts with
        neither `crb` nor the logger's own extension is an error whether or not the file exists, and
        an existing file contributes nothing (oracle, 2026-10-08/10)."""
        return bad_include_extension(spec, self.own_ext, self.cr3xx_exts)

    def _unloadable(self, spec: str) -> bool:
        """CR300/CR350: an include whose extension passes `_bad_extension` still opens only with
        `crb` or the logger's own extension; anything else is the missing-file warning even if the
        file exists (oracle, 2026-10-10, `docs/checker.md` "Round 9")."""
        ext = spec.rpartition(".")[2].upper() if "." in spec else ""
        return self.cr3xx_exts is not None and ext != "CRB" and ext not in self.cr3xx_exts

    @staticmethod
    def _nested_include(line: int, filename: str, section: str | None) -> LogicalLine:
        """An include inside an included file is an error whatever it names (a marker like the
        other include errors: not reported after `EndProg` or in an inactive `#If`)."""
        return LogicalLine(
            "Include file within an Include file is illegal.", line, filename, section, kind="include_error"
        )

    @staticmethod
    def _extension_error(spec: str, line: int, filename: str, section: str | None) -> LogicalLine:
        return LogicalLine(
            f"Include file {spec} must have valid program file extension.",
            line,
            filename,
            section,
            kind="include_error",
        )

    @staticmethod
    def _missing(spec: str, line: int, filename: str, section: str | None) -> LogicalLine:
        """A missing include is a marker the parser reports only if it reads that far: text after
        `EndProg` or in an inactive `#If` branch is never looked at (oracle, 2026-10-10)."""
        return LogicalLine(f"Cannot open include file  {spec}.", line, filename, section, kind="include_warning")

    @staticmethod
    def _include_lines(included: list[LogicalLine], line: int, filename: str, section: str | None) -> list[LogicalLine]:
        return [
            LogicalLine("", line, filename, section, kind="include_begin"),
            *included,
            LogicalLine("", line, filename, section, kind="include_end"),
        ]


def load_program(
    path: Path,
    own_ext: str | None = None,
    pull_init: bool = False,
    pull_limit: tuple[int, ...] | None = None,
    cr3xx_exts: tuple[str, ...] | None = None,
    source: SourceProvider | None = None,
    provenance: bool = True,
) -> tuple[list[LogicalLine], Diagnostics]:
    """Load `path` and every file it `Include`s/`IncludeSection`s, as one logical-line stream."""
    path = Path(path)
    diags = Diagnostics()
    loader = _Loader(path.parent, diags, own_ext, pull_init, cr3xx_exts, source, provenance)
    loader.pull_limit = pull_limit
    lines = loader.load_file(path, section=None, in_include=False)
    diags.pull_log = loader.pull_log
    diags.pull_site = loader.site
    diags.include_paths = loader.includes
    diags.occurrences = loader.occurrences
    return lines, diags


def load_text(
    text: str,
    name: str = "t.cr6",
    directory: Path | None = None,
    own_ext: str | None = None,
    pull_init: bool = False,
    cr3xx_exts: tuple[str, ...] | None = None,
) -> tuple[list[LogicalLine], Diagnostics]:
    """Like `load_program`, for source text that may not be on disk (`directory` resolves includes)."""
    diags = Diagnostics()
    loader = _Loader(directory, diags, own_ext, pull_init, cr3xx_exts)
    lines = loader.load_text(text, name, section=None, in_include=False)
    diags.occurrences = loader.occurrences
    return lines, diags
