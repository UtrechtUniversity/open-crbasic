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
from ..profiles import DottedName
from ..diagnostics import Diagnostics
from . import expressions, instr_data, instr_rules, symbols

# Array-dimension limits on a `Public`/`Dim` declaration (oracle-confirmed, `docs/checker.md`
# "Array dimension limits"): at most 3 dimensions, and each one's constant value must be in
# `[1, _MAX_DIM_VALUE]`. `_MAX_DIM_VALUE` is `INT32_MAX` -- every value at or below it that this
# session could actually get the real compiler to accept/reject cleanly (rather than crash) falls
# on the expected side of this boundary; a handful of merely-too-large-to-allocate values well
# below it instead get `Out of memory.` (a distinct message, `checker/storage.py`), so this check only ever fires where "Illegally dimension" is the confirmed text.
_MAX_ARRAY_DIMS = 3
_MAX_DIM_VALUE = 2147483647

# `Unparsed.reason` values from two oracle-confirmed parser gaps (docs/frontend.md "Known
# residual gaps"): the real compiler still accepts these lines, so the generic "not a valid
# statement" fallback below must stay silent for them. Matched as a prefix so unrelated
# `reason` text is never accidentally suppressed.
_KNOWN_PARSER_GAP_REASONS = (
    "unexpected trailing input starting at '+='",   # samplemaxmin_1.CR6: `X=X+=1`
    "dotted SNMP OID literal",                       # snmpvariable_1.CR6: a dotted-OID literal
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
# `=` seen, value empty or ending in a binary operator: `reason` is this prefix plus the raw value
# (the caller overwrites `Unparsed.text`)
_CONST_BAD_VALUE = "Const bad value: "
_CONST_BAD_NAME = "Const bad name: "
_CONST_LEAD = "Const lead: "
_CONST_LEAD_NAME = "Const lead: name "
# `Do`/`Loop` followed by something other than `While`/`Until`: `reason` is this prefix plus the
# pass-1 message (`parser._parse_doloop`; oracle, 2026-10-10, all four loggers)
_DO_LOOP_PARAM = "Do/Loop parameter: "
# `Alias <target> =` with nothing after the `=` (`parser._parse_alias`; oracle, 2026-10-10)
_ALIAS_NOTHING_AFTER_EQ = "Alias nothing after '='"
# `parser.py`'s `_parse_for`: a `For` header with no `=`, no `To`, or nothing after `To`. All four
# loggers, pass 1: `Invalid or missing parameter.` (docs/checker.md "Round 4 (CR6/CR1000X ...)").
_FOR_MISSING = "For missing parameter"
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
_ASSIGNMENT_HEAD = re.compile(r"\s*[A-Za-z_]\w*\s*(?:\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)\s*)?[-+*/\\^&]?=")


def _assignment_head(text: str, program, profile) -> bool:
    """Whether `text` starts like an assignment (`X =`, `A(1) +=`, `A((1)) =`). A subscript with
    nested parentheses is one only when every name in it is defined (`A(Len(S)) = 1)` with `S`
    undeclared is the generic fallback) and, on CR300/CR350, no call-like name in it is a scalar
    (`A(X(1)) = 1)`) (oracle, all four loggers, 2026-10-12)."""
    head = _ASSIGNMENT_HEAD.match(text)
    if head is None:
        return False
    inner = text[text.find("(") + 1:head.end()] if "(" in text[:head.end()] else ""
    if "(" in inner:
        known = expressions._known_names(program, profile)
        if any(n.upper() not in known for n in re.findall(r"[A-Za-z_]\w*", inner)):
            return False
        if profile.fallback_echo_newline:
            counts = symbols.declared_dim_counts(program)
            if any(counts.get(n.upper()) == 0 for n in re.findall(r"([A-Za-z_]\w*)\s*\(", inner)):
                return False
    return True


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


# A `Scan` while another is open (a literal-zero one too, in or out of `SlowSequence`): reported at
# the inner `Scan`, does not stop pass 2, and replaces the end-of-program `Scan without NextScan.`
# (oracle, all four loggers, 2026-10-03; CR300/CR350 print it late, `check_scan_placement_late`).
NESTED_SCAN = "Scan without NextScan or nested Scans."


def _scan_counts_for_table_placement(stmt: S.Stmt, env: consteval.Env | None = None) -> bool:
    """Whether this parsed ``Scan`` contains a valid interval for ``CallTable`` placement.

    The compilers leave a zero-interval ``Scan`` structurally open for its matching ``NextScan``,
    but a ``CallTable`` within it is still outside a Scan for an auto-sized table.  The interval
    is zero for a literal zero and for every constant the compiler's own `long` arithmetic turns
    into zero seconds and zero microseconds (``instr_rules.scan_split``): ``0.5`` Sec, ``0.9``
    mSec, ``1073741824`` Min (wraps), a string or unknown unit, and a value beyond float32's
    long range times a unit factor that wraps to zero (oracle, all four loggers, 2026-10-12,
    ``r15_scan_zero_interval.json``).
    """
    args = getattr(stmt, "args", []) or []
    if args and isinstance(args[0], S.Number) and args[0].value == 0:
        return False
    if env is not None and isinstance(stmt, S.Instruction):
        return instr_rules.scan_split(stmt, env) != (0, 0)
    return True


def _check_calltable_outside_scan(stmt: S.Instruction, program: S.Program, diags: Diagnostics,
                                  in_sub: bool, zero_scan: bool = False, env=None) -> None:
    """`CallTable` outside any `Scan` (before it, after `NextScan`, or in a `Sub`), pass 2, all four
    loggers (oracle, 2026-09-27): a table with `DataInterval` -> `Table with DataInterval must be
    called within Scan..NextScan.`; an auto-sized one (negative size) -> `Cannot autoallocate memory
    for DataTable %s called outside of Scan, e.g., called by Sub.`; a fixed-size table without
    `DataInterval` is fine. Inside a `Sub` only the second (probed) message is emitted. Inside a
    literal-zero `Scan` (`zero_scan`) a `DataInterval` table gets neither message, while an
    auto-sized one without `DataInterval` still gets the second (oracle, all four loggers,
    2026-10-03)."""
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
    if env is not None and size is not None:
        # only the sizes the logger auto-allocates: CR300/CR350 `-1`, CR6/CR1000X any negative or
        # out-of-range one (a CR300/CR350 `-1000` is a fixed 1000 and compiles; docs/tdf-rules.md)
        from .. import tables as T
        raw = T._fold(size, env, None)
        if raw is not None:
            auto = T.declared_size(raw, env.profile.name in ("cr300", "cr350"), 1) is None
    if zero_scan and has_interval:
        return
    if has_interval and not in_sub:
        diags.error(stmt.line, "Table with DataInterval must be called within Scan..NextScan.",
                    stmt.file)
    if auto and not (in_sub and has_interval):
        diags.error(stmt.line, f"Cannot autoallocate memory for DataTable {table.args[0].name} "
                    "called outside of Scan, e.g., called by Sub.", stmt.file)

def _accept_known(stmt: S.Stmt, state: dict) -> bool:
    args = getattr(stmt, "args", []) or []
    return len(args) > 2 and isinstance(args[2], S.Name) and \
        args[2].name.upper() in state.get("closed", set())


def _check_accept_table(stmt: S.Instruction, program: S.Program, diags: Diagnostics,
                        outside: bool, env) -> None:
    """`AcceptDataRecords(.., .., Table)` fills `Table` like a `CallTable` made at that statement
    (oracle, all four loggers, 2026-10-13): outside every `Scan` (or in a `Sub`) an auto-sized table
    gets the `Cannot autoallocate` message (as for `CallTable`), and a table with a `DataInterval`
    whose `Lapses` is not 0 (also `DataInterval(0,0,..)`) gets `Data Table cannot be interval
    driven.` at the statement, after it. Inside a `Scan` a fixed or auto table without interval is
    fine."""
    from .. import tables as T
    name = stmt.args[2].name.upper()
    table = None
    driven = False
    for st in program.statements:
        kw = _keyword(st)
        if kw == "DATATABLE":
            args = getattr(st, "args", []) or []
            table = st if args and isinstance(args[0], S.Name) and args[0].name.upper() == name else None
            driven = False
        elif kw == "DATAINTERVAL" and table is not None:
            lapses = getattr(st, "args", [])[3:4]
            val = T._fold(lapses[0], env, None) if lapses else 10
            driven = driven or val != 0
        elif kw == "ENDTABLE" and table is not None:
            break
    if table is None:
        return
    size = table.args[2] if len(table.args) > 2 else None
    auto = isinstance(size, S.Unary) and size.op == "-" or isinstance(size, S.Number) and size.value < 0
    raw = T._fold(size, env, None) if size is not None else None
    if raw is not None:
        auto = T.declared_size(raw, env.profile.name in ("cr300", "cr350"), 1) is None
    if outside and auto:
        diags.error(stmt.line, f"Cannot autoallocate memory for DataTable {table.args[0].name} "
                    "called outside of Scan, e.g., called by Sub.", stmt.file)
    if driven:
        diags.error(stmt.line, "Data Table cannot be interval driven.", stmt.file)


def _ends_sub(stmt: S.Stmt, kw: str) -> bool:
    return kw in ("ENDSUB", "ENDFUNCTION") or (
        kw == "END" and _block_word(getattr(stmt, "args", []) or []) in ("SUB", "FUNCTION"))


def first_misplaced_sub(program: S.Program) -> tuple[S.SubDef | S.If, list[str]] | None:
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
    consts: set[str] = set()
    in_program = False
    for stmt in program.statements:
        kw = _keyword(stmt)
        in_program = in_program or kw == "BEGINPROG"
        if kw == "ENDPROG":
            in_program = False
        if isinstance(stmt, S.If) and stmt.inline is not None and (in_program or open_sub or in_table):
            # `If C Then Sub S`: the `Sub` is inside an `If` and inside the executing code, anywhere
            # between `BeginProg` and `EndProg` (oracle, all four loggers, 2026-10-12; before
            # `BeginProg` it is `If illegally occurs before BeginProg.`)
            for inner in (stmt.inline or []) + (stmt.inline_else or []):
                if isinstance(inner, S.SubDef):
                    msgs = ["Sub declared within a Sub."] if open_sub else []
                    msgs += ["Sub declaration inside If..EndIf.",
                             f"{'Function' if inner.keyword.upper() == 'FUNCTION' else 'Sub'} "
                             "is declared in the middle of an executing sequence"]
                    return stmt, msgs
        if isinstance(stmt, S.ConstDecl):
            consts.add(stmt.name.upper())
        elif isinstance(stmt, S.SubDef) and stmt.name.upper() in consts:
            open_sub = True  # reported on its own (`_report_numeric_sub_name`), never a placement error
        elif isinstance(stmt, S.SubDef):
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


def check_apply_restart_sequence(program: S.Program, profile, diags: Diagnostics) -> None:
    """`ApplyAndRestartSequence` / `EndApplyAndRestartSequence` bookkeeping (oracle, all four
    loggers, 2026-10-13, docs/checker.md "Round 16 message-diff families"): an `End...` before any
    `ApplyAndRestartSequence` is `ApplyAndRestartSequence not declared`; a second `Apply...` (closed
    or not) is `ApplyAndRestartSequence already declared`; a first one in executable code (the
    contexts of `first_misplaced_sub`) is `ApplyAndRestartSequence is declared in the middle of an
    executing sequence` and ends the compile there."""
    declared = open_sub = in_table = before_sequence = False
    conditional_depth = active_scans = for_do_depth = 0
    for stmt in program.statements:
        kw = _keyword(stmt)
        if kw == "APPLYANDRESTARTSEQUENCE":
            executing = (open_sub or in_table or before_sequence or active_scans > 0
                         or conditional_depth > 0 or for_do_depth > 0)
            if declared:
                diags.error(stmt.line, "ApplyAndRestartSequence already declared", stmt.file)
            elif executing:
                diags.error(stmt.line, "ApplyAndRestartSequence is declared in the middle of an "
                            "executing sequence", stmt.file)
                return
            declared = True
        elif kw == "ENDAPPLYANDRESTARTSEQUENCE":
            if not declared:
                diags.error(stmt.line, "ApplyAndRestartSequence not declared", stmt.file)
        elif isinstance(stmt, S.SubDef):
            open_sub = True
        elif _ends_sub(stmt, kw):
            open_sub, conditional_depth = False, 0
        elif kw == "BEGINPROG":
            before_sequence = True
        elif kw in ("ENDPROG", "SLOWSEQUENCE"):
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
    conditional statement in PipeLine Mode.`. `Delay` only with a constant-folded option 0 (`Const Zero = 0`, `One-1`; oracle, 2026-10-03). The message
    comes before the instruction's own argument errors (`Invalid channel ...`), except `ExciteV`
    on CR6 and `PortSet` on CR1000X; those are not reordered.

    Returns `(statement, message)` pairs in order."""
    found: list[tuple[S.Stmt, str]] = []
    if profile.single_pass:
        return found
    task_rules = profile.pipeline_tasks["tasks"]
    env = consteval.Env(profile, dict(program.consts))
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
                if index >= len(args):
                    return
                try:
                    value = consteval.value_of(args[index], env)
                except consteval.NotConstant:
                    return
                if value not in allowed:
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
    """`_scan_placement_walk`, then the table-structure findings (`Sample must be used inside a
    table declaration.`, `EndTable without DataTable.`) of the statements before the walk's stop
    (CR300/CR350, oracle 2026-10-13: they are printed in line order before the placement error,
    which ends the compile; tier C does not run once the walk has failed)."""
    n_before = len(diags.items)
    stop = _scan_placement_walk(program, profile, diags)
    if stop is None:
        return None
    at = next((i for i, st in enumerate(program.statements)
               if (st.file, st.line) == stop), None)
    if at is None:
        return stop
    early = Diagnostics()
    table_stop = instr_data.check_table_structure(
        dataclasses.replace(program, statements=program.statements[:at]), profile, early)
    for d in early.items:
        if d.severity != "error":
            continue
        index = next((i for i in range(n_before, len(diags.items))
                      if diags.items[i].severity == "error" and diags.items[i].line > d.line),
                     len(diags.items))
        diags.items.insert(index, d)
    if table_stop is not None:
        diags.items[n_before:] = [d for d in diags.items[n_before:]
                                  if d.severity != "error" or d.line <= table_stop[1]]
        return table_stop
    return stop


def _scan_placement_walk(program: S.Program, profile, diags: Diagnostics) -> tuple | None:
    """CR300/CR350: `CallTable` outside `Scan`, the first extra `NextScan` and the before-`BeginProg`
    placement errors, in statement order, after the table `Reps` checks and before the
    unclosed-block checks (oracle, 2026-09-27: an unrelated `X = Q` or `Sample (0,X,FP2)` hides
    them; a `CallTable` error and a stray `NextScan` print together; an unclosed `Do` is then not
    reported). A stray `NextScan` or a placement error ends the walk; its `(file, line)` is
    returned so the caller can drop later warnings. CR6/CR1000X report these in `check_pass2`."""
    if not profile.single_pass:
        return None
    env = consteval.Env(profile, dict(program.consts))
    in_table = in_body = in_decl = seen_beginprog = False
    scans: list[bool] = []
    tables: dict = {}
    subs = symbols.declared_subs(program)
    stray = stray_controls(program)
    stray.update({k: (m, True) for k, m in scan_only_outside(program).items()})
    stray.update(subscan_controls(program, profile))
    misplaced = first_misplaced_sub(program)
    for stmt in program.statements:
        if misplaced and stmt is misplaced[0]:
            for message in misplaced[1]:
                diags.error(stmt.line, message, stmt.file)
            return stmt.file, stmt.line
        for item in [stmt] + (((stmt.inline or []) + (stmt.inline_else or [])) if isinstance(stmt, S.If) else []):
            if id(item) in stray:
                diags.error(item.line, stray[id(item)][0], item.file)
                if stray[id(item)][1]:
                    return item.file, item.line
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
        elif kw == "SLOWSEQUENCE" and scans:
            diags.error(stmt.line, "Missing NextScan.", stmt.file)   # (oracle, 2026-10-12)
            return stmt.file, stmt.line
        elif kw == "SCAN":
            if scans:
                diags.error(stmt.line, NESTED_SCAN, stmt.file)
            scans.append(_scan_counts_for_table_placement(stmt, env))
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(getattr(stmt, "args", []) or []) == "SCAN"):
            if not scans:
                diags.error(stmt.line, "NextScan with no Scan.", stmt.file)
                return stmt.file, stmt.line
            scans.pop()
        elif kw == "CALLTABLE" and not in_table and (not any(scans) or in_body) and \
                _calltable_known(stmt, tables):
            _check_calltable_outside_scan(stmt, program, diags, in_body,
                                          bool(scans) and not in_body, env)
        elif kw == "ACCEPTDATARECORDS" and not in_table and _accept_known(stmt, tables):
            _check_accept_table(stmt, program, diags, not any(scans) or in_body, env)
        if kw in _BODY_BLOCK_STARTS:
            in_body = True
        elif S.ends_routine(stmt) or kw in _BODY_BLOCK_ENDS:
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
    # Open `If`/`Select` kinds, kept across `EndSub`/`EndFunction` (oracle, 2026-10-11): an open
    # block at a Sub end is reported once there and stays open for the rest of the file.
    opens: list[str] = []
    loops = {"DO": 0, "FOR": 0}
    scans = 0
    in_sub = False
    seen_beginprog = False
    for stmt in program.statements:
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []
        word = _block_word(args) if kw in ("END", "NEXT", "EXIT") else None
        message, stops = None, False
        seen_beginprog = seen_beginprog or kw == "BEGINPROG"
        if isinstance(stmt, S.SubDef):
            if opens and not in_sub:
                # a block left open by an earlier Sub end is still open here (oracle, all four
                # loggers, 2026-10-12)
                message = "Sub declaration inside If..EndIf."
            in_sub = True
        elif kw in ("ENDSUB", "ENDFUNCTION") or (kw == "END" and word in ("SUB", "FUNCTION")):
            if not in_sub:
                message = "EndSub without Sub or nested subs."  # `EndFunction` too (oracle, 2026-10-03)
            elif opens:
                message = "If without EndIf inside of Sub."  # one per Sub end (oracle, 2026-10-11)
            in_sub = False
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            elses.append(False)
            opens.append("SELECT" if kw == "SELECT" else "IF")
        elif (kw == "ENDSELECT" or (kw == "END" and word == "SELECT")) and not in_sub \
                and not seen_beginprog and "SELECT" in opens:
            # a `Select` left open by an earlier Sub end, closed before `BeginProg` (oracle, 2026-10-11)
            message, stops = "EndSelect illegally occurs before BeginProg.", True
        elif kw in ("ENDIF", "ENDSELECT") or (kw == "END" and word in ("IF", "SELECT")):
            if elses:
                elses.pop()
            if opens:
                opens.pop()
        elif kw == "ELSE" or isinstance(stmt, S.Case) and _is_case_else(stmt):
            if not elses:
                message, stops = "Else with no If.", True
            elif elses[-1]:
                message = "If statement with duplicate Else."
            else:
                elses[-1] = True
        elif kw == "ELSEIF" and not elses:
            message, stops = "Case with no Select.", True
        elif kw in ("DO", "FOR", "WHILE"):
            loops["FOR" if kw == "FOR" else "DO"] += 1
        elif kw in ("LOOP", "WEND"):
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
        if isinstance(stmt, S.If) and stmt.inline is not None and (seen_beginprog or in_sub):
            # the same checks for the statements of a one-line `If` body (oracle, all four loggers,
            # 2026-10-12: `If C Then Exit Do` with no loop, `Exit For`, `Exit Sub`, `EndSub`)
            for sub in (stmt.inline or []) + (stmt.inline_else or []):
                sub_kw = _keyword(sub)
                sub_word = _block_word(getattr(sub, "args", []) or []) if sub_kw in ("EXIT", "END") else None
                if sub_kw == "EXIT" and sub_word in ("DO", "FOR") and loops[sub_word] == 0:
                    found[id(sub)] = (f"Exit {sub_word.capitalize()} without {sub_word}.", False)
                elif sub_kw == "EXIT" and sub_word in ("SUB", "FUNCTION") and not in_sub:
                    found[id(sub)] = ("Exit Sub without Sub or nested subs.", False)
                elif not in_sub and (sub_kw in ("ENDSUB", "ENDFUNCTION")
                                     or sub_kw == "END" and sub_word in ("SUB", "FUNCTION")):
                    found[id(sub)] = ("EndSub without Sub or nested subs.", False)
    return found


# Measurements whose compile function rejects them outside a `Scan` (including in a `Sub`) and
# ends pass 2 (decompiled `FUN_0050e960` and `PulseCount`'s own copy; oracle, 2026-09-27).
SCAN_ONLY = {"PULSECOUNT": "PulseCount", "QUADRATURE": "Quadrature", "TIMERINPUT": "TimerInput"}


def scan_only_outside(program: S.Program) -> dict[int, str]:
    """`id(stmt) -> message` for a `SCAN_ONLY` measurement outside any `Scan`."""
    found: dict[int, str] = {}
    scans = 0
    in_sub = False
    statements: list[S.Stmt] = []
    for stmt in program.statements:   # a one-line `If`'s statements count too (oracle, 2026-10-12)
        statements.append(stmt)
        if isinstance(stmt, S.If):
            statements.extend((stmt.inline or []) + (stmt.inline_else or []))
    for stmt in statements:
        kw = _keyword(stmt)
        if isinstance(stmt, S.SubDef):
            in_sub = True
        elif S.ends_routine(stmt):
            in_sub = False
        elif kw == "SCAN":
            scans += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(getattr(stmt, "args", []) or []) == "SCAN"):
            scans = max(0, scans - 1)
        elif kw in SCAN_ONLY and isinstance(stmt, S.Instruction) and (in_sub or scans == 0):
            found[id(stmt)] = f"{SCAN_ONLY[kw]} must be used within a Scan...NextScan."
    return found


_SUBSCAN_ZERO_TIME = frozenset({"CALLTABLE", "SERIALIN", "ELSE", "ENDIF", "SELECT", "ENDSELECT", "NEXT", "END",
                                "EXIT", "EXITSCAN", "CONTINUESCAN", "WHILE", "WEND"})
_SUBSCAN_PLAIN = (S.Assignment, S.Declaration, S.ConstDecl, S.Label, S.If, S.ElseIf, S.Case, S.DoLoop, S.For)


def _subscan_body_time(stmt: S.Stmt, profile) -> bool | None:
    """Whether `stmt` adds time to the open `SubScan`: `False` when certainly not (an assignment, a
    control statement, `CallTable`, `SerialIn`, a `Delay` whose option is a nonzero constant,
    `Battery` on the CR6; the observed zero-time cases), `None` for anything the model does not
    know."""
    if isinstance(stmt, _SUBSCAN_PLAIN):
        return False
    if not isinstance(stmt, S.Instruction):
        return None
    kw = stmt.keyword.upper()
    if kw in _SUBSCAN_ZERO_TIME or kw == "BATTERY" and profile.name == "cr6":
        return False
    if kw == "DELAY" and stmt.args and isinstance(stmt.args[0], S.Number) and stmt.args[0].value != 0:
        return False
    return None


def subscan_controls(program: S.Program, profile) -> dict[int, tuple[str, bool]]:
    """`id(stmt) -> (message, stops)` for `SubScan` blocks (oracle, 2026-10-12, `docs/checker.md`
    "Round 15: SubScan"): a `SubScan` outside a `Scan` (or in a `Sub`) is `SubScan used outside of
    Scan .. NextScan.` (stops pass 2, all four loggers), one inside a `SubScan` is `Cannot Nest
    SubScans.`, and on CR6/CR1000X a `SubScan` that adds no time of its own (nested ones do not
    count) is `No Measurements in SubScan.` at its `NextSubScan`. A body holding anything the model
    does not know is never reported."""
    found: dict[int, tuple[str, bool]] = {}
    scans = 0
    in_sub = False
    frames: list[list] = []   # per open SubScan: [certainly_zero_so_far]
    flat: list[S.Stmt] = []
    for stmt in program.statements:
        flat.append(stmt)
        if isinstance(stmt, S.If):
            flat.extend((stmt.inline or []) + (stmt.inline_else or []))
    for stmt in flat:
        kw = _keyword(stmt)
        word = _block_word(getattr(stmt, "args", []) or []) if kw in ("END", "NEXT") else None
        if isinstance(stmt, S.SubDef):
            in_sub = True
        elif S.ends_routine(stmt):
            in_sub = False
        elif kw == "SCAN":
            scans += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and word == "SCAN"):
            scans = max(0, scans - 1)
            frames.clear()
        elif kw == "SUBSCAN" and isinstance(stmt, S.Instruction):
            if in_sub or scans == 0:
                found[id(stmt)] = ("SubScan used outside of Scan .. NextScan.", True)
                return found
            if frames:
                found[id(stmt)] = ("Cannot Nest SubScans.", False)
            frames.append([True])
        elif kw == "NEXTSUBSCAN" or (kw == "NEXT" and word == "SUBSCAN"):
            if frames and frames.pop()[0] and not profile.single_pass:
                found[id(stmt)] = ("No Measurements in SubScan.", False)
        elif frames and frames[-1][0] and _subscan_body_time(stmt, profile) is None:
            frames[-1][0] = False
    return found


def check_stray_controls(program: S.Program, profile, diags: Diagnostics) -> int | None:
    """CR6/CR1000X pass 2 (`stray_controls`); returns the `(file, line)` pass 2 stops at, if any. CR300/CR350
    report these in `check_scan_placement_late`."""
    if profile.single_pass:
        return None
    found = stray_controls(program)
    found.update({k: (m, True) for k, m in scan_only_outside(program).items()})
    found.update(subscan_controls(program, profile))
    statements: list[S.Stmt] = []
    for stmt in program.statements:
        statements.append(stmt)
        if isinstance(stmt, S.If):
            statements.extend((stmt.inline or []) + (stmt.inline_else or []))
    for stmt in statements:
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
    if isinstance(stmt, S.Unparsed) and kw == "UNPARSED" and re.match(r"(?i)\s*ESSInitialize\s*\(", stmt.text) \
            and _quote_in_first_arg(stmt.text):
        return "ESSInitialize"   # a cut-off first string is still `ESSInitialize` (round 16)
    if isinstance(stmt, S.Assignment):
        return "Expr"
    if isinstance(stmt, S.Call):
        return "Call" if stmt.name.upper() in subs else \
            "Expr" if stmt.name.upper() in profile.functions else None
    return None


def _quote_in_first_arg(text: str) -> bool:
    """Whether the call's first top-level `"` string never closes before the first top-level comma
    (strings skipped), e.g. `Word("a,"b")`."""
    i = text.find("(") + 1
    while 0 < i < len(text):
        if text[i] == '"':
            j = text.find('"', i + 1)
            if j < 0:
                return True
            i = j
        elif text[i] == ",":
            return False
        i += 1
    return False


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


def _table_names(program: S.Program) -> set[str]:
    return {a.name.upper() for st in program.statements
            if isinstance(st, S.Instruction) and st.keyword.upper() in ("DATATABLE", "CONSTTABLE")
            for a in st.args[:1] if isinstance(a, S.Name)}


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
            inner = text[1:i]
            if _constant_assignment(inner, profile):
                # `(Double = Z) x`: the group is read as an expression whose assignment target is a
                # constant (oracle, CR300/CR350, 2026-10-11, docs/checker.md "Parenthesised assignment")
                return f"Invalid assignment in expression: {inner}."
            return f"{_FALLBACK_PREFIX} {inner}."
    return _out_of_place(profile, text[1:], stmt.at_eol)


def _constant_assignment(text: str, profile) -> bool:
    """`Double = Z`, `1 = 1`, `Sec = 1`: one constant, string or predefined-symbol token, then `=`."""
    try:
        toks = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return False
    if len(toks) < 3 or not (toks[1].kind == "OP" and toks[1].text == "="):
        return False
    first = toks[0]
    return first.kind in ("NUMBER", "STRING") or (
        first.kind == "IDENT" and first.upper not in profile.instructions
        and first.upper in set(profile.symbols) | expressions._EXTRA_PREDEFINED_SYMBOLS)


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
    if getattr(profile, "fallback_echo_newline", False) and ";" in text and (
            _semicolon_assignment(text) or _function_statement(text, profile)):
        # CR300/CR350 reject a `;` inside an assignment as an invalid symbol (oracle, 2026-10-12,
        # docs/checker.md "Round 13"); CR6/CR1000X skip it instead (parser._drop_semicolons)
        return f"Invalid symbol in expression: {text}{sep}."
    return f"Invalid, or out of place expression: {text}{sep}."


def _function_statement(text: str, profile) -> bool:
    """A built-in function written as a statement (`Timer(1 ;,Sec,0)`, `SerialOpen(...) ;`): with a
    `;` anywhere CR300/CR350 echo it as an invalid symbol (oracle, 2026-10-12)."""
    m = re.match(r"\s*([A-Za-z_]\w*)\s*\(", text)
    return bool(m) and m.group(1).upper() in profile.functions \
        and m.group(1).upper() not in profile.instructions


def _semicolon_assignment(text: str) -> bool:
    """An assignment whose `;` all come after its `=`: before the `=` it is the generic fallback."""
    from .. import parser
    try:
        toks = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return False
    if not toks or (toks[0].kind == "OP" and toks[0].text == ";"):
        return False
    depth = 0
    for t in toks:
        if t.kind == "OP" and t.text in "()":
            depth += 1 if t.text == "(" else -1
        elif t.kind == "OP" and t.text == ";":
            return False   # a `;` before the assignment operator
        elif depth == 0 and t.kind == "OP" and t.text in parser._ASSIGN_OPS:
            break
    return any(t.kind == "OP" and t.text == ";" for t in toks) and parser._is_assignment(
        [t for t in toks if not (t.kind == "OP" and t.text == ";")])


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


_END_EXIT_WORDS = frozenset({"IF", "SELECT", "SUB", "FUNCTION", "PROG", "TABLE", "SCAN", "DO", "FOR"})


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
    struct_members: set[str] = set()
    structs_seen: set[str] = set()
    const_tables = const_open = slow_sequences = 0
    seen_endprog = False
    const_shadow_reported = False
    declared_kinds: dict[str, str] = {}
    struct_types = frozenset(
        st.args[0].name.upper() for st in program.statements
        if isinstance(st, S.Instruction) and st.keyword.upper() in ("STRUCTURETYPE", "OBJECTTYPE")
        and st.args and isinstance(st.args[0], S.Name))
    decl_variables = {v.name.upper() for st in program.statements
                      if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC")
                      for v in st.vars}

    first_decl: dict[str, int] = {}
    for pos, stmt in enumerate(program.statements):
        if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
            for v in stmt.vars:
                first_decl.setdefault(v.name.upper(), pos)

    for pos, stmt in enumerate(program.statements):
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []

        fire_suppress = suppress_line is not None and stmt.line == suppress_line
        suppress_line = None

        if kw in ("END", "EXIT") and isinstance(stmt, S.Instruction) and len(args) == 1 and \
                isinstance(args[0], S.Name) and args[0].name.upper() not in _END_EXIT_WORDS and \
                args[0].name.upper() not in profile.symbols and \
                args[0].name.upper() not in profile.functions:
            # `End Sequence`/`Exit Foo` (oracle, 2026-10-03, all four loggers); a word the compiler
            # tokenizes as a predefined symbol is mapped to other messages (`End Min` is `EndSub
            # without Sub`, `End Day` `EndTable without DataTable`) and is not modelled.
            diags.error(stmt.line, f"Illegal END or EXIT {args[0].name}.", stmt.file)

        if isinstance(stmt, S.SubDef):
            if not in_sub and declared_kinds.get(stmt.name.upper()) == "CONST":
                _report_numeric_sub_name(stmt, program.consts.get(stmt.name.upper()), diags)
            if not in_sub:
                _check_header_types(stmt, structs_seen, profile, diags)
            in_sub = True
        elif kw in ("ENDSUB", "ENDFUNCTION") or (kw == "END" and _block_word(args) in ("SUB", "FUNCTION")):
            in_sub = False
        if kw in ("STRUCTURETYPE", "OBJECTTYPE"):
            in_struct = True
            struct_members = set()
            if args and isinstance(args[0], S.Name):
                structs_seen.add(args[0].name.upper())
        elif kw in ("ENDSTRUCTURETYPE", "ENDOBJECTTYPE"):
            if not in_struct:
                # a closer with no `StructureType` open (oracle, all four loggers, 2026-10-10)
                diags.error(stmt.line, "EndStructure without Structure", stmt.file)
            in_struct = False
            struct_members = set()
        dup_at = None
        dup_diags = diags
        if not in_sub and not in_struct:  # a structure body names its own members
            if (profile.single_pass and isinstance(stmt, S.Declaration)
                    and stmt.kind in ("PUBLIC", "DIM") and stmt.vars):
                dup_diags = Diagnostics()  # merged with the dimension checks below
            dup_at = _check_duplicate_declaration(stmt, declared_kinds, dup_diags, profile)
        elif in_sub and not in_struct:
            symbols.check_local_name_collision(stmt, profile, diags)
            if not seen_endprog:
                # a Sub/Function parameter, Dim or Const named like a global Const declared earlier
                # (oracle, all four loggers, 2026-10-11): `N is already declared as a CONST.`; CR300/
                # CR350 report only the first such line in the file (`_local_const_conflicts`)
                for name in _local_const_conflicts(stmt, declared_kinds):
                    if not (profile.single_pass and const_shadow_reported):
                        diags.error(stmt.line, f"{name} is already declared as a CONST.", stmt.file)
                    const_shadow_reported = True
                if (isinstance(stmt, S.Declaration) and stmt.kind == "PUBLIC") or \
                        isinstance(stmt, S.ConstDecl):
                    # `Public` and `Const` inside a routine declare globals: they conflict with, and
                    # are conflicted by, every other global of the name, even from an earlier routine
                    # (oracle, all four loggers, 2026-10-12, `docs/checker.md` "Routine-body
                    # `Public` and `Const`"); a `Dim` stays local
                    dup = Diagnostics()
                    if isinstance(stmt, S.ConstDecl):
                        prior = declared_kinds.get(stmt.name.upper())
                        if prior in ("VAR", "ALIAS"):
                            dup.error(stmt.line, f"{stmt.name} already declared as a variable.", stmt.file)
                        elif prior is None and not symbols.is_reserved_predefined_name(stmt.name, profile) \
                                and stmt.name.upper() not in profile.functions:
                            declared_kinds[stmt.name.upper()] = "CONST"
                    else:
                        _check_duplicate_declaration(stmt, declared_kinds, dup, None)
                    for item in dup.items:
                        if "is already declared as a CONST" in item.message:
                            if profile.single_pass and const_shadow_reported:
                                continue
                            const_shadow_reported = True
                        diags.error(item.line, item.message, item.file)
        if in_struct and isinstance(stmt, S.Declaration) and stmt.kind == "STRUCTUREMEMBER":
            struct_members.update(v.name.upper() for v in stmt.vars)
            _check_member_types(stmt, structs_seen, profile, diags)
            if known is None:
                known = expressions._known_names(program, profile)
                operands = pass1_operands(program, profile)
            # a member's dimensions and initializer resolve like a declaration's (oracle, all
            # four loggers, 2026-10-10: `FuzzVar = Nope` inside `StructureType`)
            if stmt.vars and any(v.init or v.dims or v.string_size is not None for v in stmt.vars):
                top = {v.name.upper() for st in program.statements if isinstance(st, S.Declaration)
                       and st.kind != "STRUCTUREMEMBER" for v in st.vars}
                member_only = {v.name.upper() for st in program.statements
                               if isinstance(st, S.Declaration) and st.kind == "STRUCTUREMEMBER"
                               for v in st.vars} - top - set(program.consts)
                _check_decl_exprs(stmt, known - member_only, decl_variables, profile, diags,
                                  struct_types)
        elif (in_struct or in_sub) and kw == "READONLY":
            # In a structure or `Sub`/`Function` body, `ReadOnly` resolves against the global
            # variables declared so far, plus the structure's own earlier members; neither a
            # `Const`, a later declaration, a local, a parameter nor another structure's member
            # counts (oracle, all four loggers, 2026-10-03).
            if not args:
                diags.error(stmt.line, "Missing variable name.", stmt.file)
            for a in args:
                if isinstance(a, S.Name) and a.name.upper() not in struct_members and \
                        declared_kinds.get(a.name.upper()) not in ("VAR", "ALIAS"):
                    diags.error(stmt.line, f"{a.name} not yet declared.", stmt.file)

        if isinstance(stmt, S.Declaration):
            for v in stmt.vars:
                if getattr(v, "size_missing", None) is not None:
                    # `Public A As String *` (oracle, all four loggers, 2026-10-10), once per
                    # statement, the first one only on CR300/CR350. The echo is the text after the
                    # `*` minus one blank; empty or a comma next -> `Size of string missing.`, else
                    # the generic fallback on the blanks (`*  'c`, `*  `)
                    echo = stmt.text[v.size_missing + 1:]
                    message = ("Size of string missing." if not echo or echo.lstrip().startswith(",")
                               else f"Invalid, or out of place expression: {echo}.")
                    if not (profile.single_pass and any(
                            d.message == "Size of string missing." for d in diags.items)):
                        diags.error(stmt.line, message, stmt.file)
                    break
        if profile.known_first_word_pass1 and isinstance(stmt, S.Unparsed) and stmt.reason != _FOR_MISSING \
                and re.match(r"\s*For\s+\w+\s*=", stmt.text, re.I) and not re.search(r"\bTo\b", stmt.text, re.I):
            # a `For` without `To` never gets to its expressions (oracle, CR6/CR1000X, 2026-10-12)
            diags.error(stmt.line, "Invalid or missing parameter.", stmt.file)
            continue
        if profile.known_first_word_pass1 and re.match(r"\s*For\s", getattr(stmt, "text", "") or "", re.I) \
                and (found := _for_header_findings(stmt.text, profile)):
            for message in found:
                diags.error(stmt.line, message, stmt.file)
            continue
        if (profile.known_first_word_pass1 or profile.single_pass) and _block_keyword_call(stmt):
            cond = re.match(r"\s*(?:ELSEIF|IF)\s(.*?)THEN", stmt.text, re.I | re.S) \
                if isinstance(stmt, (S.If, S.ElseIf)) else None
            sep = "\n" if not cond and profile.fallback_echo_newline and getattr(stmt, "at_eol", False) else ""
            diags.error(stmt.line, "Incomplete expression: "
                        f"{cond.group(1) if cond else stmt.text}{sep}.", stmt.file)
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
            elif kw == "MQTTPUBLISHCONSTTABLE" and not const_open and kw in profile.instructions:
                # (oracle, all four loggers, 2026-10-12: before `BeginProg` or in a `Scan`)
                diags.error(stmt.line, "MQTTPublishConstTable instruction must be in a ConstTable",
                            stmt.file)
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
                if known is None:
                    known = expressions._known_names(program, profile)
                    operands = pass1_operands(program, profile)
                if profile.single_pass:
                    # one bad variable stops the declaration: the first of an illegal dimension,
                    # an undefined dimension/initializer name and a redeclared name wins; on the
                    # same variable the dimension error comes before the redeclaration, which
                    # names the one two on (oracle, CR300/CR350, 2026-10-04: `Public a(-1),
                    # b(nv), c` / `a(nv), b(-1), c` / `Public P(-1), Q, R` after `Public P, Q, R`)
                    dims_diags, names_diags = Diagnostics(), Diagnostics()
                    earlier_errors = any(d.severity == "error" for d in diags.items)
                    dim_at = _check_array_dims(stmt, dims_diags, env, profile)
                    name_at = _check_decl_exprs(stmt, known, decl_variables, profile, names_diags,
                                                struct_types, dim_at)
                    bad_at = name_at if name_at is not None else dim_at
                    own_items = names_diags.items if name_at is not None else \
                        dims_diags.items + names_diags.items
                    if dup_at is None:
                        diags.items.extend(own_items)
                    elif bad_at is None or dup_at < bad_at:
                        diags.items.extend(dup_diags.items)
                    elif dup_at == bad_at:
                        diags.items.extend([d for d in own_items if not d.message.startswith(
                            "Too many parameters:")] + dup_diags.items)
                    else:
                        diags.items.extend(own_items)
                    stops = [i for i in (bad_at, dup_at) if i is not None]
                    if stops and not in_sub and not in_struct and not earlier_errors:
                        # the variables after the one that stopped the statement are not registered
                        # (oracle, CR300/CR350, 2026-10-10, in a file without earlier errors, which is where
                        # the cascade takes over: `Public R As Bad, Z` then `Z = 1`, and
                        # `Public P(-1), Q`, `Public A(Nope), B`, a redeclared name)
                        # (a structure-typed variable stays: `input.value` of `input As s` after
                        # a bad `s2() As s` is not reported, fuzz reproducer e564ca45d2c6)
                        stop = min(stops)
                        for i, v in enumerate(stmt.vars[stop + 1:], stop + 1):
                            if v.type in struct_types:
                                continue
                            if dup_at is None or i < dup_at:
                                declared_kinds.pop(v.name.upper(), None)
                                dim0_sizes.pop(v.name.upper(), None)
                        stmt.vars[stop + 1:] = [v for v in stmt.vars[stop + 1:] if v.type in struct_types]
                else:
                    _check_array_dims(stmt, diags, env, profile)
                    _check_decl_exprs(stmt, known, decl_variables, profile, diags, struct_types)
                _check_init_bounds(stmt, env, diags)
        elif fire_suppress:
            pass
        elif isinstance(stmt, S.Unparsed) and _logical_assign_accepted(stmt, program, profile):
            pass  # compiles (`X AND Y = 1`); only its string operands are reported, in pass 2
        elif isinstance(stmt, S.Unparsed):
            _check_unparsed_pass1(stmt, diags)
            for message in _junk_init_findings(stmt, profile):
                if message == "Size of string missing." and profile.single_pass and any(
                        d.message == message for d in diags.items):
                    continue  # CR300/CR350 report the first one only (oracle, 2026-10-10)
                diags.error(stmt.line, message, stmt.file)
            if profile.known_first_word_pass1 and _is_generic_unparsed(stmt):
                if known is None:
                    known = expressions._known_names(program, profile)
                    operands = pass1_operands(program, profile)
                findings = _instruction_arg_findings(stmt.text, operands, profile,
                                                     symbols.declared_variable_names(program))
                for stage, kind, arg_text in findings:
                    if stage == 1:
                        diags.error(stmt.line, _pass1_message(profile, S.Unparsed(
                            stmt.line, stmt.file, text=arg_text), kind), stmt.file)
                    elif stage == 3 or stage == 5 and findings[0][0] == 4:
                        diags.error(stmt.line, kind, stmt.file)
                    elif stage == 4:
                        diags.error(stmt.line, f"Invalid symbol in expression: {arg_text}.", stmt.file)
                # a name declared further down is not yet known here: that statement is pass 2
                # (oracle, CR6/CR1000X, 2026-10-08: `If TCTemp is <26` above `Public TCTemp`)
                earlier = pass1_operands(dataclasses.replace(program, statements=program.statements[:pos]),
                                         profile)
                verdict = (None if findings else _pass1_scan(stmt.text, earlier, profile))
                later = symbols.declared_variable_names(program) - symbols.declared_variable_names(
                    dataclasses.replace(program, statements=program.statements[:pos]))
                if verdict is None and stmt.reason == _UNCLOSED_PAREN_EXPR and \
                        _unclosed_paren_kind(stmt.text, known - later) == "pass1":
                    verdict = ("pass1", "fallback" if stmt.text.rstrip().endswith("(")
                               else "no_matching")
                if verdict and verdict[0] == "pass1":
                    for message in _unparsed_string_messages(stmt, program, profile):
                        diags.error(stmt.line, message, stmt.file)
                    diags.error(stmt.line, _pass1_message(profile, stmt, verdict[1], earlier),
                                stmt.file)
                elif verdict == ("pass2", "symbol") and re.match(r"\s*SELECT\b", stmt.text, re.I):
                    cond_depth += 1  # an unresolved `Select Case` still opens its block
                    select_depth += 1
        elif (isinstance(stmt, S.Assignment) and isinstance(stmt.target, S.Member)
              and isinstance(stmt.target.base, S.Name) and stmt.target.base.name.upper() == "PUBLIC"
              and stmt.target.member.upper() not in symbols.declared_variable_names(program)):
            # `Public.Nope = 1`: not a variable (oracle, all four loggers, 2026-10-12)
            diags.error(stmt.line, f"Could not find Public.{stmt.target.member}(1).", stmt.file)
            diags.error(stmt.line, _pass1_message(profile, stmt, "invalid_assignment"), stmt.file)
        elif (isinstance(stmt, S.Assignment) and isinstance(stmt.target, S.Member)
              and isinstance(stmt.target.base, S.Name) and stmt.target.base.name.upper() == "DIM"):
            # `Dim.X = 1`: `Dim` is no table (oracle, all four loggers, 2026-10-12; review R14C-5)
            base = stmt.target.base.name
            diags.error(stmt.line, f"Could not find DataTable {base}." if profile.single_pass
                        else f"Could not find {base}.", stmt.file)
            diags.error(stmt.line, _pass1_message(profile, stmt, "invalid_assignment"), stmt.file)
        elif (isinstance(stmt, S.Assignment) and (profile.known_first_word_pass1 or profile.single_pass)
              and isinstance(stmt.target, S.Name)
              and stmt.target.name.upper() not in profile.instructions
              and stmt.target.name.upper() not in symbols.declared_variable_names(program)
              and (stmt.target.name.upper() in set(profile.symbols) | expressions._EXTRA_PREDEFINED_SYMBOLS
                   or _assigns_user_const(stmt, program))):
            # `Sec = 3`, `String = "x"`: a predefined symbol cannot be assigned (oracle, CR6/CR1000X,
            # 2026-10-10)
            diags.error(stmt.line, _pass1_message(profile, stmt, "invalid_assignment"), stmt.file)
        elif (isinstance(stmt, S.Call) and profile.known_first_word_pass1 and not stmt.explicit
              and stmt.name.upper() not in profile.instructions
              and stmt.name.upper() not in profile.functions):
            if known is None:
                known = expressions._known_names(program, profile)
                operands = pass1_operands(program, profile)
            if stmt.name.upper() in operands and stmt.name.upper() not in symbols.declared_subs(program):
                diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file)
        elif kw == "ALIAS" and isinstance(stmt, S.Instruction):
            if known is None:
                known = expressions._known_names(program, profile)
                operands = pass1_operands(program, profile)
            if len(stmt.args) > 1 and isinstance(stmt.args[1], S.Number):
                # `Alias CD16(29) = 3`, pass 1, all four loggers (oracle, 2026-09-27)
                diags.error(stmt.line, f"Variable name {stmt.args[1].text} illegal, starts with a "
                            "number.", stmt.file)
            elif not _check_alias_index_names(stmt, known, decl_variables, profile, diags):
                src = stmt.args[0] if stmt.args else None
                sizes = dim0_sizes
                if isinstance(src, (S.Name, S.Index)) and first_decl.get(src.name.upper(), -1) > pos:
                    # declared only later in the file: not yet declared (round 16, oracle)
                    sizes = {k: v for k, v in dim0_sizes.items() if k != src.name.upper()}
                _check_alias_dims(stmt, diags, env, sizes, all_dims, seen_alias_names,
                                   seen_alias_sources, declared_kinds.get(f"#{id(stmt)}"), profile)
        if isinstance(stmt, S.If) and (hashed := S.hash_statement(stmt)) is not None:
            diags.error(hashed.line, _junk_init_findings(hashed, profile)[0], hashed.file)
        if profile.known_first_word_pass1 and isinstance(stmt, (S.If, S.ElseIf)):
            if known is None:
                known = expressions._known_names(program, profile)
                operands = pass1_operands(program, profile)
            _check_if_pass1(stmt, operands, profile, diags, program)
        elif profile.single_pass and isinstance(stmt, S.If):  # CR300/CR350: only the called keyword
            for sub in (stmt.inline or []) + (stmt.inline_else or []):
                if _block_keyword_call(sub):
                    diags.error(sub.line, f"Incomplete expression: {sub.text}.", sub.file)
                elif (isinstance(sub, S.Assignment) and isinstance(sub.target, S.Name)
                      and sub.target.name.upper() in set(profile.symbols) | expressions._EXTRA_PREDEFINED_SYMBOLS
                      and sub.target.name.upper() not in profile.instructions
                      and sub.target.name.upper() not in symbols.declared_variable_names(program)):
                    diags.error(sub.line, _pass1_message(profile, dataclasses.replace(sub, at_eol=False),
                                                         "invalid_assignment"), sub.file)

        # `S.Call` (a bare/implicit or explicit `Call`) and `S.Unparsed`'s generic "not a valid
        # statement" fallback are **not** handled here at all any more (round 4, this session):
        # both are pass-2 ("Expr"/implicit-`Call` records, `docs/checker.md` "Pass model") --
        # `check_pass2` below.

        if isinstance(stmt, S.If) and stmt.inline is not None and select_depth == 0:
            # `If C Then Case 1` outside a `Select` (oracle, all four loggers, 2026-10-12)
            for sub in (stmt.inline or []) + (stmt.inline_else or []):
                if isinstance(sub, S.Case) and not _is_case_else(sub):
                    diags.error(sub.line, "Case outside Select.", sub.file)
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


_DO_ALIASES = {"WHILE": "DO", "WEND": "LOOP"}


def _expr_resolves(e: S.Expr | None, declared: set[str], functions) -> bool:
    """An expression whose names are all declared (variables, Consts) or built-in function calls;
    an array reference (`A(1)`) is not probed and does not count."""
    if e is None or isinstance(e, (S.Number, S.String)):
        return True
    if isinstance(e, S.Name):
        return e.name.upper() in declared
    if isinstance(e, S.Unary):
        return _expr_resolves(e.operand, declared, functions)
    if isinstance(e, S.Binary):
        return _expr_resolves(e.left, declared, functions) and _expr_resolves(e.right, declared, functions)
    if isinstance(e, S.Index):
        return e.name.upper() in functions and e.name.upper() not in declared and all(
            _expr_resolves(a, declared, functions) for a in e.args)
    return False


def _slowsequence_resolves(stmt: S.Stmt, declared: set[str], functions=()) -> bool:
    """CR300/CR350 resolve a `SlowSequence` argument (`SlowSequence(Q)`, `Q is not defined.`) where
    CR6/CR1000X never read it, so there it must resolve without error: a literal, or an expression
    over declared names and built-in functions (oracle, CR300/CR350, 2026-10-16: `(1+1)`, `(A+1)`,
    `(K*2)`, `(Abs(-1))`, `(-A)`, `("a"&"b")`, `(Z=1)` open the program; `(Nope+1)`, `(A+Nope)`,
    `(A(1))` with a scalar `A` are errors and do not). Array references are not probed."""
    return not isinstance(stmt, S.Instruction) or all(
        _expr_resolves(a, declared, functions) for a in stmt.args if a is not None)


def open_program_at_slowsequence(program: S.Program, profile: profiles.Profile) -> S.Program:
    """A clean `SlowSequence` before any `BeginProg`/`EndProg` is the start of the program for the
    real compiler (oracle, 2026-10-16, all four loggers, `docs/checker.md`): `SlowSequence` /
    `EndProg` with no `BeginProg` compiles, `A = 1`, `Scan`/`NextScan`, `Sub` between them are
    in-program (`Scan without NextScan.` at `EndProg`, no `... before BeginProg`). Only when the
    statements hold no `BeginProg` (the parser stops at the first `EndProg`, so a later one is never
    seen): a `BeginProg` is inserted ahead of it. Returns `program` itself otherwise."""
    for n, st in enumerate(program.statements):
        kw = _keyword(st)
        if kw in ("BEGINPROG", "ENDPROG"):
            return program
        if isinstance(st, S.Unparsed) and re.match(r"\s*SlowSequence\b", st.text, re.I):
            return program
        if kw == "SLOWSEQUENCE":
            break
    else:
        return program
    if any(_keyword(s) == "BEGINPROG" for s in program.statements):
        return program
    declared = ({x.upper() for x in symbols.declared_names(program)}
                if profile.name in ("cr300", "cr350") else set())
    if profile.name in ("cr300", "cr350") and not _slowsequence_resolves(st, declared, profile.functions):
        return program
    begin = S.Instruction(line=st.line, file=st.file, text="BeginProg", keyword="BEGINPROG")
    return dataclasses.replace(program, statements=[*program.statements[:n], begin, *program.statements[n:]])


def check_end_blocks(program: S.Program, diags: Diagnostics) -> None:
    """End-of-program block balance, run only when passes 1 and 2 found nothing (oracle, CR6 and
    CR300, 2026-09-27: an unclosed `Scan`/`For`/`If` plus an unrelated pass-2 `X = Q` prints only
    `Invalid symbol in expression`). Reported on the last statement's line (`EndProg`) in the
    order `Scan without NextScan.`, `FOR without NEXT.`, `DO without LOOP.`, `IF or SELECT without
    EndIf or EndSelect.` (`docs/checker.md` "Unclosed blocks")."""
    last_line = program.last_line or (program.statements[-1].line if program.statements else 1)
    sub_open = False
    for st in program.statements:
        if isinstance(st, S.SubDef):
            sub_open = True
        elif _ends_sub(st, _keyword(st)):
            sub_open = False
    if not any(_keyword(st) == "BEGINPROG" for st in program.statements):
        # Only when passes 1 and 2 are clean (oracle, CR6: a program with no `BeginProg` and a
        # bad `Y=EXP()` line reports that line's fallback and `Expr illegally occurs before
        # BeginProg.`, never `No BeginProg in Program.`).
        diags.error(last_line, "No BeginProg in Program.")
        if sub_open:
            diags.error(last_line, "Sub ... EndSub not matched.")
        return
    depth = {"SCAN": 0, "FOR": 0, "DO": 0, "IF": 0}
    in_table = False
    table_name: str | None = None
    for stmt in program.statements:
        kw = _DO_ALIASES.get(_keyword(stmt), _keyword(stmt))
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
    if sub_open:
        # a `Sub`/`Function` with no `EndSub`/`EndFunction` (a `BeginProg` inside it is accepted)
        # follows the block messages, all four loggers (oracle, 2026-10-10)
        diags.error(last_line, "Sub ... EndSub not matched.")
        return
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
    pass1_count = len(diags.items)
    sub_params = {st.name.upper(): st for st in program.statements if isinstance(st, S.SubDef)}
    link_ctx = _LinkContext(
        {st.name.upper() for st in program.statements
         if isinstance(st, S.SubDef) and st.keyword.upper() == "SUB"},
        bool(subs), links if links is not None else CallLinks())
    known = expressions._known_names(program, profile)
    dims_ds = expressions.program_dims(program, profile, known)
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
    program_marker_seen = False   # a BeginProg, EndProg or SlowSequence statement has been seen
    declared_here = ({n.upper() for n in symbols.declared_names(program)}
                     if profile.name in ("cr300", "cr350") else set())
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
                diags.error(stmt.line, f"Invalid symbol in expression: {bad}.", stmt.file, show_file=False)
                return stmt.line
        elif kw == "BEGINPROG":
            if in_beginprog:
                diags.error(stmt.line, "Multiple BeginProgs.", stmt.file)
            in_beginprog = True
        elif kw == "ENDPROG":
            in_beginprog = False
        # CR300/CR350 resolve a name argument (`SlowSequence(Q)`, `Q is not defined.`) where CR6/CR1000X
        # never read it, so there the argument must resolve without error: a literal, or a declared
        # name (oracle, CR300/CR350, 2026-10-16: `SlowSequence(Q)`/`(A)`/`(Z)` with declared Publics
        # give `Multiple BeginProgs.`, `SlowSequence(Undef)` does not). Other shapes are not probed
        # and keep the pre-round-18 behaviour (no program opened).
        ss_resolves = _slowsequence_resolves(stmt, declared_here, profile.functions)
        if kw == "SLOWSEQUENCE" and not program_marker_seen and (profile.name in ("cr6", "cr1000x") or ss_resolves) \
                and not any(d.severity == "error" and d.line == stmt.line and d.file == stmt.file
                            for d in diags.items):
            # A clean `SlowSequence` before any `BeginProg`/`EndProg` opens the program: the next
            # `BeginProg` is `Multiple BeginProgs.` (oracle, all four loggers, 2026-10-16:
            # `SlowSequence` / `BeginProg`, `Sub`/`EndSub` / `SlowSequence` / `BeginProg`; a second
            # `SlowSequence` before it changes nothing, `EndProg` clears the state). A `SlowSequence`
            # with its own error (`SlowSequence(Q)` on CR300/CR350, `SlowSequence(A B)`) does not.
            in_beginprog = True
        if kw in ("BEGINPROG", "ENDPROG", "SLOWSEQUENCE") or (
                isinstance(stmt, S.Unparsed) and re.match(r"\s*SlowSequence\b", stmt.text, re.I)):
            # (an erroneous `SlowSequence` is an `Unparsed` statement, and still counts as seen)
            program_marker_seen = True
        if kw == "SLOWSEQUENCE" and pass2_scans and not profile.single_pass:
            # a `Scan` still open at `SlowSequence`: `Missing NextScan.` there, and pass 2 ends
            # (oracle, CR6/CR1000X, 2026-10-12; CR300/CR350: `check_scan_placement_late`)
            diags.error(stmt.line, "Missing NextScan.", stmt.file)
            return stmt.line
        if kw == "SCAN":
            if pass2_scans and not profile.single_pass:
                diags.error(stmt.line, NESTED_SCAN, stmt.file)
            pass2_scans.append(_scan_counts_for_table_placement(stmt, env))
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
            _check_calltable_outside_scan(stmt, program, diags, in_body_block,
                                          bool(pass2_scans) and not in_body_block, env)
        elif kw == "ACCEPTDATARECORDS" and not profile.single_pass and not in_table and \
                _accept_known(stmt, tables):
            _check_accept_table(stmt, program, diags, not any(pass2_scans) or in_body_block, env)
        if kw in _BODY_BLOCK_STARTS:
            in_body_block = True
            continue
        if S.ends_routine(stmt) or kw in _BODY_BLOCK_ENDS:
            in_body_block = False
            continue

        if isinstance(stmt, S.Call):
            stop = _check_call_pass2(stmt, subs, profile, diags, before_bp and not in_table,
                                     in_body_block,
                                     sub_params, known, link_ctx, dims_ds)
            if stop is not None:
                return stop
        elif isinstance(stmt, S.Assignment):
            stop = _check_assignment_pass2(stmt, known, diags, before_bp and not in_table,
                                            in_body_block,
                                            profile, dims_ds)
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
            if before_bp and not in_body_block and not in_table and not in_decl_block and \
                    _before_beginprog_name(stmt, "UNPARSED", profile, set()):
                diags.error(stmt.line, "ESSInitialize illegally occurs before BeginProg.", stmt.file)
                return stmt.line
            if stmt.reason.startswith(_KNOWN_PARSER_GAP_REASONS) or stmt.reason in _BENIGN_REASONS \
                    or is_hash_statement(stmt):
                continue
            if (messages := _logical_assign_undefined_messages(stmt, program, profile)) is not None:
                for message in messages:
                    diags.error(stmt.line, message, stmt.file,
                                show_file=message.endswith(" is not defined."))
                continue
            if _logical_assign_accepted(stmt, program, profile):
                from . import var_types
                for message in var_types._string_operand_messages(
                        var_types._Typer(program), S.Assignment(
                            stmt.line, stmt.file, target=S.Name(stmt.line, name="_"),
                            value=stmt.expr), every=True):
                    diags.error(stmt.line, message, stmt.file)
                continue
            bare = _bare_sub_link_name(stmt.text, link_ctx.subs)
            if bare is not None:
                link_ctx.links.calls.append(bare)
                # `S1 1,2` with `S1` a declared Sub: the link step's `S11,2 is not declared as a
                # Subroutine.` (oracle, 2026-10-02), not the generic fallback.
                stop = _unlinked_call(stmt, bare, profile, diags,
                                      not in_body_block and before_bp and not in_table,
                                      link_ctx)
                if stop is not None:
                    return stop
                continue
            if any((d.line, d.file) == (stmt.line, stmt.file) and d.severity == "error" and
                   d.message.startswith((_FALLBACK_PREFIX, "Illegal Data parameter"))
                   for d in diags.items[:pass1_count]):
                continue  # a missing-parameter leftover or `Data` line, reported in pass 1
            if instr_rules.unterminated_string_shape(stmt, profile, subs,
                                                     pass1_operands(program, profile)):
                continue  # reported in pass 1 (`instr_rules._check_unterminated_string_param`)
            if _junk_init_findings(stmt, profile):
                continue  # reported in pass 1 (`_junk_init_findings`)
            if stmt.reason in (_UNITS_WITHOUT_EQ, _CONST_WITHOUT_EQ, _ALIAS_WITHOUT_EQ, _FOR_MISSING) or \
                    stmt.reason.startswith((_INVALID_AS_CLAUSE, _CONST_BAD_VALUE, _CONST_BAD_NAME, _DO_LOOP_PARAM, _CONST_LEAD,
                                      _ALIAS_NOTHING_AFTER_EQ)) or \
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
                if kind == "pass1" and stmt.text.rstrip().endswith("("):
                    kind = "fallback"  # a text ending at the `(` is generic (oracle, all four loggers, 2026-10-10)
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
                    if profile.single_pass:  # (CR6/CR1000X report them in pass 1)
                        for message in _unparsed_string_messages(stmt, program, profile):
                            diags.error(stmt.line, message, stmt.file)
                    cond = condition_part(stmt.text)
                    operands = pass1_operands(program, profile)
                    diags.error(stmt.line, (cond and cond.strip() and _unclosed_call_message(
                        profile, cond, False, operands))   # (`If Round(X, Then`, `While Round(X`:
                        # the condition alone, as on CR6/CR1000X; oracle 2026-10-12, review R13C-5)
                        or _unclosed_call_message(profile, stmt.text, stmt.at_eol, operands)
                        or _no_matching(profile, stmt.text, stmt.at_eol),
                        stmt.file, show_file=False)
            elif stmt.reason == _EXTRA_CLOSE_PAREN and not _assignment_head(stmt.text, program, profile):
                # `Z)`, `Z + 1)`, `West)`: not an assignment, so the generic fallback (oracle, all
                # four loggers, 2026-10-11, docs/checker.md "Stray close parenthesis")
                diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file)
            elif stmt.reason == _EXTRA_CLOSE_PAREN:
                sep = "\n" if profile.fallback_echo_newline and stmt.at_eol else ""
                diags.error(stmt.line, f"Incomplete expression: {stmt.text}{sep}.", stmt.file,
                            show_file=False)
            else:
                findings = (_instruction_arg_findings(stmt.text, pass1_operands(program, profile),
                                                      profile, symbols.declared_variable_names(program))
                            if profile.known_first_word_pass1 else [])
                if findings:
                    # stage-1, -3 and -4 findings were reported in pass 1 (`check_pass1`)
                    if findings[0][0] in (2, 6):
                        diags.error(stmt.line, f"Invalid symbol in expression: {findings[0][2]}.",
                                    stmt.file, show_file=False)
                        if findings[0][0] == 6 and in_table:
                            return stmt.line  # like `_undefined_table_source`: a source ends pass 2
                        for stage, message, _ in findings[1:]:
                            if stage == 5:
                                diags.error(stmt.line, message, stmt.file)
                        ikw = re.match(r"\s*(\w*)", stmt.text).group(1).upper()
                        record = profile.instructions.get(ikw)
                        if (record and not in_body_block and not in_table and not in_decl_block
                                and before_bp and ikw in profile.before_beginprog):
                            diags.error(stmt.line, f"{record['name']} illegally occurs before "
                                        "BeginProg.", stmt.file)
                            return stmt.line
                    continue
                verdict = (_pass1_scan(stmt.text, pass1_operands(program, profile), profile)
                           if profile.known_first_word_pass1 else None)
                if verdict == ("pass2", "symbol"):
                    diags.error(stmt.line, _pass1_message(profile, stmt, "symbol"), stmt.file,
                                show_file="cr3xx")
                elif verdict and verdict[0] == "pass1" and (verdict[1] != "fallback"
                                                          or condition_part(stmt.text) is not None):
                    diags.error(stmt.line, _pass1_message(profile, stmt, verdict[1]), stmt.file,
                                show_file=False)
                else:
                    if verdict == ("pass2", "fallback") or (
                            verdict is None and profile.known_first_word_pass1
                            and _known_first_token(stmt.text, pass1_operands(program, profile))) or (
                            verdict is None and profile.single_pass
                            and _logical_led_string(stmt.text)):
                        for message in _unparsed_string_messages(stmt, program, profile):
                            diags.error(stmt.line, message, stmt.file)
                    lead = re.match(r"\s*([A-Za-z_]\w*)\.", stmt.text)
                    if lead and profile.known_first_word_pass1 and lead.group(1).upper() not in (
                            symbols.system_table_names(program) | _table_names(program)):
                        # `times.`, `X.Y.Z`: the name before the dot is no table (oracle, 2026-10-10)
                        diags.error(stmt.line, f"Could not find {lead.group(1)}.", stmt.file)
                    diags.error(stmt.line, _leftover_message(profile, stmt), stmt.file,
                                show_file="cr3xx")
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
            if not in_body_block and before_bp and not in_table:
                # not inside a `DataTable` body (oracle, CR6/CR1000X, 2026-10-03: `FuzzVar = 1`
                # there reports only the out-of-place line; a `ConstTable` body still adds this)
                head = re.match(r"\s*(IF|WHILE|DO|SELECT)\b", stmt.text, re.I)
                record = profile.instructions.get(head.group(1).upper()) if head else None
                # an unparsable `If`/`While`/`Do`/`Select` condition names its keyword (oracle,
                # CR6/CR1000X, 2026-10-08: `If Nope Y Then` -> `If illegally occurs ...`)
                display = record["name"] if record and profile.known_first_word_pass1 else "Expr"
                diags.error(stmt.line, f"{display} illegally occurs before BeginProg.", stmt.file)
                return stmt.line
    link_ctx.links.cutoff = None
    return None



_CLOSER_MESSAGES = {
    "IF": ("IF or SELECT statement illegally has SCAN without NEXT SCAN.",
           "END IF without IF or END SELECT without SELECT."),
    "FOR": ("FOR NEXT statement illegally has SCAN without NEXT SCAN.", "NEXT without FOR."),
    "DO": ("LOOP statement illegally has SCAN without NEXT SCAN.", "LOOP without DO."),
}


def _unparsed_opener(text: str) -> str | None:
    if re.match(r"\s*IF\b", text, re.I) and not re.search(r"\bTHEN\s*\S", text, re.I):
        return "IF"
    return "DO" if re.match(r"\s*(?:DO\s+(?:WHILE|UNTIL)|WHILE)\b", text, re.I) else None


def check_block_closers(program: S.Program, profile, diags: Diagnostics) -> None:
    """Pass 2, all four loggers: `NextScan` removes the innermost `Scan` from the stack wherever
    it is; a closing `EndIf`/`EndSelect`/`Next`/`Loop` pops whatever block is innermost; if that was a `Scan`/`SubScan` it reports `<kind> statement illegally has SCAN
    without NEXT SCAN.`, and with no block of its own kind open it reports `END IF without IF or
    END SELECT without SELECT.`/`NEXT without FOR.`/`LOOP without DO.`; an `EndSelect` with no
    `Select` open reports only `EndSelect w/o Select` (`docs/checker.md` "Unmatched block ends").
    `While`/`Wend` are `Do`/`Loop` here and in `check_end_blocks` (oracle, all four loggers,
    2026-10-12: `Do` ... `Wend` and `While` ... `Loop` compile, `While X` alone is `DO without LOOP.`,
    a stray `Wend` is `LOOP without DO.`).

    The block stack is an array with a depth: only a `Scan`/`SubScan` writes its slot, so one that
    a stray closer popped stays in its slot (`stale`) and every later `If`/`For`/`Do`/`Select`
    opened at that depth reports `<kind> statement illegally has SCAN without NEXT SCAN.` at its
    own closer (a one-line `If` at its own line); a further stray closer at depth 0 reports only
    its `without` message (oracle, all four loggers, 2026-10-03)."""
    frames: list[str] = []
    depth = 0
    open_count = {"IF": 0, "FOR": 0, "DO": 0, "SCAN": 0}
    selects = 0
    subscans = 0

    def push(kind: str) -> None:
        nonlocal depth
        if depth == len(frames):
            frames.append(kind)
        elif kind == "SCAN":
            frames[depth] = kind
        depth += 1

    for stmt in program.statements:
        kw = _DO_ALIASES.get(_keyword(stmt), _keyword(stmt))
        args = getattr(stmt, "args", []) or []
        word = _block_word(args) if kw in ("END", "NEXT") else None
        if isinstance(stmt, S.If) and stmt.inline is not None:
            if depth < len(frames) and frames[depth] == "SCAN" or any(
                    _keyword(b) == "SCAN" for b in (stmt.inline or []) + (stmt.inline_else or [])):
                # (a `Scan` as a one-line `If` body is a Scan block inside the `If`, oracle all
                # four loggers 2026-10-12; a Scan with a wrong argument count is a pass-1 error)
                diags.error(stmt.line, _CLOSER_MESSAGES["IF"][0], stmt.file)
        elif kw == "IF" or kw == "SELECT":
            push("IF")
            open_count["IF"] += 1
            selects += kw == "SELECT"
        elif kw in ("FOR", "DO"):
            push(kw)
            open_count[kw] += 1
        elif isinstance(stmt, S.Unparsed) and (opener := _unparsed_opener(stmt.text)):
            # an `If`/`Do` whose condition fails to parse still opens its block (oracle,
            # CR6/CR1000X, 2026-10-08: `If Nope X Then` ... `EndIf` reports only the symbol)
            push(opener)
            open_count[opener] += 1
        elif kw in ("SCAN", "SUBSCAN"):
            push("SCAN")
            subscans += kw == "SUBSCAN"
        elif kw == "NEXTSUBSCAN" and subscans == 0:
            diags.error(stmt.line, "Next SubScan without SubScan.", stmt.file)
        elif kw in ("NEXTSCAN", "NEXTSUBSCAN") or (kw == "NEXT" and word in ("SCAN", "SUBSCAN")):
            subscans -= kw == "NEXTSUBSCAN" or word == "SUBSCAN"
            if kw == "NEXTSCAN" or word == "SCAN":
                if subscans > 0:   # oracle, all four loggers, 2026-10-08 (docs/fuzz-findings.md)
                    diags.error(stmt.line, "SubScan without Next SubScan.", stmt.file)
                    subscans = 0
            # Removes the innermost `Scan` wherever it is; blocks opened inside it stay open
            # (oracle: `If X Then` / `NextScan` / `EndIf` compiles, a fuzz-found false reject).
            live = frames[:depth]
            if "SCAN" in live:
                del frames[len(live) - 1 - live[::-1].index("SCAN")]
                depth -= 1
        elif kw in ("ENDIF", "ENDSELECT", "NEXT", "LOOP") or (kw == "END" and word in ("IF", "SELECT")):
            is_select = kw == "ENDSELECT" or (kw == "END" and word == "SELECT")
            if is_select and selects == 0:
                return  # `EndSelect w/o Select` is a pass-1 error (`check_pass1`)
            kind = {"NEXT": "FOR", "LOOP": "DO"}.get(kw, "IF")
            illegal_scan, without = _CLOSER_MESSAGES[kind]
            if depth > 0:
                depth -= 1
                if frames[depth] == "SCAN":
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



def is_hash_statement(stmt: S.Unparsed) -> bool:
    """A statement starting with `#` that no preprocessor directive claimed: `Unknown instruction`."""
    return stmt.text.lstrip().startswith("#")


def _is_generic_unparsed(stmt: S.Unparsed) -> bool:
    """An `Unparsed` statement no specific pass-1 diagnosis claims (`_check_unparsed_pass1`)."""
    return not (is_hash_statement(stmt) or stmt.reason.startswith(_KNOWN_PARSER_GAP_REASONS) or stmt.reason in _BENIGN_REASONS
                or stmt.reason in (_UNITS_WITHOUT_EQ, _UNITS_TARGET_NO_EQ, _CONST_WITHOUT_EQ,
                                   _ALIAS_WITHOUT_EQ, _ALIAS_TARGET_NO_EQ, _FOR_MISSING)
                or stmt.reason.startswith((_INVALID_AS_CLAUSE, _CONST_BAD_VALUE, _CONST_BAD_NAME, _DO_LOOP_PARAM, _CONST_LEAD,
                                      _ALIAS_NOTHING_AFTER_EQ))
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


_COND_HEAD = re.compile(r"\s*(?:IF|ELSEIF|WHILE|DO\s+WHILE|DO\s+UNTIL|LOOP\s+WHILE|LOOP\s+UNTIL|SELECT\s+CASE|CASE)\b(.*)$",
                        re.I | re.S)
_COND_THEN = re.compile(r"(.*?)\bTHEN\s*$", re.I | re.S)


_INLINE_IF = re.compile(r"\s*(?:IF|ELSEIF)\b(.*?)\bTHEN\b\s*\S", re.I | re.S)


def inline_if_condition(text: str) -> str | None:
    """The condition of a one-line `If c Then stmt` (what `condition_part` leaves out: text follows
    the `Then`), left-trimmed; `None` for any other statement."""
    m = _INLINE_IF.match(text)
    return m.group(1).lstrip() if m else None


def condition_part(text: str) -> str | None:
    """The condition text of an `If`/`ElseIf`/`While`/`Do While|Until`/`Loop While|Until` statement,
    without its keyword and a closing `Then` (what the pass-1 expression messages echo, CR6/CR1000X
    oracle 2026-10-08: `If X =` -> `Invalid, or out of place expression: X =.`, `If X Y Then` ->
    `Incomplete expression: X Y .`). `None`: not such a statement, or text follows the `Then`."""
    m = _COND_HEAD.match(text)
    if not m:
        return None
    cond = m.group(1)
    if not re.match(r"\s*SELECT", text, re.I):  # `Select Case` echoes the raw remainder
        cond = cond.lstrip()
    if re.match(r"\s*CASE\b", text, re.I) and re.search(r"\b(?:TO|IS)\b", cond, re.I):
        return None
    if re.match(r"\s*(?:IF|ELSEIF)\b", text, re.I):
        t = _COND_THEN.match(cond)
        if t:
            return t.group(1)
        if re.search(r"\bTHEN\b", cond, re.I):
            return None
    return cond


_LEADING_OPS = frozenset({"=", "*", "/", "<", "<>", ">", ">=", "<=", "^", "\\", ",", ")", ".", ";"})


def _invalid_assignment(toks) -> bool:
    """`6 = X`, `"a" = X`, `-1 = X`, `X + 1 = 2`: the first top-level `=` follows something that is
    not a variable (a literal, or an expression with a binary operator) -> pass-1 `Invalid
    assignment in expression: <stmt>.` (oracle, CR6/CR1000X, 2026-10-10; `(X) = 1` is not this)."""
    depth, lhs = 0, []
    for t in toks:
        if t.kind == "OP" and t.text in ("(", "{"):
            depth += 1
        elif t.kind == "OP" and t.text in (")", "}"):
            depth -= 1
        elif t.kind == "OP" and t.text == "=" and depth == 0:
            break
        lhs.append(t)
    else:
        return False
    if not lhs or lhs[0].kind == "OP" and lhs[0].text != "-":
        return False
    if len(lhs) == 1 or (lhs[0].text == "-" and len(lhs) == 2):
        return lhs[-1].kind in ("NUMBER", "STRING")
    # a comparison on the left, or junk (adjacent operands, `x > 2 Then y = 1`), is the generic
    # fallback (oracle, CR6/CR1000X, 2026-10-12)
    depth = 0
    for t in lhs:
        if t.kind == "OP" and t.text in ("(", ")"):
            depth += 1 if t.text == "(" else -1
        elif depth == 0 and t.kind == "OP" and t.text in ("<", ">", "<>", "<=", ">="):
            return False
    if _scan_expr(lhs, 0, _Everything(), {}, assign=True, start_operand=False) is not None:
        return False
    depth = 0
    for t in lhs:
        if t.kind == "OP" and t.text == "(":
            depth += 1
        elif t.kind == "OP" and t.text == ")":
            depth -= 1
        elif depth == 0 and t.kind == "OP" and t.text in _BINARY_OPS and t is not lhs[0]:
            return True
    return False


def _semicolons_dropped(text: str, profile) -> bool:
    """Whether the parser read this statement with its `;` tokens skipped (`parser._drop_semicolons`)."""
    if ";" not in text:
        return False
    from .. import parser
    try:
        toks = lexer.tokenize(text, 0)
    except lexer.LexError:
        return False
    return len(parser._drop_semicolons(toks, profile)) < len(toks)


def _pass1_scan(text: str, operands: set[str], profile) -> tuple[str, str] | None:
    """Replays the CR6/CR1000X pass-1 left-to-right statement scan (`docs/checker.md` "Pass-1
    fallback: statements that start with a known word") over an unparsable statement.

    Returns `("pass1", kind)` when a syntax error comes before any unknown name, `("pass2",
    kind)` when an unknown name comes first (the record is left for pass 2), or `None` for a shape
    not oracle-probed. `kind` names the message: `fallback`, `incomplete`, `no_matching`,
    `symbol`."""
    cond = condition_part(text)
    drop = _semicolons_dropped(text, profile)
    if cond is not None:
        try:
            ctoks = [t for t in lexer.tokenize(cond, 0) if t.kind != "EOF"]
        except lexer.LexError:
            return None
        if drop:
            ctoks = [t for t in ctoks if not (t.kind == "OP" and t.text == ";")]
        if not ctoks:
            # a bare keyword has no expression at all (oracle, CR6/CR1000X, 2026-10-08); `If Then`,
            # `If 'c` and `Select Case` echo the empty condition
            if text == text.rstrip() and not re.search(r"\bTHEN\b|^\s*SELECT", text, re.I):
                return ("pass1", "missing_expr")
            return ("pass1", "fallback")
        return _scan_expr(ctoks, 0, operands, profile.functions, assign=True, start_operand=False,
                          cond=True, to_ok=bool(re.match(r"\s*(?:SELECT|IF|ELSEIF)\b", text, re.I)),
                          open_fallback=True)
    try:
        toks = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
        if drop:
            toks = [t for t in toks if not (t.kind == "OP" and t.text == ";")]
    except lexer.LexError as e:
        # an unterminated string: the left-to-right scan has already failed on what precedes it
        # (`X here " + 1`, `string here "`; oracle, CR6/CR1000X, 2026-10-10)
        if e.message.startswith("unrecognised character") and not text[:e.col].strip():
            # a line that starts with `%`, `[` or `]` (the CHM's `%3.2f %c", ...` wrap line)
            return ("pass1", "fallback") if text[e.col:e.col + 1] in "%[]" else None
        if e.message != "unterminated string" or text[e.col:e.col + 1] != '"':
            return None
        head = [t for t in lexer.tokenize(text[:e.col], 0) if t.kind != "EOF"]
        if head and head[0].kind == "OP" and head[0].text in _LEADING_OPS - {";"}:
            return ("pass1", "fallback")  # `,x", x)`, `)"x`: the leading operator fails first
        if len(head) < 2 or head[0].kind != "IDENT" or head[1].kind != "IDENT" \
                or head[0].upper not in operands or head[1].upper in _BINARY_WORDS \
                or head[0].upper in profile.functions or head[0].upper in profile.instructions \
                or head[0].upper in ("IF", "ELSEIF", "WHILE", "UNTIL", "SELECT", "CASE", "FOR"):
            return None
        return ("pass1", "fallback")
    if not toks:
        return None
    functions = profile.functions
    if _logical_bad_target(toks, operands, functions):
        return ("pass1", "invalid_assignment")
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
                                  unclosed="no_matching", symbol_unknown=True)
            if _bare_function_needs_paren(u, functions):
                return ("pass1", "expects_paren")
            return None
        if u not in operands:
            return None
        if i < len(toks) and toks[i].kind == "OP" and toks[i].text == "(":
            # `A(2) = Y +`: scanned like a plain operand once its clean subscript is skipped
            depth = 0
            for j in range(i, len(toks)):
                depth += (toks[j].text == "(") - (toks[j].text == ")") if toks[j].kind == "OP" else 0
                if depth == 0:
                    break
            if depth or _scan_expr(toks[i + 1:j], 0, operands, functions, assign=True,
                                   start_operand=False) is not None or _later_arg_unknown(
                    toks[i + 1:j + 1], operands, functions):
                if u in profile.symbols and not depth:
                    return ("pass1", "fallback")  # `C1 (COM1 TX) to C4`, a predefined symbol
                return None
            i = j + 1
    elif first.kind in ("NUMBER", "STRING"):
        if _invalid_assignment(toks):
            return ("pass1", "invalid_assignment")
    elif first.kind == "OP" and first.text == "-" and len(toks) > 1 and toks[1].kind == "NUMBER":
        if _invalid_assignment(toks):
            return ("pass1", "invalid_assignment")
        i = 2
    elif first.kind == "OP" and first.text in ("{", "}"):
        return ("pass1", "fallback")  # a stray brace (oracle, CR6/CR1000X, 2026-10-10)
    elif first.kind == "OP" and first.text == "(":
        verdict = _scan_expr(toks, 0, operands, functions, assign=False, start_operand=False)
        # `(Double = D) x`: an assignment inside the group (oracle, CR6/CR1000X, 2026-10-10) --
        # a constant or predefined symbol on the left is `Invalid assignment in expression`, a
        # variable keeps the `Incomplete expression` of the words after the group
        eq = next((j for j, t in enumerate(toks) if t.kind == "OP" and t.text in ("=", "==")), None)
        if eq is not None and all(not (t.kind == "OP" and t.text in ("(", ")")) for t in toks[1:eq]):
            lhs = toks[1:eq]
            if (len(lhs) == 1 and (lhs[0].kind in ("NUMBER", "STRING")
                                   or (lhs[0].kind == "IDENT" and lhs[0].upper not in profile.instructions
                                       and lhs[0].upper in set(profile.symbols) | expressions._EXTRA_PREDEFINED_SYMBOLS))
                    or (len(lhs) == 2 and lhs[0].text == "-" and lhs[1].kind == "NUMBER")):
                return ("pass1", "invalid_assignment")
            if lhs and lhs[0].kind == "IDENT" and lhs[0].upper in operands \
                    and lhs[0].upper not in set(profile.symbols) | expressions._EXTRA_PREDEFINED_SYMBOLS:
                return verdict
        depth_, close = 0, len(toks)
        for j, t in enumerate(toks):
            if t.kind == "OP" and t.text in ("(", ")"):
                depth_ += 1 if t.text == "(" else -1
                if depth_ == 0:
                    close = j
                    break
        if eq is not None and eq < close and verdict == ("pass1", "incomplete") and any(
                (t.kind == "OP" and t.text in ("<", ">", "<>", "<=", ">=")) or
                (t.kind == "IDENT" and t.upper in _BINARY_WORDS) for t in toks[1:eq]):
            return verdict  # `(Sin (x) < 0 AND y = 1) Then`: a logical group with an `=` keeps it
        return ("pass1", "fallback") if verdict == ("pass1", "incomplete") else verdict
    elif first.kind == "OP" and len(toks) > 1 and first.text in _LEADING_OPS:
        # a statement starting with a binary operator (`+ 1`, `= -1`, `< 3`, `, x`) is a pass-1
        # error whatever follows (oracle, CR6/CR1000X, 2026-10-08)
        return ("pass1", "fallback")
    elif first.kind == "OP" and len(toks) > 1 and first.text in ("+", "-"):
        return _scan_expr(toks, 1, operands, functions, assign=False, start_operand=False)
    else:
        return None
    if i == len(toks):
        return ("pass1", "fallback")
    nxt = toks[i]
    if nxt.kind == "OP" and nxt.text == "=" and first.kind == "IDENT":
        # (a plain target's value is read like a condition: a stray top-level comma is
        # `Incomplete expression`; an indexed target's is not probed that way)
        return _scan_expr(toks, i + 1, operands, functions, assign=True, start_operand=False,
                          cond=i == 1)
    if nxt.kind in ("IDENT", "NUMBER", "STRING") and not (nxt.kind == "IDENT"
                                                         and nxt.upper in _BINARY_WORDS):
        return ("pass1", "fallback")
    if nxt.kind == "OP" and nxt.text in (",", ")", "}"):
        return ("pass1", "fallback")
    if (nxt.kind == "OP" and nxt.text in _BINARY_OPS) or nxt.kind == "IDENT":
        if first.kind == "IDENT" and nxt.kind == "OP" and _invalid_assignment(toks):
            return ("pass1", "invalid_assignment")
        verdict = _scan_expr(toks, i + 1, operands, functions, assign=False, start_operand=False)
        # a statement that starts with an operand has only the fallback: `X + 1 foo`, `X + 1 )`,
        # `X and (X foo)` (oracle, all four loggers, 2026-10-10)
        if verdict is not None and verdict[0] == "pass1" and verdict[1] in (
                "incomplete", "no_matching", "fallback"):
            # ... unless a logical word leads into an assignment (`X AND X = 1 foo`): then the
            # tail after its first `=` is read as an assignment's (oracle, 2026-10-12)
            eq = _logical_assign_eq(toks, i) if nxt.kind == "IDENT" else None
            if eq is not None:
                tail = _scan_expr(toks, eq + 1, operands, functions, assign=True,
                                  start_operand=False)
                if tail in (("pass1", "incomplete"), ("pass1", "no_matching")):
                    return tail
            return ("pass1", "fallback")
        return verdict
    return None


def _assigns_user_const(stmt: S.Assignment, program: S.Program) -> bool:
    """`K = 1` with `K` a program `Const` (and no variable or enclosing-`Sub` parameter of that
    name): `Invalid assignment in expression` (oracle, all four loggers, 2026-10-12)."""
    name = stmt.target.name.upper()
    if not any(isinstance(st, S.ConstDecl) and st.name.upper() == name for st in program.statements):
        return False
    sub = _enclosing_sub(stmt, program)
    return sub is None or name not in {p.name.upper() for p in sub.params}


_LOGICAL_WORDS = ("AND", "OR", "XOR", "IMP", "EQV")


def _logical_bad_target(toks, operands, functions) -> bool:
    """`X AND Y + 1 = 1`, `X AND 1 = 1`, `X AND -Y = 1`, `X AND Not Y = 1`, `X AND Abs(Y) = 1`,
    `X AND (Y + 1) = 1`: the first top-level `=` follows a logical word and what lies between them
    is not a variable, so pass 1 says `Invalid assignment in expression` (oracle, all four loggers,
    2026-10-12; only when every name in the statement is defined)."""
    depth, last_logical, eq = 0, None, None
    for k, t in enumerate(toks):
        if t.kind == "OP":
            if t.text in ("(", "{"):
                depth += 1
            elif t.text in (")", "}"):
                depth -= 1
            elif t.text == "=" and depth == 0:
                eq = k
                break
            elif t.text == ".":
                return False
        elif t.kind == "IDENT" and depth == 0 and t.upper in _LOGICAL_WORDS:
            last_logical = k
    if eq is None or last_logical is None or last_logical + 1 >= eq:
        return False
    if any(t.kind == "STRING" for t in toks):
        return False
    for t in toks:
        if t.kind == "IDENT" and t.upper not in _BINARY_WORDS and t.upper != "NOT" \
                and t.upper not in operands and t.upper not in functions:
            return False
    seg = toks[last_logical + 1:eq]
    if any(t.kind == "OP" and t.text in ("<", ">", "<>", "<=", ">=") for t in seg):
        return False  # a comparison between the word and the `=` is not probed
    while len(seg) > 2 and seg[0].text == "(" and seg[-1].text == ")":
        d, closes_at_end = 0, True
        for j, t in enumerate(seg):
            d += (t.text == "(") - (t.text == ")") if t.kind == "OP" else 0
            if d == 0 and j < len(seg) - 1:
                closes_at_end = False
                break
        if not closes_at_end:
            break
        seg = seg[1:-1]
    if not seg:
        return False
    first = seg[0]
    if first.kind == "NUMBER":
        return True
    if first.kind == "OP" and first.text in ("-", "+"):
        return True
    if first.kind != "IDENT":
        return False
    if first.upper == "NOT":
        return True
    if first.upper in functions and first.upper not in operands:
        return len(seg) > 1 and seg[1].text == "("
    d = 0
    for j, t in enumerate(seg):
        if t.kind == "OP" and t.text == "(":
            d += 1
        elif t.kind == "OP" and t.text == ")":
            d -= 1
        elif d == 0 and j and (t.kind == "OP" and t.text in _BINARY_OPS
                               or t.kind == "IDENT" and t.upper in _BINARY_WORDS):
            return True
    return False


def _logical_assign_eq(toks, i: int) -> int | None:
    """The index of the first top-level `=` after the logical word at `toks[i]` when a name or a
    closing `)` precedes it (the `parser.LOGICAL_ASSIGN` shape), else None."""
    if toks[i].upper not in ("AND", "OR", "XOR", "IMP", "EQV"):
        return None
    depth = 0
    for k in range(i + 1, len(toks)):
        t = toks[k]
        if t.kind != "OP":
            continue
        if t.text == "(":
            depth += 1
        elif t.text == ")":
            depth -= 1
        elif t.text == "=" and depth == 0:
            prev = toks[k - 1]
            return k if k > i + 1 and (prev.kind == "IDENT" or prev.text == ")") else None
    return None


def _known_first_token(text: str, operands: set[str]) -> bool:
    """The statement starts with a name pass 1 knows or a literal (not an unknown name)."""
    try:
        toks = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return False
    return bool(toks) and (toks[0].kind in ("NUMBER", "STRING")
                           or (toks[0].kind == "IDENT" and toks[0].upper in operands))


def _logical_led_string(text: str) -> bool:
    """CR300/CR350: `X AND S = "a" AND X =` gets the string line before the fallback, as on
    CR6/CR1000X (oracle, 2026-10-11, `logical_assign.json`). Only a name, `AND`, and a string
    literal is probed: `X AND S =` (no literal) and `X = 1 AND S = "a" AND X =` (assignment) get
    no string line on CR300/CR350."""
    return re.match(r"\s*[A-Za-z_]\w*\s+AND\b", text, re.I) is not None and '"' in text


def _enclosing_sub(stmt: S.Stmt, program: S.Program) -> S.SubDef | None:
    """The `Sub`/`Function` whose body holds `stmt`, if any."""
    current = None
    for st in program.statements:
        if st is stmt:
            return current
        if isinstance(st, S.SubDef):
            current = st
        elif isinstance(st, S.Instruction) and _ends_sub(st, st.keyword.upper()):
            current = None
    return None


def _logical_assign_state(stmt: S.Unparsed, program: S.Program, profile
                          ) -> tuple[str, list[str], bool]:
    """(`"ok"`, `"undefined"` or `"rejected"`, the undefined names in source order, whether an
    undefined name is the base of an indexed target) for a `parser.LOGICAL_ASSIGN` statement:
    `ok` when every name resolves and each nested assignment target is a declared variable, an
    element of a declared array, a `Status`/data-table member, or the enclosing `Function`'s own
    name; `undefined` when only unresolved plain names (and undefined target bases) keep it from
    that (oracle, all four loggers, 2026-10-10 and 2026-10-12, `docs/checker.md`
    "Logical-prefixed assignment targets")."""
    if stmt.reason != parser.LOGICAL_ASSIGN:
        return "rejected", [], False
    sub = _enclosing_sub(stmt, program)
    local = {p.name.upper() for p in sub.params} if sub is not None else set()
    known = expressions._known_names(program, profile)
    known.update(local)
    undefined = expressions._undefined_names(stmt.expr, known)
    variables = symbols.declared_variable_names(program) | local
    if sub is not None and sub.keyword.upper() == "FUNCTION":
        variables.add(sub.name.upper())
    dims = {v.name.upper(): len(v.dims) for st in program.statements
            if isinstance(st, S.Declaration) for v in st.vars}
    tables = {st.args[0].name.upper() for st in program.statements
              if isinstance(st, S.Instruction) and st.keyword.upper() == "DATATABLE" and st.args
              and isinstance(st.args[0], S.Name)} | {"STATUS"}
    base_undefined = False
    if profile.single_pass and stmt.text.lstrip().startswith("("):
        return "rejected", [], False  # CR300/CR350 read a leading group as a lone operand
    struct_vars = {v.name.upper() for st in program.statements if isinstance(st, S.Declaration)
                   for v in st.vars if v.type and v.type.upper() not in _VARIABLE_TYPES} | {
        p.name.upper() for st in program.statements if isinstance(st, S.SubDef)
        for p in st.params if p.type and p.type.upper() not in _VARIABLE_TYPES}

    def public_member(m) -> bool:
        """`Public.X` names the declared variable `X` (oracle, CR6 and CR300, 2026-10-12)."""
        return isinstance(m.base, S.Name) and m.base.name.upper() == "PUBLIC" \
            and m.member.upper() in variables

    def members_ok(e) -> bool:
        """Every `Base.member` read has a table (or structure variable) base: `X.a` with a plain
        variable `X` is `Could not find X.` (oracle, 2026-10-12)."""
        if isinstance(e, S.Member):
            return public_member(e) or isinstance(e.base, S.Name) and (e.base.name.upper() in tables
                                                   or e.base.name.upper() in struct_vars
                                                   or e.base.name in undefined)
        if isinstance(e, S.Binary):
            return members_ok(e.left) and members_ok(e.right)
        if isinstance(e, S.Unary):
            return members_ok(e.operand)
        if isinstance(e, S.Index):
            return all(members_ok(a) for a in e.args if a is not None)
        return True

    def target_ok(t) -> bool:
        nonlocal base_undefined
        if isinstance(t, S.Name):
            return t.name.upper() in variables or t.name in undefined
        if isinstance(t, S.Index):
            name = t.name.upper()
            if name not in variables:
                if t.name in undefined:
                    base_undefined = True
                    return True
                return False
            if not profile.single_pass or name in local or name not in dims:
                return True  # CR6/CR1000X: any subscript count, even on a scalar
            return len(t.args) <= dims[name] and (len(t.args) > 0 or dims[name] > 0)
        if isinstance(t, S.Member):
            if public_member(t):
                return True
            return isinstance(t.base, S.Name) and t.base.name.upper() in tables \
                and t.base.name not in undefined
        return False

    def targets_ok(expr) -> bool:
        if not isinstance(expr, S.Binary):
            return True
        if expr.op == "ASSIGN" and not target_ok(expr.left):
            return False
        return targets_ok(expr.left) and targets_ok(expr.right)

    if not targets_ok(stmt.expr) or not members_ok(stmt.expr):
        return "rejected", [], False
    if not undefined:
        return "ok", [], False
    if any(isinstance(n, DottedName) for n in undefined):
        return "rejected", [], False
    return "undefined", list(undefined), base_undefined


def _logical_assign_accepted(stmt: S.Unparsed, program: S.Program, profile) -> bool:
    """`X AND Y = 1` compiles (`_logical_assign_state`)."""
    return _logical_assign_state(stmt, program, profile)[0] == "ok"


def _logical_assign_undefined_messages(stmt: S.Unparsed, program: S.Program, profile
                                       ) -> list[str] | None:
    """The messages for a logical-prefixed assignment whose targets are fine but whose names are
    not all defined, or None when the generic fallback applies. CR300/CR350: one `X is not
    defined.` per name (an undefined indexed-target base, `X AND Y(1) = 1`, keeps the generic
    path, which also prints the fallback). CR6/CR1000X: the fallback when a name is undefined before the first `=` (target,
    left operand, target subscripts), else `Invalid symbol in expression: <statement>.` (oracle,
    all four loggers, 2026-10-12)."""
    state, undefined, base_undefined = _logical_assign_state(stmt, program, profile)
    if state != "undefined" or base_undefined or '"' in stmt.text:
        return None
    if profile.single_pass:
        return [profile.unresolved_symbol_message.format(name=n) for n in undefined]
    try:
        toks = [t for t in lexer.tokenize(stmt.text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return None
    head = []
    for t in toks:
        if t.kind == "OP" and t.text == "=":
            break
        head.append(t)
    names = {n.upper() for n in undefined}
    if any(t.kind == "IDENT" and t.upper in names for t in head):
        return None
    return [f"Invalid symbol in expression: {stmt.text}."]


def _unparsed_string_messages(stmt: S.Unparsed, program: S.Program, profile) -> list[str]:
    """What the real compiler type-checks before it meets the junk in an unparsable statement:
    the String-operand errors of the expression it starts with (oracle, CR6/CR1000X, 2026-10-10:
    `"a" and "b" .` -> `String is illegal here`, then the fallback)."""
    if condition_part(stmt.text) is not None:
        return []
    expr = parser.partial_expression(stmt.text, profile)
    if expr is None:
        return []
    from . import var_types
    return var_types._string_operand_messages(
        var_types._Typer(program),
        S.Assignment(stmt.line, stmt.file, target=S.Name(stmt.line, name="_"), value=expr),
        every=True)


def string_reached(text: str, operands: set[str], profile) -> str | None:
    """True when the pass-1 scan of a statement cut short by an unterminated string reaches the
    string with no syntax error before it, so the lexer's `String without end quote` is the first
    message (oracle, all four loggers, 2026-10-10: `X + "abc`, `Len("abc`, `S = Left(S, "abc`,
    `X(1) "abc`, `1 "abc`; not `X here "abc`, `Len(S, "abc` whose call already has too many
    arguments, nor an unknown first name on CR6/CR1000X)."""
    if profile.single_pass:
        return None  # CR300/CR350 report undefined names first and read other shapes
    try:
        lexer.tokenize(text, 0)
        return None
    except lexer.LexError as e:
        if e.message != "unterminated string" or text[e.col:e.col + 1] != '"':
            return None
        head_text = text[:e.col]
    head = [t for t in lexer.tokenize(head_text, 0) if t.kind != "EOF"]
    if not head or (head[0].kind == "IDENT" and (
            head[0].upper in profile.instructions or head[0].upper in _START_ERROR_WORDS
            or head[0].upper in ("IF", "ELSEIF", "WHILE", "UNTIL", "SELECT", "CASE", "FOR", "NOT"))):
        return None
    if any(t.kind == "IDENT" and t.upper not in operands and t.upper not in profile.functions
           and t.upper not in _BINARY_WORDS for t in head):
        return None  # an unknown name stops the scan before the string
    stack: list[list] = []   # open calls: [function record or None, arguments so far]
    for j, t in enumerate(head):
        if t.kind != "OP":
            continue
        if t.text == "(":
            prev = head[j - 1] if j else None
            rec = profile.functions.get(prev.upper) if prev is not None and prev.kind == "IDENT" \
                and prev.upper not in operands else None
            stack.append([rec, 1])
        elif t.text == ")" and stack:
            stack.pop()
        elif t.text == "," and stack:
            stack[-1][1] += 1
    if any(rec and rec.get("nargs") and n > rec["nargs"] for rec, n in stack):
        return "many"  # the call's own argument count is reported first
    last = head[-1]
    if last.kind in ("IDENT", "NUMBER") or (last.kind == "OP" and last.text == ")"):
        # an operand then the string: only a lone statement-start operand, optionally subscripted
        first = head[0]
        if first.kind == "NUMBER" and len(head) == 1:
            return "reached"
        if first.kind == "IDENT" and first.upper in operands and first.upper not in profile.symbols \
                and not (first.upper in profile.functions and first.upper not in operands):
            inner = [t.text for t in head[2:-1]]
            return "reached" if len(head) == 1 or (
                head[1].text == "(" and last.text == ")" and "(" not in inner and ")" not in inner) else None
    probe = _pass1_scan(head_text + " __crbunk__", operands, profile)
    return "reached" if probe is not None and probe[0] == "pass2" else None


class _Everything:
    """A name set that contains every name: `_scan_expr` then reports syntax problems only."""
    def __contains__(self, item) -> bool:
        return True


def _instruction_arg_findings(text: str, operands: set[str], profile,
                              variables=frozenset()) -> list[tuple[int, str, str]]:
    """CR6/CR1000X `Keyword(arg, arg, ...)` whose arguments cannot be parsed (`Sample(1,x,FP  2)`,
    `Scan(1,S  ec,3,1)`): the instruction is still recognised, and each argument is read as an
    expression on its own, left to right (oracle, 2026-10-03). Returns `(stage, kind, arg text)`:
    stage 1 `incomplete`/`no_matching` for an adjacent-operand argument (all of them are reported),
    stage 2 `symbol` (`Invalid symbol in expression: <arg>.`) for the first argument whose name does
    not resolve, which stops the scan (a later malformed argument is then not reported). The arg
    text keeps its trailing blanks, not its leading ones. Empty list: not this shape."""
    try:
        toks = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return []
    if len(toks) < 2 or toks[0].kind != "IDENT" or toks[0].upper not in profile.instructions \
            or toks[0].upper in parser._RAW_NAME_FIRST:
        return []
    if toks[0].upper in instr_rules.ZERO_PARAM_INSTRUCTIONS and len(toks) > 1 and not (
            toks[1].kind == "OP" and toks[1].text in ("=", ".")):
        token = instr_rules._next_simple_token(text[toks[0].col + len(toks[0].text):])
        return [(3, f"Too many parameters:  {token}.", "")] if token else []
    if toks[0].upper == "CALLTABLE" and len(toks) > 2 and toks[1].kind == "IDENT" \
            and toks[2].kind != "OP":
        # `CallTable Tab is used`: the table is the first word, the rest is a second argument
        # (oracle, CR6/CR1000X, 2026-10-10)
        cut = toks[1].col + len(toks[1].text)
        return _instruction_arg_findings(text[:cut] + "," + text[cut:], operands, profile, variables)
    paren = toks[1].kind == "OP" and toks[1].text == "("
    if paren and (len(toks) < 3 or not (toks[-1].kind == "OP" and toks[-1].text == ")")):
        return []
    if not paren and (toks[1].kind == "OP" or toks[0].upper in _STATEMENT_FORM_EXCLUDED
                      or (toks[0].upper != "CALLTABLE" and not instr_rules.plain_expression_params(profile.instructions[toks[0].upper]["params"]))):
        return []       # statement form `Kw a b, c` is read like `Kw(a b, c)` (oracle, 2026-10-10)
    args: list[tuple[list, str]] = []
    depth, cur = 0, []
    start = toks[1].col + 1 if paren else toks[1].col
    for t in toks[2:] if paren else toks[1:]:
        if t.kind == "OP" and t.text == "(":
            depth += 1
        elif t.kind == "OP" and t.text == ")":
            if depth == 0:
                args.append((cur, text[start:t.col].lstrip()))
                break
            depth -= 1
        elif t.kind == "OP" and t.text == "," and depth == 0:
            args.append((cur, text[start:t.col].lstrip()))
            cur, start = [], t.col + 1
            continue
        cur.append(t)
    else:
        if paren:
            return []
        args.append((cur, text[start:].lstrip()))
    if toks[0].upper == "CALLTABLE" and len(args) > 2 and instr_rules._next_simple_token(args[2][1]):
        return [(3, f"Too many parameters:  {instr_rules._next_simple_token(args[2][1])}.", "")]
    bad = ("incomplete", "no_matching")
    syntax = [(_scan_expr(t, 0, _Everything(), profile.functions, assign=True, start_operand=False)
               if t else None, t, txt) for t, txt in args]
    # an argument starting with `To`/`Is`/a binary word is `Invalid, or out of place expression:
    # <arg>.` (oracle, 2026-10-10: `Scan to x`, `Randomize and 3`, `Calibrate(to x)`)
    syntax = [(("pass1", "fallback") if t and t[0].kind == "IDENT" and t[0].upper in _START_ERROR_WORDS
               else v, t, txt) for v, t, txt in syntax]
    bad = bad + ("fallback",)
    if any(v is not None and not (v[0] == "pass1" and v[1] in bad) for v, _, _ in syntax) or \
            not (any(v is not None for v, _, _ in syntax) or toks[0].upper == "CALLTABLE"):
        return []
    out: list[tuple[int, str, str]] = []
    record = profile.instructions[toks[0].upper]
    short = instr_rules.shortfall_message(record, len(args))
    params = instr_rules._real_params(record["params"])
    for i, (v, arg_toks, arg_text) in enumerate(syntax):
        if not arg_toks or (i == 0 and toks[0].upper == "CALLTABLE"):
            continue        # (a table name is no variable)
        code = params[i] % 100 if i < len(params) else None
        if _scan_expr(arg_toks, 0, operands, profile.functions, assign=True,
                      start_operand=False) == ("pass2", "symbol"):
            if short and not out and (code in _DEFERRED_DEST_CODES or _symbol_stage(code) != 4):
                return [(3, short, "")]     # pass 2 resolves names after the (pass-1) arity check
            return out or [(_symbol_stage(code), "symbol", arg_text)]
        if v is not None and v[1] != "fallback" and not out and code in _CONST_ONLY_CODES and any(
                t.kind == "IDENT" and t.upper not in operands and t.upper not in profile.functions
                and t.upper not in _BINARY_WORDS and t.upper != "NOT" for t in arg_toks):
            # a constant-only argument resolves every name first: `Invalid symbol`, not `Incomplete`
            after = [(5, f"Variable illegal in Parameter: {arg_text}.", "")] if any(
                t.kind == "IDENT" and t.upper in variables for t in arg_toks) else []
            return [(_symbol_stage(code), "symbol", arg_text)] + after
        if v is not None:
            out.append((1, v[1], arg_text))
            # the parameter's own kind rejects the malformed argument too (oracle, 2026-10-10)
            if code in _CONST_ONLY_CODES and any(t.kind == "IDENT" and t.upper in variables
                                                 for t in arg_toks):
                out.append((3, f"Variable illegal in Parameter: {arg_text}.", ""))
    if short and out and len(args) >= 2 and all(o[0] == 1 for o in out):
        out.append((3, short, ""))      # (one argument short of two: not with a single argument)
    return out


_STATEMENT_FORM_EXCLUDED = frozenset({"IF", "ELSEIF", "WHILE", "DO", "LOOP", "SELECT", "CASE", "FOR",
                                      "NEXT", "CALL", "RETURN", "EXIT"})
_DEFERRED_DEST_CODES = frozenset({8, 27, 29})


def _symbol_stage(code: int | None) -> int:
    """Stage of an unresolved name in an argument: 4 (pass 1, with the other const-family
    positions folded while the statement is parsed, `expressions.check_const_family_args`) for a
    constant-family parameter, else 2 (pass 2). Oracle, CR6/CR1000X, 2026-10-12: `PanelTemp(x,
    Zork Y)`, `MQTTPublishTable(MQTT QoS, 5)`, `Scan (1,S  ec,3,1)` hide the generic fallback
    lines before and after them; `PanelTemp(Zork Y, 15000)` does not."""
    kind = expressions._type_code_kinds().get(code - 100 if code and code >= 100 else code)
    if kind == "deferred_text":
        return 6    # a table-output source: pass 2, and inside a table it ends pass 2
    return 4 if kind in expressions._CONST_FAMILY_KINDS else 2
_CONST_ONLY_CODES = frozenset({1, 5, 20, 38})


def _later_arg_unknown(args, operands: set[str], functions: dict) -> bool:
    """A subscript scan stops at its first top-level comma; a later argument that names an unknown
    symbol (`cv(4, 5, & 6) with x`, `&` before a non-literal is a name) still leaves the line to
    pass 2 (oracle, CR6/CR1000X, 2026-10-12)."""
    depth, start = 0, None
    for k, t in enumerate(args):
        if t.kind == "OP" and t.text in ("(", ")"):
            depth += 1 if t.text == "(" else -1
        elif t.kind == "OP" and t.text == "," and depth == 0:
            if start is not None and _scan_expr(args[start:k], 0, operands, functions, assign=True,
                                                start_operand=False) == ("pass2", "symbol"):
                return True
            start = k + 1
    return start is not None and _scan_expr(args[start:], 0, operands, functions, assign=True,
                                            start_operand=False) == ("pass2", "symbol")


_SELF_CLOSING_FUNCTIONS = frozenset({"ABS", "ABSLONG", "ARRAYLENGTH", "TYPEOF", "LINENUM", "SIGNATURE"})


def _scan_expr(toks, i: int, operands: set[str], functions: dict, assign: bool,
               start_operand: bool, unclosed: str | None = None,
               cond: bool = False, to_ok: bool = True, open_fallback: bool = False,
               symbol_unknown: bool = False) -> tuple[str, str] | None:
    """(`to_ok`: `To` is a binary operator, once per parenthesis level and argument; everywhere but
    `If`/`While`/`Do`/`Loop` conditions, oracle 2026-10-10, all four loggers.)"""
    to_levels: set[int] = set()
    rel_levels: set[int] = set()  # levels holding a comparison; none may sit beside a `To`
    unknown = ("pass2", "symbol" if assign or symbol_unknown else "fallback")
    unclosed = unclosed or ("no_matching" if assign else "fallback")
    parens: list[str] = []          # "call" or "group"
    pnames: list[str | None] = []   # the built-in function a "call" parenthesis belongs to
    argn: list[int] = []            # the commas seen so far in each open parenthesis
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
                        pnames.append(u)
                        argn.append(0)
                        to_levels.discard(len(parens))
                        rel_levels.discard(len(parens))
                        i += 1
                elif u in operands:
                    if opens:
                        parens.append("call")
                        pnames.append(u if u in functions and isinstance(operands, _Everything) else None)
                        argn.append(0)
                        to_levels.discard(len(parens))
                        rel_levels.discard(len(parens))
                        i += 1
                    else:
                        expect_operand = False
                else:
                    return unknown
            elif t.kind == "OP" and t.text == "(":
                parens.append("group")
                pnames.append(None)
                argn.append(0)
                to_levels.discard(len(parens))
                rel_levels.discard(len(parens))
            elif t.kind == "OP" and t.text in ("-", "+"):
                pass
            elif t.kind == "OP" and t.text == "&" and not (
                    nxt is not None and nxt.kind == "IDENT" and nxt.upper == "H" and nxt.col == t.col + 1
                    and i + 2 < len(toks)):
                return unknown  # `&` in operand position that is no hex literal names an unknown symbol
            elif t.kind == "OP" and t.text in _BINARY_OPS:
                return ("pass1", "fallback")
            elif cond and t.kind == "OP" and t.text == ",":
                return ("pass1", "fallback")
            else:
                return None
        else:
            if t.kind == "OP" and t.text == ")":
                if not parens:
                    return ("pass1", "incomplete" if assign or cond else "fallback")
                parens.pop()
                pnames.pop()
                argn.pop()
            elif t.kind == "OP" and t.text == ",":
                to_levels.discard(len(parens))
                rel_levels.discard(len(parens))
                if not parens:
                    if cond:
                        return ("pass1", "incomplete")
                    return None if assign else ("pass1", "fallback")
                if parens[-1] != "call":
                    return ("pass1", "no_matching" if assign else "fallback")
                argn[-1] += 1
                expect_operand = True
            elif t.kind == "OP" and t.text in _BINARY_OPS:
                if to_ok and t.text in ("=", "<>", "<", ">", "<=", ">="):
                    if len(parens) in to_levels:
                        return ("pass1", "no_matching" if parens else "incomplete")
                    rel_levels.add(len(parens))
                expect_operand = True
            elif t.kind == "IDENT" and t.upper in _BINARY_WORDS:
                if t.upper != "MOD":
                    to_levels.discard(len(parens))
                    rel_levels.discard(len(parens))
                expect_operand = True
            elif (to_ok and t.kind == "IDENT" and t.upper == "TO" and len(parens) not in to_levels
                  and len(parens) not in rel_levels):
                to_levels.add(len(parens))
                expect_operand = True
            elif not parens and t.kind == "OP" and t.text == ".":
                # `X = 1 .` is incomplete, `X and X .` the fallback (oracle, all four loggers,
                # 2026-10-10)
                return ("pass1", "incomplete" if assign else "fallback")
            elif t.kind in ("IDENT", "NUMBER", "STRING"):
                # an operand right after an operand: inside a plain `( )` group the real
                # compiler calls the group unmatched (oracle, 2026-10-03); inside a built-in
                # call's arguments it expects a comma unless this is the call's last argument
                if parens and parens[-1] == "call" and pnames[-1] and \
                        pnames[-1] not in _SELF_CLOSING_FUNCTIONS:
                    nargs = functions[pnames[-1]].get("nargs") or 0
                    return ("pass1", "expects_comma" if argn[-1] + 1 < nargs else "no_matching")
                return ("pass1", "no_matching" if parens and (
                    parens[-1] == "group" or (to_ok and t.upper == "TO")) else "incomplete")
            else:
                return None
        i += 1
    if expect_operand:
        # (`If (x +`: the condition of an `If`/`While`/`Do` is the fallback even with a parenthesis
        # still open, oracle, CR6/CR1000X, 2026-10-12)
        return ("pass1", "fallback") if not parens or open_fallback else None
    if parens:
        return ("pass1", unclosed)
    return None if assign else ("pass1", "fallback")


def _bare_function_needs_paren(name: str, functions: dict) -> bool:
    """`expressions._is_bare_reference_problem` without its declared-variable exemption (the
    caller has already excluded pass-1 operands)."""
    record = functions.get(name)
    return bool(record) and record.get("nargs") is not None and (
        record["nargs"] > 0 or bool(record.get("argspec")))


def _mentions_then(node) -> bool:
    if isinstance(node, S.Name):
        return node.name.upper() == "THEN"
    if dataclasses.is_dataclass(node):
        return any(_mentions_then(getattr(node, f.name)) for f in dataclasses.fields(node)
                   if f.name not in ("line", "file"))
    return isinstance(node, list) and any(_mentions_then(n) for n in node)


def _check_if_pass1(stmt, operands: set[str], profile, diags: Diagnostics, program=None) -> None:
    """Pass-1 messages of an `If`/`ElseIf` the parser accepted: a dangling operator before `Then`
    (`If X = Then`, parsed with `Then` as the operand) is the pass-1 expression error on the
    condition alone; an unparsable one-line `If ... Then X = Y +` reports its statement
    (oracle, CR6/CR1000X, 2026-10-08)."""
    if _mentions_then(stmt.cond):
        cond = condition_part(stmt.text)
        verdict = _pass1_scan(stmt.text, operands, profile) if cond is not None else None
        if verdict and verdict[0] == "pass1":
            diags.error(stmt.line, _pass1_message(profile, stmt, verdict[1]), stmt.file)
    for sub in (getattr(stmt, "inline", None) or []) + (getattr(stmt, "inline_else", None) or []):
        if (isinstance(sub, S.Call) and not sub.explicit and sub.name.upper() in operands
                and sub.name.upper() not in profile.instructions
                and sub.name.upper() not in profile.functions):
            diags.error(sub.line, _out_of_place(profile, sub.text, sub.at_eol), sub.file)  # `If c Then Y`: a bare variable is a pass-1 error
        if profile.known_first_word_pass1 and _block_keyword_call(sub):
            diags.error(sub.line, f"Incomplete expression: {sub.text}.", sub.file)
        if isinstance(sub, S.Unparsed) and _is_generic_unparsed(sub):
            if program is not None and _logical_assign_accepted(sub, program, profile):
                continue   # `If c Then X AND Y = 1` compiles like the statement on its own line
            verdict = _pass1_scan(sub.text, operands, profile)
            if verdict and verdict[0] == "pass1":
                diags.error(sub.line, _pass1_message(profile, sub, verdict[1]), sub.file)


_CALLED_BLOCK_KEYWORDS = frozenset({"CASE", "DO", "FOR", "FUNCTION", "SCAN", "SELECT", "SUB",
                                    "SUBSCAN", "WHILE"})


_FOR_TO_RE = re.compile(r"^\s*For\s(?:.*?)=(?:.*?)\sTo\s(.*)$", re.I | re.S)
_FOR_STEP_RE = re.compile(r"\sStep\s", re.I)


def _for_header_incomplete(text: str, profile) -> tuple[str, bool] | None:
    """`For I = 5 To 1 Step-1`, `... Step(-1)`, `... To 12 Step`, `... Step -1 X`, `... To T.X 3`: the
    init expression and the text after `To` (split at a blank-delimited `Step`) are each an
    expression, and one with adjacent operands is a pass-1 `Incomplete expression: <half>.` (oracle,
    CR6/CR1000X 2026-10-10; CR300/CR350 2026-10-12: only the half is echoed, `For I = 1 To T.X 3` ->
    `T.X 3`, `For I = 1 4 To 3` -> ` 1 4`). Returns `(half as written, is the init half)`, the
    first broken half in order, trailing blanks kept. `None`: not this shape."""
    m = _FOR_TO_RE.match(text)
    if not m:
        return None
    init = _FOR_INIT_RE.match(text)
    parts = [(init.group(1), True)] * bool(init) + [(h, False) for h in _FOR_STEP_RE.split(
        m.group(1), maxsplit=1)]
    for part, is_init in parts:
        try:
            toks = [t for t in lexer.tokenize(part, 0) if t.kind != "EOF"]
        except lexer.LexError:
            return None
        if toks and _scan_expr(_merge_members(toks), 0, _Everything(), profile.functions,
                               assign=False, start_operand=False) == ("pass1", "incomplete"):
            return part, is_init
    return None


_FOR_INIT_RE = re.compile(r"^\s*For\s(?:.*?)=(.*?)\sTo\s", re.I | re.S)


def _merge_members(toks: list) -> list:
    """`T.X(1)`, `Cfg.n`, `Status.SerialNumber(1,1)`: a dotted member name is one operand in a
    `For` header (it compiles on CR6/CR1000X; the assignment-only `X = 1 .` rule must not see the
    dot). A trailing `.` after a name merges as well (`To T. ` compiles, oracle 2026-10-12)."""
    out: list = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.kind == "IDENT":
            j = i + 1
            text = t.text
            while j < len(toks) and toks[j].kind == "OP" and toks[j].text == "." and (
                    j + 1 >= len(toks) or toks[j + 1].kind == "IDENT"):
                text += "."
                j += 1
                if j < len(toks) and toks[j].kind == "IDENT":
                    text += toks[j].text
                    j += 1
            if j > i + 1:
                t = lexer.Token("IDENT", text, text.upper(), t.col, t.line)
            out.append(t)
            i = j
            continue
        out.append(t)
        i += 1
    return out


def _for_header_parts(text: str, profile) -> list[tuple[str, str]]:
    """The broken halves of a `For` header, init first, then `To`, then `Step`: `(kind, half as
    written)` with kind `incomplete`, `fallback` or `no_matching` (`_scan_expr` verdicts)."""
    out: list[tuple[str, str]] = []
    m = _FOR_TO_RE.match(text)
    if not m:
        return out
    halves = _FOR_STEP_RE.split(m.group(1), maxsplit=1)
    init = _FOR_INIT_RE.match(text)
    for part, is_init in [(init.group(1), True)] * bool(init) + [(h, False) for h in halves]:
        try:
            toks = [t for t in lexer.tokenize(part, 0) if t.kind != "EOF"]
        except lexer.LexError:
            return []
        if not toks:
            if is_init or part is halves[0]:
                out.append(("fallback", part))  # an empty init / `To` expression (oracle 2026-10-12)
            continue
        toks = _merge_members(toks)
        verdict = _scan_expr(toks, 0, _Everything(), profile.functions, assign=True,
                             start_operand=False, unclosed="no_matching", open_fallback=True)
        if verdict == ("pass1", "incomplete"):
            out.append(("incomplete", part))
        if verdict == ("pass1", "fallback"):
            out.append(("fallback", part))
        if verdict == ("pass1", "no_matching"):
            out.append(("no_matching", part))
    return out


def _for_header_findings(text: str, profile) -> list[str]:
    """CR6/CR1000X pass-1 message of a `For` header whose init, `To` or `Step` expression is
    broken, echoing that expression as written (oracle, 2026-10-12: `For x = y To (y +` is
    `Invalid, or out of place expression: (y +.`, `To (y 5` is `No Matching, or out of place )
    in expression: (y 5.`, `For x = y +  To 5` echoes ` y + `). Every broken half is reported, in
    order."""
    return [_for_header_message(kind, part) for kind, part in _for_header_parts(text, profile)]


def _for_header_message(kind: str, part: str) -> str:
    return {"incomplete": f"Incomplete expression: {part}.",
            "fallback": f"Invalid, or out of place expression: {part}.",
            "no_matching": f"No Matching, or out of place ) in expression: {part}."}[kind]


def _block_keyword_call(stmt: S.Stmt) -> bool:
    """A block keyword used as a function in a value (`Y = Scan(1)`, `1 + SubScan(1)`, `If Scan(1)
    Then`) is a pass-1 `Incomplete expression: <stmt>.` (oracle, CR6/CR1000X, 2026-10-10; the bare
    keyword compiles, `If(...)` is the real function)."""
    if isinstance(stmt, S.Assignment):
        exprs = [stmt.value]
    elif isinstance(stmt, (S.If, S.ElseIf)):
        exprs = [stmt.cond]
    else:
        return False
    return any(i.name.upper() in _CALLED_BLOCK_KEYWORDS
               for e in exprs for i in expressions._index_nodes(e))


def _unclosed_call_message(profile, text: str, at_eol: bool, operands: set[str]) -> str | None:
    """CR6/CR1000X: a statement left open inside a built-in function call, by that call's own
    argument count N (oracle, 2026-10-10, `unclosed_call_count.json`): `Round(X` (fewer than N,
    no trailing comma) is `Expression expects , : <stmt>.`; a trailing comma with at most N
    arguments counting the missing one (`Round(X,`, `FileOpen (S,`) is the fallback; anything
    else stays `No Matching, or out of place )`. None: not this shape (`Abs`, which also accepts
    an unclosed call, is left alone)."""
    if not (profile.known_first_word_pass1 or profile.single_pass):
        return None
    try:
        toks = [t for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return None
    if not toks or toks[-1].text == "(":
        return None
    stack: list[list] = []   # [function record or None, arguments so far]
    for j, t in enumerate(toks):
        if t.kind != "OP":
            continue
        if t.text == "(":
            prev = toks[j - 1] if j else None
            rec = profile.functions.get(prev.upper) if prev is not None and prev.kind == "IDENT" \
                and prev.upper not in operands and prev.upper != "ABS" else None
            stack.append([rec, 1])
        elif t.text == ")" and stack:
            stack.pop()
        elif t.text == "," and stack:
            stack[-1][1] += 1
    if not stack or not stack[-1][0] or not stack[-1][0].get("nargs"):
        return None
    count, nargs = stack[-1][1], stack[-1][0]["nargs"]
    if toks[-1].text == ",":
        return _out_of_place(profile, text, at_eol) if count <= nargs else None
    if count >= nargs:
        return None
    sep = "\n" if profile.fallback_echo_newline and at_eol else ""
    return f"Expression expects , : {text}{sep}."


def _pass1_message(profile, stmt: S.Stmt, kind: str, operands: set[str] | None = None) -> str:
    text = condition_part(stmt.text)
    if kind == "no_matching" and operands is not None \
            and (refined := _unclosed_call_message(profile, stmt.text if text is None else text,
                                                   getattr(stmt, "at_eol", True),
                                                   operands)) is not None:
        return refined
    if text is None and kind in ("fallback", "no_matching") and operands is not None and \
            (inline := inline_if_condition(stmt.text)) and \
            (refined := _unclosed_call_message(profile, inline, False, operands)) is not None:
        return refined   # `If Round(X Then Y = 1` (oracle, CR6/CR1000X, 2026-10-12, review R13C-5)
    if text is None:
        text = stmt.text
    return {
        "fallback": _out_of_place(profile, text, getattr(stmt, "at_eol", True)),
        "incomplete": f"Incomplete expression: {text}.",
        "no_matching": f"No Matching, or out of place ) in expression: {text}.",
        "expects_comma": f"Expression expects , : {text}.",
        "symbol": f"Invalid symbol in expression: {text}.",
        "expects_paren": f"Expression expects ( : {text}.",
        "missing_expr": "Invalid or missing expression.",
        "invalid_assignment": "Invalid assignment in expression: "
                              f"{stmt.text}{chr(10) if profile.fallback_echo_newline and getattr(stmt, 'at_eol', True) else ''}.",
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
        if nxt.reason in (_UNITS_WITHOUT_EQ, _CONST_WITHOUT_EQ, _ALIAS_WITHOUT_EQ, _FOR_MISSING) or \
                nxt.reason.startswith((_INVALID_AS_CLAUSE, _CONST_BAD_VALUE, _CONST_BAD_NAME, _DO_LOOP_PARAM, _CONST_LEAD,
                                      _ALIAS_NOTHING_AFTER_EQ)) or \
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
        elif nxt.reason == _EXTRA_CLOSE_PAREN and not _assignment_head(nxt.text, program, profile):
            diags.error(nxt.line, _out_of_place(profile, nxt.text, nxt.at_eol), nxt.file,
                        show_file=False)
        elif nxt.reason == _EXTRA_CLOSE_PAREN:
            diags.error(nxt.line, f"Incomplete expression: {nxt.text}.", nxt.file,
                        show_file=False)
        else:
            diags.error(nxt.line, _leftover_message(profile, nxt), nxt.file)


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
        # `S() tail` reports it twice (oracle, 2026-10-04)
        for _ in range(2 if stmt.tail_ignored else 1):
            diags.error(stmt.line, "Call argument required.", stmt.file)
    else:  # an empty required argument (`S(1,)` is dropped, `S(,2)`, `S(1) )` are not): one each
        for arg, param in zip(stmt.args, sub.params):
            if arg is None and param.default is None:
                diags.error(stmt.line, "Call argument required.", stmt.file)


def _report_call_arg_names(stmt: S.Call, known: set[str], profile, diags: Diagnostics,
                           is_function: bool = False, ds=None) -> None:
    """Undefined names in a `Sub` call's arguments, every argument (oracle, 2026-09-28: `Two(Q,
    Q2)` reports both). CR6/CR1000X echo the argument (`Invalid symbol in expression: Q+1.`);
    CR300/CR350 name each undefined name, plus `Dimensions not declared: Nope(1).`."""
    if stmt.juxtaposed is not None:  # `Fn(1) Zork`: oracle 2026-10-08, all four loggers
        arg_bad = [n for a in stmt.args for n in expressions._undefined_names(a, known)]
        if profile.name in expressions._WHOLE_EXPR_ECHO_LOGGERS:
            if arg_bad:  # an undefined argument wins; undefined tail names are never named
                diags.error(stmt.line, f"Invalid symbol in expression: {stmt.text}.", stmt.file)
                return
        else:  # names first; of the tail only a leading bare name is checked
            builtin = stmt.name.upper() in profile.functions
            if builtin and expressions._call_arity_problem(stmt.name, stmt.args, profile):
                return  # (the arity message is the statement's verdict)
            if builtin or stmt.tail_expr is not None:
                # a built-in or an expression head (`StrComp(a,b) > 0 Then`, oracle, CR300/CR350,
                # 2026-10-11): a subscripted undefined name ends the walk with `Dimensions not
                # declared`, no `Incomplete expression`; the tail's first operand is named by its
                # undefined names (an indexed one too)
                names, dims = expressions._names_and_dims(
                    known, ds, stmt, (), *([stmt.tail_expr] if stmt.tail_expr is not None else stmt.args))
                for name in dict.fromkeys(names):
                    diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                                stmt.file)
                if dims:
                    diags.error(stmt.line, expressions.dimensions_message(
                        stmt.text, profile, True, stmt.at_eol), stmt.file)
                    return
                arg_bad = []
                tail = stmt.juxtaposed[:1] if not stmt.juxt_opaque and isinstance(
                    stmt.juxtaposed[0], (S.Name, S.Index)) else []
            else:
                tail = stmt.juxtaposed[:1] if not stmt.juxt_opaque and isinstance(
                    stmt.juxtaposed[0], S.Name) else []
            bad = arg_bad + [n for a in tail for n in expressions._undefined_names(a, known)]
            for name in dict.fromkeys(bad):
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                            stmt.file)
        sep = "\n" if profile.fallback_echo_newline and stmt.at_eol else ""
        diags.error(stmt.line, f"Incomplete expression: {stmt.text}{sep}.", stmt.file)
        return
    if stmt.tail_expr is not None:  # `Fn(1)+Zork`: one expression statement
        bad = [n for a in [*stmt.args, stmt.tail_expr] for n in expressions._undefined_names(a, known)]
        if bad and profile.name in expressions._WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, f"Invalid symbol in expression: {stmt.text}.", stmt.file)
        else:
            names, dims = expressions._names_and_dims(known, None, stmt, (), stmt.tail_expr)
            for name in dict.fromkeys(names):
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                            stmt.file)
            if dims:
                diags.error(stmt.line, expressions.dimensions_message(
                    stmt.text, profile, True, stmt.at_eol), stmt.file)
        return
    m = re.match(r"\s*(?:call\s+)?" + re.escape(stmt.name) + r"\s*", stmt.text, re.I)
    if not m or not stmt.text[m.end():].startswith("("):
        return
    if is_function and profile.name not in expressions._WHOLE_EXPR_ECHO_LOGGERS:
        # CR300/CR350 (oracle, 2026-10-10): `Fn(Zork(1))` names the undefined names up to the
        # subscripted one, then `Dimensions not declared:` echoes the whole statement
        names, dims = expressions._names_and_dims(known, None, stmt, (), *stmt.args)
        for name in names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
        if dims:
            diags.error(stmt.line, expressions.dimensions_message(stmt.text, profile, True,
                                                                  stmt.at_eol), stmt.file)
        return
    last_tail = None
    items = _group_items(stmt.text, m.end())
    quirk = [False] * len(items or [])
    if items is not None and len(items) < len(stmt.args) and not stmt.tail_ignored:
        # the tail after `)` is more arguments (`Call S3(1) nope`); CR6/CR1000X echo it with the
        # trailing newline (oracle, 2026-10-04, all four loggers)
        close = _group_end(stmt.text, m.end())
        tail = _split_top(stmt.text[close + 1:]) if close is not None else []
        if tail and not tail[-1].strip():
            tail.pop()
        if len(items) + len(tail) == len(stmt.args):
            items, quirk = items + tail, quirk + [False] * (len(tail) - 1) + [stmt.at_eol]
            last_tail = len(items) - 1
    if items is None and _group_end(stmt.text, m.end()) is None and stmt.text[m.end()] == "(":
        # `Call S(A,Zork` -- the paren runs off the end of the line and compiles; the last argument
        # is echoed like a trailing tail piece: trailing blanks kept, newline only at the real end
        # of line (oracle, 2026-10-13, all four loggers, `docs/checker.md` "Round 16")
        items = _split_top(stmt.text[m.end() + 1:])
        quirk = [False] * (len(items) - 1) + [stmt.at_eol]
        last_tail = len(items) - 1
    if items is None or len(items) != len(stmt.args):
        return
    for i, (arg, raw, nl) in enumerate(zip(stmt.args, items, quirk)):
        names = expressions._undefined_names(arg, known)
        if not names:
            if not is_function and (counts := getattr(ds, "dim_counts", None)) and expressions._over_indexed(
                    arg, counts, expressions._dim_limit(profile)):
                # an argument indexing a declared variable past its dimensions (CR6/CR1000X: past
                # three): `Dimensions not declared: <argument>.`, every such argument (oracle,
                # 2026-10-13, all four loggers, `docs/checker.md` "Round 16")
                echo = raw.lstrip() if i == last_tail else raw.strip()
                diags.error(stmt.line, expressions.dimensions_message(echo, profile, nl, nl),
                            stmt.file)
            continue
        echo = raw.strip()
        if i == last_tail:  # the final tail piece keeps its trailing blanks (oracle, 2026-10-04)
            echo = raw.lstrip()
        if profile.name in expressions._WHOLE_EXPR_ECHO_LOGGERS and is_function:
            # A bare call of a declared `Function` is one expression statement: a single line
            # echoing the whole statement, however many arguments are undefined (oracle).
            diags.error(stmt.line, f"Invalid symbol in expression: {stmt.text}.", stmt.file)
            return
        if profile.name in expressions._WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, f"Invalid symbol in expression: {echo}{chr(10) if nl else ''}.",
                        stmt.file)
            continue
        for name in names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
        if expressions._has_undefined_index(arg, known):
            diags.error(stmt.line, expressions.dimensions_message(echo, profile, nl, nl), stmt.file)


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
                       link_ctx: "_LinkContext | None" = None, ds=None) -> int | None:
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
            if known is not None:   # the arguments are resolved first (oracle, 2026-10-12)
                _report_call_arg_names(stmt, known, profile, diags, is_function=False, ds=ds)
            return _unlinked_call(stmt, link, profile, diags, before_beginprog, link_ctx)
    if sub_params and name in sub_params:
        # names first; CR300/CR350 then print no argument-count error (oracle, 2026-10-04)
        before = len(diags.items)
        if known is not None:
            _report_call_arg_names(stmt, known, profile, diags,
                                   is_function=sub_params[name].keyword.upper() == "FUNCTION"
                                   and not stmt.explicit, ds=ds)
        if not (profile.single_pass and len(diags.items) > before):
            _check_call_args(stmt, sub_params[name], diags)
    if name in subs:
        if before_beginprog:
            diags.error(stmt.line, "Call illegally occurs before BeginProg.", stmt.file)
            return stmt.line
        return None
    if name in profile.functions:
        if profile.single_pass and known is not None and (
                stmt.tail_expr is not None or stmt.juxtaposed is not None):
            _report_call_arg_names(stmt, known, profile, diags, is_function=True, ds=ds)  # (a built-in
            # function is an expression statement like a user `Function`, oracle 2026-10-11)
        if before_beginprog:
            diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
            return stmt.line
        return None
    if profile.undefined_call_message:
        # CR300/CR350 only (`profiles.py`'s own field docstring, `docs/loggers.md`): an extra
        # `{name} is not defined.` line right before the generic fallback below, and no
        # before-`BeginProg` follow-up/stop for this branch (unlike the two branches above).
        if known is None or name not in known or name in getattr(known, "tables", ()):
            # (a declared `x()` is not `not defined`, oracle 2026-10-04; a DataTable name is)
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=stmt.name),
                        stmt.file)
        diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file)
        return None
    if re.fullmatch(r"[A-Za-z_]\w*\.", stmt.name) and re.match(r"\s*\w+\.", stmt.text) and not stmt.explicit and known is not None:
        base = stmt.name[:-1].upper()
        if base and base not in getattr(known, "tables", ()) and base not in symbols._ALWAYS_KNOWN_SYSTEM_TABLES:
            # `times.`: the name before the dot is no table (oracle, CR6/CR1000X, 2026-10-10)
            diags.error(stmt.line, f"Could not find {stmt.name[:-1]}.", stmt.file)
    diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file,
                show_file="cr3xx")
    if before_beginprog:
        diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
        return stmt.line
    return None


