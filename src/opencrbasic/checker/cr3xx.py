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
