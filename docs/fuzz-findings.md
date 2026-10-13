# Fuzzing findings (phase 4)

`tools/fuzz.py` mutates programs the real compiler accepts (`crbasic/*.CR6` with their `.crb`
siblings, and the oracle-accepted subset of `tests/corpus/chm/`) and compiles each mutant with
both opencrbasic and the real compiler (`tools/oracle.py`, cached). `tools/fuzz_replay.py`
re-checks a saved reproducer or the whole saved corpus without re-fuzzing; its `--prune-fixed`
deletes each reproducer's `.CR6`/`.json` pair once opencrbasic now matches the *recorded*
real-compiler output exactly, without re-invoking Wine. Both are re-runnable:

```
python3 tools/fuzz.py --logger cr6 --count 1500 --seed 1
python3 tools/fuzz.py --logger all          # cr6 1500, cr1000x/cr300/cr350 300 each, seed 1
python3 tools/fuzz_replay.py                # re-check every saved reproducer
python3 tools/fuzz_replay.py --logger cr350 tests/corpus/fuzz/cr350/hang/*.CR6
```

The 248-reproducer result and first-pass counts below are historical observations from the run that
produced this document. They describe that corpus at that revision and must not be read as current
parity totals. Use a fresh `tools/fuzz_replay.py` report for current counts and classes.

## Worker isolation

`run_ours` executes each opencrbasic compile in a spawned child process on every platform. Spawn
starts a fresh interpreter rather than inheriting the parent process's threaded state and locks.
Python documents the process start methods in the
[multiprocessing contexts documentation](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods).
The original fork measurements below motivated that choice, but they do not establish inherited
locks as the cause of every false hang.

An earlier implementation used `fork`; a 1,949-case replay then reported nine hangs even though
direct compilation of all nine completed in 0.000–0.024s. Replaying those nine with spawn produced
five exact results, one `false_accept`, three `message_diff`, and zero hangs. The completed full
spawn replay reported 1,101 exact, 117 `false_accept`, 697 `message_diff`, 14 `mode_diff`, 19
`oracle_error`, and one `hang`: `tests/corpus/fuzz/cr350/false_accept/165684c6e47d.CR6`. That case
completed in under 0.6s when rerun alone and changed to `message_diff`. These measurements show
that the timeout was not a deterministic compiler hang.

A separate parent-side lifecycle race was reproduced with spawn and the old Queue protocol. The
focused eight-call parallel test failed on its tenth repeated pytest run, yet the failing pytest
process finished in 0.30s. Instrumentation showed `Process.join(8)` return after 0.064s while
`is_alive()` was still true and the Queue already held the complete correct compiler result. The
parent therefore labelled a completed compile as an 8s hang without waiting 8s. `run_ours` now
uses a one-way Pipe, waits for either a result or the actual `time.monotonic()` deadline, and has no
Queue feeder thread. Its focused validation ran 40 parallel spawns, a worker exiting without a
result, and a 128 KiB pipe result; all four tests passed in three consecutive focused runs.

A subsequent full replay with `--workers 2` completed all 1,949 cases with 1,101 exact, 117
`false_accept`, 698 `message_diff`, 14 `mode_diff`, 19 `oracle_error`, and zero `hang`, `crash`, or
`false_reject` results. A fresh full four-worker replay remains pending. See `PROGRESS.md` for the
current implementation and verification status; this document retains the evidence and
interpretation from the fuzzing work.

## Method notes (read before the counts)

- **Classify counts are exact; saved-reproducer counts are a deduplicated sample.** Each run
  classifies every mutant it generates (the `classified N mutants -> ...` counts below are exact,
  complete tallies). Saving a minimal reproducer for *every* non-exact mutant, as literally
  instructed, does not scale once one systematic gap produces hundreds of near-identical mutants
  (this run's dominant case: `false_accept` from the checker's known-incomplete instruction-specific
  value checks, PROGRESS.md's phase-3 state). `tools/fuzz.py` instead deduplicates by a coarse
  signature (class + the real compiler's own detail text, digits and quoted text generalised) and
  keeps up to `--max-per-signature` exemplars (default 3, generous 10-20 for `false_reject`/`crash`/
  `hang`). `tests/corpus/fuzz/summary-<logger>.json` records the true classify-phase counts
  alongside the saved count, and this document's counts table is copied from those files.
- **The `cr6` run's save phase was cut short deliberately.** Deduplication still left several
  hundred distinct `(class, signature)` groups for `cr6`'s 1500-mutant run (`false_accept` alone
  spans most of the corpus's seed files); shrinking and saving all of them was not worth the real-
  compiler time it would cost. The scripted run was stopped once diminishing returns were clear;
  its exact classify-phase counts (all 1500 mutants, none skipped) are preserved, and the one
  `crash` and one `false_reject` it found were specifically recovered from the same deterministic
  mutant set so neither was lost. `summary-cr6.json`'s `note` has the detail.
- **`tools/fuzz.py`'s own `replace_argument`/`drop_argument`/`add_argument` operators split a
  call's arguments on every comma, not honouring commas inside a string literal.** This is a real,
  narrow gap in the fuzzer (not opencrbasic) worth fixing before the next run of these operators;
  it produced the `cr350` `false_reject` pair below, which is still a genuine, reproducible
  divergence but arose from an unintended argument split (`"private,public"` -> two "arguments").

## Historical first-pass counts per logger and class

These are preserved as evidence from the original fuzz pass, not as a current status report.

| Logger | mutants | exact | false_reject | false_accept | message_diff | mode_diff | crash | hang | saved |
|---|---|---|---|---|---|---|---|---|---|
| cr6 | 1500 | 925 | 1 | 433 | 135 | 5 | 1 | 1\* | 162 |
| cr1000x | 300 | 192 | 0 | 83 | 24 | 1 | 0 | 0 | 62 |
| cr300 | 300 | 127 | 0 | 85 | 32 | 54 | 2 | 0\*\* | 12 |
| cr350 | 300 | 134 | 2 | 70 | 35 | 58 | 0 | 1 | 12 |

\* cr6's own 1500-mutant RNG stream did not happen to draw the array-dimension mutation that
triggers the hang; the `cr6/hang/` reproducer is a hand-added copy of the case actually found while
fuzzing `cr350`, confirmed to reproduce for `cr6` too (shared `tables.py` code, `summary-cr6.json`).
\*\* Confirmed logger-independent by direct test (below); `cr300`'s own 300-mutant stream did not
draw it either.

`false_reject` (real compiler accepts, we reject) and `crash`/`hang` (opencrbasic never produces a
usable diagnostic) are the severe classes; they total 7 across all four loggers (3 false_reject, 3
crash, 1 hang -- cr6's own 1500-mutant stream did not separately draw the hang case, see the table
note), against 671 `false_accept` and 344 `message_diff`/`mode_diff` instances that mostly
reconfirm already-documented incompleteness (`docs/checker.md`). Findings below are grouped by root
cause, most severe first.

## 1. Historical hang: an oversized `Public`/`Dim` array dimension made opencrbasic never return

This was an observed compiler bug in the original run: the compiler did not fail or return, and
the case remained running past 60s in an isolated, unloaded single-file test. It has since been
fixed; the saved oversized-array cases now complete without hanging. The worker timeout remains to
contain unknown future non-termination rather than because this known case is still open.

Reproducer: `tests/corpus/fuzz/cr350/hang/dd9cc650482d.CR6` (identical copy at
`tests/corpus/fuzz/cr6/hang/dd9cc650482d.CR6`), minimized by hand to:

```crbasic
Public X(999999999999) As Long

BeginProg
  Scan (1,Sec,3,0)
  NextScan
EndProg
```

Traced with a `SIGALRM`-interrupted stack dump: `compiler.py:59 checker.check` ->
`checker/__init__.py:74 tables_hook.attach` -> `tables.py:940 build_tables` ->
`tables.py:1018 render_table_list` -> `tables.py:969 _explode_indices`, stuck inside
`for d, stride in zip(dims, strides)` (or the index-enumeration loop right after it): building the
`-l` table-list text enumerates every element of every declared `Public`/`Dim` array, with **no
upper bound checked on the declared dimension before that enumeration starts**. A
`999999999999`-element array is an attempt to iterate ~10^12 times; it never finishes in practice.

The real compiler rejects the same input immediately and cheaply:

```
line 1: Illegally dimension: Public X(999999999999) As Long
```

(verified for both `CR6Comp.exe` and `cr350comp.exe`, oracle output above). The historical analysis
identified two necessary protections: a value-range check on a `Public`/`Dim` array dimension
before downstream use, and a bound preventing `_explode_indices`/`render_table_list` from blindly
enumerating an impractically large declaration. The current implementation now completes the saved
cases; this paragraph records the original failure analysis rather than an open fix request.

## 2. Crash: a non-numeric `Scan` interval crashes `float()` in `tables.py`

Reproducers: `tests/corpus/fuzz/cr6/crash/ce0f811dd89e.CR6`,
`tests/corpus/fuzz/cr300/crash/237270eb6999.CR6` and `.../f3505c8f84ae.CR6` (three independent
mutants, two different seed files, same root cause). Minimal shape:

```crbasic
BeginProg
Scan("fuzz", sec, 1, 0 )
NextScan
```

`opencrbasic` raises `ValueError: could not convert string to float: 'fuzz'`. Traced to
`tables.py`'s `_scan_weights` (`docs/fuzz-findings.md` note: same file as the hang above, a
different function):

```python
if kw == "SCAN":
    secs = None
    if s.args:
        val = _fold(s.args[0], env, None)
        if val is not None:
            unit_name = ...
            secs = float(val) * _UNIT_SECONDS.get(unit_name, 1)
```

`_fold` constant-folds `Scan`'s first argument; when it is a string literal, `_fold` returns the
Python string itself (a legitimate fold, just not a number), and `float(val)` is called
unconditionally. This function runs for *every* `Scan` block regardless of whether any `DataTable`
is ever `CallTable`d inside it (it unconditionally pushes a weight onto its scan-nesting stack), so
the crash is reachable from the smallest possible program, not just ones with auto-sized tables.

The real compiler rejects this cleanly and specifically:

```
line 1: Scan Interval parameter must be a non-negative integer
```

(`cr6`'s oracle output for the 4-line seed shown in the reproducer's `.json`; `cr300`'s and
`cr1000x`'s equivalent message differs slightly, see the `false_accept` entries in section 6 that
hit the same missing check without also crashing, e.g. `Scan 0uSec invalid must be at least
1mSec.`). The general fix -- a `Scan`/`DataInterval` interval-value check before any downstream
code assumes a numeric, non-negative value -- is the single highest-leverage item in this report:
it would close the hang above, this crash, and a meaningful slice of the `false_accept` volume in
section 6, all at once.

## 3. False reject: `IncludeSection` with an extra argument is silently dropped

**The most important class per the task brief (real compiler accepts, we reject), and the more
clear-cut of the two found.** Reproducer:
`tests/corpus/fuzz/cr6/false_reject/28b79b4c2e87.CR6` (derived from `crbasic/Preflight_tester.CR6`;
needs its `crbasic/` siblings, marked with the `' fuzz-needs-siblings: crbasic` first line).

The `add_argument` mutation turned one of the file's real `IncludeSection` calls into:

```crbasic
IncludeSection("Rail", "CPU:BenchLib.crb",(1+2))
```

Real compiler: `28b79b4c2e87.CR6 -- Compiled in SequentialMode.` (accepts, extra argument
tolerated). opencrbasic: five cascading `Sub SetRail not defined.` errors, because `SetRail` is
defined in `BenchLib.crb`'s `"Rail"` section, which was never included. Root cause:
`source.py`'s `_INCLUDE_SECTION_RE`,

```python
_INCLUDE_SECTION_RE = re.compile(
    r'^\s*IncludeSection\s*\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)\s*$', re.IGNORECASE
)
```

requires the statement to end (`\)\s*$`) right after the second quoted string, so a call with a
third argument does not match at all and is not expanded as an include -- it falls through to being
parsed as an ordinary (and here, meaningless) statement instead, silently. The regex needs to
tolerate (and ignore, matching the real compiler's own apparent behaviour) trailing arguments after
the two it uses.

## 4. False reject: `ESSInitialize`'s argument is parsed as a full expression, not raw text

Reproducers: `tests/corpus/fuzz/cr350/false_reject/71f884967047.CR6` (from
`chm/essinitialize_1.CR6`) and `tests/corpus/fuzz/cr350/false_reject/5e3a202b1dad.CR6` (from
`chm/snmpvariable_1.CR6`) -- two instances, same shape. **Caveat first**: both were produced by
`drop_argument` naively splitting `ESSInitialize("private,public")` on its embedded comma (see
"Method notes" above), leaving `ESSInitialize(public")` -- a string missing its opening quote. That
said, the divergence itself is real and reproducible:

```
ours:   line 7: Invalid, or out of place expression: ESSInitialize(public").
theirs: essinitialize_1.CR6 -- Compiled OK.        (no warning at all)
```

`ESSInitialize`'s one parameter is type code 127 (`data/type_codes.json`: `"kind":
"variable_or_array"`, `"desc": "Optional expression/reference (GPS, ModBus family,
GetDataRecord)"`). The real compiler evidently does not lex/parse this position as a normal
quoted-string expression at all -- a stray `"` inside it is harmless -- while opencrbasic's parser
runs its general expression grammar there and rejects the malformed quote. This matches
`docs/type-codes.md`'s documented pattern of parameter kinds that capture raw source text rather
than a resolved expression (`checker/expressions.py`'s docstring already excludes several such
kinds from its undefined-symbol check for the same reason); `variable_or_array` for this specific
instruction looks like another one, worth confirming with a clean (non-fuzzer-mangled) oracle probe
before changing anything.

## 5. Message diff: missing `Invalid Variable declaration %s.` (very well evidenced, easy fix)

Eight independent mutants (different seeds, different mutation operators) hit the same shape:
a `Public`/`Dim` declaration's `As <Type>` clause has its `As` keyword corrupted (mutated case,
a stray inserted character, or a `rename_identifier` hit on the bare word `As`) --
`ours="asX"`, `"as_Fz"`, `"As_Mut"`, or similar. Examples (all in
`tests/corpus/fuzz/cr6/message_diff/`):

