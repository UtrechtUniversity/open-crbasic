"""Diagnostics in CR6Comp.exe's format.

CR6Comp prints errors as `line N: <message>` and warnings as `Warning: <message>`; the message
texts mirror the exe's format strings (docs/messages.tsv). Line numbers are those of the main
program file; for code that came from an include file the exe reports the line of the include
statement (see docs/pass1.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Diagnostic:
    severity: str           # "error" or "warning"
    line: int               # 0 when the message has no line (e.g. "No BeginProg in Program.")
    message: str            # without the "line N: " / "Warning: " prefix
    file: str = ""          # originating file, for our own richer output

    def cr6comp(self) -> str:
        if self.severity == "warning":
            return f"Warning: {self.message}"
        return f"line {self.line}: {self.message}" if self.line else self.message


@dataclass
class Diagnostics:
    items: list[Diagnostic] = field(default_factory=list)

    def error(self, line: int, message: str, file: str = "") -> None:
        self.items.append(Diagnostic("error", line, message, file))

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
