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
on CR300/CR350 specifically, not chased). **Both of the two named gaps above were closed this
round** -- see "Round 6b" below.

## Round 6b (2026-09-26): undefined bare calls, `SerialOpen`'s `ComPort` argument

Owned files this round: `checker/cr3xx.py` (new), `data/cr300/`, `data/cr350/`, `profiles.py` (one
additive field), `docs/loggers.md`. One shared-module edit, `checker/structure.py`
(`_check_call_pass2`), gated behind a new, minimal `Profile.undefined_call_message` flag rather than
a broader rewrite (`checker/__init__.py`'s own tiny wiring line for the new `cr3xx.check` is
described in that module's own docstring). Method: grouped every CR300/CR350 `chm` `verdict` case
by `(their first message, our first message)` (same technique as the agreement round above), then
oracle-probed the two largest groups with minimal scratch programs
(`python3 tools/oracle.py`/direct `wine cr300comp.exe`/`cr350comp.exe` scratch probes, no option
flags).

### Finding 1: an undefined bare call gets its own `{name} is not defined.` line first, and never the before-`BeginProg` follow-up/stop

The single largest CR300/CR350 `verdict` group by far (187/300 and 190/374 files respectively,
`(NAME is not defined., Invalid, or out of place expression: ...)` after generic-ising every
digit/instruction-name): a bare call (`Name(args)`/`Name`, no `Call` keyword) whose head name
resolves to nothing this project's own `_check_call_pass2` already recognises (not a declared
`Sub`, not a built-in function, not a recognised instruction -- most often an instruction this
logger's own smaller table lacks entirely, e.g. `DisplayMenu`/`I2COpen`/`I2CWrite`/`CardOut`/
`PortPairConfig`, or a genuinely nonexistent name) gets an extra `{name} is not defined.` line
*before* the existing generic `Invalid, or out of place expression: %s.` fallback line, oracle-
confirmed identical on both loggers:

```
DisplayMenu("t",1)   (unresolved on CR300/CR350) ->
  line 4: DisplayMenu is not defined.
  line 4: Invalid, or out of place expression:     DisplayMenu("t",1)
  .
```
(the trailing `\n.` -- the closing period on its own line -- is a further, smaller formatting
difference in how CR300/CR350 echo this one message's own argument text, not chased this round;
see "Not implemented" below.) Confirmed for every one of the six instructions the earlier round's
own "Remaining gaps" note named (`DisplayMenu`, `UDPSocketOpen`, `I2COpen`, `I2CWrite`, `CardOut`,
`PortPairConfig`) plus a fresh, deliberately-nonexistent name (`FooBarBaz`) and the corpus's own
`ACPower`/`TableFieldNames`/`MovePrecise`/`AddPrecise`/`ArgosError`/`HydraProbe` -- all identical
shape. **CR6/CR1000X are unaffected**: the same probes against `CR6Comp.exe`/`cr1000xcomp.exe`
show only the existing fallback line, no `{name} is not defined.` line (`Invalid symbol in
expression: %s.` is CR6/CR1000X's own wording for this concept, and it is never used for this
particular call shape on those two loggers, unchanged).

**A second, independent difference in the same branch**: the existing before-`BeginProg` placement
follow-up (`Expr illegally occurs before BeginProg.`) and pass-2 stop -- both oracle-confirmed real
and correct for CR6/CR1000X on this exact shape (`docs/checker.md` "Pass model") -- **do not fire
at all on CR300/CR350** for this one branch, even when the statement genuinely precedes
`BeginProg`, and pass 2 does not stop (oracle, three independent probes): a bare `FooBarBaz(1,2)`
placed before `BeginProg` gives only `FooBarBaz is not defined.`/`Invalid, or out of place
expression: FooBarBaz(1,2).` and then *keeps going*, reporting a later, unrelated `Too many
parameters: Sec.` on a subsequent line; the corpus's own `Tablefieldnames_2.CR6` (`NOTE:` used as a
bare call before `BeginProg`, a CHM-prose artifact) and `DoublePrecArith_1.CR6` show the identical
pattern. **The other two branches of the same check are unaffected** -- oracle-confirmed
identical to CR6/CR1000X on both loggers: a bare call resolving to a declared `Sub` still gets
`Call illegally occurs before BeginProg.` before `BeginProg` (probe: a declared `Sub` called bare
before `BeginProg`), and an explicit `Call NoSuchSub` still gets the unchanged `Sub %s not
defined.` alone, no extra line, before `BeginProg` or not.