def _over_indexed_target(target: S.Index, ds) -> bool:
    """A declared variable written with more subscripts than it has dimensions: with an undefined
    subscript the names are followed by the generic fallback, and the value is not read (oracle,
    CR300/CR350, 2026-10-12: `R(4,Zork) = Zork2` for `Public R(4)`)."""
    counts = getattr(ds, "dim_counts", None)
    if counts is None:
        return False
    count = counts.get(target.name.upper())
    return count is not None and len(target.args) > count and not (
        len(target.args) == 3 and target.name.upper() in getattr(counts, "strings", ()))


def _check_assignment_pass2(stmt: S.Assignment, known: set[str], diags: Diagnostics,
                             before_bp: bool, in_body_block: bool,
                             profile=None, ds=None) -> int | None:
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
    if isinstance(stmt.target, S.Member) and isinstance(stmt.target.base, S.Name) \
            and stmt.target.base.name.upper() == "DIM":
        return None   # (`Dim.X = 1` is answered by `check_pass1`, review R14C-5)
    before_beginprog = not in_body_block and before_bp
    target_names = expressions._undefined_names(stmt.target, known)
    if target_names and profile is not None and profile.single_pass:
        # CR300/CR350 (oracle, 2026-09-27): `Nope = Q` -> `Nope is not defined.`, `Q is not
        # defined.`; `Nope(2) = X` -> `Nope is not defined.` then the generic fallback. Neither
        # stops pass 2 or gets the before-`BeginProg` follow-up.
        for name in target_names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
        if isinstance(stmt.target, S.Index) and (
                (stmt.target.name.upper() not in known
                 and expressions._has_undefined_index(stmt.target, known, ds, stmt))
                or _over_indexed_target(stmt.target, ds)):
            # (an undefined array whose subscripts fit the running dimension count is not
            # a placement error, nor is a declared one with an undefined subscript: `Rv(1)=1`
            # then `Nope(1)=Sc` names only `Nope`; oracle 2026-10-04)
            diags.error(stmt.line, _out_of_place(profile, stmt.text, stmt.at_eol), stmt.file)
        else:
            plain_target = any(not isinstance(t, DottedName) for t in target_names)
            for name in expressions._undefined_names(stmt.value, known):
                if plain_target and isinstance(name, DottedName):
                    continue  # `Nope = Tmp.dat`: only the target is named (oracle, CR300, 2026-10-11)
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                            stmt.file)
            if expressions._has_undefined_index(stmt.value, known, ds, stmt, (stmt.target,)):
                diags.error(stmt.line, expressions.dimensions_message(stmt.text, profile, True,
                                                                      stmt.at_eol), stmt.file)
            elif expressions._find_bare_function_ref(stmt.value, profile, frozenset()):
                # `Socket = TCPOpen` with `Socket` undeclared: the names, then the bare function
                # (oracle, CR300/CR350, 2026-10-10)
                diags.error(stmt.line, expressions._bare_function_message(
                    stmt.text, profile, True, stmt.at_eol), stmt.file)
        return None
    if target_names:
        diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file,
                    show_file="cr3xx")
        if before_beginprog:
            diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
            return stmt.line
        return None
    if expressions._undefined_names(stmt.value, known):
        if before_beginprog and not (profile is not None and profile.single_pass):
            # `X = Nope` before `BeginProg`: CR6/CR1000X add the placement line after
            # `expressions.check`'s own (oracle, 2026-10-03; sorted last on the line, `compiler.py`)
            diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
            return stmt.line
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



