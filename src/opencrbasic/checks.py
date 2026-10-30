"""opencrbasic checks: likely defects the reference compiler accepts (`PLAN.md` "Three diagnostic
channels"). Findings never enter `Diagnostics`; `crbc --strict` prints them on stderr."""

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from . import checkconfig, consteval
from . import syntax as S
from .checker import symbols, var_types

# Built-in default severity per check code; also the code set the configuration validates against.
DEFAULT_SEVERITY = {"OCB001": "error", "OCB002": "warning", "OCB003": "warning", "OCB004": "warning",
                    "OCB005": "info", "OCB006": "info", "OCB007": "warning"}
SCHEMA_VERSION = 1


@dataclass
class Finding:
    code: str
    severity: str        # "error" | "warning" | "info"
    line: int
    file: str
    message: str
    reason: str = ""     # the suppression's reason, on `CompileResult.suppressed` only

    def text(self, program: str) -> str:
        return f"{self.file or program} line {self.line}: {self.severity} {self.code}: {self.message}"


# Names the reference accepts as an `Alias` name although CRBasic already uses them (oracle,
# 2026-10-11 `alias_predefined_name.json` and 2026-10-24 `r33_geus_false_rejects.json`, all four
# loggers). Predefined constants (`Sec`, `mV5000`, `FP2`, `String`) are reference errors instead.
_SYSTEM_TABLES = {"PUBLIC", "STATUS", "SETTINGS"}
_KEYWORDS = {"THEN", "ELSE", "TO", "STEP", "AS", "ENDIF", "NEXT", "DIM", "AND", "OR", "NOT", "XOR",
             "MOD"}
_SPECIAL_VALUES = {"NAN", "INF"}


def _reserved_kind(name: str, profile) -> str | None:
    upper = name.upper()
    if symbols.is_reserved_predefined_name(name, profile):
        return None
    if upper in _SYSTEM_TABLES:
        return f"the {name} system table"
    if upper in _KEYWORDS:
        return "a CRBasic keyword"
    if upper in profile.declarable_predefined_names:
        return "a data type"
    if upper in _SPECIAL_VALUES:
        return "a special value"
    if upper in profile.functions:
        return "a built-in function"
    if upper in profile.instructions:
        return "an instruction"
    return None