```
e40ebc09f2d0.CR6: ours   line 2: Invalid, or out of place expression: Public StringRight as String * 50, StringLeft asX String * 50.
                  theirs line 2: Invalid Variable declaration asX.
f500a368ae08.CR6: ours   line 18: Invalid, or out of place expression: Public PTemp, batt_volt, counte  r As Long.
                  theirs line 18: Invalid Variable declaration r.
ff612d56267a.CR6: ours   line 23: Invalid, or out of place expression: Public Count as_Fz Long.
                  theirs line 23: Invalid Variable declaration as_Fz.
05a9dd650a73.CR6: ours   line 2: Invalid, or out of place expression: Public pppSocket As Long,pppIP As_Mut String, ...
                  theirs line 2: Invalid Variable declaration As_Mut.
```

The real compiler always reports `Invalid Variable declaration <the bad token>.` and stops (one
diagnostic; see section 6). opencrbasic falls back to the generic "not a valid statement" message
(`structure.py`) with the whole echoed statement text, because it has no specific rule for "a
`Public`/`Dim` declaration's type-clause keyword doesn't match a known type name" -- a direct
sibling of the `Variable not named.`/`Units`/`Const`/`Alias` truncation rules `structure.py`
already implements for other malformed-declaration shapes (`docs/checker.md`). This is the single
highest-confidence, lowest-risk fix in this report: four corpus-distinct mutants, one exact real
message format, a natural place to add it (`structure.py`, next to the existing declaration
checks).

## 6. Message diff: "one diagnostic, then silence" (extends a known open question)

`docs/checker.md`'s "Open questions" already documents that the real compiler sometimes reports
*one* error and stops for the rest of the file, in a shape not fully characterised ("no
discriminator found in either round"). Fuzzing turned up several more concrete instances, all
sharing the pattern "our checker keeps going and reports N errors past the real compiler's one":

```
tests/corpus/fuzz/cr6/message_diff/71830a056193.CR6 (chm/instr_1.CR6, add_argument+rename_identifier)
  ours:   line 2: NextScan with no Scan.
          line 2: No BeginProg in Program.
  theirs: line 2: NextScan with no Scan.

tests/corpus/fuzz/cr6/message_diff/01c9fc1f33a8.CR6 (chm/filereadline_1.CR6)
  ours:   line 2: Invalid, or out of place expression: ...NextSca  n.
          line 2: No BeginProg in Program.
  theirs: line 2: Invalid, or out of place expression: ...NextSca  n.
          line 2: Expr illegally occurs before BeginProg.        <- even the SECOND message differs

tests/corpus/fuzz/cr6/message_diff/05741609c1fd.CR6 (chm/spiread_1.CR6, drop_argument+replace_argument)
  ours:   line 2: Invalid symbol in expression: UndeclaredFuzzName123.
          line 2: Invalid symbol in expression: Dest.
          line 3: Invalid symbol in expression: Z.  (+ more)
  theirs: line 2: Invalid symbol in expression: UndeclaredFuzzName123.
          line 2: Invalid symbol in expression: Dest.
          line 2: MoveBytes illegally occurs before BeginProg.
```

The `01c9fc1f33a8` case is a useful new data point for the open question: it shows the real
compiler's *second* message is context-dependent (`No BeginProg in Program.` in one shape,
`Expr illegally occurs before BeginProg.` in another) in a way that isn't just "stop after the
first error" -- something about *which* statement triggered the first error changes which follow-up
message it emits. Left for the checker workstream owning `docs/checker.md`'s open question, with
these three as fresh, minimized inputs.

## 7. Mode/warning diff: `warnings.py` implements one warning kind of at least five

Every `mode_diff` reproducer (both compilers accept, output text differs) found in this run is a
warning opencrbasic simply never emits -- `warnings.py` currently implements only
`Table %s, is declared but never called.` (`docs/checker.md`). Four more distinct, real warning
texts turned up:

