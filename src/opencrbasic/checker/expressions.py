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

# The compile-time-constant-only family (`docs/type-codes.md` codes 1/39, 5/38, 20 --
# `FUN_004b8700`/its `const`-only wrappers) is checked in its own pass, ahead of every other
# resolved kind, on CR6/CR1000X -- oracle-confirmed (round 4, this session): `WriteIO(1,BazQux,
# FooBar)` (`params=[1,25,101]`: position 0 code 1 = valid literal, position 1 code 25 = `BazQux`,
# position 2 code 101 = optional code 1 = `FooBar`) reports only `FooBar` (position 2, the later
# *const-family* position), never `BazQux` (position 1, the textually-earlier *general* position)
# -- and the reverse instruction shape, `CardTest(GenBad,ConstBad)` (`params=[8,1]`: position 0
# general, position 1 const), reports only `ConstBad` (the const-family position) even though it
# is the *later* argument. Within one family, the ordering is the ordinary ascending textual
# position (`WriteIO(FooBar,BazQux,1)` -> `FooBar`, the first *const*-family position; `WriteIO(1,
# BazQux,FooBar)` with only position 1 bad -> `BazQux`). Read together, this is one const-family
# pre-pass over every const-coded position (ascending) before the general-family pass runs at all,
# not a per-position textual scan -- plausibly the same constant-folding pre-pass CRBasic needs to
# size buffers/arrays from other arguments before it can even parse the rest of the call. Not
# traced to the decompilation; `docs/checker.md` records the oracle transcript.
_CONST_FAMILY_KINDS = frozenset({"const_expr", "const_expr_only", "datatype_kw"})

# CR300/CR350 do not share this pre-pass at all: their own position order is the plain ascending
# textual order, oracle-confirmed with the same `InternalTemp(GenBad,ConstBad)` shape
# (`params=[8,20]`: position 0 general, position 1 const-family `datatype_kw`) reporting the
# textually-*first* position (`GenBad is not defined.`) and then, unlike CR6/CR1000X, abandoning
# the rest of the statement's own parameter list entirely rather than moving on to check position 1
# at all (`Too many parameters:  ConstBad.` -- position 1's own token was never consumed). They also
# echo every undefined name found *inside* one compound expression as its own separate diagnostic
# line, not one whole-expression echo (`docs/checker.md`, "Round 4"): `Randomize(X + FooBar +
# BazQux)` is one `Invalid symbol in expression: X + FooBar + BazQux.` line on CR6/CR1000X but two
# separate `FooBar is not defined.` / `BazQux is not defined.` lines on CR300/CR350, in the source
# (left-to-right) order `_undefined_names` already walks in. Modelled below as a plain per-logger
# branch on `profile.name` (both loggers in each pair share identical behaviour, oracle-confirmed
# side by side) rather than a new `Profile` field, to keep this session's change inside
# `checker/expressions.py`/`checker/symbols.py` (`PLAN.md`'s ownership split).
_WHOLE_EXPR_ECHO_LOGGERS = frozenset({"cr6", "cr1000x"})

# `NAN`/`INF` are accepted as predefined float constants on all four loggers (oracle-confirmed,
# this session, `docs/checker.md`) despite being absent from every `data/<logger>/symbols.json` --
# that table is the compiler's *named-constant* lookup (`FUN_004ee4f0`, `docs/type-codes.md`), and
# `NAN`/`INF` evidently resolve through a separate, special-cased path in the expression compiler
# itself (a floating-point literal spelling, not a named constant) that this session did not trace
# to the decompilation. Added here rather than to the extracted `symbols.json` files so as not to
# misrepresent a value as extracted-from-the-binary-table when it demonstrably is not one of that
# table's rows (AGENTS.md: label inference and unverified behaviour explicitly; here the *acceptance*
# is oracle-verified even though the *mechanism* is not).
_EXTRA_PREDEFINED_SYMBOLS = frozenset({"NAN", "INF"})

