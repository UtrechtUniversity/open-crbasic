"""Navigation logic for editor tooling: go to definition, find references, document highlights and
semantic tokens over an `AnalysisResult`. Protocol independent (no pygls): positions in and out are
physical, a 0-based line and a decoded-character column (a byte offset into the line, as in
`positions.py`); the server converts to the client's position encoding. See `docs/lsp-api.md`
"Navigation (LSP-05b-core)".

A position is mapped to a record by the records' own ranges, never by searching text for a name:
`bindings.BindingIndex.token_of` gives the token of each declaration/reference (a column into the
statement's text), the statement's `origin` (logical line and segment offset, set by the parser)
gives the logical column, and `positions.exact_range` maps it to the physical file. A token that
does not come from one contiguous physical slice, or whose token cannot be found, has no range and
is never navigated.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from . import bindings, positions

__all__ = ["Location", "Highlight", "SemanticToken", "Site", "Navigation", "navigation", "definition",
           "references", "references_across", "declaration_sites", "document_highlights",
           "semantic_tokens", "path_keys", "TOKEN_TYPES", "TOKEN_MODIFIERS"]

# Semantic-token legend (indices into these tuples are the LSP wire values; the server declares
# them in this order).
TOKEN_TYPES = ("variable", "parameter", "function", "property", "namespace", "struct", "type")
TOKEN_MODIFIERS = ("declaration", "readonly")

_KIND_TYPE = {
    "public": "variable", "dim": "variable", "static": "variable", "alias": "variable",
    "const": "variable", "param": "parameter", "sub": "function", "function": "function",
    "structure_member": "property", "datatable": "namespace", "consttable": "namespace",
    "structuretype": "struct", "objecttype": "type",
}
_READ_ROLES = {bindings.READ, bindings.CALL, bindings.MEMBER_BASE, bindings.MEMBER,
               bindings.TABLE_REF, bindings.ALIAS_TARGET, bindings.TYPE, bindings.INSTRUCTION}
_KIND_RANK = {"write": 3, "read": 2, "text": 1}


@dataclass(frozen=True)
class Location:
    path: Path | None               # the file (None when the occurrence has no resolved path)
    file: str                       # compiler display name
    line: int                       # physical, 0-based
    start: int                      # decoded column, inclusive
    end: int                        # decoded column, exclusive
    occurrences: tuple[int, ...]    # `Occurrence.id` of each load of the file that gives this range


@dataclass(frozen=True)
class Highlight:
    line: int
    start: int
    end: int
    kind: str                       # "write" | "read" | "text"


class SemanticToken(NamedTuple):
    line: int
    start: int
    length: int
    type: str                       # one of TOKEN_TYPES
    modifiers: tuple[str, ...]      # subset of TOKEN_MODIFIERS


class Site(NamedTuple):
    """The exact physical range of one declaration or reference (`rec`) of an analysis."""
    rec: bindings.Declaration | bindings.Reference
    path: Path | None
    file: str
    occurrence: int
    line: int
    start: int
    end: int


def path_keys(path) -> set[str]:
    """The normalised spellings (absolute and symlink-resolved) under which `path` is matched."""
    p = Path(path)
    out = {os.path.normcase(str(p.absolute()))}
    try:
        out.add(os.path.normcase(str(p.resolve())))
    except OSError:
        pass
    return out


class Navigation:
    """Ranges of every record of one analysis, built lazily on the first query."""

    def __init__(self, result, index: bindings.BindingIndex | None = None):
        self.result = result
        program = getattr(result, "program", None)
        self.index = index
        if self.index is None and program is not None:
            self.index = bindings.build(program, result.request.logger)
        self._sites: list[Site] | None = None
        self._by_line: dict[tuple[str, int], list[Site]] = {}
        self._by_path: dict[str, list[Site]] = {}
        self._site_of: dict[int, Site] = {}

    # ---------------------------------------------------------------- ranges

    def occurrence_paths(self) -> dict[int, Path | None]:
        """`Occurrence.id` -> the file of that load (`None` when unresolved); id 0 is the entry."""
        occs = getattr(getattr(self.result, "program", None), "occurrences", None) or []
        paths = {o.id: o.path for o in occs}
        if paths.get(0) is None:
            paths[0] = Path(self.result.request.entry).absolute()
        return paths

    def _site(self, rec, paths) -> Site | None:
        unit = rec.unit
        origin = getattr(unit, "origin", None)
        if origin is None:
            return None
        if self.index is None:
            return None
        tok = self.index.token_of(rec)
        if tok is None:
            return None
        ll, seg = origin
        text = unit.text
        shift = None
        for cand in (0, len(ll.text[seg:]) - len(ll.text[seg:].lstrip())):    # `Units` text is stripped
            if ll.text.startswith(text, seg + cand):
                shift = cand
                break
        if shift is None:
            return None
        start = seg + shift + tok.col
        end = start + len(tok.text)
        if ll.text[start:end] != tok.text:
            return None
        r = positions.exact_range(ll, start, end)
        if r is None or r.end_col <= r.start_col:
            return None
        return Site(rec, paths.get(r.occurrence), r.file, r.occurrence, r.line - 1, r.start_col,
                     r.end_col)

    def _build(self) -> list[Site]:
        if self._sites is not None:
            return self._sites
        sites: list[Site] = []
        if self.index is not None:
            paths = self.occurrence_paths()
            for rec in (*self.index.declarations, *self.index.references):
                try:
                    site = self._site(rec, paths)
                except Exception:                       # never raise out of navigation
                    site = None
                if site is None:
                    continue
                sites.append(site)
                self._site_of[id(rec)] = site
                if site.path is not None:
                    for key in path_keys(site.path):
                        self._by_line.setdefault((key, site.line), []).append(site)
                        self._by_path.setdefault(key, []).append(site)
        self._sites = sites
        return sites

    def _path_sites(self, path) -> list[Site]:
        self._build()
        seen: set[int] = set()
        out = []
        for key in path_keys(path):
            for s in self._by_path.get(key, ()):
                if id(s) not in seen:
                    seen.add(id(s))
                    out.append(s)
        return out

    def sites_at(self, path, line: int, col: int) -> list[Site]:
        """The sites whose name covers the position (`line` 0-based, `col` decoded). A position
        inside a name selects that name; a position just after one selects it only when no name
        contains the position. Empty when none."""
        self._build()
        seen: set[int] = set()
        hits = []
        for key in path_keys(path):
            for s in self._by_line.get((key, line), ()):
                if id(s) not in seen and s.start <= col <= s.end:
                    seen.add(id(s))
                    hits.append(s)
        inside = [s for s in hits if col < s.end]
        return inside or hits               # a position just after a name still counts

    def site_of(self, record) -> Site | None:
        """The site of a declaration or reference of this analysis, `None` when its range is not
        exact (see "Range policy" in `docs/lsp-api.md`)."""
        self._build()
        return self._site_of.get(id(record))

    def _targets(self, path, line: int, col: int) -> list[bindings.Declaration]:
        out: list[bindings.Declaration] = []
        for s in self.sites_at(path, line, col):
            cands = (s.rec,) if isinstance(s.rec, bindings.Declaration) else s.rec.resolved
            for d in cands:
                if all(d is not o for o in out):
                    out.append(d)
        return out

    # ---------------------------------------------------------------- queries

    def definition(self, path, line: int, col: int) -> list[Location]:
        self._build()
        sites = [self._site_of.get(id(d)) for d in self._targets(path, line, col)]
        return _locations(s for s in sites if s is not None)

    def references(self, path, line: int, col: int, include_declaration: bool = True) -> list[Location]:
        self._build()
        return _locations(self._reference_sites(self._targets(path, line, col), include_declaration))

    def declaration_sites(self, path, line: int, col: int) -> list[Site]:
        """The sites of the declaration(s) the name at the position resolves to, for those that
        have an exact range (external and predefined declarations have none)."""
        self._build()
        sites = [self._site_of.get(id(d)) for d in self._targets(path, line, col)]
        return [s for s in sites if s is not None]

    def declaration_at(self, path, line: int, start: int, end: int) -> list[bindings.Declaration]:
        """The declarations whose own token is exactly at that range: how the same declaration is
        found in another analysis that reads the same file (LSP-R4a)."""
        return [s.rec for s in self.sites_at(path, line, start)
                if isinstance(s.rec, bindings.Declaration) and (s.start, s.end) == (start, end)]

    def _reference_sites(self, targets, include_declaration: bool) -> list[Site]:
        sites: list[Site] = []
        if self.index is None:
            return sites
        for d in targets:
            if include_declaration and id(d) in self._site_of:
                sites.append(self._site_of[id(d)])
            for r in self.index.references_to(d):
                if id(r) in self._site_of:
                    sites.append(self._site_of[id(r)])
        return sites

    def document_highlights(self, path, line: int, col: int) -> list[Highlight]:
        self._build()
        targets = self._targets(path, line, col)
        if not targets:
            return []
        ids = {id(d) for d in targets}
        best: dict[tuple[int, int, int], str] = {}
        for s in self._path_sites(path):
            rec = s.rec
            if isinstance(rec, bindings.Declaration):
                if id(rec) not in ids:
                    continue
                kind = "write"
            else:
                if not any(id(d) in ids for d in rec.resolved):
                    continue
                kind = _ref_kind(rec)
            key = (s.line, s.start, s.end)
            if _KIND_RANK[kind] > _KIND_RANK.get(best.get(key, ""), 0):
                best[key] = kind
        return [Highlight(l, a, b, k) for (l, a, b), k in sorted(best.items())]

    def semantic_tokens(self, path) -> list[SemanticToken]:
        out: dict[tuple[int, int], SemanticToken] = {}
        for s in self._path_sites(path):
            rec = s.rec
            if not isinstance(rec, bindings.Declaration) or rec.scope == "predefined":
                continue
            ttype = _KIND_TYPE.get(rec.kind)
            if ttype is None:
                continue
            if rec.kind == "const" and rec.container is not None:
                ttype = "property"
            mods = ("declaration", "readonly") if rec.kind == "const" else ("declaration",)
            out.setdefault((s.line, s.start), SemanticToken(s.line, s.start, s.end - s.start, ttype, mods))
        return [out[k] for k in sorted(out)]


def _ref_kind(ref: bindings.Reference) -> str:
    if ref.is_write or ref.role == bindings.WRITE:
        return "write"
    return "read" if ref.role in _READ_ROLES else "text"


def _locations(sites) -> list[Location]:
    merged: dict[tuple, list] = {}
    for s in sites:
        key = (str(s.path) if s.path is not None else "", s.file, s.line, s.start, s.end)
        entry = merged.setdefault(key, [s, []])
        if s.occurrence not in entry[1]:
            entry[1].append(s.occurrence)
    out = [Location(s.path, s.file, s.line, s.start, s.end, tuple(sorted(occ)))
           for s, occ in merged.values()]
    return sorted(out, key=lambda l: (str(l.path or ""), l.file, l.line, l.start))


# ------------------------------------------------------------------ module functions

_last: tuple | None = None            # (result, index argument, Navigation): the latest analysis


def navigation(result, index: bindings.BindingIndex | None = None) -> Navigation:
    """The `Navigation` of `result`, reused while the same `AnalysisResult` object is queried."""
    global _last
    if _last is not None and _last[0] is result and _last[1] is index:
        return _last[2]
    nav = Navigation(result, index)
    _last = (result, index, nav)
    return nav


def definition(result, path, line: int, col: int, *, index=None) -> list[Location]:
    """Declaration locations of the name at (`line`, `col`) of `path`; all candidates when the name
    is ambiguous, none when unresolved, external (catalog) or not an indexed name."""
    return navigation(result, index).definition(path, line, col)


def references(result, path, line: int, col: int, include_declaration: bool = True, *,
               index=None) -> list[Location]:
    """Every reference to the declaration(s) of the name at the position, in every file."""
    return navigation(result, index).references(path, line, col, include_declaration)


def _decl_key(site: Site) -> tuple:
    return (site.path, site.line, site.start, site.end)


def declaration_sites(navs, path, line: int, col: int) -> list[Site]:
    """The declaration sites the name at the position resolves to in any of `navs` (one
    `Navigation` per analysis; those that do not read `path` contribute nothing), one per range."""
    out: dict[tuple, Site] = {}
    for nav in navs:
        for s in nav.declaration_sites(path, line, col):
            out.setdefault(_decl_key(s), s)
    return list(out.values())


def references_across(navs, path, line: int, col: int,
                      include_declaration: bool = True) -> list[Location]:
    """References over several analyses (LSP-R4a): the name at the position is resolved in every
    nav that reads `path`; each resolved declaration with an exact range is then found in every
    nav by its token's file and position (`Navigation.declaration_at`), and the references of all
    of them are united. A range listed by several analyses appears once, with the `occurrences`
    of the first analysis (in `navs` order) that lists it."""
    navs = list(navs)
    keys = [_decl_key(s) for s in declaration_sites(navs, path, line, col)]
    merged: dict[tuple, Location] = {}
    for nav in navs:
        nav._build()
        targets: list[bindings.Declaration] = list(nav._targets(path, line, col))
        for p, ln, a, b in keys:
            if p is None:
                continue
            for d in nav.declaration_at(p, ln, a, b):
                if all(d is not t for t in targets):
                    targets.append(d)
        for loc in _locations(nav._reference_sites(targets, include_declaration)):
            merged.setdefault((str(loc.path or ""), loc.file, loc.line, loc.start, loc.end), loc)
    return sorted(merged.values(), key=lambda l: (str(l.path or ""), l.file, l.line, l.start))


def document_highlights(result, path, line: int, col: int, *, index=None) -> list[Highlight]:
    """The occurrences in `path` of the declaration(s) of the name at the position."""
    return navigation(result, index).document_highlights(path, line, col)


def semantic_tokens(result, path, *, index=None) -> list[SemanticToken]:
    """Declared names of `path`, sorted by position."""
    return navigation(result, index).semantic_tokens(path)
