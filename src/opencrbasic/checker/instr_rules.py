"""Instruction-specific checks that are not channel/port related (that logic lives in
`checker/instructions.py`, a different workstream, `PLAN.md`). A required argument position with
no corresponding value at all (`check`, pass-1, gated normally through `checker/__init__.py`), plus
a small set of *ungated* `Scan`/`DataInterval` numeric-value checks (`check_scan_params`,
`check_data_interval`, called from `checker/warnings.py`'s `check_early` -- oracle-confirmed to
fire even on an otherwise-failed compile, same as that module's own ungated warnings; see its
module docstring). Rules and oracle evidence: `docs/checker.md` (the required-parameter check) and
`docs/instructions.md` (the numeric-value checks).
"""

from __future__ import annotations

import re
import struct

from .. import consteval
from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from . import symbols
from .expressions import _arg_text, _known_names, _type_code_kinds, _undefined_names, _variable_refs

# `type_codes.json` codes exempt from the "missing required parameter" error: when the
# statement's own physical line runs out of tokens right at one of these, `FUN_004baf00` silently
# fills it (and everything declared after it) with a default/sentinel value and ends that
# statement's own parameter parsing with no diagnostic at all, rather than reporting anything --
# `docs/type-codes.md` code 4's row ("this code (and 16, 17, 36, 37, 45, 46) is exempt from the
# loop-level `Too few parameters in instruction.` check"); this is what lets a bare `Do`/`Loop`/
# `Else` (code 4, no trailing `While`/`Until` condition), bare `End`/`Exit` (16), and bare `Next`
# (17) all compile with zero declared arguments even though their own table row lists the code as
# non-optional (code < 100). 36/45/46 are listed here too for documentation fidelity to that
# passage, but never reach this check in practice: `_real_params` below already drops them (they
# are `none`-kind, not real argument slots at all).
_TOLERANT_CODES = frozenset({4, 16, 17, 36, 37, 45, 46})

# --- Required parameter cut off by an unterminated string: also `Invalid or missing parameter.` -

# A required argument that *starts* (a `"` was seen) but never closes before the physical line
# ends never reaches `check` above at all: the frontend's own lexer raises `LexError("unterminated
# string")` on that whole line before a statement can be built, so `parser.py` records it as
# `S.Unparsed(reason="lex error: unterminated string")`, not an `S.Instruction` -- `check` above
# only walks `S.Instruction` nodes and silently skips it. Oracle-confirmed (`httpout_1.CR6`/
# `webpagebeginwebpageend_1.CR6`, both CHM corpus, and a standalone scratch probe reproducing the
# same shape byte-for-byte): `HTTPOut("<title>CR1000X` (opening quote, no closing quote before the
# newline) reports `Invalid or missing parameter.` at that statement's own line, nothing else --
# not the continuation line the unterminated string spills onto (`Datalogger</title>")`, which the
# real compiler reports nothing for at all (consistent with the already-implemented pass-1/pass-2
# gate in `checker/__init__.py`: this is itself a pass-1 error, so pass 2's generic `Unparsed`-
# >`Expr` fallback -- the source of this project's own previous, wrong `Invalid, or out of place
# expression: HTTPOut(...` -- never runs for the rest of the file, silencing the continuation line
# for free, the same way the count-shortfall check above already relies on that gate).
#
# **Deliberately narrow, keyed to the argument's type-code `kind`, not "any known instruction"**:
# a same-shaped probe against `If <cond> Then Test = "TEMP` (`call_1.CR6`, also in the CHM corpus)
# gave a *different* real message (`String without end quote`) for its own unterminated string,
# even though `IF` is a `profile.instructions` entry too (`params=[7]`, kind `bool_expr`) -- so
# "known instruction" alone is not the trigger. `docs/type-codes.md` documents `bool_expr` (7) and
# `expr_or_name` (26, `HTTPOut`'s own single param) as reaching the shared per-code parameter
# resolver through two *different* switch cases (`_caseD_7` vs `_caseD_19`); this check fires only
# when the statement's first declared parameter is `expr_or_name` (the confirmed case), not
# generalised to every `kind` the shared resolver accepts -- no oracle evidence covers the others,
# and AGENTS.md rules out guessing at the rest.

_LEADING_IDENT_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)")

# --- Required parameter missing, `identifier`/`deferred_text` kind: `Too few parameters in
# instruction.` instead of `Invalid or missing parameter.` -------------------------------------

# `docs/type-codes.md` section 4 left the trigger for this, the loop-level "b4" guard in
# `FUN_004baf00`, as an open question ("every multi-parameter instruction tried instead hit a more
# specific check inside the parameter's own handler first"); pinned down this round by the same
# oracle method, four minimal probes plus corpus cross-checks (`docs/checker.md` has the full
# evidence table):
#
# - `Battery` bare (code 8, `variable_or_array`) -> `Invalid or missing parameter.`
# - `Average` bare (code 1, `const_expr`) -> `Invalid or missing parameter.` (also the CHM
#   corpus's own `average_1.CR6`, cross-line continuation, oracle-confirmed same session)
# - `CallTable` bare (code 6, `identifier`) -> `Too few parameters in instruction.`
# - `Histogram` bare (code 11, `deferred_text`) -> `Too few parameters in instruction.`
#
# and confirmed against the CHM corpus's own `openinterval_1.CR6` (`DataTable` bare, code 6) and
# `etsz_1.CR6` (`ETsz` bare, code 11), both `Too few parameters in instruction.`, and
# `writeio_1.CR6` (`CallTable` bare, code 6) the same. The generic per-code expression/constant
# resolver (`FUN_004b5c50`/`FUN_004b8700`, every other kind) has its own "nothing here" message and
# never falls through to the loop-level guard; `identifier` and `deferred_text` are instead
# captured via a raw `strtok` copy (`docs/type-codes.md` codes 6/11's own "Accepted forms" column)
# with no such per-code "missing" check of its own, so an empty token there reaches the top-of-loop
# guard uncaught by anything more specific -- the two kinds share this one property (a bare
# `strtok`, no dedicated handler) that every other kind lacks, which is why exactly these two, and
# not e.g. `raw_line_text` (18, `MenuPick`) or `type_name` (45, `StructureType`/`ObjectType`, also
# raw-`strtok`-captured per the same column) are included here: no oracle evidence covers those
# two, and AGENTS.md rules out extending the guess to them.
#
# Does **not** explain every CHM corpus row with this message (`tablenamerecord_1.CR6`'s
# `Main.record(1,1)`, `realtime_1.CR6`'s later lines): those sit downstream of an earlier,
# already-diverging recovery this project's own parser takes differently from the real compiler
# for unrelated reasons (confirmed by isolating `RecData = Main.record(1,1)` alone, which compiles
# clean here -- the corpus file's own divergence starts earlier), not a second, distinct trigger
# for this message; those files stay `verdict`, not claimed fixed.
_TOO_FEW_PARAMS_KINDS = frozenset({"identifier", "deferred_text"})


_DECLARATION_WORDS = frozenset({"PUBLIC", "DIM", "STATIC", "CONST", "ALIAS", "UNITS", "READONLY",
                                "SUB", "FUNCTION"})


def unterminated_string_shape(stmt: S.Stmt, profile, subs: set[str]) -> str | None:
    """How a line cut short by an unterminated string is reported (oracle, all four loggers,
    2026-09-28, `docs/checker.md` "Unterminated strings"): `"if"` (a one-line `If`), `"call"` (a
    `Sub` call), `"instruction"`, `"assignment"`, or `None` (left to the generic fallback)."""
    if not isinstance(stmt, S.Unparsed) or stmt.reason != "lex error: unterminated string":
        return None
    m = _LEADING_IDENT_RE.match(stmt.text)
    word = m.group(1).upper() if m else ""
    if word == "IF":
        return "if" if re.search(r"(?i)\bThen\b\s*\S", stmt.text) else None
    if word == "CALL" or word in subs:
        return "call"
    record = profile.instructions.get(word)
    if word in _DECLARATION_WORDS:
        return None  # `Public</a></li></ul>")` in `httpout_1.CR6` is not reported
    if record:
        params = _real_params(record["params"])
        kind = _type_code_kinds().get(params[0] % 100) if params else None
        return "instruction" if params and kind != "bool_expr" else None
    if re.match(r"\s*[A-Za-z_][A-Za-z0-9_]*\s*(?:\([^\"]*\))?\s*=\s*\"", stmt.text):
        return "assignment"
    return None


def _check_unterminated_string_param(program: S.Program, profile, diags: Diagnostics) -> None:
    """Pass 1: `HTTPOut("<title>CR1000X` / `Battery("abc` -> `Invalid or missing parameter.`;
    `Foo("abc` -> `Invalid or malformed function argument.`; `S = "abc` -> `String without end
    quote` then the fallback echoing the raw line; `If X Then S = "abc` -> `String without end
    quote` then the fallback for `S = "abc:endif` (the compiler's block-form rewrite)."""
    from . import structure, symbols
    subs = symbols.declared_subs(program)
    for stmt in program.statements:
        shape = unterminated_string_shape(stmt, profile, subs)
        if shape == "instruction":
            diags.error(stmt.line, "Invalid or missing parameter.", stmt.file)
        elif shape == "call":
            diags.error(stmt.line, "Invalid or malformed function argument.", stmt.file)
        elif shape == "assignment":
            diags.error(stmt.line, "String without end quote", stmt.file)
            diags.error(stmt.line, structure._out_of_place(profile, stmt.text, stmt.at_eol),
                        stmt.file, show_file=False)
        elif shape == "if":
            rest = re.search(r"(?i)\bThen\b\s*(.*)$", stmt.text).group(1)
            diags.error(stmt.line, "String without end quote", stmt.file)
            diags.error(stmt.line, f"Invalid, or out of place expression: {rest}:endif.",
                        stmt.file, show_file=False)


def _real_params(params: list[int]) -> list[int]:
    """`params` with every `none`-kind code removed (14 `EndSub`/`EndFunction`, 32
    `RainFlowSample`'s first slot, 36 `EndSelect`, 46 `EndStructureType`/`EndObjectType`,
    `docs/type-codes.md`) -- these are structural markers in the raw per-instruction table that
    consume no user-written token at all, so counting them as "required" would flag a legitimately
    argument-less statement (`EndSub` alone), and leaving one in the list at all would shift every
    later position out of alignment with the parsed `stmt.args` (found as a regression while
    developing this check: bare `EndSub`, `params=[14]`, was flagged as one required argument
    short; `RainFlowSample`'s real first user argument is its own table position 1, code 11, after
    the position-0 no-op marker, code 32)."""
    kinds = _type_code_kinds()
    return [c for c in params if kinds.get(c - 100 if c >= 100 else c) != "none"]


def _required_count(params: list[int]) -> int:
    """How many leading `params` entries are non-optional (code < 100). `FUN_004baf00`'s own
    rule -- "All arguments after the first optional parameter must be optional." -- means the
    first optional (code >= 100) entry marks where the required prefix ends; the CHM param tables
    never observed one out of order, and `data/instructions.json` is table-driven so this holds by
    construction."""
    for i, code in enumerate(params):
        if code >= 100:
            return i
    return len(params)


def misplaced_returns(program: S.Program) -> dict[int, bool]:
    """`id(Return statement) -> inside a Sub` for each `Return` not inside a `Function`, one-line
    `If` bodies included."""
    found: dict[int, bool] = {}
    routine = None
    flat: list[S.Stmt] = []
    for stmt in program.statements:
        flat.append(stmt)
        if isinstance(stmt, S.If):
            flat.extend((stmt.inline or []) + (stmt.inline_else or []))
    for stmt in flat:
        if isinstance(stmt, S.SubDef):
            routine = stmt.keyword.upper()
        elif isinstance(stmt, S.Instruction):
            kw = stmt.keyword.upper()
            if kw in ("ENDSUB", "ENDFUNCTION") or (kw == "END" and stmt.args and isinstance(
                    stmt.args[0], S.Name) and stmt.args[0].name.upper() in ("SUB", "FUNCTION")):
                routine = None
            elif kw == "RETURN" and routine != "FUNCTION":
                found[id(stmt)] = routine == "SUB"
    return found


def check_return_placement(program: S.Program, profile, diags: Diagnostics) -> None:
    """`Return` outside a `Function`, pass 1, all four loggers (oracle, 2026-10-03): `Return
    outside of Function.` (any context but a `Sub`, `DataTable` and `BeginProg` bodies included),
    then, for any argument, `Too many parameters:  <first argument token>.` (the instruction
    takes none there). In a `Sub` only `RETURN illegal inside Sub` (no period) and only with an
    argument; a bare `Return` there is still `Invalid or missing parameter.` (`check`)."""
    misplaced = misplaced_returns(program)
    for stmt in _return_statements(program):
        in_sub = misplaced.get(id(stmt))
        if in_sub is None:
            continue
        if in_sub:
            if stmt.args:
                diags.error(stmt.line, "RETURN illegal inside Sub", stmt.file)
            continue
        diags.error(stmt.line, "Return outside of Function.", stmt.file)
        spans = getattr(stmt, "arg_spans", None)
        if stmt.args and spans and spans[0] is not None:
            token = _next_simple_token(stmt.text[spans[0][0]:])
            if token:
                diags.error(stmt.line, f"Too many parameters:  {token}.", stmt.file)


