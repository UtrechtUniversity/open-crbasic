# Multi-logger differences: CR1000X, CR300, CR350

Evidence for `profiles.py`, `mode.py`, `checker/terminals.py`, and `data/<logger>/{mode,
terminals}.json`. All probes below were run 2026-09-25 against the real compilers under Wine
(`compiler/cr1000xcomp.exe` CR1000X.8.2.0, `cr300comp.exe` CR300.Std.11.02, `cr350comp.exe`
CR350.Std.1.07), either via `python3 tools/oracle.py` (cached, `tools/difftest.py`'s runs) or a
direct `WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all wine ../compiler/<exe> t.CR6` scratch probe (no
option flags, per the task's oracle note). CR6 rules and evidence stay in `docs/modes.md` and
`docs/checker.md`, owned by the concurrent checker workstream; this document only adds the other
three loggers plus the shared-infrastructure fix below.

## Coverage (`tools/difftest.py --logger <l>`, exact/verdict/mismatch)

Baseline (this session's start) / after this session's work. "chm" mismatch is the priority-1
metric (false accept, `docs/checker.md`'s convention); every logger's **false-reject** count (we
reject, the real compiler accepts -- the worse failure mode, AGENTS.md priority order) is reported
separately since `difftest.py` folds both directions into "mismatch".

| Logger | repo before | repo after | chm before | chm after | false-reject before | false-reject after |
|---|---|---|---|---|---|---|
| cr6 | 16/0/0 | 16/0/0 | 268/229/10 | 310/188/9 | 0 | 0 |
| cr1000x | 8/1/7 | 8/5/3 | 224/213/70 | 327/172/8 | 0 | 1 (see below) |
| cr300 | 0/3/13 | 0/8/8 | 92/335/80 | 93/396/18 | 0 | 0 |
| cr350 | 0/1/15 | 0/6/10 | 95/330/82 | 96/394/17 | 0 | 0 |

CR6's own movement (chm 268->310 exact) is the concurrent checker workstream's (`instr_rules.py`
etc.), not this session's; it is included only to show no regression. The one CR1000X false reject
(`tests/corpus/chm/structuretype_3.CR6`) is a pre-existing parser gap (`StructureType` block
members with no `As Type` clause aren't parsed as declarations), invisible on CR6 only because CR6
independently rejects the same file for an already-documented, unrelated reason (`docs/checker.md`
"Known remaining gaps": the unimplemented `GPS pps synchronization is only available on COMU1.`
check) -- CR1000X has no such restriction and so exposes the parser gap as a genuine mismatch. Not
fixed this session (general front-end parsing, not a logger difference; out of this session's
owned files); flagged for follow-up.

## Shared-infrastructure fix: `Const` folding used the wrong logger's symbols

`parser.parse_file`/`parse_text` computed their own profile from the file's extension
(`profiles.for_file`) and ignored any profile the caller had already resolved (`--logger`,
`compiler.compile_file`'s own `profile` argument). Every `.CR6` file -- all of this repo's and the
CHM corpus's programs -- therefore always folded `Const` declarations against the **CR6** symbol
table, even when compiling as CR1000X/CR300/CR350. Found via this session's own new false rejects
(see below): `ShuntLib.crb`'s `Const ShuntDiffChan = U1` folded to `201` (CR6's `U1` value) even
under `--logger cr1000x`, where `U1` is not a symbol at all (`docs/loggers.md` below) -- the
constant should be unresolvable, but instead silently carried CR6's value into every logger's
checker. Fixed by threading the caller's profile through `parser.parse_file(path, profile=...)` /
`parse_text(..., profile=...)` (defaulting to the old extension-based behaviour when omitted, so
every existing call site -- tests, `tools/tdftest.py` -- is unaffected) and `compiler.compile_file`
passing its own resolved profile in. This is very likely the single largest contributor to this
session's chm mismatch drop for CR300/CR350 (many `<lib>.crb> line N: X is not defined.` cascades
in the baseline were this bug, not a missing check).

## Output conventions

Already implemented (`profiles.py` `reports_mode`, `compiler.py`): CR6/CR1000X print `<file> --
Compiled in SequentialMode.` / `PipelineMode.`; CR300/CR350 print `<file> -- Compiled OK.`
(confirmed unchanged by every probe in this document). Failure format (`<file> -- Compile Failed!`,
blank line, `line N: <message>` lines, `Warning:` lines after success) is identical across all four
compilers.

**New finding this session:** the *text* of one common diagnostic differs by logger. An unresolved
predefined-symbol name at a checked expression position is worded `Invalid symbol in expression:
{name}.` on CR6/CR1000X but `{name} is not defined.` on CR300/CR350 (oracle: `PortSleep_tester.CR6`
under `--logger cr300`, `line 34: SW12_1 is not defined.` where CR6/CR1000X say `Invalid symbol in
expression: SW12_1.`; confirmed across 172/164 corpus `verdict` cases for cr300/cr350 respectively
before this fix). `{name} is not defined.` is in fact CR300/CR350's *general* unresolved-identifier
message -- oracle sampling across the whole chm corpus found 783 distinct `X is not defined.`
instances covering many contexts this project doesn't check at all yet (an instruction name that
doesn't exist on that logger, e.g. `ACPower is not defined.` since CR300 has no `ACPower`; a
function name; likely also `Const` initialisers, `docs/checker.md`'s own open question). This
session only applied the fix where it was a clean, minimal, already-profile-aware substitution:
`checker/expressions.py`'s one `diags.error` call now formats `profile.unresolved_symbol_message`
(new `Profile` field, `profiles.py`, defaulting to CR6/CR1000X's wording; CR300/CR350 override it)
instead of a hard-coded f-string. The broader pattern (CR300/CR350 likely collapsing several of
CR6's distinct messages, e.g. `structure.py`'s `Invalid, or out of place expression:` fallback, into
this same one) is recorded here for whoever next touches `structure.py`/`expressions.py`'s owned
message text, not implemented.

## Symbol filtering: the `mV200C` / symbol-record `extra` field question

Investigated and **not reproduced**, consistent with `docs/checker.md`'s existing note. Direct
probe (CR6): `X = mV200C` and `X = mv200c` in the same program both compile with no error --
`am25t_1.CR6`'s only diagnostic on the line using `mV200C` is for its separate `VX1` (CR1000X-only
terminal) argument, not the range constant. The symbol table's `extra` field (`docs/binary.md`) is
non-zero for exactly one record in each logger's table (`OSVERSION`, an obvious "value is a string
pointer, not a number" marker, not a visibility/acceptance flag) and zero everywhere else, across
all four loggers -- checked directly this session (`python3` over every `data/<logger>/symbols.json`).
No evidence found, in the corpus or by direct probe, of a logger rejecting a symbol present in its
own extracted symbol table.

## Mode decision (CR1000X): Ghidra evidence

Extracted `cr1000xcomp.exe` with a copy of `tools/ghidra/DumpAll.java` writing to
`/opt/re/out/decomp_cr1000x.c` (not committed, `docs/legal.md`; CR6's own dump at
`/opt/re/out/decomp.c` untouched). Found the equivalent per-statement mode hook, `FUN_0051df60`,
by searching for CR6's own five loop bounds' shape (`for (... < 9 ...)` next to a `< 0x40`-ish
second loop) -- structurally **identical** to CR6's `FUN_005239a0` (`docs/binary.md`): same five
lookup tables in the same order (9 "always sequential" ids; a switch-cased "task instruction" list
with the same three special-cased ids' *conditions* -- option-argument-equals-1, option-argument-in-
{0,2}, and an "outside slow sequence" flag; an id/arg-offset pair list for the "non-constant
argument forces sequential" rule; two 5-entry block-depth id lists), and an identical tail switch
(`SlowSequence`, `Scan`, three sign-bit-tested optional-argument cases, `NextScan`). Every raw id
CR1000X's version tests (`0x81`, `0xa7`, `0x12c`, `0x12d`, `1`, `2`, `0x41`, `0x42`, `0x43`, `0xd2`)
matches `data/cr1000x/instructions.json`'s id for the same-*named* instruction as CR6's version
(`SW12`=0x81, `Delay`=0xa7, `SerialOutBlock`=0x12c, `SerialInRecord`=0x12d, `SlowSequence`=1,
`Scan`=2, `CDM_SW12`=0x41, `CDM_SW5`=0x42, `CDM_SWPower`=0x43, `NextScan`=0xd2) -- i.e. this is the
*same* logic operating on CR1000X's own (differently-numbered) instruction table, not a modified
rule.

The only content difference: CR1000X's "task instruction" loop bound is `0x3d` (61) where CR6's is
`0x40` (64), and its "non-constant argument" loop bound is `9` where CR6's is `0xb` (11) -- and the
special dual-argument-position case CR6's version has for one id (`VibratingWire`'s 4th/5th
arguments) is absent entirely from CR1000X's version, not merely a case that never matches. Cross-
referencing CR6's 64/11-name lists (`docs/modes.md`) against `data/cr1000x/instructions.json`
explains exactly this: `ExciteI` and `VibratingWire` are **not CR1000X instructions at all** (2 of
CR6's 8 CR6-only instructions), accounting for the missing dual-argument case and both count drops
by themselves; `Resistance` (a 3rd CR6-only instruction, also absent from CR1000X) is the
remaining task-instruction-list difference (64-3=61 matches exactly; 11-2=9 matches exactly). No
other CR6-only instruction (`PanelTemp2`, `Resistance2W`, `Resistance3W`, `TCPsyc`, `Thermistor`)
is in either list, and CR1000X's one added instruction (`SWVX`) is in neither list on CR1000X
either (61 and 9 are exact subtractions, no addition). The `ALWAYS_SEQUENTIAL` (9), block-open (5)
and block-close (5) lists are identical in count and (by the same id-to-name cross-check on the
still-shared ids `1`/`2`) meaning.

**Conclusion:** CR1000X's *observable* Sequential/PipelineMode behaviour is identical to CR6's
under the existing name-based rule (`mode.py` already matches by instruction name, never by raw
id) -- the 3/2 "missing" names can never occur in a program CR1000X accepts anyway. `data/cr1000x/
mode.json` nonetheless encodes the real, reduced lists (not a copy of CR6's) so the data honestly
reflects what was verified, per `data/cr1000x/mode.json`'s construction (CR6's lists minus
`{ExciteI, VibratingWire, Resistance}` from `task_instructions` and minus `{ExciteI,
VibratingWire}` from `nonconstant_arg`; argument positions themselves were not re-derived from raw
bytes -- inferred from the shared front end plus this session's own spot check that CR1000X's and
CR6's `params` (type-code) lists are byte-identical for every mode-relevant instruction checked
(`VoltDiff`, `VoltSe`, `TCDiff`, `TCSe`, `ACPower`), medium-high confidence, not exhaustively
verified for every one of the 9 `nonconstant_arg` names).

CR300/CR350 never print a mode line (`reports_mode = False`); `mode.decide` still runs (its result
is simply discarded by `compiler.py`), so `data/cr300/mode.json` and `data/cr350/mode.json` are
**unverified copies of CR6's** -- kept only so `profile.mode_rules` resolves for every profile,
not RE-verified against either binary. Zero difftest impact either way since the value is never
shown.

## Terminal / channel model

`checker/terminals.py` + `data/<logger>/terminals.json` replace the CR6-only hard-coded
`U1..U12`/`[201,212]` check that `checker/instructions.py` used to apply, unconditionally, to every
logger (a "CR6-hard-coded assumption", item 1: the old code accepted a `profile` argument but never
used it). All four loggers validate the same four instructions' same argument position (`TCDiff`
`DiffChan`, `TCSe` `SEChan`, `VoltSe` `SEChan`, `VoltDiff` `DiffChan`, each the 4th argument --
`data/<logger>/instructions.json` confirms identical `params` type-code lists across loggers for
all four), but with different valid ranges, value semantics, and message text:

| Logger | DiffChan | SEChan | Message |
|---|---|---|---|
| CR6 | `U1..U12` only (raw value 201..212) | same | `Invalid channel {v}, must use U1..U12.` |
| CR1000X | plain int, `abs(v) <= 8` (0 and negative burst allowed) | plain int, `1 <= abs(v) <= 16` (0 invalid; negative burst allowed) | `Invalid channel {v}, must use 1..8.` / `...1..16.` |
| CR300 | plain int, `1..3` (a wider, unreplicated negative-burst allowance exists, see below) | plain int, `1..6` | `Channel {v} exceeds number of valid channels.` |
| CR350 | plain int, `1..2` | plain int, `1..4` | same as CR300 |

CR6 evidence: unchanged from `docs/checker.md`'s pre-existing rule (this session only moved it into
the shared, data-driven `checker/terminals.py`; behaviour is identical, `ACPower`'s known-incomplete
`(4, 7)` entry -- `docs/checker.md` -- carried over unmodified).

CR1000X evidence (`cr1000xcomp.exe`; `extracted_chm/parameters/diffchan.htm` is in fact CR1000X's
own CHM -- its example program and remarks are explicitly "CR1000X Series Datalogger", not CR6's,
despite `AGENTS.md`'s framing of `extracted_chm/` as CR6 reference; useful anyway since it matches
the compiler under test): `VoltDiff(...,1,...)` compiles; `...,9,...)` -> `line N: Invalid channel
9, must use 1..8.`; `...,-8,...)`/`...,0,...)` compile, `...,-9,...)` -> `Invalid channel 9, ...`
(shown value is `abs()`, confirming burst-mode negative channels, matching the CHM's "entered as a
negative value, all Reps are performed on the same channel" -- and 0 also compiles, hence `min=0`
not `min=1` for `diff_chan`). `VoltSe(...,17,...)` -> `Invalid channel 17, must use 1..16.`;
`...,16,...)` compiles. **Correction during this session:** an initial probe with `Reps=1` found
`VoltSe` channel `0` invalid and mistakenly assumed all non-positive/negative `SEChan` values were
rejected outright (`transform: "none"`); the CHM corpus's own `covspa_1.CR6` (`VoltSe(...,128,...,
-5,...)` through `-9`) is real, oracle-accepted CR1000X code and exposed this as a false reject
(`our_exit=1, their_exit=0`) once the channel check went live -- direct re-probe confirmed `SEChan
-1` also compiles (only `0` itself is invalid), so `se_chan` uses the same `abs()` transform as
`diff_chan`, with `min=1` (excluding zero) rather than `diff_chan`'s `min=0`. `TCDiff`/`TCSe` spot-
checked at one boundary each (9/17) and match `VoltDiff`/`VoltSe` exactly. `ACPower` intentionally
**not** ported to CR1000X's `terminals.json`: CR6's own `ACPower` entry is already documented as
incomplete (`docs/checker.md`: the corpus's one example gets 5 real "Invalid channel" lines, this
project's rule predicts 2), and guessing CR1000X's range without oracle confirmation risks the
opposite failure mode (a false reject on a genuinely valid channel) -- left unchecked per AGENTS.md's
priority order.

CR300/CR350 evidence (`cr300comp.exe`/`cr350comp.exe`): the ranges above were found by binary
search (`VoltSe`/`VoltDiff` with `mv2500` -- CR300/CR350's *only* two range codes are `mv2500` and
`mv34`, a real hardware fact, not missing data -- and successive channel values). Message text is
`Channel {v} exceeds number of valid channels.`, unrelated to CR6/CR1000X's wording, and (unlike
CR1000X) the **same message serves both SEChan and DiffChan positions and both control-port and
sensor terminals** -- CR300's raw channel *value* space is a flat 1..6 covering `C1, C2, SE1..SE4`
in that order (`data/cr300/symbols.json`: `C1`=1, `C2`=2, `SE1`=3 .. `SE4`=6), and a `VoltSe`
`SEChan` of `1` or `2` (nominally `C1`/`C2`) compiles without error -- the two control ports are
apparently also usable as single-ended analog inputs on this hardware. `VoltDiff`'s valid range
(`1..3`) is consistent with pairing that same 6-slot space into 3 differential channels
((C1,C2), (SE1,SE2), (SE3,SE4)). CR350 lacks the `C1`/`C2`-as-SE-input allowance (`SEChan` max is
4, not 6; `DiffChan` max is 2, not 3) -- its symbol table has the same `C1`=1/`C2`=2/`SE1..4`=3..6
numbering, but the extra 2 slots simply aren't valid channels on this hardware.

**Negative values on CR300/CR350 are deliberately left unchecked** (`skip_nonpositive: true` in
both loggers' `terminals.json`): probes found `VoltDiff` `DiffChan` `-1`, `-4` (both compile, even
though the positive range is `1..3`) and `-100` (fails, but shown as `Channel 99`, i.e.
`abs(v) - 1`, not the CR1000X-style `abs(v)`) -- boundary-tested to `-5` (fails, shown `4`) -- and
even `VoltSe` (a nominally non-burst parameter, no negative-channel mention in its own CHM page)
accepts `-1` without error. This is a real, distinct third validation shape (`abs(v) - 1` compared
against the positive max, apparently for *any* negative value on *any* channel-typed argument on
this logger family) that this session chose not to encode: every corpus mismatch this rule fixes is
a plain positive channel number written where a different range or terminal name was needed
(`docs/checker.md`'s own `TCDiff`/`VoltSe` evidence pattern, which this session confirmed still
holds for CR300/CR350), so the added complexity and residual uncertainty (is `abs(v)-1` truly
universal across every channel-typed argument on this hardware, or an artifact of the specific
instructions probed?) was not worth the false-reject risk of guessing wrong, per AGENTS.md's "never
falsely reject" priority. Left for a future session with a reason to chase burst-mode CR300/CR350
programs specifically.

## Warnings and value-range checks that differ by logger

Full rules and oracle evidence: `docs/instructions.md` (owned by the same workstream as this
file's terminal/channel section). Summary of what's per-logger there:

- **`Invalid fnotch value, must be 50,60 400 or 4000`**: CR300/CR350 only, a fixed-set restriction
  on 16 instructions' `fN1` filter-frequency argument; CR6/CR1000X accept any value in a continuous
  range instead (e.g. `PanelTemp(...,15000)` compiles clean on both).
- **`Scan Interval/BufferOption/Count parameter must be a non-negative integer`** and
  **`DataInterval TintoInt and Interval parameters must be non-negative`**: CR6/CR1000X only.
  Genuinely absent on CR300/CR350 (oracle: `Scan("fuzz",Sec,3,0)`, `Scan(-1,Sec,3,0)`, and
  `DataInterval(0,-3,Sec,0)` all compile clean on `cr300comp.exe`) -- found as a regression during
  this round's own development (the corpus's `hex_2.CR6`, already a documented CR6/CR1000X-
  rejected example, is real CR300/CR350-*accepted* code).
- **`When Main Sequence Scan %d is running, background calibration will be disabled.`**: CR6
  (threshold < 8ms) and CR1000X (threshold < 2ms, i.e. only its own minimum achievable 1ms
  granularity) only; CR300/CR350 never show it, oracle-probed down to a 100us scan interval.
- **`DataTable interval %d uSec not integrally divisible by scan interval %d uSec.`**: the one
  exception -- identical, message and all, on **all four loggers including CR300/CR350** (unlike
  the two non-negative checks above, which look similar but are CR6/CR1000X-only).
- **`%s never reached at run time.`** (reachability): identical condition and text on all four
  loggers (oracle-confirmed with the same minimal probe on each compiler).

## Extension / `--logger` mapping

Already implemented in `profiles.py` (`EXTENSIONS`, `for_file`) and `cli.py` (`--logger`); this
session added `tests/test_profiles.py` (extension-to-logger mapping for all four extensions
case-insensitively, unknown-extension default, `profiles.get` case-insensitivity and its `ValueError`
on an unknown name, every profile's data files loading, `compile_file`'s `profile` argument taking
priority over the file extension -- the regression this session's `parser.py` fix targets -- and
`cli.main`'s `--logger` flag end to end, both the CR300 `Compiled OK.` convention and an unknown
`--logger` value's error).

## CR300/CR350 agreement round (2026-09-26): const/assignment resolution, warning gating

Owned files this round: `checker/expressions.py`, `checker/__init__.py`, `profiles.py` (plus two
small, additive fields needed to support them, `syntax.py`'s `ConstDecl.value_span` and
`parser.py`'s one-line computation of it -- no behaviour change for anything that doesn't read the
new field). `tables.py`/`cli.py`/`checker/unused.py` untouched (owned by a concurrent workstream
this round). All findings oracle-confirmed 2026-09-26 with `python3 tools/oracle.py` and direct
`WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all wine ../compiler/<exe> t.CR6` scratch probes (no option
flags), against `cr300comp.exe`/`cr350comp.exe` and (for parity checks) `CR6Comp.exe`/
`cr1000xcomp.exe`.

Method: grouped every CR300/CR350 `verdict` case (`tools/difftest.py`'s `chm` corpus) by `(their
first message, our first message)`, then oracle-probed the largest groups with minimal scratch
programs. Two findings account for nearly all of the movement below.

### Finding 1: CR300/CR350 don't share CR6/CR1000X's undefined-name/variable-illegal mutual exclusivity

At a const-only-family argument position (`checker/expressions.py`'s `_CONST_ONLY_VARIABLE_REJECT_KINDS`,
type codes 1/5/38/39), CR6/CR1000X report *either* the undefined-name message *or* `Variable
illegal in Parameter: %s.`, never both (round 4's own finding, unchanged). **CR300/CR350 report
both**, in that order, whenever the argument contains any non-constant name reference (undefined
*or* a declared runtime variable) -- oracle transcripts (`TCDiff`'s `Range` argument, position 2,
type code 1):

```
TCDiff(Dest,1,BazQux,...)        cr300 -> BazQux is not defined.
                                          Variable illegal in Parameter: BazQux.
TCDiff(Dest,1,X,...)   (X live)  cr300 -> Variable illegal in Parameter: X.          (matches CR6/CR1000X's own rule)
TCDiff(Dest,1,BazQux+Corge,...)  cr300 -> BazQux is not defined.
                                          Corge is not defined.
                                          Variable illegal in Parameter: BazQux+Corge.
TCDiff(Dest,1,X+BazQux,...) (X live) cr300 -> BazQux is not defined.
                                          Variable illegal in Parameter: X+BazQux.
```
CR6/CR1000X on the same four programs (`BazQux` in place of a genuine CR6-valid range constant):
`Invalid symbol in expression: BazQux.` alone, `Variable illegal in Parameter: X.` alone,
`Invalid symbol in expression: BazQux+Corge.` alone, `Invalid symbol in expression: X+BazQux.`
alone -- confirming the mutual exclusivity really is CR6/CR1000X-specific, not a universal rule
this project had mis-scoped. Implemented as one unified computation in `expressions.py`'s
`_report_statement._check_position` (`trigger = names or var_refs`) that happens to reduce to the
pre-existing CR6/CR1000X behaviour automatically, since `names` is always empty by the time that
family reaches the second check (a non-empty `names` already returned for that family alone).

This one change accounts for the large `mv200C is not defined.` / `mv5000C is not defined.`
`verdict` groups (CR300/CR350's own symbol table lacks every CR6/CR1000X range constant except
`mv2500`/`mv34`, `docs/loggers.md` above -- so any corpus program using `mv200C`/`mv5000C` etc. at
`VoltDiff`/`VoltSe`/`TCDiff`/`TCSe`/`CurrentSe`'s `Range` argument hit exactly this shape) -- these
moved from `mismatch` (a silent false accept -- this checker only emitted the unrelated fnotch
warning, never noticing the range constant itself was undefined) to `verdict` or `exact`.

### Finding 2: `Const` initializers and plain assignments were never checked for undefined names at all

Neither `checker/expressions.py` (`Instruction` arguments only) nor any other checker module ever
walked a `ConstDecl.value` or an `Assignment.value` for an undefined name -- a real gap on **every**
logger, simply invisible on CR6/CR1000X in this project's own corpus because their symbol tables
are broad enough that it rarely comes up (found via this round's own CR300/CR350 repo-mismatch
chase, `crbasic/{FinApp,SBD,TDR,TString}_tester.CR6`'s shared libraries, each `Const <name> =
<CR6-only-terminal>`, e.g. `Const IridiumComPort = ComC3`/`Const TDRComPort = U5` -- terminals that
simply don't exist as symbols on CR300/CR350's smaller hardware).

- **`Const` folding is pass 1** (oracle: `Const Foo = BazQux` reports its own diagnostic *alongside*
  an unrelated, earlier `Public` with no name, `Variable not named.`, on all four loggers -- where
  an ordinary pass-2 finding would be completely suppressed by that same earlier pass-1 error). New
  `expressions.check_consts`, called unconditionally from `checker/__init__.py` alongside
  `structure.check_pass1`.
- **A plain `Assignment`'s RHS is pass 2**, gated exactly like the rest of `expressions.check`
  (oracle: the same `BazQux` in `wdog_timer = BazQux(0,Sec)` reports nothing at all once preceded by
  an unrelated `Variable not named.`). Added as a second loop inside `expressions.check` itself, so
  it shares that function's existing tier-B wiring/cutoff-truncation for free.
- Message family/shape for both is the same per-logger split already established (`_WHOLE_EXPR_ECHO_LOGGERS`):
  one whole-expression `Invalid symbol in expression: <text>.` line on CR6/CR1000X, one
  `<name> is not defined.` line per undefined name on CR300/CR350. The whole-expression echo for a
  `Const` is the value expression's own source text (`ConstDecl.value_span`, new field, same
  technique as `Instruction.arg_spans`); for an `Assignment` it is oracle-confirmed to be the
  *entire statement's* text, target and `=` included (`stmt.text`, already available, no new span
  needed) -- `Invalid symbol in expression:     wdog_timer = BazQux(0,Sec).`, not just the RHS.

This fully closed CR300's and CR350's repo-program `mismatch` count (8/10 -> 0/0: every one of
`crbasic/`'s 16 CR6-authored programs is now at least correctly *rejected* by both compilers, even
though the exact message text still differs further downstream for most of them -- these programs'
libraries reference enough CR6-only terminals/functions that the real compiler's own error cascade
runs for dozens of further lines this project does not attempt to reproduce line-for-line, per
`AGENTS.md`'s minimal-scope guidance). It also fixed one of CR1000X's three pre-existing repo
mismatches (a `Const`/library shape this session did not separately dig into beyond confirming the
same fix generalises).

### Finding 3: CR300/CR350 don't run `warnings.check_early` unconditionally

Round 3 documented `check_early` (reachability/fnotch/background-calibration) as "ungated -- fires
even when the file has other errors elsewhere", oracle-confirmed on CR6. That finding does not
transfer to CR300/CR350: an undefined-symbol-class error *anywhere in the file* (from tier A's new
`check_consts` or tier B's `expressions.check`) suppresses **every** `check_early` warning in the
whole file, including one on an earlier, otherwise-clean line:

```
Public PTemp / Public Dest
Scan(10,Sec,3,0)
  PanelTemp(PTemp,15000)                              ' fnotch=15000, invalid
  TCDiff(Dest,1,BazQux,1,TypeT,PTemp,True,0,15000,1,0) ' BazQux undefined, unrelated to PanelTemp
```
cr300: only `BazQux is not defined.`/`Variable illegal in Parameter: BazQux.` -- **no** fnotch
warning for `PanelTemp`, even though that line has nothing wrong with it on its own and textually
precedes the error. Reachability shows the identical suppression (a `Delay` after an infinite,
no-`ExitScan` `Scan`, plus an unrelated `X = BazQux` inside the scan body: the reachability warning
disappears too). A **non**-undefined-symbol-class error does *not* suppress it: `tablefile_1.CR6`
(corpus) keeps its own fnotch warning alongside an unrelated `Invalid drive or no drive specified`
error (a check this project does not implement). Modelled as: `check_early` on CR300/CR350 is
gated behind the *same* "tier A and tier B both clean" condition tier C already uses (consistent
with `tablefile_1.CR6`, since a tier-C-only error like the drive check by definition only occurs
once tier A/B are already clean) -- new `Profile.early_warnings_gated` (`False` for CR6/CR1000X,
`True` for CR300/CR350), and `checker/__init__.py`'s single `check_early` call site moved behind
that flag to either its existing early, unconditional position or the tier-C gate.

### Results (`tools/difftest.py --logger <l>`, `repo`+`examples`+`chm`, this round's own start/end)

| Logger | repo before -> after | chm exact before -> after | chm mismatch before -> after | false rejects |
|---|---|---|---|---|
| cr6 | 16/0/0 (unchanged) | 336 (unchanged) | 9 (unchanged) | 0 -> 0 |
| cr1000x | 8/5/3 -> 9/7/0 | 352 (unchanged) | 6 (unchanged) | 0 -> 0 |
| cr300 | 0/8/8 -> 0/16/0 | 120 -> 135 | 18 -> 16 | 0 -> 0 |
| cr350 | 0/6/10 -> 0/16/0 | 124 -> 139 | 17 -> 15 | 0 -> 0 |

`python3 -m pytest -q`: 949 passed both before and after this round's own changes (a later,
unrelated run in the same shared tree showed 957 passed/0 xfailed once the concurrent `tables.py`
workstream progressed further -- not this round's own effect, confirmed by `git diff --stat`
touching only `checker/expressions.py`/`checker/__init__.py`/`profiles.py`/`syntax.py`/`parser.py`).
`python3 tools/fuzz_replay.py --workers 2` (357 reproducers): 41 fixed, 48 changed class (several
`false_accept -> message_diff` on cr300/cr350, matching Finding 1/2 above), 0 new `false_reject`
(the two `still false_reject` entries, both cr350, are the same pre-existing ones round 4 already
attributed to an unrelated gap).

### Remaining gaps (not attempted this round, evidence only)

The `chm` corpus's remaining CR300/CR350 `verdict`/`mismatch` cases are a long, low-frequency tail
once Findings 1-2 are applied (`docs/loggers.md`'s own grouping method above finds no other group
above ~10 files): a `SerialOpen`-style instruction's own `ComPort` argument gets three further,
specific diagnostics this project does not implement (`ComPort %d illegal.`/`Parameter must be a
constant integer: %s`, oracle: `avw200_1.CR6`, `COMC1` used as `SerialOpen`'s first argument);
`Line exceeds max characters of 512.` interacting with something CR300/CR350-specific (`maxrun_1.CR6`);
several instructions absent from CR300/CR350 entirely whose bare-call syntax the parser currently
folds into the generic "Expr" fallback rather than the per-name `is not defined.` shape
(`DisplayMenu`/`UDPSocketOpen`/`I2COpen`/`I2CWrite`/`CardOut`/`PortPairConfig`, all `mismatch`es
where the instruction exists on CR6 but not CR300/CR350 -- these are calls, not `Instruction`
records this checker recognises, since `profile.instructions` doesn't have the name, so they fall
through to `structure.check_pass2`'s bare-call handling instead of `expressions.check`'s per-
argument walk); and a handful of single-file `Units`/`Const`-with-no-assignment truncation-message
differences (`The is not defined.` vs. this project's own `Need '=' in units definition:`/`Variable
not named.` on malformed CHM-prose-as-code examples, likely a parser-level tokenisation difference
on CR300/CR350 specifically, not chased).
