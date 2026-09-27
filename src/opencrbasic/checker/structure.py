"""Structural checks: `BeginProg`/`EndProg` presence, `Scan`/`NextScan` and `If`/`Select` block
matching, undefined explicit `Call` targets, and the generic "not a valid statement" fallback for
a statement our parser could not turn into a recognised shape. Rules and oracle evidence:
`docs/checker.md`.
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import expressions, instr_data, symbols

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
# partway through its own grammar, `rectpolar_1.CR6`/`sprintf_4.CR6`); `Alias` echoes an (always
# empty, in this session's evidence) trailing target, like `Units`.
_CONST_WITHOUT_EQ = "Const without '='"
_ALIAS_WITHOUT_EQ = "Alias without '='"

# `parser.py`'s `_parse_vardecl` reason for a `Public`/`Dim` variable whose own type clause is
# malformed -- a token right after the name/dims that is neither `As`, `=`, a comma nor end of
# line, most often a corrupted spelling of `As` itself (`docs/fuzz-findings.md` "5. Message diff",
# `docs/checker.md` "Invalid Variable declaration"). The reason string carries the offending
# token's own text after this prefix.
_INVALID_AS_CLAUSE = "Invalid As clause: "

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

# Ordinary, recognised `Instruction`s that are oracle- *and* corpus-confirmed illegal at the
# top level before `BeginProg` (round 5, `docs/checker.md`): unlike the generic "Expr"/`Call`
# fallback above, these are real, declared instructions our parser resolves normally, so they
# would otherwise never reach any placement check at all. Found by fuzzing (`Scan`/`Delay`, both
# oracle-confirmed single-line messages, `%s illegally occurs before BeginProg.` with `%s` = the
# instruction's own proper-cased `data/<logger>/instructions.json` name, no preceding "Invalid, or
# out of place expression" line -- the statement itself compiles fine, only its position is
# illegal, the same shape a resolved bare function call or a resolved `Assignment` already gets).
# Deliberately **not** generalised to "every instruction with runtime behaviour" or any other
# broad rule: a 740-accepted-file corpus safety scan this round (`docs/checker.md` "Round 5") found
# dozens of instructions that legitimately *do* appear before `BeginProg` (everything inside a
# `DataTable`/`ConstTable`/`StructureType` body, plus program-wide directives such as
# `PipeLineMode`/`SequentialMode`/`ReadOnly`/`StationName`/`TimedControl`/`CH400Status`/
# `SDMBeginPort`), including two (`CH400Status`, `SDMBeginPort`) that share `Scan`/`Delay`'s own
# non-null `runtime_fn` marker in the extracted instruction table -- ruling out that marker as a
# safe discriminator. Only instructions with **both** a real oracle transcript *and* zero
# counter-examples in that same scan are included here; every other instruction is left alone
# (AGENTS.md: never guess into a false reject) rather than risk misclassifying one of the many
# still-unverified entries in `data/cr6/instructions.json`.
_FORBIDDEN_BEFORE_BEGINPROG = frozenset({"SCAN", "DELAY"})


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

    for stmt in program.statements:
        kw = _keyword(stmt)
        args = getattr(stmt, "args", []) or []

        fire_suppress = suppress_line is not None and stmt.line == suppress_line
        suppress_line = None

        if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
            if not stmt.vars:
                suppress_line = _check_declaration(stmt, diags)
            else:
                _check_array_dims(stmt, diags, env)
        elif fire_suppress:
            pass
        elif isinstance(stmt, S.Unparsed):
            _check_unparsed_pass1(stmt, diags)
        elif kw == "ALIAS" and isinstance(stmt, S.Instruction):
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
            if scan_depth == 0:
                diags.error(stmt.line, "NextScan with no Scan.", stmt.file)
            else:
                scan_depth -= 1
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            cond_depth += 1
        elif kw == "ENDSELECT" or (kw == "END" and _block_word(args) == "SELECT"):
            cond_depth = max(0, cond_depth - 1)
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
    if not seen_beginprog:
        diags.error(last_line, "No BeginProg in Program.")
        return
    if scan_depth > 0:
        diags.error(last_line, "Scan without NextScan.")
    if cond_depth > 0:
        diags.error(last_line, "IF or SELECT without EndIf or EndSelect.")
    # `DataTable ... EndTable not matched.` -- no line number at all (oracle-confirmed verbatim,
    # `docs/checker.md` "Round 6"), the lowest-priority of these four summary checks (oracle: a
    # co-occurring `Scan without NextScan.` reports only that, never this too) and, unlike the
    # other three, itself conditional on whether *anything else in the whole program* references
    # the unclosed table by name: when it does (any `CallTable <name>`, oracle-confirmed at that
    # `CallTable`'s own line, tier B not pass 1 -- accumulates with an unrelated `expressions.
    # check`/`structure.check_pass2` finding, unlike this bare message, which pass-1 errors alone
    # suppress), the real compiler instead reports `Could not find DataTable %s.` there
    # (`checker/instr_data.py`'s `check_unclosed_calltable`, tier B) and says nothing here at all.
    if not diags.errors and in_table and table_name is not None:
        if table_name.upper() not in instr_data.calltable_targets(program):
            diags.error(0, "DataTable ... EndTable not matched.")


def check_pass2(program: S.Program, profile, diags: Diagnostics,
                 env: consteval.Env | None = None) -> int | None:
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
    known = expressions._known_names(program, profile)
    beginprog_line = next((s.line for s in program.statements if _keyword(s) == "BEGINPROG"), None)

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
    # `IF or SELECT statement illegally has SCAN without NEXT SCAN.` (round 6, `docs/checker.md`):
    # not "an `If`/`Select` contains a `Scan`" by itself (a `Scan...NextScan` pair fully closed
    # *inside* the same `If`/`Select` block, textually, compiles clean, oracle-confirmed) but "a
    # `Scan` opened while this block was open is still open (no matching `NextScan` yet) when the
    # block's own `EndIf`/`EndSelect` is reached" -- a genuine nesting-depth comparison, not a
    # syntactic "contains" check. Modelled the same way `check_pass1` tracks `scan_depth`/
    # `cond_depth` for its own, coarser file-wide balance checks, but as a stack here: each open
    # `If`/`Select` remembers `scan_depth`'s own value at the moment it opened, and its own
    # `EndIf`/`EndSelect` compares the *current* `scan_depth` against that remembered value --
    # greater means an unclosed `Scan` from inside this block. Reported at the `EndIf`/`EndSelect`
    # line itself, oracle-confirmed regardless of how many `ElseIf`/`Else` segments sit in between
    # (`docs/checker.md` "Round 6": both fire at the same line as a plain `If...EndIf`). Pass 2
    # (oracle-confirmed: suppressed by an earlier pass-1 `Variable not named.`, but accumulates
    # freely with an unrelated pass-2 `Invalid symbol in expression` either side of it) -- tracked
    # unconditionally here, in the same top-of-loop bookkeeping section as `in_table`/
    # `in_beginprog` above, independent of `in_body_block`/`in_table` (no oracle evidence either
    # suppresses this specific check the way they do the "before `BeginProg`" ones).
    scan_depth = 0
    cond_stack: list[int] = []
    for stmt in program.statements:
        if isinstance(stmt, S.SubDef):
            in_body_block = True
            continue
        kw = _keyword(stmt)
        if kw == "DATATABLE":
            in_table = True
        elif kw == "ENDTABLE":
            in_table = False
        elif kw == "BEGINPROG":
            if in_beginprog:
                diags.error(stmt.line, "Multiple BeginProgs.", stmt.file)
            in_beginprog = True
        elif kw == "ENDPROG":
            in_beginprog = False
        elif kw == "SCAN":
            scan_depth += 1
        elif kw == "NEXTSCAN" or (kw == "NEXT" and _block_word(getattr(stmt, "args", []) or []) == "SCAN"):
            scan_depth = max(0, scan_depth - 1)
        elif kw == "SELECT" or (kw == "IF" and not (isinstance(stmt, S.If) and stmt.inline is not None)):
            cond_stack.append(scan_depth)
        elif kw == "ENDSELECT" or (kw == "END" and _block_word(getattr(stmt, "args", []) or []) == "SELECT"):
            if cond_stack and scan_depth > cond_stack.pop():
                diags.error(stmt.line, "IF or SELECT statement illegally has SCAN without NEXT SCAN.", stmt.file)
        elif kw == "ENDIF" or (kw == "END" and _block_word(getattr(stmt, "args", []) or []) == "IF"):
            if cond_stack and scan_depth > cond_stack.pop():
                diags.error(stmt.line, "IF or SELECT statement illegally has SCAN without NEXT SCAN.", stmt.file)
        if kw in _BODY_BLOCK_STARTS:
            in_body_block = True
            continue
        if kw in ("ENDSUB", "ENDFUNCTION") or kw in _BODY_BLOCK_ENDS:
            in_body_block = False
            continue

        if isinstance(stmt, S.Call):
            stop = _check_call_pass2(stmt, subs, profile, diags, beginprog_line, in_body_block)
            if stop is not None:
                return stop
        elif isinstance(stmt, S.Assignment):
            stop = _check_assignment_pass2(stmt, known, diags, beginprog_line, in_body_block)
            if stop is not None:
                return stop
        elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() in _FORBIDDEN_BEFORE_BEGINPROG:
            if not in_body_block and not in_table and (beginprog_line is None or stmt.line < beginprog_line):
                record = profile.instructions.get(stmt.keyword.upper())
                display = record["name"] if record else stmt.keyword
                diags.error(stmt.line, f"{display} illegally occurs before BeginProg.", stmt.file)
                return stmt.line
        elif isinstance(stmt, S.Unparsed):
            if stmt.reason.startswith(_KNOWN_PARSER_GAP_REASONS) or stmt.reason in _BENIGN_REASONS:
                continue
            if stmt.reason in (_UNITS_WITHOUT_EQ, _CONST_WITHOUT_EQ, _ALIAS_WITHOUT_EQ) or \
                    stmt.reason.startswith(_INVALID_AS_CLAUSE):
                continue  # already reported by `check_pass1`
            diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file,
                        show_file=False)
            if not in_body_block and (beginprog_line is None or stmt.line < beginprog_line):
                diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
                return stmt.line
    return None


def _check_call_pass2(stmt: S.Call, subs: set[str], profile, diags: Diagnostics,
                       beginprog_line: int | None, in_body_block: bool) -> int | None:
    """One `Call`-shaped statement's pass-2 check. Returns the line to stop pass 2 at, or `None`
    to keep going. Oracle-confirmed shapes (`docs/checker.md` "Pass model"), each independently
    verified this round:

    - `Call NoSuchSub` (explicit keyword, undefined): `Sub %s not defined.`, **no** placement
      follow-up and **no** stop, before `BeginProg` or not (`show_file=False`: oracle-confirmed,
      `docs/checker.md` "Errors from an included file" -- this message never gets the `<file>`
      prefix either).
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
    before_beginprog = not in_body_block and (beginprog_line is None or stmt.line < beginprog_line)
    if stmt.explicit:
        if name not in subs:
            diags.error(stmt.line, f"Sub {stmt.name} not defined.", stmt.file, show_file=False)
        return None
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
        diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file,
                    show_file=False)
        return None
    diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file,
                show_file=False)
    if before_beginprog:
        diags.error(stmt.line, "Expr illegally occurs before BeginProg.", stmt.file)
        return stmt.line
    return None


