"""Syntax tree for CRBasic.

A program is a flat list of statements, one per logical source line (or per `:`-separated
part). Block structure (If/EndIf, Scan/NextScan, DataTable/EndTable, ...) is kept flat, as the
original compiler does, and is checked by `opencrbasic.structure`. Every node carries the
1-based source line it came from and, for diagnostics, the file it came from.
"""

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .provenance import LoadProvenance

if TYPE_CHECKING:
    from .lexer import Token
    from .source import LogicalLine

type ConstValue = int | float | str  # a folded constant (see `consteval.value_of`)


@dataclass
class Node:
    line: int
    file: str = ""


# ---------------------------------------------------------------- expressions


@dataclass
class Expr(Node):
    pass


@dataclass
class Number(Expr):
    value: float | int = 0
    text: str = ""              # source spelling, e.g. "&H1F", "1.5E3"


@dataclass
class String(Expr):
    value: str = ""


@dataclass
class Name(Expr):
    """A bare identifier: variable, constant, predefined symbol, table name, ..."""
    name: str = ""


@dataclass
class Index(Expr):
    """`name(i, j, k)`: array element, or a call when `name` is a builtin function / Function."""
    name: str = ""
    args: list[Expr | None] = field(default_factory=list)   # None marks an empty argument slot


@dataclass
class Member(Expr):
    """`Table.Field`, `Status.Battery(1)`, `Struct.member`."""
    base: Expr | None = None
    member: str = ""
    args: list[Expr | None] | None = None


@dataclass
class Unary(Expr):
    op: str = ""                # "-", "+", "NOT"
    operand: Expr | None = None


@dataclass
class ArrayLiteral(Expr):
    """`{a, b, c}`: a `Const` array value, or an array initialiser in a `Public`/`Dim`."""
    items: list[Expr | None] = field(default_factory=list)


@dataclass
class Juxtaposed(ArrayLiteral):
    """Operands side by side in a declaration dimension (`Dim G(1  8)`), which the compiler reports
    as `Incomplete expression`; `items` are the operands, so name walkers see all of them."""


@dataclass
class Binary(Expr):
    op: str = ""                # upper-case operator text: + - * / \ ^ & MOD AND OR XOR IMP EQV = <> < > <= >= << >>
    left: Expr | None = None
    right: Expr | None = None


# ---------------------------------------------------------------- statements


class _StatementMetadata:
    """Parser-attached details; annotations preserve the absence of unset attributes."""
    origin: tuple[LogicalLine, int]
    dropped_tail: list[Token]
    grouped: bool
    hoisted_last: bool
    arg_spans: list[tuple[int, int] | None]
    alias_junk: bool
    alias_item_junk: list[bool]
    extra_targets: list[Expr]


@dataclass
class Stmt(Node, _StatementMetadata):
    text: str = ""              # the logical line as written (for diagnostics that echo it)
    at_eol: bool = True         # `text` (this statement's own `:`-separated segment) reaches the
    # true end of its physical source line -- no `'` comment and no further `:`-segment cut it
    # short. Set by `parser.py`'s `_parse_line`; used only by `checker/structure.py`'s
    # `_out_of_place` to decide whether CR300/CR350 print the fallback's closing `.` on its own
    # output line (`docs/loggers.md` "Generic fallback echo on CR300/CR350").


class _DeclarationMetadata:
    size_missing: int | None


@dataclass
class VarDecl(_DeclarationMetadata):
    name: str
    dims: list[Expr] = field(default_factory=list)          # up to 3
    type: str | None = None     # upper-case: FLOAT LONG BOOLEAN STRING DOUBLE UINT1 UINT2 UINT4 BOOL8 NSEC or a StructureType name
    string_size: Expr | None = None                         # `As String * n`
    init: list[Expr | None] | None = None                   # `= value` or `= {a, b, c}`
    units: str | None = None
    dim0_span: tuple[int, int] | None = None                # (start, end) columns of the first
    # dimension expression's own source text within the enclosing `Declaration.text` -- lets
    # `checker/structure.py` echo it verbatim for `Variable %s dimension exceeds max of %d.`
    # (oracle-confirmed to always name the *first* dimension, `docs/checker.md`), without the
    # parser needing to reconstruct an expression's source spelling from its AST.
    paren_dim: bool = False  # a `(` inside the dimension list makes the variable Float whatever its `As` type
    junk_dims: list[Expr] = field(default_factory=list)
    empty_type_init: bool = False  # `As Float() = ...`: `Invalid, or out of place expression:  =.` (round 54)  # the dimensions as written when one is a `Juxtaposed`; `dims` then holds 1 in its place


