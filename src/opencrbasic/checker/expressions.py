"""Name resolution and type-code-driven value checks for instruction arguments. Rules and oracle
evidence: `docs/checker.md`.
"""

import re

import json
from functools import lru_cache
from pathlib import Path

from .. import consteval
from ..profiles import DottedName
from .. import syntax as S
from ..diagnostics import Diagnostic, Diagnostics
from . import instr_data, symbols

_TYPE_CODES_PATH = Path(__file__).resolve().parent.parent / "data" / "type_codes.json"

# `type_codes.json` `kind`s that route an argument through the real expression/named-constant
# resolver (`FUN_004b8700`/`FUN_004b5c50` and their `const`-only wrappers, `docs/type-codes.md`):
# an unresolved name anywhere inside one of these positions is what produces `Invalid symbol in
# expression: %s.`. Every other kind either never runs general symbol resolution at all (`6`
# identifier/raw text such as `DataTable`'s or `ConstTable`'s own name, captured verbatim and
# validated -- or not -- by a later, different pass) or resolves a fixed keyword list through a
# separate function (`4`/`16`/`17`/`19` `FUN_004b5db0`, a different failure message). Restricting
# to this explicit include-list (rather than excluding the handful of kinds known *not* to
# resolve) is deliberate: the default for an unrecognised kind is "don't check", so a gap in this
# list only under-reports, never falsely rejects (AGENTS.md's priority order); the `ConstTable`
# regression this session found during development (`crbasic/Main.CR6`'s
# `ConstTable(GlobalConfigTable)`, kind `expr_marker`) is the concrete case that shape of bug
# looks like.
_RESOLVED_KINDS = frozenset({
    "const_expr", "expr", "const_expr_only", "bool_expr", "variable_or_array", "expr_or_name",
    "expr_typed", "expr_special", "expr_pair", "expr_list", "datatype_kw",
})

# The compile-time-constant-only family (`docs/type-codes.md` codes 1/39, 5/38, 20 --
# `FUN_004b8700`/its `const`-only wrappers) is checked in its own pass, ahead of every other
# resolved kind, on CR6/CR1000X -- oracle-confirmed (round 4, this session): `WriteIO(1,BazQux,
# FooBar)` (`params=[1,25,101]`: position 0 code 1 = valid literal, position 1 code 25 = `BazQux`,
# position 2 code 101 = optional code 1 = `FooBar`) reports only `FooBar` (position 2, the later
# *const-family* position), never `BazQux` (position 1, the textually-earlier *general* position)
# -- and the reverse instruction shape, `CardTest(GenBad,ConstBad)` (`params=[8,1]`: position 0
# general, position 1 const), reports only `ConstBad` (the const-family position) even though it
# is the *later* argument. Within one family, the ordering is the ordinary ascending textual
# position (`WriteIO(FooBar,BazQux,1)` -> `FooBar`, the first *const*-family position; `WriteIO(1,
# BazQux,FooBar)` with only position 1 bad -> `BazQux`). Read together, this is one const-family
# pre-pass over every const-coded position (ascending) before the general-family pass runs at all,
# not a per-position textual scan -- plausibly the same constant-folding pre-pass CRBasic needs to
# size buffers/arrays from other arguments before it can even parse the rest of the call. Not
# traced to the decompilation; `docs/checker.md` records the oracle transcript.
_CONST_FAMILY_KINDS = frozenset({"const_expr", "const_expr_only", "datatype_kw"})

# CR300/CR350 do not share this pre-pass at all: their own position order is the plain ascending
# textual order, oracle-confirmed with the same `InternalTemp(GenBad,ConstBad)` shape
# (`params=[8,20]`: position 0 general, position 1 const-family `datatype_kw`) reporting the
# textually-*first* position (`GenBad is not defined.`) and then, unlike CR6/CR1000X, abandoning
# the rest of the statement's own parameter list entirely rather than moving on to check position 1
# at all (`Too many parameters:  ConstBad.` -- position 1's own token was never consumed). They also
# echo every undefined name found *inside* one compound expression as its own separate diagnostic
# line, not one whole-expression echo (`docs/checker.md`, "Round 4"): `Randomize(X + FooBar +
# BazQux)` is one `Invalid symbol in expression: X + FooBar + BazQux.` line on CR6/CR1000X but two
# separate `FooBar is not defined.` / `BazQux is not defined.` lines on CR300/CR350, in the source
# (left-to-right) order `_undefined_names` already walks in. Modelled below as a plain per-logger
# branch on `profile.name` (both loggers in each pair share identical behaviour, oracle-confirmed
# side by side) rather than a new `Profile` field, to keep this session's change inside
# `checker/expressions.py`/`checker/symbols.py` (`PLAN.md`'s ownership split).
_WHOLE_EXPR_ECHO_LOGGERS = frozenset({"cr6", "cr1000x"})

# `NAN`/`INF` are accepted as predefined float constants on all four loggers (oracle-confirmed,
# this session, `docs/checker.md`) despite being absent from every `data/<logger>/symbols.json` --
# that table is the compiler's *named-constant* lookup (`FUN_004ee4f0`, `docs/type-codes.md`), and
# `NAN`/`INF` evidently resolve through a separate, special-cased path in the expression compiler
# itself (a floating-point literal spelling, not a named constant) that this session did not trace
# to the decompilation. Added here rather than to the extracted `symbols.json` files so as not to
# misrepresent a value as extracted-from-the-binary-table when it demonstrably is not one of that
# table's rows (AGENTS.md: label inference and unverified behaviour explicitly; here the *acceptance*
# is oracle-verified even though the *mechanism* is not).
_EXTRA_PREDEFINED_SYMBOLS = frozenset({"NAN", "INF"})

# Constant-only-and-rejects-a-variable, the narrower half of `_CONST_FAMILY_KINDS` (`docs/
# type-codes.md` codes 1/39 `const_expr`, 5/38 `const_expr_only` -- **not** 20 `datatype_kw`,
# excluded below): oracle-confirmed uniform across all four loggers, identical message text and no
# per-logger branch needed (unlike the undefined-symbol family) -- `WriteIO(X,1)` ->
# `Variable illegal in Parameter: X.` and `Scan(X+1,Sec,0,0)` -> `Variable illegal in Parameter:
# X+1.` both reproduce byte-for-byte on CR6/CR1000X/CR300/CR350, whole-expression echo included
# (round 4, this session, `docs/checker.md`).
#
# Deliberately narrower than the type code alone would suggest, on two axes, both to route around
# round 3's own found regression (`Sample`'s `DataType` argument, also coded `1`, legitimately
# accepting a `StructureType` name, `structuretype_1.CR6`):
#
# 1. **`datatype_kw` (code 20) is excluded from this set entirely** -- it is a keyword-validated
#    slot (`thunk_FUN_00523010`, not `FUN_004b8700`) with its own per-instruction legal-value list
#    this project has not traced (`docs/type-codes.md` "inferred" confidence only), so no oracle
#    evidence here supports even the ordinary "rejects a variable" half of the rule for it.
# 2. **The trigger is "references a declared runtime variable" (`symbols.declared_variable_names`
#    -- `Public`/`Dim`/`Static`/a `Sub`/`Function` parameter), not "fails to fold to a constant."**
#    A `StructureType` name is neither: it is not a live variable (so this check leaves it alone,
#    unlike a generic `not consteval.is_constant(...)` test would) and it is not expected to fold
#    (so `Sample(1,S,VWSensor)` is simply never flagged by this narrower trigger, without needing
#    a `Sample`-specific carve-out at all). A corpus-wide scan this session ran (every `chm`/`repo`
#    const-only-family argument that is a bare `Name` resolving to a declared *variable*) found
#    exactly one candidate, and it turned out to be an unrelated CHM-prose-as-code parsing
#    artifact (`straincalc_1.CR6`'s `Average Strain` line, a stray heading fragment, not a real
#    statement) rather than a genuine override -- some further override may still exist outside
#    this corpus's ~400-instruction coverage, so this stays a scoped, corpus-verified rule, not a
#    claim that every const-only position everywhere is now covered.
_CONST_ONLY_VARIABLE_REJECT_KINDS = frozenset({"const_expr", "const_expr_only"})


def _variable_refs(expr: S.Expr | None, variable_names: set[str]) -> list[str]:
    """Every reference to a genuine runtime variable (`variable_names`) anywhere inside `expr`,
    in source order -- the const-only-family counterpart of `_undefined_names` above, same
    recursion shape (walks `Unary`/`Binary`/`Index`'s own name and args/`Member`'s base/
    `ArrayLiteral` items; `Member.member` text untouched, same reasoning as `_undefined_names`).
    A name that resolves to anything *other* than a declared variable (a `Const`, a
    `StructureType` name, a predefined symbol, ...) is not itself a live variable, so it is left
    alone here even though it may still be flagged separately by `_undefined_names` if it is
    undefined outright.
    """
    if expr is None:
        return []
    if isinstance(expr, S.Name):
        return [expr.name] if expr.name.upper() in variable_names else []
    if isinstance(expr, S.Unary):
        return _variable_refs(expr.operand, variable_names)
    if isinstance(expr, S.Binary):
        return _variable_refs(expr.left, variable_names) + _variable_refs(expr.right, variable_names)
    if isinstance(expr, S.Index):
        head = [expr.name] if expr.name.upper() in variable_names else []
        for a in expr.args:
            head += _variable_refs(a, variable_names)
        return head
    if isinstance(expr, S.Member):
        return _variable_refs(expr.base, variable_names)
    if isinstance(expr, S.ArrayLiteral):
        out: list[str] = []
        for item in expr.items:
            out += _variable_refs(item, variable_names)
        return out
    return []  # Number, String: nothing to resolve


# Variable-required kinds (`docs/type-codes.md` codes 8, 27, 29, 33, 41, 47, 48): the shared
# "variable or array" handler accepts a bare name, an array reference, or any expression that
# involves a real variable (silently promoted to a hidden temporary) -- but rejects an argument
# that is *itself* a compile-time constant (a bare literal, a named `Const`, or a pure constant
# expression) with `Invalid Variable or Array: %s.`, **unless the constant is a string**
# (oracle-confirmed this session: `Battery(5)`, `Battery(1+2)`, and `Battery(SomeConst)` -- a
# `Const SomeConst = 5` -- all rejected this way; `Battery("x")` compiles fine, no type check at
# all on a string constant here). Still **not** generically checked here, unlike the const-only
# family above: round 3 found two overriding instructions (`Move`'s third argument, `ModBusClient`'s
# last argument, both accepting a bare literal), and round 4's own corpus scan (this session) found
# a *third*, independent of either -- `MQTTPublishTable`'s optional `Longitude`/`Latitude`/
# `Altitude` arguments (code `148`, `mqttpublishtable_1.CR6` line 17, oracle-confirmed accepted in
# isolation on both CR6 and CR1000X: `MQTTPublishTable(1, 0, 5, Min, 2, -111.85, 41.76, 1)`). Three
# confirmed overrides found from a ~523-file corpus sampling only a fraction of each logger's
# ~400-instruction table make a blanket rule too risky to enable without a plausibly-still-
# incomplete per-instruction exception list; unlike the const-only-family rule above, no version of
# this trigger (bare-literal vs. "fails to fold", posed several ways during round 3 and again this
# round) has been found that reliably separates the genuine cases from the overrides using only the
# type-code table. Left unimplemented (AGENTS.md: never reject an accepted program); a future
# session would need to check each variable-required-coded parameter individually across all four
# loggers' instruction tables (or find the compiler's own per-instruction override flag) before
# enabling this safely.
_VARIABLE_REQUIRED_KINDS = frozenset({"variable_or_array"})

# Per-instruction overrides of a `variable_or_array` (kind 8) `Dest` position: `AddPrecise`'s and
# `MovePrecise`'s own first argument (both `params=[8, 2]`, adjacent table indices 25/26) accepts
# an *undeclared* name outright -- oracle-confirmed (round 7, this session, a saved fuzz false-
# reject reproducer, `tests/corpus/fuzz/cr6/false_reject/fac2798b6778.CR6`):
# `AddPrecise(UndeclaredFuzzName123, .000000111111)`/`MovePrecise(UndeclaredMoveDest, .111111)`
# alone both compile clean, unlike `VoltDiff`'s or `Erase`'s own kind-8 `Dest` (same code, oracle-
# swept the same session: both still reject an undeclared name there with the ordinary `Invalid
# symbol in expression: %s.`). **Not a general auto-declaration**: the name does not become usable
# anywhere else in the program (oracle: `AddPrecise(NewVar123,...)` followed by `NewVar123 =
# NewVar123 + 1` still fails, `structure.py`'s own "target does not resolve" placement check,
# `Invalid, or out of place expression: ...`) -- this is narrowly "this one argument position never
# runs the undefined-symbol check at all," the same shape round 3/4's own `Move`/`ModBusClient`/
# `MQTTPublishTable` overrides of the *variable-required* rule already document (`_VARIABLE_
# REQUIRED_KINDS`'s own docstring above), just for the *undefined-name* check instead. Modelled as a
# position exemption (rather than adding the name to `known`) so it costs nothing outside these two
# instructions' own first argument.
# CR6/CR1000X instructions whose compile function reports every undefined argument instead of the
# first one only, in the listed 0-based position order (oracle, 2026-10-04, all positions
# undefined: `Move(a,b,c,d)` reports b, a, d, c; the `*Spa` family b, c, a; `GOESSetup` reports
# its tenth argument before the eighth and ninth; `MoveBytes` 1,2,4,5,3). Positions are independent (subset probes).
_MULTI_REPORT_ORDER: dict[str, tuple[int, ...]] = {
    "MOVE": (1, 0, 3, 2),
    "RMSSPA": (1, 2, 0), "STDDEVSPA": (1, 2, 0), "MAXSPA": (1, 2, 0), "MINSPA": (1, 2, 0),
    "AVGSPA": (1, 2, 0),
    "CPIFILESEND": (0, 1, 2, 3, 4),
    "GOESSETUP": (0, 1, 2, 3, 4, 5, 6, 9, 7, 8),
    "GOESGPS": (0, 1), "CH400SENDOS": (0, 1),
    "MOVEBYTES": (0, 1, 3, 4, 2), "READIO": (1, 0), "IPROUTE": (0, 1, 2),
    "MQTTPUBLISHMETA": (0, 1, 2, 3, 4, 5),
    # Round 14 (oracle sweep, `docs/checker.md` "Round 14 multi-report order"): positions listed
    # are those the real compiler reports, in its order; a replaced position missing here is not
    # reported (or not before another one).
    "ARGOSDATAREPEAT": (0, 3), "AVGRUN": (0, 2, 4, 5), "AVW200": (4, 0), "PRT": (0, 2),
    "STRAINCALC": (0, 2, 3), "CDM_TCSE": (7, 2), "DEBUG": (2, 3, 4),
    "DEWPOINT": (0, 1, 2), "SENDVARIABLES": (0, 1, 8), "EC100CONFIGURE": (0, 3),
    "GETVARIABLES": (0, 8), "GPS": (4, 0), "HYDRAPROBE": (0, 1), "MATRIX": (1, 2, 3),
    "PEAKVALLEY": (0, 1, 3), "SATVP": (0, 1), "TIMEDCONTROL": (4, 5),
    "SDMCD16MASK": (0, 1), "SDMGENERIC": (4, 0), "SDMIO16": (0, 1), "WETDRYBULB": (0, 1, 2, 3),
    "SERIALINRECORD": (1, 5), "SORTSPA": (0, 2), "SORTSPAINDEXED": (0, 2), "SPLITSTR": (1, 0),
    "TCPACTIVECONNECTIONS": (0, 1), "TOTALRUN": (0, 2, 4), "VAPORPRESSURE": (0, 1, 2),
    "MODBUSCLIENT": (0, 5), "STDDEVRUN": (0, 2, 4, 5), "TCDIFF": (5, 0), "PORTBRIDGE": (1, 0),
    "SDMAO4A": (0, 1), "FFTSPA": (2, 0),
}

_UNDEFINED_CHECK_EXEMPT_POSITIONS: dict[str, frozenset[int]] = {
    "ADDPRECISE": frozenset({0, 1}),
    "MOVEPRECISE": frozenset({0, 1}),
    "MODEMCALLBACK": frozenset({0}),  # CR6/CR1000X; fuzz cr1000x/false_reject/5fe667412d48
}

# Round 14 (oracle, CR6/CR1000X only, `docs/checker.md` "Round 14 multi-report order"): an undeclared
# name here compiles clean (CR300/CR350 still report `NewFile(A,"x",Nope)`: `Nope is not defined.`).
_EXEMPT_WHOLE_ECHO: dict[str, frozenset[int]] = {
    "SDMSIO4": frozenset({0}), "MONITORCOMMS": frozenset({0}), "MODEMCALLBACK": frozenset({0}),
    "NEWFILE": frozenset({2}),
}

