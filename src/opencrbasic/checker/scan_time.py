"""Measurement-time budget of a `Scan` (CR6; delays, ports and pulse counts also CR1000X).

Every instruction adds a number of 100 microsecond units to the open `Scan` when it is compiled;
`NextScan` compares the sum with the interval (`docs/instructions.md`, "Scan time budget").
`tools/trace_times.py` records the real additions; the models here reproduce them (CR6, 2026-10-10,
`tests/fixtures/scan_time.json`).

Units of 100 us throughout. Instructions the model does not know add an unknown extra, so the
total of a scan holding one is a lower bound (`partial`).
"""

import math
import re
import struct
from dataclasses import dataclass, field
from typing import cast

from .. import consteval
from .. import syntax as S
from ..profiles import Profile

_US_PER_UNIT = {0: 1, 1: 1_000, 2: 1_000_000, 3: 60_000_000, 4: 3_600_000_000, 5: 86_400_000_000}
UNIT_FACTOR = {1: 10, 2: 10_000, 3: 600_000, 4: 36_000_000, 5: 864_000_000}   # 100 us per unit
UNIT_NAMES = {0: "uSec", 1: "mSec", 2: "Sec", 3: "Min", 4: "Hr", 5: "Day"}
SCAN_UNITS = (1, 2, 3)


def delay_us(value: float, unit: int) -> int:
    """The unsigned 32-bit microsecond count of a constant `Delay(0,...)` interval: `value * unit`
    modulo 2^32; outside the Long range the interval is a Double whose low word is read as a
    float32, and a non-finite one counts as 2^31 (oracle, CR6/CR1000X, round 57,
    docs/checker.md#task-memory-overflow-of-long-delays-round-57)."""
    if not -2**31 <= value < 2**31:
        value = struct.unpack("<f", struct.pack("<d", float(value))[:4])[0]
        if not math.isfinite(value):
            return 2**31
    return int(value * _US_PER_UNIT[unit]) % 2**32


def excite_delay_us(value: float) -> int:
    """`ExciteV`'s `Delay`: the Long conversion as unsigned 32 bits (same section)."""
    return consteval.to_long(value) % 2**32


