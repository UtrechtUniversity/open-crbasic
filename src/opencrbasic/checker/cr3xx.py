"""CR300/CR350-specific checks that do not generalise to CR6/CR1000X: message variants and
validations these two loggers' own smaller instruction/function set needs. Owned separately from
the shared `checker/*` modules (`PLAN.md`); `check` below is a no-op on every other logger, so
`checker/__init__.py`'s own call site can stay a single unconditional line. Rules and oracle
evidence: `docs/loggers.md`.
"""

from __future__ import annotations

import re

from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from . import expressions, symbols

# `SerialOpen`'s own `ComPort` argument (position 0, the only position checked here) is the one
# `Serial*` function on CR300/CR350 whose compile-time validator additionally requires a genuine
# compile-time constant, reporting `ComPort 0 illegal.` then `Parameter must be a constant
# integer: %s.` when it is not -- oracle-confirmed (`docs/loggers.md`): an undefined name there
# gets the ordinary per-name `%s is not defined.` line(s) first (the same shape every other
# unresolved-name position on these two loggers already gets), **plus** these two extra lines,
# which no other `Serial*` function shares (`SerialClose`/`SerialFlush`/`SerialOut`/`SerialIn`/...
# report only the plain per-name undefined-name line(s) for the same argument shape, nothing
# else -- oracle-confirmed each individually). A live, declared *variable* in this position also
# triggers the same two extra lines with no undefined-name line (it is not itself undefined) --
# "not a compile-time constant" is the real trigger, not "undefined" specifically (oracle:
# `SerialOpen(MyPort,...)`, `MyPort` a declared `Public`, still gets `ComPort 0 illegal.`/
# `Parameter must be a constant integer: ...`). Deliberately **not** modelled here: a genuine
# constant that is merely out of range (oracle: `SerialOpen(99,...)` gives `ComPort 99 illegal.`
# plus a *different* second line, `No Matching, or out of place ) in expression: ...`, not this
# one) -- the valid `ComPort` value set for CR300/CR350's own smaller hardware was not oracle-
# pinned down this round, and guessing it risks flagging a value this project cannot confirm is
# actually invalid (AGENTS.md: never guess into a false reject). Only the unambiguous "not a
# constant at all" trigger (an undefined name or a live variable, never a resolvable value) is
# implemented, which never risks flagging a value the real compiler would accept.
_SERIALOPEN = "SERIALOPEN"
_COMPORT_ARG_POSITION = 0
# Every `Serial*` function whose first argument is a `ComPort` (`data/<logger>/functions.json`:
# same `argtypes[0]` shape as `SerialOpen`'s own position 0) -- oracle-confirmed for `SerialOpen`/
# `SerialClose`/`SerialFlush`/`SerialOut` individually; `SerialIn`/`SerialInBlock`/`SerialInChk`/
# `SerialBrk` added on the same documented convention (a `Serial*` function's own first argument
# is always its `ComPort`, per every CHM `Syntax` line for this family), not each individually
# oracle-probed -- safe either way, since an inclusion here only ever *adds* an already-correct-
# for-`SerialOpen`-style undefined-name diagnostic (never the `SerialOpen`-only ComPort/constant
# pair, gated separately below), so a wrong inclusion costs nothing worse than under-reporting.
_SERIAL_FUNCTIONS_WITH_COMPORT_ARG = frozenset({
    "SERIALOPEN", "SERIALCLOSE", "SERIALFLUSH", "SERIALOUT", "SERIALIN", "SERIALINBLOCK",
    "SERIALINCHK", "SERIALBRK",
})