def _report_numeric_sub_name(stmt: S.SubDef, value: object, diags: Diagnostics) -> None:
    """`Sub`/`Function` named like an earlier `Const` (oracle, all four loggers, 2026-10-03): the
    name is replaced by the constant's value, so `Subroutine name A illegal; cannot be numeric.`;
    a non-whole number (or a whole one of 3e9 and up) first adds `Parameter <value, %g> is integer type.`"""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and (
            value != int(value) or value >= 3_000_000_000):  # probed whole values: 3e9 and up
        diags.error(stmt.line, f"Parameter {value:g} is integer type.", stmt.file)
    diags.error(stmt.line, f"Subroutine name {stmt.name} illegal; cannot be numeric.", stmt.file)


# oracle, all four loggers, 2026-10-10 (docs/fuzz-findings.md "Round 9"): the only `Const ... As` types
_CONST_TYPES = expressions._CONST_TYPES


def _bad_const_type(stmt: S.ConstDecl) -> str | None:
    """The `As` type as written when it is not one `Const` accepts, else None."""
    if not stmt.type or stmt.type.upper() in _CONST_TYPES:
        return None
    m = re.search(r"\bAs\s+([^\s,]+)", stmt.text or "", re.IGNORECASE)
    return m.group(1) if m else stmt.type


