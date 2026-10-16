"""CRBasic parser: logical lines -> `syntax.Program`.

`parse_file(path)` / `parse_text(text, ...)` never raise for malformed input; a statement the
parser cannot make sense of becomes `syntax.Unparsed` with a reason, and parsing continues with
the next statement.

Conditional compilation (`#If`/`#ElseIf`/`#Else`/`#EndIf`, `#If_no_remove` as a synonym for
`#If`, `#UnDef`) is resolved here, against `consteval`, using `source.py`'s per-line `section`
binding for `Section` inside an `IncludeSection()`. A branch that is never taken contributes no
statements and defines no `Const` -- this is also how the corpus's
`ERROR__STATIC_ASSERT__...`-style "static assert" pattern works: the identifier alone is not
valid CRBasic syntax (confirmed with the oracle: bare `Foo` where `Foo` doesn't name a Sub gives
`Invalid, or out of place expression`), so it only "works" because the branch that would parse it
is always the one that's skipped. This parser does not reject an unresolved bare identifier itself
(that is a semantic check on a compiled Sub table, left to `checker.py`); it always treats it as a
zero-argument `Call`, which is more lenient than CR6Comp on inputs the oracle would reject anyway
and does not affect acceptance on inputs the oracle accepts. See `docs/frontend.md`.

Two-word terminators (`Next Scan`, `End If`, `End Select`, `End Sub`, `Exit Sub`, ...) fall out of
the *generic* instruction parser without special-casing: `NEXT`/`END`/`EXIT`/`SELECT`'s test
expression are all "keyword, then a bare (unparenthesized) comma list of expressions", and a bare
trailing word such as `Scan` parses as a plain `Name` like any other bare identifier -- so
`Instruction(keyword="NEXT", args=[Name("Scan")])` and `Instruction(keyword="NEXT",
args=[Name("I")])` (a real loop counter) share the same shape, and one-word forms (`NextScan`,
`EndIf`, ...) are simply different, separately-tabulated instruction names with the same generic
handling.
"""

from __future__ import annotations

import collections
import dataclasses

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import consteval, lexer, profiles, source
from . import syntax as S
from .diagnostics import Diagnostic, Diagnostics
from .lexer import Token
from .sources import DiskSource, SourceProvider

_DIRECTIVE_RE = re.compile(
    r'^\s*#\s*(If_no_remove|If|ElseIf|Else|EndIf|UnDef)\b(.*)$', re.IGNORECASE
)
_THEN_SUFFIX_RE = re.compile(r'\bThen\s*$', re.IGNORECASE)
_LEADING_WORD_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*\$?)')

_RELOPS = {"=", "<>", "<", ">", "<=", ">="}
_COMPOUND_OPS = {"+=", "-=", "*=", "/=", "\\=", "^=", "&="}

# Distinguishable `Unparsed.reason` markers for a genuinely unclosed parenthesis reached through
# the *general expression* grammar (an assignment's RHS, a nested call's own arguments -- anything
# parsed via `_parse_expr`) -- unlike a bare instruction/`Call` statement's own argument list,
# which stays lenient about running off the end of the line (`expect_close`, unchanged: oracle-
# confirmed `Call Foo(1,2,3` with nothing further compiles). `checker/structure.py` matches these
# two literal strings to report the real compiler's own message instead of the generic "Invalid,
# or out of place expression" fallback. See `docs/checker.md` "Need closing ) shapes".
UNCLOSED_PAREN_EXPR = "unclosed parenthesized expression"
UNCLOSED_DIM_PREFIX = "unclosed dim: "


# `SNMPVariable(Name,1.3.6.1.4.1.5,Float,RONLY)`: a dotted OID literal is not an expression; the real
# compiler accepts any digits-and-dots OID there (oracle 2026-10-12: `1.4.9`, `2.5.7.1`, `10.20.30.40`
# compile), so such a statement is `Unparsed` with this fixed reason.
SNMP_OID_REASON = "dotted SNMP OID literal"
_SNMP_OID_STATEMENT = re.compile(r"\s*SNMPVariable\s*\(\s*(?:[^,]+,\s*)?\d[\d.\s]*[,)]", re.I)


class _ParseError(Exception):
    pass


class _TokStream:
    __slots__ = ("toks", "i", "seg_text")

    def __init__(self, toks: list[Token], seg_text: str = ""):
        self.toks = toks
        self.i = 0
        # The physical line this token stream was lexed from, verbatim (empty for the two call
        # sites that never need it: directive/`Alias`-lhs sub-token-streams). Column-sliced by
        # `_parse_vardecl`'s own unclosed-dimension-list check (`Token.col` indexes straight into
        # this string, oracle-confirmed -- see `docs/checker.md` "Need closing ) shapes").
        self.seg_text = seg_text

    def peek(self, k: int = 0) -> Token:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else self.toks[-1]

    def next(self) -> Token:
        t = self.peek()
        if t.kind != "EOF":
            self.i += 1
        return t

    def at_end(self) -> bool:
        return self.peek().kind == "EOF"

    def remaining(self) -> list[Token]:
        return self.toks[self.i:]

    def is_op(self, text: str, k: int = 0) -> bool:
        t = self.peek(k)
        return t.kind == "OP" and t.text == text

    def is_ident(self, upper: str, k: int = 0) -> bool:
        t = self.peek(k)
        return t.kind == "IDENT" and t.upper == upper

    def expect_op(self, text: str) -> Token:
        if self.is_op(text):
            return self.next()
        t = self.peek()
        raise _ParseError(f"expected {text!r}, found {t.text or t.kind!r}")

    def expect_close(self, text: str) -> Token:
        """Like `expect_op`, but a `)`/`}` implicitly closes at end-of-line: CR6Comp accepts an
        unclosed paren that runs off the end of a logical line, silently completing the call
        right there (confirmed with the oracle: `Call Foo(1,2,3` with nothing further on the
        line compiles; only a genuinely missing argument -- a trailing comma, `Call Foo(1,2,` --
        is rejected, and even then as a semantic `Call argument required.`, not a parse error).
        See docs/frontend.md.
        """
        if self.at_end():
            return self.peek()
        return self.expect_op(text)

    def expect_ident(self, upper: str | None = None) -> Token:
        t = self.peek()
        if t.kind == "IDENT" and (upper is None or t.upper == upper):
            return self.next()
        want = upper if upper else "an identifier"
        raise _ParseError(f"expected {want}, found {t.text or t.kind!r}")


def _eof_like(line: int) -> Token:
    return Token("EOF", "", "", 0, line)


def _split_text_segments(text: str) -> list[str]:
    """Split a logical line's raw text on every `:` outside `"..."` strings (parentheses do not
    protect a `:`, oracle 2026-10-16, all four loggers: `docs/checker.md` "Colon inside parentheses"),
    without tokenising -- so a segment whose content isn't tokenisable CRBasic at all (a `Units`
    label) can still be found and routed separately. Mirrors `_split_colon_segments`.
    """
    segments: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            current.append(ch)
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    current.append(text[i + 1])
                    i += 2
                    continue
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            current.append(ch)
            i += 1
            continue
        if ch == ":":
            segments.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    segments.append("".join(current))
    return segments


_RAW_NAME_FIRST = frozenset({"DATATABLE", "CONSTTABLE"})


_ASSIGN_OPS = frozenset({"=", "+=", "-=", "*=", "/=", "\\=", "^=", "&="})


def _is_assignment(toks: list[Token]) -> bool:
    """`name`, optional `(...)` subscripts and `.member` parts, then an assignment operator."""
    if not toks or toks[0].kind != "IDENT":
        return False
    i, depth = 1, 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "OP" and t.text == "(":
            depth += 1
        elif t.kind == "OP" and t.text == ")":
            depth -= 1
        elif depth == 0:
            if t.kind == "OP" and t.text in _ASSIGN_OPS:
                return True
            if not (t.kind == "OP" and t.text == "." or t.kind == "IDENT" and toks[i - 1].text == "."):
                return False
        i += 1
    return False


def _drop_semicolons(tokens: list[Token], profile) -> list[Token]:
    """CR6/CR1000X skip a `;` like a blank inside an assignment (also leading, `; X = 1`), and
    after the trailing end of a `Const`, `For`, `Next`, `While` or `Select Case` header or of the
    assignment body of a one-line `If`: `X = 1 ;`, `X =; 1` and `X = 1 ; + 2` compile, while
    `X = 1 ; Y = 2` and `X = 1 ;)` are rejected as if the `;` were absent. CR300/CR350 accept a
    trailing `;` only after `Next` (`Next Y ;`). Oracle, 2026-10-12, docs/checker.md "Round 13". An
    instruction call (`Delay(1,1,Sec) ;`), `Do ;`, `CallTable T ;`, `EndIf ;` and `If c Then ;` keep
    their own messages."""
    real = [t for t in tokens if t.kind != "EOF"]
    eof = tokens[len(real):]
    semi = lambda t: t.kind == "OP" and t.text == ";"   # noqa: E731
    body = [t for t in real if not semi(t)]
    if len(body) == len(real) or not body or body[0].kind != "IDENT":
        return tokens
    head = body[0].upper
    trailing_only = real[:len(body)] == body   # every `;` follows the last other token
    first = real[0] is body[0]

    def glued(word: str) -> bool:   # `Then;`, `Next;`: the `;` makes the keyword part of a longer word
        return any(semi(t) and a.kind == "IDENT" and a.upper == word and t.col == a.col + len(a.text)
                   for a, t in zip(real, real[1:]))

    if head == "NEXT":
        ok = first and not glued("NEXT")
    elif head == "ENDSELECT":
        if first and semi(real[1]) and not glued("ENDSELECT"):
            j = 1   # only the first gap-free group of `;` is skipped (`EndSelect ; ;` is `Too many parameters:  ;.`)
            while j + 1 < len(real) and semi(real[j + 1]) and real[j + 1].col == real[j].col + 1:
                j += 1
            return [real[0], *real[j + 1:], *eof]
        ok = False
    elif head == "CASE" and [t.upper for t in body[1:]] == ["ELSE"] and first and real[:2] == body:
        ok = True    # `Case Else ;` (all four loggers)
    elif (head == "ELSEIF" and body[-1].upper == "THEN" and real[:len(body)] == body and first
          and len(body) > 2 and not glued("THEN")):
        ok = True    # `ElseIf c Then ;` (all four loggers)
    elif head in ("SUB", "FUNCTION"):
        ok = trailing_only and first and len(body) > 1   # `Sub S ;`, `Function F(a) ;` (all four loggers)
    elif profile.name not in ("cr6", "cr1000x"):
        ok = False
    elif head in ("IF", "ELSEIF"):
        then = max((i for i, t in enumerate(body) if t.kind == "IDENT" and t.upper == "THEN"), default=-1)
        if then < 0 or not first or glued("THEN") or glued("ELSE"):
            ok = False   # also `; If c Then`
        elif then + 1 < len(body):
            ok = _is_assignment(body[then + 1:])
        else:
            ok = head == "ELSEIF" or not any(semi(t) for t in real[real.index(body[then]) + 1:])
    elif head == "CONST":
        eq = next((i for i, t in enumerate(real) if t.kind == "OP" and t.text == "="), len(real))
        ok = first and not any(semi(t) for t in real[:eq])   # `Const ; K = 1`, `Const K ; = 1` are rejected
    elif head in ("FOR", "WHILE", "SELECT"):
        ok = first
    elif head in ("DO", "LOOP", "CASE"):
        # (`Do ; While c` and `Loop ; Until c` are rejected: only the condition skips a `;`)
        ok = (first and len(body) > 1 and not (head == "CASE" and [t.upper for t in body[1:]] == ["ELSE"])
              and (head == "CASE" or real[1] is body[1]))
    elif head in profile.functions and head not in profile.instructions and trailing_only and first \
            and len(body) > 1 and body[1].text == "(" and body[-1].text == ")":
        ok = True    # `SerialOpen(ComRS232,9600,0,0,50) ;` (oracle, CR6, 2026-10-12)
    else:
        member = len(body) > 1 and body[1].kind == "OP" and body[1].text == "."   # `Public.X = 1 ;`
        ok = (head not in profile.instructions or member and head in ("PUBLIC", "DIM")) and _is_assignment(body)
    if ok:
        return body + eof
    if profile.name in ("cr6", "cr1000x") and head not in ("SUB", "FUNCTION"):
        # (the table name slot keeps its `;`: `DataTable (T ;,1,-1)`, see `_parse_segment`)
        name_slot = 3 if head == "DATATABLE" and len(real) > 3 and real[1].text == "(" \
            and real[2].kind == "IDENT" else -1
        depth, kept = 0, []
        for i, t in enumerate(real):
            if t.kind == "OP" and t.text in "()":
                depth += 1 if t.text == "(" else -1
            if not (semi(t) and depth > 0 and i != name_slot):
                kept.append(t)
        if len(kept) < len(real):
            return kept + eof
    return tokens


_S1_CALLS = frozenset({"ABS", "ABSLONG", "ARRAYLENGTH", "TYPEOF"})
_S2_CALLS = frozenset({"LINENUM", "SIGNATURE"})
_SELF_CLOSING_CALLS = _S1_CALLS | _S2_CALLS
_BIN_OPS = frozenset({"+", "-", "*", "/", "^", "&", "=", "<>", "<", ">", "<=", ">=", "\\"})
_BIN_WORDS = frozenset({"AND", "OR", "XOR", "MOD", "EQV", "IMP"})


def _match_paren(toks: list[Token], i: int) -> int | None:
    depth = 0
    for j in range(i, len(toks)):
        if toks[j].kind == "OP" and toks[j].text == "(":
            depth += 1
        elif toks[j].kind == "OP" and toks[j].text == ")":
            depth -= 1
            if depth == 0:
                return j
    return None


def _is_binary(t: Token) -> bool:
    return (t.kind == "OP" and t.text in _BIN_OPS) or (t.kind == "IDENT" and t.upper in _BIN_WORDS)


def _expr_end(toks: list[Token], i: int = 0) -> int | None:
    """Index just past the simple expression (operands, binary operators, closed groups, member
    chains) starting at `toks[i]`, or None when there is none."""
    n = len(toks)
    while True:
        while i < n and (toks[i].kind == "OP" and toks[i].text in "+-"
                         or toks[i].kind == "IDENT" and toks[i].upper == "NOT"):
            i += 1
        if i >= n:
            return None
        t = toks[i]
        if t.kind in ("NUMBER", "STRING"):
            i += 1
        elif t.kind == "IDENT":
            i += 1
            while i < n:
                if toks[i].kind == "OP" and toks[i].text == "." and i + 1 < n and toks[i + 1].kind == "IDENT":
                    i += 2
                elif toks[i].kind == "OP" and toks[i].text == "(":
                    j = _match_paren(toks, i)
                    if j is None:
                        return None
                    i = j + 1
                else:
                    break
        elif t.kind == "OP" and t.text == "(":
            j = _match_paren(toks, i)
            if j is None:
                return None
            i = j + 1
        else:
            return None
        if i < n and _is_binary(toks[i]):
            i += 1
            continue
        return i