def _check_assignment_pass2(stmt: S.Assignment, known: set[str], diags: Diagnostics,
                             beginprog_line: int | None, in_body_block: bool) -> int | None:
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
    before_beginprog = not in_body_block and (beginprog_line is None or stmt.line < beginprog_line)
    if expressions._undefined_names(stmt.target, known):
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
            try:
                val = consteval.value_of(d, env)
            except consteval.NotConstant:
                continue
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            if not (1 <= val <= _MAX_DIM_VALUE):
                diags.error(stmt.line, f"Illegally dimension: {stmt.text}", stmt.file)


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
    if stmt.reason == _CONST_WITHOUT_EQ:
        diags.error(stmt.line, "Const with no assignment.", stmt.file)
        return
    if stmt.reason == _ALIAS_WITHOUT_EQ:
        # See `parser.py`'s `_parse_alias` for the one known-imprecise sub-case (a target present
        # but no `=`) this doesn't try to echo exactly.
        diags.error(stmt.line, f"Need '=' in alias definition: {_after_keyword(stmt)}.", stmt.file)
        return
    if stmt.reason.startswith(_INVALID_AS_CLAUSE):
        bad_token = stmt.reason[len(_INVALID_AS_CLAUSE):]
        diags.error(stmt.line, f"Invalid Variable declaration {bad_token}.", stmt.file)
        return
    # Any other reason (including the generic "not a valid statement" shape) is `check_pass2`'s
    # job, not this pass's -- deliberately does nothing further here.