def _local_const_conflicts(stmt: S.Stmt, declared: dict[str, str]) -> list[str]:
    """Names in a `Sub`/`Function` header, a `Dim` or a `Const` inside its body that are already a
    global `Const` declared before it (`declared` is pass 1's own kind map). Each name counts once
    per occurrence, in source order: `Sub Foo(N,N)` -> two names (oracle, 2026-10-11)."""
    if isinstance(stmt, S.SubDef):
        names = [p.name for p in stmt.params]
    elif isinstance(stmt, S.Declaration) and stmt.kind == "DIM":
        names = [v.name for v in stmt.vars]
    elif isinstance(stmt, S.ConstDecl):
        names = [stmt.name]
    else:
        return []
    return [name for name in names if declared.get(name.upper()) == "CONST"]


def _check_duplicate_declaration(stmt: S.Stmt, declared: dict[str, str], diags: Diagnostics,
                                 profile=None) -> int | None:
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
                    return index
                continue  # a failed function-name declaration never registers a variable
            prior = declared.get(v.name.upper())
            if prior in ("VAR", "ALIAS", "CONST"):
                diags.error(stmt.line, f"{v.name} already declared as a variable." if prior != "CONST"
                            else f"{v.name} is already declared as a CONST.", stmt.file)
                if profile and profile.single_pass:
                    # the first redeclared variable stops the statement; the one two on is
                    # named (oracle, CR300/CR350, 2026-10-04: `Public P, Q, R` twice -> `... R.`)
                    if index + 2 < len(stmt.vars):
                        diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                                    stmt.file)
                    return index
            else:
                declared[v.name.upper()] = "VAR"
            if profile and symbols.report_predefined_name_collision(stmt, v.name, profile, diags):
                if profile.single_pass:
                    if index + 2 < len(stmt.vars):
                        diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                                    stmt.file)
                    return index
    elif isinstance(stmt, S.Unparsed) and stmt.reason.startswith(_CONST_LEAD_NAME):
        # `Const = X`: the name is checked as in a declaration (not registered), then `CONST needs
        # assignment.` (oracle, 2026-10-10)
        name = stmt.reason[len(_CONST_LEAD_NAME):]
        prior = declared.get(name.upper())
        if profile and name.upper() in profile.functions:
            symbols.report_function_name_collision(stmt, name, profile, diags)
        elif not (profile and symbols.report_predefined_name_collision(stmt, name, profile, diags)):
            if prior in ("VAR", "ALIAS"):
                diags.error(stmt.line, f"{name} already declared as a variable.", stmt.file)
            elif prior == "CONST":
                diags.error(stmt.line, f"{name} is already declared as a CONST.", stmt.file)
        diags.error(stmt.line, "CONST needs assignment.", stmt.file)
    elif isinstance(stmt, S.ConstDecl):
        if profile and stmt.name.upper() in profile.functions:
            symbols.report_function_name_collision(stmt, stmt.name, profile, diags)
            return
        if profile and symbols.report_predefined_name_collision(stmt, stmt.name, profile, diags):
            return  # failed Const declarations do not register a new constant
        prior = declared.get(stmt.name.upper())
        if prior in ("VAR", "ALIAS"):
            diags.error(stmt.line, f"{stmt.name} already declared as a variable.", stmt.file)
        elif prior == "CONST":
            diags.error(stmt.line, f"{stmt.name} is already declared as a CONST.", stmt.file)
        else:
            declared[stmt.name.upper()] = "CONST"
        if (bad := _bad_const_type(stmt)) is not None:
            diags.error(stmt.line, f"{bad} unknown Const type", stmt.file)
    elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "READONLY":
        # `ReadOnly Nope` -> `Nope not yet declared.`, pass 1, all four loggers (oracle)
        if not stmt.args:
            diags.error(stmt.line, "Missing variable name.", stmt.file)
        for a in stmt.args:
            if isinstance(a, S.Name) and declared.get(a.name.upper()) not in ("VAR", "ALIAS"):
                diags.error(stmt.line, f"{a.name} not yet declared.", stmt.file)
    elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ALIAS":
        # An alias name that is already a `Public`/`Dim` variable or a `Const` (not another
        # alias: that is `Duplicate Alias Name`) -> `%s already declared as a variable.` /
        # `%s is already declared as a CONST.` (oracle, all four loggers, 2026-10-04,
        # `docs/checker.md` "Alias name already declared"). Only the first comma item, and only
        # when it is a bare name with nothing after it; reported by `_check_alias_dims`, after
        # its own dimension error (stashed here under `declared[stmt]` as the message).
        for index, a in enumerate(stmt.args[1:]):
            if isinstance(a, S.Name):
                prior = declared.setdefault(a.name.upper(), "ALIAS")
                if index == 0 and not getattr(stmt, "alias_junk", False):
                    if prior == "VAR":
                        declared[f"#{id(stmt)}"] = f"{a.name} already declared as a variable."
                    elif prior == "CONST":
                        declared[f"#{id(stmt)}"] = f"{a.name} is already declared as a CONST."
                    elif profile and symbols.is_reserved_predefined_name(a.name, profile):
                        # `Alias X = Sec` (oracle, all four loggers, 2026-10-10); the name is
                        # still registered as an alias (`Duplicate Alias Name` follows)
                        declared[f"#{id(stmt)}"] = f"{a.name} is a predefined CONST."
    elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ESSVARIABLES" and profile:
        # declares its table's variables: a later `Public essBatteryStatus` -> `already declared
        # as a variable.` (oracle, 2026-10-01; `checker/ess.py`)
        from . import ess
        for row in ess._table(str(profile.data_dir)):
            if row["count"]:
                declared.setdefault(row["name"].upper(), "VAR")

