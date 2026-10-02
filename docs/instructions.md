# Per-instruction rules: warnings and value-range checks

Owned by the same workstream as `checker/warnings.py`, `checker/instr_rules.py`,
`checker/terminals.py`, `checker/instructions.py` (`PLAN.md`). Records the rule, the oracle
evidence, and per-logger differences for each check added this round. All probes below were run
2026-09-25/26 via `python3 tools/oracle.py <file> [--siblings DIR]` or a direct
`WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all wine ../compiler/<exe> t.cr6` scratch probe against the
real compilers, per `AGENTS.md`'s oracle priority. CR6 rules stay cross-referenced from
`docs/checker.md`/`docs/fuzz-findings.md`; this file adds the value-range/warning work and the
per-logger differences found while implementing it.

## Two ungated call sites

Two architectural facts, found by direct oracle probing (placing an unrelated pass-1 error both
before and, separately, nowhere near each check below): **all** checks in this document fire
regardless of what else is wrong in the file, unlike `checker/instructions.py`'s pass-2 channel
checks (`docs/checker.md` "Two-pass compilation"). Concretely: a file with an earlier `NOTE`
(undefined bare identifier) or `PanelTemp(UndeclaredVar,...)` error still gets every check below's
own diagnostic, in ascending line order together with the other errors (oracle: `t8.cr6`/`t9.cr6`/
`di9.cr6`/`di10.cr6` scratch probes, this session). `checker/warnings.py`'s `check_early` is the
single call site (`checker/__init__.py`, one additive line, ungated); `instr_rules.py`'s
`check_scan_params`/`check_data_interval` are called from there rather than from
`checker/__init__.py` directly, to keep that file's edit to one line.

A second, unrelated finding needed to make any of this visible in `crbc`'s own output: warnings
with a real line number print `line N: Warning: <message>` (not bare `Warning: <message>`, which
`diagnostics.py` previously assumed for every warning because the only warning that existed before
this round, "table declared but never called", has no line at all). A line-less warning still
prints bare, and sorts *after* every numbered line (oracle: `t10.cr6` -- a `Delay never reached at
run time.` warning at line 12 printed *before* a `Table Foo, is declared but never called.`
warning that has no line). `diagnostics.py`'s `cr6comp()` and `compiler.py`'s `result_lines()` were
adjusted for both (a stable sort by `(line == 0, line)` over errors and warnings together,
replacing the old errors-only sort).

## `%s never reached at run time.` (`checker/warnings.py`, `_check_reachability`)

Fires for the *first* executable top-level statement that textually follows a `Scan(...,Count=0)`
(loop forever) with no `ExitScan` anywhere in its body, up to the next `SlowSequence` or end of
file. All four loggers share this (oracle: identical text/condition on `cr1000xcomp.exe`,
`cr300comp.exe`, `cr350comp.exe` and `CR6Comp.exe` for the same minimal probe).

- `%s` is the statement's canonical instruction name (`Delay`, `Scan`, proper-cased from
  `data/<logger>/instructions.json`) or `Expr`, the compiler's generic bare-expression-statement
  pseudo-instruction, for a plain assignment or anything that failed to parse as a recognised
  instruction (oracle: a mutated `EndProg` that fails to parse gets both `Invalid, or out of place
  expression:` *and* `Expr never reached at run time.` on the same line,
  `tests/corpus/fuzz/cr6/message_diff/caf5c15cd16b.CR6`).
- Only the *first* such statement is ever reported; every oracle/corpus example found shows
  exactly one, matching this project's other "one diagnostic, then silence" findings
  (`docs/checker.md`).
- `SlowSequence` genuinely resets/exempts reachability (oracle: `SlowSequence`/its own
  `Scan`/`NextScan` right after an infinite, no-`ExitScan` main scan is never flagged) -- CRBasic
  semantics, not a compiler blind spot: a slow sequence is a real, separately-scheduled task.
  **Found as a regression during development**: this checker's first version only *reset*
  reachability at `SlowSequence` and kept tracking afterward, which produced 10 corpus false
  warnings on real, oracle-accepted programs whose slow sequence's own inner scan is itself
  infinite-with-no-exit (`ftpclient_1.CR6`, `sdi12recorder_3.CR6`, `udpsocketopen_1.CR6`, and 7
  more, all flagging the `EndSequence` terminator as "never reached"). No oracle evidence exists
  either way for reachability *within* a slow sequence's own (possibly chained) scan structure, so
  the fix is to stop this analysis entirely at the first `SlowSequence`, not to guess at a correct
  rule for it.
- A `Sub`/`Function` body is skipped entirely (its own statements are reachable whenever called,
  independent of where the definition textually sits).
- Non-executable (never flagged even when "dead"): `Public`/`Dim`/`Const`/`ConstTable`/`Alias`/
  `Units`/`DataTable...EndTable`/`StructureType...EndStructureType`/`ObjectType...EndObjectType`/
  `BeginProg`/`EndProg`, and (see above) anything from `SlowSequence` onward.

