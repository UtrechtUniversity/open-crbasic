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

## Extension / `--logger` mapping

Already implemented in `profiles.py` (`EXTENSIONS`, `for_file`) and `cli.py` (`--logger`); this
session added `tests/test_profiles.py` (extension-to-logger mapping for all four extensions
case-insensitively, unknown-extension default, `profiles.get` case-insensitivity and its `ValueError`
on an unknown name, every profile's data files loading, `compile_file`'s `profile` argument taking
priority over the file extension -- the regression this session's `parser.py` fix targets -- and
`cli.main`'s `--logger` flag end to end, both the CR300 `Compiled OK.` convention and an unknown
`--logger` value's error).