# Constant-only-and-rejects-a-variable, the narrower half of `_CONST_FAMILY_KINDS` (`docs/
# type-codes.md` codes 1/39 `const_expr`, 5/38 `const_expr_only` -- **not** 20 `datatype_kw`,
# excluded below): oracle-confirmed uniform across all four loggers, identical message text and no
# per-logger branch needed (unlike the undefined-symbol family) -- `WriteIO(X,1)` ->
# `Variable illegal in Parameter: X.` and `Scan(X+1,Sec,0,0)` -> `Variable illegal in Parameter:
# X+1.` both reproduce byte-for-byte on CR6/CR1000X/CR300/CR350, whole-expression echo included
# (round 4, this session, `docs/checker.md`).
#
# Deliberately narrower than the type code alone would suggest, on two axes, both to route around
# round 3's own found regression (`Sample`'s `DataType` argument, also coded `1`, legitimately
# accepting a `StructureType` name, `structuretype_1.CR6`):
#
# 1. **`datatype_kw` (code 20) is excluded from this set entirely** -- it is a keyword-validated
#    slot (`thunk_FUN_00523010`, not `FUN_004b8700`) with its own per-instruction legal-value list
#    this project has not traced (`docs/type-codes.md` "inferred" confidence only), so no oracle
#    evidence here supports even the ordinary "rejects a variable" half of the rule for it.
# 2. **The trigger is "references a declared runtime variable" (`symbols.declared_variable_names`
#    -- `Public`/`Dim`/`Static`/a `Sub`/`Function` parameter), not "fails to fold to a constant."**
#    A `StructureType` name is neither: it is not a live variable (so this check leaves it alone,
#    unlike a generic `not consteval.is_constant(...)` test would) and it is not expected to fold
#    (so `Sample(1,S,VWSensor)` is simply never flagged by this narrower trigger, without needing
#    a `Sample`-specific carve-out at all). A corpus-wide scan this session ran (every `chm`/`repo`
#    const-only-family argument that is a bare `Name` resolving to a declared *variable*) found
#    exactly one candidate, and it turned out to be an unrelated CHM-prose-as-code parsing
#    artifact (`straincalc_1.CR6`'s `Average Strain` line, a stray heading fragment, not a real
#    statement) rather than a genuine override -- some further override may still exist outside
#    this corpus's ~400-instruction coverage, so this stays a scoped, corpus-verified rule, not a
#    claim that every const-only position everywhere is now covered.
_CONST_ONLY_VARIABLE_REJECT_KINDS = frozenset({"const_expr", "const_expr_only"})


def _variable_refs(expr: S.Expr | None, variable_names: set[str]) -> list[str]:
    """Every reference to a genuine runtime variable (`variable_names`) anywhere inside `expr`,
    in source order -- the const-only-family counterpart of `_undefined_names` above, same
    recursion shape (walks `Unary`/`Binary`/`Index`'s own name and args/`Member`'s base/
    `ArrayLiteral` items; `Member.member` text untouched, same reasoning as `_undefined_names`).
    A name that resolves to anything *other* than a declared variable (a `Const`, a
    `StructureType` name, a predefined symbol, ...) is not itself a live variable, so it is left
    alone here even though it may still be flagged separately by `_undefined_names` if it is
    undefined outright.
    """
    if expr is None:
        return []
    if isinstance(expr, S.Name):
        return [expr.name] if expr.name.upper() in variable_names else []
    if isinstance(expr, S.Unary):
        return _variable_refs(expr.operand, variable_names)
    if isinstance(expr, S.Binary):
        return _variable_refs(expr.left, variable_names) + _variable_refs(expr.right, variable_names)
    if isinstance(expr, S.Index):
        head = [expr.name] if expr.name.upper() in variable_names else []
        for a in expr.args:
            head += _variable_refs(a, variable_names)
        return head
    if isinstance(expr, S.Member):
        return _variable_refs(expr.base, variable_names)
    if isinstance(expr, S.ArrayLiteral):
        out: list[str] = []
        for item in expr.items:
            out += _variable_refs(item, variable_names)
        return out
    return []  # Number, String: nothing to resolve