def check(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350 only; a no-op on every other logger. Oracle-confirmed **ungated** (fires
    alongside unrelated pass-1/pass-2 errors elsewhere in the same file, `docs/loggers.md`:
    `acceptdatarecords_2.CR6`'s own `SerialOpen(COMC1,...)` diagnostic prints together with a
    separate, later `CallTable`-shaped issue in the same file) -- called unconditionally,
    alongside `expressions.check_consts`.
    """
    if profile.name not in ("cr300", "cr350"):
        return
    known = expressions._known_names(program, profile)
    variable_names = symbols.declared_variable_names(program)
    for stmt in program.statements:
        if not isinstance(stmt, S.Call):
            continue
        name = stmt.name.upper()
        if name not in _SERIAL_FUNCTIONS_WITH_COMPORT_ARG:
            continue
        if _COMPORT_ARG_POSITION >= len(stmt.args) or stmt.args[_COMPORT_ARG_POSITION] is None:
            continue
        arg = stmt.args[_COMPORT_ARG_POSITION]
        names = expressions._undefined_names(arg, known)
        for undefined_name in names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=undefined_name),
                        stmt.file)
        if name != _SERIALOPEN:
            continue
        is_variable = bool(expressions._variable_refs(arg, variable_names))
        if names or is_variable:
            diags.error(stmt.line, "ComPort 0 illegal.", stmt.file)
            # The echoed statement text runs to the physical end of line and (oracle-confirmed,
            # `docs/loggers.md`) includes that line's own trailing newline before the closing
            # period -- the same quirk `docs/checker.md` documents for `Units`, here embedded
            # directly in the message text since `Diagnostic.cr6comp` does no wrapping of its own.
            diags.error(stmt.line, f"Parameter must be a constant integer: {stmt.text}\n.",
                        stmt.file)


# `check_too_many_cascade` (Round 7b, broadened this round -- see `docs/loggers.md` "Too many
# parameters cascade, widened" for the oracle transcript): once any undefined-name-class error
# fires anywhere in the file (an instruction-argument or assignment-RHS name `expressions.check`/
# `check_consts` could not resolve, the const-family "rejects a variable" `Variable illegal in
# Parameter: %s.` finding, **or now also a genuine `Too many parameters:  %s.` finding from
# `instr_rules.check_too_many` itself** -- oracle-confirmed self-chaining, scratch probe `t3.cr6`/
# `t4.cr6`: a real `PortSet(C1,1,2,3)` excess taints the *next* statement the same way an undefined
# name does), CR300/CR350's own parser leaves some shared state corrupted for the **rest of the
# file, not just the next statement** (widened from Round 7b's own single-shot model, which a
# three-and-four-statement scratch chain, `t.cr6`/`t3.cr6`, and the corpus's own `dewpoint_1.CR6`
# (3 in a row), `runprogram_1.CR6`/`filemanage_1.CR6`/`goesdata_1.CR6` (2 in a row) all show is too
# narrow -- Round 7b's own corpus sample never happened to have more than one qualifying follow-up
# statement, so "stops after one" was indistinguishable from "keeps going" until this round):
# **every** subsequent statement that is a recognised `S.Instruction` written with two or more
# arguments gets its own spurious `Too many parameters:  <its own second argument's verbatim
# text>.`, `checker/instr_rules.py`'s own real message reused for a shape that instruction's own
# argument count never earns on its own (`WriteIO(1,2)`, `PortSet(1,2)`, ... all compile clean
# standalone) -- oracle-confirmed (scratch `t.cr6`: an undefined name followed by three `PortSet`s
# and a `Delay`, all four flagged, each echoing its own second argument, not the first one skipped
# over). This **replaces** whatever that tainted statement's own checks would otherwise have found
# for it, rather than merely filling a silent gap -- oracle-confirmed (`t2.cr6`: `PortSet(C1,
# BadVar2)` after an earlier undefined name gets only `Too many parameters:  BadVar2.`, never a
# second `BadVar2 is not defined.` of its own; `t3.cr6`: a tainted `PortSet(C1,1,2,3)`, which would
# genuinely overflow on its own (2 declared params, 4 given), still echoes its own second argument
# (`1`), never the true excess position (`2`) `instr_rules.check_too_many` would compute for it
# standalone). Confirmed **not** anchored to the *specific* failing statement -- when several
# statements in a row each have their own undefined-name error, only the search from the *last* one
# starts the cascade (untouched from Round 7b) -- and the search itself skips forward, without
# breaking the chain, over any clean statement that does not itself qualify (fewer than two
# arguments, or not a recognised instruction at all -- a plain assignment or a one-argument call
# such as `Battery(BattV)` is silently passed over, not itself flagged and not a stopping point, and
# neither is an *unresolved* bare call such as `Move(...)`/`Routes(...)` naming an instruction this
# project's own table has no record of on this logger -- corpus: `routes_1.CR6`, where `MyRoute is
# not defined.` keeps firing normally on those two lines, untouched by the taint, because neither
# parses to a recognised `S.Instruction` at all) -- confirmed with a bare assignment and a
# one-argument call each skipped in the middle, and a `'comment` line skipped for free (comments are
# never `program.statements` entries at all). **CR6/CR1000X do not share this at all**
# (oracle-confirmed with the same shape, an undefined const-family name immediately followed by a
# two-argument instruction: no extra line).
#
# Because the taint can originate from a pass-1/ungated finding (`cr3xx.check`'s own `SerialOpen`/
# `ComPort` finding, or `instr_rules.check_too_many`'s own genuine finding -- corpus:
# `acceptdatarecords_1.CR6`, `SerialOpen (COMC1,...)`'s `COMC1 is not defined.` taints every
# following two-plus-argument instruction to end of file, `Scan`'s own `Sec` and
# `AcceptDataRecords`'s own `32769`, despite that finding being **ungated** and thus, in this
# project's own pass model, skipping tier B -- where the *previous*, narrower version of this
# function lived -- entirely), this is no longer called from inside the `if not diags.errors:`
# tier-B block the way `check_expr_cascade` was: `checker/__init__.py` now calls it once,
# unconditionally, after every other diagnostic-producing pass has run, so it can see -- and rewrite
# -- the complete, final diagnostic list regardless of which tier first tainted the file. It is
# still a no-op when nothing in the file ever taints (no `_is_cascade_trigger` match and no
# `Too many parameters:` message already present).
_UNRESOLVED_SUFFIX = " is not defined."
_VARIABLE_ILLEGAL_PREFIX = "Variable illegal in Parameter:"
_TOO_MANY_PREFIX = "Too many parameters:"


def _is_cascade_trigger(diag) -> bool:
    return diag.severity == "error" and (
        diag.message.endswith(_UNRESOLVED_SUFFIX) or diag.message.startswith(_VARIABLE_ILLEGAL_PREFIX)
        or diag.message.startswith(_TOO_MANY_PREFIX)
    )


def check_too_many_cascade(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350 only; a no-op on every other logger and when nothing in the file ever taints
    (see this module's comment above for the oracle evidence and the exact shape implemented).
    Called from `checker/__init__.py` once, unconditionally, after every other pass -- see that
    comment for why this can no longer be tier-B-only the way `check_expr_cascade` was.
    """
    if profile.name not in ("cr300", "cr350"):
        return
    # A single left-to-right sweep, **not** "find the last trigger line, then walk forward from
    # it": `tainted` latches on the first qualifying statement whose own (pre-existing, untouched)
    # diagnostics are trigger-shaped, and then stays on for every remaining statement, rewriting
    # each qualifying one in turn -- oracle-confirmed this is the right shape, not a re-scan, by
    # `t2.cr6` (`docs/loggers.md`): a tainted `PortSet(C1,BadVar2)` is *itself* trigger-shaped (its
    # own, pre-taint `BadVar2 is not defined.`), but must still be **rewritten**, not treated as a
    # fresh anchor that leaves it untouched and only starts the chain after it -- a "find the last
    # trigger" re-scan gets this backwards (picks that statement as the new anchor and leaves its
    # own stale diagnostic standing). A statement is only ever left as the untouched anchor when
    # `tainted` was still `False` on entry to it (the *first* trigger in the file, matching
    # `dewpoint_1.CR6`'s own first `VoltSE` keeping its real `mv5000C is not defined.` pair while
    # its identical-shaped successor is swallowed).
    tainted = False
    for stmt in program.statements:
        key = (stmt.line, stmt.file)
        existing = [d for d in diags.items if (d.line, d.file) == key]
        if tainted:
            if (isinstance(stmt, S.Instruction) and len(stmt.args) >= 2
                    and stmt.args[1] is not None):
                text = expressions._arg_text(stmt, 1)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                diags.error(stmt.line, f"Too many parameters:  {text}.", stmt.file)
            continue
        if any(_is_cascade_trigger(d) for d in existing):
            tainted = True


# `check_unknown_line_words` (this round): a line that parses as nothing recognisable at all --
# every corpus instance found this round is CHM example prose left uncommented, e.g. `To force
# double-precision arithmetic, declare ...` -- becomes an `S.Unparsed` statement and hits
# `checker/structure.py`'s own generic `Invalid, or out of place expression: %s.` fallback on
# every logger. CR300/CR350 additionally print one `%s is not defined.` line (`profile.
# unresolved_symbol_message`) per *leading* bare word of that statement's own source text, ahead of
# the generic line, following a "resolve one syntactic chain, plus one trailing word" shape --
# oracle-confirmed this round (`docs/loggers.md` "Round 8", scratch probes, `t.cr6`, both
# `cr300comp.exe`/`cr350comp.exe`, identical): starting at the statement's first word,
#   1. A run of bare words joined only by a single connecting *operator* character (any of
#      `+-*/^&<>=`, immediately adjacent, no surrounding space -- `alpha-beta-gamma delta` flags
#      `alpha`, `beta`, `gamma`, all three, i.e. the chain is not capped) all get checked, in
#      order, as if each were a real value reference.
#   2. Once that chain stops (the next thing is *not* one more `<op><word>` link), exactly one more
#      bare word is checked if -- and only if -- it is separated from the chain by plain
#      whitespace and nothing else (`foo bar baz qux` flags only `foo`, `bar`; `foo, bar` flags
#      only `foo`, no peek at `bar` -- a comma is not "plain whitespace"; `Part 1.` flags only
#      `Part` -- the peek must itself be a bare word, not a number). This one word is checked
#      whether or not it was actually reachable as valid syntax; it, too, only reports if
#      unresolved.
#   3. Whatever is or is not flagged, scanning stops there for good -- nothing further along the
#      line is ever checked (`foo bar baz` never touches `baz`; `This is not defined test` never
#      touches `not`/`defined`/`test`, because `is` is one of this project's own extracted
#      `functions.json` names -- see below -- and already terminates the one-word peek).
# "Resolved" here is `expressions._known_names(program, profile)`, the *same* set every other
# CR300/CR350 undefined-name check in this project already uses -- confirmed sufficient with no
# separate reserved-word list needed, because this project's own extracted per-logger tables
# already carry CRBasic's syntax keywords alongside its predefined symbols (`Double`/`Long`/
# `String`/`Boolean` in `symbols.json`, `To`/`Is` in `functions.json`, `Public` in
# `instructions.json` -- confirmed present for CR300; not re-checked for CR350 individually, but
# both loggers' own tables come from the same `extract_tables.py` process and every shape probed
# above (`Double foo`, `This is not defined test`, ...) came back byte-identical on both compilers
# this round, so the same table shape is assumed, not re-verified name-by-name).
#
# What this deliberately does *not* attempt, for lack of oracle coverage this round (documented
# rather than guessed at, `AGENTS.md`): a connecting operator with surrounding *whitespace*
# (`alpha - beta`, untested -- only the no-space `alpha-beta` form was probed); any operator other
# than the eight tested characters; and a leading word that is itself the very first token of the
# *whole statement* and already resolves (`Double foo` was probed and *does* still flag `foo` --
# handled correctly by this algorithm, since resolution is checked only when deciding whether to
# *report* a word, never when deciding whether to keep walking -- but no case where the walk
# itself needs to treat a resolved word differently from an unresolved one was found, so none is
# implemented).
_GENERIC_FALLBACK_PREFIX = "Invalid, or out of place expression: "
_LINE_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LINE_OP_RUN_RE = re.compile(r"[-+*/^&<>=]+")
_LINE_WS_RE = re.compile(r"[ \t]+")


def _leading_unknown_words(text: str, known: set[str]) -> list[str]:
    """The ordered list of `text`'s own leading bare words this project believes CR300/CR350
    would report as `%s is not defined.` ahead of the generic fallback for it -- see this
    module's `check_unknown_line_words` comment above for the oracle evidence and the exact walk
    this implements. `known` is the same upper-cased name set `expressions._known_names` builds;
    a word already in it is walked over (it can still be part of the chain) but never returned.
    """
    # Only leading whitespace is skipped to find the statement's own first token; if that token
    # is not itself a bare word (a number, punctuation, ...), no walk is attempted at all -- an
    # inferred, deliberately conservative default (`AGENTS.md`: no oracle probe this round covered
    # a statement starting with anything other than a bare word).
    pos = len(text) - len(text.lstrip(" \t"))
    if not _LINE_WORD_RE.match(text, pos):
        return []
    words: list[str] = []
    extending = True  # False once we're consuming the single trailing "peek" word.
    while True:
        word_match = _LINE_WORD_RE.match(text, pos)
        if not word_match:
            break
        words.append(word_match.group(0))
        pos = word_match.end()
        if not extending:
            break
        op_match = _LINE_OP_RUN_RE.match(text, pos)
        if op_match:
            pos = op_match.end()
            continue
        ws_match = _LINE_WS_RE.match(text, pos)
        if ws_match and _LINE_WORD_RE.match(text, ws_match.end()):
            pos = ws_match.end()
            extending = False
            continue
        break
    return [w for w in words if w.upper() not in known]


def check_unknown_line_words(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350 only; a no-op on every other logger, and on a file with no `S.Unparsed`
    statement at all. Called from `checker/__init__.py`, right after `check_expr_cascade`, once
    `structure.check_pass2` (and `expressions.check`) have had a chance to emit the generic
    `Invalid, or out of place expression: %s.` fallback for an `S.Unparsed` statement -- see
    `checker/structure.py`'s own `S.Unparsed`-`else` branch, whose before-`BeginProg` follow-up/
    stop is separately carved out for CR300/CR350 there (`profile.undefined_call_message`, mirroring
    `_check_call_pass2`'s identical carve-out; `docs/loggers.md` "Round 8" Finding 2), so a file with
    several such statements gets every one of them reacted to here, not just the first.

    Deliberately reacts only to diagnostics already sitting in `diags.items` (the same
    after-the-fact pattern `check_expr_cascade` above already uses) rather than re-deriving *why*
    `structure.py` chose to emit the fallback for a given statement, so it stays correct however
    that gating logic is refactored, and is restricted to lines that `program.statements` itself
    marks `S.Unparsed` -- deliberately **not** every statement shape that can reach the same
    generic message (`checker/structure.py`'s own docstrings: a bare unresolved call already gets
    its own, already-implemented, single-name CR300/CR350 line via `profile.
    undefined_call_message` above, in `_check_call_pass2`; an assignment with an unresolved target
    was not oracle-tested this round for this same leading-word behaviour, so is left alone here
    rather than guessed at).
    """
    if profile.name not in ("cr300", "cr350"):
        return
    if not diags.items:
        return
    unparsed_lines = {(s.line, s.file) for s in program.statements if isinstance(s, S.Unparsed)}
    if not unparsed_lines:
        return
    known: set[str] | None = None
    new_items: list[Diagnostic] = []
    changed = False
    for d in diags.items:
        if (d.severity == "error" and d.message.endswith(".")
                and d.message.startswith(_GENERIC_FALLBACK_PREFIX)
                and (d.line, d.file) in unparsed_lines):
            if known is None:
                known = expressions._known_names(program, profile)
            text = d.message[len(_GENERIC_FALLBACK_PREFIX):-1]
            for word in _leading_unknown_words(text, known):
                new_items.append(Diagnostic(
                    "error", d.line, profile.unresolved_symbol_message.format(name=word), d.file,
                    True))
                changed = True
        new_items.append(d)
    if changed:
        diags.items[:] = new_items
        return
