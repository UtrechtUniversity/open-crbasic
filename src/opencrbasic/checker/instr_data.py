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

import dataclasses
import math
import re

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from ..profiles import Profile
from . import symbols

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
    "GOESFIELD": "GoesTable",          # oracle, all four loggers, 2026-10-12 (r16_goes_field)
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
    "ETSZ": "Et",                      # checked with the oracle, all four loggers (2026-10-12):
    "ETCLEARSKY": "Et",                # the unlabelled `"Et"` call site; docs/instructions.md
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
    "LEVELCROSSING", "SAMPLEFIELDCAL", "MQTTPUBLISHTABLE", "FFT",
    "ETSZ", "ETCLEARSKY",
})


def _table_name(stmt: S.Instruction) -> str | None:
    if stmt.args and isinstance(stmt.args[0], S.Name):
        return stmt.args[0].name
    return None


def calltable_targets(program: S.Program) -> set[str]:
    """Upper-case names every `CallTable <name>` statement in `program` refers to. Used by
    `structure.py`'s `check_pass1` to decide whether an unclosed `DataTable` at end-of-file gets
    the bare `DataTable ... EndTable not matched.` (nothing references it) or is left for
    `check_unclosed_calltable` below to report as `Could not find DataTable %s.` instead (it *is*
    referenced) -- see that function's own docstring for the oracle evidence distinguishing the
    two (round 6, `docs/checker.md`).
    """
    names: set[str] = set()
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "CALLTABLE":
            name = _table_name(stmt)
            if name:
                names.add(name.upper())
    return names


def unknown_calltables(program: S.Program):
    """`(CallTable statement, name)` pairs naming no closed table, for `check_unclosed_calltable`.

    `Could not find DataTable %s.` (round 6, `docs/checker.md`) at a `CallTable <name>` site
    whose own `DataTable(name,...)` was declared somewhere in the program but never closed with a
    matching `EndTable` -- an unclosed table never gets "finalised" into the real compiler's table
    registry, so a later reference to it fails exactly like a genuine typo would, oracle-confirmed
    (`docs/checker.md`): `DataTable (Test,True,-1)` with one real field (`Sample`), no `EndTable`
    anywhere in the file, `CallTable Test` later -> `Could not find DataTable Test.` at the
    `CallTable`'s own line (not the `DataTable`'s).

    **Tier B, not pass 1** (oracle-confirmed, a fresh sweep this round): unlike the sibling
    `DataTable ... EndTable not matched.` bare message (`structure.py`'s `check_pass1`, pass 1,
    suppressed by an earlier pass-1 error) this one accumulates freely with an unrelated
    `expressions.check`/`structure.check_pass2` finding elsewhere in the file (`Randomize
    (FooBar)` on an earlier line still gets its own `Invalid symbol in expression` *and* this
    message on the `CallTable` line, in that order) -- called from `checker/__init__.py`'s own
    tier-B block alongside those two, not from this module's existing (tier-C-gated)
    `check_table_structure`.

    Deliberately simple, matching `check_table_structure`'s own stated assumption that `DataTable`
    blocks never nest: a table name counts as "closed" once *any* `EndTable` follows *any* of its
    own `DataTable` declarations anywhere in the program (no attempt to pair a specific `EndTable`
    with a specific same-named `DataTable`, since two same-named `DataTable`s in one program is not
    a shape this project has evidence for either way).
    """
    closed: set[str] = set()
    pending: str | None = None
    unknown = []
    names = {id(stmt): name for stmt, name in _calltable_names(program)}
    for stmt in _with_inline(program.statements):
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "DATATABLE":
            name = _table_name(stmt)
            if not name and stmt.args and isinstance(stmt.args[0], S.String):
                name = stmt.args[0].value  # `DataTable ("HIST4D",1,100)`, histogram4d_1.CR6
            if name:
                pending = name.upper()
        elif kw == "ENDTABLE":
            if pending is not None:
                closed.add(pending)
            pending = None
        elif id(stmt) in names and names[id(stmt)].upper() not in closed:
            # Any target that is not a table closed *before this statement* fails the same way
            # (oracle, all four loggers, 2026-09-27 and round 9): `CallTable Nope`, a variable
            # `CallTable X`, `CallTable Status`, `CallTable ("fuzz")` -> `fuzz`, `CallTable((1+2))`
            # -> `1+2`; also the table's own still-open body, a table declared later, and a `Sub`
            # above the table (docs/checker.md "CallTable before its table is closed").
            unknown.append((stmt, names[id(stmt)]))
    return unknown


def _with_inline(stmts):
    for stmt in stmts:
        yield stmt
        if isinstance(stmt, S.If):
            yield from _with_inline(stmt.inline or [])
            yield from _with_inline(stmt.inline_else or [])


def _calltable_names(program: S.Program):
    for stmt in _with_inline(program.statements):
        if not (isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "CALLTABLE"):
            continue
        target = stmt.args[0] if stmt.args else None
        if isinstance(target, S.Name):
            name = target.name
        elif isinstance(target, S.String):
            name = target.value
        elif isinstance(target, S.Binary) and stmt.arg_spans and stmt.arg_spans[0]:
            start, end = stmt.arg_spans[0]
            name = stmt.text[start:end].strip()
            while name.startswith("(") and name.endswith(")"):
                name = name[1:-1].strip()
        else:
            continue
        yield stmt, name


# Round 14 (oracle, all four loggers, 2026-10-12, `docs/checker.md` "Round 14 table references"):
# instructions naming a table that does not exist. `(0-based argument, message, ends pass 2)`.
# The ending ones behave like `CallTable` (only the earliest of them is printed, later statements
# are not compiled); `SendData`/`SendTableDef` do not end pass 2 and every one is printed.
TABLE_REFERENCES = {
    "GETRECORD": (1, "Could not find DataTable {}.", True),
    "RESETTABLE": (0, "Could not find DataTable {}.", True),
    "FILEMARK": (0, "Could not find FileMark table {}.", True),
    "SENDDATA": (3, "Cannot find DataTable {}.", False),
    "SENDTABLEDEF": (3, "Cannot find DataTable {}.", False),
}
_SPECIAL_TABLES = frozenset({"STATUS", "PUBLIC", "SETTINGS", "DATATABLEINFO", "ENDTABLE"})


def unknown_table_references(program: S.Program):
    """`(statement, message, ends pass 2)` for each `TABLE_REFERENCES` statement whose table is
    declared nowhere in the program (a table declared *later* is also not found by the real
    compiler; not modelled, the report would only be missed)."""
    declared = {st.args[0].name.upper() for st in _with_inline(program.statements)
                if isinstance(st, S.Instruction) and st.keyword.upper() in ("DATATABLE", "CONSTTABLE")
                and st.args and isinstance(st.args[0], S.Name)}
    out = []
    for stmt in _with_inline(program.statements):
        if not isinstance(stmt, S.Instruction):
            continue
        spec = TABLE_REFERENCES.get(stmt.keyword.upper())
        if spec is None or len(stmt.args) <= spec[0] or stmt.args[spec[0]] is None:
            continue
        arg = stmt.args[spec[0]]
        if stmt.keyword.upper() in ("SENDDATA", "SENDTABLEDEF"):
            from . import expressions
            name = expressions._arg_text(stmt, spec[0]).strip()
        elif isinstance(arg, S.Name):
            name = arg.name
        elif isinstance(arg, S.String):
            name = arg.value
        else:
            continue
        if not name or name.upper() in declared or name.upper() in _SPECIAL_TABLES:
            continue
        out.append((stmt, spec[1].format(name), spec[2]))
    return out


