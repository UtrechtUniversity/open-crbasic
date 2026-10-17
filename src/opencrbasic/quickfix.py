"""Quick fixes for editor tooling: candidate code actions for a bounded set of compiler messages.
Protocol independent (no pygls): an `Edit` is a physical range, a 0-based line and decoded-character
columns (byte offsets into the line), exactly as `navigation.py` reports them; the server converts
to the client's position encoding. See `docs/lsp-api.md` "Quick fixes (LSP-07c)" for the message
families, the ranking and the limits.

Every edit replaces one identifier token (or the file-name part of an include string) by a name
that already exists: a declared name visible at the use (`bindings.BindingIndex.lookup`), a catalog
name, or a file in the include directory's listing. Nothing is inserted or deleted otherwise, and
a token without an exact one-slice range (`navigation`) gets no action.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from . import bindings, navigation, positions, profiles
from .catalog import InstructionCatalog
from .sources import DiskSource, OverlaySource, SourceProvider

__all__ = ["Edit", "CodeAction", "code_actions", "apply_edits", "edit_distance",
           "MAX_ACTIONS", "FAMILIES"]

MAX_ACTIONS = 5

# Message families (docs/lsp-api.md). Each: (id, compiled pattern, name group or None).
FAMILIES = (
    ("undefined-symbol", re.compile(r"^Invalid symbol in expression: ", re.S), None),
    ("undefined-symbol", re.compile(r"^(?P<n>[A-Za-z_][A-Za-z0-9_]*\$?) is not defined\.$"), "n"),
    ("undefined-call", re.compile(r"^Invalid, or out of place expression: ", re.S), None),
    ("undefined-sub", re.compile(r"^Sub (?P<n>\S+) not defined\.$"), "n"),
    ("undefined-sub", re.compile(r"^(?P<n>\S+) is not declared as a Subroutine\.$"), "n"),
    ("missing-include", re.compile(r"^Cannot open include file  (?P<n>.*)\.$", re.S), "n"),
)

_NAME_ROLES = (bindings.READ, bindings.WRITE, bindings.CALL, bindings.ARGUMENT,
               bindings.MEMBER_BASE, bindings.TABLE_REF, bindings.ALIAS_TARGET,
               bindings.UNITS_TARGET)
_VARIABLES = ("public", "dim", "static", "param", "alias")
_TABLES = ("datatable", "consttable")
_EXTERNAL = {"instruction", "function", "symbol"}


class Edit(NamedTuple):
    path: Path
    line: int                       # physical, 0-based
    start: int                      # decoded column, inclusive
    end: int                        # decoded column, exclusive
    new_text: str


@dataclass(frozen=True)
class CodeAction:
    title: str
    edits: tuple[Edit, ...]
    kind: str = "quickfix"
    family: str = ""


def edit_distance(a: str, b: str) -> int:
    """Damerau-Levenshtein distance (unrestricted transpositions), case-insensitive."""
    a, b = a.upper(), b.upper()
    if a == b:
        return 0
    inf = len(a) + len(b)
    last: dict[str, int] = {}
    d = [[inf] * (len(b) + 2) for _ in range(len(a) + 2)]
    for i in range(len(a) + 1):
        d[i + 1][0] = inf
        d[i + 1][1] = i
    for j in range(len(b) + 1):
        d[0][j + 1] = inf
        d[1][j + 1] = j
    for i in range(1, len(a) + 1):
        last_match = 0
        for j in range(1, len(b) + 1):
            i1 = last.get(b[j - 1], 0)
            j1 = last_match
            cost = 1
            if a[i - 1] == b[j - 1]:
                cost = 0
                last_match = j
            d[i + 1][j + 1] = min(d[i][j] + cost, d[i + 1][j] + 1, d[i][j + 1] + 1,
                                  d[i1][j1] + (i - i1 - 1) + 1 + (j - j1 - 1))
        last[a[i - 1]] = i
    return d[len(a) + 1][len(b) + 1]


def _max_distance(name: str) -> int:
    return 1 if len(name) <= 3 else 2


def apply_edits(data: bytes, edits) -> bytes:
    """`data` with `edits` (all for one file) applied; columns are Latin-1 characters of a line."""
    lines = data.split(b"\n")
    for e in sorted(edits, key=lambda e: (e.line, e.start), reverse=True):
        text = lines[e.line].decode("latin-1")
        lines[e.line] = (text[:e.start] + e.new_text + text[e.end:]).encode("latin-1")
    return b"\n".join(lines)


# ------------------------------------------------------------------ names

def _profile_name(profile) -> str:
    return profiles.get(profile if isinstance(profile, str) else profile.name).name


def _kind_ok(role: str, explicit_call: bool, decl) -> bool:
    kind = decl.external.kind if decl.kind == "external" and decl.external else decl.kind
    external = decl.kind == "external"
    if explicit_call:
        return kind == "sub" and not external
    if role == bindings.WRITE:
        return kind in _VARIABLES and not external
    if role == bindings.CALL:
        return kind in ("sub", "function", "instruction") or (external and kind == "function")
    if role == bindings.TABLE_REF:
        return kind in _TABLES
    if role == bindings.MEMBER_BASE:
        return kind in _VARIABLES + _TABLES or decl.kind == "system_table"
    if role in (bindings.ALIAS_TARGET, bindings.UNITS_TARGET):
        return kind in _VARIABLES
    read = kind in _VARIABLES + ("const", "function") or (external and kind == "symbol")
    if role == bindings.ARGUMENT:
        return read or kind in _TABLES
    return read                                                    # READ


def _pool(index: bindings.BindingIndex, catalog: InstructionCatalog) -> dict[str, str]:
    """upper-case name -> spelling: declared names (first spelling wins) and catalog names."""
    pool: dict[str, str] = {}
    for d in index.declarations:
        if d.kind in ("field", "structure_member", "label", "external", "system_table") \
                or d.scope in ("structure", "table"):
            continue
        pool.setdefault(d.name.upper(), d.name)
    used: dict[str, str] = {}               # a catalog name keeps the spelling the program uses
    for r in index.references:
        if len(r.resolved) == 1 and r.resolved[0].kind == "external":
            used.setdefault(r.name.upper(), r.name)
    for entry in catalog:
        if entry.kind in _EXTERNAL:
            pool.setdefault(entry.name.upper(), used.get(entry.name.upper(), entry.name))
    for extra in bindings._EXTRA_PREDEFINED:
        pool.setdefault(extra, extra)
    return pool


def suggest(index: bindings.BindingIndex, catalog: InstructionCatalog, ref: bindings.Reference,
            pool: dict[str, str] | None = None) -> list[str]:
    """Up to `MAX_ACTIONS` spellings for the unresolved reference `ref`: Damerau-Levenshtein <= 2
    (<= 1 for a name of up to 3 characters; a distance of 2 needs the same first letter),
    case-insensitive, nearest first, then user-declared
    before catalog names, then alphabetical; each must resolve at the use, with no ambiguity, to a
    declaration whose kind fits the reference's role."""
    pool = pool if pool is not None else _pool(index, catalog)
    name = ref.name
    limit = _max_distance(name)
    explicit = bool(getattr(ref.node, "explicit", False)) and ref.role == bindings.CALL
    ranked = []
    for upper, spelling in pool.items():
        if abs(len(upper) - len(name)) > limit or upper == name.upper():
            continue
        dist = edit_distance(name, spelling)
        if dist > limit or (dist == 2 and upper[0] != name[0].upper()):
            continue
        res = index.lookup(spelling, ref.routine, ref.role)
        if not res.candidates or res.ambiguity:
            continue
        if not all(_kind_ok(ref.role, explicit, c) for c in res.candidates):
            continue
        user = any(c.kind != "external" for c in res.candidates)
        if (not user and ref.role in (bindings.READ, bindings.ARGUMENT)
                and (catalog.get(spelling, "instruction") or catalog.get(spelling, "keyword")
                     or upper == "TO")):
            continue            # `Do`, `For`, `Scan`, `To`: a statement word, not the value meant
        ranked.append((dist, not user, upper, spelling))
    ranked.sort()
    return [r[3] for r in ranked[:MAX_ACTIONS]]