```
tests/corpus/fuzz/cr300/mode_diff/3a84dfa4d9ab.CR6, tests/corpus/fuzz/cr300/crash/237270eb6999.CR6 (theirs)
  Warning: Invalid fnotch value, must be 50,60 400 or 4000

tests/corpus/fuzz/cr6/mode_diff/19a402de98a4.CR6 (repo/PowerProfile_tester.CR6)
  Warning: Delay never reached at run time.

tests/corpus/fuzz/cr6/mode_diff/54f0b2ff38cf.CR6 (chm/sdmspeed_1.CR6)
  Warning: When Main Sequence Scan 1 is running, background calibration will be disabled.

tests/corpus/fuzz/cr6/message_diff/caf5c15cd16b.CR6 (chm/angledegrees_1.CR6)
  Warning: Expr never reached at run time.   (appears even alongside a real error, on a Compile Failed run)
```

`mode_diff` accounts for the largest single class on `cr300` (54 of 300 mutants) and `cr350` (58 of
300) specifically because those two loggers' seeds lean on `fnotch`-checked instructions more; the
same missing-warnings gap is logger-independent (shared `warnings.py`). No single fix closes all
four -- each is a distinct rule (a value-range check, a static reachability analysis, a sequencing
note, a second reachability check) -- but they are a natural, bounded next-checklist item for
whoever owns `warnings.py`.

## 8. False accept: mostly already-tracked incompleteness, volume-confirmed

671 `false_accept` instances (`433 + 83 + 85 + 70`) are, on inspection, not new discoveries so
much as this run volume-confirming `docs/checker.md`'s own stated scope ("instruction-specific
argument checks" and "value never range-checked" are explicitly future work there). Grouped by
theme, most actionable first:

- **`Scan`/`DataInterval` interval value never checked at all** -- the same root cause as sections 1
  and 2, just without also crashing: `Scan Interval parameter must be a non-negative integer`
  (`cr6`), `Scan 0uSec invalid must be at least 1mSec.` (`cr1000x`), `Scan Count parameter must be a
  non-negative integer` (`cr6`, a `doloop_1.CR6`-derived mutant). Fixing this closes the hang, the
  crash, and this whole family in one pass.
- **Argument-count upper bound not checked** -- `instr_rules.py` checks a required parameter is
  *present* but nothing checks there aren't *too many*: `Too many parameters:  <name>.` appears
  repeatedly after `add_argument` mutants (`cr6/false_accept/5fc149f5044d.CR6`,
  `cr1000x/false_accept/16418626ba39.CR6`).
- **No duplicate-declaration / duplicate-structural-block check** -- `duplicate_line` mutants that
  double a `Public`/`Dim` or a `BeginProg` are silently accepted; real messages:
  `<name> already declared as a variable.`, `Multiple BeginProgs.`
  (`cr350/false_accept/53a64b574360.CR6`, `cr1000x/false_accept/02ec158b4a77.CR6`).
- **Assorted single-instruction value checks**, each low-value alone (matches
  `docs/checker.md`'s "Known remaining gaps" framing): `FileManage invalid drive: %s.`,
  `No Matching, or out of place ) in expression: %s` after a mismatched paren from `add_argument`,
  a handful more visible in `tests/corpus/fuzz/*/false_accept/*.json`.
- The remainder (a large fraction of the total) is `expressions.py`'s documented narrow scope: a
  name used inside a nested expression, an array index, or as a `SplitStr`/`FileReadLine`-style
  argument that the mutator swapped for an undeclared name, none of which the current
  "direct-instruction-argument-only" undefined-symbol check reaches (`checker/expressions.py`'s own
  docstring already lists this as not implemented).

None of these are false rejects, so per this project's own stated priority order
(`docs/checker.md`: never reject an accepted program, first; exact match, second) they are lower
urgency than sections 1-6, but the `Scan`/`DataInterval` value check is worth pulling forward since
it is also the hang and crash fix.

## Round 5: fresh seeds 201-208 (2026-10-08)

New seeds, not used before (earlier rounds used 0, 1, 42-54, 100). Run with `--workers 2` because
Wine was shared. Counts are the tool's classify-phase tallies for each run; `oracle_error` mutants
and seeds are real-compiler page faults (exit 5) or 60 s timeouts, which are no verdict and are
excluded from every other class (`tests/corpus/fuzz/summary-<logger>.json` lists them; the tool
overwrites that file per logger, so only the second run of each logger survives there).

First pass, run before any fix of this round (reproducers saved: 92):

| Seed | Logger | Mutants | exact | false_reject | false_accept | message_diff | mode_diff |
|---|---|---|---|---|---|---|---|
| 201 | cr6 | 500 | 474 | 2 | 8 | 13 | 1 |
| 202 | cr1000x | 400 | 384 | 0 | 2 | 13 | 0 |
| 203 | cr300 | 400 | 374 | 1 | 4 | 21 | 0 |
| 204 | cr350 | 400 | 372 | 0 | 8 | 19 | 0 |

Second pass, run while the fixes listed below were being added to the working tree (so the three
false-reject shapes could not recur; reproducers saved: 125; 216 new reproducers in total, one hash recurring):

| Seed | Logger | Mutants | exact | false_reject | false_accept | message_diff | mode_diff |
|---|---|---|---|---|---|---|---|
| 205 | cr6 | 1000 | 953 | 0 | 12 | 33 | 0 |
| 206 | cr1000x | 600 | 571 | 0 | 6 | 19 | 3 |
| 207 | cr300 | 600 | 562 | 0 | 3 | 35 | 0 |
| 208 | cr350 | 600 | 576 | 0 | 2 | 21 | 0 |

### False rejects (3, all minimised, all closed in the fix commits of this round)

- `cr300/false_reject/08ef74be9238`: `End  Table` (also `End Table`) closes a table on all four
  loggers; opencrbasic rejected `DataTable ... EndTable not matched.`.
- `cr6/false_reject/ab0c3e6336e7`: `CS7500(...,999999999999,6)`: opencrbasic reported
  `Invalid SDM Address: 255`, the real compiler accepts (a float32 / Long-range rule).
- `cr6/false_reject/2a20e3cd2f86`: `Scan interval too short for measurement by 1 Sec.` reported
  although the program is in SequentialMode, where the real compiler never reports it.

### Other families seen (not all closed)

False accepts with a clear single rule: `SubScan without Next SubScan.`, `Maximum() or Minimum()
must precede SampleMaxMin().`, `Optional Count parameter must be type long.` for `TotalRun` and
`StdDevRun`. A mode_diff: `Call never reached at run time.` (we said `Expr`). Not attempted this
round (each needs a per-instruction model): `Temperature unit unsupported` and `Timeout: -1, out of
range` / `Input channel ... out of range` on `CDM_*` instructions, `SampleFieldCal with no FieldCal in
program.`, `Duplicate Alias` with a negative index, `Destination variable cannot be indexed`
(CR350), `Illegal Matrix` (CR350), storage-space errors (CR350), plus the many help-corpus
message-order families owned by other workers.

## Round 9: fresh seeds 301-308 (2026-10-10)

New seeds (previous: 0, 1, 42-54, 100, 201-208), 250 mutants each, `--workers 2`. Counts are the
tool's classify-phase tallies; `oracle_error` mutants (real-compiler page fault or timeout, no
verdict) are excluded from every class. 125 reproducers saved (hash-named, not edited afterwards).

| Seed | Logger | exact | false_reject | false_accept | message_diff | mode_diff | oracle_error |
|---|---|---|---|---|---|---|---|
| 301 | cr6 | 234 | 1 | 4 | 11 | 0 | 2 |
| 302 | cr6 | 240 | 0 | 0 | 10 | 0 | 1 |
| 303 | cr1000x | 231 | 0 | 6 | 12 | 0 | 3 |
| 304 | cr1000x | 246 | 0 | 0 | 3 | 0 | 2 |
| 305 | cr300 | 238 | 0 | 3 | 8 | 1 | 3 |
| 306 | cr300 | 237 | 0 | 1 | 12 | 0 | 2 |
| 307 | cr350 | 240 | 0 | 0 | 10 | 0 | 2 |
| 308 | cr350 | 239 | 0 | 2 | 9 | 0 | 4 |

No crash, hang, or implementation_exception in any run. Fix status is recorded in the sections
that follow as each family is closed.

### R9 false reject closed: `CHR()` of an out-of-byte-range constant

`cr6/false_reject/b48d6843b286` (`Const LF = CHR(-1)`): all four real compilers accept `CHR()` in a
`Const` expression for negative, `>255`, `5000000000`, `1e300` and `2147483648` arguments
(`tests/fixtures/r9_chr_arg.json`, audited 0 differing). `consteval._fn_chr` raised on a negative
or non-Unicode argument, which surfaced as `Invalid, or out of place expression`. It now takes the
low byte (the value of the resulting string is not otherwise observable). Not covered: `CHR(-1/0)`
reports `Divide by zero in expression: CHR(-1/0).` on the real compiler, which we do not emit.

### R9 family closed: `MenuPick` value type per item (CR6, CR1000X)

Reproducers `cr6/false_accept/5cbda83b79a2`, `cr6/false_accept/801129666c06`,
`cr1000x/false_accept/6fc36667c86c`. 183 probed `MenuItem`/`MenuPick` pairs per logger, all exact
now (`tests/fixtures/r9_menupick_values.json`, audited 0 differing). CR300/CR350 have no menus.

