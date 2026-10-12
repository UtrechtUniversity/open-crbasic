"""CR300/CR350-specific checks that do not generalise to CR6/CR1000X: message variants and
validations these two loggers' own smaller instruction/function set needs. Owned separately from
the shared `checker/*` modules (`PLAN.md`); `check` below is a no-op on every other logger, so
`checker/__init__.py`'s own call site can stay a single unconditional line. Rules and oracle
evidence: `docs/loggers.md`.
"""

from __future__ import annotations

import collections
import dataclasses

import re

from .. import consteval, lexer
from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from .. import bindings
from . import expressions, instr_rules, structure, symbols

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


# Baud rates `SerialOpen` accepts on `ComRS232` (oracle, 2026-10-01; CR6 from the decompiled
# `FUN_004735b0`, which compares the absolute value: 300 only on ports above ComRS232, 230400 and
# 460800 only on it). CR300/CR350 also reject 300, 600 and any negative rate.
_BAUDS = frozenset({0, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 76800, 115200, 230400,
                    460800})


def _check_baud(stmt: S.Call, profile, diags: Diagnostics, consts: dict | None = None) -> None:
    if len(stmt.args) < 2 or not (isinstance(stmt.args[0], S.Name)
                                  and stmt.args[0].name.upper() == "COMRS232"):
        return
    arg = stmt.args[1]
    sign = 1
    if isinstance(arg, S.Unary) and arg.op == "-":
        sign, arg = -1, arg.operand
    if not (isinstance(arg, S.Number) and isinstance(arg.value, int)):
        # a constant expression is evaluated (oracle, all four loggers, 2026-10-10: `Hr` -> `4 is
        # illegal baud rate.`, `Hr+1` -> 5, `True` -> -1, `uSec` -> 0 is fine; a string is accepted)
        try:
            value = consteval.value_of(stmt.args[1], consteval.Env(profile, dict(consts or {})))
        except (consteval.NotConstant, TypeError, ValueError, ZeroDivisionError):
            return
        if isinstance(arg, S.Number) or isinstance(value, str) or value != int(value):
            return
        arg, sign = S.Number(stmt.line, stmt.file, abs(int(value)), str(abs(int(value)))), (
            -1 if value < 0 else 1)
    baud = sign * arg.value
    if not -2**31 <= baud < 2**31:
        return  # `Parameter must be integral.` instead (`checker/intervals.py`)
    if profile.name in ("cr300", "cr350"):
        ok = baud in _BAUDS - {600}
    else:
        ok = abs(baud) in _BAUDS
    if not ok:
        diags.error(stmt.line, f"{baud} is illegal baud rate.", stmt.file)


