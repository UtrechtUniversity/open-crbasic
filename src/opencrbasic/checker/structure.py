"""Structural checks: `BeginProg`/`EndProg` presence, `Scan`/`NextScan` and `If`/`Select` block
matching, undefined explicit `Call` targets, and the generic "not a valid statement" fallback for
a statement our parser could not turn into a recognised shape. Rules and oracle evidence:
`docs/checker.md`.
"""

from __future__ import annotations

import dataclasses
import re

from .. import consteval
from .. import lexer
from .. import parser
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import expressions, instr_data, instr_rules, symbols

# Array-dimension limits on a `Public`/`Dim` declaration (oracle-confirmed, `docs/checker.md`
# "Array dimension limits"): at most 3 dimensions, and each one's constant value must be in
# `[1, _MAX_DIM_VALUE]`. `_MAX_DIM_VALUE` is `INT32_MAX` -- every value at or below it that this
# session could actually get the real compiler to accept/reject cleanly (rather than crash) falls
# on the expected side of this boundary; a handful of merely-too-large-to-allocate values well
# below it instead get `Out of memory.` (a distinct, not-yet-implemented message, `docs/
# checker.md`), so this check only ever fires where "Illegally dimension" is the confirmed text.
_MAX_ARRAY_DIMS = 3
_MAX_DIM_VALUE = 2147483647

# `Unparsed.reason` values from two oracle-confirmed parser gaps (docs/frontend.md "Known
# residual gaps"): the real compiler still accepts these lines, so the generic "not a valid
# statement" fallback below must stay silent for them. Matched as a prefix so unrelated
# `reason` text is never accidentally suppressed.
_KNOWN_PARSER_GAP_REASONS = (
    "unexpected trailing input starting at '+='",   # samplemaxmin_1.CR6: `X=X+=1`
    "expected ')', found '.6'",                      # snmpvariable_1.CR6: a dotted-OID literal
)

# `Unparsed.reason` values that never denote real statement content (nothing to echo or reject).
_BENIGN_REASONS = ("empty statement",)

# `parser.py`'s `_parse_units_segment` reason for a `Units` segment with no `=` at all (most often
# a CHM corpus example that wrapped `Units Var = Label` across two physical lines with no `_`
# continuation, `Units` alone on its own line -- `am25t_1.CR6` etc., `docs/checker.md`).
_UNITS_WITHOUT_EQ = "Units without '='"

# `parser.py`'s `_parse_const`/`_parse_alias` reasons for the same CHM line-wrap shape applied to
# `Const`/`Alias` (`docs/checker.md`): `Const` never echoes any trailing text (`Const with no
# assignment.` is a fixed string, oracle-confirmed on both a fully empty `Const` and one truncated
# partway through its own grammar, `rectpolar_1.CR6`/`sprintf_4.CR6`); `Alias` echoes an (empty
# when no target at all was parsed) trailing target, like `Units`.
_CONST_WITHOUT_EQ = "Const without '='"
_ALIAS_WITHOUT_EQ = "Alias without '='"

# `parser.py`'s `_parse_alias`/`_parse_units_segment` reason for the *other* line-wrap shape: a
# target *is* present and parses as a valid lvalue, but the `=` itself is what's missing from this
# physical line (`realtime_1.CR6`: `Alias rTime(1)` / `= Year` on the next line, no `_`
# continuation). Oracle-confirmed (`docs/checker.md` "Echo quirk"): the echo embeds this line's own
# trailing newline before the closing `.` -- `Need '=' in alias definition: rTime(1)\n.` -- because
# the real parser's own line-buffer read includes it. Distinct from `_ALIAS_WITHOUT_EQ`/
# `_UNITS_WITHOUT_EQ` above (no target parsed at all: plain empty echo, no newline).
_ALIAS_TARGET_NO_EQ = "Alias target with no '='"
_UNITS_TARGET_NO_EQ = "Units target with no '='"

# `parser.py`'s `_parse_vardecl` reason for a `Public`/`Dim` variable whose own type clause is
# malformed -- a token right after the name/dims that is neither `As`, `=`, a comma nor end of
# line, most often a corrupted spelling of `As` itself (`docs/fuzz-findings.md` "5. Message diff",
# `docs/checker.md` "Invalid Variable declaration"). The reason string carries the offending
# token's own text after this prefix.
_INVALID_AS_CLAUSE = "Invalid As clause: "

# `parser.py`'s two "Need closing ) shapes" markers (`UNCLOSED_PAREN_EXPR`/`UNCLOSED_DIM_PREFIX`,
# oracle evidence in `docs/checker.md`): a genuinely unclosed parenthesis reached through the
# general expression grammar (`_UNCLOSED_PAREN_EXPR`, pass 2 -- the real compiler's own `No
# Matching, or out of place ) in expression: %s.`, `%s` = the whole statement's own verbatim
# source, same echo convention as the generic fallback below) versus one in a `Public`/`Dim`/
# `Static` dimension list (`_UNCLOSED_DIM_PREFIX`, pass 1 -- `%s is missing the ) character.`, `%s`
# = the reason's own trailing text, *not* `stmt.text`: just the variable's name through the end of
# the line, not the `Public`/`Dim` keyword). Matched against `parser.UNCLOSED_PAREN_EXPR`/
# `parser.UNCLOSED_DIM_PREFIX` themselves (imported, not re-typed) so the two files can never drift.
_UNCLOSED_PAREN_EXPR = parser.UNCLOSED_PAREN_EXPR
_UNCLOSED_DIM_PREFIX = parser.UNCLOSED_DIM_PREFIX

# `parser.py`'s pre-existing generic "leftover token after an otherwise-complete statement" reason,
# specifically an extra/unmatched `)` (`samplemaxmin_1.CR6`-style already-known reasons above cover
# the *other* leftover-token shapes) -- oracle-confirmed (`docs/checker.md` "Need closing )
# shapes") the real compiler's own message here is `Incomplete expression: %s.`, not the generic
# "Invalid, or out of place expression" fallback, `%s` = `stmt.text` (the whole statement, same
# echo convention).
_EXTRA_CLOSE_PAREN = "unexpected trailing input starting at ')'"

# Named, `EndXxx`-terminated body blocks conventionally declared before `BeginProg`, alongside
# `Sub`/`Function` (`S.SubDef`, tracked separately since it is its own AST node type, not a plain
# `S.Instruction`): a statement lexically inside one of these is never subject to the "before
# `BeginProg`" placement check in `check_pass2`, regardless of its own textual line. See that
# function's docstring for the oracle/corpus evidence behind each one.
#
# **Round 5 finding, tried and reverted**: `DATATABLE`/`CONSTTABLE`/`ENDTABLE`/`ENDCONSTTABLE`
# (and, by the same reasoning, `STRUCTURETYPE`/`OBJECTTYPE`) look like the same shape at first --
# a `DataTable...EndTable` body's own `Sample`/`Average`/... field instructions are routinely,
# legitimately placed before `BeginProg`, a 740-accepted-file corpus safety scan this round
# (`docs/checker.md` "Round 5") confirmed -- but adding them here is **wrong**: a full
# `tools/difftest.py --logger cr6 --group chm` re-run caught a real regression,
# `tests/corpus/chm/solarposition_1.CR6` (`exact` -> `verdict`), a malformed, unresolved statement
# (a CHM prose-wrap artifact, `'must be` / `in units of seconds when used in SolarPosition` split
# across two physical lines with no `'` on the second) **inside** a `ConstTable...EndConstTable`
# body still gets the real compiler's own before-`BeginProg` placement follow-up
# (`Expr illegally occurs before BeginProg.`), unlike the same shape inside a genuine `Sub`/
# `Function`/`DialSequence`/... body. The distinguishing property these body blocks share and
# `DataTable`/`ConstTable`/`StructureType`/`ObjectType` do not: their contents are *deferred*,
# invoked-later code (a `Sub` only runs when called, a `DialSequence` only runs when dialled), while
# a `DataTable`/`ConstTable`/`StructureType` body is compiled immediately, in place -- an unresolved
# statement inside one is exactly as illegal as one at the true top level, oracle-confirmed by this
# one corpus counter-example. Left out of this set entirely; `Sample`/`Average`/etc.'s own
# legitimate placement inside a `DataTable` body was never actually at risk (they are recognised,
# resolving `Instruction`s, not `Call`/`Unparsed`/`Assignment` records this check's own three
# branches would ever flag), so nothing needed this addition in the first place -- the 4-file
# `cr1000x` `chm` exact improvement this change also produced during development was a
# coincidental side effect of it, not independent evidence it was correct, and was given up along
# with the fix once the regression was found (AGENTS.md: verify a generalisation against the whole
# corpus, not a hand-written probe or a partial metric, before trusting it).
_BODY_BLOCK_STARTS = frozenset({
    "DIALSEQUENCE", "MODEMHANGUP", "MENU", "SUBMENU", "VOICE", "BURSTTRIGGER",
    "APPLYANDRESTARTSEQUENCE", "PROCHIPRI",
})
_BODY_BLOCK_ENDS = frozenset({
    "ENDDIALSEQUENCE", "ENDMODEMHANGUP", "ENDMENU", "ENDSUBMENU", "ENDVOICE", "ENDBURSTTRIGGER",
    "ENDAPPLYANDRESTARTSEQUENCE", "ENDPROCHIPRI",
})

# Instructions rejected with `%s illegally occurs before BeginProg.` (`%s` = the table name), which
# ends pass 2. The compiler's placement loop (decompiled `FUN_00424160`) flags the first
# code-generating instruction outside a declaration block (`DataTable`, `Sub`, `WebPageBegin`, ...)
# before `BeginProg`. Whether an instruction generates code depends on its own compile function
# (`SetStatus`, and `SetSetting` with a constant value, do not), so beyond `Scan`/`Delay` only
# oracle-probed keywords are listed, per logger, in `profile.before_beginprog` (`docs/checker.md`
# "Instructions before BeginProg").
_FORBIDDEN_BEFORE_BEGINPROG = frozenset({"SCAN", "DELAY"})
# Declaration blocks for that check besides `DataTable` and `Sub` bodies.
_DECL_BLOCK_BOUNDS = {"WEBPAGEBEGIN": True, "WEBPAGEEND": False,
                      "SHUTDOWNBEGIN": True, "SHUTDOWNEND": False}





def _track_tables(stmt: S.Stmt, kw: str, state: dict) -> None:
    """Closed `DataTable` names so far, in walk order (`state["closed"]`): a `CallTable` of a table
    not yet declared and closed gets `Could not find DataTable %s.` instead of the placement
    checks (oracle, 2026-09-27)."""
    args = getattr(stmt, "args", []) or []
    if kw == "DATATABLE":
        state["open"] = args[0].name.upper() if args and isinstance(args[0], S.Name) else None
    elif kw == "ENDTABLE" and state.get("open"):
        state.setdefault("closed", set()).add(state.pop("open"))


def _calltable_known(stmt: S.Stmt, state: dict) -> bool:
    args = getattr(stmt, "args", []) or []
    return bool(args) and isinstance(args[0], S.Name) and \
        args[0].name.upper() in state.get("closed", set())


def _scan_counts_for_table_placement(stmt: S.Stmt) -> bool:
    """Whether this parsed ``Scan`` contains a valid literal interval for ``CallTable`` placement.

    The compilers leave a literal-zero ``Scan`` structurally open for its matching ``NextScan``,
    but a ``CallTable`` within it is still outside a Scan for an auto-sized table.  Keep this
    intentionally narrow: only the observed literal-zero case is excluded here.
    """
    args = getattr(stmt, "args", []) or []
    return not (args and isinstance(args[0], S.Number) and args[0].value == 0)