@dataclass
class Declaration(Stmt):
    """Public / Dim / Static (keyword upper-case in `kind`)."""
    kind: str = ""
    vars: list[VarDecl] = field(default_factory=list)


@dataclass
class ConstDecl(Stmt):
    name: str = ""
    value: Expr | None = None
    type: str | None = None
    value_span: tuple[int, int] | None = None  # (start, end) columns of `value`'s own source
    # text within `Stmt.text` -- lets `checker/expressions.py` echo the initializer verbatim for
    # `check_consts`'s undefined-symbol message, the same way `Instruction.arg_spans` already
    # lets `_arg_text` echo an argument (`docs/checker.md` "Round 4"'s whole-expression echo).


@dataclass
class Assignment(Stmt):
    target: Expr | None = None  # Name, Index or Member
    value: Expr | None = None
    compound: bool = False      # `X += 1`, stored as `X = X + 1`


@dataclass
class Instruction(Stmt):
    """Any statement of the form `Keyword [(] args [)]`, including block keywords.

    `keyword` is the instruction-table spelling in upper case (`SCAN`, `NEXTSCAN`, `DATATABLE`,
    `SAMPLE`, `ENDIF`, `CALLTABLE`, ...). `args` are the comma-separated arguments, None for an
    empty slot. Statements that need extra structure use the dedicated classes below.
    """
    keyword: str = ""
    args: list[Expr | None] = field(default_factory=list)
    arg_spans: list[tuple[int, int] | None] = field(default_factory=list)   # (start, end) columns
    # of each argument's own raw source text within this statement's `text` (`None` for an empty
    # slot); lets a checker echo an argument's *verbatim* spelling (e.g. `1+2`, not a re-derived
    # `3` or a spacing-normalised `1 + 2`) without reconstructing it from the parsed `Expr` tree.


@dataclass
class If(Stmt):
    """`If cond Then` (block form, `inline` None) or `If cond Then stmt [Else stmt]`."""
    cond: Expr | None = None
    inline: list[Stmt] | None = None
    inline_else: list[Stmt] | None = None


@dataclass
class ElseIf(Stmt):
    cond: Expr | None = None


@dataclass
class For(Stmt):
    var: Expr | None = None
    start: Expr | None = None
    end: Expr | None = None
    step: Expr | None = None


@dataclass
class DoLoop(Stmt):
    """`Do [While|Until cond]` or `Loop [While|Until cond]`; `keyword` is DO or LOOP."""
    keyword: str = ""
    cond_kind: str | None = None    # WHILE / UNTIL
    cond: Expr | None = None


type CaseItem = (tuple[Literal["value"], Expr] | tuple[Literal["range"], Expr, Expr]
                 | tuple[Literal["is"], str, Expr])


@dataclass
class Case(Stmt):
    """`Case a, b To c, Is > d, Else`; each item is ("value", e) / ("range", lo, hi) / ("is", op, e)."""
    items: list[CaseItem] = field(default_factory=list)
    is_else: bool = False


@dataclass
class Param:
    name: str
    by_ref: bool | None = None      # None when not written
    type: str | None = None
    string_size: Expr | None = None
    dims: int = 0                   # `x()` style array parameter
    default: Expr | None = None     # `Optional x = 1`
    # Parsed expressions inside `x(...)`, in source order; alias declaration validation and
    # editor bindings use these. They take no part in equality or repr.
    dim_exprs: list[Expr] = field(default_factory=list, compare=False, repr=False)


