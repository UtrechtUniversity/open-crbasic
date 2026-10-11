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
_VARIABLE = {"public", "dim", "static", "alias"}        # their names can become table field names
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
            paths = self.nav._occurrence_paths()
            line_map = getattr(program, "line_map", None) or {}
            for st in program.statements:
                for sp in line_map.get(st.file, {}).get(st.line, ()):
                    p = paths.get(sp.occurrence)
                    if p is not None:
                        parsed.update((k, n - 1) for k in navigation._keys(p)
                                      for n in range(sp.first, sp.last + 1))
            self._parsed = parsed
        return any((k, line) in self._parsed for k in navigation._keys(path))

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
        paths = self.nav._occurrence_paths()
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
    nav._build()
    sites = nav._at(path, line, col)
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
        dsite = nav._site_of.get(id(d))
        if dsite is None or dsite.path is None:
            raise _Refuse("no_range", f"the declaration of {d.name!r} has no exact physical range")
        rs = {_site_key(dsite)}
        for r in ix.references_to(d):
            if r.status != "resolved":
                raise _Refuse("ambiguous", f"a reference to {d.name!r} at {r.file}:{r.line} is "
                              f"{r.status}: {r.ambiguity or 'not a single proven target'}")
            rsite = nav._site_of.get(id(r))
            if rsite is None or rsite.path is None:
                raise _Refuse("no_range", f"the reference to {d.name!r} at {r.file}:{r.line} has "
                              "no exact physical range")
            rs.add(_site_key(rsite))
            ref_ids.add(id(r))
        sets.append(frozenset(rs))
    if any(s != sets[0] for s in sets):
        raise _Refuse("occurrences_differ", f"{name!r} is declared by a file loaded more than "
                      "once and the loads differ")
    for u in ix.unparsed:
        if name in {t.upper for t in lexer_tokens(u)}:
            raise _Refuse("unparsed", f"a statement the parser could not take apart "
                          f"({u.file}:{u.line}) mentions {name!r}")
    if targets[0].kind in _VARIABLE:
        _check_field_names(ix, name)
    key = frozenset((rk[0], rk[1], rk[2], rk[3], d.kind, name) for d in targets
                    for rk in [_site_key(nav._site_of[id(d)])])
    return _Entry(state, name, tuple(targets), sets[0], frozenset(ref_ids), key), placeholder, \
        (clicked.path, clicked.line, clicked.start, clicked.end)


def lexer_tokens(stmt):
    toks = bindings.classified_tokens(stmt)
    if toks is not None:
        return [t for t, _ in toks]
    out = []
    for w in _WORD.findall(_STRING.sub(" ", getattr(stmt, "text", "") or "")):
        out.append(lexer.Token("IDENT", w, w.upper(), 0))
    return out


def _check_field_names(ix, name: str) -> None:
    """A variable's name can be a data table field name (`Sample`, `Average` -> `name_Avg`), and the
    index does not model fields: a `Table.Field` use derived from the name would be missed."""
    for r in ix.references:
        if (r.role == bindings.MEMBER and r.status == "unmodelled"
                and any(c.kind == "datatable" for c in r.container)):
            up = r.name.upper()
            if up == name or up.startswith(name + "_"):
                raise _Refuse("table_field", f"the table field use {r.file}:{r.line} "
                              f"({r.name!r}) may derive from {name!r}; field names are not modelled")


# ------------------------------------------------------------------- new name

_SYSTEM_NAMES = ("STATUS", "PUBLIC", "SETTINGS", "DATATABLEINFO")


