"""CR300/CR350-specific checks that do not generalise to CR6/CR1000X: message variants and
validations these two loggers' own smaller instruction/function set needs. Owned separately from
the shared `checker/*` modules (`PLAN.md`); `check` below is a no-op on every other logger, so
`checker/__init__.py`'s own call site can stay a single unconditional line. Rules and oracle
evidence: `docs/loggers.md`.
"""

from __future__ import annotations

from .. import syntax as S
from ..diagnostics import Diagnostics
from . import expressions, symbols

# `SerialOpen`'s own `ComPort` argument (position 0, the only position checked here) is the one
# `Serial*` function on CR300/CR350 whose compile-time validator additionally requires a genuine
# compile-time constant, reporting `ComPort 0 illegal.` then `Parameter must be a constant
# integer: %s.` when it is not -- oracle-confirmed (`docs/loggers.md`): an undefined name there
# gets the ordinary per-name `%s is not defined.` line(s) first (the same shape every other
# unresolved-name position on these two loggers already gets), **plus** these two extra lines,
# which no other `Serial*` function shares (`SerialClose`/`SerialFlush`/`SerialOut`/`SerialIn`/...
# report only the plain per-name undefined-name line(s) for the same argument shape, nothing
# else -- oracle-confirmed each individually). A live, declared *variable* in this position also
# triggers the same two extra lines with no undefined-name line (it is not itself undefined) --
# "not a compile-time constant" is the real trigger, not "undefined" specifically (oracle:
# `SerialOpen(MyPort,...)`, `MyPort` a declared `Public`, still gets `ComPort 0 illegal.`/
# `Parameter must be a constant integer: ...`). Deliberately **not** modelled here: a genuine
# constant that is merely out of range (oracle: `SerialOpen(99,...)` gives `ComPort 99 illegal.`
# plus a *different* second line, `No Matching, or out of place ) in expression: ...`, not this
# one) -- the valid `ComPort` value set for CR300/CR350's own smaller hardware was not oracle-
# pinned down this round, and guessing it risks flagging a value this project cannot confirm is
# actually invalid (AGENTS.md: never guess into a false reject). Only the unambiguous "not a
# constant at all" trigger (an undefined name or a live variable, never a resolvable value) is
# implemented, which never risks flagging a value the real compiler would accept.
_SERIALOPEN = "SERIALOPEN"
_COMPORT_ARG_POSITION = 0
# Every `Serial*` function whose first argument is a `ComPort` (`data/<logger>/functions.json`:
# same `argtypes[0]` shape as `SerialOpen`'s own position 0) -- oracle-confirmed for `SerialOpen`/
# `SerialClose`/`SerialFlush`/`SerialOut` individually; `SerialIn`/`SerialInBlock`/`SerialInChk`/
# `SerialBrk` added on the same documented convention (a `Serial*` function's own first argument
# is always its `ComPort`, per every CHM `Syntax` line for this family), not each individually
# oracle-probed -- safe either way, since an inclusion here only ever *adds* an already-correct-
# for-`SerialOpen`-style undefined-name diagnostic (never the `SerialOpen`-only ComPort/constant
# pair, gated separately below), so a wrong inclusion costs nothing worse than under-reporting.
_SERIAL_FUNCTIONS_WITH_COMPORT_ARG = frozenset({
    "SERIALOPEN", "SERIALCLOSE", "SERIALFLUSH", "SERIALOUT", "SERIALIN", "SERIALINBLOCK",
    "SERIALINCHK", "SERIALBRK",
})


