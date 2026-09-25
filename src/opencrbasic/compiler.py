"""Compile driver: parse, check, and render CR6Comp-compatible results."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import profiles
from .diagnostics import Diagnostics
from .profiles import Profile


@dataclass
class CompileResult:
    program: str                    # program name as given on the command line
    profile: Profile
    diagnostics: Diagnostics
    mode: str | None = None         # "Sequential" / "Pipeline" (CR6, CR1000X)
    tables: list = field(default_factory=list)      # tdf.TdfTable list, when available
    table_list: str | None = None   # text of the `-l` output, when available
    signature: int | None = None

    @property
    def ok(self) -> bool:
        return not self.diagnostics.errors

    def result_lines(self, show_signature: bool = False) -> list[str]:
        """The lines CR6Comp prints to stdout (and the message file) for this result."""
        if not self.ok:
            return [f"{self.program} -- Compile Failed!", ""] + [d.cr6comp() for d in self.diagnostics.errors]
        if self.profile.reports_mode:
            lines = [f"{self.program} -- Compiled in {self.mode or 'Sequential'}Mode."]
        else:
            lines = [f"{self.program} -- Compiled OK."]
        if show_signature and self.signature is not None:
            lines.append(f"ProgSignature =  {self.signature}.")
        lines += [d.cr6comp() for d in self.diagnostics.warnings]
        return lines


def compile_file(path: Path, profile: Profile | None = None, display_name: str | None = None) -> CompileResult:
    from . import checker, parser

    profile = profile or profiles.for_file(path)
    name = display_name or path.name
    program, diags = parser.parse_file(path)
    result = CompileResult(name, profile, diags)
    if len(path.name) + 1 > 60:
        diags.error(0, f"Program name exceeds maximum allowable characters by {len(path.name) - 59}.")
    checker.check(program, profile, diags, result)
    return result