- Item kind: Long variable or Long-array element, or an integer ConstTable constant -> `int`;
  Float variable (untyped included) or non-integral Float constant -> `float`; String variable or
  String constant -> `string`; other types and integral float constants not probed, unchanged.
- Each `MenuPick` value after a `MenuItem` is checked (all values for a variable item, values after
  the leading constant for a constant item; also on repeated picks of a variable item):
  `int` -> `<text> must be declared as a integer Const.` (sic) for a float literal (`5.5`, `5.0`,
  `1e2`), string literal, a `Const` that is a string or non-integral, `PI`, `NAN`, an undefined or
  variable name, an operator expression (`1+2`, `(1+2)`, `-A`, `-&H10`) or a trailing-blank token.
  `float` -> the same with `a Const.` for string literal, undefined or variable name, `PI`-free
  operator expressions and trailing blanks (numeric literals and any declared `Const`, even a
  string, pass). `string` -> nothing is reported, including variable names (we used to reject
  `MenuPick(lv)` on a String item: a latent false reject, now gone).
- A ConstTable item whose first value is not a constant checks every value as `float`.
- Parenthesised single tokens `(5)`/`(A)` pass; echo strips one `(`..`)` pair. Odd shapes
  (`a b`, `5x`, `&HZ`, nested parens) are left silent; the real compiler reports them with
  the same message but we give a different parser message.

### R9 family closed: `Const x As <type>` with an unsupported type (all four loggers)

Reproducer `cr1000x/false_accept/03ecff3743a0` (`Const myString As String2 = "123"`);
`tests/fixtures/r9_const_type.json` (204 cases, audited 0 differing).

- Accepted `As` types (case-insensitive): Long, Float, Double, String, Boolean. Everything else,
  including `Integer`, `Short`, `Single`, `Byte`, `Text`, `ULONG`, `IEEE4`, `String2`, and keywords
  (`If`, `Const`, `Public`), gives `<type as written> unknown Const type` at the Const's line.
- Reported after the duplicate-declaration messages of the same line (`a is already declared as a
  CONST.` then `Foo unknown Const type`), and as the only message for that Const: an undefined
  name or other problem in the value is not reported. Only the first item of a `Const a As Foo = 1,
  b As Bar = 2` line is looked at. In a `ConstTable` the line is the Const's own.
- CR300/CR350: the message is a declaration trigger of the existing cascade (later `Public z` is
  not defined where used; a later `Public a` gets no duplicate message).
- Not done: a bad type together with a malformed value (`Const a As Foo = `, `= 1 +`) still gives
  the value message; `Const y As Foo = 1` for an existing variable `y` misses the later `Invalid
  assignment in expression` (same pre-existing gap as plain `Const y = 1`).

### R9 not closed (evidence in the saved reproducers)

- Excess arguments (`Too many parameters:  <arg>.`) on instructions outside `check_too_many`'s
  eligible parameter kinds: `cr1000x/false_accept/d84de83e43ed` (`FFT`), `cr300/false_accept/
  dc989f733972` (`SampleMaxMin`), `f4771af98130`. Needs per-instruction evidence.
- A String or negative `Scan` interval together with `IfTime`/`TimeIntoInterval`: the real
  compilers report `Scan interval must be integrally divisible into interval.` (e.g.
  `Scan("fuzz",Sec,3,0)` with `IfTime(0,24,Hr)`, but not with `IfTime(0,5,Min)`; `Scan(-1,Sec)`
  fails only for a sub-second `IfTime`), CR6/CR1000X replacing the interval message. The effective
  scan step is not derivable from these probes (`cr300/false_accept/1376c224f200`).
- (fixed in round 15: `Measurement Delay Exceeds 60 min.` count, `docs/instructions.md`) `Variable
  rt2 out of bounds.` / paren-dimension arrays (`cr6/false_accept/2435be9885e1`,
  `cr350/false_accept/73c3fc153711`), `FieldCal Mult` (`cr1000x/false_accept/11dd65c2d832`),
  `Parameter must be a constant integer` for `SerialOpen` (`cr300/false_accept/7be9f5d53b3a`),
  `Expression expects ,` for an `FTPClient` call (`cr350/false_accept/1b3ba52e3818`), `If without
  EndIf inside of Sub` (`cr1000x/false_accept/9ec2e021b721`).
- The 52 message_diff reproducers are mostly extra or missing follow-on lines in undefined-name
  cascades and structure messages (`No BeginProg` vs a specific first error), not triaged.


## Round 14 message-diff ranking

Baseline `tools/fuzz_replay.py` on `main` (2026-10-12): 2,251 reproducers, 1,851 exact, 4 false accepts, 368 message
diffs (311 recorded as `message_diff` plus 57 recorded as `false_accept` that now differ only in messages), 12 mode diffs,
16 oracle errors. Signature of a message diff = the first differing line of the ordered output, normalised (line number,
numbers and names removed: `X is not defined.` and `Invalid symbol in expression: ...` become `ID is not defined.`/
`Invalid symbol: ...`), as `ours-first-line | theirs-first-line`; `<END>` means that side has no line there (a missing or
extra line). The 368 reproducers fall into 173 signatures; the 20 largest are below. "Fixed" counts the cluster's members
that are exact after the round-14 commits (replay of 2026-10-12, 1,908 exact / 311 message diffs left). The table is by first
differing line, which is often a symptom: several clusters share a cause and one cause is spread over several rows.

| # | Count | Per logger | Ours (first differing line) | Theirs (first differing line) | Fixed | Smallest reproducer |
|---|---|---|---|---|---|---|
| 1 | 19 | cr300 7, cr350 12 | `ID is not defined.` | `ID is not defined.` | 9 | `PortsConfig (&B10010010X,&B11111111)` |
| 2 | 18 | cr1000x 11, cr6 7 | `ID illegally occurs before BeginProg.` | `Invalid symbol: ...` | 16 | `SortSpaIndexed(iout,6,Iarray)` |
| 3 | 14 | cr1000x 3, cr6 11 | `Invalid symbol: ...` | `<END>` | 13 | `TimerInput(TimeIn(1),C1,0,2,0,usec)` |
| 4 | 14 | cr1000x 8, cr6 6 | `Invalid symbol: ...` | `Invalid symbol: ...` | 10 | `SplitStr (ResultString(1),SearchString,",",6,1)` |
| 5 | 9 | cr300 5, cr350 4 | `ID is not defined.` | `Too many parameters: ...` | 0 | `Return (x+y+z)` |
| 6 | 9 | cr1000x 1, cr300 6, cr350 1, cr6 1 | `Expression expects , : ...` | `Warning: Parameter will truncate to the nearest integer.` | 0 | `If IFTime(0,Hr) Then` |
| 7 | 8 | cr1000x 2, cr300 3, cr350 2, cr6 1 | `ID illegally occurs before BeginProg.` | `Cannot find DataTable X` | 7 | `SendData (ComRS232,0,4094,Table1)` |
| 8 | 8 | cr1000x 2, cr6 6 | `Invalid, or out of place expression: ...` | `No BeginProg in Program.` | 0 | `Function PlusTwo("fuzz")` |
| 9 | 7 | cr300 4, cr350 3 | `Invalid, or out of place expression: ...` | `ID is not defined.` | 0 | `Scan (1,S  ec,3,0)` |
| 10 | 7 | cr300 4, cr350 3 | `<END>` | `Too many parameters: ...` | 0 | `Broadcast (C  omRS232,14)` |
| 11 | 6 | cr300 2, cr350 3, cr6 1 | `NextScan X no Scan.` | `<END>` | 0 | `CalltAble (SS1990Tab) / NextScan` |
| 12 | 6 | cr1000x 1, cr300 2, cr350 1, cr6 2 | `<END>` | `Warning: Attempt to initialize variable out of bounds.` | 0 | `Dim X(3,-1) = {1,2,3,4,5,6,7,8,9}` |
| 13 | 6 | cr1000x 1, cr300 2, cr350 3 | `<END>` | `Invalid, or out of place expression: ...` | 0 | `Public NumEx AS LONG, Shift(8) AS LONG / Shift(3,UndeclaredFuzzName123)=NumEx >> 3` |
| 14 | 6 | cr1000x 2, cr6 4 | `Sub X not defined.` | `Invalid symbol: ...` | 0 | `BeginProg / SerialIn (InString,ComRS232,100,0,20) / Call  Table (SerialT)` |
| 15 | 5 | cr300 4, cr350 1 | `<END>` | `ID is not defined.` | 0 | `Public DestArray("fuzz",MaxNeighbors) As Long` |
| 16 | 5 | cr300 4, cr350 1 | `Invalid or missing parameter.` | `Variable X in Parameter: ...` | 0 | `Public TrimRight as String * 50, TrimLeft as String * 50, TrimAll as String * 50 / Public InputString AS String * 50 / S` |
| 17 | 5 | cr1000x 4, cr6 1 | `Invalid, or out of place expression: ...` | `Invalid symbol: ...` | 0 | `DataTable(VWTable1,T  rue,-1)` |
| 18 | 4 | cr300 3, cr350 1 | `<END>` | `Warning: Invalid fnotch value, must be N,N N or N` | 0 | `Public PTemp, BattVolt / DataTable( Test,True,-1 ) / DataInterval( 0, 1,Min,10 ) / DataInterval( 0, 1,Min,10 ) / Sample ` |
| 19 | 4 | cr300 1, cr350 3 | `Could not find DataTable X` | `<END>` | 0 | `Public SSDateString As String * 25 / Public SSDateLong As Long / Public Tref / SSDateString=SecsSince1990(Public.Timesta` |
| 20 | 4 | cr300 2, cr350 2 | `Too many parameters: ...` | `Dimensions not declared: ...` | 0 | `Public ResultString(6) As String * 10 / SplitStr (ResultString(1,UndeclaredFuzzName123),SearchString,",",6,1)` |

