"""Compile driver: parse, check, and render CR6Comp-compatible results."""

import bisect
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import profiles
from . import syntax as S
from .diagnostics import Diagnostics
from .profiles import Profile
from .sources import SourceProvider

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
_IMPLEMENTATION_FAILURE_MESSAGE = "Internal error: unable to compile program."


def _no_line_kind(d) -> int:
    """Secondary sort key for a no-line (`d.line == 0`) diagnostic: `-u`'s own "declared but not
    used"/"declared but never called" (`checker/unused.py`) before any other no-line warning (in
    this project, only the pre-existing "table declared but never called" check) -- see
    `CompileResult.result_lines`'s docstring for the decompiled/oracle evidence.
    """
    return 0 if d.message.endswith("declared but not used.") else 1


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
    program: str  # program name as given on the command line
    profile: Profile
    diagnostics: Diagnostics
    mode: str | None = None  # "Sequential" / "Pipeline" (CR6, CR1000X)
    tables: list = field(default_factory=list)  # tdf.TdfTable list, when available
    table_list: str | None = None  # text of the `-l` output, when available
    signature: int | None = None
    file_order: dict = field(default_factory=dict)  # file name -> its rank in expansion order
    # (first-seen position among `program.statements`), for `result_lines`' sort -- see there.
    stmt_lines: dict = field(default_factory=dict)  # file name -> sorted [(line, expansion index)]
    implementation_exceptions: list[str] = field(default_factory=list)
    program_ast: S.Program | None = None  # the parsed program (None when parsing failed)
    pass1_errors: bool | None = None  # whether the checker found pass-1 errors (the gate `-t` follows)
    findings: list = field(default_factory=list)  # opencrbasic checks (`checks.analyze`), never
    suppressed: list = field(default_factory=list)  # in `diagnostics`: `ok` ignores both

    def record_implementation_failure(self, where: str, exc: Exception) -> None:
        """Make an unexpected implementation error visible as a failed compilation.

        This is deliberately a project marker, not a claimed CRBasic diagnostic.  A swallowed
        checker exception must never let the CLI report a successful compile or write success-only
        artifacts.
        """
        self.implementation_exceptions.append(f"{where}: {type(exc).__name__}: {exc}")
        if not any(d.message == _IMPLEMENTATION_FAILURE_MESSAGE for d in self.diagnostics.errors):
            self.diagnostics.error(0, _IMPLEMENTATION_FAILURE_MESSAGE)

    def _position(self, d) -> tuple:
        """Where diagnostic `d` falls in the expanded source: the expansion index of the first
        statement of its file at or after its line (the exe prints in one top-to-bottom pass over
        the expanded program, so an included file's messages come where its `Include` is, not
        grouped by file -- oracle: `TDR_tester.CR6` on CR1000X prints `AcclimaTDRLib.crb`'s lines
        2 and 58 before the main file's line 45). A diagnostic without a file is the main file's
        (`No BeginProg`, the unclosed-block messages)."""
        file = d.file or self.program
        lines = self.stmt_lines.get(file)
        if not lines:
            return (self.file_order.get(file, 0), d.line)
        i = bisect.bisect_left(lines, (d.line, -1))
        index = lines[i][1] if i < len(lines) else lines[-1][1] + 1
        return (index, d.line)

    @property
    def ok(self) -> bool:
        return not self.diagnostics.errors and not self.implementation_exceptions

    def _sort_key(self, d) -> tuple:
        if not d.line:
            return (True, _no_line_kind(d), 0)
        if (
            self.profile.include_warning_first
            and (d.file or self.program) == self.program
            and d.severity == "warning"
            and d.message.startswith("Cannot open include file ")
        ):
            # reader pass: before any other message of the main file (`docs/checker.md`)
            return (False, (-1, d.line), 0)
        if (
            d.severity == "warning"
            and d.message == "Parameter will truncate to the nearest integer."
            and not self.diagnostics.errors
        ):
            # parser warning of a compile with no errors: before every later-pass warning
            # (reachability, fnotch), whatever its line (oracle, round 17, `docs/instructions.md`)
            return (False, (-1, d.line), 0)
        return (
            False,
            self._position(d),
            d.message.endswith("never reached at run time.") * 2
            or d.message.endswith("illegally occurs before BeginProg."),
        )

    def result_lines(self, show_signature: bool = False) -> list[str]:
        """The lines CR6Comp prints to stdout (and the message file) for this result."""
        # CR6Comp is a single top-to-bottom pass over the file, so its own diagnostics -- errors
        # *and* the small set of ungated warnings (`checker/warnings.py`'s `check_early`:
        # reachability/background-calibration/fnotch, oracle-confirmed to print interleaved with
        # errors by line even on an otherwise-failed compile, `docs/instructions.md`) -- are always
        # in ascending line order together. A warning with no line (the pre-existing "table
        # declared but never called" check, `diags.warning(0, ...)`) has no fixed position in the
        # real output either, but every oracle sample found it printed *after* every numbered line,
        # so `d.line == 0` sorts last (stable, so several such warnings keep their own relative
        # order) instead of first, which a plain ascending sort would otherwise do since 0 is the
        # smallest line number. `self.diagnostics.items` is insertion order, which several
        # independent checker passes each contribute to in their own statement-list order
        # (`checker/__init__.py`) -- individually ascending, but not necessarily interleaved
        # correctly against each other or against warnings, so this stable sort (never observed out
        # of order on the same line, so ties keep each check's own relative order) reproduces the
        # single-pass compiler's output order without each checker pass needing to coordinate.
        #
        # That per-line stable sort is only correct *within one file*: `Include`/`IncludeSection`
        # give each included file its own independent line numbering (this module's own docstring,
        # `source.py`), so a raw ascending sort across files interleaves them by coincidence, not by
        # the real single top-to-bottom pass over the *expanded* source (oracle-confirmed,
        # `checker/unused.py`'s corpus run: `SbdPayload_tester.CR6`'s `-u` warnings come out grouped
        # by included library file, in `Include` order, each *group* internally ascending by that
        # file's own line numbers -- not a single global ascending run). `self.file_order` (built
        # below from `program.statements`' own `.file` field, in expansion order) is the primary
        # sort key for exactly this reason; it is `{}` for a single-file program, where every
        # diagnostic's `.file` maps to the same missing-key default and this reduces to the
        # pre-existing ascending-line-only behaviour unchanged.
        #
        # Among no-line (`d.line == 0`) items specifically, file order does not apply (decompiled,
        # `FUN_004eab30`: the `-u` check (`thunk_FUN_004ba180`) runs once, early, well before the
        # later per-table finalisation loop that detects and prints "table declared but never
        # called" -- oracle-confirmed on the corpus, `checker/unused.py`'s docstring: every
        # no-line `-u` warning, regardless of which included file declared it, prints before every
        # "table declared but never called" warning). `_no_line_kind` below reproduces that
        # ordering by message shape rather than by which module raised it, so this stays correct
        # without `checker/warnings.py` needing to tag its own
        # diagnostics; a stable sort keeps each kind's own relative (insertion) order within itself.
        # On one line the reachability warning follows that line's errors (oracle: 15 of 15 corpus
        # and fuzz transcripts); other warnings keep their check order.
        ordered = sorted(self.diagnostics.items, key=self._sort_key)
        drive = self.profile.include_drive_prefix
        if not self.ok:
            rendered = _bounded_by_message_buffer([d.cr6comp(self.program, drive) for d in ordered])
            if rendered:
                # a message that echoes its source line keeps the newline (`Illegally dimension:`);
                # the real output is right-trimmed
                rendered[-1] = rendered[-1].rstrip("\n")
            return [f"{self.program} -- Compile Failed!", ""] + rendered
        if self.profile.reports_mode:
            lines = [f"{self.program} -- Compiled in {self.mode or 'Sequential'}Mode."]
        else:
            lines = [f"{self.program} -- Compiled OK."]
        if show_signature and self.signature is not None:
            lines.append(f"ProgSignature =  {self.signature}.")
        lines += [d.cr6comp(self.program, drive) for d in ordered if d.severity == "warning"]
        return lines


