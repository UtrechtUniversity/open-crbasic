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

from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from . import expressions, instr_rules, symbols

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


def _check_baud(stmt: S.Call, profile, diags: Diagnostics) -> None:
    if len(stmt.args) < 2 or not (isinstance(stmt.args[0], S.Name)
                                  and stmt.args[0].name.upper() == "COMRS232"):
        return
    arg = stmt.args[1]
    sign = 1
    if isinstance(arg, S.Unary) and arg.op == "-":
        sign, arg = -1, arg.operand
    if not (isinstance(arg, S.Number) and isinstance(arg.value, int)):
        return
    baud = sign * arg.value
    if not -2**31 <= baud < 2**31:
        return  # `Parameter must be integral.` instead (`checker/intervals.py`)
    if profile.name in ("cr300", "cr350"):
        ok = baud in _BAUDS - {600}
    else:
        ok = abs(baud) in _BAUDS
    if not ok:
        diags.error(stmt.line, f"{baud} is illegal baud rate.", stmt.file)


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
        if _COMPORT_ARG_POSITION >= len(stmt.args) or stmt.args[_COMPORT_ARG_POSITION] is None:
            continue
        arg = stmt.args[_COMPORT_ARG_POSITION]
        names = expressions._undefined_names(arg, known) if cr3 else []
        for undefined_name in names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=undefined_name),
                        stmt.file)
        if name != _SERIALOPEN:
            continue
        _check_baud(stmt, profile, diags)
        is_variable = bool(expressions._variable_refs(arg, variable_names))
        if names or is_variable or isinstance(arg, S.String):
            # A string or variable ComPort, all four loggers, pass 1 (oracle, 2026-10-01:
            # `SerialOpen("fuzz",...)`). CR300/CR350 echo the line end (`docs/loggers.md`).
            diags.error(stmt.line, "ComPort 0 illegal.", stmt.file)
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
_VARIABLE_ILLEGAL_PREFIX = "Variable illegal in Parameter:"
_TOO_MANY_PREFIX = "Too many parameters:"


# Declaration errors taint the same way (oracle, CR300 and CR350, 2026-09-27: each of these
# followed by a clean `Scan (1,Sec,0,0)` gives `Too many parameters:  Sec.` on it).
_DECLARATION_TRIGGERS = ("already declared as a variable.", "is already declared as a CONST.",
                         "illegal, starts with a number.", "not yet declared.", "so cannot be aliased.",
                         "not supported", "Variable not named.", " is illegal baud rate.",
                         "already defined as an internal function.", "is a predefined CONST.",
                         "Only one ConstTable allowed.", "EndConstTable with no ConstTable.",
                         "Missing variable name.", "; cannot be numeric.")
_DECLARATION_TRIGGER_PREFIXES = ("Too many Slow Sequences", "Illegal END or EXIT ", "Illegally dimensioned aliased name", "Invalid Variable declaration",
                                 "EndSelect w/o Select", "String without end quote",
                                 "Call argument required", "No Matching, or out of place )",
                                 "Expression expects ", "Incomplete expression:", "ESS variable ",
                                 "Invalid or missing parameter.",
                                 "Parameter must be a constant integer:",
                                 "Invalid, or out of place expression:",
                                 "Illegal destination variable type", "Invalid Variable or Array:",
                                 "Cannot recursively call functions.", "Parameter must be integral.",
                                 "Interval (", "Illegally dimension: ", "Scan interval must be integrally divisible")


