"""Indentation formatter for editor tooling (LSP-07b), kept free of any protocol library.

`format_indentation` returns edits that replace the *leading whitespace* of physical lines and
nothing else: no text after the first non-blank character changes, the line count and the line
endings stay, and no physical line is made longer than the loader's 510-character buffer allows.
The language server converts a `TextEdit` to an LSP edit like a `editing.Span`. Rules, refusals and
the preservation evidence: `docs/lsp-api.md` "Formatting (LSP-07b)" and
`docs/lsp-rounds/LSP-07b-handoff.md`.

Block words are not listed here: the structure comes from `editing` (`_roles`, `_classify`,
`_block_events`), the same classification folding and the outline use.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple

from . import lexer, source
from .editing import _block_events, _classify, _roles, _tolerant_tokens
from .parser import _DIRECTIVE_RE, _split_text_segments

__all__ = ["TextEdit", "apply_edits", "format_indentation", "format_range", "format_refusal"]

_MID_WORDS = frozenset({"CASE", "ELSE", "ELSEIF"})
_CASE = "CASE"          # a virtual frame: the body of one `Case` of a `Select Case`
_IMPLICIT_END = "SLOWSEQUENCE"      # editing._IMPLICIT_END: ends at the next one or at EndProg


@dataclass(frozen=True)
class TextEdit:
    """Replace `text[line][start:end]` by `new_text`. `line` is 0-based; columns are decoded
    (Latin-1) columns, i.e. byte offsets into the physical line, before LSP position conversion.
    Every edit of this module has `start == 0`."""
    line: int
    start: int
    end: int
    new_text: str


def apply_edits(text: str, edits: list[TextEdit]) -> str:
    """`text` with `edits` applied (edits must not overlap; any order)."""
    lines = text.split("\n")
    for e in edits:
        lines[e.line] = lines[e.line][:e.start] + e.new_text + lines[e.line][e.end:]
    return "\n".join(lines)


def format_refusal(result) -> str | None:
    """Why formatting must not be offered for the file behind an `AnalysisResult` (or
    `CompileResult`): it has errors. None: formatting may be offered. Formatting itself works on
    text alone and never reads a result; this is the policy gate for the server."""
    if getattr(result, "implementation_failures", None) or getattr(
            result, "implementation_exceptions", None):
        return "the analysis failed internally"
    if not result.ok:
        return "the program has errors; fix them before formatting"
    return None


# ------------------------------------------------------------------ the plan


class _Frame(NamedTuple):
    family: str
    last: int       # last physical line of the opener statement (0-based)


class _Dir(NamedTuple):
    base: list          # block stack at the `#If`
    ends: list          # block stack at the end of each earlier branch
    first: int
    last: int
    has_else: bool


class _Group(NamedTuple):
    first: int
    last: int
    level: int
    enter: int          # block depth before the statement
    directive: bool
    skip: bool          # edits suppressed (the statement is left alone)


def _load(text: str) -> tuple[list[source.LogicalLine], int | None]:
    """The loader's logical lines (includes unresolved) up to the first overflow line, and that
    line (0-based; the loader, and the compiler, stop reading there)."""
    lines, _ = source.load_text(text, "t", directory=None)
    out = []
    for ll in lines:
        if ll.phys is None:
            continue
        if ll.kind == "overflow":
            return out, ll.phys[0] - 1
        if ll.kind in ("code", "include_warning", "include_error"):
            out.append(ll)
    return out, None


def _phys(ll: source.LogicalLine) -> tuple[int, int]:
    """The physical span of a logical line, which `source.load_text` sets on every line it makes."""
    assert ll.phys is not None
    return ll.phys


def _inside_string(ll: source.LogicalLine) -> bool:
    """True when the text cannot be read as tokens, or a continuation line starts inside a string
    (changing its leading blanks would change the string)."""
    try:
        tokens = lexer.tokenize(ll.text)
    except lexer.LexError as e:
        if e.message == "unterminated string" or _phys(ll)[1] > _phys(ll)[0]:
            return True
        return False
    strings = [(t.col, t.col + len(t.text)) for t in tokens if t.kind == "STRING"]
    for piece in ll.pieces:
        if piece.line > _phys(ll)[0] and piece.col == 0:
            if any(a < piece.start < b for a, b in strings):
                return True
    return False


def _plan(text: str, profile: str) -> tuple[dict[int, int], int]:
    """`({0-based physical line: indent level}, exclusive line limit)`: the level every editable
    line should have, and the first line from which on nothing is edited."""
    roles = _roles(profile)
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    logical, overflow = _load(text)
    events: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for role, family, _name, first, _last in _block_events(text, profile):
        if family != "#IF":
            events[first].append((role, family))

    limit = len(lines) if overflow is None else overflow
    stack: list[_Frame] = []
    dirs: list[_Dir] = []
    groups: list[_Group] = []

    def drop(k: int) -> None:
        nonlocal limit
        for f in stack[k + 1:]:
            if f.family not in (_IMPLICIT_END, _CASE):
                limit = min(limit, f.last + 1)      # never closed: nothing after it is edited
        del stack[k:]

    def close(family: str, first: int) -> None:
        nonlocal limit
        for k in range(len(stack) - 1, -1, -1):
            if stack[k].family == family:
                drop(k)
                return
        limit = min(limit, first)                   # a closer with no opener

    halted = False
    for ll in logical:
        first, last = _phys(ll)[0] - 1, _phys(ll)[1] - 1
        if halted or first >= limit:
            break
        enter = len(stack)
        d = _DIRECTIVE_RE.match(ll.text) if ll.kind == "code" else None
        if d:
            kind = d.group(1).upper()
            if kind in ("IF", "IF_NO_REMOVE"):
                dirs.append(_Dir(list(stack), [], first, last, False))
            elif kind in ("ELSE", "ELSEIF") and dirs:
                top = dirs.pop()
                top.ends.append(list(stack))
                stack[:] = top.base
                dirs.append(top._replace(has_else=top.has_else or kind == "ELSE"))
            elif kind == "ENDIF" and dirs:
                top = dirs.pop()
                ends = top.ends + [list(stack)]
                if not top.has_else:
                    ends.append(top.base)
                if all([f.family for f in e] == [f.family for f in ends[0]] for e in ends):
                    stack[:] = ends[-1]
                else:
                    limit = min(limit, last + 1)    # branches leave different blocks open
            groups.append(_Group(first, last, 0, enter, True, False))
            continue
        evs = [e for e in events.get(first, [])]
        segs = _split_text_segments(ll.text) if ll.kind == "code" else []
        toks = [t for t in _tolerant_tokens(segs[0]) if t.kind != "EOF"] if segs else []
        word = toks[0].upper if toks and toks[0].kind == "IDENT" else ""
        second = toks[1].upper if len(toks) > 1 and toks[1].kind == "IDENT" else None
        lead = _classify(word, second, roles) if word else None
        if word == "CASE":
            if stack and stack[-1].family == _CASE:
                stack.pop()
            level = len(stack)
            if stack and stack[-1].family == "SELECT":
                stack.append(_Frame(_CASE, last))
        elif word in ("ELSE", "ELSEIF"):
            level = len(stack) - 1 if stack and stack[-1].family == "IF" else len(stack)
        else:
            if lead and lead[0] == "close" and evs and evs[0] == lead:
                close(lead[1], first)
                evs = evs[1:]
            elif lead == ("open", _IMPLICIT_END):
                for k in range(len(stack) - 1, -1, -1):
                    if stack[k].family == _IMPLICIT_END:
                        drop(k)
                        break
            level = len(stack)
        for role, family in evs:
            if role == "open":
                if family == _IMPLICIT_END:
                    for k in range(len(stack) - 1, -1, -1):
                        if stack[k].family == _IMPLICIT_END:
                            drop(k)
                            break
                stack.append(_Frame(family, last))
            else:
                close(family, first)
        if ll.kind == "code" and not dirs and not stack and lead == ("close", "BEGINPROG"):
            halted = True                           # the compiler stops reading at EndProg
            limit = min(limit, last + 1)
        groups.append(_Group(first, last, level, enter, False, _inside_string(ll)
                             if ll.kind == "code" else False))
    for f in stack:
        if f.family not in (_IMPLICIT_END, _CASE):
            limit = min(limit, f.last + 1)          # still open at the end of the text
    for dr in dirs:
        limit = min(limit, dr.last + 1)

    levels: dict[int, int] = {}
    for g in groups:
        if g.skip:
            continue
        levels[g.first] = g.level
        for k in range(g.first + 1, g.last + 1):
            levels[k] = g.level + 1                 # continuation lines (and pulled lines)
    levels = {n: v for n, v in levels.items() if n < limit}
    covered = {k for g in groups for k in range(g.first, g.last + 1)}
    starts = [g.first for g in groups]
    for n, line in enumerate(lines):
        if n in covered or not line.lstrip(" \t").startswith("'") or n >= limit:
            continue
        j = bisect.bisect_right(starts, n)
        if j >= len(groups):
            levels[n] = len(stack) if not halted else 0
            continue
        g = groups[j]
        levels[n] = g.enter if g.directive else g.level
    return levels, limit


def _leading(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def format_indentation(text: str, profile: str = "cr6", indent: str = "  ") -> list[TextEdit]:
    """Edits that set the leading whitespace of the lines of `text` (decoded Latin-1) to
    `indent * level`. See the module docstring and `docs/lsp-api.md` for the rules."""
    return _format(text, profile, indent, 0, None)


def format_range(text: str, start_line: int, end_line: int, profile: str = "cr6",
                 indent: str = "  ") -> list[TextEdit]:
    """`format_indentation` restricted to the 0-based physical lines `start_line..end_line`
    (inclusive). Levels are computed from the whole text, so the range needs no context."""
    return _format(text, profile, indent, start_line, end_line)


def _format(text: str, profile: str, indent: str, lo: int, hi: int | None) -> list[TextEdit]:
    if indent.strip(" \t") or not indent:
        raise ValueError("indent must be a non-empty string of spaces and tabs")
    levels, _limit = _plan(text, profile)
    lines = text.split("\n")
    edits: dict[int, TextEdit] = {}
    for n, level in levels.items():
        if n < lo or (hi is not None and n > hi):
            continue
        body = lines[n][:-1] if lines[n].endswith("\r") else lines[n]
        if not body.strip(" \t"):
            continue                                # blank lines keep their (trailing) blanks
        width = _leading(body)
        want = indent * level
        if body[:width] != want:
            edits[n] = TextEdit(n, 0, width, want)
    return _within_buffer(text, lines, edits)


def _first_overflow(text: str) -> tuple[int, int] | None:
    """Physical lines (first, last; 0-based) of the first overflow of `text`, None: no overflow."""
    loaded, _ = source.load_text(text, "t", directory=None)
    for ll in loaded:
        if ll.kind == "overflow":
            return _phys(ll)[0] - 1, _phys(ll)[1] - 1
    return None


def _within_buffer(text: str, lines: list[str], edits: dict[int, TextEdit]) -> list[TextEdit]:
    """Drop the edits of any statement that a new indentation would push over the loader's
    510-character buffer (a statement's lines are counted together when continued)."""
    base = _first_overflow(text)
    while edits:
        new = apply_edits(text, list(edits.values()))
        found = _first_overflow(new)
        if found is None or found == base:
            break
        hit = [n for n in edits if found[0] <= n <= found[1]]
        if not hit:
            break
        for n in hit:
            del edits[n]
    return [edits[n] for n in sorted(edits)]