# Variable-required kinds (`docs/type-codes.md` codes 8, 27, 29, 33, 41, 47, 48): the shared
# "variable or array" handler accepts a bare name, an array reference, or any expression that
# involves a real variable (silently promoted to a hidden temporary) -- but rejects an argument
# that is *itself* a compile-time constant (a bare literal, a named `Const`, or a pure constant
# expression) with `Invalid Variable or Array: %s.`, **unless the constant is a string**
# (oracle-confirmed this session: `Battery(5)`, `Battery(1+2)`, and `Battery(SomeConst)` -- a
# `Const SomeConst = 5` -- all rejected this way; `Battery("x")` compiles fine, no type check at
# all on a string constant here). Still **not** generically checked here, unlike the const-only
# family above: round 3 found two overriding instructions (`Move`'s third argument, `ModBusClient`'s
# last argument, both accepting a bare literal), and round 4's own corpus scan (this session) found
# a *third*, independent of either -- `MQTTPublishTable`'s optional `Longitude`/`Latitude`/
# `Altitude` arguments (code `148`, `mqttpublishtable_1.CR6` line 17, oracle-confirmed accepted in
# isolation on both CR6 and CR1000X: `MQTTPublishTable(1, 0, 5, Min, 2, -111.85, 41.76, 1)`). Three
# confirmed overrides found from a ~523-file corpus sampling only a fraction of each logger's
# ~400-instruction table make a blanket rule too risky to enable without a plausibly-still-
# incomplete per-instruction exception list; unlike the const-only-family rule above, no version of
# this trigger (bare-literal vs. "fails to fold", posed several ways during round 3 and again this
# round) has been found that reliably separates the genuine cases from the overrides using only the
# type-code table. Left unimplemented (AGENTS.md: never reject an accepted program); a future
# session would need to check each variable-required-coded parameter individually across all four
# loggers' instruction tables (or find the compiler's own per-instruction override flag) before
# enabling this safely.
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


def _known_names(program: S.Program, profile) -> set[str]:
    """Every name a checked expression position may legally reference: this program's own
    declarations (`symbols.declared_names`), this logger's predefined symbols/functions/
    instruction keywords (`profile.*`), its always-or-conditionally-valid system table names
    (`symbols.system_table_names`), and the two floating-point special values found this session to
    resolve outside the extracted symbol table entirely (`_EXTRA_PREDEFINED_SYMBOLS`).
    """
    return (
        symbols.declared_names(program)
        | set(profile.symbols)
        | set(profile.functions)
        | set(profile.instructions)
        | symbols.system_table_names(program)
        | _EXTRA_PREDEFINED_SYMBOLS
    )