def check_zero_scan_auto_table_placement(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR6/CR1000X's ungated auto-table message within a literal-zero ``Scan``.

    Their Scan-interval diagnostic is emitted before pass 2, yet this placement diagnostic still
    follows it.  The regular pass-2 walk is therefore unavailable for this one observed shape.
    """
    if profile.single_pass:
        return
    tables: dict = {}
    scans: list[bool] = []
    in_table = False
    for stmt in program.statements:
        kw = _keyword(stmt)
        _track_tables(stmt, kw, tables)
        if kw == "DATATABLE":
            in_table = True
        elif kw == "ENDTABLE":
            in_table = False
        elif kw == "SCAN":
            scans.append(_scan_counts_for_table_placement(stmt))
        elif kw == "NEXTSCAN" or (kw == "NEXT" and
                                    _block_word(getattr(stmt, "args", []) or []) == "SCAN"):
            if scans:
                scans.pop()
        elif kw == "CALLTABLE" and not in_table and scans and not any(scans) and \
                _calltable_known(stmt, tables):
            target = stmt.args[0] if stmt.args else None
            if not isinstance(target, S.Name):
                continue
            for table_stmt in program.statements:
                if _keyword(table_stmt) != "DATATABLE" or not table_stmt.args or \
                        not isinstance(table_stmt.args[0], S.Name) or \
                        table_stmt.args[0].name.upper() != target.name.upper():
                    continue
                size = table_stmt.args[2] if len(table_stmt.args) > 2 else None
                auto = isinstance(size, S.Unary) and size.op == "-" or \
                    isinstance(size, S.Number) and size.value < 0
                if auto:
                    diags.error(stmt.line, f"Cannot autoallocate memory for DataTable "
                                f"{table_stmt.args[0].name} called outside of Scan, e.g., "
                                "called by Sub.", stmt.file)
                break


def _check_calltable_outside_scan(stmt: S.Instruction, program: S.Program, diags: Diagnostics,
                                  in_sub: bool) -> None:
    """`CallTable` outside any `Scan` (before it, after `NextScan`, or in a `Sub`), pass 2, all four
    loggers (oracle, 2026-09-27): a table with `DataInterval` -> `Table with DataInterval must be
    called within Scan..NextScan.`; an auto-sized one (negative size) -> `Cannot autoallocate memory
    for DataTable %s called outside of Scan, e.g., called by Sub.`; a fixed-size table without
    `DataInterval` is fine. Inside a `Sub` only the second (probed) message is emitted."""
    target = stmt.args[0] if stmt.args else None
    if not isinstance(target, S.Name):
        return
    name = target.name.upper()
    table = None
    has_interval = False
    for st in program.statements:
        kw = _keyword(st)
        if kw == "DATATABLE":
            args = getattr(st, "args", []) or []
            table = st if args and isinstance(args[0], S.Name) and args[0].name.upper() == name else None
            has_interval = False
        elif kw == "DATAINTERVAL" and table is not None:
            has_interval = True
        elif kw == "ENDTABLE" and table is not None:
            break
    if table is None:
        return
    size = table.args[2] if len(table.args) > 2 else None
    auto = isinstance(size, S.Unary) and size.op == "-" or \
        isinstance(size, S.Number) and size.value < 0
    if has_interval and not in_sub:
        diags.error(stmt.line, "Table with DataInterval must be called within Scan..NextScan.",
                    stmt.file)
    if auto and not (in_sub and has_interval):
        diags.error(stmt.line, f"Cannot autoallocate memory for DataTable {table.args[0].name} "
                    "called outside of Scan, e.g., called by Sub.", stmt.file)

def _ends_sub(stmt: S.Stmt, kw: str) -> bool:
    return kw in ("ENDSUB", "ENDFUNCTION") or (
        kw == "END" and _block_word(getattr(stmt, "args", []) or []) in ("SUB", "FUNCTION"))


def first_misplaced_sub(program: S.Program) -> tuple[S.SubDef, list[str]] | None:
    """The first `Sub`/`Function` declared where the compiler rejects it, with its messages, or
    `None`. Oracle, all four loggers, 2026-10-02: inside another `Sub`/`Function` (`Sub declared
    within a Sub.`, whatever the keywords), inside an `If`/`Select` (`Sub declaration inside
    If..EndIf.`), and `Sub|Function is declared in the middle of an executing sequence` for any of
    those or inside an open `Scan`, between `BeginProg` and the first `Scan`, or inside a
    `DataTable`. After the first `NextScan`, a `SlowSequence` header and `EndProg` it is accepted.
    The message ends compilation (a placement error, `docs/checker.md` "Pass model")."""
    global_variables: set[str] = set()
    in_routine = False
    for stmt in program.statements:
        kw = _keyword(stmt)
        if isinstance(stmt, S.SubDef):
            in_routine = True
        elif _ends_sub(stmt, kw):
            in_routine = False
        elif not in_routine and isinstance(stmt, S.Declaration):
            global_variables.update(v.name.upper() for v in stmt.vars)

    open_sub = in_table = before_sequence = False
    conditional_depth = active_scans = for_do_depth = 0
    routines: set[str] = set()
    for stmt in program.statements:
        kw = _keyword(stmt)
        if isinstance(stmt, S.SubDef):
            executing = (open_sub or in_table or before_sequence or active_scans > 0
                         or conditional_depth > 0 or for_do_depth > 0)
            duplicate = stmt.name.upper() in routines
            variable = stmt.name.upper() in global_variables
            if executing or duplicate or variable:
                msgs = ["Sub declared within a Sub."] if open_sub else []
                if duplicate:
                    msgs.append(f"Duplicate Sub {stmt.name}")
                if variable:
                    msgs.append(f"{stmt.name} already declared as a variable.")
                if conditional_depth > 0:
                    msgs.append("Sub declaration inside If..EndIf.")
                if executing:
                    msgs.append(f"{'Function' if stmt.keyword.upper() == 'FUNCTION' else 'Sub'} "
                                "is declared in the middle of an executing sequence")
                return stmt, msgs
            routines.add(stmt.name.upper())
            open_sub = True
        elif _ends_sub(stmt, kw):
            open_sub, conditional_depth = False, 0
        elif kw == "BEGINPROG":
            before_sequence = True
        elif kw == "ENDPROG":
            before_sequence = False
        elif kw == "SLOWSEQUENCE":
            before_sequence = False
        elif kw == "SCAN":
            active_scans += 1
            before_sequence = False
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(getattr(stmt, "args", []) or []) == "SCAN"):
            active_scans = max(0, active_scans - 1)
        elif kw == "DATATABLE":
            in_table = True
        elif kw == "ENDTABLE":
            in_table = False
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            conditional_depth += 1
        elif kw == "ENDSELECT" or kw == "ENDIF" or (
                kw == "END" and _block_word(getattr(stmt, "args", []) or []) in ("SELECT", "IF")):
            conditional_depth = max(0, conditional_depth - 1)
        elif kw in ("FOR", "DO", "WHILE"):
            for_do_depth += 1
        elif kw in ("LOOP", "WEND") or (kw == "NEXT" and _block_word(
                getattr(stmt, "args", []) or []) != "SCAN"):
            for_do_depth = max(0, for_do_depth - 1)
    return None


def first_misplaced_data_table(program: S.Program, profile) -> S.Instruction | None:
    """First `DataTable` declared in executable code, if any.

    Oracle, all four loggers, 2026-10-02: a table between `BeginProg` and the first sequence,
    or inside a `Scan`, `If`/`Select`, `For`, or `Do`, is `DataTable is declared in the middle of
    an executing sequence`. A table before `BeginProg`, after `NextScan`, and directly after a
    `SlowSequence` header is accepted. CR6/CR1000X's already-reported `DO .. LOOP cannot span
    across sequences.` suppresses the table placement report for a table after that `SlowSequence`.
    """
    prescan = in_table = False
    scans = blocks = 0
    slow_block_error = False
    for stmt in program.statements:
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []
        if kw == "DATATABLE":
            if not in_table and not slow_block_error and (prescan or scans or blocks):
                return stmt
            in_table = True
        elif kw == "ENDTABLE":
            in_table = False
        elif kw == "BEGINPROG":
            prescan = True
        elif kw == "ENDPROG":
            prescan = False
        elif kw == "SLOWSEQUENCE":
            if blocks and profile.name in ("cr6", "cr1000x"):
                slow_block_error = True
            prescan = False
        elif kw == "SCAN":
            scans += 1
            prescan = False
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(args) == "SCAN"):
            scans = max(0, scans - 1)
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)) \
                or kw in ("FOR", "DO", "WHILE"):
            blocks += 1
        elif kw in ("ENDIF", "ENDSELECT", "LOOP", "WEND") or \
                (kw == "END" and _block_word(args) in ("IF", "SELECT")) or \
                (kw == "NEXT" and _block_word(args) != "SCAN"):
            blocks = max(0, blocks - 1)
    return None


def check_data_table_placement(program: S.Program, profile, diags: Diagnostics) -> None:
    """Emit the `DataTable` executable-placement error in the profile's appropriate phase."""
    stmt = first_misplaced_data_table(program, profile)
    if stmt is not None:
        diags.error(stmt.line, "DataTable is declared in the middle of an executing sequence",
                    stmt.file)


def pipeline_task_errors(program: S.Program, profile) -> list[tuple[S.Stmt, str]]:
    """CR6/CR1000X, pass 2, accumulating (it does not end the pass): after `PipeLineMode` (and no
    `SequentialMode`) earlier in the file, a `_PIPELINE_TASKS` instruction outside any `Scan` (a
    `Sub` body, before the first `Scan`, a `SlowSequence` header, after `NextScan`) is `X not
    allowed outside of Scan..NextScan in PipeLine Mode.`; inside a `Scan` but nested in an
    `If`/`Select`/`For`/`Do`/`While` (or a one-line `If`) it is `X not allowed inside of a
    conditional statement in PipeLine Mode.`. `Delay` only with a constant option 0. The message
    comes before the instruction's own argument errors (`Invalid channel ...`), except `ExciteV`
    on CR6 and `PortSet` on CR1000X; those are not reordered.

    Returns `(statement, message)` pairs in order."""
    found: list[tuple[S.Stmt, str]] = []
    if profile.single_pass:
        return found
    task_rules = profile.pipeline_tasks["tasks"]
    pipeline = sequential = False
    scans = depth = 0

    def visit(stmt: S.Stmt, inline: bool) -> None:
        nonlocal pipeline, sequential, scans, depth
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []
        word = _block_word(args) if kw in ("END", "NEXT") else None
        if kw == "SEQUENTIALMODE":
            sequential = True
        elif kw == "PIPELINEMODE":
            pipeline = True
        elif kw == "SCAN":
            scans += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and word == "SCAN"):
            scans = max(0, scans - 1)
        elif kw in ("SELECT", "FOR", "DO", "WHILE") or (kw == "IF" and not (
                isinstance(stmt, S.If) and stmt.inline is not None)):
            depth += 1
        elif kw in ("ENDIF", "ENDSELECT", "LOOP", "WEND") or (
                kw == "NEXT" and word != "SUBSCAN") or (kw == "END" and word in ("IF", "SELECT")):
            depth = max(0, depth - 1)
        elif kw in task_rules and pipeline and not sequential and isinstance(stmt, S.Instruction):
            constants = task_rules[kw].get("constant_values", {})
            for position, allowed in constants.items():
                index = int(position) - 1
                if (index >= len(args) or not isinstance(args[index], S.Number)
                        or args[index].value not in allowed):
                    return
            record = profile.instructions.get(kw)
            name = record["name"] if record else stmt.keyword
            if not scans and not task_rules[kw].get("conditional_only"):
                found.append((stmt, f"{name} not allowed outside of Scan..NextScan in PipeLine "
                              "Mode."))
            elif depth or inline:
                found.append((stmt, f"{name} not allowed inside of a conditional statement in "
                              "PipeLine Mode."))
        if isinstance(stmt, S.If) and stmt.inline is not None:
            for inner in (stmt.inline or []) + (stmt.inline_else or []):
                visit(inner, True)

    for stmt in program.statements:
        visit(stmt, False)
    return found


def check_pipeline_task_placement(program: S.Program, profile, diags: Diagnostics) -> None:
    for stmt, message in pipeline_task_errors(program, profile):
        diags.error(stmt.line, message, stmt.file)


def pipeline_argument_error_first_locations(program: S.Program, profile) -> set[tuple[str, int]]:
    return {
        (stmt.file, stmt.line)
        for stmt, _message in pipeline_task_errors(program, profile)
        if _keyword(stmt) in profile.pipeline_tasks.get("argument_error_first", [])
    }


def check_slow_sequence_blocks(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR6/CR1000X, pass 2, accumulating (oracle, 2026-10-02; CR300/CR350 accept): a `SlowSequence`
    inside an open block. At the `SlowSequence` line, one message per kind of open block, in this
    order: `FOR .. NEXT cannot span across sequences.`, `DO .. LOOP cannot span across
    sequences.` (`Do` and `While`), `IF or SELECT cannot span sequences.`. Not per nesting level
    (two open `Do`s give one message), and every later `SlowSequence` repeats it. The depth of each
    kind is counted separately, so a `Loop` closes a `Do` even with an `If` opened after it. A
    one-line `If` is not a block."""
    if profile.single_pass:
        return
    fors = dos = ifs = 0
    for stmt in program.statements:
        kw = _keyword(stmt)
        word = _block_word(getattr(stmt, "args", []) or []) if kw in ("END", "NEXT") else None
        if kw == "FOR":
            fors += 1
        elif kw == "NEXT" and word not in ("SCAN", "SUBSCAN"):
            fors = max(0, fors - 1)
        elif kw in ("DO", "WHILE"):
            dos += 1
        elif kw in ("LOOP", "WEND"):
            dos = max(0, dos - 1)
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            ifs += 1
        elif kw in ("ENDIF", "ENDSELECT") or (kw == "END" and word in ("IF", "SELECT")):
            ifs = max(0, ifs - 1)
        elif kw == "SLOWSEQUENCE":
            for depth, message in ((fors, "FOR .. NEXT cannot span across sequences."),
                                   (dos, "DO .. LOOP cannot span across sequences."),
                                   (ifs, "IF or SELECT cannot span sequences.")):
                if depth:
                    diags.error(stmt.line, message, stmt.file)


def check_scan_placement_late(program: S.Program, profile, diags: Diagnostics) -> tuple | None:
    """CR300/CR350: `CallTable` outside `Scan`, the first extra `NextScan` and the before-`BeginProg`
    placement errors, in statement order, after the table `Reps` checks and before the
    unclosed-block checks (oracle, 2026-09-27: an unrelated `X = Q` or `Sample (0,X,FP2)` hides
    them; a `CallTable` error and a stray `NextScan` print together; an unclosed `Do` is then not
    reported). A stray `NextScan` or a placement error ends the walk; its `(file, line)` is
    returned so the caller can drop later warnings. CR6/CR1000X report these in `check_pass2`."""
    if not profile.single_pass:
        return None
    in_table = in_body = in_decl = seen_beginprog = False
    scans: list[bool] = []
    tables: dict = {}
    subs = symbols.declared_subs(program)
    stray = stray_controls(program)
    stray.update({k: (m, True) for k, m in scan_only_outside(program).items()})
    misplaced = first_misplaced_sub(program)
    for stmt in program.statements:
        if misplaced and stmt is misplaced[0]:
            for message in misplaced[1]:
                diags.error(stmt.line, message, stmt.file)
            return stmt.file, stmt.line
        if id(stmt) in stray:
            diags.error(stmt.line, stray[id(stmt)][0], stmt.file)
            if stray[id(stmt)][1]:
                return stmt.file, stmt.line
        seen_beginprog = seen_beginprog or _keyword(stmt) == "BEGINPROG"
        if isinstance(stmt, S.SubDef):
            in_body = True
            continue
        kw = _keyword(stmt)
        in_decl = _DECL_BLOCK_BOUNDS.get(kw, in_decl)
        _track_tables(stmt, kw, tables)
        if kw == "DATATABLE":
            in_table = True
        elif kw == "ENDTABLE":
            in_table = False
        elif kw == "SCAN":
            scans.append(_scan_counts_for_table_placement(stmt))
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(getattr(stmt, "args", []) or []) == "SCAN"):
            if not scans:
                diags.error(stmt.line, "NextScan with no Scan.", stmt.file)
                return stmt.file, stmt.line
            scans.pop()
        elif kw == "CALLTABLE" and not in_table and (not any(scans) or in_body) and \
                _calltable_known(stmt, tables):
            _check_calltable_outside_scan(stmt, program, diags, in_body)
        if kw in _BODY_BLOCK_STARTS:
            in_body = True
        elif kw in ("ENDSUB", "ENDFUNCTION") or kw in _BODY_BLOCK_ENDS:
            in_body = False
        elif not (seen_beginprog or in_body or in_table or in_decl) and \
                (kw != "CALLTABLE" or _calltable_known(stmt, tables)):
            display = _before_beginprog_name(stmt, kw, profile, subs)
            if display:
                diags.error(stmt.line, f"{display} illegally occurs before BeginProg.", stmt.file)
                return stmt.file, stmt.line
    return None


def _is_case_else(stmt: S.Case) -> bool:
    """`Case Else`, matched as a prefix: `Case Else = 3` and `Case Elsewhere` count too (oracle,
    2026-09-27: `Else with no If.` outside a `Select`, a duplicate `Else` after `Case Else`)."""
    return stmt.is_else or re.match(r"\s*case\s+else", getattr(stmt, "text", ""), re.I) is not None


def stray_controls(program: S.Program) -> dict[int, tuple[str, bool]]:
    """`id(stmt) -> (message, stops)` for control statements outside their block (oracle,
    2026-09-27, `docs/checker.md` "Stray control statements"). `Else`/`Case Else` need an open
    `If` or `Select`, `ElseIf` too (`Case with no Select.`); both stop. A second `Else` in one `If`
    or `Select`, `Exit Do`/`Exit For` with no open loop, `Exit Sub`/`Exit Function` outside a `Sub`
    or `Function`, and `ExitScan`/`ContinueScan` with no open `Scan` do not stop."""
    found: dict[int, tuple[str, bool]] = {}
    elses: list[bool] = []
    loops = {"DO": 0, "FOR": 0}
    scans = 0
    in_sub = False
    for stmt in program.statements:
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []
        word = _block_word(args) if kw in ("END", "NEXT", "EXIT") else None
        message, stops = None, False
        if isinstance(stmt, S.SubDef):
            in_sub = True
        elif kw in ("ENDSUB", "ENDFUNCTION") or (kw == "END" and word in ("SUB", "FUNCTION")):
            in_sub = False
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            elses.append(False)
        elif kw in ("ENDIF", "ENDSELECT") or (kw == "END" and word in ("IF", "SELECT")):
            if elses:
                elses.pop()
        elif kw == "ELSE" or isinstance(stmt, S.Case) and _is_case_else(stmt):
            if not elses:
                message, stops = "Else with no If.", True
            elif elses[-1]:
                message = "If statement with duplicate Else."
            else:
                elses[-1] = True
        elif kw == "ELSEIF" and not elses:
            message, stops = "Case with no Select.", True
        elif kw in ("DO", "FOR"):
            loops[kw] += 1
        elif kw == "LOOP":
            loops["DO"] = max(0, loops["DO"] - 1)
        elif kw == "NEXT" and word not in ("SCAN", "SUBSCAN"):
            loops["FOR"] = max(0, loops["FOR"] - 1)
        elif kw == "SCAN":
            scans += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and word == "SCAN"):
            scans = max(0, scans - 1)
        elif kw == "EXIT" and word in ("DO", "FOR") and loops[word] == 0:
            message = f"Exit {word.capitalize()} without {word}."
        elif kw == "EXIT" and word in ("SUB", "FUNCTION") and not in_sub:
            message = "Exit Sub without Sub or nested subs."
        elif kw in ("EXITSCAN", "CONTINUESCAN") and scans == 0:
            message = f"{'ExitScan' if kw == 'EXITSCAN' else 'ContinueScan'} not within a Scan."
        if message:
            found[id(stmt)] = (message, stops)
    return found


# Measurements whose compile function rejects them outside a `Scan` (including in a `Sub`) and
# ends pass 2 (decompiled `FUN_0050e960` and `PulseCount`'s own copy; oracle, 2026-09-27).
SCAN_ONLY = {"PULSECOUNT": "PulseCount", "QUADRATURE": "Quadrature", "TIMERINPUT": "TimerInput"}


def scan_only_outside(program: S.Program) -> dict[int, str]:
    """`id(stmt) -> message` for a `SCAN_ONLY` measurement outside any `Scan`."""
    found: dict[int, str] = {}
    scans = 0
    in_sub = False
    for stmt in program.statements:
        kw = _keyword(stmt)
        if isinstance(stmt, S.SubDef):
            in_sub = True
        elif kw in ("ENDSUB", "ENDFUNCTION"):
            in_sub = False
        elif kw == "SCAN":
            scans += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(getattr(stmt, "args", []) or []) == "SCAN"):
            scans = max(0, scans - 1)
        elif kw in SCAN_ONLY and isinstance(stmt, S.Instruction) and (in_sub or scans == 0):
            found[id(stmt)] = f"{SCAN_ONLY[kw]} must be used within a Scan...NextScan."
    return found


def check_stray_controls(program: S.Program, profile, diags: Diagnostics) -> int | None:
    """CR6/CR1000X pass 2 (`stray_controls`); returns the `(file, line)` pass 2 stops at, if any. CR300/CR350
    report these in `check_scan_placement_late`."""
    if profile.single_pass:
        return None
    found = stray_controls(program)
    found.update({k: (m, True) for k, m in scan_only_outside(program).items()})
    for stmt in program.statements:
        if id(stmt) in found:
            message, stops = found[id(stmt)]
            diags.error(stmt.line, message, stmt.file)
            if stops:
                return stmt.file, stmt.line
    return None


def _has_error_on(stmt: S.Stmt, diags: Diagnostics) -> bool:
    """CR300/CR350: a stray `EndIf`/`Loop`/`NEXT` before `BeginProg` gets its placement error right
    after its block message, not in the late walk (oracle, 2026-09-27)."""
    return any(d.severity == "error" and d.line == stmt.line and d.file == stmt.file
               for d in diags.items)


def _before_beginprog_name(stmt: S.Stmt, kw: str, profile, subs: set[str]) -> str | None:
    """The name in `%s illegally occurs before BeginProg.` for a statement outside any block before
    `BeginProg`, or `None` when it may appear there."""
    control = _control_keyword(stmt)
    if control or isinstance(stmt, S.Instruction) and \
            (kw in _FORBIDDEN_BEFORE_BEGINPROG or kw in profile.before_beginprog):
        record = profile.instructions.get(control or kw)
        return record["name"] if record else (control or stmt.keyword)
    if isinstance(stmt, S.Assignment):
        return "Expr"
    if isinstance(stmt, S.Call):
        return "Call" if stmt.name.upper() in subs else \
            "Expr" if stmt.name.upper() in profile.functions else None
    return None


def _control_keyword(stmt: S.Stmt) -> str | None:
    """The instruction-table keyword of a control statement that may not precede `BeginProg`."""
    if isinstance(stmt, S.If):
        return "IF"
    if isinstance(stmt, S.For):
        return "FOR"
    if isinstance(stmt, S.DoLoop):
        return stmt.keyword.upper()
    if isinstance(stmt, S.Instruction):
        kw = stmt.keyword.upper()
        if kw in ("SELECT", "ENDIF"):
            return kw
        if kw == "NEXT" and _block_word(stmt.args or []) not in ("SCAN", "SUBSCAN"):
            return kw
    return None

_FALLBACK_PREFIX = "Invalid, or out of place expression:"


def _leftover_message(profile, stmt: S.Stmt) -> str:
    """The fallback for a missing-parameter leftover line. CR300/CR350 re-read a parenthesised one
    as an expression and echo what is inside: `(X,1,mV1000)` -> `X,1,mV1000.`, an unclosed
    `(X,1` -> `X,1` with the end-of-line newline (oracle, 2026-10-01)."""
    text = stmt.text.strip()
    if not (profile.fallback_echo_newline and text.startswith("(")):
        return _out_of_place(profile, stmt.text, stmt.at_eol)
    depth = 0
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            return f"{_FALLBACK_PREFIX} {text[1:i]}."
    return _out_of_place(profile, text[1:], stmt.at_eol)


def _no_matching(profile, text: str, at_eol: bool = True) -> str:
    """`No Matching, or out of place ) in expression: %s.`, with the same CR300/CR350 end-of-line
    newline as `_out_of_place` (oracle, 2026-10-01: `X = 2 * ASin( 1`)."""
    sep = "\n" if getattr(profile, "fallback_echo_newline", False) and at_eol else ""
    return f"No Matching, or out of place ) in expression: {text}{sep}."


def _out_of_place(profile, text: str, at_eol: bool = True) -> str:
    """The generic `Invalid, or out of place expression: %s.` text. CR300/CR350 echo the raw
    line including its line terminator, so their closing `.` is printed on the next output line
    -- but only when `text` truly reaches the physical end of its source line (`at_eol`,
    `Stmt.at_eol`): a trailing `'` comment or a further `:`-separated segment on the same line
    cuts `text` short instead, and then the `.` follows immediately on the same line, same as
    CR6/CR1000X (`Profile.fallback_echo_newline`, docs/loggers.md, oracle-confirmed with `Zzq1(1,2)`
    / `Zzq2(3,4) 'comment` / `Zzq3(5,6):` probes on cr300comp.exe)."""
    sep = "\n" if getattr(profile, "fallback_echo_newline", False) and at_eol else ""
    return f"Invalid, or out of place expression: {text}{sep}."

def _keyword(stmt: S.Stmt) -> str:
    if isinstance(stmt, S.Instruction):
        return stmt.keyword.upper()
    if isinstance(stmt, S.If):
        return "IF"
    if isinstance(stmt, S.For):
        return "FOR"
    if isinstance(stmt, S.DoLoop):
        return stmt.keyword.upper()
    return type(stmt).__name__.upper()


def _block_word(args: list) -> str | None:
    """`Next Scan`, `End If`, `End Select`: the word after a two-word block terminator."""
    if args and isinstance(args[0], S.Name):
        return args[0].name.upper()
    return None


def check_pass1(program: S.Program, profile, diags: Diagnostics, env: consteval.Env | None = None) -> None:
    """Structural/declaration errors: parameter parsing by shape, `Variable not named.`,
    `Units`/`Const`/`Alias` truncation, array-dimension limits, and `BeginProg`/`Scan`/`If`/
    `Select` block matching. Unconditional -- fires regardless of what else is wrong elsewhere in
    the file (`docs/checker.md` "Pass model"). `checker/__init__.py` runs `check_pass2` (below)
    only when this pass reports nothing.
    """
    if env is None:
        env = consteval.Env(profile, dict(program.consts))

    seen_beginprog = False
    scan_depth = 0
    cond_depth = 0
    select_depth = 0
    in_table = False
    table_name: str | None = None
    nested_table_line: int | None = None
    last_line = program.statements[-1].line if program.statements else 1
    # See `_check_declaration`'s docstring: the physical line right after a nameless `Public`/
    # `Dim` is never itself reported, even though it plainly failed to parse as anything valid.
    suppress_line: int | None = None
    dim0_sizes = symbols.declared_dim0_sizes(program, env)
    all_dims = symbols.declared_all_dims(program, env)
    seen_alias_names: set[str] = set()
    seen_alias_sources: dict[str, set[tuple]] = {}
    known: set[str] | None = None
    operands: set[str] = set()
    in_sub = False
    in_struct = False
    const_tables = const_open = slow_sequences = 0
    seen_endprog = False
    declared_kinds: dict[str, str] = {}
    struct_types = frozenset(
        st.args[0].name.upper() for st in program.statements
        if isinstance(st, S.Instruction) and st.keyword.upper() in ("STRUCTURETYPE", "OBJECTTYPE")
        and st.args and isinstance(st.args[0], S.Name))
    decl_variables = {v.name.upper() for st in program.statements
                      if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC")
                      for v in st.vars}

    for stmt in program.statements:
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []

        fire_suppress = suppress_line is not None and stmt.line == suppress_line
        suppress_line = None

        if isinstance(stmt, S.SubDef):
            in_sub = True
        elif kw in ("ENDSUB", "ENDFUNCTION") or (kw == "END" and _block_word(args) in ("SUB", "FUNCTION")):
            in_sub = False
        if kw in ("STRUCTURETYPE", "OBJECTTYPE"):
            in_struct = True
        elif kw in ("ENDSTRUCTURETYPE", "ENDOBJECTTYPE"):
            in_struct = False
        if not in_sub and not in_struct:  # a structure body names its own members
            _check_duplicate_declaration(stmt, declared_kinds, diags, profile)
        elif in_sub and not in_struct:
            symbols.check_local_name_collision(stmt, profile, diags)

        if isinstance(stmt, S.Instruction):
            # Oracle, all four loggers, 2026-10-02: pass 1, in line order. Every `ConstTable`
            # after the first (open or not) is `Only one ConstTable allowed.`; the counter of
            # open ones still rises, so a second `EndConstTable` matches it.
            if kw == "CONSTTABLE":
                if const_tables:
                    diags.error(stmt.line, "Only one ConstTable allowed.", stmt.file)
                const_tables += 1
                const_open += 1
            elif kw == "ENDCONSTTABLE":
                if const_open:
                    const_open -= 1
                else:
                    diags.error(stmt.line, "EndConstTable with no ConstTable.", stmt.file)
            elif kw == "ENDPROG":
                seen_endprog = True
            elif kw == "SLOWSEQUENCE" and not seen_endprog:
                # Oracle, all four loggers, 2026-10-02: pass 1; every `SlowSequence` past the
                # fourth, counted from the start of the file to the first `EndProg` (later ones
                # are not reported), whether or not it has a `Scan`.
                slow_sequences += 1
                if slow_sequences > 4:
                    diags.error(stmt.line, "Too many Slow Sequences: max = 4.", stmt.file)

        if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
            if not stmt.vars:
                suppress_line = _check_declaration(stmt, diags)
            else:
                _check_array_dims(stmt, diags, env)
                if known is None:
                    known = expressions._known_names(program, profile)
                    operands = pass1_operands(program, profile)
                _check_decl_exprs(stmt, known, decl_variables, profile, diags, struct_types)
                _check_init_bounds(stmt, env, diags)
        elif fire_suppress:
            pass
        elif isinstance(stmt, S.Unparsed):
            _check_unparsed_pass1(stmt, diags)
            if profile.known_first_word_pass1 and _is_generic_unparsed(stmt):
                if known is None:
                    known = expressions._known_names(program, profile)
                    operands = pass1_operands(program, profile)
                verdict = _pass1_scan(stmt.text, operands, profile)
                if verdict is None and stmt.reason == _UNCLOSED_PAREN_EXPR and \
                        _unclosed_paren_kind(stmt.text, known) == "pass1":
                    verdict = ("pass1", "no_matching")
                if verdict and verdict[0] == "pass1":
                    diags.error(stmt.line, _pass1_message(profile, stmt, verdict[1]), stmt.file,
                                show_file=False)
        elif (isinstance(stmt, S.Call) and profile.known_first_word_pass1 and not stmt.explicit
              and stmt.name.upper() not in profile.instructions
              and stmt.name.upper() not in profile.functions):
            if known is None:
                known = expressions._known_names(program, profile)
                operands = pass1_operands(program, profile)
            if stmt.name.upper() in operands and stmt.name.upper() not in symbols.declared_subs(program):
                diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file,
                            show_file=False)
        elif kw == "ALIAS" and isinstance(stmt, S.Instruction):
            if known is None:
                known = expressions._known_names(program, profile)
                operands = pass1_operands(program, profile)
            if len(stmt.args) > 1 and isinstance(stmt.args[1], S.Number):
                # `Alias CD16(29) = 3`, pass 1, all four loggers (oracle, 2026-09-27)
                diags.error(stmt.line, f"Variable name {stmt.args[1].text} illegal, starts with a "
                            "number.", stmt.file)
            elif not _check_alias_index_names(stmt, known, decl_variables, profile, diags):
                _check_alias_dims(stmt, diags, env, dim0_sizes, all_dims, seen_alias_names,
                                   seen_alias_sources)
        # `S.Call` (a bare/implicit or explicit `Call`) and `S.Unparsed`'s generic "not a valid
        # statement" fallback are **not** handled here at all any more (round 4, this session):
        # both are pass-2 ("Expr"/implicit-`Call` records, `docs/checker.md` "Pass model") --
        # `check_pass2` below.

        if kw == "BEGINPROG":
            seen_beginprog = True
        elif kw == "SCAN":
            scan_depth += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(args) == "SCAN"):
            if scan_depth > 0:  # `NextScan with no Scan.` is later: `check_pass2`/`check_end_blocks`
                scan_depth -= 1
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            cond_depth += 1
            select_depth += kw == "SELECT"
        elif kw == "ENDSELECT" or (kw == "END" and _block_word(args) == "SELECT"):
            cond_depth = max(0, cond_depth - 1)
            if select_depth == 0:
                # Parser errors (decompiled `FUN_0049fe50`, `FUN_0049ce10`), all four loggers:
                # a later `X = Q` or an earlier stray `Else`/`Exit Do` is not reported on CR6.
                diags.error(stmt.line, "EndSelect w/o Select", stmt.file)
            select_depth = max(0, select_depth - 1)
        elif isinstance(stmt, S.Case) and not _is_case_else(stmt) and select_depth == 0:
            diags.error(stmt.line, "Case outside Select.", stmt.file)
        elif kw == "ENDIF" or (kw == "END" and _block_word(args) == "IF"):
            cond_depth = max(0, cond_depth - 1)
        # `DataTable ... EndTable not matched.` (round 6, below): a single "currently open" flag,
        # not a depth stack -- `DataTable` blocks never nest (`instr_data.py`'s own docstring), so
        # this only tracks the *last*-opened table's own name; a second `DataTable` opened while
        # one is already unclosed is `DataTable without EndTable.` instead (round 7, below,
        # `nested_table_line`) -- `in_table`/`table_name` themselves stay pointed at the *first*
        # table regardless, since nesting deeper than two levels still reports only the one
        # violation (oracle-confirmed, `docs/checker.md` "Round 7": a *third* `DataTable` opened
        # while the second is also still open adds no second diagnostic).
        elif kw == "DATATABLE" and not in_table:
            in_table = True
            table_name = args[0].name if args and isinstance(args[0], S.Name) else None
        elif kw == "DATATABLE" and in_table and nested_table_line is None:
            nested_table_line = stmt.line
        elif kw == "ENDTABLE":
            in_table = False

    # Round 6 finding, oracle-confirmed: these three block-balance summary checks are themselves
    # suppressed entirely once *any* other pass-1 error already fired in this same loop --
    # `Variable not named.`/`Illegally dimension: ...`/`Illegally dimensioned aliased name`/
    # `Need '=' in units definition: ...` (one from each of `_check_declaration`/
    # `_check_array_dims`/`_check_alias_dims`/`_check_unparsed_pass1`, oracle-swept independently)
    # all suppress a *following* `No BeginProg in Program.`/`Scan without NextScan.` the same way
    # -- a minimal one-line `Public` (no name at all, no `BeginProg` anywhere in the file) real-
    # rejects with only `Variable not named.`, never a second, additional `No BeginProg in
    # Program.` alongside it, and the same holds for an unbalanced `Scan`/`If` in a file that also
    # has an earlier declaration error. `diags.errors` at this point only reflects this same
    # function's own loop above (`check_pass1` is always the first thing `checker/__init__.py`
    # runs), so this is safe without threading a separate "did this loop itself find anything"
    # flag through it.
    if diags.errors:
        return
    # `DataTable without EndTable.` (round 7, this session, no `%s`/echoed text): the *highest*-
    # priority summary check of the five in this gated section -- a nested `DataTable` reports
    # only this, before even `No BeginProg in Program.` (oracle-confirmed, `docs/checker.md`
    # "Round 7": a file with nested tables and no `BeginProg` anywhere shows only this one line),
    # and before `Scan without NextScan.`/`IF or SELECT without EndIf or EndSelect.` too (a nested
    # table plus a separately-unbalanced `Scan` in the same file shows only this one line as well).
    # Reported at the *second* `DataTable`'s own line (`nested_table_line`, captured above), and,
    # like the other four checks in this section, stops here rather than falling through to them.
    if nested_table_line is not None:
        diags.error(nested_table_line, "DataTable without EndTable.")
        return
    # `No BeginProg in Program.` is also reported after pass 2: `check_end_blocks`.
    # `Scan without NextScan.`, `FOR without NEXT.`, `DO without LOOP.`, `IF or SELECT without
    # EndIf or EndSelect.` and `DataTable ... EndTable not matched.` come after pass 2:
    # `check_end_blocks` below.


def check_end_blocks(program: S.Program, diags: Diagnostics) -> None:
    """End-of-program block balance, run only when passes 1 and 2 found nothing (oracle, CR6 and
    CR300, 2026-09-27: an unclosed `Scan`/`For`/`If` plus an unrelated pass-2 `X = Q` prints only
    `Invalid symbol in expression`). Reported on the last statement's line (`EndProg`) in the
    order `Scan without NextScan.`, `FOR without NEXT.`, `DO without LOOP.`, `IF or SELECT without
    EndIf or EndSelect.` (`docs/checker.md` "Unclosed blocks")."""
    last_line = program.last_line or (program.statements[-1].line if program.statements else 1)
    if not any(_keyword(st) == "BEGINPROG" for st in program.statements):
        # Only when passes 1 and 2 are clean (oracle, CR6: a program with no `BeginProg` and a
        # bad `Y=EXP()` line reports that line's fallback and `Expr illegally occurs before
        # BeginProg.`, never `No BeginProg in Program.`).
        diags.error(last_line, "No BeginProg in Program.")
        return
    depth = {"SCAN": 0, "FOR": 0, "DO": 0, "IF": 0}
    in_table = False
    table_name: str | None = None
    for stmt in program.statements:
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []
        word = _block_word(args) if kw in ("END", "NEXT") else None
        if kw == "SCAN":
            depth["SCAN"] += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and word == "SCAN"):
            depth["SCAN"] = max(0, depth["SCAN"] - 1)
        elif kw == "FOR":
            depth["FOR"] += 1
        elif kw == "NEXT" and word not in ("SCAN", "SUBSCAN"):
            depth["FOR"] = max(0, depth["FOR"] - 1)
        elif kw == "DO":
            depth["DO"] += 1
        elif kw == "LOOP":
            depth["DO"] = max(0, depth["DO"] - 1)
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            depth["IF"] += 1
        elif kw in ("ENDSELECT", "ENDIF") or (kw == "END" and word in ("SELECT", "IF")):
            depth["IF"] = max(0, depth["IF"] - 1)
        elif kw == "DATATABLE" and not in_table:
            in_table = True
            table_name = args[0].name if args and isinstance(args[0], S.Name) else None
        elif kw == "ENDTABLE":
            in_table = False
    messages = {"SCAN": "Scan without NextScan.", "FOR": "FOR without NEXT.",
                "DO": "DO without LOOP.", "IF": "IF or SELECT without EndIf or EndSelect."}
    reported = False
    for kind, message in messages.items():
        if depth[kind] > 0:
            diags.error(last_line, message)
            reported = True
    if reported:
        return
    menus = 0
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "DISPLAYMENU":
            menus += 1
        elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ENDMENU":
            menus = max(0, menus - 1)
    if menus:
        # oracle, CR6/CR1000X, 2026-09-28: hidden by an unrelated `X = Q`
        diags.error(last_line, "DisplayMenu without EndMenu.")
        return
    # `DataTable ... EndTable not matched.` -- no line number at all (oracle-confirmed verbatim,
    # `docs/checker.md` "Round 6"), the lowest-priority of these four summary checks (oracle: a
    # co-occurring `Scan without NextScan.` reports only that, never this too) and, unlike the
    # other three, itself conditional on whether *anything else in the whole program* references
    # the unclosed table by name: when it does (any `CallTable <name>`, oracle-confirmed at that
    # `CallTable`'s own line, tier B not pass 1 -- accumulates with an unrelated `expressions.
    # check`/`structure.check_pass2` finding, unlike this bare message, which pass-1 errors alone
    # suppress), the real compiler instead reports `Could not find DataTable %s.` there
    # (`checker/instr_data.py`'s `check_unclosed_calltable`, tier B) and says nothing here at all.
    if in_table and table_name is not None:
        if table_name.upper() not in instr_data.calltable_targets(program):
            diags.error(0, "DataTable ... EndTable not matched.")


@dataclasses.dataclass
class CallLinks:
    """What `check_pass2` learned about `Call`s that name no declared `Sub` (`docs/checker.md`
    "Unresolved `Call`s"). `calls`: the name of every explicit `Call` (and bare `Sub` statement
    with trailing text) in source order; `registered`: upper-case names of the `Sub`s the walk
    reached before it ended (a placement error ends it); `first`: CR300/CR350 only, the deferred
    `(line, file, message)` of the first no-routines `Sub %s not defined.`.
    `finalize_call_links` turns these into diagnostics."""
    calls: list = dataclasses.field(default_factory=list)
    registered: set = dataclasses.field(default_factory=set)
    first: tuple | None = None
    positions: dict[tuple[str, int], int] = dataclasses.field(default_factory=dict)
    cutoff: tuple[str, int] | None = None


def check_pass2(program: S.Program, profile, diags: Diagnostics,
                 env: consteval.Env | None = None, links: CallLinks | None = None) -> int | None:
    """The generic "Expr"/implicit-`Call` record family: a statement our parser turns into `Call`
    (a bare `Name`/`Name(args)`, with or without the `Call` keyword) or `Unparsed` (it could not
    parse at all) that never got a specific pass-1 diagnosis (`check_pass1`'s
    `_check_unparsed_pass1`). Only called by `checker/__init__.py` when `check_pass1` found nothing
    wrong anywhere in the file (`docs/checker.md` "Pass model", oracle-confirmed round 4: an
    earlier "Variable not named." on an unrelated line suppresses `Sub %s not defined.`/`Invalid,
    or out of place expression: %s.` just as much as it suppresses `expressions.py`'s own checks).

    Returns the line of the first **placement** error -- a misplaced statement before `BeginProg`
    (oracle-confirmed; `docs/checker.md` "Pass model" -- the only placement shape implemented this
    round, see that section for the other two, real but unimplemented, kinds) -- or `None`.
    `checker/__init__.py` truncates every *other* pass-2 module's own diagnostics to that same line,
    reproducing the real compiler's own "stop entirely" behaviour for the rest of the file without
    each pass-2 module needing to coordinate the cutoff itself.

    "Before `BeginProg`" only means the top level of the program, never a statement lexically
    inside a `Sub`/`Function` body (regardless of that body's own textual position, almost always
    before `BeginProg` itself, `docs/checker.md` "Pass model": found as a **regression** against
    this repository's own `crbasic/Main.CR6` and several siblings, all of which call a built-in
    function bare, with no `Call` keyword, e.g. `SerialOpen(...)` inside a `Function`/`Sub` defined
    above `BeginProg` -- perfectly ordinary CRBasic, since the body only executes when the
    `Sub`/`Function` is later called from inside `Scan`/`NextScan`, oracle-reconfirmed by re-running
    this exact shape standalone) *and* the same shape inside any of CRBasic's other named,
    `EndXxx`-terminated body blocks that are themselves conventionally declared before `BeginProg`
    (`_BODY_BLOCK_STARTS`/`_BODY_BLOCK_ENDS` below -- found from the corpus, `modemhangupendmodemhangup_1.CR6`:
    a bare `SerialOpen(...)` inside `ModemHangup...EndModemHangup`, the same false-reject shape as
    `Sub`/`Function`). `in_body_block` tracks this the same way `structure.py`'s block-matching
    tracks `Scan`/`If` depth: a flat pass over the statement list toggled on the relevant start/end
    keyword, since the AST keeps block structure flat (`syntax.py`'s own docstring). Only `Sub`/
    `Function`/`ModemHangup`/`DialSequence` have corpus or repository evidence; the rest
    (`Menu`/`SubMenu`/`Voice`/`BurstTrigger`/`ApplyAndRestartSequence`/`ProcHiPri`) are the same
    documented shape (a named, `EndXxx`-terminated, conventionally-before-`BeginProg` body block,
    `data/instructions.json`) added on that pattern rather than individually oracle-tested --
    including them only ever *suppresses* a placement diagnostic, never adds one, so a wrong
    inclusion costs at most an "exact" vs "verdict" precision loss on some other file, never a new
    false reject (AGENTS.md's priority order).

    **Round 5 addition** (`docs/checker.md` "Round 5"): an `S.Assignment` statement goes through
    the exact same three-way split as `S.Call` above (`_check_assignment_pass2`), keyed off
    whether its own *target* resolves rather than a bare name -- see that function's docstring for
    the oracle evidence and its interaction with `expressions.check`'s own RHS-undefined-name check
    on the same statement.
    """
    if env is None:
        env = consteval.Env(profile, dict(program.consts))
    subs = symbols.declared_subs(program)
    sub_params = {st.name.upper(): st for st in program.statements if isinstance(st, S.SubDef)}
    link_ctx = _LinkContext(
        {st.name.upper() for st in program.statements
         if isinstance(st, S.SubDef) and st.keyword.upper() == "SUB"},
        bool(subs), links if links is not None else CallLinks())
    known = expressions._known_names(program, profile)
    # Before the first `BeginProg` in expansion order (an included file has its own line numbers).
    seen_beginprog = False

    in_body_block = False
    # Narrower than `in_body_block` and used *only* by `_FORBIDDEN_BEFORE_BEGINPROG` below -- kept
    # deliberately separate rather than folded into `_BODY_BLOCK_STARTS`/`_BODY_BLOCK_ENDS`
    # (round 5, `docs/checker.md`): a fuzz-found false reject (`Delay(0,1,Sec)` inserted inside a
    # `DataTable...EndTable` body, `structuretype_2.CR6`-derived) shows `DataTable` bodies *do*
    # suppress this specific check, unlike the "before `BeginProg`" placement check the paragraph
    # above documents reverting `DataTable`/`ConstTable` out of (a `ConstTable` body's own
    # unresolved-statement fallback does **not** get the same suppression, oracle-confirmed via
    # `solarposition_1.CR6` -- the two container kinds genuinely behave differently for these two
    # different checks, so one shared flag cannot correctly cover both).
    in_table = False
    in_decl_block = False
    tables: dict = {}
    # `Multiple BeginProgs.` (round 6, `docs/checker.md`): a genuine nesting-depth check, not "has
    # `BeginProg` appeared more than once in the file" -- oracle-confirmed, a dozen probes: a
    # *second*, complete `BeginProg...EndProg` pair (the first one properly closed first) compiles
    # clean, while a `BeginProg` encountered before the previous one's own `EndProg` real-rejects
    # at that second `BeginProg`'s own line, with no preceding echo. Accumulates with other pass-2
    # errors rather than stopping the pass (oracle: an unrelated `Randomize(FooBar)` right after a
    # nested `BeginProg` still gets its own, separate `Invalid symbol in expression` line), and a
    # *third* `BeginProg` nested inside the second gets its own second `Multiple BeginProgs.` line
    # too (one diagnostic per excess `BeginProg`, not just the first).
    in_beginprog = False
    pass2_scans: list[bool] = []
    misplaced = first_misplaced_sub(program) if not profile.single_pass else None
    misplaced_table = first_misplaced_data_table(program, profile) if not profile.single_pass else None
    # `IF or SELECT statement illegally has SCAN without NEXT SCAN.`: `check_block_closers`.
    for position, source_stmt in enumerate(program.statements):
        link_ctx.links.positions.setdefault((source_stmt.file, source_stmt.line), position)
    for stmt in program.statements:
        link_ctx.links.cutoff = (stmt.file, stmt.line)   # the stop, if this statement returns
        seen_beginprog = seen_beginprog or _keyword(stmt) == "BEGINPROG"
        # CR300/CR350 report these placement errors late (`check_scan_placement_late`).
        before_bp = not seen_beginprog and not profile.single_pass
        if misplaced and stmt is misplaced[0]:
            for message in misplaced[1]:
                diags.error(stmt.line, message, stmt.file)
            return stmt.line
        if stmt is misplaced_table:
            diags.error(stmt.line, "DataTable is declared in the middle of an executing sequence",
                        stmt.file)
            return stmt.line
        if isinstance(stmt, S.SubDef):
            in_body_block = True
            if stmt.keyword.upper() == "SUB":
                link_ctx.links.registered.add(stmt.name.upper())
            continue
        kw = _keyword(stmt)
        if kw in _DECL_BLOCK_BOUNDS:
            in_decl_block = _DECL_BLOCK_BOUNDS[kw]
        _track_tables(stmt, kw, tables)
        if kw == "DATATABLE":
            in_table = True
        elif kw == "ENDTABLE":
            in_table = False
        elif in_table and isinstance(stmt, S.Instruction) and \
                profile.name in expressions._WHOLE_EXPR_ECHO_LOGGERS:
            bad = _undefined_table_source(stmt, profile, known)
            if bad is not None:
                # CR6/CR1000X: an unresolved table-output source (`Sample (1,Nope,FP2)`) is
                # `Invalid symbol in expression: Nope.` and ends pass 2 (oracle: a later `X = Q`
                # and a second bad source in the same table are not reported).
                diags.error(stmt.line, f"Invalid symbol in expression: {bad}.", stmt.file)
                return stmt.line
        elif kw == "BEGINPROG":
            if in_beginprog:
                diags.error(stmt.line, "Multiple BeginProgs.", stmt.file)
            in_beginprog = True
        elif kw == "ENDPROG":
            in_beginprog = False
        if kw == "SCAN":
            pass2_scans.append(_scan_counts_for_table_placement(stmt))
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(getattr(stmt, "args", []) or []) == "SCAN"):
            if pass2_scans:
                pass2_scans.pop()
            elif not profile.single_pass:
                # CR6/CR1000X: pass 2, and it ends pass 2 (oracle: only the first extra `NextScan`
                # is reported; a later `X = Q` is not). CR300/CR350: `check_scan_placement_late`.
                diags.error(stmt.line, "NextScan with no Scan.", stmt.file)
                return stmt.line
        elif kw == "CALLTABLE" and not profile.single_pass and not in_table and \
                (not any(pass2_scans) or in_body_block) and _calltable_known(stmt, tables):
            _check_calltable_outside_scan(stmt, program, diags, in_body_block)
        if kw in _BODY_BLOCK_STARTS:
            in_body_block = True
            continue
        if kw in ("ENDSUB", "ENDFUNCTION") or kw in _BODY_BLOCK_ENDS:
            in_body_block = False
            continue

        if isinstance(stmt, S.Call):
            stop = _check_call_pass2(stmt, subs, profile, diags, before_bp, in_body_block,
                                     sub_params, known, link_ctx)
            if stop is not None:
                return stop
        elif isinstance(stmt, S.Assignment):
            stop = _check_assignment_pass2(stmt, known, diags, before_bp, in_body_block,
                                            profile)
            if stop is not None:
                return stop
        elif _control_keyword(stmt) and not in_body_block and not in_table and not seen_beginprog \
                and (not profile.single_pass or _has_error_on(stmt, diags)):
            # Control statements before `BeginProg` (oracle, all four loggers, 2026-09-27):
            # `If X Then` -> `If illegally occurs before BeginProg.`, likewise `FOR`, `Do`,
            # `SELECT`, and a stray `EndIf`/`Loop`/`NEXT` after its own block message.
            kw_ctrl = _control_keyword(stmt)
            record = profile.instructions.get(kw_ctrl)
            display = record["name"] if record else kw_ctrl
            diags.error(stmt.line, f"{display} illegally occurs before BeginProg.", stmt.file)
            return stmt.line
        elif isinstance(stmt, S.Instruction) and (kw in _FORBIDDEN_BEFORE_BEGINPROG
                                                  or kw in profile.before_beginprog):
            if not in_body_block and not in_table and not in_decl_block and before_bp and \
                    (kw != "CALLTABLE" or _calltable_known(stmt, tables)):
                record = profile.instructions.get(stmt.keyword.upper())
                display = record["name"] if record else stmt.keyword
                diags.error(stmt.line, f"{display} illegally occurs before BeginProg.", stmt.file)
                return stmt.line
        elif isinstance(stmt, S.Unparsed):
            if stmt.reason.startswith(_KNOWN_PARSER_GAP_REASONS) or stmt.reason in _BENIGN_REASONS:
                continue
            bare = _bare_sub_link_name(stmt.text, link_ctx.subs)
            if bare is not None:
                link_ctx.links.calls.append(bare)
                # `S1 1,2` with `S1` a declared Sub: the link step's `S11,2 is not declared as a
                # Subroutine.` (oracle, 2026-10-02), not the generic fallback.
                stop = _unlinked_call(stmt, bare, profile, diags, not in_body_block and before_bp,
                                      link_ctx)
                if stop is not None:
                    return stop
                continue
            if any((d.line, d.file) == (stmt.line, stmt.file) and
                   d.message.startswith((_FALLBACK_PREFIX, "Illegal Data parameter"))
                   for d in diags.errors):
                continue  # a missing-parameter leftover or `Data` line, reported in pass 1
            if instr_rules.unterminated_string_shape(stmt, profile, subs):
                continue  # reported in pass 1 (`instr_rules._check_unterminated_string_param`)
            if stmt.reason in (_UNITS_WITHOUT_EQ, _CONST_WITHOUT_EQ, _ALIAS_WITHOUT_EQ) or \
                    stmt.reason.startswith(_INVALID_AS_CLAUSE) or \
                    stmt.reason.startswith(_UNCLOSED_DIM_PREFIX):
                continue  # already reported by `check_pass1`
            if stmt.reason == _UNCLOSED_PAREN_EXPR:
                # `parser.py`'s `UNCLOSED_PAREN_EXPR` marker: a genuinely unclosed parenthesis
                # inside the general expression grammar (`docs/checker.md` "Need closing )
                # shapes"). `stmt.text` already matches oracle-confirmed exactly for the shape this
                # was implemented for (an assignment's own RHS run off the end of the line); a call
                # nested inside a bare statement's own arguments echoes a narrower substring the
                # real compiler doesn't derive from the whole statement text, not attempted.
                kind = (_unclosed_paren_kind(stmt.text, known)
                        if profile.known_first_word_pass1 else "pass1")
                if kind == "symbol":
                    diags.error(stmt.line, f"Invalid symbol in expression: {stmt.text}.", stmt.file,
                                show_file=False)
                elif kind == "fallback":
                    diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol),
                                stmt.file, show_file=False)
                else:
                    target = re.match(r"\s*([A-Za-z_]\w*)\s*=", stmt.text)
                    if profile.fallback_echo_newline and target and \
                            target.group(1).upper() not in expressions._known_names(program, profile):
                        # CR300/CR350: `Y = (1` -> `Y is not defined.` first (oracle, 2026-10-01)
                        diags.error(stmt.line, profile.unresolved_symbol_message.format(
                            name=target.group(1)), stmt.file, show_file=False)
                    diags.error(stmt.line, _no_matching(profile, stmt.text, stmt.at_eol),
                                stmt.file, show_file=False)
            elif stmt.reason == _EXTRA_CLOSE_PAREN:
                sep = "\n" if profile.fallback_echo_newline and stmt.at_eol else ""
                diags.error(stmt.line, f"Incomplete expression: {stmt.text}{sep}.", stmt.file,
                            show_file=False)
            else:
                verdict = (_pass1_scan(stmt.text, pass1_operands(program, profile), profile)
                           if profile.known_first_word_pass1 else None)
                if verdict == ("pass2", "symbol"):
                    diags.error(stmt.line, _pass1_message(profile, stmt, "symbol"), stmt.file,
                                show_file=False)
                else:
                    diags.error(stmt.line, _leftover_message(profile, stmt), stmt.file,
                                show_file=False)
                if profile.undefined_call_message:
                    # CR300/CR350 only (`profiles.py`'s own field, reused here from
                    # `_check_call_pass2`'s identical carve-out below): no before-`BeginProg`
                    # follow-up/stop for the generic fallback either -- oracle-confirmed,
                    # `docs/loggers.md` "Round 8" Finding 2 (`foo bar` before `BeginProg` gets only
                    # the leading-word/generic lines, then compilation continues to later
                    # statements). Scoped to this `else` shape alone, matching what was oracle-
                    # tested -- the `_UNCLOSED_PAREN_EXPR`/`_EXTRA_CLOSE_PAREN` branches above keep
                    # the ordinary follow-up/stop below, unprobed this round.
                    continue
            if not in_body_block and before_bp:
                diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
                return stmt.line
    link_ctx.links.cutoff = None
    return None



_CLOSER_MESSAGES = {
    "IF": ("IF or SELECT statement illegally has SCAN without NEXT SCAN.",
           "END IF without IF or END SELECT without SELECT."),
    "FOR": ("FOR NEXT statement illegally has SCAN without NEXT SCAN.", "NEXT without FOR."),
    "DO": ("LOOP statement illegally has SCAN without NEXT SCAN.", "LOOP without DO."),
}


def check_block_closers(program: S.Program, profile, diags: Diagnostics) -> None:
    """Pass 2, all four loggers: `NextScan` removes the innermost `Scan` from the stack wherever
    it is; a closing `EndIf`/`EndSelect`/`Next`/`Loop` pops whatever block is innermost; if that was a `Scan`/`SubScan` it reports `<kind> statement illegally has SCAN
    without NEXT SCAN.`, and with no block of its own kind open it reports `END IF without IF or
    END SELECT without SELECT.`/`NEXT without FOR.`/`LOOP without DO.`; an `EndSelect` with no
    `Select` open reports only `EndSelect w/o Select` (`docs/checker.md` "Unmatched block ends").
    Programs using `While`/`Wend` are skipped (not probed)."""
    if any(_keyword(st) in ("WHILE", "WEND") for st in program.statements):
        return
    stack: list[str] = []
    open_count = {"IF": 0, "FOR": 0, "DO": 0, "SCAN": 0}
    selects = 0
    subscans = 0
    for stmt in program.statements:
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []
        word = _block_word(args) if kw in ("END", "NEXT") else None
        if kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None) or kw == "SELECT":
            stack.append("IF")
            open_count["IF"] += 1
            selects += kw == "SELECT"
        elif kw in ("FOR", "DO"):
            stack.append(kw)
            open_count[kw] += 1
        elif kw in ("SCAN", "SUBSCAN"):
            stack.append("SCAN")
            subscans += kw == "SUBSCAN"
        elif kw == "NEXTSUBSCAN" and subscans == 0:
            diags.error(stmt.line, "Next SubScan without SubScan.", stmt.file)
        elif kw in ("NEXTSCAN", "NEXTSUBSCAN") or (kw == "NEXT" and word in ("SCAN", "SUBSCAN")):
            subscans -= kw == "NEXTSUBSCAN" or word == "SUBSCAN"
            # Removes the innermost `Scan` wherever it is; blocks opened inside it stay open
            # (oracle: `If X Then` / `NextScan` / `EndIf` compiles, a fuzz-found false reject).
            if "SCAN" in stack:
                del stack[len(stack) - 1 - stack[::-1].index("SCAN")]
        elif kw in ("ENDIF", "ENDSELECT", "NEXT", "LOOP") or (kw == "END" and word in ("IF", "SELECT")):
            is_select = kw == "ENDSELECT" or (kw == "END" and word == "SELECT")
            if is_select and selects == 0:
                return  # `EndSelect w/o Select` is a pass-1 error (`check_pass1`)
            kind = {"NEXT": "FOR", "LOOP": "DO"}.get(kw, "IF")
            illegal_scan, without = _CLOSER_MESSAGES[kind]
            if stack and stack.pop() == "SCAN":
                diags.error(stmt.line, illegal_scan, stmt.file)
            if open_count[kind] == 0:
                diags.error(stmt.line, without, stmt.file)
            else:
                open_count[kind] -= 1
                selects -= is_select


def _undefined_table_source(stmt: S.Instruction, profile, known: set[str]) -> str | None:
    """The echo text of the first `deferred_text` (type code 11, table-output source) argument
    of `stmt` that names something undefined, or `None`."""
    record = profile.instructions.get(stmt.keyword.upper())
    if not record:
        return None
    kinds = expressions._type_code_kinds()
    params = [c for c in record["params"]]
    for i, code in enumerate(params[:len(stmt.args)]):
        arg = stmt.args[i]
        if kinds.get(code % 100 if code >= 100 else code) != "deferred_text" or arg is None:
            continue
        if expressions._undefined_names(arg, known):
            return expressions._arg_text(stmt, i).strip()
    return None

def _leftover_first_name_known(text: str, known: set[str]) -> bool:
    """Only the *first* identifier tokenisable out of `text` (skipping any leading punctuation --
    `(`/`,` -- and numeric/string literals) needs to be in `known` (`expressions._known_names`);
    a later identifier in the same leftover is not checked. `True` when the leftover has no
    identifier at all (all literals/punctuation, e.g. `,200,15000,1.0,0)`).

    Narrower than "every referenced name must resolve" -- that broader version was tried first and
    reverted after `cdmresistance_1.CR6` (`docs/checker.md` "Missing-parameter followup") showed it
    was wrong: that file's *second* leftover (`Tblk1, 1,mV5000,1,TypeT,Tref,0,0,60,1.8,32 )`) gets
    the follow-up in the real compiler even though it references `Tref`, undeclared throughout the
    file (the stranded name after an earlier nameless `Dim`, never truly registered) -- the same
    undeclared `Tref` that correctly suppresses the *first* leftover (`Tref,1 ,1,60)`, oracle-
    confirmed absent) in the same file. The only difference between the two is which name comes
    *first*: `Tblk1` (genuinely declared, `Dim Tblk1`) leads the second, `Tref` itself leads the
    first. Every other corpus/scratch-probe case this session checked is consistent with checking
    only the first identifier (`getdatarecord_1.CR6` vs `getdatarecord_3.CR6`'s `Result`/`ResultTD`
    both lead their own leftover text, `average_1.CR6`/`cosh_1.CR6` have no leading identifier to
    fail at all). A genuinely unlexable leftover (an unterminated string, say) is conservatively
    treated as "not known" (no oracle evidence either way for that shape, and this only ever
    *suppresses* the diagnostic, never adds a wrong one, so the safe default costs at most a
    missed "exact")."""
    try:
        tokens = lexer.tokenize(text, 0)
    except lexer.LexError:
        return False
    for t in tokens:
        if t.kind == "IDENT":
            return t.upper in known
    return True



def _unclosed_paren_kind(text: str, known: set[str]) -> str:
    """Which message an unclosed `(` statement gets on CR6/CR1000X (`docs/checker.md` "Pass-1
    fallback: statements that start with a known word"): `"pass1"` (`No Matching, or out of place
    ) in expression: %s.`, pass 1) when the first token is a known name and every name resolves;
    `"fallback"` (the generic pass-2 `Invalid, or out of place expression: %s.`) when the first
    token is not a known name; `"symbol"` (pass-2 `Invalid symbol in expression: %s.`) when the
    first name is known but a later one is not."""
    try:
        tokens = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return "fallback"
    if not tokens or tokens[0].kind != "IDENT" or tokens[0].upper not in known:
        return "fallback"
    if any(t.kind == "IDENT" and t.upper not in known for t in tokens):
        return "symbol"
    return "pass1"



def _is_generic_unparsed(stmt: S.Unparsed) -> bool:
    """An `Unparsed` statement no specific pass-1 diagnosis claims (`_check_unparsed_pass1`)."""
    return not (stmt.reason.startswith(_KNOWN_PARSER_GAP_REASONS) or stmt.reason in _BENIGN_REASONS
                or stmt.reason in (_UNITS_WITHOUT_EQ, _UNITS_TARGET_NO_EQ, _CONST_WITHOUT_EQ,
                                   _ALIAS_WITHOUT_EQ, _ALIAS_TARGET_NO_EQ)
                or stmt.reason.startswith(_INVALID_AS_CLAUSE)
                or stmt.reason.startswith(_UNCLOSED_DIM_PREFIX))

_BINARY_WORDS = frozenset({"MOD", "OR", "XOR", "AND", "IMP", "EQV"})
_START_ERROR_WORDS = _BINARY_WORDS | {"IS", "TO"}
_BINARY_OPS = frozenset({"=", "<>", "<", ">", "<=", ">=", "+", "-", "*", "/", "^", "&", "\\",
                         ">>", "<<"})


def pass1_operands(program: S.Program, profile) -> set[str]:
    """Names pass 1 resolves as operands (`_pass1_scan`): declared variables, `Sub`/`Function`
    parameters, `Const`s, `Alias` names, `For` variables and predefined symbols. Not `Sub`,
    `DataTable` or system-table names, which pass 1 leaves for pass 2 (`MySub bar`, `Tab bar`,
    `Status bar` are silent after a pass-1 error, `docs/checker.md` "Pass-1 fallback")."""
    names = symbols.declared_variable_names(program) | set(program.consts)
    names |= {k.upper() for k in program.consts}
    for stmt in program.statements:
        if isinstance(stmt, S.ConstDecl):
            names.add(stmt.name.upper())
        elif isinstance(stmt, S.For) and isinstance(stmt.var, S.Name):
            names.add(stmt.var.name.upper())
        elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ALIAS":
            names.update(a.name.upper() for a in stmt.args[1:] if isinstance(a, S.Name))
    return names | set(profile.symbols) | expressions._EXTRA_PREDEFINED_SYMBOLS


def _pass1_scan(text: str, operands: set[str], profile) -> tuple[str, str] | None:
    """Replays the CR6/CR1000X pass-1 left-to-right statement scan (`docs/checker.md` "Pass-1
    fallback: statements that start with a known word") over an unparsable statement.

    Returns `("pass1", kind)` when a syntax error comes before any unknown name, `("pass2",
    kind)` when an unknown name comes first (the record is left for pass 2), or `None` for a shape
    not oracle-probed. `kind` names the message: `fallback`, `incomplete`, `no_matching`,
    `symbol`."""
    try:
        toks = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return None
    if not toks:
        return None
    functions = profile.functions
    first = toks[0]
    i = 1
    if first.kind == "IDENT":
        u = first.upper
        if u in _START_ERROR_WORDS:
            return ("pass1", "fallback")
        if u == "NOT":
            return _scan_expr(toks, 1, operands, functions, assign=False, start_operand=False)
        if u in functions and u not in operands:
            if i < len(toks) and toks[i].kind == "OP" and toks[i].text == "(":
                return _scan_expr(toks, 0, operands, functions, assign=False, start_operand=False,
                                  unclosed="no_matching")
            if _bare_function_needs_paren(u, functions):
                return ("pass1", "expects_paren")
            return None
        if u not in operands:
            return None
        if i < len(toks) and toks[i].kind == "OP" and toks[i].text == "(":
            return None
    elif first.kind in ("NUMBER", "STRING"):
        pass
    elif first.kind == "OP" and first.text == "-" and len(toks) > 1 and toks[1].kind == "NUMBER":
        i = 2
    elif first.kind == "OP" and first.text == "(":
        verdict = _scan_expr(toks, 0, operands, functions, assign=False, start_operand=False)
        return ("pass1", "fallback") if verdict == ("pass1", "incomplete") else verdict
    else:
        return None
    if i == len(toks):
        return ("pass1", "fallback")
    nxt = toks[i]
    if nxt.kind == "OP" and nxt.text == "=" and first.kind == "IDENT":
        return _scan_expr(toks, i + 1, operands, functions, assign=True, start_operand=False)
    if nxt.kind in ("IDENT", "NUMBER", "STRING") and not (nxt.kind == "IDENT"
                                                         and nxt.upper in _BINARY_WORDS):
        return ("pass1", "fallback")
    if nxt.kind == "OP" and nxt.text == ",":
        return ("pass1", "fallback")
    if (nxt.kind == "OP" and nxt.text in _BINARY_OPS) or nxt.kind == "IDENT":
        return _scan_expr(toks, i + 1, operands, functions, assign=False, start_operand=False)
    return None


def _scan_expr(toks, i: int, operands: set[str], functions: dict, assign: bool,
               start_operand: bool, unclosed: str | None = None) -> tuple[str, str] | None:
    unknown = ("pass2", "symbol" if assign else "fallback")
    unclosed = unclosed or ("no_matching" if assign else "fallback")
    parens: list[str] = []          # "call" or "group"
    expect_operand = not start_operand
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        opens = nxt is not None and nxt.kind == "OP" and nxt.text == "("
        if expect_operand:
            if t.kind in ("NUMBER", "STRING"):
                expect_operand = False
            elif t.kind == "IDENT":
                u = t.upper
                if u == "NOT":
                    pass
                elif u in _START_ERROR_WORDS:
                    return None
                elif u in functions and u not in operands:
                    if not opens:
                        if _bare_function_needs_paren(u, functions):
                            return ("pass1", "expects_paren")
                        expect_operand = False
                    else:
                        parens.append("call")
                        i += 1
                elif u in operands:
                    if opens:
                        parens.append("call")
                        i += 1
                    else:
                        expect_operand = False
                else:
                    return unknown
            elif t.kind == "OP" and t.text == "(":
                parens.append("group")
            elif t.kind == "OP" and t.text in ("-", "+"):
                pass
            elif t.kind == "OP" and t.text in _BINARY_OPS:
                return ("pass1", "fallback")
            else:
                return None
        else:
            if t.kind == "OP" and t.text == ")":
                if not parens:
                    return ("pass1", "incomplete")
                parens.pop()
            elif t.kind == "OP" and t.text == ",":
                if not parens:
                    return None if assign else ("pass1", "fallback")
                if parens[-1] != "call":
                    return ("pass1", "no_matching" if assign else "fallback")
                expect_operand = True
            elif t.kind == "OP" and t.text in _BINARY_OPS:
                expect_operand = True
            elif t.kind == "IDENT" and t.upper in _BINARY_WORDS:
                expect_operand = True
            elif t.kind in ("IDENT", "NUMBER", "STRING"):
                return ("pass1", "incomplete")
            else:
                return None
        i += 1
    if expect_operand:
        return ("pass1", "fallback") if not parens else None
    if parens:
        return ("pass1", unclosed)
    return None if assign else ("pass1", "fallback")


def _bare_function_needs_paren(name: str, functions: dict) -> bool:
    """`expressions._is_bare_reference_problem` without its declared-variable exemption (the
    caller has already excluded pass-1 operands)."""
    record = functions.get(name)
    return bool(record) and record.get("nargs") is not None and (
        record["nargs"] > 0 or bool(record.get("argspec")))


def _pass1_message(profile, stmt: S.Stmt, kind: str) -> str:
    return {
        "fallback": _out_of_place(profile, stmt.text, getattr(stmt, "at_eol", True)),
        "incomplete": f"Incomplete expression: {stmt.text}.",
        "no_matching": f"No Matching, or out of place ) in expression: {stmt.text}.",
        "symbol": f"Invalid symbol in expression: {stmt.text}.",
        "expects_paren": f"Expression expects ( : {stmt.text}.",
    }[kind]

def check_missing_param_followup(program: S.Program, profile, diags: Diagnostics) -> None:
    """When `instr_rules.check`'s required-parameter-shortfall message (`Invalid or missing
    parameter.`) fires for an instruction whose argument list was wrapped across two physical
    lines with no `_` continuation, the physical line the wrapped arguments spill onto is itself
    an unrecognisable statement (it starts mid-argument-list -- `(...)`/a bare `,...)` -- our
    parser records as `S.Unparsed`). Oracle-confirmed (`docs/checker.md` "Missing-parameter
    followup"), this specific leftover statement can get its own `Invalid, or out of place
    expression: %s.` line even though `check_pass2`'s own generic fallback for *every other* bare
    statement in the file is fully suppressed once pass 1 found anything wrong anywhere
    (`checker/__init__.py`'s `if not diags.errors:` gate) -- a scratch probe confirms an unrelated
    `foo bar baz` elsewhere in a file that also has an earlier `Variable not named.` stays fully
    silent, while this specific leftover-line diagnostic does not.

    **Gated on whether the leftover text's own first identifier is itself a genuinely resolvable
    symbol** (`_leftover_first_name_known`, reusing `expressions._known_names` -- see its own
    docstring for the full oracle evidence, including the `cdmresistance_1.CR6` case that narrowed
    this from an earlier, broader "every referenced name" version down to "only the first") --
    found by a corpus regression, not guessed: `getdatarecord_3.CR6` has this exact shape
    (`GetDataRecord` / `(ResultTD,COMC1,0,8,0000,0,2,1,TempDat,1)`) and is already `exact` with
    *no* follow-up line, unlike `getdatarecord_1.CR6`'s byte-for-byte-identical shape, which *does*
    get one. The only difference: `getdatarecord_3.CR6`'s `ResultTD` was never actually declared
    (it is the stranded name after a nameless `Public`, `_check_declaration`'s own "diagnostic
    suppressed, but the name itself was never registered" gap below) -- a from-scratch scratch
    probe confirms the effect directly: adding a real `Public ResultTD` declaration alongside the
    otherwise-identical, otherwise-still-undeclared program flips the follow-up on, while adding
    an unrelated `Public Foo` (declaring a name the leftover text never references) does not.

    Deliberately keyed to the *very next* statement in program order (every corpus case has them
    adjacent), and skips exactly the same `Unparsed.reason` classes `check_pass2` above already
    special-cases (a known parser gap, an empty statement, or one already reported by
    `check_pass1`'s own `Units`/`Const`/`Alias`/array-dimension checks) so the two paths can never
    double-report the same statement. Called unconditionally from `checker/__init__.py`, alongside
    `instr_rules.check` itself, not gated behind "pass 1 found nothing" -- it fires precisely
    *because* `instr_rules.check` found something on the immediately preceding statement.
    """
    known: set[str] | None = None
    statements = program.statements
    for i in range(len(statements) - 1):
        stmt = statements[i]
        if not isinstance(stmt, S.Instruction):
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        params = instr_rules._real_params(record["params"])
        required = instr_rules._required_count(params)
        if len(stmt.args) >= required:
            continue
        missing_code = params[len(stmt.args)] if len(stmt.args) < len(params) else None
        if missing_code in instr_rules._TOLERANT_CODES:
            continue
        nxt = statements[i + 1]
        if not isinstance(nxt, S.Unparsed):
            continue
        if nxt.reason.startswith(_KNOWN_PARSER_GAP_REASONS) or nxt.reason in _BENIGN_REASONS:
            continue
        if nxt.reason in (_UNITS_WITHOUT_EQ, _CONST_WITHOUT_EQ, _ALIAS_WITHOUT_EQ) or \
                nxt.reason.startswith(_INVALID_AS_CLAUSE) or \
                nxt.reason.startswith(_UNCLOSED_DIM_PREFIX):
            continue  # already reported by `check_pass1`
        if known is None:
            known = expressions._known_names(program, profile)
        if not _leftover_first_name_known(nxt.text, known):
            continue
        if any(d.line == nxt.line and d.file == nxt.file for d in diags.errors):
            continue  # already reported by `check_pass1`'s pass-1 scan
        if nxt.reason == _UNCLOSED_PAREN_EXPR:
            diags.error(nxt.line, f"No Matching, or out of place ) in expression: {nxt.text}.",
                        nxt.file, show_file=False)
        elif nxt.reason == _EXTRA_CLOSE_PAREN:
            diags.error(nxt.line, f"Incomplete expression: {nxt.text}.", nxt.file,
                        show_file=False)
        else:
            diags.error(nxt.line, _leftover_message(profile, nxt), nxt.file, show_file=False)


def _check_call_args(stmt: S.Call, sub: S.SubDef, diags: Diagnostics) -> None:
    """`Sub` call argument counts, pass 2, all four loggers (oracle, 2026-09-27): fewer arguments
    than the `Sub` has parameters without a default (including none: `Log1`, `Log1()`) -> `Call
    argument required.`; more than it has parameters -> `Call to Log1 with 2 parameters, requires
    1 arguments.` (`%s` = the `Sub`'s declared name)."""
    if sub.keyword.upper() != "SUB":
        return
    given = len(stmt.args)
    required = sum(1 for p in sub.params if p.default is None)
    if given > len(sub.params):
        diags.error(stmt.line, f"Call to {sub.name} with {given} parameters, requires "
                    f"{len(sub.params)} arguments.", stmt.file)
    elif given < required:
        diags.error(stmt.line, "Call argument required.", stmt.file)


def _report_call_arg_names(stmt: S.Call, known: set[str], profile, diags: Diagnostics) -> None:
    """Undefined names in a `Sub` call's arguments, every argument (oracle, 2026-09-28: `Two(Q,
    Q2)` reports both). CR6/CR1000X echo the argument (`Invalid symbol in expression: Q+1.`);
    CR300/CR350 name each undefined name, plus `Dimensions not declared: Nope(1).`."""
    m = re.match(r"\s*(?:call\s+)?" + re.escape(stmt.name) + r"\s*", stmt.text, re.I)
    if not m or not stmt.text[m.end():].startswith("("):
        return
    items = _group_items(stmt.text, m.end())
    if items is None or len(items) != len(stmt.args):
        return
    for arg, raw in zip(stmt.args, items):
        names = expressions._undefined_names(arg, known)
        if not names:
            continue
        echo = raw.strip()
        if profile.name in expressions._WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, f"Invalid symbol in expression: {echo}.", stmt.file)
            continue
        for name in names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
        if expressions._has_undefined_index(arg, known):
            diags.error(stmt.line, expressions.dimensions_message(echo, profile, False), stmt.file)


@dataclasses.dataclass
class _LinkContext:
    subs: set          # upper-case names of declared `Sub`s (not `Function`s)
    has_routines: bool  # any `Sub` or `Function` declared
    links: CallLinks


def _call_link_name(text: str) -> str:
    """The name the link step reports for an explicit `Call`: the statement text after `Call`
    with all whitespace removed, up to the first `(` (oracle, 2026-10-02, all four loggers:
    `Call S1 1` -> `S11`, `Call  S2 (1)` -> `S2`)."""
    text = re.sub(r"^\s*call(?![A-Za-z0-9_])", "", text, flags=re.I)
    return re.sub(r"\s+", "", text).split("(", 1)[0]


def _bare_sub_link_name(text: str, subs: set) -> str | None:
    """`S1 1,2` -- a bare statement whose first word is a declared `Sub` and whose remainder does
    not start with `(` -- reports `S11,2` (oracle, 2026-10-02). `None` for any other shape."""
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?=[^\s(=])", text)
    if not m or m.group(1).upper() not in subs:
        return None
    return re.sub(r"\s+", "", text).split("(", 1)[0]


def _unlinked_call(stmt, link: str, profile, diags: Diagnostics, before_beginprog: bool,
                   ctx: _LinkContext) -> int | None:
    """A `Call` naming no declared `Sub` (oracle, 2026-10-02, all four loggers; `docs/checker.md`
    "Unresolved `Call`s"). Program with no `Sub`/`Function`: `line N: Sub %s not defined.` at the
    call, ending the compile there (CR300/CR350: deferred, see `finalize_call_links`). Otherwise
    the call is only recorded for the link step, which reports `%s is not declared as a
    Subroutine.` (no line) once per call, last, in reverse source order; the call itself still
    gets `Call illegally occurs before BeginProg.` and a stop when it precedes `BeginProg`."""
    if ctx.has_routines:
        if before_beginprog:
            diags.error(stmt.line, "Call illegally occurs before BeginProg.", stmt.file)
            return stmt.line
        return None
    if profile.single_pass:
        if ctx.links.first is None:
            ctx.links.first = (stmt.line, stmt.file, f"Sub {link} not defined.")
        return None
    diags.error(stmt.line, f"Sub {link} not defined.", stmt.file, show_file=False)
    return stmt.line


# CR300/CR350 errors that leave the link step running (oracle, 2026-10-02, each probed beside a
# `Call Nope` in a program with a `Sub`); every other error there (name resolution, parameter
# shape, declarations) is a pass-1 error and skips it.
_LINK_TOLERATED = ("Call argument required", "Call to ", "Call illegally occurs", "Expr illegally occurs",
                   "Could not find DataTable", "Sample must be used inside", "EndTable without",
                   "DataTable is declared in the middle", "NextScan with no Scan", "EndSub without",
                   "IF or SELECT without", "FOR without NEXT", "Excitation exceeds", "Channel ",
                   "Invalid port")


def _link_step_runs(profile, diags: Diagnostics, pass1_errors: bool) -> bool:
    """The link step is skipped when pass 1 found errors: CR6/CR1000X (`check_pass2` never ran)
    or, on CR300/CR350 where name resolution is a pass-1 check, any error outside
    `_LINK_TOLERATED`."""
    if profile.single_pass:
        return not any(d.severity == "error" and not d.message.startswith(_LINK_TOLERATED)
                       for d in diags.items)
    return not pass1_errors


def commit_deferred_sub_error(links: CallLinks, profile, diags: Diagnostics) -> None:
    """CR300/CR350, called right after pass 2's tier B: the first no-routines `Sub %s not
    defined.` ends the compile there (later errors go, so the late and tier-C checks are skipped
    as after any error), unless a pass-1 error (`_link_step_runs`) hides it."""
    if links.first is None or not _link_step_runs(profile, diags, False):
        return
    line, file, message = links.first
    cutoff = links.positions.get((file, line))
    diags.items[:] = [d for d in diags.items
                      if d.severity != "error" or not d.line or cutoff is None or
                      links.positions.get((d.file, d.line), cutoff) <= cutoff]
    diags.error(line, message, file, show_file=False)


def finalize_call_links(links: CallLinks, profile, diags: Diagnostics, pass1_errors: bool) -> None:
    """Emit the link-time `%s is not declared as a Subroutine.` lines `_unlinked_call` recorded:
    last, one per call, newest first."""
    unresolved = [n for n in links.calls if n.upper() not in links.registered]
    if not unresolved or not _link_step_runs(profile, diags, pass1_errors):
        return
    for name in reversed(unresolved):
        diags.error(0, f"{name} is not declared as a Subroutine.")


def _check_call_pass2(stmt: S.Call, subs: set[str], profile, diags: Diagnostics,
                       before_bp: bool, in_body_block: bool,
                       sub_params: dict | None = None,
                       known: set[str] | None = None,
                       link_ctx: "_LinkContext | None" = None) -> int | None:
    """One `Call`-shaped statement's pass-2 check. Returns the line to stop pass 2 at, or `None`
    to keep going. Oracle-confirmed shapes (`docs/checker.md` "Pass model"), each independently
    verified this round:

    - `Call NoSuchSub` (explicit keyword, naming no declared `Sub`): see `_unlinked_call` and
      `docs/checker.md` "Unresolved `Call`s" (2026-10-02: `Sub %s not defined.` and a stop in a
      program without any `Sub`/`Function`, a deferred link-time message otherwise).
    - A bare bare `Name`/`Name(args)` (no `Call` keyword) resolving to a declared `Sub`/`Function`:
      valid, silent, *unless* it precedes `BeginProg`, where it is `Call illegally occurs before
      BeginProg.` (`%s` = literally `Call`) -- and then processing stops.
    - The same, resolving to a built-in *function* (`Timer`, `SerialOpen`, ... -- `data/
      functions.json`): also valid/silent after `BeginProg`, but `Expr illegally occurs before
      BeginProg.` (`%s` = literally `Expr`, **no** preceding "Invalid, or out of place expression"
      line -- the call itself compiles fine, only its position is illegal) before it, and stops.
    - Resolving to nothing at all: `Invalid, or out of place expression: %s.` (`%s` = the
      statement's own verbatim source text) always, **plus** `Expr illegally occurs before
      BeginProg.` and a stop when it precedes `BeginProg` -- **CR6/CR1000X only**
      (`profile.undefined_call_message`, `profiles.py`). **CR300/CR350** additionally report the
      call's own name as unresolved first (`{name} is not defined.`, `profile.
      unresolved_symbol_message`) and, oracle-confirmed the opposite of CR6/CR1000X here, never
      show the placement follow-up or stop for this one branch, even when the statement does
      precede `BeginProg` (`docs/loggers.md`) -- the other two branches above (resolving to a
      `Sub`/function) are unaffected, oracle-confirmed identical on all four loggers.
    """
    name = stmt.name.upper()
    before_beginprog = not in_body_block and before_bp
    if stmt.explicit and link_ctx is not None:
        link = _call_link_name(stmt.text)
        if link_ctx.has_routines:
            link_ctx.links.calls.append(link)
        if link.upper() not in link_ctx.subs:
            return _unlinked_call(stmt, link, profile, diags, before_beginprog, link_ctx)
    if sub_params and name in sub_params:
        _check_call_args(stmt, sub_params[name], diags)
        if known is not None:
            _report_call_arg_names(stmt, known, profile, diags)
    if name in subs:
        if before_beginprog:
            diags.error(stmt.line, "Call illegally occurs before BeginProg.", stmt.file)
            return stmt.line
        return None
    if name in profile.functions:
        if before_beginprog:
            diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
            return stmt.line
        return None
    if profile.undefined_call_message:
        # CR300/CR350 only (`profiles.py`'s own field docstring, `docs/loggers.md`): an extra
        # `{name} is not defined.` line right before the generic fallback below, and no
        # before-`BeginProg` follow-up/stop for this branch (unlike the two branches above).
        diags.error(stmt.line, profile.unresolved_symbol_message.format(name=stmt.name), stmt.file)
        diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file,
                    show_file=False)
        return None
    diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file,
                show_file=False)
    if before_beginprog:
        diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
        return stmt.line
    return None


def _check_assignment_pass2(stmt: S.Assignment, known: set[str], diags: Diagnostics,
                             before_bp: bool, in_body_block: bool,
                             profile=None) -> int | None:
    """One `Assignment` (`target = value`) statement's pass-2 check. Returns the line to stop pass 2
    at, or `None` to keep going. Oracle-confirmed shapes (`docs/checker.md` "Round 5", a scratch
    probe set alongside `_check_call_pass2`'s own):

    - **Target does not resolve** (`expressions._undefined_names(stmt.target, known)` -- the same
      walk a read-context argument uses, applied to the target: an undeclared bare name, an
      undeclared array *base*, or a declared array with an undeclared *index* expression all land
      here, oracle-confirmed each independently, `FuzzVar = 1` / `X(BadIdx) = 1` with `X`
      undeclared / `X(BadIdx) = 1` with `X` declared and `BadIdx` not): `Invalid, or out of place
      expression: %s.`, `%s` = the whole statement's own verbatim text (`stmt.text`, same echo
      convention as `_check_call_pass2`'s own "resolves to nothing" case) -- **regardless of
      whether the RHS is itself also undefined** (`FuzzVar1 = FuzzVar2`, both undeclared, still
      gives only this one message, not a second `Invalid symbol in expression` line for the RHS
      too -- `expressions.check`'s own assignment loop skips a statement shaped this way for
      exactly this reason, see its docstring) -- plus, if before `BeginProg` (and not inside a
      `Sub`/`Function`/other body block, same `in_body_block` rule as `_check_call_pass2`), `Expr
      illegally occurs before BeginProg.` and a stop.
    - **Target resolves, RHS has an undefined name** (`X = FuzzVar` with `X` declared): silent
      here, **including when before `BeginProg`** -- oracle-confirmed (`X = FuzzVar` on its own
      line before `BeginProg` gives only `expressions.check`'s own `Invalid symbol in expression: X
      = FuzzVar.`, no placement follow-up at all, unlike every other shape in this function). Left
      entirely to `expressions.check`'s own RHS-undefined-name loop, which already reports this
      correctly; this function does nothing further for such a statement (no diagnostic, no stop).
    - **Target resolves, RHS is clean**: normally silent (an ordinary assignment), but `Expr
      illegally occurs before BeginProg.` (no preceding "Invalid, or out of place expression" line
      -- the assignment itself compiles fine, only its position is illegal, same shape as a bare
      built-in-function call) and a stop when it precedes `BeginProg`.
    """
    before_beginprog = not in_body_block and before_bp
    target_names = expressions._undefined_names(stmt.target, known)
    if target_names and profile is not None and profile.single_pass:
        # CR300/CR350 (oracle, 2026-09-27): `Nope = Q` -> `Nope is not defined.`, `Q is not
        # defined.`; `Nope(2) = X` -> `Nope is not defined.` then the generic fallback. Neither
        # stops pass 2 or gets the before-`BeginProg` follow-up.
        for name in target_names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
        if isinstance(stmt.target, S.Index):
            diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file,
                        show_file=False)
        else:
            for name in expressions._undefined_names(stmt.value, known):
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                            stmt.file)
            if expressions._has_undefined_index(stmt.value, known):
                diags.error(stmt.line, expressions.dimensions_message(stmt.text, profile, True,
                                                                      stmt.at_eol), stmt.file)
        return None
    if target_names:
        diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file,
                    show_file=False)
        if before_beginprog:
            diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
            return stmt.line
        return None
    if expressions._undefined_names(stmt.value, known):
        return None  # `expressions.check` reports this alone; no placement follow-up (oracle).
    if before_beginprog:
        diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
        return stmt.line
    return None


def _check_declaration(stmt: S.Declaration, diags: Diagnostics) -> int:
    """A `Public`/`Dim` with nothing at all following on its own physical line (`parser.py`'s
    `_parse_declaration` models this as an empty `vars` list rather than raising a parse error).
    Oracle-confirmed (`docs/checker.md`): the real compiler's declaration parser reports `Variable
    not named.` right at the keyword's own line -- most CHM corpus instances are a name list
    wrapped onto the next physical line with no `_` continuation (`log10_1.CR6`: `Public` /
    `Number, B, Log10ofNumber, LogBofNumber`).

    Returns the line number of the statement immediately following this one to suppress: every
    corpus instance of this shape also has the real compiler saying nothing at all about that next
    physical line (the stranded name list), even though it independently fails to parse as a valid
    statement -- consistent across every sample this session checked, including ones with several
    consecutive nameless `Public`s (`getdatarecord_3.CR6`) and ones where the file's compile
    otherwise continues normally afterward (`cdmresistance_1.CR6`), so this is modelled as "this
    one specific next line is silent", not the broader (and only sometimes correct, `docs/
    checker.md`'s "before BeginProg" section) "the whole file stops" behaviour.
    """
    diags.error(stmt.line, "Variable not named.", stmt.file)
    return stmt.line + 1


def _first_dim_text(stmt_text: str, v: S.VarDecl) -> str:
    if v.dim0_span is None:
        return ""
    start, end = v.dim0_span
    return stmt_text[start:end]



def _check_duplicate_declaration(stmt: S.Stmt, declared: dict[str, str], diags: Diagnostics,
                                 profile=None) -> None:
    """Global-scope redeclaration, pass 1, all four loggers (oracle, 2026-09-27, `docs/checker.md`
    "Duplicate declarations"): a `Public`/`Dim` or `Const` name already declared as a variable
    (`Public`/`Dim`, or an `Alias` name) -> `%s already declared as a variable.`; a `Public`/`Dim`
    name already declared as a `Const` -> `%s is already declared as a CONST.` (`%s` as written in
    the later declaration). `Sub` parameters and `Dim`s inside a `Sub`/`Function` may shadow a
    global (the caller skips those bodies)."""
    if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM"):
        for index, v in enumerate(stmt.vars):
            if profile and v.name.upper() in profile.functions:
                symbols.report_function_name_collision(stmt, v.name, profile, diags)
                if profile.single_pass:
                    if index + 2 < len(stmt.vars):
                        diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                                    stmt.file)
                    break
                continue  # a failed function-name declaration never registers a variable
            prior = declared.get(v.name.upper())
            if prior == "VAR":
                diags.error(stmt.line, f"{v.name} already declared as a variable.", stmt.file)
            elif prior == "CONST":
                diags.error(stmt.line, f"{v.name} is already declared as a CONST.", stmt.file)
            else:
                declared[v.name.upper()] = "VAR"
            if profile and symbols.report_predefined_name_collision(stmt, v.name, profile, diags):
                if profile.single_pass:
                    if index + 2 < len(stmt.vars):
                        diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                                    stmt.file)
                    break
    elif isinstance(stmt, S.ConstDecl):
        if profile and stmt.name.upper() in profile.functions:
            symbols.report_function_name_collision(stmt, stmt.name, profile, diags)
            return
        if profile and symbols.report_predefined_name_collision(stmt, stmt.name, profile, diags):
            return  # failed Const declarations do not register a new constant
        prior = declared.get(stmt.name.upper())
        if prior == "VAR":
            diags.error(stmt.line, f"{stmt.name} already declared as a variable.", stmt.file)
        elif prior == "CONST":
            diags.error(stmt.line, f"{stmt.name} is already declared as a CONST.", stmt.file)
        else:
            declared[stmt.name.upper()] = "CONST"
    elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "READONLY":
        # `ReadOnly Nope` -> `Nope not yet declared.`, pass 1, all four loggers (oracle)
        for a in stmt.args:
            if isinstance(a, S.Name) and declared.get(a.name.upper()) != "VAR":
                diags.error(stmt.line, f"{a.name} not yet declared.", stmt.file)
    elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ALIAS":
        for a in stmt.args[1:]:
            if isinstance(a, S.Name):
                declared.setdefault(a.name.upper(), "VAR")
    elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ESSVARIABLES" and profile:
        # declares its table's variables: a later `Public essBatteryStatus` -> `already declared
        # as a variable.` (oracle, 2026-10-01; `checker/ess.py`)
        from . import ess
        for row in ess._table(str(profile.data_dir)):
            if row["count"]:
                declared.setdefault(row["name"].upper(), "VAR")

def _check_array_dims(stmt: S.Declaration, diags: Diagnostics, env: consteval.Env) -> None:
    """`Public`/`Dim` array-dimension limits (oracle-confirmed, `docs/checker.md` "Array
    dimension limits"): more than `_MAX_ARRAY_DIMS` dimensions on one variable, checked before any
    per-dimension value, reports `Variable %s dimension exceeds max of %d.` naming the variable
    plus its *first* dimension's own raw source text (`Dim Arr(2,2,2,2)` ->
    `Variable Arr(2 dimension exceeds max of 3.`, verified for both `Public` and `Dim`, and
    independent of which later dimension would itself be illegal). Otherwise, each dimension that
    folds to a constant outside `[1, _MAX_DIM_VALUE]` (a literal zero/negative, or one too large to
    represent, such as `999999999999` -- the fuzz-found hang, `docs/fuzz-findings.md`) reports
    `Illegally dimension: %s`, `%s` = the *whole declaration statement's* own verbatim source text
    (not just the offending variable -- oracle-confirmed with a multi-variable declaration where
    only one variable's dimension is illegal), no trailing period (oracle-confirmed, unlike most
    other messages here). One diagnostic per illegal dimension value found (not deduplicated by
    statement), oracle-confirmed with two illegal dimensions on one variable and with two on
    separate variables of the same declaration.

    A dimension that does not fold to a constant at all (`NotConstant`) is left alone: no oracle
    evidence this session found supports guessing at that shape, and AGENTS.md's priority (never
    reject an accepted program) rules out it.
    """
    for v in stmt.vars:
        if len(v.dims) > _MAX_ARRAY_DIMS:
            first_text = _first_dim_text(stmt.text, v)
            diags.error(
                stmt.line,
                f"Variable {v.name}({first_text} dimension exceeds max of {_MAX_ARRAY_DIMS}.",
                stmt.file,
            )
            continue
        for d in v.dims:
            if isinstance(d, S.String):
                # `Public Q("fuzz")`, pass 1, all four loggers (oracle, 2026-09-27)
                diags.error(stmt.line, f"Illegally dimension: {stmt.text}", stmt.file)
                continue
            try:
                val = consteval.value_of(d, env)
            except consteval.NotConstant:
                continue
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            if not (1 <= val <= _MAX_DIM_VALUE):
                diags.error(stmt.line, f"Illegally dimension: {stmt.text}", stmt.file)



def _split_top(text: str, sep: str = ",") -> list[str]:
    """`text` split on `sep` outside parentheses, braces and strings."""
    items, depth, in_str, start = [], 0, False, 0
    for i, ch in enumerate(text):
        if ch == '"':
            in_str = not in_str
        elif in_str:
            continue
        elif ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        elif ch == sep and depth == 0:
            items.append(text[start:i])
            start = i + 1
    items.append(text[start:])
    return items


def _group_items(text: str, open_at: int) -> list[str] | None:
    """Raw comma-separated items of the `(`/`{` group opening at `text[open_at]`."""
    close = {"(": ")", "{": "}"}[text[open_at]]
    depth, in_str = 0, False
    for i in range(open_at, len(text)):
        ch = text[i]
        if ch == '"':
            in_str = not in_str
        elif in_str:
            continue
        elif ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
            if depth == 0:
                return _split_top(text[open_at + 1:i]) if ch == close else None
    return None


def _report_decl_expr(expr, echo: str, known: set[str], variables: set[str], profile,
                      stmt: S.Stmt, diags: Diagnostics, allow_variable: bool = False) -> bool:
    """An undefined name (`Invalid symbol in expression: %s.`; CR300/CR350: `%s is not defined.`
    per name, then `Variable illegal in Parameter: %s.`) or a live variable (`Variable illegal in
    Parameter: %s.`) inside a declaration's constant expression (`docs/checker.md` "Declaration
    expressions"). Returns whether anything was reported."""
    names = expressions._undefined_names(expr, known)
    if names:
        if profile.name in expressions._WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, f"Invalid symbol in expression: {echo}.", stmt.file)
        else:
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
            diags.error(stmt.line, f"Variable illegal in Parameter: {echo}.", stmt.file)
        return True
    if not allow_variable and expressions._variable_refs(expr, variables):
        diags.error(stmt.line, f"Variable illegal in Parameter: {echo}.", stmt.file)
        return True
    return False