@dataclass
class SubDef(Stmt):
    """`Sub name(params)` or `Function name(params) [As type]`; `keyword` SUB or FUNCTION."""
    keyword: str = ""
    name: str = ""
    params: list[Param] = field(default_factory=list)
    return_type: str | None = None
    # pass-1 messages of a malformed header (`parser._parse_subdef`), in source order
    header_errors: list[str] = field(default_factory=list, compare=False, repr=False)


@dataclass
class Call(Stmt):
    """`Call name(args)` or a bare `name(args)` / `name args` invocation of a Sub."""
    name: str = ""
    args: list[Expr | None] = field(default_factory=list)
    explicit: bool = False          # written with `Call`
    tail_ignored: bool = False      # `S()` followed by ignored trailing text
    tail_expr: Expr | None = None  # a `Function` call statement continued as one expression
    juxtaposed: list[Expr] | None = None  # operands of a juxtaposed tail: `Fn(1) 1`
    juxt_opaque: bool = False        # tail starts with `,` or `(`: `Fn(1),Zork`


@dataclass
class Label(Stmt):
    name: str = ""


@dataclass
class Unparsed(Stmt):
    """A line the parser could not handle; carries the reason for diagnostics."""
    reason: str = ""
    expr: Expr | None = None  # the expression of a statement the parser took apart but cannot compile


def ends_routine(stmt: Stmt) -> bool:
    """`EndSub`/`EndFunction`, or the two-word `End Sub`/`End Function` (parsed as `END` with the
    word as its argument); the real compiler closes the routine with either spelling (oracle,
    2026-10-12, all four loggers, `docs/checker.md` "Two-word routine closers")."""
    if not isinstance(stmt, Instruction):
        return False
    kw = stmt.keyword.upper()
    if kw in ("ENDSUB", "ENDFUNCTION"):
        return True
    return kw == "END" and bool(stmt.args) and isinstance(stmt.args[0], Name) and \
        stmt.args[0].name.upper() in ("SUB", "FUNCTION")


def hash_statement(st: Stmt) -> Unparsed | None:
    """The `#...` statement that ends the compile (`Unknown instruction`): `st` itself or one in
    an inline `If` body."""
    if isinstance(st, Unparsed):
        return st if st.text.lstrip().startswith("#") else None
    for sub in (getattr(st, "inline", None) or []) + (getattr(st, "inline_else", None) or []):
        if (h := hash_statement(sub)) is not None:
            return h
    return None


def cut_after_hash(st: Stmt) -> Stmt:
    """`st` without the inline statements that follow its `#...` statement (and without the `Else`
    body when the `#...` is in the `Then` body): the compile stops there."""
    then, other = getattr(st, "inline", None) or [], getattr(st, "inline_else", None) or []
    for body, name in ((then, "inline"), (other, "inline_else")):
        k = next((i for i, sub in enumerate(body) if hash_statement(sub)), None)
        if k is not None and isinstance(st, If):
            cut = body[:k] + [cut_after_hash(body[k])]
            if name == "inline":
                return dataclasses.replace(st, inline=cut, inline_else=[])
            return dataclasses.replace(st, inline_else=cut)
    return st


@dataclass
class Program(LoadProvenance):
    name: str
    statements: list[Stmt] = field(default_factory=list)
    last_line: int = 0      # `EndProg`'s line, else the main file's last physical line
    hash_cut: int | None = None  # parser diagnostics kept: those before the first `#...` statement ends
    unclosed_if: bool = False  # a `#If` still open at the end of the file (before any `EndProg`)
    include_error_at: list[int] = field(default_factory=list)  # statement count when each reported
    # include error (extension, nested include) was read: where the CR300/CR350 cascade starts
    consts: dict[str, ConstValue] = field(default_factory=dict)   # name (upper) -> resolved value,
    # for every `Const` the parser could constant-fold in an active branch; `checker.py` reuses
    # this (via `consteval.Env`) instead of re-walking the program to rebuild it.
