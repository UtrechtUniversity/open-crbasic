"""Data-table structure and output-processing placement checks (`PLAN.md`'s "data tables and
output processing" workstream). Two pass-2 rules (gated the same way as `tables_hook.attach`/
`warnings.check`, i.e. only when pass 1 found no error anywhere in the file -- oracle-confirmed for
both below, the same "two-pass compilation" architecture `docs/checker.md` documents for
`instructions.py`'s channel check):

- `check_table_structure` -- `%s must be used inside a table declaration.` for every output-
  processing instruction found (via the decompiled call sites of its shared validator function,
  `thunk_FUN_004ea5d0` on CR6 / `thunk_FUN_004ea5f0` on CR1000X) to require `DataTable...EndTable`
  placement, plus `EndTable without DataTable.` for an orphan/extra `EndTable`.
- `check_duplicate_fields` -- `Duplicate field name %s in DataTable %s.`, reusing the already-
  computed field names in `result.tables` (`tables.py`, a different workstream's module) rather
  than re-deriving each instruction's own output-field-naming rules (`_Avg`/`_Min`/... suffixes) a
  second time.

Full rules and oracle evidence: `docs/instructions.md` (new "Data tables and output processing"
section).
"""

from __future__ import annotations

from .. import syntax as S
from ..diagnostics import Diagnostics
from ..profiles import Profile

# Instructions the real compiler's shared "must be inside a DataTable" validator checks --
# `%s must be used inside a table declaration.` -- keyed by instruction-table keyword (upper case),
# valued by the *displayed* name in that message. Derived from the decompiled call sites of the
# shared validator function on both CR6 (`thunk_FUN_004ea5d0`) and CR1000X (`thunk_FUN_004ea5f0`,
# same call-site set, `/opt/re/out/decomp.c` and `decomp_cr1000x.c`, `docs/instructions.md`): every
# one of these instructions passes its own name (read directly out of the compiled string table at
# its call site, `pefile.get_string_at_rva`) as the validator's argument, except `Maximum`/
# `Minimum`, whose call sites pass the unlabelled string constants `"Max"`/`"Min"` instead (byte-
# confirmed the same way) -- both independently oracle-confirmed (`docs/instructions.md`; the `Min`
# spelling is also what a saved fuzz reproducer, `tests/corpus/fuzz/cr6/false_accept/
# 5b5ae86fd087.json`, shows for a bare `Minimum(...)`). Every other entry below with a **checked with
# the oracle** note was independently confirmed with a from-scratch scratch probe against
# `CR6Comp.exe` (and, for one representative case, all three other loggers, which gave byte-
# identical text); the rest are the decompiled call sites alone (an "official repository reference",
# AGENTS.md's evidence tier 3) and are *not* independently oracle-confirmed this round -- see
# `docs/instructions.md`'s "not independently oracle-confirmed" list for exactly which those are.
# One more call site exists (the unlabelled string `"Et"`, near the `SampleFieldCal` call site)
# that could not be attributed to a specific instruction with confidence and is deliberately left
# out -- AGENTS.md: never guess into a false reject. The other originally-unlabelled string this
# round found nearby, `"FFT"`, *was* independently confirmed (a fresh scratch probe, `tfft.cr6`,
# and two CHM corpus files whose `DataTable` bodies contain only `FFT(...)` calls, `fft_1.CR6`/
# `covspa_1.CR6` -- both were a **false-reject regression** this round's own full difftest re-run
# caught, from `check_table_structure`'s sibling `Table %s declared with no output fields.` rule
# below not yet recognising `FFT` as a field producer, before `FFT` was added here too) and is
# included as `FFT`.
TABLE_ONLY_INSTRUCTIONS: dict[str, str] = {
    "CARDOUT": "CardOut",              # checked with the oracle
    "MQTTPUBLISHTABLE": "MQTTPublishTable",
    "GOESTABLE": "GoesTable",
    "TABLEFILE": "TableFile",          # checked with the oracle
    "TOTALIZE": "Totalize",            # checked with the oracle
    "AVERAGE": "Average",              # checked with the oracle (also a saved fuzz reproducer)
    "MAXIMUM": "Max",                  # checked with the oracle
    "MINIMUM": "Min",                  # a saved fuzz reproducer (5b5ae86fd087.json); not
    # independently re-confirmed with a fresh scratch probe this round
    "SAMPLEMAXMIN": "SampleMaxMin",    # checked with the oracle
    "STDDEV": "StdDev",                # checked with the oracle
    "MOMENT": "Moment",
    "SAMPLE": "Sample",                # checked with the oracle (also a saved fuzz reproducer, on
    # all four loggers)
    "HISTOGRAM": "Histogram",          # checked with the oracle
    "HISTOGRAM4D": "Histogram4D",
    "COVARIANCE": "Covariance",
    "RAINFLOW": "RainFlow",            # checked with the oracle
    "LEVELCROSSING": "LevelCrossing",  # checked with the oracle
    "WINDVECTOR": "WindVector",        # checked with the oracle
    "MEDIAN": "Median",                # checked with the oracle
    "SAMPLEFIELDCAL": "SampleFieldCal",
    "FILLSTOP": "FillStop",            # checked with the oracle
    "DATATIME": "DataTime",            # checked with the oracle
    "OPENINTERVAL": "OpenInterval",    # checked with the oracle
    "DATAINTERVAL": "DataInterval",    # a saved fuzz reproducer (1ded68266aec.json/5b5ae86fd087.json)
    "FFT": "FFT",                      # checked with the oracle (also two saved CHM corpus files,
    # `fft_1.CR6`/`covspa_1.CR6`, whose `DataTable` bodies contain only `FFT(...)` calls)
    "DATAEVENT": "DataEvent",          # decompiled call site only -- a from-scratch probe placing
    # a bare `DataEvent(...)` outside any table crashes the real compiler under Wine rather than
    # printing this text (a real-compiler-side crash, the same class `docs/checker.md`'s
    # `pointervariable_2.CR6` entry already documents as unmatchable; not chased further, since our
    # own clean rejection here is strictly better behaviour than reproducing a crash), so this entry
    # is *not* oracle-confirmed by its own message text, only by the decompiled call site plus the
    # fact that every corpus/fuzz use of `DataEvent` this project has seen is always correctly
    # placed inside a table.
    "TABLEHIDE": "TableHide",          # checked with the oracle
}

