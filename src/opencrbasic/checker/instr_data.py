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

import re

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
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


def check_unclosed_calltable(program: S.Program, diags: Diagnostics) -> None:
    """`Could not find DataTable %s.` (round 6, `docs/checker.md`) at a `CallTable <name>` site
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
    opened: set[str] = set()
    closed: set[str] = set()
    pending: str | None = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "DATATABLE":
            name = _table_name(stmt)
            if not name and stmt.args and isinstance(stmt.args[0], S.String):
                name = stmt.args[0].value  # `DataTable ("HIST4D",1,100)`, histogram4d_1.CR6
            if name:
                opened.add(name.upper())
                pending = name.upper()
        elif kw == "ENDTABLE":
            if pending is not None:
                closed.add(pending)
            pending = None
    # Any other target that is not a closed table fails the same way (oracle, all four loggers,
    # 2026-09-27): `CallTable Nope`, a variable `CallTable X`, `CallTable Status`, `CallTable
    # ("fuzz")` -> `fuzz`, `CallTable((1+2))` -> `1+2` (quotes and parentheses dropped).
    for stmt in program.statements:
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
        if name.upper() not in closed:
            diags.error(stmt.line, f"Could not find DataTable {name}.", stmt.file)


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
            yield f.name, False, (f.begin_index, f.begin_index + max(f.dimension, 1) - 1)
        return
    for f in fields:
        if f.ch400:
            for _ in range(max(f.dim, 1)):
                for n in _CH400_FIELDS:
                    yield n, False, (1, 1)
        else:
            yield f.name, f.renamed, f.rng


def check_duplicate_fields(program: S.Program, result, diags: Diagnostics) -> None:
    """`Duplicate field name %s in DataTable %s.` (no line number), once for every pair of
    conflicting fields, reported for the later field of the pair in table order. Rule, evidence and
    the error-buffer cap: `docs/checker.md` "Duplicate field names".

    Two fields conflict when their names match case-insensitively and either both were named by
    `FieldNames` or their source-element ranges overlap. Field names are reused from `tables.py`
    (`TdfTable._dup_fields`), which also supplies name-only FFT and CH400Status fields. A field
    named exactly `"Anonymous"` (`tables.py`'s placeholder for a non-variable source) is skipped.
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
        for name, renamed, rng in _dup_entries(table):
            if remaining < shortest:
                break
            if name == "Anonymous":
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
        if in_struct or kw != "UNITS" or not st.args:
            continue  # inside a `StructureType`, `Units` names a member (`structuretype_1.CR6`)
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
            for _ in range(len(statements) - 1):
                diags.error(st.line, f"{st.args[0].name} already assigned Units {units}.", st.file)


def check_table_and_menu_placement(program: S.Program, profile: Profile,
                                   diags: Diagnostics, env=None) -> int | None:
    """Pass 2 (oracle, suppressed by a pass-1 error): a second `DataInterval` in one table ->
    `Only one DataInterval allowed within DataTable..EndTable.` (all four loggers), and the display
    menu structure (CR6/CR1000X; CR300/CR350 have no menus), following the decompiled menu builder
    (`FUN_004309c0`, `FUN_00430aa0`, `FUN_00430b50`; oracle, 2026-09-28, `docs/checker.md`
    "Display menus"). Returns the line pass 2 stops at (`EndSubMenu without SubMenu.`), if any."""
    in_table = False
    intervals = 0
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
    item_bool = False              # the current `MenuItem`/`MenuRecompile` variable is Boolean
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
            in_table, intervals = True, 0
        elif kw == "ENDTABLE":
            in_table = False
        elif kw == "DATAINTERVAL" and in_table:
            intervals += 1
            if intervals == 2:
                diags.error(st.line, "Only one DataInterval allowed within DataTable..EndTable.",
                            st.file)
        elif kw not in profile.instructions:
            continue
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
                target = st.args[1] if kw in ("MENUITEM", "MENURECOMPILE") and len(st.args) > 1 \
                    else None
                item_bool = isinstance(target, (S.Name, S.Index)) and \
                    types.get(target.name.upper()) == "BOOLEAN"
                if kw == "SUBMENU":
                    levels.append([])
                    submenus += 1
        elif kw == "MENUPICK":
            # After a `MenuItem` or `MenuRecompile` it is accepted (chm `menuitem_2.CR6`), and
            # repeated for an ordinary variable; a ConstTable constant's item takes one pick list,
            # naming the constant first (oracle, 2026-10-01, fuzz `62246db1b167`).
            if within(st, kw):
                if not levels[-1] or levels[-1][-1] not in ("MENUITEM", "MENURECOMPILE"):
                    diags.error(st.line, "MenuPick must follow a MenuItem instruction.", st.file)
                elif levels[-1][-1] == "MENUITEM" and item_const:
                    first = st.args[0] if st.args else None
                    if isinstance(first, S.Name) and first.name.upper() in table_consts:
                        levels[-1].append(kw)
                    else:
                        diags.error(st.line, "When using a ConstTable constant in MenuPick, the "
                                    "constant name must be the first parameter.", st.file)
                bad = next((a for a in st.args if isinstance(a, S.Name)
                            and a.name.upper() in variables), None)
                if item_bool and levels[-1] and levels[-1][-1] in ("MENUITEM", "MENURECOMPILE"):
                    _check_boolean_picklist(st, env, diags)
                elif bad is not None:
                    # `MenuPick (Y)` for `Public Y` (oracle, 2026-10-01)
                    diags.error(st.line, f"{bad.name} must be declared as a Const.", st.file)
        elif kw == "ENDSUBMENU":
            if not submenus:
                diags.error(st.line, "EndSubMenu without SubMenu.", st.file)
                return st.line
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
      index 4, reps 3, out of bounds.`. The first such table statement ends the stage.
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
            diags.error(st.line, profile.unresolved_symbol_message.format(name=name), st.file)
        if expressions._has_undefined_index(arg, known):
            diags.error(st.line, expressions.dimensions_message(
                expressions._arg_text(st, i).strip(), profile, False), st.file)
        return True
    return False


def _check_stage2a(program: S.Program, profile: Profile, diags: Diagnostics, env,
                   destinations: bool) -> bool:
    from . import expressions
    reported = False
    dims = symbols.declared_all_dims(program, env)
    known = expressions._known_names(program, profile) if not destinations else set()
    for st in program.statements:
        if not isinstance(st, S.Instruction):
            continue
        kw = st.keyword.upper()
        record = profile.instructions.get(kw)
        if not record:
            continue
        if destinations and kw not in _OWN_VARIABLE_CHECK:
            for i, code in enumerate(record["params"][:len(st.args)]):
                arg = st.args[i]
                if code in (8, 27, 29) and arg is not None and not isinstance(arg, S.String) \
                        and not _has_names(arg):  # (27/29: `Erase(5)`, `RealTime(5)`, 2026-10-01)
                    diags.error(st.line, f"Invalid Variable or Array: {expressions._arg_text(st, i).strip()}.",
                                st.file)
                    reported = True
        if kw in _TABLE_OUTPUT and len(st.args) >= 2:
            reps = _reps_value(st.args[0], env)
            if reps is not None and reps < 1:
                diags.error(st.line, f"Illegal Reps {reps}", st.file)
                return True
            if not destinations and _report_undefined_source(st, record, known, profile, diags):
                return True
            source = st.args[1]
            if reps is None or not isinstance(source, (S.Name, S.Index)):
                continue
            shape = dims.get(source.name.upper())
            if shape is None or len(shape) != 1:
                continue
            start = 1
            if isinstance(source, S.Index):
                if len(source.args) != 1 or source.args[0] is None:
                    continue
                start = _reps_value(source.args[0], env)
                if start is None:
                    continue
            if start + reps - 1 > shape[0]:
                name = next((v.name for d in program.statements if isinstance(d, S.Declaration)
                             for v in d.vars if v.name.upper() == source.name.upper()), source.name)
                diags.error(st.line, f"Variable {name} out of bounds.", st.file)
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
        if kw == "DATATABLE" and st.args and isinstance(st.args[0], S.Name):
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
