"""Diagnostics in CR6Comp.exe's format.

CR6Comp prints errors as `line N: <message>` and warnings as `Warning: <message>`; the message
texts mirror the exe's format strings (docs/messages.tsv). Line numbers are per-file, not
translated to the site of the `Include`/`IncludeSection` call that pulled the code in
(`source.py`'s own docstring, oracle-confirmed).

**File-origin prefix** (`docs/checker.md` "Errors from an included file" and "Prefix by message
kind in an included file"): an *error* whose statement came from a file other than the main program
is printed as `<file> line N: <message>`, the included file's own name in angle brackets before the
usual `line N:`, when the real compiler emits it while reading the file. Messages it emits after the
file was closed carry none: `_DEFERRED_KINDS` below (properties of `Diagnostic.kind`) and
`Diagnostic.show_file` (call sites: False never, "cr3xx" only on CR300/CR350).
Warnings are untested against this rule and are left unprefixed (`cr6comp` only applies it to
errors) rather than guessed at.
"""

import re
from contextvars import ContextVar
from dataclasses import dataclass, field

from .provenance import LoadProvenance

_DRIVE_RE = re.compile(r"^(CPU|USR|CRD|USB):", re.IGNORECASE)

_ACTIVE_POSITIONS: ContextVar[dict[tuple[str, int], int] | None] = ContextVar(
    "opencrbasic_diag_positions", default=None
)
_ACTIVE_PHASE: ContextVar[str] = ContextVar("opencrbasic_diag_phase", default="")

INVALID_PORT_KINDS = frozenset({"invalid_port", "invalid_port_comma"})
TABLE_NAME_KINDS = frozenset({"table_name_too_long", "table_name_number", "table_name_illegal"})
PLACEMENT_MENU_KINDS = frozenset({"placement_menu", "placement_menu_recompile"})
COULD_NOT_FIND_KINDS = frozenset({"could_not_find", "could_not_find_table", "cannot_find_table"})

# Message families matched by prefix/regex -> canonical `Diagnostic.kind`. Order matters where a
# narrower rule precedes a broader one (e.g. `could_not_find_table` before `could_not_find`,
# `sig_length` before `must_be_length`). Every pattern previously in `_DEFERRED_PATTERNS` maps to
# a distinct `kind` in `_DEFERRED_COMMON_KINDS` / `_DEFERRED_CR6_KINDS` / `_DEFERRED_CR3XX_KINDS`.
_DEFERRED_COMMON_SPECS: tuple[tuple[str, str], ...] = (
    ("before_beginprog", r".+ illegally occurs before BeginProg\.$"),
    ("could_not_find_table", r"Could not find DataTable "),
    ("could_not_find", r"Could not find "),
    ("cannot_find_table", r"Cannot find DataTable"),
    ("cannot_autoallocate_table", r"Cannot autoallocate memory for DataTable"),
    ("duplicate_sub", r"Duplicate Sub "),
    ("endtable_without_datatable", r"EndTable without DataTable"),
    ("empty_table", r"Table \S+ declared with no output fields"),
    ("table_interval_uncalled", r"Table with DataInterval must be called"),
    ("sample_outside_table", r"Sample must be used inside a table"),
    ("illegal_matrix", r"Illegal Matrix"),
    ("endif_without_if", r"END IF without IF"),
    ("else_without_if", r"Else with no If"),
    ("loop_without_do", r"LOOP without DO"),
    ("endsub_without_sub", r"EndSub without Sub"),
    ("next_without_for", r"NEXT without FOR"),
    ("nextscan_without_scan", r"NextScan with no Scan"),
    ("nested_scan", r"Scan without NextScan or nested Scans\.$"),
    ("scan_without_nextscan", r"Scan without NextScan"),
    ("missing_nextscan", r"Missing NextScan"),
    ("for_next_illegal", r"FOR NEXT statement illegally has"),
    ("if_select_illegal", r"IF or SELECT statement illegally has"),
    ("loop_illegal", r"LOOP statement illegally has"),
    ("out_of_bounds", r"Variable .* out of bounds"),
    ("file_manage", r"FileManage "),
    ("out_of_memory", r"Out of memory\."),
    ("illegal_semaphore", r"Illegal Semaphore"),
)

