"""Diagnostics in CR6Comp.exe's format.

CR6Comp prints errors as `line N: <message>` and warnings as `Warning: <message>`; the message
texts mirror the exe's format strings (docs/messages.tsv). Line numbers are per-file, not
translated to the site of the `Include`/`IncludeSection` call that pulled the code in
(`source.py`'s own docstring, oracle-confirmed).

**File-origin prefix** (`docs/checker.md` "Errors from an included file" and "Prefix by message
kind in an included file"): an *error* whose statement came from a file other than the main program
is printed as `<file> line N: <message>`, the included file's own name in angle brackets before the
usual `line N:`, when the real compiler emits it while reading the file. Messages it emits after the
file was closed carry none: `_DEFERRED` below (message families) and `Diagnostic.show_file` (call
sites: False never, "cr3xx" only on CR300/CR350).
Warnings are untested against this rule and are left unprefixed (`cr6comp` only applies it to
errors) rather than guessed at.
"""

import re
from dataclasses import dataclass, field

from .provenance import LoadProvenance

_DRIVE_RE = re.compile(r"^(CPU|USR|CRD|USB):", re.IGNORECASE)

# Messages the real compiler reports after the included file was closed (block pairing, placement
# before `BeginProg`, table/scan/array checks run after the read loop): they never carry the
# `<file>` prefix when the file has no `BeginProg` of its own (and print a garbled name when it
# has, not modelled). Observed per logger family on corpus programs split around `BeginProg`
# (`docs/checker.md` "Prefix by message kind"); a family with any prefixed observation is absent.
_DEFERRED_COMMON = (
    r".+ illegally occurs before BeginProg\.$", r"Could not find ", r"Cannot find DataTable",
    r"Cannot autoallocate memory for DataTable", r"Duplicate Sub ", r"EndTable without DataTable",
    r"Table \S+ declared with no output fields", r"Table with DataInterval must be called",
    r"Sample must be used inside a table", r"Illegal Matrix", r"END IF without IF",
    r"Else with no If", r"LOOP without DO", r"EndSub without Sub", r"NEXT without FOR", r"NextScan with no Scan",
    r"Scan without NextScan", r"Missing NextScan", r"FOR NEXT statement illegally has",
    r"IF or SELECT statement illegally has", r"LOOP statement illegally has", r"Variable .* out of bounds",
    r"FileManage ", r"Out of memory\.", r"Illegal Semaphore",
)
_DEFERRED_PATTERNS = {
    False: _DEFERRED_COMMON + (  # CR6 / CR1000X
        r"Case with no Select", r"Next SubScan without SubScan", r"Delay not allowed outside",
        r"Scan \S+ invalid", r"Scan interval too short", r"Scan (Interval|BufferOption|Count) parameter",
        r"Slow Scan interval", r"MenuRecompile must be used", r"MenuPick must", r"EndMenu must be used",
        r"Invalid channel ", r"Invalid port ", r"Invalid reps ", r"Invalid SDM Address", r"Illegal table name",
        r"Table name .* illegal", r"DataTable name .* too long", r"DNP Variable must be",
        r"Can't assign units to", r"\S+ already assigned Units", r"PanelTemp channel out of range",
        r"Input channel", r"Reps cycle input", r"SPIOpen must precede", r"Illegal PakBus address",
        r"Insufficient ", r"CPI Address must", r"Bridge parameter must", r"Optional Count parameter",
        r"Source and Dest types", r"Unable to find Dest", r"multiply result illegal",
        r"Destination variable must", r"Legal configuration is", r"Sig\d+ must be of length",
        r"Number of bytes ", r"number of averages ", r"SampleFieldCal with no FieldCal",
        r"CDM_PulsePort instruction not supported", r"CH\d+Status update interval",
        r".+ is not a Settings Field Name", r"GoesTable not active", r"Missing DialSequence",
        r"Boolean picklist must", r"\S+ must be declared as a integer Const",
        r"If source and destination are of type STRING", r"DataInterval TintoInt",
        r"Multiple BeginProgs", r"Sub .* EndSub not matched", r"IF or SELECT without EndIf",
    ),
    True: _DEFERRED_COMMON + (  # CR300 / CR350
        r"FOR without NEXT", r"Scans > ", r"DNP\d+Statics and/or", r"Destination variable cannot",
        r"Illegal option", r"Illegal dimension", r"Illegal message type", r"Only one DataInterval allowed",
        r"Settling time ", r"\S+ must be of length", r"Invalid drive or no drive", r"Invalid file type option",
        r"\S+ is not a Status or Settings Field Name", r"DataTable name .* too long",
    ),
}
_DEFERRED = {k: re.compile("|".join(f"(?:{p})" for p in v)) for k, v in _DEFERRED_PATTERNS.items()}


@dataclass(frozen=True)
class Diagnostic:
    severity: str           # "error" or "warning"
    line: int               # 0 when the message has no line (e.g. "No BeginProg in Program.")
    message: str            # without the "line N: " / "Warning: " prefix
    file: str = ""          # originating file, for our own richer output
    show_file: bool | str = True  # whether an origin outside `main_file` gets a `<file>` prefix:
    # True always, False never, "cr3xx" only on CR300/CR350 (pass-2 on CR6/CR1000X, pass-1 there);
    # see this module's docstring.
    cascade: bool = True    # CR300/CR350: may start the `Too many parameters` cascade (`cr3xx`)
    trigger: bool = field(default=False, compare=False)  # starts it whatever the message (`cr3xx`)
    stmt: int = field(default=0, compare=False)  # id() of the statement that raised it, when tagged

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
        show = self.show_file
        if show == "cr3xx":
            show = drive_prefix
        if show and _DEFERRED[drive_prefix].match(self.message):
            show = False
        if show and name and (name.lower() != main_file.lower()
                                         or _DRIVE_RE.match(self.file)):
            # An included file prints as written in its `Include`: CR6/CR1000X without the drive
            # (`<lib.CRB>` for `"cpu:lib.CRB"`), CR300/CR350 with it, `cpu:` added when none was
            # written (`<cpu:Lib.crb>`) -- oracle, 2026-09-27, `docs/loggers.md`.
            if drive_prefix:
                name = self.file if _DRIVE_RE.match(self.file) else f"cpu:{self.file}"
            prefix = f"<{name}> "
        return f"{prefix}line {self.line}: {self.message}"


@dataclass
class Diagnostics(LoadProvenance):
    items: list[Diagnostic] = field(default_factory=list)
    cascade_gone: set[str] = field(default_factory=set)  # CR300/CR350 names a declaration cascade left unregistered

    def error(self, line: int, message: str, file: str = "", show_file: bool | str = True,
              stmt: object = None) -> None:
        self.items.append(Diagnostic("error", line, message, file, show_file, stmt=id(stmt) if stmt else 0))

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
