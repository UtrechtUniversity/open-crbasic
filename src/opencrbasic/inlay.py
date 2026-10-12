"""Parameter-name inlay hints, independent of any language-server protocol.

A hint is drawn before each positional argument of an instruction or builtin-function call, using
the parameter names `hover.signature_help` shows. Positions are physical: 0-based line and
decoded-character column (`docs/lsp-api.md` "Source positions"); the server converts encodings.
See `docs/lsp-api.md` "Inlay hints (LSP-09a)".
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from pathlib import Path

from . import lexer, positions
from . import syntax as S
from .hover import declarations, signature_help
from .navigation import path_keys

_UNITS_STATEMENT = re.compile(r"\s*Units\s+\w", re.I)

__all__ = ["InlayHint", "parameter_hints"]


@dataclass(frozen=True)
class InlayHint:
    line: int               # 0-based physical line
    column: int             # decoded-character column of the argument's first character
    label: str              # `Name:`
    kind: str = "parameter"
    padding_right: bool = True


class _Frame:
    __slots__ = ("sig", "index", "first", "last")

    def __init__(self, sig):
        self.sig = sig          # SignatureInfo of a call frame; None for grouping `(` and `{`
        self.index = 0
        self.first = None       # first and last token of the current argument
        self.last = None


def _occurrence_paths(program, result) -> dict[int, Path | None]:
    paths = {o.id: o.path for o in getattr(program, "occurrences", None) or []}
    if paths.get(0) is None:
        paths[0] = Path(result.request.entry).absolute()
    return paths


def _shift(unit, ll, seg: int) -> int | None:
    """Offset of `unit.text` within `ll.text[seg:]` (a `Units` text is stripped), or None."""
    tail = ll.text[seg:]
    for cand in (0, len(tail) - len(tail.lstrip())):
        if ll.text.startswith(unit.text, seg + cand):
            return cand
    return None


def parameter_hints(result, path, start_line: int, end_line: int, profile,
                    help_provider=None) -> list[InlayHint]:
    """Hints for the calls of `path` (a file of the analysis) on physical lines
    `start_line..end_line`, both 0-based and inclusive, sorted by position.

    A hint needs the call's help names to pair with the catalog positions (`SignatureInfo.paired`),
    so none appear without a help provider. No hint for an empty argument, for an argument whose
    text already equals the parameter name (case-insensitive), past the parameter count, or when the
    argument's first character has no exact single-piece physical position. Never raises."""
    try:
        return _hints(result, path, start_line, end_line, profile, help_provider)
    except Exception:
        return []


def _hints(result, path, start_line, end_line, profile, help_provider) -> list[InlayHint]:
    program = getattr(result, "program", None)
    statements = getattr(program, "statements", None)
    if not statements or help_provider is None:
        return []
    keys = path_keys(path)
    paths = _occurrence_paths(program, result)
    declared = declarations(program)
    sigs: dict[tuple[str, bool], object] = {}
    found: dict[tuple[int, int], InlayHint] = {}

    def signature(name: str, instruction: bool):
        key = (name.upper(), instruction)
        if key not in sigs:
            sig = None if name.upper() in declared else signature_help(name, 0, profile, help_provider)
            if sig is not None and (sig.kind == "instruction") != instruction:
                sig = None
            sigs[key] = sig if sig is not None and sig.paired and sig.parameters else None
        return sigs[key]

    for stmt in statements:
        text = getattr(stmt, "text", "")
        origin = getattr(stmt, "origin", None)
        if origin is None or "(" not in text or _UNITS_STATEMENT.match(text):
            continue
        ll, seg = origin
        shift = _shift(stmt, ll, seg)
        if shift is None:
            continue
        try:
            toks = lexer.tokenize(text, stmt.line)
        except lexer.LexError:
            continue

        def emit(first, last, frame) -> None:
            if first is None or frame.index >= len(frame.sig.parameters):
                return
            label = frame.sig.parameters[frame.index].label
            written = text[first.col:last.col + len(last.text)]
            if written.strip().lower() == label.lower():
                return
            start = seg + shift + first.col
            r = positions.exact_range(ll, start, start + len(first.text))
            if r is None or r.end_col <= r.start_col or not start_line <= r.line - 1 <= end_line:
                return
            p = paths.get(r.occurrence)
            if p is None or not (path_keys(p) & keys):
                return
            found.setdefault((r.line - 1, r.start_col),
                             InlayHint(r.line - 1, r.start_col, label + ":"))

        stack: list[_Frame] = []
        for i, t in enumerate(toks):
            if t.kind == "EOF":
                break
            top = stack[-1] if stack else None
            closing = t.kind == "OP" and t.text in (")", "}")
            comma = t.kind == "OP" and t.text == ","
            if top is not None and top.sig is not None:
                if comma:
                    emit(top.first, top.last, top)
                    top.index += 1
                    top.first = top.last = None
                    continue
                if closing:
                    emit(top.first, top.last, top)
                    stack.pop()
                    # the closing token belongs to the enclosing argument
                    if stack and stack[-1].sig is not None:
                        _extend(stack[-1], t)
                    continue
                _extend(top, t)
            elif closing and top is not None:
                stack.pop()
                if stack and stack[-1].sig is not None:
                    _extend(stack[-1], t)
                continue
            if t.kind == "OP" and t.text in ("(", "{"):
                sig = None
                if t.text == "(" and i and toks[i - 1].kind == "IDENT" and not (
                        i > 1 and toks[i - 2].kind == "OP" and toks[i - 2].text == "."):
                    name = toks[i - 1]
                    first_in_stmt = i == 1 and isinstance(stmt, S.Instruction) \
                        and stmt.keyword == name.upper
                    sig = signature(name.text, first_in_stmt)
                stack.append(_Frame(sig))
        for frame in stack:                         # an unclosed call ends at the end of the line
            if frame.sig is not None:
                emit(frame.first, frame.last, frame)
    return sorted(found.values(), key=lambda h: (h.line, h.column))


def _extend(frame: _Frame, tok) -> None:
    if frame.first is None:
        frame.first = tok
    frame.last = tok
