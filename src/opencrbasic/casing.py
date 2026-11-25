"""On-type case correction for editor tooling: the spelling a typed name should have. Protocol
independent; `lsp/features.py` turns a `Correction` into an LSP text edit.

CRBasic ignores case, so nothing here changes what a program means. A name is respelled only from
a source that knows its conventional spelling, first match wins:

1. a name the program declares: the spelling of its declaration;
2. with a help directory, the spelling of the help page's syntax line (`InstructionHelp.syntax`);
3. a structure keyword: `catalog.TABLE_KEYWORDS` / `catalog.EXTRA_KEYWORDS`;
4. a data type or Boolean literal: `_WORDS`;
5. the catalog's table spelling of an instruction (mixed case such as `VoltSe`, or an all-upper
   sensor or device name such as `CS616`: the keywords the table spells in upper case are rule
   3), of a predefined constant that is mixed case or has a digit or `_` (`mV5000`, `CR1000X`),
   or of an operator word (`AND`, `MOD`).

Anything else is left as typed: the compiler tables spell every function and some constants in
upper case (`ABS`, `TABLE`), which is not how programs are written.
"""

import re
from collections.abc import Iterable
from typing import NamedTuple

from .catalog import EXTRA_KEYWORDS, TABLE_KEYWORDS, InstructionCatalog

OPERATOR_WORDS = frozenset({"AND", "EQV", "IMP", "INTDV", "IS", "MOD", "NOT", "OR", "TO", "XOR"})
_WORDS = {w.upper(): w for w in ("Float", "Long", "String", "Boolean", "Double", "True", "False", "NAN")}
_KEYWORDS = {w.upper(): w for w in (*TABLE_KEYWORDS, *EXTRA_KEYWORDS)}
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\$?")
_FORM = re.compile(r"^\s*(?:[A-Za-z_][\w.]*\s*=\s*)?([A-Za-z_]\w*\$?)")


class Correction(NamedTuple):
    line: int  # 0-based
    start: int  # decoded column, inclusive
    end: int  # decoded column, exclusive
    text: str  # the new spelling


def spelling(word: str, profile: str, declared: Iterable[str] = (), help_provider=None) -> str | None:
    """The conventional spelling of `word`, or None when no source knows it."""
    upper = word.upper()
    for name in declared:
        if name.upper() == upper:
            return name
    if help_provider is not None:
        found = help_provider.lookup(upper)
        m = _FORM.match(found.syntax) if found is not None else None
        if m and m.group(1).upper() == upper:
            return m.group(1)
    if upper in _KEYWORDS:
        return _KEYWORDS[upper]
    if upper in _WORDS:
        return _WORDS[upper]
    for entry in InstructionCatalog.for_profile(profile).entries(word):
        name = entry.name
        if (
            upper in OPERATOR_WORDS
            or entry.kind == "instruction"
            or (entry.kind == "symbol" and (not name.isupper() or any(c.isdigit() or c == "_" for c in name)))
        ):
            return name
    return None


def _code_end(line: str) -> int:
    """Where the code of `line` ends: at a `'` comment outside a string, else the line's end. An
    unclosed string runs to the end, so nothing after its quote counts as code."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == "'" and not quoted:
            return i
    return -1 if quoted else len(line)


def word_before(lines: list[str], line: int, column: int, newline: bool = False) -> tuple[int, int, int] | None:
    """(line, start, end) of the name that ends just before the character typed before `column`
    of `line`; for a `newline`, the name that ends the line above. None inside a string or
    comment, after a `.` (a member), `#` (a directive) or `&` (a radix literal), and when no
    name ends there."""
    if newline:
        if line <= 0 or line > len(lines):
            return None
        line, end = line - 1, len(lines[line - 1].rstrip())
    elif 0 <= line < len(lines):
        end = column - 1  # the typed character sits at column - 1
    else:
        return None
    text = lines[line]
    if end <= 0 or end > _code_end(text) or text[:end].count('"') % 2:
        return None
    start = end
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_$"):
        start -= 1
    m = _NAME.fullmatch(text[start:end])
    if m is None or (start > 0 and text[start - 1] in ".#&"):
        return None
    return line, start, end


def correction(
    lines: list[str],
    line: int,
    column: int,
    profile: str,
    declared: Iterable[str] = (),
    help_provider=None,
    newline: bool = False,
) -> Correction | None:
    """The respelling of the name just typed, or None when it is already right or unknown."""
    found = word_before(lines, line, column, newline)
    if found is None:
        return None
    at, start, end = found
    word = lines[at][start:end]
    better = spelling(word, profile, declared, help_provider)
    if better is None or better == word:
        return None
    return Correction(at, start, end, better)
