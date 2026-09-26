"""Per-logger analog-channel and port terminal model.

Generalises the analog-channel range check (originally CR6-only, hard-coded `U1..U12`) across all
four loggers, driven by oracle-probed per-logger data (`data/<logger>/terminals.json`; evidence,
probes and the message-text differences: `docs/loggers.md`, `docs/instructions.md`).

Each logger's `terminals.json` names its own channel "kinds" -- CR6 has separate `u_terminal`
(single-ended-shaped: any `U1..U12`, no oddness constraint; `ACPower`, `CS616`'s `CS616Chan`,
`ExciteV`/`ExciteI`'s `ExChan`), `u_terminal_diff` (`VoltDiff`/`TCDiff`'s `DiffChan`: `U1..U12` only
on an *odd* terminal, a second, distinct message when the value is otherwise in range) and
`u_terminal_se` (`VoltSe`/`TCSe`'s `SEChan`: any `U1..U12`, no oddness) -- both of the latter two
also allow a burst-mode negative value (`transform: "abs"`); CR1000X and CR300/CR350 instead use
`diff_chan`/`se_chan`, a plain integer with a different valid range and message
(`docs/loggers.md`). A `"port"`-shaped kind (`port_c1c4_u1u12` on CR6, `port_c1c8` on CR1000X) is a
disjoint union of ranges (`PortSet`/`PortGet`'s `Port`, `CS616`'s `CS616Port`); `sdi_port` (CR6) /
`sdi_port_cr1000x` (CR1000X) is a disjoint union restricted to *odd* terminals only (SDI-12's own
half-duplex wiring, `docs/instructions.md`). `checker/instructions.py` calls `check` here; the two
modules stay separate because this one owns the per-logger *data*, while `instructions.py` is the
shared entry point another workstream also uses.

A `kind` dict:
  - `ranges`: list of `[min, max]` pairs (a single-range kind may instead give `min`/`max` directly,
    kept for the pre-existing CR1000X/CR300/CR350 `diff_chan`/`se_chan` entries).
  - `transform`: `"abs"` (test/show `abs(value)`, burst-mode) or `"none"` (default).
  - `skip_nonpositive`: CR300/CR350 (`docs/loggers.md`) -- a value `<= 0` is deliberately left
    unchecked (real, different, not-yet-modelled validation shape there; never guessed at).
  - `odd_only` + `odd_message`: after the range check passes, the (transformed) value must also be
    odd, else `odd_message` fires instead of `message` (`docs/instructions.md`'s "Invalid start
    channel for Differential Measurement..." / "Invalid SDI-12 port..." findings).
  - `message`: the range-check failure text, `{v}` = the displayed (transformed) value.
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile


def _ranges(kind: dict) -> list[tuple[int, int]]:
    if "ranges" in kind:
        return [tuple(r) for r in kind["ranges"]]
    return [(kind["min"], kind["max"])]


def _in_range(v: float, kind: dict) -> bool:
    return any(lo <= v <= hi for lo, hi in _ranges(kind))


def _evaluate(value: float, kind: dict) -> tuple[bool, bool, int]:
    """(in_range, odd_ok, displayed_value) for a constant `value` against one channel `kind`.

    `transform: "abs"`: the real compiler's shown value and both tests use the argument's absolute
    value -- burst-mode measurements encode "same channel, repeated" as a negative channel number
    (`docs/loggers.md`, oracle: CR6 `VoltDiff` `DiffChan` -1 (raw -201) compiles, `TCSe` `SEChan`
    -202 compiles). `transform: "none"` (every other kind): the raw value is both tested and shown.
    """
    v = abs(value) if kind.get("transform") == "abs" else value
    in_range = _in_range(v, kind)
    odd_ok = (not kind.get("odd_only")) or (in_range and int(v) % 2 == 1)
    return in_range, odd_ok, int(v)


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
    if not isinstance(value, (int, float)):
        return
    if kind.get("skip_nonpositive") and value <= 0:
        # CR300/CR350 (docs/loggers.md): a channel <= 0 is accepted far outside any range this
        # checker would derive with confidence (oracle: DiffChan -4 compiles with a 1..3 range,
        # -100 fails as "Channel 99"; -1 also compiles for an SEChan that never takes negative
        # values per its own CHM doc). Left unchecked rather than guessed, per AGENTS.md's "never
        # falsely reject" priority -- every corpus mismatch this rule fixes is a plain positive
        # channel number written where a different range or terminal name was required.
        return
    in_range, odd_ok, shown = _evaluate(value, kind)
    if not in_range:
        diags.error(stmt.line, kind["message"].format(v=shown), stmt.file)
    elif not odd_ok:
        diags.error(stmt.line, kind["odd_message"].format(v=shown), stmt.file)