def dropped_message(result: CompileResult):
    """The first error the 4096-byte message buffer cannot hold, or None. The reference stops
    compiling there, and `-p`/`-i` output ends at its line (artifact matrix, round 74,
    `docs/artifacts.md`)."""
    if result.ok:
        return None
    ordered = sorted(result.diagnostics.items, key=result._sort_key)
    kept = len(
        _bounded_by_message_buffer([d.cr6comp(result.program, result.profile.include_drive_prefix) for d in ordered])
    )
    return ordered[kept] if kept < len(ordered) else None


def compile_file(
    path: Path,
    profile: Profile | None = None,
    display_name: str | None = None,
    warn_unused: bool = False,
    source: SourceProvider | None = None,
    signature: bool = True,
    provenance: bool = True,
    macros: bool = False,
) -> CompileResult:
    """`provenance=False` (the CLI and the tools) skips the position data only the language server
    reads (`LogicalLine.pieces`, `Program.line_map`, `Stmt.origin`); the compile output is unchanged.
    `macros=True` (the language server) expands the entry file's `#Define`/`#Macro` macros
    (`macros.py`), which the reference rejects.

    Never lets an unexpected exception escape (`docs/checker.md` "Robustness"): the real
    compiler never crashes on a syntactically-loadable file, so an internal bug in this project
    should surface as a rejected program, not a Python traceback that takes down whatever called
    this (`tools/fuzz.py`'s own worker pool did exactly that before it grew its own timeout guard,
    `docs/fuzz-findings.md` "Hang"). No oracle text can ever exist for "opencrbasic itself broke",
    so the fallback message here is a generic marker, never a claim about what CR6Comp.exe itself
    would say -- `checker.check`'s own finer-grained per-pass wrapping is what actually keeps
    *most* internal bugs from reaching here at all.
    """
    profile = profile or profiles.for_file(path)
    name = display_name or path.name
    result = _compile_once(path, profile, name, None, source, provenance, macros)
    if profile.single_pass:
        # CR300/CR350 stop pulling a `Public`/`Dim` initializer's next line once any error has been
        # reported ahead of the declaration (oracle, 2026-10-10, `docs/checker.md` "Round 8"): re-run
        # with the pulls from the first affected declaration on disabled.
        limit = None
        for _ in range(16):
            log = getattr(result.program_ast, "pull_log", None) or []
            site = getattr(result.program_ast, "pull_site", {})
            errs = [
                (*site.get(d.file, ()), d.line)
                for d in result.diagnostics.items
                if d.severity == "error" and d.line and not d.message.endswith("illegally occurs before BeginProg.")
            ]
            bad = next((start for start, end in log if any(e < end for e in errs)), None)
            if bad is None or (limit is not None and bad >= limit):
                break
            limit = bad
            result = _compile_once(path, profile, name, limit, source, provenance, macros)
    if result.program_ast is None:
        return result
    return _finish(result, path, warn_unused, source, signature)