# `As` types a `Public`/`Dim` variable may have besides a declared `StructureType` (oracle, all four
# loggers, 2026-09-27): `UInt2`, `UInt4`, `Bool8`, `NSec`, `IEEE4`, `IEEE8`, `FP2`, `Int4`,
# `Integer` and `ULong` all give `Variable Type %s not supported` (no period) although the
# binaries know them as table data types.
_VARIABLE_TYPES = frozenset({"FLOAT", "LONG", "BOOLEAN", "STRING", "DOUBLE", "UINT1"})
_EMPTY_DIMS_AS_RE = re.compile(r"(?i)\s*\(\s*\)(\s*As)\b")
_AS_TYPE_RE = re.compile(r"(?i)\bAs\s+([A-Za-z_][A-Za-z0-9_]*)")
_TYPED_DIMS_RE = re.compile(r"(?i)\s*As\s+[A-Za-z_][A-Za-z0-9_]*\s*(?=\()")


def _check_init_bounds(stmt: S.Declaration, env: consteval.Env, diags: Diagnostics) -> None:
    """`Warning: Attempt to initialize variable out of bounds.`, once per variable with more
    initializer values than elements, all four loggers (oracle, 2026-09-28: `Public A(2) = {1,2,3}`,
    `Public A = {1,2}`, `Dim A(2,2) = {1,2,3,4,5}`; an unresolved dimension counts as 1)."""
    for v in stmt.vars:
        if not v.init:
            continue
        size = 1
        for d in v.dims:
            try:
                n = consteval.value_of(d, env)
            except consteval.NotConstant:
                continue
            if isinstance(n, bool) or not isinstance(n, (int, float)) or int(n) < 1:
                size = None
                break
            size *= int(n)
        if size is not None and len(v.init) > size:
            diags.warning(stmt.line, "Attempt to initialize variable out of bounds.", stmt.file)