def _check_new_name(entry: _Entry, new_name: str) -> None:
    state = entry.state
    ix = state.nav.index
    up = new_name.upper()
    cat = InstructionCatalog.for_profile(state.result.request.logger)
    if cat.get(new_name) is not None or up in _SYSTEM_NAMES or up in bindings._EXTRA_PREDEFINED:
        raise _Refuse("reserved_name", f"{new_name!r} is a keyword, instruction, function or "
                      f"predefined symbol of {cat.profile}")
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
            if t.kind == "structure_member":
                clash = r.role == bindings.MEMBER and any(c is t.container for c in r.container)
            else:
                clash = r.role != bindings.MEMBER
            if clash:
                raise _Refuse("collision", f"{new_name!r} is already used at {r.file}:{r.line} "
                              f"({r.status})")
    if entry.targets[0].kind in _VARIABLE:
        _check_field_names(ix, up)
    hit = state.string_words().get(up)
    if hit is not None:
        raise _Refuse("string_mention", f"{new_name!r} occurs inside a string literal "
                      f"({hit[0]}:{hit[1]})")
    for path, ln, _, _ in state.unindexed().get(up, ()):
        raise _Refuse("unindexed_code", f"{new_name!r} occurs in code the index does not cover "
                      f"({path.name}:{ln + 1})")


def _valid_identifier(new_name: str) -> bool:
    m = _WORD.fullmatch(new_name)
    return m is not None and not new_name.endswith("$")


# ------------------------------------------------------------------- API

def _collect(results, path, line, col):
    path = Path(path).absolute()
    if not results:
        raise _Refuse("no_results", "no analysis result was given")
    entries: list[_Entry] = []
    first = None
    for r in results:
        st = _state(r)
        reads = r.dependencies.reads
        if not any(Path(p) == path or _same(p, path) for p in reads):
            continue                                    # this entry does not depend on the file
        if getattr(r, "program", None) is not None and not st.parses(path, line):
            continue                                    # the declaration is not in this entry's program
        e, placeholder, rng = _entry(st, path, line, col)
        if first is None:
            first = (placeholder, rng)
        entries.append(e)
    if not entries:
        raise _Refuse("no_results", "no given analysis depends on the file")
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


def prepare_rename(results, path, line: int, column: int) -> PrepareResult:
    """The range and current name at (`line`, `column`) when a rename of it can be offered, else
    the reason it cannot. `results`: the `AnalysisResult` of every configured entry that depends
    on `path`."""
    try:
        entries, (placeholder, rng) = _collect(results, path, line, column)
        _versions(entries, {rng[0]})
    except _Refuse as e:
        return PrepareResult(False, e.refusal)
    p, ln, a, b = rng
    return PrepareResult(True, None, p, ln, a, b, placeholder)


def rename(results, path, line: int, column: int, new_name: str) -> RenameResult:
    """Edits renaming the declaration at (`line`, `column`) and every reference to it, across
    every entry in `results`, or a `Refusal`. Edits are deduplicated; `versions` maps each edited
    path to the sha256 of the bytes the edits were computed against (equal in every entry)."""
    try:
        entries, (placeholder, rng) = _collect(results, path, line, column)
        if not _valid_identifier(new_name):
            raise _Refuse("invalid_name", f"{new_name!r} is not a valid identifier")
        if new_name == placeholder or (new_name.upper() == entries[0].name
                                       and new_name == entries[0].targets[0].name):
            raise _Refuse("unchanged", "the new name equals the current name")
        for e in entries:
            _check_new_name(e, new_name)
        ranges = sorted(set().union(*(e.ranges for e in entries)),
                        key=lambda k: (str(k[0]), k[1], k[2]))
        versions = _versions(entries, {k[0] for k in ranges})
        edits: dict[Path, list[TextEdit]] = {}
        for p, ln, a, b in ranges:
            data = entries[0].state.data(p)
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


def _versions(entries: list[_Entry], paths) -> dict:
    """sha256 of each path's bytes, which must be what every entry that read the path analysed."""
    out = {}
    for p in sorted(paths, key=str):
        for e in entries:
            analysed = next((h for q, h in e.state.result.dependencies.reads.items()
                             if Path(q) == p or _same(q, p)), None)
            if analysed is None:
                continue                                # this entry did not read the file
            digest = hashlib.sha256(e.state.data(p)).hexdigest()
            if analysed != digest:
                raise _Refuse("stale", f"{p.name} differs from the bytes the analysis read")
            if out.setdefault(p, digest) != digest:
                raise _Refuse("stale", f"{p.name} differs between entries")
        if p not in out:
            raise _Refuse("stale", f"{p.name} was not read by any analysis")
    return out