def _illegal_dim_message(stmt: S.Declaration) -> str:
    # the echo ends with the statement's newline unless a comment follows (oracle, all four loggers)
    return f"Illegally dimension: {stmt.text}{chr(10) if stmt.at_eol else ''}"


def _check_array_dims(stmt: S.Declaration, diags: Diagnostics, env: consteval.Env,
                      profile=None) -> None:
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
    for index, v in enumerate(stmt.vars):
        before = len(diags.items)
        _check_var_dims(stmt, v, diags, env)
        if len(diags.items) > before and profile and profile.single_pass:
            # single-pass loggers stop the declaration at its first bad variable; the item two
            # on supplies `Too many parameters` (oracle, CR300/CR350)
            if index + 2 < len(stmt.vars):
                diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                            stmt.file)
            return index
    return None


def _check_var_dims(stmt: S.Declaration, v, diags: Diagnostics, env: consteval.Env) -> None:
    if len(v.dims) > _MAX_ARRAY_DIMS:
        first_text = _first_dim_text(stmt.text, v)
        diags.error(
            stmt.line,
            f"Variable {v.name}({first_text} dimension exceeds max of {_MAX_ARRAY_DIMS}.",
            stmt.file,
        )
        return
    for d in v.dims:
        if isinstance(d, S.String):
            # `Public Q("fuzz")`, pass 1, all four loggers (oracle, 2026-09-27)
            diags.error(stmt.line, _illegal_dim_message(stmt), stmt.file)
            continue
        try:
            val = consteval.value_of(d, env)
        except consteval.NotConstant:
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        if not (1 <= val <= _MAX_DIM_VALUE):
            diags.error(stmt.line, _illegal_dim_message(stmt), stmt.file)


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