def check_unclosed_calltable(program: S.Program, diags: Diagnostics) -> None:
    refs = unknown_table_references(program)
    first = next(iter(unknown_calltables(program)), None)
    ending = [(stmt, message) for stmt, message, ends in refs if ends]
    stopper = None     # the earliest statement that ends the walk and prints its message
    if ending and (first is None or (ending[0][0].file, ending[0][0].line) <= (first[0].file, first[0].line)):
        stopper = ending[0]
    elif first is not None:
        stopper = (first[0], f"Could not find DataTable {first[1]}.")
    for stmt, message, ends in refs:
        if ends or (stopper is not None and stmt.line > stopper[0].line):
            continue
        at = next((i for i, d in enumerate(diags.items) if d.line == stmt.line and d.file == stmt.file
                   and d.message.endswith("illegally occurs before BeginProg.")), len(diags.items))
        diags.items.insert(at, Diagnostic("error", stmt.line, message, stmt.file))
    if stopper is not None:
        # only the first one is printed (oracle, all four loggers, 2026-10-03)
        diags.error(stopper[0].line, stopper[1], stopper[0].file)


# `FieldNames`, `FieldClassify` and `FieldOrigin` records directly after a field-producing
# instruction are read by that instruction's builder (docs/checker.md "FieldNames owner line");
# `SampleFieldCal` was probed not to read them, the other three exclusions are unprobed.
_FIELD_METADATA = frozenset({"FIELDNAMES", "FIELDCLASSIFY", "FIELDORIGIN"})
_FIELDNAMES_OWNERS = _FIELD_PRODUCING_INSTRUCTIONS - {
    "SAMPLEFIELDCAL", "ETSZ", "ETCLEARSKY", "MQTTPUBLISHTABLE"}
_FIRST_WORD = re.compile(r"\s*([A-Za-z_]\w*)")


def fieldnames_owners(program: S.Program) -> dict[int, S.Stmt | None]:
    """`id(statement)` of every `FieldNames` (parsed or not) -> the output instruction whose
    builder reads it: the last owner before it with only `FieldNames`/`FieldClassify`/`FieldOrigin`
    in between, inside a `DataTable`, else None (a `FieldNames` anywhere else is never resolved;
    a `Sample` outside a table owns nothing, replay `921b96a020e3`)."""
    owners: dict[int, S.Stmt | None] = {}
    owner = None
    in_table = False
    for st in program.statements:
        if isinstance(st, S.Instruction):
            kw = st.keyword.upper()
        elif isinstance(st, S.Unparsed) and (m := _FIRST_WORD.match(st.text)):
            kw = m.group(1).upper()
        else:
            continue
        if kw in ("DATATABLE", "ENDTABLE"):
            in_table = kw == "DATATABLE"
        if kw == "FIELDNAMES":
            owners[id(st)] = owner
        elif kw not in _FIELD_METADATA:
            owner = st if kw in _FIELDNAMES_OWNERS and in_table else None
    return owners


def move_fieldnames_symbol_errors(program: S.Program, items: list[Diagnostic]):
    """CR6/CR1000X: the pass-2 `Invalid symbol in expression` findings of a `FieldNames` argument
    belong to its owner (line, and before the owner's own argument errors), followed by the two
    semantic errors; an unowned `FieldNames` is never resolved, so they vanish. Edits `items` in
    place; returns ({id(old): replacement or None}, added) for the caller's bookkeeping."""
    owners = fieldnames_owners(program)
    replaced: dict[int, Diagnostic | None] = {}
    added: list[Diagnostic] = []
    next_at: dict[int, int] = {}
    for st in program.statements:
        if id(st) not in owners:
            continue
        mine = [d for d in items if (d.file, d.line) == (st.file, st.line)
                and d.message.startswith("Invalid symbol in expression")]
        if not mine:
            continue
        items[:] = [d for d in items if not any(d is m for m in mine)]
        owner = owners[id(st)]
        if owner is None:
            replaced.update((id(d), None) for d in mine)
            continue
        moved = [dataclasses.replace(d, line=owner.line, file=owner.file) for d in mine]
        replaced.update((id(d), n) for d, n in zip(mine, moved))
        pair = [Diagnostic("error", owner.line, message, owner.file, show_file=False)
                for message in ("FieldNames cannot be a variable.", "FieldNames canot be NULL")]
        added += pair
        at = next_at.get(id(owner), next((
            i for i, d in enumerate(items) if (d.file, d.line) == (owner.file, owner.line)
            and d.message.startswith("Invalid symbol in expression")), len(items)))
        items[at:at] = moved + pair
        next_at[id(owner)] = at + len(moved) + len(pair)
    return replaced, added


def check_fieldnames(program: S.Program, profile: Profile, diags: Diagnostics, env) -> None:
    """`FieldNames` takes a non-empty constant string (docs/checker.md "FieldNames argument"): a
    numeric constant or a variable is `FieldNames cannot be a variable.`, an empty string `FieldNames
    canot be NULL`, and a String variable both (its compile-time value is empty). Undefined names are
    left to the symbol checks. The output instruction whose builder reads it (`fieldnames_owners`)
    reports it, so the line is that instruction's; an unowned `FieldNames` is not checked."""
    tables = symbols.scope_tables(program, env, scalars=True)
    owners = fieldnames_owners(program)
    for st in program.statements:
        if not isinstance(st, S.Instruction) or st.keyword.upper() != "FIELDNAMES":
            continue
        owner = owners.get(id(st))
        if len(st.args) != 1 or st.args[0] is None or owner is None:
            continue
        line, file = owner.line, owner.file
        arg = st.args[0]
        try:
            value = consteval.value_of(arg, env)
        except consteval.NotConstant:
            kind = tables(st).types.get(arg.name.upper()) if isinstance(arg, (S.Name, S.Index)) else None
            if kind is None:
                continue
            diags.error(line, "FieldNames cannot be a variable.", file)
            value = "" if kind == "STRING" else None
        else:
            if not isinstance(value, str):
                diags.error(line, "FieldNames cannot be a variable.", file)
        if value == "":
            diags.error(line, "FieldNames canot be NULL", file)


def check_table_structure(program: S.Program, profile: Profile,
                          diags: Diagnostics) -> tuple[str | None, int] | None:
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

    Either `EndTable` message ends pass 2: the first one is returned as the stop location and
    later findings are dropped (oracle, all four loggers, 2026-10-03: `EndTable` then `Sample`
    outside a table reports only the `EndTable`; `Sample` then `EndTable` reports both).

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
    env = consteval.Env(profile, dict(program.consts))
    in_table = False
    table_name = ""
    has_field = False
    goes_seen = False        # a `GOESTable` was declared (CR300/CR350 keep it across tables)
    goes_orphans = 0         # `GOESField`s in this table without it
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            if isinstance(stmt, S.Unparsed):
                has_field = has_field or in_table  # a line we could not parse may be the field
            continue
        kw = stmt.keyword.upper()
        if kw == "DATATABLE":
            in_table = True
            has_field = False
            goes_orphans = 0
            goes_seen = goes_seen and profile.single_pass
            table_name = (_declared_table_name(stmt) or "")[:_TABLE_NAME_LIMIT]
            continue
        if kw == "ENDTABLE":
            if not in_table:
                diags.error(stmt.line, "EndTable without DataTable.", stmt.file)
                return (stmt.file, stmt.line)
            if not has_field and table_name:
                diags.error(stmt.line, f"Table {table_name} declared with no output fields.", stmt.file)
                return (stmt.file, stmt.line)
            for _ in range(goes_orphans):   # one per such `GOESField`
                diags.error(stmt.line, "GoesField declared without a GoesTable declaration.", stmt.file)
            in_table = False
            continue
        if in_table:
            if kw in _FIELD_PRODUCING_INSTRUCTIONS and not _fieldless(stmt, profile, env):
                has_field = True
            elif kw == "GOESTABLE":
                goes_seen = True
            elif kw == "GOESFIELD" and kw in profile.instructions and not goes_seen:
                diags.error(stmt.line, "GoesTable not active.", stmt.file)
                goes_orphans += 1
            continue
        display = TABLE_ONLY_INSTRUCTIONS.get(kw)
        if display is not None and kw in profile.instructions:
            diags.error(stmt.line, f"{display} must be used inside a table declaration.", stmt.file)
    return None