def _check_decl_exprs(stmt: S.Declaration, known: set[str], variables: set[str], profile,
                      diags: Diagnostics, struct_types: frozenset[str] = frozenset()) -> None:
    """Pass-1 name resolution in `Public`/`Dim`/`Static` array dimensions and initializers.
    Oracle, all four loggers (`docs/checker.md` "Declaration expressions"): `Public A(3, Nope)` ->
    `Invalid symbol in expression: Nope.` (each bad dimension, echo left-trimmed); `Dim A(Y)`, `Y`
    a variable -> `Variable illegal in Parameter: Y.`; `Public B As Long = Nope * 2` -> `Invalid
    symbol in expression: Nope * 2.` then `Variable initialization of B has bad syntax.`"""
    body = stmt.text.lstrip()
    body = body[len(body.split(None, 1)[0]):] if body else ""
    segments = _split_top(body)
    if len(segments) != len(stmt.vars):
        return
    for v, seg in zip(stmt.vars, segments):
        name_at = seg.upper().find(v.name.upper())
        if name_at < 0:
            continue
        empty_as = _EMPTY_DIMS_AS_RE.match(seg[name_at + len(v.name):])
        if empty_as:
            # `Public A() As Long` -> `Invalid symbol in expression:  As.` (CR300/CR350: `As is not
            # defined.` then `Variable illegal in Parameter:  As.`); plain `Public A()` compiles
            # (oracle, 2026-09-27).
            echo = empty_as.group(1)
            if profile.name in expressions._WHOLE_EXPR_ECHO_LOGGERS:
                diags.error(stmt.line, f"Invalid symbol in expression: {echo}.", stmt.file)
            else:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=echo.strip()),
                            stmt.file)
                diags.error(stmt.line, f"Variable illegal in Parameter: {echo}.", stmt.file)
            continue
        if (stmt.kind in ("PUBLIC", "DIM") and v.type and v.type not in _VARIABLE_TYPES
                and v.type not in struct_types):
            m = _AS_TYPE_RE.search(seg)
            if m and m.group(1).upper() == v.type:
                diags.error(stmt.line, f"Variable Type {m.group(1)} not supported", stmt.file)
        rest_at = name_at + len(v.name)
        rest = seg[rest_at:]
        typed_dims = _TYPED_DIMS_RE.match(rest)
        if v.dims and (rest.lstrip().startswith("(") or typed_dims):
            # `Public A(Nope)` and `Public A As Float(Nope)` alike (oracle, 2026-09-28)
            open_at = rest_at + (typed_dims.end() if typed_dims else len(rest) - len(rest.lstrip()))
            items = _group_items(seg, open_at)
            if items is not None and len(items) == len(v.dims):
                for d, text in zip(v.dims, items):
                    _report_decl_expr(d, text.lstrip(), known, variables, profile, stmt, diags)
        if v.init:
            eq = _split_top(seg, "=")
            if len(eq) != 2:
                continue
            init_text = eq[1]
            stripped = init_text.lstrip()
            if stripped.startswith("{"):
                items = _group_items(init_text, len(init_text) - len(stripped))
            else:
                items = [init_text]
            if items is None or len(items) != len(v.init):
                continue
            bad = False
            for e, text in zip(v.init, items):
                bad |= _report_decl_expr(e, text.lstrip(), known, variables, profile, stmt,
                                         diags, allow_variable=True)
            if bad:
                diags.error(stmt.line, f"Variable initialization of {v.name} has bad syntax.",
                            stmt.file)