# Built-in function call arity (this session, oracle-confirmed against `CR6Comp.exe`, byte-for-byte
# re-confirmed on `cr1000xcomp.exe` for the same probe batch -- `docs/checker.md`): a call to a
# `data/functions.json` built-in inside an expression (a `Name(args)` the parser resolves as
# `syntax.Index`, or a bare `syntax.Call` when the whole statement is just the call) with fewer
# arguments than that function's own `nargs` reports `Expression expects , : <echo>.`; more than
# `nargs` reports `No Matching, or out of place ) in expression: <echo>.`. Both are genuinely
# **pass 1**: oracle-confirmed to fire alongside an unrelated, earlier pass-1 `Variable not named.`,
# and themselves to suppress an unrelated *pass-2* `Invalid symbol in expression` finding on a
# different, otherwise-clean statement elsewhere in the same file -- the same "pass 1 found
# something anywhere in the file, pass 2 does not run at all" architecture already documented for
# `instr_rules.py`'s instruction-argument checks (`docs/checker.md` "Pass model").
#
# `nargs` is **not** a safe universal minimum or maximum, so neither direction is applied to every
# function with a decoded `argtypes`: a 92-function oracle sweep (every `data/functions.json` entry
# with `argtypes is not None`, tried at `nargs-1` and `nargs+1` arguments each) found real
# exceptions on both sides -- `NetworkTimeProtocol`/`PingIP`/`HttpPost`/`HttpPut`/`Sprintf`/`Date`
# all accept *fewer* than `nargs` (an undocumented optional trailing argument each); `SerialOpen`
# accepts `nargs+1` (a 6th, undocumented optional argument -- oracle: `SerialOpen(ComC1,9600,0,0,
# 1000,1)` compiles clean on CR6/CR1000X; CR350's own extracted table already lists `nargs=6` for
# it, so this is specifically a CR6/CR1000X table gap, not a real difference in compiler behaviour);
# `Sprintf` is genuinely variadic (2..9 arguments all compile clean; 10 hits an unrelated
# `Expression nesting level exceeded, break up expression.`, not an arity rejection). A one-
# argument-short call to a **zero- or one-argument** function is also excluded from the "too few"
# side entirely: at `nargs` 0 or 1 the "one too few" case is zero arguments, `NAME()`, which the
# oracle sweep found is a *different*, inconsistent message family there (most often the ordinary
# bare-call fallback `Invalid, or out of place expression: %s.`, sometimes a genuinely different
# pass-1 message, `Incomplete expression: %s.`, and for a handful -- e.g. `Sprintf()` itself,
# `PppOpen(1)` -- silently accepted) rather than this rule's own two messages, so it is not modelled
# here (AGENTS.md: implement only the confirmed subset).
#
# Both directions are therefore restricted to an explicit, oracle-verified allow-list per direction
# (`_ARITY_MIN_SAFE`/`_ARITY_MAX_SAFE`) -- exactly the functions this sweep found reject `nargs-1`/
# `nargs+1` with the expected message and nothing else (no unrelated crash, no different message, no
# silent accept), cross-checked identically on `cr1000xcomp.exe` for the whole probe batch (the only
# difference found across the two loggers in that re-run was an unrelated port-range message,
# `CheckPort`'s own `Port must be C1..C4 or U1..U12.` vs `Port must be C1..C8.`, nothing to do with
# arity). CR300/CR350 were spot-checked on `Round`/`Instr` only (not the full 92-function sweep,
# `docs/checker.md`); see `_ARITY_TRAILING_NEWLINE_LOGGERS` below for the one real formatting
# difference found there.
#
# Both maps are keyed by name **and** the `nargs` value this session's oracle sweep actually tested
# (the default/`cr6`/`cr1000x` `data/functions.json`, byte-identical between those two) -- a
# *value*, not just a name allow-list -- because a full-corpus difftest re-run (`tools/difftest.py
# --logger cr350`) caught a real false reject this session first got wrong: CR350's own
# `functions.json` gives `SerialOpen` `nargs=6` (its own table already bakes in the undocumented
# 6th argument CR6/CR1000X's table omits, see above), so a name-only allow-list applied `SerialOpen`
# to `_ARITY_MIN_SAFE` using *CR350's* `nargs` (6) and rejected the ordinary 5-argument form
# (`tests/corpus/chm/serialflush_1.CR6`, `examples/SBD_example_hirise*.CR6`) that CR350 genuinely
# accepts. `MQTTSubscribe`/`NetworkTimeProtocol` have the same shape on CR300/CR350 (a smaller
# `nargs` there than CR6/CR1000X, `docs/checker.md`'s own earlier per-logger `functions.json` diff),
# not yet oracle-tested for whether their *own* true min/max still matches their own smaller
# `nargs` the way `SerialOpen`'s doesn't -- so rather than assume either way, `_call_arity_problem`
# only fires when the profile's own `nargs` for that name is **exactly** the value this sweep
# tested, and silently does not check a name whose profile disagrees (AGENTS.md: under-report
# rather than guess at an unverified cross-logger transfer). This is why the two maps below are
# `{name: nargs}`, not plain `frozenset`s.
#
# **Argument-*kind* mismatches are not implemented**: the same sweep tried swapping a string literal
# where `argtypes` says a float/int expression and vice versa (`Round("x",1)`, `Round(1.5,"x")`,
# `Left(1,2)`, `Mid(1,2,3)`, `Replace(1,"x","y")`, `InStr("x","x","x",1.5)`) and the real compiler
# compiled every one of them clean -- `argtypes`'s 0/1/2/3 codes evidently only select how the value
# is *coerced* at the call site, not a compile-time type check, so there is no "wrong kind"
# diagnostic to reproduce (AGENTS.md: never invent a rejection with no oracle evidence for it).
_ARITY_MIN_SAFE = {
    "CHECKSUM": 3, "DAYLIGHTSAVING": 8, "DIALMODEM": 4, "EMAILRECV": 6, "EMAILSEND": 6,
    "ENCRYPTION": 7, "FILECOPY": 2, "FILELIST": 2, "FILEOPEN": 3, "FILEREAD": 3,
    "FILEREADLINE": 3, "FILERENAME": 2, "FILEWRITE": 3, "FINDSPA": 4, "FORMATFLOAT": 2,
    "FORMATLONG": 2, "FTPCLIENT": 6, "HTTPGET": 3, "IIF": 3, "INSTR": 4, "IPINFO": 2, "LEFT": 2,
    "MID": 3, "MQTTPUBLISH": 4, "MQTTSUBSCRIBE": 4, "REPLACE": 3, "RIGHT": 2, "ROUND": 2,
    "SECSSINCE1990": 2, "SERIALINBLOCK": 3, "SERIALOPEN": 5, "SERIALOUT": 5, "SMSRECV": 3,
    "STRCOMP": 2, "TCPOPEN": 3, "TIMEISBETWEEN": 4, "UDPDATAGRAM": 6, "UDPOPEN": 3,
    "WATCHDOGTIMER": 2, "XMLPARSE": 6,
    # A second, smaller oracle sweep (this session, `docs/checker.md`) of every `argtypes is None`
    # entry (the uniform-float-argument math functions, `kind` 295) found the same rule holds for
    # its own `nargs>=2` members, `TIMER`'s own excepted below:
    "AVE": 2, "PWR": 2, "ATN2": 2, "IFTIME": 3, "TIMEINTOINTERVAL": 3,
    # `TIMER(...)` at `nargs-1` gets `Parameter must be a constant integer: %s.` instead (its own
    # arguments are constant-integer-checked before arity would ever matter) -- deliberately not in
    # this map.
}
_ARITY_MAX_SAFE = {
    "ARRAYINDEX": 1, "ASCII": 1, "CHECKPORT": 1, "CHR": 1, "CHR$": 1, "COMPORTISACTIVE": 1,
    "DAYLIGHTSAVING": 8, "DAYLIGHTSAVINGUS": 1, "ENCRYPTION": 7, "FILECLOSE": 1, "FILECOPY": 2,
    "FILEENCRYPT": 1, "FILELIST": 2, "FILEOPEN": 3, "FILEREAD": 3, "FILEREADLINE": 3,
    "FILERENAME": 2, "FILESIZE": 1, "FILETIME": 1, "FILEWRITE": 3, "FINDSPA": 4,
    "FORMATFLOAT": 2, "FORMATLONG": 2, "FORMATLONGLONG": 1, "HEX": 1, "HEXTODEC": 1, "IIF": 3,
    "INSTR": 4, "IPINFO": 2, "IPTRACE": 1, "LEFT": 2, "LEN": 1, "LOADFIELDCAL": 1,
    "LOWERCASE": 1, "LTRIM": 1, "MID": 3, "MQTTUNSUBSCRIBE": 1,
    "NEWFIELDCAL": 0, "PINGIP": 3, "PPPCLOSE": 0, "REPLACE": 3, "RIGHT": 2, "ROUND": 2,
    "ROUTE": 1, "RTRIM": 1, "SECSSINCE1990": 2, "SERIALCLOSE": 1, "SERIALFLUSH": 1,
    "SERIALINBLOCK": 3, "SERIALINCHK": 1, "SERIALOUT": 5, "SMSRECV": 3, "STRCOMP": 2,
    "TCPCLOSE": 1, "TIMEISBETWEEN": 4, "TRIM": 1, "UPPERCASE": 1, "VOICEKEY": 1,
    "VOICENUMBER": 1, "WATCHDOGTIMER": 2,
    # Same second sweep, `nargs+1` this time: every plain single-float math function tried rejects
    # cleanly, plus the three shared with `_ARITY_MIN_SAFE` above and the two zero-argument
    # functions this specific pair happened to also reject cleanly (`Rnd`/`ClockChange`, unlike the
    # inconsistent zero-argument functions in the first sweep, `docs/checker.md`'s own top-of-file
    # comment) -- `Abs`, `Ave` (this direction only) and `Timer` excepted below.
    "SIN": 1, "COS": 1, "LOG": 1, "LN": 1, "SQR": 1, "EXP": 1, "CSGN": 1, "INT": 1, "FRAC": 1,
    "SGN": 1, "ATN": 1, "TAN": 1, "FIX": 1, "ASIN": 1, "ACOS": 1, "LOG10": 1, "SINH": 1,
    "TANH": 1, "COSH": 1, "FLOOR": 1, "CEILING": 1, "RND": 0, "CLOCKCHANGE": 0,
    "PWR": 2, "ATN2": 2, "IFTIME": 3, "TIMEINTOINTERVAL": 3,
    # `Abs(x,y)` gets `Incomplete expression: %s.` instead (a different message, not modelled by
    # this rule); `Ave(x,y,z)` gets `Parameter must be a constant integer: %s.` (its own later
    # arguments are constant-integer-checked, same shape as `Timer`); `Timer` itself gets that same
    # constant-integer message on *this* side too. None of the three are in this map.
}

# A small group of function shapes reaches a different parser diagnostic before the ordinary
# argument-count handler.  Each entry is restricted to the directly observed boundary: a string
# in a constant-integer position, or one argument beyond the extracted count.
_FUNCTION_CONST_INTEGER_STRING_ARGS = {
    "SERIALOPEN": frozenset({3, 4}),
    "TIMER": frozenset({1}),
}
_CONST_INTEGER_OVERFLOW_STRING = {"TCPOPEN": 3}
# Argument positions that must be an integer constant: a string, `NAN`, a float literal (`1.0`) or an
# integer beyond 32 bits is `Parameter must be a constant integer` (oracle, all four loggers,
# 2026-10-10: `TCPOpen(Addr,6785,"fuzz")`; an integer constant expression and `Hr` compile)
# `SerialOpen`'s 4th and 5th arguments (oracle, all four loggers, 2026-10-11: `1.0`, `999999999999`
# -> `Parameter must be a constant integer`; `Hr`, `1+1`, `-1` compile on CR300 and also CR6/CR1000X
# for `-1` only the range message `usec delay before transmit is illegal`, not modelled).
_CONST_INTEGER_BAD_ARGS = {"TCPOPEN": frozenset({2}), "SERIALOPEN": frozenset({3, 4})}


# Outside an earlier `IfTime`/`TimeIntoInterval` (`intervals.timeout_gate`; set per statement by
# `check_function_arity`, `None` elsewhere so other callers never apply the rule) the trailing
# `TimeOut` of `HTTPPut` (9th) and `FTPClient` (11th) argument, a string (also `""`), `NAN` or a
# float literal, is `Parameter must be a constant integer`, before the argument-count check (oracle,
# all four loggers). After one, the same arguments give a truncate warning / `Parameter must be
# integral.` instead, reported by `intervals.check`. Overflowing literals and Consts are the constant-
# integer error too when `intervals.timeout_value` classifies them as float (`_TIMEOUT_WIDE`, set per
# program by `check_function_arity`). Not modelled: a variable or function call there.
_TIMEOUT_GATE: list = [None]
_TIMEOUT_WIDE: list = [frozenset()]
_CONST_INTEGER_TYPE_ARGS = {"HTTPPUT": frozenset({8}), "FTPCLIENT": frozenset({10})}
# `Timer`'s third argument is the same error whatever came before (it resets the mode first; oracle,
# CR6/CR350, 2026-10-16: also after an `IfTime`).
_CONST_INTEGER_TYPE_ARGS_ALWAYS = {"TIMER": frozenset({2})}


def _timeout_wide(arg, profile) -> bool:
    """Whether a `TimeOut` argument is a float-classified constant (`intervals.timeout_value`)."""
    from . import intervals  # lazy: `intervals` imports this package's modules
    return intervals.timeout_value(arg, None, profile, _TIMEOUT_WIDE[0]) == ("wide", 0)


def _not_integer_type(arg) -> bool:
    if isinstance(arg, S.Number):
        return isinstance(arg.value, float)
    return isinstance(arg, S.String) or (isinstance(arg, S.Name) and arg.name.upper() == "NAN")


def _not_constant_integer(arg) -> bool:
    if isinstance(arg, S.String):
        return True
    if isinstance(arg, S.Name):
        return arg.name.upper() == "NAN"
    if isinstance(arg, S.Number):
        return isinstance(arg.value, float) or not -2**31 <= arg.value < 2**31
    return False
_INCOMPLETE_OVERFLOW = {"ABS": 1, "MQTTPUBLISH": 4}
# Observed (min, max) argument counts where they differ from the table's `nargs`, keyed by that
# profile `nargs` (oracle, 2026-10-03, all four loggers).
# `GZip` (table nargs 1) takes exactly 2 (`Filename, ZippedFilename`; CR6/CR1000X oracle 2026-10-13:
# 1 -> `Expression expects ,`, 3+ -> `No Matching, or out of place )`; absent on CR300/CR350).
# `SerialOpen` (table nargs 5, CR350 6) takes 5 or 6 arguments, CR350 5 to 8 (oracle 2026-10-13: more is
# `No Matching, or out of place )`, 4 or fewer `Expression expects ,`; CR350 accepted 4 or fewer).
_ARITY_RANGE = {"NETWORKTIMEPROTOCOL": {5: (3, 4), 3: (3, 3)}, "GZIP": {1: (2, 2)},
                "SERIALOPEN": {5: (5, 6), 6: (5, 8)}, "TIMER": {3: (3, 3)}}
# (`Timer` takes exactly 3: fewer `Expression expects ,`, more `No Matching, or out of place )`; oracle,
# all four loggers, 2026-10-13, `N = Timer(1,sec)`, `Timer(1,2)`, `N = Timer(1,sec,0,0)`)
# Argument counts a call accepts when they are not one range, keyed by profile `nargs` (oracle,
# 2026-10-10, all four loggers): `HTTPPut` takes 4, 8 or 9 (2, 3, 5, 6, 7 are `few`, 10+ `many`).
# `FTPClient` (table nargs 6) takes 6, 10 or 11 (oracle, all four loggers, 2026-10-11: 7-9 are
# `Expression expects , :`, 12+ `No Matching, or out of place )`).
# `EmailSend` (table nargs 6) takes 9, 13 or 14; `EmailRecv` 9-13; `UDPDatagram` 6-8; `XMLParse` 8;
# `HTTPGet` 3-4; `UDPOpen` 3-4; `TCPOpen` 3, 4 or 6; `HTTPPost` 4, 8 or 9; `CheckSum` 3, 4 or 6
# (CR6 host sweep 2026-10-11, `tests/fixtures/r13_function_arity.json`; fewer is `few`, more `many`).
_ARITY_ALLOWED = {"HTTPPUT": {5: frozenset({4, 8, 9})}, "FTPCLIENT": {6: frozenset({6, 10, 11})},
                  "EMAILSEND": {6: frozenset({9, 13, 14})}, "EMAILRECV": {6: frozenset(range(9, 14))},
                  "UDPDATAGRAM": {6: frozenset({6, 7, 8})}, "XMLPARSE": {6: frozenset({8})},
                  "HTTPGET": {3: frozenset({3, 4})}, "UDPOPEN": {3: frozenset({3, 4})},
                  "TCPOPEN": {3: frozenset({3, 4, 6})}, "HTTPPOST": {5: frozenset({4, 8, 9})},
                  "CHECKSUM": {3: frozenset({3, 4, 6})}}

# CR300/CR350 echo this rule's messages with the offending statement's text followed by a literal
# embedded newline, then the closing `.` alone on the next line -- CR6/CR1000X put the `.`
# immediately after the text on one line (oracle, `Round(X)`/`Round(X,1,2)` alone, both directions,
# `docs/checker.md`). The same split this file already uses for other per-logger message-shape
# differences (`_WHOLE_EXPR_ECHO_LOGGERS`); reused directly rather than adding a new set, since the
# two happen to be the same two loggers on each side for this rule too.
_ARITY_TRAILING_NEWLINE_LOGGERS = frozenset({"cr300", "cr350"})


@lru_cache(maxsize=1)
def _type_code_kinds() -> dict[int, str]:
    data = json.loads(_TYPE_CODES_PATH.read_text())
    return {int(code): entry["kind"] for code, entry in data.items()}


def _position_kinds(params: list[int], keyword: str = "", profile=None) -> dict[int, str]:
    """0-based argument index -> `type_codes.json` `kind`, for every position whose type code
    routes through the general symbol/expression compiler (`_RESOLVED_KINDS`), minus any position
    `_UNDEFINED_CHECK_EXEMPT_POSITIONS` names for this instruction's own keyword."""
    kinds = _type_code_kinds()
    exempt = _UNDEFINED_CHECK_EXEMPT_POSITIONS.get(keyword.upper(), frozenset())
    if profile is not None and profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
        exempt = exempt | _EXEMPT_WHOLE_ECHO.get(keyword.upper(), frozenset())
    out = {}
    for i, code in enumerate(params):
        if i in exempt:
            continue
        base = code - 100 if code >= 100 else code
        kind = kinds.get(base)
        if kind in _RESOLVED_KINDS:
            out[i] = kind
    return out


def _undefined_names(expr: S.Expr | None, known: set[str]) -> list[str]:
    """`_undefined_names_walk`, minus the dotted names after the first plain one: once a name is
    `is not defined.`, a later `Undeclared.member` is not reported (oracle, CR300, 2026-10-11)."""
    out = _undefined_names_walk(expr, known)
    for i, n in enumerate(out):
        if not isinstance(n, DottedName):
            return out[:i + 1] + [m for m in out[i + 1:] if not isinstance(m, DottedName)]
    return out


def _undefined_names_walk(expr: S.Expr | None, known: set[str]) -> list[str]:
    """Every undeclared name referenced anywhere inside `expr` (in source order), recursing into
    unary/binary operators, array indices and array-literal items. `Member` (`Table.Field`,
    `Struct.member`) only recurses into its own base, never its `member` text -- that is a
    field/member name resolved against the base's own type, not a bare symbol lookup, and this
    session found no oracle evidence for how (or whether) it is checked, so it is left alone
    (AGENTS.md: under-report rather than guess). A name used as a `Call`-like `Index`'s own
    function/array name *is* checked, alongside its arguments, matching how the existing bare-name
    case already treated a direct `Index` argument before this session's own extension.
    """
    if expr is None:
        return []
    if isinstance(expr, S.Name):
        if expr.name.endswith(".") and len(expr.name) > 1 and (
                expr.name[0].isalpha() or expr.name[0] == "_"):
            return []  # `T.` / `X.` / `Zork.`: `check_digit_members` owns a trailing-dot name
        upper = expr.name.upper()
        if id(expr) in getattr(known, "out_of_scope", ()):
            return [expr.name]
        return [] if upper in known and upper not in getattr(known, "tables", ()) else [expr.name]
    if isinstance(expr, S.Unary):
        return _undefined_names_walk(expr.operand, known)
    if isinstance(expr, S.Binary):
        return _undefined_names_walk(expr.left, known) + _undefined_names_walk(expr.right, known)
    if isinstance(expr, S.Index):
        head = [] if expr.name.upper() in known and id(expr) not in getattr(
            known, "out_of_scope", ()) else [expr.name]
        for a in expr.args:
            head += _undefined_names_walk(a, known)
        return head
    if isinstance(expr, S.Member):
        # ... but its index arguments are (`Status.TimeStamp(Nope,1)`, oracle 2026-09-28). A
        # table name is fine as the base (`T.Field`).
        base = expr.base
        out = [] if isinstance(base, S.Name) and base.name.upper() in known \
            and id(base) not in getattr(known, "out_of_scope", ()) else \
            _undefined_names_walk(base, known)
        if out and isinstance(base, S.Name) and getattr(known, "dotted_tables", False):
            out = [DottedName(base.name)]
        for a in getattr(expr, "args", None) or []:
            out += _undefined_names_walk(a, known)
        return out
    if isinstance(expr, S.ArrayLiteral):
        out: list[str] = []
        for item in expr.items:
            out += _undefined_names_walk(item, known)
        return out
    return []  # Number, String: nothing to resolve



def _names_through_over_index(expr: S.Expr | None, known: set[str], dim_counts: dict) -> list[str]:
    """The undefined names of `expr` up to and including the arguments of its first over-indexed
    subscript (`_over_indexed`): the compile of the statement ends there (oracle, CR300/CR350,
    2026-10-12: `x = R(4,Zork) + R(5,Zork2)` for `Public R(4)` names only `Zork`)."""
    out: list[str] = []

    def walk(e: S.Expr | None) -> bool:
        if e is None:
            return False
        if isinstance(e, S.Unary):
            return walk(e.operand)
        if isinstance(e, S.Binary):
            return walk(e.left) or walk(e.right)
        if isinstance(e, S.Index):
            if e.name.upper() not in known:
                out.append(e.name)
            for a in e.args:
                if walk(a):
                    return True
            count = dim_counts.get(e.name.upper())
            return count is not None and len(e.args) > count and not (
                len(e.args) == 3 and e.name.upper() in getattr(dim_counts, "strings", ()))
        out.extend(_undefined_names_walk(e, known))
        return False

    walk(expr)
    return out


def _dim_limit(profile) -> int | None:
    """`_over_indexed`'s `limit`: CR6/CR1000X reject only more than three subscripts (round 16 of
    `docs/checker.md`), CR300/CR350 any beyond the declared dimensions."""
    return 3 if profile.name in _WHOLE_EXPR_ECHO_LOGGERS else None


def _over_indexed(expr: S.Expr | None, dim_counts: dict[str, int], limit: int | None = None) -> bool:
    """Whether `expr` indexes a declared variable with more subscripts than it has dimensions
    (`limit`: more than `limit` subscripts instead). CR300/CR350 reject `R(2,1)` for `Public R(5)`
    and `X(1)` for a scalar; CR6/CR1000X only more than 3 (oracle, 2026-09-28)."""
    if expr is None:
        return False
    if isinstance(expr, S.Index):
        count = dim_counts.get(expr.name.upper())
        if count is not None and len(expr.args) > (count if limit is None else limit) and not (
                len(expr.args) == 3 and expr.name.upper() in getattr(dim_counts, "strings", ())):
            return True  # (a String takes three subscripts: CR300 accepts `S(1,2,3)`, 2026-10-01)
        return any(_over_indexed(a, dim_counts, limit) for a in expr.args)
    if isinstance(expr, S.Unary):
        return _over_indexed(expr.operand, dim_counts, limit)
    if isinstance(expr, S.Binary):
        return _over_indexed(expr.left, dim_counts, limit) or \
            _over_indexed(expr.right, dim_counts, limit)
    if isinstance(expr, S.Member):
        return any(_over_indexed(a, dim_counts, limit) for a in (expr.args or []))
    return False


