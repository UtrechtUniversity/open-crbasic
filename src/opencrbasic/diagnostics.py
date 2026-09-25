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

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Diagnostic:
    severity: str           # "error" or "warning"
    line: int               # 0 when the message has no line (e.g. "No BeginProg in Program.")
    message: str            # without the "line N: " / "Warning: " prefix
    file: str = ""          # originating file, for our own richer output
    show_file: bool = True  # whether an origin outside `main_file` gets a `<file>` prefix; see
    # this module's docstring for the two oracle-confirmed exceptions that set this False.

    def cr6comp(self, main_file: str = "") -> str:
        if self.severity == "warning":
            return f"Warning: {self.message}"
        if not self.line:
            return self.message
        prefix = ""
        if self.show_file and self.file and self.file.lower() != main_file.lower():
            prefix = f"<{self.file}> "
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