# `Sample(Reps, CH400Status, ...)` samples the predefined CH400Status table: `Reps` consecutive
# copies of these 32 field names (oracle-observed through the duplicate-name messages,
# `docs/checker.md` "Duplicate field names").
_CH400_FIELDS = (
    "ChargeAVoltage", "ChargeACurrent", "ChargeBVoltage", "ChargeBCurrent", "InputChargeStatus",
    "InputCurrentLimit", "BattAVoltage", "BattACurrent", "BattAChargeStatus", "BattASetVoltage",
    "BattACurrentLimit", "BattADepthofDischarge", "BattBVoltage", "BattBCurrent",
    "BattBChargeStatus", "BattBSetVoltage", "BattBCurrentLimit", "BattBDepthofDischarge",
    "SystemVoltage", "SystemCurrent", "AlwaysOn12Voltage", "AlwaysOn12Current",
    "Switched12Voltage", "Switched12Current", "Switched24Voltage", "Switched24Current",
    "PowerSwitchState", "MaxBoardTemp", "MinBoardTemp", "PanelTemp", "BattAProbeTemp",
    "BattBProbeTemp",
)

# Error-text buffer: messages are appended while they fit in 4095 bytes counting CRLF per line.
_ERROR_BUFFER_BYTES = 4095


def _dup_entries(table):
    """(name, renamed, range) for each field of `table`, in order (`docs/checker.md` "Duplicate
    field names"). Falls back to the TDF fields for a table without `tables.py` bookkeeping."""
    fields = getattr(table, "_dup_fields", None)
    if fields is None:
        for f in table.fields:
            yield f.name, False, (f.begin_index, f.begin_index + max(f.dimension, 1) - 1), False
        return
    for f in fields:
        if f.ch400:
            for _ in range(max(f.dim, 1)):
                for n in _CH400_FIELDS:
                    yield n, False, (1, 1), False
        else:
            yield f.name, f.renamed, f.rng, f.placeholder


def check_duplicate_fields(program: S.Program, result, diags: Diagnostics) -> None:
    """`Duplicate field name %s in DataTable %s.` (no line number), once for every pair of
    conflicting fields, reported for the later field of the pair in table order. Rule, evidence and
    the error-buffer cap: `docs/checker.md` "Duplicate field names".

    Two fields conflict when their names match case-insensitively and either both were named by
    `FieldNames` or their source-element ranges overlap. Field names are reused from `tables.py`
    (`TdfTable._dup_fields`), which also supplies name-only FFT and CH400Status fields. Entries
    explicitly marked as generated placeholders are skipped; a user field named `Anonymous` is
    still checked.
    """
    tables = getattr(result, "tables", None)
    if not tables:
        return
    user_tables: list[str] = []
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "DATATABLE":
            name = _table_name(stmt)
            if name and name.upper() not in user_tables:
                user_tables.append(name.upper())
    by_upper = {t.name.upper(): t for t in tables}
    remaining = _ERROR_BUFFER_BYTES
    for key in user_tables:
        table = by_upper.get(key)
        if table is None:
            continue
        seen: dict[str, list[tuple[bool, tuple[int, int] | None]]] = {}
        shortest = len(f"Duplicate field name x in DataTable {table.name}.") + 2
        for name, renamed, rng, placeholder in _dup_entries(table):
            if remaining < shortest:
                break
            if placeholder:
                continue
            earlier = seen.setdefault(name.upper(), [])
            text = f"Duplicate field name {name} in DataTable {table.name}."
            cost = len(text) + 2
            for e_renamed, e_rng in earlier:
                if renamed and e_renamed:
                    conflict = True
                elif rng is None or e_rng is None:
                    conflict = False
                else:
                    conflict = rng[0] <= e_rng[1] and e_rng[0] <= rng[1]
                if not conflict:
                    continue
                if cost <= remaining:
                    diags.error(0, text)
                    remaining -= cost
            earlier.append((renamed, rng))


def check_units_targets(program: S.Program, profile: Profile, diags: Diagnostics) -> None:
    """`Can't assign units to %s, it must be declared Public or referenced as a source in a Data
    Table.` (all four loggers, checked after pass 2 like the table finalisation): a `Units`
    target whose base name is not a `Public`/`Dim` variable of a plain type or an `Alias` name,
    wherever declared. Oracle, 2026-09-27 (`docs/checker.md` "Units targets"): `Units D = m` on a
    `Dim D` that no table uses compiles; `Const`s, undeclared names, `Sub` parameters,
    `StructureType`-typed variables and table field names (`P_Avg`) are all rejected; the name
    is echoed as written, the base name for `Units Nope(1)`."""
    struct_types = {st.args[0].name.upper() for st in program.statements
                    if isinstance(st, S.Instruction)
                    and st.keyword.upper() in ("STRUCTURETYPE", "OBJECTTYPE")
                    and st.args and isinstance(st.args[0], S.Name)}
    allowed: set[str] = set()
    for st in program.statements:
        if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC"):
            allowed |= {v.name.upper() for v in st.vars
                        if not (v.type and v.type in struct_types)}
        elif isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS":
            allowed |= {a.name.upper() for a in st.args[1:] if isinstance(a, S.Name)}
    in_struct = False
    assigned: dict[str, list[S.Instruction]] = {}
    indexed: set[str] = set()
    for st in program.statements:
        kw = st.keyword.upper() if isinstance(st, S.Instruction) else ""
        if kw in ("STRUCTURETYPE", "OBJECTTYPE", "ENDSTRUCTURETYPE", "ENDOBJECTTYPE"):
            in_struct = not kw.startswith("END")
        if in_struct or kw != "UNITS" or not isinstance(st, S.Instruction) or not st.args:
            continue  # inside a `StructureType`, `Units` names a member (`structuretype_1.CR6`)
        for extra in getattr(st, "extra_targets", ()):
            if extra.name.upper() not in allowed:
                diags.error(st.line, f"Can't assign units to {extra.name}, it must be declared "
                            "Public or referenced as a source in a Data Table.", st.file)
        target = st.args[0]
        if isinstance(target, (S.Name, S.Index)) and target.name.upper() not in allowed:
            diags.error(st.line, f"Can't assign units to {target.name}, it must be declared Public "
                        "or referenced as a source in a Data Table.", st.file)
        elif isinstance(target, S.Name):
            assigned.setdefault(target.name.upper(), []).append(st)
        elif isinstance(target, S.Index):
            indexed.add(target.name.upper())
    # Each of a variable's n `Units` statements gets n-1 copies of `%s already assigned Units %s.`,
    # echoing its own spelling (oracle, all four loggers, 2026-09-28: three `Units X` lines give two
    # lines each). Indexed targets are not probed.
    for name, statements in assigned.items():
        if len(statements) < 2 or name in indexed:
            continue
        for st in statements:
            units = st.args[1].value if len(st.args) > 1 and isinstance(st.args[1], S.String) else None
            if units is None:
                continue
            target = st.args[0]
            assert isinstance(target, S.Name)  # (only bare-name targets are collected)
            for _ in range(len(statements) - 1):
                diags.error(st.line, f"{target.name} already assigned Units {units}.", st.file)


_SEQUENCE_OPENERS = frozenset({"DIALSEQUENCE", "MODEMHANGUP"})
_SEQUENCE_CLOSERS = {"ENDDIALSEQUENCE": ("Missing DialSequence().", "DIALSEQUENCE"),
                     "ENDMODEMHANGUP": ("Missing ModemHangup().", "MODEMHANGUP"),
                     "ENDVOICE": ("EndVoice without Voice", None)}