_NO_NAMES: frozenset = frozenset()


class DimsState:
    """The CR300/CR350 compiler's running "dimension count" (oracle, CR300, 2026-10-03,
    `docs/checker.md` "Dimensions not declared"): every reference to a declared variable, in text
    order and across statements, sets it to that variable's number of dimensions (a scalar 0, a
    `String` one more than declared); constants, predefined names and undefined names leave it
    alone. An undefined name with more subscripts than the current count is
    `Dimensions not declared`. `var_dims` maps names to counts (None: counted differently in
    two declarations), `before` maps `id(stmt)` to the count when the statement starts."""

    def __init__(self, var_dims: dict, before: dict | None = None, dim_counts: dict | None = None):
        self.var_dims = var_dims
        self.before = before or {}
        self.dim_counts = dim_counts    # `symbols.declared_dim_counts(program, strings=True)`


def _stmt_exprs(stmt: S.Stmt) -> list:
    if isinstance(stmt, S.Assignment):
        return [stmt.target, stmt.value]
    if isinstance(stmt, (S.If, S.ElseIf, S.DoLoop)):
        return [stmt.cond]
    if isinstance(stmt, S.For):
        return [stmt.var, stmt.start, stmt.end, stmt.step]
    if isinstance(stmt, S.Call) and stmt.tail_expr is not None:
        return [stmt.tail_expr]
    if isinstance(stmt, (S.Instruction, S.Call)):
        return list(stmt.args)
    return []


def program_dims(program: S.Program, profile, known) -> DimsState:
    """The one `DimsState` every consumer shares: single-pass loggers (CR300/CR350) stop a
    statement at its first undefined name (`known`), the others simulate over declared variables."""
    return dims_state(program, known if profile.single_pass else _NO_NAMES)


def dims_state(program: S.Program, known=_NO_NAMES) -> DimsState:
    """`DimsState` for `program`: variable dimension counts, and the count at each statement's
    start, simulated over every statement in order (one-line `If` bodies included)."""
    var_dims: dict[str, int | None] = {}

    def declare(name: str, count: int) -> None:
        name = name.upper()
        var_dims[name] = count if var_dims.get(name, count) == count else None

    for stmt in program.statements:
        if isinstance(stmt, S.Declaration):
            for v in stmt.vars:
                declare(v.name, len(v.dims) + ((v.type or "").upper() == "STRING"))
        elif isinstance(stmt, S.SubDef):
            for prm in stmt.params:
                declare(prm.name, prm.dims + ((prm.type or "").upper() == "STRING"))
    ordered: list[S.Stmt] = []
    present = {id(st) for st in program.statements}
    for stmt in program.statements:
        ordered.append(stmt)
        if isinstance(stmt, S.If):
            ordered.extend(st for st in (stmt.inline or []) + (stmt.inline_else or [])
                           if id(st) not in present)
    before: dict[int, int | None] = {}
    state: list = [0]
    for stmt in ordered:
        before[id(stmt)] = state[0]
        for expr in _stmt_exprs(stmt):
            if _dims_walk(expr, known, var_dims, state):
                break
    return DimsState(var_dims, before, symbols.declared_dim_counts(program, strings=True))



def _dims_walk(expr: S.Expr | None, known, var_dims: dict, state: list) -> bool:
    """Walks `expr` in text order updating `state[0]`; True when an undefined name has more
    subscripts than the current count. With `known` empty every name counts as undefined, so only
    declared variables (`var_dims`) move the state: that is the simulation mode."""
    if expr is None:
        return False
    oos = id(expr) in getattr(known, "out_of_scope", ())  # another routine's `Dim`: undefined here
    if isinstance(expr, S.Name):
        upper = expr.name.upper()
        if upper in var_dims and not oos:
            state[0] = var_dims[upper]
        return False
    if isinstance(expr, S.Index):
        upper = expr.name.upper()
        if upper in var_dims and not oos:
            state[0] = var_dims[upper]
        elif (upper not in known or oos) and known is not _NO_NAMES:
            count = sum(1 for a in expr.args if a is not None)
            if state[0] is not None and count > state[0]:
                return True
        return any(_dims_walk(a, known, var_dims, state) for a in expr.args)
    if isinstance(expr, S.Unary):
        return _dims_walk(expr.operand, known, var_dims, state)
    if isinstance(expr, S.Binary):
        return (_dims_walk(expr.left, known, var_dims, state)
                or _dims_walk(expr.right, known, var_dims, state))
    if isinstance(expr, S.Member):
        return (_dims_walk(expr.base, known, var_dims, state)
                or any(_dims_walk(a, known, var_dims, state) for a in expr.args or []))
    if isinstance(expr, S.ArrayLiteral):
        return any(_dims_walk(a, known, var_dims, state) for a in expr.items)
    return False


def _has_undefined_index(expr: S.Expr | None, known: set[str], ds: DimsState | None = None,
                         stmt: S.Stmt | None = None, prior: tuple = ()) -> bool:
    """Whether CR300/CR350 follow the unresolved-name lines with `Dimensions not declared: %s`:
    the walk over `prior` (the statement's earlier arguments, or the assignment target) then
    `expr`, starting from the count at `stmt`'s start (`DimsState`), meets an undefined name with
    more subscripts than the current count. Without `ds` the count is 0 (any subscript)."""
    state = [ds.before.get(id(stmt), 0) if ds and stmt is not None else 0]
    var_dims = ds.var_dims if ds else {}
    return any(_dims_walk(e, known, var_dims, state) for e in (*prior, expr))


def _names_and_dims(known: set[str], ds: DimsState | None, stmt: S.Stmt | None, prior: tuple,
                    *exprs: S.Expr | None) -> tuple[list[str], bool]:
    """The undefined names of `exprs` and whether `Dimensions not declared` follows them
    (`_has_undefined_index`). When it does, the walk ends at the triggering subscripted name, so
    the names after it are not reported (oracle, CR300, 2026-10-03: `Battery(Nope(1)+Nope2(1))`
    names only `Nope`)."""
    dims = _has_undefined_index(None, known, ds, stmt, (*prior, *exprs))
    if not dims:
        return [n for e in exprs for n in _undefined_names(e, known)], False
    state = [ds.before.get(id(stmt), 0) if ds and stmt is not None else 0]
    var_dims = ds.var_dims if ds else {}
    for e in prior:
        _dims_walk(e, known, var_dims, state)
    out: list[str] = []

    def walk(e: S.Expr | None) -> bool:
        if e is None:
            return False
        oos = id(e) in getattr(known, "out_of_scope", ())
        if isinstance(e, S.Name):
            if e.name.upper() in var_dims and not oos:
                state[0] = var_dims[e.name.upper()]
            if e.name.upper() not in known or oos or e.name.upper() in getattr(known, "tables", ()):
                out.append(e.name)
            return False
        if isinstance(e, S.Index):
            upper = e.name.upper()
            if upper in var_dims and not oos:
                state[0] = var_dims[upper]
            if upper not in known or oos:
                out.append(e.name)
                count = sum(1 for a in e.args if a is not None)
                if state[0] is not None and count > state[0]:
                    for a in e.args:  # its own arguments are still named
                        walk(a)
                    return True
            for a in e.args:
                if walk(a):
                    return True
            return False
        children = {S.Unary: lambda: [e.operand], S.Binary: lambda: [e.left, e.right],
                    S.Member: lambda: [*([] if isinstance(e.base, S.Name)
                                         and e.base.name.upper() in known else [e.base]),
                                       *(e.args or [])],
                    S.ArrayLiteral: lambda: e.items}.get(type(e))
        if children:
            for c in children():
                if walk(c):
                    return True
        return False

    for e in exprs:
        if walk(e):
            break
    return out, True


def dimensions_message(text: str, profile, whole_statement: bool, at_eol: bool = True) -> str:
    sep = "\n" if whole_statement and profile.fallback_echo_newline and at_eol else ""
    return f"Dimensions not declared: {text}{sep}."

def _arg_text(stmt: S.Instruction, i: int) -> str:
    """The argument's own verbatim source text (`Instruction.arg_spans`, `parser.py`), for the
    messages below that echo a whole expression rather than a single name (oracle-confirmed,
    `docs/checker.md`: `Scan(X+1,...)` echoes `X+1`, not a re-derived or spacing-normalised form).
    Falls back to the bare name (still correct for the common single-name case) if a span was
    somehow not captured.
    """
    if i == 0 and getattr(stmt, "keyword", "").upper() == "SELECT":
        m = re.match(r"(?i)\s*Select\s+Case(.*)$", stmt.text or "")
        if m:
            return m.group(1)  # every space after `Case` kept (oracle: `Select Case  X+Nope`)
    spans = getattr(stmt, "arg_spans", None)
    if spans and i < len(spans) and spans[i] is not None:
        start, end = spans[i]
        return stmt.text[start:end]
    arg = stmt.args[i]
    return arg.name if isinstance(arg, S.Name) else ""



def _call_arg_text(text: str, index: int) -> str:
    """Source text of top-level argument `index` of a call statement (`S.Call` keeps no spans)."""
    depth, quote, start, n = 0, False, None, 0
    for pos, ch in enumerate(text):
        if ch == '"':
            quote = not quote
        elif quote:
            continue
        elif ch == "(":
            depth += 1
            if depth == 1 and start is None:
                start = pos + 1
        elif ch == ")" or ch == ",":
            if ch == ")":
                depth -= 1
            if (ch == "," and depth == 1) or depth == 0:
                if n == index:
                    return text[start:pos].lstrip()
                n, start = n + 1, pos + 1
    return ""


_IF_COND_RE = re.compile(r"(?is)^\s*(?:Else)?If\s(.*?)\bThen\b")


def _if_condition_text(stmt: S.Stmt) -> str | None:
    """The echo the real compilers use for an `If` condition: the text between `If ` and `Then`,
    trailing space included (oracle: `If X > 1 And Nope Then` -> `X > 1 And Nope .`)."""
    m = _IF_COND_RE.match(stmt.text or "")
    if m is None and isinstance(stmt, S.If) and stmt.inline is None:
        # no `Then`: the condition runs to the end of the statement (oracle, 2026-10-08)
        m = re.match(r"(?is)^\s*If\s(.*)$", stmt.text or "")
    return m.group(1) if m else None

_IF_NO_THEN_RE = re.compile(r"(?is)^\s*If\s([^'_]*)$")


def _if_condition_text_no_then(stmt: S.Stmt) -> str | None:
    """`If Nope = True` with no `Then` (accepted when the names resolve): the echo is the rest of
    the line (oracle, all four loggers, 2026-10-08). Lines with a comment or continuation are left
    alone."""
    text = stmt.text or ""
    if re.search(r"(?i)\bThen\b", text):
        return None
    m = _IF_NO_THEN_RE.match(text)
    return m.group(1) if m else None


# Instruction names resolve in no expression position (`X = Battery(X)`, `If Delay(1) Then`,
# `Battery`, argument positions) except the ones that are also functions (`Randomize`, which is in
# `profile.functions` anyway) and the block keywords, whose real message is `Incomplete expression`
# (oracle, all four loggers, 2026-10-08: every instruction name probed, `docs/checker.md`).
_CONTROL_KEYWORDS = frozenset({"CASE", "DO", "FOR", "FUNCTION", "IF", "SCAN", "SELECT", "SUB",
                               "SUBSCAN", "WHILE"})


def _known_names(program: S.Program, profile) -> set[str]:
    """Every name a checked expression position may legally reference: this program's own
    declarations (`symbols.declared_names`), this logger's predefined symbols/functions/
    instruction keywords (`profile.*`), its always-or-conditionally-valid system table names
    (`symbols.system_table_names`), the two floating-point special values found this session to
    resolve outside the extracted symbol table entirely (`_EXTRA_PREDEFINED_SYMBOLS`), and, when
    the program declares `ESSVariables`, whichever `ess`-prefixed names it actually references
    (`symbols.ess_variable_names` -- round 5, `docs/checker.md`).
    """
    resolvable = (
        set(profile.symbols)
        | set(profile.functions)
        | (set(profile.instructions) & _CONTROL_KEYWORDS)
        | symbols.system_table_names(program)
        | symbols.ess_variable_names(program)
        | _EXTRA_PREDEFINED_SYMBOLS
    )
    known = symbols.declared_names(program) | resolvable
    if any(isinstance(st, S.Instruction) and st.keyword.upper() == "CH400STATUS"
           for st in program.statements):
        known.add("CH400STATUS")  # the instruction names the table `Sample(1, CH400Status, ...)` reads
    known -= _failed_consts(program, known, profile)
    if profile.single_pass:
        known -= _unclosed_structure_variables(program)
        # an `Alias` whose subscript names something undefined is not registered (oracle, CR300/
        # CR350, 2026-10-03: `Alias Q(2,Nope) = NetDir`, then `NetDir` is not defined), nor is one
        # whose source was never declared (`Quad not yet declared so cannot be aliased.`; oracle,
        # CR300, 2026-10-04, `tests/corpus/fuzz/cr300/message_diff/829cfa80394a`). A name that a
        # declaration or another valid `Alias` registers stays known (oracle, 2026-10-04:
        # `Public x` / `Alias Nope = x` and `Alias x = a` / `Alias Nope = a`, then `a`/`x` resolve).
        declared = symbols.declared_variable_names(program)
        failed, valid = set(), set(declared)
        for st in program.statements:
            if not (isinstance(st, S.Instruction) and st.keyword.upper() == "ALIAS"
                    and len(st.args) == 2 and isinstance(st.args[1], S.Name)
                    and isinstance(st.args[0], (S.Name, S.Index))):
                continue
            src = st.args[0]
            bad = src.name.upper() not in declared or (
                isinstance(src, S.Index) and any(
                    _undefined_names(a, known) for a in src.args if a is not None))
            if not bad and src.name.upper() in declared:
                # `Alias is illegally dimensioned.` (empty `Q()`, a parenthesised index item
                # `Q((1+2))`) registers no name; the out-of-range
                # `Illegally dimensioned aliased name` one does (oracle, CR300/CR350, 2026-10-12)
                from . import structure
                open_at = st.text.find("(")
                items = structure._group_items(st.text, open_at) if open_at >= 0 else None
                bad = (isinstance(src, S.Index) and not src.args) or bool(
                    items and isinstance(src, S.Index) and any(
                        i.strip().startswith("(") and i.strip().endswith(")") for i in items))
            (failed if bad else valid).add(st.args[1].name.upper())
        known -= failed - valid
    return KnownNames(known, tables=_value_less_tables(program, profile),
                      dotted_tables=profile.single_pass,
                      out_of_scope=symbols.out_of_scope_nodes(program, resolvable))


def _unclosed_structure_variables(program: S.Program) -> set[str]:
    """CR300/CR350: upper-case names of variables declared `As <T>` where `StructureType T` never
    reaches `EndStructureType`; they are `is not defined.` (oracle 2026-10-03,
    `docs/instructions.md` "Unclosed StructureType")."""
    open_type = None
    unclosed: set[str] = set()
    for st in program.statements:
        if isinstance(st, S.Instruction) and st.keyword.upper() == "STRUCTURETYPE" \
                and st.args and isinstance(st.args[0], S.Name):
            open_type = st.args[0].name.upper()
        elif isinstance(st, S.Instruction) and st.keyword.upper() == "ENDSTRUCTURETYPE":
            open_type = None
    if open_type is not None:
        for st in program.statements:
            if isinstance(st, S.Declaration) and st.kind in ("PUBLIC", "DIM", "STATIC"):
                unclosed.update(v.name.upper() for v in st.vars if (v.type or "") == open_type)
    return unclosed


class KnownNames(set):
    """`_known_names`, plus the `DataTable` names that are known only as table references: a bare
    `T` used as a value is undefined (`X = T`, `Battery(T)`, `Foo(X, T)`; oracle, all four
    loggers, 2026-09-28) while `T.Field` and `CallTable T` resolve."""
    def __init__(self, names=(), tables: frozenset = frozenset(), dotted_tables: bool = False,
                 out_of_scope: frozenset = frozenset()):
        super().__init__(names)
        self.out_of_scope = out_of_scope  # `symbols.out_of_scope_nodes`: ids of other-routine `Dim` uses
        self.tables = tables
        self.dotted_tables = dotted_tables  # CR300/CR350: `Undeclared.member` names a table


def _value_less_tables(program: S.Program, profile=None) -> frozenset:
    tables = {st.args[0].name.upper() for st in program.statements
              if isinstance(st, S.Instruction) and st.keyword.upper() == "DATATABLE"
              and st.args and isinstance(st.args[0], S.Name)}
    # `Status` and `Public` as a bare value are undefined on all four loggers, `Settings` on CR300/
    # CR350 (oracle, 2026-10-08: `X = Status`, `Battery(Public)`; CR6/CR1000X accept `X = Settings`)
    tables |= {"STATUS", "PUBLIC"} | ({"SETTINGS"} if profile is not None and profile.single_pass
                                      else set())
    others = symbols.declared_variable_names(program) | {n.upper() for n in program.consts} | \
        {st.name.upper() for st in program.statements if isinstance(st, S.SubDef)}
    return frozenset(tables - others)


def _not_yet_declared(program: S.Program, profile) -> dict[int, frozenset[str]]:
    """Per top-level `ConstDecl` (by `id`): the names it may not use yet -- itself and every
    `Const` or variable first declared on a later top-level line (`Const J = J`, `Const K = L` /
    `Const L = 2`, `Const K = Y2` / `Public Y2`): `Invalid symbol in expression` /
    `<name> is not defined.` (oracle, all four loggers, 2026-10-18, docs/checker.md "Round 20").
    Names a logger predefines stay resolvable."""
    stmts = program.statements
    variables = symbols.declared_variable_names(program)
    base = set(profile.symbols) | set(profile.functions) | set(profile.instructions)
    seen: set[str] = set()
    out: dict[int, frozenset[str]] = {}
    consts_after: dict[int, set[str]] = {}
    pending: set[str] = set()
    for i in range(len(stmts) - 1, -1, -1):
        if isinstance(stmts[i], S.ConstDecl):
            pending = pending | {stmts[i].name.upper()}
        consts_after[i] = pending
    in_routine = False
    for i, st in enumerate(stmts):
        if isinstance(st, S.SubDef):
            in_routine = True
        elif S.ends_routine(st):
            in_routine = False
        elif in_routine:
            continue  # routine-local names stay invisible at top level, its own Consts keep the old rule
        elif isinstance(st, S.ConstDecl):
            later = (consts_after[i] | (variables - seen)) - seen - base
            out[id(st)] = frozenset(later)
            seen.add(st.name.upper())
        elif isinstance(st, S.Declaration):
            seen.update(v.name.upper() for v in st.vars)
    return out


def _failed_consts(program: S.Program, known: set[str], profile=None) -> set[str]:
    """`Const` names whose value does not resolve, directly or through another failed `Const`:
    the real compilers never declare them, so every later use is itself unresolved (oracle, all
    four loggers: `Const P = Nope` / `Const R = P + 1` / `PortSet(R,1)` reports `Nope`, then `P + 1`
    on CR6 (`P is not defined.` on CR300), then `R`; `crbasic/AcclimaTDRLib.crb`'s `Const
    TDRComPort = U5` on CR1000X, which has no `U5`; `docs/checker.md` "Pass-1 instruction
    arguments")."""
    consts = [s for s in program.statements if isinstance(s, S.ConstDecl) and s.value is not None]
    failed: set[str] = set()
    if profile is not None:
        # a value with a bad built-in call (`4*ATN()`, `4*ATN(1,2)`) is a pass-1 error and the
        # constant is never declared (oracle, CR300/CR350, 2026-10-12)
        failed |= {c.name.upper() for c in consts if _find_arity_problem(c.value, profile)
                   or _find_bare_function_ref(c.value, profile, frozenset())}
    unready = _not_yet_declared(program, profile) if profile is not None else {}
    changed = True
    while changed:
        changed = False
        for c in consts:
            name = c.name.upper()
            if name not in failed and _undefined_names(c.value, known - failed - unready.get(id(c), frozenset())):
                failed.add(name)
                changed = True
    return failed


