"""opencrbasic checks: likely defects the reference compiler accepts (`PLAN.md` "Three diagnostic
channels"). Findings never enter `Diagnostics`; `crbc --strict` prints them on stderr."""

from dataclasses import dataclass

from . import syntax as S
from .checker import symbols


@dataclass
class Finding:
    code: str
    severity: str        # "error" | "warning" | "info"
    line: int
    file: str
    message: str

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
