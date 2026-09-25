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


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env | None = None) -> None:
    subs = symbols.declared_subs(program)
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
        elif isinstance(stmt, S.Call):
            _check_call(stmt, subs, profile, diags)
        elif isinstance(stmt, S.Unparsed):
            _check_unparsed(stmt, diags)

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


def _check_call(stmt: S.Call, subs: set[str], profile, diags: Diagnostics) -> None:
    name = stmt.name.upper()
    if name in subs or name in profile.functions:
        # A declared `Sub`/`Function` (oracle: docs/checker.md, `MySub(5)`) or a built-in
        # *function* (`Timer`, `SerialOpen`, ... -- `data/functions.json`, not the 410-entry
        # instruction table) called for its side effect with the return value discarded: real,
        # working code in this repository's own `Main.CR6` does this throughout.
        return
    if stmt.explicit:
        # `Call NoSuchSub`: oracle-confirmed (docs/type-codes.md section 3). `show_file=False`:
        # oracle-confirmed this session (`docs/checker.md` "Errors from an included file") that
        # this specific message never gets the `<file>` prefix other errors from an included
        # statement do, even when the undefined `Call` itself is inside one.
        diags.error(stmt.line, f"Sub {stmt.name} not defined.", stmt.file, show_file=False)
        return
    # A bare `Name` or `Name(args)` with no `Call` keyword, resolving to nothing declared: the
    # real compiler's generic bare-expression-statement fallback ("Expr" in its instruction
    # table) rejects it with this message, echoing the statement's own source text (oracle:
    # docs/checker.md, `NOTE` / `SWVX(Vx1,1,1)` / `TableFieldNames(...)`-shaped corpus cases).
    # `show_file=False`: see the note on the `Sub %s not defined.` branch above -- the same
    # oracle-confirmed exception applies to this message too.
    diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file,
                show_file=False)


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


def _check_unparsed(stmt: S.Unparsed, diags: Diagnostics) -> None:
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
    # `show_file=False`: the same oracle-confirmed exception as `_check_call`'s identical fallback
    # message -- see that function's comment.
    diags.error(stmt.line, f"Invalid, or out of place expression: {stmt.text}.", stmt.file,
                show_file=False)
