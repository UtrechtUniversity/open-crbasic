"""Editing-feature logic for editor tooling (outline, folding, include links, completion), kept
free of any protocol library. The language server (LSP-03b-wire) only converts what these functions
return. See `docs/lsp-api.md` "Editing features (LSP-03b-core)".

Positions. Every range here is *before LSP position-encoding conversion*: a `Span` is 0-based
physical lines and 0-based decoded (Latin-1) columns, i.e. byte offsets into the physical line,
the unit `source.Piece` and `positions.PhysicalRange` use. `end_col=None` means "the end of
`end_line`" (whole-line spans: the AST carries lines only, so symbol ranges are whole lines). The
server turns a `Span` into an LSP range with `positions.lsp_position(data, line + 1, col, ...)`
(that function takes a 1-based line).

Language knowledge is not duplicated: block words come from the instruction catalog (`catalog.py`)
and are matched the way the parser and the structure checker match them; `Include` statements are
found by the loader itself; completion candidates are the catalog's entries plus names read from
the parsed program and the table definitions `tables.build_tables` computes.
"""

from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, Sequence

from . import lexer, positions, source
from . import syntax as S
from .catalog import InstructionCatalog
from .parser import _DIRECTIVE_RE, _split_text_segments
from .sources import DiskSource, OverlaySource

__all__ = [
    "Span", "Symbol", "FoldRange", "IncludeLink", "DeclaredName", "CompletionItem", "Completion",
    "block_pairs", "document_symbols", "folding_ranges", "include_links", "declared_names",
    "completion_candidates",
]


@dataclass(frozen=True)
class Span:
    """0-based physical lines and decoded columns, before LSP position-encoding conversion.
    `end_col=None`: through the end of `end_line` (whole-line spans have `start_col == 0`)."""
    start_line: int
    start_col: int
    end_line: int
    end_col: int | None


def _lines(first: int, last: int) -> Span:
    """Whole-line `Span` for 1-based physical lines `first..last`."""
    return Span(first - 1, 0, last - 1, None)


# ------------------------------------------------------------------ block words

# Block pairs whose closer is not `End` + opener. Every word is a row of the instruction table
# (checked against the catalog for each profile in `block_pairs`, and against the parser in
# `tests/lsp/test_editing.py`). All other pairs are derived from the catalog: an instruction row
# `X` for which the row `EndX` also exists (If/EndIf, Select/EndSelect, Sub/EndSub, ...).
_IRREGULAR_PAIRS = {
    "FOR": "NEXT", "DO": "LOOP", "WHILE": "WEND", "DATATABLE": "ENDTABLE",
    "BEGINPROG": "ENDPROG", "SCAN": "NEXTSCAN", "SUBSCAN": "NEXTSUBSCAN",
    "SLOWSEQUENCE": "ENDSEQUENCE",
}


@functools.lru_cache(maxsize=None)
def block_pairs(profile: str = "cr6") -> dict[str, str]:
    """Upper-case opener word -> closer word, for the blocks of `profile`'s instruction table."""
    cat = InstructionCatalog.for_profile(profile)
    pairs = {o: c for o, c in _IRREGULAR_PAIRS.items()
             if cat.get(o, "instruction") and cat.get(c, "instruction")}
    for entry in cat:
        if entry.kind == "instruction":
            opener = entry.name.upper()
            if cat.get("END" + opener, "instruction"):
                pairs.setdefault(opener, "END" + opener)
    return pairs


# Blocks that the programs the compiler accepts (this project's `Main.CR6`, the CHM samples) end
# without writing the closer: a `SlowSequence` runs to the next `SlowSequence` or to `EndProg`. The
# compiler's own rule was not observed, so where such a block ends is an editor heuristic.
_IMPLICIT_END = frozenset({"SLOWSEQUENCE"})


class _Roles(NamedTuple):
    openers: dict[str, str]     # opener -> closer
    closers: dict[str, str]     # closer -> opener (the family name)


@functools.lru_cache(maxsize=None)
def _roles(profile: str) -> _Roles:
    pairs = block_pairs(profile)
    return _Roles(pairs, {c: o for o, c in pairs.items()})


