"""Instruction-specific checks that are not channel/port related (that logic lives in
`checker/instructions.py`, a different workstream, `PLAN.md`). A required argument position with
no corresponding value at all (`check`, pass-1, gated normally through `checker/__init__.py`), plus
a small set of *ungated* `Scan`/`DataInterval` numeric-value checks (`check_scan_params`,
`check_data_interval`, called from `checker/warnings.py`'s `check_early` -- oracle-confirmed to
fire even on an otherwise-failed compile, same as that module's own ungated warnings; see its
module docstring). Rules and oracle evidence: `docs/checker.md` (the required-parameter check) and
`docs/instructions.md` (the numeric-value checks).
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from .expressions import _type_code_kinds

# `type_codes.json` codes exempt from the "missing required parameter" error: when the
# statement's own physical line runs out of tokens right at one of these, `FUN_004baf00` silently
# fills it (and everything declared after it) with a default/sentinel value and ends that
# statement's own parameter parsing with no diagnostic at all, rather than reporting anything --
# `docs/type-codes.md` code 4's row ("this code (and 16, 17, 36, 37, 45, 46) is exempt from the
# loop-level `Too few parameters in instruction.` check"); this is what lets a bare `Do`/`Loop`/
# `Else` (code 4, no trailing `While`/`Until` condition), bare `End`/`Exit` (16), and bare `Next`
# (17) all compile with zero declared arguments even though their own table row lists the code as
# non-optional (code < 100). 36/45/46 are listed here too for documentation fidelity to that
# passage, but never reach this check in practice: `_real_params` below already drops them (they
# are `none`-kind, not real argument slots at all).
_TOLERANT_CODES = frozenset({4, 16, 17, 36, 37, 45, 46})


def _real_params(params: list[int]) -> list[int]:
    """`params` with every `none`-kind code removed (14 `EndSub`/`EndFunction`, 32
    `RainFlowSample`'s first slot, 36 `EndSelect`, 46 `EndStructureType`/`EndObjectType`,
    `docs/type-codes.md`) -- these are structural markers in the raw per-instruction table that
    consume no user-written token at all, so counting them as "required" would flag a legitimately
    argument-less statement (`EndSub` alone), and leaving one in the list at all would shift every
    later position out of alignment with the parsed `stmt.args` (found as a regression while
    developing this check: bare `EndSub`, `params=[14]`, was flagged as one required argument
    short; `RainFlowSample`'s real first user argument is its own table position 1, code 11, after
    the position-0 no-op marker, code 32)."""
    kinds = _type_code_kinds()
    return [c for c in params if kinds.get(c - 100 if c >= 100 else c) != "none"]


def _required_count(params: list[int]) -> int:
    """How many leading `params` entries are non-optional (code < 100). `FUN_004baf00`'s own
    rule -- "All arguments after the first optional parameter must be optional." -- means the
    first optional (code >= 100) entry marks where the required prefix ends; the CHM param tables
    never observed one out of order, and `data/instructions.json` is table-driven so this holds by
    construction."""
    for i, code in enumerate(params):
        if code >= 100:
            return i
    return len(params)


def check(program: S.Program, profile, diags: Diagnostics) -> None:
    """Flags an `Instruction` statement whose declared required-parameter prefix runs past the end
    of the parsed argument list -- almost always a CHM corpus example whose instruction call was
    wrapped across two physical lines with no `_` continuation (`docs/checker.md`: `Average` alone
    on one line, `(1,TCTemp,FP2,False)` on the next -- our own tolerant "auto-close an unclosed `(`
    at end of line" parsing, `docs/frontend.md`, silently produces a *short* argument list rather
    than a syntax error, unlike the real compiler, which reports the gap and abandons the rest of
    that statement's own parsing there).

    Deliberately checks only a *shortfall in count* (`len(stmt.args) < required`), never a `None`
    gap from a skipped comma in the middle of the list -- no oracle evidence this session found
    supports flagging that shape, and AGENTS.md's priority (never reject an accepted program) rules
    out guessing.

    This is pass-1 work (`checker/__init__.py`'s docstring): it fires regardless of what other
    errors the file already has. It does *not* need to separately suppress
    `checker/instructions.py`'s own per-instruction checks (channel ranges etc.) for the statement
    it flags -- `checker/__init__.py` already gates all of pass 2 behind "no pass-1 error anywhere
    in the file" (oracle evidence there), which covers this case for free.
    """
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        params = _real_params(record["params"])
        required = _required_count(params)
        if len(stmt.args) >= required:
            continue
        missing_code = params[len(stmt.args)]
        if missing_code in _TOLERANT_CODES:
            continue
        diags.error(stmt.line, "Invalid or missing parameter.", stmt.file)