# `FieldNames` was checked directly (a bare `FieldNames("X")` with no enclosing `DataTable` at all)
# and found to compile clean -- the real compiler does *not* route it through this same validator,
# unlike every instruction above -- so it is deliberately left out despite appearing in the task's
# own illustrative instruction list; see `docs/instructions.md`.

# The subset of `TABLE_ONLY_INSTRUCTIONS` that actually *produces* an output field, for
# `Table %s declared with no output fields.` (oracle-confirmed at the `EndTable` line, `docs/
# instructions.md`: a `DataTable` body containing only a configuration instruction -- `DataInterval`
# alone, or `CardOut` alone, both from-scratch probes this round -- or nothing at all still gets
# this error). Deliberately excludes every purely-configuration instruction in
# `TABLE_ONLY_INSTRUCTIONS` (`DataInterval`, `CardOut`, `TableFile`, `DataEvent`, `FillStop`,
# `DataTime`, `OpenInterval`, `TableHide`) rather than reusing `result.tables`' already-computed
# field *count* the way `check_duplicate_fields` does: `tables.py`'s own `_emit_output_fields` does
# not yet derive fields for several instructions this project already knows are real field
# producers (`RainFlow`/`LevelCrossing`/`Histogram4D`/`SampleFieldCal`, `docs/tdf-rules.md`'s open
# gaps) -- reusing its field count here would misreport a table containing *only* one of those as
# "no output fields" too, a false reject this purely syntactic, tables.py-independent set avoids by
# construction. `GoesTable`/`MQTTPublishTable` are included leniently, not from oracle confirmation
# that either alone (with no `Sample`/`GOESField` alongside it, an unseen real-world shape) counts as
# a field producer by itself: if that lenient guess is ever wrong, the failure mode is under-
# reporting (a table this rule should flag but doesn't), never a false reject.
_FIELD_PRODUCING_INSTRUCTIONS = frozenset({
    "SAMPLE", "AVERAGE", "TOTALIZE", "STDDEV", "MAXIMUM", "MINIMUM", "MEDIAN", "MOMENT",
    "SAMPLEMAXMIN", "COVARIANCE", "WINDVECTOR", "HISTOGRAM", "HISTOGRAM4D", "RAINFLOW",
    "LEVELCROSSING", "SAMPLEFIELDCAL", "GOESTABLE", "MQTTPUBLISHTABLE", "FFT",
})


def _table_name(stmt: S.Instruction) -> str | None:
    if stmt.args and isinstance(stmt.args[0], S.Name):
        return stmt.args[0].name
    return None