def _group_end(text: str, open_at: int) -> int | None:
    """Index of the `)` closing the `(` at `text[open_at]`."""
    depth, in_str = 0, False
    for i in range(open_at, len(text)):
        ch = text[i]
        if ch == '"':
            in_str = not in_str
        elif not in_str and ch in "(":
            depth += 1
        elif not in_str and ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


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
_STRING_SIZE_RE = re.compile(r"(?i)\bAs\s+String\s*\* ?([^=]*)", re.S)
_EMPTY_DIMS_INIT_RE = re.compile(r"\s*\(\s*\)(\s*)=")
_TYPED_DIMS_RE = re.compile(r"(?i)\s*As\s+[A-Za-z_][A-Za-z0-9_]*\s*(?=\()")


def _check_member_types(stmt: S.Declaration, structs_seen: set[str], profile, diags: Diagnostics) -> None:
    """`Variable Type %s not supported` for a structure member whose `As` type is neither a
    variable type nor a `StructureType` declared earlier in the file (itself included), all four
    loggers (oracle, 2026-10-03: `A As String_Fz`, `A As Floatx`, `A As SU` before `SU`)."""
    segments = _split_top(stmt.text)
    if len(segments) != len(stmt.vars):
        return
    for v, seg in zip(stmt.vars, segments):
        if v.type and v.type not in _VARIABLE_TYPES and v.type not in structs_seen:
            m = _AS_TYPE_RE.search(seg)
            if m and m.group(1).upper() == v.type:
                diags.error(stmt.line, f"Variable Type {m.group(1)} not supported", stmt.file)
                if profile.single_pass:
                    return  # CR300/CR350 stop at the first one in a statement