def check_table_and_menu_placement(program: S.Program, profile: Profile,
                                   diags: Diagnostics, env=None) -> tuple[str, int] | None:
    """Pass 2 (oracle, suppressed by a pass-1 error): a second `DataInterval` in one table ->
    `Only one DataInterval allowed within DataTable..EndTable.` (all four loggers), and the display
    menu structure (CR6/CR1000X; CR300/CR350 have no menus), following the decompiled menu builder
    (`FUN_004309c0`, `FUN_00430aa0`, `FUN_00430b50`; oracle, 2026-09-28, `docs/checker.md`
    "Display menus"). Returns the `(file, line)` pass 2 stops at (`EndSubMenu without SubMenu.`), if any."""
    in_table = False
    intervals = 0
    has_maxmin = False
    menus = submenus = 0
    open_list = False      # the builder's insertion point; an empty `SubMenu` clears it
    levels: list[list[str]] = []
    table_consts: set[str] = set()
    in_const_table = False
    for st in program.statements:
        kw = st.keyword.upper() if isinstance(st, S.Instruction) else ""
        if kw in ("CONSTTABLE", "ENDCONSTTABLE"):
            in_const_table = kw == "CONSTTABLE"
        elif in_const_table and isinstance(st, S.ConstDecl):
            table_consts.add(st.name.upper())
    item_const: str | None = None  # the current `MenuItem`'s ConstTable constant, if it is one
    item_plain = False             # the current `MenuItem` names a `Const` outside the ConstTable
    has_const_table = any(isinstance(st, S.Instruction) and st.keyword.upper() == "CONSTTABLE"
                          for st in program.statements)
    item_bool = False              # the current `MenuItem`/`MenuRecompile` variable is Boolean
    item_kind: str | None = None   # "int" / "float" / "string": what its `MenuPick` values must be
    first_is_const = False
    sequences: dict[str, int] = {}   # open DialSequence / ModemHangup blocks
    variables = symbols.declared_variable_names(program)
    types = symbols.declared_variable_types(program)
    if env is None:
        env = consteval.Env(profile, dict(program.consts))

    def within(st: S.Instruction, kw: str) -> bool:
        if menus and open_list:
            return True
        diags.error(st.line, f"{profile.instructions[kw]['name']} must be used within "
                    "DisplayMenu..EndMenu.", st.file)
        return False

    for st in program.statements:
        if not isinstance(st, S.Instruction):
            continue
        kw = st.keyword.upper()
        if kw == "DATATABLE":
            in_table, intervals, has_maxmin = True, 0, False
            if not profile.single_pass:   # CR300/CR350: `finalize_illegal_table_names`
                diags.items += _illegal_table_name(st)
        elif kw == "ENDTABLE":
            in_table = False
        elif in_table and kw in ("MAXIMUM", "MINIMUM"):
            has_maxmin = True
        elif in_table and kw == "SAMPLEMAXMIN" and not has_maxmin:
            # oracle, all four loggers, 2026-10-08 (docs/fuzz-findings.md "Round 5"); CR300/CR350
            # drop it when the same statement has an excess, missing or empty-last argument
            # (2026-10-10/12, docs/checker.md "Round 13")
            from . import instr_rules
            need = len(instr_rules._real_params(profile.instructions[kw]["params"]))
            if profile.single_pass and (len(st.args) < need or st.args[need - 1] is None
                                        or any(a is not None for a in st.args[need:])):
                continue
            diags.error(st.line, "Maximum() or Minimum() must precede SampleMaxMin().", st.file)
        elif kw == "DATAINTERVAL" and in_table:
            intervals += 1
            if intervals == 2:
                diags.error(st.line, "Only one DataInterval allowed within DataTable..EndTable.",
                            st.file)
        elif kw not in profile.instructions:
            continue
        elif not profile.single_pass and kw in _SEQUENCE_CLOSERS:
            # an `EndDialSequence`/`EndModemHangup`/`EndVoice` with no opener (CR6/CR1000X, oracle
            # 2026-10-13, `docs/checker.md` "Round 16 ... second worker"): printed in line order,
            # every one, and pass 2 goes on
            message, opener = _SEQUENCE_CLOSERS[kw]
            if opener and sequences.get(opener):
                sequences[opener] -= 1
            else:
                diags.error(st.line, message, st.file)
        elif not profile.single_pass and kw in _SEQUENCE_OPENERS:
            sequences[kw] = sequences.get(kw, 0) + 1
        elif kw == "DISPLAYMENU" and menus:
            # a second `DisplayMenu` before `EndMenu` (the first stays open; fuzz-found, oracle)
            diags.error(st.line, "DisplayMenu without EndMenu.", st.file)
        elif kw == "DISPLAYMENU":
            menus += 1
            levels.append([])
            open_list = True
        elif kw in _MENU_ITEMS:
            if within(st, kw):
                levels[-1].append(kw)
                target = st.args[1] if kw == "MENUITEM" and len(st.args) > 1 else None
                item_const = target.name.upper() if isinstance(target, S.Name) and \
                    target.name.upper() in table_consts else None
                item_plain = isinstance(target, S.Name) and target.name.upper() not in table_consts \
                    and target.name.upper() in {str(c).upper() for c in program.consts} \
                    and target.name.upper() not in variables
                target = st.args[1] if kw in ("MENUITEM", "MENURECOMPILE") and len(st.args) > 1 \
                    else None
                item_bool = isinstance(target, (S.Name, S.Index)) and \
                    types.get(target.name.upper()) == "BOOLEAN"
                item_kind = None
                if kw == "MENUITEM" and isinstance(target, (S.Name, S.Index)):
                    if item_const:
                        value = env.consts.get(item_const)
                        # a non-finite float (`Const F=1e999`) is a float item (real: any numeric pick)
                        item_kind = ("string" if isinstance(value, str) else "int"
                                     if isinstance(value, int) and not isinstance(value, bool)
                                     else "float" if isinstance(value, float)
                                     and (not math.isfinite(value) or value != int(value)) else None)
                    else:
                        item_kind = {"LONG": "int", "FLOAT": "float", "STRING": "string"}.get(
                            types.get(target.name.upper(), ""))
                first_is_const = False
                if kw == "SUBMENU":
                    levels.append([])
                    submenus += 1
        elif kw == "MENUPICK":
            # After a `MenuItem` or `MenuRecompile` it is accepted (chm `menuitem_2.CR6`), and
            # repeated for an ordinary variable; a ConstTable constant's item takes one pick list,
            # naming the constant first (oracle, 2026-10-01, fuzz `62246db1b167`).
            if within(st, kw):
                after_item = bool(levels[-1]) and levels[-1][-1] == "MENUITEM"
                if not levels[-1] or levels[-1][-1] not in ("MENUITEM", "MENURECOMPILE"):
                    diags.error(st.line, "MenuPick must follow a MenuItem instruction.", st.file)
                elif levels[-1][-1] == "MENUITEM" and item_plain:
                    # the item names a `Const` outside the ConstTable (oracle, CR6/CR1000X,
                    # 2026-10-12): with a ConstTable anywhere in the program every pick list is
                    # the ConstTable message, even `MenuPick(D,1,2)`; without one, its own message
                    diags.error(st.line, "When using a ConstTable constant in MenuPick, the constant "
                                "name must be the first parameter." if has_const_table else
                                "Value in preceding MenuItem must be a variable or a Const within "
                                "ConstTable..EndConstTable.", st.file)
                    continue
                elif levels[-1][-1] == "MENUITEM" and item_const:
                    first = st.args[0] if st.args else None
                    first_is_const = isinstance(first, S.Name) and first.name.upper() in table_consts
                    if first_is_const:
                        levels[-1].append(kw)
                    else:
                        diags.error(st.line, "When using a ConstTable constant in MenuPick, the "
                                    "constant name must be the first parameter.", st.file)
                bad = next((a for a in st.args if isinstance(a, S.Name)
                            and a.name.upper() in variables), None)
                if item_bool and levels[-1] and levels[-1][-1] in ("MENUITEM", "MENURECOMPILE"):
                    _check_boolean_picklist(st, env, diags)
                elif after_item and (item_kind is not None or item_const):
                    # a ConstTable item whose first parameter is not a constant checks every
                    # value as a plain `Const` (oracle, 2026-10-10)
                    wrong_first = item_const and not first_is_const
                    _check_pick_consts(st, "float" if wrong_first else item_kind, env, program,
                                       profile, variables, diags,
                                       skip_first=bool(item_const) and first_is_const)
                elif bad is not None:
                    # `MenuPick (Y)` for `Public Y` (oracle, 2026-10-01)
                    diags.error(st.line, f"{bad.name} must be declared as a Const.", st.file)
        elif kw == "ENDSUBMENU":
            if not submenus:
                diags.error(st.line, "EndSubMenu without SubMenu.", st.file)
                return st.file, st.line
            submenus -= 1
            if within(st, kw) and not levels.pop():
                diags.error(st.line, "Empty SubMenu ... EndSubMenu.", st.file)
                open_list = False
        elif kw == "ENDMENU":
            if within(st, kw):
                menus -= 1
                if not levels.pop():
                    diags.error(st.line, "Empty DisplayMenu .. EndMenu.", st.file)
                if submenus:
                    diags.error(st.line, "SubMenu with no EndSubMenu.", st.file)
    return None


