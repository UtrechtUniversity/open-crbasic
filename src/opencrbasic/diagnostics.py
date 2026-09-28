"""Diagnostics in CR6Comp.exe's format.

CR6Comp prints errors as `line N: <message>` and warnings as `Warning: <message>`; the message
texts mirror the exe's format strings (docs/messages.tsv). Line numbers are per-file, not
translated to the site of the `Include`/`IncludeSection` call that pulled the code in
(`source.py`'s own docstring, oracle-confirmed).

**File-origin prefix** (`docs/checker.md` "Errors from an included file", oracle-confirmed this
session): an *error* whose statement came from a file other than the main program is printed as
`<file> line N: <message>`, the included file's own name in angle brackets before the usual `line
N:` -- but only for most message classes. Two are oracle-confirmed exceptions, both the generic
"this statement could not be resolved as anything valid" fallback (`checker/structure.py`'s `Sub
%s not defined.` and `Invalid, or out of place expression: %s.`): these never carry the prefix,
regardless of which file the statement came from. No other exception is known; every other message
class this session tested (from `structure.py`, `expressions.py`, and `checker/instr_rules.py`'s
`Invalid or missing parameter.`) does carry it. `Diagnostic.show_file` is how a call site opts a
message *out* of the default (`Diagnostics.error(..., show_file=False)`); nothing needs to opt in.
Warnings are untested against this rule and are left unprefixed (`cr6comp` only applies it to
errors) rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_DRIVE_RE = re.compile(r"^(CPU|USR|CRD|USB):", re.IGNORECASE)


@dataclass(frozen=True)
class Diagnostic:
    severity: str           # "error" or "warning"
    line: int               # 0 when the message has no line (e.g. "No BeginProg in Program.")
    message: str            # without the "line N: " / "Warning: " prefix
    file: str = ""          # originating file, for our own richer output
    show_file: bool = True  # whether an origin outside `main_file` gets a `<file>` prefix; see
    # this module's docstring for the two oracle-confirmed exceptions that set this False.
    cascade: bool = True    # CR300/CR350: may start the `Too many parameters` cascade (`cr3xx`)

    def cr6comp(self, main_file: str = "", drive_prefix: bool = False) -> str:
        if self.severity == "warning":
            # A warning with a real line (`checker/warnings.py`'s ungated reachability/background-
            # calibration/fnotch checks) is prefixed `line N: `, same as an error (oracle:
            # `docs/instructions.md`, e.g. `line 6: Warning: Delay never reached at run time.`); one
            # with no line (the pre-existing "table declared but never called" check, `diags.warning
            # (0, ...)`) stays bare (oracle: `Warning: Table Foo, is declared but never called.`,
            # no `line N:` at all). The `<file>` include prefix is left untested for warnings, as
            # before (see this module's docstring).
            if self.line:
                return f"line {self.line}: Warning: {self.message}"
            return f"Warning: {self.message}"
        if not self.line:
            return self.message
        prefix = ""
        name = _DRIVE_RE.sub("", self.file or "")
        if self.show_file and name and name.lower() != main_file.lower():
            # An included file prints as written in its `Include`: CR6/CR1000X without the drive
            # (`<lib.CRB>` for `"cpu:lib.CRB"`), CR300/CR350 with it, `cpu:` added when none was
            # written (`<cpu:Lib.crb>`) -- oracle, 2026-09-27, `docs/loggers.md`.
            if drive_prefix:
                name = self.file if _DRIVE_RE.match(self.file) else f"cpu:{self.file}"
            prefix = f"<{name}> "
        return f"{prefix}line {self.line}: {self.message}"


@dataclass
class Diagnostics:
    items: list[Diagnostic] = field(default_factory=list)

    def error(self, line: int, message: str, file: str = "", show_file: bool = True) -> None:
        self.items.append(Diagnostic("error", line, message, file, show_file))

    def warning(self, line: int, message: str, file: str = "") -> None:
        self.items.append(Diagnostic("warning", line, message, file))

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == "warning"]

    def __bool__(self) -> bool:
        return bool(self.errors)