def _report_statement(stmt: S.Instruction, positions: dict[int, str], known: set[str],
                       variable_names: set[str], profile, diags: Diagnostics,
                       families: str = "all", dim_counts: dict[str, int] | None = None,
                       ds: DimsState | None = None) -> bool:
    """One statement's own undefined-symbol and constant-only-family checks, at every position
    `_position_kinds` marks as routed through the general expression/symbol resolver -- a full
    expression-tree walk (`_undefined_names`), not just a direct bare-`Name` argument (round 4,
    this session: safe now that `_known_names` closes the two gaps -- `NAN`/`Status` -- that made
    this walk reject real programs when first tried in round 3, `docs/checker.md`).

    Both the *order* positions are checked in and the *shape* of what gets reported differ by
    logger family (oracle-confirmed this session, `docs/checker.md` "Round 4" -- see
    `_CONST_FAMILY_KINDS`/`_WHOLE_EXPR_ECHO_LOGGERS` above for the evidence): CR6/CR1000X check
    every constant-only-family position (ascending) before any general-family position regardless
    of textual order; CR300/CR350 check every position in plain ascending textual order, no family
    split. Both stop at the first position with *any* problem (an undefined name, checked at every
    position, or a live-variable reference, checked at const-only-family positions only,
    `_CONST_ONLY_VARIABLE_REJECT_KINDS`) and report only that one -- **within one position**, the
    undefined-name check runs first (the two are mutually exclusive for a single name: a name is
    either undefined or resolves to something, so this ordering only matters for picking which
    message a position with *both* an undefined name and, elsewhere in the same expression, a live
    variable gets -- not evidenced either way this session, so undefined-first was kept as the
    simpler, already-existing rule). Oracle-confirmed cross-family interleaving, not just two
    independent passes (`WriteIO(X,BazQux)`, `X` a live variable at the const-family position 0,
    `BazQux` undefined at the general-family position 1: CR6/CR1000X report `Variable illegal in
    Parameter: X.`, never reaching `BazQux`'s own undefined-name error even though the *undefined-
    name* pass alone, run across both families before any variable-reject check, would reach
    `BazQux` first if the two checks were separate top-level passes over the statement -- they are
    not; `PRT(GenBad,X,3,3,3)` on CR300, `GenBad` undefined at the general-family position 0 (the
    textually-first position there), `X` a live variable at the const-family position 1: CR300
    reports only `GenBad is not defined.`, never reaching `X` at all, confirming CR300/CR350 have
    no family reordering for this combined check either). Neither family is known to check past the
    first bad position in the same statement -- under-reporting a second, independent bad position
    in the same call is the deliberate, safe side of that gap (AGENTS.md: never reject an accepted
    program), not a claim that the real compiler is silent about it. The variable-reject message
    itself (`Variable illegal in Parameter: %s.`) is oracle-confirmed uniform across all four
    loggers (`docs/checker.md`), so no per-logger branch for its own text.
    """
    def _check_position(i: int) -> bool:
        """Checks position `i`, reports at most one diagnostic (CR6/CR1000X) or up to two
        (CR300/CR350, see below), returns whether it reported anything.

        **CR300/CR350 do not share CR6/CR1000X's mutual exclusivity** between the undefined-name
        check and the const-only-family "rejects a variable" check, oracle-confirmed this round
        (`docs/loggers.md`): at a const-family position (`_CONST_ONLY_VARIABLE_REJECT_KINDS`), an
        *undefined* name gets **both** its own undefined-name line(s) *and* a `Variable illegal in
        Parameter: <whole arg text>.` line right after -- `TCDiff(Dest,1,BazQux,...)` on
        `cr300comp.exe` -> `BazQux is not defined.` then `Variable illegal in Parameter: BazQux.`,
        two lines, where CR6/CR1000X give only `Invalid symbol in expression: BazQux.` (one line,
        the existing rule, unchanged). A compound expression mixing an undefined name with a live
        variable (`TCDiff(Dest,1,X+BazQux,...)`, `X` declared) gives `BazQux is not defined.` (the
        undefined name only, per CR300/CR350's existing per-name echo) then `Variable illegal in
        Parameter: X+BazQux.` (the whole expression, since it is not a foldable constant either
        way) -- i.e. the second check's trigger is "argument contains any non-constant name
        reference at all" (undefined *or* a declared variable), not "is a declared variable"
        alone, and always echoes the whole argument once. Modelled below as one unified
        computation (`trigger = names or var_refs`) that happens to reduce to the pre-existing
        CR6/CR1000X mutual-exclusivity behaviour automatically, since `names` is always empty by
        the time CR6/CR1000X (`_WHOLE_EXPR_ECHO_LOGGERS`) reach the second check (a non-empty
        `names` already returned above, for that family only).
        """
        if i >= len(stmt.args) or stmt.args[i] is None:
            return False
        arg = stmt.args[i]
        names = _undefined_names(arg, known)
        whole_echo = profile.name in _WHOLE_EXPR_ECHO_LOGGERS
        dims = False
        if names and not whole_echo:
            names, dims = _names_and_dims(known, ds, stmt, tuple(stmt.args[:i]), arg)
        # CR300/CR350: an undefined name at a code-20 keyword slot is also `Variable illegal`
        # (oracle, 2026-10-01: `PanelTemp(X,Nope)`).
        is_const_family = positions[i] in _CONST_ONLY_VARIABLE_REJECT_KINDS or (
            bool(names) and not whole_echo and positions[i] == "datatype_kw") or (
            # CR6/CR1000X `fN1` slot: a variable there is `Variable illegal in Parameter`
            # (oracle, 2026-10-02, `docs/instructions.md` "fN1 / fnotch").
            whole_echo and positions[i] == "datatype_kw" and
            profile.fnotch.get("instructions", {}).get(stmt.keyword.upper()) == i + 1)

        if names:
            if whole_echo:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=_arg_text(stmt, i)),
                            stmt.file)
                return True
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
            if dims:
                # CR300/CR350: `SplitStr(ResultString(1),SearchString,...)` -> `Dimensions not
                # declared: ResultString(1).` and the rest of the list collapses into `Too many
                # parameters:  SearchString.`
                diags.error(stmt.line, dimensions_message(_arg_text(stmt, i).strip(), profile, False),
                            stmt.file)
                if is_const_family:  # (oracle, CR300, 2026-10-03: `Delay(Nope(1),2,Sec)`)
                    diags.error(stmt.line, f"Variable illegal in Parameter: {_arg_text(stmt, i)}.",
                                stmt.file, stmt=stmt)
                    return True
                if i + 1 < len(stmt.args) and stmt.args[i + 1] is not None:
                    from .instr_rules import _next_simple_token
                    token = _next_simple_token(_arg_text(stmt, i + 1))
                    diags.error(stmt.line, f"Too many parameters:  {token}.", stmt.file)
                return True
            if not is_const_family:
                return True
            # CR300/CR350 const-family position: fall through to also run the
            # "Variable illegal in Parameter" check below, independent of the undefined-name
            # report just made (oracle-confirmed; see this method's own docstring).

        if dim_counts and _over_indexed(arg, dim_counts, _dim_limit(profile)):
            # CR300/CR350: `Battery(R(2,1))` for `Public R(5)` (oracle, 2026-09-28); the argument
            # list ends there (`SplitStr(S(1,2),T,...)` -> `Too many parameters:  T.`, 2026-10-01).
            # CR6/CR1000X: past three subscripts, no follow-up (`Delay(1,B(1,1,1,1),mSec)`, round 16)
            text = _arg_text(stmt, i)
            diags.error(stmt.line, dimensions_message(
                text if stmt.keyword.upper() == "SELECT" else text.strip(), profile, False),
                        stmt.file)
            if not whole_echo and i + 1 < len(stmt.args) and stmt.args[i + 1] is not None:
                from .instr_rules import _next_simple_token
                token = _next_simple_token(_arg_text(stmt, i + 1)) or _arg_text(stmt, i + 1)
                diags.error(stmt.line, f"Too many parameters:  {token}.", stmt.file)
            return True

        if is_const_family:
            var_refs = _variable_refs(arg, variable_names)
            if names or var_refs:
                diags.error(stmt.line, f"Variable illegal in Parameter: {_arg_text(stmt, i)}.", stmt.file,
                            stmt=stmt)
                return True

        return bool(names)

    multi = _MULTI_REPORT_ORDER.get(stmt.keyword.upper())
    if multi is not None and profile.name in _WHOLE_EXPR_ECHO_LOGGERS and families == "general":
        reported = False
        for i in multi:
            if i in positions and _check_position(i):
                reported = True
        return reported
    if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
        order = []
        if families in ("all", "const"):
            order += sorted(i for i, k in positions.items() if k in _CONST_FAMILY_KINDS)
        if families in ("all", "general"):
            order += sorted(i for i, k in positions.items() if k not in _CONST_FAMILY_KINDS)
    else:
        order = sorted(positions)

    for i in order:
        if _check_position(i):
            return True
    return False


def _member_index_text(args: list | None) -> str:
    """The `(N)` echoed for an absent DataTable member: 1 for no index, the truncated literal, and
    nothing for a variable, expression or negative index (oracle, CR300, docs/instructions.md)."""
    if not args or args[0] is None:
        return "(1)"
    index = args[0]
    if isinstance(index, S.Number) and index.value >= 0:
        return f"({int(index.value)})"
    return ""


def _table_member_problem(expr: S.Expr | None, const_tables: dict[str, set[str]],
                          data_tables: set[str], absent: frozenset[str] = frozenset()
                          ) -> tuple[str, str, str] | None:
    if expr is None:
        return None
    if isinstance(expr, S.Member):
        if isinstance(expr.base, S.Name):
            table = expr.base.name.upper()
            member = expr.member.upper()
            if table in const_tables and member not in const_tables[table]:
                return expr.base.name, expr.member, "(1)"
            if table in data_tables and member == "TABLEBYTESX":
                return expr.base.name, expr.member, "(1)"
            if table in data_tables and f"{table}.{member}" in absent:
                return expr.base.name, expr.member, _member_index_text(expr.args)
        for arg in expr.args or []:
            found = _table_member_problem(arg, const_tables, data_tables, absent)
            if found:
                return found
        return _table_member_problem(expr.base, const_tables, data_tables, absent)
    if isinstance(expr, S.Index):
        for arg in expr.args:
            found = _table_member_problem(arg, const_tables, data_tables, absent)
            if found:
                return found
    elif isinstance(expr, S.Unary):
        return _table_member_problem(expr.operand, const_tables, data_tables, absent)
    elif isinstance(expr, S.Binary):
        return _table_member_problem(expr.left, const_tables, data_tables, absent) or \
            _table_member_problem(expr.right, const_tables, data_tables, absent)
    elif isinstance(expr, S.ArrayLiteral):
        for item in expr.items:
            found = _table_member_problem(item, const_tables, data_tables, absent)
            if found:
                return found
    return None


def check_table_members(program: S.Program, profile, diags: Diagnostics) -> None:
    """Resolved ConstTable fields, plus the observed misspelled `TableBytesX` DataTable member.

    The complete DataTable output-field naming grammar is deliberately not inferred here.
    """
    const_tables = symbols.const_table_fields(program)
    data_tables = {
        stmt.args[0].name.upper() for stmt in program.statements
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "DATATABLE" and stmt.args
        and isinstance(stmt.args[0], S.Name)
    }
    if not const_tables and not data_tables:
        return
    renamed: set[str] = set()  # tables with a FieldNames entry exactly BytesPerRecord
    table = None
    for st in program.statements:
        if not isinstance(st, S.Instruction):
            continue
        kw = st.keyword.upper()
        if kw == "DATATABLE":
            table = st.args[0].name.upper() if st.args and isinstance(st.args[0], S.Name) else None
        elif kw == "ENDTABLE":
            table = None
        elif kw == "FIELDNAMES" and table and any(
                isinstance(a, S.String) and "BYTESPERRECORD" in
                (f.strip().upper() for f in a.value.split(",")) for a in st.args):
            renamed.add(table)
    absent = frozenset(f"{t}.BYTESPERRECORD" for t in data_tables - renamed) if profile.single_pass \
        else frozenset()
    for stmt in program.statements:
        exprs: list[S.Expr | None] = []
        if isinstance(stmt, S.Assignment):
            exprs = [stmt.target, stmt.value]
        elif isinstance(stmt, S.If):
            exprs = [stmt.cond]
        elif isinstance(stmt, S.Instruction):
            exprs = stmt.args
        elif isinstance(stmt, S.Call):
            exprs = stmt.args
        for expr in exprs:
            found = _table_member_problem(expr, const_tables, data_tables, absent)
            if found:
                table, member, index = found
                diags.error(stmt.line, f"Could not find {table}.{member}{index}.", stmt.file)
                break


def _dotted_members(expr: S.Expr | None) -> list[tuple[str, S.Member | None]]:
    """Every `Name.member` access (and `Name.` with a trailing dot, member `None`) in `expr`, in
    source order (`X.5`, `T.1(2)`, `Status.1`, `X.dat`, `T.`)."""
    if expr is None:
        return []
    if isinstance(expr, S.Name):
        return [(expr.name[:-1], None)] if expr.name.endswith(".") and len(expr.name) > 1 \
            and (expr.name[0].isalpha() or expr.name[0] == "_") else []
    if isinstance(expr, S.Member):
        out = _dotted_members(expr.base)
        if isinstance(expr.base, S.Name) and not expr.base.name.endswith("."):
            out.append((expr.base.name, expr))
        for a in expr.args or []:
            out += _dotted_members(a)
        return out
    if isinstance(expr, S.Index):
        return [m for a in expr.args for m in _dotted_members(a)]
    if isinstance(expr, S.Unary):
        return _dotted_members(expr.operand)
    if isinstance(expr, S.Binary):
        return _dotted_members(expr.left) + _dotted_members(expr.right)
    if isinstance(expr, S.ArrayLiteral):
        return [m for item in expr.items for m in _dotted_members(item)]
    return []


_TABLE_FIELDS = frozenset({"TIMESTAMP", "RECORD", "RECNUM"})


def _unknown_status_member(m, profile) -> bool:
    return not m.member[:1].isdigit() and bool(profile.status_members) and \
        m.member.upper() not in profile.status_members


def _unknown_public_member(m, variables: set[str]) -> bool:
    return not m.member[:1].isdigit() and m.member.upper() not in variables | _TABLE_FIELDS