_BOOL_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*$")
_BOOL_INT_RE = re.compile(r"[+-]?\d+$")


def _boolean_pick_ok(token: str, env) -> bool:
    """Whether one raw `MenuPick` token is acceptable in a Boolean picklist (oracle, 2026-10-02,
    `docs/checker.md` "Boolean picklists"): a constant name whose value is 0 or -1 (`True`,
    `False`, a `Const`), or a bare integer literal that is 0 or -1 as a 32-bit value. Leading
    blanks are skipped; blanks after a literal reject it; anything else (expression, string,
    float, parenthesis, variable, undefined name, empty token) is rejected."""
    token = token.lstrip(" \t")
    if _BOOL_NAME_RE.match(token):
        try:
            value = consteval._lookup(token.strip(), env)
        except consteval.NotConstant:
            return False
        return not isinstance(value, str) and value in (0, -1)
    if _BOOL_INT_RE.match(token):
        return int(token) % (1 << 32) in (0, (1 << 32) - 1)
    prefix, body = token[:2].upper(), token[2:]
    if prefix == "&H" and re.fullmatch("[0-9A-Fa-f]+", body):
        digits, ok = 8, (0, 0xFFFFFFFF)
    elif prefix == "&B" and re.fullmatch("[01]+", body):
        digits, ok = 32, (0, 0xFFFFFFFF)
    else:
        return False
    if len(body.lstrip("0")) > digits:
        return True  # past the probed range: stay silent rather than guess
    return int(body, 16 if digits == 8 else 2) in ok


def _pick_tokens(stmt: S.Instruction) -> list[str] | None:
    """The comma-split raw argument text of a `MenuPick` statement (quotes are not honoured, as
    in the real compiler), without its comment and enclosing parentheses."""
    m = re.match(r"\s*MenuPick\b(.*)$", stmt.text or "", re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    rest, quoted = "", False
    for ch in m.group(1):
        if ch == '"':
            quoted = not quoted
        elif ch == "'" and not quoted:
            break
        rest += ch
    rest = rest.strip()
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1]
    return rest.split(",")


def _check_boolean_picklist(stmt: S.Instruction, env, diags: Diagnostics) -> None:
    tokens = _pick_tokens(stmt)
    if tokens is None:
        return
    trailing = len(tokens) > 1 and tokens[-1] == ""
    for token in tokens[:-1] if trailing else tokens:
        if not _boolean_pick_ok(token, env):
            diags.error(stmt.line, "Boolean picklist must be True or False.", stmt.file)
    if trailing:
        diags.error(stmt.line, "Unable to parse MenuPick strings.", stmt.file)


_PICK_INT_RE = re.compile(r"[+-]?\d+$")
_PICK_FLOAT_RE = re.compile(r"[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?$|[+-]?\d+[eE][+-]?\d+$")
_PICK_RADIX_RE = re.compile(r"&[Hh][0-9A-Fa-f]+$|&[Bb][01]+$")
_PICK_NAME_RE = re.compile(r"[A-Za-z_]\w*$")


def _pick_value_bad(token: str, kind: str, env, program, profile, variables) -> str | None:
    """The echoed text if the real compiler rejects this raw `MenuPick` value, else None. Rule
    (oracle, CR6/CR1000X, 2026-10-10, `docs/fuzz-findings.md` "Round 9"): a Long item takes
    integer constants, a Float item (or ConstTable constant) any numeric constant; a String item
    anything. Silent for any token shape not probed."""
    t = token.lstrip(" \t")
    if t == "":
        return "" if kind == "int" else None
    if t != t.rstrip():
        return t
    inner = t
    if t[0] == "(" and t[-1] == ")":
        inner = t[1:-1]
    if "(" in inner or ")" in inner or "," in inner:
        return None
    if inner.startswith('"') and inner.endswith('"') and len(inner) >= 2 and inner.count('"') == 2:
        return inner
    if _PICK_INT_RE.match(inner) or _PICK_RADIX_RE.match(inner):
        return None
    if _PICK_FLOAT_RE.match(inner):
        return inner if kind == "int" else None
    if _PICK_NAME_RE.match(inner):
        try:
            value = consteval._lookup(inner, env)
        except consteval.NotConstant:
            from .expressions import _known_names
            if inner.upper() in variables or inner.upper() not in _known_names(program, profile) \
                    or (kind == "int" and inner.upper() == "NAN"):
                return inner
            return None
        if kind == "int" and (isinstance(value, str) or value != value or value != int(value)):
            return inner
        return None
    if re.fullmatch(r"[A-Za-z0-9_.&+\-*/ ]+", inner) and re.search(r"[A-Za-z0-9_.)]\s*[-+*/]", inner):
        return inner  # an operator expression, or `-A` / `-&H10`
    if re.fullmatch(r"-([A-Za-z_]\w*|&[Hh][0-9A-Fa-f]+)", inner):
        return inner
    return None


def _check_pick_consts(stmt, kind, env, program, profile, variables, diags, skip_first) -> None:
    if kind is None or kind == "string":
        return
    tokens = _pick_tokens(stmt)
    if tokens is None:
        return
    word = "integer Const" if kind == "int" else "Const"
    for token in tokens[1:] if skip_first else tokens:
        bad = _pick_value_bad(token, kind, env, program, profile, variables)
        if bad is not None:
            diags.error(stmt.line, f"{bad} must be declared as a {word}.", stmt.file)


_MENU_ITEMS = ("SUBMENU", "MENUITEM", "DISPLAYVALUE", "MENURECOMPILE", "DISPLAYLINE")


_TABLE_OUTPUT = ("TOTALIZE", "AVERAGE", "MAXIMUM", "MINIMUM", "SAMPLEMAXMIN", "STDDEV", "MOMENT",
                 "SAMPLE", "COVARIANCE", "MEDIAN")
_OWN_VARIABLE_CHECK = ("ADDPRECISE", "MOVEPRECISE")  # `instr_rules.check_precision_variable`
_INT32_MIN = -2147483648


def _has_names(expr) -> bool:
    """Whether `expr` references any name (variable, constant, function or symbol)."""
    if isinstance(expr, (S.Name, S.Index, S.Member)):
        return True
    if isinstance(expr, S.Unary):
        return _has_names(expr.operand)
    if isinstance(expr, S.Binary):
        return _has_names(expr.left) or _has_names(expr.right)
    return False