def _is_cascade_trigger(diag) -> bool:
    return diag.severity == "error" and diag.cascade and (
        diag.message.endswith(_UNRESOLVED_SUFFIX) or diag.message.startswith(_VARIABLE_ILLEGAL_PREFIX)
        or diag.message.startswith(_TOO_MANY_PREFIX)
        or diag.message == "Miss match of types"  # SNMPVariable (oracle, CR300/CR350)
        or diag.message.endswith(_DECLARATION_TRIGGERS)
        or diag.message.startswith(_DECLARATION_TRIGGER_PREFIXES)
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
    taint_at = 0
    dropped: set[int] = set()      # tainted declarations: their names are never registered
    tainted_subs: set[int] = set()  # tainted `Sub`/`Function` headers: parameters dropped
    rewritten: set[int] = set()
    for index, stmt in enumerate(program.statements):
        key = (stmt.line, stmt.file)
        existing = [d for d in diags.items if (d.line, d.file) == key]
        if tainted:
            if isinstance(stmt, S.Unparsed) and _TABLE_LINE_RE.match(stmt.text):
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key or not (
                    d.message.startswith("Could not find DataTable ") or (
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
                continue
            if isinstance(stmt, S.SubDef):
                tainted_subs.add(id(stmt))
                # `Sub Foo (A, B)` -> `Too many parameters:  B.`, `Function F (x) As Long` ->
                # `... As.`: the second token after the name (oracle, 2026-09-28)
                m = re.search(r"\((.*)$", stmt.text or "")  # `(x) As Long` -> x, As
                tokens = [t for t in re.split(r"[\s,()=]+", m.group(1)) if t] if m else []
                if len(tokens) >= 2:
                    diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                    diags.error(stmt.line, f"Too many parameters:  {tokens[1]}.", stmt.file)
                continue
            if (isinstance(stmt, S.Instruction) and len(stmt.args) >= 2
                    and stmt.args[1] is not None
                    and stmt.keyword.upper() not in ("UNITS", "READONLY", "ALIAS", "CONSTTABLE")):
                # (`Units X = m` is never rewritten: repository libraries, oracle; nor is an
                # `Alias` or a `ConstTable (T,0)`, oracle 2026-10-03.)
                # the next simple token, like a genuine overflow (oracle: `MainScanSecs * 4` ->
                # `MainScanSecs`, `SearchString()` -> `SearchString`)
                text = instr_rules._next_simple_token(expressions._arg_text(stmt, 1)) or \
                    expressions._arg_text(stmt, 1)
                diags.items[:] = [d for d in diags.items if (d.line, d.file) != key]
                at = next((i for i, d in enumerate(diags.items)
                           if d.file == stmt.file and d.line > stmt.line), len(diags.items))
                diags.items.insert(at, Diagnostic("error", stmt.line,
                                                  f"Too many parameters:  {text}.", stmt.file))
                rewritten.add(id(stmt))
            continue
        if any(_is_cascade_trigger(d) for d in existing):
            tainted = True
            taint_at = index
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
        arg = stmt.args[0]
        key = (stmt.line, stmt.file)
        undefined = [n for n in _expr_names(arg) if n.upper() not in known]
        if undefined:
            if not isinstance(arg, (S.Name, S.Index)):
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
        if keep and expressions._has_undefined_index(arg, known):
            # (oracle, CR300, 2026-10-03: `Battery(Nope(1))`, `PanelTemp(Nope(1),15000)`)
            new.append(Diagnostic("error", stmt.line, expressions.dimensions_message(
                expressions._arg_text(stmt, 0).strip(), profile, False), stmt.file))
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
    bad = {(d.line, d.file) for d in diags.errors
           if d.message.startswith(_VARIABLE_ILLEGAL_PREFIX) and per_line[(d.line, d.file)] == 1}
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
        if id(stmt) not in rewritten or not stmt.args or stmt.args[0] is None:
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
        if id(st) in dropped or id(st) in rewritten or isinstance(st, S.SubDef) or \
                (isinstance(st, S.Instruction) and st.keyword.upper() == "RETURN"):
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
    for d in scratch.items:
        key = (d.line, d.file)
        if key in affected and d.severity == "error":
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
_INSTRUCTION_PROSE_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)[ \t]+([A-Za-z_][^(]*)$", re.S)
_LINE_OPERAND_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?\b")
# Keyword operators join the chain like `+` (oracle, CR300, 2026-10-01: `foo and bar baz qux` ->
# foo, bar, baz; `Is` and `Not` do not).
_LINE_OP_WORD_RE = re.compile(r"[ \t]+(?i:and|or|xor|eqv|imp|mod|to)[ \t]+(?=[A-Za-z_0-9])")
# A line starting `word.` reads as a table field: `Could not find DataTable word.` (even for a
# declared name), and nothing at all once the cascade has started (oracle, CR300, 2026-10-01).
_TABLE_LINE_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\.")
_LINE_OP_RUN_RE = re.compile(r"[-+*/^&<>=]+")
_LINE_WS_RE = re.compile(r"[ \t]+")


def prose_known(program: S.Program, profile) -> set[str]:
    """Names a prose word resolves to: an instruction keyword that is not also a symbol or function
    is reported (oracle, CR300, 2026-10-01: `foo Battery` -> `Battery is not defined.`, `foo Sin`
    and `foo Scan` are not)."""
    instruction_only = set(profile.instructions) - set(profile.symbols) - set(profile.functions)
    return expressions._known_names(program, profile) - instruction_only


def _leading_unknown_words(text: str, known: set[str]) -> list[str]:
    """The ordered list of `text`'s own leading bare words this project believes CR300/CR350
    would report as `%s is not defined.` ahead of the generic fallback for it -- see this
    module's `check_unknown_line_words` comment above for the oracle evidence and the exact walk
    this implements. `known` is the same upper-cased name set `expressions._known_names` builds;
    a word already in it is walked over (it can still be part of the chain) but never returned.
    """
    # The chain starts at the first word or number (a leading `2 when the` reports `when`).
    pos = len(text) - len(text.lstrip(" \t"))
    if not _LINE_OPERAND_RE.match(text, pos):
        return []
    words: list[str] = []
    extending = True  # False once we're consuming the single trailing "peek" word.
    while True:
        operand = _LINE_OPERAND_RE.match(text, pos) if extending else _LINE_WORD_RE.match(text, pos)
        if not operand:
            break
        pos = operand.end()
        if operand.group(0)[0].isdigit():
            pass  # a number is part of the chain but never reported
        elif text.startswith(".", pos):
            break  # `word.` is a table field reference, not a name (oracle, 2026-10-01)
        else:
            words.append(operand.group(0))
        if not extending:
            break
        op_match = _LINE_OP_RUN_RE.match(text, pos) or _LINE_OP_WORD_RE.match(text, pos)
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


def _call_arg_undefined_names(args: list[S.Expr | None], known: set[str]) -> list[str]:
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
    if len(args) > 3:
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
            names = _call_arg_undefined_names(stmt.args, known)
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
                known = prose_known(program, profile)
            text = d.message[len(_GENERIC_FALLBACK_PREFIX):-1]
            table = _TABLE_LINE_RE.match(text)
            if table:
                new_items.append(Diagnostic("error", d.line, f"Could not find DataTable "
                                            f"{table.group(1)}.", d.file, cascade=False))
                d = dataclasses.replace(d, cascade=False)  # (no cascade: oracle, `r1` probe)
                changed = True
            lead = _INSTRUCTION_PROSE_RE.match(text)
            info = profile.instructions.get(lead.group(1).upper()) if lead else None
            if info and lead.group(1).upper() not in ("DATA", "DATALONG") and info["params"]:
                # `Battery sensor using the`: the instruction reads the prose as its argument
                # (oracle, CR300, 2026-10-01): its words, `Incomplete expression: <prose>.`, and
                # for a constant-only first parameter `Variable illegal in Parameter: <prose>.`
                rest = lead.group(2).rstrip("\n")
                for word in _leading_unknown_words(rest, known):
                    new_items.append(Diagnostic("error", d.line,
                                                profile.unresolved_symbol_message.format(name=word),
                                                d.file))
                new_items.append(Diagnostic("error", d.line, f"Incomplete expression: {rest}.",
                                            d.file))
                if info["params"][0] in _CONST_FIRST_PARAM:
                    new_items.append(Diagnostic("error", d.line,
                                                f"Variable illegal in Parameter: {rest}.", d.file))
                changed = True
                continue
            for word in _leading_unknown_words(text, known):
                new_items.append(Diagnostic(
                    "error", d.line, profile.unresolved_symbol_message.format(name=word), d.file,
                    True))
                changed = True
        new_items.append(d)
    if changed:
        diags.items[:] = new_items
        return


_ARITY_PREFIXES = ("No Matching, or out of place ) in expression: ", "Expression expects , : ")


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
        elif problem == "few":
            state.setdefault("kind", "few")
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
        _arity_walk(expr.base, known, profile, state)
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
        arity = [(i, d) for i, d in own if d.message.startswith(_ARITY_PREFIXES)]
        if known is None:
            known = expressions._known_names(program, profile)
        if not arity and isinstance(stmt, S.Assignment) and own and expressions._undefined_names(
                stmt.target, known):
            # an unresolved target hides the pass-1 arity check, but the parse still reaches it
            kind = expressions._find_arity_problem(stmt.value, profile)
            if kind in ("few", "many"):
                arity = [(own[0][0], Diagnostic("error", stmt.line, expressions._arity_message(
                    kind, stmt.text, profile, stmt.at_eol, True), stmt.file, show_file=False))]
        if len(arity) != 1 or any(
                d.message != arity[0][1].message and not d.message.endswith(_UNRESOLVED_SUFFIX)
                for _, d in own):
            continue
        state = {"names": []}
        for e in exprs:
            _arity_walk(e, known, profile, state)
        if state.get("bad") or state.get("kind") is None:
            continue
        new = [Diagnostic("error", stmt.line, profile.unresolved_symbol_message.format(name=n),
                          stmt.file) for n in state["names"]] + [arity[0][1]]
        first = own[0][0]
        drop = {id(d) for _, d in own}
        items = [d for d in diags.items if id(d) not in drop]
        at = first - sum(1 for i, d in enumerate(diags.items[:first]) if id(d) in drop)
        items[at:at] = new
        diags.items[:] = items


_CLOSER_ERRORS = frozenset({
    "END IF without IF or END SELECT without SELECT.", "NEXT without FOR.", "LOOP without DO.",
    "IF or SELECT statement illegally has SCAN without NEXT SCAN.",
    "FOR NEXT statement illegally has SCAN without NEXT SCAN.",
    "LOOP statement illegally has SCAN without NEXT SCAN."})


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
    if not any(d.severity == "error" and id(d) not in ours for d in diags.items):
        return
    diags.items[:] = [d for d in diags.items if id(d) not in ours]


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