def _alias_keyword(program: S.Program, profile) -> list[Finding]:
    """OCB001: an `Alias` named after a CRBasic name (`Alias D(3)=Status`). The reference accepts
    it, but the name then shadows the original: a bare `Status` becomes the alias while
    `Status.Battery` still reads the table (`docs/checker.md` "Aliases named Status, Public or
    Settings"), which is easy to misread. Human request, 2026-10-24."""
    out = []
    for st in program.statements:
        if not (isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS"):
            continue
        for arg in st.args[1:]:
            if isinstance(arg, (S.Name, S.Index)) and (kind := _reserved_kind(arg.name, profile)):
                out.append(Finding("OCB001", "error", st.line, st.file,
                                   f"Alias name {arg.name} is also {kind}; choose another name."))
    return out


_NUMERIC_TYPES = {"FLOAT", "LONG", "BOOLEAN", "DOUBLE"}


def _statements(program: S.Program):
    """Every statement, a one-line If's own statements included."""
    for st in program.statements:
        yield st
        if isinstance(st, S.If):
            yield from (st.inline or []) + (st.inline_else or [])


def _declared_names(program: S.Program) -> list[str]:
    """Every declared variable and routine parameter name, upper case, once per declaration."""
    names = [v.name.upper() for st in program.statements
             if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC") for v in st.vars]
    return names + [p.name.upper() for st in program.statements if isinstance(st, S.SubDef) for p in st.params]


def _const_env(program: S.Program, profile) -> consteval.Env:
    """The program's constants without those a variable or parameter of the same name can shadow
    (`Sub P(Z As Long)` with a later `Const Z = 0`, `docs/checker.md` "Sub names shadowing an
    earlier Const")."""
    shadowed = set(_declared_names(program))
    return consteval.Env(profile, {k: v for k, v in program.consts.items() if k.upper() not in shadowed})


def _names_in(expr) -> set[str]:
    """Upper-case names an expression refers to, at any depth."""
    if isinstance(expr, S.Name):
        return {expr.name.upper()}
    if isinstance(expr, S.Index):
        return {expr.name.upper()}.union(*(_names_in(a) for a in expr.args))
    if isinstance(expr, S.Member):
        return _names_in(expr.base).union(*(_names_in(a) for a in expr.args or []))
    if isinstance(expr, S.Binary):
        return _names_in(expr.left) | _names_in(expr.right)
    if isinstance(expr, S.Unary):
        return _names_in(expr.operand)
    return set()


def _ambiguous_names(program: S.Program) -> set[str]:
    """Names whose declaration a check cannot pick without scope resolution: declared more than once
    (a `Dim` in two routines, a global and a parameter) or used in an `Alias`."""
    names = _declared_names(program)
    out = {n for n in names if names.count(n) > 1}
    for st in program.statements:
        if isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS":
            out |= {a.name.upper() for a in st.args if isinstance(a, (S.Name, S.Index))}
    return out


def _numeric_concatenation(program: S.Program, profile) -> list[Finding]:
    """OCB002: a String `+` (or `&`) assigned to a numeric variable (`L = 2000 + Mid(S,5,2)`). The
    reference accepts it, but `+` with a String operand concatenates, and the variable receives
    the converted text: a GPS clock set this way read 2057 (`knowledge/crbasic/lessons.md` "`+` with
    a string operand concatenates", hardware 2026-10-22). Only a chain mixing numeric and String
    operands is reported: a String alone (`X = Mid(...)`) is a plain conversion, and Strings joined
    into a number's text (`X = HexStr(6) + "." + HexStr(7)`, GEUS programs) are deliberate."""
    typer, out, ambiguous = var_types._Typer(program), [], _ambiguous_names(program)
    for st in _statements(program):
        if not isinstance(st, S.Assignment) or not isinstance(st.value, S.Binary) \
                or st.value.op not in ("+", "&"):
            continue
        if isinstance(st.target, (S.Name, S.Index)) and st.target.name.upper() in ambiguous:
            continue
        var = typer.variable(st.target)
        if var is None or typer.types[var] not in _NUMERIC_TYPES:
            continue
        operands, stack = [], [st.value]
        while stack:
            node = stack.pop()
            if isinstance(node, S.Binary) and node.op in ("+", "&"):
                stack += [node.left, node.right]
            else:
                operands.append("SCOPED" if _names_in(node) & ambiguous else typer.of(node))
        if "SCOPED" in operands:
            continue   # an operand's type needs scope resolution
        if "STRING" not in operands or not any(t in _NUMERIC_TYPES for t in operands):
            continue   # `HexStr(6) + "." + HexStr(7)` builds a number's text on purpose (GEUS)
        name = st.target.name if isinstance(st.target, (S.Name, S.Index)) else var
        out.append(Finding("OCB002", "warning", st.line, st.file,
                           f"`{st.value.op}` with a String operand concatenates; {name} "
                           "receives the converted text. Convert the String to a number first."))
    return out


# microseconds per `Delay`/`DataInterval` unit code (uSec, mSec, Sec, Min, Hr, Day)
_US_PER_UNIT = {0: 1, 1: 1_000, 2: 1_000_000, 3: 60_000_000, 4: 3_600_000_000, 5: 86_400_000_000}


def _negative_delays(program: S.Program, profile) -> list[Finding]:
    """OCB003: a constant negative `Delay` interval or `ExciteV` delay that the reference accepts
    (`Delay(1,-5,Sec)`, `Delay(2,-1,Sec)`; `ExciteV(Vx1,0,-10)` on CR300/CR350 only; most negative
    `Delay(0,...)` and every `ExciteV` delay of `-1` or less on CR6/CR1000X are reference errors). The
    compiler keeps the delay as an unsigned 32-bit count, so it is not the written one; the runtime
    effect is not observed (`docs/checker.md` "Task-memory overflow of long delays (round 57)",
    human proposal 2026-10-28). A zero delay is legitimate."""
    env, out = _const_env(program, profile), []
    for st in _statements(program):
        if not isinstance(st, S.Instruction):
            continue
        kw = st.keyword.upper()
        pos = {"DELAY": 1, "EXCITEV": 2}.get(kw)
        if pos is None or len(st.args) <= 2 or st.args[2] is None:
            continue
        try:
            value = consteval.value_of(st.args[pos], env)
            unit = 0 if kw == "EXCITEV" else consteval.value_of(st.args[2], env)
        except consteval.NotConstant:
            continue
        # at least one whole microsecond below zero: `ExciteV(U1,0,-0.9)` compiles on CR6 where
        # `-1` is a reference error, so a fraction is not shown to wrap (oracle, 2026-10-30)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and unit in _US_PER_UNIT \
                and value * _US_PER_UNIT[unit] <= -1:
            out.append(Finding("OCB003", "warning", st.line, st.file,
                               f"{st.keyword} delay {value:g} is negative; the logger does not "
                               "wait the written time."))
    return out


def _zero_divisors(program: S.Program, profile) -> list[Finding]:
    """OCB004: a division by a constant zero (`Y / 0`, `Y \\ 0`, `Y MOD 0`, `Y / Z` with
    `Const Z = 0`), which the reference compiles on all four loggers (oracle, round 65). The
    result is undefined for every dividend (`datatypes.htm`: undefined mathematical results give
    NAN or INF); its runtime value is not observed."""
    env, out = _const_env(program, profile), []

    def walk(expr):
        if isinstance(expr, S.Binary):
            yield expr
            yield from walk(expr.left)
            yield from walk(expr.right)
        elif isinstance(expr, S.Unary):
            yield from walk(expr.operand)
        elif isinstance(expr, (S.Index, S.Member)):
            for a in expr.args or []:
                yield from walk(a)

    for st in _statements(program):
        for top in var_types._statement_expressions(st):
            for node in walk(top):
                if node.op not in ("/", "\\", "MOD", "INTDV"):
                    continue
                try:
                    divisor = consteval.value_of(node.right, env)
                except consteval.NotConstant:
                    continue
                if isinstance(divisor, (int, float)) and not isinstance(divisor, bool) and divisor == 0:
                    out.append(Finding("OCB004", "warning", st.line, st.file,
                                       "Division by a constant zero."))
    return out


def _unsized_string_parameters(program: S.Program, profile) -> list[Finding]:
    """OCB005: a `Sub`/`Function` String parameter without `* size`. It holds 23 characters (24
    bytes with the terminator) and a longer argument is cut (hardware, `knowledge/crbasic/
    lessons.md` "Default string parameter size"). Information only: a short argument is fine."""
    return [Finding("OCB005", "info", st.line, st.file,
                    f"String parameter {p.name} of {st.name} has no size and holds 23 characters; "
                    "give it a size (As String * n) if a longer value can be passed.")
            for st in program.statements if isinstance(st, S.SubDef)
            for p in st.params if (p.type or "").upper() == "STRING" and p.string_size is None]


def _unaligned_intervals(program: S.Program, profile) -> list[Finding]:
    """OCB006: a constant `DataInterval` that divides neither an hour nor a day (`7 Min`, `25 Hr`).
    Its records are phased from the start hour and may not align with midnight (hardware,
    `knowledge/crbasic/lessons.md` "`DataInterval` phase"). Information only; an interval of 0
    (every call) is not reported."""
    env, out = _const_env(program, profile), []
    for st in _statements(program):
        if not (isinstance(st, S.Instruction) and st.keyword.upper() == "DATAINTERVAL") or len(st.args) < 3:
            continue
        try:
            value, unit = consteval.value_of(st.args[1], env), consteval.value_of(st.args[2], env)
        except consteval.NotConstant:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or unit not in _US_PER_UNIT:
            continue
        us = value * _US_PER_UNIT[unit]
        if us <= 0 or us != int(us) or 3_600_000_000 % int(us) == 0 or 86_400_000_000 % int(us) == 0:
            continue
        out.append(Finding("OCB006", "info", st.line, st.file,
                           "DataInterval divides neither an hour nor a day; its records are phased "
                           "from the start hour and may not align with midnight."))
    return out


def _long_string_literals(program: S.Program, profile) -> list[Finding]:
    """OCB007: a String literal assigned to a String variable that cannot hold it (`S = "..."` with
    36 characters on `S As String * 32`). The size is the most characters the variable holds
    (`dim.htm`, `public.htm`); the reference accepts the assignment on all four loggers (oracle,
    2026-10-30). The capacity is the larger reading: the round-29 storage slot `4*ceil(max(4,
    L+1)/4) - 1` (`docs/checker.md` "String alias storage (round 29)"), except `String * 1`, which
    holds one character (`dim.htm`). Names declared more than once or used in an `Alias`, and
    character indices, are not checked."""
    env, decls, out = _const_env(program, profile), {}, []
    ambiguous = _ambiguous_names(program)
    for st in program.statements:
        if not (isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC")):
            continue
        for v in st.vars:
            if (v.type or "").upper() != "STRING" or v.paren_dim or len(v.dims) > 2 \
                    or v.name.upper() in ambiguous:
                continue
            try:
                size = 23 if v.string_size is None else consteval.value_of(v.string_size, env)
            except consteval.NotConstant:
                continue
            if isinstance(size, bool) or not isinstance(size, (int, float)) or not 1 <= size < 2**31:
                continue
            size = int(size)
            decls[v.name.upper()] = (1 if size == 1 else 4 * -(-max(4, size + 1) // 4) - 1, len(v.dims))
    for st in _statements(program):
        if not (isinstance(st, S.Assignment) and isinstance(st.value, S.String)
                and isinstance(st.target, (S.Name, S.Index))):
            continue
        capacity, rank = decls.get(st.target.name.upper(), (None, 0))
        if capacity is None or (isinstance(st.target, S.Index) and len(st.target.args) > rank):
            continue
        if len(st.value.value) > capacity:
            out.append(Finding("OCB007", "warning", st.line, st.file,
                               f"String literal of {len(st.value.value)} characters is longer than "
                               f"{st.target.name} holds ({capacity}); it is cut."))
    return out


def run(program: S.Program | None, profile) -> list[Finding]:
    if program is None:
        return []
    return [f for check in (_alias_keyword, _numeric_concatenation, _negative_delays, _zero_divisors,
                                        _unsized_string_parameters, _unaligned_intervals, _long_string_literals)
            for f in check(program, profile)]


def analyze(result, entry: Path, config: checkconfig.Config | None = None,
            cli: dict[str, str] | None = None, lsp: dict[str, str] | None = None) -> None:
    """Run the checks on a compiled `result` and store them, after severity and suppression
    configuration, in `result.findings` / `result.suppressed` (never in `result.diagnostics`, so
    `result.ok` is unchanged). `cli` and `lsp` are severity maps (`checkconfig.parse_overrides`)."""
    cfg = config or checkconfig.Config()
    entry = entry.resolve()
    root = cfg.root or entry.parent

    def rel(p: Path) -> str:
        try:
            return Path(os.path.relpath(p, root)).as_posix().lower()
        except ValueError:   # another Windows drive: no relative path, so no entry or glob matches
            return p.as_posix().lower()

    for f in run(result.program_ast, result.profile):
        sev = checkconfig.severity(cfg, f.code, DEFAULT_SEVERITY[f.code], result.profile.name,
                                   rel(entry), cli or {}, lsp or {})
        if sev == "off":
            continue
        f = replace(f, severity=sev)
        # an included file is named as written in the `Include` (drive prefix and all), relative
        # to the entry file's directory; the entry itself by its name
        file = entry if f.file in ("", entry.name) else entry.parent / re.sub(r"^[A-Za-z]+:", "", f.file).replace("\\", "/")
        if s := checkconfig.suppression(cfg, f.code, rel(file), f.line):
            result.suppressed.append(replace(f, reason=s.reason))
        else:
            result.findings.append(f)


def report(result, program: str, strict: bool, config_file: Path | None, config_error: str | None) -> dict:
    """The `crbc --report` document (`docs/checker.md` "Check configuration and report file")."""

    def item(f: Finding) -> dict:
        return {"code": f.code, "severity": f.severity, "file": f.file or program, "line": f.line,
                "message": f.message}

    return {
        "schema_version": SCHEMA_VERSION,
        "program": program,
        "logger": result.profile.name,
        "reference": {
            "ok": result.ok,
            "mode": result.mode,
            "diagnostics": [{"severity": d.severity, "file": d.file or program, "line": d.line,
                             "message": d.message} for d in result.diagnostics.items],
        },
        "strict": strict,
        "config": {"file": str(config_file) if config_file else None, "error": config_error},
        "findings": [item(f) for f in result.findings],
        "suppressed": [{**item(f), "reason": f.reason} for f in result.suppressed],
    }


def missing_program_report(program: str, logger: str, strict: bool) -> dict:
    """The `crbc --report` document when the entry file does not exist: the reference's `No Program
    File` failure, no checks run."""
    return {
        "schema_version": SCHEMA_VERSION,
        "program": program,
        "logger": logger,
        "reference": {"ok": False, "mode": None,
                      "diagnostics": [{"severity": "error", "file": program, "line": None,
                                       "message": "No Program File"}]},
        "strict": strict,
        "config": {"file": None, "error": None},
        "findings": [],
        "suppressed": [],
    }