def _close_open_flat(tokens: list[Token], profile) -> list[Token]:
    """`X = Abs(X`, `If Abs(X + 1 Then`, `Do While AbsLong(X`: these six built-ins accept their
    one argument without the closing parenthesis, which the end of the statement (or the `Then` of
    an `If`/`ElseIf`) supplies, when it is the only open group and holds a single argument
    (oracle, all four loggers, 2026-10-12, `tests/fixtures/r14_abs_unclosed.json`). The comma,
    juxtaposed-operand, nested-call and empty-argument shapes behave differently per function and
    stay as they were."""
    real = [t for t in tokens if t.kind != "EOF"]
    stack: list[int] = []
    end = len(real)
    head = real[0].upper if real and real[0].kind == "IDENT" else ""
    for i, t in enumerate(real):
        if t.kind == "OP" and t.text == "(":
            stack.append(i)
        elif t.kind == "OP" and t.text == ")":
            if not stack:
                return tokens
            stack.pop()
        elif t.kind == "IDENT" and t.upper == "THEN" and stack and head in ("IF", "ELSEIF"):
            end = i
            break
    if len(stack) != 1 or head in ("WHILE", "LOOP") and end != len(real):
        return tokens
    open_at = stack[0]
    prev = real[open_at - 1] if open_at else None
    if prev is None or prev.kind != "IDENT" or prev.upper not in _SELF_CLOSING_CALLS \
            or open_at + 1 >= end or (open_at > 1 and real[open_at - 2].text == "."):
        return tokens
    if any(t.kind == "OP" and t.text in (",", "{", "}") for t in real[open_at + 1:end]) \
            or any(t.kind == "IDENT" and t.upper == "THEN" for t in real[open_at + 1:end]):
        return tokens
    close = lexer.Token("OP", ")", ")", real[end - 1].col + len(real[end - 1].text), real[end - 1].line)
    return real[:end] + [close] + real[end:] + tokens[len(real):]


class _Closed(list):
    """Tokens of a statement whose unclosed group was closed; `dropped` holds the second,
    side-by-side expression of `Abs(X Q` that the closing left out (see `cr3xx.check_dropped_tail`)."""
    dropped: list = []


def _close_open_call(tokens: list[Token], profile) -> list[Token]:
    """Unclosed groups the real compilers accept (oracle, all four loggers, 2026-10-12,
    `tests/fixtures/r14_abs_unclosed.json`, `r15_abs_unclosed.json`; `docs/checker.md` "Round 15").
    The `Then` and everything after it in a `While`/`Do While|Until`/`Loop While|Until` condition
    is ignored; then the flat `Abs(X`, and the nested / trailing-comma / juxtaposed / empty and
    `LineNum(X)`-in-an-open-group shapes of `_close_open_extra`."""
    real = [t for t in tokens if t.kind != "EOF"]
    original = tokens
    if real and real[0].kind == "IDENT" and (
            real[0].upper == "WHILE" or real[0].upper in ("DO", "LOOP") and len(real) > 1
            and real[1].kind == "IDENT" and real[1].upper in ("WHILE", "UNTIL")):
        cut = next((i for i, t in enumerate(real) if t.kind == "IDENT" and t.upper == "THEN"), None)
        if cut is not None:
            tokens = real[:cut] + tokens[len(real):]
    out = _close_open_extra(tokens, profile)
    if out is tokens:
        out = _close_open_flat(tokens, profile)
    if out is tokens and tokens is not original:
        depth = sum(1 if t.text == "(" else -1 if t.text == ")" else 0 for t in tokens if t.kind == "OP")
        if depth > 0:   # still unclosed after the cut (`While (C Then`): keep the old rejection
            return original
    return out


def _close_open_extra(tokens: list[Token], profile) -> list[Token]:
    real = [t for t in tokens if t.kind != "EOF"]
    eof = tokens[len(real):]
    head = real[0].upper if real and real[0].kind == "IDENT" else ""
    stack: list[int] = []
    end = len(real)
    for i, t in enumerate(real):
        if t.kind == "OP" and t.text == "(":
            stack.append(i)
        elif t.kind == "OP" and t.text == ")":
            if not stack:
                return tokens
            stack.pop()
        elif t.kind == "IDENT" and t.upper == "THEN" and stack and head in ("IF", "ELSEIF"):
            end = i
            break
    if not stack or any(t.kind == "OP" and t.text in "{}" for t in real[:end]):
        return tokens
    last = real[end - 1]

    def mk(kind: str, text: str, value=None) -> Token:
        return lexer.Token(kind, text, text, last.col + len(last.text), last.line, value)

    def call_name(o: int) -> str:
        return real[o - 1].upper if o and real[o - 1].kind == "IDENT" and not (o > 1 and real[o - 2].text == ".") \
            else ""

    names = [call_name(o) for o in stack]
    n = len(stack)
    inner = stack[-1]
    content = real[inner + 1:end]
    if head in ("IF", "ELSEIF") and any(t.kind == "IDENT" and t.upper == "THEN" for t in real[:end]):
        return tokens

    def closed(body: list[Token]) -> list[Token]:
        return real[:inner + 1] + body + [mk("OP", ")")] * n + real[end:] + eof

    # ---- calls of the self-closing family, possibly nested in one another ----
    if all(nm in _S1_CALLS for nm in names[:-1]) and names[-1] in _SELF_CLOSING_CALLS:
        for k in range(1, n):   # what stands between an outer `(` and the next call: nothing or `... op`
            prefix = real[stack[k - 1] + 1:stack[k] - 1]
            if any(t.kind == "OP" and t.text == "," for t in prefix) or (
                    prefix and not _is_binary(prefix[-1])):
                return tokens
        if names[-1] in _S1_CALLS and content:
            # `n` open groups take up to `n + 1` side-by-side expressions, or `n` of them before a
            # trailing comma (oracle, all four loggers, 2026-10-12: `Abs(X Z` | `Abs(Abs(X Z Z` |
            # `Abs(Abs(Abs(X Z Z Z` compile, one more does not; `Abs(X,` | `Abs(Abs(X Z,` compile,
            # `Abs(X Z,` | `Abs(Abs(X Z Z,` do not)
            exprs: list[list[Token]] = []
            tail = content
            while tail and tail[0].text != ",":
                e = _expr_end(tail, 0)
                if not e:
                    return tokens
                exprs.append(tail[:e])
                tail = tail[e:]
            comma = len(tail) == 1 and tail[0].text == ","
            if (tail and not comma) or len(exprs) > (n if comma else n + 1) or not exprs:
                return tokens
            if len(exprs) == 1 and not comma:
                return closed(content) if n > 1 else tokens
            dropped = [t for part in exprs[1:] for t in part]
            if any(part[0].kind == "IDENT" and len(part) > 1 and part[1].text == "(" for part in exprs[1:]):
                return tokens   # `Abs(X Q(1)`: Incomplete expression (oracle, 2026-10-12)
            out = _Closed(closed(exprs[0]))
            out.dropped = dropped
            return out
        if names[-1] in _S2_CALLS and (not content or n == 1 and [t.text for t in content] == [","]) \
                and not any(t.kind == "IDENT" and t.upper in _S2_CALLS for t in real[:inner - 1]):
            return closed([mk("NUMBER", "0", 0)])
        if names[-1] in _S2_CALLS and n > 1 and content:
            e1 = _expr_end(content, 0)
            if e1 == len(content) and not any(t.text == "," for t in content):
                return closed(content)
        return tokens
    # ---- `Y = A(LineNum(X) + 1`: a closed LineNum/Signature call inside one other open group ----
    simple_assign = len(real) > 2 and real[0].kind == "IDENT" and real[1].kind == "OP" and real[1].text == "="
    if n == 1 and simple_assign and head not in profile.instructions and end == len(real) \
            and names[0] not in _SELF_CLOSING_CALLS:
        if any(t.text == "," for t in content) or any(
                t.kind == "IDENT" and t.upper in _S2_CALLS for t in real[:inner - 1]):
            return tokens
        calls = [i for i, t in enumerate(content) if t.kind == "IDENT" and t.upper in _S2_CALLS]
        if len(calls) != 1 or calls[0] + 1 >= len(content) or content[calls[0] + 1].text != "(":
            return tokens
        c = calls[0]
        close = _match_paren(content, c + 1)
        pre, post = content[:c], content[close + 1:] if close is not None else None
        if close is None:
            return tokens
        if pre and not _is_binary(pre[-1]):
            return tokens
        if post and not (_is_binary(post[0]) and _expr_end(post, 1) == len(post)):
            return tokens
        return real[:end] + [mk("OP", ")")] + real[end:] + eof
    # ---- statement `A(LineNum(X) = 1`: the call closes the index too ----
    if n == 1 and stack[0] == 1 and len(real) > 5 and real[0].kind == "IDENT" and real[2].kind == "IDENT" \
            and real[2].upper in _S2_CALLS and real[3].text == "(" and head not in profile.instructions:
        close = _match_paren(real, 3)
        if close is not None and close + 1 < len(real) and real[close + 1].kind == "OP" \
                and real[close + 1].text == "=" and close + 2 < len(real):
            one = lexer.Token("NUMBER", "1", "1", real[2].col, real[2].line, 1)
            return real[:2] + [one, lexer.Token("OP", ")", ")", real[2].col, real[2].line)] + real[close + 1:] + eof
    return tokens


_INLINE_BLOCK_WORDS = frozenset({"SELECT", "WHILE", "NEXT", "ENDSELECT", "WEND", "SUBSCAN", "NEXTSUBSCAN", "NEXTSCAN"})


def _is_block_statement(st: S.Stmt) -> bool:
    if isinstance(st, (S.DoLoop, S.For)):
        return True
    if not isinstance(st, S.Instruction):
        return False
    word = st.keyword.upper()
    return word in _INLINE_BLOCK_WORDS or word == "END" and bool(st.args) and isinstance(st.args[0], S.Name) \
        and st.args[0].name.upper() == "SELECT"


def _hoist_block_statements(stmt: S.If) -> list[S.Stmt]:
    """`If c Then Do`, `If c Then Y = 1 Else For ...`, `If c Then Next`: a block keyword in the body
    of a one-line `If` opens or closes its block like a statement of its own (oracle, all four
    loggers, 2026-10-12: the `Loop`/`Next`/`EndSelect` that follows is matched, a missing one is
    `DO without LOOP.`). The statements move out of the body, in order, behind the `If`."""
    hoisted: list[S.Stmt] = []
    for attr in ("inline", "inline_else"):
        body = getattr(stmt, attr)
        if body:
            for position, st in enumerate(body):
                if _is_block_statement(st):
                    st.hoisted_last = position == len(body) - 1   # (the reachability warning)
                    hoisted.append(st)
            setattr(stmt, attr, [st for st in body if not _is_block_statement(st)])
    for st in hoisted:
        if hasattr(stmt, "origin"):
            st.origin = stmt.origin
    return hoisted


def _split_colon_segments(tokens: list[Token]) -> list[list[Token]]:
    """Split a fully-tokenised logical line on every `:`."""
    segments: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    eof = tokens[-1]
    for t in tokens[:-1]:
        if t.kind == "OP" and t.text == ":":
            segments.append(current)
            current = []
            continue
        current.append(t)
    segments.append(current)
    return [seg + [eof] for seg in segments]


@dataclass
class _CondFrame:
    any_true: bool
    current: bool
    parent_ok: bool


