"""Structural checks: `BeginProg`/`EndProg` presence, `Scan`/`NextScan` and `If`/`Select` block
matching, undefined explicit `Call` targets, and the generic "not a valid statement" fallback for
a statement our parser could not turn into a recognised shape. Rules and oracle evidence:
`docs/checker.md`.
"""

from __future__ import annotations

from .. import syntax as S
from ..diagnostics import Diagnostics
from . import symbols

# `Unparsed.reason` values from two oracle-confirmed parser gaps (docs/frontend.md "Known
# residual gaps"): the real compiler still accepts these lines, so the generic "not a valid
# statement" fallback below must stay silent for them. Matched as a prefix so unrelated
# `reason` text is never accidentally suppressed.
_KNOWN_PARSER_GAP_REASONS = (
    "unexpected trailing input starting at '+='",   # samplemaxmin_1.CR6: `X=X+=1`
    "expected ')', found '.6'",                      # snmpvariable_1.CR6: a dotted-OID literal
)

# `Unparsed.reason` values that never denote real statement content (nothing to echo or reject).
_BENIGN_REASONS = ("empty statement",)


def _keyword(stmt: S.Stmt) -> str:
    if isinstance(stmt, S.Instruction):
        return stmt.keyword.upper()
    if isinstance(stmt, S.If):
        return "IF"
    if isinstance(stmt, S.For):
        return "FOR"
    if isinstance(stmt, S.DoLoop):
        return stmt.keyword.upper()
    return type(stmt).__name__.upper()


def _block_word(args: list) -> str | None:
    """`Next Scan`, `End If`, `End Select`: the word after a two-word block terminator."""
    if args and isinstance(args[0], S.Name):
        return args[0].name.upper()
    return None


def check(program: S.Program, profile, diags: Diagnostics) -> None:
    subs = symbols.declared_subs(program)

    seen_beginprog = False
    scan_depth = 0
    cond_depth = 0
    last_line = program.statements[-1].line if program.statements else 1

    for stmt in program.statements:
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []

        if isinstance(stmt, S.Call):
            _check_call(stmt, subs, profile, diags)
        elif isinstance(stmt, S.Unparsed):
            _check_unparsed(stmt, diags)

        if kw == "BEGINPROG":
            seen_beginprog = True
        elif kw == "SCAN":
            scan_depth += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(args) == "SCAN"):
            if scan_depth == 0:
                diags.error(stmt.line, "NextScan with no Scan.", stmt.file)
            else:
                scan_depth -= 1
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            cond_depth += 1
        elif kw == "ENDSELECT" or (kw == "END" and _block_word(args) == "SELECT"):
            cond_depth = max(0, cond_depth - 1)
        elif kw == "ENDIF" or (kw == "END" and _block_word(args) == "IF"):
            cond_depth = max(0, cond_depth - 1)

    if not seen_beginprog:
        diags.error(last_line, "No BeginProg in Program.")
        return
    if scan_depth > 0:
        diags.error(last_line, "Scan without NextScan.")
    if cond_depth > 0:
        diags.error(last_line, "IF or SELECT without EndIf or EndSelect.")


def _check_call(stmt: S.Call, subs: set[str], profile, diags: Diagnostics) -> None:
    name = stmt.name.upper()
    if name in subs or name in profile.functions:
        # A declared `Sub`/`Function` (oracle: docs/checker.md, `MySub(5)`) or a built-in
        # *function* (`Timer`, `SerialOpen`, ... -- `data/functions.json`, not the 410-entry
        # instruction table) called for its side effect with the return value discarded: real,
        # working code in this repository's own `Main.CR6` does this throughout.
        return
    if stmt.explicit:
        # `Call NoSuchSub`: oracle-confirmed (docs/type-codes.md section 3).
        diags.error(stmt.line, f"Sub {stmt.name} not defined.", stmt.file)
        return
    # A bare `Name` or `Name(args)` with no `Call` keyword, resolving to nothing declared: the
    # real compiler's generic bare-expression-statement fallback ("Expr" in its instruction
    # table) rejects it with this message, echoing the statement's own source text (oracle:
    # docs/checker.md, `NOTE` / `SWVX(Vx1,1,1)` / `TableFieldNames(...)`-shaped corpus cases).
    diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file)


def _check_unparsed(stmt: S.Unparsed, diags: Diagnostics) -> None:
    if stmt.reason.startswith(_KNOWN_PARSER_GAP_REASONS) or stmt.reason in _BENIGN_REASONS:
        return
    diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file)