def _check_header_types(stmt: S.SubDef, structs_seen: set[str], profile, diags: Diagnostics) -> None:
    """`Variable Type %s not supported` for each `As` type in a `Sub`/`Function` header (parameters
    in order, then the return type) that is neither a variable type nor an earlier `StructureType`
    (oracle, all four loggers, 2026-10-03); CR300/CR350 report only the first."""
    for name in _AS_TYPE_RE.findall(stmt.text):
        if name.upper() not in _VARIABLE_TYPES and name.upper() not in structs_seen:
            diags.error(stmt.line, f"Variable Type {name} not supported", stmt.file)
            if profile.single_pass:
                return


def _check_init_bounds(stmt: S.Declaration, env: consteval.Env, diags: Diagnostics) -> None:
    """`Warning: Attempt to initialize variable out of bounds.`, once per variable with more
    initializer values than elements, all four loggers (oracle, 2026-09-28: `Public A(2) = {1,2,3}`,
    `Public A = {1,2}`, `Dim A(2,2) = {1,2,3,4,5}`; an unresolved or illegal dimension counts as 1)."""
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
                continue    # an illegal dimension (zero, negative, a string) counts as 1
                # (oracle, 2026-10-12: `Dim X(3,-1) = {...9 values}`, `Public X(0) = {1,2}`)
            size *= int(n)
        if size is not None and len(v.init) > size:
            diags.warning(stmt.line, "Attempt to initialize variable out of bounds.", stmt.file)


