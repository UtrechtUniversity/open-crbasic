"""Hover and signature-help logic, independent of any language-server protocol.

Positions in and out are physical: 0-based line, decoded-character column (the convention of
`docs/lsp-api.md` "Source positions"); the server converts encodings. Help text is display
information only: it never changes a checker rule. See `docs/lsp-api.md` "Hover and signature
help (LSP-04b-core)".
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from . import positions, profiles
from . import syntax as S
from .bindings import closes_routine
from .catalog import CatalogEntry, InstructionCatalog
from .checker.expressions import (_ARITY_ALLOWED, _ARITY_MAX_SAFE, _ARITY_MIN_SAFE,
                                  _ARITY_RANGE)
from .editing import _declared_name_arg
from .helpdocs import HelpProvider, InstructionHelp, pair_signature
from .helpdocs import _md
from .source import _is_continuation, _strip_comment

__all__ = [
    "CallContext", "call_context", "call_stack", "ParameterInfo", "SignatureInfo", "signature_help",
    "Declaration", "declarations", "Hover", "hover",
]

def _esc(text: str) -> str:
    """Markdown-escaped text on one line (help descriptions keep their source line breaks)."""
    return _md(" ".join(text.split()))


_TYPE_CODES_PATH = Path(__file__).resolve().parent / "data" / "type_codes.json"

# Words that may be followed by `(` without being a call: operators, the words the parser
# dispatches on (`If (a) Then`, `While (a)`, ...). Grammar words, not a catalog subset.
_NOT_CALLS = frozenset({
    "AND", "OR", "NOT", "XOR", "MOD", "EQV", "IMP", "TO", "IS", "THEN", "STEP", "AS",
    "IF", "ELSEIF", "WHILE", "UNTIL", "SELECT", "CASE", "RETURN",
})

_CALL_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\$?(?:\.[A-Za-z0-9_]+\$?)*\s*$")
_WORD_RE = re.compile(r"(?<![A-Za-z0-9_$.])[A-Za-z_][A-Za-z0-9_]*\$?")
_RADIX_RE = re.compile(r"&[HhBb][0-9A-Fa-f]+")


# ---------------------------------------------------------------------------- text scanning


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").split("\n")


class _Frame:
    __slots__ = ("name", "line", "col", "commas")

    def __init__(self, name: str | None, line: int, col: int):
        self.name = name        # None: grouping parenthesis or `{`
        self.line = line
        self.col = col
        self.commas = 0


class _Scan:
    """Character scanner over physical lines: strings (`""` escape), `'` comments, and a stack of
    open `(`/`{` with the top-level comma count of each."""

    def __init__(self) -> None:
        self.stack: list[_Frame] = []
        self.state = "code"     # state at the end of the last fed text: code | string | comment

    def feed(self, lineno: int, text: str, end: int) -> None:
        """Scan `text[:end]`; a string still open at the end of the previous fed line stays open
        (the loader continues a string across `_`), a comment does not."""
        if self.state == "comment":
            self.state = "code"
        i, n = 0, min(end, len(text))
        while i < n:
            ch = text[i]
            if self.state == "string":
                if ch == '"':
                    if text[i + 1:i + 2] == '"':
                        i += 2
                        continue
                    self.state = "code"
                i += 1
                continue
            if ch == '"':
                self.state = "string"
            elif ch == "'":
                self.state = "comment"
                return
            elif ch == "(":
                m = _CALL_NAME_RE.search(text, 0, i)
                name = m.group(0).rstrip() if m else None
                if name is not None and name.upper() in _NOT_CALLS:
                    name = None
                self.stack.append(_Frame(name, lineno, m.start() if m and name else i))
            elif ch == "{":
                self.stack.append(_Frame(None, lineno, i))
            elif ch in ")}":
                if self.stack:
                    self.stack.pop()
            elif ch == "," and self.stack:
                self.stack[-1].commas += 1
            i += 1


@dataclass(frozen=True)
class CallContext:
    name: str           # as written; may be dotted (`Table.Field`)
    active: int         # 0-based argument index
    line: int           # physical line / column where the name starts
    column: int


def _statement_scan(lines: list[str], line: int, column: int) -> _Scan | None:
    """The scanner after feeding the statement (the physical line joined with its `_`
    continuations) up to `column` of `line`; None when the position lies in a comment."""
    first = line
    while first > 0 and _is_continuation(_strip_comment(lines[first - 1]))[0]:
        first -= 1
    scan = _Scan()
    for k in range(first, line + 1):
        scan.feed(k, lines[k], len(lines[k]) if k < line else column)
        if scan.state == "comment" and k == line:
            return None
    return scan


def call_stack(text: str, line: int, column: int) -> list[CallContext]:
    """Every named call enclosing the position, innermost first, each with the argument the
    position is in (grouping parentheses and `{}` are skipped). Same scanning rules as
    `call_context`; an array index or user Sub call nested in an instruction call is a frame of
    its own, so the caller can fall through to the enclosing instruction."""
    lines = _lines(text)
    if not 0 <= line < len(lines) or column < 0:
        return []
    scan = _statement_scan(lines, line, column)
    if scan is None:
        return []
    return [CallContext(f.name, f.commas, f.line, f.col) for f in reversed(scan.stack) if f.name]


def call_context(text: str, line: int, column: int) -> CallContext | None:
    """The innermost call enclosing the position and the argument the position is in.

    The statement is the physical line joined with its `_` continuations; strings (also across a
    `_`), `'` comments, nested parentheses and `{}` are respected, and an incomplete call (no `)`,
    trailing comma, unterminated string) still answers. None outside any call, in a comment, or
    for a position outside the text. `call_stack` gives the enclosing calls as well."""
    stack = call_stack(text, line, column)
    return stack[0] if stack else None


def _word_at(lines: list[str], line: int, column: int) -> tuple[str, int, int] | None:
    """The identifier at the position (or ending at it) when it lies in code."""
    if not 0 <= line < len(lines) or column < 0:
        return None
    text = lines[line]
    radix = [m.span() for m in _RADIX_RE.finditer(text)]
    found = None
    for m in _WORD_RE.finditer(text):
        s, e = m.span()
        if any(a <= s < b for a, b in radix):
            continue
        if s <= column < e or (e == column and found is None):
            found = (m.group(0), s, e)
            if s <= column < e:
                break
    if found is None:
        return None
    scan = _statement_scan(lines, line, found[1])
    if scan is None or scan.state != "code":
        return None
    return found


# ---------------------------------------------------------------------------- type-code kinds


@lru_cache(maxsize=1)
def _type_code_kinds() -> dict[int, str]:
    data = json.loads(_TYPE_CODES_PATH.read_text())
    return {int(code): entry["kind"] for code, entry in data.items()}


def _kind_of(code: int) -> tuple[str | None, bool]:
    """(`type_codes.json` kind, optional) of a catalog parameter code; codes >= 100 are the
    optional form of code-100 (docs/type-codes.md)."""
    base = code - 100 if code >= 100 else code
    return _type_code_kinds().get(base), code >= 100


# ---------------------------------------------------------------------------- signature help


@dataclass(frozen=True)
class ParameterInfo:
    label: str                      # help name when paired, else `argN`
    label_span: tuple[int, int]     # offsets of the parameter in `SignatureInfo.label`
    documentation: str              # Markdown; "" when none
    type_code: int | None           # catalog code (instructions); None for functions
    kind: str | None                # `type_codes.json` kind of the code; None for functions
    optional: bool                  # catalog code >= 100, or the help syntax says optional


@dataclass(frozen=True)
class SignatureInfo:
    name: str                       # the profile's spelling
    kind: str                       # "instruction" | "function"
    label: str                      # `Name(p1, p2)`
    documentation: str              # Markdown: summary, help syntax, mismatch note, provenance
    parameters: tuple[ParameterInfo, ...]
    active_parameter: int | None    # clamped; None when there are no parameters
    paired: bool                    # parameter names come from help
    catalog_count: int | None
    help_count: int | None          # None: no help
    note: str                       # why names are not paired ("" when paired)
    help_source: Path | None


def _catalog(profile) -> InstructionCatalog:
    return InstructionCatalog.for_profile(profile if isinstance(profile, str) else profile.name)


def _provenance(help: InstructionHelp) -> str:
    p = help.source_path
    return f"Source: help page `{p.parent.name}/{p.name}` (local Campbell Scientific documentation)."


def _callable_entry(cat: InstructionCatalog, name: str) -> CatalogEntry | None:
    return cat.get(name, "instruction") or cat.get(name, "function")


def _accepted_counts(name: str, nargs: int) -> tuple[int, ...] | None:
    """Argument counts the checker accepts for a builtin function whose table has `nargs`, from its
    own tables (`checker/expressions.py`, `_call_arity_problem`); None when those tables do not bound
    the count both ways. `nargs` is not a universal bound (`_ARITY_ALLOWED`, `_ARITY_RANGE`)."""
    upper = name.upper()
    if allowed := _ARITY_ALLOWED.get(upper, {}).get(nargs):
        return tuple(sorted(allowed))
    if bounds := _ARITY_RANGE.get(upper, {}).get(nargs):
        return tuple(range(bounds[0], bounds[1] + 1))
    if _ARITY_MIN_SAFE.get(upper) == nargs and _ARITY_MAX_SAFE.get(upper) == nargs:
        return (nargs,)
    return None


def _count_text(counts: tuple[int, ...]) -> str:
    """`6, 10 or 11` for (6, 10, 11)."""
    words = [str(c) for c in counts]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} or {words[-1]}"


# Explicit placeholders when no help text is available (human instruction, LSP-04e). Never inferred.
_HELP_TODO = "Help: still to do (No help page for this name)."
_HELP_OFF = "Help: not configured (No help directory configured)."


def _no_help_line(help_configured: bool) -> str:
    return _HELP_TODO if help_configured else _HELP_OFF


def _accepted_note(accepted: tuple[int, ...]) -> str:
    return (f"Accepted argument counts (checker): {_count_text(accepted)}. "
            f"Parameters after the first {accepted[0]} are optional.")


def _build_signature(entry: CatalogEntry, help: InstructionHelp | None, active: int,
                     help_configured: bool = True) -> SignatureInfo | None:
    accepted: tuple[int, ...] | None = None
    if entry.kind == "instruction":
        codes = tuple(entry.params)
        kinds = [_kind_of(c) for c in codes]
        paired = pair_signature(entry.name, codes, help)
        count = len(codes)
        type_codes: list[int | None] = list(codes)
    else:
        # Help names apply when the largest count the checker accepts is the help parameter count
        # (FTPClient: 6, 10 or 11 against 11 help names). Parameters past the smallest accepted count
        # are optional; the accepted counts are stated in the documentation.
        if entry.nargs is not None:
            accepted = _accepted_counts(entry.name, entry.nargs)
        if help is not None and accepted and len(help.params) == accepted[-1]:
            count = accepted[-1]
        else:
            accepted = None
            count = entry.nargs if entry.nargs is not None else (len(help.params) if help and help.params else None)
        if count is None:
            return None
        kinds = [(None, False)] * count
        paired = pair_signature(entry.name, (0,) * count, help)
        if accepted:
            paired = dataclasses.replace(paired, params=tuple(
                dataclasses.replace(p, optional=True) if i >= accepted[0] else p
                for i, p in enumerate(paired.params)))
        type_codes = [None] * count
    labels, spans, pos = [], [], len(entry.name) + 1
    params = []
    for i, p in enumerate(paired.params):
        kind, cat_optional = kinds[i] if i < len(kinds) else (None, False)
        type_code = type_codes[i] if i < len(type_codes) else None
        piece = p.label + (" (optional)" if p.optional or cat_optional else "")
        spans.append((pos, pos + len(piece)))
        pos += len(piece) + 2
        labels.append(piece)
        params.append(ParameterInfo(p.label, spans[-1], _esc(p.description) if p.from_help else "",
                                    type_code, kind, p.optional or cat_optional))
    label = f"{entry.name}({', '.join(labels)})"
    doc: list[str] = []
    if help is not None:
        if help.summary:
            doc.append(_esc(help.summary))
        if not paired.matched:
            doc.append(_mismatch(help, paired.note))
        doc.append(_provenance(help))
    else:
        doc.append(_no_help_line(help_configured))
    if accepted:
        doc.append(_accepted_note(accepted))
    elif entry.kind == "function" and entry.nargs is not None:
        doc.append("Argument count from the function table; a trailing argument may be optional.")
    clamped = None if count == 0 else max(0, min(active, count - 1))
    return SignatureInfo(entry.name, entry.kind, label, "\n\n".join(doc), tuple(params), clamped,
                         paired.matched, count, paired.help_count, paired.note,
                         help.source_path if help else None)


def _mismatch(help: InstructionHelp, note: str) -> str:
    syntax = f"Help syntax: `{help.syntax}`. " if help.syntax else ""
    return f"{syntax}{note[:1].upper()}{note[1:]}; help parameter names are not applied to the catalog positions."


def signature_help(name: str, active: int, profile, help_provider: HelpProvider | None = None
                   ) -> SignatureInfo | None:
    """Signature of an instruction or builtin function for the profile (name or `Profile`), with
    parameter names and text from help when its parameter count equals the catalog's, otherwise
    `argN` labels from the catalog count. None for a name that is neither (including dotted
    names), or a function whose argument count is unknown."""
    entry = _callable_entry(_catalog(profile), name)
    if entry is None:
        return None
    help = help_provider.lookup(entry.name) if help_provider is not None else None
    return _build_signature(entry, help, active, help_configured=help_provider is not None)


# ---------------------------------------------------------------------------- declarations


@dataclass(frozen=True)
class Declaration:
    name: str
    kind: str                       # Public | Dim | Static | Const | Sub | Function | DataTable |
                                    # ConstTable | Alias
    type: str | None = None         # as written (upper case); None: no `As` written
    dims: tuple[str, ...] = ()
    string_size: str | None = None
    units: str | None = None
    value: str | None = None        # Const: resolved value when known, else the written expression
    detail: str = ""                # Sub/Function: parameter list; Alias: the aliased name
    scope: str | None = None        # enclosing Sub/Function name, None: top level
    file: str = ""                  # compiler display name
    line: int = 0                   # reported line
    physical_line: int | None = None  # first physical line of the statement, when known


def _expr_text(e, depth: int = 0) -> str:
    if e is None:
        return ""
    if depth > 6:
        return "..."
    if isinstance(e, S.Number):
        return e.text or str(e.value)
    if isinstance(e, S.String):
        return '"' + e.value.replace('"', '""') + '"'
    if isinstance(e, S.Name):
        return e.name
    if isinstance(e, S.Index):
        return f"{e.name}({', '.join(_expr_text(a, depth + 1) for a in e.args)})"
    if isinstance(e, S.Member):
        base = _expr_text(e.base, depth + 1)
        args = "" if e.args is None else f"({', '.join(_expr_text(a, depth + 1) for a in e.args)})"
        return f"{base}.{e.member}{args}"
    if isinstance(e, S.Unary):
        return f"{e.op} {_expr_text(e.operand, depth + 1)}" if e.op.isalpha() else f"{e.op}{_expr_text(e.operand, depth + 1)}"
    if isinstance(e, S.Binary):
        return f"{_expr_text(e.left, depth + 1)} {e.op} {_expr_text(e.right, depth + 1)}"
    if isinstance(e, S.ArrayLiteral):
        return "{" + ", ".join(_expr_text(a, depth + 1) for a in e.items) + "}"
    return "..."


def _target_name(e) -> str | None:
    if isinstance(e, S.Name):
        return e.name
    if isinstance(e, S.Index):
        return e.name
    return None


def declarations(program) -> dict[str, list[Declaration]]:
    """Declared names of a parsed program (`AnalysisResult.program`; None gives `{}`), keyed by
    upper-case name, in source order. Covers `Public`/`Dim`/`Static`, `Const`, `Sub`/`Function`,
    `DataTable`/`ConstTable` names and `Alias` names, with `Units` labels attached. Scope is the
    enclosing Sub/Function name only; the same name can be declared in several scopes."""
    out: dict[str, list[Declaration]] = {}
    statements = getattr(program, "statements", None)
    if not statements:
        return out
    line_map = getattr(program, "line_map", None)
    consts = getattr(program, "consts", {}) or {}
    units: dict[str, str] = {}
    scope: str | None = None

    def where(st) -> tuple[str, int, int | None]:
        file = st.file or getattr(program, "name", "")
        span = positions.line_span(line_map, file, st.line) if line_map else None
        return file, st.line, span[0] if span else None

    def add(name: str, st, kind: str, **kw) -> None:
        file, line, phys = where(st)
        out.setdefault(name.upper(), []).append(
            Declaration(name, kind, scope=kw.pop("scope", scope), file=file, line=line,
                        physical_line=phys, **kw))

    for st in statements:
        if isinstance(st, S.Declaration):
            for v in st.vars:
                add(v.name, st, st.kind.capitalize(), type=v.type,
                    dims=tuple(_expr_text(d) for d in v.dims),
                    string_size=_expr_text(v.string_size) if v.string_size is not None else None,
                    units=v.units)
        elif isinstance(st, S.ConstDecl):
            known = consts.get(st.name.upper())
            add(st.name, st, "Const", type=st.type,
                value=str(known) if known is not None else _expr_text(st.value))
        elif isinstance(st, S.SubDef):
            detail = ", ".join(
                ("Optional " if p.default is not None else "") + p.name
                + ("()" if p.dims else "") + (f" As {p.type}" if p.type else "") for p in st.params)
            add(st.name, st, st.keyword.capitalize(), type=st.return_type, detail=detail, scope=None)
            scope = st.name
        elif isinstance(st, S.Instruction):
            kw = st.keyword
            if closes_routine(st):
                scope = None
            elif kw in ("DATATABLE", "CONSTTABLE") and st.args and (
                    table := _declared_name_arg(st.args[0], kw)):
                add(table, st, "DataTable" if kw == "DATATABLE" else "ConstTable")
            elif kw == "UNITS" and len(st.args) == 2 and isinstance(st.args[1], S.String):
                target = _target_name(st.args[0])
                if target:
                    units.setdefault(target.upper(), st.args[1].value)
            elif kw == "ALIAS" and st.args:
                source = _expr_text(st.args[0])
                for a in st.args[1:]:
                    alias = _target_name(a)
                    if alias:
                        add(alias, st, "Alias", detail=source)
    for entries in out.values():
        for k, d in enumerate(entries):
            u = units.get(d.name.upper())
            if u is not None and d.units is None and d.kind in ("Public", "Dim", "Static", "Alias"):
                entries[k] = dataclasses.replace(d, units=u)
    return out


def _declaration_markdown(decls: list[Declaration]) -> str:
    parts = []
    for d in decls[:5]:
        dims = f"({', '.join(d.dims)})" if d.dims else ""
        if d.kind in ("Sub", "Function"):
            tail = f" As {d.type}" if d.type else ""
            code = f"{d.kind} {d.name}({d.detail}){tail}"
            lines = []
        elif d.kind == "Const":
            code = f"Const {d.name} = {d.value}"
            lines = [f"Type: {d.type}"] if d.type else []
        elif d.kind in ("DataTable", "ConstTable"):
            code = f"{d.kind}({d.name}, ...)"
            lines = []
        elif d.kind == "Alias":
            code = f"Alias {d.detail} = {d.name}"
            lines = []
        else:
            size = f" * {d.string_size}" if d.string_size else ""
            code = f"{d.kind} {d.name}{dims}" + (f" As {d.type}{size}" if d.type else "")
            lines = [] if d.type else ["Type: none written (Float)"]
        if d.kind in ("Public", "Dim", "Static") and d.dims:
            lines.append(f"Dimensions: {', '.join(d.dims)}")
        if d.units:
            lines.append(f"Units: {_esc(d.units)}")
        where = f"Declared in `{d.file}`, line {d.physical_line or d.line}"
        if d.scope:
            where += f", local to `{d.scope}`"
        lines.append(where + ".")
        parts.append("\n".join([f"**{_esc(d.name)}** ({d.kind})", "", "```crbasic", code, "```", "", *lines]))
    if len(decls) > 5:
        parts.append(f"{len(decls) - 5} more declaration(s) of this name.")
    if len(decls) > 1:
        parts.insert(0, "Declared more than once; scope is not resolved at hover.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------- hover


@dataclass(frozen=True)
class Hover:
    markdown: str
    line: int           # physical line and decoded columns of the word
    start_column: int
    end_column: int


def _callable_markdown(entry: CatalogEntry, help: InstructionHelp | None, help_configured: bool,
                       others: tuple[CatalogEntry, ...]) -> str:
    sig = _build_signature(entry, help, 0, help_configured)
    label = entry.kind
    if any(e.kind == "keyword" for e in others):
        label = "statement keyword" if entry.kind == "instruction" else label
    head = f"**{_esc(help.title)}**" if help is not None else f"**{_esc(entry.name)}** ({label})"
    out = [head]
    if sig is None:
        out += ["", f"Function table row: code {entry.code}, kind {entry.func_kind}, argspec {entry.argspec}; "
                    "no argument count in the table."]
    else:
        out += ["", "```crbasic", sig.label, "```"]
        if help is not None and help.summary:
            out += ["", _esc(help.summary)]
        if help is not None and not sig.paired:
            out += ["", _mismatch(help, sig.note)]
        rows = []
        for p in sig.parameters:
            info = ", ".join(x for x in (p.kind, "optional" if p.optional else None) if x)
            text = f"- `{p.label}`" + (f" ({info})" if info else "")
            rows.append(text + (f": {p.documentation}" if p.documentation else ""))
        if rows:
            out += [""] + rows
        accepted = (_accepted_counts(entry.name, entry.nargs)
                    if entry.kind == "function" and entry.nargs is not None else None)
        if accepted and sig.paired:
            out += ["", _accepted_note(accepted)]
        elif entry.kind == "function" and entry.nargs is not None:
            out += ["", "Argument count from the function table; a trailing argument may be optional."]
    if help is not None:
        out += ["", _provenance(help)]
    else:
        out += ["", _no_help_line(help_configured)]
    return "\n".join(out)


def _simple_markdown(entry: CatalogEntry) -> str:
    if entry.kind == "symbol":
        return f"**{_esc(entry.name)}** (predefined constant)\n\n```crbasic\n{entry.name} = {entry.value}\n```"
    return f"**{_esc(entry.name)}** (CRBasic keyword)"


def hover(result, text: str, line: int, column: int, profile,
          help_provider: HelpProvider | None = None,
          declared: Mapping[str, list[Declaration]] | None = None) -> Hover | None:
    """Markdown for the word at the position, or None.

    `result` is an `AnalysisResult` (or None): its AST gives declared names unless `declared`
    (upper-case name -> declarations, e.g. from LSP-05a bindings) is passed. Words in comments
    and strings, numbers, and member names after `.` give None. Declared names win over catalog
    names; catalog names resolve instruction, function, predefined constant, keyword."""
    word = _word_at(_lines(text), line, column)
    if word is None:
        return None
    name, start, end = word
    decls = declared if declared is not None else declarations(getattr(result, "program", None))
    found = decls.get(name.upper())
    if found:
        return Hover(_declaration_markdown(list(found)), line, start, end)
    cat = _catalog(profile)
    entry = cat.get(name)
    if entry is None:
        return None
    others = tuple(e for e in cat.entries(name) if e is not entry)
    callable_entry = _callable_entry(cat, name)
    if callable_entry is not None:
        help = help_provider.lookup(callable_entry.name) if help_provider is not None else None
        md = _callable_markdown(callable_entry, help, help_provider is not None,
                                cat.entries(name))
        sym = cat.get(name, "symbol")
        if sym is not None:
            md += f"\n\nAlso a predefined constant: {sym.name} = {sym.value}."
    else:
        md = _simple_markdown(entry)
    return Hover(md, line, start, end)
