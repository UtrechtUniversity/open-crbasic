"""Rename logic for editor tooling: `prepare_rename` and `rename` over the `AnalysisResult` of every
configured entry that depends on the file. Protocol independent (no pygls): positions in and out
are physical, a 0-based line and a decoded-character column (a byte offset into the line, as in
`positions.py`). See `docs/lsp-api.md` "Rename (LSP-07a)".

The edits come only from the binding index (`bindings`) and the ranges of `navigation`: the
declaration's range plus the range of every reference that resolves to it, in every entry. No
source text is searched to find occurrences. Anything that cannot be proven a single, fully
ranged binding is refused with a reason (`Refusal`), never guessed. A few conservative refusal
checks read identifiers of lines the parser did not turn into statements (inactive `#If`
branches, directives, text after `EndProg`) only to refuse; they never produce an edit.
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, Sequence

from . import bindings, lexer, navigation
from .catalog import InstructionCatalog
from .source import _strip_comment
from .sources import DiskSource, OverlaySource

__all__ = ["TextEdit", "Refusal", "PrepareResult", "RenameResult", "prepare_rename", "rename",
           "REASONS"]

REASONS = (
    "no_results", "no_ast", "stale", "index_failure", "not_a_name", "unresolved", "ambiguous",
    "unmodelled", "external", "quoted", "kind", "no_range", "text_mismatch", "occurrences_differ",
    "entries_disagree", "unparsed", "unindexed_code", "string_mention", "table_field", "unchanged", "invalid_name",
    "reserved_name", "collision", "overlap",
)

# Declaration kinds whose every use the index records. Labels are declared but their uses (`GoTo`)
# are not references, so they are not renamable.
_RENAMABLE = {"public", "dim", "static", "const", "sub", "function", "param", "alias", "datatable",
              "consttable", "structuretype", "objecttype", "structure_member"}
_VARIABLE = {"public", "dim", "static", "alias"}
# kinds whose names can become data table field names (`Sample(1, P, Pt)` names fields after members)
_FIELD_SOURCES = _VARIABLE | {"structure_member"}
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\$?")
_STRING = re.compile(r'"(?:[^"]|"")*"?')


class TextEdit(NamedTuple):
    line: int                   # physical, 0-based
    start: int                  # decoded column, inclusive
    end: int                    # decoded column, exclusive
    new_text: str


@dataclass(frozen=True)
class Refusal:
    code: str                   # one of REASONS
    message: str


@dataclass(frozen=True)
class PrepareResult:
    ok: bool
    refusal: Refusal | None = None
    path: Path | None = None
    line: int = 0
    start: int = 0
    end: int = 0
    placeholder: str = ""       # the name as spelled at the position
    files: frozenset = frozenset()  # every file with the declaration or a reference to it


@dataclass(frozen=True)
class RenameResult:
    ok: bool
    refusal: Refusal | None = None
    edits: dict = field(default_factory=dict)      # Path -> [TextEdit], sorted, no overlap
    versions: dict = field(default_factory=dict)   # Path -> sha256 hex of the bytes the edits fit


class _Refuse(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.refusal = Refusal(code, message)


# ------------------------------------------------------------------- per-result cache

class _State:
    """Navigation and file facts of one `AnalysisResult`, computed once."""

    def __init__(self, result):
        self.result = result
        self.nav = navigation.Navigation(result)
        self._unindexed: dict | None = None
        self._strings: dict[str, tuple[str, int]] | None = None
        self._parsed: set[tuple[str, int]] | None = None
        self._text: dict[Path, bytes] = {}

    def data(self, path: Path) -> bytes:
        got = self._text.get(path)
        if got is None:
            overlay = self.result.request.overlays.get(path)
            got = overlay if overlay is not None else path.read_bytes()
            self._text[path] = got
        return got

    def parses(self, path: Path, line: int) -> bool:
        """Whether physical `line` (0-based) of `path` is in a statement of this program (not in an
        `IncludeSection` that is not read, an inactive `#If` branch, a directive or text after
        `EndProg`)."""
        if self._parsed is None:
            parsed = set()
            program = self.result.program
            paths = self.nav.occurrence_paths()
            line_map = getattr(program, "line_map", None) or {}
            for st in program.statements:
                for sp in line_map.get(st.file, {}).get(st.line, ()):
                    p = paths.get(sp.occurrence)
                    if p is not None:
                        parsed.update((k, n - 1) for k in navigation.path_keys(p)
                                      for n in range(sp.first, sp.last + 1))
            self._parsed = parsed
        return any((k, line) in self._parsed for k in navigation.path_keys(path))

    def unindexed(self) -> dict[str, list[tuple[Path, int, int, int]]]:
        """Words of the physical lines that are in no statement (inactive `#If`, directives, text
        after `EndProg`, includes): upper-case word -> [(path, 0-based line, start, end)]. Strings
        and comments are blanked first."""
        if self._unindexed is not None:
            return self._unindexed
        out: dict[str, list] = {}
        done = set()
        for path, n in self._unparsed_lines():
            lines = self.data(path).decode("latin-1").replace("\r\n", "\n").split("\n")
            if (path, n) in done or not 0 <= n < len(lines):
                continue
            done.add((path, n))
            text = _STRING.sub(lambda m: " " * len(m.group()), _strip_comment(lines[n]))
            for m in _WORD.finditer(text):
                out.setdefault(m.group().upper(), []).append((path, n, m.start(), m.end()))
        self._unindexed = out
        return out

    def string_words(self) -> dict[str, tuple[str, int]]:
        """Identifier-shaped words inside string literals, upper-case -> (file, line) of one
        occurrence: statements via the lexer, other code lines by their quotes. A string such as
        `SetSetting("Table.Const", ...)` names a declaration; the index does not model that."""
        if self._strings is not None:
            return self._strings
        out: dict[str, tuple[str, int]] = {}
        for st in self.result.program.statements:
            toks = bindings.classified_tokens(st)
            for t, kind in toks or ():
                if kind == "string":
                    for w in _WORD.findall(str(t.value)):
                        out.setdefault(w.upper(), (st.file, st.line))
        for path, n in set(self._unparsed_lines()):
            lines = self.data(path).decode("latin-1").replace("\r\n", "\n").split("\n")
            if 0 <= n < len(lines):
                for m in _STRING.finditer(_strip_comment(lines[n])):
                    for w in _WORD.findall(m.group()):
                        out.setdefault(w.upper(), (path.name, n + 1))
        self._strings = out
        return out

    def _unparsed_lines(self):
        program = self.result.program
        covered = {(st.file, st.line) for st in program.statements}
        paths = self.nav.occurrence_paths()
        for file, by_line in (getattr(program, "line_map", None) or {}).items():
            for line, spans in by_line.items():
                if (file, line) in covered:
                    continue
                for span in spans:
                    path = paths.get(span.occurrence)
                    if path is not None:
                        for n in range(span.first, span.last + 1):
                            yield path, n - 1


_states: "OrderedDict[int, _State]" = OrderedDict()


def _state(result) -> _State:
    key = id(result)
    st = _states.get(key)
    if st is not None and st.result is result:
        _states.move_to_end(key)
        return st
    st = _State(result)
    _states[key] = st
    while len(_states) > 64:
        _states.popitem(last=False)
    return st


# ------------------------------------------------------------------- one entry

class _Entry(NamedTuple):
    state: _State
    name: str                       # upper-case current name
    targets: tuple                  # bindings.Declaration
    ranges: frozenset               # {(path, line, start, end)} to edit
    refs: frozenset                 # id() of the references to edit
    key: frozenset                  # the declaration set, comparable across entries


def _site_key(site) -> tuple:
    return (site.path, site.line, site.start, site.end)


def _entry(state: _State, path: Path, line: int, col: int) -> tuple[_Entry, str, tuple]:
    result, nav = state.result, state.nav
    if getattr(result, "program", None) is None or nav.index is None:
        raise _Refuse("no_ast", "the program could not be parsed")
    ix = nav.index
    if ix.failures:
        raise _Refuse("index_failure", f"the binding index failed for a statement: "
                      f"{ix.failures[0].message}")
    sites = nav.sites_at(path, line, col)
    if not sites:
        raise _Refuse("not_a_name", "the position is not on a name the program declares or uses")
    targets: list[bindings.Declaration] = []
    clicked = sites[0]
    for s in sites:
        rec = s.rec
        if isinstance(rec, bindings.Declaration):
            cands: Sequence = (rec,)
        else:
            status = rec.status
            if status == "unmodelled":
                raise _Refuse("unmodelled", f"{rec.name!r} is a table field name; field names are "
                              "not modelled")
            if status == "unresolved":
                raise _Refuse("unresolved", f"{rec.name!r} does not resolve to a declaration")
            if status == "ambiguous":
                raise _Refuse("ambiguous", f"{rec.name!r} is ambiguous: "
                              f"{rec.ambiguity or 'several candidate declarations'}")
            cands = rec.resolved
        for d in cands:
            if all(d is not o for o in targets):
                targets.append(d)
    if not targets:
        raise _Refuse("not_a_name", "the position is not on a name the program declares or uses")
    for d in targets:
        if d.kind == "field":       # `Tab.Field`, `Status.X`: field names are not modelled (LSP-05c)
            raise _Refuse("unmodelled", f"{d.name!r} is a table field name; field names are not "
                          "modelled")
        if d.kind in ("external", "system_table") or d.scope == "predefined" or d.external is not None:
            raise _Refuse("external", f"{d.name!r} is predefined (catalog or system table)")
        if d.quoted:
            raise _Refuse("quoted", f"table {d.name!r} is named by a string; string names are "
                          "never rewritten")
        if d.kind not in _RENAMABLE:
            raise _Refuse("kind", f"a {d.kind} is not renamable")
        if d.occurrence is None:
            raise _Refuse("no_range", f"{d.name!r} has no token position")
    names = {d.name.upper() for d in targets}
    if len(names) != 1:
        raise _Refuse("ambiguous", "the position resolves to differently named declarations")
    name = names.pop()
    placeholder = clicked.rec.name
    if clicked.end - clicked.start != len(placeholder) or placeholder.upper() != name:
        raise _Refuse("no_range", f"the range at the position is not the name {name!r}")
    sets = []
    ref_ids: set[int] = set()
    for d in targets:
        dsite = nav.site_of(d)
        if dsite is None or dsite.path is None:
            raise _Refuse("no_range", f"the declaration of {d.name!r} has no exact physical range")
        rs = {_site_key(dsite)}
        for r in ix.references_to(d):
            if r.status != "resolved":
                raise _Refuse("ambiguous", f"a reference to {d.name!r} at {r.file}:{r.line} is "
                              f"{r.status}: {r.ambiguity or 'not a single proven target'}")
            rsite = nav.site_of(r)
            if rsite is None or rsite.path is None:
                raise _Refuse("no_range", f"the reference to {d.name!r} at {r.file}:{r.line} has "
                              "no exact physical range")
            rs.add(_site_key(rsite))
            ref_ids.add(id(r))
        sets.append(frozenset(rs))
    if any(s != sets[0] for s in sets):
        raise _Refuse("occurrences_differ", f"{name!r} is declared by a file loaded more than "
                      "once and the loads differ")
    _check_ordinals(ix, targets)
    for u in ix.unparsed:
        if name in {t.upper for t in lexer_tokens(u)}:
            raise _Refuse("unparsed", f"a statement the parser could not take apart "
                          f"({u.file}:{u.line}) mentions {name!r}")
    if targets[0].kind in _FIELD_SOURCES:
        _check_field_names(ix, name)
    key = frozenset((rk[0], rk[1], rk[2], rk[3], d.kind, name) for d in targets
                    for rk in [_site_key(nav.site_of(d))])
    return _Entry(state, name, tuple(targets), sets[0], frozenset(ref_ids), key), placeholder, \
        (clicked.path, clicked.line, clicked.start, clicked.end)


def _check_ordinals(ix, targets) -> None:
    """`token_of` picks a record's token by its ordinal among the same-name tokens of its unit, so a
    token of that name the index has no record for (a name the parser skipped, such as the ignored
    words of `Alias A = X junk, Y`) shifts every later ordinal onto the wrong token. In every unit an
    edit falls in, the tokens of each edited name must not outnumber the records of it."""
    seen: set[tuple] = set()
    for d in targets:
        for rec in (d, *ix.references_to(d)):
            unit, key = rec.unit, rec.tok_key
            if unit is None or not key or (id(unit), key) in seen:
                continue
            seen.add((id(unit), key))
            toks = bindings.classified_tokens(unit) or ()
            n_tok = sum(1 for t, k in toks if k == key[1] and bindings._tok_name(t, k) == key[0])
            n_rec = sum(1 for o in ix.records_in(unit) if o.tok_key == key)
            n_rec += sum(1 for k in bindings.skipped_keys(unit) if k == key)
            if n_tok > n_rec:
                raise _Refuse("no_range", f"{unit.file}:{unit.line}: the statement spells "
                              f"{rec.name!r} {n_tok} times but the index records {n_rec}; the "
                              "token of each occurrence is not proven")


def lexer_tokens(stmt):
    toks = bindings.classified_tokens(stmt)
    if toks is not None:
        return [t for t, _ in toks]
    out = []
    for w in _WORD.findall(_STRING.sub(" ", getattr(stmt, "text", "") or "")):
        out.append(lexer.Token("IDENT", w, w.upper(), 0))
    return out


def _check_field_names(ix, name: str) -> None:
    """A variable's name can be a data table field name (`Sample`, `Average` -> `name_Avg`), so a
    `Table.Field` use derived from the name would not follow a rename. Field names are not renamed
    (`unmodelled`), so every table member use is checked, whether it is unmodelled or resolves to a
    field declaration (LSP-05c)."""
    for r in ix.references:
        if r.role == bindings.MEMBER and any(c.kind == "datatable" for c in r.container):
            up = r.name.upper()
            if up == name or up.startswith(name + "_"):
                raise _Refuse("table_field", f"the table field use {r.file}:{r.line} "
                              f"({r.name!r}) may derive from {name!r}; field names are not modelled")


# ------------------------------------------------------------------- new name

_SYSTEM_NAMES = ("STATUS", "PUBLIC", "SETTINGS", "DATATABLEINFO")


def _check_new_name(entry: _Entry, new_name: str) -> None:
    state = entry.state
    ix = state.nav.index
    assert ix is not None                   # an entry exists only for a program with an index
    up = new_name.upper()
    cat = InstructionCatalog.for_profile(state.result.request.logger)
    if cat.get(new_name) is not None or up in _SYSTEM_NAMES or up in bindings._EXTRA_PREDEFINED:
        raise _Refuse("reserved_name", f"{new_name!r} is a keyword, instruction, function or "
                      f"predefined symbol of {cat.profile}")
    if up == entry.name:
        return          # a case-only change binds nothing new; the old name was checked in `_entry`
    for u in ix.unparsed:
        if up in {t.upper for t in lexer_tokens(u)}:
            raise _Refuse("unparsed", f"a statement the parser could not take apart "
                          f"({u.file}:{u.line}) mentions {new_name!r}")
    ours = {id(d) for d in entry.targets}
    for t in entry.targets:
        for d in ix.declarations_named(up):
            if id(d) in ours:
                continue
            if t.kind == "structure_member":
                clash = d.kind == "structure_member" and d.container is t.container
            elif d.kind == "structure_member":
                clash = False
            elif t.scope == "routine":
                clash = (d.scope == "global" or d.unverified_scope
                         or (d.scope == "routine" and d.routine is t.routine))
            else:
                clash = True                    # a global is visible in every routine
            if clash:
                raise _Refuse("collision", f"{new_name!r} is already declared "
                              f"({d.kind} at {d.file}:{d.line})")
        for r in ix.references:
            if r.name.upper() != up or id(r) in entry.refs:
                continue
            if r.role == bindings.MEMBER:
                # a member use binds the new name when its container is the namespace of the target:
                # the owning type, or the Public table for a Public variable or alias
                clash = ((t.container is not None and any(c is t.container for c in r.container))
                         or (t.kind in ("public", "alias")
                             and any(c.kind == "system_table" and c.name.upper() == "PUBLIC"
                                     for c in r.container)))
            else:
                clash = t.kind != "structure_member"
            if clash:
                raise _Refuse("collision", f"{new_name!r} is already used at {r.file}:{r.line} "
                              f"({r.status})")
    if entry.targets[0].kind in _FIELD_SOURCES:
        _check_field_names(ix, up)
    hit = state.string_words().get(up)
    if hit is not None:
        raise _Refuse("string_mention", f"{new_name!r} occurs inside a string literal "
                      f"({hit[0]}:{hit[1]})")
    for path, ln, _, _ in state.unindexed().get(up, ()):
        raise _Refuse("unindexed_code", f"{new_name!r} occurs in code the index does not cover "
                      f"({path.name}:{ln + 1})")


def _check_unchanged(entries: list[_Entry], new_name: str) -> None:
    """`unchanged` only when every range to edit is already spelled `new_name` exactly; a case-only
    change of any occurrence (`TOTAL` -> `total` for `Public total`) is a rename."""
    if new_name.upper() != entries[0].name:
        return
    for e in entries:
        for p, ln, a, b in e.ranges:
            text = e.state.data(p).decode("latin-1").replace("\r\n", "\n").split("\n")
            if not 0 <= ln < len(text) or text[ln][a:b] != new_name:
                return
    raise _Refuse("unchanged", "the new name equals the current name")


def _valid_identifier(new_name: str) -> bool:
    m = _WORD.fullmatch(new_name)
    return m is not None and not new_name.endswith("$")


# ------------------------------------------------------------------- API

class _Current:
    """The bytes the files have now, read at the operation boundary (never the `_State` cache, which
    serves only checks derived from the analysis). `source` is the caller's provider, else each
    result's own overlays on the disk."""

    def __init__(self, source):
        self.source = source
        self._own: dict[int, OverlaySource] = {}
        self._bytes: dict[tuple, bytes | None] = {}

    def _provider(self, result):
        if self.source is not None:
            return self.source
        got = self._own.get(id(result))
        if got is None:
            got = self._own[id(result)] = OverlaySource(DiskSource(), result.request.overlays)
        return got

    def read(self, result, path) -> bytes | None:
        key = (id(result) if self.source is None else 0, Path(path))
        if key not in self._bytes:
            self._bytes[key] = self._provider(result).read(Path(path))
        return self._bytes[key]

    def verify(self, result) -> None:
        """Every file the analysis read, edited or not, must have the analysed bytes now (and a
        missing one must still be missing): bindings and collisions depend on all of them."""
        for q, analysed in result.dependencies.reads.items():
            data = self.read(result, q)
            now = None if data is None else hashlib.sha256(data).hexdigest()
            if now != analysed:
                raise _Refuse("stale", f"{Path(q).name} differs from the bytes the analysis read")