Causes behind the rows, by what was done about them:

- **Rows 2, 3, 4, 14 (partly) and the CR6/CR1000X "missing Invalid symbol" signatures: fixed in part.** One instruction
  naming several undeclared variables reports each, in an instruction-specific order (`expressions._MULTI_REPORT_ORDER`,
  commits "several undeclared variables" and "TotalRun/StdDevRun/AvgRun", `docs/checker.md` "Round 14 multi-report order");
  `TimerInput` outside a `Scan` and `MenuItem`/`MenuRecompile` outside `DisplayMenu` give only the placement error
  ("Round 14 placement before arguments"). Not fixed: `Call run(a,b,c)` / `Call Table (SerialT)` of an undefined `Sub` still
  omit the argument names (row 14, 6 reproducers), and `FieldCal`, `FieldCalStrain`, `CovSpa`, `WindVector` in a table
  have their own orders (see the doc).
- **Row 7 (`SendData`/`SendTableDef`/`GetRecord`/`ResetTable` of a missing table): fixed** ("Round 14 table references").
  Same family, not fixed: `AcceptDataRecords`, `GetDataRecord`, `INSATData`/`EnviroSatData`/`GOESData`/`ArgosData`,
  `WorstCase`.
- **Row 1 (CR300/CR350 `... is not defined.` count), partly fixed:** the cascade lost a one-line `If` condition's names
  (`If Nope Then GetVariables(...)`), alias names of an illegally dimensioned `Alias` and constants with a bad built-in
  call stayed registered ("Round 14 one-line If condition" and "declaration tails"). Left: names inside a `Function` body
  that the cascade drops (`x is not defined.` at the `Return`/`Exit Function` lines of `functionendfunction_3`), `ThenX` after
  `If cond ThenX` (an `X is not defined.` line before `Incomplete expression`), `NetworkTimeProtocol(...,Undeclared)`,
  `TCPActiveConnections(Dest,...)`.
- **Row 5, 9, 10, 15, 16, 20 (CR300/CR350 `Too many parameters` cascade variants): open.** Examples: `Return (x+y+z)` in
  a `Function` header with an optional parameter, `Scan (1,S  ec,3,0)` as the first error, `Broadcast (C  omRS232,14)` after
  a trigger, `SplitStr (ResultString(1,Undeclared),...)` (`Dimensions not declared`), `Public DestArray("fuzz",Max) As Long`.
- **Row 6:** `If IFTime(0,Hr) Then` (one argument short) prints `Warning: Parameter will truncate to the nearest integer.`
  before `Expression expects , :` (all four loggers); opencrbasic prints `If illegally occurs before BeginProg.` because the
  program has no `BeginProg`; open.
- **Row 8:** `Function PlusTwo("fuzz")` outside any block prints `No BeginProg in Program.` then `Sub ... EndSub not matched.`
  on CR6/CR1000X (structure message ordering); open.
- **Rows 11, 12, 13, 17, 18, 19:** `NextScan with no Scan.` hidden by a typo'd `CallTable`; a missing
  `Warning: Attempt to initialize variable out of bounds.` for negative dimensions (`Dim X(3,-1) = {...}`); a table-array
  assignment `Shift(3,Undeclared)=NumEx >> 3` ; `DataTable(VWTable1,T  rue,-1)`; `Warning: Invalid fnotch value` after a
  doubled `DataInterval`; `Could not find DataTable` hidden by a CR300 cascade. All open.
- Found while probing, outside this ranking (false rejects fixed): an unmatched `)`/`}` after a `Const`/`Public`/`Dim` value,
  `SDMSIO4`/`MonitorComms`/`ModemCallBack` first argument and `NewFile` third argument (CR6/CR1000X) naming an undeclared
  variable. False accepts left: CR6/CR1000X `Const Pi = 1 / 0` (`Divide by zero in expression: 1 / 0.`), `SendData(...,T1)` etc. counted as calling
  `T1` for the "declared but never called" warning, and `PulseCount` outside a `Scan` (argument errors come before the
  placement message).
## Round 14: fresh seeds 401-404 (2026-10-12)

New seeds (previous: 0, 1, 42-54, 100, 201-208, 301-308), 600 mutants per logger and seed
(`--workers 4`; seed 402 cr350 was rerun alone after its first run was cut off by the 30 minute
background limit). Counts are the tool's classify-phase tallies; `oracle_error` mutants (real-compiler
page fault) are excluded. 353 new reproducers saved. Seeds 401-402 ran before any fix of this round,
403-404 with the first fixes already in the tree.

| Seed | Logger | exact | false_reject | false_accept | message_diff | mode_diff | other |
|---|---|---|---|---|---|---|---|
| 401 | cr6 | 582 | 1 | 7 | 7 | 1 | 4 oracle errors |
| 401 | cr1000x | 580 | 0 | 3 | 15 | 1 | 2 |
| 401 | cr300 | 570 | 0 | 4 | 26 | 0 | 3 |
| 401 | cr350 | 579 | 0 | 4 | 16 | 0 | 4 |
| 402 | cr6 | 578 | 0 | 2 | 18 | 0 | 3 |
| 402 | cr1000x | 572 | 1 | 5 | 20 | 1 | 3 |
| 402 | cr300 | 565 | 0 | 4 | 30 | 1 | 6 |
| 402 | cr350 | 576 | 0 | 3 | 21 | 0 | 2 |
| 403 | cr6 | 580 | 1 | 5 | 14 | 0 | 2 |
| 403 | cr1000x | 569 | 0 | 7 | 23 | 0 | 5 |
| 403 | cr300 | 580 | 2 | 3 | 14 | 1 | 5 |
| 403 | cr350 | 576 | 0 | 4 | 19 | 1 | 3 |
| 404 | cr6 | 582 | 0 | 3 | 13 | 0 | 4 |
| 404 | cr1000x | 576 | 1 | 7 | 14 | 2 | 4 |
| 404 | cr300 | 577 | 0 | 4 | 18 | 1 | 3 |
| 404 | cr350 | 564 | 0 | 5 | 27 | 2 | 6 (1 implementation_exception) |

No crash or hang. Replay of the 353 new reproducers after the fixes below: 11 exact, 65 false accepts,
277 message/mode diffs, 0 false rejects (the old 2251-reproducer set: 1855 exact, 4 false accepts,
no false reject). The `summary-<logger>.json` files hold the last run per logger only (this document
is the record of the others). Two cr300 false rejects of seed 403 were not saved because the real
compiler page-faulted while shrinking them; they were recovered with an unshrunk dump
(`structuretype_1.CR6` mutants) and are covered by the fixture below instead.

### Closed this round (7 families, one fixture each, `tests/fixtures/r14_*.json`)

False rejects: `Delay` option tested as an unsigned byte, with out-of-Long constants as float32
(`cr6/false_reject/77cb0ca38412`); `ModemCallBack` first argument unresolved on CR6/CR1000X
(`cr1000x/false_reject/5fe667412d48`); `AddPrecise`/`MovePrecise` second argument unresolved
(`cr6/false_reject/9124242872d5`); `NewFile` third argument unresolved on CR6/CR1000X
(`cr1000x/false_reject/802ff201c036`); CR300/CR350 `StructureType` output source (reps not
bounds-checked, one element, `Fieldnames` inert; two unsaved `structuretype_1` mutants). Crash:
String `DataInterval` argument on CR300/CR350 (`cr350/implementation_exception/25a45bf41175`).
False warning: bare `End`/`End Prog` after an endless `Scan` (8 mode_diff reproducers on all loggers).
Rules are in `docs/instructions.md`.

### Not closed (minimal reproducers saved in the corpus)

