"""Tokenizer for one CRBasic logical line.

`source.py` hands this module already-joined, comment-stripped logical lines (continuation
lines merged, in-string content untouched). This module never sees raw physical files.

Token kinds: ``IDENT`` (identifier or case-insensitive keyword), ``NUMBER``, ``STRING``,
``OP`` (operator/punctuation, including compound assignments and ``#``), ``EOF``.

Numbers: decimal integers and floats (with optional exponent), and ``&H..``/``&B..`` integer
literals. Identifiers: letters, digits, ``_`` (leading ``_`` allowed, e.g. ``_60Hz``), with an
optional single trailing ``$`` (e.g. ``CHR$``). Strings: ``"..."`` with ``""`` as an escaped
quote. Every token keeps its original spelling (`text`), an upper-cased form for
case-insensitive matching (`upper`), and its `col` (0-based offset into the logical line's
joined text) and `line` (the physical source line supplied by the caller -- see docs/frontend.md
for why CR6Comp reports the *last* physical line of a continued statement, which is what
`source.py` assigns as the logical line's `line`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Longest operators first so the scanner prefers the multi-character spelling.
_OPERATORS = [
    "<>", "<=", ">=", "<<", ">>",
    "+=", "-=", "*=", "/=", "\\=", "^=", "&=",
    "+", "-", "*", "/", "\\", "^", "&",
    "=", "<", ">",
    "(", ")", ",", ".", ":", ";", "{", "}", "#",
]
# Sort by length desc so the regex alternation tries multi-char spellings first.
_OPERATORS.sort(key=len, reverse=True)
_OP_RE = re.compile("|".join(re.escape(op) for op in _OPERATORS))

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\$?")
_HEX_RE = re.compile(r"&[Hh][0-9A-Fa-f]+")
_BIN_RE = re.compile(r"&[Bb][01]+")
# Exponent / decimal point are both optional; `_NUMBER_RE` requires at least one digit before
# any decimal point (CRBasic examples never write a bare `.5`, and the compiler's own lexer
# treats a leading `.` as an operator token, so we do not special-case it).
_NUMBER_RE = re.compile(r"\d+(\.\d+)?([Ee][+-]?\d+)?")

KEYWORD_KINDS = {"IDENT"}


@dataclass(frozen=True)
class Token:
    kind: str            # "IDENT" | "NUMBER" | "STRING" | "OP" | "EOF"
    text: str            # original spelling
    upper: str            # case-folded form (identifiers/keywords only; same as text otherwise)
    col: int              # 0-based offset into the logical line's joined text
    line: int = 0          # physical source line (assigned by the caller)
    value: object = None  # NUMBER: int|float; STRING: decoded contents (quotes/escapes resolved)


class LexError(ValueError):
    def __init__(self, message: str, col: int):
        super().__init__(message)
        self.message = message
        self.col = col


def _number_value(text: str) -> int | float:
    if text[0] == "&":
        base = 16 if text[1] in "Hh" else 2
        return int(text[2:], base)
    if "." in text or "e" in text or "E" in text:
        return float(text)
    return int(text)


def tokenize(text: str, line: int = 0) -> list[Token]:
    """Tokenize one already-joined logical line. Raises `LexError` on an unrecognised character."""
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t":
            i += 1
            continue
        if ch == '"':
            start = i
            i += 1
            out = []
            while True:
                if i >= n:
                    raise LexError("unterminated string", start)
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        out.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                out.append(text[i])
                i += 1
            raw = text[start:i]
            tokens.append(Token("STRING", raw, raw, start, line, "".join(out)))
            continue
        m = _HEX_RE.match(text, i) or _BIN_RE.match(text, i)
        if m:
            raw = m.group(0)
            tokens.append(Token("NUMBER", raw, raw.upper(), i, line, _number_value(raw)))
            i = m.end()
            continue
        if ch.isdigit():
            m = _NUMBER_RE.match(text, i)
            raw = m.group(0)
            tokens.append(Token("NUMBER", raw, raw, i, line, _number_value(raw)))
            i = m.end()
            continue
        m = _IDENT_RE.match(text, i)
        if m:
            raw = m.group(0)
            tokens.append(Token("IDENT", raw, raw.upper(), i, line))
            i = m.end()
            continue
        m = _OP_RE.match(text, i)
        if m:
            raw = m.group(0)
            tokens.append(Token("OP", raw, raw, i, line))
            i = m.end()
            continue
        raise LexError(f"unrecognised character {ch!r}", i)
    tokens.append(Token("EOF", "", "", n, line))
    return tokens