def _reads(result, path: Path) -> bool:
    return any(Path(p) == path or _same(p, path) for p in result.dependencies.reads)


def _collect(results, path, line, col, current):
    """The entries of `results` that the rename concerns: those that read `path` and parse `line`
    (the clicked binding), then, until nothing changes, every other result that reads the
    declaration's file or an edited file. Such a result is identified with the same declaration
    by the declaration token's file and position; one that parses an edited line but not the
    declaration is refused (`entries_disagree`), one that parses none of them is skipped."""
    path = Path(path).absolute()
    if not results:
        raise _Refuse("no_results", "no analysis result was given")
    entries: list[_Entry] = []
    used: set[int] = set()
    first = None
    readers = 0
    for r in results:
        st = _state(r)
        if not _reads(r, path):
            continue                                    # this entry does not depend on the file
        readers += 1
        if getattr(r, "program", None) is not None and not st.parses(path, line):
            continue                                    # the declaration is not in this entry's program
        current.verify(r)
        e, placeholder, rng = _entry(st, path, line, col)
        if first is None:
            first = (placeholder, rng)
        entries.append(e)
        used.add(id(r))
    if not entries and readers:
        raise _Refuse("not_a_name", "the position is not in code any entry parses (a comment "
                      "line, an inactive #If branch, a directive or text after EndProg)")
    if not entries:
        raise _Refuse("no_results", "no given analysis depends on the file")
    assert first is not None                # set with the first entry
    decl = min(entries[0].key, key=lambda k: (str(k[0]), k[1], k[2]))[:4]
    grown = True
    while grown:                            # entries reached through the declaration or an edit
        grown = False
        union = set().union(*(e.ranges for e in entries))
        for r in results:
            if id(r) in used:
                continue
            touched = [k for k in union | {decl} if _reads(r, k[0])]
            if not touched:
                continue
            st = _state(r)
            if getattr(r, "program", None) is not None:
                if not _reads(r, decl[0]) or not st.parses(decl[0], decl[1]):
                    if any(st.parses(k[0], k[1]) for k in touched):
                        raise _Refuse("entries_disagree", "an entry uses an edited line but does "
                                      "not declare the target")
                    continue
            current.verify(r)
            e, _, _ = _entry(st, decl[0], decl[1], decl[2])
            entries.append(e)
            used.add(id(r))
            grown = True
    if any(e.key != entries[0].key or e.name != entries[0].name for e in entries):
        raise _Refuse("entries_disagree", "the entries disagree on the declaration set")
    for e in entries:
        hit = e.state.string_words().get(e.name)
        if hit is not None:
            raise _Refuse("string_mention", f"{e.name!r} occurs inside a string literal "
                          f"({hit[0]}:{hit[1]}); names in strings are never rewritten")
    union = set().union(*(e.ranges for e in entries))
    for e in entries:                       # the old name in code no entry indexes cannot be renamed
        for p, ln, a, b in e.state.unindexed().get(e.name, ()):
            if (p, ln, a, b) not in union:
                raise _Refuse("unindexed_code", f"{e.name!r} occurs in code the index does not "
                              f"cover ({p.name}:{ln + 1}: inactive #If branch, directive or text "
                              "after EndProg)")
    return entries, first