def _return_statements(program: S.Program):
    for stmt in program.statements:
        for st in [stmt] + ((stmt.inline or []) + (stmt.inline_else or []) if isinstance(stmt, S.If) else []):
            if isinstance(st, S.Instruction) and st.keyword.upper() == "RETURN":
                yield st


def check(program: S.Program, profile, diags: Diagnostics) -> None:
    """Flags an `Instruction` statement whose declared required-parameter prefix runs past the end
    of the parsed argument list -- almost always a CHM corpus example whose instruction call was
    wrapped across two physical lines with no `_` continuation (`docs/checker.md`: `Average` alone
    on one line, `(1,TCTemp,FP2,False)` on the next -- our own tolerant "auto-close an unclosed `(`
    at end of line" parsing, `docs/frontend.md`, silently produces a *short* argument list rather
    than a syntax error, unlike the real compiler, which reports the gap and abandons the rest of
    that statement's own parsing there).

    Deliberately checks only a *shortfall in count* (`len(stmt.args) < required`), never a `None`
    gap from a skipped comma in the middle of the list -- no oracle evidence this session found
    supports flagging that shape, and AGENTS.md's priority (never reject an accepted program) rules
    out guessing.

    The message itself depends on the missing parameter's own `kind` -- see
    `_TOO_FEW_PARAMS_KINDS`'s docstring above for the oracle evidence: `identifier` (6) and
    `deferred_text` (11) report `Too few parameters in instruction.`, every other kind the
    original `Invalid or missing parameter.`.

    This is pass-1 work (`checker/__init__.py`'s docstring): it fires regardless of what other
    errors the file already has. It does *not* need to separately suppress
    `checker/instructions.py`'s own per-instruction checks (channel ranges etc.) for the statement
    it flags -- `checker/__init__.py` already gates all of pass 2 behind "no pass-1 error anywhere
    in the file" (oracle evidence there), which covers this case for free.
    """
    outside = {k for k, in_sub in misplaced_returns(program).items() if not in_sub}
    known = _known_names(program, profile)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or id(stmt) in outside:
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        params = _real_params(record["params"])
        required = _required_count(params)
        if len(stmt.args) >= required:
            continue
        missing_code = params[len(stmt.args)]
        if missing_code in _TOLERANT_CODES:
            continue
        if profile.name in ("cr6", "cr1000x"):
            # an unresolved name in an argument other than a destination (8/27/29) or a code 28
            # is reported as `Invalid symbol`, and the shortfall is not (oracle, 2026-10-10)
            kinds = _type_code_kinds()
            if any(a is not None and params[i] % 100 not in (8, 27, 28, 29)
                   and kinds.get(params[i] % 100) in _TOO_MANY_SAFE_KINDS
                   and _undefined_names(a, known) for i, a in enumerate(stmt.args[:len(params)])):
                continue
            _report_constant_destinations(stmt, record, params, program, profile, diags)
        kind = _type_code_kinds().get(missing_code - 100 if missing_code >= 100 else missing_code)
        if kind in _TOO_FEW_PARAMS_KINDS:
            diags.error(stmt.line, "Too few parameters in instruction.", stmt.file)
        else:
            diags.error(stmt.line, "Invalid or missing parameter.", stmt.file)
    _check_unterminated_string_param(program, profile, diags)


def shortfall_message(record: dict, given: int) -> str | None:
    """The message for a statement of `given` arguments that is short of `record`'s required
    parameters (the rule `check` applies to a parsed `S.Instruction`), else None."""
    params = _real_params(record["params"])
    if given >= _required_count(params) or params[given] in _TOLERANT_CODES:
        return None
    kind = _type_code_kinds().get(params[given] - 100 if params[given] >= 100 else params[given])
    return "Too few parameters in instruction." if kind in _TOO_FEW_PARAMS_KINDS \
        else "Invalid or missing parameter."


def _report_constant_destinations(stmt: S.Instruction, record: dict, params: list, program,
                                  profile, diags: Diagnostics) -> None:
    """CR6/CR1000X, a statement short of required parameters: the arguments given are still
    examined first, so a constant at a destination-variable position (`PanelTemp(15000)`,
    `CDM_PanelTemp(CDM_A108,1,1,1,15000)`) gives `Invalid Variable or Array: <arg>.` ahead of
    `Invalid or missing parameter.` (oracle, 2026-10-03, `tests/fixtures/short_constant_dest.json`).
    The checks are those `instr_data._check_stage2a` and `check_precision_variable` make for a
    complete statement, which pass 1 now suppresses for this one."""
    from . import instr_data
    if stmt.keyword.upper() in _PRECISION_VARIABLE_POSITIONS:
        known = _known_names(program, profile)
        variables = symbols.declared_variable_names(program)
        if stmt.args and stmt.args[0] is not None and not (
                _undefined_names(stmt.args[0], known) or _variable_refs(stmt.args[0], variables)):
            diags.error(stmt.line, f"Invalid Variable or Array: {_arg_text(stmt, 0)}.", stmt.file)
        return
    for i, code in enumerate(params[:len(stmt.args)]):
        arg = stmt.args[i]
        if code in (8, 27, 29) and arg is not None and not isinstance(arg, S.String) \
                and not instr_data._has_names(arg):
            diags.error(stmt.line, f"Invalid Variable or Array: {_arg_text(stmt, i).strip()}.",
                        stmt.file)


# --- Too many parameters: `Too many parameters:  %s.` (round 5, new this round) -----------------

# `docs/type-codes.md` `kind`s eligible for `check_too_many` below: every `kind` whose own argument
# is always exactly one ordinary, comma-delimited token compiled through the generic per-code
# expression/constant/keyword resolver -- the same shared assumption `expressions.py`'s
# `_RESOLVED_KINDS` makes for a *different* purpose (name resolution), deliberately narrower here.
# **Not** included, each for its own oracle/corpus-evidenced reason a corpus safety scan (`docs/
# checker.md` "Round 5") found necessary:
#
# - `identifier` (6), `deferred_text` (11), `raw_line_text` (18), `expr_marker` (37), `type_name`
#   (45): raw-text-capturing codes whose own instruction (`Units`, `ConstTable`, `MenuPick`, ...)
#   may swallow embedded commas as part of *one* declared slot's own text (`Units X = Volts` is one
#   code-6 argument to the real compiler, never comma-split) -- comparing this checker's own,
#   always-comma-split `stmt.args` length against the declared code count for these would flag
#   real, oracle-accepted programs as having "too many" parameters purely from this checker's own
#   parsing convention, not a real excess (found as 631 `Alias`/534 `Units`/35 `MenuPick`/17
#   `ConstTable` false hits on this project's own *accepted* `crbasic/`+`chm` corpus before this
#   exclusion was added).
# - `sub_params` (12/35), `for_header` (3), `expr_list` (13): declaration/loop-header/call-argument
#   grammars with their own, already-implemented parsing (`Sub`/`Function`/`FOR`/`Call`), never
#   reached via a plain `S.Instruction`'s `args` list at all (`Call` parses to `S.Call`, `Sub`/
#   `Function` to `S.SubDef`, `FOR` to `S.For` -- `checker/structure.py`'s own AST node types), so
#   excluding them here is defensive rather than corpus-evidenced.
# Instructions checked although a parameter kind above excludes them, each oracle-probed on all
# four loggers (2026-09-27/28): `DataTable (T,1,-1,"fuzz")`, `CallTable(T,X,Y)`, and table output
# with its `deferred_text` source, `Minimum (1,X,FP2,False,False,"fuzz")` -> `Too many parameters:
# "fuzz".`, `SendTableDef(ComRS232,0,4094,T,(1+2))` -> `... 1+2.`.
_TOO_MANY_EXTRA = frozenset({"DATATABLE", "CALLTABLE", "SAMPLE", "AVERAGE", "MAXIMUM", "MINIMUM",
                             "TOTALIZE", "STDDEV", "MEDIAN", "SENDTABLEDEF", "ESSINITIALIZE",
                             "WINDVECTOR"})

# Parameterless instructions (`params == []`): any argument is `Too many parameters:  <first token>.`
# (oracle, CR6/CR1000X, 2026-10-10, each probed as `Name junk` and `Name(1)`). Not `EndIf`/
# `EndSelect` (block-closer messages come with it), nor the custom-parsed `Const`/`Data`/`Alias`/...
ZERO_PARAM_INSTRUCTIONS = frozenset({
    "BEGINPROG", "ENDPROG", "NEXTSCAN", "NEXTSUBSCAN", "EXITSCAN", "CONTINUESCAN", "EXITDO",
    "EXITFOR", "EXITSUB", "ABORTSCAN", "PRESERVEVARIABLES", "PROCHIPRI", "ENDPROCHIPRI", "LEDOFF",
    "CARDFLUSH", "OPENINTERVAL", "TABLEHIDE", "RESTORE", "PIPELINEMODE", "ANGLEDEGREES", "WEND",
    "RESTART", "DEBUGBREAK", "ALLOWSPECIALCOMMS", "PULSECOUNTRESET"})

_TOO_MANY_SAFE_KINDS = frozenset({
    "const_expr", "expr", "const_expr_only", "bool_expr", "variable_or_array", "expr_or_name",
    "expr_typed", "expr_special", "expr_pair", "datatype_kw", "const_int_kw", "next_kw",
    "end_exit_kw",
})

# `FUN_004baf00`'s own "end of a simple token" delimiter set (`docs/type-codes.md` section 1,
# `FUN_004ba440`): comma, colon, both parens, space, `=`, newline, tab, `>`. The "too many
# parameters" message's own echo uses *this* flat tokenizer, not the depth-aware one a normal
# argument boundary is parsed with (`FUN_004b9700`) -- oracle-confirmed, `docs/checker.md` "Round
# 5": an extra, fully-parenthesised argument (`(1+2)`) echoes as `1+2`, its own leading `(`
# consumed as a delimiter (the *skip-leading-delimiters* step) and its first inner `)` ending the
# token, the nested nesting being coincidental rather than honoured; `Battery(X,X,X)` (two bare
# extra `X`s) echoes only the *first* one, `X` -- confirming the echo is a single next-token
# lookup, not the whole remaining comma list, matching `docs/type-codes.md`'s own worked example.
_TOO_MANY_DELIMS = frozenset(",:() =\n\t>")


def _next_simple_token(text: str) -> str:
    i, n = 0, len(text)
    while i < n and text[i] in _TOO_MANY_DELIMS:
        i += 1
    start = i
    while i < n and text[i] not in _TOO_MANY_DELIMS:
        i += 1
    return text[start:i]


def _too_many_eligible(params: list[int]) -> bool:
    """`params` (the raw, un-filtered declared code list) is eligible for `check_too_many` only
    when it is non-empty and every code's `kind` is either `_TOO_MANY_SAFE_KINDS` or `none`
    (`_real_params` already strips `none`-kind codes for the actual count, but a `none` code does
    not itself disqualify the surrounding instruction). An **empty** `params` list (`Alias`,
    `Units`, `Data`, `EssVariables`, oracle/corpus-evidenced above) is never eligible: it means this
    project's own extracted table has no per-code model for the instruction's arguments at all --
    real, custom parsing the generic per-code loop this check models does not apply to.
    """
    if not params:
        return False
    kinds = _type_code_kinds()
    for code in params:
        base = code - 100 if code >= 100 else code
        kind = kinds.get(base)
        if kind != "none" and kind not in _TOO_MANY_SAFE_KINDS:
            return False
    return True


_RAW_TEXT_KINDS = frozenset({"identifier", "deferred_text", "raw_line_text", "expr_marker", "type_name",
                             "sub_params", "for_header", "expr_list"})


def plain_expression_params(params: list[int]) -> bool:
    """No parameter reads raw text (so every comma-separated chunk is an ordinary expression)."""
    kinds = _type_code_kinds()
    return bool(params) and not any(kinds.get(c % 100) in _RAW_TEXT_KINDS for c in params)