# ------------------------------------------------------------------ entry point

_cache: tuple | None = None            # (result, profile name, index, navigation)


def _tools(result, profile_name: str, index):
    global _cache
    if index is None and _cache is not None and _cache[0] is result and _cache[1] == profile_name:
        return _cache[2], _cache[3]
    ix = index if index is not None else bindings.build(result.program, profile_name)
    nav = navigation.navigation(result, ix)
    if index is None:
        _cache = (result, profile_name, ix, nav)
    return ix, nav


def _norm(path) -> str:
    return os.path.normcase(str(Path(path).absolute()))


def code_actions(result, path, diagnostic, profile, *, index=None,
                 source: SourceProvider | None = None) -> list[CodeAction]:
    """Quick fixes for `diagnostic` (an `AnalysisDiagnostic` of `result`) in the file `path`.
    Empty for any message outside `FAMILIES` or when no exact range exists. `profile` is a profile
    name or `Profile`. `source` (default: the disk plus `result.request.overlays`) is read for the
    include families. Never raises for a program."""
    try:
        return _code_actions(result, Path(path), diagnostic, _profile_name(profile), index, source)
    except Exception:
        return []


def _code_actions(result, path: Path, diag, profile_name: str, index, source) -> list[CodeAction]:
    message = diag.message
    family = name = None
    for fam, pattern, group in FAMILIES:
        m = pattern.match(message)
        if m:
            family, name = fam, (m.group(group) if group else None)
            break
    if family is None:
        return []
    if family == "missing-include":
        assert name is not None             # its pattern always captures the include spec
        return _include_actions(result, path, diag, name, source)
    if getattr(result, "program", None) is None:
        return []
    ix, nav = _tools(result, profile_name, index)
    if diag.line and diag.span is None:
        return []
    if diag.line and diag.path is not None and _norm(diag.path) != _norm(path):
        return []
    catalog = InstructionCatalog.for_profile(profile_name)
    pool = _pool(ix, catalog)
    nav._build()
    here = _norm(path)
    actions: list[CodeAction] = []
    seen: set = set()
    for ref in ix.references:
        if ref.status != "unresolved" or ref.role not in _NAME_ROLES:
            continue
        if name is not None and ref.name.upper() != name.upper():
            continue
        if family in ("undefined-sub", "undefined-call") and ref.role not in (
                bindings.CALL, bindings.WRITE):
            continue
        site = nav._site_of.get(id(ref))
        if site is None or site.path is None or _norm(site.path) != here:
            continue
        if diag.line and not (diag.span[0] - 1 <= site.line <= diag.span[1] - 1):
            continue
        for spelling in suggest(ix, catalog, ref, pool):
            edit = Edit(site.path, site.line, site.start, site.end, spelling)
            if edit in seen:
                continue
            seen.add(edit)
            actions.append(CodeAction(f"Change to `{spelling}`", (edit,), family=family))
    return actions