def _check_alias_index_names(stmt: S.Instruction, known: set[str], variables: set[str],
                             profile, diags: Diagnostics) -> bool:
    """`Alias X( Nope ) = Q` -> `Invalid symbol in expression:  Nope .` (raw index text) then
    `Alias is illegally dimensioned.`, all loggers (CR300/CR350 with their own undefined-name
    pair). Returns whether it reported, so `_check_alias_dims` is skipped."""
    target = stmt.args[0] if stmt.args else None
    if not isinstance(target, S.Index) or target.name.upper() not in variables:
        return False
    if any(isinstance(a, S.String) for a in target.args):
        # `Alias W("fuzz") = WF`, all four loggers (oracle, 2026-09-27)
        diags.error(stmt.line, "Illegally dimensioned aliased name", stmt.file)
        return True
    open_at = stmt.text.find("(")
    items = _group_items(stmt.text, open_at) if open_at >= 0 else None
    if items is None or len(items) != len(target.args):
        return False
    for text in items:
        item = text.strip()
        if item.startswith("(") and item.endswith(")"):
            # `Alias V((1+2)) = W` -> `No Matching, or out of place ) in expression: (1+2.`, all
            # four loggers (oracle, 2026-10-01): the item's own `)` closes the index list
            diags.error(stmt.line, f"No Matching, or out of place ) in expression: {item[:-1]}.",
                        stmt.file)
            diags.error(stmt.line, "Alias is illegally dimensioned.", stmt.file)
            return True
    bad = False
    for a, text in zip(target.args, items):
        if a is not None and expressions._undefined_names(a, known):
            bad |= _report_decl_expr(a, text, known, variables, profile, stmt, diags)
    if bad:
        diags.error(stmt.line, "Alias is illegally dimensioned.", stmt.file)
    return bad