False accepts, each needing a per-instruction value model: negative or `-2147483648` constants rejected
by the real compiler for a port/channel/`SW12` argument (`cr6/false_accept/5fcfdf4bf199`,
`ff5870227e02`, `cr300/3e5633d3dbab`, `cr1000x/f543da8c46ab`); `-1 usec delay before transmit is illegal`
(`cr1000x/aa4b5b0d7146`, `cr350/7c946122d849`); `Settling time: -1, out of range` (`cr1000x/9b74d6ed1ab5`);
`Invalid transfer option -1, must be 0..4` (`cr300/ac67a412327f`); `Unrecognized port!`
(`cr6/05c461af995f`, `cr1000x/496cca6dbced`); `Variable X out of bounds` for destinations
(`cr1000x/21b098070e8c`, `e782a0af6cb3`, `caa680dc46c4`, `cr350/5a5580d7665d`); `A string variable
destination is required` (`cr6/bcdf64fca4ad`, `c36a6d1186cb`); `Parameter must be a constant integer`
(`cr300/ef8a04a7c081`, `cr350/918143423fb9`); `Destination variable cannot be indexed` (`cr350/00c15e34f28e`);
`No Matching ) in expression` for an added argument to a nested call (`cr1000x/8fec3d32a4c5`, `ee26b0ad7cda`);
storage allocation (`cr300/8888b85b87ce`, `cr350/ec31b827adb1`); `Missing NextScan` after a swapped
`NextScan` (`cr6/1c3aa2095176`, `46663179c624`). Known partial: `Delay(256,61,min)` loses the
`Scan interval too short` line (see `docs/instructions.md`); `NewFile(fa,Nope,nf)` lacks `FileName
parameter must be type String.`; `End 1` / `End Prog 1`. The 272 message/mode diffs are mostly
undefined-name cascade lines and were not triaged.

Environment note: the machine's POSIX semaphore table was exhausted during this round, so `fuzz_replay.py`
and pytest were run with `--workers 2` / `-n 8 -m "not server"` and no semaphore errors occurred.

## Round 15: fresh seeds 405-408 (2026-10-12)

New seeds (previous: 0, 1, 42-54, 100, 201-208, 301-308, 401-404), 600 mutants per logger and seed
(`--workers 3`, the four loggers of a seed in parallel, `scratch/r15/run.sh`; every run finished well inside the
30 minute limit). Counts are the tool's classify-phase tallies; `oracle_error` mutants (real-compiler page fault) are
excluded. 323 reproducers saved (319 new files). All 16 runs had the round-14 fixes in the tree; the fixes of this round
came afterwards.

| Seed | Logger | exact | false_reject | false_accept | message_diff | mode_diff | other |
|---|---|---|---|---|---|---|---|
| 405 | cr6 | 584 | 0 | 2 | 12 | 0 | 2 oracle errors |
| 405 | cr1000x | 582 | 0 | 7 | 10 | 0 | 1 oracle errors |
| 405 | cr300 | 580 | 0 | 2 | 18 | 0 | - |
| 405 | cr350 | 578 | 0 | 2 | 17 | 1 | 2 oracle errors |
| 406 | cr6 | 571 | 0 | 5 | 21 | 1 | 2 oracle errors |
| 406 | cr1000x | 581 | 0 | 5 | 13 | 1 | - |
| 406 | cr300 | 575 | 0 | 1 | 22 | 0 | 2 oracle errors |
| 406 | cr350 | 575 | 0 | 2 | 23 | 0 | - |
| 407 | cr6 | 578 | 0 | 3 | 17 | 0 | 2 oracle errors |
| 407 | cr1000x | 582 | 1 | 2 | 12 | 1 | 2 oracle errors |
| 407 | cr300 | 574 | 0 | 3 | 23 | 0 | - |
| 407 | cr350 | 580 | 0 | 2 | 17 | 0 | 1 oracle errors |
| 408 | cr6 | 579 | 0 | 4 | 17 | 0 | - |
| 408 | cr1000x | 574 | 2 | 4 | 18 | 0 | 2 oracle errors |
| 408 | cr300 | 573 | 0 | 5 | 20 | 0 | 2 oracle errors |
| 408 | cr350 | 582 | 0 | 1 | 17 | 0 | - |

No crash, hang or `implementation_exception`. Three false rejects (all CR1000X), three families, all fixed below.
Replay of the whole corpus after the fixes (`tools/fuzz_replay.py`, 2,923 reproducers): old set (2,604 reproducers, rounds
up to 14) 2,034 exact / 33 false accepts / 52 recorded false accepts that now differ only in messages / 459 message diffs /
10 mode diffs / 16 oracle errors / 0 false rejects (round-14 baseline 2,017 exact, 33 false accepts); new set (319
reproducers of this round) 16 exact / 46 false accepts / 253 message diffs / 4 mode diffs / 0 false rejects.

### Closed this round (4 families, one fixture each, `tests/fixtures/r15_*.json`)

False rejects: `MoveBytes` option tested on its low 15 bits (`MoveBytes(...,1,"fuzz")`, `NAN`, `65540`, `-2^31`;
`cr1000x/false_reject/240340975c77`; `r15_movebytes_option`); `AddPrecise`/`MovePrecise` first argument given as a
string, string `Const`, function call or `Status.X` (`cr1000x/false_reject/bea2207a74da`; `r15_addprecise_string_arg`);
`Call r  un(...)` with spaces inside the Sub name, plus `Call S(a,b,c,` with the empty slot after the last comma
(`cr1000x/false_reject/f4932fe73ffd`; `r15_call_name_spaces`). Message diffs: no `never reached at run time` warning after
a `Scan` before `BeginProg` on CR6/CR1000X, `Expr never reached` for a bare non-Sub name (13 reproducers,
`r15_unreachable_warning`). Rules are in `docs/instructions.md` and `docs/checker.md` ("Round 15").

### Not closed

False accepts (46 new, each needs a value model or a statement-level rule; examples with hashes):
CR300/CR350 storage allocation `Unable to allocate enough data table space to manage flash write cycle limits.`
(`cr300/361cb4b5d2d5`, `cr350/7d495abe4b98`, `cr350/fa0787025392`; owned by the storage-limit worker); SNMP
`Illegal OID since already declared.` (`cr300/68f6c2e84a10`, `d64756cdf94c`, `cr350/143c2e649ab2`; owned by the
DNP3/SNMP worker); `Settling time -2147483648 too short, min time is 20.` (`cr1000x/414ffa08bfec`, `642794f6e777`);
`Invalid port -2147483648, use C1..C8.` (`cr1000x/870ca9a9562e`, `e8c854e3493d`); `Unrecognized port!`
(`cr1000x/ad8bc2adcf5d`, `cr6/6ac24bd775f1`); `Invalid symbol in expression: UndeclaredFuzzName123.` for an argument that
is not name-checked (`cr1000x/b69e022a8ebb`, `cr6/943ff7306384`, `cr1000x/06044c89e4ee` `SIO4Data(Undeclared)`);
`Destination variable cannot be indexed` (`cr300/2efe1052586d`, `ef53e495b0d1`); `Quad not yet declared so cannot be
aliased.` (`cr300/92a6b05b0d8b`, `cr350/92a6b05b0d8b`); `Warning: Invalid fnotch value, must be N,N N or N`
(`cr300/b18b9f8fee66`, `cr350/e5b2c9e12e2a`); `Illegal object id.` (`cr6/05b403830c47`, `362bb82ee0d7`); singles:
`Number of bytes 100 must be less than input buffer 0` (`cr1000x/25f44329bc6e`), `Illegal spectral binning.`
(`cr1000x/51f39a5f2393`), `Scan illegally occurs after EndSequence.` (`cr1000x/9bad4ab7944b`), `Excitation magnitude:
-2147483648, out of range (20 - 5000).` (`cr1000x/a0b2b0b059b3`), `Variable Mux2 out of bounds.`
(`cr1000x/ae03090ec885`), `GoesTable not active.` (`cr1000x/bcfde245a27a`), `StartWord, NumberBytes and EndWord cannot
all be not used` (`cr1000x/e9d625f3f51f`), `Value in preceding MenuItem must be a variable or a Const` (`cr1000x/ec6b11bc3176`),
`P1 already used as PulseCount in this scan.` (`cr1000x/f741f116b199`), `FieldNames cannot be a variable.`
(`cr300/8734cd779df1`), `DataTable is declared in the middle of an executing sequence` (`cr300/ae9eb6bb2d37`), `Justify
option ... is illegal when spliting into a structure` (`cr300/fb802c166ba6`), `Option must be 0-N.` (`cr350/1d86ada2d10b`),
`Cannot autoallocate memory for DataTable AmpPhase called outside of Scan` (`cr350/c01544a79750`, storage worker),
`Flag paramter must be of type LONG` (`cr6/72f95eac103f`), `Illegal EVT, N..N are legal` (`cr6/d12785e05faf`),
`Variable Ver_Value out of bounds.` (`cr6/cbfffbba2919`), `GOESResult1 not yet declared so cannot be aliased.`
(`cr6/2b97599754d2`). Three `Warning: Table X, is declared but never called` false accepts hide a missing `No Matching ) /
Dimensions not declared / Expression expects ,` error inside a long `"got=" + FormatLong(...)` string expression
(`cr1000x/cca13e3dfce5`, `cr6/fe18dc272183`, `cr6/9d2dd45508e6`, `cr6/95e2a4d5d433`, `cr6/db82630bee84`); not analysed, probably the
unclosed-call family (see overlaps).