def check_status_member_reads_late(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR300/CR350: an unknown `Status.X` / `Public.X` read (see `check_digit_members`) is reported
    only in an otherwise clean file (oracle, 2026-10-12: a later `WatchdogTimer is not defined.`
    hides `Status.CardBytesFree`); run from the late stage."""
    if not profile.single_pass:
        return
    variables = {n for n in symbols.declared_variable_names(program)
                 if not symbols.is_reserved_predefined_name(n, profile)}
    for stmt in program.statements:
        if isinstance(stmt, S.Assignment):
            exprs, target = [stmt.target, stmt.value], len(_dotted_members(stmt.target))
        elif isinstance(stmt, S.If):
            exprs, target = [stmt.cond], 0
        elif isinstance(stmt, (S.Instruction, S.Call)):
            exprs, target = stmt.args, 0
        else:
            continue
        for k, (base, m) in enumerate(mm for e in exprs for mm in _dotted_members(e)):
            if m is None or k < target:
                continue
            if base.upper() == "STATUS" and _unknown_status_member(m, profile) or \
                    base.upper() == "PUBLIC" and _unknown_public_member(m, variables):
                diags.error(stmt.line, f"Could not find {base}.{m.member}{_member_index_text(m.args)}.",
                            stmt.file)


def check_digit_members(program: S.Program, profile, diags: Diagnostics) -> None:
    """A member of a declared name that no variable, data table or `Status` has (oracle, all four
    loggers, 2026-10-12, `docs/checker.md` "Digit-led members", "Letter-led members"):

    - `ident.digits` (`X = X.5`, `A.1`, `T.1`, `Status.1`): `Could not find X.` for a declared
      variable (CR300/CR350 `Could not find DataTable X.`), `Could not find T.1(1).` for a data
      table or `Status` (the index echoed as `_member_index_text`), one line per access in source
      order, in assignment values, subscripts, `If` conditions, `For` headers and function
      arguments; an assignment target (`X.5 = 1`, `T.1 = 1`) adds `Invalid assignment in
      expression: <line>.`.
    - a letter-led member of a plain declared name (`X.Y`, `A.Y`, `S.Y`, `D.Y`, `K.Y`, an alias, a
      pointer, a routine parameter, a function name) is `Could not find X.` as well (structure-typed
      names and data tables / `Status` keep their members: field existence is not modelled).
    - a trailing dot (`T.`, `Status.`) is a valid operand for a data table or `Status`; any other
      name (`X.`, `Zork.`) is `Could not find X.` (CR300/CR350 `Could not find DataTable X.`).

    Statements that also name an undefined symbol and structure-typed bases are not touched."""
    from . import structs
    variables = {n for n in symbols.declared_variable_names(program)
                 if not symbols.is_reserved_predefined_name(n, profile)}
    struct_types = structs._structure_members(program)
    kinds = structs._variable_kinds(program, struct_types)
    variables = {n for n in variables if not str(kinds.get(n, "")).startswith("STRUCT")}
    # (a `Sub S(C As Pt)` parameter keeps its members too; oracle, CR6/CR1000X, 2026-10-12)
    variables -= {p.name.upper() for st in program.statements if isinstance(st, S.SubDef)
                  for p in st.params if p.type and p.type.upper() in struct_types}
    variables |= {str(n).upper() for n in program.consts}
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "ALIAS" \
                and len(stmt.args) == 2 and isinstance(stmt.args[1], S.Name):
            variables.add(stmt.args[1].name.upper())  # (`Alias Xal = X`: `Xal.Y` is `Could not find Xal.`)
        elif isinstance(stmt, S.SubDef) and stmt.keyword.upper() == "FUNCTION":
            variables.add(stmt.name.upper())
    data_tables = {
        stmt.args[0].name.upper() for stmt in program.statements
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "DATATABLE" and stmt.args
        and isinstance(stmt.args[0], S.Name)
    } | {"STATUS"}
    known = _known_names(program, profile)
    for stmt in program.statements:
        if isinstance(stmt, S.Assignment):
            exprs = [stmt.target, stmt.value]
        elif isinstance(stmt, S.If):
            exprs = [stmt.cond]
        elif isinstance(stmt, S.For):
            exprs = [stmt.var, stmt.start, stmt.end, stmt.step]
        elif isinstance(stmt, (S.Instruction, S.Call)):
            exprs = stmt.args
        else:
            continue
        members = [m for e in exprs for m in _dotted_members(e)]
        if not members or any(_undefined_names(e, known) for e in exprs):
            continue
        lines = []
        target = len(_dotted_members(stmt.target)) if isinstance(stmt, S.Assignment) else 0
        for k, (base, m) in enumerate(members):
            if k == target and lines:
                break   # a bad member target ends the statement (`X.Q = X.R + 1`: one line)
            upper = base.upper()
            if m is None:
                if upper not in data_tables:
                    lines.append(f"Could not find DataTable {base}." if profile.single_pass
                                 else f"Could not find {base}.")
            elif upper == "STATUS" and k >= target and not profile.single_pass and \
                    _unknown_status_member(m, profile):
                # an unknown Status field read (`Y = Status.Nope`, `If Status.Nope`): oracle, all
                # four loggers, 2026-10-12; the names: data/**/status_members.json
                lines.append(f"Could not find Status.{m.member}{_member_index_text(m.args)}.")
            elif upper in data_tables:
                if m.member[:1].isdigit():
                    lines.append(f"Could not find {base}.{m.member}{_member_index_text(m.args)}.")
            elif upper == "PUBLIC" and k >= target and not profile.single_pass and \
                    _unknown_public_member(m, variables):
                # `Y = Public.Nope(2)` -> `Could not find Public.Nope(2).` (oracle, all four, 2026-10-12)
                lines.append(f"Could not find Public.{m.member}{_member_index_text(m.args)}.")
            elif upper in variables:
                lines.append(f"Could not find DataTable {base}." if profile.single_pass
                             else f"Could not find {base}.")
        if not lines:
            continue
        for line in lines:
            diags.error(stmt.line, line, stmt.file)
        if isinstance(stmt, S.Assignment) and any(m for _, m in _dotted_members(stmt.target)):
            sep = "\n" if profile.fallback_echo_newline and stmt.at_eol else ""
            diags.error(stmt.line, f"Invalid assignment in expression: {stmt.text}{sep}.", stmt.file)


def check_const_family_args(program: S.Program, profile, diags: Diagnostics) -> None:
    """CR6/CR1000X pass 1: the constant-family instruction positions (`_CONST_FAMILY_KINDS`) are
    folded while the statement is parsed, so an unresolved name there (`Invalid symbol in
    expression: %s.`) or a live variable (`Variable illegal in Parameter: %s.`) is reported next
    to other pass-1 errors, while general positions stay pass 2 (`check`). Oracle, CR6, with a
    pass-1 `Dim Z banana` in the file: `PortSet(X,1)`, `PanelTemp(X, Nope)`, `VoltSe(X,1,Nope,...)`
    and `SDI12Recorder(X, Nope, ...)` are all still reported; `VoltSe(Nope,...)`, `Randomize(Nope)`
    and `Delay(0,Nope,mSec)` are not (`docs/checker.md` "Pass-1 instruction arguments")."""
    if profile.name not in _WHOLE_EXPR_ECHO_LOGGERS:
        return
    known = _known_names(program, profile)
    variable_names = {name for name in symbols.declared_variable_names(program)
                      if not symbols.is_reserved_predefined_name(name, profile)}
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction):
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        if any(_find_arity_problem(a, profile) or
               _find_bare_function_ref(a, profile, frozenset(variable_names))
               for a in stmt.args if a is not None):
            continue  # a parse error inside an argument owns the statement (`check_function_arity`)
        positions = _position_kinds(record["params"], stmt.keyword, profile)
        if positions:
            _report_statement(stmt, positions, known, variable_names, profile, diags,
                              families="const")


def check(program: S.Program, profile, diags: Diagnostics, env: consteval.Env,
          skip: frozenset = frozenset()) -> None:
    """Undefined-symbol checking at every `Instruction` argument position whose declared type code
    routes through the real expression/named-constant resolver (`_position_kinds`) -- a full
    expression-tree walk (`_undefined_names`), not restricted to a direct bare `Name` argument
    (round 4, this session; rules and evidence: `_report_statement` above, `docs/checker.md`).

    Still deliberately narrow in scope, unchanged from round 3: only positions
    `_RESOLVED_KINDS` names (never an identifier/raw-text-kind position such as a `DataTable`/
    `ConstTable` name, `Member.member` text, or a `Sub`/`Function` argument list's own runtime
    scoping); `symbols.declared_names` stays whole-program and unscoped rather than tracking which
    names are visible inside which `Sub`/`Function` (both documented limitations, `docs/checker.md`
    "Not implemented" -- tightening either risks a false reject this session did not have the
    oracle evidence to rule out, so neither was attempted here).

    `env` is accepted (unused) to keep this call site's signature stable for a future session
    that wires in the variable-required type-code check (still not implemented -- see the
    docstring above `_VARIABLE_REQUIRED_KINDS`; the const-only-family "rejects a variable" check
    is implemented, `_CONST_ONLY_VARIABLE_REJECT_KINDS`).
    """
    known = _known_names(program, profile)
    variable_names = symbols.declared_variable_names(program)
    ds = program_dims(program, profile, known)
    dim_counts = symbols.declared_dim_counts(
        program, strings=True)

    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or id(stmt) in skip:
            continue
        record = profile.instructions.get(stmt.keyword.upper())
        if not record:
            continue
        if stmt.keyword.upper() == "SLOWSEQUENCE" and profile.name in ("cr6", "cr1000x"):
            continue  # `SlowSequence junk` compiles: its argument is never resolved (oracle, 2026-10-10)
        positions = _position_kinds(record["params"], stmt.keyword, profile)
        if (positions and stmt.keyword.upper() == "SERIALOPEN" and stmt.args and stmt.args[0]
                and profile.name in _WHOLE_EXPR_ECHO_LOGGERS
                and _undefined_names(stmt.args[0], known)):
            continue  # `ComPort 0 illegal.` replaces every `Invalid symbol` (`cr3xx.check`)
        if positions:
            # CR6/CR1000X: constant-family positions are pass 1 (`check_const_family_args`).
            reported = len(diags.items)
            _report_statement(stmt, positions, known, variable_names, profile, diags,
                              families="general", dim_counts=dim_counts, ds=ds)
            if stmt.keyword.upper() == "MATRIX" and profile.name in ("cr6", "cr1000x") and \
                    len(diags.items) > reported and all(
                        d.message.startswith("Invalid symbol in expression: ")
                        for d in diags.items[reported:]):
                from . import instr_rules
                rewritten = instr_rules.matrix_undefined_operands(stmt, program, env, known, profile)
                if rewritten is not None:
                    del diags.items[reported:]
                    for message in rewritten:
                        diags.error(stmt.line, message, stmt.file)

            # `_VARIABLE_REQUIRED_KINDS` (`Invalid Variable or Array: %s.`) is intentionally not
            # applied here despite oracle evidence that it is a real rule for *some* instructions
            # (`Battery(5)`, `Erase(5)`, `ReadIO(5,1)`, all code 8/27/29 on CR6, all oracle-
            # confirmed rejected). Found as a **regression** during round 3's own testing against
            # the full corpus on all four loggers: `Move`'s own third argument (code 41) accepts a
            # literal `0` (`embracer.cr6`), and `ModBusClient`'s own last, optional argument (also
            # code 27, the *same* code that correctly rejects a literal for `Erase`) also accepts a
            # literal `2` (`modbusclient_2.CR6`) -- both on CR1000X. So the same type code does not
            # behave uniformly across every instruction that uses it; at least `Move` and
            # `ModBusClient` override it (most likely their own `compile_fn`, similar to the
            # const-only kinds' `Sample`/`DataType` exception, `docs/checker.md`). Still not
            # implemented this session either: see `docs/checker.md` "Constant-only and
            # variable-required type codes" for the full evidence and what a future session would
            # need to verify first.

    # An `Assignment`'s own RHS (`x = <value>`): not an `Instruction` at all, so untouched by the
    # loop above -- a previously-unaddressed gap on *every* logger, found via this round's own
    # CR300/CR350 repo-mismatch chase (`WDogTimer = WatchdogTimer(0,Sec)`, `WatchdogTimer` a real
    # CR6/CR1000X function absent from CR300/CR350 entirely, `docs/loggers.md`). Oracle-confirmed
    # pass 2, same gating as the rest of this function (`BazQux(0,Sec)` in place of `WatchdogTimer`
    # on CR6: alone, `line N: Invalid symbol in expression: <assignment's own text>.`; preceded by
    # an unrelated pass-1 `Variable not named.`, suppressed entirely -- `docs/loggers.md`). Two
    # differences from the instruction-argument case above, both oracle-confirmed:
    #
    # 1. The whole-expression echo (CR6/CR1000X) is the **entire assignment statement's own
    #    text**, target and `=` included (`stmt.text`, already the raw segment text with its
    #    original indentation -- the same field `structure.py`'s "Expr" fallback echoes), not just
    #    the value sub-expression (unlike a `Const`'s `value_span`, there is no separate span to
    #    prefer here since the oracle's own echo includes the target).
    # 2. CR300/CR350 additionally emit a `Dimensions not declared: <text>` follow-up line after
    #    their own per-name `<name> is not defined.` line(s) -- not reproduced here (no oracle
    #    evidence yet for its exact trigger/shape beyond this one example, and it is a second,
    #    distinct message rather than a difference in this one's own text); the per-name line(s)
    #    alone already turn the previously-silent false accept into a correct reject.
    #
    # **Round 5 addition, oracle-confirmed (`docs/checker.md` "Round 5")**: an assignment whose own
    # *target* itself does not resolve (`FuzzVar = 1`, `FuzzVar` never declared) is a completely
    # different message family -- the generic "Expr" fallback `structure.py`'s `check_pass2` already
    # owns for `Call`/`Unparsed` (`Invalid, or out of place expression: <whole statement>.`, plus a
    # before-`BeginProg` placement follow-up) -- never this module's own `Invalid symbol in
    # expression: %s.`, even when the RHS is *also* undefined (`FuzzVar1 = FuzzVar2` ->
    # `Invalid, or out of place expression: FuzzVar1 = FuzzVar2.` alone, no second, separate
    # "Invalid symbol" line for `FuzzVar2`). `_undefined_names` doubles as the target-resolution
    # test here (an `Index`'s own base name, or a `Member`'s base, exactly like a normal read
    # reference -- oracle-confirmed for an undeclared array base, `X(BadIdx) = 1` with `X`
    # undeclared, and for an undeclared *index* on an otherwise-declared array, `X(BadIdx) = 1`
    # with `X` declared and `BadIdx` not -- both land in the same "Invalid, or out of place
    # expression" bucket, `structure.py`'s job, not checked again here). Skipping these statements
    # entirely (rather than still checking the RHS) is what reproduces the oracle's "one message,
    # not two" behaviour without `structure.py` needing to know this module's own diagnostics.
    # An `If` condition (block or one-line) with an unresolved name, CR6/CR1000X, pass 2 (oracle,
    # 2026-09-27): `If Nope Then` -> `Invalid symbol in expression: Nope .`, `If Foo(X) > 20 Then
    # X = 1` -> `... Foo(X) > 20 .`; suppressed by a pass-1 error. CR300/CR350 print per-name
    # `%s is not defined.` lines instead (not modelled).
    for stmt in program.statements:
        if not isinstance(stmt, S.If):
            continue
        names = _undefined_names(stmt.cond, known)
        text = _if_condition_text(stmt)
        if text is None:
            text = _if_condition_text_no_then(stmt)
        if text is not None and not names and \
                _over_indexed(stmt.cond, symbols.declared_dim_counts(program, strings=True),
                              _dim_limit(profile)):
            # CR300/CR350: `If Q(1) Then` for a scalar `Q` (or `Public Q()`), `If Z(1,1) = 1 Then`
            # for `Public Z(3)` -> `Dimensions not declared: Q(1) .` (oracle, 2026-10-10)
            diags.error(stmt.line, dimensions_message(text, profile, False), stmt.file)
            continue
        if not names or text is None:
            continue
        names, dims = _names_and_dims(known, ds, stmt, (), stmt.cond)
        before = len(diags.items)
        if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, f"Invalid symbol in expression: {text}.", stmt.file)
        else:
            # CR300/CR350: `If Foo(X) > 20 Then` -> `Foo is not defined.`, `Dimensions not
            # declared: Foo(X) > 20 .`; `If Nope Then` -> `Nope is not defined.` (oracle)
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
            if dims:
                diags.error(stmt.line, dimensions_message(text, profile, False), stmt.file)
        if stmt.inline is not None:
            # the condition precedes the inline statements' own findings on the same line
            # (oracle, all four loggers, 2026-10-08)
            at = next((i for i, d in enumerate(diags.items[:before])
                       if (d.line, d.file) == (stmt.line, stmt.file)), None)
            if at is not None:
                moved = diags.items[before:]
                del diags.items[before:]
                diags.items[at:at] = moved

    # `Do While Nope` / `Loop Until (Nope <> 1)`: the condition after `While`/`Until`, like an
    # `If` condition (oracle, 2026-09-28: CR6 `Invalid symbol in expression: (Nope <> 1).`,
    # CR300 `Nope is not defined.`).
    open_do = 0
    for stmt in program.statements:
        if isinstance(stmt, S.DoLoop):
            if stmt.keyword.upper() == "DO":
                open_do += 1
            elif open_do:
                open_do -= 1
            elif profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
                continue  # `Loop Until Nope` with no `Do`: only `LOOP without DO.` (oracle, 2026-10-10)
        if not isinstance(stmt, S.DoLoop) or stmt.cond is None:
            continue
        names = _undefined_names(stmt.cond, known)
        m = re.search(r"(?i)\b(?:While|Until)\b\s*(.*)$", stmt.text or "")
        if m and not names and \
                _over_indexed(stmt.cond, symbols.declared_dim_counts(program, strings=True),
                              _dim_limit(profile)):
            diags.error(stmt.line, dimensions_message(m.group(1), profile, False), stmt.file)
            continue
        if not names or not m:
            continue
        if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, f"Invalid symbol in expression: {m.group(1)}.", stmt.file)
        else:
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)

    _check_for_headers(program, profile, diags, known)
    _check_elseif_and_case(program, profile, diags, known)
    _check_function_statements(program, profile, diags, known)

    dim_counts = symbols.declared_dim_counts(
        program, strings=True)
    struct_vars = _struct_var_names(program)
    functions = {st.name.upper() for st in program.statements
                 if isinstance(st, S.SubDef) and st.keyword.upper() == "FUNCTION"}
    for stmt in program.statements:
        if (isinstance(stmt, S.Call) and not stmt.explicit and stmt.name.upper() in functions
                and stmt.tail_expr is None and stmt.juxtaposed is None
                and not any(_undefined_names(a, known) for a in stmt.args)
                and any(_over_indexed(a, dim_counts, _dim_limit(profile)) for a in stmt.args)):
            # CR300/CR350: `Fn(X(1,2))` is checked like `Y = Fn(X(1,2))` (oracle, 2026-10-10)
            diags.error(stmt.line, dimensions_message(stmt.text, profile, True, stmt.at_eol),
                        stmt.file)
        if (isinstance(stmt, S.Call) and stmt.tail_expr is not None
                and not _undefined_names(stmt.tail_expr, known)
                and _over_indexed(stmt.tail_expr, dim_counts, _dim_limit(profile))):
            # CR300/CR350: `Fn(1)+X(1,2)` is checked like `Y = Fn(1)+X(1,2)` (oracle, 2026-10-10)
            diags.error(stmt.line, dimensions_message(stmt.text, profile, True, stmt.at_eol),
                        stmt.file)
        if not isinstance(stmt, S.Assignment):
            continue
        if _undefined_names(stmt.target, known):
            continue  # target itself unresolved: `structure.check_pass2`'s job entirely, above.
        if _multi_indexed_struct(stmt.target, struct_vars) or _multi_indexed_struct(stmt.value, struct_vars):
            continue  # already reported by `check_function_arity` (pass 1)
        if profile.single_pass and _function_target_message(stmt, profile, frozenset()):
            continue  # a function-named target (even a local scalar `exp(1) = 2`): same
        names = _undefined_names(stmt.value, known)
        whole_echo = profile.name in _WHOLE_EXPR_ECHO_LOGGERS
        if _over_indexed(stmt.target, dim_counts, 3 if whole_echo else None):
            # `R(2,1) = 1` for `Public R(5)`: the fallback (CR6/CR1000X only past 3 subscripts)
            from . import structure
            diags.error(stmt.line, structure._out_of_place(profile, stmt.text, stmt.at_eol),
                        stmt.file, show_file="cr3xx")
            continue
        if not names:
            if _over_indexed(stmt.value, dim_counts, _dim_limit(profile)):
                # CR300/CR350: `X = R(2,1)` -> `Dimensions not declared:     X = R(2,1)` (oracle);
                # CR6/CR1000X past three subscripts, round 16
                diags.error(stmt.line, dimensions_message(stmt.text, profile, True, stmt.at_eol),
                            stmt.file)
            continue
        if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=stmt.text), stmt.file,
                        stmt=stmt)
        else:
            names, dims = _names_and_dims(known, ds, stmt, (stmt.target,), stmt.value)
            if not dims and _over_indexed(stmt.value, dim_counts):
                names = _names_through_over_index(stmt.value, known, dim_counts)
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
            if dims or _over_indexed(stmt.value, dim_counts):   # (names, then the over-indexed read: oracle, CR300, 2026-10-12)
                diags.error(stmt.line, dimensions_message(stmt.text, profile, True, stmt.at_eol),
                            stmt.file)


# the `Const ... As` types the real compilers accept (see `structure._CONST_TYPES`)
_CONST_TYPES = frozenset({"LONG", "FLOAT", "DOUBLE", "STRING", "BOOLEAN"})


def check_consts(program: S.Program, profile, diags: Diagnostics) -> None:
    """`Const <name> = <value>.` initializer resolution (this round's own new check, all four
    loggers): a `Const`'s value expression is folded eagerly, at declaration time, so an
    unresolved name inside it is diagnosed there and then rather than deferred to any later
    argument-checking pass -- oracle-confirmed **pass 1** (ungated, unconditional), not pass 2:
    `Const Foo = BazQux` reports its own diagnostic *alongside* an unrelated, earlier `Public`
    with no name (`Variable not named.`), on every logger, where an ordinary pass-2 finding (e.g.
    `expressions.check`'s own instruction-argument checks) would have been completely suppressed
    by that same earlier pass-1 error (`docs/checker.md` "Pass model"; probe transcript,
    `docs/loggers.md`). Message family and per-logger shape are identical to the general
    undefined-symbol check above, reusing the same `_WHOLE_EXPR_ECHO_LOGGERS` split: CR6/CR1000X
    report one whole-expression `Invalid symbol in expression: <value text>.` line; CR300/CR350
    report each undefined name in the expression as its own `<name> is not defined.` line (oracle,
    a compound expression, `docs/loggers.md`: `Const Foo = BazQux + Corge` -> one line, `Invalid
    symbol in expression: BazQux + Corge.`, on CR6; two lines, `BazQux is not defined.` then
    `Corge is not defined.`, on CR300 -- same shape as an instruction argument's own compound
    expression, `_report_statement` above). `ConstDecl.value_span` (new field, `syntax.py`/
    `parser.py`, same technique as `Instruction.arg_spans`) captures the value expression's own
    source columns so the whole-expression echo is the verbatim text, not a re-derived form.

    Narrow and deliberately conservative, mirroring `check` above: only an outright-undefined
    name is flagged (`_undefined_names` against the same `known` universe, so a `Const` that
    legitimately forward/cross-references another declared name is never flagged); a `Const`
    referencing a *live* runtime variable is a real, different, oracle-confirmed rule of its own
    (`Variable used in CONST expression.`, all loggers, `docs/loggers.md`) but not implemented
    here -- zero occurrences of that message anywhere in this project's own corpus sample, so
    there is no evidence this specific project's programs need it, and guessing its trigger
    shape/gating without corpus signal risks a false reject for no measured benefit.
    """
    known = _known_names(program, profile)
    whole_echo = profile.name in _WHOLE_EXPR_ECHO_LOGGERS
    unready = _not_yet_declared(program, profile)
    for stmt in program.statements:
        if not isinstance(stmt, S.ConstDecl):
            continue
        if stmt.type and stmt.type.upper() not in _CONST_TYPES:
            continue  # `unknown Const type` is the only message (oracle, 2026-10-10)
        names = _undefined_names(stmt.value, known - unready.get(id(stmt), frozenset()))
        if not names:
            continue
        if whole_echo:
            if stmt.value_span is not None:
                text = stmt.text[stmt.value_span[0]:stmt.value_span[1]]
            else:  # defensive: only reached if a `Const` was built without a span
                text = stmt.value.name if isinstance(stmt.value, S.Name) else names[0]
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=text), stmt.file)
        else:
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)


# `Data`/`DataLong` share one `compile_fn` in `data/instructions.json` (`0x406a32`) and were
# oracle-confirmed identical this round (`DataLong Tables and Store Data`, same two-line message
# below, byte-identical to `Data`'s own).
_DATA_KEYWORDS = frozenset({"DATA", "DATALONG"})


def check_data_params(program: S.Program, profile, diags: Diagnostics) -> None:
    """`Data`/`DataLong`'s own parameters (`docs/checker.md` "Data parameters"): each must be a
    constant expression referencing **no name at all**, defined or not -- oracle-confirmed with a
    from-scratch scratch probe set (`t.cr6` under Wine, no siblings/corpus file, `docs/checker.md`
    "Data parameters" records the transcript):

    - `Data SomeVar` (`SomeVar` undeclared): two lines on CR6/CR1000X, `Invalid symbol in
      expression: SomeVar\\n.` then `Illegal Data parameter SomeVar\\n.`; CR300/CR350 give
      `SomeVar is not defined.` (the existing per-logger `unresolved_symbol_message` split,
      `_WHOLE_EXPR_ECHO_LOGGERS`, reused as-is) in place of the first line, same second line --
      the embedded raw `\\n` (not a formatting artifact here, a literal newline byte in the real
      compiler's own message, present in `Illegal Data parameter`'s own text on *every* logger,
      `docs/checker.md` "Data parameters") only appears when the bad parameter is the *last* one
      on its physical line (`Data 1,SomeVar,3` echoes plain `SomeVar.`, no embedded newline,
      since a comma follows it on the same line) -- the real compiler's own internal source
      buffer keeps the terminating newline; ours (`stmt.text`) does not, so `_data_param_text`
      below adds it back only for that one shape.
    - `Data Batt_Volt` (`Batt_Volt` declared `Public`): only `Illegal Data parameter Batt_Volt\\n.`
      -- no undefined-symbol line at all, on any logger, since the name itself resolves fine; it
      is simply not a constant. This is why the check below cannot reuse `_undefined_names` alone
      the way every other rule in this module does: a Data parameter is illegal whether or not the
      name it references is defined, `_variable_refs(arg, known)` (reusing `known`, the full
      resolvable-name universe, in place of its usual `variable_names` argument) doubling as "does
      this expression contain any name that *does* resolve" for exactly that reason.
    - `Data 1,2,3` / `Data 1+2`: no error (a pure literal/arithmetic constant).

    Stops at the first bad parameter in a statement (no oracle evidence for two bad parameters in
    the same `Data`, and every other per-position check in this module already stops at the first
    problem the same way).

    Ungated pass 1, same as `check_consts` above (`cdmbrfull_1.CR6`: this fires alongside an
    earlier, unrelated `Need '=' in units definition:` pass-1 error, not suppressed by it).

    A `Data`/`DataLong` line the parser could not resolve into a clean argument list at all --
    the common CHM corpus shape, a `'`-less comment-continuation line the statement's own bare-
    argument-list grammar absorbs (`brfull6w_1.CR6`'s `Data Tables and Store Data`, no comma
    anywhere in it) -- never reaches the loop below as an `Instruction` at all: `parser.py`'s
    `_parse_bare_arglist` parses only the first word (`Tables`) as `Data`'s own first parameter,
    and the rest (`and Store Data`) is left as trailing input that turns the *whole statement*
    into an `S.Unparsed` (`Instruction.args`/`arg_spans` never populated). On CR6/CR1000X the
    oracle shows the real compiler still reports exactly this module's own two-line shape for it,
    spanning from right after the keyword to end of line (`Invalid symbol in expression: Tables
    and Store Data\\n.` / `Illegal Data parameter Tables and Store Data\\n.` -- not `Tables\\n.`
    alone, the substring our own parser would have captured as parameter 0's span had it not given
    up) -- `_check_unparsed_data` below reconstructs that same span textually (the whole line minus
    the keyword and its following whitespace) rather than trying to make the parser itself recover
    a partial argument list for this one shape. **CR300/CR350 are genuinely different here, not
    just a message-template swap**: oracle-confirmed (`docs/checker.md` "Data parameters"),
    `Data Tables and Store Data` there tokenises `and` as the real logical `AND` operator (a
    reserved word on those two loggers this shape happens to expose), so it becomes a compound
    `Tables AND (Store Data)` parse attempt -- three separate `%s is not defined.` lines (`Tables`,
    `Store`, `Data`, each its own undefined name) and `Incomplete expression: %s.` (not `Invalid
    symbol in expression`) as the wrapper, before the same `Illegal Data parameter %s.` line. Not
    implemented: reproducing it needs its own compound-expression walk this session did not build
    (AGENTS.md: under-report rather than guess), so `_check_unparsed_data` is scoped to
    `_WHOLE_EXPR_ECHO_LOGGERS` only and CR300/CR350 fall through unchanged to whatever later,
    already-existing pass would otherwise handle this `S.Unparsed` statement.
    """
    known = _known_names(program, profile)
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() in _DATA_KEYWORDS:
            _check_data_instruction(stmt, known, profile, diags)
        elif isinstance(stmt, S.Unparsed):
            _check_unparsed_data(stmt, diags, profile, program)


def _data_param_text(stmt: S.Instruction, i: int) -> str:
    text = _arg_text(stmt, i)
    if i == len(stmt.args) - 1:
        text += "\n"  # last parameter on the line: oracle-confirmed embedded newline, see above.
    return text


def _check_data_instruction(stmt: S.Instruction, known: set[str], profile,
                             diags: Diagnostics) -> None:
    for i, arg in enumerate(stmt.args):
        if arg is None:
            continue
        undefined = _undefined_names(arg, known)
        resolved_refs = _variable_refs(arg, known)
        if not undefined and not resolved_refs:
            continue  # a pure constant expression: legal.
        text = _data_param_text(stmt, i)
        if undefined:
            if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=text),
                            stmt.file)
            else:
                for name in undefined:
                    diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                                stmt.file)
        diags.error(stmt.line, f"Illegal Data parameter {text}.", stmt.file)
        return  # stop at the first bad parameter, see docstring above.


def _check_unparsed_data(stmt: S.Unparsed, diags: Diagnostics, profile, program) -> None:
    """`Data foo bar`: CR6/CR1000X `Invalid symbol in expression: foo bar` (whole text); CR300/
    CR350 each leading word as in a prose line (`Data Tables and Store Data` -> Tables, Store,
    Data), then `Incomplete expression: foo bar` (oracle, 2026-10-01). Both end with `Illegal Data
    parameter`."""
    if not stmt.reason.startswith("unexpected trailing input starting at"):
        return
    stripped = stmt.text.lstrip()
    upper = stripped.upper()
    for kw in ("DATALONG", "DATA"):  # longer name first: "DATALONG" also starts with "DATA".
        if upper.startswith(kw) and len(stripped) > len(kw) and stripped[len(kw)].isspace():
            text = stripped[len(kw):].lstrip() + "\n"
            if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
                diags.error(stmt.line, f"Invalid symbol in expression: {text}.", stmt.file)
            else:
                from . import cr3xx
                for name in cr3xx._leading_unknown_words(text, cr3xx.prose_known(program, profile)):
                    diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                                stmt.file)
                diags.error(stmt.line, f"Incomplete expression: {text}.", stmt.file)
            diags.error(stmt.line, f"Illegal Data parameter {text}.", stmt.file)
            return


def _call_arity_problem(name: str, args: list[S.Expr | None], profile) -> str | None:
    """`"few"`/`"many"`/`None` for one call's own argument count against `data/functions.json`'s
    `nargs`, restricted to `_ARITY_MIN_SAFE`/`_ARITY_MAX_SAFE` (the oracle-verified-safe subset,
    this module's own top-of-file comment) -- and, within that, only when *this profile's own*
    `nargs` for the name still matches the value the oracle sweep tested (`_ARITY_MIN_SAFE[name]`/
    `_ARITY_MAX_SAFE[name]`): a profile whose own table disagrees (`SerialOpen` on CR350, `docs/
    checker.md`) is silently left unchecked for that name, rather than trusting an unverified
    transfer of this session's CR6/CR1000X-only evidence. A name absent from `profile.functions`
    (not a built-in function at all -- a declared `Sub`/`Function`, or unresolved) is never this
    rule's concern.
    """
    record = profile.functions.get(name.upper())
    if not record:
        return None
    n = len(args)
    if name.upper() == "CTYPE":  # table nargs is 0; the call takes (value, type) (oracle, 4 loggers)
        return None if None in args or n == 2 else "few" if n == 1 else "many" if n > 2 else None
    nargs = record.get("nargs")
    if nargs is None:
        return None
    upper = name.upper()
    if any(i < len(args) and isinstance(args[i], S.String)
           for i in _FUNCTION_CONST_INTEGER_STRING_ARGS.get(upper, ())):
        return "constint"
    if any(i < n and _not_constant_integer(args[i]) for i in _CONST_INTEGER_BAD_ARGS.get(upper, ())):
        return "constint"
    if _TIMEOUT_GATE[0] is False and any(
            i < n and (_not_integer_type(args[i]) or _timeout_wide(args[i], profile))
            for i in _CONST_INTEGER_TYPE_ARGS.get(upper, ())):
        return "constint"
    if any(i < n and _not_integer_type(args[i]) for i in _CONST_INTEGER_TYPE_ARGS_ALWAYS.get(upper, ())):
        return "constint"
    if (_CONST_INTEGER_OVERFLOW_STRING.get(upper) == nargs and len(args) == nargs + 1
            and isinstance(args[-1], S.String)):
        return "constint"
    if _INCOMPLETE_OVERFLOW.get(upper) == nargs and len(args) == nargs + 1:
        return "incomplete"
    if n == 0 and (nargs > 0 or record.get("argspec")):
        # `X = Int()`, `SerialFlush()`: the generic fallback on the whole statement, pass 1, all
        # four loggers (oracle, 2026-09-27).
        return "empty"
    if allowed := _ARITY_ALLOWED.get(upper, {}).get(nargs):
        return None if n in allowed else "few" if n < max(allowed) else "many"
    if bounds := _ARITY_RANGE.get(upper, {}).get(nargs):
        return "few" if n < bounds[0] else "many" if n > bounds[1] else None
    if n < nargs and _ARITY_MIN_SAFE.get(name.upper()) == nargs:
        return "few"
    if n > nargs and _ARITY_MAX_SAFE.get(name.upper()) == nargs:
        return "many"
    return None


def _find_arity_problem(expr: S.Expr | None, profile) -> str | None:
    """The first (left-to-right, outer-before-inner) built-in-function call anywhere inside `expr`
    with a bad argument count, `_call_arity_problem`'s own `"few"`/`"many"`/`None`. Same recursion
    shape as `_undefined_names`/`_variable_refs` above (`Unary`/`Binary`/`Index`'s own name and
    args/`Member.base`/`ArrayLiteral` items); a `Member`'s own `.member` args (`Status.Battery(1)`
    -- a *field* call, never a built-in function) are deliberately not checked, same reasoning as
    `_undefined_names` leaving `Member.member` alone.

    Checks an `Index` node itself (a real parser-recognised call, or an array reference that
    happens to share a built-in function's name -- `_call_arity_problem` only fires when the name is
    genuinely in `profile.functions`, so an ordinary array reference is never affected) before
    recursing into its own arguments, matching the outer-call-first order the oracle showed for a
    single-argument-short outer call around an otherwise-fine inner one (`docs/checker.md`); a
    *doubly* bad-arity nested call (both the outer and an inner call wrong) is not oracle-tested
    either way and is left as this function's own documented order (outer first) rather than guessed
    at further.
    """
    if expr is None:
        return None
    if isinstance(expr, S.Index):
        direct = _call_arity_problem(expr.name, expr.args, profile)
        if direct:
            return direct
        for a in expr.args:
            found = _find_arity_problem(a, profile)
            if found:
                return found
        return None
    if isinstance(expr, S.Unary):
        return _find_arity_problem(expr.operand, profile)
    if isinstance(expr, S.Binary):
        return _find_arity_problem(expr.left, profile) or _find_arity_problem(expr.right, profile)
    if isinstance(expr, S.Member):
        if expr.args is not None and len(expr.args) > 2:
            # a table field takes at most (index, records back): `Tb.X(1,1,1)`, `Public.V(1,1,1)`
            # -> `No Matching, or out of place )` (oracle, all four loggers, 2026-10-01). Raised by
            # the pass-2 compile of the statement, not by pass 1 (message tracer, 2026-10-10), so
            # `check_function_arity` reports it through `held`.
            return "many_member"
        return _find_arity_problem(expr.base, profile)
    if isinstance(expr, S.ArrayLiteral):
        for item in expr.items:
            found = _find_arity_problem(item, profile)
            if found:
                return found
        return None
    return None  # Number, Name, String: no call to check


def _arity_message(kind: str, text: str, profile, at_eol: bool = True,
                   whole_statement: bool = False) -> str:
    if kind in ("constint", "incomplete"):
        verb = ("Parameter must be a constant integer: " if kind == "constint"
                else "Incomplete expression: ")
        sep = "\n" if profile.name in _ARITY_TRAILING_NEWLINE_LOGGERS and at_eol else ""
        return f"{verb}{text}{sep}."
    if kind == "empty":
        sep = "\n" if whole_statement and at_eol and profile.fallback_echo_newline else ""
        return f"Invalid, or out of place expression: {text}{sep}."
    verb = ("Expression expects , : " if kind == "few"
            else "No Matching, or out of place ) in expression: ")
    if profile.name in _ARITY_TRAILING_NEWLINE_LOGGERS and at_eol:  # not before a `'` comment
        return f"{verb}{text}\n."
    return f"{verb}{text}."


def _is_bare_reference_problem(name: str, profile, declared_variables: frozenset[str]) -> bool:
    """True when `name` is a built-in function (`data/<logger>/functions.json`) that can take at
    least one argument -- a required one (`nargs>=1`) or a purely-optional one (`nargs==0` but
    `argspec` non-zero: `EMAILRELAY`'s own shape, the sole `nargs==0` outlier, oracle-confirmed
    identical across all four loggers' own tables -- `docs/checker.md`) -- so a bare reference to
    it with no `(` at all is `Expression expects ( : %s.`.

    Unlike `_call_arity_problem`'s off-by-one arity check, this is not restricted to
    `_ARITY_MIN_SAFE`/`_ARITY_MAX_SAFE`: the oracle sweep for *that* rule needed a safe subset
    because some functions accept fewer/more than their own table `nargs` (an undocumented
    optional argument). This rule only asks whether the call has *no* argument list whatsoever,
    which every corpus example with `nargs>=1` -- `Sin`, `Timer`, `FindSpa`, `TCPOpen`,
    `SerialOpen`, `SerialOut`, `EmailSend`, `HTTPPut`, `IPTrace`, `SecsSince1990`,
    `DaylightSaving` -- confirms uniformly (`docs/checker.md`). A genuinely zero-argument function
    (`argspec` also 0: `Rnd`, `Signature`, `LineNum`, `PPPOpen`, `PPPClose`, `TimeUntilTransmit`,
    `IntDV`, `NewFieldCal`, `CType`, `AbsLong`, `ClockChange`) is oracle-confirmed to compile clean
    bare, with or without parens, on all four loggers -- this rule leaves those alone.

    A name the program itself declares as a genuine runtime variable
    (`symbols.declared_variable_names` -- `Public`/`Dim`/`Static`/a `Sub`/`Function` parameter)
    shadows the built-in function of the same name for bare-identifier resolution and is never
    flagged here -- found by this session's own false-reject sweep (`examples/embracer.cr6`'s
    `Dim sgn As Long, exp As Long, mant As Long` followed by the plain arithmetic
    `... + (exp << 13) + ...`, which the real compiler resolves as the *variable* `exp`, not the
    math function `Exp`, and compiles clean).
    """
    if name.upper() in declared_variables:
        return False
    record = profile.functions.get(name.upper())
    if not record:
        return False
    nargs = record.get("nargs")
    if nargs is None:
        return False
    return nargs > 0 or bool(record.get("argspec"))


def _find_bare_function_ref(expr: S.Expr | None, profile, declared_variables: frozenset[str]) -> bool:
    """Whether a `Name` node anywhere inside `expr` (same recursion shape as
    `_find_arity_problem`) bare-references a function `_is_bare_reference_problem` flags -- a
    built-in function used as a plain name, with no argument list at all, rather than as a call
    (`S.Index`). An `Index` node's own name is by definition already followed by `(`, so only its
    *arguments* are recursed into, matching `Randomize(Sin)` -> flags the nested `Sin`, not
    `Randomize` itself (oracle-confirmed, `docs/checker.md`).
    """
    if expr is None:
        return False
    if isinstance(expr, S.Name):
        return _is_bare_reference_problem(expr.name, profile, declared_variables)
    if isinstance(expr, S.Index):
        return any(_find_bare_function_ref(a, profile, declared_variables) for a in expr.args)
    if isinstance(expr, S.Unary):
        return _find_bare_function_ref(expr.operand, profile, declared_variables)
    if isinstance(expr, S.Binary):
        return (_find_bare_function_ref(expr.left, profile, declared_variables)
                or _find_bare_function_ref(expr.right, profile, declared_variables))
    if isinstance(expr, S.Member):
        return _find_bare_function_ref(expr.base, profile, declared_variables)
    if isinstance(expr, S.ArrayLiteral):
        return any(_find_bare_function_ref(item, profile, declared_variables) for item in expr.items)
    return False  # Number, String: no name to check


def _bare_function_message(text: str, profile, whole_statement: bool, at_eol: bool = True) -> str:
    # `whole_statement`: CR300/CR350 embed a literal newline before the closing `.` only when
    # `text` is the *whole* logical statement (`Call`/`Assignment`, matching `_arity_message`'s
    # own `_ARITY_TRAILING_NEWLINE_LOGGERS` convention for their arity messages) -- **not** for an
    # `Instruction` argument's own sub-span. Oracle-confirmed, this session: `Randomize(Sin)` on
    # `cr300comp.exe`/`cr350comp.exe` gives the plain, single-line `Expression expects ( : Sin.`
    # (no embedded newline), while `X = Sin` alone gives `Expression expects ( :   X = Sin\n.`
    # (`docs/checker.md`).
    if whole_statement and at_eol and profile.name in _ARITY_TRAILING_NEWLINE_LOGGERS:
        return f"Expression expects ( : {text}\n."
    return f"Expression expects ( : {text}."


def _struct_var_names(program: S.Program) -> frozenset[str]:
    from . import structs  # lazy: structs imports this module
    types = structs._structure_members(program)
    if not types:
        return frozenset()
    return frozenset(n for n, k in structs._variable_kinds(program, types).items()
                     if k.startswith("STRUCT:"))


def _multi_indexed_struct(expr: S.Expr | None, struct_vars: frozenset[str]) -> bool:
    """True when `expr` contains a structure-variable reference with two or more indices
    (`s1(10,2)`, `s2(1,2).value`): a structure reference takes at most one (`s1(1).value(2)`
    is fine -- the member's own index is separate). Same walk shape as `_find_arity_problem`."""
    if expr is None:
        return False
    if isinstance(expr, S.Index):
        return (expr.name.upper() in struct_vars and len(expr.args) >= 2) or any(
            _multi_indexed_struct(a, struct_vars) for a in expr.args)
    if isinstance(expr, S.Unary):
        return _multi_indexed_struct(expr.operand, struct_vars)
    if isinstance(expr, S.Binary):
        return (_multi_indexed_struct(expr.left, struct_vars)
                or _multi_indexed_struct(expr.right, struct_vars))
    if isinstance(expr, S.Member):
        return _multi_indexed_struct(expr.base, struct_vars) or any(
            _multi_indexed_struct(a, struct_vars) for a in expr.args or [])
    return False


def _hold(held: list | None, diags: Diagnostics, problem: str) -> None:
    if held is not None and problem == "many_member":
        held.append(diags.items[-1])


def check_function_arity(program: S.Program, profile, diags: Diagnostics,
                         held: list | None = None) -> None:
    """Built-in function call arity (`Expression expects , : %s.` / `No Matching, or out of place )
    in expression: %s.`) *and* a bare function-name reference with no argument list at all
    (`Expression expects ( : %s.`, `_is_bare_reference_problem`/`_find_bare_function_ref`) --
    this module's own top-of-file comment for the arity oracle evidence, `docs/checker.md`
    ("Expression expects ( (bare function name)") for the bare-reference evidence, and the pass-1
    tier this needs (wired unconditionally in `checker/__init__.py`, alongside
    `instr_rules.check`/`check_too_many`, not inside the gated `check` above). The bare-reference
    check runs first in each branch below and, when it fires, is the statement's only diagnostic
    from this function (matching the oracle: a bare reference is a parse-time concern that
    preempts the arity check, which only ever sees a real call with a real, if wrong, argument
    count).

    At most one diagnostic per statement, matching the oracle (a second, independent bad-arity call
    later in the *same* statement is never additionally reported -- confirmed both across two
    `Instruction` argument positions and, separately, within one `Call`'s own argument list):

    - `Instruction`: the first argument position (ascending, plain textual order -- **not** the
      constant-family reordering `_report_statement` above uses for a *different* check; arity is a
      raw parse-time concern, oracle-confirmed unaffected by that semantic reordering, `docs/
      checker.md`) whose own expression tree contains a bad-arity call, echoed as *that argument's
      own verbatim text* (`_arg_text`, matching `Randomize(Round(X))` -> echoes `Round(X)`, not the
      whole statement -- oracle-confirmed, `docs/checker.md`).
    - `Call` (a bare statement, `Round(Number,1,2)` alone, or a builtin used for its side effect):
      the call's own arity (if `stmt.name` is itself a built-in function) or any nested call inside
      its own argument list, echoed as the *whole statement's own verbatim text* (`stmt.text` --
      oracle-confirmed, `WatchdogTimer(0,Round(X))` -> echoes the whole statement, unlike the
      `Instruction`-argument case above; `Call` has no per-argument span to prefer, and the oracle
      shows it uses the whole-statement span here even for a call nested inside one of its own
      arguments).
    - `Assignment`: `stmt.value`'s own expression tree, echoed as the whole statement's own verbatim
      text (`stmt.text`, target and `=` included -- same convention `check` above already uses for
      this module's other assignment-RHS echo, `docs/checker.md` "Round 5"). The target itself is
      not checked (a function call is never a valid assignment target; not oracle-tested, left
      alone rather than guessed at).
    """
    declared_variables = frozenset(symbols.declared_variable_names(program))
    if profile.single_pass:
        # CR300/CR350 accept a local `Dim exp` / parameter named like a built-in function but
        # resolve its bare uses as the function (oracle, 2026-10-08)
        declared_variables = declared_variables - frozenset(profile.functions)
    known: set[str] | None = None
    from . import intervals, structs, structure  # lazy: all import this module
    struct_types = structs._structure_members(program)
    kinds = structs._variable_kinds(program, struct_types) if struct_types else {}
    struct_vars = _struct_var_names(program)
    sub_names = {st.name.upper() for st in program.statements
                 if isinstance(st, S.SubDef) and st.keyword.upper() == "SUB"}
    start = len(diags.items)
    mine: list = []
    table_mine: list = []   # the `mine` entries from DataTable output instructions
    in_table = False
    seen_timeint = False
    _TIMEOUT_WIDE[0] = intervals.wide_constants(program, profile)
    for stmt in program.statements:
        _TIMEOUT_GATE[0], seen_timeint = intervals.timeout_gate(seen_timeint, intervals.gate_text(stmt))
        if isinstance(stmt, S.Instruction):
            kw = stmt.keyword.upper()
            in_table = kw == "DATATABLE" or (in_table and kw != "ENDTABLE")
            # outside a table the "must be used inside a table declaration" check wins
            table_only = kw in instr_data.TABLE_ONLY_INSTRUCTIONS and not in_table
            for i, arg in enumerate(stmt.args):
                if arg is None:
                    continue
                if not table_only and _multi_indexed_struct(arg, struct_vars):
                    diags.error(stmt.line, _arity_message("many", _arg_text(stmt, i), profile, False),
                                stmt.file)
                    mine.append(diags.items[-1])
                    if in_table:
                        table_mine.append(diags.items[-1])
                    break
                if kw not in ("ALIAS", "UNITS") and _find_bare_function_ref(arg, profile, declared_variables):
                    msg = _bare_function_message(_arg_text(stmt, i), profile, whole_statement=False)
                    diags.error(stmt.line, msg, stmt.file)
                    break
                problem = _find_arity_problem(arg, profile)
                if problem == "empty":
                    diags.error(stmt.line, _arity_message(problem, _arg_text(stmt, i), profile),
                                stmt.file)
                    break
                if problem == "many_member" and table_only:
                    break  # the "must be used inside a table declaration" error wins
                if problem:
                    diags.error(stmt.line, _arity_message(problem, _arg_text(stmt, i), profile, False), stmt.file)
                    _hold(held, diags, problem)
                    break
        elif isinstance(stmt, S.Call):
            # A bare call with no argument list at all (`TCPOpen` alone, no trailing `(`
            # anywhere in this statement's own text) is textually indistinguishable, once
            # parsed, from an explicit empty call (`TCPOpen()`, also `args=[]`) -- `stmt.text`
            # is this statement's own source segment (`parser.py`'s `seg_text`), so checking it
            # for `(` recovers exactly that distinction without touching the parser (`docs/
            # checker.md`: `SerialOpen`/`TCPOpen`/`Timer`/`IPTrace` alone, each split from its
            # own argument list onto the next source line, all `Expression expects (`).
            if (not stmt.args and "(" not in stmt.text
                    and _is_bare_reference_problem(stmt.name, profile, declared_variables)):
                diags.error(stmt.line, _bare_function_message(stmt.text, profile, whole_statement=True, at_eol=stmt.at_eol), stmt.file)
                continue
            # a `Function` call statement continued as an expression (`Fn(1)+Sin`) is checked as
            # the whole expression, like `X = Fn(1)+Sin` (oracle, 2026-10-10)
            call_exprs = [stmt.tail_expr] if stmt.tail_expr is not None else stmt.args
            if any(_find_bare_function_ref(a, profile, declared_variables) for a in call_exprs):
                diags.error(stmt.line, _bare_function_message(stmt.text, profile, whole_statement=True, at_eol=stmt.at_eol), stmt.file)
                continue
            bad = next((i for i, a in enumerate(stmt.args) if _multi_indexed_struct(a, struct_vars)),
                       None)
            if bad is not None:
                # `Foo(A(1,2).V)`: the argument is echoed alone (oracle, 2026-10-03)
                diags.error(stmt.line, _arity_message("many", _call_arg_text(stmt.text, bad),
                                                      profile, False), stmt.file)
                mine.append(diags.items[-1])
                continue
            problem = _call_arity_problem(stmt.name, stmt.args, profile)
            in_arg = None
            if not problem:
                for k, a in enumerate(call_exprs):
                    problem = _find_arity_problem(a, profile)
                    if problem:
                        # inside a `Sub` call the offending argument alone is echoed (oracle,
                        # 2026-10-13, all four loggers); a `Function` statement echoes it whole
                        if stmt.tail_expr is None and stmt.juxtaposed is None and \
                                stmt.name.upper() in sub_names:
                            in_arg = k
                        break
            if problem:
                if (problem == "constint" and any(
                        d.line == stmt.line and d.file == stmt.file
                        and d.message.startswith("Parameter must be a constant integer:")
                        for d in diags.items)):
                    continue
                if in_arg is not None:
                    diags.error(stmt.line, _arity_message(
                        problem, _call_arg_text(stmt.text, in_arg).strip(), profile, False), stmt.file)
                    _hold(held, diags, problem)
                    continue
                diags.error(stmt.line, _arity_message(problem, stmt.text, profile, stmt.at_eol,
                                                            whole_statement=True), stmt.file)
                _hold(held, diags, problem)
        elif isinstance(stmt, S.If):
            # All four loggers, pass 1 (oracle, 2026-09-27): `If TimeIsBetween(0,15,60,sec,1) Then`
            # -> `No Matching, or out of place ) in expression: TimeIsBetween(0,15,60,sec,1) .`
            # (the condition echo, no CR300/CR350 newline).
            problem = _find_arity_problem(stmt.cond, profile)
            struct_cond = not problem and _multi_indexed_struct(stmt.cond, struct_vars)
            problem = problem or ("many" if struct_cond else None)
            text = _if_condition_text(stmt)
            if problem == "empty" and text is not None:
                diags.error(stmt.line, _arity_message(problem, text, profile), stmt.file)
            elif problem and text is not None:
                verb = ("Expression expects , : " if problem == "few"
                        else "No Matching, or out of place ) in expression: ")
                diags.error(stmt.line, f"{verb}{text}.", stmt.file)
                _hold(held, diags, problem)
                if struct_cond:
                    mine.append(diags.items[-1])
        elif isinstance(stmt, S.ConstDecl) and stmt.value_span:
            # `Const CRLF = CHR(13,1) + CHR(10)` -> `No Matching, or out of place ) in expression:
            # CHR(13,1) + CHR(10).`, the value echoed, all four loggers (oracle, 2026-10-01)
            problem = _find_arity_problem(stmt.value, profile)
            text = stmt.text[stmt.value_span[0]:stmt.value_span[1]]
            if problem == "empty":
                # `Const Pi = 4*ATN() 'c` -> `Invalid, or out of place expression: 4*ATN() .`,
                # the value to the comment, no CR300 newline (oracle, 2026-10-01)
                diags.error(stmt.line, "Invalid, or out of place expression: "
                            f"{stmt.text[stmt.value_span[0]:]}.", stmt.file)
            elif problem:
                verb = ("Expression expects , : " if problem == "few"
                        else "No Matching, or out of place ) in expression: ")
                diags.error(stmt.line, f"{verb}{text}.", stmt.file)
        elif isinstance(stmt, S.Assignment):
            if known is None:
                known = _known_names(program, profile)
            if _undefined_names(stmt.target, known):
                # An undeclared target defers the whole statement to pass 2 (the fallback and
                # its placement follow-up there; oracle: `Y=EXP()` before `BeginProg`).
                continue
            if _multi_indexed_struct(stmt.target, struct_vars):
                # a structure reference takes one index; oracle 2026-10-03, docs/checker.md
                diags.error(stmt.line, structure._out_of_place(profile, stmt.text, stmt.at_eol),
                            stmt.file)
                mine.append(diags.items[-1])
                continue
            target_message = _function_target_message(stmt, profile, declared_variables)
            if target_message:
                diags.error(stmt.line, target_message, stmt.file)
                continue
            if _find_bare_function_ref(stmt.value, profile, declared_variables):
                diags.error(stmt.line, _bare_function_message(stmt.text, profile, whole_statement=True, at_eol=stmt.at_eol), stmt.file)
                continue
            problem = _find_arity_problem(stmt.value, profile)
            struct_value = not problem and _multi_indexed_struct(stmt.value, struct_vars)
            if struct_value:
                problem = "many"
                if (profile.single_pass and not stmt.compound and isinstance(stmt.value, S.Index)
                        and structs._operand_kind(stmt.target, kinds, struct_types) == "S"):
                    diags.items.append(Diagnostic("error", stmt.line, structs._WRONG_TYPE, stmt.file,
                                                  cascade=False))
                    mine.append(diags.items[-1])
            if problem:
                diags.error(stmt.line, _arity_message(problem, stmt.text, profile, stmt.at_eol,
                                                            whole_statement=True), stmt.file,
                            stmt=stmt)
                _hold(held, diags, problem)
                if struct_value:
                    mine.append(diags.items[-1])
    _TIMEOUT_GATE[0] = None
    _TIMEOUT_WIDE[0] = frozenset()
    if not profile.single_pass and table_mine:
        # CR6/CR1000X: only the first DataTable instruction is reported, and none at all once a
        # statement outside a table has one (oracle, 2026-10-03)
        outside = any(d not in table_mine for d in mine)
        keep = [] if outside else table_mine[:1]
        drop = [d for d in table_mine if d not in keep]
        diags.items[start:] = [d for d in diags.items[start:] if d not in drop]
        mine[:] = [d for d in mine if d not in drop]
    if profile.single_pass and any(d not in mine for d in diags.items[start:]):
        # CR300/CR350: any call-arity error in the file suppresses every structure-index one,
        # before or after it (oracle, 2026-10-03)
        diags.items[start:] = [d for d in diags.items[start:] if d not in mine]


def _function_target_message(stmt: S.Assignment, profile, declared: frozenset[str]) -> str | None:
    """An assignment whose target is a built-in function (oracle, all four loggers, 2026-10-08):
    `Sin = 1` / `Timer = X` -> `Expression expects ( : <stmt>`; `Rnd = 2`, `Sin(X) = 3`,
    `Round(1,2) = 3` -> `Invalid assignment in expression: <stmt>`; a call with a wrong argument
    count gets that count's message (`Sin(1,2) = 3`, `Round(1) = 2`, `Sin() = 3`). Only arities
    this module already trusts (`_call_arity_problem`, or exactly the table `nargs`) are reported."""
    target = stmt.target
    if stmt.compound or not isinstance(target, (S.Name, S.Index)):
        return None
    name = target.name.upper()
    record = profile.functions.get(name)
    if not record or name in declared:
        return None
    sep = "\n" if profile.fallback_echo_newline and stmt.at_eol else ""
    if isinstance(target, S.Name):
        if _is_bare_reference_problem(target.name, profile, declared):
            return _bare_function_message(stmt.text, profile, whole_statement=True, at_eol=stmt.at_eol)
        return f"Invalid assignment in expression: {stmt.text}{sep}." \
            if record.get("nargs") == 0 and not record.get("argspec") else None
    problem = _call_arity_problem(target.name, target.args, profile)
    if problem in ("few", "many", "empty"):
        return _arity_message(problem, stmt.text, profile, stmt.at_eol, whole_statement=True)
    if problem is None and None not in target.args and record.get("nargs") == len(target.args) \
            and record.get("nargs"):
        return f"Invalid assignment in expression: {stmt.text}{sep}."
    return None


def check_function_call_counts(program: S.Program, profile, diags: Diagnostics) -> None:
    """A user `Function` called with too few arguments -> `Call argument required.` then the
    fallback; with too many -> `No Matching, or out of place ) in expression: %s.`. The echo is the
    assignment, the `If` condition, or the instruction argument holding the call. Pass 1 on
    CR6/CR1000X (a later `X = Q` is not reported), all four loggers (oracle, 2026-09-28,
    `docs/checker.md` "Function argument counts")."""
    from . import structure
    functions = {st.name.upper(): st for st in program.statements
                 if isinstance(st, S.SubDef) and st.keyword == "FUNCTION"}
    if not functions:
        return
    known = _known_names(program, profile)
    missing = [1]

    def problem(expr: S.Expr | None) -> str | None:
        for node in _index_nodes(expr):
            fn = functions.get(node.name.upper())
            if fn is None:
                continue
            required = sum(1 for p in fn.params if p.default is None)
            if len(node.args) < required:
                # one message per missing required argument, nested calls in the arguments add
                # theirs, a later sibling call adds none (oracle, 2026-10-12)
                missing[0] = 0
                for inner in _index_nodes(node):
                    f2 = functions.get(inner.name.upper())
                    if f2 is not None:
                        missing[0] += max(0, sum(1 for p in f2.params if p.default is None)
                                          - len(inner.args))
                return "few"
            if len(node.args) > len(fn.params):
                return "many"
        return None

    def report(stmt: S.Stmt, kind: str, echo: str, whole: bool) -> None:
        if kind == "few":
            for _ in range(missing[0]):   # one per missing required argument (oracle, 2026-10-12)
                diags.error(stmt.line, "Call argument required.", stmt.file)
            text = structure._out_of_place(profile, echo, stmt.at_eol) if whole else \
                f"Invalid, or out of place expression: {echo}."
        else:
            sep = "\n" if whole and profile.fallback_echo_newline and stmt.at_eol else ""
            text = f"No Matching, or out of place ) in expression: {echo}{sep}."
        diags.error(stmt.line, text, stmt.file)

    def visit(stmt: S.Stmt) -> None:
        if isinstance(stmt, S.Assignment):
            kind = problem(stmt.target) or problem(stmt.value)
            if kind and _undefined_names(stmt.target, known) and not (
                    profile.single_pass and (isinstance(stmt.target, S.Name) or (
                        isinstance(stmt.target, S.Index) and stmt.target.name.upper() in known))):
                kind = None  # an undefined target never reaches the value's call (oracle, all four
                #              loggers, 2026-10-11, docs/checker.md "Function calls after an undefined target")
            if kind:
                report(stmt, kind, stmt.text, True)
        elif isinstance(stmt, S.If):
            kind = problem(stmt.cond)
            echo = _if_condition_text(stmt)
            if kind and echo is not None:
                report(stmt, kind, echo, False)
            for inner in (stmt.inline or []) + (stmt.inline_else or []):
                visit(inner)
        elif isinstance(stmt, S.Instruction):
            for i, arg in enumerate(stmt.args):
                kind = problem(arg)
                if kind:
                    report(stmt, kind, _arg_text(stmt, i).strip(), False)
                    break
        elif isinstance(stmt, S.Call):
            # `F1(1,2)` as a statement echoes the whole line like `X = F1(1,2)` (oracle,
            # 2026-10-02, all four loggers, `docs/checker.md` "Function argument counts").
            if not stmt.explicit and stmt.tail_expr is not None and (kind := problem(stmt.tail_expr)):
                report(stmt, kind, stmt.text, True)
                return
            fn = functions.get(stmt.name.upper())
            if fn is None and stmt.tail_expr is None and stmt.juxtaposed is None:
                # a `Sub` call: a user `Function` called wrongly inside one argument echoes that
                # argument (oracle, 2026-10-13, all four loggers)
                for i, arg in enumerate(stmt.args):
                    if kind := problem(arg):
                        report(stmt, kind, _call_arg_text(stmt.text, i).strip(), False)
                        return
            bare = not stmt.args and not (stmt.text or "").lstrip()[len(stmt.name):].lstrip().startswith("(")
            if not stmt.explicit and fn is not None and not bare:  # a bare `Fn` (no parentheses) is accepted (oracle)
                required = sum(1 for p in fn.params if p.default is None)
                kind = ("few" if len(stmt.args) < required
                        else "many" if len(stmt.args) > len(fn.params) else None)
                missing[0] = max(1, required - len(stmt.args))
                if kind:
                    report(stmt, kind, stmt.text, True)
                elif (m := re.match(r"\s*" + re.escape(stmt.name) + r"\s*(?=\()", stmt.text or "", re.I)) \
                        and structure._group_end(stmt.text, m.end()) is None:
                    # a user `Function` called as a statement needs its `)` (a `Sub` call does
                    # not): oracle, 2026-10-13, all four loggers
                    report(stmt, "many", stmt.text, True)
                else:
                    # a wrong call nested in an argument: the whole statement is echoed (oracle,
                    # 2026-10-13, all four loggers)
                    for arg in stmt.args:
                        if kind := problem(arg):
                            report(stmt, kind, stmt.text, True)
                            return

    for stmt in program.statements:
        visit(stmt)


def _index_nodes(expr: S.Expr | None):
    """Every `Index` (call or subscript) inside `expr`, outermost first."""
    if expr is None:
        return
    if isinstance(expr, S.Index):
        yield expr
        for a in expr.args:
            yield from _index_nodes(a)
    elif isinstance(expr, S.Unary):
        yield from _index_nodes(expr.operand)
    elif isinstance(expr, S.Binary):
        yield from _index_nodes(expr.left)
        yield from _index_nodes(expr.right)
    elif isinstance(expr, S.Member):
        for a in expr.args or []:
            yield from _index_nodes(a)


_FOR_HEADER_RE = re.compile(r"(?i)^\s*For\s(.*?)=(.*?)\sTo\s(.*?)(?:\sStep\s(.*))?$")


def _check_for_headers(program: S.Program, profile, diags: Diagnostics, known) -> None:
    """Undefined names in a `For` header (oracle, 2026-10-01). CR6/CR1000X: `Invalid symbol in
    expression: %s.` for the start, end and step text (each echoed raw between its keywords, one
    separating space dropped either side: `For X = A to 2` -> ` A`), and for the loop variable on
    its matching `Next` line (`For j = 1 To 2` -> `j .`). CR300/CR350: per-name `%s is not
    defined.` on the `For` line in source order, plus `Dimensions not declared: j(1) .` for an
    undefined indexed loop variable."""
    ds = program_dims(program, profile, known)
    pending: list[tuple[S.For, str]] = []
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "NEXT" and pending and \
                not re.match(r"(?i)\s*Next\s+(Sub)?Scan\b", stmt.text or ""):
            loop, var_text = pending.pop()
            if var_text:
                diags.error(stmt.line, f"Invalid symbol in expression: {var_text}.", stmt.file)
            continue
        if not isinstance(stmt, S.For):
            continue
        m = _FOR_HEADER_RE.match(stmt.text or "")
        parts = [(stmt.var, m.group(1) if m else None), (stmt.start, m.group(2) if m else None),
                 (stmt.end, m.group(3) if m else None), (stmt.step, m.group(4) if m else None)]
        whole_echo = profile.name in _WHOLE_EXPR_ECHO_LOGGERS
        var_text = ""
        for i, (expr, text) in enumerate(parts):
            names = _undefined_names(expr, known) if expr is not None else []
            if not names:
                if i and text is not None and _over_indexed(
                        expr, symbols.declared_dim_counts(program, strings=True), _dim_limit(profile)):
                    # `For N = 1 To B(1,1,1,1)`: the header part echoed (oracle, 2026-10-13)
                    diags.error(stmt.line, dimensions_message(text, profile, False), stmt.file)
                continue
            if whole_echo:
                if text is None:
                    continue
                if i == 0:
                    var_text = text
                else:
                    diags.error(stmt.line, f"Invalid symbol in expression: {text}.", stmt.file)
            else:
                for name in names:
                    diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                                stmt.file)
                if i == 0 and text is not None and _has_undefined_index(expr, known, ds, stmt):
                    diags.error(stmt.line, dimensions_message(text, profile, False), stmt.file)
        pending.append((stmt, var_text))


def _check_elseif_and_case(program: S.Program, profile, diags: Diagnostics, known) -> None:
    """`ElseIf` conditions and `Case` values (oracle, 2026-10-01). An `ElseIf` is reported like an
    `If`. A `Case` echoes the text after `Case ` on CR6/CR1000X, preceded by one `Case outside
    Select.` per item up to the first item whose value (or `To` lower bound) is undefined (none
    when an `Is` comparison or an upper bound fails first): `Case 1, 2, Nope` -> three.
    CR300/CR350 print each undefined name; there a `Select Case` with an undefined name opens no
    block, so its `Case`s give `Case outside Select.` and its end `EndSelect w/o Select`."""
    ds = program_dims(program, profile, known)
    whole_echo = profile.name in _WHOLE_EXPR_ECHO_LOGGERS
    orphans: list[bool] = []  # per open Select: True when CR300 did not open it
    open_ifs = 0
    for stmt in program.statements:
        kw = stmt.keyword.upper() if isinstance(stmt, S.Instruction) else ""
        if kw == "SELECT":
            # (an over-indexed selector opens no block either, on all four loggers)
            orphans.append(bool(stmt.args) and (
                (not whole_echo and bool(_undefined_names(stmt.args[0], known)))
                or (not _undefined_names(stmt.args[0], known) and _over_indexed(
                    stmt.args[0], symbols.declared_dim_counts(program, strings=True),
                    _dim_limit(profile)))))
            continue
        if kw == "ENDSELECT" or (kw == "END" and re.match(r"(?i)\s*End\s+Select\b", stmt.text or "")):
            if orphans and orphans.pop():
                diags.error(stmt.line, "EndSelect w/o Select", stmt.file)
            continue
        if isinstance(stmt, S.If) and stmt.inline is None:
            open_ifs += 1
        elif kw == "ENDIF" or (kw == "END" and re.match(r"(?i)\s*End\s+If\b", stmt.text or "")):
            open_ifs = max(0, open_ifs - 1)
        if isinstance(stmt, S.ElseIf):
            if whole_echo and not open_ifs:
                continue  # `ElseIf Nope Then` with no `If`: only `Case with no Select.`
            names = _undefined_names(stmt.cond, known)
            text = _if_condition_text(stmt)
            if text is not None and not names and _over_indexed(
                    stmt.cond, symbols.declared_dim_counts(program, strings=True),
                    _dim_limit(profile)):
                diags.error(stmt.line, dimensions_message(text, profile, False), stmt.file)
                continue
            if not names or text is None:
                continue
            if whole_echo:
                diags.error(stmt.line, f"Invalid symbol in expression: {text}.", stmt.file)
            else:
                for name in names:
                    diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                                stmt.file)
                if _has_undefined_index(stmt.cond, known, ds, stmt):
                    diags.error(stmt.line, dimensions_message(text, profile, False), stmt.file)
            continue
        if not isinstance(stmt, S.Case) or stmt.is_else or \
                re.match(r"(?i)\s*Case\s+Else", stmt.text or ""):
            continue  # (`Case Elsewhere` is a `Case Else`, `checker/structure.py`)
        if orphans and orphans[-1]:
            diags.error(stmt.line, "Case outside Select.", stmt.file)
        names: list[str] = []
        outside = 0
        for index, item in enumerate(stmt.items, 1):
            if item[0] == "value":
                lower, upper = item[1], None
            elif item[0] == "range":
                lower, upper = item[1], item[2]
            else:
                lower, upper = None, item[2]
            low, high = _undefined_names(lower, known), _undefined_names(upper, known)
            if low and not outside and not names:
                outside = index
            names += low + high
        if not names:
            cm = re.match(r"(?i)\s*Case\s(.*)$", stmt.text or "")
            if cm:
                counts = symbols.declared_dim_counts(program, strings=True)
                if any(_over_indexed(e, counts, _dim_limit(profile)) for item in stmt.items
                       for e in item[1:] if isinstance(e, S.Expr)):
                    # the whole item list after `Case ` is echoed (oracle, 2026-10-13)
                    diags.error(stmt.line, dimensions_message(cm.group(1), profile, False),
                                stmt.file)
            continue
        if whole_echo:
            m = re.match(r"(?i)\s*Case\s(.*)$", stmt.text or "")
            if not m:
                continue
            for _ in range(outside):
                diags.error(stmt.line, "Case outside Select.", stmt.file)
            diags.error(stmt.line, f"Invalid symbol in expression: {m.group(1)}.", stmt.file)
        else:
            for name in names:
                diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name),
                            stmt.file)