# ------------------------------------------------------------------ include family

_DRIVE = re.compile(r"^(?:CPU|USR|CRD|USB):", re.I)
_STRING = re.compile(r'"([^"]*)"')
_INCLUDE_EXTENSIONS = (".crb", ".cr6")


def _include_actions(result, path: Path, diag, spec: str, source) -> list[CodeAction]:
    if diag.path is None or _norm(diag.path) != _norm(path) or diag.span is None:
        return []
    src = source or OverlaySource(DiskSource(), getattr(result.request, "overlays", None))
    data = src.read(path)
    listing = src.list_dir(path.parent)
    if data is None or not listing:
        return []
    wanted = _DRIVE.sub("", spec.strip())
    if not wanted or "/" in wanted or "\\" in wanted:
        return []
    entry = Path(result.request.entry).absolute().name.lower()
    ranked = []
    for fname in listing:
        if fname.lower() == entry or not fname.lower().endswith(_INCLUDE_EXTENSIONS) \
                or fname == wanted:
            continue
        dist = edit_distance(wanted, fname)
        if dist <= _max_distance(wanted):
            ranked.append((dist, fname.upper(), fname))
    ranked.sort()
    if not ranked:
        return []
    lines = positions.physical_lines(data)
    actions: list[CodeAction] = []
    for lineno in range(diag.span[0], diag.span[1] + 1):
        if not 1 <= lineno <= len(lines):
            continue
        text = lines[lineno - 1].decode("latin-1")
        for m in _STRING.finditer(text):
            inner = m.group(1)
            if _DRIVE.sub("", inner.strip()) != wanted:
                continue
            at = m.start(1) + inner.find(wanted)
            if at < m.start(1):
                continue
            for _, _, fname in ranked[:MAX_ACTIONS]:
                actions.append(CodeAction(f"Change to `{fname}`",
                                          (Edit(path, lineno - 1, at, at + len(wanted), fname),),
                                          family="missing-include"))
            return actions
    return actions