def _classify(word: str, second: str | None, roles: _Roles) -> tuple[str, str] | None:
    """`("open" | "close", family)` for a statement starting with `word` (`second`: the identifier
    after `End` / `Next`, for the two-word closers `End If`, `Next Scan`, `End Sub`, ...)."""
    w = word.upper()
    if w in ("END", "NEXT") and second and (w + second.upper()) in roles.closers:
        return "close", roles.closers[w + second.upper()]
    if w in roles.closers:
        return "close", roles.closers[w]
    if w in roles.openers:
        return "open", w
    return None


def _ast_event(stmt: S.Stmt, roles: _Roles) -> tuple[str, str] | None:
    """The block role of one parsed statement (the parser's statement classes, see `syntax.py`)."""
    if isinstance(stmt, S.If):
        return None if stmt.inline is not None else _classify("IF", None, roles)
    if isinstance(stmt, S.For):
        return _classify("FOR", None, roles)
    if isinstance(stmt, (S.DoLoop, S.SubDef)):
        return _classify(stmt.keyword, None, roles)
    if isinstance(stmt, S.Instruction):
        second = None
        if stmt.keyword in ("END", "NEXT") and stmt.args and isinstance(stmt.args[0], S.Name):
            second = stmt.args[0].name
        return _classify(stmt.keyword, second, roles)
    return None


# ------------------------------------------------------------------ loading helpers


def _tolerant_tokens(text: str) -> list[lexer.Token]:
    """`lexer.tokenize`, cutting the text at an unlexable character instead of raising."""
    while True:
        try:
            return lexer.tokenize(text)
        except lexer.LexError as e:
            text = text[:e.col]


def _logical_lines(text: str) -> list[tuple[source.LogicalLine, int, int]]:
    """The loader's logical lines of `text` (includes unresolved), with 0-based absolute physical
    first/last lines. A line the loader calls an overflow (over 510 characters) ends its pass, so
    loading restarts after it: editing a very long line does not hide the rest of the file."""
    physical = source._split_physical_lines(text)
    out: list[tuple[source.LogicalLine, int, int]] = []
    base = 0
    while base < len(physical):
        lines, _ = source.load_text("\n".join(physical[base:]), "t", directory=None)
        restart = None
        for ll in lines:
            if ll.phys is None:
                continue
            if ll.kind == "overflow":
                restart = base + ll.phys[1]
                break
            out.append((ll, base + ll.phys[0] - 1, base + ll.phys[1] - 1))
        if restart is None or restart <= base:
            break
        base = restart
    return out


# ------------------------------------------------------------------ folding


@dataclass(frozen=True)
class FoldRange:
    """Fold physical lines `start_line..end_line` (0-based, inclusive). A block's range ends on the
    line before its closer, so the closer stays visible. `kind` is an LSP folding kind: "region"
    for blocks and `#If`, "comment" for runs of comment lines."""
    start_line: int
    end_line: int
    kind: str = "region"


class _Block(NamedTuple):
    family: str              # opener word (`IF`, `SUB`, ...) or `#IF`
    name: str | None         # the routine name for Sub / Function
    start: int               # 0-based first physical line of the opener
    header_end: int          # 0-based last physical line of the opener statement
    end: int | None          # 0-based first physical line of the closer; None: never closed


def _block_events(text: str, profile: str):
    """Yield `(role, family, name, first, last)` for every block word and `#If` directive of
    `text`, in order. Words inside strings and comments never appear (the loader strips comments,
    the lexer reads strings)."""
    roles = _roles(profile)
    colon_if = profile in ("cr6", "cr1000x")    # `If c Then : stmt` is a one-line If (parser.py)
    for ll, first, last in _logical_lines(text):
        if ll.kind != "code":
            continue
        d = _DIRECTIVE_RE.match(ll.text)
        if d:
            kind = d.group(1).upper()
            if kind in ("IF", "IF_NO_REMOVE"):
                yield "open", "#IF", None, first, last
            elif kind == "ENDIF":
                yield "close", "#IF", None, first, last
            continue
        segments = _split_text_segments(ll.text)
        for idx, seg in enumerate(segments):
            tokens = [t for t in _tolerant_tokens(seg) if t.kind != "EOF"]
            if not tokens or tokens[0].kind != "IDENT":
                continue
            word = tokens[0].upper
            second = tokens[1].upper if len(tokens) > 1 and tokens[1].kind == "IDENT" else None
            event = _classify(word, second, roles)
            if event is None:
                continue
            if event == ("open", "IF"):
                depth, then = 0, None
                for k, t in enumerate(tokens):
                    if t.kind == "OP" and t.text in "({":
                        depth += 1
                    elif t.kind == "OP" and t.text in ")}":
                        depth -= 1
                    elif depth == 0 and t.kind == "IDENT" and t.upper == "THEN":
                        then = k
                        break
                if then is not None and (then < len(tokens) - 1 or (
                        colon_if and idx < len(segments) - 1)):
                    break       # a one-line If: the rest of the line belongs to it
            name = None
            if event[0] == "open" and event[1] in ("SUB", "FUNCTION") and second is not None:
                name = tokens[1].text
            yield event[0], event[1], name, first, last