Boundary probes (`Scan(N,Sec,3,2000)`, a finite count -- no warning, since the loop returns after
2000 iterations) and (`ExitScan` present anywhere in the body, even inside a conditional that may
never fire -- no warning either) both confirm the rule is purely syntactic ("does this Scan block
contain the *text* `ExitScan` anywhere, and is `Count` a resolvable constant `0`"), not a real
control-flow/liveness analysis.

## `When Main Sequence Scan %d is running, background calibration will be disabled.`

CR6/CR1000X only (`profile.background_calibration`, absent for CR300/CR350). Fires once per
*main-sequence* `Scan` (not one declared under `SlowSequence`, whose own minimum interval, 100mSec,
is far above every threshold below) whose folded `Interval`/`Units` is below a threshold, reported
at the file's *last* statement's line (oracle: `t.cr6`/`54f0b2ff38cf.CR6`/`19a402de98a4.CR6` all
report at `EndProg`'s own line, not the `Scan` statement's line -- the same "last statement" spot
`structure.py`'s `No BeginProg in Program.` uses).

Threshold, found by binary search on the `Scan` interval (`mSec`/`uSec` units, boundary confirmed
to within 0.1ms on CR6 and to the minimum achievable step on CR1000X):

| Logger | Threshold | Evidence |
|---|---|---|
| CR6 | < 8ms (8000us) fires; >= 8ms does not | `7.9mSec` fires, `8.0mSec` doesn't; `7999uSec` fires* |
| CR1000X | < 2ms fires (in practice, only exactly `1ms`, since CR1000X's own minimum `Scan` granularity is 1ms) | `1mSec` fires, `2mSec` doesn't |
| CR300/CR350 | never (no such warning at any interval probed) | oracle-probed down to `100uSec` (far below CR6/CR1000X's own minimum), no warning either logger |

\* `7999uSec` on CR6 actually fails a *different*, unrelated check first (`Scans must be a
multiple of 1mSec.` -- not implemented, see "Known gaps" below); the threshold above was
confirmed with the `mSec`-unit probes, which don't hit that other check.

Both thresholds fit one formula: fires when `floor(interval_us / granule_us) < 2`, `granule_us` =
4000 (CR6) / 1000 (CR1000X) -- consistent with the decompiled condition this message's function
tests (`*(uint*)(...) < 2`, `docs/checker.md`-style evidence, not itself re-derived byte-for-byte
this round) and exactly matching both loggers' independently-probed boundaries.

`Scan`'s `Units` argument folds (via `profile.symbols`) to a `usec`=0..`day`=5 index
(`data/symbols.json`); `checker/warnings.py`'s `_MS_PER_UNIT` converts it to whole milliseconds for
this comparison.

## `Invalid fnotch value, must be 50,60 400 or 4000` (CR300/CR350 only, no trailing period)

Every instruction with an `fN1` filter-frequency argument (found exhaustively: every CR300/CR350
instruction whose `data/<logger>/instructions.json` `params` contains type code 20, cross-checked
one-for-one against each instruction's own CHM `Syntax` line to confirm the position, three of
them -- `PanelTemp`, `VoltSe`, `TCDiff` -- also directly oracle-verified) restricts it to exactly
`{50, 60, 400, 4000}` on CR300/CR350; CR6/CR1000X accept any value in a continuous range instead
(oracle: `PanelTemp(...,15000)` compiles clean on both, and `15000` is even the CHM's own example
value for that instruction). `0` is *not* exempt (unlike CR6's own, unrelated fnotch minimum-value
error, see "Known gaps" below) -- oracle-confirmed `PanelTemp(X,0)` on CR300 still warns.

Instruction -> 1-based argument position of the sole `fN1` slot (`data/cr300/fnotch.json` /
`data/cr350/fnotch.json`, identical on both loggers -- their `instructions.json` `params` arrays
for every one of these are byte-identical):

| Instruction | Position | Instruction | Position |
|---|---|---|---|
| `InternalTemp` | 2 | `BrHalf` | 10 |
| `PanelTemp` | 2 | `BrHalf3W` | 10 |
| `Therm107`/`108`/`109` | 6 | `BrFull` | 11 |
| `VoltSe`/`VoltDiff`/`CurrentSe` | 7 | `BrHalf4W`/`BrFull6W` | 12 |
| `TCSe`/`TCDiff` | 9 | `AM25T` | 13 |

Fires even when the file has unrelated errors elsewhere (oracle: `tablefile_1.CR6` under
`cr300comp.exe` shows this warning *alongside* an unrelated `Invalid drive` error). This single
warning, once implemented, is the dominant fix for CR300/CR350's fuzz `mode_diff` class (`docs/
fuzz-findings.md`: 54/300 and 58/300 mutants respectively; both loggers' single saved `mode_diff`
reproducer was exactly this warning) and for a large slice of the `chm` corpus's own `verdict`
count (`PanelTemp`/`TCDiff` alone appear in 188 and 102 corpus files respectively).

## `Invalid fnotch, minimum allowed is N Hz.` (CR6 `2 Hz`, CR1000X `.5 Hz`; an error)

Observed 2026-10-02 on `CR6Comp.exe` and `cr1000xcomp.exe` (compiler hashes in
`tests/fixtures/fnotch_min.json`), 134 fixture cases
(`tests/test_fnotch_min.py`), data `data/fnotch.json` (CR6) and `data/cr1000x/fnotch.json`.
Unlike CR300/CR350 above (a warning on a fixed value set), this is a hard **error** and has no
maximum (`1e30` and `15000` compile clean).

Value rule on the folded `fN1` argument: `0`, `+INF` and `-INF` pass; `NAN`, every other negative
number, a string (including `""` and `"60"`) and `True` (= -1) fail; a positive number fails when it
is below the minimum. The message always says `2 Hz` (CR6) / `.5 Hz` (CR1000X) but the real cut-off
is not that number. Bisected: CR6 fails at `1.43052` and passes at `1.43053`; CR1000X fails at
`0.47684` and passes at `0.476845` (`1e6 / f` is about 699,04x us and 2,097,1xx us, the CR1000X
figure is three times the CR6 one). `min_hz` in the data files is the midpoint of each bracket
(1.430525, 0.4768425), so the unprobed digits beyond it are an assumption.

Instruction and 1-based position (identical positions on both loggers, `params` type code 20 in
`data/instructions.json`): `PanelTemp` 2, `VoltSe`/`VoltDiff`/`CurrentSe` 7, `TCSe`/`TCDiff` 9, `Therm107/108/109` 6,
`BrHalf`/`BrHalf3W` 10, `BrFull` 11, `BrHalf4W`/`BrFull6W` 12, `AM25T` 13. CR6 additionally has
`PanelTemp2` 4, `Thermistor` 8, `Resistance` 11 and `Resistance2W`/`Resistance3W` 8 (each only
probed with `-1`, a single failing value, and with unusual argument lists; the positions come from
the instruction table). The message is printed **twice** for `Therm107/108/109`, `BrHalf`,
`BrHalf3W` and `BrFull` (both loggers, also with `Reps` 2 and with other `RevEx` values), once for
the rest; the reason is unknown, it is modelled as the data key `twice`.

Pass and ordering (all oracle-confirmed on both loggers unless noted):

- It is a pass-1, constant-family value check, like the unresolved-symbol check of
  `expressions.check_const_family_args`. It is reported next to earlier pass-1 errors (`Dim Z
  banana`, `Delay(0,5,Foo)`, a stage-2a `Battery((1+2))`), and it makes pass 2 not run:
  `PanelTemp(PTemp,-1)` followed or preceded by `Foo = Bar` prints only the fnotch line, and an
  undefined destination (`PanelTemp(Nope,60)`) is not reported either.
- Within a statement: an unresolved name or a variable in any constant position (e.g. `U99`,
  `mV7`) abandons the statement before the fN1 check; the `Range` code error (below) comes first
  when both apply; the fnotch line precedes `Invalid or missing parameter.` and `Too many
  parameters:  N.` on the same line (`PanelTemp(PTemp,True,1)`, `VoltSe(...,-1)` with `Mult`
  missing). A bad `Mult`/`Offset` name (general family) is not reported. Two fnotch errors on one
  line (`:` separated) are both printed, and an inline `If ... Then PanelTemp(PTemp,-1)` counts.
- A variable at the fN1 slot (`PanelTemp(PTemp,PTemp)`, `X(1)`, `PTemp+1`) is `Variable illegal in
  Parameter: <text>.` on CR6/CR1000X (previously a false accept; CR300/CR350 not touched).
- `Range` code (`Invalid range code, valid options are: ...`) is the same kind of check: it is
  reported after `Dim Z banana` and `Delay(0,5,Foo)` errors on both loggers (CR1000X cases use
  channel `1`). It used to be gated as a tier-C check; it now runs in the same pass-1 function
  (`instr_measure.check_pass1_values`).

CR350/CR300 fuzz reproducers that were listed as fnotch false accepts (`cr350/false_accept/2e32a4ebade7`,
`165c693049da`, `1549108234a4`, `881ad67a2b3f`, `cb998c48d68a`, `cr300/false_accept/6c8fb0434bdb`)
are not fnotch problems: the warning (`Warning:`, which does not fail a compile by itself) is
already emitted exactly. The compile fails because of other errors we do not model (`DataTable
interval ... not integrally divisible by scan interval`, `Illegal Semaphore, 1..3 are legal`,
`Could not find ScanConstTable.ScanConst(1).`, `Illegal option.`, `Illegal Matrix dimensioned`,
`Variable Temp out of bounds.` for `Therm109` with a missing argument).

Not modelled: on `Scan (1,Sec,0,0)` a low `fN1` (CR6 1.43 to 2, CR1000X 0.477 to 2) additionally
gives `Scan interval too short for measurement by N Sec.` on the `NextScan` line; it depends on the
fnotch value and is not implemented.

## `Scan Interval/BufferOption/Count parameter must be a non-negative integer` (CR6/CR1000X only)

No trailing period (verbatim oracle/`docs/messages.tsv` text, function `FUN_00468900`), for
`Scan`'s 1st/3rd/4th arguments (`Scan(Interval,Units,BufferOption,Count)`). Fires when the argument
is a compile-time constant that is either non-numeric (a string: `Scan("fuzz",Sec,1,0)`) or, once
truncated *toward zero*, negative:

```
Interval=-1   -> fires (int(-1) == -1, negative)
Interval=-0.5 -> does NOT fire (int(-0.5) == 0, not negative -- falls through to a separate,
                 unimplemented "too small" check instead, `Scan 0uSec invalid must be at least
                 1mSec.`)
Count=-0.5, BufferOption=-0.5 -> neither fires (same truncation rule; neither has a further
                 downstream range check the way Interval does, so these two compile clean)
TRUE (-1)     -> fires; FALSE (0) -> does not
```

**CR300/CR350 do not perform this validation at all** -- found as a regression during development:
the corpus's own `hex_2.CR6` (`Scan(1,Sec,3,-1)`, already a documented CR6/CR1000X-rejected
example, `docs/checker.md` "Known remaining gaps") is real CR300/CR350-*accepted* code
(oracle-confirmed: `Scan("fuzz",Sec,3,0)` and `Scan(-1,Sec,3,0)` both compile clean on
`cr300comp.exe`). Implementing this logger-unconditionally was a genuine false reject on CR300 (`0
false rejects` regressed to `1` in this round's own difftest run before the fix); gated to CR6/
CR1000X only (`instr_rules._SCAN_VALUE_CHECK_LOGGERS`) after that was found.

This closes the fuzz-found crash in `tables.py`'s `_scan_weights` (a non-numeric `Scan` interval
unconditionally passed to `float()`, `docs/fuzz-findings.md` "2. Crash") and the fuzz-found hang in
`tables.py`'s `render_table_list`/`_explode_indices` was independently already fixed by the
concurrent `structure.py` workstream's own array-dimension check (`Illegally dimension: ...`) before
this check was written -- both re-verified fixed this round (`tools/fuzz_replay.py`).

## `DataInterval TintoInt and Interval parameters must be non-negative` (CR6/CR1000X only)

No trailing period (function `FUN_004f11d0`), when either of `DataInterval`'s first two arguments
(`TintoInt`, `Interval`) is a negative compile-time-constant number, reported at the `DataInterval`
statement's own line (not the `CallTable` site, unlike the divisibility check below). **Also
CR300/CR350-absent** (oracle: `DataInterval(0,-3,Sec,0)` compiles clean on `cr300comp.exe`), found
and gated the same way as the `Scan` checks above.

## `DataTable interval %d uSec not integrally divisible by scan interval %d uSec.` (all four loggers)

Confirmed project knowledge (`knowledge/crbasic/lessons.md` "`DataInterval` versus `Scan`": "a
table interval must be at least the scan interval and an exact integer multiple of it", human-
verified against `CR6Comp.exe` 2026-08-21), now oracle-re-confirmed with the exact message text
and, unlike the two checks just above, found identical on **all four loggers including CR300**
(oracle: `DataInterval(0,3,Sec,0)` + `Scan(5,Sec,...)` gives the same message, digit-for-digit, on
`cr300comp.exe` as on `CR6Comp.exe`). `Interval = 0` (CHM: "the data storage interval is the scan
interval") trivially always divides evenly and needs no special case. Reported at each
`CallTable <name>` site, not the `DataInterval` statement's own line (oracle: `di10.cr6` scratch
probe -- the divisibility error's line is the `CallTable` line, the non-negative error's line
above is the `DataInterval` line, in the same file).

**Deliberately conservative** (`checker/instr_rules.py`'s `check_data_interval` docstring has the
full reasoning): only tracks the textually innermost `Scan`/`SlowSequence Scan` a `CallTable` sits
inside, with no depth stack beyond "currently open or not" (`Scan` never nests with itself; only
`SubScan` does, using the distinct `SUBSCAN`/`NEXTSUBSCAN` keywords, so it never interferes). Not
attempted: a table called indirectly through a `Sub`, a table called from more than one
differently-interval'd `Scan`, a table called from outside any `Scan` at all (e.g.
`AcceptDataRecords` -- silently skipped, no check), or the CHM's documented `GetDataRecord`-
destination-table exception. None of these gaps risk a false reject (they only mean the check
silently doesn't fire), consistent with AGENTS.md's priority order.

## Scan and table interval limits (2026-09-27, all four loggers)

Decompiled `FUN_004f6540` (the `CallTable` compile helper) keeps each interval as whole seconds
plus microseconds. The divisibility check depends on the table interval:

- Under 3601 s: compared in 100 uSec units and printed as `%d00 uSec`. The scan interval may be
  up to a day (`Scan(1,Day)` against a 1-hour table -> `DataTable interval 3600000000 uSec not
  integrally divisible by scan interval 86400000000 uSec.`).
- 3601 s or more, with a whole-second scan: compared in seconds, `DataTable interval %d Sec not
  integrally divisible by scan interval %d Sec.` (`Scan(2,Hr)` against a 3-hour table -> `10800
  Sec ... 7200 Sec.`).
- 3601 s or more with a fractional-second scan: `DataTable with interval > 1 hour cannot be called
  by scan whose interval is not an integral second is not divisible into a second.` (from the
  decompilation; not probed).
- Monthly table intervals are not checked.

`Maximum Scan interval is 1 Day.` fires on the `Scan` line when the interval truncated in its own
unit is over 86400 s (`Scan(86401,Sec)`, `Scan(1441,Min)`, `Scan(25,Hr)`, `Scan(2,Day)`). It
prints before a divisibility error. `Scan(1,Day)` is accepted.

An interval above `INT32_MAX` gets neither message. CR6/CR1000X report `Scan Interval parameter
must be a non-negative integer`, and CR350 accepts `Scan(999999999999,Sec,...)` (a fuzz-found
false reject before this). Oracle probes and `tests/test_scan_interval_limits.py`.

## `Invalid Variable or Array: %s.` (`AddPrecise`/`MovePrecise`'s `PrecisionVariable`, CR6/CR1000X only)

`AddPrecise`/`MovePrecise`'s own first argument (`PrecisionVariable`) is a `variable_or_array`
(type code 8) position on every logger that has these two instructions at all -- present in
`data/instructions.json`/`data/cr1000x/instructions.json`, **absent entirely from
`data/cr300/instructions.json` and `data/cr350/instructions.json`**, so `checker/instr_rules.py`'s
`check_precision_variable` gates on `profile.instructions` and is a no-op on those two loggers.

Oracle-confirmed (scratch probes, `CR6Comp.exe` and `cr1000xcomp.exe`, byte-identical on both):
this position rejects with `Invalid Variable or Array: <echo>.` whenever the argument has no
genuine variable reference anywhere in it -- a bare literal (`AddPrecise(1, .1)` -> `... 1.`), a
parenthesised or unary-negated constant expression (`(1+2)` -> `... (1+2).`, `-1` -> `... -1.`), a
declared `Const` name (`K` -> `... K.`), and the special float literal `NAN` (`... NAN.`) all
reject this way. A declared `Public`/`Dim` variable compiles clean regardless of how it appears --
bare, negated, parenthesised, indexed, or mixed into a larger expression with literals (`Precise`,
`-Precise`, `(Precise)`, `d(1)`, `Precise+1`, `1+2+Precise`) -- and so does an *undeclared* name
(`AddPrecise(UndeclaredFuzzName123, .1)`, the pre-existing saved fuzz false-reject reproducer,
`tests/corpus/fuzz/cr6/false_reject/fac2798b6778.CR6`; this position also never runs the ordinary
undefined-symbol check, `checker/expressions.py`'s own `_UNDEFINED_CHECK_EXEMPT_POSITIONS`). No
type restriction was found: a `String` variable is accepted here exactly like a numeric one.

The trigger is "does the argument's own name(s), if any, fail to resolve as a fixed compile-time
symbol" -- reusing `checker/expressions.py`'s existing `_undefined_names`/`_variable_refs` walk
(`not (_undefined_names(...) or _variable_refs(...))`) rather than a fresh `consteval.is_constant`
check: `NAN` is *not* `consteval`-constant (`_EXTRA_PREDEFINED_SYMBOLS` is a separate, untraced
resolution path) yet still rejects here, so plain constant-foldability alone is not what the real
compiler is testing. `X` (the second argument, a plain `expr` position) has no such restriction --
`AddPrecise(Precise, (1+2))` compiles clean.

**Pass 1, unconditional** (`checker/__init__.py`, alongside `instr_rules.check`/`check_too_many`):
oracle-confirmed to accumulate with an unrelated, earlier pass-1 declaration error in the same
file, and, run alone with no other error in the file, to itself suppress a later, otherwise-live
pass-2 finding elsewhere in the file -- the same "pass 1 found something anywhere in the file, pass
2 does not run at all" architecture this document's "Pass model" (`docs/checker.md`) already
describes for this module's other unconditional checks.

**Deliberately scoped to just these two instructions' own position 0**, not generalised to every
`variable_or_array` position: `checker/expressions.py`'s `_VARIABLE_REQUIRED_KINDS` docstring
documents a real regression at the same type code (`Move`'s third argument, `ModBusClient`'s last
argument, both oracle-confirmed to accept a literal there on CR1000X) this session has no evidence
either way for, so this stays narrow rather than guessing.

## Data tables and output processing (`checker/instr_data.py`)

New module, `checker/instr_data.py`, alongside this workstream's other files (`PLAN.md`: "data
tables and output processing"). Wired into `checker/__init__.py` with two additive lines, both
inside the pre-existing "no pass-1 error anywhere in the file" gate (`docs/checker.md`'s
"Two-pass compilation"; both rules below are oracle-confirmed to belong there, same method as that
section -- an unrelated pass-1 error placed both before and after the statement in question
suppresses the message either way, only firing when the file has no other error at all).

### `%s must be used inside a table declaration.` / `EndTable without DataTable.`

`check_table_structure` tracks one "currently inside a `DataTable`" flag (CRBasic `DataTable`
blocks do not nest, `tables.py`'s own `_scan_program` docstring) over the flat statement list:

- An orphan or extra `EndTable` (no `DataTable` currently open) is `EndTable without DataTable.`
  at its own line -- oracle-confirmed (scratch probes `te1.cr6`/`te3.cr6`/`te5.cr6`): a bare
  `EndTable` alone, and a *second* `EndTable` right after a properly-closed table, both give this
  exact message at the extra statement's own line. A `DataTable` that is *never* closed does
  **not** get a complementary "not matched" message of any kind (despite one existing in
  `docs/messages.tsv`, `'DataTable ... EndTable not matched.'`) -- oracle-confirmed (`te2.cr6`): it
  instead surfaces later, indirectly, as `Could not find DataTable %s.` at the table's first
  `CallTable` site, because everything after the unclosed `DataTable` (including `BeginProg` and
  the whole rest of the program) is apparently still absorbed as part of the table's own body.
  Not implemented (a different, unimplemented message; low corpus value; this checker's own
  simple boolean-flag tracking degrades safely there -- see below).
- Every instruction in `TABLE_ONLY_INSTRUCTIONS` found *outside* an open `DataTable` is `%s must
  be used inside a table declaration.`, `%s` = that dict's own display spelling. The set and
  spelling were found from the decompiled call sites of the shared validator function this
  message comes from -- `thunk_FUN_004ea5d0` on CR6, `thunk_FUN_004ea5f0` on CR1000X,
  `/opt/re/out/decomp.c`/`decomp_cr1000x.c`, grepped narrowly (never the whole file) for each
  function's own call sites, which pass each instruction's own name (or, for `Maximum`/`Minimum`,
  the unlabelled short strings `"Max"`/`"Min"`, read directly out of the compiled string table at
  their call sites with `pefile.get_string_at_rva`, not guessed) as the validator's argument. Both
  loggers' call-site lists are identical: `CardOut`, `MQTTPublishTable`, `GoesTable`, `TableFile`,
  `Totalize`, `Average`, `Max`(imum), `Min`(imum), `SampleMaxMin`, `StdDev`, `Moment`, `Sample`,
  `Histogram`, `Histogram4D`, `Covariance`, `RainFlow`, `LevelCrossing`, `WindVector`, `Median`,
  `SampleFieldCal`, `FillStop`, `DataTime`, `OpenInterval`, `DataInterval`, `DataEvent`,
  `TableHide`. Two more call sites (unlabelled strings `"FFT"`/`"Et"`, near the
  `Histogram4D`/`SampleFieldCal` call sites) could not be attributed to a specific instruction
  with confidence and are deliberately left out of the checked set (AGENTS.md: never guess into a
  false reject).

  Independently oracle-confirmed this round (fresh scratch probes against `CR6Comp.exe`, each
  instruction called correctly-shaped but outside any table): `CardOut`, `TableFile`, `Totalize`,
  `Average`, `Maximum` (as `Max`), `SampleMaxMin`, `StdDev`, `Median`, `WindVector`, `Histogram`,
  `RainFlow`, `LevelCrossing`, `FillStop`, `DataTime`, `OpenInterval`, `TableHide` all reproduce
  exactly, digit for digit. `Sample`, `Average`, `Minimum` (as `Min`) and `DataInterval` are also
  each backed by a saved fuzz reproducer (`tests/corpus/fuzz/cr6/false_accept/
  {470441546122,5b5ae86fd087,1ded68266aec}.json`, all `duplicate_line`/`delete_line` mutants of
  real CHM corpus programs -- not hand-written). `DataEvent` alone is decompiled-call-site
  evidence only: a from-scratch probe placing a bare, correctly-shaped `DataEvent(...)` outside
  any table **crashes the real compiler under Wine** rather than printing this text -- the same
  class of real-compiler-side crash `docs/checker.md`'s `pointervariable_2.CR6` entry already
  documents as unmatchable, so this checker's own clean rejection there is a deliberate
  improvement on the real compiler's own behaviour, not a claim to reproduce it exactly.
  `Moment`, `Covariance`, `Histogram4D`, `GoesTable`, `MQTTPublishTable` and `SampleFieldCal` are
  decompiled-call-site evidence only (each individually blocked from a clean from-scratch probe
  this round by an unrelated pass-1 argument-shape error in the minimal test case -- e.g.
  `MQTTPublishTable`'s first argument must itself be a constant -- not chased further, given the
  time already spent and the strength of the call-site evidence, which is byte-identical in
  structure to every instruction already confirmed).

  Verified this round that `FieldNames` -- named in the task's own illustrative instruction list
  alongside `Sample`/`Average`/etc. -- is **not** in this set despite the obvious analogy: a bare
  `FieldNames("X")` outside any table compiles clean (oracle-confirmed, scratch probe `tfn.cr6`).
  It plainly has its own validation (`FUN_004ec300`'s `'FieldNames cannot be a variable.'`/`'Must
  use FieldNames with reserved field .%s.'`/... in `docs/messages.tsv`) but not this placement
  rule; not implemented (out of this round's time budget).

Per-logger: a from-scratch probe of `Sample` alone outside a table gives the identical message,
digit for digit, on all four loggers' own compilers (`CR6Comp.exe`/`cr1000xcomp.exe`/
`cr300comp.exe`/`cr350comp.exe`); CR1000X's own decompiled call-site list
(`thunk_FUN_004ea5f0`) is identical in membership to CR6's. CR300/CR350 have no separate
decompilation available (`/opt/re/out/` only carries CR6 and CR1000X); the one live probe is
this round's only direct CR300/CR350 evidence, so the checked set is applied uniformly to all four
loggers on the strength of that probe plus the CR6/CR1000X agreement, filtered per logger by
`kw in profile.instructions` (an instruction a given logger's own table does not have, e.g. a
CR6-only CDM/GOES instruction absent from CR300/CR350, simply never matches -- safe by
construction, never a false reject from a logger difference this round did not individually probe).

### `Table %s declared with no output fields.`

Also `check_table_structure` (same function, same pass-2 gate): at a properly-paired `EndTable`,
if the just-closed table's body contained no instruction from `_FIELD_PRODUCING_INSTRUCTIONS` (a
curated subset of `TABLE_ONLY_INSTRUCTIONS` -- every instruction there that is purely
configuration, `DataInterval`/`CardOut`/`TableFile`/`DataEvent`/`FillStop`/`DataTime`/
`OpenInterval`/`TableHide`, is excluded), the table declaration itself is `Table %s declared with
no output fields.` at the `EndTable` line -- oracle-confirmed with three from-scratch probes (a
body with only `DataInterval`, only `CardOut`, or nothing at all, all three give this exact message
at the `EndTable`'s own line) and a saved fuzz reproducer for the "nothing at all" shape
(`tests/corpus/fuzz/cr6/false_accept/5d5ffec92ac4.json`, a `change_interval` mutant of a real CHM
seed).

Deliberately does **not** reuse `result.tables`' already-computed field *count* the way
`check_duplicate_fields` does (a `len(table.fields) == 0` test would have been simpler): `tables.py`
does not yet derive output fields for several instructions this project already knows are real
field producers (`RainFlow`/`LevelCrossing`/`Histogram4D`/`SampleFieldCal`, `docs/tdf-rules.md`'s
open gaps), so that shortcut would misreport a table containing *only* one of those as having no
output fields too. **Found as a regression during this round's own development, fixed before
landing**: `FFT` is exactly this shape (not handled by `tables.py`'s `_emit_output_fields` at all)
and was originally left out of both `TABLE_ONLY_INSTRUCTIONS` and `_FIELD_PRODUCING_INSTRUCTIONS`
for lack of a confident decompiled-call-site attribution (an unlabelled string constant, `"FFT"`,
this document's own instruction-set section originally logged as one of two it could not attribute
with confidence) -- a full `tools/difftest.py --logger <l> --group all` re-run after first landing
this check caught two real CHM corpus files (`fft_1.CR6`, and CR1000X's `covspa_1.CR6`, both
`DataTable` bodies containing only `FFT(...)` calls) going from accepted to a false reject on the
loggers that have `FFT`. A fresh scratch probe (a bare `FFT(...)` outside any table) confirmed `FFT`
does belong in `TABLE_ONLY_INSTRUCTIONS` too (`FFT must be used inside a table declaration.`,
digit for digit), and it is now included in both sets; the one remaining unattributed string,
`"Et"`, stays excluded.

`GoesTable`/`MQTTPublishTable` are included in `_FIELD_PRODUCING_INSTRUCTIONS` leniently, without
oracle confirmation that either alone (with no accompanying `Sample`/`GOESField`, a shape not seen
in this project's corpus) counts as a field producer by itself -- if that lenient guess is ever
wrong, the failure mode is under-reporting a table this rule should flag but doesn't, never a false
reject.

### `Duplicate field name %s in DataTable %s.`

`check_duplicate_fields` deliberately does not re-derive each output instruction's own default
field-naming rule (`_Avg`/`_Min`/`_TMx`/... suffixes, `FieldNames` renaming, reps-array expansion)
a second time; it reuses the names `tables.py` (a concurrently-owned module) has already computed
in `result.tables`, once per table, flagging every field beyond the first that shares an
already-seen name (oracle-confirmed shape: exactly one diagnostic for a name that occurs twice, not
one per pair -- `tests/corpus/fuzz/cr6/false_accept/{0004dc1d7a39,7076907a77b3,
d4b49fb47507}.json`, three independent `duplicate_line` fuzz mutants of real CHM seeds, plus the
CHM corpus's own `ch400status_2.CR6`). No line number (oracle-confirmed, matching the pre-existing
"table declared but never called" warning, which shares this message's emitting function,
`FUN_004eab30`, per `docs/messages.tsv`).

**Found as a regression during this round's own development, fixed before landing**: `tables.py`'s
`_source_name` returns the fixed sentinel `"Anonymous"` for any output-instruction source that is
not a plain `Name`/`Index` expression (a `Table.Field` cross-table reference, or a string literal
source) -- not a real field name, just a "cannot derive one" placeholder that is never itself
exercised by the TDF/`-l` output this project has verified byte-identical, so it was never
previously visible as a bug. Two or more such sources in the same table collide on this one shared
sentinel and were flagged as duplicates of each other, even though the real compiler accepts the
file cleanly: `tests/corpus/chm/goestable_1.CR6` (two `Sample`s of a cross-table `HR_DATA.
battery_voltage_Avg`/`HR_DATA.panel_temperature_Avg`) went from accepted to a false reject on all
four loggers, caught by this round's own full `tools/difftest.py --logger <l>` re-run before
landing (AGENTS.md: verify a generalisation against the whole corpus, not a hand-written probe,
before trusting it -- the same methodological finding `docs/checker.md`'s "Round 3" section
records for a different check). Fixed by excluding every field named exactly `"Anonymous"` from
this check on both sides of the comparison (never recorded as seen, never itself flagged) --
conservative in the correct direction, since a name already known not to be a genuine derived name
is not evidence of a genuine collision either way. `ch400status_2.CR6`'s own `CH400Status`
reps-cycling shape (`docs/checker.md`'s "Known remaining gaps") is a *different*, already-documented
gap in `tables.py`'s field derivation (it collapses a 30-rep structured measurement into a single
field, never per-element aliased names) that this exclusion does not touch and does not need to:
that gap only ever *under*-reports here (a single field can never collide with itself), never
over-reports.

Difftest impact of this section (`tools/difftest.py --logger <l> --group all`, before this round's
end-of-session measurement / after, all four loggers, zero false rejects throughout both
measurements): cr6 chm 311/187/9 -> 312/186/9; cr1000x chm 327/172/8 -> 329/172/6; cr300 chm
119/369/19 -> 120/369/18; cr350 chm 123/366/18 -> 124/366/17. `tools/fuzz_replay.py --workers 2`:
the seven false-accept reproducers this section targets by name above all now report "FIXED (now
exact)"; no reproducer of any class newly reports `false_reject`.

### Investigated, not implemented (this section's own scope)

- **`SerialClose`'s own `ComPort` argument**: `examples/SBD_example_hirise.CR6`/
  `SBD_example_hirise_SBDWB.CR6` (the top-level repo's historical `examples/`, read-only, used here
  only as `tools/difftest.py`'s pre-existing third oracle-comparison group, never compiled or
  modernised as production CRBasic per `AGENTS.md`) each real-reject twice with `Invalid port %d,
  use C1..C4 or U1..U12.` (CR6) / `...C1..C8.` (CR1000X) at their own two `SerialClose
  (IRIDIUM_COM_PORT)` call sites, where `IRIDIUM_COM_PORT` folds to `ComRS232`. The same constant
  is used freely at `SerialOpen`/`SerialFlush`/`SerialOut`/`SerialIn` call sites earlier in the same
  file with no error at all -- so this is specifically `SerialClose`'s own argument validation
  being stricter than every other `Serial*` instruction's, not a general "COMRS232 is never a valid
  port" rule. Not implemented: confirming the exact boundary (is it `ComRS232` specifically, or
  every non-hardware/virtual port name?) needs more oracle probing than this round's remaining time
  allowed; left for a future session with this entry as its starting point.
- **GPS ports**: implemented 2026-10-01; the earlier `PPSMethod` interpretation was incorrect.
  The fourth parameter is `MaxTimeDiff`; synchronization mode is selected by the port sign.
  See [GPS ports](#gps-ports) for the measured rule and boundaries.

### GPS ports

Oracle evidence: 88 fresh CR6/CR1000X runs on 2026-10-01, including named and numeric ports,
both port signs, undefined port constants, and `MaxTimeDiff` values -1, 0, 1, 9, 10, 99, 100,
101, 199, 200, 201, 255, 256, 300, 1000. The local reference
`../../extracted_chm/Instructions/gps.htm` names the third/fourth arguments `TimeOffset` and
`MaxTimeDiff`; `../../extracted_chm/parameters/comport5.htm` explains port negation for NMEA
time synchronization. Its COMC1 PPS description is specific to CR1000X, not CR6.

The shared `instr_measure._check_gps_port` handler runs in the existing instruction-validation
tier, with profile data in `data/{,cr1000x}/measure.json`:

- CR6: legal absolute port codes are 1 (`ComRS232`) and 9..16 (`ComC1`, `ComC3`, `ComU1` through
  `ComU11`); a positive port must be 11 (`ComU1`), otherwise
  `GPS pps synchronization is only available on COMU1.`
- CR1000X: legal absolute codes are 1 and 9..12 (`ComC1`, `ComC3`, `ComC5`, `ComC7`); a positive
  port must be 9 (`ComC1`), otherwise `GPS pps synchronization is only available on COMC1.`
- Legal negative ports compile (NMEA mode), independently of the tested `MaxTimeDiff` values.
  Illegal absolute ports, including zero, emit only the logger's legal-port list; they do not
  also emit the PPS message. Undefined names are already pass-1 errors and suppress this tier.

For `Public G(15)` followed by `GPS(G(),ComC1,0,100)` inside a scan, CR6 emits only the COMU1
message; CR1000X compiles. `GPS(G(),-ComC1,0,100)` compiles on both. Numeric port `0` emits
`Only COMRS232, COMC1, COMC3, or COMU1,COMU3...COMU11 are legal.` on CR6 and
`Only COMRS232, COMC1, COMC3..COMC7 are legal.` on CR1000X.

Representative verbatim expected outputs, inputs, observation date, and compiler hashes:
`../tests/fixtures/gps_ports.json`; regression: `../tests/test_gps_ports.py`. All 88 development
probes matched ordered diagnostic output after the handler was added. CR6 help cases
`gps_2.CR6` and `structuretype_3.CR6` now match exactly. CR300/CR350 have a different GPS
signature and are deliberately not covered by this handler. Optional NMEA string-array
validation is a separate remaining rule.

## Investigated, not implemented

- **`%s declared but not used.`** (`FUN_004ba180`, `docs/messages.tsv`) -- probed an unused
  top-level `Const`, an unused top-level `Public`, and an unused `Sub` parameter plus an unused
  `Sub`-local `Dim`; none produced this warning on CR6. Not chased further (no fuzz/corpus
  evidence of it firing anywhere either), so its actual trigger is still unknown; left for a future
  session with a concrete example to start from.
- **CR6's own `Invalid fnotch, minimum allowed is 2 Hz.`** (an *error*, `docs/messages.tsv`
  function `FUN_005166a0`, unrelated to CR300/CR350's warning above) -- confirmed real (binary
  search: `PanelTemp(X,1.4)` fails, `PanelTemp(X,1.44)` compiles, boundary near but not exactly
  the message's own claimed "2 Hz", and `0` is exempt, unlike CR300/CR350's check). Not
  implemented: the true numeric boundary sits at a non-round value (~1.435-1.44) that doesn't match
  the message text's own claimed threshold, and getting it wrong by rounding to a clean "2" would
  falsely reject any real program using a value in `[1.44, 2)` -- not found in the corpus, but not
  worth the false-reject risk for an edge case this narrow (AGENTS.md priority order).
- **`Scan %duSec invalid must be at least %dmSec.` / `Scans must be a multiple of %dmSec.`** --
  **implemented, round 6** (`checker/instr_rules.check_scan_min_interval`, `docs/checker.md` "Round
  6" item 1): the "boundary this session could not pin down cleanly" turned out to have no float-
  precision subtlety at all -- the declared `Interval` truncates toward zero *in its own declared
  unit* before multiplying by that unit's microsecond factor, which reproduces the `7999uSec`-fails/
  `7.9mSec`-compiles asymmetry exactly, with one rule and no per-unit branch.
- **`Maximum Scan interval is 1 Day.`** -- implemented 2026-09-27; see "Scan and table interval
  limits" below.
- **`ExChan must be U1-U12.`** (`BrHalf`/`BrHalf3W`/`BrHalf4W`/`BrFull`/`BrFull6W`'s own
  `ExChan`/`SEChan` positions, `FUN_005147a0`/`FUN_005177a0`) -- already noted as not implemented
  in `docs/checker.md`; not picked up this round either (no corpus mismatch currently needs it).
- **CR6's per-instruction "measurement instruction reps/range/channel/excitation" checks** beyond
  the existing `U1..U12`/CR300-CR350 channel-range check (`checker/terminals.py`) -- picked up in
  the round below ("Measurement/I-O instruction checks").

## Measurement/I-O instruction checks (`checker/instr_measure.py`, `checker/terminals.py`)

Owned by the same workstream as `checker/terminals.py`/`data/<logger>/terminals.json` (the
channel/port *terminal* model) and the new `checker/instr_measure.py`/`data/<logger>/measure.json`
(everything else: range codes, `SW12`, `Delay`, `CS616`, `TimerInput`, `ExciteV`/`ExciteI`).
`instr_measure.check` is pass 2 (`docs/checker.md` "Pass model"), wired into `checker/__init__.py`
alongside `instructions.check`; it emits one diagnostic per statement with that statement's own
line number and relies on no ordering trick of its own -- the record/line-order interleaving with
`expressions.py`'s errors and the placement-error cutoff are both handled centrally in
`checker/__init__.py`. All rules below are oracle-confirmed this round (`python3 tools/oracle.py`
CR6 scratch probes, and direct `wine <logger>comp.exe` scratch probes for CR1000X/CR300/CR350 --
`tools/oracle.py`'s own CLI always runs `CR6Comp.exe` regardless of a `.cr1x`/`.cr300`/`.cr350`
file's extension, so the other three loggers need the direct invocation the task/AGENTS.md
describes).

### Channel/port terminal model fixes and additions (`checker/terminals.py`)

**Real bug fixed, CR6 only (the session's highest-priority finding: an active false reject).**
`VoltDiff`/`VoltSe`/`TCDiff`/`TCSe`'s `DiffChan`/`SEChan` (terminals.py's pre-existing `u_terminal`
kind, unconditionally `transform: "none"`) rejected a burst-mode negative channel value
(`VoltDiff(X,1,mv5000C,-U1,0,0,15000,1,0)`, oracle-accepted) with `Invalid channel -201, must use
U1..U12.` -- confirmed by direct call before any other change this round. Root cause: the CR6
`u_terminal` kind never gained the `transform: "abs"` burst-mode allowance CR1000X's own
`diff_chan`/`se_chan` already had (`docs/loggers.md`). Split into three kinds, all oracle-verified
this round with a `U1`..`U12`/`-U1`..`-U12`/plain-integer-literal boundary sweep on `CR6Comp.exe`:

- `u_terminal_se` (`VoltSe`/`TCSe` position 4): any `U1..U12`, burst negative allowed (`transform:
  "abs"`) -- unchanged range, only the missing burst allowance added.
- `u_terminal_diff` (`VoltDiff`/`TCDiff` position 4): burst negative allowed, **plus** a second,
  previously entirely missing constraint -- the (absolute) value must be an *odd* terminal
  (`U1,U3,U5,...,U11`); an even one in range (`U2`..`U12`) gets a distinct message, `Invalid start
  channel for Differential Measurement. Must be U1, U3, U5 ...` (`terminals.py`'s new `odd_only`/
  `odd_message` fields). `docs/checker.md`'s round-2 entry claiming `VoltDiff`'s `DiffChan` is "a
  plain small integer, not a terminal name" is now known wrong -- that session's own probe used a
  CR1000X CHM example's argument style (`DiffChan=1`) against `CR6Comp.exe` without checking the
  odd/burst rule, and mistook the two-pass gate's suppression of an *unrelated* co-occurring error
  for "no channel check at all"; corrected here with a from-scratch, isolated sweep.
- `u_terminal` (unchanged: `ACPower`, plus newly added `CS616`'s `CS616Chan` and `ExciteV`/
  `ExciteI`'s `ExChan`) keeps the old no-burst, no-oddness shape -- not re-verified for burst/odd
  behaviour on those three, so left conservative rather than guessed.

**New kinds, both oracle-confirmed on CR6 and CR1000X (absent on CR300/CR350, which never validate
either shape -- `PortSet(1,1)`/`PortSet(C1,1)` both compile clean on `cr300comp.exe`/
`cr350comp.exe`):**

- `port_c1c4_u1u12` (CR6: `PortSet` position 1, `PortGet` position 2, `CS616`'s `CS616Port`
  position 4) / `port_c1c8` (same three, CR1000X): a disjoint two-range union, `C1..C4` or
  `U1..U12` on CR6 (`Invalid port {v}, use C1..C4 or U1..U12.`) vs. `C1..C8` only (no `U`-terminal
  at all) on CR1000X (`Invalid port {v}, use C1..C8.`). Closes two long-standing corpus mismatches,
  `examples/SBD_example_hirise.CR6`/`SBD_example_hirise_SBDWB.CR6` (`docs/checker.md`'s own "Known
  remaining gaps": "a check not yet implemented ... likely the same `_check_channel_arg`-shaped
  rule ... but for a 'port' parameter accepting `C1..C4` too").
- `sdi_port` (CR6: `SDI12Recorder` position 2, odd-only `C1,C3` or `U1,U3,U5,U7,U9,U11`, message
  `Invalid SDI-12 port, must be C1,C3 or U1,U3...U11`) / `sdi_port_cr1000x` (same position, odd-only
  `C1,C3,C5,C7` only, no `U`-terminal at all, message `Invalid SDI-12 port, must be C1, C3, C5, or
  C7`) -- CR1000X's 8-control-port hardware exposes twice CR6's own `C1..C4` range for this check
  specifically, oracle-confirmed with a `C1..C8` sweep. CR300/CR350 accept any value here too.
- `excite_channel` (CR6 only: `ExciteV`/`ExciteI` position 1, same `U1..U12` range as `u_terminal`
  but its own message, `Invalid Excitation channel (must be U1-U12).`) -- both this and the shared
  `u_terminal` message fire *together* for the same bad value (oracle: a literal `1` or `C1` gets
  both `Invalid channel 1, must use U1..U12.` and `Invalid Excitation channel...` on the same line,
  in that order), so `terminals.json` lists the position twice, once per kind, rather than
  reworking the model to emit two messages from one entry.

`terminals.py` itself: `_evaluate` now returns `(in_range, odd_ok, shown)` instead of `(is_valid,
shown)`, and a kind's range may be `"ranges": [[min,max], ...]` (checked as a union) in place of a
single `"min"`/`"max"` pair, kept for the pre-existing CR1000X/CR300/CR350 `diff_chan`/`se_chan`
entries. Both changes are additive to the existing per-kind dict shape; no existing kind's
behaviour changed except the two CR6 fixes above.

### `Invalid range code, valid options are: mV5000, mV1000, mV200, or AutoRange.` (CR6/CR1000X only)

`VoltSe`/`VoltDiff`/`TCSe`/`TCDiff`/`CurrentSe`'s own `Range` argument (position 3 on all five,
`FUN_00514690`) is a named constant like any other (type code 1, not `20` -- it folds via the same
fast path as `U1` or `Sec`, so a genuinely undefined name there is `expressions.py`'s concern, not
this one's); this instruction-layered check additionally restricts the *folded value* to
`{mV5000, mV1000, mV200, AutoRange}` and their open-detection `...C` siblings (`data/symbols.json`
values `800,802,804,810,820,822,824,830`) -- every other defined range symbol (`mv2500`, `mv250`,
`mv50`, `mv25`, `mv20`, `mv7_5`, `mv2_5`, `mv10000`, and their own `...c` siblings) is rejected with
this exact message, oracle-confirmed identical on CR6 and CR1000X (`VoltDiff(X,1,mv2500,...)` fails
with this text on both `CR6Comp.exe` and `cr1000xcomp.exe`; `VoltDiff(X,1,mv5000,...)`/`mv1000`/
`mv200`/the `...c` variants all compile clean on both). **CR300/CR350 have no such check at all**:
their own symbol table simply does not define `mv5000`/`mv1000`/`mv200` (`docs/loggers.md`: "CR300/
CR350's *only* two range codes are `mv2500` and `mv34`"), so the same corpus shape surfaces there
as a plain, already-handled `mv5000 is not defined.` (`expressions.py`'s concern) -- oracle-
confirmed this round (`cr300comp.exe`/`cr350comp.exe` both reject `mv5000` as undefined, and both
compile `VoltDiff(X,1,mv2500,...)`/`mv34` clean with no warning).

### `SW12`: `Invalid SW12 port %d.`

An exact, small, per-logger set of valid ports -- both a plain small integer and the logger's own
named `SW12_n`/`P_SW12` constant resolve to *different* raw values but both compile; every other
value in the swept neighbourhood fails, oracle-confirmed by a boundary sweep (`0` through `12`, and
each logger's own named constants, `data/<logger>/symbols.json`) on each of the four compilers:

| Logger | Valid ports | Evidence |
|---|---|---|
| CR6 | `{1, 2, 501, 502}` | `SW12_1`=501/`SW12_2`=502 and their plain-integer siblings `1`/`2` all compile; `0,3..12,501..503` swept and rejected (`SW12_CSIO`=503 also rejected) |
| CR1000X | `{501, 502}` only | same symbol values as CR6, but the plain integers `1`/`2`/`3`/`-1` all **crash the real compiler** under Wine (a wow64 page fault, not a clean rejection -- `docs/checker.md`'s own precedent for `pointervariable_2.CR6`, no oracle text to match); deliberately not modelled, since guessing a message for an input that crashes the oracle risks the exact false-reject/false-accept ambiguity AGENTS.md warns against -- only the two crash-free, confirmed-valid values are in the set, so nothing this checker doesn't understand is ever flagged |
| CR300 | none (`SW12(Port)`, type code 25, no compile-time check at all) | `7`/`1`/`2`/`501`/`3`/`0` all compile clean |
| CR350 | `{1, 2, 9, 10}` | own `SW12_1`=9/`SW12_2`=10 plus plain `1`/`2`; `0,3..8,11,12` swept and rejected |

### `Delay`: three checks, split by `Option`

`Delay(Option, Interval, Units)` (`FUN_0045d530`/`FUN_00450e80`/`FUN_004f11d0`-family). Oracle-swept
per logger (boundary values in `min`/`hr`/`sec` units, and a string/variable `Interval`):

- `Illegal option %d` (all four loggers) when `Option` does not fold to `0`, `1` or `2` -- the shown
  value is the folded number truncated to an unsigned byte (oracle: `Option=-1` shows as `255`,
  `Option=3` shows as `3`, matching Python's own `value % 256`).
- `Delay interval cannot be of type String.` (all four loggers, any `Option`) when `Interval` folds
  to a string constant -- this is the *only* diagnostic for a string interval (oracle: no
  accompanying "must be constant" line, unlike a non-constant *variable* interval).
- Then, by `Option`:
  - `Option=0` (measurement-task-sequence delay) -- **CR6/CR1000X only** (oracle: CR300/CR350 both
    compile `Delay(0,61,min)`/a variable interval clean, no error at all): `Interval` must itself be
    a compile-time constant (`Measurement delay must have constant interval.`) and, once resolved,
    at most 60 minutes inclusive (`60,min` compiles; `61,min` / `3601` seconds worth in any unit
    fails with `Measurement Delay Exceeds 60 min.`).
  - `Option=1` (processing delay) -- **all four loggers**: no constant-interval requirement (a
    variable compiles clean, unlike `Option=0`), but the resolved interval must be strictly less
    than 24 hours (`23.999,hr` compiles; `24,hr`/`1440,min`/`86400,sec` all fail with `Max Delay is
    24 Hours`, oracle-confirmed identical text and boundary on all four compilers).
  - `Option=2` (SDM delay) -- **all four loggers**: `Interval` must be a compile-time constant
    (`SDM Delay interval must be constant.`); no duration limit found (`24hr`/`25hr` both compile).

### `CS616`: `Invalid reps %d, must be > 0` (CR6 only)

`CS616(Dest,Reps,CS616Chan,CS616Port,MeasPerPort,Mult,Offset)`'s `Reps` (position 2) must be a
positive folded constant; `0` and negative values fail with this exact text. Not re-verified on
CR1000X/CR300/CR350 this round (`CS616Chan`/`CS616Port` themselves are covered by the terminal-model
entries above, which *are* per-logger); left CR6-only rather than guessed.

### `TimerInput`: `TimerInput starting channel must be C1 or U1.` (CR6 only)

`TimerInput(Dest,StartChan,Edge,Function,Timeout,Timeout_Units)`'s `StartChan` (position 2) must
fold to exactly `C1` or `U1` -- every other terminal in either family (`C2`, `U2`, ...) fails with
this text, oracle-confirmed with a `C1/C2/U1/U2` sweep. Not re-verified on CR1000X/CR300/CR350.

### `ExciteV`/`ExciteI`: `Excitation exceeds 2500.000000.` (CR6 only)

`ExciteV(ExChan,ExmV,Delay)`/`ExciteI(ExChan,ExmI,Delay)`'s excitation magnitude (position 2) must
have `|value| <= 2500`; `2500` compiles, `2501` (and `-2501`) fail with this exact text (note the
message's own `%f`-style fixed six-decimal formatting of the *limit*, not the input value).
**Deliberately not applied to CR1000X**: oracle-confirmed `ExciteV(Vx1,2501,0)` (CR1000X's own
excitation-channel name) compiles clean at `2501` and even `2500`-plus values were not found to have
any limit in the range swept -- applying the CR6 threshold there would be a false reject, so this
check is CR6-only rather than logger-unconditional. `ExciteI` does not exist as an instruction on
CR1000X/CR300/CR350 at all (`docs/loggers.md`), so it is moot there regardless.

### Investigated, not implemented (this round)

- **`AM25T`'s own channel/excitation checks** -- a boundary sweep of `AM25TChan` (1, 16, 17, 0)
  against `CS1000, AM25T(...)` all reported the *same* `Invalid channel -1, must use U1..U12.`
  regardless of the swept value, meaning some *other* argument (not `AM25TChan`) is what that
  message is actually validating in this probe's shape; not chased further to avoid guessing at an
  argument mapping this round could not isolate cleanly.
- **`PortsConfig`'s `Function` argument** -- swept `0..5` and `-1`, all compiled clean (type code 2,
  no compile-time validation found).
- **`WriteIO`/`ReadIO`'s `Mask` argument** -- swept `0, 255, 256, -1, 1000`, all compiled clean (type
  code 1, no range restriction found).
- **`SDI12Recorder`'s `SDIAddress` argument** -- swept `0, 9, 10, -1, "A", "Z", "z"`, all compiled
  clean (resolved at run time, not compile time).
- **General terminal-allocation-conflict tracking** (`%s: channel %c%d already in use or
  unavailable.`, `FUN_0050cf90`) -- decompiled call-site tracing found this shared validator is
  called from many instructions' own handlers, including ones in this document's scope
  (`TimerInput`, `PulseCount`, the channel-range function `VoltSe`/etc. itself use), but the exact
  conflict semantics (which instruction/position pairs actually collide, and the meaning of each
  call site's own flags word) were not isolated with confidence this round; implementing a plausible
  but wrong version of this risks the worst failure mode (AGENTS.md), so it stays unimplemented.
- **`Resistance`/`PeriodAvg`/`Pulsecount`'s own excitation/gain/reps checks** -- not reached this
  round (time budget); `Pulsecount`'s low-level-AC-pulse "even U channel only" restriction and its
  own reps range are both real (`messages.tsv`, `FUN_00510250`) but not oracle-probed yet.

## SDM addresses (2026-09-28, CR6/CR1000X)

`Invalid SDM Address: %d, must be 0 - 14.` (decompiled `FUN_0043c680`) for a constant address
whose low byte is 15 or more: `SDMX50 (-1,4)` prints `255`, and `SDMX50 (256,4)` is accepted. It
is a pass-2 error that accumulates with others (`instr_rules.check_sdm_address`,
`tests/test_sdm_address.py`). The address position was measured per instruction by setting each
numeric argument of a chm example to 99: 0 for `SDMX50`; 1 for `EC100`, `EC100Configure`,
`SDMGeneric`, `TDR100` and `TDR200`; 2 for `CS7500`, `CSAT3`, `CSAT3B`, `CSAT3BMonitor`, `LI7200`,
`LI7700`, `SDMCD16AC`, `SDMCD16Mask`, `SDMIO16`, `SDMSIO4` and `SDMSW8A`. Not measured: `SDMAO4`,
`SDMAO4A`, `SDMCVO4`, `SDMINT8`, `SDMCAN`. `SerialInRecord`/`SerialOutBlock` reach the same
check only for SDM ports.
## SerialIn destination

The supplied four compiler versions require `SerialIn`'s first argument to be a String
variable or String array element. A Float, Long, Boolean, or UINT1 destination, a numeric or
string literal, a named constant, and an expression such as `Dest+1` produce
`A string variable destination is required` (no final period). String destinations and aliases
of String storage compile; aliases of Long storage fail. Parenthesized String variables compile;
runtime String concatenation and String-producing `FormatFloat`/`CHR` calls fail. This is specific
to `SerialIn`, not a
uniform rule for its parameter type code 33. The local `serialin.htm` reference mentions UINT1
receivers in newer OS behavior, but the supplied executable probes reject them; this documents
the observed versions rather than inferring behavior from that note.

This check belongs to pass 1: it survives an earlier unrelated declaration/shape error and
suppresses CR6/CR1000X pass-2 errors elsewhere in the file. On CR6/CR1000X, missing or excess
arguments still follow the destination error. An undefined destination instead keeps the
ordinary `Invalid symbol in expression: Dest.` result. CR300/CR350 additionally report the
next argument token as `Too many parameters:  ComRS232.`, ending the argument list before any
ordinary count checks. The same destination check still runs on subsequent `SerialIn` statements
after this starts the CR300/CR350 cascade. An undefined bare destination on these profiles
reports the undefined name, then the String-destination error, then the next-token error.

Evidence: 100 fresh observations, recorded with executable SHA256 identities in
`tests/fixtures/serialin_destination.json` and replayed by `tests/test_serialin_destination.py`.
They cover all four profiles, scalar/array types, aliases, constants/literals/expressions,
undefined names, earlier errors, repeated statements, following instructions, and short/excess
argument lists, and String expression boundaries. Structure-member and scoped parameter type
resolution remain separate gaps.
Implementation: `instr_rules.check_serialin_destination` in pass 1, with the existing CR300/CR350
argument-abort and cascade recovery retaining the String-destination diagnostic.

## SetSetting and SetStatus field names

`SetSetting("Name", value)` and `SetStatus("Name", value)` look their constant first argument up
in the logger's field tables (`FUN_004610e0`). A name that is not found gives
`Name is not a Settings Field Name.` (`SetSetting`) or `Name is not a Status or Settings Field
Name.` (`SetStatus`). `Name` is printed exactly as written, including any trailing blanks, and an
empty string prints `NULL` (CR6, CR1000X) or `(null)` (CR300, CR350).

Field tables (`data/**/settings_fields.json`). The Settings table is a pointer array in each exe
(entry stride 0x4c on CR6/CR1000X, 0x48 on CR300/CR350; the entry's first word points at the name).
Every candidate name from the four exes' tables plus a few extras was then compiled through
`SetSetting` and `SetStatus` on each logger, 40 names per program. `SetSetting` accepts exactly
the Settings table (CR6 257 names, CR1000X 259, CR300 209, CR350 232). `SetStatus` accepts the
Settings table plus the Status table (additional names: 55, 52, 40, 50). The lists differ per
logger: `CPUDriveFree` is a Settings name on CR6/CR1000X/CR300 but a Status-only name on CR350,
`USRDriveSize` is a Settings name on CR6/CR1000X and in neither list on CR300/CR350, and
`DisableTcpDelayedAck` is in no list. The walked CR300/CR350 Status tables also contain six
`DataTableName`-style fields that are rejected, so the lists come from the oracle result, not from
the walk. Status-only names such as `Battery` or `OSVersion` give
`is not a Settings Field Name.` under `SetSetting`.

Spelling rules, identical on all four loggers unless noted:

- Matching is case-insensitive; the message echoes the source spelling.
- CR6/CR1000X ignore trailing spaces and tabs when matching (`"PakBusAddress  "` is accepted);
  CR300/CR350 do not (`PakBusAddress   is not a Settings Field Name.`). Leading blanks are never
  ignored. Other trailing characters (`\x0b`, `\x0c`, `\xa0`, `\x01`) are not trimmed.
- The name must be a compile-time constant: a string literal, a String `Const`, or `+` of those
  (`"Pak"+"BusAddress"` is accepted, `"Pak"+"Foo"` reports `PakFoo`). `CHR(9)`, variables and
  non-strings give `Status Field name must be constant.` (not implemented, so such arguments are
  skipped).
- `Name(index)`: only the text before the first `(` is looked up (`Foo(1)` reports `Foo`,
  `DNS(1)`, `Security(2)` pass). The index is checked afterwards (`Illegal dimension N`, depends on
  the field's size, not implemented). An empty name before `(` is skipped.
- A name containing `.` reports `Cannot find <text before the first dot>` (no final period) for
  every table name tried, including `Status`, `Settings` and a `DataTable`. A declared
  `ConstTable` prefix (`ScanConstTable.ScanConst`, as in the repository's `PowerProfile_tester.CR6`)
  is valid; its field lookup is not modelled (CR300/CR350 print `Cannot find <field>` for an
  unknown field; CR6/CR1000X crashed under Wine on those probes, so there is no CR6 evidence).

Ordering and gating (CR6/CR1000X versus CR300/CR350):

- It is not a pass-1 check: an earlier or later declaration or pass-1 error (`Variable Type
  Bogus not supported`, `Too many parameters`, wrong argument count) suppresses it, anywhere in
  the file.
- CR6/CR1000X: a tier-B finding (`Invalid symbol in expression`) does not suppress it, and it does
  not suppress later tier-C findings (`Invalid SW12 port 9.` appears together with it). On one
  line the argument error comes first (`Invalid symbol in expression: Bar.` then `Foo is not a ...`).
  It is hidden after a placement stop (`Sw12 illegally occurs before BeginProg.` on an earlier
  line; CR6 only, the CR1000X compiler crashed under Wine on that probe) and after `Could not find DataTable X.` on an earlier line; before such a line it is
  reported. The unclosed-block end checks (`IF or SELECT without EndIf or EndSelect.`) do not run
  once it has fired.
- CR300/CR350: an undefined name in the same statement prints only `Bar is not defined.`; any
  earlier or later non-`CallTable` error (undefined name, pass-1 error) hides the field-name
  error, while the `Invalid port specified 103` tier-C error and `Could not find DataTable` do not.
  Once a hiding error has started the `Too many parameters` cascade (`checker/cr3xx.py`) the
  later `SetSetting` line prints `Too many parameters:  5.` instead. A field-name error does not
  itself start the cascade.
- Applies inside `Sub`, `Function`, `SlowSequence`, `Scan`, one-line `If` and before `BeginProg`
  (a constant `SetSetting` before `BeginProg` is accepted when the name is valid). Statements
  after `EndProg` are not checked.

Not implemented (observed, separate rules): `X is read only.` (valid but read-only fields, for
example `SetStatus("Battery", 5)` and `SetSetting("CPUDriveFree", 5)`), `Status Field name must
be constant.`, `Illegal dimension N`, ConstTable field lookup, and the `(1)` name whose message
prints `1)`.

Evidence: 474 compiled programs plus an eight-part name sweep in `tests/fixtures/settings_fields.json` (compiler SHA-256 values and
observation date 2026-10-02 inside), replayed by `tests/test_settings_fields.py`. They cover all
four profiles and the candidate-name acceptance sweep. Probe sources are not kept. The ConstTable
and `Name(index)` rules above are partly outside the fixture (`Illegal dimension`,
ConstTable field lookup) and are described from the same-day probes only.
Implementation: `instr_rules.check_settings_fields`, called from `checker/__init__.py`
(CR6/CR1000X in the tier-B block, CR300/CR350 in the late section), findings collected aside so
tier C is not hidden.

## Variable data types (`checker/var_types.py`)

Per-instruction type requirements on variable arguments, pass 2 tier C (run after
`check_dest_bounds`; findings sort by line, so a bounds error on the same statement stays first).
There is no uniform rule per type code: the "variable-required" codes 27/29 only say a variable is
needed, and each instruction then applies its own type test. Fixture
`tests/fixtures/var_types.json` (real-compiler output, compiler SHA-256 and date 2026-10-02 inside),
replayed by `tests/test_var_types.py`.

- `RoutersNeighbors(Dest)` (all four loggers): the destination must be a `Long` variable or array
  element, else `Destination variable must be type LONG`. A string literal gives the same message;
  a number literal is still `Invalid Variable or Array`. Float, String, Boolean and Double all fail.
- `SortSpaIndexed(Dest,Reps,Source)` (CR6/CR1000X; CR300/CR350 lack the instruction): `Source and
  Dest types must be the same.` unless the types are equal, with one oddity, a String `Dest` with a
  Float `Source` is accepted. `Source` is an expression with an inferred type: an integer or `&H`
  literal, `Const` integer, `TRUE`/`FALSE` and `+ - *` of Longs are Long; a literal with `.` or `E`,
  `NaN`, a `/` and any mix with a Float are Float; a String literal is String; variables (through
  `Alias`) have their declared type. Functions, comparisons and other operators are not typed
  (`Int(3.5)` is Float for the compiler but unknown here, so it stays silent). A scalar `Source`
  also draws an out-of-bounds error (destination-bounds rule, not here).
- Parenthesised dimension (all four loggers): a `(` anywhere in a variable's dimension list,
  `Public A((1+2),5) As Long`, `A(3,2*(1+1))`, `A(Abs(3))`, makes the variable Float whatever its
  `As` type (seen through `RoutersNeighbors` and `SortSpaIndexed`; String and Boolean too). The
  parser sets `VarDecl.paren_dim` and `symbols.declared_variable_types` returns FLOAT for it. The
  dimension following the type (`As Float(5)`) was not probed.

Probed, not implemented: `Median` of a String (`Type must be Float, FP2 or Long`),
`Sample(...,Bool8)` with Reps 1.

### `Sample` with data type `Nsec`

Same module, all of CR6/CR1000X/CR300/CR350 (fixture cases `*-sample-nsec-*`). `Sample(Reps,Source,
Nsec)` with a String, Boolean or Double source (variable, element, array, or a String expression
such as `S+"x"`) gives `Source type must be either LONG or FLOAT or NSEC for data type NSEC`. A
Float source (variable, literal or expression; an expression counts as one element) needs the
variable's whole array to hold at least `7*Reps` elements, else `Float to NSEC expects source array
size >= 7` (`FB(7)` Reps 1 passes, also as `FB(2)`; Reps 2 passes for 14 elements, fails for 13).
This one is skipped when the statement already has an error (an out-of-bounds finding replaces it).
Long sources and integer literals pass. Only `Sample` has the rule: `Maximum(1,S,Nsec,...)` compiles.

### SDMCD16Mask/SDMCD16AC, SNMPVariable, MenuRecompile

Same module (fixture cases `*-sdmcd16-*`, `*-snmpvariable-*`, `*-menurecompile-*`).

- `SDMCD16Mask(Source,Mask,Command)` (CR6/CR1000X): a Boolean `Mask` or Boolean `Source` variable gives
  `Source and Mask type must be LONG.`; Float, String, Double, Long, literal masks and expression
  sources all pass. A literal or `Const` `Source` (also `SDMCD16AC`) gives `Invalid Source.`, after the
  type message when both apply. A Boolean `Source` additionally gets `Variable X out of bounds.`, which
  is not modelled (so that shape stays a message difference).
- `SNMPVariable(Name,OID,Type,...)` (a dotted OID is an `Unparsed` statement, so the rule reads its
  text): `Miss match of types` when the declared variable type and the `Type` keyword disagree. Float
  variables accept only `Float`; String variables `String` or `Float`; Long, Boolean and Double
  variables anything but `String`. Omitted type always passes. On CR300/CR350 the message also starts
  the `Too many parameters` cascade (`cr3xx._is_cascade_trigger`); `RoutersNeighbors` does not.
- `MenuRecompile(Text,Var)`: a String variable, element or `Const` string gives `String type variable
  not allowed.`; `MenuItem`/`DisplayValue` accept strings. A numeric `Const` gives `Invalid Variable or
  Array: A.` (not modelled here).
