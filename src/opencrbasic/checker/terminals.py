"""Per-logger analog-channel and port terminal model.

Models the analog-channel range check for all four loggers, driven by oracle-probed per-logger
data (`data/<logger>/terminals.json`; evidence, probes and the message-text differences:
`docs/loggers.md`, `docs/instructions.md`).

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
  - `allowed` + `allowed_message`: after the range check passes, the value must also be one of
    `allowed` (`SPIOpen`'s `C1, U1, U5, or U9`, `TimerInput`'s starting channel).
  - `secondary_when_invalid`: CR1000X -- an out-of-range value also gets the `odd_message`/
    `allowed_message` line after `message` (its port converter returns 0 for a bad port, which
    then fails the instruction's own test; CR6 stops at `message`).
  - `message`: the range-check failure text, `{v}` = the displayed (transformed) value.

CR6 ports go through one converter (decompiled `FUN_00515ff0`): `C1..C16` (101..116) and
`U1..U12` (201..212) are valid, `C5..C16` being the same terminals as `U1..U12` (oracle,
2026-10-01: `PortSet(109,1)`, `SPIOpen(105,...)` compile); CR1000X takes `C1..C8`.
"""

import math

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile
from . import longconv


def _ranges(kind: dict) -> list[tuple[int, int]]:
    if "ranges" in kind:
        return [tuple(r) for r in kind["ranges"]]
    return [(kind["min"], kind["max"])]


def _in_range(v: float, kind: dict) -> bool:
    return any(lo <= v <= hi for lo, hi in _ranges(kind))


def _cr6_burst_kind(kind: dict) -> bool:
    return kind.get("transform") == "abs" and _ranges(kind) == [(201, 212)]


def _evaluate(value: float, kind: dict) -> tuple[bool, bool, int]:
    """(in_range, odd_ok, displayed_value) for a constant `value` against one channel `kind`.

    `transform: "abs"`: the real compiler's shown value and both tests use the argument's absolute
    value -- burst-mode measurements encode "same channel, repeated" as a negative channel number
    (`docs/loggers.md`, oracle: CR6 `VoltDiff` `DiffChan` -1 (raw -201) compiles, `TCSe` `SEChan`
    -202 compiles). `transform: "none"` (every other kind): the raw value is both tested and shown.
    """
    v = abs(value) if kind.get("transform") == "abs" else value
    if _cr6_burst_kind(kind) and -200 < value < 0:
        v = value  # a small negative is no burst code: tested and shown signed (oracle, CR6)
    if kind.get("port_wrap"):
        # CR6 ports (PortSet/PortGet/CS616, round 9): the value is cast to a 32-bit integer (out of
        # range -> -2147483648, which is what the message shows) and any v >= 201 with v mod 200
        # in 0..12 is accepted besides C1..C16/U1..U12 (400, 1000, 1001, 3012 compile; 200, 213,
        # 413, 1100 do not).
        v = int(v) if -(2**31) <= v < 2**31 else -(2**31)
        in_range = _in_range(v, kind) or (v >= 201 and v % 200 <= 12)
    else:
        in_range = _in_range(v, kind)
    odd_ok = (not kind.get("odd_only")) or (in_range and int(v) % 2 == 1)
    return in_range, odd_ok, int(v)


CR6_BRIDGES = frozenset({"BRHALF", "BRHALF3W", "BRHALF4W", "BRFULL", "BRFULL6W"})
_I2C = frozenset({"I2COPEN", "I2CREAD", "I2CWRITE"})
_SERIAL_BUS = _I2C | {"SPIOPEN", "SPIREAD", "SPIWRITE"}


def _bus_port(arg, env, profile, names):
    if isinstance(arg, S.Name) and arg.name.upper() == "INF" and "INF" not in env.consts:
        return 2**31 - 1
    value = longconv.long_value(arg, env, profile, *names)
    if value is not None:
        return value
    try:
        folded = consteval.value_of(arg, env)
    except consteval.NotConstant:
        return None
    return consteval.to_long(folded) if consteval.is_finite_number(folded) else None


def _check_bus_port(stmt, kind, value, diags) -> None:
    if value is None:
        return
    in_range, odd_ok, shown = _evaluate(value, kind)
    if not in_range:
        diags.error(stmt.line, kind["message"].format(v=shown), stmt.file)
        if not kind.get("secondary_when_invalid"):
            return
    if not odd_ok:
        diags.error(stmt.line, kind["odd_message"], stmt.file)
    elif "allowed" in kind and (not in_range or shown not in kind["allowed"]):
        diags.error(stmt.line, kind["allowed_message"], stmt.file)