Message-diff families (266 new; the large ones, signatures not counted):
- `NextScan with no Scan.` printed where the real compiler prints nothing, 14 on CR300/CR350 (`cr300/005046ba1509`,
  `5229bbe0cbb4`, `6406c500041e`, `96c2c3b42ded`): round-14 row 11 (typo'd `CallTable` hides it), still open.
- `No BeginProg in Program.` where the real compiler reports a name error first (`Table name 1+2 illegal, starts with a
  number.` 5: `cr1000x/3693375da054`, `4dca2b0a45a4`, `cr350/ebcbd5751522`, `cr6/892d21ccb256`; `DataTable name
  UndeclaredFuzzName123 is 4 chars too long.` 3: `cr1000x/439122e6595b`, `cr6/1ad6364e0c01`, `fcbf187409c5`;
  `GoesTable must be used inside a table declaration.` 3: `cr300/bb04db7cf478`, `d410db849320`, `cr350/a620020511bd`;
  `Could not find DataTable CRNXTable.` 2: `cr6/08fa9332d936`, `961836f88a83`; `ApplyAndRestartSequence not declared`
  2: `cr300/0e7029d14d19`, `cr350/65a18f9b4cfc`). A structure/ordering family: which pass reports before `No BeginProg`.
- CR300/CR350 `Too many parameters` / `... is not defined.` cascade variants (`Too many parameters:  LastRx_Secs.` vs
  `Dest is not defined.` 5: `cr300/2e569d46941b`, `cr350/13987052713c`, `75ceeb9c9c39`, `a9cef0d82705`;
  `<END>` vs `Too many parameters:  N.` 4: `cr300/05be9ef4063f`, `4a8bd6af936d`, `c775ad36cd0f`, `cr350/f13ecd39e0ea`; `x is
  not defined.` missing 3: `cr300/0d350c869cac`, `30177829c087`, `c7ded82225ac`): round-14 rows 1, 5, 10, 15, 16, 20.
- `Variable illegal in Parameter: X` where we print `Invalid or missing parameter.` (CR300/CR350, 12:
  `cr300/0381a1474b84`, `4b6bdd8c4103`, `51dd5446fc0e`, `782fb1d134cc`, `e36a3f9b4e79`, `cr350/120155f840e6`, `27db3e7b50a8`,
  `533b7df22e8b`, `827b704d58e1`, `ab64efa0d671`, `dbd33291e3df`): a constant-only argument given a variable; round-14 row 16.
- `Sample must be used inside a table declaration.` printed instead of our `X is not defined.` (CR300/CR350, 12:
  `cr300/1316b1da473f`, `3fef0909986c`, `5d4d735705eb`, `88d0f1bca601`, `a0e9247aae41`, `e5c3d3be4cdd`, `cr350/032f5ef88507`,
  `15bd80dc798a`, `165d1165e8a1`, `189fa1a9ece9`, `677ae3d5d36e`, `94cab2c6f117`, `dcc5099556cc`, `f3d9ade7862e`): a `Sample`/`Min`
  outside a table (mutated `EndTable`) is reported by name before the cascade.
- `Cannot use DataTable value as a MenuItem, use DisplayValue.` missing (`cr6/793d279a9673`, `a7167e69be69`, `f84bd157c864`);
  `Dimensions not declared: array1(i) <> array1(i) .` vs `i is not defined.` (`cr300/a4c5769e043f`, `b25d267e4102`, `f34bd1b2445c`,
  `cr350/a4c5769e043f`); `EndSelect w/o Select` vs `Sub InitPowerProfile not defined.` (`cr6/5239b3e5584c`, `829ba0937859`,
  `fa45417faea4`); `Warning: Attempt to initialize variable out of bounds.` missing (`cr1000x/e9385f666883`, `cr300/52e19bffe312`, round-14 row 12);
  `Duplicate variable name` echo with bytes `l\x93"` (`cr350/7c413416d561`, `e9adffc4ffb3`: the real compiler echoes a corrupted
  name, not reproducible); a trailing space in the echoed text `Dimensions not declared: X( I ) .` (`cr350/03da58aee91f`, `402a6e663007`).
- Found while probing the `Call` fix, left open: `Call run(A,B,C,D` with an undeclared argument still lacks the leading
  `Invalid symbol in expression: D.` (CR6/CR1000X) / `D is not defined.` (CR300/CR350) line, and `Call run(A` / `Call run(` /
  `Call run` print one `Call argument required.` where the real compiler prints two or three; `Call run(A,B,C,A` etc. are exact.
  CR300/CR350 `Abs(x)` after an endless scan is `Expr never reached`, `Foo(1)` (wrong count) and `Nope.x` keep their warning there.

Overlaps with other workers (not fixed here): unclosed `Abs`-like calls / one-line `ElseIf` / inline-`If` bodies
(`cr6/95e2a4d5d433`, `cr6/db82630bee84`, `cr350/05a9c1794237`, `cr350/396192db3ee7`, `cr300/c04335b534c6`, `cr350/0f82541c62ee`
`Incomplete expression: Xabs = ABS(X,Undeclared)`, `cr300/fc8f299fb83a`, `cr300/6df6fa35c5e6`); DNP3/SNMP/CDM ports/FieldCal/Debug/
RoutersNeighbors value models (`Illegal OID`, `Illegal object id.`, `Settling time`, `Excitation magnitude`, `Unrecognized port!`,
`Invalid port`, `Option must be`, `cr300/c2df4fc25e40` and `cr300/8da22221926c` `MaxNeighbors is not defined.`);
CR300/CR350 storage limit / autoallocate / `CDM_Delay` (`cr300/361cb4b5d2d5`, `cr350/7d495abe4b98`, `cr350/fa0787025392`,
`cr350/c01544a79750`); message-diff ranking (everything in the list above).
## Round 15 message-diff ranking (2026-10-12)

`tools/fuzz_replay.py` on `main`: 2,604 reproducers, 2,017 exact, 33 false accepts, 528 message diffs, 10 mode diffs, 16 oracle
errors; after the round-15 commits 2,157 exact, 388 message diffs left (0 false rejects). Ranking method as in round 14
(first differing line, normalised). Largest clusters at the start: `ID is not defined.` count differences (26), `ID is not
defined.`/`Sample must be used inside a table declaration.` (23, fixed), `NextScan with no Scan.` hidden by an unknown
`CallTable` (18, fixed with the reachability warnings, 12), `ID is not defined.`/`Too many parameters` (13),
`<END>`/`Invalid, or out of place expression` (12, over-indexed targets, fixed), CR6 `No BeginProg in Program.` (12, an `_`
at the end of the file, fixed). Fixed families are in `docs/checker.md` "Round 15 message-diff families" and following
sections. Left open: `Return (expr)` outside a `Function` on CR300/CR350 (names must not be printed), `CallTable (T  rimTab)`
(`rimTab is not defined.` only), the rest of the `ID is not defined.` count cluster (unparsed `Const X Y=5`, `&B...X` names,
declarations after a failed declaration), multi-variable `Illegally dimension` echo, `Units a  b = c`, `Function F("x")`.

## Round 16 message-diff ranking (2026-10-13)

`tools/fuzz_replay.py` on `main` ee0e97f4: 2,923 reproducers, 2,272 exact, 52 false accepts, 569 message diffs (523 still +
46 changed from false accept), 14 mode diffs, 16 oracle errors (page faults). Ranking as in round 15 (first differing line,
digits normalised), CDM reproducers dropped: the tail is flat (largest cluster 8, then 8, 7, 7, 6 ...), so no family is
dominant. Clusters seen: CR300/CR350 `Too many parameters:  X.` vs `Dest is not defined.` cascade (8); `No BeginProg in
Program.` where the real compiler reports a table name / instruction-outside-program error first (~55 rows in all: table
names 15, `GoesTable must be used` 3, `ApplyAndRestartSequence not declared` 4, `ESSInitialize illegally occurs before
BeginProg.` 3, `Could not find DataTable X.` + `Illegal table name` for `AcceptDataRecords` 3, `Units ...` 4);
`Sample must be used inside a table declaration.` vs `X is not defined.` (CR300/CR350, 13); block-closer errors
(`EndSelect w/o Select`) hidden by an earlier `Call` of an undefined Sub in a file without `BeginProg` (11);
`Scan illegally occurs before BeginProg.` vs `Scan without NextScan.` (6); `Dimensions not declared: X( I ) .` echo
spacing (5, CR300/CR350); `#EndIf without #If` vs `Unknown instruction: #EndIf` (4, CR6).
Fixed in round 16 (`docs/checker.md` "Round 16 message-diff families"): table names over 20 characters and digit-led
operator-expression names (15), `GOESField`/`GOESTable` (3 + table-only), `ApplyAndRestartSequence` (4). Left open: the
rest above, `ESSInitialize` before `BeginProg`, `AcceptDataRecords` unknown table, `Sample`/`X is not defined.` order on
CR300/CR350, the no-`BeginProg` block-closer ordering (`Call Foo` / `x=1` / `EndSelect`: real prints only `Sub Foo not
defined.` when an undefined-name statement precedes the closer).


## Round 16: fresh seeds 409-412 (2026-10-13)

New seeds (previous: 0, 1, 42-54, 100, 201-208, 301-308, 401-408), 600 mutants per logger and seed (`--workers 3`, the four
loggers of a seed in parallel, `scratch/r16/run.sh`; seed 409 took about 12 minutes, 410-412 about 5-7 minutes thanks to the
oracle cache; 410-412 ran as one chained background command). Counts are the tool's classify-phase tallies; `oracle_error`
mutants (real-compiler page fault) are excluded. All 16 runs had the round-15 tree. 241 reproducers saved; the 6 CDM/CPI ones
(3 CR1000X false accepts, 2 CR6 and 1 CR1000X message diffs) are counted here only and were not committed, per the human
decision of 2026-10-12.

| Seed | Logger | exact | false_reject | false_accept | message_diff | mode_diff | other |
|---|---|---|---|---|---|---|---|
| 409 | cr6 | 581 | 0 | 5 | 11 | 0 | 6 oracle errors |
| 409 | cr1000x | 585 | 0 | 5 | 10 | 0 | 3 oracle errors |
| 409 | cr300 | 581 | 0 | 2 | 16 | 0 | 4 oracle errors |
| 409 | cr350 | 583 | 0 | 1 | 15 | 0 | 4 oracle errors |
| 410 | cr6 | 585 | 2 | 0 | 10 | 1 | 4 oracle errors |
| 410 | cr1000x | 584 | 0 | 3 | 12 | 1 | 3 oracle errors |
| 410 | cr300 | 581 | 0 | 1 | 17 | 1 | 3 oracle errors |
| 410 | cr350 | 587 | 0 | 1 | 9 | 0 | 6 oracle errors |
| 411 | cr6 | 581 | 0 | 4 | 14 | 0 | 2 oracle errors |
| 411 | cr1000x | 586 | 0 | 1 | 11 | 1 | 2 oracle errors |
| 411 | cr300 | 575 | 1 | 3 | 21 | 0 | 6 oracle errors |
| 411 | cr350 | 572 | 0 | 4 | 21 | 0 | 6 oracle errors |
| 412 | cr6 | 578 | 0 | 4 | 17 | 0 | 3 oracle errors |
| 412 | cr1000x | 581 | 1 | 6 | 11 | 0 | 4 oracle errors |
| 412 | cr300 | 590 | 0 | 1 | 8 | 0 | 3 oracle errors |
| 412 | cr350 | 585 | 0 | 0 | 15 | 0 | 4 oracle errors |

No crash, hang or `implementation_exception`. Four false rejects, three families fixed, one open: CR6 `Delay(0,999999999999,Sec)`
(`cr6/false_reject/7ee5e9c9ebee`) and CR6 `Public x As String * 16(5) = {...}` (not saved: the shrinker hit an oracle error;
found by re-running seed 410 with a copy of `fuzz.py` that dumps unshrunk false rejects) and CR1000X `Units PP  S = ms` (same
reason, seed 412) are fixed below; the CR300 `Unable to allocate enough data table space to manage flash write cycle limits.`
false reject (`cr300/false_reject/9354d3641c2a`, `Scan(ScanConst,...)` with a 9999-record table over a 15 s `DataInterval`) belongs
to the storage/auto-table worker and stays open. Replay of the whole corpus after the fixes (`tools/fuzz_replay.py`): old set
(2,923 reproducers, rounds up to 15) 2,272 exact / 52 false accepts / 569 message diffs / 14 mode diffs / 16 oracle errors / 0
false rejects (unchanged from the round-15 baseline); new set (235 reproducers of this round without CDM/CPI) 1 exact / 32 false
accepts / 197 message diffs / 4 mode diffs / 1 false reject (the CR300 storage one).

### Closed this round (3 families, one fixture each, `tests/fixtures/r16_*.json`)

False rejects: constant `Delay` interval of 2^31 or more on CR6/CR1000X (`r16_delay_long_interval`); dimension after a `String`
size with initializer (`r16_string_size_dims`); words after the first `Units` target and `Units A, B = ms`
(`r16_units_extra_words`). Rules are in `docs/checker.md` "Round 16".

### Not closed

False accepts (32 new, 0 CDM; each needs a value model or statement-level rule; examples with hashes): `Variable X out of
bounds.` (`cr1000x/3316999a9aae`, `cr6/eae4709fbd91`, `cr6/3e20860e8d27`, `cr6/5eaff780b01b`, `cr6/a99ccb3134e8`,
`cr1000x/d46658b76a26`); `Structure inside Structure` (`cr300/48efbd16f793`, `82edfe04af7d`); `FieldNames cannot be a variable.`
(`cr300/4f3d882614c3`, `65a6de8f06bc`); `Settling time -N too short` (`cr1000x/a1b7277c7cd5`); `Number of bytes N must be less than
input buffer N` (`cr1000x/437f0d77eed9`); `Invalid PulseCount channel.` (`cr350/d2576b1675ed`); `N is an invalid CommsMode
parameter.` (`cr350/2c6d7051e308`); `Warning: Invalid fnotch value` (`cr300/67148abb5fb4`); `Scan illegally occurs after
EndSequence.` (`cr6/9142ff727f5b`); `Delay illegally occurs before BeginProg.` (`cr1000x/807280ae0826`); `Missing DialSequence().`
(`cr1000x/876d392458b6`); `Missing instance number` (`cr1000x/b93de6a1a116`); `Variable Type LongN not supported`
(`cr1000x/f94e121fc217`); `N+N must be declared as a Const.` (`cr6/022d98dd3efc`); `Invalid output option; range N..N.`
(`cr6/4156d89a014d`); `Invalid function option in SDMSWNA.` (`cr6/77636b10567c`); `When using TimedControl reps must be N.`
(`cr6/7f9efa8af42c`); `Invalid clock set mode, must be N..N.` (`cr6/c060e9f1402f`); `Incomplete expression: &HN  NaN.`
(`cr1000x/072c74da6af5`); `Illegal parameter(s)` (`cr1000x/7290cd87d3b0`); GZip with an unclosed call
(`cr6/5688599287a4`, value-model worker). Singles on CR300/CR350: `Expression expects ,` (`cr350/ecd1a672b918`), `X is not defined.`
(`cr350/1ab8532817a1`, `882d52e3099e`, `cr300/bd1648cdb628`). Found while probing the fixes (not reproducers):
`Public x(3) As String * 16(5)` (`Incomplete expression: 16(5).`), `Public x As String * 16(5) = { }` (`Variable
initialization of x has bad syntax.`), `Units 3 Q = ms`, `Units = ms`, `Units PPS, PPS = ms`, CR300/CR350 `Delay` with a huge
`mSec`/`uSec` interval (`Max Delay is 24 Hours`), and `Delay(0,3601,Sec)` under `Scan(1,Sec)` where we print `by 3601 Sec` and
the real compiler `by 3600 Sec` (not reproduced elsewhere; CR6/CR1000X).

Message-diff families (197 new; first differing line, normalised): `No BeginProg in Program.` where the real compiler reports a name
error first (`DataTable name UndeclaredFuzzNameN is N chars too long.` 5: `cr350/6398c3662bd7`, `7fa84bc5a23d`, `cr1000x/42141e76055b`;
`Table name N+N illegal, starts with a number.` 4: `cr1000x/bfdee1e42ed1`, `cr300/2f735b71aa65`, `30787a5a5a43`;
`GoesTable must be used inside a table declaration.` 2: `cr350/4f9151e085de`, `cr300/a620020511bd`; round 15 family);
`Cannot use DataTable value as a MenuItem, use DisplayValue.` missing (5: `cr1000x/a7167e69be69`, `b07d06f6a0f6`, `fe43be59b478`);
`EndSelect w/o Select` vs `Sub InitPowerProfile not defined.` (4: `cr1000x/e406ca2fdda2`, `cr6/62e642b46f39`, `7e992c91dd9e`) and vs
nothing (4: `cr6/37e0c8f02912`, `3870a684e57e`, `5c994c3a7375`); CR300/CR350 `x is not defined.` missing (3: `cr350/cf6b7746ff60`,
`cr300/d6b595f7c2a3`, `f72e3e545f2d`) and `Sample is not defined.` vs `Sample must be used inside a table declaration.` (2:
`cr350/2e93a51dcc12`, `cr300/8b1537d7f82b`); an unresolvable symbol in `FinAppLib.crb` echoed differently (3: `cr6/1a0adf09197f`,
`e45722386a0c`, `f8a295fb01aa`); `Warning: Invalid fnotch value` before `EndTable without DataTable.` (`cr350/032625ac1c8b`,
`cr300/032625ac1c8b`); `Too many parameters:  SearchString.` vs `Dimensions not declared` (`cr350/1910a4ff9dbe`, `5d3b18cc3e0a`);
`Flag not yet declared so cannot be aliased.` where the real compiler prints nothing (`cr350/2908622773b2`, `cr300/6d94546b742e`);
`DisplayMenu without EndMenu.` missing (`cr1000x/10d9c28bfffb`, `61143927c9cc`); `Invalid symbol in expression: True.` where the
real compiler is silent (`cr1000x/90c3a37e8add`, `94dae8a8b7fb`). 159 distinct first-line signatures in all; the rest are singletons
of the CR300/CR350 cascade (`Too many parameters` / `... is not defined.`) family of rounds 14-15 (`docs/fuzz-findings.md` "Round 15").

### Overlaps with other workers

Storage/auto-table: the CR300 false reject above and the flash-write-cycle false accepts of round 15. Value-model false accepts:
the `Settling time`, `Number of bytes`, GZip and `Variable X out of bounds.` families. Message-diff ranking: the `No BeginProg` and
CR300/CR350 cascade families. Nothing here touches FieldCal, EOF continuation or LineNum closers.