def _report_statement(stmt: S.Instruction, positions: dict[int, str], known: set[str],
                       variable_names: set[str], profile, diags: Diagnostics) -> None:
    """One statement's own undefined-symbol and constant-only-family checks, at every position
    `_position_kinds` marks as routed through the general expression/symbol resolver -- a full
    expression-tree walk (`_undefined_names`), not just a direct bare-`Name` argument (round 4,
    this session: safe now that `_known_names` closes the two gaps -- `NAN`/`Status` -- that made
    this walk reject real programs when first tried in round 3, `docs/checker.md`).

    Both the *order* positions are checked in and the *shape* of what gets reported differ by
    logger family (oracle-confirmed this session, `docs/checker.md` "Round 4" -- see
    `_CONST_FAMILY_KINDS`/`_WHOLE_EXPR_ECHO_LOGGERS` above for the evidence): CR6/CR1000X check
    every constant-only-family position (ascending) before any general-family position regardless
    of textual order; CR300/CR350 check every position in plain ascending textual order, no family
    split. Both stop at the first position with *any* problem (an undefined name, checked at every
    position, or a live-variable reference, checked at const-only-family positions only,
    `_CONST_ONLY_VARIABLE_REJECT_KINDS`) and report only that one -- **within one position**, the
    undefined-name check runs first (the two are mutually exclusive for a single name: a name is
    either undefined or resolves to something, so this ordering only matters for picking which
    message a position with *both* an undefined name and, elsewhere in the same expression, a live
    variable gets -- not evidenced either way this session, so undefined-first was kept as the
    simpler, already-existing rule). Oracle-confirmed cross-family interleaving, not just two
    independent passes (`WriteIO(X,BazQux)`, `X` a live variable at the const-family position 0,
    `BazQux` undefined at the general-family position 1: CR6/CR1000X report `Variable illegal in
    Parameter: X.`, never reaching `BazQux`'s own undefined-name error even though the *undefined-
    name* pass alone, run across both families before any variable-reject check, would reach
    `BazQux` first if the two checks were separate top-level passes over the statement -- they are
    not; `PRT(GenBad,X,3,3,3)` on CR300, `GenBad` undefined at the general-family position 0 (the
    textually-first position there), `X` a live variable at the const-family position 1: CR300
    reports only `GenBad is not defined.`, never reaching `X` at all, confirming CR300/CR350 have
    no family reordering for this combined check either). Neither family is known to check past the
    first bad position in the same statement -- under-reporting a second, independent bad position
    in the same call is the deliberate, safe side of that gap (AGENTS.md: never reject an accepted
    program), not a claim that the real compiler is silent about it. The variable-reject message
    itself (`Variable illegal in Parameter: %s.`) is oracle-confirmed uniform across all four
    loggers (`docs/checker.md`), so no per-logger branch for its own text.
    """
    def _check_position(i: int) -> bool:
        """Checks position `i`, reports at most one diagnostic (CR6/CR1000X) or up to two
        (CR300/CR350, see below), returns whether it reported anything.

        **CR300/CR350 do not share CR6/CR1000X's mutual exclusivity** between the undefined-name
        check and the const-only-family "rejects a variable" check, oracle-confirmed this round
        (`docs/loggers.md`): at a const-family position (`_CONST_ONLY_VARIABLE_REJECT_KINDS`), an
        *undefined* name gets **both** its own undefined-name line(s) *and* a `Variable illegal in
        Parameter: <whole arg text>.` line right after -- `TCDiff(Dest,1,BazQux,...)` on
        `cr300comp.exe` -> `BazQux is not defined.` then `Variable illegal in Parameter: BazQux.`,
        two lines, where CR6/CR1000X give only `Invalid symbol in expression: BazQux.` (one line,
        the existing rule, unchanged). A compound expression mixing an undefined name with a live
        variable (`TCDiff(Dest,1,X+BazQux,...)`, `X` declared) gives `BazQux is not defined.` (the
        undefined name only, per CR300/CR350's existing per-name echo) then `Variable illegal in
        Parameter: X+BazQux.` (the whole expression, since it is not a foldable constant either
        way) -- i.e. the second check's trigger is "argument contains any non-constant name
        reference at all" (undefined *or* a declared variable), not "is a declared variable"
        alone, and always echoes the whole argument once. Modelled below as one unified
        computation (`trigger = names or var_refs`) that happens to reduce to the pre-existing
        CR6/CR1000X mutual-exclusivity behaviour automatically, since `names` is always empty by
        the time CR6/CR1000X (`_WHOLE_EXPR_ECHO_LOGGERS`) reach the second check (a non-empty
        `names` already returned above, for that family only).
        """
        if i >= len(stmt.args) or stmt.args[i] is None:
            return False
        arg = stmt.args[i]
        names = _undefined_names(arg, known)
        whole_echo = profile.name in _WHOLE_EXPR_ECHO_LOGGERS
        is_const_family = positions[i] in _CONST_ONLY_VARIABLE_REJECT_KINDS

        if names:
            if whole_echo:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=_arg_text(stmt, i)),
                            stmt.file)
                return True
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
            if not is_const_family:
                return True
            # CR300/CR350 const-family position: fall through to also run the
            # "Variable illegal in Parameter" check below, independent of the undefined-name
            # report just made (oracle-confirmed; see this method's own docstring).

        if is_const_family:
            var_refs = _variable_refs(arg, variable_names)
            if names or var_refs:
                diags.error(stmt.line, f"Variable illegal in Parameter: {_arg_text(stmt, i)}.", stmt.file)
                return True

        return bool(names)

    if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
        order = sorted(i for i, k in positions.items() if k in _CONST_FAMILY_KINDS)
        order += sorted(i for i, k in positions.items() if k not in _CONST_FAMILY_KINDS)
    else:
        order = sorted(positions)

    for i in order:
        if _check_position(i):
            return


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """Undefined-symbol checking at every `Instruction` argument position whose declared type code
    routes through the real expression/named-constant resolver (`_position_kinds`) -- a full
    expression-tree walk (`_undefined_names`), not restricted to a direct bare `Name` argument
    (round 4, this session; rules and evidence: `_report_statement` above, `docs/checker.md`).

    Still deliberately narrow in scope, unchanged from round 3: only positions
    `_RESOLVED_KINDS` names (never an identifier/raw-text-kind position such as a `DataTable`/
    `ConstTable` name, `Member.member` text, or a `Sub`/`Function` argument list's own runtime
    scoping); `symbols.declared_names` stays whole-program and unscoped rather than tracking which
    names are visible inside which `Sub`/`Function` (both documented limitations, `docs/checker.md`
    "Not implemented" -- tightening either risks a false reject this session did not have the
    oracle evidence to rule out, so neither was attempted here).

    `env` is accepted (unused) to keep this call site's signature stable for a future session
    that wires in the variable-required type-code check (still not implemented -- see the
    docstring above `_VARIABLE_REQUIRED_KINDS`; the const-only-family "rejects a variable" check
    is implemented, `_CONST_ONLY_VARIABLE_REJECT_KINDS`).
    """
    known = _known_names(program, profile)
    variable_names = symbols.declared_variable_names(program)

    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        positions = _position_kinds(record["params"])
        if positions:
            _report_statement(stmt, positions, known, variable_names, profile, diags)

            # `_VARIABLE_REQUIRED_KINDS` (`Invalid Variable or Array: %s.`) is intentionally not
            # applied here despite oracle evidence that it is a real rule for *some* instructions
            # (`Battery(5)`, `Erase(5)`, `ReadIO(5,1)`, all code 8/27/29 on CR6, all oracle-
            # confirmed rejected). Found as a **regression** during round 3's own testing against
            # the full corpus on all four loggers: `Move`'s own third argument (code 41) accepts a
            # literal `0` (`embracer.cr6`), and `ModBusClient`'s own last, optional argument (also
            # code 27, the *same* code that correctly rejects a literal for `Erase`) also accepts a
            # literal `2` (`modbusclient_2.CR6`) -- both on CR1000X. So the same type code does not
            # behave uniformly across every instruction that uses it; at least `Move` and
            # `ModBusClient` override it (most likely their own `compile_fn`, similar to the
            # const-only kinds' `Sample`/`DataType` exception, `docs/checker.md`). Still not
            # implemented this session either: see `docs/checker.md` "Constant-only and
            # variable-required type codes" for the full evidence and what a future session would
            # need to verify first.

    # An `Assignment`'s own RHS (`x = <value>`): not an `Instruction` at all, so untouched by the
    # loop above -- a previously-unaddressed gap on *every* logger, found via this round's own
    # CR300/CR350 repo-mismatch chase (`WDogTimer = WatchdogTimer(0,Sec)`, `WatchdogTimer` a real
    # CR6/CR1000X function absent from CR300/CR350 entirely, `docs/loggers.md`). Oracle-confirmed
    # pass 2, same gating as the rest of this function (`BazQux(0,Sec)` in place of `WatchdogTimer`
    # on CR6: alone, `line N: Invalid symbol in expression: <assignment's own text>.`; preceded by
    # an unrelated pass-1 `Variable not named.`, suppressed entirely -- `docs/loggers.md`). Two
    # differences from the instruction-argument case above, both oracle-confirmed:
    #
    # 1. The whole-expression echo (CR6/CR1000X) is the **entire assignment statement's own
    #    text**, target and `=` included (`stmt.text`, already the raw segment text with its
    #    original indentation -- the same field `structure.py`'s "Expr" fallback echoes), not just
    #    the value sub-expression (unlike a `Const`'s `value_span`, there is no separate span to
    #    prefer here since the oracle's own echo includes the target).
    # 2. CR300/CR350 additionally emit a `Dimensions not declared: <text>` follow-up line after
    #    their own per-name `<name> is not defined.` line(s) -- not reproduced here (no oracle
    #    evidence yet for its exact trigger/shape beyond this one example, and it is a second,
    #    distinct message rather than a difference in this one's own text); the per-name line(s)
    #    alone already turn the previously-silent false accept into a correct reject.
    for stmt in program.statements:
        if not isinstance(stmt, S.Assignment):
            continue
        names = _undefined_names(stmt.value, known)
        if not names:
            continue
        if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=stmt.text), stmt.file)
        else:
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)