def _alias_flat_offset(index: tuple[int, ...], dims: tuple[int, ...]) -> int:
    """The 1-based row-major flat offset of `index` into an array declared with dimension sizes
    `dims` (both same length), last index varying fastest -- the ordinary multi-dimensional-
    array-as-flat-buffer formula: `flat = i_n + sum_{k=1}^{n-1} (i_k - 1) * prod(dims[k:n-1])`.
    `docs/checker.md` "Round 7" has the oracle sweep that found this is what `Illegally
    dimensioned aliased name` actually bounds-checks (`[1, prod(dims)]`), not "only the first
    index against its own dimension's size" as round 6 originally (and, this round's own sweep
    found, incorrectly) concluded.
    """
    flat = index[-1]
    mult = 1
    for k in range(len(dims) - 2, -1, -1):
        mult *= dims[k + 1]
        flat += (index[k] - 1) * mult
    return flat


def _check_alias_dims(stmt: S.Instruction, diags: Diagnostics, env: consteval.Env,
                       dim0_sizes: dict[str, int], all_dims: dict[str, tuple[int, ...]],
                       seen_alias_names: set[str],
                       seen_alias_sources: dict[str, set[tuple]]) -> None:
    """Four `Alias <name>(<index>) = <target>` checks, all pass 1/unconditional (oracle-confirmed
    each accumulates with an unrelated, later `Variable not named.` in the same file, and, being
    pass 1, all suppress every pass-2 check the same way any other pass-1 error already does -- no
    special "stop the file" behaviour of its own needed for any of the four):

    1. **`%s not yet declared so cannot be aliased.`** (round 7, this session; `docs/messages.tsv`
       `FUN_004b8e70`): `name` -- the source side's own base name, scalar or array, with or without
       an index -- is not a declared `Public`/`Dim`/`Static` variable at all (`dim0_sizes`, `%s` =
       `name` in its own written case). Oracle-confirmed to pre-empt every other check below
       (`docs/checker.md` "Round 7"): an out-of-range or duplicate index on an *undeclared* source
       never additionally fires "Illegally dimensioned"/"Duplicate Alias" -- only this one line,
       once per `Alias` statement referencing the undeclared name (not deduplicated the way 3/4
       below are: three separate `Alias undeclaredvar(N) = ...` lines each get their own copy).
    2. **`Illegally dimensioned aliased name`** (round 6, no trailing period; **round 7
       correction** to how the index is checked): the index tuple -- every declared dimension, not
       only the first -- folds via `int()` (truncating toward zero, matching every other value
       check in this project) into a single **row-major flat offset** (`_alias_flat_offset`:
       `flat = i_n + sum_{k<n} (i_k - 1) * prod(d_{k+1..n})`, the ordinary multi-dimensional-array-
       as-flat-buffer formula, last index varying fastest) that must land in `[1,
       prod(all declared dims)]`. Round 6's own original account of this check ("only the *first*
       index is ever checked, however far out of range a later one is") is **superseded**: it was
       built from two probes (`Public grid(3,3)`: `Alias grid(2,4) = Ts` compiles, `Alias grid(4,2)
       = Ts` fails) that happen to *also* agree with the flat-offset formula purely by coincidence
       (`(2-1)*3+4=7` and `(4-1)*3+2=11`, both landing on the correct side of `[1,9]` for unrelated
       reasons) -- a fresh, wider oracle sweep this round (`docs/checker.md` "Round 7") found the
       "first index alone" account is wrong in general: `Alias grid(4,-2) = Ts` (first index `4`,
       out of `[1,3]` by the old account) **compiles clean** (`(4-1)*3+(-2)=7`, in `[1,9]`), while
       `Alias grid(2,-1) = Ts` (a valid first index, an out-of-range *second* index by the old
       account, which the old rule never checked at all) also compiles clean
       (`(2-1)*3+(-1)=2`, in `[1,9]`) -- both correctly predicted by the flat formula, neither by
       "only check position 0". A **scalar** (no declared dimensions at all) behaves exactly like a
       size-1 array (`Public y` / `Alias y(1) = Ts` compiles, `Alias y(2) = Ts` fails: flat
       formula with `dims=(1,)` reduces to `flat=i1`, range `[1,1]`), which is why `declared_all_
       dims` maps every declared variable, not just array ones, the same convention `dim0_sizes`
       already used. Skipped (no check at all, under-reporting only) when any declared dimension or
       any index argument does not fold to a constant (`declared_all_dims`/the tuple-folding loop
       below, both AGENTS.md-conservative the same way check 3 already was) or when the index
       tuple's own length does not match the variable's own declared dimension count (no oracle
       evidence gathered for that shape either way).
    3. **`Duplicate Alias`** (round 7; no trailing period, no echoed name): the source side's own
       full index tuple -- every declared dimension, not just the first, each truncated via `int()`
       like check 2 -- has already been used to alias *some* name (any name; the two are
       independent, oracle-confirmed: reusing an index with a *different* target name still fires
       this, `Alias wind(1) = Ux` then `Alias wind(1) = Uy`) earlier in the same program, keyed per
       declared array/scalar base name (a plain integer index colliding on `wind` never collides
       with the same index on an unrelated `irga`, and a *different* full tuple on a multi-
       dimensional array, e.g. `grid(1,1)` vs `grid(1,2)`, never collides either -- oracle-
       confirmed both ways). A **scalar** source (bare `name`, no parens at all) behaves as if its
       own index tuple were always the same one value -- re-aliasing the same scalar under any
       second name collides exactly like reusing an array index. Independent of (and additive
       with, not exclusive to) check 2: the same out-of-range index reused twice reports "Illegally
       dimensioned aliased name" on *both* occurrences and *also* "Duplicate Alias" on the second
       (oracle-confirmed). Only tracked when the index folds to a constant on every dimension
       (`AGENTS.md`: a non-constant index is left unchecked here rather than guessed at, under-
       reporting only).
    4. **`Duplicate Alias Name: %s.`** (round 7; `%s` = the *current* statement's own rhs spelling,
       not the first occurrence's): the alias target name itself (case-insensitively) has already
       been used as *some* `Alias`'s own target earlier in the program, independent of the source
       side entirely (oracle-confirmed: two different scalars each aliased to the same name collide;
       two different array elements of the *same* array each aliased to the same name collide too).
       Reported on every repeat occurrence (2nd, 3rd, ...), not just the first repeat. Both this and
       check 3 require the *current* statement's own source to itself be declared (`dim0_sizes`;
       check 1's own gate) -- an undeclared source's target name is never added to `seen_alias_
       names` and never checked against it either (oracle-confirmed, `docs/checker.md` "Round 7":
       re-aliasing an already-used name via an undeclared source reports only check 1's line).
       Ordering, when both check 3 and check 4 fire on the same statement (an exact repeat of a
       prior `Alias` statement, source and target both duplicated): check 3 first, then check 4
       (oracle-confirmed both orderings are never reversed).
    """
    target = stmt.args[0] if stmt.args else None
    if not isinstance(target, (S.Index, S.Name)):
        return
    name_upper = target.name.upper()
    size = dim0_sizes.get(name_upper)
    if size is None:
        diags.error(stmt.line, f"{target.name} not yet declared so cannot be aliased.", stmt.file)
        return

    if isinstance(target, S.Index) and not target.args:
        # `Alias Flag() = WriteMessage` (empty parens, no index at all) -- a distinct shape from
        # an out-of-range index, oracle-confirmed by a saved fuzz reproducer (`docs/checker.md`
        # "Round 6", `tests/corpus/fuzz/cr6/false_accept/2b628e5fb8f1.CR6`, cross-checked with a
        # from-scratch probe): **two** diagnostics together, `Need closing )` then `Alias is
        # illegally dimensioned.` (a different message from the out-of-range one above -- no
        # trailing period either), both at the `Alias` statement's own line. An empty-parens
        # `Index` elsewhere (a plain array reference, `y()`) is ordinary and legal, oracle-confirmed
        # compiling clean, so this is specific to `Alias`'s own target grammar, not a general
        # "empty parens" parser gap. Not combined with checks 3/4 below -- no oracle evidence this
        # session found for what (if anything) this shape contributes to either dedup set.
        diags.error(stmt.line, "Need closing )", stmt.file)
        diags.error(stmt.line, "Alias is illegally dimensioned.", stmt.file)
        return

    index_key: tuple | None = ()  # scalar `name` (bare, no parens): one implicit index position
    if isinstance(target, S.Index):
        index_key = ()
        for a in target.args:
            try:
                v = consteval.value_of(a, env)
            except consteval.NotConstant:
                index_key = None
                break
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                index_key = None
                break
            index_key = (*index_key, int(v))

        dims = all_dims.get(name_upper)
        if index_key is not None and dims is not None and len(index_key) == len(dims):
            total = 1
            for d in dims:
                total *= d
            if not (1 <= _alias_flat_offset(index_key, dims) <= total):
                diags.error(stmt.line, "Illegally dimensioned aliased name", stmt.file)

    if index_key is not None:
        bucket = seen_alias_sources.setdefault(name_upper, set())
        if index_key in bucket:
            diags.error(stmt.line, "Duplicate Alias", stmt.file)
        else:
            bucket.add(index_key)

    for rhs in stmt.args[1:]:
        if not isinstance(rhs, S.Name):
            continue
        rhs_upper = rhs.name.upper()
        if rhs_upper in seen_alias_names:
            diags.error(stmt.line, f"Duplicate Alias Name: {rhs.name}.", stmt.file)
        else:
            seen_alias_names.add(rhs_upper)