def _compile_once(
    path: Path,
    profile: Profile,
    name: str,
    pull_limit: tuple[int, ...] | None,
    source: SourceProvider | None = None,
    provenance: bool = True,
    macros: bool = False,
):
    from . import checker, parser

    try:
        program, diags = parser.parse_file(
            path, profile=profile, pull_limit=pull_limit, source=source, provenance=provenance, macros=macros
        )
    except Exception as exc:
        diags = Diagnostics()
        diags.error(0, "Internal error: unable to parse program.")
        result = CompileResult(name, profile, diags)
        result.implementation_exceptions.append(f"parser.parse_file: {type(exc).__name__}: {exc}")
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise
        return result
    file_order: dict[str, int] = {}
    stmt_lines: dict[str, list[tuple[int, int]]] = {}
    for index, stmt in enumerate(program.statements):
        file = getattr(stmt, "file", "") or name
        file_order.setdefault(file, len(file_order))
        stmt_lines.setdefault(file, []).append((stmt.line, index))
    for lines in stmt_lines.values():
        lines.sort()
    result = CompileResult(name, profile, diags, file_order=file_order, stmt_lines=stmt_lines, program_ast=program)
    if len(path.name) + 1 > 60:
        diags.error(0, f"Program name exceeds maximum allowable characters by {len(path.name) - 59}.")
    try:
        checker.check(program, profile, diags, result)
    except Exception as exc:
        result.record_implementation_failure("checker.check", exc)
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise
    if program.unclosed_if and not diags.errors:
        # the message is printed alone: with errors the real compilers differ in which messages
        # win (`docs/checker.md` "Unclosed #If"), so only the error-free file is modelled; warnings
        # do not show (oracle, all four loggers, 2026-10-12, "Round 13")
        diags.items[:] = []
        diags.error(0, "#If with no #Endif")
    return result


def _finish(
    result: CompileResult, path: Path, warn_unused: bool, source: SourceProvider | None = None, signature: bool = True
) -> CompileResult:
    program, diags = result.program_ast, result.diagnostics
    if warn_unused and program is not None and not diags.errors:
        # `-u` (`docs/cli.md` "-u", `checker/unused.py`): oracle-confirmed to add nothing at all
        # once any error is present anywhere in the file, so this only ever runs on an otherwise-
        # clean pass -- matching the ordinary checker's own post-check tier (`checker/__init__.py`:
        # `tables_hook.attach`/`warnings.check`/`instr_data.check_duplicate_fields` are all gated
        # the same way). A crash here must not take down an otherwise-successful compile any more
        # than a checker pass's own crash does (`checker/__init__.py`'s `_run_pass`).
        try:
            from .checker import unused

            unused.check(program, diags)
        except Exception as exc:
            result.record_implementation_failure("checker.unused.check", exc)
            if os.environ.get("OPENCRBASIC_STRICT") == "1":
                raise
    if not signature:
        return result
    from .signature import program_signature

    try:
        result.signature = program_signature(path, source)
    except Exception as exc:
        result.record_implementation_failure("signature.program_signature", exc)
        result.signature = None
        if os.environ.get("OPENCRBASIC_STRICT") == "1":
            raise
    return result