def check_table_structure(program: S.Program, profile: Profile, diags: Diagnostics) -> None:
    """`%s must be used inside a table declaration.` for an output-processing instruction (see
    `TABLE_ONLY_INSTRUCTIONS`) found outside any `DataTable...EndTable` block,
    `EndTable without DataTable.` for an orphan/extra `EndTable` (oracle-confirmed at the `EndTable`
    statement's own line: a bare `EndTable` with no `DataTable` anywhere before it, and a second,
    extra `EndTable` right after a properly-closed table, both give this message -- `docs/
    instructions.md`), and `Table %s declared with no output fields.` for a properly-closed
    `DataTable` block whose body has no instruction in `_FIELD_PRODUCING_INSTRUCTIONS` (oracle-
    confirmed at the `EndTable` line: a body containing only `DataInterval`, only `CardOut`, or
    nothing at all, three from-scratch probes this round, plus a saved fuzz reproducer,
    `tests/corpus/fuzz/cr6/false_accept/5d5ffec92ac4.json`, for the "nothing at all" shape -- `docs/
    instructions.md`).

    Pass-2 (called from `checker/__init__.py` only when pass 1 found no error anywhere in the
    file): oracle-confirmed for both messages this round by placing an unrelated pass-1 error (a
    truncated instruction call) elsewhere in a file that also has a misplaced `Sample`/orphan
    `EndTable` -- neither message appears, only the pass-1 error (`docs/instructions.md`).

    Deliberately simple state tracking (a single "currently inside a `DataTable`" flag toggled by
    `DataTable`/`EndTable`, no depth stack): CRBasic `DataTable` blocks do not nest (matching
    `tables.py`'s own `_scan_program` docstring, "DataTable and ConstTable bodies do not nest"), so
    this is exact for every well-formed program; it only degrades gracefully (never a false reject)
    on an already-malformed one, e.g. an unclosed `DataTable` -- oracle-confirmed separately that
    this shape gets a *different*, unimplemented message (`Could not find DataTable %s.`, at the
    table's first use) rather than an "EndTable"/"not matched" pairing error, so no attempt is made
    to detect it here (`docs/instructions.md`).
    """
    in_table = False
    table_name = ""
    has_field = False
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "DATATABLE":
            in_table = True
            has_field = False
            table_name = _table_name(stmt) or ""
            continue
        if kw == "ENDTABLE":
            if not in_table:
                diags.error(stmt.line, "EndTable without DataTable.", stmt.file)
            elif not has_field and table_name:
                diags.error(stmt.line, f"Table {table_name} declared with no output fields.", stmt.file)
            in_table = False
            continue
        if in_table:
            if kw in _FIELD_PRODUCING_INSTRUCTIONS:
                has_field = True
            continue
        display = TABLE_ONLY_INSTRUCTIONS.get(kw)
        if display is not None and kw in profile.instructions:
            diags.error(stmt.line, f"{display} must be used inside a table declaration.", stmt.file)


def check_duplicate_fields(program: S.Program, result, diags: Diagnostics) -> None:
    """`Duplicate field name %s in DataTable %s.` (no line number -- oracle-confirmed, same as the
    pre-existing "table declared but never called" warning that shares its emitting function,
    `docs/checker.md`'s `FUN_004eab30`): fires once for every field beyond the first that shares its
    already-computed output-field name with an earlier field in the same table (oracle-confirmed
    shape, both from three saved fuzz reproducers -- `0004dc1d7a39.json`/`7076907a77b3.json`/
    `d4b49fb47507.json`, each a `duplicate_line` mutant that repeats one `Sample`/`Minimum` call
    verbatim -- and from the CHM corpus's own `ch400status_2.CR6`: exactly one diagnostic per
    repeated name, not one per pair).

    Deliberately reuses `result.tables` (`tables.py`, a concurrently-owned module, wired in by
    `tables_hook.attach` earlier in the same pass-2 block) instead of re-deriving each output
    instruction's own field-naming rule (`_Avg`/`_Min`/`_TMx`/... suffixes, `FieldNames` renaming,
    reps-array expansion) a second time here -- both for `AGENTS.md`'s "do not copy a fact into
    several homes" and because it is safely *conservative* for the gap already documented there
    (an instrument-specific structured measurement `tables.py` does not yet expand per-element,
    `docs/checker.md`'s open gaps; `ch400status_2.CR6`'s own `CH400Status` reps-cycling shape is
    exactly this) -- that shows up as a single field, never as several colliding names, so
    under-reporting is the only risk there.

    **Found as a regression during this round's own development, fixed here**: `tables.py`'s
    `_source_name` has a *different* gap that is not safely conservative -- it returns the fixed
    sentinel `"Anonymous"` (never a real field name; not `tables.py`'s concern, since the TDF/`-l`
    output this project has actually verified byte-identical never exercises this path) for any
    output-instruction source that is not a plain `Name`/`Index` expression, e.g. a `Table.Field`
    cross-table reference (`HR_DATA.battery_voltage_Avg`) or a string literal (`Sample(1,"Forced
    RT: ",String)`, both real CHM corpus shapes, `docs/instructions.md`). Two or more such sources
    in the same table collide on this one shared sentinel and were flagged as duplicates of *each
    other* here even though the real compiler accepts the file cleanly (`tests/corpus/chm/
    goestable_1.CR6`, oracle-confirmed accepted on all four loggers, found as a false reject this
    round's own difftest run caught before this fix landed) -- so every field named exactly
    `"Anonymous"` is excluded from this check entirely, on both sides of the comparison: it is
    never recorded as "seen" and never itself flagged, since a name this project already knows is
    not a genuine derived name is not evidence of a genuine collision either way.
    """
    tables = getattr(result, "tables", None)
    if not tables:
        return
    user_tables: set[str] = set()
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "DATATABLE":
            name = _table_name(stmt)
            if name:
                user_tables.add(name.upper())
    if not user_tables:
        return
    by_upper = {t.name.upper(): t for t in tables}
    for key in user_tables:
        table = by_upper.get(key)
        if table is None:
            continue
        seen: set[str] = set()
        for f in table.fields:
            if f.name == "Anonymous":
                continue
            if f.name in seen:
                diags.error(0, f"Duplicate field name {f.name} in DataTable {table.name}.")
            else:
                seen.add(f.name)