def task_memory_overflow(us: int) -> bool:
    """`FUN_00522b50` splits a delay into 43000 us loops with a signed 16-bit count; a negative
    count unrolls it past the 5119 task words (same section)."""
    return bool((us // 43000) & 0x8000)


def amount(excess: int, unit: int) -> int:
    """`FUN_0046c5c0`: an excess in 100 us units as a count of `unit`, rounded up (uSec: x100)."""
    return excess * 100 if unit == 0 else -(-excess // UNIT_FACTOR[unit])
_OPENERS = frozenset({"SELECT", "WHILE"})
_CLOSERS = frozenset({"ENDIF", "ENDSELECT", "WEND"})
# measurement ranges (FUN_00514690): code -> (gain index, open-detect "C" variant)
_RANGES = {800: (0, False), 802: (1, False), 804: (2, False), 810: (3, False),
           820: (0, True), 822: (1, True), 824: (2, True), 830: (3, True)}


def _f32(x: float) -> float:
    try:
        return struct.unpack("f", struct.pack("f", x))[0]
    except OverflowError:   # (a C float cast overflows to infinity: fN1 `1E300`, round 31)
        return math.copysign(math.inf, x)


def i32(x: int) -> int:
    """C `int` arithmetic: wrapped to 32 bits."""
    return (x + 2**31) % 2**32 - 2**31


def _cint(x: float) -> int:
    """x87 float -> int: truncated; out of range gives -2147483648."""
    return int(x) if -2**31 <= x < 2**31 else -2**31


def _cdiv(a: int, b: int) -> int:
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


@dataclass(frozen=True)
class Model:
    """The constants of one compiler's time accounting (from its measurement code generator)."""
    overhead: int      # first analog measurement of a scan
    refund: int        # taken back when less than 50 ms of slack remains
    integ_num: float   # integration code = int(integ_num / fN1 + 0.5)
    integ_zero: int    # ... for fN1 = 0
    default_settle: int    # documentation only
    round_up: int      # added before the final division by 100
    busy_extra: int    # added to the longest integration time (units) for the calibration gap

    def integ_code(self, fnotch: float) -> int | None:
        if _f32(fnotch) == 0:   # (`1E-50` is a float zero, round 31)
            return self.integ_zero
        if not math.isfinite(fnotch) or fnotch <= 0:
            return None
        code = int(_f32(_f32(self.integ_num) / _f32(fnotch)) + 0.5)
        if code > 0xFFFF:
            return None
        return code or 1

    def integ_time(self, k: int) -> int:           # microseconds-ish, before settling
        raise NotImplementedError

    def settle_code(self, settling: int) -> int:
        raise NotImplementedError

    def settle_units(self, code: int) -> int:
        raise NotImplementedError

    def open_detect_code(self) -> int:
        raise NotImplementedError

    def fixed_code(self, constant: int) -> int:
        """Settling code of a constant the compiler itself passes (PanelTemp 900, Battery 200)."""
        raise NotImplementedError


class _CR6(Model):
    def integ_time(self, k):
        return (k << 5) // 3 + 1

    def settle_code(self, settling):
        return _cint(_f32(_f32(_f32(i32((settling or 500) + 810)) * 3.0) / 32.0) + 0.5)

    def settle_units(self, code):
        return i32(_cint(_f32(_f32(code * 32.0) / 3.0)) + 5)

    def open_detect_code(self):
        return 5

    def fixed_code(self, constant):
        return int(_f32(_f32(constant * 3.0) / 32.0) + 0.5)


class _CR1000X(Model):
    def integ_time(self, k):
        return k * 32 + 180

    def settle_code(self, settling):
        return _cint(_f32(_f32(settling or 450) / 32.0) + 0.5)

    def settle_units(self, code):
        return i32(_cint(_f32(code * 32.0)) + 5)

    def open_detect_code(self):
        return 2

    def fixed_code(self, constant):
        return int(_f32(constant / 32.0) + 0.5)


MODELS = {
    "cr6": _CR6(78, 77, 93750.0, 24, 500, 0x28, 13),
    "cr1000x": _CR1000X(16, 15, 31250.0, 8, 450, 0x50, 17),
}


def analog_units(m: Model, reps: int, gain: int, fnotch: float, settling: int, single_ended: bool,
                 meas_off: bool = False, reverse: bool = False, excite: bool = False,
                 open_detect: bool = False, groups: int = 1) -> int | None:
    """Time of one analog measurement call. `meas_off` (single-ended) adds one more measurement,
    `reverse` (differential) and `excite` double the total, autorange (gain 3) settles twice, the
    open-detect ranges settle longer and add 50 per rep. Any settling time counts as converted
    (not clamped by its range check), in 32-bit C arithmetic (round 31)."""
    k = m.integ_code(fnotch)
    if k is None or not 1 <= reps <= 100_000:
        return None
    integ = m.integ_time(k)
    settle = m.settle_units(m.settle_code(settling) + (m.open_detect_code() if open_detect else 0))
    total = i32((settle + integ) * reps)
    if gain == 3:
        total = i32(total + reps * settle)
    if excite:
        total = i32(total * 2)
    total = i32(total * groups)
    if meas_off and single_ended:
        total = i32(total + integ + settle)
    if reverse and not single_ended:
        total = i32(total * 2)
    if open_detect:
        total = i32(total + reps * 0x32)
    return _cdiv(i32(total + m.round_up), 100) + 1


def panel_units(m: Model, fnotch: float) -> int | None:
    """`PanelTemp(Dest, fN1)`: two measurements with a fixed settling argument (900)."""
    k = m.integ_code(fnotch)
    if k is None:
        return None
    return (m.settle_units(m.fixed_code(900)) + m.integ_time(k) + m.round_up) // 100 + 1


def battery_units(m: Model) -> int:
    """CR1000X `Battery`: one analog call with integration code 10 and a fixed settling of 200."""
    return (m.settle_units(m.fixed_code(200)) + m.integ_time(10) + m.round_up) // 100 + 1


@dataclass
class Budget:
    scan: S.Instruction
    end: S.Instruction
    value: int                    # Scan interval argument
    unit: int                     # its Units code
    interval: int                 # 100 us units
    msg_unit: int                 # the unit the messages count in (a SubScan can leave its own)
    total: int = 0                # after the analog adjustment
    analog: bool = False
    pulse_only: bool = True       # nothing but PulseCount adds time (such a scan never warns)
    max_integ: int = 0            # longest integration time (units) of an analog call
    partial: bool = False
    sub_short: list = field(default_factory=list)   # (NextSubScan statement, amount, unit)


def _value(expr: S.Expr | None, env: consteval.Env) -> object | None:
    if expr is None or not consteval.is_constant(expr, env):
        return None
    try:
        return consteval.value_of(expr, env)
    except consteval.NotConstant:
        return None


def _num(expr: S.Expr | None, env: consteval.Env) -> float | None:
    v = _value(expr, env)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) \
        else None


def _long(expr: S.Expr | None, env: consteval.Env) -> int | None:
    v = _value(expr, env)
    if isinstance(v, bool):
        return -1 if v else 0
    return consteval.to_long(v) if isinstance(v, (int, float)) else None


def _settling(expr: S.Expr | None, env: consteval.Env) -> int | None:
    """A settling time as the compiler converts it (a string is -2147483648, round 31; `INF`
    times as a float overflow does, `1E39`)."""
    if isinstance(expr, S.Name) and expr.name.upper() == "INF" and "INF" not in env.consts:
        return consteval.to_long(math.inf)
    v = _value(expr, env)
    return consteval.to_long(v) if isinstance(v, str) else _long(expr, env)


# keyword -> (reps position, range position, flag position, settle position, fnotch position,
#             single-ended)
_ANALOG = {
    "VOLTSE": (2, 3, 5, 6, 7, True), "VOLTDIFF": (2, 3, 5, 6, 7, False),
    "TCSE": (2, 3, 7, 8, 9, True), "TCDIFF": (2, 3, 7, 8, 9, False),
    "CURRENTSE": (2, 3, 5, 6, 7, True),
}
# bridges (round 31): keyword -> (ranges, channel, MeasPEx, RevEx, RevDiff, settle, fnotch positions,
#                                  single-ended, measurement groups)
_BRIDGE = {
    "BRHALF": ((3,), 4, 6, 8, None, 9, 10, True, 1),
    "BRHALF3W": ((3,), 4, 6, 8, None, 9, 10, True, 2),
    "BRFULL": ((3,), 4, 6, 8, 9, 10, 11, False, 1),
    "BRHALF4W": ((3, 4), 5, 7, 9, 10, 11, 12, False, 2),
    "BRFULL6W": ((3, 4), 5, 7, 9, 10, 11, 12, False, 2),
}
_PLAIN = ((0, False), (1, False), (2, False))   # fixed ranges without open detect


def _bridge_units(m: Model, args: list, env: consteval.Env, spec: tuple
                  ) -> tuple[int, bool, int] | None:
    """One analog call per MeasPEx group of reps (one call when MeasPEx equals Reps); each doubles
    per RevEx, RevDiff (differential) and second measurement group. Autorange and open-detect
    ranges of the two-group bridges, burst channels and Reps above 100000 are not modelled
    (round 31, traced CR6, docs/checker.md "Bridge channel spans and settling-time timing")."""
    ranges, cp, mp, xp, dp, sp, ip, single, groups = spec
    if len(args) < ip:
        return None
    reps, chan, meas, revex = (_long(args[i - 1], env) for i in (2, cp, mp, xp))
    revdiff = _long(args[dp - 1], env) if dp else 0
    rngs = [_long(args[i - 1], env) for i in ranges]
    settling, fn = _settling(args[sp - 1], env), _num(args[ip - 1], env)
    if reps is None or chan is None or meas is None or revex is None or revdiff is None or settling is None or fn is None:
        return None
    if None in rngs:
        return None
    rngs = cast(list[int], rngs)
    if chan <= 0 or \
            not 1 <= reps <= 100_000 or any(r not in _RANGES for r in rngs) or \
            (groups > 1 and any(_RANGES[r] not in _PLAIN for r in rngs)):
        return None
    meas = max(meas, 1)
    chunks = [reps] if meas == reps else [meas] * (reps // meas) + ([reps % meas] if reps % meas else [])
    total = 0
    for n in chunks:
        units = analog_units(m, n, _RANGES[rngs[0]][0], fn, settling, single,
                             excite=(revex & 0xFF) != 0, reverse=(revdiff & 0xFF) != 0,
                             open_detect=_RANGES[rngs[0]][1], groups=groups)
        if units is None:
            return None
        total = i32(total + units)
    code = m.integ_code(fn)
    if code is None:
        return None
    return total, True, m.integ_time(code) // 100


def instruction_units(stmt: S.Instruction, env: consteval.Env, profile: Profile
                      ) -> tuple[int, bool, int] | None:
    """`(units, analog, integration time in units)` the instruction adds, or None when not modelled / not
    readable."""
    kw = stmt.keyword.upper()
    args = stmt.args
    if kw == "DELAY":
        if len(args) != 3:
            return None
        option, value, unit = (_num(a, env) for a in args)
        if option is None or int(option) != 0 or value is None or unit is None \
                or int(unit) not in _US_PER_UNIT:
            return None
        if 0 <= value < 1:
            return None
        micro = delay_us(value, int(unit))
        if micro == 0 or task_memory_overflow(micro):
            return None
        if micro >= 2**31:   # rounded up instead (an empirical fit, round 57; mechanism not derived)
            return -(-micro // 100), False, 0
        return (2 * (micro // 2) + 100) // 100, False, 0
    if kw == "EXCITEV":
        delay = _long(args[2], env) if len(args) == 3 else None
        if delay is None:
            return None
        delay = excite_delay_us(delay)
        if task_memory_overflow(max(delay - 10, 0)):
            return None
        return ((delay + 100) % 2**32) // 100, False, 0
    if kw == "PORTSET":
        return 1, False, 0
    if kw in ("SW12", "PULSECOUNT"):
        return 1, False, 0
    m = MODELS.get(profile.name)
    if m is None:
        return None
    if kw == "PORTGET" or (kw == "BATTERY" and profile.name == "cr6"):
        return 0, False, 0
    if kw == "BATTERY":
        return battery_units(m), True, 0     # only the floor (oracle, CR1000X Scan(3,mSec))
    if kw == "PANELTEMP" and len(args) == 2:
        fn = _num(args[1], env)
        units = panel_units(m, fn) if fn is not None else None
        return (2 * units, True, m.integ_time(m.integ_code(fn)) // 100) if units and fn is not None else None
    if kw in _ANALOG:
        rp, gp, fp, sp, ip, single = _ANALOG[kw]
        if len(args) < ip:
            return None
        reps, rng, flag = (_long(args[i - 1], env) for i in (rp, gp, fp))
        settling = _settling(args[sp - 1], env)
        fn = _num(args[ip - 1], env)
        if (reps is None or rng is None or flag is None or settling is None or fn is None) or rng not in _RANGES:
            return None
        gain, open_detect = _RANGES[rng]
        cr6_current = kw == "CURRENTSE" and profile.name == "cr6"   # MeasOff and "C" ignored
        on = (flag & 0xFF) != 0 and not cr6_current
        open_detect = open_detect and not cr6_current
        flags = dict(meas_off=on and single, reverse=on and not single, open_detect=open_detect)
        units = analog_units(m, reps, gain, fn, settling, single, **flags)
        return (units, True, m.integ_time(m.integ_code(fn)) // 100) if units is not None else None
    if kw in _BRIDGE:
        return _bridge_units(m, args, env, _BRIDGE[kw])
    if kw in ("THERM107", "THERM108", "THERM109") and len(args) >= 6:
        reps, settling = _long(args[1], env), _long(args[4], env)
        fn = _num(args[5], env)
        if (reps is None or settling is None or fn is None) or not 1 <= reps <= 255 or not 0 <= settling < 6_000_000:
            return None
        units = analog_units(m, reps, 0, fn, settling, True, excite=True)
        return (units, True, m.integ_time(m.integ_code(fn)) // 100) if units else None
    if kw == "CS616" and len(args) >= 2:
        reps = _long(args[1], env)
        return (reps * 5 + 2, False, 0) if reps is not None and 1 <= reps <= 255 else None
    return None


def budgets(program: S.Program, env: consteval.Env, profile: Profile, include_zero: bool = False):
    """One `Budget` per main-sequence `Scan` with a constant mSec/Sec/Min interval, in order."""
    from .instr_rules import _scan_raw
    model = MODELS.get(profile.name) or MODELS["cr6"]
    slow = False
    cur: Budget | None = None
    frames: list[tuple[int, int, int, int | None, int] | None] = []   # open SubScans: (start, count, interval, unit, saved unit)
    total = 0
    for stmt in program.statements:
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        text = stmt.text or ""
        if kw == "NEXT" and re.match(r"(?i)\s*Next\s+Scan\b", text):
            kw = "NEXTSCAN"
        elif kw == "NEXT" and re.match(r"(?i)\s*Next\s+SubScan\b", text):
            kw = "NEXTSUBSCAN"
        if kw == "SLOWSEQUENCE":
            slow = True
        elif isinstance(stmt, S.Instruction) and (kw == "SCAN" and not slow):
            raw = _scan_raw(stmt, env)
            if include_zero and len(stmt.args) > 1:
                v, u = _value(stmt.args[0], env), _value(stmt.args[1], env)
                if u in (2, 3) and not isinstance(u, bool) and isinstance(v, (int, float)) \
                        and not isinstance(v, bool) and (0 <= v < 1 or v > 2147483647):
                    raw = (0, u)
            if raw and raw[1] in SCAN_UNITS:
                value, unit = raw
                cur = Budget(stmt, stmt, value, unit, value * _US_PER_UNIT[unit] // 100, unit)
            else:
                cur = None
            frames, total = [], 0
        elif cur is None:
            continue
        elif isinstance(stmt, S.Instruction) and (kw == "NEXTSCAN"):
            if frames:
                cur.partial = True
            cur.end = stmt
            cur.total = total
            if cur.analog:
                cur.total = i32(cur.total + model.overhead)
                if i32(cur.interval - cur.total) < 500:
                    cur.total = i32(cur.total - model.refund)
            yield cur
            cur = None
        elif isinstance(stmt, S.Instruction) and (kw == "SUBSCAN"):
            args = [_value(a, env) for a in stmt.args]
            count = args[2] if len(args) == 3 else None
            ok = isinstance(count, int) and not isinstance(count, bool) and count > 0
            step = 0
            if ok and args[0] != 0:
                u = _US_PER_UNIT.get(args[1]) if isinstance(args[1], int) else None
                if u is None or not consteval.is_finite_number(args[0]) or args[0] <= 0:
                    ok = False
                else:
                    step = int(args[0]) * u // 100
            if not ok:
                cur.partial = True
            sub_unit = args[1] if ok and isinstance(args[1], int) and not isinstance(args[1], bool) \
                and args[1] in UNIT_NAMES else None
            if ok and sub_unit is None:
                cur.partial = True
            frames.append((total, cast(int, count), step, sub_unit, cur.msg_unit) if ok else None)
            if sub_unit is not None:
                cur.msg_unit = sub_unit
        elif kw == "NEXTSUBSCAN":
            frame = frames.pop() if frames else None
            if frame is None:
                continue
            start, count, step, _sub_unit, saved = frame
            body = total - start
            restore = True
            if count < 2 or step == 0 or body == 0:
                total += (count - 1) * body
            elif step < body:
                cur.sub_short.append((stmt, amount(body - step, cur.msg_unit), cur.msg_unit))
                total += (count - 1) * body
            elif step - body > 1:
                total += (count - 1) * step
                restore = False      # the real compiler returns before restoring the unit
            else:
                total += (count - 1) * body
            if restore:
                cur.msg_unit = saved
        elif isinstance(stmt, S.Instruction):
            got = instruction_units(stmt, env, profile)
            if got is None:
                if kw in _KNOWN_TIMED:
                    cur.partial = True
                    cur.analog = cur.analog or kw in _ANALOG_KINDS
            else:
                total = i32(total + got[0])
                if kw != "PULSECOUNT":
                    cur.pulse_only = False
                cur.analog = cur.analog or got[1]
                cur.max_integ = max(cur.max_integ, got[2])


# of those, the ones that take the analog-measurement overhead
_ANALOG_KINDS = frozenset({
    "VOLTSE", "VOLTDIFF", "TCSE", "TCDIFF", "PANELTEMP", "THERM107", "THERM108", "THERM109",
    "BRHALF", "BRHALF3W", "BRHALF4W", "BRFULL", "BRFULL6W", "RESISTANCE", "CURRENTSE",
})
# instructions that add time but are not modelled (a scan holding one has a lower-bound total)
_KNOWN_TIMED = frozenset({
    "VOLTSE", "VOLTDIFF", "TCSE", "TCDIFF", "PANELTEMP", "THERM107", "THERM108", "THERM109",
    "CS616", "DELAY", "BRHALF", "BRHALF3W", "BRHALF4W", "BRFULL", "BRFULL6W", "RESISTANCE",
    "CURRENTSE", "PERIODAVG", "AM25T", "THERMISTOR", "VIBRATINGWIRE", "EXCITEV", "EXCITEI",
    "CALIBRATE", "PANELTEMP2", "RESISTANCE3W", "RESISTANCE2W", "TCPSYC", "MUXSELECT",
})