def _scan(text: str, profile: str) -> list[_Block]:
    """Every block of `text` with its closer (or None), by nearest-matching-opener recovery: a
    closer closes the innermost open block of its family and drops the blocks opened inside it
    that were never closed, a closer with no opener is ignored. `SlowSequence` also ends, without
    `EndSequence`, where the next `SlowSequence` or the enclosing `EndProg` begins (`_IMPLICIT`).
    Works on any text."""
    stack: list[_Block] = []
    done: list[_Block] = []

    def close(k: int, first: int) -> None:
        for lost in stack[k + 1:]:
            if lost.family in _IMPLICIT_END:
                done.append(lost._replace(end=first))
            else:
                done.append(lost)
        done.append(stack[k]._replace(end=first))
        del stack[k:]

    for role, family, name, first, last in _block_events(text, profile):
        if role == "open":
            if family in _IMPLICIT_END:
                for k in range(len(stack) - 1, -1, -1):
                    if stack[k].family == family:
                        done.extend(stack[k + 1:])
                        done.append(stack[k]._replace(end=first))
                        del stack[k:]
                        break
            stack.append(_Block(family, name, first, last, None))
            continue
        for k in range(len(stack) - 1, -1, -1):
            if stack[k].family == family:
                close(k, first)
                break
    done.extend(stack)
    return done


def folding_ranges(text: str, profile: str = "cr6") -> list[FoldRange]:
    """Fold ranges for the buffer `text` (decoded as the compiler decodes: Latin-1): every block
    that has an opener and a closer and at least one line between them, `#If`..`#EndIf` (inactive
    branches included: conditions are not evaluated), and runs of two or more comment-only lines.
    Unclosed blocks give no range. Sorted by start line, outer before inner."""
    out = [FoldRange(b.start, b.end - 1, "region")
           for b in _scan(text, profile) if b.end is not None and b.end - 1 > b.header_end]
    run = None
    lines = source._split_physical_lines(text)
    for n, line in enumerate(lines + [""]):
        if n < len(lines) and line.lstrip(" \t").startswith("'"):
            run = n if run is None else run
        else:
            if run is not None and n - 1 > run:
                out.append(FoldRange(run, n - 1, "comment"))
            run = None
    out.sort(key=lambda f: (f.start_line, -f.end_line))
    return out


# ------------------------------------------------------------------ include links


@dataclass(frozen=True)
class IncludeLink:
    """One `Include` / `IncludeSection` statement. `range` is the file name inside the quotes;
    `target` is the file the compiler resolved (`Program.include_paths`), None when it did not."""
    file: Path
    range: Span
    spec: str
    target: Path | None
    keyword: str


def _same_path(a: Path, b: Path) -> bool:
    na, nb = os.path.normpath(a), os.path.normpath(b)
    return na == nb or na.lower() == nb.lower()


def _program_files(result) -> list[Path]:
    seen: list[Path] = []
    program = result.program
    paths = [o.path for o in getattr(program, "occurrences", None) or () if o.path is not None]
    for p in [*paths] or [result.request.entry]:
        if not any(_same_path(p, q) for q in seen):
            seen.append(p)
    return seen