def _check_function_statements(program: S.Program, profile, diags: Diagnostics, known) -> None:
    """A built-in function written as a statement (`Timer(Nope,Sec,0)`) with an undefined name in
    its arguments (oracle, 2026-10-01): CR6/CR1000X `Invalid symbol in expression: <statement>.`
    (indentation and trailing spaces kept); CR300/CR350 each name, then `Dimensions not declared:
    <statement>` when an undefined name is indexed."""
    ds = program_dims(program, profile, known)
    first_on_line: dict[tuple, S.Stmt] = {}
    for stmt in program.statements:
        first_on_line.setdefault((stmt.line, stmt.file), stmt)
    for stmt in program.statements:
        if not isinstance(stmt, S.Call) or stmt.explicit or stmt.name.upper() not in profile.functions:
            continue
        names = [n for a in stmt.args for n in _undefined_names(a, known)]
        # (its own `Expr illegally occurs before BeginProg.` does not hide it, unless an earlier
        # statement of the line owns that placement error: `X = 1 : Timer(Nope,1,0)`)
        if not names or any((d.line, d.file) == (stmt.line, stmt.file)
                            and not (d.message == "Expr illegally occurs before BeginProg."
                                     and first_on_line[(stmt.line, stmt.file)] is stmt)
                            for d in diags.errors):
            continue  # (`SerialOpen(COMC1,...)` is already reported by `cr3xx.check`)
        if profile.name in _WHOLE_EXPR_ECHO_LOGGERS:
            diags.error(stmt.line, f"Invalid symbol in expression: {stmt.text}.", stmt.file)
            continue
        names, dims = _names_and_dims(known, ds, stmt, (), *stmt.args)
        for name in names:
            diags.error(stmt.line, profile.unresolved_symbol_message.format(name=name), stmt.file)
        if dims:
            diags.error(stmt.line, dimensions_message(stmt.text, profile, True, stmt.at_eol),
                        stmt.file)


