"""Per-logger analog-channel terminal model.

Generalises the analog-channel range check (originally CR6-only, hard-coded `U1..U12`) across all
four loggers, driven by oracle-probed per-logger data (`data/<logger>/terminals.json`; evidence,
probes and the message-text differences: `docs/loggers.md`).

Each logger's `terminals.json` names its own channel "kinds" -- CR6 has one shared `u_terminal`
kind for both differential and single-ended channels (both take a `U1..U12` terminal name, same
value range 201..212); CR1000X and CR300/CR350 distinguish `diff_chan` from `se_chan` (a plain
integer, different valid ranges and, for CR1000X's `diff_chan` only, a burst-mode negative-value
allowance, `docs/loggers.md`) -- and maps each channel-checked instruction to the 1-based argument
position(s) that use each kind. `checker/instructions.py` calls `check` here; the two modules stay
separate because this one owns the per-logger *data*, while `instructions.py` is where a future,
non-channel per-instruction check would go.
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile


def _evaluate(value: float, kind: dict) -> tuple[bool, int]:
    """(is_valid, displayed_value) for a constant `value` against one channel `kind`'s range.

    `transform: "abs"` (CR1000X's `diff_chan` only): the real compiler's shown value and range
    test both use the argument's absolute value -- burst-mode measurements encode "same channel,
    repeated" as a negative channel number (`docs/loggers.md`, oracle: `VoltDiff` `DiffChan` -8
    compiles, -9 fails as "Invalid channel 9, must use 1..8."). `transform: "none"` (every other
    kind): the raw value is both tested and shown.
    """
    v = abs(value) if kind.get("transform") == "abs" else value
    return kind["min"] <= v <= kind["max"], int(v)


def check(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    terminals = profile.terminals
    checked_instructions: dict = terminals.get("instructions", {})
    channels: dict = terminals.get("channels", {})
    if not checked_instructions:
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        positions = checked_instructions.get(stmt.keyword.upper())
        if not positions:
            continue
        for pos, kind_name in positions:
            _check_arg(stmt, pos, channels[kind_name], env, diags)


def _check_arg(stmt: S.Instruction, pos: int, kind: dict, env: consteval.Env, diags: Diagnostics) -> None:
    idx = pos - 1
    if idx >= len(stmt.args) or stmt.args[idx] is None:
        return
    arg = stmt.args[idx]
    if not consteval.is_constant(arg, env):
        return  # a variable here is already rejected by the generic const-expr parameter (type
        # code 1's own "Variable illegal in Parameter" -- docs/type-codes.md); not our job.
    try:
        value = consteval.value_of(arg, env)
    except consteval.NotConstant:
        return
    if kind.get("skip_nonpositive") and value <= 0:
        # CR300/CR350 (docs/loggers.md): a channel <= 0 is accepted far outside any range this
        # checker would derive with confidence (oracle: DiffChan -4 compiles with a 1..3 range,
        # -100 fails as "Channel 99"; -1 also compiles for an SEChan that never takes negative
        # values per its own CHM doc). Left unchecked rather than guessed, per AGENTS.md's "never
        # falsely reject" priority -- every corpus mismatch this rule fixes is a plain positive
        # channel number written where a different range or terminal name was required.
        return
    ok, shown = _evaluate(value, kind)
    if not ok:
        diags.error(stmt.line, kind["message"].format(v=shown), stmt.file)