def include_links(result) -> list[IncludeLink]:
    """The `Include` / `IncludeSection` statements of every file of the analysed program, in file
    then line order. The statements are the loader's (`source.py`, so continuation lines and
    case follow the compiler); the file text is read through the request's overlays. A file
    included twice is listed once. With no parsed program only the entry file is scanned and every
    target is None."""
    includes = getattr(result.program, "include_paths", None) or {}
    reader = OverlaySource(DiskSource(), result.request.overlays)
    out: list[IncludeLink] = []
    for path in _program_files(result):
        data = reader.read(Path(path).absolute())
        if data is None:
            continue
        text = data.decode("latin-1")
        physical = source._split_physical_lines(text)
        for ll, first, last in _logical_lines(text):
            if ll.kind not in ("include_warning", "include_error"):
                continue
            strings = []
            keyword = None
            for n in range(first, last + 1):
                toks = _tolerant_tokens(source._strip_comment(physical[n]))
                if keyword is None and toks and toks[0].kind == "IDENT":
                    keyword = toks[0].text
                strings.extend((n, t) for t in toks if t.kind == "STRING")
            index = 1 if keyword and keyword.upper() == "INCLUDESECTION" else 0
            if keyword is None or index >= len(strings):
                continue
            n, tok = strings[index]
            spec = str(tok.value)
            out.append(IncludeLink(Path(path), Span(n, tok.col + 1, n, tok.col + len(tok.text) - 1),
                                   spec, includes.get(spec.strip()), keyword))
    return out


# ------------------------------------------------------------------ outline

_SCOPE_FAMILIES = {"SUB", "FUNCTION", "DATATABLE", "CONSTTABLE", "STRUCTURETYPE",
                   "BEGINPROG", "SCAN", "SUBSCAN", "SLOWSEQUENCE"}
_ROUTINES = {"SUB", "FUNCTION"}


@dataclass(frozen=True)
class Symbol:
    """One outline entry. `range` runs from the header through the closer (the header only when
    the block is never closed); `selection` is the header. Both are whole-line spans. `occurrence`
    is the `source.Occurrence.id` of the load the symbol came from: a file included twice has one
    entry per occurrence, at the same physical lines."""
    name: str
    kind: str
    detail: str
    range: Span
    selection: Span
    occurrence: int
    children: tuple["Symbol", ...] = ()


class _Frame:
    def __init__(self, family: str, symbol: dict, children: list):
        self.family, self.symbol, self.children = family, symbol, children


def _param_text(p: S.Param) -> str:
    text = p.name + ("()" if p.dims else "")
    return text + (f" As {p.type.title() if p.type else ''}" if p.type else "")


def _sub_detail(stmt: S.SubDef) -> str:
    detail = "(" + ", ".join(_param_text(p) for p in stmt.params) + ")"
    return detail + (f" As {stmt.return_type.title()}" if stmt.return_type else "")


def _locations(program, path: Path):
    """`[(stmt, first, last, occurrence)]` for the statements of `program` that come from the file
    at `path`, in program order, with 1-based physical lines. The AST gives each statement a
    reported line; `Program.line_map` turns it into physical lines. A file loaded twice has its
    statements attributed in load order: the reported line going back means the next load."""
    occs = [o for o in program.occurrences if o.path is not None and _same_path(o.path, path)]
    ids: dict[str, list[int]] = {}
    for o in occs:
        ids.setdefault(o.file.lower(), []).append(o.id)
    state: dict[str, list[int]] = {}
    out = []
    for stmt in program.statements:
        key = stmt.file.lower()
        if key not in ids:
            continue
        known = ids[key]
        spans = [s for s in positions.reported_spans(program.line_map, stmt.file, stmt.line)
                 if s.occurrence in known]
        if not spans:
            continue
        st = state.setdefault(key, [0, 0])      # [index of the load, last reported line]
        if stmt.line < st[1]:
            st[0] = min(st[0] + 1, len(known) - 1)
        st[1] = stmt.line
        span = next((s for s in spans if s.occurrence == known[st[0]]), spans[0])
        out.append((stmt, span.first, span.last, span.occurrence))
    return out


def _is_keyword(cat: InstructionCatalog, word: str) -> bool:
    return cat.get(word, "keyword") is not None