_WORD_BINARY_OPS = {"MOD", "AND", "OR", "XOR", "EQV", "IMP"}  # `Fn(1) MOD 2` is one expression
_FUNCTION_DEF_RE = re.compile(r"\s*Function\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


_LOGICAL_LEAD_OPS = {"+", "-", "*", "/", "\\", "^", "&", "<", ">", "<=", ">=", "<>", "=<", "=>", "><"}

LOGICAL_ASSIGN = "logical-prefixed assignment"


def _has_assign(expr) -> bool:
    return isinstance(expr, S.Binary) and (expr.op == "ASSIGN" or _has_assign(expr.left)
                                           or _has_assign(expr.right))


_DIGIT_NAME = re.compile(r"[\d.][\w.]*")

_DEFERRED_SOURCE_INSTRUCTIONS = frozenset({
    "SAMPLE", "AVERAGE", "MAXIMUM", "MINIMUM", "TOTALIZE", "MEDIAN", "STDDEV", "MOMENT", "COVARIANCE"})


class _Parser:
    def __init__(self, profile: profiles.Profile, diags: Diagnostics, provenance: bool = True):
        self.provenance = provenance  # False: no `Stmt.origin`, no `line_map` (language-server-only data)
        self.profile = profile
        self.diags = diags
        self.env = consteval.Env(profile)
        self.cond_stack: list[_CondFrame] = []
        self.program = S.Program(name="")
        self.line_map: dict[str, dict[int, list[source.LineSpan]]] = {}  # reported line -> physical
        self._done = False  # set once a top-level EndProg is seen; see `run()`
        self._inline_body = False  # parsing the statements of a one-line `If`
        self._in_struct = False  # inside StructureType ... EndStructureType
        self._in_table = False   # inside DataTable ... EndTable
        self.function_names: set[str] = set()
        self._collect = False
        self._to_ok = False  # `To` as a binary operator: assignment values and `Select Case`
        self._logical_assign = False  # `A AND B = C`: a logical operand may be an assignment

    # ------------------------------------------------------------ driver

    def run(self, lines: list[source.LogicalLine], name: str) -> tuple[S.Program, Diagnostics]:
        self.program.name = name
        if not self._collect and any(_FUNCTION_DEF_RE.match(ll.text) for ll in lines):
            # only definitions the compiler really reads count (not after EndProg, not in an
            # inactive #If; oracle, 2026-10-10): a throwaway pass finds them
            probe = _Parser(self.profile, Diagnostics(), self.provenance)
            probe._collect = True
            probe.run(lines, name)
            self.function_names = probe.function_names
        skipped_include_depth = 0
        todo = collections.deque(lines)
        unshift: dict[str, int] = {}  # per file: physical lines of unread (inactive) initializer pulls given back
        while todo:
            ll = todo.popleft()
            if unshift.get(ll.file):
                ll = dataclasses.replace(ll, line=ll.line + unshift[ll.file])
            if ll.unpulled is not None and not skipped_include_depth and not self._active():
                todo.extendleft(reversed(ll.unpulled))
                unshift[ll.file] = unshift.get(ll.file, 0) + ll.pulls
                continue
            self._note_line(ll)
            if skipped_include_depth:
                if ll.kind == "include_begin":
                    skipped_include_depth += 1
                elif ll.kind == "include_end":
                    skipped_include_depth -= 1
                continue
            if ll.kind == "include_begin":
                if not self._active():
                    skipped_include_depth = 1
                continue
            if ll.kind == "include_end":
                continue
            if ll.kind in ("include_warning", "include_error"):
                if self._active():
                    (self.diags.warning if ll.kind == "include_warning" else self.diags.error)(
                        ll.line, ll.text, ll.file)
                    if ll.kind == "include_error":  # (CR300/CR350 cascade trigger, `cr3xx`)
                        self.program.include_error_at.append(len(self.program.statements))
                continue
            if ll.kind == "overflow":
                self.diags.error(ll.line, "Line exceeds max characters of 512.", ll.file)
                break
            m = _DIRECTIVE_RE.match(ll.text)
            if m:
                kind = m.group(1).upper()
                if kind == "IF_NO_REMOVE":
                    kind = "IF"
                self.env.section = ll.section
                self._handle_directive(kind, m.group(2), ll.line, ll.file)
                continue
            if not self._active():
                continue
            self.env.section = ll.section
            if self._collect and (m := _FUNCTION_DEF_RE.match(ll.text)):
                self.function_names.add(m.group(1).upper())
            if ll.bad_init:
                self.diags.error(ll.line, ll.bad_init[0], ll.file)
                self.diags.error(ll.line, f"Variable initialization of {ll.bad_init[1]} has bad syntax.",
                                 ll.file)
            before = len(self.program.statements)
            self._parse_line(ll)
            if self.program.hash_cut is None and any(
                    S.hash_statement(st) for st in self.program.statements[before:]):
                self.program.hash_cut = len(self.diags.items)
            if self._done:
                # CR6Comp stops reading the file at the first top-level EndProg -- confirmed with
                # the oracle: garbage (even unlexable text, even a second full program) after it
                # compiles fine, because it is never looked at. See docs/frontend.md.
                break
        for ll in todo:  # never reached (after EndProg or an overflow line): positions only
            for l in (ll.unpulled or [ll]):
                self._note_line(dataclasses.replace(
                    l, line=l.line + unshift.get(ll.file, 0) + (ll.pulls if ll.unpulled else 0)))
            if ll.unpulled is not None:
                unshift[ll.file] = unshift.get(ll.file, 0) + ll.pulls
        if self.cond_stack:  # read lines only: nothing after the first `EndProg` is looked at
            # `#If True : #Foo : #EndIf` leaves the `#If` open: the rest of a directive line is not read,
            # so its `#EndIf` is not seen (oracle, all four loggers, 2026-10-11)
            self.program.unclosed_if = True
        self.program.consts = dict(self.env.consts)
        return self.program, self.diags

    def _note_line(self, ll: source.LogicalLine) -> None:
        """Record, for `positions.py`, the physical lines of `ll` under the line it is reported at."""
        if ll.phys is None or self._collect or not self.provenance:
            return
        span = source.LineSpan(ll.phys[0], ll.phys[1], ll.occurrence)
        spans = self.line_map.setdefault(ll.file, {}).setdefault(ll.line, [])
        if span not in spans:
            spans.append(span)

    def _active(self) -> bool:
        return all(f.current for f in self.cond_stack)

    # -------------------------------------------------------- directives

    def _handle_directive(self, kind: str, rest: str, line: int, file: str) -> None:
        if kind == "IF":
            parent_ok = self._active()
            cond = self._eval_directive_cond(rest, line, file) if parent_ok else False
            self.cond_stack.append(_CondFrame(any_true=cond, current=cond, parent_ok=parent_ok))
        elif kind == "ELSEIF":
            if not self.cond_stack:
                self.diags.error(line, "#ElseIf without #If.", file)
                return
            frame = self.cond_stack[-1]
            if not frame.parent_ok or frame.any_true:
                frame.current = False
            else:
                cond = self._eval_directive_cond(rest, line, file)
                frame.current = cond
                frame.any_true = frame.any_true or cond
        elif kind == "ELSE":
            if not self.cond_stack:
                self.diags.error(line, "#Else without #If.", file)
                return
            frame = self.cond_stack[-1]
            frame.current = frame.parent_ok and not frame.any_true
            frame.any_true = frame.any_true or frame.current
        elif kind == "ENDIF":
            if self.cond_stack:
                self.cond_stack.pop()
            else:
                self.diags.error(line, "#EndIf without #If.", file)
        elif kind == "UNDEF":
            name = rest.strip().split()[0].upper() if rest.strip() else ""
            self.env.consts.pop(name, None)

    def _eval_directive_cond(self, rest: str, line: int, file: str) -> bool:
        text = rest.strip()
        m = _THEN_SUFFIX_RE.search(text)
        if m:
            text = text[: m.start()].rstrip()
        try:
            tokens = lexer.tokenize(text, line)
        except lexer.LexError as e:
            self.diags.error(line, f"invalid #If expression: {e.message}", file)
            return False
        p = _TokStream(tokens)
        try:
            expr = self._parse_expr(p, line, file)
        except _ParseError as e:
            self.diags.error(line, f"invalid #If expression: {e}", file)
            return False
        try:
            value = consteval.value_of(expr, self.env)
        except consteval.NotConstant:
            self.diags.error(line, f"Invalid symbol in expression: {text}.", file)
            return False
        return consteval.truthy(value)

    # ---------------------------------------------------------- one line

    def _parse_line(self, ll: source.LogicalLine) -> None:
        # `Units`' label is free-form text, not tokenisable CRBasic (`%`, a raw ISO-8859-1 `deg`
        # byte, ...), so a top-level (outside `(...)`/strings) `:` split happens at the character
        # level first, and only a segment that isn't a `Units` statement gets tokenised at all
        # (found by corpus testing -- `Public TC(120) : Units TC = C` -- see docs/frontend.md).
        segments = _split_text_segments(ll.text)
        last_idx = len(segments) - 1
        seg_offsets = [0]
        for seg in segments:
            seg_offsets.append(seg_offsets[-1] + len(seg) + 1)
        in_inline_if = False
        colon_if = False
        for idx, seg_text_raw in enumerate(segments):
            if not seg_text_raw.strip() or seg_text_raw.strip() == "=":
                continue  # (a segment holding only `=` compiles silently, oracle, all four loggers, 2026-10-10)
            if colon_if and re.fullmatch(r"\s*(?:Else|EndIf|End\s+If)\s*", seg_text_raw, re.I):
                continue  # inside `If c Then : ...` these are part of the one-line If
            # Only the segment that is both last on its physical line (nothing after it split off
            # by a top-level `:`) and not followed by a stripped `'` comment truly reaches that
            # line's real end -- see `Stmt.at_eol`'s docstring.
            at_eol = idx == last_idx and not ll.had_trailing_comment and not in_inline_if
            word = _LEADING_WORD_RE.match(seg_text_raw)
            if word and word.group(1).upper() == "UNITS":
                stmt = self._parse_units_segment(seg_text_raw, ll.line, ll.file)
            else:
                stmt = self._parse_text_segment(seg_text_raw, ll.line, ll.file)
            stmt.at_eol = at_eol
            if self.provenance:
                stmt.origin = (ll, seg_offsets[idx])  # positions only: logical line and segment offset
            if (isinstance(stmt, S.If) and stmt.inline is None and idx < last_idx
                    and self.profile.name in ("cr6", "cr1000x")
                    and re.search(r"\bThen\s*$", seg_text_raw, re.I)):
                stmt.inline = []  # `If X Then : Y = 1` is a one-line If (oracle, 2026-10-10)
                colon_if = True
            # Segments after a one-line `If` belong to its body: no newline in the echo (oracle,
            # CR300, 2026-10-01: `If X Then X = 1 : Foo(1)`).
            in_inline_if = in_inline_if or (isinstance(stmt, S.If) and stmt.inline is not None)
            self.program.statements.append(stmt)
            if isinstance(stmt, S.If) and stmt.inline is not None:
                self.program.statements.extend(_hoist_block_statements(stmt))
            if isinstance(stmt, S.Unparsed) and self.profile.single_pass:
                self.program.statements.extend(self._inline_body_after_bad_condition(stmt, seg_text_raw))
            # A `Const` just parsed is folded into `self.env` inside `_parse_const` itself, so
            # later statements/#If on the same or a later line see it.
            if isinstance(stmt, S.Instruction) and stmt.keyword == "ENDPROG":
                self._done = True
                return

    def _inline_body_after_bad_condition(self, stmt: S.Unparsed, seg_text_raw: str) -> list[S.Stmt]:
        """CR300/CR350: `If X Y Then Y = Nope` reports the unreadable condition and still checks the
        one-line body (oracle, 2026-10-10: `Nope is not defined.` follows). Returns the body's
        statements, parsed as if the condition were fine."""
        m = re.match(r"(?is)\s*If\b.*?\bThen\b(\s*\S.*)$", seg_text_raw)
        if not m or not stmt.reason.startswith("expected THEN"):
            return []
        probe = self._parse_text_segment("If 1 Then" + m.group(1), stmt.line, stmt.file)
        if not isinstance(probe, S.If) or not probe.inline:
            return []
        body = list(probe.inline) + list(probe.inline_else or [])
        for st in body:
            st.at_eol = False
        return body

    def _parse_text_segment(self, seg_text_raw: str, line: int, file: str) -> S.Stmt:
        try:
            tokens = lexer.tokenize(seg_text_raw, line)
        except lexer.LexError as e:
            if re.match(r"\s*ESSInitialize\b", seg_text_raw, re.I):
                # Its argument is read raw: `ESSInitialize(public")` and `ESSInitialize("public)`
                # compile on all four loggers (oracle, 2026-09-28; a fuzz-found false reject).
                return S.Unparsed(line, file, text=seg_text_raw.strip(), reason="empty statement")
            m = re.match(r"\s*([A-Za-z_]\w*)", seg_text_raw)
            rec = self.profile.instructions.get(m.group(1).upper()) if m else None
            real = [c for c in rec["params"] if c not in (14, 32, 36, 46)] if rec else []
            if (e.message == "unterminated string" and real and real[0] >= 100
                    and m.group(1).upper() != "SLOWSEQUENCE"
                    and seg_text_raw[e.col:e.col + 1] == '"'):
                # An optional first parameter: the cut-off string is dropped and the rest read as
                # the instruction (oracle, 2026-10-16: `Calibrate(")` compiles, `Calibrate(A,")`
                # reports `Variable A out of bounds.`).
                return self._parse_text_segment(seg_text_raw[:e.col].rstrip().rstrip(","), line, file)
            # An unterminated string is echoed verbatim, indentation and trailing spaces included
            # (oracle, 2026-09-28: `    S = "abc   .`).
            text = seg_text_raw if e.message == "unterminated string" else seg_text_raw.strip()
            return S.Unparsed(line, file, text=text, reason=f"lex error: {e.message}")
        real = [t for t in tokens if t.kind != "EOF"]
        if not real or all(t.kind == "OP" and t.text in "()," for t in real):
            # A segment of only `(`, `)` and `,` compiles silently on all four loggers
            # (`docs/checker.md` "Pass-1 fallback: statements that start with a known word").
            return S.Unparsed(line, file, text=seg_text_raw.strip(), reason="empty statement")
        seg_text = self._segment_text(seg_text_raw, real)
        tokens = _drop_semicolons(tokens, self.profile)
        tokens = _close_open_call(tokens, self.profile)
        dropped = getattr(tokens, "dropped", None)
        stmt = self._parse_segment(tokens, line, file, seg_text)
        if dropped:
            stmt.dropped_tail = dropped
        return stmt

    @staticmethod
    def _segment_text(line_text: str, real_tokens: list[Token]) -> str:
        # The real compiler's own "Invalid, or out of place expression: %s." (`docs/checker.md`)
        # echoes the segment's raw source text verbatim -- leading indentation and any trailing
        # whitespace before a stripped-off `'` comment both preserved, oracle-confirmed
        # (`udpsocketopen_1.CR6` etc.: a 4-space-indented `UDPSocketOpen(...)` followed by a
        # comment is echoed as `    UDPSocketOpen(SockID, 0, 3) .`, not the token-trimmed form an
        # earlier version of this function returned). `real_tokens` is unused now but kept in the
        # signature since callers already have it computed.
        del real_tokens
        return line_text

    def _cr3xx_argument_semicolon(self, seg: list[Token], line: int, file: str,
                                  seg_text: str) -> list[Token]:
        """CR300/CR350: a `;` inside an instruction's argument is `Invalid symbol in expression:
        <argument up to the ;>.`; an argument at a parameter that ends the argument list
        (`checker.cr3xx._ABORTING_PARAMS`) adds `Too many parameters:  <next argument>.`. The
        statement is otherwise read without the `;` (oracle, 2026-10-12: `Delay(0,10 ;,mSec)`,
        `PortSet(C1 ;,1)`, `Sample(1,X ;,FP2)`, `Scan (1 ;,Sec,3,0)`)."""
        if len(seg) < 4 or seg[0].kind != "IDENT" or seg[1].text != "(" or \
                seg[0].upper not in self.profile.instructions or \
                self.profile.instructions[seg[0].upper]["params"][:1] == [7]:
            return seg   # (code 7, `Case (1 ;)`/`If (c ;)`: a parenthesised expression)
        depth, index, start = 1, 0, 2
        for i in range(2, len(seg)):
            t = seg[i]
            if t.kind != "OP":
                continue
            if t.text == "(":
                depth += 1
            elif t.text == ")":
                depth -= 1
                if depth == 0:
                    return seg
            elif t.text == "," and depth == 1:
                index, start = index + 1, i + 1
            elif t.text == ";" and depth == 1 and i > start:
                break
        else:
            return seg
        from .checker.cr3xx import _ABORTING_PARAMS
        table = seg[0].upper == "DATATABLE"   # (its errors start the cascade: oracle, 2026-10-12)
        if table and index == 0 and i == 3 and seg[2].kind == "IDENT":
            if t.col == seg[2].col + len(seg[2].text):
                return seg   # `DataTable (T;,...)` names the table `T;` (`_parse_segment`)
            index, symbol = 1, ";"   # `DataTable (T ;,1,-1)`: the `;` takes the next slot
        else:
            symbol = seg_text[seg[start].col:t.col + 1].strip()

        def error(message: str) -> None:
            self.diags.items.append(Diagnostic("error", line, message, file, trigger=table))

        error(f"Invalid symbol in expression: {symbol}.")
        params = self.profile.instructions[seg[0].upper]["params"]
        if index < len(params) and params[index] in _ABORTING_PARAMS:
            depth = 1
            for j in range(i + 1, len(seg)):
                u = seg[j]
                if u.kind == "OP" and u.text in "()":
                    depth += 1 if u.text == "(" else -1
                if u.kind == "OP" and u.text == "," and depth == 1:
                    m = re.match(r"\s*([^\s,()]+)", seg_text[u.col + 1:])
                    if m:
                        error(f"Too many parameters:  {m.group(1)}.")
                    break
                if depth == 0:
                    break
            # the list has ended: later arguments are not reported again (`DataTable (T ;,1,-1,0)`)
            depth, commas = 0, 0
            for j, u in enumerate(seg):
                if u.kind == "OP" and u.text in "()":
                    depth += 1 if u.text == "(" else -1
                elif u.kind == "OP" and u.text == "," and depth == 1:
                    commas += 1
                    if commas == len(params):
                        close = next(k for k in range(len(seg) - 1, j, -1)
                                     if seg[k].kind == "OP" and seg[k].text == ")")
                        seg = seg[:j] + seg[close:]
                        break
        return [u for u in seg if u is not t]

    def _parse_segment(self, seg: list[Token], line: int, file: str, seg_text: str) -> S.Stmt:
        if self.profile.single_pass:
            seg = self._cr3xx_argument_semicolon(seg, line, file, seg_text)
        if (len(seg) > 3 and seg[0].upper == "DATATABLE" and seg[1].text == "("
                and seg[2].kind == "IDENT" and seg[3].kind == "OP" and seg[3].text == ";"):
            # CR6/CR1000X (`_drop_semicolons` keeps only this `;`): a glued `T;` is the table name
            # `T;` (`CallTable T` is then `Could not find DataTable T.`); `T ;` makes the `;` the
            # next parameter (`Invalid, or out of place expression: ;.`, `Too many parameters:
            # -1.`), like a second word below (oracle, 2026-10-12)
            if seg[3].col == seg[2].col + len(seg[2].text):
                name = seg[2].text + ";"
                seg = seg[:2] + [lexer.Token("IDENT", name, name.upper(), seg[2].col,
                                             seg[2].line)] + seg[4:]
            else:
                self.diags.error(line, "Invalid, or out of place expression: ;.", file)
                seg = seg[:3] + [lexer.Token("OP", ",", ",", seg[3].col, seg[3].line),
                                 lexer.Token("NUMBER", "0", "0", seg[3].col, seg[3].line, 0)] + seg[4:]
        if (len(seg) > 3 and seg[0].kind == "IDENT" and seg[0].upper in _RAW_NAME_FIRST
                and seg[1].kind == "OP" and seg[1].text == "(" and seg[2].kind == "IDENT"
                and seg[3].kind == "IDENT"):
            # The table name is read up to whitespace too, so a second word becomes the next
            # parameter: `DataTable (Ten Sec,True,-1)` names `Ten` and reports `Too many
            # parameters:  -1.`; `ConstTable (my Tab,0)` compiles (oracle, 2026-10-01)
            seg = seg[:3] + [lexer.Token("OP", ",", ",", seg[3].col, seg[3].line)] + seg[3:]
        if len(seg) > 2 and seg[0].kind == "IDENT" and seg[0].upper == "CALLTABLE" and (
                seg[1].kind == "NUMBER" or seg[1].kind == "OP" and seg[1].text == "("
                and len(seg) > 3 and seg[2].kind == "NUMBER"):
            at = 1 if seg[1].kind == "NUMBER" else 2   # `CallTable 15Min` / `CallTable(15Min)`
            m = _DIGIT_NAME.match(seg_text, seg[at].col)
            if m:
                rest = [t for t in seg[at + 1:] if t.col >= m.end()]
                seg = seg[:at] + [lexer.Token("IDENT", m.group(0), m.group(0).upper(), seg[at].col,
                                              seg[at].line)] + rest
        if (len(seg) > 3 and seg[0].kind == "IDENT" and seg[0].upper == "DATATABLE"
                and seg[1].kind == "OP" and seg[1].text == "(" and seg[2].kind in ("NUMBER", "STRING")):
            # a table name starting with a digit (`DataTable(15Min,...)`, `("15Min")`) is one name,
            # reported by the checker (oracle, all four loggers, 2026-10-11); one with a dot
            # (`1.5`, `.5`, `1.5x`, `"1.5"`) is read whole too (`DataTable name 1.5 is illegal.`)
            m = _DIGIT_NAME.match(seg_text, seg[2].col)
            if seg[2].kind == "STRING" and _DIGIT_NAME.fullmatch(str(seg[2].value)):
                name, end = str(seg[2].value), seg[2].col + len(seg[2].text)
            elif m and seg[2].kind == "NUMBER":
                name, end = m.group(0), m.end()
            else:
                name = end = None
            if name:
                rest = [t for t in seg[3:] if t.col >= end]
                seg = seg[:2] + [lexer.Token("IDENT", name, name.upper(), seg[2].col, seg[2].line)] + rest
        p = _TokStream(seg, seg_text)
        try:
            if (self._in_struct and seg and seg[0].kind == "IDENT"
                    and seg[0].upper not in self.profile.instructions):
                # StructureType members: `Name`, `A, B, C`, `Name As Double`,
                # `Arr(2) As String * 110` -- a declaration list without Public/Dim (the real
                # compilers accept bare members; structuretype_3.CR6 on CR1000X).
                vars_ = [self._parse_vardecl(p, line, file)]
                while p.is_op(","):
                    p.next()
                    if p.at_end():
                        break
                    vars_.append(self._parse_vardecl(p, line, file))
                stmt = S.Declaration(line, file, kind="STRUCTUREMEMBER", vars=vars_)
            else:
                stmt = self._parse_stmt(p, line, file, seg_text)
            if isinstance(stmt, S.Instruction) and stmt.keyword in ("STRUCTURETYPE", "ENDSTRUCTURETYPE"):
                self._in_struct = stmt.keyword == "STRUCTURETYPE"
            if isinstance(stmt, S.Instruction) and stmt.keyword in ("DATATABLE", "ENDTABLE"):
                self._in_table = stmt.keyword == "DATATABLE"
            if stmt is None:
                return S.Unparsed(line, file, text=seg_text, reason="empty statement")
            if not p.at_end():
                extra = p.peek()
                return S.Unparsed(
                    line, file, text=seg_text,
                    reason=f"unexpected trailing input starting at {extra.text or extra.kind!r}",
                )
            stmt.text = seg_text
            return stmt
        except _ParseError as e:
            if re.match(r"expected '\)', found '\.\d+'", str(e)) and _SNMP_OID_STATEMENT.match(seg_text):
                return S.Unparsed(line, file, text=seg_text, reason=SNMP_OID_REASON)
            if (placed := self._table_only_outside_table(seg, p, line, file, seg_text)) is not None:
                return placed
            return S.Unparsed(line, file, text=seg_text, reason=str(e))
        except Exception as e:  # never raise out of the parser
            return S.Unparsed(line, file, text=seg_text, reason=f"internal error: {e!r}")

    def _table_only_outside_table(self, seg: list[Token], p: _TokStream, line: int, file: str,
                                  seg_text: str) -> S.Instruction | None:
        """`Sample (1,x y,FP2)` outside a table (oracle, all four loggers, 2026-10-13): the source
        argument (the second) of the output-processing instructions is not read before `X must be
        used inside a table declaration.`, so a parse failure inside it, in a balanced argument
        list, still leaves the instruction; the later arguments become placeholders."""
        if (self._in_table or self._in_struct or len(seg) < 4 or seg[0].kind != "IDENT"
                or seg[0].upper not in _DEFERRED_SOURCE_INSTRUCTIONS
                or seg[0].upper not in self.profile.instructions
                or seg[1].kind != "OP" or seg[1].text != "("):
            return None
        depth, commas = 0, []
        for i, tok in enumerate(seg[1:], 1):
            if tok.kind != "OP":
                continue
            depth += (tok.text == "(") - (tok.text == ")")
            if depth < 0 or (depth == 0 and tok.text == ")" and seg[i + 1:] and seg[i + 1].kind != "EOF"):
                return None
            if tok.text == "," and depth == 1:
                commas.append(i)
        if depth != 0 or len(commas) < 2 or not commas[0] < p.i <= commas[1]:
            return None
        sub = _TokStream(seg[2:commas[0]] + [Token("EOF", "", "", seg[commas[0]].col)], seg_text)
        try:
            first = self._parse_expr(sub, line, file)
        except _ParseError:
            return None
        if not sub.at_end():
            return None
        args: list[S.Expr | None] = [first] + [S.Number(line, file, value=0, text="0") for _ in commas]
        return S.Instruction(line, file, text=seg_text, keyword=seg[0].upper, args=args,
                             arg_spans=[None] * len(args))

    # ------------------------------------------------------- statements

    def _parse_stmt(self, p: _TokStream, line: int, file: str, seg_text: str) -> S.Stmt | None:
        if p.at_end():
            return None
        first = p.peek()
        if first.kind == "OP" and first.text in ("!", "@"):
            # `!ptr = ...`: a pointer dereference used as an assignment target (see
            # docs/frontend.md; pointervariable_3.CR6).
            return self._parse_assignment_or_call(p, line, file)
        if first.kind != "IDENT":
            start = p.i
            if first.text == "(" and not self.profile.single_pass:
                # `(Y) = 1`, `(A(1)) += 1`: a parenthesised variable is an assignment target on
                # CR6/CR1000X (oracle, 2026-10-12); CR300/CR350 reject a leading group
                try:
                    stmt = self._parse_assignment_or_call(p, line, file)
                except _ParseError:
                    stmt = None
                if isinstance(stmt, S.Assignment) and isinstance(
                        stmt.target, (S.Name, S.Index, S.Member)):
                    return stmt
                p.i = start
            if first.kind in ("NUMBER", "STRING") or (first.kind == "OP" and first.text in ("(", "-", "+")):
                if (result := self._try_logical_assign(p, start, line, file)) is not None:
                    return result  # `1 And Y = 1`, `-X And Y = 1`
                p.i = start
            raise _ParseError(
                f"statement does not start with an identifier (found {first.text or first.kind!r})"
            )
        kw = first.upper
        if kw == "IF":
            return self._parse_if(p, line, file)
        if kw == "ELSEIF":
            return self._parse_elseif(p, line, file)
        if kw == "FOR":
            return self._parse_for(p, line, file)
        if kw in ("DO", "LOOP"):
            return self._parse_doloop(p, line, file, kw)
        if kw == "SELECT":
            return self._parse_select(p, line, file)
        if kw == "CASE":
            return self._parse_case(p, line, file)
        if kw in ("SUB", "FUNCTION"):
            return self._parse_subdef(p, line, file, kw)
        if kw == "CONST":
            return self._parse_const(p, line, file)
        if kw in ("PUBLIC", "DIM") and p.is_op(".", 1):
            return self._parse_assignment_or_call(p, line, file)  # `Public.X = 1`
        if kw in ("PUBLIC", "DIM"):
            return self._parse_declaration(p, line, file, kw)
        if kw == "ALIAS":
            return self._parse_alias(p, line, file)
        if kw == "UNITS":
            # `_parse_line` intercepts every top-level `:`-segment starting with `Units` before
            # tokenising it (see `_parse_units_segment`); reached here only for a `Units` nested
            # inside something already tokenised, such as an inline `If`'s then-clause, which is
            # not a real CRBasic pattern.
            raise _ParseError("Units nested inside another statement is not supported")
        if kw == "CALL":
            return self._parse_call(p, line, file)
        if kw in self.profile.instructions:
            return self._parse_generic_instruction(p, line, file, kw)
        return self._parse_assignment_or_call(p, line, file)

    def _parse_generic_instruction(self, p: _TokStream, line: int, file: str, kw: str) -> S.Stmt:
        outer, self._to_ok = self._to_ok, True  # `To` in arguments compiles (docs/checker.md)
        try:
            return self._parse_generic_instruction_args(p, line, file, kw)
        finally:
            self._to_ok = outer

    def _parse_generic_instruction_args(self, p: _TokStream, line: int, file: str, kw: str) -> S.Stmt:
        p.next()
        args: list[S.Expr | None] = []
        spans: list[tuple[int, int] | None] = []
        if p.is_op("("):
            p.next()
            # `strict=False`: an instruction's own argument list left open to end-of-line
            # silently completes right there, oracle-confirmed (`docs/checker.md` "Need closing )
            # shapes"), unlike a call reached through the general expression grammar.
            args, spans = self._parse_arglist(p, ")", line, file, strict=False)
            self._absorb_trailing(p, args, spans, line, file, kw)
        elif p.is_op("="):
            # A handful of instructions (`StationName=Value`) take `=Value` instead of an
            # argument list, confirmed valid CRBasic by corpus testing; see docs/frontend.md.
            p.next()
            args, spans = self._parse_bare_arglist(p, line, file)
        elif kw == "READONLY":
            # names split by commas or blanks; empty items are ignored (oracle, all four loggers,
            # 2026-10-03: `ReadOnly A,`, `ReadOnly A,,B`, `ReadOnly A B` compile)
            while not p.at_end():
                if p.is_op(","):
                    p.next()
                    continue
                start_col = p.peek().col
                args.append(self._parse_expr(p, line, file))
                spans.append((start_col, p.peek().col))
        elif not p.at_end():
            args, spans = self._parse_bare_arglist(p, line, file)
            if p.is_op(";") and kw == "EXIT" and not self._inline_body:
                self._absorb_trailing(p, args, spans, line, file, kw)
        if kw == "END" and len(args) == 1 and isinstance(args[0], S.Name) and args[0].name.upper() == "TABLE":
            # `End Table` is `EndTable` on all four loggers (oracle, 2026-10-08; docs/fuzz-findings.md).
            return S.Instruction(line, file, keyword="ENDTABLE")
        return S.Instruction(line, file, keyword=kw, args=args, arg_spans=spans)

    def _absorb_trailing(self, p: _TokStream, args: list, spans: list, line: int, file: str,
                         kw: str) -> None:
        """`Instr(a,b) tail`: the real compiler appends one gap-free tail chunk as one more
        parameter (`PortSet(C1,1) 1` == `PortSet(C1,1,1)`), and ignores stray `)` `,` `=` tokens
        (oracle, all four loggers, 2026-10-04, `docs/checker.md` "Trailing tokens after an
        instruction call"). Anything else is left unconsumed and stays a rejected statement."""
        rest = [t for t in p.remaining() if t.kind != "EOF"]
        if not rest:
            return
        pieces: list[list[Token]] = [[]]
        for t in rest:
            if t.kind == "OP" and t.text == ",":
                pieces.append([])
            else:
                pieces[-1].append(t)
        params = self.profile.instructions[kw]["params"]
        found: list[tuple[S.Expr, tuple[int, int]]] = []
        if (len(args) >= len(params) and rest[0].kind == "OP" and rest[0].text == ";"
                and not self._inline_body):
            # `Battery(X) ;`: a `;` after the last parameter is one more (gap-free) parameter
            # (`Too many parameters:  ;.`, oracle, all four loggers, 2026-10-12)
            end = 1
            while end < len(rest) and rest[end].kind == "OP" and rest[end].text == ";" \
                    and rest[end].col == rest[end - 1].col + 1:
                end += 1
            text = ";" * end
            args.append(S.Name(line, file, name=text))
            spans.append((rest[0].col, rest[0].col + end))
            p.i = len(p.toks) - 1
            return
        for piece in pieces:
            slot = len(args) + len(found)
            const_slot = slot >= len(params) or params[slot] in (4, 104)
            got = self._tail_chunk(piece, p.seg_text, line, file, const_slot)
            if got is None and const_slot:
                # a constant-integer slot, or one past the last: whitespace separates parameters
                # (`PortSet(C1,1) 1 2` -> `Too many parameters:  2.`, `docs/checker.md`)
                groups: list[list[Token]] = [[piece[0]]] if piece else []
                for a, b in zip(piece, piece[1:]):
                    if b.col == a.col + len(a.text):
                        groups[-1].append(b)
                    else:
                        groups.append([b])
                got = [self._tail_chunk(g, p.seg_text, line, file, True) for g in groups]
                if not got or None in got:
                    return
                found.extend(g for g in got if g is not True)
                continue
            if got is None and piece:
                # an expression slot reads the whole piece as one expression (`Delay(1,1) 1 +2`)
                got = self._whole_expr(piece, p.seg_text, line, file)
            if got is None:
                return
            if got is not True:
                found.append(got)
        for expr, span in found:
            args.append(expr)
            spans.append(span)
        p.i = len(p.toks) - 1

    def _absorb_sub_tail(self, p: _TokStream, args: list, line: int, file: str) -> bool:
        """`S(a) tail` (user `Sub` call): the tail is more comma-separated arguments (oracle, all
        four loggers, 2026-10-04, `docs/checker.md` "Trailing tokens after a Sub call"). Modelled:
        pieces that are one complete expression (gaps allowed, or one with a stray closing
        `)`); empty pieces count except a final one; a lone `)` or `,` is one empty argument. Anything else stays
        unparsed. After empty parentheses `S()` the whole tail is ignored (returns True)."""
        rest = [t for t in p.remaining() if t.kind != "EOF"]
        if not rest:
            return False
        if not args:
            p.i = len(p.toks) - 1
            return True
        pieces: list[list[Token]] = [[]]
        depth = 0
        for t in rest:
            if t.kind == "OP" and t.text == "(":
                depth += 1
            elif t.kind == "OP" and t.text == ")":
                depth = max(0, depth - 1)
            if t.kind == "OP" and t.text == "," and depth == 0:
                pieces.append([])
            else:
                pieces[-1].append(t)
        if not pieces[-1]:
            pieces.pop()
        found: list[S.Expr | None] = []
        for piece in pieces:
            if not piece:
                found.append(None)
            elif len(piece) == 1 and piece[0].text == ")":
                if len(pieces) > 1:
                    return False
                found.append(None)
            else:
                got = self._whole_expr(piece, p.seg_text, line, file)
                if got is None:
                    return False
                found.append(got[0])
        args.extend(found)
        p.i = len(p.toks) - 1
        return False

    def _whole_expr(self, piece: list[Token], seg_text: str, line: int, file: str):
        """`piece` (gaps allowed) as one expression, or with one stray trailing `)` dropped ->
        `(expr, span)`, else `None`."""
        for cand in (piece, piece[:-1] if piece[-1].text == ")" else None):
            if not cand:
                continue
            sub = _TokStream(cand + [lexer.Token("EOF", "", "", cand[-1].col)], seg_text)
            try:
                expr = self._parse_expr(sub, line, file)
            except _ParseError:
                continue
            if sub.at_end():
                return expr, (cand[0].col, cand[-1].col + len(cand[-1].text))
        return None

    def _tail_chunk(self, piece: list[Token], seg_text: str, line: int, file: str,
                    const_slot: bool = False):
        """One comma-free tail piece -> `(expr, span)`, `True` (only stray `)`/`=`), or `None`."""
        lead = 0
        while lead < len(piece) and piece[lead].kind == "OP" and piece[lead].text == "(":
            lead += 1
        loose = [t for t in piece[lead:] if not (t.kind == "OP" and t.text in (")", "="))] if lead and const_slot else []
        for chunk in (piece, [t for t in piece if not (t.kind == "OP" and t.text in (")", "="))],
                      loose):
            if not chunk:
                if chunk is loose:
                    continue
                return True
            if any(a.kind == "STRING" or b.col != a.col + len(a.text) for a, b in zip(chunk, chunk[1:])):
                continue
            sub = _TokStream(chunk + [lexer.Token("EOF", "", "", chunk[-1].col)], seg_text)
            try:
                expr = self._parse_expr(sub, line, file)
            except _ParseError:
                continue
            if sub.at_end():
                return expr, (chunk[0].col, chunk[-1].col + len(chunk[-1].text))
        return None

    def _parse_select(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # SELECT
        p.expect_ident("CASE")
        self._to_ok = True
        try:
            expr = self._parse_expr(p, line, file)
        finally:
            self._to_ok = False
        return S.Instruction(line, file, keyword="SELECT", args=[expr])

    def _parse_call(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # CALL
        name_tok = p.expect_ident()
        j = p.i
        while p.toks[j].kind == "IDENT":
            j += 1
        if j > p.i and (p.toks[j].kind == "EOF" or (p.toks[j].kind == "OP" and p.toks[j].text == "(")):
            # spaces inside the Sub name are dropped: `Call r un(A,B)` calls `run` (oracle, all
            # four loggers, 2026-10-12; fuzz false reject `cr1000x/false_reject/f4932fe73ffd`)
            merged = name_tok.text + "".join(t.text for t in p.toks[p.i:j])
            name_tok = dataclasses.replace(name_tok, text=merged, upper=merged.upper())
            p.i = j
        args: list[S.Expr | None] = []
        if p.is_op("("):
            p.next()
            args, _spans = self._parse_arglist(p, ")", line, file, strict=False)  # see above
            if len(args) > 1 and args[-1] is None and not (p.toks[p.i - 1].kind == "OP"
                                                          and p.toks[p.i - 1].text == ")"):
                args.pop()   # `Call S(a,b,` (never closed): the empty slot after the last comma is
                # not an argument (oracle, all four loggers, 2026-10-12)
            ignored = self._absorb_sub_tail(p, args, line, file)
            return S.Call(line, file, name=name_tok.text, args=args, explicit=True,
                          tail_ignored=ignored)
        elif p.is_op(";") and not self._inline_body:
            # `Call Foo ;` links to the sub named `Foo;` (`structure._call_link_name`)
            args = [S.Name(line, file, name=";")]
            p.i = len(p.toks) - 1
        elif not p.at_end():
            args, _spans = self._parse_bare_arglist(p, line, file)
        return S.Call(line, file, name=name_tok.text, args=args, explicit=True)

    def _try_logical_assign(self, p: _TokStream, start: int, line: int, file: str
                            ) -> S.Stmt | None:
        """The `LOGICAL_ASSIGN` reading of the whole statement from token `start`, or None
        (position restored by the caller)."""
        p.i = start
        saved = p.toks
        n = len(saved) - 1
        while self.profile.name in ("cr6", "cr1000x") and n > start and saved[n - 1].kind == "OP" \
                and saved[n - 1].text == ";":
            n -= 1  # a trailing `;` is skipped like a blank on CR6/CR1000X (`X AND Y = 1 ;`)
        p.toks = saved[:n] + saved[-1:]
        self._logical_assign = True
        try:
            expr = self._parse_expr(p, line, file)
        except _ParseError:
            expr = None
        finally:
            self._logical_assign = False
        if expr is not None and p.at_end() and _has_assign(expr):
            return S.Unparsed(line, file, text="", reason=LOGICAL_ASSIGN, expr=expr)
        p.toks = saved
        return None

    def _parse_assignment_or_call(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        # An lvalue only (never a full expression): `=` is also the relational operator, so
        # going through `_parse_expr` here would swallow it as an equality test instead of
        # leaving it for the assignment check below (found by corpus testing; see
        # docs/frontend.md).
        # `strict=False`: this atom may turn out to be a bare call's own name (`Foo(1,2,3` with
        # nothing further), which tolerates an unclosed paren at end-of-line just like `Call`/a
        # recognised instruction (oracle-confirmed) -- but only for *this* atom's own outermost
        # parens; anything nested inside it is parsed via `_parse_expr` and is strict again.
        start = p.i
        if p.is_ident("NOT") and (result := self._try_logical_assign(p, start, line, file)):
            return result  # `Not X And Y = 1`
        p.i = start
        outer, self._to_ok = self._to_ok, True  # `A(1 To 2) = 3` compiles
        try:
            target = self._parse_atom(p, line, file, strict=False)
        except _ParseError:
            if (result := self._try_logical_assign(p, start, line, file)) is not None:
                return result
            raise
        finally:
            self._to_ok = outer
        t = p.peek()
        if (t.kind == "OP" and t.text == "=" and isinstance(target, S.Index) and not target.args
                and p.toks[p.i + 1].kind == "EOF"):
            # `S2() =`: a Sub call whose tail is ignored (oracle, 2026-10-04, all four loggers)
            p.i = len(p.toks) - 1
            return S.Call(line, file, name=target.name, args=[], explicit=False, tail_ignored=True)
        if t.kind == "OP" and (t.text == "=" or t.text in _COMPOUND_OPS):
            op_tok = p.next()
            self._to_ok = True  # `X = 1 To 12`, `X += 1 To 12` (oracle, 2026-10-10)
            try:
                value = self._parse_expr(p, line, file)
            finally:
                self._to_ok = False
            if op_tok.text != "=":
                base_op = op_tok.text[0]
                value = S.Binary(op_tok.line, file, base_op, target, value)
            return S.Assignment(line, file, target=target, value=value,
                                compound=op_tok.text != "=")
        if (p.peek().kind == "IDENT" and p.peek().upper in _WORD_BINARY_OPS - {"MOD"}) or (
                p.peek().kind == "OP" and p.peek().text in _LOGICAL_LEAD_OPS):
            # `X > 2 And Y = 1`, `X + 1 And Y = 1`, `(X > 2) And Y = 1`: any left operand may
            # lead into the logical-assignment reading (oracle, all four loggers, 2026-10-12)
            end = p.i
            if (result := self._try_logical_assign(p, start, line, file)) is not None:
                return result
            p.i = end
        if isinstance(target, S.Name) and p.is_ident("AS") and (
                self._in_struct or not self.profile.single_pass):
            # (CR300/CR350 outside a StructureType read `Flag As Boolean` as an expression: oracle,
            # 2026-10-11, `chm/not_1.CR6`)
            # A StructureType member declaration (`Frequency As Float`, no Public/Dim prefix):
            # confirmed valid CRBasic inside StructureType...EndStructureType by corpus testing
            # (structuretype_1.CR6); see docs/frontend.md. Reuses `Declaration`/`VarDecl` with a
            # distinct `kind` rather than adding a new node.
            p.next()  # AS
            type_tok = p.expect_ident()
            vtype = type_tok.upper
            if p.is_op("!"):
                p.next()  # pointer type (see docs/frontend.md)
                vtype += "!"
            strsize = None
            if vtype == "STRING" and p.is_op("*"):
                p.next()
                strsize = self._parse_expr(p, line, file)
            var = S.VarDecl(name=target.name, dims=[], type=vtype, string_size=strsize, init=None)
            return S.Declaration(line, file, kind="STRUCTUREMEMBER", vars=[var])
        if isinstance(target, S.Name):
            return S.Call(line, file, name=target.name, args=[], explicit=False)
        if isinstance(target, S.Index) and (target.name.upper() in self.function_names or (
                self.profile.single_pass and target.name.upper() in self.profile.functions
                and target.name.upper() not in self.profile.instructions)):
            # a `Function` call statement is one expression (oracle, 2026-10-08: `F0()+Nope`
            # and `Fn(1)+Zork` report the undefined name, `Fn(1)+1` compiles), not a Sub tail
            if p.at_end():
                return S.Call(line, file, name=target.name, args=list(target.args), explicit=False)
            t = p.peek()
            if (t.kind in ("IDENT", "NUMBER", "STRING") and t.upper not in _WORD_BINARY_OPS) or (
                    t.kind == "OP" and t.text in ("(", ",")):
                # a juxtaposed tail (`Fn(1) 1`, `Fn(1),Zork`): `Incomplete expression` (oracle,
                # 2026-10-08, all four loggers); its operands are still name-checked
                juxt: list = []
                comma = t.kind == "OP"  # `,` or `(`: tail names are not checked
                while not p.at_end():
                    if p.peek().kind == "OP" and p.peek().text == ",":
                        p.i += 1
                        continue
                    juxt.append(self._parse_expr(p, line, file))
                return S.Call(line, file, name=target.name, args=list(target.args),
                              explicit=False, juxtaposed=juxt, juxt_opaque=comma)
            p.i = start
            expr = self._parse_expr(p, line, file)
            if not p.at_end():
                t = p.peek()
                if not (self.profile.single_pass and t.kind in ("IDENT", "NUMBER", "STRING")
                        and t.upper not in _WORD_BINARY_OPS):
                    raise _ParseError("expected an assignment or a call")
                # a complete expression, then a juxtaposed operand (`StrComp(a,b) > 0 Then`): CR300/
                # CR350 (oracle, 2026-10-11) name the first operand, then `Incomplete expression`
                juxt = []
                while not p.at_end():
                    juxt.append(self._parse_expr(p, line, file))
                return S.Call(line, file, name=target.name, args=list(target.args), explicit=False,
                              tail_expr=expr, juxtaposed=juxt)
            return S.Call(line, file, name=target.name, args=list(target.args), explicit=False,
                          tail_expr=expr)
        if isinstance(target, S.Index):
            args = list(target.args)
            if len(args) > 1 and args[-1] is None and not (p.toks[p.i - 1].kind == "OP"
                                                          and p.toks[p.i - 1].text == ")"):
                args.pop()   # never closed: see `_parse_call`
            ignored = self._absorb_sub_tail(p, args, line, file)
            return S.Call(line, file, name=target.name, args=args, explicit=False,
                          tail_ignored=ignored)
        raise _ParseError("expected an assignment or a call")

    # ---- If / ElseIf ----------------------------------------------------

    def _parse_if(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # IF
        outer, self._to_ok = self._to_ok, True  # `If X To 2 Then` compiles (oracle, round 9)
        try:
            cond = self._parse_expr(p, line, file)
        finally:
            self._to_ok = outer
        then_tok = None
        if p.is_ident("THEN"):
            then_tok = p.next()
        elif not p.at_end():
            # Block-form `If` may omit `Then` entirely (confirmed with the oracle: `If Flag` /
            # `EndIf` compiles) -- but inline form (something follows on the line) still needs
            # it, so only tolerate the omission when nothing follows. See docs/frontend.md.
            raise _ParseError(f"expected THEN, found {p.peek().text or p.peek().kind!r}")
        if p.at_end():
            return S.If(line, file, cond=cond, inline=None, inline_else=None)
        rest = [t for t in p.remaining() if t.kind != "EOF"]
        p.i = len(p.toks) - 1  # consume the rest of this segment
        depth = 0
        else_idx = None
        for idx, t in enumerate(rest):
            if t.kind == "OP" and t.text == "(":
                depth += 1
            elif t.kind == "OP" and t.text == ")":
                depth = max(0, depth - 1)
            elif t.kind == "IDENT" and t.upper == "ELSE" and depth == 0:
                else_idx = idx
                break
        if else_idx is None:
            then_toks, else_toks = rest, None
        else:
            then_toks, else_toks = rest[:else_idx], rest[else_idx + 1:]
        # The first statement after `Then`/`Else` is echoed from one column past the keyword to
        # one column before `Else` or the segment's end, trailing spaces kept (oracle, 2026-10-01).
        text_end = len(p.seg_text)
        else_tok = rest[else_idx] if else_idx is not None else None
        then_bounds = (then_tok.col + len(then_tok.text) + 1 if then_tok else None,
                       else_tok.col - 1 if else_tok else text_end)
        then_stmts = self._parse_stmt_list(then_toks, line, file, p.seg_text, then_bounds)
        inline_else = self._parse_stmt_list(
            else_toks, line, file, p.seg_text,
            (else_tok.col + len(else_tok.text) + 1, text_end)) if else_toks is not None else None
        return S.If(line, file, cond=cond, inline=then_stmts, inline_else=inline_else)

    def _parse_stmt_list(self, toks: list[Token], line: int, file: str, outer: str = "",
                         bounds: tuple[int | None, int] | None = None) -> list[S.Stmt]:
        """Statements of a one-line `If`. Each gets its verbatim source slice as `text` (the real
        compiler echoes `X=Q+1`, oracle 2026-09-27) with `arg_spans` re-based onto it, and
        `at_eol` False (no newline in the CR300/CR350 echo)."""
        segs = _split_colon_segments(toks + [_eof_like(line)])
        out = []
        for seg in segs:
            real = [t for t in seg if t.kind != "EOF"]
            if not real:
                continue
            start, end = real[0].col, real[-1].col + len(real[-1].text)
            if bounds and seg is segs[0]:
                if bounds[0] is not None:
                    start = min(start, bounds[0])
                end = max(end, bounds[1])
            verbatim = outer[start:end] if outer and len(outer) >= end else ""
            self._inline_body = True
            try:
                stmt = self._parse_segment(seg, line, file, verbatim or " ".join(t.text for t in real))
            finally:
                self._inline_body = False
            if verbatim:
                if hasattr(stmt, "text"):
                    stmt.text = verbatim
                spans = getattr(stmt, "arg_spans", None)
                if spans:
                    stmt.arg_spans = [None if sp is None else (sp[0] - start, sp[1] - start)
                                      for sp in spans]
            stmt.at_eol = False
            out.append(stmt)
        return out

    def _parse_elseif(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # ELSEIF
        outer, self._to_ok = self._to_ok, True
        try:
            cond = self._parse_expr(p, line, file)
        finally:
            self._to_ok = outer
        if p.is_ident("THEN"):
            p.next()
            p.i = len(p.toks) - 1   # whatever follows `Then` on the line is ignored (oracle, 2026-10-12)
        return S.ElseIf(line, file, cond=cond)

    # ---- For --------------------------------------------------------

    def _parse_for(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # FOR
        try:
            var = self._parse_atom(p, line, file)  # lvalue only; see `_parse_assignment_or_call`
            p.expect_op("=")
        except _ParseError:
            raise _ParseError("For missing parameter")
        if p.at_end():
            raise _ParseError("For missing parameter")
        start = self._parse_expr(p, line, file)
        if p.at_end():
            raise _ParseError("For missing parameter")
        p.expect_ident("TO")
        if p.at_end():
            raise _ParseError("For missing parameter")
        end = self._parse_expr(p, line, file)
        step = None
        if p.is_ident("STEP"):
            p.next()
            step = self._parse_expr(p, line, file)
        return S.For(line, file, var=var, start=start, end=end, step=step)

    # ---- Do / Loop ----------------------------------------------------

    def _parse_doloop(self, p: _TokStream, line: int, file: str, which: str) -> S.Stmt:
        p.next()  # DO or LOOP
        cond_kind = None
        cond = None
        if p.is_ident("WHILE") or p.is_ident("UNTIL"):
            tok = p.next()
            cond_kind = tok.upper
            cond = self._parse_expr(p, line, file)
        elif not p.at_end():
            # `Do X`/`Loop "a"`: the word is read as an instruction parameter (oracle, 2026-10-10)
            t = p.peek(1) if p.is_op("(") and p.peek(1).kind == "IDENT" else p.peek()
            if t.kind in ("IDENT", "STRING"):
                raise _ParseError(f"Do/Loop parameter: Illegal parameter {t.text}, "
                                  "expecting constant integer.")
            if (t.kind == "NUMBER" and p.peek(1).kind == "EOF") or (
                    t.kind == "OP" and t.text == "-" and p.peek(1).kind == "NUMBER"
                    and p.peek(2).kind == "EOF"):
                raise _ParseError("Do/Loop parameter: Invalid or missing expression.")
        return S.DoLoop(line, file, keyword=which, cond_kind=cond_kind, cond=cond)

    # ---- Case -----------------------------------------------------------

    def _parse_case(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # CASE
        if p.is_ident("ELSE") and p.peek(1).kind == "EOF":
            p.next()
            return S.Case(line, file, items=[], is_else=True)
        items = []
        while True:
            if p.is_ident("IS"):
                p.next()
                op_tok = p.next()
                if not (op_tok.kind == "OP" and op_tok.text in _RELOPS):
                    raise _ParseError(f"expected a comparison after Is, found {op_tok.text!r}")
                e = self._parse_expr(p, line, file)
                items.append(("is", op_tok.text, e))
            else:
                lo = self._parse_expr(p, line, file)
                if p.is_ident("TO"):
                    p.next()
                    hi = self._parse_expr(p, line, file)
                    items.append(("range", lo, hi))
                else:
                    items.append(("value", lo))
            if p.is_op(","):
                p.next()
                continue
            break
        return S.Case(line, file, items=items, is_else=False)

    # ---- Sub / Function -------------------------------------------------

    def _parse_subdef(self, p: _TokStream, line: int, file: str, which: str) -> S.Stmt:
        p.next()
        name_tok = p.expect_ident()
        params: list[S.Param] = []
        if p.is_op("("):
            p.next()
            if not p.is_op(")") and not p.at_end():
                params.append(self._parse_param(p, line, file))
                while p.is_op(","):
                    p.next()
                    if p.at_end():
                        break  # a header cut off at a comma is accepted (oracle, 2026-10-12)
                    params.append(self._parse_param(p, line, file))
            p.expect_close(")")
        return_type = None
        if which == "FUNCTION" and p.is_ident("AS"):
            p.next()
            type_tok = p.expect_ident()
            return_type = type_tok.upper
            if p.is_op("!"):
                p.next()  # pointer type (see docs/frontend.md)
                return_type += "!"
            if return_type == "STRING" and p.is_op("*"):
                p.next()
                self._parse_expr(p, line, file)  # size; SubDef has no slot for it (see docs/frontend.md)
        return S.SubDef(line, file, keyword=which, name=name_tok.text, params=params,
                         return_type=return_type)

    def _parse_param(self, p: _TokStream, line: int, file: str) -> S.Param:
        if p.is_ident("OPTIONAL"):
            p.next()
        name_tok = p.expect_ident()
        dims = 0
        dim_exprs: list[S.Expr] = []
        if p.is_op("("):
            p.next()
            if not p.is_op(")"):
                dim_exprs.append(self._parse_expr(p, line, file))
                while p.is_op(","):
                    p.next()
                    dim_exprs.append(self._parse_expr(p, line, file))
            p.expect_close(")")
            dims = 1
        vtype = None
        strsize = None
        if p.is_ident("AS"):
            p.next()
            type_tok = None if p.at_end() else p.expect_ident()  # `b As` cut off at the line end
            vtype = type_tok.upper if type_tok else None
            if p.is_op("!"):
                p.next()  # pointer type (see docs/frontend.md)
                vtype += "!"
            if vtype == "STRING" and p.is_op("*"):
                p.next()
                strsize = self._parse_expr(p, line, file)
        default = None
        if p.is_op("="):
            p.next()
            default = self._parse_expr(p, line, file)
        return S.Param(name=name_tok.text, by_ref=None, type=vtype, string_size=strsize,
                        dims=dims, default=default, dim_exprs=dim_exprs)

    # ---- Const ------------------------------------------------------

    def _parse_const(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()
        eq_at = None
        lead = False
        if p.is_op("="):
            # `Const = X ...`: the leading `=` is skipped (oracle, 2026-10-10, `docs/checker.md`
            # "Round 9"); a first item that cannot be a name is its own message
            j = p.i
            while p.is_op("="):
                p.next()
            tok, after = p.peek(), p.peek(1)
            if tok.kind == "EOF":
                return S.Unparsed(line, file, text="", reason="Const without '='")
            if tok.kind == "IDENT" and not (after.kind == "IDENT" and after.upper == "AS"):
                lead = True
            elif tok.kind == "OP" and tok.text == ",":
                p.i = len(p.toks) - 1
                return S.Unparsed(line, file, text="", reason="Const lead: CONST needs assignment.")
            elif (tok.kind == "STRING" or (tok.kind == "OP" and tok.text == "*") or (tok.kind == "NUMBER" and not tok.text.isdigit()
                                           and "E" not in tok.text.upper().lstrip("&"))
                  or (tok.kind == "OP" and tok.text == "-" and after.kind == "NUMBER")):
                if tok.kind == "OP" and tok.text == "-":
                    text = tok.text + after.text
                    rest_at = 2
                else:
                    text, rest_at = tok.text, 1
                msgs = [f"Invalid variable or constant name: {text}."]
                if text[0].isdigit():
                    msgs.append(f"Const name {text} illegal, starts with a number.")
                elif p.peek(rest_at).kind == "EOF":
                    msgs.append("CONST needs assignment.")
                p.i = len(p.toks) - 1
                return S.Unparsed(line, file, text="", reason="Const lead: " + "\n".join(msgs))
            else:
                p.i = j
        try:
            name_tok = p.expect_ident()
            vtype = None
            if p.is_ident("AS"):
                p.next()
                type_tok = p.expect_ident()
                vtype = type_tok.upper
                if p.is_op("!"):
                    p.next()  # pointer type (see docs/frontend.md)
                    vtype += "!"
                if vtype == "STRING" and p.is_op("*"):
                    p.next()
                    self._parse_expr(p, line, file)
            if lead and p.at_end():
                return S.Unparsed(line, file, text="", reason="Const lead: name " + name_tok.text)
            if lead and p.is_op(","):
                p.next()  # (`Const = X, Y`: `Y` is the value)
            elif lead and not p.is_op("="):
                pass  # (`Const = X Y`: `Y` is the value)
            else:
                p.expect_op("=")
            eq_at = p.i
            value_start_col = p.peek().col
            outer, self._to_ok = self._to_ok, True  # `Const K = 5 To 2` is 0 (oracle, round 9)
            try:
                value = self._parse_expr(p, line, file)
            finally:
                self._to_ok = outer
            value_span = (value_start_col, p.peek().col)
            if p.is_op(",") or p.is_op(")") or p.is_op("}"):
                # a top-level comma ends the statement, the rest is ignored; so does an unmatched `)`
                # (`Const Pi = 1) + 2` compiles on all four loggers, oracle 2026-10-12)
                p.i = len(p.toks) - 1
        except _ParseError:
            # `Const` with no complete `Name [As Type] = value` on its own physical line -- a CHM
            # corpus example wrapped the assignment onto the next line with no `_` continuation
            # (`rectpolar_1.CR6`: `Const` entirely alone, then `Pi=4*ATN(1)`; `sprintf_4.CR6`:
            # `Const ToAddr As` / `String = "..."`, truncated further into the grammar). The real
            # compiler reports the same message either way (oracle-confirmed, `docs/checker.md`),
            # so this doesn't try to distinguish how far through its own grammar it got.
            if eq_at is not None:
                # `=` seen, value empty or ending in a binary operator: `Invalid, or out of place
                # expression: <value>.` (oracle, 2026-10-10, docs/checker.md "Round 7")
                rest = p.toks[eq_at:]
                if rest[0].kind == "EOF" or (rest[-2].kind == "OP" and rest[-2].text in "+-*/^"
                                             and len(rest[-2].text) == 1):
                    value = p.seg_text[rest[0].col:] if rest[0].kind != "EOF" else ""
                    return S.Unparsed(line, file, text="", reason="Const bad value: " + value)
            body = re.sub(r"(?i)^\s*Const\b", "", p.seg_text, count=1)
            bad = re.match(r"\s*(?:=\s*)*(\d\w*)(?![\w.])", body)
            if bad and "=" in body:
                p.i = len(p.toks) - 1  # (the rest of the statement is consumed: the reason stands)
                return S.Unparsed(line, file, text="", reason="Const bad name: " + bad.group(1))
            if "=" not in body or body.strip() == "=":
                p.i = len(p.toks) - 1
            return S.Unparsed(line, file, text="", reason="Const without '='")
        node = S.ConstDecl(line, file, name=name_tok.text, value=value, type=vtype, value_span=value_span)
        try:
            self.env.define(name_tok.text, consteval.value_of(value, self.env),
                            consteval.is_float(value, self.env))
        except consteval.NotConstant:
            pass
        return node

    # ---- Public / Dim -------------------------------------------------

    def _parse_declaration(self, p: _TokStream, line: int, file: str, kind: str) -> S.Stmt:
        p.next()
        if kind == "DIM" and p.is_ident("PUBLIC"):
            p.next()
            kind = "PUBLIC"
        if p.at_end():
            # `Public`/`Dim` with nothing at all following on its own physical line -- a CHM
            # corpus example wrapped the name list onto the next line with no `_` continuation
            # (`log10_1.CR6`: `Public` / `Number, B, Log10ofNumber, LogBofNumber`). The real
            # compiler's declaration parser reports `Variable not named.` right here rather than
            # raising a generic parse error (oracle-confirmed, `docs/checker.md`); modelled as a
            # `Declaration` with an empty `vars` list so `structure.py` can emit that specific
            # message instead of the generic "not a valid statement" fallback an `Unparsed` would
            # get.
            return S.Declaration(line, file, kind=kind, vars=[])
        vars_ = [self._parse_vardecl(p, line, file)]
        while p.is_op(","):
            p.next()
            if p.at_end():
                # A trailing comma that runs off the end of the line: confirmed valid CRBasic
                # (stddevrun_3.CR6, `Public Counter As Long,` with nothing further) -- tolerated
                # the same way as an unclosed paren (`expect_close`). See docs/frontend.md.
                break
            vars_.append(self._parse_vardecl(p, line, file))
        if p.is_op(")") or p.is_op("}"):
            p.i = len(p.toks) - 1   # an unmatched `)`/`}` ends the declaration (oracle, round 14)
        return S.Declaration(line, file, kind=kind, vars=vars_)

    def _close_dims_or_raise(self, p: _TokStream, name_tok: Token) -> None:
        """A `Public`/`Dim`/`Static` dimension list's own closing `)`, unlike an instruction's
        argument list, is **not** tolerant of running off the end of the physical line: oracle-
        confirmed (`docs/checker.md` "Need closing ) shapes") that `Public x(5` alone -- or
        `Public x(5,3`, or with `EndProg`/more statements on later lines, no different -- always
        fails with `%s is missing the ) character.`, `%s` = the variable's own name plus
        everything from its own `(` through the end of *this* physical line, verbatim (identical
        on all four loggers). Only this exact run-off-the-end-of-line shape is handled here,
        mirroring `expect_close`'s own EOL condition exactly (`Token.col` indexes straight into
        `p.seg_text`, oracle-confirmed column-exact); any other malformed token in a dimension
        list (a real, non-EOL mismatch) still falls through to `expect_close`'s ordinary parse
        error, unchanged -- not attempted, no oracle evidence gathered for that shape's own
        message text.
        """
        if p.at_end():
            text = p.seg_text[name_tok.col:] if p.seg_text else name_tok.text
            raise _ParseError(f"{UNCLOSED_DIM_PREFIX}{text}")
        p.expect_close(")")

    def _parse_vardecl(self, p: _TokStream, line: int, file: str) -> S.VarDecl:
        name_tok = p.expect_ident()
        dims: list[S.Expr] = []
        dim0_span: tuple[int, int] | None = None
        paren_dim = False
        if not p.is_op("(") and not p.is_ident("AS"):
            # `Public A B(10)`, `A B.C(1)`, `A B C(1)`: the words before the parenthesis are
            # skipped, the parenthesis is the dimension (oracle, 2026-10-10)
            j = 0
            while p.peek(j).kind in ("IDENT", "NUMBER", "STRING") or (
                    j and p.peek(j).kind == "OP" and p.peek(j).text == "."):
                j += 1
            if j and p.peek(j).kind == "OP" and p.peek(j).text == "(" and not any(
                    p.peek(k).kind == "IDENT" and p.peek(k).upper == "AS" for k in range(j)):
                p.i += j
        if p.is_op("("):
            p.next()
            first = p.i
            if not p.is_op(")"):
                start_col = p.peek().col
                dims.append(self._parse_expr(p, line, file))
                dim0_span = (start_col, p.peek().col)
                while p.is_op(","):
                    p.next()
                    dims.append(self._parse_expr(p, line, file))
            paren_dim = any(t.kind == "OP" and t.text == "(" for t in p.toks[first:p.i])
            self._close_dims_or_raise(p, name_tok)
        vtype = None
        strsize = None
        init = None
        size_missing = None
        if p.is_ident("AS"):
            p.next()
            type_tok = p.expect_ident()
            vtype = type_tok.upper
            if p.is_op("!"):
                p.next()  # pointer type (see docs/frontend.md)
                vtype += "!"
            if vtype == "STRING" and p.is_op("*"):
                star = p.next()
                if p.at_end() or p.is_op(","):
                    size_missing = star.col + 1  # `String *` with no size (oracle, 2026-10-10)
                elif not dims and p.peek().kind == "NUMBER" and (
                        p.peek(1).kind == "OP" and p.peek(1).text == "("):
                    # `Public A As String * 16(5) = {...}`: the dimension follows the size
                    # (oracle, all four loggers, round 16; docs/frontend.md)
                    num = p.next()
                    strsize = S.Number(num.line, file, num.value, num.text)
                    p.next()
                    start_col = p.peek().col
                    dims.append(self._parse_expr(p, line, file))
                    dim0_span = (start_col, p.peek().col)
                    while p.is_op(","):
                        p.next()
                        dims.append(self._parse_expr(p, line, file))
                    self._close_dims_or_raise(p, name_tok)
                else:
                    strsize = self._parse_expr(p, line, file)
            elif not dims and p.is_op("("):
                # `Public floatArr As Float(5) = {...}`: the dimension can follow the type
                # instead of the variable name. Confirmed valid CRBasic by corpus testing
                # (pointervariable_1.CR6); see docs/frontend.md.
                p.next()
                if not p.is_op(")"):
                    start_col = p.peek().col
                    dims.append(self._parse_expr(p, line, file))
                    dim0_span = (start_col, p.peek().col)
                    while p.is_op(","):
                        p.next()
                        dims.append(self._parse_expr(p, line, file))
                self._close_dims_or_raise(p, name_tok)
            if p.is_op("="):
                p.next()
                val = self._parse_expr(p, line, file)
                init = list(val.items) if isinstance(val, S.ArrayLiteral) else [val]
            # Oracle-confirmed quirk (`docs/frontend.md`): once a variable's own `As Type` clause
            # has been fully parsed, any further tokens before the next comma (or end of line) are
            # silently discarded rather than rejected -- `Public X As Long asX String, Y As Float`
            # compiles, `Y` declared normally, `asX String` simply vanishes (confirmed not
            # resolvable afterwards, not a second variable).
            while not p.at_end() and not p.is_op(","):
                p.next()
        elif p.is_op("="):
            p.next()
            val = self._parse_expr(p, line, file)
            init = list(val.items) if isinstance(val, S.ArrayLiteral) else [val]
        elif dims and not (p.at_end() or p.is_op(",")):
            # Oracle-confirmed round-5 exception to the malformed-`As`-clause rule just below
            # (`docs/checker.md` "Round 5", a fuzz-found false reject): an **array** declaration
            # (`dims` non-empty) never raises here at all, regardless of what token follows the
            # dimensions -- `Public Result(4) as_Fz Boolean` compiles clean (`Result` left at its
            # default type, the same as `Public Result(4)` alone), where the exact same malformed
            # token after a *scalar* declaration (`Public Result as_Fz Boolean`, no dims) correctly
            # raises `Invalid Variable declaration as_Fz.` (oracle-confirmed side by side). A later
            # variable in the same comma list still parses normally (`Public Result(4) as_Fz
            # Boolean, Y As Long` declares `Y` fine), matching the ordinary "discard anything before
            # the next comma" quirk just above -- so this is modelled the same way, silently
            # skipping to the next comma/end of line rather than raising.
            while not p.at_end() and not p.is_op(","):
                p.next()
        elif not (p.at_end() or p.is_op(",")):
            # Oracle-confirmed (`docs/fuzz-findings.md` "5. Message diff", well evidenced by 6
            # independent fuzz mutants): a token here -- right after the variable's own name/dims,
            # where `As`, `=`, a comma or end of line was expected -- that is none of those is a
            # malformed `As` clause, most often a corrupted spelling of the keyword itself (`asX`,
            # `As_Mut`, a stray extra space splitting one identifier into two, ...). The real
            # compiler reports it immediately and abandons the *whole* declaration statement, not
            # just this one variable: no later variable in the same comma list is parsed or
            # reported, confirmed with a valid first variable followed by a malformed second one.
            bad = p.peek()
            raise _ParseError(f"Invalid As clause: {bad.text}")
        decl = S.VarDecl(name=name_tok.text, dims=dims, type=vtype, string_size=strsize, init=init,
                         dim0_span=dim0_span, paren_dim=paren_dim)
        decl.size_missing = size_missing  # column just after the `*`, or None
        return decl

    # ---- Alias / Units --------------------------------------------------

    def _parse_alias(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()
        try:
            lhs = self._parse_atom(p, line, file)  # lvalue only; see `_parse_assignment_or_call`
        except _ParseError:
            # `Alias` with no `Target = Name` on its own physical line -- the same CHM line-wrap
            # shape as `Const`/`Units`/`Public` above (`rectpolar_1.CR6`: `Alias` entirely alone,
            # then `XY(1)=X`). Oracle-confirmed message: `docs/checker.md`.
            return S.Unparsed(line, file, text="", reason="Alias without '='")
        try:
            p.expect_op("=")
        except _ParseError:
            # Target parsed fine (present, valid lvalue), but the `=` itself is what runs off the
            # end of this physical line (`realtime_1.CR6`: `Alias rTime(1)` / `= Year` on the next
            # line, no `_` continuation) -- a distinct reason from the fully-empty case above so
            # `checker/structure.py` can echo the oracle-confirmed embedded-newline shape
            # (`docs/checker.md` "Echo quirk"): `Need '=' in alias definition: rTime(1)\n.`.
            while not p.at_end():
                p.next()  # junk after the target belongs to the same message
            return S.Unparsed(line, file, text="", reason="Alias target with no '='")
        if p.at_end():
            return S.Unparsed(line, file, text="", reason="Alias nothing after '='")
        if p.peek().kind == "IDENT":
            # Each comma-separated item is one name (optionally indexed); anything after it up to
            # the next comma is ignored (oracle, all four loggers, 2026-09-27: `= Spare2_1  3`,
            # `= Sp + 1`, `= Sp As Long` compile; in `= A1, B1` both names are declared, in
            # `= A1 B1` only `A1`) -- a fuzz-found false reject.
            rhs = []
            junk = False
            item_junk: list[bool] = []
            while not p.at_end() and p.peek().kind == "IDENT":
                rhs.append(self._parse_atom(p, line, file))
                item_junk.append(False)
                depth = 0
                while not p.at_end() and not (depth == 0 and p.is_op(",")):
                    junk = junk or len(rhs) == 1
                    item_junk[-1] = True
                    tok = p.next()
                    if tok.kind == "OP" and tok.text == "(":
                        depth += 1
                    elif tok.kind == "OP" and tok.text == ")":
                        depth = max(0, depth - 1)
                if p.at_end():
                    break
                p.next()  # the comma
            while not p.at_end():
                p.next()
            inst = S.Instruction(line, file, keyword="ALIAS", args=[lhs, *rhs])
            inst.alias_junk = junk  # something follows the first name (`= Sp 3`, `= Sp As Long`)
            inst.alias_item_junk = item_junk  # per name; the dimension checks skip such items
            return inst
        rhs = [self._parse_expr(p, line, file)]
        while p.is_op(","):
            p.next()
            rhs.append(self._parse_expr(p, line, file))
        return S.Instruction(line, file, keyword="ALIAS", args=[lhs, *rhs])

    def _parse_units_segment(self, seg_text: str, line: int, file: str) -> S.Stmt:
        """`Units` is handled before general tokenisation of its segment: `UnitLabel` is
        free-form text, not an expression (`Units Acclima_BulkEC()=uS/cm`, `Units
        Acclima_VWC()=%` -- `%` is not even a valid CRBasic token, and some real labels carry a
        raw ISO-8859-1 byte, e.g. a degree sign), so the label is taken verbatim from the source
        text rather than lexed (found by corpus testing; see docs/frontend.md).
        """
        eq = seg_text.find("=")
        if eq == -1:
            # No `=` anywhere on this physical line. If there's a target after the keyword and it
            # parses as a valid lvalue by itself (`Units x` alone, the next line starting a new
            # statement with no `_` continuation), the real compiler's echo embeds this line's own
            # trailing newline before the closing `.` (oracle probe, `docs/checker.md` "Echo
            # quirk"): `Need '=' in units definition: x\n.`. A fully empty target (`Units` alone)
            # or one that fails to parse gets the plain, un-suffixed reason instead -- no oracle
            # evidence for the latter shape.
            target_text = seg_text.strip()[len("UNITS"):].strip()
            if target_text:
                try:
                    target_tokens = lexer.tokenize(target_text, line)
                    tp = _TokStream(target_tokens)
                    self._parse_atom(tp, line, file)
                    if tp.at_end():
                        return S.Unparsed(line, file, text=target_text,
                                           reason="Units target with no '='")
                except (lexer.LexError, _ParseError):
                    pass
            return S.Unparsed(line, file, text=seg_text.strip(), reason="Units without '='")
        lhs_text, label = seg_text[:eq], seg_text[eq + 1:].strip()
        try:
            lhs_tokens = lexer.tokenize(lhs_text, line)
        except lexer.LexError as e:
            return S.Unparsed(line, file, text=seg_text.strip(),
                               reason=f"lex error in Units target: {e.message}")
        p = _TokStream(lhs_tokens)
        try:
            p.expect_ident("UNITS")
            lhs = self._parse_atom(p, line, file)
            # anything between the first target and the `=` is ignored (`Units PP  S = ms`,
            # `Units A + 1 = ms`; oracle, all four loggers, round 16). A `,` list checks every
            # name and a non-name first target has its own error, so those stay unparsed.
            extra_targets: list[S.Expr] = []
            if not p.at_end() and not isinstance(lhs, (S.Name, S.Index)):
                raise _ParseError("unexpected trailing input in Units target")
            while p.is_op(","):   # `Units A, B = ms`: every name is checked
                p.next()
                if p.at_end():
                    break
                extra = self._parse_atom(p, line, file)
                if not isinstance(extra, (S.Name, S.Index)):
                    raise _ParseError("unexpected trailing input in Units target")
                extra_targets.append(extra)
        except _ParseError as e:
            return S.Unparsed(line, file, text=seg_text.strip(), reason=str(e))
        stmt = S.Instruction(line, file, keyword="UNITS", args=[lhs, S.String(line, file, label)])
        stmt.extra_targets = extra_targets
        stmt.text = seg_text.strip()
        return stmt

    # ------------------------------------------------------- expressions

    _LOGICAL_OPS = {"IMP", "EQV", "OR", "XOR", "AND"}

    def _parse_expr(self, p: _TokStream, line: int, file: str) -> S.Expr:
        return self._parse_logical(p, line, file)

    def _parse_logical(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_not(p, line, file)
        while p.peek().kind == "IDENT" and p.peek().upper in self._LOGICAL_OPS:
            op = p.next().upper
            if self._logical_assign and self._at_assign_target(p):
                # `=` binds below the logical words here: `A AND B = C AND D` is `A AND (B = (C AND
                # D))` (oracle, all four loggers, 2026-10-10, docs/checker.md "Logical-prefixed
                # assignment targets")
                target = self._parse_atom(p, line, file)
                p.next()
                right = S.Binary(line, file, "ASSIGN", target, self._parse_logical(p, line, file))
            else:
                right = self._parse_not(p, line, file)
            left = S.Binary(line, file, op, left, right)
        return left

    @staticmethod
    def _at_assign_target(p: _TokStream) -> bool:
        """`name =`, `name(...) =` or `name.member[(...)] =` ahead."""
        if p.is_op("("):
            # `(Y) = 1`, `((Y)) = 1`, `(A(1)) = 1`: a parenthesised target (oracle, all four
            # loggers, 2026-10-12)
            depth, k = 0, 0
            while p.peek(k).kind != "EOF":
                t = p.peek(k)
                depth += (t.text == "(") - (t.text == ")") if t.kind == "OP" else 0
                k += 1
                if depth == 0:
                    break
            return p.is_op("=", k)
        if p.peek().kind != "IDENT" or p.peek().upper in _WORD_BINARY_OPS | {"NOT"}:
            return False
        k = 1
        while p.is_op(".", k) and p.peek(k + 1).kind == "IDENT":
            k += 2
        if k > 1 and not p.is_op("(", k):
            return p.is_op("=", k)
        if p.is_op("(", k):
            depth = 0
            while p.peek(k).kind != "EOF":
                t = p.peek(k)
                depth += (t.text == "(") - (t.text == ")") if t.kind == "OP" else 0
                k += 1
                if depth == 0:
                    break
        return p.is_op("=", k)

    def _parse_not(self, p: _TokStream, line: int, file: str) -> S.Expr:
        if p.is_ident("NOT"):
            tok = p.next()
            operand = self._parse_not(p, line, file)
            return S.Unary(tok.line, file, "NOT", operand)
        return self._parse_relational(p, line, file)

    def _parse_relational(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_shift(p, line, file)
        if self._to_ok and p.is_ident("TO"):
            # `To`: a binary operator between comparison and shift, no comparison on either side
            # of it, once per operand; a string literal operand is a different error (oracle,
            # 2026-10-10: `String is illegal here`)
            p.next()
            nots = []
            while p.is_ident("NOT"):  # a `Not` operand is not a relational: `1 To Not 2 To 3` is incomplete
                nots.append(p.next())
            right = self._parse_shift(p, line, file)
            for tok in reversed(nots):
                right = S.Unary(tok.line, file, "NOT", right)
            return S.Binary(line, file, "TO", left, right)
        while p.peek().kind == "OP" and p.peek().text in _RELOPS:
            op = p.next().text
            right = self._parse_shift(p, line, file)
            left = S.Binary(line, file, op, left, right)
        return left

    def _parse_shift(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_additive(p, line, file)
        while p.peek().kind == "OP" and p.peek().text in ("<<", ">>"):
            op = p.next().text
            right = self._parse_additive(p, line, file)
            left = S.Binary(line, file, op, left, right)
        return left

    def _parse_additive(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_mod(p, line, file)
        while p.peek().kind == "OP" and p.peek().text in ("&", "+", "-"):
            op = p.next().text
            right = self._parse_mod(p, line, file)
            left = S.Binary(line, file, op, left, right)
        return left

    def _parse_mod(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_intdiv(p, line, file)
        while p.is_ident("MOD"):
            p.next()
            right = self._parse_intdiv(p, line, file)
            left = S.Binary(line, file, "MOD", left, right)
        return left

    def _parse_intdiv(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_muldiv(p, line, file)
        while (p.peek().kind == "OP" and p.peek().text == "\\") or p.is_ident("INTDV"):
            # `INTDV` is a word spelling of `\` (integer division; docs/binary.md kind 311),
            # found missing by corpus testing -- see docs/frontend.md.
            p.next()
            right = self._parse_muldiv(p, line, file)
            left = S.Binary(line, file, "\\", left, right)
        return left

    def _parse_muldiv(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_unary(p, line, file)
        while p.peek().kind == "OP" and p.peek().text in ("*", "/"):
            op = p.next().text
            right = self._parse_unary(p, line, file)
            left = S.Binary(line, file, op, left, right)
        return left

    def _parse_unary(self, p: _TokStream, line: int, file: str) -> S.Expr:
        if p.peek().kind == "OP" and p.peek().text in ("-", "+"):
            tok = p.next()
            operand = self._parse_unary(p, line, file)
            return S.Unary(tok.line, file, tok.text, operand)
        if p.is_ident("NOT"):  # `1 + Not 2 To 3`, `1 = Not 2`, `-Not 2` compile: Not is an operand prefix
            tok = p.next()
            return S.Unary(tok.line, file, "NOT", self._parse_unary(p, line, file))
        return self._parse_power(p, line, file)

    def _parse_power(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_atom(p, line, file)
        while p.peek().kind == "OP" and p.peek().text == "^":
            p.next()
            # The right operand must NOT recurse into `_parse_unary` -> `_parse_power`: that
            # would let it swallow a *chain* of further `^`, making `^` effectively
            # right-associative. `^` is left-associative (confirmed with the oracle: `2^3^2 =
            # 64`, not 512 -- see docs/frontend.md), so the right operand is just a single
            # (optionally signed) atom; the enclosing `while` loop handles the chaining.
            right = self._parse_power_operand(p, line, file)
            left = S.Binary(line, file, "^", left, right)
        return left

    def _parse_power_operand(self, p: _TokStream, line: int, file: str) -> S.Expr:
        if p.peek().kind == "OP" and p.peek().text in ("-", "+"):
            tok = p.next()
            operand = self._parse_power_operand(p, line, file)
            return S.Unary(tok.line, file, tok.text, operand)
        if p.is_ident("NOT"):  # `2 ^ Not 2 To 3` compiles (oracle)
            tok = p.next()
            return S.Unary(tok.line, file, "NOT", self._parse_power_operand(p, line, file))
        return self._parse_atom(p, line, file)

    def _parse_atom(self, p: _TokStream, line: int, file: str, strict: bool = True) -> S.Expr:
        # `strict=False` is passed only from `_parse_assignment_or_call`'s own initial lvalue-or-
        # bare-call atom parse: a bare `Foo(1,2,3` (no `Call` keyword, nothing further on the
        # line) is oracle-confirmed to compile just like the explicit-`Call`/instruction-keyword
        # forms (`expect_close`'s own EOL tolerance) -- but *only* for this atom's own outermost
        # parens; an argument nested inside it (parsed via `_parse_expr`, not this parameter) is
        # ordinary and strict again, oracle-confirmed (`docs/checker.md` "Need closing ) shapes",
        # `Foo(Sin(1` alone: the outer call compiles as far as its own `)`, but the unclosed inner
        # `Sin(1` still fails). Every other caller leaves this at its default.
        tok = p.peek()
        if tok.kind == "OP" and tok.text in ("!", "@"):
            # Pointer variables: `!x` dereferences (also valid as an assignment target -- `!val1
            # = !val2` -- so this is handled at atom level, not just as a read-only unary op),
            # `@x` takes the address of `x`. Confirmed valid CRBasic by corpus testing
            # (pointervariable_1.CR6, pointervariable_3.CR6); see docs/frontend.md.
            p.next()
            operand = self._parse_atom(p, line, file)
            return S.Unary(tok.line, file, tok.text, operand)
        if tok.kind == "NUMBER":
            p.next()
            return S.Number(tok.line, file, tok.value, tok.text)
        if tok.kind == "STRING":
            p.next()
            return S.String(tok.line, file, tok.value)
        if tok.kind == "OP" and tok.text == "(":
            p.next()
            e = self._parse_expr(p, line, file)
            if strict and p.at_end():
                # Oracle-confirmed (`docs/checker.md` "Need closing ) shapes"): a bare grouping
                # paren left open to end-of-line inside a general expression is rejected exactly
                # like an unclosed call, `No Matching, or out of place ) in expression: %s.`, not
                # silently completed the way an instruction/`Call` argument list is.
                raise _ParseError(UNCLOSED_PAREN_EXPR)
            p.expect_close(")")
            return e
        if tok.kind == "OP" and tok.text == "{":
            p.next()
            items: list[S.Expr | None] = []
            if not p.is_op("}"):
                items.append(self._parse_expr(p, line, file))
                while p.is_op(","):
                    p.next()
                    items.append(self._parse_expr(p, line, file))
            p.expect_close("}")
            return S.ArrayLiteral(tok.line, file, items)
        if tok.kind == "IDENT":
            p.next()
            node: S.Expr = S.Name(tok.line, file, tok.text)
            if p.is_op("("):
                p.next()
                args, _spans = self._parse_arglist(p, ")", line, file, strict=strict)
                node = S.Index(tok.line, file, tok.text, args)
            while p.is_op("."):
                if (isinstance(node, S.Name) and (
                        p.peek(1).kind != "IDENT" or p.peek(1).col > p.peek().col + 1)
                        and (self.profile.known_first_word_pass1 or p.i > 1)):
                    # a trailing `.` stays on the name (`junk.`: an unknown name, oracle 2026-10-08;
                    # `T.` compiles for a table, `X.` is `Could not find X.`, all four loggers; a blank
                    # between the dot and a name ends the member: `T. To 3`, `T. Then`, `T. X` adjacent operands)
                    p.next()
                    node = S.Name(tok.line, file, tok.text + ".")
                    break
                p.next()
                member_tok = p.expect_ident()
                margs = None
                if p.is_op("("):
                    p.next()
                    margs, _mspans = self._parse_arglist(p, ")", line, file)
                node = S.Member(tok.line, file, node, member_tok.text, margs)
            return node
        raise _ParseError(f"expected an expression, found {tok.text or tok.kind!r}")

    def _parse_arglist(
        self, p: _TokStream, close: str, line: int, file: str, strict: bool = True
    ) -> tuple[list[S.Expr | None], list[tuple[int, int] | None]]:
        # `strict=False` (only `_parse_generic_instruction`/`_parse_call`'s own direct argument
        # list, plus the one bare-call-detection atom above): an instruction/`Call` argument list
        # left open to end-of-line silently completes right there (`expect_close`, pre-existing,
        # oracle-confirmed). `strict=True` (the default -- every other caller, i.e. this argument
        # list belongs to a call reached through the general expression grammar): the same shape
        # instead raises, becoming the real compiler's own `No Matching, or out of place ) in
        # expression: %s.` (`docs/checker.md` "Need closing ) shapes").
        args: list[S.Expr | None] = []
        spans: list[tuple[int, int] | None] = []
        if p.is_op(close):
            p.next()
            return args, spans
        if not p.at_end():
            while True:
                if p.at_end() or (p.peek().kind == "OP" and p.peek().text in (",", close)):
                    args.append(None)
                    spans.append(None)
                else:
                    start_col = p.peek().col
                    args.append(self._parse_expr(p, line, file))
                    spans.append((start_col, p.peek().col))
                if p.is_op(","):
                    p.next()
                    continue
                break
        if strict and p.at_end():
            # Ran off the end of the line with the call never closed -- lenient callers keep the
            # pre-existing "implicit close at end-of-line" behaviour via `expect_close` below;
            # strict callers (any call reached through the general expression grammar) raise
            # instead, oracle-confirmed (see `_parse_atom`'s own docstring above).
            raise _ParseError(UNCLOSED_PAREN_EXPR)
        p.expect_close(close)
        return args, spans

    def _parse_bare_arglist(
        self, p: _TokStream, line: int, file: str
    ) -> tuple[list[S.Expr | None], list[tuple[int, int] | None]]:
        start_col = p.peek().col
        args: list[S.Expr | None] = [self._parse_expr(p, line, file)]
        spans: list[tuple[int, int] | None] = [(start_col, p.peek().col)]
        while p.is_op(","):
            p.next()
            start_col = p.peek().col
            args.append(self._parse_expr(p, line, file))
            spans.append((start_col, p.peek().col))
        return args, spans


def _profile_for(name: str) -> profiles.Profile:
    return profiles.for_file(Path(name))


def partial_expression(text: str, profile: profiles.Profile) -> S.Expr | None:
    """The longest expression an unparsable statement starts with (after a plain `target =`), or
    None: what the real compiler has parsed, and type-checked, before it meets the junk."""
    try:
        toks = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
        start = 0
        if len(toks) > 2 and toks[0].kind == "IDENT":
            if toks[1].text == "=":
                start = 2
            elif toks[1].text == "(":
                depth = 0
                for j in range(1, len(toks)):
                    depth += (toks[j].text == "(") - (toks[j].text == ")") if toks[j].kind == "OP" else 0
                    if depth == 0:
                        start = j + 2 if j + 1 < len(toks) and toks[j + 1].text == "=" else 0
                        break
        parser_ = _Parser(profile, Diagnostics())
        parser_._logical_assign = True
        body = toks[start:]
        depth = sum((t.text == "(") - (t.text == ")") for t in body if t.kind == "OP")
        if body and ((body[-1].kind == "IDENT" and body[-1].upper in _Parser._LOGICAL_OPS)
                     or (body[-1].kind == "OP" and body[-1].text == "=")):
            # a logical word (or `=`) with no right operand still has its left operand's strings
            # counted, as if a number followed (oracle, all four loggers, 2026-10-12)
            body = body + [Token("NUMBER", "0", "0", 0, 0, 0)]
        try:
            return parser_._parse_expr(_TokStream(
                body + [Token("OP", ")", ")", 0, 0)] * max(depth, 0) + [_eof_like(0)], text), 0, "")
        except _ParseError:
            pass
        for cut in range(len(toks), start, -1):  # a dangling tail (`... AND state =`) is dropped
            try:
                return parser_._parse_expr(_TokStream(toks[start:cut] + [_eof_like(0)], text), 0, "")
            except _ParseError:
                continue
        return None
    except Exception:  # a shape the expression grammar cannot take (`{`): nothing was parsed
        return None


_source_module = source  # parse_file's `source` parameter shadows the module


def parse_file(path: Path | str, profile: profiles.Profile | None = None,
               pull_limit: tuple[int, ...] | None = None,
               source: SourceProvider | None = None,
               provenance: bool = True) -> tuple[S.Program, Diagnostics]:
    """`provenance=False` skips the pieces/line-map/origin data only the language server reads (the CLI
    path); everything the checker sees is unchanged. `profile` is normally left to the file's own extension (`profiles.for_file`, the historical
    default); the caller passes it explicitly when the logger was chosen another way (`--logger`,
    `compiler.compile_file`'s own `profile` argument) so `Const` folding during parsing (predefined
    symbols such as `U1`) uses the *same* profile the checker later runs with. Before this, a
    `.CR6` file always folded against the CR6 profile regardless of `--logger`, silently baking a
    CR6 symbol value into a non-CR6 profile's `program.consts` -- `docs/loggers.md`."""
    path = Path(path)
    lines, diags = _source_module.load_program(path, _INCLUDE_OWN_EXT.get((profile or _profile_for(path.name)).name),
                                       _pulls_init(profile or _profile_for(path.name)), pull_limit,
                                       _CR3XX_INCLUDE_EXTS.get((profile or _profile_for(path.name)).name),
                                       source, provenance)
    pull_log = diags.pull_log
    parser = _Parser(profile or _profile_for(path.name), diags, provenance)
    program, diags = parser.run(lines, path.name)
    program.pull_log = pull_log
    program.pull_site = diags.pull_site
    program.include_paths = diags.include_paths
    program.line_map = parser.line_map
    program.occurrences = diags.occurrences
    _include_warning_drives(diags, profile or _profile_for(path.name))
    main = (source or DiskSource()).read(path)
    if main is None:
        raise FileNotFoundError(str(path))
    _set_last_line(program, parser, main.decode("latin-1"))
    return program, diags


def _pulls_init(profile: profiles.Profile) -> bool:
    """All four loggers pull the line after a `Public`/`Dim` initializer's `=`/`,` into the declaration."""
    return profile.name in ("cr6", "cr1000x", "cr300", "cr350")


_MISSING_INCLUDE_DRIVE_RE = re.compile(r"^(Cannot open include file  )(?:CPU|USR|CRD|USB):", re.I)


_INCLUDE_OWN_EXT = {"cr6": "CR6", "cr1000x": "CR1X"}
_CR3XX_INCLUDE_EXTS = {"cr300": ("CR300", "CR310"), "cr350": ("CR350",)}


def _include_warning_drives(diags: Diagnostics, profile: profiles.Profile) -> None:
    """CR6/CR1000X print a missing include without its drive (`Cannot open include file
    NoSuch.crb.` for `"CPU:NoSuch.crb"`); CR300/CR350 keep it (oracle, 2026-09-28)."""
    if profile.include_drive_prefix:
        return
    diags.items[:] = [dataclasses.replace(d, message=_MISSING_INCLUDE_DRIVE_RE.sub(r"\1", d.message))
                      if d.severity == "warning" else d for d in diags.items]


def _set_last_line(program: S.Program, parser: "_Parser", main_text: str) -> None:
    """The line end-of-program messages are reported on: `EndProg`'s, or else the main file's
    last physical line, trailing blank lines included (oracle, CR6: `Public X` plus two blank
    lines -> `line 3: No BeginProg in Program.`)."""
    if parser._done and program.statements:
        program.last_line = program.statements[-1].line
    else:
        program.last_line = max(1, len(main_text.splitlines()))


def parse_text(text: str, name: str = "t.cr6", directory: Path | str | None = None,
               profile: profiles.Profile | None = None) -> tuple[S.Program, Diagnostics]:
    directory = Path(directory) if directory is not None else None
    lines, diags = source.load_text(
        text, name, directory, _INCLUDE_OWN_EXT.get((profile or _profile_for(name)).name),
        _pulls_init(profile or _profile_for(name)),
        _CR3XX_INCLUDE_EXTS.get((profile or _profile_for(name)).name))
    parser = _Parser(profile or _profile_for(name), diags)
    program, diags = parser.run(lines, name)
    program.line_map = parser.line_map
    program.occurrences = diags.occurrences
    _include_warning_drives(diags, profile or _profile_for(name))
    _set_last_line(program, parser, text)
    return program, diags