def _adjacent_literals(text: str) -> bool:
    """Two literals side by side (`1  0`, `1 "a"`): the pass-1 `Incomplete expression`."""
    try:
        kinds = [t.kind for t in lexer.tokenize(text, 0) if t.kind != "EOF"]
    except lexer.LexError:
        return False
    return any(a == "NUMBER" and b in ("NUMBER", "STRING") for a, b in zip(kinds, kinds[1:]))


def _check_decl_exprs(stmt: S.Declaration, known: set[str], variables: set[str], profile,
                      diags: Diagnostics, struct_types: frozenset[str] = frozenset(),
                      stop_at: int | None = None) -> int | None:
    """Pass-1 name resolution in `Public`/`Dim`/`Static` array dimensions and initializers.
    Oracle, all four loggers (`docs/checker.md` "Declaration expressions"): `Public A(3, Nope)` ->
    `Invalid symbol in expression: Nope.` (each bad dimension, echo left-trimmed); `Dim A(Y)`, `Y`
    a variable -> `Variable illegal in Parameter: Y.`; `Public B As Long = Nope * 2` -> `Invalid
    symbol in expression: Nope * 2.` then `Variable initialization of B has bad syntax.`"""
    body = stmt.text.lstrip()
    if stmt.kind != "STRUCTUREMEMBER":
        body = body[len(body.split(None, 1)[0]):] if body else ""
    segments = _split_top(body)
    if len(segments) != len(stmt.vars):
        return None
    unsupported = empty_eq = False
    unsupported_at = None
    for index, (v, seg) in enumerate(zip(stmt.vars, segments)):
        if unsupported_at is not None:
            break  # CR300/CR350: an unsupported type ends the statement after that variable's checks
        name_at = seg.upper().find(v.name.upper())
        if name_at < 0:
            continue
        after_name = seg[name_at + len(v.name):]
        junk = re.match(r"\s+(?:[\w.\"]+\s*)+?(?=\()", after_name)
        if junk and not re.match(r"(?i)\s*as\b", after_name):
            after_name = after_name[junk.end():]  # `Public Resul  tString() As ...` (see below)
        empty_as = _EMPTY_DIMS_AS_RE.match(after_name)
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
                if index + 2 < len(stmt.vars):
                    diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                                stmt.file)
                return index
            continue
        if (stmt.kind in ("PUBLIC", "DIM") and v.type and v.type not in _VARIABLE_TYPES
                and v.type not in struct_types):
            m = _AS_TYPE_RE.search(seg)
            if m and m.group(1).upper() == v.type and not (profile.single_pass and unsupported):
                diags.error(stmt.line, f"Variable Type {m.group(1)} not supported", stmt.file)
                unsupported = True  # CR300/CR350 stop at the first one in a statement
                if profile.single_pass:
                    unsupported_at = index
        if v.type == "STRING" and v.string_size is not None and (
                m := _STRING_SIZE_RE.search(seg)) and _adjacent_literals(m.group(1)):
            # `Public S As String * 1  0` -> `Incomplete expression: 1  0.` (echo: everything after
            # the `*`, untrimmed), all four loggers (oracle, 2026-10-10); CR300/CR350 stop there
            diags.error(stmt.line, f"Incomplete expression: {m.group(1)}.", stmt.file)
            if profile.single_pass:
                if index + 2 < len(stmt.vars):
                    diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                                stmt.file)
                return index
        rest_at = name_at + len(v.name)
        rest = seg[rest_at:]
        if (junk := re.match(r"\s+(?:[\w.\"]+\s*)+?(?=\()", rest)) and not re.match(r"(?i)\s*as\b", rest):
            rest_at += junk.end()  # `Public A B(Nope)`: the words before the parenthesis are skipped
            rest = seg[rest_at:]
        if (m := _EMPTY_DIMS_INIT_RE.match(rest)) and not (profile.single_pass and empty_eq):
            # `Dim A() = {1,2}` -> `Invalid, or out of place expression:  =.` (echo: the blanks
            # after `)` and the `=`), all four loggers (oracle, 2026-10-10); CR300/CR350 stop there
            diags.error(stmt.line, f"Invalid, or out of place expression: {m.group(1)}=.", stmt.file)
            empty_eq = True  # CR300/CR350 report only the first
        typed_dims = _TYPED_DIMS_RE.match(rest)
        if v.dims and (rest.lstrip().startswith("(") or typed_dims):
            # `Public A(Nope)` and `Public A As Float(Nope)` alike (oracle, 2026-09-28)
            open_at = rest_at + (typed_dims.end() if typed_dims else len(rest) - len(rest.lstrip()))
            items = _group_items(seg, open_at)
            if items is not None and len(items) == len(v.dims) and (
                    stop_at is None or index < stop_at):
                bad_dim = False
                for d, text in zip(v.dims, items):
                    bad_dim |= _report_decl_expr(d, text.lstrip(), known, variables, profile, stmt, diags)
                if bad_dim and profile.single_pass:
                    # CR300/CR350 stop at the first bad variable; the one two on is named
                    # (oracle, 2026-10-04: `Public a(nv), b(nv), c` -> `Too many parameters:  c.`)
                    if index + 2 < len(stmt.vars):
                        diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                                    stmt.file)
                    return index
        if v.init and (stop_at is None or index < stop_at):
            eq = _split_top(seg, "=")
            if len(eq) != 2:
                continue
            init_text = eq[1]
            stripped = init_text.lstrip()
            if _adjacent_literals(init_text) and not stripped.startswith("{"):
                # `Public X As Long = 1 2` (the `As` clause swallowed the junk), as in
                # `_junk_init_findings`
                diags.error(stmt.line, f"Incomplete expression: {init_text.strip()}.", stmt.file)
                diags.error(stmt.line, f"Variable initialization of {v.name} has bad syntax.",
                            stmt.file)
                if profile.single_pass:
                    return index
                continue
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
                if profile.single_pass:
                    if index + 2 < len(stmt.vars):
                        diags.error(stmt.line, f"Too many parameters:  {stmt.vars[index + 2].name}.",
                                    stmt.file)
                    return index
    if unsupported_at is not None:  # (the unsupported variable's own dimension/initializer found nothing)
        if unsupported_at + 2 < len(stmt.vars):
            diags.error(stmt.line, f"Too many parameters:  {stmt.vars[unsupported_at + 2].name}.",
                        stmt.file)
        return unsupported_at
    return None


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
                       seen_alias_sources: dict[str, set[tuple]],
                       name_collision: str | None = None, profile=None) -> None:
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
    base = total = None
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
        if index_key is not None and dims is not None and 0 < len(index_key) <= len(dims):
            # a zero or negative declared size (itself `Illegally dimension`) counts as 1 (oracle,
            # 2026-10-03: `Public a(0)` / `Alias a(1)` compiles past the alias check, `a(2)` fails)
            dims = tuple(max(d, 1) for d in dims)
            total = 1
            for d in dims:
                total *= d
            # Fewer indices than dimensions are padded with 1s (oracle, all four loggers,
            # 2026-10-03: `Alias W(5)` on `W(4,5)` fails, `Alias W(2,4)` on `W(2,3,4)` fails,
            # `Alias W(1,4)` compiles).
            padded = (*index_key, *([1] * (len(dims) - len(index_key))))
            base = _alias_flat_offset(padded, dims)
            if not (1 <= base <= total):
                diags.error(stmt.line, "Illegally dimensioned aliased name", stmt.file)
    elif index_key == () and dim0_sizes.get(name_upper) is not None and all_dims.get(name_upper):
        dims = tuple(max(d, 1) for d in all_dims[name_upper])
        base, total = 1, 1
        for d in dims:
            total *= d

    first = stmt.args[1] if len(stmt.args) > 1 else None
    if total is not None and isinstance(first, S.Index) and len(first.args) != 1 \
            and not (getattr(stmt, "alias_item_junk", None) or [True])[0]:
        # `Alias Foo = Zed()` / `Zed(2,3)`: as for an empty-parenthesis target (oracle, round 9)
        diags.error(stmt.line, "Need closing )", stmt.file)
        diags.error(stmt.line, "Alias is illegally dimensioned.", stmt.file)
        return
    sizes = _alias_name_sizes(stmt, env) if total is not None else None
    cur = base
    if sizes is not None and not _alias_dim_ok(stmt, diags, cur, sizes[0], total, False):
        return
    predefined = bool(name_collision) and name_collision.endswith("predefined CONST.")
    if predefined:  # before `Duplicate Alias`, `already declared` after it (oracle, round 9)
        diags.error(stmt.line, name_collision, stmt.file)
    dedup_key = index_key
    dims = all_dims.get(name_upper)
    if index_key is not None and dims is not None and len(index_key) <= len(dims):
        # collisions are by row-major flat offset, a bare name being element 1 (oracle,
        # 2026-10-08: `grid(3,3)`: `grid(1,1)`/`grid(1)`/`grid`, `grid(1,2)`/`grid(2,-1)`); a
        # flat offset below 1 keeps the written tuple (`grid(1,-5)` and `grid(0,1)` do not collide)
        dims = tuple(max(d, 1) for d in dims)
        flat = _alias_flat_offset((*index_key, *([1] * (len(dims) - len(index_key)))), dims)
        if flat >= 1:
            dedup_key = (flat,)
    if dedup_key is not None:
        bucket = seen_alias_sources.setdefault(name_upper, set())
        if dedup_key in bucket:
            diags.error(stmt.line, "Duplicate Alias", stmt.file)
        else:
            bucket.add(dedup_key)
    if name_collision and not predefined:
        diags.error(stmt.line, name_collision, stmt.file)

    lead = _alias_name_lead(stmt)
    for k, rhs in enumerate(stmt.args[1:]):
        if sizes is not None and k:
            cur += sizes[k - 1]
            if not _alias_dim_ok(stmt, diags, cur, sizes[k], total):
                return
            if isinstance(rhs, S.Name) and profile and symbols.is_reserved_predefined_name(rhs.name, profile):
                diags.error(stmt.line, f"{rhs.name} is a predefined CONST.", stmt.file)
        if not (isinstance(rhs, S.Name) or (sizes is not None and isinstance(rhs, S.Index))):
            continue
        rhs_upper = rhs.name.upper()
        if name_collision and rhs is stmt.args[1] and not name_collision.endswith("predefined CONST."):
            continue  # a colliding first name is never registered as an alias
        if rhs_upper in seen_alias_names:
            # (the echo is the written name with its dimension and, after a comma, its blank)
            written = lead[k] if k < len(lead) else ("", rhs.name)
            diags.error(stmt.line, f"Duplicate Alias Name: {written[0]}{written[1]}.", stmt.file)
        else:
            seen_alias_names.add(rhs_upper)
    if sizes is not None and (stmt.text or "").rstrip().endswith(",") and \
            not 1 <= cur + sizes[-1] <= total:
        # a trailing comma is one more, empty, name
        diags.error(stmt.line, "Illegally dimensioned aliased name", stmt.file)


def _alias_name_sizes(stmt: S.Instruction, env: consteval.Env) -> list[int] | None:
    """Element count of each name of `Alias Var = n1, n2(3), ...` (a bare name is 1, `n(k)` is
    `int(k)`), or None when a name is not a bare/singly-dimensioned constant or has trailing junk
    (not probed; the dimension checks are then skipped)."""
    junk = getattr(stmt, "alias_item_junk", None)
    names = stmt.args[1:]
    if not names or junk is None or any(junk) or len(junk) != len(names):
        return None
    out = []
    for a in names:
        if isinstance(a, S.Name):
            out.append(1)
        elif isinstance(a, S.Index) and len(a.args) == 1 and a.args[0] is not None:
            try:
                v = consteval.value_of(a.args[0], env)
            except consteval.NotConstant:
                return None
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return None
            out.append(int(v))
        else:
            return None
    return out


def _alias_dim_ok(stmt: S.Instruction, diags: Diagnostics, cur: int, n: int, total: int,
                  check_start: bool = True) -> bool:
    """One name of an `Alias Var = n1, n2(3), ...` list starting at element `cur` of a variable of
    `total` elements (oracle, round 9, docs/checker.md "Alias name lists"): `Illegally
    dimensioned aliased name` when `cur` is outside 1..total (the first name's own is reported by
    the caller, `check_start` False), then `Alias is illegally dimensioned.` when `n` < 1 or, for
    `cur` >= 0, `n` > total + cur - 1; False after that second message (it ends the statement)."""
    if check_start and not 1 <= cur <= total:
        diags.error(stmt.line, "Illegally dimensioned aliased name", stmt.file)
    if n < 1 or (cur >= 1 and n > total + cur - 1):
        diags.error(stmt.line, "Alias is illegally dimensioned.", stmt.file)
        return False
    return True


def _alias_name_lead(stmt: S.Instruction) -> list[tuple[str, str]]:
    """`(blanks after the comma, written name)` for each name of the list (`Duplicate Alias Name:
    <blanks><name>.` echoes both, `Zed(2)` with its dimension); empty list when it cannot be split."""
    text = stmt.text or ""
    eq = text.find("=")
    if eq < 0:
        return []
    parts, depth, cur = [], 0, ""
    for ch in text[eq + 1:]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [("" if k == 0 else p[:len(p) - len(p.lstrip())], p.strip()) for k, p in enumerate(parts)]


def _after_keyword(stmt: S.Unparsed) -> str:
    """`stmt.text` (the raw, unstripped physical-line text, `parser.py`'s `_segment_text`) with
    its own leading whitespace and 5-letter keyword (`Units`/`Const`/`Alias`, all coincidentally
    5 characters) removed -- the echoed trailing text these "ran off the end of the line" messages
    share (empty in every corpus instance this session found)."""
    return stmt.text.lstrip()[5:].strip()


_JUNK_INIT_HEAD = re.compile(r"(?is)\s*(CONST|PUBLIC|DIM)\s+(.*)$")


def _junk_init_findings(stmt: S.Unparsed, profile) -> list[str]:
    """`Const X = 1 2` -> `Incomplete expression: 1 2.`; `Public X = 1 2, Y` -> that line, then
    `Variable initialization of X has bad syntax.` (echo: the trimmed value, or the first bad
    `{...}` item), all four loggers (oracle, 2026-10-10); CR6/CR1000X report every variable,
    CR300/CR350 the first. Pass 1 on all four, so the generic fallback does not follow."""
    head = _JUNK_INIT_HEAD.match(stmt.text)
    out: list[str] = []
    if stmt.text.lstrip().startswith("#"):
        # a statement starting with `#` (echo: the raw text, the line end as a newline on all four
        # loggers), pass 1 (oracle, 2026-10-10: `#1 ____`, `# 1`, ` #abc` after a `:`)
        return [f"Unknown instruction: {stmt.text}{chr(10) if stmt.at_eol else ''}."]
    if not head:
        return out
    const = head.group(1).upper() == "CONST"
    for seg in _split_top(head.group(2)):
        name = re.match(r"\s*([A-Za-z_]\w*)", seg)
        if not name or "=" not in seg:
            continue
        value = seg.split("=", 1)[1].strip()
        items = _split_top(value[1:-1]) if value.startswith("{") and value.endswith("}") else [value]
        bad = next((i.strip() for i in items if _adjacent_literals(i)), None)
        if bad is None:
            continue
        out.append(f"Incomplete expression: {bad}.")
        if not const:
            out.append(f"Variable initialization of {name.group(1)} has bad syntax.")
        if const or profile.single_pass:
            break
    return out


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
        # a name with no `=` at all adds `Too many parameters:  X.` (oracle, all four loggers,
        # 2026-10-10); `Const X =` is `Const bad value:` from the parser
        rest = _after_keyword(stmt)
        diags.error(stmt.line, "Const with no assignment.", stmt.file)
        if "=" not in rest and (named := re.match(r'\s*("[^"]*"|[A-Za-z_]\w*(?=\()|[^\s,]+)', rest)):
            # (`Const J(3)`: a subscript list is dropped, oracle 2026-10-16, docs/checker.md "Round 18")
            # the first item is read again as a surplus parameter (oracle, all four loggers,
            # 2026-10-10: `Const X Y`, `Const 1 2 3` -> `1`, `Const "a"`, `Const -1`)
            diags.error(stmt.line, f"Too many parameters:  {named.group(1)}.", stmt.file)
        return
    if stmt.reason == _ALIAS_NOTHING_AFTER_EQ:
        body = stmt.text.lstrip()[5:]
        target = body[:body.find("=")].lstrip()
        if "(" not in target:
            target = target.strip()
        diags.error(stmt.line, f"Nothing after '=' in alias definition: {target}.", stmt.file)
        return
    if stmt.reason.startswith(_DO_LOOP_PARAM):
        diags.error(stmt.line, stmt.reason[len(_DO_LOOP_PARAM):], stmt.file)
        return
    if stmt.reason.startswith(_CONST_LEAD_NAME):
        return  # `Const = X`: reported with the name checks (`_check_duplicate_declaration`)
    if stmt.reason.startswith(_CONST_LEAD):  # `Const = "a"`: messages joined by newlines
        for message in stmt.reason[len(_CONST_LEAD):].split("\n"):
            diags.error(stmt.line, message, stmt.file)
        return
    if stmt.reason.startswith(_CONST_BAD_NAME):
        diags.error(stmt.line, f"Const name {stmt.reason[len(_CONST_BAD_NAME):]} illegal, starts with a number.",
                    stmt.file)
        return
    if stmt.reason.startswith(_CONST_BAD_VALUE):
        diags.error(stmt.line, f"Invalid, or out of place expression: "
                    f"{stmt.reason[len(_CONST_BAD_VALUE):]}.", stmt.file)
        return
    if stmt.reason == _FOR_MISSING:
        diags.error(stmt.line, "Invalid or missing parameter.", stmt.file)
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
