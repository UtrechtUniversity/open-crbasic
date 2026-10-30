"""Instruction-specific measurement/I-O semantic checks that are not the channel/port *terminal*
range model (`terminals.py`, `data/<logger>/terminals.json`). Value-set and value-range checks for
`Range` codes, `SW12` ports, `Delay`, `CS616`, `TimerInput` and `ExciteV`/`ExciteI`, per logger,
driven by `data/<logger>/measure.json`. Pass 2 (`docs/checker.md` "Two-pass compilation"): called
from `checker/__init__.py` alongside `instructions.check`, inside the `if not diags.errors:` gate,
so (matching every existing pass-2 check) it never fires when the file has an earlier, unrelated
error. All rules below are oracle-confirmed per logger this round; see `docs/instructions.md`
"Measurement/I-O instruction checks" for the evidence and each logger's coverage.
"""

import math
from typing import TypeGuard, cast

import re

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from ..profiles import Profile
from . import longconv, scan_time, symbols


def check(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    measure = profile.measure
    if not measure:
        return
    file_reported = False  # only the first bad `FileManage`/`RunProgram` file name is reported
    sequential: bool | None = None
    from .instr_data import _with_inline
    # a one-line If's statement too (oracle, CR6/CR1000X, round 58, docs/checker.md#one-line-if-statements-in-tier-c-round-58)
    slow = False
    for stmt in _with_inline(program.statements):
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        slow = kw == "SLOWSEQUENCE" or slow and kw != "ENDPROG"
        if kw == "SW12" and "sw12_ports" in measure:
            _check_sw12(stmt, measure, env, diags)
        if kw == "DELAY" and "delay" in measure:
            if sequential is None:
                sequential = is_sequential(program, profile, env)
            _check_delay(stmt, measure["delay"], env, diags, sequential, slow)
        if kw == "EXCITEV" and measure.get("delay", {}).get("task_memory") and len(stmt.args) == 3:
            delay = _int_value(stmt.args[2], env)
            if delay is not None and scan_time.task_memory_overflow(
                    max(scan_time.excite_delay_us(delay) - 10, 0)):
                _task_memory_full(stmt, diags)
        if kw == "CS616" and "cs616_min_reps_pos" in measure:
            _check_cs616_reps(stmt, measure, env, diags)
        if kw in ("EXCITEV", "EXCITEI") and "excite_max_mv" in measure:
            _check_excite_magnitude(stmt, measure, env, diags)
        if kw == "GPS" and "gps" in measure:
            _check_structure_members(stmt, program, measure["gps"], env, diags)
            _check_gps_nmea(stmt, program, env, diags)
            _check_gps_port(stmt, measure["gps"], env, diags)
        if kw == "CH400STATUS" and "CH400STATUS" in profile.instructions:
            _check_ch400status(stmt, env, diags)
        if kw == "FFT" and "FFT" in profile.instructions:
            _check_fft_source(stmt, symbols.declared_all_dims(symbols.scoped_view(program, stmt), env),
                              env, diags)
        if kw == "SDI12RECORDER" and profile.single_pass:
            _check_sdi12_structure_dest(stmt, program, diags)
        if kw == "TABLEFILE" and "tablefile" in measure:
            _check_tablefile(stmt, measure["tablefile"], env, diags)
        for rule in measure.get("string_prefixes", ()):
            if kw == rule["instr"] and not (file_reported and "ext" in rule):
                file_reported = _check_string_prefix(stmt, rule, env, diags) or file_reported


def check_const_ranges(flat: list, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    """Data-driven constant-parameter checks (`measure.json` `const_ranges`, `docs/instructions.md`
    "Constant-parameter range checks"): the parameter at `pos` converts as C's `(long)` and must
    lie in `min..max` or in `values`, else `message` (`{value}` is the converted number, or that
    of the parameter at `show`). `wrap16` rules (`StaticRoute` addresses) keep the low 16 bits of
    the converted number. A variable or unresolved name is another check's error."""
    by_instr: dict[str, list[dict]] = {}
    for rule in profile.measure.get("const_ranges", ()):
        by_instr.setdefault(rule["instr"], []).append(rule)
    if not by_instr:
        return
    from .instr_rules import cdm_null_module, cdm_unsupported_module
    names = longconv.float_names(flat), longconv.literal_names(flat)
    for stmt in flat:
        if not isinstance(stmt, S.Instruction):
            continue
        if stmt.keyword.upper().startswith("CDM_") and (cdm_null_module(stmt, env)
                                                         or cdm_unsupported_module(stmt, env)):
            continue
        tccomp = bool(stmt.args) and stmt.keyword.upper() == "CDM_TCCOMP" and \
            _int_value(stmt.args[0], env) == 224
        for rule in by_instr.get(stmt.keyword.upper(), ()):
            if tccomp and rule["pos"] == 5:
                _tccomp_before_channel(stmt, env, profile, names, diags)
            _apply_const_rule(stmt, rule, env, profile, names, diags)
            if tccomp and rule["pos"] == 5:
                _tccomp_after_channel(stmt, env, profile, names, diags)


def _tccomp_before_channel(stmt, env, profile, names, diags) -> None:
    """`CDM_TCComp(TEMP120, ...)`: a negative channel first gives `Burst measurements unsupported,
    input channel must be > 0!` (oracle, CR6/CR1000X, 2026-10-10; docs/instructions.md "CPI
    measurement time")."""
    channel = _long_arg(stmt, 5, {}, env, profile, names)
    if channel is not None and channel < 0:
        diags.error(stmt.line, "Burst measurements unsupported, input channel must be > 0!", stmt.file)


def _tccomp_after_channel(stmt, env, profile, names, diags) -> None:
    """After the channel message: `Reps cycle beyond last channel!` when `channel - 1 + Reps > 20`
    (a channel outside 1..20 counts as 1), then `Thermocouple type unsupported.` for a type
    outside 0..7."""
    channel, reps = (_long_arg(stmt, pos, {}, env, profile, names) for pos in (5, 4))
    if channel is not None and reps is not None:
        if not 1 <= channel <= 20:
            channel = 1
        if channel - 1 + reps > 20:
            diags.error(stmt.line, "Reps cycle beyond last channel!", stmt.file)
    tc_type = _long_arg(stmt, 6, {}, env, profile, names)
    if tc_type is not None and not 0 <= tc_type <= 7:
        diags.error(stmt.line, "Thermocouple type unsupported.", stmt.file)


def _apply_cdm_channel(stmt, rule, env, profile, names, diags) -> None:
    """CDM analog measurement channel (`channel_by_module`: module value -> last channel; oracle,
    CR6/CR1000X, 2026-10-12, docs/instructions.md "CDM measurement channels"). A negative channel
    is a two-measurement pair counted by magnitude; `-2147483648` (a string, overflow) is only
    the range line; `Reps` that run past the last channel are `Reps cycle input beyond last
    channel!` unless `Reps` is itself out of range."""
    last = rule["channel_by_module"].get(str(_int_value(stmt.args[0], env)))
    value = _long_arg(stmt, rule["pos"], rule, env, profile, names)
    if last is None or value is None:
        return
    int_min = -2 ** 31
    magnitude = -value if int_min < value < 0 else value
    role = rule["role"]
    if role == "ic":
        if value == int_min or magnitude < 1 or magnitude > last:
            diags.error(stmt.line, rule["message"].format(value=magnitude if value != int_min else value,
                                                        max=last), stmt.file)
    elif role == "rc":
        reps = _long_arg(stmt, 4, rule, env, profile, names) if len(stmt.args) >= 4 else None
        if value > last or (1 <= value and reps is not None and 1 <= reps <= 65536
                            and value - 1 + reps > last):
            diags.error(stmt.line, rule["message"], stmt.file)
    elif role == "tm" and int_min < value < 0 and magnitude > last:
        diags.error(stmt.line, rule["message"], stmt.file)


def _apply_const_rule(stmt, rule, env, profile, names, diags) -> None:
    if "channel_by_module" in rule:
        _apply_cdm_channel(stmt, rule, env, profile, names, diags)
        return
    if "skip_modules" in rule and stmt.args and str(_int_value(stmt.args[0], env)) in rule["skip_modules"]:
        return
    raw = _raw_arg(stmt, rule["pos"], env)
    if raw is None or (rule.get("skip_strings") and isinstance(raw, str)):
        return
    if "invalid_raw" in rule:
        if _matches_invalid_raw(raw, rule["invalid_raw"]):
            diags.error(stmt.line, rule["message"].format(value=consteval.to_long(raw)), stmt.file)
        return
    value = _long_arg(stmt, rule["pos"], rule, env, profile, names)
    if value is None:
        return
    if "lower_gate" in rule and value < rule["lower_gate"]:
        return
    if value in rule.get("allow", ()):
        return
    if value in rule.get("warn_values", ()):
        diags.warning(stmt.line, rule["warn_message"], stmt.file)
        return
    if "by_module" in rule:
        # a per-device port set (oracle 2026-10-12); a module outside the table is left alone
        allowed = rule["by_module"].get(str(_int_value(stmt.args[0], env)), rule.get("values"))
        if allowed is None or value in allowed:
            return
    elif value in rule["values"] if "values" in rule else rule["min"] <= value <= rule["max"]:
        return
    shown = _long_arg(stmt, rule.get("show", rule["pos"]), rule, env, profile, names)
    if shown is not None:
        diags.error(stmt.line, rule["message"].format(value=shown), stmt.file)


def _matches_invalid_raw(value: object, invalid: dict) -> bool:
    if invalid.get("strings") and isinstance(value, str):
        return True
    if not isinstance(value, (int, float)):
        return False
    if invalid.get("nan") and value != value:
        return True
    if value == float("inf") or value == float("-inf"):
        return False
    if invalid.get("outside_long") and (value < -2147483648 or value > 2147483647):
        return True
    return value in invalid.get("values", ())


def _long_arg(stmt: S.Instruction, pos: int, rule: dict, env: consteval.Env, profile: Profile,
              names) -> int | None:
    value = _raw_arg(stmt, pos, env)
    if value is None:
        return None
    if rule.get("string_zero") and isinstance(value, str):
        return 0
    if rule.get("inf_max") and isinstance(value, (int, float)) and value >= 2**128 - 2**103:
        return 2147483647   # (a Settling time that is a float infinity, `1E39`/`INF`, round 31)
    if not rule.get("wrap16"):
        # The profile-aware conversion (`checker/longconv.py`) where it covers the expression;
        # `to_long` for the forms it does not (function calls, `TRUE`).
        converted = longconv.long_value(stmt.args[pos - 1], env, profile, *names)
        return consteval.to_long(value) if converted is None else converted
    # A string or NaN is 0; a number outside Long range is 0 on CR6/CR1000X and unobserved
    # (`overflow: null`, skipped) on CR300/CR350; infinity is 65535 (not modelled).
    if isinstance(value, str) or value != value:
        return 0
    if abs(value) == float("inf"):
        return None
    if abs(value) >= 2 ** 31:
        return rule["overflow"]
    return int(value) & 0xFFFF


def _raw_arg(stmt: S.Instruction, pos: int, env: consteval.Env):
    return _fnotch_value(stmt.args[pos - 1], env) if pos <= len(stmt.args) and \
        stmt.args[pos - 1] is not None else None


def _check_string_prefix(stmt: S.Instruction, rule: dict, env: consteval.Env,
                         diags: Diagnostics) -> bool:
    """A constant file name's drive (`prefixes`) and, for `FileManage`/`RunProgram` (`ext`), its
    extension: with option bits 2 or 4 set the text after the last `.` must start with `crb` or
    the logger's own program extension, else `FileManage name <name> invalid extension.`
    (docs/instructions.md#filemanage-drive-and-dnp-comport-constants). True when it reported."""
    idx = rule["pos"] - 1
    if idx >= len(stmt.args) or stmt.args[idx] is None:
        return False
    value = _const_value(stmt.args[idx], env)
    if not isinstance(value, str):
        return False
    drive = value[:4]
    if drive.upper() not in (f"{p}:" for p in rule["prefixes"]):
        diags.error(stmt.line, rule["message"].format(drive=drive), stmt.file)
        return True
    option = _int_value(stmt.args[1], env) if "ext" in rule and len(stmt.args) > 1 else None
    if option is not None and abs(option) < 2 ** 31 and option & 6:
        ext = value.strip().rpartition(".")[2].upper() if "." in value else ""
        if not ext.startswith(("CRB", rule["ext"])):
            diags.error(stmt.line, f"FileManage name {value} invalid extension.", stmt.file)
            return True
    return False


def _check_ch400status(stmt: S.Instruction, env: consteval.Env, diags: Diagnostics) -> None:
    """`CH400Status(Interval, Units)`: the converted interval times the unit, in microseconds,
    must be a whole number of seconds; docs/instructions.md "CH400Status interval and FFT source length"."""
    if len(stmt.args) < 2:
        return
    value, unit = _int_value(stmt.args[0], env), _const_value(stmt.args[1], env)
    if value is None or not isinstance(unit, (int, float)) or int(unit) not in _DELAY_US_PER_UNIT:
        return
    if value * _DELAY_US_PER_UNIT[int(unit)] % 1_000_000:
        diags.error(stmt.line, "CH400Status update interval must be an integer multiple of 1 second.",
                    stmt.file)


def _check_fft_source(stmt: S.Instruction, dims: dict, env: consteval.Env, diags: Diagnostics) -> None:
    """`FFT(Source, Res, N, ...)`: the source from the given element on must hold `N` values
    (`N` a power of two), else `<name> must be of length >= N.`"""
    if len(stmt.args) < 3 or not isinstance(stmt.args[0], (S.Name, S.Index)):
        return
    n = _int_value(stmt.args[2], env)
    shape = dims.get(stmt.args[0].name.upper())
    if n is None or n < 1 or n & (n - 1) or shape is None:
        return
    offset = _flat_offset(stmt.args[0], shape)
    if offset is not None and math.prod(shape) - offset < n:
        diags.error(stmt.line, f"{stmt.args[0].name} must be of length >= {n}.", stmt.file)


def _check_sdi12_structure_dest(stmt: S.Instruction, program: S.Program, diags: Diagnostics) -> None:
    """CR300/CR350: a StructureType variable as the `SDI12Recorder` destination gives `Could not
    find DataTable <argument text>.`; docs/instructions.md#sdi12recorder-structure-destination."""
    dest = stmt.args[0] if stmt.args else None
    if not isinstance(dest, (S.Name, S.Index)) or (isinstance(dest, S.Index) and None in dest.args):
        return
    type_name = symbols.declared_variable_types(program).get(dest.name.upper())
    if type_name is not None and any(
            isinstance(st, S.Instruction) and st.keyword.upper() == "STRUCTURETYPE" and st.args and
            isinstance(st.args[0], S.Name) and st.args[0].name.upper() == type_name
            for st in program.statements):
        from .expressions import _arg_text
        diags.error(stmt.line, f"Could not find DataTable {_arg_text(stmt, 0)}.", stmt.file)


def _check_tablefile(stmt: S.Instruction, cfg: dict, env: consteval.Env,
                     diags: Diagnostics) -> None:
    """`TableFile` file name drive and file-type option; docs/instructions.md#tablefile-drive."""
    name = _name_text(stmt.args[0], env) if stmt.args else None
    if not name:
        return
    if name[:4].upper() not in (f"{d}:" for d in cfg["drives"]):
        diags.error(stmt.line, f"Invalid drive or no drive specified in filename {name}.", stmt.file)
    option = _int_value(stmt.args[1], env) if len(stmt.args) > 1 else None
    if option is not None and cfg.get("option_mod100"):
        low = abs(int(math.fmod(option, 100)))
        if option >= 0 and low >= 64 and name[:4].upper() in (f"{d}:" for d in cfg.get("tob3_drives", ())):
            diags.error(stmt.line, "TOB3 TableFile output only allowed on CRD: drive.", stmt.file)
        if 24 <= low <= 31 or low >= 40:
            diags.error(stmt.line, "Invalid file type option.", stmt.file)


def _name_text(arg: S.Expr | None, env: consteval.Env) -> str | None:
    """A constant file name; `Status.SerialNumber` folds to the empty string (oracle)."""
    if isinstance(arg, S.Member) and isinstance(arg.base, S.Name) and arg.args is None and \
            (arg.base.name.upper(), arg.member.upper()) == ("STATUS", "SERIALNUMBER"):
        return ""
    if isinstance(arg, S.Binary) and arg.op in ("+", "&"):
        left, right = _name_text(arg.left, env), _name_text(arg.right, env)
        return None if left is None or right is None else left + right
    value = _const_value(arg, env)
    return value if isinstance(value, str) else None


def _check_structure_members(stmt: S.Instruction, program: S.Program, cfg: dict,
                             env: consteval.Env, diags: Diagnostics) -> None:
    """Data-driven checks on members of a StructureType used as an instruction destination."""
    rules = cfg.get("structure_members", ())
    if not rules or not stmt.args:
        return
    dest = stmt.args[0]
    if not isinstance(dest, (S.Name, S.Index)):
        return
    type_name = symbols.declared_variable_types(program).get(dest.name.upper())
    if type_name is None:
        return
    members = _structure_members(program, type_name)
    for rule in rules:
        member = members.get(rule["member"])
        if member is None or (member.type or "FLOAT").upper() != rule["type"]:
            continue
        size = _int_value(member.string_size, env)
        if size is not None and size < rule["min_string_size"]:
            diags.error(stmt.line, rule["message"], stmt.file)


def _structure_members(program: S.Program, type_name: str) -> dict[str, S.VarDecl]:
    members: dict[str, S.VarDecl] = {}
    active = False
    for candidate in program.statements:
        if isinstance(candidate, S.Instruction):
            kw = candidate.keyword.upper()
            if kw == "STRUCTURETYPE":
                active = bool(candidate.args and isinstance(candidate.args[0], S.Name) and
                              candidate.args[0].name.upper() == type_name)
                continue
            if kw == "ENDSTRUCTURETYPE" and active:
                break
        if active and isinstance(candidate, S.Declaration) and candidate.kind == "STRUCTUREMEMBER":
            members.update({var.name.upper(): var for var in candidate.vars})
    return members


def check_pass1_values(program: S.Program, profile: Profile, diags: Diagnostics,
                       env: consteval.Env) -> None:
    """CR6/CR1000X pass 1 value checks on const-family positions: `Range` code, then `fN1`
    (position order within one statement). Oracle: both are reported next to an earlier pass-1
    error (`Dim Z banana`, `Delay(0,5,Foo)`), unlike the tier-C checks of `check`
    (`docs/instructions.md` "fN1 / fnotch"). A statement that already has an error on its line
    (unresolved name, variable in a constant position) is abandoned before these run."""
    from . import expressions, scan_time
    measure = profile.measure
    fnotch = profile.fnotch
    ranges = measure.get("range_code_instructions", {}) if measure else {}
    fn_pos = fnotch.get("instructions", {}) if fnotch and "min_hz" in fnotch else {}
    if not ranges and not fn_pos:
        return
    variable_names = frozenset(name for name in symbols.declared_variable_names(program)
                               if not symbols.is_reserved_predefined_name(name, profile))
    earlier = {(d.line, d.file) for d in diags.items if d.severity == "error"}
    flat = []
    for st in program.statements:
        flat.append(st)
        if isinstance(st, S.If):
            flat.extend((st.inline or []) + (st.inline_else or []))
    for stmt in flat:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw not in ranges and kw not in fn_pos:
            continue
        if (stmt.line, stmt.file) in earlier:
            continue
        if any(expressions._find_arity_problem(a, profile) or
               expressions._find_bare_function_ref(a, profile, variable_names)
               for a in stmt.args if a is not None):
            continue
        if kw in ranges:
            # a bridge checks its one Range twice, `BrHalf4W`/`BrFull6W` each of their two
            # (oracle, CR6/CR1000X, round 59)
            for pos in ranges[kw] if isinstance(ranges[kw], list) else [ranges[kw]]:
                _check_range_code(stmt, measure, pos, env, diags)
        if kw in fn_pos:
            _check_fnotch_min(stmt, fnotch, fn_pos[kw], env, diags,
                              scan_time.MODELS[profile.name].integ_num)


_INF_NAMES = {"INF": float("inf"), "NAN": float("nan")}


def _fnotch_value(arg: S.Expr | None, env: consteval.Env):
    """Folded `fN1` value; `NAN`/`INF` (not named constants in `consteval`) handled here."""
    inner, sign = arg, 1.0
    while isinstance(inner, S.Unary) and inner.op == "-":
        inner, sign = inner.operand, -sign
    if isinstance(inner, S.Name) and inner.name.upper() in _INF_NAMES and \
            inner.name.upper() not in env.consts:
        return sign * _INF_NAMES[inner.name.upper()]
    return _const_value(arg, env)


def _check_fnotch_min(stmt: S.Instruction, cfg: dict, pos: int, env: consteval.Env,
                      diags: Diagnostics, integ_num: float) -> None:
    """`Invalid fnotch, minimum allowed is N Hz.` (CR6 2, CR1000X .5; docs/instructions.md). Zero
    (also as a float, `1E-50`) and +-INF pass, NAN, strings and values under the logger's minimum
    fail; there is no maximum. A negative value fails when its integration code
    `int(integ_num / fN1 + 0.5)` is negative, so a large one passes (CR6 `-1E5`, round 31)."""
    from .scan_time import _f32
    idx = pos - 1
    if idx >= len(stmt.args) or stmt.args[idx] is None:
        return
    value = _fnotch_value(stmt.args[idx], env)
    if value is None:
        return
    if isinstance(value, str):
        bad = True
    else:
        value = float(value)
        f = _f32(value)
        bad = f != f or (f != 0 and abs(f) != float("inf") and (
            value < cfg["min_hz"] if f > 0 else _f32(_f32(integ_num) / f) <= -1.5))
    if bad:
        times = 2 if stmt.keyword.upper() in cfg.get("twice", ()) else 1
        for _ in range(times):
            diags.error(stmt.line, cfg["message"], stmt.file)


def _const_value(arg: S.Expr | None, env: consteval.Env):
    """The folded constant value of `arg`, or `None` if it is missing, a variable, or otherwise
    not resolvable at compile time (in every case, some *other* check's concern, not this one's --
    matching `terminals.py`'s own convention)."""
    if arg is None or not consteval.is_constant(arg, env):
        return None
    try:
        return consteval.value_of(arg, env)
    except consteval.NotConstant:
        return None


def _int_value(arg: S.Expr | None, env: consteval.Env) -> int | None:
    v = _const_value(arg, env)
    return int(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def _check_gps_nmea(stmt: S.Instruction, program: S.Program, env: consteval.Env,
                    diags: Diagnostics) -> None:
    """`GPS`'s optional fifth argument must be a String variable of at least 96 characters
    (declared size; 24 when none is given); docs/instructions.md#gps-ports."""
    if len(stmt.args) < 5 or stmt.args[4] is None:
        return
    arg = stmt.args[4]
    if isinstance(arg, S.String):
        bad = True
    elif isinstance(arg, (S.Name, S.Index)):
        decl = next((v for st in reversed(symbols.scoped_view(program, stmt).statements) if isinstance(st, S.Declaration)
                     and st.kind in ("PUBLIC", "DIM", "STATIC") for v in st.vars
                     if v.name.upper() == arg.name.upper()), None)
        if decl is None:
            return
        if (decl.type or "FLOAT").upper() != "STRING":
            bad = (decl.type or "FLOAT").upper() in ("FLOAT", "LONG", "BOOLEAN", "DOUBLE")
        else:
            size_expr = decl.string_size
            if isinstance(size_expr, S.Binary) and size_expr.op == "=":
                size_expr = size_expr.left  # `As String * 16 = {...}` parses the initializer in
            size = _int_value(size_expr, env) if size_expr is not None else 24
            bad = size is not None and size < 96
    else:
        return
    if bad:
        diags.error(stmt.line, "Optional parameter must be an array of type string of length >= 100",
                    stmt.file)


def _check_gps_port(stmt: S.Instruction, cfg: dict, env: consteval.Env,
                    diags: Diagnostics) -> None:
    """GPS port sign selects PPS or NMEA synchronization; docs/instructions.md#gps-ports."""
    if len(stmt.args) < 2:
        return
    port = _int_value(stmt.args[1], env)
    if port is None:
        return
    if abs(port) not in cfg["ports"]:
        diags.error(stmt.line, cfg["port_message"], stmt.file)
    elif port > 0 and port != cfg["pps_port"]:
        diags.error(stmt.line, cfg["pps_message"], stmt.file)


# --- Range codes: `Invalid range code, valid options are: mV5000, mV1000, mV200, or AutoRange.`
# (CR6/CR1000X only; CR300/CR350 have no such check -- their own `mv5000`/`mv200` etc. are simply
# not symbols at all, an unrelated, already-handled undefined-symbol rejection, `docs/
# instructions.md`). Fires for `VoltSe`/`VoltDiff`/`TCSe`/`TCDiff`/`CurrentSe`'s own `Range`
# argument (position 3 on all five) when the folded value is not one of the CHM's documented
# "mV5000/mV1000/mV200/AutoRange" family, including their open-detection `...C` siblings.
def _check_range_code(stmt: S.Instruction, measure: dict, pos: int, env: consteval.Env, diags: Diagnostics) -> None:
    idx = pos - 1
    if idx >= len(stmt.args):
        return
    value = _const_value(stmt.args[idx], env)
    if value is None:
        return
    valid = measure["range_codes"]
    # any string is invalid, `"800"` too (oracle, CR6/CR1000X, round 58)
    if isinstance(value, str) or int(value) not in valid:
        diags.error(stmt.line, measure["range_code_message"], stmt.file)


# --- `SW12`: `Invalid SW12 port %d.` -- an exact, small, per-logger set of valid ports (both a
# plain-integer slot number and the named `SW12_n` constant resolve to different raw values but
# both compile; every other value in the probed neighbourhood fails), `docs/instructions.md`.
def _check_sw12(stmt: S.Instruction, measure: dict, env: consteval.Env, diags: Diagnostics) -> None:
    if not stmt.args:
        return
    value = _int_value(stmt.args[0], env)
    if isinstance(stmt.args[0], S.String) and measure.get("string_port_int_min"):
        value = -2147483648   # (`SW12("x",0)`, oracle CR6/CR350 2026-10-12: a string folds to INT_MIN)
    if value is None:
        return
    if value not in measure["sw12_ports"]:
        text = measure.get("sw12_port_messages", {}).get(str(value), f"Invalid SW12 port {value}.")
        diags.error(stmt.line, text, stmt.file)


# --- `Delay`: `Delay(Option, Interval, Units)`. Three checks, oracle-confirmed per logger
# (`docs/instructions.md`):
#   - `Illegal option %d` (all four loggers) when `Option` is not 0, 1 or 2 -- shown value is the
#     folded number truncated to an unsigned byte (oracle: `Option=-1` shows as `255`).
#   - `Delay interval cannot be of type String.` (all four loggers, any `Option`) when `Interval`
#     folds to a string constant.
#   - Depending on `Option` (0 = measurement-task-sequence delay, 1 = processing delay, 2 = SDM
#     delay): `Option=0` needs `Interval` to itself be a compile-time constant
#     (`Measurement delay must have constant interval.`) and, once resolved, at most 60 minutes
#     (`Measurement Delay Exceeds 60 min.`) -- both CR6/CR1000X only, oracle-confirmed absent on
#     CR300/CR350 (`Delay(0,61,min)` compiles clean there); `Option=1` needs the resolved interval
#     to be strictly less than 24 hours (`Max Delay is 24 Hours`, all four loggers); `Option=2`
#     needs `Interval` to be a compile-time constant (`SDM Delay interval must be constant.`, all
#     four loggers).
_DELAY_US_PER_UNIT = {0: 1, 1: 1_000, 2: 1_000_000, 3: 60_000_000, 4: 3_600_000_000, 5: 86_400_000_000}


def _delay_option_byte(value: int) -> int:
    """`Option` is taken as an unsigned byte (256 acts as 0, 257 as 1, -254 as 2). A constant
    outside the Long range is a float32: its low byte is 0, or 255 above the float32 maximum
    (oracle, all four loggers; docs/instructions.md#delay). Not modelled: CR300/CR350 report
    255 for exactly 2147483648."""
    if value > 3.4028235e38:
        return 255
    if not -2**31 <= value < 2**31:
        return 0
    return value % 256


# `Program too big for task memory` repeats for every further task word until the 4096-byte
# message buffer is full (`compiler.py`); 100 copies always fill it
_TASK_MEMORY_COPIES = 100


def _task_memory_full(stmt: S.Instruction, diags: Diagnostics) -> None:
    for _ in range(_TASK_MEMORY_COPIES):
        diags.error(stmt.line, "Program too big for task memory", stmt.file)


def _check_delay(stmt: S.Instruction, cfg: dict, env: consteval.Env, diags: Diagnostics,
                 sequential: bool = False, slow: bool = False) -> None:
    args = stmt.args
    option_arg = args[0] if len(args) > 0 else None
    interval_arg = args[1] if len(args) > 1 else None
    units_arg = args[2] if len(args) > 2 else None

    option = _int_value(option_arg, env)
    if option is not None:
        option = _delay_option_byte(option)
        if option not in (0, 1, 2):
            diags.error(stmt.line, f"Illegal option {option}", stmt.file)

    interval_value = _const_value(interval_arg, env)
    if isinstance(interval_value, str):
        diags.error(stmt.line, "Delay interval cannot be of type String.", stmt.file)
        return  # oracle: the string-type rejection is the only diagnostic for a string interval.

    if option == 0 and cfg.get("option0_limit"):
        if sequential:
            # SequentialMode checks neither the interval nor its expansion (oracle, CR6, round 57)
            return
        if interval_arg is not None and not consteval.is_constant(interval_arg, env):
            diags.error(stmt.line, "Measurement delay must have constant interval.", stmt.file)
            return
        unit = _const_value(units_arg, env)
        if not isinstance(interval_value, (int, float)) or isinstance(interval_value, bool) \
                or not math.isfinite(interval_value) or not isinstance(unit, int) or isinstance(unit, bool) \
                or unit not in _DELAY_US_PER_UNIT:
            return
        # the unsigned 32-bit microsecond count, so a negative interval is too long as well
        # (oracle, CR6/CR1000X, rounds 35 and 57; docs/checker.md#task-memory-overflow-of-long-delays-round-57)
        us = scan_time.delay_us(interval_value, unit)
        if us > cfg["option0_limit"]:
            diags.error(stmt.line, "Measurement Delay Exceeds 60 min.", stmt.file)
        if cfg.get("task_memory") and scan_time.task_memory_overflow(us):
            _task_memory_full(stmt, diags)
    elif option == 1 and cfg.get("option1_limit"):
        us = _delay_interval_us(interval_arg, units_arg, env)
        if us is not None and us >= cfg["option1_limit"]:
            diags.error(stmt.line, "Max Delay is 24 Hours", stmt.file)
    elif option == 2 and cfg.get("option2_constant", True):
        if interval_arg is not None and not consteval.is_constant(interval_arg, env):
            diags.error(stmt.line, "SDM Delay interval must be constant.", stmt.file)
        elif interval_value == 0 and cfg.get("option0_limit"):   # (CR6/CR1000X data only)
            # `Delay(2,0,Sec)` (oracle, CR6/CR1000X, 2026-10-25, round 35; 1 and -1 compile)
            diags.error(stmt.line, "Invalid time specified.", stmt.file)
        elif slow and cfg.get("sdm_slow_sequence"):
            # written after `SlowSequence` in the program body, also in SequentialMode and in a
            # one-line If; a Sub it calls is not checked (oracle, CR6/CR1000X, round 64,
            # docs/checker.md#sdm-delay-in-a-slow-sequence-round-64)
            diags.error(stmt.line, "SDM Delay Not Allowed in Slow Sequence.", stmt.file)


def _delay_interval_us(interval_arg, units_arg, env: consteval.Env) -> float | None:
    value = _const_value(interval_arg, env)
    unit = _const_value(units_arg, env)
    if not isinstance(value, (int, float)) or not isinstance(unit, (int, float)) or value < 0:
        return None
    if value >= 2**31:   # outside the Long range: no limit applies (oracle, CR6/CR1000X, round 16)
        return None
    us_per_unit = _DELAY_US_PER_UNIT.get(int(unit))
    return value * us_per_unit if us_per_unit is not None else None


# --- `CS616`: `Invalid reps %d, must be > 0` for a non-positive `Reps` (CR6 only, oracle-
# confirmed; not chased on CR1000X/CR300/CR350 this round, `docs/instructions.md`).
def _check_cs616_reps(stmt: S.Instruction, measure: dict, env: consteval.Env, diags: Diagnostics) -> None:
    pos = measure["cs616_min_reps_pos"]
    idx = pos - 1
    if idx >= len(stmt.args):
        return
    value = _int_value(stmt.args[idx], env)
    if value is not None and value <= 0:
        diags.error(stmt.line, f"Invalid reps {value}, must be > 0", stmt.file)


# --- `ExciteV`/`ExciteI`: `Excitation exceeds 2500.000000.` when `|ExmV|`/`|ExmI|` > 2500 (CR6
# only, oracle-confirmed; CR1000X accepts at least 2501 mV with no error, so this is deliberately
# not applied there -- `docs/instructions.md`). `ExciteI` does not exist on CR1000X/CR300/CR350 at
# all (`docs/loggers.md`), so this only ever matches on CR6 regardless.
def _check_excite_magnitude(stmt: S.Instruction, measure: dict, env: consteval.Env, diags: Diagnostics) -> None:
    pos = measure["excite_magnitude_pos"]
    idx = pos - 1
    if idx >= len(stmt.args):
        return
    value = _const_value(stmt.args[idx], env)
    if isinstance(value, (int, float)) and abs(value) > measure["excite_max_mv"]:
        diags.error(stmt.line, f"Excitation exceeds {float(measure['excite_max_mv']):.6f}.", stmt.file)


_UNIT_NAMES = {1: "mSec", 2: "Sec", 3: "Min"}
_OPENERS = frozenset({"SELECT", "WHILE"})
_CLOSERS = frozenset({"ENDIF", "ENDSELECT", "NEXTSUBSCAN", "WEND"})


def is_sequential(program: S.Program, profile: Profile, env: consteval.Env) -> bool:
    """`mode.decide` == "Sequential"; a failure is left for the compile's own `mode.decide` call
    to record (here it reads as Pipeline)."""
    from .. import mode
    try:
        return mode.decide(program.statements, profile, lambda e: consteval.is_constant(e, env),
                           lambda e: consteval.value_of(e, env)) == "Sequential"
    except Exception:
        return False


def check_scan_delay_budget(program: S.Program, profile: Profile, diags: Diagnostics,
                            env: consteval.Env, zero: bool = False) -> None:
    """CR6/CR1000X: the measurement time of a main `Scan` (`checker/scan_time.py`, 100 us units)
    above its interval -> `Scan interval too short for measurement by %d %s.` at `NextScan`, the
    excess rounded up to the scan's own unit (`Scan(50,mSec)` + `Delay(0,1,Sec)` -> `by 951 mSec`;
    `Scan(1,Sec)` + `Delay(0,1,Sec)` -> `by 1 Sec`; a `SubScan(0,0,n)` body counts n times).
    A `SubScan(Interval,...)` body longer than its interval gives `SubScan interval too short for
    measurement by %d %s.` at its `NextSubScan`. A Sec/Min `Scan` whose interval is 0 or past
    `INT32_MAX` counts as interval 0. The zero-interval scans (`zero=True`) are reported even
    though the scan's own parameter error already failed the compile, so the caller runs them
    ahead of the error-gated tier C. SequentialMode never reports it. docs/instructions.md "Scan
    time budget"."""
    if profile.name not in ("cr6", "cr1000x"):
        return
    from . import scan_time
    # SequentialMode never reports it (oracle, CR6/CR1000X, 2026-10-08; docs/fuzz-findings.md).
    if is_sequential(program, profile, env):
        return
    for b in scan_time.budgets(program, env, profile, include_zero=zero):
        if (b.interval == 0) != zero:
            continue
        for stmt, amount, unit in b.sub_short:
            diags.error(stmt.line, f"SubScan interval too short for measurement by {amount} "
                        f"{scan_time.UNIT_NAMES[unit]}.", stmt.file)
        if b.total % 2**32 > b.interval:   # unsigned: a negative total also fails (round 31)
            amount = scan_time.amount(scan_time.i32(b.total - b.interval), b.msg_unit)
            diags.error(b.end.line, f"Scan interval too short for measurement by {amount} "
                        f"{scan_time.UNIT_NAMES[b.msg_unit]}.", b.end.file)


def check_cpi_time(program: S.Program, profile: Profile, diags: Diagnostics,
                   env: consteval.Env) -> None:
    """CR6/CR1000X: a `Scan` shorter than the CPI measurement time of a module -> `Insufficient
    time (%d ms) for measurements from CPI address %d (needs at least %d ms).`, on the program's
    last line, only when the compile has no other error (docs/instructions.md "CPI measurement
    time"). The binary walks the module list in order of first use; for each module it totals the
    module's time in every scan holding CDM instructions, keeps per scan the (first) module with
    the largest total so far, and then reports every such scan whose interval is below that
    figure, so a program with k modules can print up to k lines per scan, naming the leader so far.
    Modelled: `CDM_TCComp(TEMP120, ...)`, `Reps*430+275` microseconds rounded up to 100 us per
    statement, summed per address and rounded up to whole ms. Every occurrence counts once wherever
    it sits (loops, `If` branches, `Select` cases in SequentialMode). A `SubScan(Interval,Units,n)`
    body counts n times at `Interval` spacing (zero: its own length); an interval shorter than the
    body gives `SubScan interval too short for measurements from CPI address %d by %d uSec.` first.
    Programs with any other CDM instruction, an unreadable instruction or a SubScan form not
    probed are skipped whole."""
    if profile.name not in ("cr6", "cr1000x") or not program.statements:
        return
    from .instr_rules import _US_PER_UNIT, _scan_raw
    scans: list[dict] = []      # {"ms": interval, "times": {addr: 100 us units}, "short": [(addr, by)]}
    order: list[int] = []       # CPI addresses in order of first use
    scan: dict | None = None
    sub: tuple[int, int] | None = None   # open SubScan: (count, interval in 100 us units)
    sub_start: dict[int, int] = {}
    sequential = is_sequential(program, profile, env)
    depth = 0                            # open If/For/Do/Select blocks
    items: list[tuple[S.Stmt, bool]] = []
    for stmt in program.statements:
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        text = stmt.text or ""
        if isinstance(stmt, S.For) or (isinstance(stmt, S.DoLoop) and stmt.keyword == "DO") or \
                (isinstance(stmt, S.If) and stmt.inline is None) or kw in _OPENERS:
            items.append((stmt, depth > 0))
            depth += 1
            continue
        if (kw == "NEXT" and not re.match(r"(?i)\s*Next\s+(Sub)?Scan\b", text)) or \
                (isinstance(stmt, S.DoLoop) and stmt.keyword == "LOOP") or kw in _CLOSERS - {"NEXTSUBSCAN"} \
                or (kw == "END" and re.match(r"(?i)\s*End\s+(If|Select)\b", text)):
            depth = max(depth - 1, 0)
        items.append((stmt, depth > 0))
        if isinstance(stmt, S.If):
            items += [(child, True) for child in (stmt.inline or []) + (stmt.inline_else or [])]
    for stmt, conditional in items:
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        text = stmt.text or ""
        if kw == "NEXT" and re.match(r"(?i)\s*Next\s+Scan\b", text):
            kw = "NEXTSCAN"
        elif kw == "NEXT" and re.match(r"(?i)\s*Next\s+SubScan\b", text):
            kw = "NEXTSUBSCAN"
        if kw.startswith("CDM_") and kw != "CDM_TCCOMP" or kw == "CPIADDMODULE2":
            return
        if isinstance(stmt, S.Instruction) and (kw == "SCAN"):
            raw = _scan_raw(stmt, env)
            if raw is None:
                return
            scan = {"ms": raw[0] * _US_PER_UNIT[raw[1]] // 1000, "times": {}, "short": []}
            scans.append(scan)
        elif scan is None:
            continue
        elif kw == "NEXTSCAN":
            if sub is not None:
                return
            scan = None
        elif isinstance(stmt, S.Instruction) and (kw == "SUBSCAN"):
            args = [_value(a, env) for a in stmt.args]
            count = args[2] if len(args) == 3 else None
            step = 0
            if not isinstance(count, int) or isinstance(count, bool) or count < 1 or sub is not None:
                return
            if args[0] != 0:
                unit = _US_PER_UNIT.get(args[1]) if isinstance(args[1], int) else None
                if unit is None or not isinstance(args[0], (int, float)) or args[0] <= 0:
                    return
                step = int(args[0]) * unit // 100
                if sequential and step <= 10:   # "SequentialMode SubScan interval must be > 1 mS."
                    return
                if step * count > scan["ms"] * 10:   # "Product of SubScan interval and iterations ..."
                    return
            sub = (count, step)
            sub_start = dict(scan["times"])
        elif kw == "NEXTSUBSCAN":
            if sub is None:
                return
            count, step = sub
            for addr, units in list(scan["times"].items()):
                length = units - sub_start.get(addr, 0)
                if length > 0:
                    if step and length > step:
                        scan["short"].append((addr, (length - step) * 100))
                    scan["times"][addr] = units + (step or length) * (count - 1)
            sub = None
        elif isinstance(stmt, S.Instruction) and (kw == "CDM_TCCOMP"):
            module, addr, reps = (_int_value(stmt.args[i], env) if i < len(stmt.args) else None
                                  for i in (0, 1, 3))
            if module != 224 or addr is None or not 1 <= addr <= 120 or reps is None or reps < 1:
                return
            if conditional and not sequential:   # "not allowed inside of a conditional statement"
                return
            if addr not in order:
                order.append(addr)
            scan["times"][addr] = scan["times"].get(addr, 0) + -(-(reps * 430 + 275) // 100)
    last = program.statements[-1].line
    leader: dict[int, tuple[int, int]] = {}   # scan index -> (ms needed, address)
    for addr in order:
        for i, sc in enumerate(scans):
            if not sc["times"]:
                continue
            for a, by in sc["short"]:
                if a == addr:
                    diags.error(last, f"SubScan interval too short for measurements from CPI "
                                f"address {a} by {by} uSec.")
            need = -(-sc["times"].get(addr, 0) // 10)
            if need > leader.get(i, (0, 0))[0]:
                leader[i] = (need, addr)
        for i, sc in enumerate(scans):
            if i in leader and sc["ms"] < leader[i][0]:
                diags.error(last, f"Insufficient time ({sc['ms']} ms) for measurements from CPI "
                            f"address {leader[i][1]} (needs at least {leader[i][0]} ms).")


def _value(expr: S.Expr | None, env: consteval.Env) -> object | None:
    if expr is None or not consteval.is_constant(expr, env):
        return None
    try:
        return consteval.value_of(expr, env)
    except consteval.NotConstant:
        return None


# `Reps` (the second argument) of measurement instructions, oracle-probed 2026-10-01 (the decompiled
# CR6 check `FUN_005163b0` is shared by 23 instructions; only the ones probed on a logger are
# listed). CR6/CR1000X: `Invalid reps %d, must be > 0` (no period) for Reps <= 0, and `Variable %s
# out of bounds.` when the Float destination cannot hold the output count (also for negative
# output counts). Optional outputs adjust that count below. CR300/CR350: `Invalid reps 0, must
# be > 0.` for Reps 0 only.
# CR300/CR350 (round 31): all of these check the destination like VoltSe (round 30); Reps <= 0
# is `Invalid reps %d, must be > 0.`, except a negative Reps on Therm/PeriodAvg, which instead
# (with PulseCount) overruns the destination.
_SINGLE_PASS_REPS = frozenset({"VOLTSE", "VOLTDIFF", "TCSE", "TCDIFF", "BRHALF", "BRHALF3W",
                               "BRHALF4W", "BRFULL", "BRFULL6W", "THERM107", "THERM108", "THERM109",
                               "CURRENTSE", "PERIODAVG", "PULSECOUNT"})
_NEGATIVE_REPS_SILENT = frozenset({"THERM107", "THERM108", "THERM109", "PERIODAVG"})
_NEGATIVE_REPS_DEST = frozenset({"THERM107", "THERM108", "THERM109", "PERIODAVG", "PULSECOUNT"})
_REPS_CHECKED = {
    "cr6": frozenset({"VOLTSE", "VOLTDIFF", "TCDIFF", "TCSE", "BRHALF", "BRHALF3W", "BRHALF4W",
                      "BRFULL", "BRFULL6W", "RESISTANCE", "RESISTANCE2W", "RESISTANCE3W",
                      "PULSECOUNT", "CS616", "PERIODAVG", "CURRENTSE", "THERM107", "THERM108",
                      "THERM109"}),
    "cr1000x": frozenset({"VOLTSE", "VOLTDIFF", "TCDIFF", "TCSE", "CURRENTSE", "PERIODAVG",
                          "PULSECOUNT", "BRHALF", "BRHALF3W", "BRHALF4W", "BRFULL", "BRFULL6W",
                          "CS616", "THERM107", "THERM108", "THERM109"}),
    "cr300": _SINGLE_PASS_REPS,
    "cr350": _SINGLE_PASS_REPS,
}
_REPS_DEST_SIZE = frozenset({"VOLTSE", "VOLTDIFF", "TCDIFF", "TCSE", "CURRENTSE", "PERIODAVG",
                             "BRHALF", "BRHALF3W", "BRHALF4W", "BRFULL", "BRFULL6W", "RESISTANCE",
                             "RESISTANCE2W", "RESISTANCE3W", "PULSECOUNT", "CS616"})


def _measurement_output_count(stmt: S.Instruction, reps: int, env: consteval.Env) -> int:
    """Optional extra outputs, from the bridge/resistance builders and round-25 oracle probes.

    The option is an integer equal to 1, not a Boolean: True (-1) does not enable it.
    Bridges return one extra value per repetition; resistance returns one extra value overall.
    """
    kw = stmt.keyword.upper()
    option_index = {"BRHALF4W": 14, "BRFULL6W": 14, "RESISTANCE": 13,
                    "RESISTANCE2W": 10, "RESISTANCE3W": 10}.get(kw)
    if option_index is None or len(stmt.args) <= option_index:
        return reps
    if _int_value(stmt.args[option_index], env) != 1:
        return reps
    return reps * 2 if kw in {"BRHALF4W", "BRFULL6W"} else reps + 1



def _measurement_aliases(program: S.Program, tables, env: consteval.Env):
    """Alias destinations: `(backing elements, start offset, diagnostic name, declared size)`,
    the size None for a bare alias name (rounds 26 and 30).

    Bind at the alias declaration, so a later local shadow of its backing name does not
    change the storage.
    """
    aliases = {}
    routine = None
    for st in program.statements:
        if isinstance(st, S.SubDef):
            routine = st
        elif S.ends_routine(st):
            routine = None
        if not isinstance(st, S.Instruction) or st.keyword.upper() != "ALIAS" or not st.args:
            continue
        target = st.args[0]
        if not isinstance(target, (S.Name, S.Index)):
            continue
        scope = tables(st)
        shape = scope.dims.get(target.name.upper())
        param = next((p for p in routine.params if p.name.upper() == target.name.upper()), None) \
            if routine is not None else None
        if param is not None and (param.type or "FLOAT").upper() == "FLOAT":
            # a Float parameter's own header shape backs the alias (round 30 review, `Sub S(P(4))`)
            shape = tuple(_int_value(d, env) for d in param.dim_exprs) or (1,)
            if any(n is None or n < 1 for n in shape):
                continue
        elif shape is None or scope.types.get(target.name.upper()) != "FLOAT":
            continue
        # Alias declaration subscripts may be named constants.
        if isinstance(target, S.Index):
            indices = [_int_value(a, env) for a in target.args]
            if any(i is None for i in indices):
                continue
            indices = cast(list[int], indices)
            if len(indices) == 1:
                offset = indices[0] - 1
            elif len(indices) == len(shape) and all(1 <= i <= n for i, n in zip(indices, shape)):
                offset = 0
                for i, n in zip(indices, shape):
                    offset = offset * n + i - 1
            else:
                continue
        else:
            offset = 0
        total = math.prod(shape)
        declared_names = {v.name.upper(): v.name
                          for decl in symbols.scoped_view(program, st).statements
                          if isinstance(decl, S.Declaration) for v in decl.vars}
        entries = {}
        for alias in st.args[1:]:
            if not isinstance(alias, (S.Name, S.Index)):
                break
            size = 1 if isinstance(alias, S.Name) else (
                _int_value(alias.args[0], env) if len(alias.args) == 1 else None)
            if size is None or size < 1 or not 0 <= offset < total:
                break
            entries[alias.name.upper()] = (total, offset,
                                           declared_names.get(target.name.upper(), target.name)
                                           if offset == 0 else alias.name,
                                           None if isinstance(alias, S.Name) else size)
            offset += size
        aliases[id(st)] = entries
    cache = {}
    parameters = {id(tables(st)): {p.name.upper() for p in st.params}
                  for st in program.statements if isinstance(st, S.SubDef)}

    def lookup(stmt, dest):
        if dest.name.upper() in tables(stmt).dims or \
                dest.name.upper() in parameters.get(id(tables(stmt)), set()):
            return None
        key = id(tables(stmt))
        if key not in cache:
            visible = {}
            for st in symbols.scoped_view(program, stmt).statements:
                visible.update(aliases.get(id(st), {}))
            cache[key] = visible
        entry = cache[key].get(dest.name.upper())
        if entry is not None and isinstance(dest, S.Index) and entry[3] is None:
            return None  # a subscripted bare alias is not modelled
        return entry

    return lookup


def _alias_overrun(stmt: S.Instruction, dest, alias, count: int, profile: Profile,
                   env: consteval.Env) -> bool:
    """An alias destination's explicit index must lie within its declared size; the outputs
    must then fit the backing storage from the destination element (round 30). CR6/CR1000X
    count from the alias's own start, CR300/CR350 from the backing variable's start."""
    total, offset, _, size = alias
    index = 0
    if isinstance(dest, S.Index):
        k = _index_value(dest.args[0], env) if len(dest.args) == 1 else None
        if k is None:
            return False
        if not 1 <= k <= size:
            return True
        index = k - 1
    available = total - index - (0 if profile.single_pass else offset)
    return count < 0 or count > available


def check_reps(program: S.Program, profile: Profile, diags: Diagnostics, env: consteval.Env) -> None:
    checked = _REPS_CHECKED.get(profile.name, frozenset())
    if not checked:
        return
    from . import symbols
    tables = symbols.scope_tables(program, env, scalars=True, parameters=True)
    alias_storage = _measurement_aliases(program, tables, env)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() not in checked or \
                len(stmt.args) < 2:
            continue
        scope = tables(stmt)
        reps = _int_value(stmt.args[1], env)
        if reps is None and isinstance(_const_value(stmt.args[1], env), str):
            # `Therm107(T,"fuzz",...)` is 0 on CR300; CR6/CR1000X take any string as INT_MIN
            # (`PulseCount(R(1),"2",C1,...)`, oracle, 2026-10-25, round 35)
            reps = 0 if profile.single_pass else -2147483648
        if reps is None:
            continue
        if profile.single_pass:
            # before this line's other diagnostics, e.g. its fnotch warning (oracle, CR300, fuzz
            # `84bd80cc420c`); destination bounds: rounds 30-31
            kw = stmt.keyword.upper()
            messages: list[str | None] = []
            if reps == 0 or (reps < 0 and kw not in _NEGATIVE_REPS_SILENT):
                messages.append(f"Invalid reps {reps}, must be > 0.")
            if reps > 0 or (reps < 0 and kw in _NEGATIVE_REPS_DEST):
                messages.append(_dest_overrun(stmt, stmt.args[0], reps, profile, env, alias_storage,
                                              scope))
            at = next((i for i, d in enumerate(diags.items)
                       if (d.line, d.file) == (stmt.line, stmt.file)), len(diags.items))
            diags.items[at:at] = [Diagnostic("error", stmt.line, m, stmt.file) for m in messages if m]
            continue
        if reps <= 0 and stmt.keyword.upper() != "CS616":  # (CS616: `_check_cs616_reps`)
            diags.error(stmt.line, f"Invalid reps {reps}, must be > 0", stmt.file)
        dest = stmt.args[0]
        if reps == 0 or not isinstance(dest, (S.Name, S.Index)):
            continue
        if stmt.keyword.upper() not in _REPS_DEST_SIZE:
            # Therm107/108/109: no output count, but an alias index past its size (round 31)
            alias = alias_storage(stmt, dest) if isinstance(dest, (S.Name, S.Index)) else None
            if alias is not None and _alias_overrun(stmt, dest, alias, 0, profile, env):
                diags.error(stmt.line, f"Variable {alias[2]} out of bounds.", stmt.file)
            continue
        message = _dest_overrun(stmt, dest, reps, profile, env, alias_storage, scope)
        if message is not None:
            diags.error(stmt.line, message, stmt.file)
        if reps > 1 and stmt.keyword.upper() == "CURRENTSE" and profile.name == "cr6":
            diags.error(stmt.line, "Reps >1 not allowed in this device.", stmt.file)  # (round 31)
    _check_bridge_burst(program, profile, diags, env)
    _check_bridge_span(program, profile, diags, env)
    _check_pulse_span(program, profile, diags, env)


# CR6 bridges: (channel, ExChan, MeasPEx argument indexes, measurement groups, channel stride)
_BRIDGE_SPAN = {"BRHALF": (3, 4, 5, 1, 1), "BRHALF3W": (3, 4, 5, 2, 1), "BRFULL": (3, 4, 5, 1, 2),
                "BRHALF4W": (4, 5, 6, 2, 2), "BRFULL6W": (4, 5, 6, 2, 2)}


def _check_bridge_span(program: S.Program, profile: Profile, diags: Diagnostics,
                       env: consteval.Env) -> None:
    """CR6 bridge span validator (round 31, docs/checker.md "Bridge channel spans"): an excitation
    start inside the measurement span -> `Measurement channels and excitation channels overlap.`;
    the first excitation, else (Reps > 1) measurement, channel past U12 -> `Channel %d exceeds
    number of valid channels.` In line order after the destination bounds error. The start channel
    itself is `terminals.py`'s."""
    if profile.name != "cr6":
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() not in _BRIDGE_SPAN:
            continue
        cp, xp, mp, groups, stride = _BRIDGE_SPAN[stmt.keyword.upper()]
        if len(stmt.args) <= mp:
            continue
        reps, chan, ex, meas = (_int_value(stmt.args[i], env) for i in (1, cp, xp, mp))
        if (reps is None or chan is None or ex is None or meas is None) or reps < 1 or not (201 <= chan <= 212 and 201 <= ex <= 212) \
                or (stride == 2 and chan % 2 == 0):
            continue
        s, e, meas = chan - 201, ex - 201, max(meas, 1)
        meas_last = s + (reps - 1) * groups * stride + (groups - 1) * stride
        ex_last = e + -(-reps // meas) - 1
        found = []
        if s < e <= meas_last:   # (the binary's other branch, e < s, cannot fire for Reps >= 1)
            found.append("Measurement channels and excitation channels overlap.")
        past = next((c for c in range(e + 1, ex_last + 1) if c + 1 > 12), None)
        if past is None and ex_last <= 11 and reps > 1:
            past = next((c for c in range(s + stride, meas_last + 1, stride) if c + 1 > 12), None)
        if past is not None:
            found.append(f"Channel {past + 1} exceeds number of valid channels.")
        _insert_in_line_order(diags, stmt, found)


def _insert_in_line_order(diags: Diagnostics, stmt: S.Instruction, texts: list[str]) -> None:
    """After this tier's errors on earlier lines and on this line, before later lines."""
    at = next((i for i, d in enumerate(diags.items) if d.severity == "error" and
               d.file == stmt.file and d.line > stmt.line), len(diags.items))
    diags.items[at:at] = [Diagnostic("error", stmt.line, text, stmt.file) for text in texts]


def _pulse_index(code: int) -> tuple[int, int] | None:
    """(port index, highest index Reps may reach) of a CR6 PulseCount channel (round 31). From
    201 up, the port converter keeps `code % 200` (`412` is U12, `400` C4; docs/checker.md)."""
    idx = code - 101 if 101 <= code <= 116 else code % 200 + 3 \
        if code >= 201 and code % 200 <= 12 else None
    return None if idx is None else (idx, 4 if idx < 4 else 16)   # one past C4 / U12


def _check_pulse_span(program: S.Program, profile: Profile, diags: Diagnostics,
                      env: consteval.Env) -> None:
    """CR6 `PulseCount`: Reps channels from the start must stay in its terminal family ->
    `Invalid PulseCount reps.` after the destination bounds error (round 31, docs/checker.md
    "Bridge channel spans"; CR1000X: the `pulse_chan_cr1000x` terminal span)."""
    if profile.name != "cr6":
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() != "PULSECOUNT" or \
                len(stmt.args) < 3:
            continue
        reps, chan = _int_value(stmt.args[1], env), _int_value(stmt.args[2], env)
        port = _pulse_index(chan) if chan is not None else None
        if reps is None or port is None:
            continue
        if max(reps, 1) - 1 + port[0] > port[1]:
            _insert_in_line_order(diags, stmt, ["Invalid PulseCount reps."])


def _dest_overrun(stmt, dest, reps, profile, env, alias_storage, scope) -> str | None:
    """`Variable %s out of bounds.` when the Float destination cannot hold the output count."""
    if not isinstance(dest, (S.Name, S.Index)):
        return None
    count = _measurement_output_count(stmt, reps, env)
    alias = alias_storage(stmt, dest)
    if alias is not None:
        return f"Variable {alias[2]} out of bounds." if _alias_overrun(
            stmt, dest, alias, count, profile, env) else None
    if scope.types.get(dest.name.upper()) != "FLOAT" or dest.name.upper() not in scope.dims:
        return None
    shape = scope.dims[dest.name.upper()]
    offset = _flat_offset(dest, shape, env)
    if offset is None:
        return None
    total = math.prod(shape)
    return f"Variable {dest.name} out of bounds." if count < 0 or count > total - offset else None


def _check_bridge_burst(program: S.Program, profile: Profile, diags: Diagnostics,
                        env: consteval.Env) -> None:
    """The shared bridge builder checks burst MeasPEx after resolving its destination."""
    if profile.name not in {"cr6", "cr1000x"}:
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() not in {
                "BRHALF4W", "BRFULL6W"} or len(stmt.args) < 7:
            continue
        reps, channel, meas = (_int_value(stmt.args[i], env) for i in (1, 4, 6))
        if reps is None or channel is None or meas is None or meas < 1:
            continue
        # Only valid burst channels: invalid channels have their own conversion diagnostics.
        valid = (-211 <= channel <= -201 and channel % 2 != 0) if profile.name == "cr6" \
            else -8 <= channel <= -1
        if valid and meas != reps:
            diags.error(stmt.line,
                        "When channel < 0, measurements per excitation must equal reps.", stmt.file)


# Destination size rules (oracle, 2026-10-02; docs/checker.md#destination-bounds): elements the
# Float destination must hold from its start -> `Variable %s out of bounds.`. `(per_rep,)` scales the
# Reps argument (index 1); `REALTIME` needs a fixed nine (Therm107-109: `check_reps`, round 31).
# Only CalFile also rejects negative Reps.
_DEST_FIXED = {"REALTIME": 9}
_DEST_PER_REP = {"CALFILE": 1, "AVGRUN": 1, "MOVE": 1, "MINRUN": 1, "MAXRUN": 1, "TOTALRUN": 1,
                 "STDDEVRUN": 1,   # (the other running statistics: oracle, CR6/CR1000X, round 35)
                 "PRTCALC": 1}     # (oracle, CR6/CR1000X, round 57)
_DEST_NEGATIVE_OOB = frozenset({"CALFILE", "PRTCALC"})
_RUNNING_STATS = frozenset({"MINRUN", "MAXRUN", "TOTALRUN", "STDDEVRUN"})   # (AvgRun not probed)


# Element counts the CR6 instruction builders pass to the shared destination resolver, read from the
# decompiled builders and confirmed on the real compilers (docs/checker.md "Builder destination counts").
# Arguments the builder stores in a byte are reduced modulo 256 (`_byte`).
_SDM_MODE_VALUES = {"CS7500": (2, 2, 4, 3, 11, 3, 4, 4), "LI7200": (2, 5, 10, 12, 10, 5, 1, 5),
                    "LI7700": (3, 5, 9, 13, 17, 3, 7, 9)}
_TGA_VALUES = ((2, 3, 4), (5, 7, 9), (8, 10, 12), (19, 25, 31), (21, 27, 33))
_EC100_VALUES = (8, 12, 13, 15, 36)
_BUILDER_FIXED = {"CSAT3B": ((0, 5),), "CSAT3BMONITOR": ((0, 4),), "HYDRAPROBE": ((0, 11), (1, 4)),
                  "GOESGPS": ((0, 6), (1, 7)), "RECTPOLAR": ((0, 2), (1, 2))}


def _byte(value: int) -> int:
    return value & 0xFF


def _builder_counts(kw: str, args: list, env: consteval.Env) -> list[tuple[int, int]]:
    """`(argument, elements)` pairs a destination or source must hold from its start."""
    def arg(i: int) -> int | None:
        return _int_value(args[i], env) if i < len(args) else None
    if kw in _BUILDER_FIXED:
        return [(i, n) for i, n in _BUILDER_FIXED[kw] if i < len(args)]
    if kw == "SDMGENERIC":
        return [(0, count)] if (count := arg(5)) is not None else []
    if kw == "SDMSW8A":
        return [(0, _byte(count))] if (count := arg(1)) is not None else []
    if kw == "SDMCD16AC":
        return [(0, 16 * _byte(count))] if (count := arg(1)) is not None else []
    if kw == "CSAT3":
        if (count := arg(1)) is None:
            return []
        reps = _byte(count)
        return [(0, 5 * reps)] if reps < 128 else []  # a signed byte: negative is `Out of memory.`
    if kw == "PEAKVALLEY":
        return [(i, count) for i in (0, 1, 3) if i < len(args)] if (count := arg(2)) is not None else []
    if kw in _SDM_MODE_VALUES:
        reps, mode = arg(1), arg(3)
        if reps is None or mode is None or _byte(mode) > 7:
            return []
        return [(0, _SDM_MODE_VALUES[kw][_byte(mode)] * _byte(reps))]
    if kw == "TGA":
        first, second = arg(2), arg(3)
        if first is None or second is None or not (1 <= _byte(first) <= 5 and 1 <= _byte(second) <= 3):
            return []
        return [(0, _TGA_VALUES[_byte(first) - 1][_byte(second) - 1])]
    if kw == "EC100":
        option = arg(2)
        if option is None or (option != 2718 and _byte(option) > 3):
            return []
        return [(0, _EC100_VALUES[4 if option == 2718 else _byte(option)])]
    return []


# Spatial family (oracle, 2026-10-02, all four loggers): argument 3, the source, needs Swath
# elements (Float or Long; negative Swath is out of bounds); the destination needs a fixed 2
# (Max/Min: value + location), Swath (SortSpa) or 2 * Swath (SortSpaIndexed); Avg/StdDev/RMS
# impose nothing beyond the index rule. Entries are (fixed, per Swath).
_SPA_DEST = {"MAXSPA": (2, 0), "MINSPA": (2, 0), "SORTSPA": (0, 1), "SORTSPAINDEXED": (0, 2)}
# (oracle, round 56: `MaxSpa(L,...)` -> `Dest type must be Float.`, `SortSpa(LongDest,6,Float)` ->
# `Source and Dest types must be the same.`)
_SPA_TYPED = frozenset({"MAXSPA", "MINSPA", "SORTSPA"})   # (SortSpaIndexed: expressions.py)
_SPA_SOURCE = frozenset({"AVGSPA", "MAXSPA", "MINSPA", "RMSSPA", "STDDEVSPA", "SORTSPA",
                         "SORTSPAINDEXED"})


def check_dest_bounds(program: S.Program, profile: Profile, diags: Diagnostics,
                      env: consteval.Env) -> None:
    from . import symbols
    tables = symbols.scope_tables(program, env, scalars=True)
    # the running statistics also bound a routine's scalar parameter (`Sub R(Q)` / `MinRun(Q,2,...)`
    # is `Variable Q out of bounds.`; oracle, CR6/CR1000X, 2026-10-25, round 35)
    with_parameters = symbols.scope_tables(program, env, scalars=True, parameters=True)
    dims: dict[str, tuple[int, ...]] = {}
    types: dict[str, str] = {}
    arrays: set[str] = set()

    def overruns(arg: S.Expr | None, need: int, negative: bool,
                 kinds: tuple[str, ...]) -> TypeGuard[S.Name | S.Index]:
        if not isinstance(arg, (S.Name, S.Index)):
            return False
        shape = dims.get(arg.name.upper())
        if shape is None or types.get(arg.name.upper()) not in kinds:
            return False
        offset = _flat_offset(arg, shape)
        if offset is None:
            return False
        total = 1
        for n in shape:
            total *= n
        if offset >= total:  # an index past the dimension is the index rule's
            return False
        return negative or need > total - offset

    names = longconv.float_names(program.statements), longconv.literal_names(program.statements)
    alias_storage = _measurement_aliases(program, tables, env)
    for stmt in program.statements:
        scope = tables(stmt)
        dims, types, arrays = scope.dims, scope.types, scope.arrays
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "CDM_TCCOMP" and \
                len(stmt.args) > 3 and isinstance(stmt.args[2], (S.Name, S.Index)):
            # Reps (fourth) of the destination (third); negative Reps overruns too (oracle, CR6/
            # CR1000X, 2026-10-10; docs/instructions.md "CPI measurement time")
            reps = longconv.long_value(stmt.args[3], env, profile, *names)
            if reps is not None and overruns(stmt.args[2], reps, reps < 0, ("FLOAT",)):
                diags.error(stmt.line, f"Variable {stmt.args[2].name} out of bounds.", stmt.file)
            continue
        if not isinstance(stmt, S.Instruction) or not stmt.args or \
                not isinstance(stmt.args[0], (S.Name, S.Index)):
            continue
        kw = stmt.keyword.upper()
        if kw in _RUNNING_STATS:
            scope = with_parameters(stmt)
            dims, types, arrays = scope.dims, scope.types, scope.arrays
        dest = stmt.args[0]
        name = dest.name.upper()
        bad: list[S.Name | S.Index] = []
        if kw in _SPA_SOURCE and len(stmt.args) > 2:
            swath = _int_value(stmt.args[1], env)
            if swath is not None:
                fixed, per = _SPA_DEST.get(kw, (0, 0))
                if (fixed or per) and overruns(dest, fixed + per * swath, per > 0 and swath < 0,
                                               ("FLOAT", "LONG")):   # (Long too, round 56)
                    bad.append(dest)
                if overruns(source := stmt.args[2], swath, swath < 0, ("FLOAT", "LONG")):
                    bad.append(source)
        elif kw == "SPLITSTR":  # a scalar destination takes one value (Reps > 1 overruns it)
            reps = _int_value(stmt.args[3], env) if len(stmt.args) > 3 else None
            if isinstance(dest, S.Name) and name in dims and name not in arrays and \
                    reps is not None and reps > 1 and \
                    types.get(name) in ("FLOAT", "STRING"):  # a structure takes Reps 3
                bad.append(dest)
        elif kw == "CSAT3" and len(stmt.args) > 1 and (reps := _int_value(stmt.args[1], env)) is not None and \
                _byte(reps) >= 128:
            # a negative signed-byte Reps fails the builder's allocation before the destination
            # is resolved; the compile ends there (round 31, docs/checker.md "Bridge channel spans")
            diags.error(stmt.line, "Out of memory.", stmt.file)
            return
        elif kw in _BUILDER_FIXED or kw in ("SDMGENERIC", "SDMSW8A", "SDMCD16AC", "CSAT3",
                                              "PEAKVALLEY", "TGA", "EC100") or kw in _SDM_MODE_VALUES:
            # SDMGeneric checks any non-String destination, including a negative count
            kinds = ("FLOAT", "LONG", "BOOLEAN") if kw == "SDMGENERIC" else ("FLOAT",)
            for i, need in _builder_counts(kw, stmt.args, env):
                if overruns(target := stmt.args[i], need, need < 0, kinds):
                    bad.append(target)
        elif kw in _DEST_FIXED or kw in _DEST_PER_REP:
            negative = False
            if kw in _DEST_FIXED:
                need = _DEST_FIXED[kw]
            else:
                reps = _int_value(stmt.args[1], env) if len(stmt.args) > 1 else None
                if reps is None or (reps < 0 and kw not in _DEST_NEGATIVE_OOB):
                    continue
                negative = reps < 0
                need = reps * _DEST_PER_REP[kw]
            alias = alias_storage(stmt, dest) if kw in _DEST_PER_REP else None
            if alias is not None:
                # an alias destination counts from the alias (oracle, CR6/CR1000X, round 57: PRTCalc,
                # CalFile, Move and the running statistics)
                if _alias_overrun(stmt, dest, alias, -1 if negative else need, profile, env):
                    diags.error(stmt.line, f"Variable {alias[2]} out of bounds.", stmt.file)
            elif overruns(dest, need, negative, ("FLOAT",)):
                bad.append(dest)
        # single-pass loggers: before this line's warnings and channel errors (oracle)
        for arg in (reversed(bad) if profile.single_pass else bad):
            diag = Diagnostic("error", stmt.line, f"Variable {arg.name} out of bounds.", stmt.file)
            if profile.single_pass:
                at = next((i for i, d in enumerate(diags.items)
                           if (d.line, d.file) == (stmt.line, stmt.file)), len(diags.items))
                diags.items.insert(at, diag)
            else:
                diags.items.append(diag)
        if kw in _SPA_TYPED and len(stmt.args) > 2 and profile.name in ("cr6", "cr1000x"):
            source = stmt.args[2]
            dest_type = types.get(name)
            source_type = types.get(source.name.upper()) if isinstance(source, (S.Name, S.Index)) else None
            if kw in ("MAXSPA", "MINSPA") and dest_type not in (None, "FLOAT"):
                diags.error(stmt.line, "Dest type must be Float.", stmt.file)
            elif kw not in ("MAXSPA", "MINSPA") and dest_type and source_type and dest_type != source_type:
                diags.error(stmt.line, "Source and Dest types must be the same.", stmt.file)


def _index_value(arg: S.Expr | None, env: consteval.Env | None) -> int | None:
    """An integer literal index, or with `env` any integer constant (`X(K)`, round 30 review)."""
    if isinstance(arg, S.Number) and isinstance(arg.value, int):
        return arg.value
    v = _const_value(arg, env) if env is not None else None
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _flat_offset(dest: S.Name | S.Index, shape: tuple[int, ...], env: consteval.Env | None = None) -> int | None:
    """0-based row-major offset of the destination's first element, or None if not literal
    (or, with `env`, constant)."""
    if isinstance(dest, S.Name) or not dest.args or all(a is None for a in dest.args):
        return 0
    index = [k for a in dest.args if (k := _index_value(a, env)) is not None]
    if len(index) != len(dest.args):
        return None
    if len(index) == 1:
        return index[0] - 1 if 1 <= index[0] else None
    if len(index) != len(shape) or any(not 1 <= k <= n for k, n in zip(index, shape)):
        return None
    offset = 0
    for k, n in zip(index, shape):
        offset = offset * n + (k - 1)
    return offset