def _outline(located, roles: _Roles, cat: InstructionCatalog, occ: int) -> list[Symbol]:
    root: list[Symbol] = []
    frames: list[_Frame] = []
    prev_last = 0       # last physical line of the previous statement (implicit block ends)

    def leaf(name, kind, detail, first, last) -> Symbol:
        r = _lines(first, last)
        return Symbol(name, kind, detail, r, r, occ)

    def pop(k: int, end: int | None) -> None:
        """Close `frames[k]` through physical line `end` (None: header only); the frames opened
        inside it and never closed keep their header only."""
        inner = frames[k + 1:]
        frame = frames[k]
        del frames[k:]
        for fr in inner:
            frame.children.append(_symbol(fr, None))
        (frames[-1].children if frames else root).append(_symbol(frame, end))

    def _symbol(fr: _Frame, end: int | None) -> Symbol:
        sym = fr.symbol
        r = sym["range"] if end is None else _lines(sym["first"], end)
        return Symbol(sym["name"], sym["kind"], sym["detail"], r, sym["selection"], occ,
                      tuple(fr.children))

    for stmt, first, last, _ in located:
        event = _ast_event(stmt, roles)
        if event and event[0] == "close":
            for k in range(len(frames) - 1, -1, -1):
                if frames[k].family == event[1]:
                    for j in range(len(frames) - 1, k, -1):     # unclosed SlowSequence inside
                        if frames[j].family in _IMPLICIT_END:
                            pop(j, prev_last)
                    pop(k, last)
                    break
            prev_last = last
            continue
        if event and event[0] == "open" and event[1] in _SCOPE_FAMILIES:
            family = event[1]
            if family in _IMPLICIT_END:
                for k in range(len(frames) - 1, -1, -1):
                    if frames[k].family == family:
                        pop(k, prev_last)
                        break
            hdr = _lines(first, last)
            name, kind, detail = _header(stmt, family, cat)
            children = ([Symbol(p.name, "parameter", p.type.title() if p.type else "", hdr, hdr, occ)
                         for p in stmt.params] if isinstance(stmt, S.SubDef) else [])
            frames.append(_Frame(family, dict(name=name, kind=kind, detail=detail, range=hdr,
                                              selection=hdr, first=first), children))
            prev_last = last
            continue
        prev_last = last
        scope = frames[-1].family if frames else None
        container = frames[-1].children if frames else root
        if scope in _ROUTINES:
            continue
        if scope == "DATATABLE":
            if isinstance(stmt, S.Instruction) and not _is_keyword(cat, stmt.keyword):
                spelled = cat.get(stmt.keyword, "instruction")
                container.append(leaf(spelled.name if spelled else stmt.keyword, "output",
                                      " ".join(stmt.text.split())[:80], first, last))
            continue
        if scope == "STRUCTURETYPE":
            if isinstance(stmt, S.Declaration):
                container.extend(leaf(v.name, "member", v.type.title() if v.type else "", first,
                                      last) for v in stmt.vars)
            continue
        if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
            container.extend(leaf(v.name, stmt.kind.lower(),
                                  (v.type.title() if v.type else "") + (" array" if v.dims else ""),
                                  first, last) for v in stmt.vars)
        elif isinstance(stmt, S.ConstDecl):
            container.append(leaf(stmt.name, "const", stmt.type.title() if stmt.type else "",
                                  first, last))
        elif isinstance(stmt, S.Instruction) and stmt.keyword == "ALIAS":
            container.extend(leaf(a.name, "alias", "", first, last)
                             for a in stmt.args[1:] if isinstance(a, S.Name))
    while frames:       # never closed: header only
        pop(len(frames) - 1, None)
    return root


def _declared_name_arg(first, family: str) -> str | None:
    """The name a DataTable / ConstTable / StructureType statement declares, from its first
    argument: the bare name, or for DataTable and ConstTable the quoted name of
    `DataTable("Hist", ...)` (declared the same way by `bindings.py`)."""
    if isinstance(first, S.Name):
        return first.name
    if isinstance(first, S.String) and first.value and family in ("DATATABLE", "CONSTTABLE"):
        return first.value
    return None