def check_too_many(program: S.Program, profile, diags: Diagnostics) -> None:
    """`Too many parameters:  %s.` (note the two spaces before `%s`, oracle-confirmed verbatim,
    `docs/type-codes.md`) when an `Instruction` statement's parsed argument list runs *past* its
    declared parameter count -- the mirror image of `check` above (a shortfall), and, like it, pass
    1 (unconditional -- oracle-confirmed this round, `docs/checker.md` "Round 5": fires alongside an
    earlier, unrelated `Variable not named.` in the same file, the same "ungated" shape `check`
    already has). Deliberately restricted to instructions whose declared codes are *all*
    `_too_many_eligible` -- see that function and `_TOO_MANY_SAFE_KINDS` for the corpus-evidenced
    reasoning; this is the check's actual safety margin, not the echo logic below.

    The echoed text is the *next* token by the real compiler's own flat, non-depth-aware tokenizer
    (`_next_simple_token`) starting at the first excess argument's own raw source position
    (`Instruction.arg_spans`) -- not a re-derived or whole-remaining-text form. A missing span
    (an empty/skipped argument slot with no captured position) is left unflagged rather than
    guessed at.
    """
    outside = {k for k, in_sub in misplaced_returns(program).items() if not in_sub}
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or id(stmt) in outside:
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        # `DataTable (T,1,-1,"fuzz")` -> `Too many parameters:  "fuzz".`, pass 1, all four
        # loggers (oracle, 2026-09-27), although its name parameter is not an eligible kind; so is
        # `CallTable` (`CallTable(T,X,Y)` -> `Too many parameters:  Y.`, 2026-09-28).
        if not record or not (_too_many_eligible(record["params"])
                              or stmt.keyword.upper() in _TOO_MANY_EXTRA
                              or (profile.name in ("cr6", "cr1000x")
                                  and stmt.keyword.upper() in ZERO_PARAM_INSTRUCTIONS)):
            continue
        max_count = len(_real_params(record["params"]))
        if len(stmt.args) <= max_count:
            continue
        spans = getattr(stmt, "arg_spans", None)
        if not spans or max_count >= len(spans) or spans[max_count] is None:
            continue
        start = spans[max_count][0]
        token = _next_simple_token(stmt.text[start:])
        if not token:
            continue
        # CR6/CR1000X: an earlier parameter-kind error on the statement hides it (oracle,
        # 2026-10-04: `PortSet(C1,1,x,y)`, `PortSet(x,1,y,z)`, `Delay(1,x,y,z)`)
        if not profile.single_pass and _earlier_error_in_stmt(
                program, stmt, diags, ("Illegal parameter ", "Variable illegal in Parameter")):
            continue
        diags.error(stmt.line, f"Too many parameters:  {token}.", stmt.file)


def _earlier_error_in_stmt(program: S.Program, stmt, diags: Diagnostics, prefixes: tuple) -> bool:
    """A `prefixes` error on `stmt`'s line that belongs to `stmt` itself. On a `:`-joined line
    (oracle, 2026-10-08, CR6/CR1000X: `PortSet(C1,1,x):PortSet(C1,1,1,2)` still reports `Too many
    parameters:  2.`) a diagnostic belongs to the statement whose text contains its echoed
    argument; a single-statement line keeps the plain line match."""
    here = [d for d in diags.items if d.line == stmt.line and d.file == stmt.file
            and d.message.startswith(prefixes)]
    if not here:
        return False
    if sum(1 for o in program.statements if (o.file, o.line) == (stmt.file, stmt.line)) < 2:
        return True
    for d in here:
        if d.stmt:  # raised by a tagged emitter: exact statement identity, not echo text
            if d.stmt == id(stmt):
                return True
            continue
        echo = re.sub(r"^(?:Illegal parameter |[^:]*: )|(?:, expecting constant integer)?\.$", "",
                      d.message).strip()
        if echo in stmt.text:
            return True
    return False


def check_essinitialize_too_many(program: S.Program, profile, diags: Diagnostics) -> None:
    """An unterminated second `ESSInitialize` argument still reaches its excess-argument
    diagnostic.  The lexer leaves this exact malformed line as `Unparsed`, so the ordinary
    `check_too_many` walk cannot see its second argument."""
    for stmt in program.statements:
        if not isinstance(stmt, S.Unparsed):
            continue
        m = re.match(r"(?is)^\s*ESSInitialize\s*\((.*)\)\s*$", stmt.text)
        if not m:
            continue
        body = m.group(1)
        depth = 0
        quoted = False
        split = None
        for i, char in enumerate(body):
            if char == '"':
                quoted = not quoted
            elif not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                elif char == "," and depth == 0:
                    split = i
                    break
        if split is None:
            continue
        token = _next_simple_token(body[split + 1:])
        if token:
            diags.error(stmt.line, f"Too many parameters:  {token}.", stmt.file)


_SNMP_SPACED_OID = re.compile(
    r"^\s*SNMPVARIABLE\s*\(\s*[^,]+\s*,\s*(?:\d+\.)+\s+\d+(?:\.\d+)*\s*,\s*([A-Za-z_]\w*)\s*,",
    re.I,
)
_SNMP_OID_DEST = re.compile(r"^\s*SNMPVARIABLE\s*\(\s*(\d+(?:\.\d+)*)\s*,", re.I)
_SNMP_TAIL = re.compile(r"^\s*SNMPVARIABLE\s*\(\s*[^,]+,\s*\d+(?:\.\d+)*\s*,", re.I)
_SNMP_TYPES = frozenset({"INTEGER", "COUNTER", "STRING", "TIMETICKS", "OPAQUE", "GAUGE", "FLOAT"})


def check_modemcallback_baud(program: S.Program, profile, diags: Diagnostics) -> None:
    """`ModemCallBack(R,COMSDC7,<baud>,...)` -> `<baud> is illegal baud rate.` unless the baud,
    truncated toward zero (outside 32 bits it becomes -2147483648), is 0 or 600..460800 (oracle, CR6/CR1000X,
    2026-10-10: 599, 300, 460801 and 2^31-1 fail; 1234, 100000 and 0.5 pass; 2^31 and wider
    give -2147483648). Only the COMSDC7 port was probed (`ComC1,300` passes)."""
    for stmt in program.statements:
        if not (isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "MODEMCALLBACK"
                and "MODEMCALLBACK" in profile.instructions and len(stmt.args) > 2):
            continue
        port, arg = stmt.args[1], stmt.args[2]
        if not (isinstance(port, S.Name) and port.name.upper() == "COMSDC7"):
            continue
        sign = 1
        if isinstance(arg, S.Unary) and arg.op == "-":
            sign, arg = -1, arg.operand
        if not (isinstance(arg, S.Number) and isinstance(arg.value, (int, float))):
            continue
        baud = int(sign * arg.value)
        if not -2**31 <= baud < 2**31:
            baud = -2**31
        if baud != 0 and not 600 <= baud <= 460800:
            diags.error(stmt.line, f"{baud} is illegal baud rate.", stmt.file)


def check_snmpvariable_too_many(program: S.Program, profile, diags: Diagnostics) -> None:
    """Whitespace within a dotted SNMP OID makes the real parser resume at its type token.

    The normal dotted OID is an intentional parser gap that compiles; this is only its observed
    whitespace-split recovery shape, which emits the ordinary excess-parameter message.
    """
    for stmt in program.statements:
        if not isinstance(stmt, S.Unparsed) or not stmt.reason.startswith("expected ')', found"):
            continue
        match = _SNMP_SPACED_OID.match(stmt.text)
        if match:
            diags.error(stmt.line, f"Too many parameters:  {match.group(1)}.", stmt.file)
            continue
        match = _SNMP_TAIL.match(stmt.text)
        if match:
            # `SNMPVariable(V,1.3.6.1.4,Float,RONLY,1)` -> `Too many parameters:  1.`; a Type that
            # is not an SNMP type ends the list one argument earlier (`Nope,RONLY`), all four
            # loggers (oracle, 2026-10-10); an `RWRITE` access also takes the Valid argument
            starts, depth, quoted, begin = [], 0, False, match.end()
            for i in range(match.end(), len(stmt.text)):
                c = stmt.text[i]
                if c == '"':
                    quoted = not quoted
                elif not quoted:
                    depth += (c == "(") - (c == ")")
                    if depth < 0:
                        break
                    if c == "," and depth == 0:
                        starts.append(begin)
                        begin = i + 1
            starts.append(begin)
            ends = [b - 1 for b in starts[1:]] + [None]
            words = [stmt.text[b:e].strip().upper() for b, e in zip(starts, ends)]
            allowed = 1 if words[0] not in _SNMP_TYPES else 2
            if allowed == 2 and len(words) > 1 and words[1] == "RWRITE":
                allowed = 3  # RWRITE takes the Valid argument
            if len(starts) > allowed and (token := _next_simple_token(stmt.text[starts[allowed]:])):
                diags.error(stmt.line, f"Too many parameters:  {token}.", stmt.file)
                continue
        match = _SNMP_OID_DEST.match(stmt.text)
        if match:
            oid = match.group(1)
            diags.error(stmt.line, "Illegal object id.", stmt.file)
            diags.error(stmt.line, "SNMP OID must be greater than 1.3.6.1.2.1.1.7", stmt.file)
            diags.error(stmt.line, f"Variable name {oid} illegal, starts with a number.", stmt.file)


# --- Ungated `Scan`/`DataInterval` numeric-value checks (called from `checker/warnings.py`'s
# `check_early`; see this module's docstring and `docs/instructions.md` for the oracle evidence
# that both fire even when the file has other errors elsewhere) ------------------------------

# CR300/CR350 do not perform the `Scan`/`DataInterval` non-negative-value validation at all --
# oracle-confirmed a genuine per-logger difference, not a shared check with a different message
# (`docs/instructions.md`): `Scan("fuzz",Sec,3,0)`, `Scan(-1,Sec,3,0)` and `DataInterval(0,-3,
# Sec,0)` all compile clean on `cr300comp.exe` (found as a **regression** during development --
# the corpus's own `hex_2.CR6`, `Scan(1,Sec,3,-1)`, is real CR300/CR350-accepted code, a false
# reject once this went live logger-unconditionally). The `DataTable`-interval-vs-`Scan`-interval
# divisibility check (`check_data_interval`'s other half) is *not* gated this way -- oracle-
# confirmed identical, message and all, on `cr300comp.exe` too.
_SCAN_VALUE_CHECK_LOGGERS = frozenset({"cr6", "cr1000x"})


def _is_negative_or_invalid(value: object) -> bool:
    """`True` when a folded constant `value` is what the real compiler's shared `Scan`/
    `DataInterval` parameter validator (`FUN_00468900`/`FUN_004f11d0`) rejects: a non-numeric
    constant (a string, oracle: `Scan("fuzz",Sec,1,0)` -> `Scan Interval parameter must be a
    non-negative integer`), a number whose value truncated toward zero is negative, or one that
    overflows a signed 32-bit int. Python's `int()` on a float already truncates toward zero,
    matching the oracle's own boundary exactly: `-0.5` (`int(-0.5) == 0`) passes this check and is
    accepted (`docs/instructions.md`), `-1` fails it. **Round 5 addition, oracle-confirmed**
    (`docs/checker.md` "Round 5", a fuzz-found false accept): the same message also covers a
    positive value too large to fit `INT32_MAX` (`2147483647`) -- `Scan(1,Sec,3,2147483647)`
    compiles, `Scan(1,Sec,3,2147483648)` fails with this exact text, boundary-swept on both `Count`
    and `Interval`; `DataInterval`'s own sibling check (`check_data_interval` below, same shared
    predicate) shares the identical boundary (`DataInterval(2147483648,10,Sec,10)` ->
    `DataInterval TintoInt and Interval parameters must be non-negative`). An unrecognised constant
    type (never observed; `consteval.value_of` only ever returns `int`/`float`/`str`) is
    deliberately treated as valid -- AGENTS.md: never guess into a false rejection.
    """
    if isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return int(value) < 0 or int(value) > 2147483647
    return False


