"""Name resolution and type-code-driven value checks for instruction arguments. Rules and oracle
evidence: `docs/checker.md`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import symbols

_TYPE_CODES_PATH = Path(__file__).resolve().parent.parent / "data" / "type_codes.json"

# `type_codes.json` `kind`s that route an argument through the real expression/named-constant
# resolver (`FUN_004b8700`/`FUN_004b5c50` and their `const`-only wrappers, `docs/type-codes.md`):
# an unresolved name anywhere inside one of these positions is what produces `Invalid symbol in
# expression: %s.`. Every other kind either never runs general symbol resolution at all (`6`
# identifier/raw text such as `DataTable`'s or `ConstTable`'s own name, captured verbatim and
# validated -- or not -- by a later, different pass) or resolves a fixed keyword list through a
# separate function (`4`/`16`/`17`/`19` `FUN_004b5db0`, a different failure message). Restricting
# to this explicit include-list (rather than excluding the handful of kinds known *not* to
# resolve) is deliberate: the default for an unrecognised kind is "don't check", so a gap in this
# list only under-reports, never falsely rejects (AGENTS.md's priority order); the `ConstTable`
# regression this session found during development (`crbasic/Main.CR6`'s
# `ConstTable(GlobalConfigTable)`, kind `expr_marker`) is the concrete case that shape of bug
# looks like.
_RESOLVED_KINDS = frozenset({
    "const_expr", "expr", "const_expr_only", "bool_expr", "variable_or_array", "expr_or_name",
    "expr_typed", "expr_special", "expr_pair", "expr_list", "datatype_kw",
})

# Constant-only kinds (`docs/type-codes.md` codes 1/39, 5/38, 20) are **not** generically checked
# here, despite oracle evidence that the real compiler's constant-folding path does reject a
# genuine variable reference at some of them (`WriteIO(Mode,X)` -> `Variable illegal in Parameter:
# Mode.`, `Scan(X+1,Sec,0,0)` -> `Variable illegal in Parameter: X+1.`, both oracle-confirmed this
# session, `docs/checker.md` "Constant-only and variable-required type codes"). Found as a
# **regression** during development: `Sample`'s own `DataType` argument is *also* coded `1`
# (`const_expr`) in `data/instructions.json`, yet legitimately accepts a `StructureType` name
# (`structuretype_1.CR6`, `Sample(1,S,VWSensor)`) -- not a foldable constant by this project's own
# `consteval` either. This means at least one specific argument silently overrides the generic
# code-1 "reject a variable" rule (most likely `Sample`'s own `compile_fn` recognising a `DataType`-
# shaped keyword/type-name before generic constant-folding would even run), and this session found
# no reliable way to tell such an argument apart from an ordinary code-1 position using only the
# type-code table -- so a blanket rule here risks an unknown number of further false rejects this
# session's oracle probing did not have time to rule out. Left unimplemented rather than guessed
# at (AGENTS.md: never reject an accepted program); a future session would need to check each
# const-only-coded parameter individually (or find the compiler's own keyword-argument flag) before
# enabling this safely.

# Variable-required kinds (`docs/type-codes.md` codes 8, 27, 29, 33, 41, 47, 48): the shared
# "variable or array" handler accepts a bare name, an array reference, or any expression that
# involves a real variable (silently promoted to a hidden temporary) -- but rejects an argument
# that is *itself* a compile-time constant (a bare literal, a named `Const`, or a pure constant
# expression) with `Invalid Variable or Array: %s.`, **unless the constant is a string**
# (oracle-confirmed this session: `Battery(5)`, `Battery(1+2)`, and `Battery(SomeConst)` -- a
# `Const SomeConst = 5` -- all rejected this way; `Battery("x")` compiles fine, no type check at
# all on a string constant here).
_VARIABLE_REQUIRED_KINDS = frozenset({"variable_or_array"})


@lru_cache(maxsize=1)
def _type_code_kinds() -> dict[int, str]:
    data = json.loads(_TYPE_CODES_PATH.read_text())
    return {int(code): entry["kind"] for code, entry in data.items()}


def _position_kinds(params: list[int]) -> dict[int, str]:
    """0-based argument index -> `type_codes.json` `kind`, for every position whose type code
    routes through the general symbol/expression compiler (`_RESOLVED_KINDS`)."""
    kinds = _type_code_kinds()
    out = {}
    for i, code in enumerate(params):
        base = code - 100 if code >= 100 else code
        kind = kinds.get(base)
        if kind in _RESOLVED_KINDS:
            out[i] = kind
    return out


def _undefined_names(expr: S.Expr | None, known: set[str]) -> list[str]:
    """Every undeclared name referenced anywhere inside `expr` (in source order), recursing into
    unary/binary operators, array indices and array-literal items. `Member` (`Table.Field`,
    `Struct.member`) only recurses into its own base, never its `member` text -- that is a
    field/member name resolved against the base's own type, not a bare symbol lookup, and this
    session found no oracle evidence for how (or whether) it is checked, so it is left alone
    (AGENTS.md: under-report rather than guess). A name used as a `Call`-like `Index`'s own
    function/array name *is* checked, alongside its arguments, matching how the existing bare-name
    case already treated a direct `Index` argument before this session's own extension.
    """
    if expr is None:
        return []
    if isinstance(expr, S.Name):
        return [] if expr.name.upper() in known else [expr.name]
    if isinstance(expr, S.Unary):
        return _undefined_names(expr.operand, known)
    if isinstance(expr, S.Binary):
        return _undefined_names(expr.left, known) + _undefined_names(expr.right, known)
    if isinstance(expr, S.Index):
        head = [] if expr.name.upper() in known else [expr.name]
        for a in expr.args:
            head += _undefined_names(a, known)
        return head
    if isinstance(expr, S.Member):
        return _undefined_names(expr.base, known)
    if isinstance(expr, S.ArrayLiteral):
        out: list[str] = []
        for item in expr.items:
            out += _undefined_names(item, known)
        return out
    return []  # Number, String: nothing to resolve


def _arg_text(stmt: S.Instruction, i: int) -> str:
    """The argument's own verbatim source text (`Instruction.arg_spans`, `parser.py`), for the
    messages below that echo a whole expression rather than a single name (oracle-confirmed,
    `docs/checker.md`: `Scan(X+1,...)` echoes `X+1`, not a re-derived or spacing-normalised form).
    Falls back to the bare name (still correct for the common single-name case) if a span was
    somehow not captured.
    """
    spans = getattr(stmt, "arg_spans", None)
    if spans and i < len(spans) and spans[i] is not None:
        start, end = spans[i]
        return stmt.text[start:end]
    arg = stmt.args[i]
    return arg.name if isinstance(arg, S.Name) else ""


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """Only checks a direct `Instruction` argument that parsed as a bare `Name`, at a position
    whose declared type code actually resolves through the general symbol/expression compiler
    (`_position_kinds`) -- not one nested inside a larger expression, an array index,
    `Table.Field`, or a unary/binary operator, and not an identifier/raw-text-kind position such
    as a `DataTable`/`ConstTable` name. This project's evidence for the rule is all of this shape
    (a CR1000X-only terminal name such as `Vx1`/`P1`/`C5` used where CR6 does not define it --
    `docs/checker.md`).

    This session tried, and then deliberately backed out, two extensions once the full corpus (not
    just this session's own hand-written oracle probes) exposed real false rejects: a full
    expression-tree name-resolution walk (`_undefined_names`, unused for now -- `NAN` and the
    system `Status` table both turned out to be missing from `known` in ways only a nested lookup
    reached) and the constant-only/variable-required type-code rules (`_CONST_ONLY_KINDS`/
    `_VARIABLE_REQUIRED_KINDS`, both real per-instruction oracle evidence, but not uniform across
    every instruction sharing a code -- `Sample`'s `DataType`, `Move`'s third argument and
    `ModBusClient`'s last argument each override their own code's generic rule). See the module
    docstring above each constant for the full evidence trail; `env` is accepted (unused by the
    active code below) so a future session can re-enable either without changing the call site in
    `checker/__init__.py` again.
    """
    known = symbols.declared_names(program) | set(profile.symbols) | set(profile.functions) | set(profile.instructions)

    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        for i, kind in _position_kinds(record["params"]).items():
            if i >= len(stmt.args):
                continue
            arg = stmt.args[i]
            if arg is None:
                continue

            # Undefined-symbol resolution stays deliberately restricted to a *direct* bare `Name`
            # argument, not a full expression-tree walk (`_undefined_names` above is currently
            # unused for this reason, kept for a future, more thoroughly oracle-tested session):
            # found as a **regression** during this session's own development, extending it to
            # look inside a compound expression immediately produced false rejects across this
            # repository's own `crbasic/*.CR6` entry programs and several `chm` corpus files --
            # `NAN` (a predefined float constant used throughout this repo's sensor code, e.g.
            # `PT1000_Temp_C <> NAN`) and `Status` (the system `Status` table, as in
            # `Status.SerialNumber`) both turned out to be missing from `known`
            # (`symbols.declared_names`/`profile.symbols`) in ways the previous, narrower check
            # never exercised. Given AGENTS.md's priority (never reject an accepted program) and
            # no budget left this session to audit `known` exhaustively against every name a
            # nested expression might reference, this stays at the original, corpus-verified
            # scope; `_undefined_names` is ready for a session that can do that verification.
            if isinstance(arg, S.Name) and arg.name.upper() not in known:
                # message text is per-logger data (`profile.unresolved_symbol_message`,
                # `docs/loggers.md`): CR300/CR350 word this "{name} is not defined." instead of
                # CR6/CR1000X's "Invalid symbol in expression: {name}."
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=arg.name), stmt.file)
                continue

            # `_VARIABLE_REQUIRED_KINDS` (`Invalid Variable or Array: %s.`) is intentionally not
            # applied here despite oracle evidence that it is a real rule for *some* instructions
            # (`Battery(5)`, `Erase(5)`, `ReadIO(5,1)`, all code 8/27/29 on CR6, all oracle-
            # confirmed rejected). Found as a **regression** during this session's own testing
            # against the full corpus on all four loggers: `Move`'s own third argument (code 41)
            # accepts a literal `0` (`Move (IridiumData(),ID, 0,1)`, `modbusclient_2.CR6`... no,
            # `embracer.cr6`), and `ModBusClient`'s own last, optional argument (also code 27, the
            # *same* code that correctly rejects a literal for `Erase`) also accepts a literal `2`
            # (`modbusclient_2.CR6`) -- both on CR1000X. So the same type code does not behave
            # uniformly across every instruction that uses it; at least `Move` and `ModBusClient`
            # override it (most likely their own `compile_fn`, similar to the const-only kinds'
            # `Sample`/`DataType` exception above). No budget left this session to check each
            # variable-required-coded parameter individually across four loggers' instruction
            # tables before enabling this safely; see `docs/checker.md` "Constant-only and
            # variable-required type codes" for the full evidence and what a future session would
            # need to verify first.