def _reps_value(expr, env) -> int | None:
    """A `Reps` argument's value as the real compiler reads it: a string or a value outside the
    32-bit range becomes -2147483648 (oracle: `Sample("fuzz",...)`, `Maximum (999999999999,...)`)."""
    if isinstance(expr, S.String):
        return _INT32_MIN
    try:
        value = consteval.value_of(expr, env)
    except consteval.NotConstant:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = int(value)
    return value if -2**31 <= value < 2**31 else _INT32_MIN


def check_stage2a(program: S.Program, profile: Profile, diags: Diagnostics, env) -> bool:
    """CR6/CR1000X instruction checks that run after pass 1 but before pass 2's expression checks
    (oracle, 2026-09-27: each is suppressed by a pass-1 error, and each suppresses a pass-2
    `Invalid symbol in expression` elsewhere, even on an earlier line). Returns whether anything
    was reported (then the caller skips pass 2's expression tier).

    - A constant (no names at all) where a destination variable is required (type code 8):
      `Battery((1+2))` -> `Invalid Variable or Array: (1+2).`, `PanelTemp(3,15000)` -> `... 3.`.
    - A table-output `Reps` below 1: `Sample (0,X,FP2)` -> `Illegal Reps 0`; a source running past
      its array: `Sample (3,A(4),FP2)` with `A(5)` -> `Variable A out of bounds.` and `Variable A,
      index 4, reps 3, out of bounds.`. The first such table statement ends the stage (round 24
      forms `A()`, multi-dimensional and expression-subscripted sources: docs/checker.md "Table
      output source with `X()`").
    """
    if profile.name not in ("cr6", "cr1000x"):
        return False
    return _check_stage2a(program, profile, diags, env, destinations=True)


def check_table_reps_late(program: S.Program, profile: Profile, diags: Diagnostics, env) -> None:
    """CR300/CR350: the same `Illegal Reps` / `Variable %s out of bounds.` table checks, reported
    only when nothing else failed and stopping at the first (oracle, 2026-09-27: `Sample (0,X,FP2)`
    plus an unrelated `X = Q` prints only `Q is not defined.`)."""
    if profile.name in ("cr300", "cr350"):
        _check_stage2a(program, profile, diags, env, destinations=False)


def _report_undefined_source(st: S.Instruction, record: dict, known: set[str], profile: Profile,
                             diags: Diagnostics) -> bool:
    """CR300/CR350: an undefined name in a table-output source (`Sample (1,Nope,FP2)` -> `Nope is
    not defined.`, and `Dimensions not declared: Nope(2).` for `Nope(2)`), late and only the first
    (oracle, 2026-09-27; CR6/CR1000X: `structure._undefined_table_source`)."""
    from . import expressions
    kinds = expressions._type_code_kinds()
    for i, code in enumerate(record["params"][:len(st.args)]):
        arg = st.args[i]
        if arg is None or kinds.get(code % 100 if code >= 100 else code) != "deferred_text":
            continue
        names = expressions._undefined_names(arg, known)
        if not names:
            continue
        for name in names:
            diags.error(st.line, profile.unresolved_symbol_message.format(name=name), st.file,
                        show_file=False)
        if expressions._has_undefined_index(arg, known):
            diags.error(st.line, expressions.dimensions_message(
                expressions._arg_text(st, i).strip(), profile, False), st.file)
        return True
    return False


def _is_struct_typed(program: S.Program, name: str) -> bool:
    types = {st.args[0].name.upper() for st in program.statements
             if isinstance(st, S.Instruction) and st.keyword.upper() == "STRUCTURETYPE"
             and st.args and isinstance(st.args[0], S.Name)}
    return any((v.type or "").upper() in types for d in program.statements
               if isinstance(d, S.Declaration) for v in d.vars if v.name.upper() == name.upper())


def _short_array(arg, reps: int, dims: dict, env) -> bool:
    """`arg` is a declared variable (or a constant 1-D element) with fewer than `reps` elements left."""
    if not isinstance(arg, (S.Name, S.Index)) or (shape := dims.get(arg.name.upper())) is None:
        return False
    offset = 0
    if isinstance(arg, S.Index) and arg.args:
        if len(arg.args) != 1 or len(shape) != 1 or (sub := _reps_value(arg.args[0], env)) is None:
            return False
        offset = sub - 1
    return offset + reps > math.prod(shape)


def _check_stage2a(program: S.Program, profile: Profile, diags: Diagnostics, env,
                   destinations: bool) -> bool:
    from . import expressions
    reported = False
    tables = symbols.scope_tables(program, env)
    known = expressions._known_names(program, profile)
    source_vars = None
    if destinations:
        # whole program first: any constant destination hides every `Reps`/bounds check, wherever it
        # sits (oracle, round 52, docs/checker.md#datainterval-before-a-table-reps-error-round-52),
        # a one-line If's statement too (docs/checker.md#constant-destinations-inside-a-one-line-if-round-52)
        for st in _with_inline(program.statements):
            record = profile.instructions.get(st.keyword.upper()) if isinstance(st, S.Instruction) else None
            if not record or st.keyword.upper() in _OWN_VARIABLE_CHECK:
                continue
            for i, code in enumerate(record["params"][:len(st.args)]):
                arg = st.args[i]
                if code in (8, 27, 29) and arg is not None and not isinstance(arg, S.String) \
                        and not _has_names(arg):  # (27/29: `Erase(5)`, `RealTime(5)`, 2026-10-01)
                    diags.error(st.line, f"Invalid Variable or Array: {expressions._arg_text(st, i).strip()}.",
                                st.file)
                    reported = True
        if reported:
            return True
    in_table = False
    sources_ok = True   # CR6/CR1000X: an undefined table source ends the table checks (round 38)
    top_level = {id(st) for st in program.statements}
    for st in _with_inline(program.statements):
        if not isinstance(st, S.Instruction):
            continue
        dims = tables(st).dims
        kw = st.keyword.upper()
        inline = id(st) not in top_level   # a one-line If's statement: only the destination check (round 52)
        if not inline:
            in_table = kw == "DATATABLE" or (in_table and kw != "ENDTABLE")
        record = profile.instructions.get(kw)
        if not record:
            continue
        if inline and not destinations:
            continue
        if kw == "WINDVECTOR" and st.args and not inline:
            reps = _reps_value(st.args[0], env)
            if reps is not None and _INT32_MIN < reps < 0:
                diags.error(st.line, "Out of memory.", st.file)
                return True
        if kw == "DEBUG" and not inline:
            from .instr_rules import debug_history_oom
            if debug_history_oom(st, env, profile, program):
                diags.error(st.line, "Out of memory.", st.file)
                return True
        if inline:
            continue
        if destinations and in_table and kw in _TABLE_OUTPUT and sources_ok:
            from .structure import _undefined_table_source
            sources_ok = _undefined_table_source(st, profile, known) is None
        wind = kw == "WINDVECTOR" and destinations and in_table and len(st.args) >= 3 and \
            isinstance(st.args[1], (S.Name, S.Index)) and (_reps_value(st.args[0], env) or 0) >= 1
        if wind and _short_array(st.args[2], _reps_value(st.args[0], env) or 0, dims, env):
            # the direction first, one line (oracle, round 56, docs/checker.md#round-56-fuzz-rules)
            diags.error(st.line, f"Variable {getattr(st.args[2], 'name', '')} out of bounds.", st.file)
        if (kw in _TABLE_OUTPUT or wind) and len(st.args) >= 2 and sources_ok and (in_table or not destinations):
            # (CR6/CR1000X: outside a table only `must be used inside a table declaration`, round 39)
            reps = _reps_value(st.args[0], env)
            if reps is not None and reps < 1:
                diags.error(st.line, f"Illegal Reps {reps}", st.file)
                return True
            if not destinations and in_table and _report_undefined_source(st, record, known, profile, diags):
                return True
            source = st.args[1]
            if kw == "SAMPLE" and len(st.args) > 2:
                from .. import tables as output_tables
                if output_tables._dtype_code(st.args[2], env) == 11:
                    if source_vars is None:
                        source_vars = {v.name.upper(): output_tables._make_var_info(v, env)
                                       for v in output_tables._scan_program(program)["all_vars"]}
                    if output_tables._is_anonymous_source(source, source_vars, env) and \
                            output_tables._string_stored_len(output_tables._anonymous_string_len(
                                source, source_vars, env)) <= 0:
                        diags.error(st.line, "Out of memory.", st.file)
                        return True
            if reps is not None and reps > 1 and not isinstance(source, (S.Name, S.Index)):
                diags.error(st.line, "Reps must be 1 when the source is an expression.", st.file)
                diags.error(st.line, f"Variable Anonymous1, index 1, reps {reps}, out of bounds.",
                            st.file)
                return True
            if reps is None or not isinstance(source, (S.Name, S.Index)):
                continue
            shape = dims.get(source.name.upper())
            if shape is None:
                continue
            if not destinations and _is_struct_typed(program, source.name):
                continue  # CR300/CR350 never bounds-check a StructureType source (oracle, 2026-10-12)
            start, offset = 1, 0
            empty = isinstance(source, S.Index) and not source.args
            if isinstance(source, S.Index) and not empty:
                if len(source.args) != len(shape) or any(a is None for a in source.args):
                    continue
                subs = [k for a in source.args if (k := _reps_value(a, env)) is not None]
                if len(subs) != len(source.args):
                    continue
                start = subs[0] if len(shape) == 1 else 1
                for sub, n in zip(subs, shape):
                    offset = offset * n + sub - 1
            total = math.prod(shape)
            if offset + reps > total:
                if destinations and in_table and (message := _odd_pair_reps(st, env)):
                    diags.error(st.line, message, st.file)   # (first on the line, round 38)
                name = next((v.name for d in program.statements if isinstance(d, S.Declaration)
                             for v in d.vars if v.name.upper() == source.name.upper()), source.name)
                if not isinstance(source, S.Index) or all(isinstance(a, S.Number) for a in source.args):
                    diags.error(st.line, f"Variable {name} out of bounds.", st.file)
                if empty:
                    diags.error(st.line, "Invalid, or out of place expression: ().", st.file)
                if start + reps - 1 > total:
                    diags.error(st.line, f"Variable {name}, index {start}, reps {reps}, out of bounds.",
                                st.file)
                    return True
    return reported