# Parameter codes whose plain array-element argument is bounds-checked, and instructions whose own
# compile function takes that argument over (oracle, 2026-10-01: `AddPrecise(X,A(9))` compiles).
_BOUNDS_CHECKED_CODES = frozenset({2, 8, 24, 27, 28, 29, 41})
_BOUNDS_UNCHECKED = frozenset({"ADDPRECISE", "MOVEPRECISE"})
# Codes 27/29 only for oracle-probed instructions (2026-10-02, docs/checker.md "Destination bounds").
_BOUNDS_PROBED = {
    24: frozenset({"SDMIO16", "PORTPAIRCONFIG"}),   # (Float and Long, oracle 2026-10-12)
    27: frozenset({"MOVE", "ERASE", "GETRECORD", "SPLITSTR", "MOVEBYTES"}),
    29: frozenset({"REALTIME", "READIO", "PORTGET", "AVGSPA", "MAXSPA", "MINSPA", "RMSSPA",
                   "STDDEVSPA", "SORTSPA", "SORTSPAINDEXED"}),
}


def check_instruction_index_bounds(program: S.Program, profile, diags: Diagnostics,
                                   env: consteval.Env) -> None:
    """`Battery(A(9))` for `Public A(5)` -> `Variable A out of bounds.` (oracle, 2026-10-01): pass 2
    on CR6/CR1000X, the late stage on CR300/CR350 (only in a file with no other error). Only an
    argument that is itself an element of a Float array, indexed by number literals: index 0 or
    past its dimension (a single index on a multi-dimensional array against the element count).
    On CR6/CR1000X a scalar is shape `(1,)` and subscripts beyond the declared dimensions count as
    size-1 dimensions (`R(1,2)`, `X(2)`, `B(1,1,2)` are out of bounds; `R(4,1)`, `X(1,1)` compile);
    CR300/CR350 report those as `Dimensions not declared`. An expression (`A(6)+1`), `A(-1)`, a
    Long array and an assignment (`X = A(6)`, checked at run time) all compile."""
    padded = profile.name in _WHOLE_EXPR_ECHO_LOGGERS
    tables = symbols.scope_tables(program, env, scalars=padded)
    for stmt in program.statements:
        if isinstance(stmt, S.Instruction) and stmt.keyword.upper() == "WHILE" and \
                len(stmt.args) == 1 and isinstance(stmt.args[0], S.Index):
            candidates = [(2, stmt.args[0])]
        elif isinstance(stmt, (S.If, S.ElseIf, S.DoLoop)) and isinstance(stmt.cond, S.Index):
            # a condition that is one element (`If A(3) Then`, `While A(0)`, `ElseIf A(5) Then`):
            # checked like a code-2 argument (oracle, all four loggers, 2026-10-12; `If A(3) > 1`
            # and `Y = A(3)` compile)
            candidates = [(2, stmt.cond)]
        elif isinstance(stmt, S.Instruction) and stmt.keyword.upper() not in _BOUNDS_UNCHECKED \
                and (record := profile.instructions.get(stmt.keyword.upper())):
            candidates = [(record["params"][i] if i < len(record["params"]) else None, arg)
                          for i, arg in enumerate(stmt.args)
                          # (`FieldCal` options 0 and 4 never resolve `KnownVar`, oracle 2026-10-12:
                          # `Meter(3)` of `Meter(2)` compiles)
                          if not (stmt.keyword.upper() == "FIELDCAL" and i == 6 and stmt.args
                                  and isinstance(stmt.args[0], S.Number) and stmt.args[0].value in (0, 4))]
        else:
            continue
        scope = tables(stmt)
        dims, types, arrays = scope.dims, scope.types, scope.arrays
        for code, arg in candidates:
            if code not in _BOUNDS_CHECKED_CODES or not isinstance(arg, S.Index) or not arg.args:
                continue
            if code in _BOUNDS_PROBED and stmt.keyword.upper() not in _BOUNDS_PROBED[code]:
                continue
            name = arg.name.upper()
            shape = dims.get(name)
            # Code 27 (Move/Erase/GetRecord/SplitStr destinations) also checks Long and String
            # arrays (oracle, 2026-10-02); the other codes are only probed for Float.
            if not shape or (types.get(name) != "FLOAT" and not (code == 27 and name in arrays) and
                             not (code in (24, 29) and types.get(name) == "LONG")) or \
                    not all(isinstance(a, S.Number) and isinstance(a.value, int) for a in arg.args):
                continue
            index = [a.value for a in arg.args]
            if len(index) == 1 and len(shape) > 1:
                total = 1
                for n in shape:
                    total *= n
                bad = not 1 <= index[0] <= total
            elif len(index) == len(shape) or (padded and len(index) > len(shape)):
                shape = shape + (1,) * (len(index) - len(shape))
                bad = any(not 1 <= k <= n for k, n in zip(index, shape))
            else:
                continue
            if bad:
                diags.error(stmt.line, f"Variable {arg.name} out of bounds.", stmt.file)