def _same(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def prepare_rename(results, path, line: int, column: int, source=None) -> PrepareResult:
    """The range and current name at (`line`, `column`) when a rename of it can be offered, else
    the reason it cannot. `results`: the `AnalysisResult` of every configured entry that depends
    on `path`. `source`: optional `SourceProvider` giving the current bytes of every file (default:
    each result's overlays on the disk)."""
    try:
        current = _Current(source)
        entries, (placeholder, rng) = _collect(results, path, line, column, current)
        _versions(entries, {rng[0]}, current)
    except _Refuse as e:
        return PrepareResult(False, e.refusal)
    p, ln, a, b = rng
    files = frozenset(k[0] for e in entries for k in e.ranges)
    return PrepareResult(True, None, p, ln, a, b, placeholder, files)


def rename(results, path, line: int, column: int, new_name: str, source=None) -> RenameResult:
    """Edits renaming the declaration at (`line`, `column`) and every reference to it, across
    every entry in `results`, or a `Refusal`. Edits are deduplicated; `versions` maps each edited
    path to the sha256 of the bytes the edits were computed against (equal in every entry). Every
    file any entry read must still have the analysed bytes now, read through `source` (optional
    `SourceProvider`; default each result's overlays on the disk), else `stale`."""
    try:
        current = _Current(source)
        entries, (placeholder, rng) = _collect(results, path, line, column, current)
        if not _valid_identifier(new_name):
            raise _Refuse("invalid_name", f"{new_name!r} is not a valid identifier")
        _check_unchanged(entries, new_name)
        for e in entries:
            _check_new_name(e, new_name)
        ranges = sorted(set().union(*(e.ranges for e in entries)),
                        key=lambda k: (str(k[0]), k[1], k[2]))
        versions = _versions(entries, {k[0] for k in ranges}, current)
        edits: dict[Path, list[TextEdit]] = {}
        for p, ln, a, b in ranges:
            data = current.read(entries[0].state.result, p)
            if data is None:
                raise _Refuse("stale", f"{p.name} can no longer be read")
            text = data.decode("latin-1").replace("\r\n", "\n").split("\n")[ln]
            if text[a:b].upper() != entries[0].name:
                raise _Refuse("text_mismatch", f"{p.name}:{ln + 1}: the text at the range is "
                              f"{text[a:b]!r}, not the name")
            lst = edits.setdefault(p, [])
            if lst and lst[-1].line == ln and lst[-1].end > a:
                raise _Refuse("overlap", f"{p.name}:{ln + 1}: edits overlap")
            lst.append(TextEdit(ln, a, b, new_name))
        return RenameResult(True, None, edits, versions)
    except _Refuse as e:
        return RenameResult(False, e.refusal)


def _versions(entries: list[_Entry], paths, current: _Current) -> dict:
    """sha256 of the current bytes of each edited path; `_Current.verify` has already shown that
    they are what every entry that read the path analysed."""
    out = {}
    for p in sorted(paths, key=str):
        for e in entries:
            r = e.state.result
            if not any(Path(q) == p or _same(q, p) for q in r.dependencies.reads):
                continue                                # this entry did not read the file
            data = current.read(r, p)
            digest = None if data is None else hashlib.sha256(data).hexdigest()
            if digest is None or out.setdefault(p, digest) != digest:
                raise _Refuse("stale", f"{p.name} differs between entries")
        if p not in out:
            raise _Refuse("stale", f"{p.name} was not read by any analysis")
    return out