_DEFERRED_CR6_SPECS: tuple[tuple[str, str], ...] = (
    ("case_without_select", r"Case with no Select"),
    ("next_subscan_without_subscan", r"Next SubScan without SubScan"),
    ("delay_outside_scan", r"Delay not allowed outside"),
    ("scan_invalid", r"Scan \S+ invalid"),
    ("scan_interval_short", r"Scan interval too short"),
    ("scan_param", r"Scan (Interval|BufferOption|Count) parameter"),
    ("slow_scan_interval", r"Slow Scan interval"),
    ("placement_menu_recompile", r"MenuRecompile must be used"),
    ("menu_pick", r"MenuPick must"),
    ("endmenu_must_be_used", r"EndMenu must be used"),
    ("invalid_channel", r"Invalid channel "),
    ("invalid_port", r"Invalid port "),
    ("invalid_reps", r"Invalid reps "),
    ("invalid_sdm_address", r"Invalid SDM Address"),
    ("illegal_table_name", r"Illegal table name"),
    ("table_name_number", r"Table name .* illegal"),
    ("table_name_too_long", r"DataTable name .* too long"),
    ("dnp_variable", r"DNP Variable must be"),
    ("cant_assign_units", r"Can't assign units to"),
    ("already_assigned_units", r"\S+ already assigned Units"),
    ("paneltemp_channel", r"PanelTemp channel out of range"),
    ("input_channel", r"Input channel"),
    ("reps_cycle_input", r"Reps cycle input"),
    ("spiopen_precede", r"SPIOpen must precede"),
    ("illegal_pakbus_address", r"Illegal PakBus address"),
    ("insufficient", r"Insufficient "),
    ("cpi_address", r"CPI Address must"),
    ("bridge_param", r"Bridge parameter must"),
    ("optional_count_param", r"Optional Count parameter"),
    ("source_dest_types", r"Source and Dest types"),
    ("unable_to_find_dest", r"Unable to find Dest"),
    ("multiply_result_illegal", r"multiply result illegal"),
    ("destination_var_must", r"Destination variable must"),
    ("legal_configuration", r"Legal configuration is"),
    ("sig_length", r"Sig\d+ must be of length"),
    ("number_of_bytes", r"Number of bytes "),
    ("number_of_averages", r"number of averages "),
    ("samplefieldcal_without_fieldcal", r"SampleFieldCal with no FieldCal"),
    ("cdm_pulseport_unsupported", r"CDM_PulsePort instruction not supported"),
    ("ch_status_interval", r"CH\d+Status update interval"),
    ("not_settings_field", r".+ is not a Settings Field Name"),
    ("goestable_not_active", r"GoesTable not active"),
    ("missing_dialsequence", r"Missing DialSequence"),
    ("boolean_picklist", r"Boolean picklist must"),
    ("must_be_int_const", r"\S+ must be declared as a integer Const"),
    ("string_source_dest_same", r"If source and destination are of type STRING"),
    ("datainterval_non_negative", r"DataInterval TintoInt"),
    ("multiple_beginprogs", r"Multiple BeginProgs"),
    ("sub_endsub_unmatched", r"Sub .* EndSub not matched"),
    ("if_select_without_endif", r"IF or SELECT without EndIf"),
)

_DEFERRED_CR3XX_SPECS: tuple[tuple[str, str], ...] = (
    ("for_without_next", r"FOR without NEXT"),
    ("scans_gt", r"Scans > "),
    ("dnp_statics", r"DNP\d+Statics and/or"),
    ("destination_var_cannot", r"Destination variable cannot"),
    ("illegal_option", r"Illegal option"),
    ("illegal_dimension", r"Illegal dimension"),
    ("illegal_message_type", r"Illegal message type"),
    ("only_one_datainterval", r"Only one DataInterval allowed"),
    ("settling_time", r"Settling time "),
    ("must_be_length", r"\S+ must be of length"),
    ("invalid_drive", r"Invalid drive or no drive"),
    ("invalid_file_type", r"Invalid file type option"),
    ("not_status_settings_field", r"\S+ is not a Status or Settings Field Name"),
)

_OTHER_KIND_SPECS: tuple[tuple[str, str], ...] = (
    ("table_name_illegal", r"DataTable name .* is illegal\.$"),
    ("invalid_port_comma", r"Invalid port"),
    ("wind_subinterval", r"WindVector: non-zero SubInterval"),
    ("invalid_symbol", r"Invalid symbol in expression"),
    ("invalid_expression", r"Invalid, or out of place expression"),
    ("dimensions_not_declared", r"Dimensions not declared"),
    ("sdi12_conflict", r"SDI12"),
    ("fnotch", r"Invalid fnotch value"),
    ("cannot_open_include", r"Cannot open include file "),
    ("truncate_warning", r"Parameter will truncate to the nearest integer\.$"),
    ("placement_menu", r".+ must be used within DisplayMenu\.\.EndMenu\.$"),
    ("placement_scan", r".+ must be used within a Scan\.\.\.NextScan\.$"),
    ("unreached", r".+ never reached at run time\.$"),
    ("unused_variable", r".+ declared but not used\.$"),
    ("not_defined", r".+ is not defined\.$"),
)

