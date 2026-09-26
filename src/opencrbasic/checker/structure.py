"""Structural checks: `BeginProg`/`EndProg` presence, `Scan`/`NextScan` and `If`/`Select` block
matching, undefined explicit `Call` targets, and the generic "not a valid statement" fallback for
a statement our parser could not turn into a recognised shape. Rules and oracle evidence:
`docs/checker.md`.
"""

from __future__ import annotations

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostics
from . import symbols

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
_BODY_BLOCK_STARTS = frozenset({
    "DIALSEQUENCE", "MODEMHANGUP", "MENU", "SUBMENU", "VOICE", "BURSTTRIGGER",
    "APPLYANDRESTARTSEQUENCE", "PROCHIPRI",
})
_BODY_BLOCK_ENDS = frozenset({
    "ENDDIALSEQUENCE", "ENDMODEMHANGUP", "ENDMENU", "ENDSUBMENU", "ENDVOICE", "ENDBURSTTRIGGER",
    "ENDAPPLYANDRESTARTSEQUENCE", "ENDPROCHIPRI",
})


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
    last_line = program.statements[-1].line if program.statements else 1
    # See `_check_declaration`'s docstring: the physical line right after a nameless `Public`/
    # `Dim` is never itself reported, even though it plainly failed to parse as anything valid.
    suppress_line: int | None = None

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

    if not seen_beginprog:
        diags.error(last_line, "No BeginProg in Program.")
        return
    if scan_depth > 0:
        diags.error(last_line, "Scan without NextScan.")
    if cond_depth > 0:
        diags.error(last_line, "IF or SELECT without EndIf or EndSelect.")


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
    """
    if env is None:
        env = consteval.Env(profile, dict(program.consts))
    subs = symbols.declared_subs(program)
    beginprog_line = next((s.line for s in program.statements if _keyword(s) == "BEGINPROG"), None)

    in_body_block = False
    for stmt in program.statements:
        if isinstance(stmt, S.SubDef):
            in_body_block = True
            continue
        kw = _keyword(stmt)
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
      BeginProg.` and a stop when it precedes `BeginProg`.
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
    diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file,
                show_file=False)
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
