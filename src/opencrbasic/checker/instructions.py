"""Per-instruction argument checks that sit on top of the generic type-code parsing
(`docs/type-codes.md`) and are not expressible from the type code alone. Currently just the
"analog channel" range check. Rules and oracle evidence: `docs/checker.md`.
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics

# Instruction name (upper-case) -> 1-based argument positions that must be a constant in
# U1..U12's value range (201..212, `data/symbols.json`), not merely any constant integer.
# Found by cross-referencing `FUN_005160a0` ("Invalid channel %d, must use U1..U12.",
# `docs/messages.tsv`) call sites against each instruction's declared parameter shape
# (`data/instructions.json`); confirmed against the corpus (`docs/checker.md`): every
# `TCDiff`/`VoltSe`/`VoltDiff` "channel" mismatch in `tests/corpus/chm` is this instruction's own
# 4th argument (`DiffChan`/`SEChan`) written as a plain CR1000/CR800-style channel number instead
# of CR6's `U1`..`U12` terminal name. `ACPower`'s `ChanV`/`ChanI` (4th/7th) are the same shape but
# not fully verified (`ACPower`'s own corpus example raises more channel errors than this alone
# predicts -- see docs/checker.md).
CHANNEL_ARGS: dict[str, tuple[int, ...]] = {
    "TCDIFF": (4,),
    "TCSE": (4,),
    "VOLTSE": (4,),
    "VOLTDIFF": (4,),
    "ACPOWER": (4, 7),
}

_U_MIN, _U_MAX = 201, 212


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        positions = CHANNEL_ARGS.get(stmt.keyword.upper())
        if not positions:
            continue
        for pos in positions:
            _check_channel_arg(stmt, pos, env, diags)


def _check_channel_arg(stmt: S.Instruction, pos: int, env: consteval.Env, diags: Diagnostics) -> None:
    idx = pos - 1
    if idx >= len(stmt.args) or stmt.args[idx] is None:
        return
    arg = stmt.args[idx]
    if not consteval.is_constant(arg, env):
        return  # a variable here is already rejected by the generic const-expr parameter (code
        # 1's own "Variable illegal in Parameter" -- docs/type-codes.md); not our job to re-check.
    try:
        value = consteval.value_of(arg, env)
    except consteval.NotConstant:
        return
    if not (_U_MIN <= value <= _U_MAX):
        diags.error(stmt.line, f"Invalid channel {int(value)}, must use U1..U12.", stmt.file)