def _header(stmt: S.Stmt, family: str, cat: InstructionCatalog) -> tuple[str, str, str]:
    """`(name, kind, detail)` of a block-opening statement."""
    if isinstance(stmt, S.SubDef):
        return stmt.name, family.lower(), _sub_detail(stmt)
    if family in ("DATATABLE", "CONSTTABLE", "STRUCTURETYPE"):
        first = stmt.args[0] if getattr(stmt, "args", None) else None
        name = _declared_name_arg(first, family)
        if name is not None:
            return name, family.lower(), ""
    m = re.match(r"\s*(\w+)", stmt.text)
    return (m.group(1) if m else family), family.lower(), " ".join(stmt.text.split())[:80]


def document_symbols(result, path: Path, profile: str | None = None) -> list[Symbol]:
    """The outline of the file at `path` from the analysed program `result` (an
    `analysis.AnalysisResult`): Sub / Function (parameters as children), DataTable (its
    instructions), ConstTable (its constants), StructureType (its members), BeginProg / Scan /
    SubScan / SlowSequence blocks (declarations inside them as children), and the Const / Public /
    Dim / Alias declarations. Declarations inside a Sub or Function are not listed. Only code the
    compiler reads is there: inactive `#If` branches and text after `EndProg` have no symbols. A
    file included twice has one entry per occurrence. An empty list when nothing was parsed."""
    program = result.program
    if program is None or not hasattr(program, "line_map") or not hasattr(program, "occurrences"):
        return []
    profile = profile or result.request.logger
    roles, cat = _roles(profile), InstructionCatalog.for_profile(profile)
    located = _locations(program, Path(path))
    out: list[Symbol] = []
    for occ in dict.fromkeys(o for *_, o in located):
        out.extend(_outline([x for x in located if x[3] == occ], roles, cat, occ))
    return out


# ------------------------------------------------------------------ declared names


class DeclaredName(NamedTuple):
    """A name the program declares. `scope` is None for a global, else the Sub / Function it is
    local to (parameters and routine-level declarations)."""
    name: str
    kind: str       # variable | constant | alias | sub | user_function | table | type | parameter
    scope: str | None


def declared_names(program, profile: str = "cr6") -> list[DeclaredName]:
    """Names declared by the parsed `program` (an AST walk; LSP-05a's binding index will replace
    it). Text the compiler does not read has none."""
    if program is None:
        return []
    roles = _roles(profile)
    out: list[DeclaredName] = []
    routine: str | None = None
    for stmt in getattr(program, "statements", ()):
        event = _ast_event(stmt, roles)
        if isinstance(stmt, S.SubDef):
            out.append(DeclaredName(stmt.name, "sub" if stmt.keyword == "SUB" else "user_function", None))
            out.extend(DeclaredName(p.name, "parameter", stmt.name) for p in stmt.params)
            routine = stmt.name
        elif event and event[0] == "close" and event[1] in _ROUTINES:
            routine = None
        elif isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
            out.extend(DeclaredName(v.name, "variable", routine) for v in stmt.vars)
        elif isinstance(stmt, S.ConstDecl):
            out.append(DeclaredName(stmt.name, "constant", routine))
        elif isinstance(stmt, S.Instruction):
            first = stmt.args[0] if stmt.args else None
            if stmt.keyword == "ALIAS":
                out.extend(DeclaredName(a.name, "alias", routine) for a in stmt.args[1:]
                           if isinstance(a, S.Name))
            elif stmt.keyword in ("DATATABLE", "CONSTTABLE"):
                table = _declared_name_arg(first, stmt.keyword)
                if table is not None:
                    out.append(DeclaredName(table, "table", None))
            elif stmt.keyword == "STRUCTURETYPE" and isinstance(first, S.Name):
                out.append(DeclaredName(first.name, "type", None))
    return out


# ------------------------------------------------------------------ completion


@dataclass(frozen=True)
class CompletionItem:
    label: str
    kind: str                   # instruction | function | symbol | keyword (catalog); variable |
    # constant | alias | sub | user_function | table | type | parameter (program); field (member)
    source: str                 # "catalog" | "program" | "member"
    detail: str = ""
    param_count: int | None = None   # catalog instructions: number of table parameters; catalog
    # functions: `nargs` / number of `argtypes` when the table has them; otherwise None


@dataclass(frozen=True)
class Completion:
    items: tuple[CompletionItem, ...]
    replace: Span           # the identifier prefix before the cursor (empty at a word boundary)
    prefix: str
    member_of: str | None = None    # the name before the `.` when completing a member