def check_consts(program: S.Program, profile, diags: Diagnostics) -> None:
    """`Const <name> = <value>.` initializer resolution (this round's own new check, all four
    loggers): a `Const`'s value expression is folded eagerly, at declaration time, so an
    unresolved name inside it is diagnosed there and then rather than deferred to any later
    argument-checking pass -- oracle-confirmed **pass 1** (ungated, unconditional), not pass 2:
    `Const Foo = BazQux` reports its own diagnostic *alongside* an unrelated, earlier `Public`
    with no name (`Variable not named.`), on every logger, where an ordinary pass-2 finding (e.g.
    `expressions.check`'s own instruction-argument checks) would have been completely suppressed
    by that same earlier pass-1 error (`docs/checker.md` "Pass model"; probe transcript,
    `docs/loggers.md`). Message family and per-logger shape are identical to the general
    undefined-symbol check above, reusing the same `_WHOLE_EXPR_ECHO_LOGGERS` split: CR6/CR1000X
    report one whole-expression `Invalid symbol in expression: <value text>.` line; CR300/CR350
    report each undefined name in the expression as its own `<name> is not defined.` line (oracle,
    a compound expression, `docs/loggers.md`: `Const Foo = BazQux + Corge` -> one line, `Invalid
    symbol in expression: BazQux + Corge.`, on CR6; two lines, `BazQux is not defined.` then
    `Corge is not defined.`, on CR300 -- same shape as an instruction argument's own compound
    expression, `_report_statement` above). `ConstDecl.value_span` (new field, `syntax.py`/
    `parser.py`, same technique as `Instruction.arg_spans`) captures the value expression's own
    source columns so the whole-expression echo is the verbatim text, not a re-derived form.

    Narrow and deliberately conservative, mirroring `check` above: only an outright-undefined
    name is flagged (`_undefined_names` against the same `known` universe, so a `Const` that
    legitimately forward/cross-references another declared name is never flagged); a `Const`
    referencing a *live* runtime variable is a real, different, oracle-confirmed rule of its own
    (`Variable used in CONST expression.`, all loggers, `docs/loggers.md`) but not implemented
    here -- zero occurrences of that message anywhere in this project's own corpus sample, so
    there is no evidence this specific project's programs need it, and guessing its trigger
    shape/gating without corpus signal risks a false reject for no measured benefit.
    """
    known = _known_names(program, profile)
    whole_echo = profile.name in _WHOLE_EXPR_ECHO_LOGGERS
    for stmt in program.statements:
        if not isinstance(stmt, S.ConstDecl):
            continue
        names = _undefined_names(stmt.value, known)
        if not names:
            continue
        if whole_echo:
            if stmt.value_span is not None:
                text = stmt.text[stmt.value_span[0]:stmt.value_span[1]]
            else:  # defensive: only reached if a `Const` was built without a span
                text = stmt.value.name if isinstance(stmt.value, S.Name) else names[0]
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=text), stmt.file)
        else:
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