_ALL_KIND_SPECS = _DEFERRED_COMMON_SPECS + _DEFERRED_CR6_SPECS + _DEFERRED_CR3XX_SPECS + _OTHER_KIND_SPECS
_KIND_RE = re.compile("|".join(f"(?P<{name}>{pattern})" for name, pattern in _ALL_KIND_SPECS))

_DEFERRED_COMMON_KINDS = frozenset(name for name, _ in _DEFERRED_COMMON_SPECS)
_DEFERRED_KINDS: dict[bool, frozenset[str]] = {
    False: _DEFERRED_COMMON_KINDS | frozenset(name for name, _ in _DEFERRED_CR6_SPECS),
    True: (
        _DEFERRED_COMMON_KINDS
        | frozenset(name for name, _ in _DEFERRED_CR3XX_SPECS)
        | frozenset({"sig_length", "table_name_too_long"})
    ),
}


def infer_kind(severity: str, message: str) -> str:
    """Infer the canonical `Diagnostic.kind` from a rendered message when none was passed."""
    if severity == "warning":
        if message.endswith(" never reached at run time."):
            return "unreached"
        if message.startswith("Invalid fnotch value"):
            return "fnotch"
        if message.startswith("Cannot open include file "):
            return "cannot_open_include"
        if message == "Parameter will truncate to the nearest integer.":
            return "truncate_warning"
        if message.endswith(" declared but not used."):
            return "unused_variable"
    match = _KIND_RE.match(message)
    return match.lastgroup or "" if match and match.lastgroup else ""


@dataclass(frozen=True)
class Diagnostic:
    severity: str  # "error" or "warning"
    line: int  # 0 when the message has no line (e.g. "No BeginProg in Program.")
    message: str  # without the "line N: " / "Warning: " prefix
    file: str = ""  # originating file, for our own richer output
    show_file: bool | str = True  # whether an origin outside `main_file` gets a `<file>` prefix:
    # True always, False never, "cr3xx" only on CR300/CR350 (pass-2 on CR6/CR1000X, pass-1 there);
    # see this module's docstring.
    stmt: int = field(default=0, compare=False)  # id() of the statement that raised it, when tagged
    kind: str = field(default="", compare=False)  # canonical message-family identifier
    phase: str = field(default="", compare=False)  # checker stage or aside bucket that emitted it
    pos: int = field(default=-1, compare=False)  # expanded-source statement index (-1 when unmapped)

    def __post_init__(self) -> None:
        if not self.kind:
            object.__setattr__(self, "kind", infer_kind(self.severity, self.message))
        if not self.phase and (active_phase := _ACTIVE_PHASE.get()):
            object.__setattr__(self, "phase", active_phase)
        if (positions := _ACTIVE_POSITIONS.get()) is not None:
            object.__setattr__(self, "pos", positions.get((self.file, self.line), -1))

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
        if show and self.kind in _DEFERRED_KINDS[drive_prefix]:
            show = False
        if show and name and (name.lower() != main_file.lower() or _DRIVE_RE.match(self.file)):
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
    asides: list[Diagnostic] = field(default_factory=list)  # findings gated/merged by checker resolve step

    def error(
        self,
        line: int,
        message: str,
        file: str = "",
        show_file: bool | str = True,
        stmt: object = None,
        *,
        kind: str = "",
        phase: str = "",
    ) -> None:
        self.items.append(
            Diagnostic("error", line, message, file, show_file, stmt=id(stmt) if stmt else 0, kind=kind, phase=phase)
        )

    def warning(self, line: int, message: str, file: str = "", *, kind: str = "", phase: str = "") -> None:
        self.items.append(Diagnostic("warning", line, message, file, kind=kind, phase=phase))

    def trim(self, cut: int | None) -> None:
        """Drop trailing diagnostics at or past `cut` (used for the `#...` cutoff)."""
        if cut is not None:
            del self.items[cut:]

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity == "warning"]

    def __bool__(self) -> bool:
        return bool(self.errors)