def check_dropped_tail(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: the second, side-by-side expression of an unclosed `Abs`-like call (`Y = Abs(X
    Q`, `If Abs(X Q Then`, `Abs(X Then`) is name-resolved, unlike on CR6/CR1000X: `Q is not defined.`,
    `Then is not defined.` (oracle, 2026-10-12, `docs/checker.md` "Round 15"). The parser drops it
    from the statement and keeps the tokens in `dropped_tail`; a call (`Q(1)`) or a dotted name in
    it is left alone (those shapes have their own messages)."""
    if profile.name not in ("cr300", "cr350"):
        return
    known = None
    for stmt in program.statements:
        tail = getattr(stmt, "dropped_tail", None)
        if not tail or any(t.kind == "OP" and t.text in ("(", ".", ",") for t in tail):
            continue
        if known is None:
            known = expressions._known_names(program, profile)
        for t in tail:
            if t.kind == "IDENT" and t.upper not in known:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=t.text), stmt.file)


def check(program: S.Program, profile, diags: Diagnostics) -> None:
    """`SerialOpen`'s `ComPort`: a string or variable on all four loggers; on CR300/CR350 also an
    undefined name, and the other `Serial*` functions' undefined first argument. Oracle-confirmed
    **ungated** (fires
    alongside unrelated pass-1/pass-2 errors elsewhere in the same file, `docs/loggers.md`:
    `acceptdatarecords_2.CR6`'s own `SerialOpen(COMC1,...)` diagnostic prints together with a
    separate, later `CallTable`-shaped issue in the same file) -- called unconditionally,
    alongside `expressions.check_consts`.
    """
    cr3 = profile.name in ("cr300", "cr350")
    known = expressions._known_names(program, profile)
    variable_names = symbols.declared_variable_names(program)
    for stmt in program.statements:
        if not isinstance(stmt, S.Call):
            continue
        name = stmt.name.upper()
        if name not in _SERIAL_FUNCTIONS_WITH_COMPORT_ARG:
            continue
        if name == _SERIALOPEN and not stmt.explicit and not stmt.args:
            diags.error(stmt.line, "ComPort 0 illegal.", stmt.file)  # bare `SerialOpen[()]`, all four loggers (oracle, 2026-10-08)
        if _COMPORT_ARG_POSITION >= len(stmt.args) or stmt.args[_COMPORT_ARG_POSITION] is None:
            continue
        arg = stmt.args[_COMPORT_ARG_POSITION]
        names = expressions._undefined_names(arg, known)
        for undefined_name in names if cr3 else []:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=undefined_name),
                        stmt.file)
        if name != _SERIALOPEN:
            continue
        _check_baud(stmt, profile, diags, program.consts)
        is_variable = bool(expressions._variable_refs(arg, variable_names))
        if not (names or is_variable or isinstance(arg, S.String)) and len(stmt.args) > 3 \
                and stmt.args[3] is not None:
            # `TXDelay` (oracle, all four loggers, 2026-10-12): a constant outside 0..14000000 is
            # `%d usec delay before transmit is illegal, must be 0..14000000`; a variable is
            # `Parameter must be a constant integer: <statement>.`
            delay = stmt.args[3]
            if expressions._variable_refs(delay, variable_names):
                sep = "\n" if cr3 and stmt.at_eol else ""
                diags.error(stmt.line, f"Parameter must be a constant integer: {stmt.text}{sep}.",
                            stmt.file)
                continue
            try:
                value = consteval.value_of(delay, consteval.Env(profile, dict(program.consts or {})))
            except (consteval.NotConstant, TypeError, ValueError, ZeroDivisionError):
                value = None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value == int(value) \
                    and -2**31 <= value < 2**31 and not 0 <= value <= 14000000:
                # (beyond 32 bits: `Parameter must be a constant integer` only, `intervals.py`)
                diags.error(stmt.line, f"{int(value)} usec delay before transmit is illegal, must be "
                            "0..14000000", stmt.file)
        if names or is_variable or isinstance(arg, S.String):
            # A string or variable ComPort, all four loggers, pass 1 (oracle, 2026-10-01:
            # `SerialOpen("fuzz",...)`). CR300/CR350 echo the line end (`docs/loggers.md`).
            diags.error(stmt.line, "ComPort 0 illegal.", stmt.file)
            if not cr3 and names and not is_variable:
                continue  # CR6/CR1000X: an undefined name gives only this line (docs/checker.md)
            sep = "\n" if cr3 and stmt.at_eol else ""
            diags.error(stmt.line, f"Parameter must be a constant integer: {stmt.text}{sep}.",
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
_LEFTOVER_LEAD_RE = re.compile(r"\s*(?:\(\s*([A-Za-z_]\w*)\s*[,(]|([A-Za-z_]\w*)\s*,)")
_VARIABLE_ILLEGAL_PREFIX = "Variable illegal in Parameter:"
_TOO_MANY_PREFIX = "Too many parameters:"


# Declaration errors taint the same way (oracle, CR300 and CR350, 2026-09-27: each of these
# followed by a clean `Scan (1,Sec,0,0)` gives `Too many parameters:  Sec.` on it).
_DECLARATION_TRIGGERS = ("instruction must be in a ConstTable", "already declared as a variable.", "is already declared as a CONST.",
                         "illegal, starts with a number.", "not yet declared.", "so cannot be aliased.",
                         "not supported", "Variable not named.", " is illegal baud rate.",
                         "already defined as an internal function.", "is a predefined CONST.",
                         "Only one ConstTable allowed.", "EndConstTable with no ConstTable.",
                         "Missing variable name.", "; cannot be numeric.",
                         "Too few parameters in instruction.", "Const with no assignment.",
                         " unknown Const type")
_DECLARATION_TRIGGER_PREFIXES = ("Too many Slow Sequences", "Duplicate Alias", "Need closing )", "Illegal END or EXIT ", "Illegally dimensioned aliased name", "Alias is illegally dimensioned.", "Invalid Variable declaration",
                                 "EndSelect w/o Select", "EndStructure without Structure", "String without end quote",
                                 "Call argument required", "No Matching, or out of place )",
                                 "Expression expects ", "Incomplete expression:", "ESS variable ",
                                 "Invalid or missing parameter.",
                                 "Parameter must be a constant integer:",
                                 "Invalid, or out of place expression:", "Invalid assignment in expression:",
                                 "Illegal destination variable type", "Invalid Variable or Array:",
                                 "Cannot recursively call functions.", "Parameter must be integral.",
                                 "Interval (", "Illegally dimension: ", "Scan interval must be integrally divisible",
                                 "Dimensions not declared:")


def _is_short_instruction(stmt, profile) -> bool:
    """An instruction call written with fewer arguments than it requires."""
    record = profile.instructions.get(stmt.keyword.upper()) if isinstance(stmt, S.Instruction) else None
    return record is not None and instr_rules.given_count(stmt, profile) < instr_rules._required_count(
        instr_rules._real_params(record["params"]))


def _is_cascade_trigger(diag) -> bool:
    return diag.severity == "error" and diag.cascade and (
        diag.trigger or diag.message.endswith(_UNRESOLVED_SUFFIX) or diag.message == _TCPOPEN_HANDLE or diag.message.startswith(_VARIABLE_ILLEGAL_PREFIX)
        or diag.message.startswith(_TOO_MANY_PREFIX)
        or diag.message in ("Miss match of types", "Illegal object id.", "Illegal OID since already declared.")
        or diag.message.startswith("SNMP OID must be greater than")  # SNMPVariable (oracle, CR300/CR350)
        or diag.message.endswith(_DECLARATION_TRIGGERS)
        or diag.message.startswith(_DECLARATION_TRIGGER_PREFIXES)
    )


def _reword_prose(program: S.Program, profile, diags: Diagnostics, stmt: S.Unparsed,
                  gone: set[str]) -> None:
    """A prose line after the cascade has started: a name whose declaration the cascade dropped is
    undefined there too (oracle, CR300, 2026-10-08: `Current` declared after the first error, then
    prose mentioning `current`). Re-derives the leading-word lines of the generic fallback."""
    key = (stmt.line, stmt.file)
    own = [i for i, d in enumerate(diags.items) if (d.line, d.file) == key]
    echoes = {_echo_key(stmt.text), _echo_key(structure.condition_part(stmt.text) or stmt.text)}
    fb = next((i for i in own if diags.items[i].message.startswith(_GENERIC_FALLBACK_PREFIX)
               and _echo_key(diags.items[i].message[len(_GENERIC_FALLBACK_PREFIX):-1])
               in echoes), None)
    if fb is None or not all(diags.items[i].message.endswith(_UNRESOLVED_SUFFIX)
                             for i in own if i < fb):
        return
    text = diags.items[fb].message[len(_GENERIC_FALLBACK_PREFIX):-1]
    lead = _INSTRUCTION_PROSE_RE.match(text)
    info = profile.instructions.get(lead.group(1).upper()) if lead else None
    if _TABLE_LINE_RE.match(text) or (info and info["params"]):
        return
    known = prose_known(program, profile) - declared_later(first_declarations(program), stmt.line,
                                                           stmt.file) - gone
    words = _leading_unknown_words(text, known)
    old = [diags.items[i].message for i in own if i < fb]
    new = [profile.unresolved_symbol_message.format(name=w) for w in words]
    if old != new:
        fresh = [Diagnostic("error", stmt.line, m, stmt.file, True) for m in new]
        diags.items[own[0] if own else fb:fb] = fresh


def _tainted_prose_instruction(stmt: S.Unparsed, profile) -> str | None:
    """`""`: the prose is an instruction whose first parameter takes the text unread (no output);
    a token: the surplus second parameter; `None`: not such a line."""
    lead = _INSTRUCTION_PROSE_RE.match(stmt.text or "")
    info = profile.instructions.get(lead.group(1).upper()) if lead else None
    if not info or not info["params"] or lead.group(1).upper() in ("DATA", "DATALONG"):
        return None
    codes, rest = info["params"], lead.group(2)
    if codes[0] % 100 == 11 and "," not in rest and len(codes) > 1 and codes[1] < 100:
        return ""
    if codes[0] % 100 == 6 and len(codes) > 1:
        name_tok = instr_rules._next_simple_token(rest)
        return instr_rules._next_simple_token(rest[rest.index(name_tok) + len(name_tok):]) or None
    return None


def _inline_owner(statements, index: int):
    """The one-line `If` whose body holds `statements[index]`, if any (on the same source line,
    however many `:`-separated statements the body has; review R14C-6)."""
    stmt = statements[index]
    for i in range(index - 1, -1, -1):
        prev = statements[i]
        if (prev.line, prev.file) != (stmt.line, stmt.file):
            break
        if isinstance(prev, S.If) and any(x is stmt for x in (prev.inline or []) + (prev.inline_else or [])):
            return prev
    return None


def _names_in(cond, profile, message: str) -> bool:
    """Whether `message` is the unresolved-symbol line for a name written in `cond`."""
    if cond is None:
        return False
    return any(message == profile.unresolved_symbol_message.format(name=n) for n in _expr_names(cond))


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
    taint_at = 0
    stash: dict[tuple, list] = {}   # inline-`If` condition diagnostics set aside while the body is rewritten
    inline_bodies = {id(b) for st in program.statements if isinstance(st, S.If)
                     for b in (st.inline or []) + (st.inline_else or [])}
    dropped: set[int] = set()      # tainted declarations: their names are never registered
    tainted_subs: set[int] = set()  # tainted `Sub`/`Function` headers: parameters dropped
    rewritten: set[int] = set()
    dropped_names: set[str] = set()
    registered_names: set[str] = set()
    include_errors = sorted(program.include_error_at)  # `Include` extension / nesting errors trigger it too
    for index, stmt in enumerate(program.statements):
        key = (stmt.line, stmt.file)
        if not tainted and include_errors and include_errors[0] <= index:
            tainted = True  # (oracle, 2026-10-12: the statements after the error are rewritten)
            taint_at = index
        if not tainted and isinstance(stmt, S.Declaration):
            registered_names.update(v.name.upper() for v in stmt.vars)
        existing = [d for d in diags.items if (d.line, d.file) == key]
        if isinstance(stmt, S.If) and stmt.inline is not None:   # (a body hoisted out of it too)
            # only the condition's own lines belong to the `If`; the body's are its statements'
            existing = [d for d in existing if _names_in(stmt.cond, profile, d.message)]
        elif (owner0 := _inline_owner(program.statements, index)) is not None and not tainted:
            existing = [d for d in existing if not _names_in(owner0.cond, profile, d.message)]
        if tainted:
            owner = _inline_owner(program.statements, index)
            if owner is not None:
                # the body of a one-line `If` is rewritten like any statement, the condition's own
                # `X is not defined.` lines stay (oracle, CR300/CR350, 2026-10-12)
                held = [d for d in existing if d.severity == "error" and _names_in(owner.cond, profile, d.message)]
                if held:
                    stash.setdefault(key, []).extend(held)
                    diags.items[:] = [d for d in diags.items if not any(d is h for h in held)]
            if (isinstance(stmt, S.Call) and stmt.name.upper() == "SERIALOPEN" and len(stmt.args) > 1
                    and isinstance(stmt.args[1], (S.Unary, S.Binary))):
                # a negative or computed baud is swallowed once the cascade has started, a plain
                # illegal one still prints (oracle, CR300/CR350, 2026-10-10)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key
                                  or not d.message.endswith(" is illegal baud rate.")]
            lead = _LEFTOVER_LEAD_RE.match(stmt.text) if isinstance(stmt, S.Unparsed) else None
            lead_name = (lead.group(1) or lead.group(2)) if lead else None
            if lead_name and lead_name.upper() in dropped_names - registered_names:
                # a missing-parameter leftover `(Name,1,...)` whose first name is a cascade-dropped
                # declaration reports it first (oracle, CR300/CR350, 2026-10-08)
                at = next((i for i, d in enumerate(diags.items) if (d.line, d.file) == key
                           and d.message.startswith(_GENERIC_FALLBACK_PREFIX)), None)
                if at is not None:
                    diags.items.insert(at, Diagnostic("error", stmt.line, profile.unresolved_symbol_message
                                                      .format(name=lead_name), stmt.file))
                continue
            if isinstance(stmt, S.Unparsed) and dropped_names - registered_names:
                _reword_prose(program, profile, diags, stmt, dropped_names - registered_names)
            if isinstance(stmt, S.Unparsed) and (_TABLE_LINE_RE.match(stmt.text)
                                                 or _SIGNED_TABLE_RE.match(stmt.text)):
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key or not (
                    d.message.startswith("Could not find DataTable ")
                    or d.message == "Expr illegally occurs before BeginProg." or (
                        d.message.startswith(_GENERIC_FALLBACK_PREFIX)
                        and stmt.text.strip() in d.message))]
                continue
            if isinstance(stmt, S.Declaration) or (
                    isinstance(stmt, S.Unparsed)
                    and stmt.text.lstrip().upper().startswith(("PUBLIC ", "DIM "))):
                # A tainted declaration's own error is dropped (oracle: `Public A As StringX * 50`
                # then `Dim Z banana` prints no `Invalid Variable declaration banana.`); one with
                # two or more variables reads like an instruction (`Dim A(), B` -> `Too many
                # parameters:  B.`).
                if not (isinstance(stmt, S.Declaration) and not stmt.vars):
                    # (`Public` alone keeps `Variable not named.`: oracle, 2026-10-01, `asin_1`)
                    diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                if isinstance(stmt, S.Declaration) and len(stmt.vars) >= 2:
                    diags.error(stmt.line, f"Too many parameters:  {stmt.vars[1].name}.", stmt.file)
                if isinstance(stmt, S.Declaration) and stmt.kind in ("PUBLIC", "DIM", "STATIC"):
                    dropped.add(id(stmt))
                    dropped_names.update(v.name.upper() for v in stmt.vars)
                continue
            alias_src = None
            if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ALIAS" and stmt.args \
                    and isinstance(stmt.args[0], (S.Index, S.Name)):
                alias_src = stmt.args[0].name
            elif isinstance(stmt, S.Unparsed):  # `Alias A(1) =` with the name on the next line
                m_alias = re.match(r"(?i)\s*Alias\s+([A-Za-z_]\w*)\s*(?:\([^()]*\))?\s*=\s*$", stmt.text)
                alias_src = m_alias.group(1) if m_alias else None
            if alias_src and alias_src.upper() in dropped_names - registered_names:
                # a cascade-dropped declaration is never registered, so its aliases name an
                # undeclared source (oracle, CR300/CR350, 2026-10-04)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                at = next((i for i, d in enumerate(diags.items)
                           if d.file == stmt.file and d.line > stmt.line), len(diags.items))
                diags.items.insert(at, Diagnostic("error", stmt.line,
                                                  f"{alias_src} not yet declared so cannot be aliased.",
                                                  stmt.file))
                # the alias name is not registered either (prose that mentions it: `current`)
                if isinstance(stmt, S.Instruction):
                    dropped_names.update(a.name.upper() for a in stmt.args[1:] if isinstance(a, S.Name))
                continue
            if isinstance(stmt, S.SubDef):
                tainted_subs.add(id(stmt))
                # `Sub Foo (A, B)` -> `Too many parameters:  B.`, `Function F (x) As Long` ->
                # `... As.`: the second token after the name (oracle, 2026-09-28)
                m = re.search(r"\((.*)$", stmt.text or "")  # `(x) As Long` -> x, As
                tokens = [t for t in re.split(r"[\s,()=]+", m.group(1)) if t] if m else []
                if m:
                    # with two or more parameters the second parameter's name is echoed (oracle,
                    # CR300, 2026-10-10: `(a As Long!, rst As Long)` -> `rst`, not `As`)
                    parts, depth, cur = [], 0, ""
                    for ch in m.group(1):
                        depth += (ch == "(") - (ch == ")")
                        if depth < 0:
                            break
                        if ch == "," and depth == 0:
                            parts.append(cur)
                            cur = ""
                        else:
                            cur += ch
                    parts.append(cur)
                    second = re.match(r"[\s]*([^\s,()=]+)", parts[1]) if len(parts) > 1 else None
                    if second:
                        tokens = [tokens[0] if tokens else "", second.group(1)]
                    elif len(parts) == 1 and not parts[0].strip():
                        tokens = []  # `F( ) As Long`: no parameters, nothing to echo
                if len(tokens) >= 2:
                    diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                    diags.error(stmt.line, f"Too many parameters:  {tokens[1]}.", stmt.file)
                continue
            if isinstance(stmt, S.Unparsed) and (taken := _tainted_prose_instruction(stmt, profile)) is not None:
                # an instruction written as prose, after the cascade started: a deferred-text first
                # parameter loses its shortfall line, a name token is followed by the surplus second
                # parameter (oracle, CR300/CR350, 2026-10-11: `Histogram instruction.`, `CallTable
                # instruction is used to ...`)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                if taken:
                    at = next((i for i, d in enumerate(diags.items)
                               if d.file == stmt.file and d.line > stmt.line), len(diags.items))
                    diags.items.insert(at, Diagnostic("error", stmt.line,
                                                      f"Too many parameters:  {taken}.", stmt.file))
                continue
            if isinstance(stmt, S.Unparsed) and re.fullmatch(r"(?i)\s*(?:do|loop)\s+(?:while|until)\s*", stmt.text or ""):
                # (`Do While` with nothing after it: the incomplete-expression line is gone)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key
                                  or not d.message.startswith("Incomplete expression:")]
                continue
            m = (re.search(r"(?i)\b(?:While|Until)\b\s*(.*)$", stmt.text or "")
                 if isinstance(stmt, S.DoLoop) and stmt.cond is not None else
                 re.match(r"(?i)\s*(?:do|loop)\s+(?:while|until)\b\s*(.*\S.*)$", stmt.text or "")
                 if isinstance(stmt, S.Unparsed) else None)  # (`Do Until X >`: unparsable too)
            if m:
                # a `Do`/`Loop` condition is rewritten like a second argument (oracle, CR300,
                # 2026-10-04: `Loop Until X=-1` -> `Too many parameters:  X.`)
                text = instr_rules._next_simple_token(m.group(1)) or m.group(1)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                at = next((i for i, d in enumerate(diags.items)
                           if d.file == stmt.file and d.line > stmt.line), len(diags.items))
                diags.items.insert(at, Diagnostic("error", stmt.line,
                                                  f"Too many parameters:  {text}.", stmt.file))
                rewritten.add(id(stmt))
                continue
            trigger_short = (taint_at < index and (program.statements[taint_at].line,
                                                   program.statements[taint_at].file) == key
                             and _is_short_instruction(program.statements[taint_at], profile))
            kept_shortfall = [d for d in diags.items if (d.line, d.file) == key
                              and d.message in _SHORTFALL_MESSAGES][:1] if trigger_short else []
            if (isinstance(stmt, S.Instruction) and instr_rules.given_count(stmt, profile) == 1
                    and _is_short_instruction(stmt, profile)):
                if id(stmt) in inline_bodies:
                    continue  # (a one-line `If` body keeps its shortfall, oracle 2026-10-12: `I = Nope` then `If X Then Delay`)
                # a short instruction after the cascade started has no second argument to echo and
                # its own shortfall disappears (oracle, CR300, 2026-10-10: `Delay(1)` then
                # `PanelTemp(X)` prints one line; on a shared line the trigger's own line stays)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key
                                  or d.message not in _SHORTFALL_MESSAGES or any(d is k for k in kept_shortfall)]
                continue
            if (isinstance(stmt, S.Instruction) and len(stmt.args) >= 2
                    and stmt.args[1] is not None
                    and stmt.keyword.upper() not in ("UNITS", "READONLY", "ALIAS", "CONSTTABLE", "SNMPVARIABLE")):
                # (`Units X = m` is never rewritten: repository libraries, oracle; nor is an
                # `Alias` or a `ConstTable (T,0)`, oracle 2026-10-03.)
                # the next simple token, like a genuine overflow (oracle: `MainScanSecs * 4` ->
                # `MainScanSecs`, `SearchString()` -> `SearchString`)
                text = instr_rules._next_simple_token(expressions._arg_text(stmt, 1)) or \
                    expressions._arg_text(stmt, 1)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                at = next((i for i, d in enumerate(diags.items)
                           if d.file == stmt.file and d.line > stmt.line), len(diags.items))
                diags.items[at:at] = kept_shortfall
                diags.items.insert(at + len(kept_shortfall), Diagnostic(
                    "error", stmt.line, f"Too many parameters:  {text}.", stmt.file))
                rewritten.add(id(stmt))
            continue
        if any(_is_cascade_trigger(d) for d in existing):
            tainted = True
            taint_at = index
    for (line, file), held in stash.items():   # condition diagnostics go back before the body's
        at = next((i for i, d in enumerate(diags.items) if d.file == file and d.line >= line), len(diags.items))
        diags.items[at:at] = held
    diags.cascade_gone = dropped_names - registered_names  # (names the cascade left unregistered)
    if rewritten:
        _report_rewritten_first_arg(program, profile, diags, dropped, rewritten)
    if dropped or tainted_subs:
        _report_unregistered(program, profile, diags, taint_at, dropped, tainted_subs, rewritten)


_FLOAT_DEST = 8  # `variable_or_array` "Dest" parameter (`docs/type-codes.md`)
_DEST_MESSAGE = "Illegal destination variable type, expecting Float."


_variable_types = symbols.declared_variable_types


def _is_bad_destination(expr: S.Expr, types: dict[str, str]) -> bool | None:
    """True when a defined first argument is not a Float variable, None when unknown."""
    if isinstance(expr, (S.Name, S.Index)):
        kind = types.get(expr.name.upper())
        return None if kind is None else kind != "FLOAT"
    return isinstance(expr, (S.Number, S.String, S.Binary, S.Unary))


def check_float_destinations(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: a code-8 first argument that fails ends the argument list, so the next
    argument is reported as `Too many parameters` and nothing else on the statement is: an
    undefined name keeps its `is not defined.` line, any other non-Float destination (a Long or
    String variable, a literal, an expression) gives `Illegal destination variable type,
    expecting Float.` (oracle, CR300, 2026-10-01: `PanelTemp(L,60)`, `PanelTemp(Nope,60)`,
    `VoltSe(L,1,mV1000,...)`, `Battery(L)`)."""
    if profile.name not in ("cr300", "cr350"):
        return
    types: dict[str, str] | None = None
    known = None
    per_line = collections.Counter((st.line, st.file) for st in program.statements)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or not stmt.args or stmt.args[0] is None \
                or per_line[(stmt.line, stmt.file)] > 1:
            continue
        info = profile.instructions.get(stmt.keyword.upper())
        literal = next((i for i, (code, a) in enumerate(zip(info["params"], stmt.args))
                        if code in (27, 29) and a is not None and not isinstance(a, S.String)
                        and not _expr_names(a)), None) if info and info["params"] else None
        if literal is not None and per_line[(stmt.line, stmt.file)] == 1 and not any(
                (d.line, d.file) == (stmt.line, stmt.file) for d in diags.errors):
            # `RealTime(5)`, `Erase(5)`, `Matrix(5,(1+2),0,R)` -> `Invalid Variable or Array:
            # 5.`, then the argument list ends (oracle, CR300, 2026-10-01: `Move(5,1,X,1)` ->
            # `Too many parameters:  1.`)
            at = next((i for i, d in enumerate(diags.items) if d.file == stmt.file
                       and d.line > stmt.line), len(diags.items))
            new = [Diagnostic("error", stmt.line, "Invalid Variable or Array: "
                              f"{expressions._arg_text(stmt, literal).strip()}.", stmt.file)]
            if len(stmt.args) > literal + 1 and stmt.args[literal + 1] is not None:
                text = instr_rules._next_simple_token(
                    expressions._arg_text(stmt, literal + 1)) or \
                    expressions._arg_text(stmt, literal + 1)
                new.append(Diagnostic("error", stmt.line, f"Too many parameters:  {text}.",
                                      stmt.file))
            diags.items[at:at] = new
            continue
        if not info or not info["params"] or info["params"][0] != _FLOAT_DEST:
            continue
        if types is None:
            types, known = _variable_types(program), expressions._known_names(program, profile)
            ds = expressions.program_dims(program, profile, known)
        arg = stmt.args[0]
        if isinstance(arg, S.Name) and arg.name.upper() in profile.functions:
            continue  # a bare function name is `Expression expects (` instead (expressions.py)
        key = (stmt.line, stmt.file)
        undefined = [n for n in _expr_names(arg) if n.upper() not in known]
        expression = False
        if undefined:
            if isinstance(arg, (S.Binary, S.Unary)):
                # an arithmetic expression is no destination: after its names, `Dimensions not
                # declared` or the destination-type error (oracle, CR300, 2026-10-03)
                expression = True
                undefined, dims = expressions._names_and_dims(known, ds, stmt, (), arg)
            elif not isinstance(arg, (S.Name, S.Index)):
                continue
            keep = [profile.unresolved_symbol_message.format(name=n) for n in undefined]
        elif _is_bad_destination(arg, types):
            keep = []
        else:
            continue
        own = [d for d in diags.items if (d.line, d.file) == key and d.severity == "error"]
        if not keep and any(d.message.startswith("Dimensions not declared") for d in own):
            continue  # (`Battery(L(2))` for a Long scalar: over-indexing is reported first)
        if keep and [d.message for d in own[:len(keep)]] != keep:
            continue
        at = diags.items.index(own[0]) if own else next(
            (i for i, d in enumerate(diags.items) if d.file == stmt.file and d.line > stmt.line),
            len(diags.items))
        new = list(own[:len(keep)]) or [Diagnostic("error", stmt.line, _DEST_MESSAGE, stmt.file)]
        if keep and (dims if expression else expressions._has_undefined_index(arg, known, ds, stmt)):
            # (oracle, CR300, 2026-10-03: `Battery(Nope(1))`, `PanelTemp(Nope(1),15000)`)
            new.append(Diagnostic("error", stmt.line, expressions.dimensions_message(
                expressions._arg_text(stmt, 0).strip(), profile, False), stmt.file))
        elif keep and expression:
            new.append(Diagnostic("error", stmt.line, _DEST_MESSAGE, stmt.file))
        if len(stmt.args) >= 2 and stmt.args[1] is not None:
            text = instr_rules._next_simple_token(expressions._arg_text(stmt, 1)) or \
                expressions._arg_text(stmt, 1)
            new.append(Diagnostic("error", stmt.line, f"Too many parameters:  {text}.", stmt.file))
        diags.items[:] = [d for d in diags.items[:at] if (d.line, d.file) != key or
                          d.severity != "error"] + new + [
            d for d in diags.items[at:] if (d.line, d.file) != key or d.severity != "error"]


# Parameter codes resolved as a runtime expression or variable: an undefined name there ends the
# argument list on CR300/CR350 (`docs/type-codes.md`; code 1 constants report `Variable illegal`).
_ABORTING_PARAMS = frozenset({2, 8, 24, 25, 26, 27, 28, 29, 33, 40, 41, 47})


def drop_too_many_after_variable_illegal(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: `Variable illegal in Parameter` ends the argument list, so the statement's
    genuine `Too many parameters` is not reached (oracle, 2026-10-01: `PortSet(C4,1,1)`)."""
    if profile.name not in ("cr300", "cr350"):
        return
    per_line = collections.Counter((st.line, st.file) for st in program.statements)
    declarations = {(st.line, st.file) for st in program.statements if isinstance(st, S.Declaration)}
    bad = {(d.line, d.file) for d in diags.errors
           if d.message.startswith(_VARIABLE_ILLEGAL_PREFIX) and per_line[(d.line, d.file)] == 1
           and (d.line, d.file) not in declarations}  # (a declaration names the variable two on)
    if bad:
        diags.items[:] = [d for d in diags.items if not (
            (d.line, d.file) in bad and d.message.startswith(_TOO_MANY_PREFIX))]
    # The excess argument is reached after the earlier ones' names: `Battery(foo, bar)` ->
    # `foo is not defined.`, then `Too many parameters:  bar.` (oracle, CR300, 2026-10-01).
    items = diags.items
    for i in range(len(items) - 1, -1, -1):
        d = items[i]
        if d.severity != "error" or not d.message.startswith(_TOO_MANY_PREFIX) or \
                per_line[(d.line, d.file)] != 1:
            continue
        later = [j for j in range(i + 1, len(items)) if (items[j].line, items[j].file) ==
                 (d.line, d.file) and items[j].message.endswith(_UNRESOLVED_SUFFIX)]
        if later:
            items.insert(later[-1], items.pop(i))  # (`later` indices shift by one after the pop)


def check_undefined_arg_aborts(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: the first argument with an undefined name, at a runtime-expression parameter,
    ends the argument list: its `is not defined.` lines are followed by `Too many parameters:
    <next argument>.` and nothing else on the statement is reported (oracle, CR300, 2026-10-01:
    `Delay(0,Nope,Sec)` -> `Too many parameters:  Sec.`, `Move(A(1),2,Nope,1)` -> `... 1.`)."""
    if profile.name not in ("cr300", "cr350"):
        return
    known = None
    per_line = collections.Counter((st.line, st.file) for st in program.statements)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or per_line[(stmt.line, stmt.file)] > 1:
            continue
        info = profile.instructions.get(stmt.keyword.upper())
        if not info or not info["params"]:
            continue
        if known is None:
            known = expressions._known_names(program, profile)
        for index, arg in enumerate(stmt.args):
            names = [n for n in _expr_names(arg) if n.upper() not in known]
            if not names:
                continue
            code = info["params"][index] if index < len(info["params"]) else None
            if code not in _ABORTING_PARAMS or (index == 0 and code == _FLOAT_DEST) or \
                    index + 1 >= len(stmt.args) or stmt.args[index + 1] is None:
                break
            key = (stmt.line, stmt.file)
            wanted = {profile.unresolved_symbol_message.format(name=n) for n in names}
            own = [d for d in diags.items if (d.line, d.file) == key and d.severity == "error"]
            keep = [d for d in own if d.message in wanted]
            if not keep or any(d.message.startswith("Dimensions not declared") for d in own):
                break  # (`expressions._report_statement` already ended an undefined `A(i)`)
            too_many = [d for d in own if d.message.startswith(_TOO_MANY_PREFIX)]
            if too_many:
                # a genuine overflow found earlier still follows the names (`SW12(SW12_1,1)`)
                rest = [d for d in diags.items if d not in keep]
                at = rest.index(too_many[0])
                followup = []
                if stmt.keyword.upper() == "SERIALIN" and index == 0 \
                        and isinstance(stmt.args[0], S.Name):
                    followup.append(Diagnostic("error", stmt.line,
                                               instr_rules._SERIALIN_DEST_MESSAGE, stmt.file))
                diags.items[:] = rest[:at] + keep + followup + rest[at:]
                break
            text = instr_rules._next_simple_token(expressions._arg_text(stmt, index + 1)) or \
                expressions._arg_text(stmt, index + 1)
            at = diags.items.index(keep[-1]) + 1
            if stmt.keyword.upper() == "SERIALIN" and index == 0 \
                    and isinstance(stmt.args[0], S.Name):
                diags.items.insert(at, Diagnostic("error", stmt.line,
                                                  instr_rules._SERIALIN_DEST_MESSAGE, stmt.file))
                at += 1
            diags.items.insert(at, Diagnostic("error", stmt.line, f"Too many parameters:  {text}.",
                                              stmt.file))
            drop = {id(d) for d in own if d not in keep}
            diags.items[:] = [d for d in diags.items if id(d) not in drop]
            break


def _expr_names(expr: S.Expr | None) -> list[str]:
    if isinstance(expr, S.Name):
        return [expr.name]
    if isinstance(expr, S.Index):
        return [expr.name] + [n for a in expr.args for n in _expr_names(a)]
    if isinstance(expr, S.Binary):
        return _expr_names(expr.left) + _expr_names(expr.right)
    if isinstance(expr, S.Unary):
        return _expr_names(expr.operand)
    return []


# Parameter type codes whose argument is resolved as an expression or variable
# (`docs/type-codes.md`); identifiers, declarations and deferred text are not.
_RESOLVED_FIRST_PARAM = frozenset({1, 2, 5, 7, 8, 24, 25, 27, 28, 29, 33, 38, 39, 40, 41, 47})
_CONST_FIRST_PARAM = frozenset({1, 5, 38, 39})  # constant-only (`docs/type-codes.md`)


def _report_rewritten_first_arg(program: S.Program, profile, diags: Diagnostics,
                                dropped: set[int], rewritten: set[int]) -> None:
    """A rewritten instruction's first argument is still checked before its `Too many
    parameters` line: each undefined name (unregistered ones included), then `Dimensions not
    declared: A(I).` for an undefined indexed name (oracle, CR300, 2026-10-01: `PanelTemp(PTemp,
    15000)` after `Dim Z banana` then `Public PTemp`)."""
    import dataclasses
    known = expressions._known_names(dataclasses.replace(
        program, statements=[st for st in program.statements if id(st) not in dropped]), profile)
    tables = getattr(known, "tables", ())

    def undefined(name: str) -> bool:
        return name.upper() not in known or name.upper() in tables

    for stmt in program.statements:
        if id(stmt) not in rewritten or not getattr(stmt, "args", None) or stmt.args[0] is None:
            continue
        info = profile.instructions.get(stmt.keyword.upper())
        if not info or not info["params"] or info["params"][0] not in _RESOLVED_FIRST_PARAM:
            continue
        messages: list[str] = []

        def walk(expr: S.Expr | None) -> None:
            if isinstance(expr, S.Name):
                if undefined(expr.name):
                    messages.append(profile.unresolved_symbol_message.format(name=expr.name))
            elif isinstance(expr, S.Index):
                bad = undefined(expr.name)
                if bad:
                    messages.append(profile.unresolved_symbol_message.format(name=expr.name))
                for a in expr.args:
                    walk(a)
                if bad and any(a is not None for a in expr.args):
                    messages.append(f"Dimensions not declared: {expressions._arg_text(stmt, 0)}.")
            elif isinstance(expr, S.Binary):
                walk(expr.left)
                walk(expr.right)
            elif isinstance(expr, S.Unary):
                walk(expr.operand)

        walk(stmt.args[0])
        if messages and info["params"][0] in _CONST_FIRST_PARAM:
            # a constant-only parameter ends the list: `PortSet(C4,1)` -> `C4 is not defined.`,
            # `Variable illegal in Parameter: C4.`, no `Too many` (oracle, CR300, 2026-10-01)
            messages = [m for m in messages if m.endswith(" is not defined.")]
            messages.append(f"Variable illegal in Parameter: {expressions._arg_text(stmt, 0).strip()}.")
            diags.items[:] = [d for d in diags.items if not (
                (d.line, d.file) == (stmt.line, stmt.file) and d.message.startswith(_TOO_MANY_PREFIX))]
            at = next((i for i, d in enumerate(diags.items) if d.file == stmt.file
                       and d.line > stmt.line), len(diags.items))
            diags.items[at:at] = [Diagnostic("error", stmt.line, m, stmt.file, cascade=False)
                                  for m in messages]
            continue
        if info["params"][0] == _FLOAT_DEST:
            arg = stmt.args[0]
            names_ok = not messages
            if not isinstance(arg, (S.Name, S.Index)) or (
                    names_ok and _is_bad_destination(arg, _variable_types(program))):
                messages.append(_DEST_MESSAGE)
        if stmt.keyword.upper() == "SERIALIN" and (instr_rules._serialin_bad_destination(
                stmt.args[0], instr_rules._serialin_types(program, profile)) or
                (messages and isinstance(stmt.args[0], S.Name))):
            messages.append(instr_rules._SERIALIN_DEST_MESSAGE)
        if not messages:
            continue
        at = next(i for i, d in enumerate(diags.items) if (d.line, d.file) == (stmt.line, stmt.file)
                  and d.message.startswith(_TOO_MANY_PREFIX))
        diags.items[at:at] = [Diagnostic("error", stmt.line, m, stmt.file, cascade=False) for m in messages]


def _report_unregistered(program: S.Program, profile, diags: Diagnostics, taint_at: int,
                         dropped: set[int], tainted_subs: set[int], rewritten: set[int]) -> None:
    """Names a tainted declaration would have registered are undefined afterwards: `Public A`
    after `Dim Z banana` makes a later `X = A` print `A is not defined.`, and a tainted `Function
    F (x)` loses its parameters (`F(1)` -> `No Matching, or out of place ) ...`; `X = a` in a
    tainted `Sub S (a)` -> `a is not defined.`). A `Const` is still registered (oracle, CR300,
    2026-09-28, `docs/loggers.md` "Tainted declarations"). The tier-B name checks are re-run on
    a copy of the program without those declarations, keeping only their findings on statements
    after the taint that use an unregistered name or a tainted `Function`."""
    import dataclasses
    from .. import consteval
    from . import structure, unused
    statements = []
    for st in program.statements:
        if id(st) in dropped:
            continue
        statements.append(dataclasses.replace(st, params=[]) if id(st) in tainted_subs else st)
    modified = dataclasses.replace(program, statements=statements)
    unregistered = expressions._known_names(program, profile) - \
        expressions._known_names(modified, profile)
    functions = {st.name.upper() for st in program.statements
                 if id(st) in tainted_subs and st.keyword == "FUNCTION"}
    affected: set[tuple[int, str]] = set()
    for st in program.statements[taint_at + 1:]:
        if id(st) in dropped or id(st) in rewritten or isinstance(st, S.SubDef):
            continue
        names: set[str] = set()
        unused._walk_stmt(st, names)
        if names & unregistered or (not isinstance(st, S.Call) and names & functions):
            affected.add((st.line, st.file))
    if not affected:
        return
    scratch = Diagnostics()
    env = consteval.Env(profile, dict(program.consts))
    structure.check_pass2(modified, profile, scratch, env)
    expressions.check(modified, profile, scratch, env)
    expressions.check_function_call_counts(modified, profile, scratch)
    existing = collections.Counter((e.line, e.file, e.message) for e in diags.items)
    for key in sorted(affected, key=lambda k: (k[1] or "", k[0])):
        fresh = [d for d in scratch.items if (d.line, d.file) == key and d.severity == "error"]
        at = [i for i, d in enumerate(diags.items) if (d.line, d.file) == key]
        old = collections.Counter(diags.items[i].message for i in at)
        if (at and at == list(range(at[0], at[-1] + 1))
                and all(diags.items[i].severity == "error" for i in at)
                and not old - collections.Counter(d.message for d in fresh)):
            # the names are reported in text order: a newly undefined name (`For i = 1 To nv`
            # with `i` dropped) goes before an already reported one (oracle, CR300, 2026-10-04)
            diags.items[at[0]:at[-1] + 1] = fresh
            for d in fresh:
                existing[(d.line, d.file, d.message)] -= 1
            continue
        for d in fresh:
            if existing[(d.line, d.file, d.message)]:
                existing[(d.line, d.file, d.message)] -= 1
            else:
                diags.items.append(d)
    _respliced_call_args(modified, profile, diags, scratch, affected)


def _respliced_call_args(modified: S.Program, profile, diags: Diagnostics, scratch: Diagnostics,
                         affected: set[tuple[int, str]]) -> None:
    """An unresolved call's argument names, rechecked without the unregistered declarations
    (`CDM_Battery(CDM_A108,1,Batt_Volt)` after the taint also reports `Batt_Volt`)."""
    check_undefined_call_args(modified, profile, scratch)
    calls = {(st.line, st.file) for st in modified.statements
             if isinstance(st, S.Call) and not st.explicit}
    for key in affected & calls:
        fresh = [d for d in scratch.items if (d.line, d.file) == key and d.severity == "error"]
        at = [i for i, d in enumerate(diags.items) if (d.line, d.file) == key]
        if not at or at != list(range(at[0], at[0] + len(at))) or len(fresh) <= len(at):
            continue
        if [d.message for d in diags.items[at[0]:at[-1] + 1]] != \
                [d.message for d in fresh if d.message in {e.message for e in diags.items[at[0]:at[-1] + 1]}]:
            continue
        diags.items[at[0]:at[-1] + 1] = fresh


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
# Instructions without parameters: text after the name is a surplus parameter (oracle, CR300 and
# CR350, 2026-10-11, one probe each; `EndProg` ignores its tail and `Alias`, `Const`, `Data`, ... read
# it themselves, so they are not listed).
_TAIL_REJECTING = frozenset(n.upper() for n in (
    "BeginProg PipeLineMode SequentialMode EndIf WEND OpenInterval NEXTSCAN NEXTSUBSCAN ENDTABLE "
    "FillStop ExitScan ContinueScan ExitSub ExitDo ExitFor Restore AngleDegrees ShutdownBegin "
    "ShutdownEnd EndSequence ExitFunction AbortScan SampleFieldCal WaitTriggerSequence "
    "PulseCountReset WebPageEnd TableHide DHCPRenew Restart PreserveVariables "
    "ApplyAndRestartSequence EndApplyAndRestartSequence").split())
_ZERO_TAIL_RE = re.compile(r"\s*([A-Za-z_]\w*)(?=[\s,(])(.*)$", re.S)
# A parameter text that starts with a binary operator word: `Invalid, or out of place expression`
# after the next operand is named (`is`/`mod` stop at once), not `Incomplete expression`.
_LEAD_OP_WORD_RE = re.compile(r"(?i)(is|mod|and|or|xor|imp|eqv|to)(?![A-Za-z0-9_])")
_LEAD_OP_NO_OPERAND = frozenset({"IS", "MOD"})
_LEAD_OP_SKIP_RE = re.compile(r"(?i)^[ \t]*(?:(?:and|or|xor|imp|eqv|to|not|mod|is)(?![A-Za-z0-9_])[ \t]*)*")
_INSTRUCTION_PROSE_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)[ \t]+([A-Za-z_][^(]*)$", re.S)
_LINE_OPERAND_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?\b|\"[^\"]*\"")
# Keyword operators join the chain like `+` (oracle, CR300, 2026-10-01: `foo and bar baz qux` ->
# foo, bar, baz; `Is` and `Not` do not).
_LINE_OP_WORD_RE = re.compile(r"[ \t]+(?i:and|or|xor|eqv|imp|mod|to)[ \t]+(?=[A-Za-z_0-9\"])")
# A line starting `word.` reads as a table field: `Could not find DataTable word.` (even for a
# declared name), and nothing at all once the cascade has started (oracle, CR300, 2026-10-01).
_TABLE_LINE_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\.")
# the same with a sign in front (`+ Test.TCTemp(1)`, a wrapped string concatenation, oracle,
# CR300/CR350, 2026-10-11): `Could not find DataTable` for an unknown table, nothing after the cascade
_SIGNED_TABLE_RE = re.compile(r"\s*[-+][ \t]*([A-Za-z_][A-Za-z0-9_]*)\.(?=[A-Za-z_])")
_LINE_OP_RUN_RE = re.compile(r"(?:[ \t]*(?:<>|<=|>=|[-+*/^&<>=])[ \t]*)(?=[A-Za-z_0-9\"])|(?:<>|<=|>=|[-+*/^&<>=])")
_LINE_WS_RE = re.compile(r"[ \t]+")
_LINE_PAREN_RE = re.compile(r"[ \t]*\(")


def _split_top_commas(text: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth <= 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return parts + [cur]


def _inside_table(program: S.Program, line: int, file: str) -> bool:
    """The statement at `line` sits in a `DataTable` body."""
    inside = False
    for st in program.statements:
        kw = st.keyword.upper() if isinstance(st, S.Instruction) else ""
        if (st.line, st.file) == (line, file):
            return inside
        if kw == "DATATABLE":
            inside = True
        elif kw == "ENDTABLE":
            inside = False
    return False


def _loose_before_beginprog(program: S.Program, line: int, file: str) -> bool:
    """The statement at `line` precedes `BeginProg` outside any Sub/Function/table body."""
    depth = 0
    for st in program.statements:
        kw = st.keyword.upper() if isinstance(st, S.Instruction) else ""
        if (st.line, st.file) == (line, file):
            return depth == 0
        if kw == "BEGINPROG":
            return False
        if isinstance(st, S.SubDef) or kw in ("DATATABLE", "CONSTTABLE", "DISPLAYMENU", "STRUCTURETYPE"):
            depth += 1
        elif S.ends_routine(st) or kw in ("ENDTABLE", "ENDCONSTTABLE", "ENDMENU", "ENDSTRUCTURETYPE"):
            depth = max(0, depth - 1)
    return False


def prose_known(program: S.Program, profile) -> set[str]:
    """Names a prose word resolves to: an instruction keyword that is not also a symbol or function
    is reported (oracle, CR300, 2026-10-01: `foo Battery` -> `Battery is not defined.`, `foo Sin`
    and `foo Scan` are not)."""
    instruction_only = set(profile.instructions) - set(profile.symbols) - set(profile.functions)
    known = expressions._known_names(program, profile)
    return set(known) - instruction_only - set(known.tables)


def prose_known_tables(program: S.Program, profile) -> set[str]:
    """Upper-cased user `DataTable` names (a table field line is not an unknown table)."""
    return set(getattr(expressions._known_names(program, profile), "tables", ()))


def first_declarations(program: S.Program) -> dict[str, tuple[str, int]]:
    """Upper-cased variable/`Const`/`Alias` name -> (file, line) of its first declaration."""
    first: dict[str, tuple[str, int]] = {}
    for st in program.statements:
        names: list[str] = []
        if isinstance(st, S.Declaration):
            names = [v.name for v in st.vars]
        elif isinstance(st, S.ConstDecl):
            names = [st.name]
        elif isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS":
            names = [a.name for a in st.args[1:] if isinstance(a, S.Name)]
        for n in names:
            first.setdefault(n.upper(), (st.file, st.line))
    return first


def declared_later(first: dict[str, tuple[str, int]], line: int, file: str) -> set[str]:
    """Names whose first declaration comes after `line`: single-pass compilers do not know them
    yet when a prose line mentions them (oracle, CR300, 2026-10-08)."""
    return {n for n, (f, l) in first.items() if f == file and l > line}


def _paren_items_words(inner: str, known: set[str]) -> list[str]:
    """The undefined names of the text after a call's `(`: with top-level commas, the first three
    items are walked like a line (words, chain and peek) and the fourth only for its first name,
    the rest is not read (oracle, CR300, 2026-10-11: `cv(zz, yy, 6, ww) text` names `zz`, `yy`,
    `ww`; `cv(4, zz yy, 6)` names both). An item starting `&` is a hex literal (`&h0F`) or, in
    operand position, a name made of the `&` and what follows it up to a blank (`& 6` -> `&`,
    `&6` -> `&6`); the fourth item's `&h0F` names `h0F`, its other `&` forms name nothing."""
    items, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    if len(items) == 1 and "&" not in inner:
        return _leading_unknown_words(inner, known)
    words: list[str] = []
    for idx, item in enumerate(items[:4]):
        item = item.lstrip(" \t")
        amp = re.match(r"&([A-Za-z0-9_]*)", item)
        if amp:
            hexed = re.match(r"&[hH][0-9A-Fa-f]+", item)
            if idx == 3:
                if hexed and amp.group(1) not in known:
                    words.append(amp.group(1))
                continue
            if not hexed:
                words.append(amp.group(0))
                continue
            item = "0" + item[hexed.end():]
        walked: dict = {}
        found = _leading_unknown_words(item, known, state=walked)
        words += found[:1] if idx == 3 else found
        if walked.get("peeked"):
            break
    return words


def _leading_unknown_words(text: str, known: set[str], callables: frozenset = frozenset(),
                           state: dict | None = None) -> list[str]:
    """The ordered list of `text`'s own leading bare words this project believes CR300/CR350
    would report as `%s is not defined.` ahead of the generic fallback for it -- see this
    module's `check_unknown_line_words` comment above for the oracle evidence and the exact walk
    this implements. `known` is the same upper-cased name set `expressions._known_names` builds;
    a word already in it is walked over (it can still be part of the chain) but never returned.
    """
    lead_op = re.match(r"[ \t]*(?:<>|<=|>=|<<|>>|[=<>])[ \t]*(?=[A-Za-z_])", text)
    if lead_op:
        # a line that starts with a comparison operator reads the next word as its first operand
        # and stops there (oracle, CR300/CR350, 2026-10-11: `= Year   ` -> `Year`)
        word = _LINE_WORD_RE.match(text, lead_op.end())
        return [word.group(0)] if word.group(0).upper() not in known else []
    # The chain starts at the first word or number (a leading `2 when the` reports `when`).
    pos = len(text) - len(text.lstrip(" \t"))
    if re.match(r"-\d", text[pos:pos + 2]):
        pos += 1  # a signed number starts the chain like a number (oracle, CR300, 2026-10-10: `-1 Then`)
    elif text[pos:pos + 1] == '"' and text.find('"', pos + 1) > 0:
        text = text[:pos] + "0" + text[text.find('"', pos + 1) + 1:]  # a string literal likewise
    if not _LINE_OPERAND_RE.match(text, pos):
        return []
    if text[pos:pos + 3].upper() == "IS " or text[pos:].upper() == "IS":
        return []  # a leading `Is` ends the walk before any word (oracle, CR300, 2026-10-08)
    words: list[str] = []
    extending = True  # False once we're consuming the single trailing "peek" word.
    while True:
        operand = _LINE_OPERAND_RE.match(text, pos) if extending else _LINE_WORD_RE.match(text, pos)
        if not operand:
            break
        pos = operand.end()
        if operand.group(0)[0].isdigit() or operand.group(0)[0] == '"':
            pass  # a number or a string is part of the chain but never reported
        elif text.startswith(".", pos):
            break  # `word.` is a table field reference, not a name (oracle, 2026-10-01)
        else:
            words.append(operand.group(0))
        if not extending:
            break
        paren = None if operand.group(0)[0].isdigit() or operand.group(0)[0] == '"' \
            else _LINE_PAREN_RE.match(text, pos)
        if paren and len(words) == 1 and (words[0].upper() not in known or words[0].upper() in callables):
            # `device (8 channels)`: the first word's parenthesis opens a second walk of the same
            # shape, which ends at the first `)`, and nothing after it is examined
            # (oracle, CR300, 2026-10-08).
            words += _paren_items_words(text[paren.end():], known)  # (a declared variable or function keeps the walk too, oracle 2026-10-10: `X (foo bar)`)
            break
        op_match = _LINE_OP_RUN_RE.match(text, pos) or _LINE_OP_WORD_RE.match(text, pos)
        if op_match:
            pos = op_match.end()
            continue
        ws_match = _LINE_WS_RE.match(text, pos)
        if ws_match and _LINE_WORD_RE.match(text, ws_match.end()):
            pos = ws_match.end()
            extending = False
            if state is not None:
                state["peeked"] = True  # (a juxtaposed word: the walk is over)
            continue
        break
    return [w for w in words if w.upper() not in known]


# `Instruction(a, b c, d)`: the first argument with adjacent operands (oracle, CR300/CR350,
# 2026-10-12, a sweep of every instruction and position, `tests/fixtures/r15_cr3xx_call_adjacent_arg.json`):
# the arguments before it are read normally; its own first two operands are looked up, then
# `Incomplete expression: <arg>.`. A constant-only parameter (codes 1, 5, 20, 38, 39) adds
# `Variable illegal in Parameter: <arg>.` when one of those two operands is a variable or an
# undefined name; any other parameter is followed by `Too many parameters:  <first token of the
# next argument>.` when an argument follows. The arguments after it are never read.
_CALL_ADJACENT_EXCLUDED = frozenset({"DATA", "DATALONG", "ALIAS", "UNITS", "SUB", "FUNCTION", "IF", "ELSEIF",
                                     "WHILE", "UNTIL", "SELECT", "CASE", "FOR", "DO", "LOOP"})
_ADJACENT_CONST_CODES = frozenset({1, 5, 20, 38, 39})
_ADJACENT_EXPR_CODES = frozenset({2, 8, 24, 25, 26, 27, 28, 29, 33, 40, 41, 47, 48})
_ADJACENT_RE = re.compile(r"[\w\")]\s+[\w\"]")
_ADJACENT_OP_WORD_RE = re.compile(r"(?i)\b(?:and|or|xor|eqv|imp|mod|is|to|not)\b")


def _split_args_quoted(text: str) -> list[str] | None:
    """The top-level comma-separated arguments of a call's text after the `(` (the call's own
    closing parenthesis stays on the last one); `None` when the parentheses do not balance as one
    call would."""
    parts, depth, cur, quoted = [], 0, "", False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < -1:
                    return None
            elif ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
                continue
        cur += ch
    parts.append(cur)
    if quoted:
        return None
    return parts


def _adjacent_operands(arg: str) -> bool:
    stripped = re.sub(r'"[^"]*"', '"', arg)
    return bool(_ADJACENT_RE.search(stripped)) and not _ADJACENT_OP_WORD_RE.search(arg)


_ADJACENT_TABLE_SOURCES = frozenset({"SAMPLE", "AVERAGE", "MAXIMUM", "MINIMUM", "TOTALIZE", "STDDEV"})


def _call_adjacent_arg_messages(inner: str, record: dict, here: set[str], variables: frozenset,
                                profile, in_table: bool) -> list[str] | None:
    params = record["params"]
    args = _split_args_quoted(inner)
    if args is None:
        return None
    for k, arg in enumerate(args):
        last = k == len(args) - 1
        if last and arg.rstrip().endswith(")") and arg.count("(") < arg.count(")"):
            arg = arg.rstrip()[:-1]
        if _adjacent_operands(arg):
            break
        if k < len(params) and params[k] % 100 in (6, 11):
            continue    # a name or deferred text: not looked at while the statement is read
        if not arg.strip() or arg.count("(") != arg.count(")") or _leading_unknown_words(arg, here):
            return None     # an earlier argument with problems of its own: not this shape
    else:
        return None
    if k >= len(params):
        return None
    code = params[k] % 100
    if code == 11:
        # a table source with adjacent operands: its words and the incomplete expression, in a table
        # (outside one the placement error replaces them); a shortfall hides them
        if not in_table or record["name"].upper() not in _ADJACENT_TABLE_SOURCES:
            return None
        carg = arg.strip() if last else arg.lstrip()
        short = instr_rules.shortfall_message(record, instr_rules.given_count_of(args))
        if short:
            return [short]
        return [profile.unresolved_symbol_message.format(name=w) for w in _leading_unknown_words(carg, here)] + [
            f"Incomplete expression: {carg}."]
    if code not in _ADJACENT_CONST_CODES and code not in _ADJACENT_EXPR_CODES:
        return None
    carg = arg.strip() if last else arg.lstrip()
    out = [profile.unresolved_symbol_message.format(name=w) for w in _leading_unknown_words(carg, here)]
    out.append(f"Incomplete expression: {carg}.")
    if code in _ADJACENT_CONST_CODES:
        chain = _leading_unknown_words(carg, set(), frozenset(profile.functions))
        if any(w.upper() in variables or w.upper() not in here for w in chain
               if w.upper() not in profile.functions):
            out.append(f"Variable illegal in Parameter: {carg}.")
    elif not last:
        token = instr_rules._next_simple_token(",".join(args[k + 1:]))
        if token:
            out.append(f"Too many parameters:  {token}.")
    return out



# `check_undefined_call_args` (this round): a `Name(args)`/`Name args` call whose own name never
# resolves to a `Sub`, a built-in function or an instruction gets, on CR6/CR1000X, only the generic
# `Invalid, or out of place expression: %s.` fallback (`structure._check_call_pass2`'s "resolving
# to nothing" branch); CR300/CR350 additionally print `{name} is not defined.` for the call's own
# name first (`profile.undefined_call_message`, already implemented in `structure.py`) -- but, not
# yet modelled anywhere until this round, they *also* print one `%s is not defined.` line per
# undefined argument name the call was written with, spliced in between that first line and the
# generic fallback -- oracle-confirmed this round (`docs/loggers.md` "CDM_* argument names", both
# `cr300comp.exe`/`cr350comp.exe`, identical), corpus: every `CDM_*` instruction this project's own
# tables have no record of on CR300/CR350 (`cdmbrfull6w_1.CR6`, `cdmexcitev_1.CR6`, `cdmdelay_1.CR6`,
# ... -- CDM support requires hardware these two loggers' own smaller product line never carries),
# plus scratch probe `t3.cr6`.
#
# The rule, walking the call's arguments in source order (oracle probes 2026-10-01):
#   - Arguments 1-3 are walked fully: bare names, names inside operator expressions (left then
#     right; unary operands), and names inside index arguments. A declared name is skipped and the
#     scan continues; a bare DataTable name counts as undefined. Literals and empty slots are skipped.
#   - An undefined indexed name `B(...)` is reported, then its own arguments are walked, then the
#     whole scan stops. A defined indexed name `X(...)` has its arguments walked and the scan goes on.
#   - Argument 4 contributes only its leftmost term; arguments 5+ are never examined.
# Examples (undefined names B0.., declared array X):
#   Foo(B0, B1, B2, B3, B4, B5)      -> B0 B1 B2 B3
#   Foo(1, 2, 3, B3+B4)              -> B3
#   Foo(B0+B1, B2+B3, B4+B5)         -> all six
#   Foo(B0(B9), B1)                  -> B0 B9
#   Foo(B0, X(B1), B2)               -> B0 B1 B2
#   Foo(X, 0, Bad0, 2, Bad1)         -> Bad0 only (Bad1 is argument 5)
# Corpus: `cdmbrhalf3w_1.CR6` (`mv200` is argument 5, never reported).
MEMBER_STOP = "\0member"  # (oracle, CR300, 2026-10-01: `Foo(Bad,Tb.X)` -> Foo, Bad, nothing else)


def _call_arg_undefined_names(args: list[S.Expr | None], known: set[str],
                              routines: frozenset[str] = frozenset()) -> list[str]:
    """The undefined names among an unresolved call's arguments, in source order -- see
    `check_undefined_call_args`'s module comment above for the oracle evidence and the rule."""
    names: list[str] = []
    tables = getattr(known, "tables", ())

    def undefined(name: str) -> bool:
        return name.upper() not in known or name.upper() in tables

    def walk(expr: S.Expr | None) -> bool:
        """Append `expr`'s undefined names; False once an undefined indexed name or a table
        member (`Tb.X`, then no fallback line either: `MEMBER_STOP`) ends the scan."""
        if isinstance(expr, S.Member):
            names.append(MEMBER_STOP)
            return False
        if isinstance(expr, S.Name):
            if undefined(expr.name):
                names.append(expr.name)
        elif isinstance(expr, S.Index):
            bad = undefined(expr.name)
            if bad:
                names.append(expr.name)
            if not all(walk(a) for a in expr.args) or bad:
                return False
        elif isinstance(expr, S.Binary):
            return walk(expr.left) and walk(expr.right)
        elif isinstance(expr, S.Unary):
            return walk(expr.operand)
        return True

    for arg in args[:3]:
        if not walk(arg):
            return names
    if len(args) > 3 and isinstance(args[3], S.Number) and args[3].text[:2].lower() == "&h":
        if undefined(args[3].text[1:]) or args[3].text[1:].upper() in routines:  # (a declared variable `h0F` is not named, a Sub's name is: oracle, CR300/CR350, 2026-10-12)
            names.append(args[3].text[1:])  # (oracle, CR300, 2026-10-11: a fourth `&h0000` names `h0000`)
    elif len(args) > 3:
        first = args[3]
        while isinstance(first, (S.Binary, S.Unary)):
            first = first.left if isinstance(first, S.Binary) else first.operand
        if isinstance(first, (S.Name, S.Index)) and undefined(first.name):
            names.append(first.name)
    return names


def check_undefined_call_args(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350 only; a no-op on every other logger. Called from `checker/__init__.py`, right
    after `check_unknown_line_words`, in the same tier-B block -- see this module's
    `check_undefined_call_args` module comment above for the oracle evidence and the exact rule
    implemented (and its deliberate limits).

    Reacts to `diags.items` already emitted above (the same after-the-fact pattern
    `check_unknown_line_words` already uses) rather than re-deriving *why* `structure.py` chose to
    report a given `S.Call` as unresolved: finds each `S.Call` this program's own `program.
    statements` shows was written without the `Call` keyword and never resolves to a declared
    `Sub` (`symbols.declared_subs`) or a built-in function (`profile.functions`) -- the same
    "resolves to nothing" condition `structure._check_call_pass2` itself gates its own
    `{name} is not defined.` + generic-fallback pair on -- then, only where that exact two-line
    pair is already sitting in `diags.items` at that statement's own `(line, file)` (confirming
    `structure.py` actually reported it there, not truncated by an earlier placement cutoff or
    left unreported for some other reason this module does not re-derive), splices in this
    statement's own undefined-argument-name lines between them.
    """
    if profile.name not in ("cr300", "cr350"):
        return
    if not diags.items:
        return
    subs = symbols.declared_subs(program)
    calls_by_key: dict[tuple[int, str], S.Call] = {}
    for stmt in program.statements:
        if not isinstance(stmt, S.Call) or stmt.explicit:
            continue
        name = stmt.name.upper()
        if name in subs or name in profile.functions:
            continue
        calls_by_key[(stmt.line, stmt.file)] = stmt
    if not calls_by_key:
        return
    known: set[str] | None = None
    items = diags.items
    new_items: list[Diagnostic] = []
    changed = False
    i = 0
    n = len(items)
    while i < n:
        d = items[i]
        stmt = calls_by_key.get((d.line, d.file))
        nxt = items[i + 1] if i + 1 < n else None
        if (stmt is not None and d.severity == "error"
                and d.message == profile.unresolved_symbol_message.format(name=stmt.name)
                and nxt is not None and nxt.severity == "error"
                and (nxt.line, nxt.file) == (d.line, d.file)
                and nxt.message.startswith(_GENERIC_FALLBACK_PREFIX)):
            if known is None:
                known = expressions._known_names(program, profile)
            routines = frozenset(st.name.upper() for st in program.statements if isinstance(st, S.SubDef)) \
                - symbols.declared_variable_names(program)
            names = _call_arg_undefined_names(stmt.args, known, routines)
            new_items.append(d)
            for undefined_name in names:
                if undefined_name == MEMBER_STOP:
                    i += 1  # a table member ends the call: no fallback line
                    changed = True
                    break
                new_items.append(Diagnostic(
                    "error", d.line, profile.unresolved_symbol_message.format(name=undefined_name),
                    d.file, True))
                changed = True
            i += 1
            continue
        new_items.append(d)
        i += 1
    if changed:
        diags.items[:] = new_items


def order_email_destination(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: `A string variable destination is required` (`instr_rules.
    check_emailrecv_destination`, a pass-1 style check) follows the statement's own `X is not
    defined.` lines (oracle, 2026-10-08)."""
    if profile.name not in ("cr300", "cr350"):
        return
    items = diags.items
    for i, d in enumerate(items):
        if d.message != instr_rules._SERIALIN_DEST_MESSAGE:
            continue
        j = i + 1
        while j < len(items) and (items[j].line, items[j].file) == (d.line, d.file) \
                and items[j].message.endswith(_UNRESOLVED_SUFFIX):
            j += 1
        if j > i + 1:
            items[i:j] = items[i + 1:j] + [d]


_SHORTFALL_MESSAGES = ("Invalid or missing parameter.", "Too few parameters in instruction.")


def drop_shortfall_with_names(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: an undefined name hides the too-few-arguments message of its own statement and
    of every later statement written with arguments, but not of an earlier one (oracle, 2026-10-08/10: `PanelTemp(Nope)`,
    `Delay(1,Nope)`, `VoltDiff (X,1,mV200,1)` print only the name lines; `PanelTemp(X) : X=Nope`,
    `PanelTemp(X)` then `PanelTemp(Nope)` keep the first shortfall; `X=Nope : PanelTemp(X)` and
    `X=Nope` then `PanelTemp(X)` on the next line hide it)."""
    if profile.name not in ("cr300", "cr350"):
        return
    named = {(d.line, d.file) for d in diags.items
             if d.severity == "error" and d.message.endswith(_UNRESOLVED_SUFFIX)}
    # `Variable illegal in Parameter` ends the statement like an undefined name does, and hides the
    # shortfalls of the statement and of the later ones (oracle, CR300, 2026-10-12: `Delay(y)`)
    illegal = {(d.line, d.file) for d in diags.items
               if d.severity == "error" and d.message.startswith(_VARIABLE_ILLEGAL_PREFIX)}
    named |= illegal
    if not named:
        return
    stmts = program.statements
    known = expressions._known_names(program, profile)

    def has_names(stmt) -> bool:
        exprs = (stmt.args if isinstance(stmt, S.Instruction)
                 else [stmt.target, stmt.value] if isinstance(stmt, S.Assignment) else [])
        if isinstance(stmt, S.Instruction) and (stmt.line, stmt.file) in illegal:
            return True
        return any(expressions._undefined_names(e, known) for e in exprs)

    def is_short(stmt) -> bool:
        record = profile.instructions.get(stmt.keyword.upper()) if isinstance(stmt, S.Instruction) else None
        return record is not None and instr_rules.given_count(stmt, profile) < instr_rules._required_count(
            instr_rules._real_params(record["params"]))

    first = next((i for i, st in enumerate(stmts) if (st.line, st.file) in named and has_names(st)),
                 None)
    # A line whose name errors come from no instruction/assignment (prose, `S.Unparsed`) keeps the
    # per-line rule: it hides only its own line's shortfall.
    with_names = {(st.line, st.file) for st in stmts if has_names(st)}
    line_only = named - with_names
    first_key = None if first is None else (stmts[first].line, stmts[first].file)

    seen: collections.Counter = collections.Counter()  # shortfall diagnostics met per line

    def hidden(d: Diagnostic) -> bool:
        if d.severity != "error" or d.message not in _SHORTFALL_MESSAGES:
            return False
        key = (d.line, d.file)
        idxs = [i for i, st in enumerate(stmts) if (st.line, st.file) == key]
        if any(isinstance(stmts[i], S.Unparsed) and stmts[i].reason == structure._FOR_MISSING
               for i in idxs):
            return False  # a `For` without `=` is never hidden by names (oracle, CR300, 2026-10-10)
        short = [i for i in idxs if is_short(stmts[i])]
        nth = seen[key]
        seen[key] += 1
        if key in line_only or (key in named and (not idxs or (not short and key == first_key))):
            return True
        if first is None:
            return False
        cand = short[nth] if nth < len(short) else (short or idxs)[0]
        # A statement written with no argument at all is never hidden (`X=Nope` then `PanelTemp`).
        return cand >= first and bool(getattr(stmts[cand], "args", True))

    diags.items[:] = [d for d in diags.items if not hidden(d)]
    # a `For` without `=` or a short instruction that ends a line whose earlier segment named
    # undefined words prints after them (oracle, CR300/CR350, 2026-10-10: `X=Nope : PanelTemp`)
    for i, st in enumerate(stmts):
        if not ((isinstance(st, S.Unparsed) and st.reason == structure._FOR_MISSING)
                or is_short(st)):
            continue
        key = (st.line, st.file)
        if i + 1 < len(stmts) and (stmts[i + 1].line, stmts[i + 1].file) == key:
            continue
        if not any((o.line, o.file) == key and has_names(o) for o in stmts[:i]):
            continue
        if any((o.line, o.file) == key and o is not st and (is_short(o) or (
                isinstance(o, S.Unparsed) and o.reason == structure._FOR_MISSING)) for o in stmts):
            continue  # (only a line's single shortfall moves)
        moved = [d for d in diags.items if (d.line, d.file) == key and d.message in _SHORTFALL_MESSAGES]
        if moved:
            at = max(j for j, d in enumerate(diags.items) if (d.line, d.file) == key)
            diags.items[:] = [d for d in diags.items if d is not moved[0]]
            diags.items.insert(at, moved[0])


def check_for_header(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: a `For` header whose `To`/`Step` half has adjacent operands (`structure.
    _for_header_incomplete`, CR6 pass 1) prints the half's leading undefined words after the
    `From` expression's, then `Incomplete expression: <half>.` (oracle, CR300/CR350, 2026-10-10:
    `To 1 Step-1` -> `Step is not defined.`, `Nope To 12 Step-1` -> `Nope`, `Step`, then the line;
    `Step-Nope` names only `Step`)."""
    if profile.name not in ("cr300", "cr350"):
        return
    known = None
    for stmt in program.statements:
        text = getattr(stmt, "text", None) or ""
        if not re.match(r"\s*For\s", text, re.I):
            continue
        found = structure._for_header_incomplete(text, profile)
        kind = "incomplete"
        if found is None:
            parts = structure._for_header_parts(text, profile)
            if not parts:
                continue
            kind = parts[0][0]
            init = structure._FOR_INIT_RE.match(text)
            found = (parts[0][1], bool(init) and parts[0][1] == init.group(1))
        echo, is_init = found
        key = (stmt.line, stmt.file)
        own = [d for d in diags.items if (d.line, d.file) == key]
        if not all(d.message.endswith(_UNRESOLVED_SUFFIX) or d.message.startswith(
                (_GENERIC_FALLBACK_PREFIX, "Incomplete expression:",
                 "No Matching, or out of place )")) for d in own):
            continue
        if known is None:
            known = prose_known(program, profile)
        here = known - declared_later(first_declarations(program), stmt.line, stmt.file)
        start = structure._FOR_TO_RE.match(text).start(1)
        eq = re.search(r"=(.*?)\sTo\s", text[:start], re.I | re.S)
        words = ([] if is_init or not eq else _leading_unknown_words(eq.group(1), here)) + \
            _leading_unknown_words(echo, here)
        at = next((i for i, d in enumerate(diags.items) if (d.line, d.file) == key), len(diags.items))
        diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
        diags.items[at:at] = [Diagnostic("error", stmt.line, profile.unresolved_symbol_message.format(name=w),
                                         stmt.file, True) for w in words] + [
            Diagnostic("error", stmt.line, structure._for_header_message(kind, echo), stmt.file)]


def _echo_key(text: str) -> str:
    """An echoed statement compared with its `S.Unparsed` text (the echo drops outer parentheses)."""
    return text.replace("(", "").replace(")", "").strip()


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
    # (a line with several `:`-separated statements echoes each one; only the Unparsed one counts)
    unparsed_text = {(s.line, s.file, _echo_key(s.text)): s for s in program.statements
                     if isinstance(s, S.Unparsed)}
    if not unparsed_lines:
        return
    known: set[str] | None = None
    new_items: list[Diagnostic] = []
    changed = False
    for d in diags.items:
        if (d.severity == "error" and d.message.endswith(".")
                and d.message.startswith(_GENERIC_FALLBACK_PREFIX)
                and (d.line, d.file) in unparsed_lines
                and (d.line, d.file, _echo_key(d.message[len(_GENERIC_FALLBACK_PREFIX):-1]))
                in unparsed_text):
            if known is None:
                known = prose_known(program, profile)
                first = first_declarations(program)
            here = known - declared_later(first, d.line, d.file)
            text = d.message[len(_GENERIC_FALLBACK_PREFIX):-1]
            if re.match(r"(?i)\s*Alias\b", text) and any(
                    (e.line, e.file) == (d.line, d.file) and e.message.startswith("Need '=' in alias definition")
                    for e in diags.items):
                # an `Alias` line without its `=` has been answered by the alias message alone
                # (oracle, CR300/CR350, 2026-10-11: `Alias rTime(1)` / `= Year` on the next line)
                changed = True
                continue
            table = _TABLE_LINE_RE.match(text)
            signed = None if table else _SIGNED_TABLE_RE.match(text)
            if signed and (signed.group(1).upper() in symbols._ALWAYS_KNOWN_SYSTEM_TABLES
                           or signed.group(1).upper() in prose_known_tables(program, profile)):
                signed = None
            table = table or signed
            if table:
                new_items.append(Diagnostic("error", d.line, f"Could not find DataTable "
                                            f"{table.group(1)}.", d.file, cascade=False))
                d = dataclasses.replace(d, cascade=False)  # (no cascade: oracle, `r1` probe)
                changed = True
                if _loose_before_beginprog(program, d.line, d.file):
                    # (oracle, CR300, 2026-10-11: `foo.` before `BeginProg` also gets the placement line)
                    new_items.append(d)
                    new_items.append(Diagnostic("error", d.line, "Expr illegally occurs before BeginProg.",
                                                d.file, cascade=False))
                    continue
            bare = text.rstrip("\n")
            cp = structure.condition_part(bare)
            if cp is not None and not cp.strip() and not (
                    re.match(r"(?i)\s*(?:DO|LOOP)\b", bare) and any(
                        e.severity == "error" and e.line and e.line < d.line for e in diags.items)
                    ) and (empty := structure._pass1_scan(
                    bare, structure._Everything(), profile)) in (("pass1", "missing_expr"),
                                                                 ("pass1", "fallback")):
                # a block keyword with no condition at all: the same pass-1 verdicts as CR6/CR1000X
                # (oracle, CR300/CR350, 2026-10-11: bare `If`/`ElseIf`/`Case`/`Loop While` are
                # `Invalid or missing expression.`, `If ` and `Select Case` an empty echo)
                new_items.append(dataclasses.replace(d, message=(
                    "Invalid or missing expression." if empty[1] == "missing_expr"
                    else f"{_GENERIC_FALLBACK_PREFIX}.")))
                changed = True
                continue
            zero = _ZERO_TAIL_RE.match(bare)
            if zero and zero.group(1).upper() in _TAIL_REJECTING and (
                    tok := instr_rules._next_simple_token(zero.group(2))):
                # an instruction with no parameters followed by text: the first token is the
                # surplus (oracle, CR300/CR350, 2026-10-11: `BeginProg, EndProg (Begin Program,
                # End Program)` -> `Too many parameters:  EndProg.`)
                new_items.append(Diagnostic("error", d.line, f"Too many parameters:  {tok}.", d.file))
                changed = True
                continue
            call = re.match(r"\s*([A-Za-z_]\w*)\s*\((.*)$", bare, re.S)
            cinfo = profile.instructions.get(call.group(1).upper()) if call else None
            if cinfo and cinfo["params"] and call.group(1).upper() not in _CALL_ADJACENT_EXCLUDED and (
                    found := _call_adjacent_arg_messages(call.group(2), cinfo, here,
                                                         symbols.declared_variable_names(program),
                                                         profile, _inside_table(program, d.line, d.file))) is not None:
                for message in found:
                    new_items.append(Diagnostic("error", d.line, message, d.file))
                changed = True
                continue
            if cinfo and cinfo["params"] and cinfo["params"][0] % 100 not in (6, 11) and call.group(
                    1).upper() not in ("DATA", "DATALONG", "ALIAS", "UNITS", "SUB", "FUNCTION", "IF", "ELSEIF",
                                      "WHILE", "UNTIL", "SELECT", "CASE", "FOR", "DO", "LOOP") and \
                    (carg := _split_top_commas(call.group(2))[0].rstrip()) and re.search(
                        r"[\w\"]\s+[\w\"]", re.sub(r'"[^"]*"', '"', carg)) and not re.search(
                            r"(?i)\b(?:and|or|xor|eqv|imp|mod|is|to|not)\b", carg):
                # `Instruction(a b, ...)`: the first argument is read as an expression with
                # adjacent operands (oracle, CR300/CR350, 2026-10-11: `MQTTPublishTable(MQTT QoS,
                # ...)` -> both words, `Incomplete expression: MQTT QoS.`, and for a constant-only
                # parameter `Variable illegal in Parameter: MQTT QoS.`); the rest is never read
                if carg.endswith(")") and carg.count("(") < carg.count(")"):
                    carg = carg[:-1].rstrip()  # (the call's own closing parenthesis)
                for word in _leading_unknown_words(carg, here):
                    new_items.append(Diagnostic("error", d.line,
                                                profile.unresolved_symbol_message.format(name=word), d.file))
                new_items.append(Diagnostic("error", d.line, f"Incomplete expression: {carg}.", d.file))
                if cinfo["params"][0] in _CONST_FIRST_PARAM:
                    new_items.append(Diagnostic("error", d.line, f"Variable illegal in Parameter: {carg}.", d.file))
                changed = True
                continue
            lead = _INSTRUCTION_PROSE_RE.match(text)
            info = profile.instructions.get(lead.group(1).upper()) if lead else None
            if info and lead.group(1).upper() not in ("DATA", "DATALONG") and info["params"] \
                    and not (structure.condition_part(text) or "").strip() \
                    and not structure.inline_if_condition(text):
                # `Battery sensor using the`: the instruction reads the prose as its argument
                # (oracle, CR300, 2026-10-01): its words, `Incomplete expression: <prose>.`, and
                # for a constant-only first parameter `Variable illegal in Parameter: <prose>.`
                rest = lead.group(2).rstrip("\n")
                codes = list(info["params"])
                if codes[0] % 100 == 11 and "," not in rest and len(codes) > 1 and codes[1] < 100:
                    # a deferred-text first parameter (`Histogram`, `FFT`, ...) takes the whole text
                    # unread; the next required parameter is then missing (oracle, CR300/CR350,
                    # 2026-10-11)
                    new_items.append(Diagnostic("error", d.line, "Invalid or missing parameter.", d.file))
                    changed = True
                    continue
                if codes[0] % 100 == 6:
                    # a name token (`CallTable`, `DataTable`, `FileMark`): the first word is taken
                    # unchecked, the rest is the next parameter, or the surplus when there is none
                    name_tok = instr_rules._next_simple_token(rest)
                    rest = rest[rest.index(name_tok) + len(name_tok):] if name_tok else rest
                    codes = codes[1:]
                    if not codes:
                        if (tok := instr_rules._next_simple_token(rest)):
                            new_items.append(Diagnostic("error", d.line,
                                                        f"Too many parameters:  {tok}.", d.file))
                            changed = True
                            continue
                    rest = rest.lstrip(" \t")
                if codes and rest:
                    op = _LEAD_OP_WORD_RE.match(rest)
                    words = [] if op else _leading_unknown_words(rest, here)
                    after = ""
                    if op and op.group(1).upper() not in _LEAD_OP_NO_OPERAND:
                        # a leading binary operator word: the next operand is read, then the
                        # expression is out of place (oracle, CR300/CR350, 2026-10-11)
                        after = _LEAD_OP_SKIP_RE.sub("", rest[op.end():], count=1)
                        words = _leading_unknown_words(after, here)[:1] if _LINE_WORD_RE.match(after) else []
                    for word in words:
                        new_items.append(Diagnostic("error", d.line,
                                                    profile.unresolved_symbol_message.format(name=word),
                                                    d.file))
                    new_items.append(Diagnostic("error", d.line, (
                        f"{_GENERIC_FALLBACK_PREFIX}{rest}." if op else f"Incomplete expression: {rest}."),
                        d.file))
                    if codes[0] in _CONST_FIRST_PARAM and not (
                            op and not _LINE_WORD_RE.match(after)):
                        new_items.append(Diagnostic("error", d.line,
                                                    f"Variable illegal in Parameter: {rest}.", d.file))
                    changed = True
                    continue
            callables = frozenset(symbols.declared_variable_names(program) | set(profile.functions))
            fw = _LINE_WORD_RE.match(text, len(text) - len(text.lstrip(" \t")))
            needs_paren = bool(
                fw and fw.group(0).upper() not in profile.instructions
                and not text[fw.end():].lstrip(" \t").startswith(("(", "."))
                and fw.group(0).upper() not in symbols.declared_variable_names(program)
                and structure._bare_function_needs_paren(fw.group(0).upper(), profile.functions))
            cond_text = structure.condition_part(text)
            cond_text = cond_text if cond_text and cond_text.strip() else None
            if cond_text is None and re.match(r"(?i)\s*if\b", text):
                cond_text = structure.inline_if_condition(text) or None  # (a one-line `If`'s condition)
            words = _leading_unknown_words(text if cond_text is None else cond_text, here, callables)
            if needs_paren:
                # the operands after a parenthesis-less function are named (oracle, CR300,
                # 2026-10-10: `Timer is bar baz` -> `bar`, `baz`)
                rest = text[fw.end():]  # (a leading `is` counts as an operand, never named)
                if re.fullmatch(r"[\w\s]*", rest):
                    # as many operands as the function has parameters are read (oracle, CR300,
                    # 2026-10-10: `Timer foo bar baz` names three, `COS foo bar` one)
                    count = profile.functions[fw.group(0).upper()].get("nargs") or 1
                    words = [w for w in re.findall(r"[A-Za-z_]\w*|\d+", rest)[:count]
                             if not w[0].isdigit() and w.upper() not in here and w.upper() != "IS"]
                else:
                    words = _leading_unknown_words(rest, here)
            elif re.match(r"(?i)\s*NOT\s", text):
                words = _leading_unknown_words(re.sub(r"(?i)^\s*NOT\b", "", text), here)
            for word in words:
                new_items.append(Diagnostic(
                    "error", d.line, profile.unresolved_symbol_message.format(name=word), d.file,
                    True))
                changed = True
            if needs_paren:
                # a built-in function used as the first word without its parenthesis (oracle,
                # CR300/CR350, 2026-10-08: `Sin foo`, `Timer 1`, `COS of angle`)
                d = dataclasses.replace(d, message=f"Expression expects ( : {text}.")
                changed = True
            if d.message.startswith(_GENERIC_FALLBACK_PREFIX) and (stmt := unparsed_text.get(
                    (d.line, d.file, _echo_key(text)))):
                # an assignment's pass-1 syntax verdicts are the same words on CR300/CR350
                # (oracle, 2026-10-10: `X = 1 2` -> `Incomplete expression`, `X = (1 2)` -> `No Matching,
                # or out of place )`), whatever names precede them
                cond = structure.condition_part(stmt.text)
                cond = cond if cond and cond.strip() else None
                scan_text = stmt.text
                inline = structure.inline_if_condition(stmt.text) if cond is None else None
                if inline and ";" in inline:
                    cond, inline = inline, None   # `If X ; = 1 Then Y = 2`: the condition alone
                if inline:
                    # `If X Y Then Y = 1`: an unreadable condition is reported alone, as for a block
                    # `If` (oracle, CR300/CR350 and CR6, 2026-10-10)
                    scan_text = "If " + inline + "Then"
                    first = structure._pass1_scan(scan_text, structure._Everything(), profile)
                    if first and first[0] == "pass1" and first[1] in ("incomplete", "no_matching", "fallback"):
                        cond = inline
                verdict = (structure._pass1_scan(scan_text, structure._Everything(), profile)
                           if cond is not None
                           or re.match(r"\s*(?:[A-Za-z_]\w*\s*=|NOT\b)", stmt.text, re.I)
                           or re.match(r"\s*(?:\d[\w.]*|-\s*\d[\w.]*|\"[^\"]*\")\s*=", stmt.text)
                           or re.match(r"\s*[A-Za-z_]\w*\s+(?:AND|OR|XOR|IMP|EQV)\b[^=]*[\w)]\s*=", stmt.text, re.I)
                           or re.search(r"\b(?:AND|OR|XOR|IMP|EQV)\b[^=]*=", stmt.text, re.I)
                           else None)  # (prose stays generic)
                head = {"invalid_assignment": "Invalid assignment in expression: ",
                        "incomplete": "Incomplete expression: ",
                        "no_matching": "No Matching, or out of place ) in expression: ",
                        "expects_comma": "Expression expects , : "}.get(
                            verdict[1] if verdict and verdict[0] == "pass1" else None)
                call = re.match(r"\s*([A-Za-z_]\w*)\s*\(([^()]*)", stmt.text)
                juxt = call and stmt.reason != "lex error: unterminated string" and re.search(
                    r"[\w\"]\s+(?!(?:AND|OR|XOR|MOD|IMP|EQV)\b)[A-Za-z_\d\"]", call.group(2), re.I)
                if (head is None and call and call.group(1).upper() in profile.functions
                        and call.group(1).upper() not in symbols.declared_variable_names(program)
                        and juxt):
                    # a built-in call whose argument list holds juxtaposed operands (oracle, CR300,
                    # 2026-10-10: `Sin (foo bar)`, `ArrayIndex (Array Index)`): `Expression expects ,`
                    # when the argument is not the function's last, else `No Matching` (oracle,
                    # CR300, 2026-10-11: `Round (A B, 1)`, `UDPDataGram("a",UDP Port, ...) - tail`
                    # expect a comma, `Sin (A B)`, `Round (A, 1 B)` do not); text after the `)` is not read
                    k = call.group(2)[:juxt.start() + 1].count(",") + 1
                    nargs = profile.functions[call.group(1).upper()].get("nargs") or 0
                    head = ("Expression expects , : " if k < nargs
                            else "No Matching, or out of place ) in expression: ")
                    if call.group(1).upper() in structure._SELF_CLOSING_FUNCTIONS:
                        head = "Incomplete expression: "   # (`Abs(x y)`, oracle, CR300, 2026-10-12)
                rhs_call = re.match(r"\s*[A-Za-z_]\w*\s*=\s*([A-Za-z_]\w*)\s*\((.*)$", stmt.text, re.S)
                if (verdict and verdict[0] == "pass1" and rhs_call
                        and rhs_call.group(1).upper() in profile.functions and not words
                        and (verdict[1] in ("no_matching", "expects_comma") or (
                            verdict[1] == "incomplete"
                            and rhs_call.group(1).upper() in structure._SELF_CLOSING_FUNCTIONS))):
                    # the bad argument's own undefined words come first (oracle, CR300, 2026-10-12:
                    # `x = Sin(Zork Y)` -> `Zork is not defined.` then the message)
                    parts = _split_args_quoted(rhs_call.group(2)) or []
                    bad = next((a for a in parts if _adjacent_operands(a)), None)
                    for word in (_leading_unknown_words(bad.strip(), here) if bad else []):
                        new_items.append(Diagnostic("error", d.line,
                                                    profile.unresolved_symbol_message.format(name=word),
                                                    d.file, True))
                if head == "No Matching, or out of place ) in expression: " and cond is None \
                        and stmt.reason != "lex error: unterminated string" and (
                        refined := structure._unclosed_call_message(
                            profile, stmt.text, stmt.at_eol, symbols.declared_variable_names(program))):
                    # a call left open at the end of the line: its argument count decides the message
                    # as on CR6/CR1000X (oracle, CR300/CR350, 2026-10-11: `x = Round(x` -> `Expression
                    # expects ,`, `x = FileOpen (s, "r",` -> the generic fallback)
                    d = dataclasses.replace(d, message=refined)
                    changed = True
                elif cond is not None:
                    if head == "No Matching, or out of place ) in expression: " and (
                            refined := structure._unclosed_call_message(
                                profile, cond, False, symbols.declared_variable_names(program))
                            ) and refined.startswith("Expression expects ,"):
                        # `If Round(X Then`: a call left open in a condition, same count rule as a
                        # statement (oracle, CR300/CR350, 2026-10-12)
                        head = "Expression expects , : "
                    # a `While`/`Do While|Until`/`Loop While|Until` condition echoes alone, no
                    # line-end newline (oracle, CR300/CR350, 2026-10-10)
                    if head is None and ";" in cond:   # (oracle, 2026-10-12: `Do While X ;`, `Case 1 ;`)
                        head = "Invalid symbol in expression: "
                    d = dataclasses.replace(d, message=(head or _GENERIC_FALLBACK_PREFIX) + cond + ".")
                    changed = True
                elif head:
                    d = dataclasses.replace(d, message=head + d.message[len(_GENERIC_FALLBACK_PREFIX):])
                    changed = True
        new_items.append(d)
    if changed:
        diags.items[:] = new_items
        return


_ARITY_PREFIXES = {"many": "No Matching, or out of place ) in expression: ",
                   "few": "Expression expects , : ",
                   "empty": "Invalid, or out of place expression: "}


def _arity_walk(expr: S.Expr | None, known: set[str], profile, state: dict) -> None:
    """Left-to-right parse-order walk collecting `state["names"]` (undefined names met) until a
    built-in call with too many arguments stops it at its first surplus argument
    (`state["kind"] == "many"`); too few arguments only mark `state["kind"] == "few"`."""
    if expr is None or state.get("stop"):
        return
    if isinstance(expr, S.Name):
        if expr.name.upper() not in known or expr.name.upper() in getattr(known, "tables", ()):
            state["names"].append(expr.name)
        return
    if isinstance(expr, S.Index):
        if expr.name.upper() not in known:
            state["names"].append(expr.name)
        problem = expressions._call_arity_problem(expr.name, expr.args, profile)
        args, stop = expr.args, False
        if problem == "many":
            at = next(k for k in range(len(args))
                      if expressions._call_arity_problem(expr.name, args[:k + 1], profile) == "many")
            args, stop = args[:at], True
            state.setdefault("kind", "many")
        elif problem in ("few", "empty"):
            state.setdefault("kind", problem)
        elif problem:
            state["bad"] = True
        for a in args:
            _arity_walk(a, known, profile, state)
        state["stop"] = state.get("stop") or stop
        return
    if isinstance(expr, S.Unary):
        _arity_walk(expr.operand, known, profile, state)
    elif isinstance(expr, S.Binary):
        _arity_walk(expr.left, known, profile, state)
        _arity_walk(expr.right, known, profile, state)
    elif isinstance(expr, S.Member):
        if not (isinstance(expr.base, S.Name) and expr.base.name.upper() in known):
            _arity_walk(expr.base, known, profile, state)  # (`Tb.x`: a table is fine as the base)
        for a in expr.args or []:
            _arity_walk(a, known, profile, state)
    elif isinstance(expr, S.ArrayLiteral):
        for item in expr.items:
            _arity_walk(item, known, profile, state)


def order_names_around_arity(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: the parser reports undefined names as it meets them and the arity error of a
    built-in call last (oracle, 2026-10-03): `Sc = Nope3+Round(Nope1)` -> `Nope3`, `Nope1`, then
    `Expression expects , :`. A call with too many arguments stops the parse at its first surplus
    argument, so later names are not reported (`Floor(Nope1,Nope)` -> `Nope1` only,
    `Floor(Sd,Nope)+Nope3` -> none); too few arguments let the rest of the expression be parsed."""
    if profile.name not in ("cr300", "cr350"):
        return
    per_line = collections.Counter((st.line, st.file) for st in program.statements)
    known = None
    for stmt in program.statements:
        if isinstance(stmt, S.Assignment):
            exprs = [stmt.target, stmt.value]
        elif isinstance(stmt, S.If):
            exprs = [stmt.cond]
        else:
            continue
        key = (stmt.line, stmt.file)
        if per_line[key] != 1:
            continue
        own = [(i, d) for i, d in enumerate(diags.items) if (d.line, d.file) == key
               and d.severity == "error"]
        if known is None:
            known = expressions._known_names(program, profile)
        state = {"names": []}
        for e in exprs:
            _arity_walk(e, known, profile, state)
        kind = state.get("kind")
        if state.get("bad") or kind is None:
            continue
        arity = [(i, d) for i, d in own if d.message.startswith(_ARITY_PREFIXES[kind])]
        if not arity and isinstance(stmt, S.Assignment) and own and expressions._undefined_names(
                stmt.target, known):
            # an unresolved target hides the pass-1 arity check, but the parse still reaches it
            arity = [(own[0][0], Diagnostic("error", stmt.line, expressions._arity_message(
                kind, stmt.text, profile, stmt.at_eol, True), stmt.file, show_file=False))]
        if len(arity) != 1 or any(
                d.message != arity[0][1].message and not d.message.endswith(_UNRESOLVED_SUFFIX)
                for _, d in own):
            continue
        new = [Diagnostic("error", stmt.line, profile.unresolved_symbol_message.format(name=n),
                          stmt.file) for n in state["names"]] + [arity[0][1]]
        first = own[0][0]
        drop = {id(d) for _, d in own}
        items = [d for d in diags.items if id(d) not in drop]
        at = first - sum(1 for i, d in enumerate(diags.items[:first]) if id(d) in drop)
        items[at:at] = new
        diags.items[:] = items


def order_names_before_call_arity(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: an assignment that calls a user `Function` with the wrong argument count and
    also names undefined variables reports the names first, then the call's messages (oracle,
    2026-10-12: `A(Z) = F2(1,2,3)`, `Q = F2(1,2,3)`, `A(Z) = A(Y) + F2(1)`: `Z is not defined.`,
    `Y is not defined.`, then `Call argument required.` / the `No Matching` or fallback line)."""
    if profile.name not in ("cr300", "cr350"):
        return
    functions = {st.name.upper() for st in program.statements
                 if isinstance(st, S.SubDef) and st.keyword == "FUNCTION"}
    if not functions:
        return
    per_line = collections.Counter((st.line, st.file) for st in program.statements)
    for stmt in program.statements:
        if not isinstance(stmt, S.Assignment) or per_line[(stmt.line, stmt.file)] != 1:
            continue
        if not any(re.search(rf"(?i)\b{re.escape(f)}\s*\(", stmt.text or "") for f in functions):
            continue
        key = (stmt.line, stmt.file)
        at = [i for i, d in enumerate(diags.items) if (d.line, d.file) == key]
        if not at or at != list(range(at[0], at[-1] + 1)):
            continue
        own = [diags.items[i] for i in at]
        names = [d for d in own if re.fullmatch(r"\w+ is not defined\.", d.message)]
        rest = [d for d in own if d not in names]
        if not names or not rest or not all(
                d.message == "Call argument required." or d.message.startswith((
                    "No Matching, or out of place ) in expression: ",
                    "Invalid, or out of place expression: ")) for d in rest):
            continue
        diags.items[at[0]:at[-1] + 1] = names + rest


_CLOSER_ERRORS = frozenset({
    "END IF without IF or END SELECT without SELECT.", "NEXT without FOR.", "LOOP without DO.",
    "IF or SELECT statement illegally has SCAN without NEXT SCAN.",
    "FOR NEXT statement illegally has SCAN without NEXT SCAN.",
    "LOOP statement illegally has SCAN without NEXT SCAN."})


# Errors that do not hide the closer / `CallTable` errors below (oracle, CR300, 2026-10-04, the
# fuzz reproducers `false_accept/ca5935f3a78b` and `a2f667cdbbc7`): another block-structure error
# (`EndSub without Sub or nested subs.`) and a built-in call's arity error.
def _hides_nothing(d) -> bool:
    if d.message.endswith("without Sub or nested subs."):
        return True
    if d.message == "If without EndIf inside of Sub.":  # oracle, CR300/CR350, 2026-10-11 (`t5` probe)
        return True
    # a built-in call's arity error hides nothing only when a `Public.<name>` reference precedes
    # its first `)` on the line (oracle, 2026-10-04: `Round(Public.x,1,2)`, `Round(x,Public.T,2,3)`
    # hide nothing; `Round(x)+Public.T`, `FormatLong(x)`, `Floor(x,1)` hide the closers)
    if d.message.startswith(("No Matching, or out of place ) in expression", "Expression expects")):
        text = re.sub(r'"[^"]*"', lambda m: " " * len(m.group(0)), d.message.split(":", 1)[-1].lower())
        at = text.find("public.")
        return 0 <= at < (text.find(")") if ")" in text else len(text))
    return False


def drop_block_closer_errors_after_errors(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: the unmatched-closer errors (`END IF without IF ...`, `NEXT without FOR.`,
    `LOOP without DO.` and their `... illegally has SCAN without NEXT SCAN.` companions, with the
    `<closer> illegally occurs before BeginProg.` line) are only reported when no other error
    exists anywhere in the file, before or after them (oracle, CR300, 2026-10-03: `Sc = Nope1`
    then `EndIf`, `EndIf` then `Sc = Nope1`, a duplicate declaration, `Too many parameters`;
    several closer errors do not hide one another). CR6/CR1000X always report them."""
    if profile.name not in ("cr300", "cr350"):
        return
    closers = [d for d in diags.items if d.severity == "error" and d.message in _CLOSER_ERRORS]
    if not closers:
        return
    lines = {(d.line, d.file) for d in closers}
    placement = [d for d in diags.items if d.severity == "error" and (d.line, d.file) in lines
                 and d.message.endswith(" illegally occurs before BeginProg.")]
    ours = {id(d) for d in closers + placement}
    if not any(d.severity == "error" and id(d) not in ours and not _hides_nothing(d)
               for d in diags.items):
        return
    diags.items[:] = [d for d in diags.items if id(d) not in ours]


def drop_calltable_error_with_other_errors(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: the `CallTable` (or table field) `Could not find DataTable %s.` is hidden by any other error
    found by pass 1 or tier B, before or after it (oracle, CR300, 2026-10-03: `CallTable Hourly`
    with `Sc = Nope1` before or after it, or a `Too many parameters`). The late checks run after
    it and print beside it (`SetSetting("Baudrate(9)",1)`, a `Matrix` option error; tests of
    `tests/fixtures/file_drives*.json`, `option_args.json`, `settings_fields.json`)."""
    if profile.name not in ("cr300", "cr350"):
        return
    # (a table field in an expression is hidden the same way: oracle, CR300/CR350, 2026-10-11,
    # `x = Nope.Field(1,1)` with `Zork = 1` before or after it, `chm/tablenameeventend_1.CR6`)
    sites = {(st.line, st.file) for st in program.statements if not isinstance(st, S.Unparsed)}
    mine = [d for d in diags.items if d.severity == "error" and (d.line, d.file) in sites
            and d.message.startswith(("Could not find DataTable ", "Could not find FileMark table ",
                                      "Cannot find DataTable "))]
    lines = {(d.line, d.file) for d in mine}  # (an error on the same line does not hide it: oracle,
    # CR300/CR350, 2026-10-11, `X = Tmp.dat + Zed` prints both)
    if mine and any(d.severity == "error" and d not in mine and not _hides_nothing(d)
                    and (d.line, d.file) not in lines for d in diags.items):
        diags.items[:] = [d for d in diags.items if d not in mine]


def drop_compound_target_repeat(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: `Nope += 1` reports `Nope is not defined.` once, though it is stored as
    `Nope = Nope + 1` (oracle, 2026-10-01; also under the cascade, `i2copen_1.CR6`)."""
    if profile.name not in ("cr300", "cr350"):
        return
    for stmt in program.statements:
        if not (isinstance(stmt, S.Assignment) and stmt.compound
                and isinstance(stmt.target, (S.Name, S.Index))):
            continue
        message = profile.unresolved_symbol_message.format(name=stmt.target.name)
        same = [i for i, d in enumerate(diags.items)
                if (d.line, d.file) == (stmt.line, stmt.file) and d.message == message]
        if len(same) >= 2:
            del diags.items[same[1]]


_CALL_KEYWORD_RE = re.compile(r"\b(?:CASE|DO|FOR|FUNCTION|SCAN|SELECT|SUB|SUBSCAN|WHILE)\s*\(", re.I)


def order_block_keyword_calls(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: a block keyword used as a function (`Y = Scan(1)`) ends the statement's name
    resolution at that call. The names before it are reported (`Q = Scan(1)`, `Y = Q + Scan(1)`,
    `If Q = 1 Then Y = Scan(1)`: `Q is not defined.`) ahead of its `Incomplete expression`;
    names inside or after it are not (`Y = Scan(Q)`, `Y = Scan(1) + Q`, `Y = Scan(Zork(1))`, no
    `Dimensions not declared` either). An indexed target that is not a declared array is the
    generic `Invalid, or out of place expression:` instead, with no `Incomplete`. Oracle, CR300/CR350, 2026-10-10."""
    if profile.name not in ("cr300", "cr350"):
        return
    for stmt in program.statements:
        if not isinstance(stmt, (S.Assignment, S.If, S.ElseIf)):
            continue
        key = (stmt.line, stmt.file)
        # string literals are not code: `LEN("Scan(")` is no call, `LEN("Nope")` names nothing
        code = re.sub(r'"[^"]*"', lambda q: " " * len(q.group()), stmt.text or "")
        m = _CALL_KEYWORD_RE.search(code)
        marks = [d for d in diags.items if (d.line, d.file) == key
                 and d.message.startswith("Incomplete expression:")]
        if not (m and marks):
            continue
        if any((d.line, d.file) == key and d.message.startswith("Invalid, or out of place expression:")
               for d in diags.items):  # `Q(1) = Scan(1)`, an undeclared or scalar indexed target
            diags.items[:] = [d for d in diags.items if d not in marks]
            continue
        before = {w.upper() for w in re.findall(r"[A-Za-z_]\w*", code[:m.start()])}
        keep = []
        for d in diags.items:
            if (d.line, d.file) == key:
                name = re.fullmatch(r"(\w+) is not defined\.", d.message)
                if d.message.startswith("Dimensions not declared") or (
                        name and name.group(1).upper() not in before):
                    continue
            keep.append(d)
        group = [d for d in keep if (d.line, d.file) == key]
        group = [d for d in group if d not in marks] + [d for d in group if d in marks]
        first = next(i for i, d in enumerate(keep) if (d.line, d.file) == key)
        rest = [d for d in keep if (d.line, d.file) != key]
        rest[first:first] = group  # (`first` counts only earlier other-line items: all kept)
        diags.items[:] = rest


def fix_then_operand(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: an `If` whose condition runs into its `Then` (`If X = Then`, `If X + Then`) is
    the condition's own syntax error, not `Then is not defined.` (oracle, 2026-10-10; the same
    message CR6/CR1000X give from `structure._check_if_pass1`)."""
    if profile.name not in ("cr300", "cr350"):
        return
    for stmt in program.statements:
        if not (isinstance(stmt, (S.If, S.ElseIf)) and structure._mentions_then(stmt.cond)):
            continue
        verdict = structure._pass1_scan(stmt.text, structure._Everything(), profile)
        if not (verdict and verdict[0] == "pass1"):
            continue
        key = (stmt.line, stmt.file)
        hits = [i for i, d in enumerate(diags.items)
                if (d.line, d.file) == key and d.message == profile.unresolved_symbol_message.format(name="Then")]
        if not hits:
            continue
        message = structure._pass1_message(profile, dataclasses.replace(stmt, at_eol=False), verdict[1])
        diags.items[hits[0]] = dataclasses.replace(diags.items[hits[0]], message=message)
        for i in reversed(hits[1:]):
            del diags.items[i]


def order_invalid_assignments(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: the undefined names of an assignment come before its `Invalid assignment in
    expression:` line (oracle, 2026-10-10: `Sec = Nope`, `6 = estimated`), which pass 1 emitted first."""
    if profile.name not in ("cr300", "cr350"):
        return
    for key in {(d.line, d.file) for d in diags.items if d.message.startswith("Invalid assignment in expression:")}:
        own = [d for d in diags.items if (d.line, d.file) == key]
        marks = [d for d in own if d.message.startswith("Invalid assignment in expression:")]
        group = [d for d in own if d not in marks] + marks
        first = next(i for i, d in enumerate(diags.items) if (d.line, d.file) == key)
        rest = [d for d in diags.items if (d.line, d.file) != key]
        rest[first:first] = group
        diags.items[:] = rest


_STRING_END_QUOTE = "String without end quote"
_IF_STRING_RE = re.compile(r"(?i)\s*If\s+(.*?)\s+Then\s+([A-Za-z_]\w*)\s*(?:\([^()]*\))?\s*=")


_BINARY_SYMBOLS = frozenset({"+", "-", "*", "/", "^", "&", "<", ">", "=", "<>", "<=", ">=", "\\"})
_WORD_OPERATORS = frozenset({"AND", "OR", "XOR", "EQV", "IMP", "MOD", "IS", "TO"})


def _string_reached(text: str) -> bool:
    """True when the tokens before an unterminated string are a well-formed expression prefix, so
    the lexer's `String without end quote` is met (oracle, CR300/CR350, 2026-10-11: `Foo" bar`,
    `Nope + "abc`, `Len("abc` reach it, `Datalogger</title>")` fails at the `/` first)."""
    try:
        lexer.tokenize(text, 0)
        return False
    except lexer.LexError as e:
        if e.message != "unterminated string" or text[e.col:e.col + 1] != '"':
            return False
        head = text[:e.col]
    toks = [t for t in lexer.tokenize(head, 0) if t.kind != "EOF"]
    expect, depth = True, 0
    for t in toks:
        word_op = t.kind == "IDENT" and t.upper in _WORD_OPERATORS
        if expect:
            if t.kind == "OP" and t.text in ("(", "-", "+"):
                depth += t.text == "("
            elif t.kind == "IDENT" and t.upper == "NOT":
                pass
            elif t.kind in ("IDENT", "NUMBER", "STRING") and not word_op:
                expect = False
            else:
                return False
        elif (t.kind == "OP" and t.text in _BINARY_SYMBOLS) or word_op:
            expect = True
        elif t.kind == "OP" and t.text == "(":
            depth += 1
            expect = True
        elif t.kind == "OP" and t.text == ")":
            depth -= 1
        elif t.kind == "OP" and t.text == "," and depth > 0:
            expect = True
        else:
            return False  # a juxtaposed operand or anything else before the string: not modelled
    return True


def order_unterminated_string(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: a statement cut short by an unterminated string prints its undefined names,
    then `String without end quote`, then its final message (oracle, CR300 and CR350, 2026-10-11:
    `Foo" bar`, `x + "abc`, `Len("abc`, `Nope = "abc`, `If Nope Then Nope2 = "abc`; the final
    message was already right for most shapes). A call of a built-in function that the string leaves
    open is the generic fallback, not `No Matching` (`SprintF ( s, "The ...`). Left alone: a line
    that already has an instruction/call message (`Battery("abc`), and a declared variable with a
    subscript before the string (`x(1) "abc`, no string line)."""
    if profile.name not in ("cr300", "cr350"):
        return
    known = None
    declared = symbols.declared_variable_names(program)
    for stmt in program.statements:
        if not isinstance(stmt, S.Unparsed) or stmt.reason != "lex error: unterminated string":
            continue
        key = (stmt.line, stmt.file)
        own = [d for d in diags.items if (d.line, d.file) == key]
        final = next((d for d in own if d.message.startswith((_GENERIC_FALLBACK_PREFIX, "Incomplete expression:",
                                                              "No Matching, or out of place )"))), None)
        if final is None or not all(d.message.endswith(_UNRESOLVED_SUFFIX) or d.message == _STRING_END_QUOTE
                                    or d is final for d in own):
            continue
        sub = re.match(r"\s*([A-Za-z_]\w*)\s*\([^()\"]*\)\s+\"", stmt.text)
        if (sub and sub.group(1).upper() in declared) or re.match(r"(?i)\s*(?:Public|Dim)\b", stmt.text):
            continue
        if not _IF_STRING_RE.match(stmt.text) and not _string_reached(stmt.text):
            continue
        names = [d for d in own if d.message.endswith(_UNRESOLVED_SUFFIX)]
        if not names and (m := _IF_STRING_RE.match(stmt.text)):
            if known is None:
                known = prose_known(program, profile)
            here = known - declared_later(first_declarations(program), stmt.line, stmt.file) \
                - getattr(diags, "cascade_gone", set())
            words = _leading_unknown_words(m.group(1), here) + (
                [m.group(2)] if m.group(2).upper() not in here else [])
            names = [Diagnostic("error", stmt.line, profile.unresolved_symbol_message.format(name=w),
                                stmt.file, True) for w in words]
        if final.message.startswith("No Matching") and re.match(
                r"\s*([A-Za-z_]\w*)\s*\(", stmt.text) and stmt.text.lstrip().split("(")[0].strip().upper() in profile.functions:
            final = dataclasses.replace(final, message=f"{_GENERIC_FALLBACK_PREFIX}{stmt.text}"
                                        f"{chr(10) if final.message.endswith(chr(10) + '.') else ''}.")
        at = diags.items.index(own[0])
        diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
        diags.items[at:at] = [*names, Diagnostic("error", stmt.line, _STRING_END_QUOTE, stmt.file, True), final]


def semicolon_headers(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: a `;` in a `For` header or `Const` value is one `Invalid symbol in expression:
    <value> ;.` that replaces everything else reported for the line; the echo runs from the value
    that holds the first `;` (after `=`, `To` or `Step`) to the next `To`/`Step` word or the end of the
    statement, the blank before that word excluded (oracle, 2026-10-12:
    `For Y = 1 To 3 Step 1 ;` -> `1 ;`, `For Y = 1 ; To 3` -> ` 1 ;`, `Const K = ; 1` -> `; 1`)."""
    if profile.name not in ("cr300", "cr350"):
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Unparsed) or ";" not in stmt.text:
            continue
        m = re.match(r"\s*(FOR|CONST)\b", stmt.text, re.I)
        if not m:
            continue
        try:
            toks = [t for t in lexer.tokenize(stmt.text, 0) if t.kind != "EOF"]
        except lexer.LexError:
            continue
        semi = next((i for i, t in enumerate(toks) if t.kind == "OP" and t.text == ";"), None)
        if semi is None or semi < 2:
            continue
        if m.group(1).upper() == "CONST":
            eq = next((t for t in toks if t.kind == "OP" and t.text == "="), None)
            if eq is None:
                continue
            echo = stmt.text[(toks[semi].col if toks[semi].col < eq.col else eq.col + 1):].lstrip()
        else:
            start = None
            for t in toks[:semi]:
                if t.kind == "OP" and t.text == "=":
                    start = t.col + 1
                elif t.kind == "IDENT" and t.upper in ("TO", "STEP"):
                    rest = stmt.text[t.col + len(t.text):]
                    start = t.col + len(t.text) + len(rest) - len(rest.lstrip())
            if start is None:
                continue
            stop = next((t for t in toks[semi:] if t.kind == "IDENT" and t.upper in ("TO", "STEP")), None)
            end = len(stmt.text) if stop is None else stop.col - (stop.col > 0 and stmt.text[stop.col - 1] in " \t")
            echo = stmt.text[start:end]
        key = (stmt.line, stmt.file)
        at = next((i for i, d in enumerate(diags.items) if (d.line, d.file) == key), None)
        if at is None:
            continue
        diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
        diags.items.insert(at, Diagnostic("error", stmt.line, f"Invalid symbol in expression: {echo}.", stmt.file, True))


def add_string_illegal(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: an unparsable statement that starts with a well-formed expression holding a
    String operand of a numeric/logical operator prints `String is illegal here` before its final
    message, as on CR6/CR1000X (`structure._unparsed_string_messages`; oracle, CR300/CR350,
    2026-10-11: `"BattV" and "Counter".`, `x = "BattV" and "Counter".`; a lone string or `x "a" and
    "b"` print none)."""
    if profile.name not in ("cr300", "cr350"):
        return
    for stmt in program.statements:
        if not isinstance(stmt, S.Unparsed) or stmt.reason == "lex error: unterminated string":
            continue
        key = (stmt.line, stmt.file)
        own = [d for d in diags.items if (d.line, d.file) == key]
        prefixes = (_GENERIC_FALLBACK_PREFIX, "Incomplete expression:") + (
            ("Invalid symbol in expression:",) if ";" in stmt.text else ())   # `X = "a" and "b" ;`
        final = next((d for d in own if d.message.startswith(prefixes)), None)
        if final is None or any(d.message == "String is illegal here" for d in own):
            continue
        messages = structure._unparsed_string_messages(stmt, program, profile)
        if messages:
            at = diags.items.index(final)
            diags.items[at:at] = [Diagnostic("error", stmt.line, m, stmt.file, True) for m in messages]
    _order_arith_string_lines(diags)


_ARITH_STRING_MESSAGES = ("Illegal String operand", "Illegal operand of type STRING")


def _order_arith_string_lines(diags: Diagnostics) -> None:
    """CR300/CR350: the arithmetic String errors (`Illegal String operand`) come before the
    statement's `X is not defined.` lines, the logical `String is illegal here` after them (oracle,
    CR300, 2026-10-12: `"a" * 2 foo`, `S * 2 foo`, `"a" MOD 2 foo` vs `X AND "a" foo`)."""
    items = diags.items
    strings = (*_ARITH_STRING_MESSAGES, "String is illegal here")
    for key in {(d.line, d.file) for d in items if d.message in strings}:
        slots = [i for i, d in enumerate(items) if (d.line, d.file) == key]
        own = [items[i] for i in slots]
        arith = [d for d in own if d.message in _ARITH_STRING_MESSAGES]
        logical = [d for d in own if d.message == "String is illegal here"]
        names = [d for d in own if d.message.endswith(" is not defined.")]
        if not names or (arith and logical):
            continue
        rest = [d for d in own if d not in arith and d not in logical and d not in names]
        order = arith + names + logical + rest
        if order != own:
            for i, d in zip(slots, order):
                items[i] = d


_TCPOPEN_HANDLE = "Parameter must be a variable of type long"


def check_tcpopen_handle_early(program: S.Program, profile, diags: Diagnostics) -> None:
    """The handle error is a `Too many parameters` cascade trigger (oracle, CR300/CR350,
    2026-10-12: a bad handle inside a `Sub` makes the following `Scan (1,Sec,3,0)` and `Delay(1,..)`
    print `Too many parameters:  Sec.` / `1.`), so it is added before `check_too_many_cascade`;
    `check_tcpopen_handle` re-adds it afterwards on a line the cascade rewrote."""
    check_tcpopen_handle(program, profile, diags, early=True)


def check_tcpopen_handle(program: S.Program, profile, diags: Diagnostics, early: bool = False) -> None:
    """CR300/CR350: `TCPOpen`'s fifth argument (the connection handle) must be a Long or Boolean
    variable or array (a `Boolean` is a 4-byte integer: oracle, CR300/CR350, 2026-10-12, `Public
    Sock As Boolean`, `Sock(3) As Boolean`, `H As Boolean` Sub parameter compile), including a
    `Sub` parameter (`H As Long` / `H As Boolean`; an untyped, `As Float`, `As String` or defaulted
    untyped `Optional H = 1` parameter is bad: same date): a Float or String variable, a constant, an expression or an undefined name (a
    declaration the cascade dropped too) is `Parameter must be a variable of type long` (oracle,
    CR300, CR350 and CR6, 2026-10-11, `chm/tcpopen_4.CR6`), printed after the statement's
    `X is not defined.` lines and before `Dimensions not declared`. CR6/CR1000X not done: their
    line comes first, with the connection count check."""
    if profile.name not in ("cr300", "cr350"):
        return
    types = symbols.declared_variable_types(program)
    known = expressions._known_names(program, profile)
    gone = getattr(diags, "cascade_gone", set())
    params: dict[str, str | None] = {}  # the enclosing Sub/Function's plain parameters: name -> type
    for stmt in program.statements:
        if isinstance(stmt, S.SubDef):
            params = {p.name.upper(): ((p.type or "FLOAT").upper() if p.by_ref is None and not p.dims
                                       and (p.type or "FLOAT").upper() in ("LONG", "BOOLEAN", "FLOAT", "STRING")
                                       else None) for p in stmt.params}
            continue
        if bindings.closes_routine(stmt):
            params = {}
            continue
        if isinstance(stmt, S.Call) and stmt.name.upper() == "TCPOPEN":
            calls = [stmt.args]
        else:
            calls = [ix.args for e in expressions._stmt_exprs(stmt) for ix in expressions._index_nodes(e)
                     if ix.name.upper() == "TCPOPEN"]
        for args in calls:
            if len(args) < 5 or args[4] is None:
                continue
            arg = args[4]
            own_dims = any((d.line, d.file) == (stmt.line, stmt.file)
                           and d.message.startswith("Dimensions not declared") for d in diags.items)
            if own_dims and not (isinstance(args[4], S.Index) and args[4].name.upper() not in known):
                continue  # (an earlier argument's dimension error ends the walk before the fifth)
            if isinstance(args[3], (S.Name, S.Index)) and args[3].name.upper() not in known:
                continue  # (a non-constant fourth argument ends the list: `Parameter must be a constant integer`, not done)
            if isinstance(arg, (S.Name, S.Index)):
                up = arg.name.upper()
                if up in params:
                    if params[up] is None:
                        continue  # (a ByVal/ByRef, array or unusual-type parameter: not probed)
                    bad = params[up] not in ("LONG", "BOOLEAN")
                elif up in gone or up not in known:
                    bad = True
                elif up in types:
                    bad = types[up] not in ("LONG", "BOOLEAN")
                else:
                    continue  # (an alias or a constant: not probed)
            else:
                bad = isinstance(arg, (S.Number, S.String, S.Binary, S.Unary))
            if not bad:
                continue
            key = (stmt.line, stmt.file)
            own = [i for i, d in enumerate(diags.items) if (d.line, d.file) == key]
            if any(diags.items[i].message == _TCPOPEN_HANDLE for i in own) and not early:
                continue
            names = [i for i in own if diags.items[i].message.endswith(_UNRESOLVED_SUFFIX)]
            at = (names[-1] + 1) if names else (own[0] if own else len(diags.items))
            diags.items.insert(at, Diagnostic("error", stmt.line, _TCPOPEN_HANDLE, stmt.file, True))


def order_target_name_first(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: `Socket = TCPOpen` with `Socket` undeclared names the target before the
    `Expression expects (` of the bare function on the right (oracle, 2026-10-10: comportisactive_1,
    ethernetpower_1), though pass 1 emitted that one first."""
    if profile.name not in ("cr300", "cr350"):
        return
    for stmt in program.statements:
        if not (isinstance(stmt, S.Assignment) and isinstance(stmt.target, S.Name)):
            continue
        key = (stmt.line, stmt.file)
        message = profile.unresolved_symbol_message.format(name=stmt.target.name)
        own = [d for d in diags.items if (d.line, d.file) == key]
        mark = next((d for d in own if d.message == message), None)
        if mark is None or own[0] is mark or not any(d.message.startswith("Expression expects (") for d in own):
            continue
        first = next(i for i, d in enumerate(diags.items) if (d.line, d.file) == key)
        diags.items.remove(mark)
        diags.items.insert(first, mark)
