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


class _ParseError(Exception):
    pass


class _TokStream:
    __slots__ = ("toks", "i")

    def __init__(self, toks: list[Token]):
        self.toks = toks
        self.i = 0

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


class _Parser:
    def __init__(self, profile: profiles.Profile, diags: Diagnostics):
        self.profile = profile
        self.diags = diags
        self.env = consteval.Env(profile)
        self.cond_stack: list[_CondFrame] = []
        self.program = S.Program(name="")
        self._done = False  # set once a top-level EndProg is seen; see `run()`

    # ------------------------------------------------------------ driver

    def run(self, lines: list[source.LogicalLine], name: str) -> tuple[S.Program, Diagnostics]:
        self.program.name = name
        for ll in lines:
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
        for seg_text_raw in _split_text_segments(ll.text):
            if not seg_text_raw.strip():
                continue
            word = _LEADING_WORD_RE.match(seg_text_raw)
            if word and word.group(1).upper() == "UNITS":
                stmt = self._parse_units_segment(seg_text_raw, ll.line, ll.file)
                self.program.statements.append(stmt)
            else:
                stmt = self._parse_text_segment(seg_text_raw, ll.line, ll.file)
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
            return S.Unparsed(line, file, text=seg_text_raw.strip(), reason=f"lex error: {e.message}")
        real = [t for t in tokens if t.kind != "EOF"]
        if not real:
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
        p = _TokStream(seg)
        try:
            stmt = self._parse_stmt(p, line, file, seg_text)
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
        if p.is_op("("):
            p.next()
            args = self._parse_arglist(p, ")", line, file)
        elif p.is_op("="):
            # A handful of instructions (`StationName=Value`) take `=Value` instead of an
            # argument list, confirmed valid CRBasic by corpus testing; see docs/frontend.md.
            p.next()
            args = self._parse_bare_arglist(p, line, file)
        elif not p.at_end():
            args = self._parse_bare_arglist(p, line, file)
        return S.Instruction(line, file, keyword=kw, args=args)

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
            args = self._parse_arglist(p, ")", line, file)
        elif not p.at_end():
            args = self._parse_bare_arglist(p, line, file)
        return S.Call(line, file, name=name_tok.text, args=args, explicit=True)

    def _parse_assignment_or_call(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        # An lvalue only (never a full expression): `=` is also the relational operator, so
        # going through `_parse_expr` here would swallow it as an equality test instead of
        # leaving it for the assignment check below (found by corpus testing; see
        # docs/frontend.md).
        target = self._parse_atom(p, line, file)
        t = p.peek()
        if t.kind == "OP" and (t.text == "=" or t.text in _COMPOUND_OPS):
            op_tok = p.next()
            value = self._parse_expr(p, line, file)
            if op_tok.text != "=":
                base_op = op_tok.text[0]
                value = S.Binary(op_tok.line, file, base_op, target, value)
            return S.Assignment(line, file, target=target, value=value)
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
        if isinstance(target, S.Index):
            return S.Call(line, file, name=target.name, args=target.args, explicit=False)
        raise _ParseError("expected an assignment or a call")

    # ---- If / ElseIf ----------------------------------------------------

    def _parse_if(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()  # IF
        cond = self._parse_expr(p, line, file)
        if p.is_ident("THEN"):
            p.next()
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
        then_stmts = self._parse_stmt_list(then_toks, line, file)
        inline_else = self._parse_stmt_list(else_toks, line, file) if else_toks is not None else None
        return S.If(line, file, cond=cond, inline=then_stmts, inline_else=inline_else)

    def _parse_stmt_list(self, toks: list[Token], line: int, file: str) -> list[S.Stmt]:
        segs = _split_colon_segments(toks + [_eof_like(line)])
        out = []
        for seg in segs:
            real = [t for t in seg if t.kind != "EOF"]
            if not real:
                continue
            seg_text = " ".join(t.text for t in real)
            out.append(self._parse_segment(seg, line, file, seg_text))
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
        var = self._parse_atom(p, line, file)  # lvalue only; see `_parse_assignment_or_call`
        p.expect_op("=")
        start = self._parse_expr(p, line, file)
        p.expect_ident("TO")
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
            value = self._parse_expr(p, line, file)
        except _ParseError:
            # `Const` with no complete `Name [As Type] = value` on its own physical line -- a CHM
            # corpus example wrapped the assignment onto the next line with no `_` continuation
            # (`rectpolar_1.CR6`: `Const` entirely alone, then `Pi=4*ATN(1)`; `sprintf_4.CR6`:
            # `Const ToAddr As` / `String = "..."`, truncated further into the grammar). The real
            # compiler reports the same message either way (oracle-confirmed, `docs/checker.md`),
            # so this doesn't try to distinguish how far through its own grammar it got.
            return S.Unparsed(line, file, text="", reason="Const without '='")
        node = S.ConstDecl(line, file, name=name_tok.text, value=value, type=vtype)
        try:
            self.env.define(name_tok.text, consteval.value_of(value, self.env))
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

    def _parse_vardecl(self, p: _TokStream, line: int, file: str) -> S.VarDecl:
        name_tok = p.expect_ident()
        dims: list[S.Expr] = []
        if p.is_op("("):
            p.next()
            if not p.is_op(")"):
                dims.append(self._parse_expr(p, line, file))
                while p.is_op(","):
                    p.next()
                    dims.append(self._parse_expr(p, line, file))
            p.expect_close(")")
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
            elif not dims and p.is_op("("):
                # `Public floatArr As Float(5) = {...}`: the dimension can follow the type
                # instead of the variable name. Confirmed valid CRBasic by corpus testing
                # (pointervariable_1.CR6); see docs/frontend.md.
                p.next()
                if not p.is_op(")"):
                    dims.append(self._parse_expr(p, line, file))
                    while p.is_op(","):
                        p.next()
                        dims.append(self._parse_expr(p, line, file))
                p.expect_close(")")
        init = None
        if p.is_op("="):
            p.next()
            val = self._parse_expr(p, line, file)
            init = list(val.items) if isinstance(val, S.ArrayLiteral) else [val]
        return S.VarDecl(name=name_tok.text, dims=dims, type=vtype, string_size=strsize, init=init)

    # ---- Alias / Units --------------------------------------------------

    def _parse_alias(self, p: _TokStream, line: int, file: str) -> S.Stmt:
        p.next()
        try:
            lhs = self._parse_atom(p, line, file)  # lvalue only; see `_parse_assignment_or_call`
            p.expect_op("=")
        except _ParseError:
            # `Alias` with no `Target = Name` on its own physical line -- the same CHM line-wrap
            # shape as `Const`/`Units`/`Public` above (`rectpolar_1.CR6`: `Alias` entirely alone,
            # then `XY(1)=X`). Oracle-confirmed message: `docs/checker.md`. A partially-parsed
            # target (present but no `=`, e.g. `realtime_1.CR6`'s `Alias rTime(1)` / `= Year`) gets
            # the same reason with an empty echo rather than reconstructing the target's source
            # text -- no oracle evidence pinned down that echo's exact shape (it embeds a literal
            # newline the empty-target case does not), so this only claims the simpler, verified
            # case.
            return S.Unparsed(line, file, text="", reason="Alias without '='")
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

    def _parse_atom(self, p: _TokStream, line: int, file: str) -> S.Expr:
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
                args = self._parse_arglist(p, ")", line, file)
                node = S.Index(tok.line, file, tok.text, args)
            while p.is_op("."):
                p.next()
                member_tok = p.expect_ident()
                margs = None
                if p.is_op("("):
                    p.next()
                    margs = self._parse_arglist(p, ")", line, file)
                node = S.Member(tok.line, file, node, member_tok.text, margs)
            return node
        raise _ParseError(f"expected an expression, found {tok.text or tok.kind!r}")

    def _parse_arglist(self, p: _TokStream, close: str, line: int, file: str) -> list[S.Expr | None]:
        args: list[S.Expr | None] = []
        if p.is_op(close):
            p.next()
            return args
        if p.at_end():
            return args  # implicit close at end-of-line; see `expect_close`
        while True:
            if p.at_end() or (p.peek().kind == "OP" and p.peek().text in (",", close)):
                args.append(None)
            else:
                args.append(self._parse_expr(p, line, file))
            if p.is_op(","):
                p.next()
                continue
            break
        p.expect_close(close)
        return args

    def _parse_bare_arglist(self, p: _TokStream, line: int, file: str) -> list[S.Expr | None]:
        args: list[S.Expr | None] = [self._parse_expr(p, line, file)]
        while p.is_op(","):
            p.next()
            args.append(self._parse_expr(p, line, file))
        return args


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
    lines, diags = source.load_program(path)
    parser = _Parser(profile or _profile_for(path.name), diags)
    return parser.run(lines, path.name)


def parse_text(text: str, name: str = "t.cr6", directory: Path | str | None = None,
               profile: profiles.Profile | None = None) -> tuple[S.Program, Diagnostics]:
    directory = Path(directory) if directory is not None else None
    lines, diags = source.load_text(text, name, directory)
    parser = _Parser(profile or _profile_for(name), diags)
    return parser.run(lines, name)