def check_splitstr_element_reps(program: S.Program, profile, diags: Diagnostics, env) -> None:
    """`SplitStr(F(i),...,Reps,...)` into an element of a one-dimensional non-String array:
    `Variable F out of bounds.` when `i + Reps - 1` exceeds its size, whatever the option (oracle,
    all four loggers, 2026-10-12: `Public F(3)` with `F(1)`/Reps 6 or `F(2)`/Reps 3, Long arrays too,
    and `G((1+2)) As String * 10`, a parenthesised dimension making a Float array; a String array
    `T(3)` compiles with Reps 6)."""
    strings = {v.name.upper() for st in program.statements if isinstance(st, S.Declaration)
               for v in st.vars if (v.type or "").upper() == "STRING" and not v.paren_dim}
    tables = symbols.scope_tables(program, env)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() != "SPLITSTR" \
                or len(stmt.args) < 4:
            continue
        dest = stmt.args[0]
        if not isinstance(dest, S.Index) or len(dest.args) != 1 or \
                not isinstance(dest.args[0], S.Number) or not isinstance(dest.args[0].value, int):
            continue
        name = dest.name.upper()
        shape = tables(stmt).dims.get(name)
        if not shape or len(shape) != 1 or name in strings or name not in tables(stmt).arrays:
            continue
        try:
            reps = consteval.value_of(stmt.args[3], env)
        except (consteval.NotConstant, consteval.DivideByZero):
            continue
        if isinstance(reps, bool) or not isinstance(reps, (int, float)) or reps != int(reps) or reps < 1:
            continue
        if dest.args[0].value + int(reps) - 1 > shape[0]:
            diags.error(stmt.line, f"Variable {dest.name} out of bounds.", stmt.file)


_CALIBRATE_MIN = {"cr6": 6, "cr1000x": 6, "cr300": 51, "cr350": 51}


def check_calibrate_size(program: S.Program, profile, diags: Diagnostics, env) -> None:
    """`Calibrate(Dest)` needs at least 6 Float elements from `Dest` to the end of its array on
    CR6/CR1000X, 51 on CR300/CR350 (whole array, `Dest()` or `Dest(i)`), else `Variable Dest out of
    bounds.` (oracle, 2026-10-12: sizes 1..5 fail and 6..100 compile on CR6; 1..50 fail and 51..100
    compile on CR300/CR350; `Public K()` is one element)."""
    need = _CALIBRATE_MIN.get(profile.name)
    if need is None:
        return
    tables = symbols.scope_tables(program, env, scalars=True)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() != "CALIBRATE" or not stmt.args:
            continue
        dest = stmt.args[0]
        if not isinstance(dest, (S.Name, S.Index)):
            continue
        shape = tables(stmt).dims.get(dest.name.upper())
        if not shape or len(shape) != 1:
            continue
        start = 1
        if isinstance(dest, S.Index) and dest.args:
            if len(dest.args) != 1 or not isinstance(dest.args[0], S.Number) or \
                    not isinstance(dest.args[0].value, int):
                continue
            start = dest.args[0].value
        if shape[0] - start + 1 < need:
            diags.error(stmt.line, f"Variable {dest.name} out of bounds.", stmt.file)


def check_splitstr_whole_array(program: S.Program, profile, diags: Diagnostics, env) -> None:
    """`SplitStr(A(),...,Reps,...)` with `A` an array and no subscript: `Variable A out of bounds.`
    when `Reps` exceeds the element count (oracle, 2026-10-11, all four loggers: `Public S(5)` with
    Reps 6 or 20, `Public M(2,3)` with 7, `Public S((2+3))` with 6 are out of bounds; Reps 5 and 6
    for `M(2,3)` compile; Reps 0, 1 and -1 compile). Float, Long and String arrays alike. A
    subscripted destination (`SplitStr(S(1),...,6,7)` compiles) is not this rule. Run after
    `instr_rules.check_option_values`: `SplitStr(S(),...,6,-1)` prints `Invalid option -1` first."""
    tables = symbols.scope_tables(program, env)
    for stmt in program.statements:
        if not isinstance(stmt, S.Instruction) or stmt.keyword.upper() != "SPLITSTR":
            continue
        dims, arrays = tables(stmt).dims, tables(stmt).arrays
        if not stmt.args or not isinstance(stmt.args[0], S.Index) or stmt.args[0].args \
                or len(stmt.args) < 4:
            continue
        dest = stmt.args[0]
        name = dest.name.upper()
        shape = dims.get(name)
        if not shape or name not in arrays:
            continue
        try:
            reps = consteval.value_of(stmt.args[3], env)
        except consteval.DivideByZero as exc:
            # oracle, 2026-10-12: with integer operands CR6/CR1000X name the Reps expression (`1/0`,
            # `1 / 0`, `(1/0)`, `1/0+1`, `-1/0`, `0/0`, `K/(K-3)`); every other case is a plus
            # infinity (out of bounds, all four loggers: CR6/CR1000X `2.5/0`, `1/0.0`; CR300/CR350
            # also `1/0`, `5/0`) when the Reps is itself `positive/0`, and `0/0`, `-1/0` compile on
            # CR300/CR350. `1/0+1` on CR300/CR350 is out of bounds too (not modelled).
            text = _arg_text(stmt, 3).strip()
            if "." not in text and not profile.single_pass:
                diags.error(stmt.line, f"Divide by zero in expression: {text}.", stmt.file)
            elif isinstance(stmt.args[3], S.Binary) and stmt.args[3].op == "/" and \
                    isinstance(exc.numerator, (int, float)) and exc.numerator > 0:
                diags.error(stmt.line, f"Variable {dest.name} out of bounds.", stmt.file)
            continue
        except consteval.NotConstant:
            continue
        if not isinstance(reps, int) or isinstance(reps, bool):
            continue
        total = 1
        for n in shape:
            total *= n
        if reps > total:
            diags.error(stmt.line, f"Variable {dest.name} out of bounds.", stmt.file)


def check_subcall_element_bounds(program: S.Program, profile, diags: Diagnostics, env) -> None:
    """`Sub` call with an array element literal argument out of its declared range: `S1(P(10))` for
    `Public P(9)` -> `Variable P out of bounds.` (oracle, 2026-10-11, all four loggers; `Call S1(P(10))`
    and `S1 (P(0))` too, `S1(R(4))` for `Public R(3)`, `S1(S(6))` for `Public S(5) As String * 20`,
    `S1(F9(10))` for `Public F9(9)`). Valid: 1..n per dimension. Checked for a single-subscript or
    per-dimension subscript on a 1-D or multi-dimensional array with number-literal subscripts only;
    a negative literal (`P(-1)`), an expression (`P(10)+1`, `P(I)`), a single subscript on a
    multi-dimensional array (`M(6)` on `M(2,3)` is out of bounds on the real compiler but not
    modelled) and `P(1,1)` on a 1-D array (`Dimensions not declared` on CR300/CR350) are not this rule.
    Functions are not checked (`X = FN(P(10))`)."""
    subs = symbols.declared_subs(program)
    tables = symbols.scope_tables(program, env)
    for stmt in program.statements:
        if not isinstance(stmt, S.Call) or stmt.name.upper() not in subs:
            continue
        dims = tables(stmt).dims
        for arg in stmt.args:
            if not isinstance(arg, S.Index) or not arg.args:
                continue
            shape = dims.get(arg.name.upper())
            index = [a.value for a in arg.args if isinstance(a, S.Number) and not isinstance(a.value, float)]
            if not shape or len(index) != len(arg.args) or len(index) != len(shape) or \
                    any(k < 0 for k in index):
                continue
            if any(not 1 <= k <= n for k, n in zip(index, shape)):
                diags.error(stmt.line, f"Variable {arg.name} out of bounds.", stmt.file)


def check_recursive_functions(program: S.Program, profile, diags: Diagnostics) -> None:
    """A `Function` called inside its own body (to its `EndFunction`, or the end of the file when
    there is none) -> `Cannot recursively call functions.`, pass 1, all four loggers (oracle,
    2026-10-01: `Return F(a)`, `X = F(a) + 1`, `If F(a) > 1 Then`; a `Sub` calling itself and
    `F = 1` compile)."""
    current: str | None = None
    for stmt in program.statements:
        if isinstance(stmt, S.SubDef):
            current = stmt.name.upper() if stmt.keyword == "FUNCTION" else None
            continue
        if isinstance(stmt, S.Instruction) and (stmt.keyword.upper() in ("ENDFUNCTION", "ENDSUB")
                                                or re.match(r"(?i)\s*End\s+(Function|Sub)\b",
                                                            stmt.text or "")):
            current = None
            continue
        if current is None:
            continue
        exprs: list[S.Expr | None] = []
        if isinstance(stmt, S.Assignment):
            exprs.append(stmt.value)
        elif isinstance(stmt, (S.If, S.ElseIf, S.DoLoop)):
            exprs.append(stmt.cond)
        elif isinstance(stmt, (S.Instruction, S.Call)):
            exprs += list(stmt.args)
        elif isinstance(stmt, S.For):
            exprs += [stmt.start, stmt.end, stmt.step]
        if any(node.name.upper() == current for e in exprs for node in _index_nodes(e)):
            diags.error(stmt.line, "Cannot recursively call functions.", stmt.file)