def check_scan_params(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`Scan Interval/BufferOption/Count parameter must be a non-negative integer` (no trailing
    period -- verbatim oracle/`docs/messages.tsv` text, function `FUN_00468900`) for `Scan`'s 1st,
    3rd and 4th arguments respectively (`Scan(Interval,Units,BufferOption,Count)`, `docs/
    instructions.md`). Only checked when the argument is already a compile-time constant (a
    variable there is a different check's concern, matching `checker/terminals.py`'s own
    convention); this is deliberately narrower than the real compiler's full `Scan` validation
    (minimum-interval, maximum-interval and multiple-of-a-logger-granularity checks all exist too,
    oracle-confirmed present but with boundaries this project could not pin down precisely enough
    to implement without risking a false reject on a borderline-valid interval -- `docs/
    instructions.md` "Known gaps"). This closes the fuzz-found crash in `tables.py`'s
    `_scan_weights` (a non-numeric `Scan` interval, `docs/fuzz-findings.md` "2. Crash") with a real
    diagnostic instead of an unhandled exception, and the corpus's own `hex_2.CR6`
    (`Scan Count parameter must be a non-negative integer`, `docs/checker.md` "Known remaining
    gaps"). CR6/CR1000X only -- see `_SCAN_VALUE_CHECK_LOGGERS`.
    """
    if profile.name not in _SCAN_VALUE_CHECK_LOGGERS:
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() != "SCAN":
            continue
        for idx, label in ((0, "Interval"), (2, "BufferOption"), (3, "Count")):
            if idx >= len(stmt.args) or stmt.args[idx] is None:
                continue
            arg = stmt.args[idx]
            if not consteval.is_constant(arg, env):
                continue
            try:
                value = consteval.value_of(arg, env)
            except consteval.NotConstant:
                continue
            if _is_negative_or_invalid(value):
                diags.error(stmt.line, f"Scan {label} parameter must be a non-negative integer", stmt.file)
            if idx == 0:
                _scan_min_interval(stmt, diags, env)


# `Scan`/`DataInterval`'s `Units` argument folds (via `consteval`, `profile.symbols`) to this 0..5
# code (`data/symbols.json`: `usec`=0 .. `day`=5); microseconds per unit.
_US_PER_UNIT = {0: 1, 1: 1_000, 2: 1_000_000, 3: 60_000_000, 4: 3_600_000_000, 5: 86_400_000_000}


def _scan_min_interval(stmt: S.Instruction, diags: Diagnostics, env: consteval.Env) -> None:
    """`Scan %duSec invalid must be at least %dmSec.` / `Scans must be a multiple of %dmSec.`
    (round 6, CR6/CR1000X only, same `FUN_00468900` family as `check_scan_params` above -- oracle-
    confirmed ungated the same way, fires alongside unrelated errors both before and after it in
    the file). Previously investigated and left unimplemented (`docs/instructions.md`
    "Investigated, not implemented": "each has a boundary this session could not pin down cleanly
    ... smells like float-precision artifacts") -- pinned down this round with a dozen fresh
    `Interval` x `Units` probes: the declared `Interval` is truncated toward zero **in its own
    declared unit** (`int(value)`, matching `_is_negative_or_invalid`'s own truncation convention)
    *before* being multiplied by that unit's microsecond factor (`_US_PER_UNIT`) -- not the real,
    fractional microsecond value. This single rule, applied uniformly regardless of which unit was
    declared, reproduces every probe in the sweep:

    - `Scan(0.5,mSec,...)` -> `int(0.5) * 1_000 = 0` uSec (not `500`) -> `Scan 0uSec invalid...`
    - `Scan(0.0015,Sec,...)` -> `int(0.0015) * 1_000_000 = 0` uSec (not `1500`) -> same message
    - `Scan(1.9999,mSec,...)` -> `int(1.9999) * 1_000 = 1000` uSec -> compiles clean
    - `Scan(1500,uSec,...)` -> `int(1500) * 1 = 1500` uSec -> `Scans must be a multiple of 1mSec.`
      (not a multiple of 1000), while the numerically-equal `Scan(1.5,mSec,...)` truncates to
      `int(1.5) * 1_000 = 1000` uSec first and compiles clean -- this reproduces the previous
      session's own unresolved `uSec`-vs-`mSec` asymmetry (`7999uSec` fails, `7.9mSec` compiles)
      exactly, with no per-unit branch: after truncate-then-multiply, an `mSec`/`Sec`-declared
      interval is *always* already an exact multiple of 1000, so the "must be a multiple" branch
      can structurally never fire for those two units.

    Both comparisons run on this one folded `raw_us` value, **independently of whether
    `check_scan_params`'s own non-negative check also already fired for the same `Interval`
    argument** -- oracle-confirmed not mutually exclusive: `Scan(-1,mSec,...)` fires *both* `Scan
    Interval parameter must be a non-negative integer` *and* `Scan -1000uSec invalid must be at
    least 1mSec.`, on the same line, in that order, both before the `BufferOption`/`Count`
    messages (`check_scan_params` calls this right after the `Interval` check). A negative value
    at or below -1000000 uSec gets no "at least" message (oracle, CR6/CR1000X, 2026-10-03:
    `Scan(-999999,uSec,..)` does, `Scan(-1000000,uSec,..)`, `Scan(-1,Sec,..)`, `Scan(-2147,mSec,..)`
    do not; `Scan(-1000001,uSec,..)` gets the multiple-of message). Only `Interval` (position 0) -- no oracle evidence either message
    applies to `BufferOption`/`Count`.
    """
    args = stmt.args
    interval = args[0] if args else None
    units = args[1] if len(args) > 1 else None
    if interval is None or units is None:
        return
    if not (consteval.is_constant(interval, env) and consteval.is_constant(units, env)):
        return
    try:
        value = consteval.value_of(interval, env)
        unit = consteval.value_of(units, env)
    except consteval.NotConstant:
        return
    if isinstance(value, str) or not isinstance(value, (int, float)):
        return
    # any other unit (`99`, `"2"`, `"fuzz"`) counts as 0 uSec (oracle, CR6, 2026-10-01)
    us_per_unit = _US_PER_UNIT.get(unit, 0) if not isinstance(unit, bool) else 0
    raw_us = int(value) * us_per_unit
    if -1_000_000 < raw_us < 1000:
        diags.error(stmt.line, f"Scan {raw_us}uSec invalid must be at least 1mSec.", stmt.file)
    elif raw_us % 1000 != 0:
        diags.error(stmt.line, "Scans must be a multiple of 1mSec.", stmt.file)


def check_scan_max_interval(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`Maximum Scan interval is 1 Day.` on a `Scan` longer than 86400 s, all four loggers (oracle,
    2026-09-27: `Scan(86401,Sec,...)`, `Scan(1441,Min,...)`, `Scan(2,Day,...)`; `Scan(1,Day,...)`
    is accepted). An interval past `INT32_MAX` gets no such message."""
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "SCAN":
            raw = _scan_raw(stmt, env)
            if raw is not None and raw[0] * _US_PER_UNIT[raw[1]] > 86_400_000_000:
                diags.error(stmt.line, "Maximum Scan interval is 1 Day.", stmt.file)


def _scan_raw(stmt: S.Instruction, env: consteval.Env) -> tuple[int, int] | None:
    """`(int(interval), units)` of a constant `Scan` interval that fits `INT32_MAX`, else `None`."""
    args = stmt.args
    if len(args) < 2 or args[0] is None or args[1] is None or \
            not (consteval.is_constant(args[0], env) and consteval.is_constant(args[1], env)):
        return None
    try:
        value = consteval.value_of(args[0], env)
        unit = consteval.value_of(args[1], env)
    except consteval.NotConstant:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or unit not in _US_PER_UNIT \
            or not 0 <= int(value) <= 2147483647:
        return None
    return int(value), unit


def _interval_us(interval_arg: S.Expr | None, units_arg: S.Expr | None, env: consteval.Env) -> float | None:
    if interval_arg is None or units_arg is None:
        return None
    if not (consteval.is_constant(interval_arg, env) and consteval.is_constant(units_arg, env)):
        return None
    try:
        value = consteval.value_of(interval_arg, env)
        unit = consteval.value_of(units_arg, env)
    except consteval.NotConstant:
        return None
    us_per_unit = _US_PER_UNIT.get(unit)
    if us_per_unit is None or not isinstance(value, (int, float)) or value < 0:
        return None
    return value * us_per_unit


def _divisible_interval_us(interval_arg: S.Expr | None, units_arg: S.Expr | None,
                            env: consteval.Env) -> float | None:
    """`_interval_us` with the interval's value truncated toward zero to a whole number of its own
    units first. Oracle-confirmed (`docs/checker.md` "Interval truncation", all four loggers
    identical): `DataInterval(0,1.5,Sec,10)` against `Scan(1,Min,0,0)` reports `interval 1000000
    uSec`, `1.5,Msec` -> `1000 uSec`, `1000.5,Msec` -> `1000000 uSec`, `90.5,Sec` -> `90000000 uSec`;
    the same truncation applies to the `Scan` interval (`Scan(3.5,Sec)` -> `scan interval 3000000
    uSec`, `Scan(3000.7,Msec)` -> `3000000`). A fractional interval therefore still fires whenever
    its truncated value is not a divisor (`2.5` Sec in a `1` Min table fires; `1.5` Sec in a
    `1` Sec scan is clean because it truncates to `1`).
    """
    us = _interval_us(interval_arg, units_arg, env)
    if us is None:
        return None
    try:
        value = consteval.value_of(interval_arg, env)
    except consteval.NotConstant:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) * _US_PER_UNIT[consteval.value_of(units_arg, env)]


def check_data_interval(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """Two `DataInterval` checks, both oracle-confirmed ungated (`docs/instructions.md`):

    - `DataInterval TintoInt and Interval parameters must be non-negative` (function
      `FUN_004f11d0`, verbatim, no trailing period) when either of `DataInterval`'s first two
      arguments (`TintoInt`, `Interval`) is a negative compile-time-constant number, reported at
      the `DataInterval` statement's own line. CR6/CR1000X only -- oracle-confirmed a genuine
      per-logger difference, not shared with CR300/CR350 (`_SCAN_VALUE_CHECK_LOGGERS`; found the
      same way as `check_scan_params`'s own CR300/CR350 gate, same session).
    - `DataTable interval %d uSec not integrally divisible by scan interval %d uSec.` (function
      `FUN_004f6540`) -- project knowledge (`knowledge/crbasic/lessons.md` "`DataInterval` versus
      `Scan`": "a table interval must be at least the scan interval and an exact integer multiple
      of it", human-verified against `CR6Comp.exe`), reported at each `CallTable <name>` site whose
      table has a `DataInterval` and which sits directly inside a `Scan...NextScan` block (main
      sequence or `SlowSequence`, both accepted per the CHM) with its own resolvable constant
      interval, when the two aren't an exact multiple. `Interval = 0` (CHM: "the data storage
      interval is the scan interval") trivially always divides evenly, so it needs no special case.

      **Deliberately conservative** (AGENTS.md: never falsely reject a program the real compiler
      accepts) -- this only tracks the *textually innermost* `Scan`/`SlowSequence` `Scan` a
      `CallTable` sits inside (no depth stack beyond "currently open or not": `Scan` never nests
      with itself, only `SubScan`, which uses distinct `SUBSCAN`/`NEXTSCAN` -- wait, distinct
      `SUBSCAN`/`NEXTSUBSCAN` keywords, so it never resets this tracker). It does **not** attempt:
      a table called indirectly through a `Sub` (the call site's enclosing `Scan` is not visible at
      the `Sub`'s own definition), a table called from more than one differently-interval'd `Scan`
      (each call site is still checked independently against whatever `Scan` textually contains
      it, which is correct for the common case but under-covers a table called from *outside* any
      `Scan` at all, e.g. via `AcceptDataRecords` -- silently skipped), or the CHM's documented
      `GetDataRecord`-destination-table exception (not implemented; a false reject there is
      possible but not found in this project's corpus/fuzz evidence, `docs/instructions.md`).
    """
    declared_interval_us: dict[str, tuple[float, int]] = {}  # table name -> (interval_us, line)
    current_table: str | None = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "DATATABLE":
            current_table = stmt.args[0].name.upper() if stmt.args and isinstance(stmt.args[0], S.Name) else None
            continue
        if kw == "ENDTABLE":
            current_table = None
            continue
        if kw != "DATAINTERVAL" or not current_table:
            continue
        args = stmt.args
        tinto = args[0] if len(args) > 0 else None
        interval = args[1] if len(args) > 1 else None
        units = args[2] if len(args) > 2 else None
        for arg in (tinto, interval) if profile.name in _SCAN_VALUE_CHECK_LOGGERS else ():
            if arg is None or not consteval.is_constant(arg, env):
                continue
            try:
                value = consteval.value_of(arg, env)
            except consteval.NotConstant:
                continue
            if _is_negative_or_invalid(value):
                diags.error(stmt.line, "DataInterval TintoInt and Interval parameters must be non-negative", stmt.file)
                break
        interval_us = _divisible_interval_us(interval, units, env)
        if interval_us is not None:
            declared_interval_us[current_table] = (interval_us, stmt.line)

    scan_interval_us: float | None = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "SCAN":
            args = stmt.args
            scan_interval_us = _divisible_interval_us(
                args[0] if args else None, args[1] if len(args) > 1 else None, env
            ) if _scan_raw(stmt, env) else None
            continue
        if kw == "NEXTSCAN":
            scan_interval_us = None
            continue
        if kw != "CALLTABLE" or not scan_interval_us or not stmt.args:
            continue
        name_arg = stmt.args[0]
        if not isinstance(name_arg, S.Name):
            continue
        entry = declared_interval_us.get(name_arg.name.upper())
        if entry is None:
            continue
        message = _divisibility_message(int(entry[0]), int(scan_interval_us))
        if message:
            diags.error(stmt.line, message, stmt.file)


def _divisibility_message(dt_us: int, scan_us: int) -> str | None:
    """Decompiled `FUN_004f6540`: a table interval under 3601 s is compared in 100 uSec units
    (printed `%d00 uSec`), a longer one in whole seconds (oracle, 2026-09-27: `Scan(2,Hr)` against
    a 3-hour table -> `DataTable interval 10800 Sec not integrally divisible by scan interval 7200
    Sec.`; `Scan(1,Day)` against a 1-hour table -> `3600000000 uSec ... 86400000000 uSec.`)."""
    dt_sec, dt_sub = divmod(dt_us, 1_000_000)
    scan_sec, scan_sub = divmod(scan_us, 1_000_000)
    scan_units = scan_sec * 10000 + scan_sub // 100
    if scan_units == 0:
        return None
    if dt_sec < 3601:
        dt_units = dt_sec * 10000 + dt_sub // 100
        if dt_units > 0 and dt_units % scan_units:
            return (f"DataTable interval {dt_units}00 uSec not integrally divisible by "
                    f"scan interval {scan_units}00 uSec.")
    elif scan_sub == 0:
        if dt_sec % scan_sec:
            return (f"DataTable interval {dt_sec} Sec not integrally divisible by "
                    f"scan interval {scan_sec} Sec.")
    elif scan_sec or 10000 % scan_units:
        return ("DataTable with interval > 1 hour cannot be called by scan whose interval is not "
                "an integral second is not divisible into a second.")
    return None


# `AddPrecise`/`MovePrecise`'s own first argument (`PrecisionVariable`, `params=[8, 2]`, a
# `variable_or_array` kind-8 position on every logger that has these two instructions at all --
# `data/instructions.json`/`data/cr1000x/instructions.json`; **absent from `data/cr300/
# instructions.json` and `data/cr350/instructions.json` entirely**, so this never applies there):
# oracle-confirmed (scratch probes this round, `CR6Comp.exe` and `cr1000xcomp.exe`, byte-identical
# on both) to reject with `Invalid Variable or Array: <echo>.` whenever the argument has no genuine
# variable reference anywhere in it -- a bare literal (`AddPrecise(1, .1)` -> `... 1.`), a
# parenthesised or unary-negated constant expression (`(1+2)` -> `... (1+2).`, `-1` -> `... -1.`),
# a declared `Const` name (`AddPrecise(K, .1)`, `K` a `Const` -> `... K.`), and the special float
# literal `NAN` (`AddPrecise(NAN, .1)` -> `... NAN.`) all reject this way. A declared `Public`/
# `Dim` variable compiles clean regardless of how it appears -- bare, negated, parenthesised,
# indexed, or mixed into a larger expression with literals (`Precise`, `-Precise`, `(Precise)`,
# `d(1)`, `Precise+1`, `1+2+Precise` all oracle-confirmed accepted) -- and so does an *undeclared*
# name (`AddPrecise(UndeclaredFuzzName123, .1)`, the existing saved fuzz false-reject reproducer,
# `tests/corpus/fuzz/cr6/false_reject/fac2798b6778.CR6`, and `expressions.py`'s own
# `_UNDEFINED_CHECK_EXEMPT_POSITIONS` docstring, which documents that this position never runs the
# separate undefined-symbol check either). Reusing that same "any genuine variable reference,
# declared or undeclared, anywhere in the expression" test here (`_undefined_names` OR
# `_variable_refs`, both already written for the undefined-symbol/const-only-family checks in
# `expressions.py`) rather than a fresh `consteval.is_constant` check: `NAN` is *not*
# `consteval`-constant (it fails `value_of`, `_EXTRA_PREDEFINED_SYMBOLS` is a separate, untraced
# resolution path -- `expressions.py`'s own docstring) yet still rejects here, so plain constant-
# foldability is not what the real compiler is testing; "does the expression's own name(s), if any,
# fail to resolve as a fixed compile-time symbol" is the trigger the oracle evidence above actually
# supports, and this is exactly `not (_undefined_names(...) or _variable_refs(...))`.
#
# `X` (the second argument, `params[1]` code 2, a plain `expr` position) has no such restriction --
# oracle-confirmed separately (`AddPrecise(Precise, (1+2))` compiles clean) -- so only position 0
# is listed.
#
# Deliberately scoped to just these two instructions' own position 0, not generalised to every
# `variable_or_array` position: `expressions.py`'s `_VARIABLE_REQUIRED_KINDS` docstring documents a
# real regression at the same type code (`Move`'s third argument, `ModBusClient`'s last argument,
# both oracle-confirmed to accept a literal there on CR1000X) that this session has no evidence
# either way for, so this stays narrow rather than guessing.
#
# Pass 1, unconditional (`checker/__init__.py`, alongside this module's own `check`/
# `check_too_many`): oracle-confirmed (a scratch probe combining a genuine, earlier pass-1
# declaration error -- `Public x / foo bar baz` -- with `AddPrecise(1, .1)` on a later line)
# to accumulate with that earlier pass-1 error rather than being suppressed by it, and, run alone
# with no other error in the file, to itself suppress a later, otherwise-live pass-2 finding
# elsewhere in the file (an undefined name inside a following `Randomize(...)`) -- the same "pass 1
# found something anywhere in the file, pass 2 does not run at all" architecture `docs/checker.md`
# ("Pass model") already documents for this module's other unconditional checks.
_PRECISION_VARIABLE_POSITIONS: dict[str, frozenset[int]] = {
    "ADDPRECISE": frozenset({0}),
    "MOVEPRECISE": frozenset({0}),
}


def check_precision_variable(program: S.Program, profile, diags: Diagnostics) -> None:
    """`Invalid Variable or Array: %s.` for `AddPrecise`/`MovePrecise`'s own `PrecisionVariable`
    argument when it has no genuine variable reference (declared or undeclared) anywhere in it.
    See `_PRECISION_VARIABLE_POSITIONS`'s own docstring above for the oracle evidence and the exact
    rule. Gated per-`profile.instructions` (not just the keyword spelling) so this is a no-op on
    CR300/CR350, which do not have either instruction at all.
    """
    known: set[str] | None = None
    variable_names: set[str] | None = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        positions = _PRECISION_VARIABLE_POSITIONS.get(kw)
        if not positions or kw not in profile.instructions:
            continue
        if known is None:
            known = _known_names(program, profile)
            variable_names = symbols.declared_variable_names(program)
        record = profile.instructions[kw]
        if len(stmt.args) < _required_count(_real_params(record["params"])) and \
                profile.name in ("cr6", "cr1000x"):
            continue  # reported with the missing-parameter line, `_report_constant_destinations`
        for i in sorted(positions):
            if i >= len(stmt.args) or stmt.args[i] is None:
                continue
            arg = stmt.args[i]
            if _undefined_names(arg, known) or _variable_refs(arg, variable_names):
                continue
            diags.error(stmt.line, f"Invalid Variable or Array: {_arg_text(stmt, i)}.", stmt.file)


# SDM address argument position, measured by setting each numeric argument of a chm example to 99
# (oracle, CR6, 2026-09-28; `docs/instructions.md` "SDM addresses").
_SDM_ADDRESS_ARG = {
    "CS7500": 2, "CSAT3": 2, "CSAT3B": 2, "CSAT3BMONITOR": 2, "EC100": 1, "EC100CONFIGURE": 1,
    "LI7200": 2, "LI7700": 2, "SDMCD16AC": 2, "SDMCD16MASK": 2, "SDMGENERIC": 1, "SDMIO16": 2,
    "SDMSIO4": 2, "SDMSW8A": 2, "SDMX50": 0, "TDR100": 1, "TDR200": 1,
}


def check_sdm_address(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`Invalid SDM Address: %d, must be 0 - 14.` (decompiled `FUN_0043c680`) for a constant
    address whose low byte is 15 or more (`-1` -> `255`; `256` is accepted), pass 2, accumulating
    with other pass-2 errors (oracle, CR6/CR1000X, 2026-09-28)."""
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        pos = _SDM_ADDRESS_ARG.get(kw)
        if pos is None or kw not in profile.instructions or pos >= len(stmt.args):
            continue
        arg = stmt.args[pos]
        if arg is None or not consteval.is_constant(arg, env):
            continue
        try:
            value = consteval.value_of(arg, env)
        except consteval.NotConstant:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        # A decimal constant, or an integer beyond Long, is a float32 first (`16777233.0` -> 16);
        # a float32 outside Long range is accepted unchecked (oracle, CR6/CR1000X, 2026-10-08;
        # docs/fuzz-findings.md "Round 5, SDM address").
        # consteval values are float-valued throughout, so `consteval.is_float` tracks the type;
        # where it is not established an integer-valued result in Long range reads as integer.
        is_float = consteval.is_float(arg, env)
        if is_float is None:
            is_float = value != int(value) if abs(value) < 2**63 else True
        if is_float or not -2**31 <= value < 2**31:
            try:
                value = struct.unpack("f", struct.pack("f", value))[0]
            except OverflowError:   # float32 +inf saturates to Long max (255); -inf is 0
                value = 2**31 - 1 if value > 0 else 0
            if not -2**31 <= value < 2**31:
                continue
        address = int(value) & 0xFF
        if address >= 15:
            diags.error(stmt.line, f"Invalid SDM Address: {address}, must be 0 - 14.", stmt.file)


_AVGRUN_TYPE_ERROR = frozenset({"FLOAT", "STRING"})
_RUNNING_AVG = frozenset({"AVGRUN", "TOTALRUN", "MINRUN", "MAXRUN", "STDDEVRUN"})


def check_avgrun_options(program: S.Program, profile, diags: Diagnostics,
                         env: consteval.Env) -> None:
    """`AvgRun`'s optional arguments after `ResetVar` (decompiled AvgRun compile function; oracle,
    2026-10-01): a Float or String variable as `Count` -> `Optional Count parameter must be type
    long.` (all four loggers; a Long/Boolean variable, a literal or an expression compile). On
    CR6/CR1000X a variable or `0` as the number of distinct instances -> `Optional number of
    distinct instances must be a constant of type long.`, and any instances argument without the
    instance number after it -> `Missing instance number`. A constant `Reps` or `Number` below 1
    -> `number of averages %d and reps %d in RunningAvg must be > 0.` (all four). Reported with
    pass 2 without hiding the tier-C checks on CR6/CR1000X, only in an otherwise clean file on
    CR300/CR350. A negative instance count gives `Out of memory.`, which ends the compile (not
    modelled).

    `Reps` and `Number` of `TotalRun`/`MinRun`/`MaxRun` and, on CR6/CR1000X, `StdDevRun` go through
    the same message (oracle, 2026-10-02, all four loggers). Values convert as C does: truncated
    toward zero, a string or anything outside Long range gives `-2147483648`."""
    if not _RUNNING_AVG & profile.instructions.keys():
        return
    types = symbols.declared_variable_types(program)

    def variable_type(arg: S.Expr | None) -> str | None:
        return types.get(arg.name.upper()) if isinstance(arg, (S.Name, S.Index)) else None

    from . import longconv
    names = longconv.float_names(program.statements)
    literals = longconv.literal_names(program.statements)

    def number(arg: S.Expr | None) -> int | None:
        converted = longconv.long_value(arg, env, profile, names, literals)
        if converted is not None:
            return converted
        try:
            value = consteval.value_of(arg, env) if arg is not None else None
        except consteval.NotConstant:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        return consteval.to_long(value)

    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() not in _RUNNING_AVG:
            continue
        kw = stmt.keyword.upper()
        args = list(stmt.args) + [None] * (8 - len(stmt.args))
        if kw in ("TOTALRUN", "STDDEVRUN") and args[4] is not None \
                and variable_type(args[5]) in _AVGRUN_TYPE_ERROR:
            # same Count message as `AvgRun` (oracle, CR6 and CR300, 2026-10-08)
            diags.error(stmt.line, "Optional Count parameter must be type long.", stmt.file)
        if kw == "AVGRUN" and args[4] is not None:
            if variable_type(args[5]) in _AVGRUN_TYPE_ERROR:
                diags.error(stmt.line, "Optional Count parameter must be type long.", stmt.file)
            if args[6] is not None and len(profile.instructions["AVGRUN"]["params"]) >= 8:
                instances = number(args[6])
                if instances is not None and instances < 0:
                    continue  # (`-1` -> `Out of memory.`, not modelled)
                if variable_type(args[6]) is not None or instances == 0:
                    diags.error(stmt.line, "Optional number of distinct instances must be a "
                                "constant of type long.", stmt.file)
                if args[7] is None:
                    diags.error(stmt.line, "Missing instance number", stmt.file)
        reps, count = number(args[1]), number(args[3])
        if reps is not None and count is not None and (reps < 1 or count < 1):
            diags.error(stmt.line, f"number of averages {count} and reps {reps} in RunningAvg "
                        "must be > 0.", stmt.file)


def check_option_values(program: S.Program, profile, diags: Diagnostics,
                        env: consteval.Env) -> None:
    """Constant option arguments (oracle, 2026-10-02, `docs/checker.md` "Option arguments"),
    converted to a 32-bit `long` as in `checker/longconv.py`. Reported with pass 2 without hiding
    the tier-C checks on CR6/CR1000X, only in an otherwise clean file on CR300/CR350 (like
    `check_avgrun_options`).

    - `SplitStr` `Options` (fifth argument), all four loggers: below 0 -> `Invalid option %d`.
    - `IPRoute` `Network` (second argument): outside 0..5 (CR300/CR350: 0..4) -> `%d is illegal
      network`; otherwise the optional third argument outside 0..1 -> `%d is illegal option`
      (the first line hides the second); a variable in any argument silences both. A string option crashes the real compiler: not modelled.
    - `CDM_Delay` `Option` (third argument, CR6/CR1000X): the value modulo 256 other than 0 or 1
      -> `Illegal option %d` (0 draws a different message about the module, not modelled).
    - `Matrix` `Option` (first argument): `(value & 0x7FFFFFFF)` outside 1..5 -> `Illegal option.`.
    """
    from . import longconv
    names = longconv.float_names(program.statements)
    literals = longconv.literal_names(program.statements)
    cr3 = profile.name in ("cr300", "cr350")
    matrix_shapes = None
    for stmt in program.statements:
        if not isinstance(stmt, (S.Instruction, S.Call)):
            continue
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else stmt.name.upper()
        if kw not in ("SPLITSTR", "IPROUTE", "MATRIX", "CDM_DELAY") or \
                kw not in profile.instructions:
            continue
        args = list(stmt.args)

        def value(i: int) -> int | None:
            return longconv.long_value(args[i], env, profile, names, literals) if i < len(args) else None

        if kw == "SPLITSTR":
            v = value(4)
            if v is not None and v < 0:
                diags.error(stmt.line, f"Invalid option {v}", stmt.file)
        elif kw == "CDM_DELAY":
            v = value(2)
            if v is not None and v % 256 not in (0, 1):
                diags.error(stmt.line, f"Illegal option {v % 256}", stmt.file)
        elif kw == "MATRIX":
            v = value(0)
            if v is not None and not 1 <= v & 0x7FFFFFFF <= 5:
                diags.error(stmt.line, "Illegal option.", stmt.file)
            elif v is not None and len(args) == 4:
                if matrix_shapes is None:
                    matrix_shapes = _matrix_shapes(program, env)
                for message in _matrix_dimension_errors(v & 0x7FFFFFFF, args, matrix_shapes):
                    diags.error(stmt.line, message, stmt.file)
        elif all(consteval.is_constant(a, env) for a in args if a is not None):
            net = value(1)
            if net is not None and not 0 <= net <= (4 if cr3 else 5):
                diags.error(stmt.line, f"{net} is illegal network", stmt.file)
                continue
            if len(args) > 2 and isinstance(args[2], S.String):
                continue
            opt = value(2)
            if opt is not None and opt not in (0, 1):
                diags.error(stmt.line, f"{opt} is illegal option", stmt.file)


def _matrix_shapes(program: S.Program, env: consteval.Env) -> dict[str, tuple[int, int]]:
    """Name -> (rows, columns) of every variable declared exactly once as a Float of at most two
    constant dimensions (a 1-D array is `(n, 1)`, a scalar `(1, 1)`)."""
    dims = symbols.declared_all_dims(program, env)
    types = symbols.declared_variable_types(program)
    seen: set[str] = set()
    repeated: set[str] = set()
    for st in program.statements:
        if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC"):
            for v in st.vars:
                (repeated if v.name.upper() in seen else seen).add(v.name.upper())
    return {n: _ILLEGAL_MATRIX if len(d) > 2 else (d[0], d[1] if len(d) == 2 else 1)
            for n, d in dims.items() if len(d) <= 3 and types.get(n) == "FLOAT" and n not in repeated}


# Shape given to an operand the compiler calls `Illegal Matrix` (a String literal, an array of
# three dimensions); it differs from every real shape, which reproduces the messages that follow.
_ILLEGAL_MATRIX = (-1, -1)


def _matrix_dimension_errors(option: int, args, shapes) -> list[str]:
    """`Matrix` operand dimension messages (oracle 2026-10-03, all four loggers,
    `docs/instructions.md` "Matrix dimensions"); empty unless every checked operand is a bare
    Float variable of known shape (`B` is unused by options 4 and 5). A String literal operand or an
    array of three dimensions is `Illegal Matrix` (oracle 2026-10-08, `docs/instructions.md`)."""
    ops = [args[1], args[2], args[3]] if option <= 3 else [args[1], args[3]]
    shape = []
    for i, a in enumerate(ops):
        if (isinstance(a, S.String) and i < len(ops) - 1) or \
                (isinstance(a, S.Number) and option <= 3 and i == 1):
            # (a String destination has its own message; a number is only probed as `B`)
            shape.append(_ILLEGAL_MATRIX)
        elif isinstance(a, S.Name) and a.name.upper() in shapes:
            shape.append(shapes[a.name.upper()])
        else:
            return []
    out = ["Illegal Matrix"] if _ILLEGAL_MATRIX in shape else []
    out += ["Matrix cannot be > 4X4" for sh in shape if max(sh) > 4]
    result = shape[-1]
    if option == 3:
        (m, n), (p, q) = shape[0], shape[1]
        if n != p:
            out.append("multiply A columns and B rows must be the same")
        if result[0] != m and result[1] != q:
            out.append(f"multiply result illegal dimensioned, must be {m},{q}")
    elif any(sh != shape[0] for sh in shape) or (option > 3 and shape[0][0] != shape[0][1]):
        out.append("Illegal Matrix dimensioned")
    return out


def check_const_int_args(program: S.Program, profile, diags: Diagnostics, env: consteval.Env) -> None:
    """`Illegal parameter %s, expecting constant integer.` at a type-code-4 position (`SW12`'s third
    argument, `PortSet`, `PortGet`, `Calibrate`, `CDM_SW12`, CR300 `SwVx`) holding anything but an
    integer constant: a variable, an undefined name, `Nope+1`, `0.5`, `"a"` (echoed verbatim).
    Pass 1, all four loggers (oracle, 2026-09-28; `docs/type-codes.md`). `Do`/`Loop`/`Else` read
    `While`/`Until` there and are left to the parser."""
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        record = profile.instructions.get(kw)
        if not record or kw in ("DO", "LOOP", "ELSE"):
            continue
        if not profile.single_pass and _earlier_error_in_stmt(
                program, stmt, diags, ("Invalid symbol in expression", "Variable illegal in Parameter")):
            continue  # CR6/CR1000X: an earlier const-family error on the line ends the statement
        for i, code in enumerate(record["params"][:len(stmt.args)]):
            arg = stmt.args[i]
            if code not in (4, 104) or arg is None:
                continue
            from . import expressions
            text = _strip_parens(expressions._arg_text(stmt, i).strip())
            if not text or _is_integer_token(text, arg, env):
                continue
            echo = "-" if re.match(r"-\s", text) else text
            diags.error(stmt.line, f"Illegal parameter {echo}, expecting constant integer.",
                        stmt.file, stmt=stmt)


def _strip_parens(text: str) -> str:
    """`text` without enclosing parentheses (`(1+2)` is echoed as `1+2`, oracle 2026-09-28)."""
    while text.startswith("(") and text.endswith(")") and \
            _paren_depth_ok(text[1:-1]):
        text = text[1:-1].strip()
    return text


def _paren_depth_ok(text: str) -> bool:
    depth = 0
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if depth < 0:
            return False
    return depth == 0


def _is_integer_token(text: str, arg: S.Expr, env: consteval.Env) -> bool:
    """One integer token (after `_strip_parens`): `2`, `-1`, `&H1`, `(1)`, or a name whose constant
    value is an integer (`Const K = 1`, `True`). `1+2`, `K+1`, `1.0`, `1E0` and `- 1` are not
    (oracle, 2026-09-28)."""
    if re.fullmatch(r"(?:\+?\d+|-?(?:\d+|&[Hh][0-9A-Fa-f]+|&[Bb][01]+))", text):
        return True
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) or not consteval.is_constant(arg, env):
        return False
    try:
        value = consteval.value_of(arg, env)
    except consteval.NotConstant:
        return False
    return not isinstance(value, str) and isinstance(value, (int, float)) and value == int(value)


_SERIALIN_DEST_MESSAGE = "A string variable destination is required"


def _serialin_types(program: S.Program, profile) -> dict[str, str]:
    types = symbols.declared_variable_types(program)
    for stmt in program.statements:
        if isinstance(stmt, S.ConstDecl):
            types[stmt.name.upper()] = "CONST"
        elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ALIAS" \
                and stmt.args and isinstance(stmt.args[0], (S.Name, S.Index)):
            kind = types.get(stmt.args[0].name.upper())
            if kind is not None:
                for alias in stmt.args[1:]:
                    if isinstance(alias, S.Name):
                        types[alias.name.upper()] = kind
    for name in profile.functions:
        types.setdefault(name, "FUNCTION")
    return types


def _serialin_bad_destination(arg: S.Expr, types: dict[str, str]) -> bool | None:
    if isinstance(arg, (S.Name, S.Index)):
        kind = types.get(arg.name.upper())
        if kind == "FUNCTION" and isinstance(arg, S.Name):
            return None
        return None if kind is None else kind != "STRING"
    if isinstance(arg, (S.Number, S.String, S.Binary, S.Unary)):
        return True
    return None


def check_serialin_destination(program: S.Program, profile, diags: Diagnostics) -> None:
    """Pass-1 SerialIn destination check; docs/instructions.md#serialin-destination.

    Unknown names and structure members remain with the existing name/type checks. This rule
    deliberately does not generalize code 33 to other receiving instructions.
    """
    types = _serialin_types(program, profile)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() != "SERIALIN" \
                or "SERIALIN" not in profile.instructions or not stmt.args or stmt.args[0] is None:
            continue
        if not _serialin_bad_destination(stmt.args[0], types):
            continue
        key = (stmt.line, stmt.file)
        at = next((i for i, d in enumerate(diags.items) if (d.line, d.file) == key),
                  len(diags.items))
        new = [Diagnostic("error", stmt.line, _SERIALIN_DEST_MESSAGE, stmt.file)]
        if profile.single_pass:
            # A failed destination ends this argument list before count checks.
            diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
            at = min(at, len(diags.items))
            if len(stmt.args) > 1 and stmt.args[1] is not None:
                text = _next_simple_token(_arg_text(stmt, 1)) or _arg_text(stmt, 1)
                new.append(Diagnostic("error", stmt.line, f"Too many parameters:  {text}.",
                                      stmt.file))
        diags.items[at:at] = new


_EMAIL_FUNCTIONS = ("EMAILRECV", "EMAILSEND")


def check_emailrecv_destination(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR6/CR1000X `EmailRecv`/`EmailSend` (same behaviour, oracle 2026-10-04): argument 9 must
    exist and every argument from 9 on must be a String variable/element, else `A string variable destination is required` ahead of any arity message
    (docs/instructions.md#emailrecv-destination). Only literals, constants and non-String
    variables count as bad; expressions are left unchecked."""
    if "EMAILRECV" not in profile.functions:
        return
    types = _serialin_types(program, profile)
    from .expressions import _index_nodes, _known_names, _undefined_names
    known = _known_names(program, profile)

    def bad(a):
        # CR300/CR350 name the undefined variable first and still report the destination
        # (oracle, 2026-10-08)
        return _serialin_bad_destination(a, types) or (
            profile.single_pass and isinstance(a, (S.Name, S.Index)) and a.name.upper() not in known)
    for stmt in program.statements:
        if isinstance(stmt, S.Assignment) and _undefined_names(stmt.target, known):
            continue  # an unresolved target is the generic `Invalid, or out of place expression`
        call = stmt.value if isinstance(stmt, S.Assignment) else stmt
        if isinstance(call, (S.Index, S.Call)) and call.name.upper() in _EMAIL_FUNCTIONS:
            args = call.args
        elif isinstance(call, S.Name) and call.name.upper() in _EMAIL_FUNCTIONS \
                and isinstance(stmt, S.Assignment):
            args = []  # `X = EmailSend` with its arguments wrapped onto the next line
        elif isinstance(stmt, S.Assignment):
            nested = [n for n in _index_nodes(call) if n.name.upper() in _EMAIL_FUNCTIONS
                      and len(n.args) >= 9]  # inside a larger expression (oracle: `...+1`)
            if not nested:
                continue
            args = nested[0].args
        else:
            continue
        tail = args[8:]
        if None in tail or (tail and not any(
                not isinstance(a, (S.Binary, S.Unary)) and bad(a) for a in tail)):
            continue
        key = (stmt.line, stmt.file)
        at = next((i for i, d in enumerate(diags.items) if (d.line, d.file) == key),
                  len(diags.items))
        while profile.single_pass and at < len(diags.items) and (diags.items[at].line, diags.items[at].file) == key \
                and diags.items[at].message.endswith(" is not defined."):
            at += 1
        new = [Diagnostic("error", stmt.line, _SERIALIN_DEST_MESSAGE, stmt.file)]
        if 6 <= len(args) < 9 and isinstance(stmt, S.Assignment):
            # The table `nargs` (6) leaves 6..8 arguments to this pass; fewer reach the arity check.
            from .expressions import _arity_message
            new.append(Diagnostic("error", stmt.line, _arity_message("few", stmt.text, profile),
                                  stmt.file))
        diags.items[at:at] = new


_PULSE_CHANNELS = {"cr6": re.compile(r"C[1-4]|U(?:[1-9]|1[0-2])"), "cr1000x": re.compile(r"C[1-8]")}


def check_pulsecount_channels(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR6/CR1000X: a second `PulseCount` naming a terminal an earlier one in the same
    `Scan..NextScan` already took is `<T> already used as PulseCount in this scan.` (CR1000X first
    adds `Pulsecount: channel <T> already in use or unavailable.`); docs/instructions.md. Static
    textual order: branches, `SubScan`s and the scan's other measurements do not matter."""
    channel = _PULSE_CHANNELS.get(profile.name)
    if channel is None:
        return
    used: set[str] | None = None
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "SCAN":
            used = set()
        elif kw == "NEXTSCAN" or (kw == "NEXT" and re.match(r"(?i)\s*Next\s+Scan\b", stmt.text or "")):
            used = None
        elif kw == "PULSECOUNT" and used is not None and len(stmt.args) > 2 \
                and isinstance(stmt.args[2], S.Name) and channel.fullmatch(stmt.args[2].name.upper()):
            name = stmt.args[2].name.upper()
            if name in used:
                if profile.name == "cr1000x":
                    diags.error(stmt.line, f"Pulsecount: channel {name} already in use or unavailable.",
                                stmt.file)
                diags.error(stmt.line, f"{name} already used as PulseCount in this scan.", stmt.file)
            used.add(name)


_SPI_PORTS = {"cr6": {101, 201, 205, 209}, "cr1000x": {101, 105}}
_PORT_NAME = re.compile(r"([CU])(\d+)")


def _port_number(arg: S.Expr | None) -> int | None:
    """`C<n>` -> 100+n, `U<n>` -> 200+n, an integer literal as is; `None` when not recognisable."""
    if isinstance(arg, S.Name):
        m = _PORT_NAME.fullmatch(arg.name.upper())
        return (100 if m.group(1) == "C" else 200) + int(m.group(2)) if m else None
    if isinstance(arg, S.Number) and float(arg.value).is_integer():
        return int(arg.value)
    return None


def check_spi_open_order(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR6/CR1000X: `SPIRead`/`SPIWrite` on a port no earlier (textual order, `Sub` bodies and
    branches included) `SPIOpen` opened -> `SPIOpen must precede SPIWrite.` (`...SPIRead.`);
    docs/instructions.md. Ports compare by terminal (`C1` = 101, `U1` = 201). An `SPIOpen` on a
    numeric port outside the valid set opens nothing but later reads/writes are still checked; one whose
    port is not a plain terminal/number (or, on CR6, a bare number below 100) makes the rest of the file
    unchecked."""
    valid = _SPI_PORTS.get(profile.name)
    if valid is None:
        return
    opened: set[int] = set()
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        kw = stmt.keyword.upper()
        if kw == "SPIOPEN":
            port = _port_number(stmt.args[0]) if stmt.args else None
            if port is None or (port < 100 and profile.name == "cr6"):
                return  # CR6 reports `Invalid port N` for a non-terminal number instead
            if port in valid:
                opened.add(port)
        elif kw in ("SPIREAD", "SPIWRITE") and len(stmt.args) == 3:
            port = _port_number(stmt.args[0])
            if port in valid and port not in opened:
                name = "SPIRead" if kw == "SPIREAD" else "SPIWrite"
                diags.error(stmt.line, f"SPIOpen must precede {name}.", stmt.file)


# Event/frozen DNP3 groups and the static group a `DNPVariable` must have assigned before them.
_DNP_BASE_GROUP = {2: 1, 22: 20, 31: 30, 32: 30}


def check_dnp_update(program: S.Program, profile, diags: Diagnostics, env=None) -> None:
    """`DNPUpdate` with no `DNPVariable` textually before it -> `DNP3Statics and/or DNP3Events are
    not defined` (no period; docs/instructions.md). A `DNPVariable` of any kind counts, so a
    malformed one under-reports -- except a group whose static group is not assigned yet
    (`Must assign group 30 before using group 32.`), after which every `DNPUpdate` gets the line
    too unless the static group is assigned later in the program. Also the `DNPVariable` type rules (docs/instructions.md "DNPVariable groups")."""
    defined = False
    failed_bases: list[int] = []     # static groups an event group was used before
    assigned: set[int] = set()
    types = symbols.declared_variable_types(program)
    every_group = {_dnp_group(st, env) for st in program.statements
                   if isinstance(st, S.Instruction) and st.keyword.upper() == "DNPVARIABLE"}
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction):
            kw = stmt.keyword.upper()
            if kw == "DNPVARIABLE":
                defined = True
                group = _dnp_group(stmt, env)
                if group is None:
                    continue
                base = _DNP_BASE_GROUP.get(group)
                if base is not None and base not in assigned:
                    diags.error(stmt.line, f"Must assign group {base} before using group {group}.",
                                stmt.file)
                    failed_bases.append(base)
                    continue
                assigned.add(group)
                src = stmt.args[0]
                kind = types.get(src.name.upper()) if isinstance(src, (S.Name, S.Index)) else None
                if group in (30, 32) and kind in ("STRING", "BOOLEAN"):
                    diags.error(stmt.line, f"For DNP3 group {group} variable must be of type Long.",
                                stmt.file)
                elif group in (1, 2) and kind in ("FLOAT", "LONG", "STRING"):
                    diags.error(stmt.line, f"For DNP3 group {group} variable must be of type Boolean.",
                                stmt.file)
            elif kw == "DNPUPDATE" and (not defined or any(b not in every_group
                                                              for b in failed_bases)):
                diags.error(stmt.line, "DNP3Statics and/or DNP3Events are not defined", stmt.file)


def _dnp_group(stmt: S.Instruction, env) -> int | None:
    """The constant integer `Group` (third argument) of a `DNPVariable`, else None."""
    if env is None or len(stmt.args) < 3 or stmt.args[0] is None or stmt.args[2] is None:
        return None
    try:
        value = consteval.value_of(stmt.args[2], env)
    except consteval.NotConstant:
        return None
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and value == int(value) else None


def check_portbridge_array(program: S.Program, profile, diags: Diagnostics, env) -> None:
    """`PortBridge`'s second argument must be a variable array whose first two dimensions are
    (2,3) and whose type is Long, else `Bridge parameter must be an Array(2,3) of type long.`;
    docs/instructions.md. A literal or a plainly declared variable of another shape/type is flagged;
    anything not resolvable (undeclared, `B()`, `B(1,1)`, `Const`, `Alias`) is left alone."""
    types = symbols.declared_variable_types(program)
    dims = symbols.declared_all_dims(program, env)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() != "PORTBRIDGE" \
                or len(stmt.args) != 2:
            continue
        arg = stmt.args[1]
        if isinstance(arg, S.Number):
            bad = True
        elif isinstance(arg, S.Name) and arg.name.upper() in types and arg.name.upper() in dims:
            shape = dims[arg.name.upper()]
            bad = types[arg.name.upper()] != "LONG" or len(shape) < 2 or shape[:2] != (2, 3)
        else:
            continue
        if bad:
            diags.error(stmt.line, "Bridge parameter must be an Array(2,3) of type long.", stmt.file)


# CDM instructions whose first two arguments are `CDMType, CPIAddress` and that were each observed to
# register their address (oracle, CR6; docs/instructions.md "CDM address and module type").
_CDM_ADDRESS_INSTRUCTIONS = frozenset({
    "CDM_BATTERY", "CDM_VOLTSE", "CDM_VOLTDIFF", "CDM_TCDIFF", "CDM_TCSE", "CDM_PANELTEMP",
    "CDM_BRHALF", "CDM_EXCITEV", "CDM_EXCITEI", "CDM_SW12", "CDM_SW5", "CDM_SWPOWER",
    "CDM_DELAY", "CDM_PULSEPORT", "CDM_MUXSELECT", "CDM_THERM107", "CDM_RESISTANCE",
    "CDM_ACPOWER", "CDM_CURRENTDIFF", "CDM_BRFULL6W", "CDM_BRHALF3W", "CDM_TCCOMP",
    "CDM_PERIODAVG",
})
# `CDMType` values that name a module (the other values 0..65536 are the unnamed `NULL` module;
# oracle sweep 0..299 on CR6 and CR1000X, spot values above).
_CDM_NAMED_TYPES = frozenset(range(100, 106)) | {200, 201, 202} | frozenset(range(220, 232)) | {260, 261}
_CDM_ANALOG = "Analog measurement"
# Text before ` instruction not supported by module NULL.` for a NULL-module type (one or two lines).
_CDM_NULL_MODULE = {
    "CDM_VOLTSE": [_CDM_ANALOG], "CDM_VOLTDIFF": [_CDM_ANALOG], "CDM_TCSE": [_CDM_ANALOG],
    "CDM_PANELTEMP": [_CDM_ANALOG], "CDM_BRFULL6W": [_CDM_ANALOG], "CDM_BRHALF3W": [_CDM_ANALOG],
    "CDM_THERM107": [_CDM_ANALOG], "CDM_RESISTANCE": [_CDM_ANALOG],
    "CDM_BATTERY": ["CDM_Battery"], "CDM_DELAY": ["CDM_Delay"], "CDM_PULSEPORT": ["CDM_PulsePort"],
    "CDM_TCCOMP": ["CDM_TCComp"], "CDM_ACPOWER": ["CDM_ACPower"], "CDM_PERIODAVG": ["CDM_PeriodAvg"],
    "CDM_EXCITEV": ["CDM_Excite"], "CDM_EXCITEI": ["CDM_Excite"], "CDM_MUXSELECT": ["CDM_MuxSel"],
    "CDM_SW12": ["SW"], "CDM_SW5": ["SW"],
    "CDM_SWPOWER": ["CDM_SWPower", "SW"], "CDM_CURRENTDIFF": ["CDM_CurrentDiff", _CDM_ANALOG],
}


def _delay_option(stmt: S.Instruction, env):
    """`CDM_Delay`'s constant `Option` (only option 0, the measurement delay, needs the module)."""
    try:
        return consteval.value_of(stmt.args[2], env) if len(stmt.args) > 2 else None
    except consteval.NotConstant:
        return None


def check_samplefieldcal(program: S.Program, profile, diags: Diagnostics) -> None:
    """`SampleFieldCal with no FieldCal in program.` on the first `SampleFieldCal` when the program
    holds no `FieldCal` or `FieldCalStrain` call (docs/instructions.md "SampleFieldCal"); only the
    first is reported."""
    stmts = [st for st in program.statements if isinstance(st, S.Instruction)]
    if any(st.keyword.upper() in ("FIELDCAL", "FIELDCALSTRAIN") for st in stmts):
        return
    for st in stmts:
        if st.keyword.upper() == "SAMPLEFIELDCAL" and "SAMPLEFIELDCAL" in profile.instructions:
            diags.error(st.line, "SampleFieldCal with no FieldCal in program.", st.file)
            return


def _cdm_type_value(kind) -> int | None:
    """A `CDMType` constant as the integer the compiler reads: a number truncates, a string is 0
    (oracle: `"fuzz"` and `1.5` are the `NULL` module); `TRUE` and other values are not modelled."""
    if isinstance(kind, bool):
        return None
    if isinstance(kind, str):
        return 0
    return int(kind) if isinstance(kind, (int, float)) and kind == kind and abs(kind) < 2 ** 31 else None


def cdm_null_module(stmt: S.Instruction, env) -> bool:
    """A constant `CDMType` that names no module on a CDM instruction that reports the `NULL`
    module; such a statement gets no `const_ranges` diagnostic (oracle, `CDM_TCComp` and
    `CDM_PeriodAvg`: only the module line)."""
    kw = stmt.keyword.upper()
    if kw not in _CDM_NULL_MODULE or not stmt.args:
        return False
    try:
        kind = _cdm_type_value(consteval.value_of(stmt.args[0], env))
    except consteval.NotConstant:
        return False
    return kind is not None and 0 <= kind <= 65536 and kind not in _CDM_NAMED_TYPES \
        and (kw != "CDM_DELAY" or _delay_option(stmt, env) == 0)


def check_cdm_address_types(program: S.Program, profile, diags: Diagnostics, env) -> None:
    """CR6/CR1000X CDM instructions with a constant `CDMType` and a `CPIAddress` (1..120);
    docs/instructions.md "CDM address and module type".

    - A type different from the first one used for that address -> `Address <n> already used with
      a different type CDM.` (every such statement; types compare by value, `VOLT116` is 223).
    - Otherwise a type that names no module (the `NULL` module) -> `<name> instruction not supported
      by module NULL.` (`SWPower` and `CurrentDiff` add a second line; `CDM_Delay` only for Option 0).
      The clash replaces it.
    A constant `CPIAddress` outside 1..120 -> `CPI Address must be 1..120.`
    first; the statement then counts as address 1 (docs/instructions.md "CPI address range").
    Non-constant arguments are skipped."""
    if profile.name not in ("cr6", "cr1000x"):
        return
    first: dict[int, object] = {}
    for stmt in program.statements:
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        if kw not in _CDM_ADDRESS_INSTRUCTIONS or len(stmt.args) < 2:
            continue
        try:
            address = consteval.value_of(stmt.args[1], env)
        except consteval.NotConstant:
            continue
        # The address converts as C's `(long)`; outside 1..120 (a string, `TRUE`, overflow) is
        # `CPI Address must be 1..120.` and the statement then registers as address 1 (oracle).
        address = consteval.to_long(address)
        if not 1 <= address <= 120:
            if kw != "CDM_PERIODAVG":  # its message is a `const_ranges` rule (printed last)
                diags.error(stmt.line, "CPI Address must be 1..120.", stmt.file)
            address = 1
        try:
            kind = consteval.value_of(stmt.args[0], env)
        except consteval.NotConstant:
            continue
        if isinstance(kind, (bool, str)) or not isinstance(kind, (int, float)) or kind != int(kind):
            # a string or fraction only gets the module line (its clash behaviour is not probed)
            if cdm_null_module(stmt, env):
                for name in _CDM_NULL_MODULE.get(kw, ()):
                    diags.error(stmt.line, f"{name} instruction not supported by module NULL.", stmt.file)
            continue
        if first.setdefault(address, kind) != kind:
            diags.error(stmt.line, f"Address {address} already used with a different type CDM.",
                        stmt.file)
        elif cdm_null_module(stmt, env):
            for name in _CDM_NULL_MODULE.get(kw, ()):
                diags.error(stmt.line, f"{name} instruction not supported by module NULL.", stmt.file)


def check_epi_scan_buffers(program: S.Program, profile, diags: Diagnostics, env) -> None:
    """Pipeline mode, CR6/CR1000X: `CDM_BridgeFilt`/`CDM_FFTFilt` in a main `Scan` with a constant
    interval of 1 mSec or more, per call (also inside conditionals): `Insufficient Scan buffers to
    read EPI data, minimum is N.` when the constant buffer count is below `min(100, 2 s / interval)`
    (integer division; intervals of 1 mSec..500 mSec only), and for `CDM_BridgeFilt` only (`CDM_FFTFilt` accepts 2 Sec) `Invalid Output Rate.` when the interval
    is not one of 1/2/5/10/20/50/100/200/500/1000 mSec, and `Maximum Scan interval for <name> is 1
    Sec.` past 1 Sec; docs/instructions.md. Not modelled inside `SubScan` (only the buffer message
    is), nor below 1 mSec."""
    from .. import mode
    if profile.name not in ("cr6", "cr1000x"):
        return
    stmts = [st for st in program.statements if isinstance(st, S.Instruction)]
    if not any(st.keyword.upper() in _EPI_FILTERS for st in stmts):
        return
    try:
        if mode.decide(program.statements, profile, lambda e: consteval.is_constant(e, env),
                       lambda e: consteval.value_of(e, env)) != "Pipeline":
            return
    except Exception:
        return
    slow = False
    us = minimum = None
    subscan = 0
    for st in stmts:
        kw = st.keyword.upper()
        if kw == "SLOWSEQUENCE":
            slow = True
        elif kw == "SCAN":
            us = minimum = None
            subscan = 0
            raw = None if slow else _scan_raw(st, env)
            if raw is not None and raw[0] * _US_PER_UNIT[raw[1]] >= 1000:
                us = raw[0] * _US_PER_UNIT[raw[1]]
                buffers = _const_int(st.args[2], env) if len(st.args) > 2 else None
                if buffers is not None and us <= 500_000 and buffers < min(100, 2_000_000 // us):
                    minimum = min(100, 2_000_000 // us)
        elif kw == "SUBSCAN":
            subscan += 1
        elif kw == "NEXT" and re.match(r"(?i)\s*Next\s+SubScan\b", st.text or "") or kw == "NEXTSUBSCAN":
            subscan = max(0, subscan - 1)
        elif kw == "NEXT" and re.match(r"(?i)\s*Next\s+Scan\b", st.text or "") or kw == "NEXTSCAN":
            us = minimum = None
        elif kw in _EPI_FILTERS and us is not None:
            if minimum is not None:
                diags.error(st.line, f"Insufficient Scan buffers to read EPI data, minimum is {minimum}.", st.file)
            if not subscan and kw == "CDM_BRIDGEFILT":
                if us not in _EPI_RATES_US:
                    diags.error(st.line, "Invalid Output Rate.", st.file)
                if us > 1_000_000:
                    diags.error(st.line, f"Maximum Scan interval for CDM_BridgeFilt is 1 Sec.", st.file)


_EPI_RATES_US = frozenset(ms * 1000 for ms in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000))
_EPI_FILTERS = frozenset({"CDM_BRIDGEFILT", "CDM_FFTFILT"})


def _const_int(arg, env) -> int | None:
    if arg is None or not consteval.is_constant(arg, env):
        return None
    try:
        value = consteval.value_of(arg, env)
    except consteval.NotConstant:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def check_avw200_scan_time(program: S.Program, profile, diags: Diagnostics, env) -> None:
    """Pipeline mode, CR6/CR1000X: in the one main `Scan` that holds every `AVW200`, each `AVW200`
    after which twice the running sum of `Reps` (2 s per sensor) exceeds the scan interval gets
    `Scan interval <I> secs is too small; AVW200 measurement time is <T> secs (2 secs per sensor).`;
    docs/instructions.md. Only whole-second intervals, constant `Reps` 1..16 and channels 1..32, and no `SubScan` or
    conditional block in the scan."""
    from .. import mode
    if profile.name not in ("cr6", "cr1000x") or "AVW200" not in profile.instructions:
        return
    stmts = [st for st in program.statements if isinstance(st, (S.Instruction, S.If, S.For, S.DoLoop, S.Case))]
    if not any(isinstance(st, S.Instruction) and st.keyword.upper() == "AVW200" for st in stmts):
        return
    try:
        decided = mode.decide(program.statements, profile, lambda e: consteval.is_constant(e, env),
                              lambda e: consteval.value_of(e, env))
    except Exception:
        return
    if decided != "Pipeline":
        return
    scan = None            # the open main-sequence Scan statement
    slow = False
    sums: list[tuple[S.Instruction, int, list]] = []   # (scan, interval seconds, [(stmt, reps)])
    for st in stmts:
        if not isinstance(st, S.Instruction):
            if scan is not None:
                return  # AVW200 in a conditional/loop has its own, unmodelled message
            continue
        kw = st.keyword.upper()
        if kw in ("SELECT", "WHILE") and scan is not None:
            return
        if kw == "SLOWSEQUENCE":
            slow = True
        elif kw == "SCAN":
            scan = None if slow else st
            if scan is not None:
                sums.append((st, None, []))
        elif kw == "NEXTSCAN" or (kw == "NEXT" and re.match(r"(?i)\s*Next\s+Scan\b", st.text or "")):
            scan = None
        elif kw == "SUBSCAN" and scan is not None:
            return
        elif kw == "AVW200":
            if scan is None:
                return
            sums[-1][2].append(st)
    holders = [entry for entry in sums if entry[2]]
    if len(holders) != 1 or sum(len(e[2]) for e in sums) != sum(
            1 for st in stmts if isinstance(st, S.Instruction) and st.keyword.upper() == "AVW200"):
        return
    scan_stmt, _, calls = holders[0]
    interval_us = _interval_us(scan_stmt.args[0] if scan_stmt.args else None,
                               scan_stmt.args[1] if len(scan_stmt.args) > 1 else None, env)
    if interval_us is None or interval_us % 1_000_000 or interval_us <= 0:
        return
    interval = int(interval_us // 1_000_000)
    total = 0
    for st in calls:
        try:
            reps = consteval.value_of(st.args[7], env) if len(st.args) > 7 else None
        except consteval.NotConstant:
            return
        if isinstance(reps, bool) or not isinstance(reps, (int, float)) or reps != int(reps) \
                or not 1 <= reps <= 16:
            return
        for position in (5, 6):   # AVWChan / MuxChannel: outside 1..32 the compiler adds `Bad parameter(s)`
            try:
                channel = consteval.value_of(st.args[position], env)
            except (consteval.NotConstant, IndexError):
                return
            if isinstance(channel, bool) or not isinstance(channel, (int, float)) or not 1 <= channel <= 32:
                return
        total += int(reps)
        if 2 * total > interval:
            diags.error(st.line, f"Scan interval {interval} secs is too small; AVW200 measurement "
                        f"time is {2 * total} secs (2 secs per sensor).", st.file)


def check_mqtt_publish_payload(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350 require MQTTPublish's second argument to be string-typed.

    CR6/CR1000X accept the same numeric literals and variables.  This only covers a direct
    assignment from the function, the observed form; composite expressions are left unchecked.
    """
    if profile.name not in {"cr300", "cr350"}:
        return
    types = _serialin_types(program, profile)
    string_consts = {
        stmt.name.upper() for stmt in program.statements
        if isinstance(stmt, S.ConstDecl) and isinstance(stmt.value, S.String)
    }
    for stmt in program.statements:
        if not isinstance(stmt, S.Assignment) or not isinstance(stmt.value, S.Index):
            continue
        call = stmt.value
        if call.name.upper() != "MQTTPUBLISH" or len(call.args) < 2:
            continue
        payload = call.args[1]
        if isinstance(payload, S.String):
            continue
        if isinstance(payload, S.Name) and (
                payload.name.upper() in string_consts or
                types.get(payload.name.upper()) == "STRING"):
            continue
        if isinstance(payload, (S.Number, S.Name)) or _numeric_expression(payload, types):
            diags.error(stmt.line, "A string or string variable is required", stmt.file)


def _numeric_expression(expr: S.Expr | None, types: dict) -> bool:
    """A negated number or an arithmetic `+ - * / ^` of numbers and declared non-String variables
    (oracle 2026-10-08, CR300/CR350: `1+2`, `F+1` and `-1` are `A string or string variable is
    required`, `"a"+"b"` and `Payload+"x"` compile). Anything else, a function call included, is
    left unchecked."""
    if isinstance(expr, S.Number):
        return True
    if isinstance(expr, S.Name):
        return types.get(expr.name.upper()) not in (None, "STRING")
    if isinstance(expr, S.Unary):
        return expr.op == "-" and _numeric_expression(expr.operand, types)
    if isinstance(expr, S.Binary):
        return expr.op in ("+", "-", "*", "/", "^") and _numeric_expression(expr.left, types) \
            and _numeric_expression(expr.right, types)
    return False


_SETTINGS_FIELD_KEYWORDS = {"SETSETTING": "Settings", "SETSTATUS": "Status or Settings"}


def _constant_name_text(arg: S.Expr | None, env: consteval.Env, depth: int = 0) -> str | None:
    """The text of a `SetSetting`/`SetStatus` name argument the compiler treats as constant: a
    string literal, a String `Const`, or `+` of those. A call such as `CHR(9)` is not constant
    there (oracle), so `consteval`'s own folding is not used. `None` for anything else."""
    if arg is None or depth > 16:
        return None
    if isinstance(arg, S.String):
        return arg.value
    if isinstance(arg, S.Name):
        value = env.consts.get(arg.name.upper())
        return value if isinstance(value, str) else None
    if isinstance(arg, S.Binary) and arg.op == "+":
        left = _constant_name_text(arg.left, env, depth + 1)
        right = _constant_name_text(arg.right, env, depth + 1)
        return None if left is None or right is None else left + right
    return None


def _check_port_index(stmt: S.Instruction, key: str, index: str | None, cfg: dict | None,
                      env: consteval.Env, profile, found: Diagnostics) -> None:
    """`Illegal dimension N` for `Baudrate(index)`/`Security(index)`; docs/instructions.md#field-index-dimension."""
    if not cfg or index is None or key not in cfg or not index.endswith(")"):
        return
    index = index[:-1]
    if re.fullmatch(r"0|-?[1-9]\d*", index):
        value = int(index)
    elif re.fullmatch(r"[A-Za-z]\w*", index) and index.upper() not in env.consts:
        symbol = profile.symbols.get(index.upper())
        value = symbol["value"] if symbol else 0
    else:
        return
    if not 1 <= value <= cfg[key]:
        found.error(stmt.line, f"Illegal dimension {value}", stmt.file)


def check_settings_fields(program: S.Program, profile, diags: Diagnostics, found: Diagnostics,
                          env: consteval.Env) -> None:
    """`%s is not a Settings Field Name.` / `%s is not a Status or Settings Field Name.` /
    `Cannot find %s` for a constant first argument of `SetSetting`/`SetStatus`;
    docs/instructions.md#setsetting-and-setstatus-field-names. Findings go to `found` (the
    caller's aside list); `diags` is only read, for the `CallTable` stop line."""
    fields = profile.settings_fields
    trim = fields["trim_trailing_blanks"]
    settings = {n.upper() for n in fields["settings"]}
    both = settings | {n.upper() for n in fields["status"]}
    stop: tuple[str, int] | None = None
    for d in diags.items:
        if d.severity == "error" and d.message.startswith("Could not find DataTable ") and d.line \
                and (stop is None or d.line < stop[1]):
            stop = (d.file, d.line)
    const_tables = symbols.const_table_fields(program)
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ENDPROG":
            break
        if not isinstance(stmt, S.Instruction) or not stmt.args:
            continue
        kw = stmt.keyword.upper()
        label = _SETTINGS_FIELD_KEYWORDS.get(kw)
        if label is None or kw not in profile.instructions:
            continue
        if stop is not None and stmt.file == stop[0] and stmt.line > stop[1]:
            continue
        name = _constant_name_text(stmt.args[0], env)
        if name is None:
            continue
        if "." in name:
            table, field = name.split(".", 1)
            fields_for_table = const_tables.get(table.strip().upper())
            if fields_for_table is None:
                found.error(stmt.line, f"Cannot find {table}", stmt.file)
            elif field.split("(", 1)[0].strip().upper() not in fields_for_table and not (
                    field.split("(", 1)[0].strip().upper() == "APPLYANDRESTART" and
                    fields_for_table):
                found.error(stmt.line, f"Cannot find {field.split('(', 1)[0].strip()}", stmt.file)
            continue
        if "(" in name:
            # `Name(index)`: only the field name before the first `(` is looked up here; the
            # index is validated separately (`Illegal dimension N`, not implemented).
            name, _, index = name.partition("(")
            if not name:
                continue
        else:
            index = None
        key = (name.rstrip(" \t") if trim else name).upper()
        if key in (both if kw == "SETSTATUS" else settings):
            _check_port_index(stmt, key, index, fields.get("index_size"), env, profile, found)
            continue
        shown = name if name else fields["empty_name"]
        found.error(stmt.line, f"{shown} is not a {label} Field Name.", stmt.file)