_PREFIX_RE = re.compile(r"(?<![A-Za-z0-9_$])[A-Za-z_][A-Za-z0-9_]*\$?$")
_BASE_RE = re.compile(r"(?<![A-Za-z0-9_$.])([A-Za-z_][A-Za-z0-9_]*\$?)\s*\.\s*$")


def _catalog_items(cat: InstructionCatalog) -> list[CompletionItem]:
    best: dict[str, CompletionItem] = {}
    for entry in cat:
        if entry.kind == "instruction":
            count = len(entry.params)
        elif entry.kind == "function":
            count = entry.nargs if entry.nargs is not None else (
                len(entry.argtypes) if entry.argtypes is not None else None)
        else:
            count = None
        item = CompletionItem(entry.name, entry.kind, "catalog", "", count)
        key = entry.name.upper()
        # the language words win over their instruction row (`If`, `Scan`, ...), then the
        # catalog's usual priority
        order = ("keyword", "instruction", "function", "symbol")
        if key not in best or order.index(entry.kind) < order.index(best[key].kind):
            best[key] = item
    return list(best.values())


def _member_items(result, name: str, profile: str) -> list[CompletionItem]:
    program = getattr(result, "program", None)
    if program is None or not hasattr(program, "statements"):
        return []
    from . import profiles, tables
    try:
        built = tables.build_tables(program, profiles.get(profile), getattr(program, "consts", {}))
    except Exception:       # a half-edited program the table builder rejects: no members
        return []
    items = []
    for table in built[0]:
        if table.name.lower() == name.lower():
            for f in table.fields:
                for label in (f.name, *f.aliases):
                    items.append(CompletionItem(label, "field", "member", table.name))
    return items


def completion_candidates(result, text: str, line: int, column: int, profile: str = "cr6",
                          declared: Sequence[tuple] | None = None) -> Completion:
    """Completion at 0-based physical `line` and decoded `column` of the buffer `text` (Latin-1
    decoded, like the compiler). `result` is the last `analysis.AnalysisResult` or None; it may be
    stale, it only supplies names and table fields. `declared` replaces the names read from the
    program with `(name, kind, scope)` tuples (`declared_names` is the default).

    Inside a string or after a `'` comment start there are no candidates. After `name.` the
    candidates are the fields of the table called `name` (Status, Public, a DataTable, a
    StructureType variable), none when the table is unknown. Otherwise: the program's names (the
    locals and parameters only inside their own Sub / Function, found from `text`) and the
    catalog's instructions, functions, symbols and keywords in the profile's spelling, matched
    case-insensitively on the prefix. Program names come first, then alphabetically."""
    lines = source._split_physical_lines(text)
    row = lines[line] if 0 <= line < len(lines) else ""
    column = max(0, min(column, len(row)))
    before = row[:column]
    here = Span(line, column, line, column)
    nothing = Completion((), here, "")
    if len(source._strip_comment(before)) < len(before) or before.count('"') % 2:
        return nothing
    m = _PREFIX_RE.search(before)
    prefix = m.group(0) if m else ""
    start = column - len(prefix)
    replace = Span(line, start, line, column)
    base = _BASE_RE.search(before[:start])
    if base is not None:
        items = [i for i in _member_items(result, base.group(1), profile)
                 if i.label.lower().startswith(prefix.lower())]
        items.sort(key=lambda i: i.label.lower())
        return Completion(tuple(items), replace, prefix, base.group(1))
    if before[:start].rstrip().endswith("."):
        return Completion((), replace, prefix)
    if declared is None:
        declared = declared_names(getattr(result, "program", None), profile)
    routine = None
    for b in _scan(text, profile):
        if b.family in _ROUTINES and b.name and b.start <= line and (b.end is None or line <= b.end):
            routine = b.name.lower()
    program_items = [CompletionItem(n, k, "program") for n, k, s in declared
                     if s is None or (routine is not None and s.lower() == routine)]
    items = [i for i in program_items + _catalog_items(InstructionCatalog.for_profile(profile))
             if i.label.lower().startswith(prefix.lower())]
    items.sort(key=lambda i: (i.source != "program", i.label.lower(), i.label))
    return Completion(tuple(items), replace, prefix)