def check_i2c_open_order(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    kinds = profile.terminals.get("channels", {})
    kind = kinds.get("i2c_port" if profile.name == "cr6" else "i2c_port_cr1000x")
    if kind is None:
        return
    opened: set[int] = set()
    names = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() not in _I2C or not stmt.args:
            continue
        keyword = stmt.keyword.upper()
        if len(stmt.args) != (2 if keyword == "I2COPEN" else 5):
            continue
        if names is None:
            names = longconv.float_names(program.statements), longconv.literal_names(program.statements)
        port = _bus_port(stmt.args[0], env, profile, names)
        if port is None:
            if keyword == "I2COPEN":
                return
            continue
        in_range, odd_ok, _ = _evaluate(port, kind)
        if not in_range and profile.name == "cr6":
            port = 101  # the CR6 converter uses its first slot on invalid input
        elif not odd_ok:
            continue
        if profile.name == "cr6" and port >= 201:
            port -= 96  # U1..U12 alias C5..C16
        if keyword == "I2COPEN":
            opened.add(port)
        elif port not in opened:
            name = "I2CRead" if keyword == "I2CREAD" else "I2CWrite"
            diags.error(stmt.line, f"I2COpen must precede {name}.", stmt.file)


def check(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    terminals = profile.terminals
    checked_instructions: dict = terminals.get("instructions", {})
    channels: dict = terminals.get("channels", {})
    if not checked_instructions:
        return
    from .instr_data import _with_inline

    bus_names = None
    for stmt in _with_inline(program.statements):  # a one-line If's statement too (round 58)
        if not isinstance(stmt, S.Instruction):
            continue
        positions = checked_instructions.get(stmt.keyword.upper())
        if not positions:
            continue
        if (
            stmt.keyword.upper() == "ACPOWER"
            and profile.name == "cr6"
            and _check_acpower(stmt, channels[positions[0][1]], env, diags)
        ):
            continue
        if profile.single_pass and stmt.keyword.upper() in CR6_BRIDGES:
            _check_bridge_excitation(stmt, profile, env, diags, positions[0][0])
        for pos, kind_name, *extra in positions:
            # optional third and fourth items: the Reps position and the channels per repetition;
            # the channel span is then checked (rounds 30-31, docs/checker.md#measurement-
            # destination-and-channel-spans-round-31)
            reps = _const_int(stmt, extra[0], env, truncate=True) if extra else None
            width = extra[1] if len(extra) > 1 else 1
            kind = channels[kind_name]
            if pos == 1 and stmt.keyword.upper() in _SERIAL_BUS and stmt.args:
                if bus_names is None:
                    bus_names = longconv.float_names(program.statements), longconv.literal_names(program.statements)
                _check_bus_port(stmt, kind, _bus_port(stmt.args[0], env, profile, bus_names), diags)
                continue
            if reps is not None and profile.name == "cr6" and stmt.keyword.upper() in CR6_BRIDGES:
                reps = min(reps, 1)  # start channel only: `instr_measure._check_bridge_span` owns the span
            pulse = stmt.keyword.upper() == "PULSECOUNT" and profile.name in ("cr1000x", "cr300", "cr350")
            ports = _pulse_config(stmt, profile, kind, env, diags, reps) if pulse else ()
            if reps is not None and "span" in kind:
                _check_arg(stmt, pos, kind, env, diags, reps=reps, width=width)
            else:  # CR300/CR350: the last channel used is tested and shown
                _check_arg(stmt, pos, kind, env, diags, max(reps, 1) * width - 1 if reps is not None else 0)
            for message in ports:
                diags.error(stmt.line, message, stmt.file)


def _const_int(stmt: S.Instruction, pos: int, env: consteval.Env, truncate: bool = False) -> int | None:
    """An integral constant argument; `truncate` takes the integer part of any finite one."""
    if pos > len(stmt.args) or stmt.args[pos - 1] is None or not consteval.is_constant(stmt.args[pos - 1], env):
        return None
    try:
        value = consteval.value_of(stmt.args[pos - 1], env)
    except consteval.NotConstant:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return int(value) if truncate or value == int(value) else None


def _check_bridge_excitation(
    stmt: S.Instruction, profile: Profile, env: consteval.Env, diags: Diagnostics, channel_pos: int
) -> None:
    """CR300/CR350 bridges advance the excitation channel once per group of measurements."""
    channel = _const_int(stmt, channel_pos, env, truncate=True)
    reps = _const_int(stmt, 2, env, truncate=True)
    excitation = _const_int(stmt, channel_pos + 1, env, truncate=True)
    measurements = _const_int(stmt, channel_pos + 2, env, truncate=True)
    if (
        channel is None
        or channel < 1
        or reps is None
        or excitation is None
        or measurements is None
        or reps < 1
        or measurements < 1
    ):
        return
    first, last = (int(profile.symbols[name]["value"]) for name in ("VX1", "VX2"))
    if profile.name == "cr350" and excitation in (1, 2):
        excitation += first - 1
    if first <= excitation <= last and excitation + (reps - 1) // measurements > last:
        diags.error(stmt.line, "ExChan exceeds number of excitation channels.", stmt.file)


def _pulse_config(
    stmt: S.Instruction, profile: Profile, kind: dict, env: consteval.Env, diags: Diagnostics, reps: int | None
) -> tuple[str, ...]:
    channel = _const_int(stmt, 3, env, truncate=True)
    config = _const_int(stmt, 4, env, truncate=True)
    if channel is None or config is None or reps is None or reps < 1 or not _in_range(channel, kind):
        return ()
    if profile.name == "cr1000x":
        if (channel in (301, 302) and config == 2) or (101 <= channel <= 108 and config == 5):
            diags.error(stmt.line, "Selected PulseCount config option is not compatible with channel type.", stmt.file)
        return ()
    config &= 0xFF
    switch_port = 8 if profile.name == "cr300" else 7
    if config > 2 or (profile.name == "cr350" and channel == 8 and config == 2):
        diags.error(stmt.line, "Invalid PulseCount PConfig option.", stmt.file)
        return ()
    if any(
        d.line == stmt.line
        and d.file == stmt.file
        and d.message.startswith("Variable ")
        and d.message.endswith(" out of bounds.")
        for d in diags.errors
    ):
        return ()
    if config == 1 and channel == switch_port:
        return ("P_SW is not a valid low level AC input port.",)
    if config == 1 and channel <= 6:
        suffix = "low level AC input port."
    elif config == 2 and profile.name == "cr300" and 3 <= channel <= 6:
        suffix = "pulse count port."
    else:
        return ()
    end = 2 if channel <= 2 else 6
    count = reps if channel + reps - 1 <= end else 1
    ports = (f"C{port}" if port <= 2 else f"SE{port - 2}" for port in range(channel, channel + count))
    return tuple(f"{port} is not a valid {suffix}" for port in ports)


def _check_acpower(stmt: S.Instruction, kind: dict, env: consteval.Env, diags: Diagnostics) -> bool:
    """CR6 `ACPower` with a bad `ChanV` (4th) and/or `ChanI` (7th) and one or two `RepsI` (10th)
    (oracle, 2026-10-10, `tests/fixtures/acpower_channels.json`): a bad voltage channel `v` prints
    `v, v, -v, v`; per rep `k` (one or two) a bad voltage channel then adds `v` (from the second
    rep) and a bad current channel `i` adds `-(i+k)`. True when handled."""
    v, i, reps = (_const_int(stmt, pos, env) for pos in (4, 7, 10))
    if v is None or i is None or reps not in (1, 2) or v < 0:
        return False
    v_bad, i_bad = not _in_range(v, kind), not _in_range(i, kind)
    shown = [v, v, -v, v] if v_bad else []
    for k in range(reps):
        if k and v_bad:
            shown.append(v)
        if i_bad:
            shown.append(-(i + k))
    for value in shown:
        diags.error(stmt.line, kind["message"].format(v=value), stmt.file)
    return True


def _check_arg(
    stmt: S.Instruction,
    pos: int,
    kind: dict,
    env: consteval.Env,
    diags: Diagnostics,
    span: int = 0,
    reps: int | None = None,
    width: int = 1,
) -> None:
    idx = pos - 1
    if idx >= len(stmt.args) or stmt.args[idx] is None:
        return
    arg = stmt.args[idx]
    huge = False
    if kind.get("huge_int_min") or "overflow_add" in kind:
        # CR1000X (oracle, 2026-10-25, round 35): a constant outside int32 becomes INT_MIN; a
        # single-ended channel then reports it, a differential one compiles, as does one whose
        # `|channel| + overflow_add` passes INT_MAX (from 2147483641, any Reps)
        try:
            folded = consteval.value_of(arg, env) if consteval.is_constant(arg, env) else None
        except consteval.NotConstant:
            folded = None
        if isinstance(folded, (int, float)) and not isinstance(folded, bool) and math.isfinite(folded):
            huge = not -(2**31) < int(folded) < 2**31  # (INT_MIN itself too: `abs` keeps it)
            if "overflow_add" in kind and (huge or abs(int(folded)) + kind["overflow_add"] > 2**31 - 1):
                return
    if (isinstance(arg, S.String) or huge) and kind.get("string_int_min" if not huge else "huge_int_min"):
        # a string literal (`PortSet("fuzz",1)`, `"102"`, `""`) folds to INT_MIN (oracle, all
        # four loggers, 2026-10-12)
        value = -2147483648
        in_range, odd_ok, _ = _evaluate(value, kind)
        shown = value  # (`abs` of INT_MIN is INT_MIN in C: CR1000X `VoltSe(X,1,mV1000,"abc",...)`)
        if not in_range:
            diags.error(stmt.line, kind["message"].format(v=shown), stmt.file)
            if kind.get("secondary_when_invalid") and (not odd_ok or "allowed" in kind):
                diags.error(
                    stmt.line,
                    (kind["odd_message"] if not odd_ok else kind["allowed_message"]).format(v=shown),
                    stmt.file,
                )
        return
    non_numeric_int_min = kind.get("string_or_nan_int_min")
    try:
        value = consteval.value_of(arg, env)
    except consteval.NotConstant:
        # The compiler recognizes NAN even when it is absent from the profile's symbol table.
        if not (non_numeric_int_min and isinstance(arg, S.Name) and arg.name.upper() == "NAN"):
            return  # the constant-expression check owns variable arguments
        value = longconv.INT_MIN
    if non_numeric_int_min and (isinstance(value, str) or (isinstance(value, float) and math.isnan(value))):
        value = longconv.INT_MIN
    if not consteval.is_finite_number(value):
        return
    if kind.get("truncate") and math.isfinite(value):
        value = int(value)  # (CR1000X CurrentSE `402.5` is RG2, PulseCount `108.5` C8, round 31)
    if (
        env.profile.single_pass
        and pos == 4
        and stmt.keyword.upper() in {"VOLTDIFF", "VOLTSE", "TCDIFF"}
        and -1 < value < 1
    ):
        diags.items[:] = [
            d
            for d in diags.items
            if (d.file, d.line) != (stmt.file, stmt.line) or d.message != "Invalid reps 0, must be > 0."
        ]
        diags.error(stmt.line, "Invalid channel number.", stmt.file)
        return
    if kind.get("skip_nonpositive") and value <= 0:
        # CR300/CR350 (docs/loggers.md): a channel <= 0 is accepted far outside any range this
        # checker would derive with confidence (oracle: DiffChan -4 compiles with a 1..3 range,
        # -100 fails as "Channel 99"; -1 also compiles for an SEChan that never takes negative
        # values per its own CHM doc). Left unchecked rather than guessed, per AGENTS.md's "never
        # falsely reject" priority -- every corpus mismatch this rule fixes is a plain positive
        # channel number written where a different range or terminal name was required.
        return
    if _cr6_burst_kind(kind) and value <= -200 and not -212 <= value <= -201:
        return  # CR6 -200 and -213 and below behave irregularly (accepted, odd-start or port messages)
    spec = kind.get("span") if reps is not None else None
    top = max(hi for _, hi in _ranges(kind))
    if spec is not None and not spec.get("start_upper", True) and value > top:
        # CR6: a start channel above U12 is reported as the span (`250`: `Channel 50`, round 31)
        if kind.get("odd_only") and int(value) % 2 != 1:
            diags.error(stmt.line, kind["odd_message"].format(v=int(value)), stmt.file)
        else:
            diags.error(stmt.line, spec["message"].format(v=int(value) + spec.get("shift", 0)), stmt.file)
        return
    in_range, odd_ok, shown = _evaluate(value + span, kind)
    allowed_ok = "allowed" not in kind or (in_range and shown in kind["allowed"])
    if not in_range:
        diags.error(stmt.line, kind["message"].format(v=shown), stmt.file)
        if not kind.get("secondary_when_invalid"):
            return
    if not odd_ok:
        diags.error(stmt.line, kind["odd_message"].format(v=shown), stmt.file)
    elif not allowed_ok:
        diags.error(stmt.line, kind["allowed_message"].format(v=shown), stmt.file)
    elif spec is not None and reps is not None and reps > 1 and (value > 0 or not in_range):
        # the last repetition's start channel must stay in the start's own range; the message
        # shows the first channel past it (`spec`: own `ranges`, `step`, display `shift`). CR300/
        # CR350 PulseCount spans an invalid start from `invalid_start` (round 31)
        start = value if in_range else spec["invalid_start"]
        last = start + (reps - 1) * width * spec.get("step", 1)
        end = next(hi for lo, hi in _ranges(kind) if lo <= start <= hi)
        if last > end or not _in_range(last, spec if "ranges" in spec else kind):
            diags.error(stmt.line, spec["message"].format(v=int(end) + 1 + spec.get("shift", 0)), stmt.file)