Implemented as `Profile.undefined_call_message` (`profiles.py`, `False` default, `True` for
cr300/cr350) and a single gated branch inside `structure.py`'s `_check_call_pass2`, in the
"resolves to nothing" case only -- the other two branches (`Sub`/function) are untouched. Result
(`tools/difftest.py --logger <l> --show 0`, `chm`, before this finding's own fix / after): cr300
135/359/13 -> 138/358/11 exact/verdict/mismatch; cr350 139/356/12 -> 142/355/10. (The 2-file
`mismatch` improvement on each logger, ahead of Finding 2 below, is this finding's own residual
effect on files that also touch `SerialOpen`; not chased apart from Finding 2's own fix.) The
"NAME is not defined." vs. "Invalid, or out of place expression" `verdict` group itself shrank from
187 to 81 files (cr300) and 190 to 81 (cr350) -- the remainder are CHM-prose-as-code artifacts
(`Unparsed`, not `S.Call`, several undefined *words* per physical line, e.g. `DoublePrecArith_1.CR6`
line 2's `To force double-precision arithmetic, ...` -- deliberately not chased, see "Not
implemented" below) or a compound expression this project's own single-name `stmt.name` head does
not fully cover.

### Finding 2: `SerialOpen`'s own `ComPort` argument (position 0) needs a genuine compile-time constant

`SerialOpen` (and every other `Serial*` function: `SerialClose`/`SerialFlush`/`SerialOut`/
`SerialIn`/`SerialInBlock`/`SerialInChk`/`SerialBrk`) is a *function* (`data/<logger>/
functions.json`, `kind: 309`), not an `S.Instruction` -- called as a bare statement, its arguments
were never checked for undefined names at all (a real, general gap in this project's own scope,
`docs/checker.md`: `expressions.check` only ever walks a recognised `S.Instruction`'s own
arguments, "never ... a `Sub`/`Function` argument list's own runtime scoping"). Oracle
(`avw200_1.CR6`/`acceptdatarecords_2.CR6`, both real corpus programs using `SerialOpen(COMC1,...)`
-- `COMC1` a genuine CR1000X-only symbol absent from CR300/CR350's own smaller symbol table,
`docs/loggers.md` above): `SerialOpen`'s own `ComPort` argument (position 0) uniquely, among every
`Serial*` function checked this round, additionally requires a genuine compile-time constant:

```
SerialOpen(COMC1,...)          (COMC1 undefined)      -> COMC1 is not defined.
                                                           ComPort 0 illegal.
                                                           Parameter must be a constant integer: <stmt.text>
                                                           .
SerialOpen(MyPort,...)  (MyPort a declared Public)      -> ComPort 0 illegal.
                                                           Parameter must be a constant integer: <stmt.text>
                                                           .
SerialOpen(ComRS232,...)         (a real, defined port) -> compiles clean
SerialClose(BadCom)/SerialFlush(BadCom)/SerialOut(BadCom,...)
                                (undefined arg, same position) -> BadCom is not defined. (alone --
                                                           no `ComPort .../Parameter must be a
                                                           constant integer` follow-up on these)
```
i.e. the trigger for the extra two lines is "not a compile-time constant at all" (an undefined
name *or* a live declared variable, not "undefined" specifically), unique to `SerialOpen` among
this family -- every other `Serial*` function's same-position argument gets only the plain
undefined-name line, oracle-confirmed individually for `SerialClose`/`SerialFlush`/`SerialOut`.
**Deliberately not implemented**: a genuine constant that is merely out of range (oracle:
`SerialOpen(99,...)` -> `ComPort 99 illegal.` plus a *different* second line, `No Matching, or out
of place ) in expression: ...`, not the "must be a constant integer" line) -- CR300/CR350's own
valid `ComPort` value set was not oracle-pinned down this round (their smaller hardware's own
comm-port symbol table, `Com1`/`ComRS232`/`ComUSB`/`ComRF`/... plus `Com2`/`Com3` on CR350, gives no
obvious small integer range to bound-check against), and guessing it risks flagging a value this
project cannot confirm is actually invalid (AGENTS.md: never guess into a false reject) --
`checker/cr3xx.py`'s own module docstring has the full evidence and the same reasoning.

Implemented in the new `checker/cr3xx.py` (a no-op on every logger but cr300/cr350, so
`checker/__init__.py`'s own call site is unconditional), reusing `expressions._known_names`/
`_undefined_names`/`_variable_refs` and `symbols.declared_variable_names` rather than
reimplementing name resolution. Oracle-confirmed **ungated** (fires alongside unrelated errors
elsewhere in the same file, same as `expressions.check_consts`): `acceptdatarecords_2.CR6`'s own
`SerialOpen(COMC1,...)` diagnostic (line 10) prints together with a separate, later
`CallTable`-shaped issue (line 15) in the same file, both present in the oracle transcript.

Result (`tools/difftest.py --logger <l> --show 0`, `chm`, after Finding 1 / after both findings):
cr300 138/358/11 -> 138/358/11 exact/verdict unchanged, `mismatch` 13 -> 11 (2 files: both real
`SerialOpen(COMC1,...)`-using corpus programs moved from a silent false accept to a correct
reject); cr350 142/355/10, `mismatch` 12 -> 10, same two-file effect. Combined round total (before
this round's start / after both findings): cr300 135/359/13 -> 138/358/11; cr350 139/356/12 ->
142/355/10. Zero false rejects throughout on all four loggers
(`their_exit==0 and our_exit!=0`, checked after every step); cr6/cr1000x completely unaffected
(both changes are cr300/cr350-only, either by the new module's own logger check or the
`undefined_call_message` flag's default). `python3 -m pytest -q`: 963 passed throughout (0 failed).

Fixed-seed fuzz (`tools/fuzz.py --logger cr300/cr350 --count 300 --seed 42/43`), before this
round's start / after both findings, `false_reject = 0` throughout except the one already-
documented, pre-existing `cr350` seed-43 flake (`docs/fuzz-findings.md` "4."), unchanged by this
round: cr300 seed42 false_accept 27->27 (exact 210->210), cr300 seed43 33->33 (203->203), cr350
seed42 33->33 (exact 201->202), cr350 seed43 26->26 (exact 207->208, one pre-existing flake). The
random fixed-seed fuzz corpus does not happen to exercise either of this round's two fixed shapes
often (both are corpus-representative but low-frequency per any single random seed of 300
mutants), so the raw false-accept *count* barely moves even though the underlying `chm`-corpus
groups they target are large; the `chm` corpus numbers above are the more representative measure
for this specific pair of fixes. The remaining `false_accept` reproducers from this round's own
fresh fuzz runs are a long, low-frequency, scattered tail (largest group found, `Invalid fnotch
value` warning interacting with an unrelated `SDI12Recorder`/reps-argument gap, 7 of 119 combined
reproducers) not owned by this workstream's files (`checker/warnings.py`/`instr_measure.py`) and
not chased this round.

### Not implemented (this round's own scope)

- **The trailing-newline echo quirk** (`Invalid, or out of place expression: %s.`'s own argument
  text, and `Parameter must be a constant integer: %s`'s, both include the statement's own raw
  source line's trailing newline before the closing period when nothing else follows on that
  physical line, unlike CR6/CR1000X's own version of the first message and unlike either logger's
  `Sub %s not defined.` -- same *general* quirk `docs/checker.md` already documents for `Units`,
  applied to two further messages this round found it also applies to) -- implemented directly in
  `checker/cr3xx.py`'s own new "Parameter must be a constant integer" message (isolated, low risk),
  left unimplemented in the shared `structure.py` fallback line touched by Finding 1 (higher risk of
  disturbing CR6/CR1000X's own, unaffected version of the same call site; only ever costs an
  "exact" vs. "verdict" precision loss, never a wrong verdict).
- **CHM-prose-as-code `Unparsed` statements' own per-word `is not defined.` echoing** (Finding 1's
  own residual 81/81-file group): the real compiler echoes each undefined *word* of a malformed,
  multi-word line as its own diagnostic (`This example shows...` -> `This is not defined.`/`example
  is not defined.`, apparently stopping after the first two words and falling back to one whole-line
  echo for the rest) -- reproducing this exactly would need re-tokenising already-malformed
  `Unparsed` text to find bare-identifier-shaped words, a meaningfully riskier and more speculative
  change than Finding 1's own clean `S.Call` case, and these are CHM documentation-prose artifacts
  the corpus contains for historical-example reasons, not real programs (`AGENTS.md`: `examples/`
  and, by the same reasoning, the CHM prose-as-code corpus rows are not to be over-fitted).
- **`SerialOpen`'s own valid `ComPort` numeric range/message** -- see Finding 2 above.

## Round 7b (2026-09-26): `PortSet`/`PortGet`/`SDI12Recorder` port ranges, the undefined-name cascade

Owned files this round: `data/cr300/terminals.json`, `data/cr350/terminals.json` (pure data, no
code change -- `checker/terminals.py` already generalises via this data for CR6/CR1000X's own
`port_c1c4_u1u12`/`port_c1c8`/`sdi_port*` kinds), `checker/cr3xx.py` (one new function), one
additive line plus docstring update in `checker/__init__.py` (a shared module -- see "The
undefined-name cascade" below for why this needed a wiring line, not just data). Method: `tools/
fuzz.py --logger cr300/cr350 --count 300 --seed 42/43/44/45` (this round's own fresh baseline, since
the task brief's own seed42/43 counts did not reproduce exactly on this box -- see "Measured
impact"), then direct oracle probing (`tools/oracle.py`'s `run_oracle`, scratch programs, no CLI
flags) for `PortSet`/`PortGet`/`WriteIO`/`SDI12Recorder`/`SerialOpen`'s `BaudRate`/
`SerialOpenFormat`, plus a `difftest-cr300.json`/`difftest-cr350.json` first-message grouping
(`(their first diagnostic, our first diagnostic)`, generalising digits/quoted text) to find the
largest remaining `verdict`/`mismatch` groups.

### Finding 1: `PortSet`/`PortGet`'s `Port` and `SDI12Recorder`'s `SDIPort` have real, oracle-confirmed, per-logger ranges -- and they are not simply "C1..C8"

The generic CHM help page for `PortSet`/`PortGet` lists `C1`..`C8` for every logger, and
`SDI12Recorder`'s own page lists `C1`,`C3`,`C5`,`C7` (odd control ports) -- neither is what CR300 or
CR350 actually accept. Oracle swept (binary search + exhaustive small-range sweep, both loggers,
`PortSet(<v>,1)` / `PortGet(y,<v>)` / `SDI12Recorder(sd(),<v>,"0","M!",1,0)`):

```
cr300 PortSet  Port:   valid 1..102 (inclusive), invalid 0, negative, 103+        -- "Invalid port specified %d"
cr300 PortGet  Port:   valid (-inf, 8]     (no lower bound at all, tested to -999999999999), invalid 9+
                                                                                    -- "Invalid port specified %d"
cr350 PortSet  Port:   valid {1..7, 9..12} (gap at 8), invalid 0, negative, 8, 13+ -- "Invalid port specified %d"
cr350 PortGet  Port:   identical set to cr350 PortSet (same message)
cr300/cr350 SDI12Recorder SDIPort: valid {1, 2} only, invalid everything else      -- "Invalid port %d, must be 1 or 2"
```
`PortSet`'s and `PortGet`'s different ranges on the *same* logger (cr300: 1..102 vs. (-inf,8]) is
real, not a probe error -- reconfirmed with a second, independent sweep. Cross-checked against each
logger's own extracted `symbols.json`: cr350's gap at 8 lines up exactly with its own named-symbol
table (`C1`=1..`P_SW`=7, then a *different* symbol `P_LL`=8, then `SW12_1`=9..`VX2`=12 -- 8 is
genuinely "no port lives there"); cr300 has no such gap in its own symbol table either (`C1`=1
through `P_SW`=8, `P_LL`=9), so cr300's 1..102 upper bound and cr350's 12 are each real but not
derivable from the symbol table alone -- both were oracle-pinned by binary search, not guessed
(AGENTS.md: hardware identity/range is exactly the kind of fact that must be observed, not
inferred). `SDI12Recorder`'s `{1,2}` (not "odd only", unlike CR6/CR1000X) is likewise oracle-pinned,
identical on both loggers, with its own distinct message text (no trailing period, unlike CR1000X's
`sdi_port_cr1000x` message which has one). Implemented as three new `terminals.json` channel kinds
per logger (`port_1_102`/`port_le8`/`sdi_port_1_2` for cr300, `port_gap8_12`/`sdi_port_1_2` for
cr350) wired to `PORTSET`/`PORTGET`/`SDI12RECORDER` -- pure data, `checker/terminals.py`'s existing
`_ranges`/`_in_range` already handles a disjoint `ranges` list and (verified this round) a `-
Infinity` bound (Python's `json.loads` accepts it by default; `-2147483648` was rejected as a stand-
in because cr300's own `PortGet` genuinely has no lower bound at all, evidenced past
`-999999999999`, so a finite floor risks a false reject a real program's own arithmetic expression
could still reach).

**Not implemented, evidence only**: `SerialOpen`'s own `BaudRate`/`SerialOpenFormat` arguments also
have real, oracle-confirmed per-logger illegal values (`%d is illegal baud rate.`, cr300 rejects 300
and 600 baud but accepts 1200 upward including some values absent from the CHM's own documented
list such as 230400; cr350 accepts 300 but still rejects 600; both reject every *negative* baud
rate outright, unlike the CHM doc's own "negative enables autobaud" claim -- real corpus evidence,
`tests/corpus/chm/sendvariables_2.CR6`'s own `-9600 is illegal baud rate.`) -- but this message
triggers the exact same downstream cascade described in Finding 2 below, and this round did not
finish mapping the full legal `BaudRate` set (only the documented list plus a few extra probes),
so it is deliberately left unimplemented rather than guessed into a false reject on a legal baud
rate this round didn't test.

### Finding 2: the undefined-name cascade -- a spurious `Too many parameters:  %s.` on the *next* statement

The single largest remaining CR300/CR350 `chm` `verdict` group by far (grouped by `(their first
diagnostic, our first diagnostic)`, generalising digits: e.g. `(mv5000C is not defined. / Variable
illegal in Parameter: mv5000C., <same>)`, 42-44 files per logger -- more than every other group in
Finding 1's own earlier round combined): whenever an undefined-name-class error fires anywhere in
tier B (`docs/checker.md` "Pass model") on CR300/CR350 -- an instruction argument, or (per the
"CR300/CR350 agreement round" above) a plain `Assignment`'s RHS -- the *next* statement gets a
spurious extra diagnostic of its own, `Too many parameters:  <its own second argument's verbatim
source text>.`, *if and only if* that next statement is a bare instruction call written with two or
more arguments and is not already erroring for some other reason. Corpus example
(`tests/corpus/chm/SWVX_1.CR6`, real file, both loggers):

```crbasic
VoltSe(AirTC,1,mv5000C,2,0,0,15000,0.1,-40.0)   ' mv5000C undefined
SWVX(VX1,0,1)                                    ' otherwise completely valid, standalone-clean
```
```
line 23: mv5000C is not defined.
line 23: Variable illegal in Parameter: mv5000C.
line 24: Too many parameters:  0.                ' SWVX's own SECOND argument, "0" -- spurious
```
Oracle-isolated with a dozen minimal scratch probes (`cr300comp.exe`; not reprobed against
`cr350comp.exe` beyond the corpus files above and Finding 1's own shared-message confirmation, but
the corpus evidence covers both loggers identically) to find the exact trigger and search rule,
each confirming or ruling out one hypothesis:

- `WriteIO(1,2)` compiles clean **standalone** -- the extra line is not that instruction's own
  argument-count problem; it only appears immediately after an unrelated undefined-name error.
- The *kind* of preceding error does not matter: a `VoltSe`/`PortSet` *instruction-argument*
  undefined name and a plain **`Assignment`'s RHS** (`x = FooBarUndefined + 1`) both trigger it
  identically -- this is not confined to `expressions.check`'s instruction-argument loop, it is
  shared with its assignment-RHS loop too (both are tier B, `docs/checker.md`).
- The echoed text is the raw, verbatim second-argument source span (`PortSet(1,y+1)` ->
  `Too many parameters:  y+1.`), not a re-derived or normalised form -- the same "echo the source
  span, not the value" convention `checker/expressions.py`'s own `_arg_text` already implements and
  now reuses.
- The search **skips forward** over statements that don't qualify -- a clean plain assignment
  (`x = 5`) or a clean one-argument call (`Battery(BattV)`) in between is neither flagged itself nor
  a stopping point; the cascade still lands on the next two-argument call after it. A `'comment`
  line in between is skipped for free (comments are never `program.statements` entries).
- When **two** statements in a row each have their own undefined-name error, only **one** cascade
  line appears in total, anchored after the *later* of the two -- not one per failing statement.
- **Nothing follows** (the failing statement is the last one before `NextScan`) -> no cascade at
  all; correctly, nothing spurious is invented.
- **CR6/CR1000X do not share this**: the identical shape (`VoltSe(AirTC,1,BadConstXYZ,...)` then
  `WriteIO(1,2)`) against `CR6Comp.exe` shows only the expected `Invalid symbol in expression:
  BadConstXYZ.`, no extra line.

Implemented as `checker/cr3xx.py`'s new `check_expr_cascade` (CR300/CR350 only, a no-op on every
other logger): finds the *last* statement in `program.statements` whose own line already carries a
`"... is not defined."` or `"Variable illegal in Parameter: ..."` diagnostic, then scans forward
from there for the first statement that is (a) an `S.Instruction`, (b) has two or more parsed
arguments with the second one present, and (c) does not already have a diagnostic on its own line
-- skipping (never stopping at) anything that fails (a)-(c) -- and appends `Too many parameters:
<_arg_text(stmt, 1)>.` there. This could not live in `cr3xx.check`'s existing, earlier call site
(step 3 of the pipeline, which runs *before* `expressions.check` even executes, and ungated, so it
can never see the diagnostics this new check depends on) -- it needed a new call site inside the
existing `if not diags.errors:` tier-B block in `checker/__init__.py`, right after `expressions.
check`/`instr_data.check_unclosed_calltable` and before that block's own placement-error cutoff
truncation (so a cascade line past a cutoff is truncated the same as every other tier-B finding,
untested but the conservative default). This is the one shared-module edit this round made outside
`checker/cr3xx.py`/`data/cr300/`/`data/cr350/`/`profiles.py`: one new `_run_pass` call plus a
docstring update, no restructuring, gated entirely by `cr3xx.check_expr_cascade`'s own internal
`profile.name not in ("cr300", "cr350")` guard rather than a new `Profile` field (no per-logger
*parameter* varies, only whether the function is a no-op, so a boolean field would have added a
layer of indirection for no benefit).

**Not fully chased this round** (evidence only, safe to leave for a future session since under-
triggering only costs precision, never a wrong verdict -- both sides already reject every file this
touches):
- Whether a placement error (`structure.check_pass2`'s own before-`BeginProg` finding) or an
  unclosed-`DataTable` finding (`instr_data.check_unclosed_calltable`) also anchors the same
  cascade the way an undefined-name error does -- not oracle-probed this round.
- Whether the cascade can anchor on `cr3xx.check`'s own, separately-ungated `SerialOpen`/`ComPort`
  finding -- oracle-confirmed **impossible** in this checker's own pass model regardless (that
  finding fires before the `if not diags.errors:` gate is even evaluated, so tier B, and this new
  function with it, never runs once it has fired) -- but whether the *real* compiler's own cascade
  mechanism behaves the same way in that specific combination was not independently oracle-checked.
- The exact legal `BaudRate` set for `SerialOpen` (Finding 1's own "not implemented" note) -- would
  reuse this same cascade once implemented, since its own trigger (`%d is illegal baud rate.`) was
  confirmed to cascade identically (`sendvariables_2.CR6`'s own transcript in Finding 1 shows the
  same `Too many parameters:` chain, four lines deep in that particular file since it echoes
  through several subsequent statements' own arguments in turn -- consistent with "skip forward to
  the next qualifying statement" applied repeatedly, but not independently re-verified for a chain
  longer than two).

### Measured impact

`tools/difftest.py --logger <l> --show 0` (`chm`, exact/verdict/mismatch), before this round's
start (re-measured fresh, matching the prior "Round 6b" end state) / after both findings (Finding 1
+ Finding 2 together -- Finding 1 alone did not move any of these three counts, since no corpus
file happened to combine an out-of-range `PortSet`/`PortGet`/`SDI12Recorder` port with an otherwise-
clean file; its value is fuzz-corpus-representative, not `chm`-corpus-representative, unlike Finding
2):

| Logger | chm before | chm after |
|---|---|---|
| cr300 | 138/358/11 | 157/339/11 |
| cr350 | 142/355/10 | 161/336/10 |
| cr6 | 336/162/9 (unaffected, unchanged) | 336/162/9 |
| cr1000x | 352/149/6 (unaffected, unchanged) | 352/149/6 |

`repo`/`examples` unchanged on all four loggers throughout (cr300/cr350 `0/16/0` and `2/2/0`; cr6
`16/0/0` and `2/2/0`; cr1000x `9/7/0` and `3/1/0`). Zero false rejects on all four loggers
throughout (`their_exit==0 and our_exit!=0`, checked after every step, empty every time).
`python3 -m pytest -q`: 963 passed, 311 skipped throughout (0 failed, no change in count).

Fixed-seed fuzz (`tools/fuzz.py --logger cr300/cr350 --count 300 --seed 42/43/44/45`), this round's
own fresh before/after (the task brief's own seed42/43 baseline, 27/33 for cr300 and 33/26 for
cr350, did not reproduce exactly on this box -- plausibly upstream drift between when that baseline
was taken and this round's own start, since this round made no changes before taking its own first
measurement; the numbers below are this round's own internally-consistent before/after, both
measured on the same box back to back):

| Logger | seed | false_accept before | after | false_reject before | after |
|---|---|---|---|---|---|
| cr300 | 42 | 21 | 21 | 0 | 0 |
| cr300 | 43 | 32 | 31 | 0 | 0 |
| cr300 | 44 | 22 | 21 | 1 (pre-existing, see below) | 1 (unchanged) |
| cr300 | 45 | 18 | 18 | 1 (pre-existing, see below) | 1 (unchanged) |
| cr350 | 42 | 30 | 29 | 0 | 0 |
| cr350 | 43 | 22 | 22 | 1 (pre-existing, `docs/fuzz-findings.md` "4.") | 1 (unchanged) |
| cr350 | 44 | 26 | 25 | 2 (pre-existing, see below) | 2 (unchanged) |
| cr350 | 45 | 28 | 28 | 0 | 0 |

Every `false_reject` present is pre-existing and unaffected by this round's changes (identical
count before/after on the same seed, and traced to files unrelated to `PortSet`/`PortGet`/
`SDI12Recorder`/the cascade -- e.g. cr300 seeds 44/45's own reproducers are both `ESSInitialize
("private,<fuzzed-text>)` shapes, a malformed quoted-string-plus-stray-`)` argument the real
compiler still accepts, a general lexer/parser quirk unrelated to any CR300/CR350-specific rule and
outside this workstream's owned files). The raw `false_accept` count barely moves per seed (this
round's own two fixes are each individually low-frequency in any single random 300-mutant sample,
the same pattern Round 6b's own fuzz measurement documented) -- `exact`/`hang` counts on repeated
runs of the *same* seed also drift by a few (observed hang counts: 0, 1, 4, 5, 6, 7 across repeated
runs of identical seeds with no code change in between), consistent with this project's own
documented worker-contention flakiness (`docs/fuzz-findings.md`), not a regression; the `chm`-corpus
exact-count jump above (+19 on each logger) is the more representative measure of this round's own
impact, the same conclusion Round 6b drew for its own two fixes.

## Round 8 (2026-09-27): leading-word `%s is not defined.` scan for the generic fallback

Motivation: many `chm` corpus `verdict` cases (CR300/CR350) are CHM help-page prose accidentally
left uncommented (e.g. `To force double-precision arithmetic, declare ...`), which parses as
`S.Unparsed` and hits `checker/structure.py`'s generic `Invalid, or out of place expression: %s.`
fallback on every logger. CR300/CR350 additionally print one `%s is not defined.` line per leading
bare word of the statement's own source text, ahead of the generic line -- this project did not
model that at all (it printed only the generic line).

### Finding 1: the leading-word walk itself

Oracle-probed this round (scratch `t.cr6`, `WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all wine
../compiler/<cr300comp.exe|cr350comp.exe> t.cr6`, no option flags; both compilers gave
byte-identical results on every probe below, so only one is shown):

| Probe | Result |
|---|---|
| `foo` | `foo is not defined.` |
| `foo bar` | `foo is not defined.` / `bar is not defined.` |
| `foo bar baz`, `foo bar baz qux` | `foo`/`bar` only -- `baz`/`qux` never checked |
| `alpha-beta-gamma delta` | all four flagged (`alpha`/`beta`/`gamma`/`delta`) |
| `foo, bar` | `foo` only -- a comma grants no further check |
| `Part 1.` | `Part` only -- the next token must be a bare word, not a number |
| `NOTE.` | `NOTE` only |
| `This is not defined test` | `This` only -- `Is` is a real CR300 token (`functions.json`) |
| `Double foo` | `foo` only -- `Double` is a real CR300 token (`symbols.json`) |
| `foo Double bar` | `foo` only -- `bar` never reached |
| `BattV foo`, `foo BattV bar baz` | both words flagged in each (plain, undeclared identifiers) |

Model (implemented as `checker/cr3xx.py::_leading_unknown_words`, unit-tested in
`tests/test_cr3xx.py`): from the statement's own first bare word, walk a chain of words joined only
by an immediately-adjacent operator run (`+-*/^&<>=`, no surrounding space -- checking every
operand, uncapped); once that chain can no longer continue via an operator, check exactly one more
bare word if it is separated from the chain by plain whitespace only (not a comma or other
punctuation, and it must itself be a word, not a number); either way, stop for good once that
one-word "peek" has been checked. "Resolved" reuses `expressions._known_names(program, profile)` --
already sufficient, since this project's own extracted per-logger tables carry CRBasic's syntax
keywords alongside its predefined symbols (`Double`/`Is`/`Public`/... all present in `symbols.json`/
`functions.json`/`instructions.json`), so no separate reserved-word list was needed. Untested and
not implemented: an operator with surrounding whitespace (`alpha - beta`), any operator outside the
eight characters above, and a statement whose first token is not a bare word at all (conservatively
treated as zero leading words, `_leading_unknown_words`'s own docstring).

Implemented as `checker/cr3xx.py::check_unknown_line_words(program, profile, diags)`: CR300/CR350
only, reacts to `diags.items` already containing the generic fallback message for a statement
`program.statements` marks `S.Unparsed` (not any other statement shape -- a bare unresolved *call*
already gets its own, already-implemented, single-name line via `profile.undefined_call_message`;
an unresolved *assignment target* was not probed this round for this same leading-word behaviour,
so is deliberately left alone), and splices in one `profile.unresolved_symbol_message` line per
returned word, immediately before it. **Not yet wired in** -- `checker/structure.py`/`checker/
__init__.py` are owned by a concurrent workstream this round; the one line needed, right after
whichever pass can emit `structure.py`'s own generic fallback for an `S.Unparsed` statement:

    cr3xx.check_unknown_line_words(program, profile, diags)

### Finding 2 (newly observed this round, not yet implemented anywhere): no placement stop either

The same scratch probes and the CHM transcripts above also show CR300/CR350 do **not** apply
`structure.py`'s "before `BeginProg`" follow-up (`Expr illegally occurs before BeginProg.` plus a
pass-2 stop) to the generic `S.Unparsed` fallback either -- e.g. `foo bar` before `BeginProg` gets
only the two `is not defined.` lines and the generic line, then compilation continues on to later
statements (`docs/loggers.md` above already documents the identical carve-out for the *Call*-shaped
"resolves to nothing" branch, `profile.undefined_call_message`; this is the same carve-out for the
`S.Unparsed` branch, oracle-confirmed independently this round). This one is **not implementable as
a `cr3xx.py` reaction to `diags.items`**, unlike Finding 1: the missing behaviour is that
`structure.py`'s own pass-2 loop returns early and never even evaluates the statements after the
stop, so there is nothing in `diags` afterward to react to. It needs a small conditional directly in
`structure.py`'s own `S.Unparsed`-`else` branch, gating the existing `if not in_body_block and
(...): diags.error("Expr illegally occurs before BeginProg."); return stmt.line` behind `profile.
name not in ("cr300", "cr350")` (mirroring the existing `if profile.undefined_call_message:` early
`return None` a few lines above in `_check_call_pass2`).

### Measured impact

Neither hook above is wired in this round (both live in files owned by a concurrent workstream, per
this round's own scope). `chm` `exact` is therefore **unchanged**: cr300 157, cr350 161 (cr6 338,
cr1000x 354, also unaffected -- this work is CR300/CR350-only). Simulated in-process (calling
`check_unknown_line_words` manually right after `checker.check`, Finding 1 alone, Finding 2 not
simulated): 0 of the 507 `chm` files flip to `exact`, because essentially every corpus instance of
this shape is a `S.Unparsed` statement before `BeginProg` (CHM examples put their prose near the top
of the file) and so is already blocked from becoming exact by Finding 2's still-missing stop
suppression -- Finding 1 is real and oracle-tested (verified line-for-line against every probe in
this section) but has no *measurable* corpus effect until Finding 2 also lands. `python3 -m pytest
-q`: 1039 passed, 311 skipped (16 new, all passing, `tests/test_cr3xx.py`); zero false rejects on
all four loggers (`their_exit==0 and our_exit!=0`, checked after this round's change, empty on all
four, unaffected since the change is a no-op addition pending both hooks).

## Generic fallback echo on CR300/CR350 (2026-09-27)

`Invalid, or out of place expression: %s.` on CR300/CR350 echoes the raw line including its
terminator, so the closing `.` is printed on the next output line (CR6/CR1000X print it on the
same line). Seen in 144 help examples, e.g. `acpower_1.CR6` on cr300comp.exe:
`line 34: Invalid, or out of place expression:     ACPower (...,15,1)` then `.`.
Implemented as `Profile.fallback_echo_newline`; chm exact CR300 157 -> 180, CR350 161 -> 184.
The assignment-statement variant of the fallback is not switched yet (no evidence either way).

## Too many parameters cascade, widened (2026-09-27)

Round 7b's `check_expr_cascade` (above) modelled the CR300/CR350 "undefined name taints the next
statement" quirk as a single, one-shot event: the *next* qualifying statement after the last
trigger gets one spurious `Too many parameters:  %s.`, then the chain stops. Four scratch probes
this round (`t.cr6`..`t4.cr6`, `WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all wine
../compiler/cr300comp.exe|cr350comp.exe t.cr6`, no option flags, both compilers byte-identical)
against the corpus's own multi-line instances (`dewpoint_1.CR6`, 3 in a row; `runprogram_1.CR6`/
`filemanage_1.CR6`/`goesdata_1.CR6`, 2 in a row) show this is too narrow -- Round 7b's own sample
never happened to have more than one qualifying follow-up statement, so "stops after one" was
indistinguishable from "keeps going" until now. Corrected model, all four points oracle-confirmed:

- **The taint never clears.** Once any undefined-name-class finding (`%s is not defined.`/
  `Variable illegal in Parameter: %s.`) *or* a genuine `Too many parameters:  %s.` finding has
  fired anywhere in the file, **every** later statement that is a recognised `S.Instruction` with
  two or more arguments gets its own spurious `Too many parameters:  <its own second argument's
  verbatim text>.`, all the way to end of file -- not just the next one (`t.cr6`: an undefined name
  followed by three `PortSet`s and a `Delay`, all four flagged).
- **It replaces, not just fills a gap.** A tainted statement's *own* checks never run at all on the
  real compiler: `t2.cr6`'s `PortSet(C1,BadVar2)` (itself referencing an undefined name) gets only
  `Too many parameters:  BadVar2.`, never a second `BadVar2 is not defined.`; `t3.cr6`'s tainted
  `PortSet(C1,1,2,3)` (a genuine 2-declared/4-given overflow) still echoes its own second argument
  (`1`), never the true excess position (`2`) `instr_rules.check_too_many` computes for it
  standalone.
- **A genuine overflow finding itself taints too**, with no preceding undefined name needed
  (`t4.cr6`: a standalone `PortSet(C1,1,2,3)` keeps its own correct, position-based `Too many
  parameters:  2.`, but a clean `Delay(0,10,mSec)` right after it still gets swallowed into
  `Too many parameters:  10.`).
- The taint's own first statement is left untouched (matches Round 7b: `dewpoint_1.CR6`'s first
  `VoltSE` keeps its real `mv5000C is not defined.` pair; only the *second*, identically-shaped
  `VoltSE` is swallowed) -- only statements *strictly after* the one that first goes trigger-shaped
  are rewritten. A statement that is not a recognised instruction at all (an unresolved bare call
  such as `routes_1.CR6`'s `Move(...)`/`Routes(...)`, naming instructions this project's own table
  has no record of on CR300/CR350) is unaffected by the taint either way -- its own undefined-name
  detection fires normally, because it never reaches the generic per-instruction argument checking
  the taint corrupts.

Implemented as `checker/cr3xx.check_too_many_cascade` (replaces `check_expr_cascade`): a single
left-to-right sweep over `program.statements`, not a "find the last trigger line, then walk
forward" re-scan the way `check_expr_cascade` was -- the re-scan shape gets `t2.cr6` backwards
(a tainted statement is itself trigger-shaped from its own now-superseded diagnostic, so a re-scan
picks it as a fresh anchor and leaves its stale diagnostic standing instead of rewriting it).
Because the taint can originate from a pass-1/ungated finding that would otherwise skip tier B
entirely (`acceptdatarecords_1.CR6`: `SerialOpen (COMC1,...)`'s `COMC1 is not defined.`, from
`cr3xx.check`'s own separately-ungated `ComPort` finding, still taints `Scan`'s `Sec` and
`AcceptDataRecords`'s `32769` even though tier B never runs for this file), `checker/__init__.py`
now calls this once, unconditionally, after every other diagnostic-producing pass, instead of from
inside the tier-B `if not diags.errors:` block.

**Known gap, not fixed this round** (a synthetic case, not observed in the corpus): when a genuine
overflow on an *earlier* statement would itself be the first taint, but a *later* statement in the
same file has the real first undefined name, this project's own pass-1/tier-B split reports the
genuine overflow first (since `instr_rules.check_too_many` is unconditional pass 1, run before tier
B) and the file order the real compiler needs (earliest statement's error first, whichever kind)
is lost -- an existing architectural gap in the tier split, not new to this round, and out of this
round's own scope (`instr_rules.py`/`cr3xx.py` only).

Measured impact (`tools/difftest.py --logger <l> --show 0`, `chm` group): cr300 exact 180 -> 219,
cr350 exact 184 -> 228; zero false rejects on all four loggers (`their_exit==0 and our_exit!=0`,
checked after this round's change, empty on all four; cr6/cr1000x untouched, `check_too_many_cascade`
no-ops immediately on every other logger). `uv run pytest -q`: 1057 passed, 311 skipped (11 new,
`tests/test_cr3xx.py`).

## CDM_* argument names

An `S.Call`-shaped statement whose own name never resolves to a `Sub`, a built-in function or an
instruction (`docs/checker.md` "Pass model": `structure._check_call_pass2`'s "resolving to
nothing" branch) already gets, on CR300/CR350, `{name} is not defined.` for the call's own name
(`profile.undefined_call_message`) ahead of the generic `Invalid, or out of place expression: %s.`
fallback -- but this project's own checker printed nothing else for the call's *arguments*,
undefined or not, until this round. Corpus: every `CDM_*` instruction this project's own tables
have no record of on CR300/CR350 (`cdmbrfull6w_1.CR6`, `cdmexcitev_1.CR6`, `cdmdelay_1.CR6`,
`cdmmuxselect_1.CR6`, `cdmfftfilt_1.CR6`, ... -- CDM support needs hardware these two loggers'
smaller product line never carries), plus scratch probe `t3.cr6`
(`FooBar(BadName1,1,2,BadName2,3,GoodKnownVar,BadName3)`, `WINEPREFIX=$HOME/.wine-cr6
WINEDEBUG=-all wine ../compiler/cr300comp.exe|cr350comp.exe t3.cr6`, no option flags, both
compilers byte-identical).

**The rule** (walking the call's own arguments left to right):

- A literal, or any argument that is not a bare `Name` or an `Index` (`Name(args)`, e.g. `Meas(i)`
  -- only its own head name is checked, not its sub-arguments, not oracle-tested either way this
  round), is silently skipped over: neither flagged nor a stop, whatever it contains (every `CDM_*`
  call's own numeric gain/range/multiplier arguments pass over this way).
- A bare `Name`, or an `Index`'s own head name: **undefined** -> flag it and keep scanning the
  arguments after it the same way (`cdmexcitei_1.CR6`/`cdmexcitev_1.CR6`: `CDM_A108` (argument 0)
  and `X1` (argument 2, past the literal `1` at argument 1) both flagged in the same call; `t3.cr6`
  flags both `BadName1` and `BadName2`, past *two* skipped literals). **Resolves** (a declared
  variable, a predefined symbol, a `Const`, ...) -> scanning **stops there for good**, whether or
  not a later argument is itself undefined (`t3.cr6`: `GoodKnownVar` stops the scan before ever
  reaching `BadName3`; `cdmbrhalf3w_1.CR6`'s `CDM_BrHalf3W(CDM_A108,1,HBr3W,1,mv200,1,X1,...)` stops
  at its own declared `HBr3W` (argument 2), silently skipping the genuinely-undefined
  `mv200`/`X1` after it; `cdmfftfilt_1.CR6` stops at its own declared `Const ADDR` (argument 1);
  `cdmmuxselect_1.CR6`'s `CDM_VoltDiff(CDM_A108,1,Meas(i),...)` stops at `Meas(i)` itself (argument
  2, its own head resolving)).

**Known gap, not fixed this round**: a `Binary`/`Unary`/`ArrayLiteral` argument (an operator
expression, `UnknownA+UnknownB`) is treated as an opaque, skipped-over argument like a literal --
two scratch probes for this shape specifically gave **different** results for the identical
`UnknownB+UnknownC` text depending on which argument position it sat in (`t.cr6`,
`FooBar(UnknownA,1,KnownVar,UnknownB+UnknownC,5)`, flags only the first name of it, `UnknownB`,
never `UnknownC`; `t2.cr6`, the same text one argument earlier, flags both names of *every*
operator-expression argument instead) -- a sign this project does not yet understand the real
trigger, not a fluke, so this deliberately leaves the shape unimplemented (AGENTS.md: under-report
rather than guess at a rule two probes already contradict each other on). No corpus file this round
exercises an operator-expression argument on an unresolved call.

Also **not fixed this round**, a distinct and narrower shape (`cdmbattery_1.CR6`/
`cdmexcitei_1.CR6`, e.g. `CDM_Battery` and its own `(CDM_A116,1,CDM_BattV)` argument list wrapped
onto the *next* physical line with no `_` continuation): this is an `S.Unparsed` statement, not an
`S.Call` at all (a leading `(` fails `Call`'s own grammar), so it is untouched by the rule above.
The real compiler still flags the first undefined name inside it (`CDM_A116 is not defined.`) and
echoes the generic fallback with the outer parens stripped, no leading whitespace, and no trailing-
newline quirk -- all three oracle-confirmed different from every other `S.Unparsed` echo this
project already models (`check_unknown_line_words`'s own leading-bare-word case). Implementing this
needs a hook into `checker/structure.py`'s own `S.Unparsed`-`else` echo (out of this round's own
file ownership); left as a follow-up.

Implemented as `checker/cr3xx.check_undefined_call_args`, wired into `checker/__init__.py` right
after `check_unknown_line_words`, in the same tier-B block; reacts to `diags.items` already
emitted by `structure.check_pass2` (the same after-the-fact pattern `check_unknown_line_words`
already uses), splicing the new lines in between a matched call's own `{name} is not defined.` and
its generic fallback.

Measured impact (`tools/difftest.py --logger <l> --show 0`, `chm` group): cr300 exact 219 -> 250,
cr350 exact 228 -> 259; zero false rejects on all four loggers (`their_exit==0 and our_exit!=0`,
checked after this round's change, empty on all four; cr6/cr1000x untouched, exact unchanged at
378/400 -- `check_undefined_call_args` no-ops immediately on every other logger). Of the corpus's
own ~12 `CDM_*` verdict cases this round targeted, 10 are now exact; the remaining 2
(`cdmbattery_1.CR6`, `cdmexcitei_1.CR6`) keep one residual diff each, the split-continuation-line
gap above. `uv run pytest -q`: 1060 passed, 311 skipped (3 new, `tests/test_cr3xx.py`).

## Declaration errors taint (2026-09-27)

CR300/CR350 (both identical): `%s already declared as a variable.`, `%s is already declared as a
CONST.`, `Variable Type %s not supported`, `Illegally dimensioned aliased name` and `Invalid
Variable declaration %s.` also start the `Too many parameters` cascade (a clean `Scan
(1,Sec,0,0)` later gets `Too many parameters:  Sec.`), and once tainted, a later `Public`/`Dim`
declaration's own error is dropped (`Public A As StringX * 50` then `Dim Z banana` prints no
`Invalid Variable declaration banana.`). `cr3xx._is_cascade_trigger`. Included files print as
`<CPU:Lib.crb>` as written, or `<cpu:Lib.crb>` when the `Include` names no drive; CR6/CR1000X
print the name as written without its drive (`diagnostics.Diagnostic.cr6comp`). chm exact cr300
251 -> 254, cr350 261 -> 264.

CR300/CR350 have no pass gate: undefined names, the generic fallback and other tier-B findings are
reported even after a pass-1 error (`Dim Z banana` then `X = Q` / `foo bar` / `X = 1 +` prints all
of them; `profiles.single_pass`). An undeclared assignment target gives `%s is not defined.` for
it, then the right-hand side's own undefined names (`Nope = Q`), or the generic fallback for an
indexed target (`Nope(2) = X`); no placement follow-up. chm exact cr300 256, cr350 266.

`Dimensions not declared` (CR300/CR350, oracle 2026-09-27): an undefined name used with
parentheses adds `Dimensions not declared: <echo>` after the per-name lines -- the argument text
for an instruction argument, after which the rest of the argument list collapses into `Too many
parameters:  <next simple token>.` (`SplitStr(ResultString(1),SearchString(),...)`); the whole
statement, newline-terminated, for an assignment (`Store = Fmt(Num(1))`); the condition text for
an `If`. The `Too many parameters` cascade also echoes the next simple token (`MainScanSecs * 4`
-> `MainScanSecs`). Control statements before `BeginProg` (`If`, `FOR`, `Do`, `SELECT`, stray
`EndIf`/`Loop`/`NEXT`) get `%s illegally occurs before BeginProg.` on all four loggers. chm exact
cr300 268, cr350 278; `crbasic/` 5/16 exact on both.

## Undefined table sources (2026-09-27)

CR300/CR350 report an undefined name in a table-output source (`Sample (1,Nope,FP2)`) as `Nope
is not defined.`. `Nope(2)` adds `Dimensions not declared: Nope(2).`. Only the first such
statement is reported. The check is late, with the table `Reps` checks
(`instr_data.check_table_reps_late`), so an unrelated `X = Q` hides it. CR6/CR1000X report
`Invalid symbol in expression: Nope.` in pass 2 instead (`docs/checker.md`).

Errors from the CR300/CR350 late stages (table checks, the late walk, unclosed blocks) do not
start the `Too many parameters` cascade (`Diagnostic.cascade`). Oracle: `Sample (1,Nope,FP2)`
before a `Scan (1,Sec,0,0)` gives no `Too many parameters:  Sec.`. Tests:
`tests/test_cr3xx_table_source.py`. Replay: 812 of 1469 exact.

## Tainted declarations (2026-09-28)

Once the CR300/CR350 `Too many parameters` cascade has started, later `Public`/`Dim`/`Static`
declarations are not registered, and neither are the parameters of a later `Sub`/`Function`. A
`Const` still is. Uses after that point report as undefined, in the usual CR300 shapes: `X = A`
-> `A is not defined.`, and `A(1) = 2` -> `A is not defined.` plus the fallback. `X = F(1)` for a
tainted `Function F (x)` -> `No Matching, or out of place ) in expression:     X = F(1)`.
`X = a` inside a tainted `Sub S (a)` -> `a is not defined.`. `Return x + 1` and `Call S(1)` are
not reported (oracle, `tests/test_cr3xx_tainted_declarations.py`).

`cr3xx._report_unregistered` re-runs the tier-B name checks on a copy of the program without
those declarations (and with the tainted headers' parameters removed). It keeps only the
findings on later statements that use an unregistered name or a tainted `Function`. This only
adds errors to programs that already fail. chm exact CR300 269 -> 273, CR350 279 -> 283; replay
915 of 1785 exact.

## CR300/CR350 argument rules (2026-10-01)

Oracle probes, CR300/CR350. Code: `checker/cr3xx.py` (`_call_arg_undefined_names`,
`_report_rewritten_first_arg`, `check_float_destinations`), `parser.py` (`_parse_if`,
`_parse_stmt_list`, `_parse_line`).

- Unknown call `Foo(...)`: arguments 1-3 are name-checked fully (operators, index arguments);
  argument 4 only its leftmost term; 5+ never. A declared name is skipped; an undefined indexed
  name `B(...)` is reported, then its arguments, then the scan stops. `Foo(B0, B1, B2, B3+B4, B5)`
  reports B0 B1 B2 B3.
- A bare DataTable name used as a value is undefined on all four loggers (CR6: `Invalid symbol in
  expression`).
- After the `Too many parameters` cascade starts, a rewritten instruction still checks its first
  argument when its parameter type code resolves an expression/variable (not code 6 identifiers
  such as a DataTable name): undefined names, then `Dimensions not declared: A(I).`, then for
  code 8 a non-variable gives `Illegal destination variable type, expecting Float.`, before
  `Too many parameters:  <arg 2>.`. `Dim Z banana` / `Public PTemp` / `PanelTemp(PTemp,15000)`
  gives `PTemp is not defined.`, `Too many parameters:  15000.`
- Code 8 (Dest) first argument: anything other than a Float variable (Long, Double, String,
  Boolean variable, literal, expression) gives `Illegal destination variable type, expecting
  Float.`; an undefined name gives its `is not defined.`. Either way the next argument follows as
  `Too many parameters:  <token>.` and starts the cascade. `PanelTemp(L,60)` with `L As Long`.
- Inline `If ... Then` echo: the first statement after Then/Else is echoed from one column past
  the keyword to the delimiter, trailing whitespace kept (`If X Then Foo(1)   'c` ->
  `Foo(1)   .`); before `Else` one space is kept. On CR300/CR350, colon segments after a one-line
  If have no newline in the echo.
