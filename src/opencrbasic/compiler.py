"""Compile driver: parse, check, and render CR6Comp-compatible results."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import profiles
from .diagnostics import Diagnostics
from .profiles import Profile

# Error messages accumulate in a 4096-byte buffer (decoded from the exe, `docs/checker.md` "Error
# message buffer"); once a message no longer fits, it is dropped and pass 1 reports nothing
# further. Oracle-confirmed by construction (a 150-line program with a distinct undefined-symbol
# error on every line): the printed output stops after the 69th message, the last one whose own
# `line N: <text>` plus a 2-byte CRLF terminator keeps the running total at or under 4096 bytes --
# the 70th would push it over and is silently missing, with no truncation marker of any kind. This
# accounts only for the rendered `line N: <message>` text (the `<file> ` prefix included, when
# present), not the `"<program> -- Compile Failed!"` header or the blank line after it, which are
# printed unconditionally and are not part of this budget.
_MESSAGE_BUFFER_LIMIT = 4096


def _bounded_by_message_buffer(lines: list[str]) -> list[str]:
    used = 0
    out = []
    for line in lines:
        cost = len(line.encode("latin-1", "replace")) + 2  # + CRLF, as the real buffer counts it
        if used + cost > _MESSAGE_BUFFER_LIMIT:
            break
        used += cost
        out.append(line)
    return out


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
            # CR6Comp is a single top-to-bottom pass over the file, so its own diagnostics are
            # always in ascending line order (oracle-confirmed throughout `docs/checker.md`'s
            # corpus evidence). `self.diagnostics.errors` is insertion order, which several
            # independent checker passes each contribute to in their own statement-list order
            # (`checker/__init__.py`) -- individually ascending, but not necessarily interleaved
            # correctly against each other, so a stable sort by line (never observed out of order
            # on the same line, so ties keep each check's own relative order) reproduces the
            # single-pass compiler's output order without each checker pass needing to coordinate.
            errors = sorted(self.diagnostics.errors, key=lambda d: d.line)
            rendered = _bounded_by_message_buffer([d.cr6comp(self.program) for d in errors])
            return [f"{self.program} -- Compile Failed!", ""] + rendered
        if self.profile.reports_mode:
            lines = [f"{self.program} -- Compiled in {self.mode or 'Sequential'}Mode."]
        else:
            lines = [f"{self.program} -- Compiled OK."]
        if show_signature and self.signature is not None:
            lines.append(f"ProgSignature =  {self.signature}.")
        lines += [d.cr6comp(self.program) for d in self.diagnostics.warnings]
        return lines


def compile_file(path: Path, profile: Profile | None = None, display_name: str | None = None) -> CompileResult:
    """Never lets an unexpected exception escape (`docs/checker.md` "Robustness"): the real
    compiler never crashes on a syntactically-loadable file, so an internal bug in this project
    should surface as a rejected program, not a Python traceback that takes down whatever called
    this (`tools/fuzz.py`'s own worker pool did exactly that before it grew its own timeout guard,
    `docs/fuzz-findings.md` "Hang"). No oracle text can ever exist for "opencrbasic itself broke",
    so the fallback message here is a generic marker, never a claim about what CR6Comp.exe itself
    would say -- `checker.check`'s own finer-grained per-pass wrapping is what actually keeps
    *most* internal bugs from reaching here at all.
    """
    from . import checker, parser

    profile = profile or profiles.for_file(path)
    name = display_name or path.name
    try:
        program, diags = parser.parse_file(path, profile=profile)
    except Exception:
        diags = Diagnostics()
        diags.error(0, "Internal error: unable to parse program.")
        return CompileResult(name, profile, diags)
    result = CompileResult(name, profile, diags)
    if len(path.name) + 1 > 60:
        diags.error(0, f"Program name exceeds maximum allowable characters by {len(path.name) - 59}.")
    try:
        checker.check(program, profile, diags, result)
    except Exception:
        pass
    from .signature import program_signature
    try:
        result.signature = program_signature(path)
    except Exception:
        result.signature = None
    return result