def check(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350 only; a no-op on every other logger. Oracle-confirmed **ungated** (fires
    alongside unrelated pass-1/pass-2 errors elsewhere in the same file, `docs/loggers.md`:
    `acceptdatarecords_2.CR6`'s own `SerialOpen(COMC1,...)` diagnostic prints together with a
    separate, later `CallTable`-shaped issue in the same file) -- called unconditionally,
    alongside `expressions.check_consts`.
    """
    if profile.name not in ("cr300", "cr350"):
        return
    known = expressions._known_names(program, profile)
    variable_names = symbols.declared_variable_names(program)
    for stmt in program.statements:
        if not isinstance(stmt, S.Call):
            continue
        name = stmt.name.upper()
        if name not in _SERIAL_FUNCTIONS_WITH_COMPORT_ARG:
            continue
        if _COMPORT_ARG_POSITION >= len(stmt.args) or stmt.args[_COMPORT_ARG_POSITION] is None:
            continue
        arg = stmt.args[_COMPORT_ARG_POSITION]
        names = expressions._undefined_names(arg, known)
        for undefined_name in names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=undefined_name),
                        stmt.file)
        if name != _SERIALOPEN:
            continue
        is_variable = bool(expressions._variable_refs(arg, variable_names))
        if names or is_variable:
            diags.error(stmt.line, "ComPort 0 illegal.", stmt.file)
            # The echoed statement text runs to the physical end of line and (oracle-confirmed,
            # `docs/loggers.md`) includes that line's own trailing newline before the closing
            # period -- the same quirk `docs/checker.md` documents for `Units`, here embedded
            # directly in the message text since `Diagnostic.cr6comp` does no wrapping of its own.
            diags.error(stmt.line, f"Parameter must be a constant integer: {stmt.text}\n.",
                        stmt.file)


# `check_expr_cascade` (Round 7b): once any undefined-name-class error has fired anywhere in tier
# B (an instruction-argument or assignment-RHS name `expressions.check`/`check_consts` could not
# resolve, or the const-family "rejects a variable" `Variable illegal in Parameter: %s.` finding),
# CR300/CR350's own parser leaves some shared state pointing one argument further along than it
# should -- oracle-confirmed (`docs/loggers.md`): the *next* statement that both (a) is a bare
# instruction call written with two or more arguments and (b) is not itself already erroring gets
# a spurious extra `Too many parameters:  <its own second argument's verbatim text>.` on its own
# line, `checker/instr_rules.py`'s own real message reused for a shape that instruction's own
# argument count never earns on its own (`WriteIO(1,2)`, `PortSet(1,2)`, ... all compile clean
# standalone). Confirmed **not** anchored to the *specific* failing statement -- when several
# statements in a row each have their own undefined-name error, only the search from the *last* one
# produces a cascade line, and the search itself skips forward over any statement that already has
# a diagnostic of its own (whether or not it has two-plus arguments) as well as any clean statement
# that does not qualify (fewer than two arguments, or not an instruction at all -- a plain
# assignment or a one-argument call such as `Battery(BattV)` is silently passed over, not itself
# flagged and not a stopping point) -- confirmed with two chained failures each searching past the
# other, a bare assignment and a one-argument call each skipped in the middle, and a `'comment`
# line skipped for free (comments are never `program.statements` entries at all). **CR6/CR1000X do
# not share this at all** (oracle-confirmed with the same shape, an undefined const-family name
# immediately followed by a two-argument instruction: no extra line). Not yet confirmed beyond this
# narrow, oracle-tested trigger set (`docs/loggers.md` "Round 7b" has the full transcript and the
# open questions this round did not chase): whether a placement error or an unclosed-`DataTable`
# finding (this project's other two tier-B error sources) triggers the same cascade, and whether
# `cr3xx.check`'s own, separately-ungated `SerialOpen`/`ComPort` finding above ever interacts with
# it (oracle-confirmed it cannot in *this* checker's own pass model: that finding fires ungated,
# before tier B's `if not diags.errors` gate is even evaluated, so tier B -- and this function, only
# ever called from inside it -- never runs at all once it has fired).
_UNRESOLVED_SUFFIX = " is not defined."
_VARIABLE_ILLEGAL_PREFIX = "Variable illegal in Parameter:"


def _is_cascade_trigger(diag) -> bool:
    return diag.severity == "error" and (
        diag.message.endswith(_UNRESOLVED_SUFFIX) or diag.message.startswith(_VARIABLE_ILLEGAL_PREFIX)
    )


def check_expr_cascade(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350 only; a no-op on every other logger and when tier B recorded no undefined-name-
    class error. Called from `checker/__init__.py` immediately after `expressions.check`/
    `instr_data.check_unclosed_calltable`, inside the same `if not diags.errors:` tier-B block, so
    it sees every diagnostic tier B has recorded so far (and only those -- see this module's
    comment above for why `cr3xx.check`'s own earlier, ungated finding can never coexist with it).
    """
    if profile.name not in ("cr300", "cr350"):
        return
    trigger_lines = {(d.line, d.file) for d in diags.items if _is_cascade_trigger(d)}
    if not trigger_lines:
        return
    statements = program.statements
    anchor_index = None
    for i, stmt in enumerate(statements):
        if (stmt.line, stmt.file) in trigger_lines:
            anchor_index = i
    if anchor_index is None:
        return
    existing_lines = {(d.line, d.file) for d in diags.items}
    for stmt in statements[anchor_index + 1:]:
        if (stmt.line, stmt.file) in existing_lines:
            continue
        if not isinstance(stmt, S.Instruction):
            continue
        if len(stmt.args) < 2 or stmt.args[1] is None:
            continue
        text = expressions._arg_text(stmt, 1)
        diags.error(stmt.line, f"Too many parameters:  {text}.", stmt.file)
        return