def check_eventcount_tables(program: S.Program, profile: Profile, diags: Diagnostics) -> None:
    """`Tb.EventCount` for a table without `DataEvent` -> `DataTable Tb must have DataEvent() to
    request EventCount.` (oracle, 2026-10-01: pass 2 on CR6/CR1000X, alongside other pass-2
    errors; CR300/CR350 only in an otherwise clean file)."""
    tables: dict[str, bool] = {}
    current: str | None = None
    for st in program.statements:
        kw = st.keyword.upper() if isinstance(st, S.Instruction) else ""
        if isinstance(st, S.Instruction) and (kw == "DATATABLE" and st.args and isinstance(st.args[0], S.Name)):
            current = st.args[0].name.upper()
            tables.setdefault(current, False)
        elif kw == "ENDTABLE":
            current = None
        elif kw == "DATAEVENT" and current:
            tables[current] = True
    if not tables:
        return
    for st in program.statements:
        for expr in _statement_exprs(st):
            for node in _members(expr):
                base = node.base
                if isinstance(base, S.Name) and node.member.upper() == "EVENTCOUNT" and \
                        tables.get(base.name.upper()) is False:
                    diags.error(st.line, f"DataTable {base.name} must have DataEvent() to "
                                "request EventCount.", st.file)
                    break
            else:
                continue
            break


def _statement_exprs(st: S.Stmt) -> list[S.Expr]:
    out: list[S.Expr | None] = []
    if isinstance(st, S.Assignment):
        out += [st.target, st.value]
    elif isinstance(st, (S.If, S.ElseIf, S.DoLoop)):
        out.append(st.cond)
    elif isinstance(st, (S.Instruction, S.Call)):
        out += list(st.args)
    elif isinstance(st, S.For):
        out += [st.start, st.end, st.step]
    return [e for e in out if e is not None]


def _members(expr: S.Expr | None):
    if isinstance(expr, S.Member):
        yield expr
        yield from _members(expr.base)
        for a in expr.args or []:
            yield from _members(a)
    elif isinstance(expr, S.Index):
        for a in expr.args:
            yield from _members(a)
    elif isinstance(expr, S.Binary):
        yield from _members(expr.left)
        yield from _members(expr.right)
    elif isinstance(expr, S.Unary):
        yield from _members(expr.operand)


_TABLE_NAME_LIMIT = 20
_EXPR_NAME = re.compile(r"[0-9][0-9A-Za-z_]*(?:[-+*/^][0-9A-Za-z_]+)+\Z")


def _declared_table_name(st: S.Instruction) -> str | None:
    """The text the real compiler takes as a `DataTable` name: a bare name, a string without
    spaces, or (r16) a digit-led operator expression read as raw text, `(1+2)` / `5-2` -> `1+2`
    (no spaces, no inner parentheses; oracle, cr6 and cr300, 2026-10-12)."""
    if not st.args:
        return None
    first = st.args[0]
    if isinstance(first, S.Name):
        return first.name
    if isinstance(first, S.String) and re.fullmatch(r"[0-9A-Za-z_.]+", first.value):
        return first.value
    if isinstance(first, S.Binary) and st.arg_spans and (span := st.arg_spans[0]):
        text = st.text[span[0]:span[1]].strip()
        if text.startswith("(") and text.endswith(")"):
            text = text[1:-1]
        if _EXPR_NAME.match(text):
            return text
    return None


def _illegal_table_name(st: S.Instruction) -> list[Diagnostic]:
    """oracle, all four loggers, 2026-10-11 (docs/checker.md "Table names starting with a digit");
    a name over 20 characters is `DataTable name X is N chars too long.` and wins over the digit
    messages (2026-10-12, docs/checker.md "Round 16 message-diff families")"""
    name = _declared_table_name(st)
    if name is None:
        return []
    if len(name) > _TABLE_NAME_LIMIT:
        text = f"DataTable name {name} is {len(name) - _TABLE_NAME_LIMIT} chars too long."
    elif name[:1] in "0123456789.":
        text = (f"DataTable name {name} is illegal." if "." in name
                else f"Table name {name} illegal, starts with a number.")
    else:
        return []
    return [Diagnostic("error", st.line, text, st.file, cascade=False)]


def is_table_name_diag(d: Diagnostic) -> bool:
    """CR6/CR1000X: a table-name error does not keep the tier-C checks (instruction channel/range
    validation, `Sample must be used inside a table declaration.`, `Table T declared with no
    output fields.`) from running (oracle, cr6, 2026-10-12)."""
    return d.message.startswith(("Table name ", "DataTable name ")) and d.message.endswith(
        ("starts with a number.", "is illegal.", "chars too long."))


