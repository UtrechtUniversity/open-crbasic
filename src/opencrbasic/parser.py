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

import dataclasses

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import consteval, lexer, profiles, source
from . import syntax as S
from .diagnostics import Diagnostics
from .lexer import Token

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
    """Split a logical line's raw text on top-level `:` (outside `(...)` and `"..."` strings),
    without tokenising -- so a segment whose content isn't tokenisable CRBasic at all (a `Units`
    label) can still be found and routed separately. Mirrors `_split_colon_segments`'s paren-depth
    rule at the character level.
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
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == ":" and depth == 0:
            segments.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    segments.append("".join(current))
    return segments


_RAW_NAME_FIRST = frozenset({"DATATABLE", "CONSTTABLE"})


def _split_colon_segments(tokens: list[Token]) -> list[list[Token]]:
    """Split a fully-tokenised logical line on top-level `:` (not inside `(...)`)."""
    segments: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    eof = tokens[-1]
    for t in tokens[:-1]:
        if t.kind == "OP" and t.text == "(":
            depth += 1
        elif t.kind == "OP" and t.text == ")":
            depth = max(0, depth - 1)
        if t.kind == "OP" and t.text == ":" and depth == 0:
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


_FUNCTION_DEF_RE = re.compile(r"\s*Function\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


class _Parser:
    def __init__(self, profile: profiles.Profile, diags: Diagnostics):
        self.profile = profile
        self.diags = diags
        self.env = consteval.Env(profile)
        self.cond_stack: list[_CondFrame] = []
        self.program = S.Program(name="")
        self._done = False  # set once a top-level EndProg is seen; see `run()`
        self._in_struct = False  # inside StructureType ... EndStructureType
        self.function_names: set[str] = set()

    # ------------------------------------------------------------ driver

    def run(self, lines: list[source.LogicalLine], name: str) -> tuple[S.Program, Diagnostics]:
        self.program.name = name
        self.function_names = {m.group(1).upper() for ll in lines
                               if (m := _FUNCTION_DEF_RE.match(ll.text))}
        skipped_include_depth = 0
        for ll in lines:
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
            self._parse_line(ll)
            if self._done:
                # CR6Comp stops reading the file at the first top-level EndProg -- confirmed with
                # the oracle: garbage (even unlexable text, even a second full program) after it
                # compiles fine, because it is never looked at. See docs/frontend.md.
                break
        self.program.consts = dict(self.env.consts)
        return self.program, self.diags

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
        in_inline_if = False
        for idx, seg_text_raw in enumerate(segments):
            if not seg_text_raw.strip():
                continue
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
            # Segments after a one-line `If` belong to its body: no newline in the echo (oracle,
            # CR300, 2026-10-01: `If X Then X = 1 : Foo(1)`).
            in_inline_if = in_inline_if or (isinstance(stmt, S.If) and stmt.inline is not None)
            self.program.statements.append(stmt)
            # A `Const` just parsed is folded into `self.env` inside `_parse_const` itself, so
            # later statements/#If on the same or a later line see it.
            if isinstance(stmt, S.Instruction) and stmt.keyword == "ENDPROG":
                self._done = True
                return

    def _parse_text_segment(self, seg_text_raw: str, line: int, file: str) -> S.Stmt:
        try:
            tokens = lexer.tokenize(seg_text_raw, line)
        except lexer.LexError as e:
            if re.match(r"\s*ESSInitialize\b", seg_text_raw, re.I):
                # Its argument is read raw: `ESSInitialize(public")` and `ESSInitialize("public)`
                # compile on all four loggers (oracle, 2026-09-28; a fuzz-found false reject).
                return S.Unparsed(line, file, text=seg_text_raw.strip(), reason="empty statement")
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
        return self._parse_segment(tokens, line, file, seg_text)

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

    def _parse_segment(self, seg: list[Token], line: int, file: str, seg_text: str) -> S.Stmt:
        if (len(seg) > 3 and seg[0].kind == "IDENT" and seg[0].upper in _RAW_NAME_FIRST
                and seg[1].kind == "OP" and seg[1].text == "(" and seg[2].kind == "IDENT"
                and seg[3].kind == "IDENT"):
            # The table name is read up to whitespace too, so a second word becomes the next
            # parameter: `DataTable (Ten Sec,True,-1)` names `Ten` and reports `Too many
            # parameters:  -1.`; `ConstTable (my Tab,0)` compiles (oracle, 2026-10-01)
            seg = seg[:3] + [lexer.Token("OP", ",", ",", seg[3].col, seg[3].line)] + seg[3:]
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
            return S.Unparsed(line, file, text=seg_text, reason=str(e))
        except Exception as e:  # never raise out of the parser
            return S.Unparsed(line, file, text=seg_text, reason=f"internal error: {e!r}")

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
        expr = self._parse_expr(p, line, file)
        return S.Instruction(line, file, keyword="SELECT", args=[expr])

    def _parse_call(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # CALL
        name_tok = p.expect_ident()
        args: list[S.Expr | None] = []
        if p.is_op("("):
            p.next()
            args, _spans = self._parse_arglist(p, ")", line, file, strict=False)  # see above
            ignored = self._absorb_sub_tail(p, args, line, file)
            return S.Call(line, file, name=name_tok.text, args=args, explicit=True,
                          tail_ignored=ignored)
        elif not p.at_end():
            args, _spans = self._parse_bare_arglist(p, line, file)
        return S.Call(line, file, name=name_tok.text, args=args, explicit=True)

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
        target = self._parse_atom(p, line, file, strict=False)
        t = p.peek()
        if (t.kind == "OP" and t.text == "=" and isinstance(target, S.Index) and not target.args
                and p.toks[p.i + 1].kind == "EOF"):
            # `S2() =`: a Sub call whose tail is ignored (oracle, 2026-10-04, all four loggers)
            p.i = len(p.toks) - 1
            return S.Call(line, file, name=target.name, args=[], explicit=False, tail_ignored=True)
        if t.kind == "OP" and (t.text == "=" or t.text in _COMPOUND_OPS):
            op_tok = p.next()
            value = self._parse_expr(p, line, file)
            if op_tok.text != "=":
                base_op = op_tok.text[0]
                value = S.Binary(op_tok.line, file, base_op, target, value)
            return S.Assignment(line, file, target=target, value=value,
                                compound=op_tok.text != "=")
        if isinstance(target, S.Name) and p.is_ident("AS"):
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
        if isinstance(target, S.Index) and target.name.upper() in self.function_names:
            # a `Function` call statement is one expression (oracle, 2026-10-08: `F0()+Nope`
            # and `Fn(1)+Zork` report the undefined name, `Fn(1)+1` compiles), not a Sub tail
            if p.at_end():
                return S.Call(line, file, name=target.name, args=list(target.args), explicit=False)
            t = p.peek()
            if t.kind in ("IDENT", "NUMBER", "STRING") or (t.kind == "OP" and t.text in ("(", ",")):
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
                raise _ParseError("expected an assignment or a call")
            return S.Call(line, file, name=target.name, args=list(target.args), explicit=False,
                          tail_expr=expr)
        if isinstance(target, S.Index):
            args = list(target.args)
            ignored = self._absorb_sub_tail(p, args, line, file)
            return S.Call(line, file, name=target.name, args=args, explicit=False,
                          tail_ignored=ignored)
        raise _ParseError("expected an assignment or a call")

    # ---- If / ElseIf ----------------------------------------------------

    def _parse_if(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # IF
        cond = self._parse_expr(p, line, file)
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
            stmt = self._parse_segment(seg, line, file, verbatim or " ".join(t.text for t in real))
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
        cond = self._parse_expr(p, line, file)
        if p.is_ident("THEN"):
            p.next()
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
            if not p.is_op(")"):
                params.append(self._parse_param(p, line, file))
                while p.is_op(","):
                    p.next()
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
        if p.is_op("("):
            p.next()
            if not p.is_op(")"):
                self._parse_expr(p, line, file)
                while p.is_op(","):
                    p.next()
                    self._parse_expr(p, line, file)
            p.expect_close(")")
            dims = 1
        vtype = None
        strsize = None
        if p.is_ident("AS"):
            p.next()
            type_tok = p.expect_ident()
            vtype = type_tok.upper
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
                        dims=dims, default=default)

    # ---- Const ------------------------------------------------------

    def _parse_const(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()
        eq_at = None
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
            p.expect_op("=")
            eq_at = p.i
            value_start_col = p.peek().col
            value = self._parse_expr(p, line, file)
            value_span = (value_start_col, p.peek().col)
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
        if p.is_ident("AS"):
            p.next()
            type_tok = p.expect_ident()
            vtype = type_tok.upper
            if p.is_op("!"):
                p.next()  # pointer type (see docs/frontend.md)
                vtype += "!"
            if vtype == "STRING" and p.is_op("*"):
                p.next()
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
        return S.VarDecl(name=name_tok.text, dims=dims, type=vtype, string_size=strsize, init=init,
                          dim0_span=dim0_span, paren_dim=paren_dim)

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
            return S.Unparsed(line, file, text="", reason="Alias target with no '='")
        if p.peek().kind == "IDENT":
            # Each comma-separated item is one name (optionally indexed); anything after it up to
            # the next comma is ignored (oracle, all four loggers, 2026-09-27: `= Spare2_1  3`,
            # `= Sp + 1`, `= Sp As Long` compile; in `= A1, B1` both names are declared, in
            # `= A1 B1` only `A1`) -- a fuzz-found false reject.
            rhs = []
            junk = False
            while not p.at_end() and p.peek().kind == "IDENT":
                rhs.append(self._parse_atom(p, line, file))
                depth = 0
                while not p.at_end() and not (depth == 0 and p.is_op(",")):
                    junk = junk or len(rhs) == 1
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
            if not p.at_end():
                raise _ParseError("unexpected trailing input in Units target")
        except _ParseError as e:
            return S.Unparsed(line, file, text=seg_text.strip(), reason=str(e))
        stmt = S.Instruction(line, file, keyword="UNITS", args=[lhs, S.String(line, file, label)])
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
            right = self._parse_not(p, line, file)
            left = S.Binary(line, file, op, left, right)
        return left

    def _parse_not(self, p: _TokStream, line: int, file: str) -> S.Expr:
        if p.is_ident("NOT"):
            tok = p.next()
            operand = self._parse_not(p, line, file)
            return S.Unary(tok.line, file, "NOT", operand)
        return self._parse_relational(p, line, file)

    def _parse_relational(self, p: _TokStream, line: int, file: str) -> S.Expr:
        left = self._parse_shift(p, line, file)
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
                if (isinstance(node, S.Name) and p.peek(1).kind != "IDENT"
                        and self.profile.known_first_word_pass1):
                    # a trailing `.` stays on the name (`junk.`: an unknown name, oracle 2026-10-08)
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


def parse_file(path: Path | str, profile: profiles.Profile | None = None) -> tuple[S.Program, Diagnostics]:
    """`profile` is normally left to the file's own extension (`profiles.for_file`, the historical
    default); the caller passes it explicitly when the logger was chosen another way (`--logger`,
    `compiler.compile_file`'s own `profile` argument) so `Const` folding during parsing (predefined
    symbols such as `U1`) uses the *same* profile the checker later runs with. Before this, a
    `.CR6` file always folded against the CR6 profile regardless of `--logger`, silently baking a
    CR6 symbol value into a non-CR6 profile's `program.consts` -- `docs/loggers.md`."""
    path = Path(path)
    lines, diags = source.load_program(path, _INCLUDE_OWN_EXT.get((profile or _profile_for(path.name)).name),
                                       _pulls_init(profile or _profile_for(path.name)))
    parser = _Parser(profile or _profile_for(path.name), diags)
    program, diags = parser.run(lines, path.name)
    _include_warning_drives(diags, profile or _profile_for(path.name))
    _set_last_line(program, parser, path.read_bytes().decode("latin-1"))
    return program, diags


def _pulls_init(profile: profiles.Profile) -> bool:
    """CR6/CR1000X pull the line after a `Public`/`Dim` initializer's `=`/`,` into the declaration."""
    return profile.name in ("cr6", "cr1000x")


_MISSING_INCLUDE_DRIVE_RE = re.compile(r"^(Cannot open include file  )(?:CPU|USR|CRD|USB):", re.I)


_INCLUDE_OWN_EXT = {"cr6": "CR6", "cr1000x": "CR1X"}


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
        _pulls_init(profile or _profile_for(name)))
    parser = _Parser(profile or _profile_for(name), diags)
    program, diags = parser.run(lines, name)
    _include_warning_drives(diags, profile or _profile_for(name))
    _set_last_line(program, parser, text)
    return program, diags