def _after_keyword(stmt: S.Unparsed) -> str:
    """`stmt.text` (the raw, unstripped physical-line text, `parser.py`'s `_segment_text`) with
    its own leading whitespace and 5-letter keyword (`Units`/`Const`/`Alias`, all coincidentally
    5 characters) removed -- the echoed trailing text these "ran off the end of the line" messages
    share (empty in every corpus instance this session found)."""
    return stmt.text.lstrip()[5:].strip()


def _check_unparsed_pass1(stmt: S.Unparsed, diags: Diagnostics) -> None:
    """The specific-reason `Unparsed` diagnoses only -- true pass-1 (`docs/checker.md` "Pass
    model": `Need '=' in units definition: %s.`/`Const with no assignment.`/`Need '=' in alias
    definition: %s.`/`Invalid Variable declaration %s.` are all in the coordinator's own pass-1
    list, unlike the generic "not a valid statement" fallback, which is pass-2 (`check_pass2`
    above) and is *not* handled here at all."""
    if stmt.reason.startswith(_KNOWN_PARSER_GAP_REASONS) or stmt.reason in _BENIGN_REASONS:
        return
    if stmt.reason == _UNITS_WITHOUT_EQ:
        # `Units`'s own sub-parser reports this (not the generic fallback). `docs/frontend.md`'s
        # `Units X =` experiment shows the *paired* message ("Nothing after '=' in units
        # definition: %s.", not implemented here, no corpus evidence for it) uses the same echo
        # shape.
        diags.error(stmt.line, f"Need '=' in units definition: {_after_keyword(stmt)}.", stmt.file)
        return
    if stmt.reason == _UNITS_TARGET_NO_EQ:
        # Target parsed, no `=` on this physical line: oracle-confirmed echo embeds a literal `\n`
        # before the closing `.` (`docs/checker.md` "Echo quirk"). `stmt.text` here is already just
        # the target's own source text (set by `_parse_units_segment`, not overwritten the way
        # `Alias`'s is -- see that branch below).
        diags.error(stmt.line, f"Need '=' in units definition: {stmt.text}\n.", stmt.file)
        return
    if stmt.reason == _CONST_WITHOUT_EQ:
        diags.error(stmt.line, "Const with no assignment.", stmt.file)
        return
    if stmt.reason == _ALIAS_WITHOUT_EQ:
        diags.error(stmt.line, f"Need '=' in alias definition: {_after_keyword(stmt)}.", stmt.file)
        return
    if stmt.reason == _ALIAS_TARGET_NO_EQ:
        # Same "target parsed, no '=' on this line" shape as `_UNITS_TARGET_NO_EQ` above
        # (`realtime_1.CR6`: `Alias rTime(1)` / `= Year`), oracle-confirmed identical echo quirk.
        # `stmt.text` here *is* overwritten to the full raw line by `parser.py`'s `_parse_segment`
        # (unlike `Units`, `Alias` goes through the general statement path), so `_after_keyword`
        # still does the keyword-stripping work.
        diags.error(stmt.line, f"Need '=' in alias definition: {_after_keyword(stmt)}\n.", stmt.file)
        return
    if stmt.reason.startswith(_INVALID_AS_CLAUSE):
        bad_token = stmt.reason[len(_INVALID_AS_CLAUSE):]
        diags.error(stmt.line, f"Invalid Variable declaration {bad_token}.", stmt.file)
        return
    if stmt.reason.startswith(_UNCLOSED_DIM_PREFIX):
        # `parser.py`'s `_close_dims_or_raise`: a `Public`/`Dim`/`Static` dimension list left open
        # to the end of the physical line. `%s` here is *not* `stmt.text` -- the real compiler
        # echoes just the variable's own name through end of line, not the `Public`/`Dim` keyword
        # (oracle-confirmed, `docs/checker.md` "Need closing ) shapes").
        text = stmt.reason[len(_UNCLOSED_DIM_PREFIX):]
        diags.error(stmt.line, f"{text} is missing the ) character.", stmt.file)
        return
    # Any other reason (including the generic "not a valid statement" shape) is `check_pass2`'s
    # job, not this pass's -- deliberately does nothing further here.