def finalize_illegal_table_names(program: S.Program, profile: Profile, diags: Diagnostics) -> None:
    """CR300/CR350 only: the first `Table name X illegal, starts with a number.` is the only one
    reported, and only when no other error was found earlier in the compile (oracle, 2026-10-11,
    docs/checker.md "Table names starting with a digit"). Errors outside table bodies and
    unknown-statement errors inside them come first and hide it; an undefined name inside a table
    body (`Zed is not defined.`) races it in file order, the first one wins and ends the rest."""
    marks = [d for st in program.statements
             if isinstance(st, S.Instruction) and st.keyword.upper() == "DATATABLE"
             for d in _illegal_table_name(st)][:1]
    if not marks:
        return
    bodies: list[tuple[str, int, int]] = []
    start = None
    for st in program.statements:
        kw = st.keyword.upper() if isinstance(st, S.Instruction) else ""
        if kw == "DATATABLE":
            start = st
        elif kw == "ENDTABLE" and start is not None:
            bodies.append((start.file, start.line, st.line))
            start = None
    if start is not None:
        bodies.append((start.file, start.line, 10 ** 9))
    unparsed = {(st.file, st.line) for st in program.statements if isinstance(st, S.Unparsed)}

    def late(d) -> bool:   # resolved after the table names: `CallTable T9`, an unclosed table
        return d.message.startswith("Could not find DataTable ") or d.message in (
            "DataTable ... EndTable not matched.", "No BeginProg in Program.") or \
            d.message.endswith(("must be used inside a table declaration.", "declared with no output fields.")) or \
            d.message.startswith("Invalid port specified ")

    def stage_b(d) -> bool:
        return late(d) or d.message.endswith(" is not defined.") and (d.file, d.line) not in unparsed and \
            any(f == d.file and a < d.line <= b for f, a, b in bodies)

    others = [d for d in diags.items if d.severity == "error"]
    if any(not stage_b(d) for d in others):
        return
    first = sorted([*marks, *others], key=lambda d: (late(d), d.file or "", d.line))[0]
    diags.items[:] = [d for d in diags.items if d.severity != "error"] + [first]


# CR300/CR350: codes in 1..30 that the real compiler rejects with `Illegal data type parameter %d
# in %s instruction.` alone (oracle sweep 0..30 of `Sample(1,X,n)`, 2026-10-12; 0, 16, 29 and 30
# also print a `declared with no output fields` line and 1, 17, 23 other messages, so they are
# left out there).
# CR6/CR1000X, round 36 (sweep 0..255 of `Sample`, alone and beside a second field): every code in
# 0..127 but the legal ones and 1, 14, 17 is illegal; the codes in `_FIELDLESS_DATA_TYPES` also
# give no field (with `_SILENT_FIELDLESS`, which print nothing), so a table of only those is
# `declared with no output fields` (docs/checker.md#output-data-type-codes-round-36).
_CR6_ILLEGAL = frozenset(range(128)) - {1, 2, 3, 6, 7, 9, 11, 14, 17, 18, 20, 24, 25, 28}
_FIELDLESS_DATA_TYPES = frozenset({0, 16, *range(29, 44), *range(48, 57), *range(60, 64), 94})
_SILENT_FIELDLESS = frozenset({*range(156, 160), *range(180, 184), *range(204, 212),
                               *range(220, 224), *range(236, 240), *range(252, 256)})
_ILLEGAL_DATA_TYPES = {
    "cr6": _CR6_ILLEGAL,
    "cr1000x": _CR6_ILLEGAL,
    "cr300": frozenset({2, 3, 4, 5, 8, 10, 12, 13, 14, 15, 19, 26, 27}),
    "cr350": frozenset({2, 3, 4, 5, 8, 10, 12, 13, 14, 15, 19, 26, 27}),
}
_DATA_TYPE_ARG = {"SAMPLE": 2, "AVERAGE": 2, "MAXIMUM": 2, "MINIMUM": 2, "WINDVECTOR": 3}   # WindVector: round 36, Minimum: 56


def _constant_data_type(stmt: S.Instruction, pos: int, env: consteval.Env) -> int | None:
    """The truncated constant `DataType` at `pos`, or None (not constant, negative, past 255)."""
    try:
        value = consteval.value_of(stmt.args[pos], env)
    except consteval.NotConstant:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < 256:
        return None
    return int(value)


def _fieldless(stmt: S.Instruction, profile: Profile, env: consteval.Env) -> bool:
    pos = _DATA_TYPE_ARG.get(stmt.keyword.upper())
    if profile.name not in ("cr6", "cr1000x") or pos is None or pos >= len(stmt.args) \
            or stmt.args[pos] is None:
        return False
    return _constant_data_type(stmt, pos, env) in _FIELDLESS_DATA_TYPES | _SILENT_FIELDLESS


def check_output_datatype(program: S.Program, profile: Profile, diags: Diagnostics,
                          env: consteval.Env) -> None:
    """A constant `DataType` that is no data type: `Sample(1,X,(1+2))` on CR300/CR350,
    `Sample(1,X,4)` everywhere -> `Illegal data type parameter 3 in Sample instruction.` (the value
    truncated: `2.5` is 2). Reported like `instr_rules.check_avgrun_options` (with pass 2 on
    CR6/CR1000X, alone in an otherwise clean file on CR300/CR350); oracle, 2026-10-12."""
    bad = _ILLEGAL_DATA_TYPES.get(profile.name)
    if not bad:
        return
    in_table = False   # outside a table, `must be used inside a table declaration` wins
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        in_table = kw == "DATATABLE" or (in_table and kw != "ENDTABLE")
        pos = _DATA_TYPE_ARG.get(kw)
        if pos is None or not in_table or pos >= len(stmt.args) or stmt.args[pos] is None:
            continue
        value = _constant_data_type(stmt, pos, env)
        if value is not None and value in bad:
            name = profile.instructions[stmt.keyword.upper()]["name"]
            diags.error(stmt.line, f"Illegal data type parameter {int(value)} in {name} instruction.",
                        stmt.file)


def _odd_pair_reps(stmt: S.Instruction, env: consteval.Env) -> str | None:
    """CR6/CR1000X: UINT1 / BOOL8 pack bits or bytes in pairs, so odd constant Reps is illegal
    (round 36); the message, or None."""
    pos = _DATA_TYPE_ARG.get(stmt.keyword.upper())
    if pos is None or pos >= len(stmt.args) or stmt.args[pos] is None or stmt.args[0] is None \
            or _constant_data_type(stmt, pos, env) not in (1, 17):
        return None
    try:
        reps = consteval.value_of(stmt.args[0], env)
    except consteval.NotConstant:
        return None
    if isinstance(reps, int) and not isinstance(reps, bool) and reps > 0 and reps % 2:
        return f"Reps {reps} illegal; with BOOL8 and UINT1 data types, reps must be evenly divisible by 2"
    return None


def check_odd_pair_reps(program: S.Program, profile: Profile, diags: Diagnostics,
                        env: consteval.Env) -> None:
    """`_odd_pair_reps` inside a table, with pass 2 but printed first on its line (oracle, round 38:
    ahead of `Invalid symbol in expression: Nope.` in `Sample(9,Nope,UINT1)`)."""
    if profile.name not in ("cr6", "cr1000x"):
        return
    in_table = False
    types = {v.name.upper(): v.type or "FLOAT" for st in program.statements
             if isinstance(st, S.Declaration) for v in st.vars}
    for st in program.statements:   # an `Alias` has its target's type
        if isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS" and len(st.args) > 1 \
                and isinstance(st.args[0], (S.Name, S.Index)) and isinstance(st.args[1], S.Name):
            types.setdefault(st.args[1].name.upper(), types.get(st.args[0].name.upper(), "UINT1"))
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction):
            kw = stmt.keyword.upper()
            in_table = kw == "DATATABLE" or (in_table and kw != "ENDTABLE")
            if in_table and (message := _odd_pair_reps(stmt, env)):
                diags.error(stmt.line, message, stmt.file)
            # (oracle, round 56: `Sample(1,X,UINT1)` with a Float or Long X)
            if in_table and kw == "SAMPLE" and len(stmt.args) > 2 and stmt.args[2] is not None \
                    and _constant_data_type(stmt, 2, env) == 1 and isinstance(stmt.args[1], (S.Name, S.Index)) \
                    and types.get(stmt.args[1].name.upper(), "UINT1") != "UINT1":
                diags.error(stmt.line, "Sample type UINT1 can only sample a variable of type UINT1.", stmt.file)
