"""opencrbasic checks: likely defects the reference compiler accepts (`PLAN.md` "Three diagnostic
channels"). Findings never enter `Diagnostics`; `crbc --strict` prints them on stderr."""

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from . import checkconfig
from . import syntax as S
from .checker import symbols

# Built-in default severity per check code; also the code set the configuration validates against.
DEFAULT_SEVERITY = {"OCB001": "error"}
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


def run(program: S.Program | None, profile) -> list[Finding]:
    return [] if program is None else _alias_keyword(program, profile)


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
