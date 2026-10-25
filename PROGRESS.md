# opencrbasic progress

Current compiler state and handoff, updated **2026-10-25 after development round 35** (M0 fuzzer
realism and fresh seeds, M1 fixes). Rounds 33 to 35 are committed.
Measurements describe the round-35 implementation unless marked otherwise. Inspect
`git status` before editing; use Git to check commit and publication state.

Goal, priorities and the v1.0 roadmap: [PLAN.md](PLAN.md#remaining-work-strategy). The human
decision of 2026-10-24 makes verdict safety, execution mode and the first error the priorities;
later-diagnostic order and cascades no longer block a release. Previous measurements, round logs
and superseded handoffs: [progress history](docs/progress-history.md). Language-server work has
its own [plan](LSP_PLAN.md) and [progress ledger](LSP_PROGRESS.md).

## Current status

The CR6, CR1000X, CR300 and CR350 profiles are implemented. Reference crashes and runner
failures are not compiler verdicts. Exact diagnostic agreement means equal exit codes and
normalized ordered stdout, not byte parity.

Latest observed validation, on macOS Apple Silicon with Wine 11.17 and Python 3.15:

| Check | Latest result |
|---|---|
| `pytest -q -n 4 -m 'not server'` | 50,239 passed, 311 skipped, 231 strict xfail (round 35) |
| `pytest -q -m server` | 163 passed |
| Round-24–31 fixture audits (15 fixtures, cached oracle) | 0 differing, 0 without verdict |
| Round-31 fixtures (four profiles): measurement spans / settling and overlap / alias cascades / alias collisions / review fixes | 1,014 / 801 / 300 / 514 / 420 reference observations; 960 / 749 / 288 / 512 / 406 match; 54 / 52 / 12 / 2 / 14 strict xfail |
| Earlier alias fixtures (r26–r30) | r26 282/282, r27 152/152, r28 368/368, r29 headers 180/180, r29 Strings 320/320, r29 review 104/104, r30 dimensioned 180/180 match; r30 review 92 match, 8 strict xfail |
| Round-24 destination/settling/FieldNames fixture | 674 observations: 672 match, 2 strict xfail |
| Saved fuzz replay, `fuzz_replay.py --recorded` (round 35) | Round 35, 3,221 inputs (63 new from seeds 421-424): 2,423 exact, 44 false accepts (CR6 18, CR1000X 9, CR300 9, CR350 8), no false reject, gate passes. Round 34, 3,158 inputs: 2,410 exact and 35 false accepts (CR6 11, CR1000X 7, CR300 9, CR350 8), no false reject; the gate against `tests/replay_baseline.json` passes. Round 33, whose output was unchanged from round 31 on every input: 2,390 exact; 48 false accepts; no false reject; one mode and 15 warning differences; 60 missing the first error, 35 earlier first location, 220 same location with a different message, 370 later-diagnostic or order differences; 19 operational (one recorded result is a Wine page fault). The two extra first-error misses against the round-31 table are the `hang/dd9cc650482d` reproducers, whose recorded reference result differs from the round-31 rerun |
| GEUS corpus, `difftest.py --group geus --logger cr1000x` (round 34) | 66/66 exact (round 32: 64 exact, two false rejects); no GEUS `.CR6` programs |
| Round-31 replay against the round-30 checker | Two outputs changed: one message difference became exact, one changed text but remains a message difference. Reference compilers were not rerun |
| Fresh fuzz, seeds 421-424 (round 35, 500 mutants each, CR6 421/422 and CR1000X 423/424 with GEUS seeds and the new operators) | 1,923 exact, 3 false rejects, 17 false accepts, 48 message differences, 18 runner failures; all three false rejects and 8 false accepts fixed in round 35 |
| `tools/timingtest.py` (round 31, by the integration worker) | repository 19/19 on all four profiles; help examples CR6 506/507, CR1000X 505 + one documented mismatch (`covspa_1`), CR300/CR350 505/507 |
| CR6 TDF and table-list corpus comparison (round 22) | Both 210/210 byte-identical; 308 reference rejections, six missing expected artifacts and one reference fault |

The impact classes are now produced by `tools/impact.py` (`fuzz_replay.py` and `difftest.py`
print the per-profile table); [PLAN.md](PLAN.md#where-v10-starts-round-30-measurement) keeps the
round-30 starting table.

The four 2026-10-24 probe false accepts outside the replay (unknown table field, unknown
`Settings.` name, assignment to an unknown `Status.` field, `Status.` initializer) are fixed on
CR6/CR1000X since round 34.

GEUS corpus: the reference CR1000X compiler accepts all 66 GEUS `.CR1X` programs; since round 33
opencrbasic matches all 66 exactly ([details](PLAN.md#where-v10-starts-round-30-measurement)).

Four-profile diagnostic comparison, rerun in round 34 and unchanged from round 28
(`tools/difftest.py --group all`):

| Profile | Repository `.CR6` programs: exact / same verdict, different text | Help examples: exact / reference failures |
|---|---|---|
| CR6 | 19 / 0 | 506 / 1 |
| CR1000X | 19 / 0 | 506 / 1 |
| CR300 | 8 / 11 | 505 / 2 |
| CR350 | 9 / 10 | 505 / 2 |

No corpus verdict mismatches, implementation exceptions or lost exact cases were observed on
usable reference results. The CR300/CR350 repository rejections reflect CR6-specific programs. The
help-corpus failures are `pointervariable_2.CR6` on all four profiles and `strcomp_3.CR6` on
CR300/CR350. The differential gates exit 1 while reference verdicts are missing; compare individual
valid results and keep failures separate.

Repository verification passed in round 34 both with the reference compiler and with
`CR6COMP=opencrbasic`:

```text
BUILD  host tests 146 pass | CRBasic 20/20 compiled, 6 warning(s) | repository pass
```

The host-test count includes uncommitted local-maintenance tests present in the checkout. Expected
warnings are owned by [repository build status](../STATUS.md#build). Compilation and host tests do
not establish logger runtime behavior. No hardware tests were run.

Rounds 23–31 did not rerun full corpus TDF/table-list comparisons. CDM binary images (`-b`) are
[explicitly excluded from parity](PLAN.md#explicit-exclusions-from-parity). Distribution policy is
in [legal and provenance notes](docs/legal.md).

## Latest changes and evidence

**Round 35** (one session, no subagents or Codex):
- **M0, fuzzer realism.** `tools/fuzz.py` gains three operators for plausible mistakes (a typo in a
  declared name, a neighbouring number, two swapped arguments; `tests/test_fuzz_operators.py`) and,
  for CR1000X, the 50 GEUS programs without `Include` as seeds. The configuration format is recorded
  in [PLAN.md](PLAN.md#three-diagnostic-channels).
- **Fresh seeds 421-424** found three false rejects. One was a round-34 regression (a second
  `AVW200`'s ComPort is not resolved by the reference); the other two were 32-bit conversions
  (CR1000X differential channel `999999999999`, `String * 999999999999`). All are fixed.
- **M1 fixes** from the same run, each probed on CR6/CR1000X: aliases at constant-only positions,
  a string Reps as INT_MIN, `Delay(2,0,...)` and a negative option-0 delay, running-statistic
  destination bounds (also scalar Sub parameters), the `SerialOpen` baud check on every port (it
  was `ComRS232` only; a mutated `Main.CR6` showed it), and CR1000X single-ended channels outside
  int32. Evidence: `tests/fixtures/r35_fresh_seeds.json` (449 observations, 402 match; the 47
  others are message-level, plus the four negative-`Delay` cascades). Rules:
  [fresh-seed findings](docs/checker.md#fresh-seed-findings-round-35).
- The 63 reproducers of seeds 421-424 are in `tests/corpus/fuzz/` and the replay baseline.

**Round 34** (M1, verdict safety; one session, no subagents or Codex). The 48 replay false accepts
were grouped by the reference's first error: 31 are on CR6/CR1000X, mostly singletons. Fixed:
- **Undeclared names outside `_MULTI_REPORT_ORDER`.** Round 14 listed only the positions reported
  when every argument was undeclared, and the checker checked only those. A help-example sweep (57
  single-position mutants, CR6/CR1000X) now backs checking the rest; it also found `StdDevRun`
  position 8 never resolved, `TimedControl` Size/Interval must be constants (a variable there was a
  false accept), and `SDMSIO4` resolving its destination unless the value count is 0, with a
  count-fits-destination bounds rule.
- **The four probe false accepts**: unknown DataTable fields (field names from the TDF builder),
  unknown `Settings.` names (all 259 `SetSetting` names read clean on CR6/CR1000X), assignments to
  unknown `Status.`/`Settings.` fields, and declaration initializers that read a variable or a
  `Status.`/`Settings.` member (`Variable illegal in Parameter`).
- Evidence: `tests/fixtures/r34_undeclared_positions.json` (76 observations, 75 match),
  `r34_multi_order_sweep.json` (112, 95 match; 17 differ only in later diagnostics) and
  `r34_table_settings.json` (606, 594 match; the 12 others are CR300/CR350, not modelled). Rules:
  [positions outside the multi-report order](docs/checker.md#positions-outside-the-multi-report-order-round-34),
  [table fields, Settings and Status](docs/checker.md#datatable-fields-settings-names-and-status-targets-round-34).
- Second batch, from the replay triage, all CR6/CR1000X with the probes in
  `tests/fixtures/r34_false_accepts.json` (688 observations, all match; 22 more were dropped after
  repeated Wine timeouts):
  - `SerialInRecord`: the input-buffer rule (0 without a `SerialOpen` of the port, else
    max(256, BufferSize), NumberBytes compared unsigned) and the StartWord/NumberBytes/EndWord rule
    (fitted, 16-bit words); `SerialOpen` CommsMode per port (errors on `ComC*`, warnings on RS232,
    ME, U and SDC ports) and a variable BufferSize;
  - code between `EndSequence` and the next `SlowSequence`; `SubMenu` is no deferred body;
  - instance slots of `MinRun`/`MaxRun`/`TotalRun`/`StdDevRun`; CR1000X `PulseCount` on `P1`/`P2`;
    `As Long2,` with a trailing comma; a blank after a last empty `()` in a declaration.
  Rules: [code after EndSequence](docs/checker.md#code-after-endsequence-submenu-bodies-round-34),
  [SerialInRecord and SerialOpen](docs/checker.md#serialinrecord-serialopen-and-running-statistic-instances-round-34).
- Replay false accepts 48 to 35, exact 2,390 to 2,410; repository, help and GEUS corpora unchanged.
- `tables.TABLE_PROPERTIES` is now shared by the checker and `bindings.py`.

**Round 33** (one session, no subagents or Codex):
- **M1, GEUS false rejects fixed.** A statement in one pair of parentheses (`(w = 0)`,
  `If c Then (w = 0)`, `(Delay(0,1,mSec))`) compiles as that statement on all four loggers; an
  alias named `Status`, `Public` or `Settings` makes the name a value. Probes also found and fixed
  a CR6/CR1000X false reject of `Double` as a declared or alias name, and of `Public.X` after
  `Alias D=Public`. Evidence: `tests/fixtures/r33_geus_false_rejects.json`, 256 reference
  observations on four profiles, 246 match, 10 strict xfail (CR6/CR1000X first-error text for an
  unclosed `(w = 0` and for `(w = 0)(w=1)`; CR300/CR350 accept both and miss
  `Dimensions not declared` for `Status(1)` on a scalar alias). Rules:
  [parenthesized statements](docs/checker.md#parenthesized-statements-round-33),
  [aliases named Status](docs/checker.md#aliases-named-status-public-or-settings-round-33),
  [alias names that are CRBasic names](docs/checker.md#alias-names-that-are-crbasic-names-round-33).
  Whether the logger executes a parenthesized assignment is not observed on hardware.
- **M0, impact measure and gates.** `tools/impact.py` classifies every row on verdict, mode, first
  error, warnings and later diagnostics, with operational failures apart. `fuzz_replay.py` gains
  `--recorded` (no Wine), `--baseline`/`--save-baseline` and a `no-regressions` gate for new
  CR6/CR1000X false accepts, false rejects, mode differences and first-error regressions;
  `tests/replay_baseline.json` is the committed baseline. A recorded reference result that is a
  runner failure now counts as operational, not as a verdict. `difftest.py` reports the impact
  table and has `--group geus`.
- **M0, CI.** `.github/workflows/opencrbasic.yml` runs the Wine-free suites and the recorded replay
  gate. It has not run on GitHub yet; the replay command passed locally.
- **M3 start, human request.** `crbc --strict` and `src/opencrbasic/checks.py` with check `OCB001`:
  an `Alias` named after an instruction, function, system table, keyword or special value, which
  the reference accepts (probed on all four profiles). Output contract: findings on stderr, stdout
  and `CR6cmple.xxx` unchanged, exit 3 for an accepted program with a strict error
  (`tests/test_checks.py`, [PLAN](PLAN.md#three-diagnostic-channels)). Controls: no FirnMelt
  program fires; one GEUS program does (`Alias D2out(3)= Status`, the motivating case).
- `tools/probe.py` now compares the result line too, so mode differences show.

**Round 31** closed the remaining strict xfails of rounds 24, 26, 27 and 29 and extended
measurement spans. Four workers ran in parallel; three had Codex (gpt-6.1-sol) research. They
were stopped at the weekly usage limit on 2026-10-23. A fifth worker integrated the four diffs on
2026-10-24. The integration's 91 failures were overlapping rules from two workers (double-reported
CR1000X PulseCount and CR6 bridge channel lines, a missing `Reps > 1` gate) and two unfinished
pieces (the CR300/CR350 `-t` section dropped after a settling error, a destination-overrun guard
that hid a duplicate bounds line). Rules and evidence are in four `docs/checker.md` sections:
- [measurement destination and channel spans](docs/checker.md#measurement-destination-and-channel-spans-round-31);
- [alias cascades](docs/checker.md#alias-cascades-non-float-alias-destinations-and-parameter-capacity-round-31);
- [alias name collisions](docs/checker.md#alias-name-collisions-const-first-assignment-and-string-lengths-round-31);
- [bridge spans and settling timing](docs/checker.md#bridge-channel-spans-and-settling-time-timing-round-31).

A Codex review of the integrated round-31 diff (`scratch/codex/review-r31.md`) predicted nine
defects:
- routine-scoped alias destination types leaking between routines on CR300/CR350;
- wrapped PulseCount port codes;
- fractional CR1000X CurrentSE channels;
- alias-named String lengths;
- an indexed Const assignment in a one-line `If`;
- an `OverflowError` for extreme bridge fN1;
- the CSAT3 OOM cutoff;
- alias-collision cutoff across includes;
- empty-array parameter capacity.

A fix worker confirmed each against the reference before changing code.
[Round-31 review fixes](#round-31-review-fixes) records the outcome.

### Round-31 review fixes

All nine findings were confirmed on the reference compilers and fixed. None was refuted. The
probes also exposed and fixed neighbouring gaps:
- CR300/CR350 PulseCount channel families and an invalid start (false accepts and false
  rejects);
- CR1000X PulseCount span families and fractional channel truncation;
- negative and float-zero fN1 (false rejects and a ZeroDivisionError);
- infinite settling times reporting the maximum.

Evidence is in `tests/fixtures/r31_review_fixes.json`: 420 observations, 406 match and 14 are
strict xfail. The remaining gaps are CR300/CR350 dimensionless-parameter indexing and the
`Q() As Long` header. Rules are in the round-31 `docs/checker.md` sections and the fN1 rule in
`docs/instructions.md`. Unverified generalizations:
- the infinite-settling rule (`inf_max`) was observed on VoltSe only;
- channel truncation was observed on CR1000X CurrentSE/PulseCount and CR300/CR350 PulseCount only.

**Round 32 (planning).** Under the 2026-10-24 decision, [PLAN.md](PLAN.md) now defines v1.0:
- three diagnostic channels: reference diagnostics, opencrbasic checks, coverage notices;
- a fix, flag or accept rule for remaining differences;
- probed check candidates;
- milestones M0–M7, about 12–22 rounds.

A Codex review of the draft plan (`scratch/codex/review-r32-plan.md`) corrected:
- the mode-difference count (one, not 16);
- the corpus provenance (2,983 of 3,158 reproducers are help-example mutants);
- the classifier's first-error ordering;
- the claim that a notice can waive verdict safety.

All four corrections are in the plan. Prior state: the
[round-30 handoff snapshot](docs/progress-history.md#round-30-handoff-snapshot-2026-10-23).
Production CRBasic was not changed. No hardware was flashed or tested.

### Local maintenance queue (round 32)

A local review queue was prepared on 2026-10-24 at HEAD `356c2258`, following
[LocalMaintenance.md](../verification/LocalMaintenance.md#quality-first-kickoff). It is in
`scratch/local-maintenance/r32-2026-10-24/`, with nine packets in `packets.json`:
- plan versus handoff counts;
- the README purpose versus the 2026-10-24 decision;
- the `Status` lesson conflict (a positive control);
- two scope controls;
- round-31 handoff versus `docs/checker.md`;
- the LSP plan scope;
- two test-overlap candidates.

**Not completed.** The quality-first worker started (supervisor PID 8852, Qwen3.8-27B
UD-Q4_K_M, `xhigh`, 2,048-token reasoning budget, 4,096 output, 8K context, 1,200 s timeout). The
runner's memory guard stopped the model server during loading: swap grew from 951 to 5,937 MiB
(`memory-*.json`, `stopped_for_swap_growth: true`; `kickoff.log`, `server.log`). No packet was
attempted, and the guard and other applications were left intact.

To resume with more free memory, check that no worker is live, then rerun the launcher from
`verification/LocalMaintenance.md` with `queue` set to that directory. It processes only
unattempted packets. Local output is a proposal; check it against the cited sources at the same
revision before acting on it.

## Next steps

Follow the [v1.0 milestones](PLAN.md#v10-milestones); M0 comes first.

1. **M0, remaining:** confirm the CI workflow on GitHub after the next push; optionally record
   the parenthesized-assignment runtime on the bench CR6 (unknown so far).
2. Human decisions of 2026-10-24, recorded in [PLAN.md](PLAN.md#v10-milestones):
   - CR6 and CR1000X gate v1.0; CR300/CR350 are best effort;
   - the GEUS programs may be compiled as a corpus (`AGENTS.md` updated);
   - the `Status` lesson was corrected after the old observation could not be reproduced
     ([lessons](../knowledge/crbasic/lessons.md#status-names-are-compile-checked));
   - an `--strict` error for aliases named after CRBasic names (`OCB001`, round 33).
3. **M3, remaining framework:** `--report FILE`, configuration and suppressions, the separate
   result field for the language server, then the next checks from the PLAN table.
4. **M1, remaining false accepts.** The 27 left on CR6/CR1000X fall under the PLAN's flag rule:
   instructions neither FirnMelt nor GEUS uses, or allocation and 32-bit clamping models. Round 34's
   18: `072c74da6af5` (`&H0  10a0101`), `51f39a5f2393` and `7b4b2177d56b` (`CDM_FFTFilt`),
   `55821bbfb248` (`Program too big for task memory` cascade), `870ca9a9562e`/`e8c854e3493d` (I2C/SPI
   port clamp), `a0b2b0b059b3` (CDM excitation clamp), `ec6b11bc3176`/`022d98dd3efc` (`MenuPick`),
   `4156d89a014d` (`EC100`), `6ac24bd775f1` (`CDM_MuxSelect`), `72f95eac103f` (`DNPVariable`),
   `77636b10567c` (`SDMSW8A`), `7f9efa8af42c` (`SDMCD16AC`), `c060e9f1402f` (`TimedControl`),
   `c3d3adda9412` (`SerialOpen` string format), `d12785e05faf` (`TriggerSequence`), `f2f6e5d8b8e6`
   (`SNMPVariable`). Round 35's 9: `0e23c61229f4` (`CS7500`), `39a03364be83` (`FieldCalStrain`),
   `52138d7bd179` (`CDM_PulsePort`), `577a59ecb08c` (`AVW200` destination), `778ea45e64b7`
   (`CDM_BrFull6W` settling clamp), `9c6367a2c598` (`MenuRecompile`), `ecc509dd06de`/`f61f825392f2`
   (`TableFile`), `fa923ecef6f4` (`MenuPick`). Coverage notices for them wait for M4. The 17
   CR300/CR350 false accepts are best effort. Next: more fresh seeds, weighted to the relevant
   instruction set, and the message-level first-error differences (M2).
5. **M3/M4** can start after M0, in parallel with M1/M2: the check framework, and the CR6 evidence
   inventory from `tools/builder_map.py` and `tools/message_catalog.py`.
6. The old next-step items (Const diagnostics, interval gaps, table metadata, artifact refresh)
   are kept in the [round-30 snapshot](docs/progress-history.md#round-30-handoff-snapshot-2026-10-23).
   Re-rank each under the fix, flag or accept rule before working on it.

## Environment setup

Run package tools from `opencrbasic/`, using its external uv environment:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.uv-envs/opencrbasic"
export PYTHONPATH="$PWD/src"
uv sync
```

Python 3.15+ is required. Never create a repository `.venv`. The explicit `PYTHONPATH` also keeps
worktree runs from importing the main checkout through the shared editable installation.

The current macOS Wine setup is documented in [scripts/README.md](../scripts/README.md#compiling-on-macos).
The oracle uses `~/.wine-cr6`, `WINEDEBUG=-all`, and defaults to disabling GUI drivers through
`WINEDLLOVERRIDES=winemac.drv,winex11.drv,winewayland.drv=d`; explicit overrides are preserved.
For a new prefix, disable crash dialogs as recorded in the
[archived environment setup](docs/progress-history.md#environment-setup-needed-to-resume). Runner
failures and timeouts must never be cached or counted as accepted programs.

Current reverse-engineering artifacts: `scratch/re/CR6Comp.decomp.c` and
`scratch/re/fields.decomp.c`, regenerated with Ghidra 12.1.4. The installed headless launcher is
`/opt/homebrew/Cellar/ghidra/12.1.4/libexec/support/analyzeHeadless`; Java is
`/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home`. Use a project directory under
`/private/tmp/`: Ghidra rejects the comma in this checkout's path. Restricted runs used
`JAVA_TOOL_OPTIONS=-Duser.home=/private/tmp/opencrbasic-ghidra-home` for writable settings.
Regeneration script: `tools/ghidra/DumpAll.java`; tracing method: [binary notes](docs/binary.md).
Old scratch briefs, research notes and timing helpers mentioned in history may be absent.

## Work method

Follow [repository instructions](../AGENTS.md) and the current human request. Rounds 29–31 (human
instruction 2026-10-22) used Claude subagents for implementation and Codex `gpt-6.1-sol` for
read-only decompilation research and review, run with the ChatGPT app's bundled CLI
(`/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex exec
-m gpt-6.1-sol -s read-only`; `~/.local/bin/codex` 0.158 rejects the model). Workers follow
`scratch/r31/brief-worker.md`; treat Codex output as hypotheses to confirm on the reference. Commit or push only when
authorized under the repository instructions.

Work on one observed rule family at a time: minimize a reproducer, inspect decompiled behavior,
probe accepted boundaries and failures on all four profiles, implement the smallest change,
and preserve reference observations in a fixture and the owning behavior document. Decompiled
code informs the rules and is never pasted into `src/`.

Run focused checks immediately, then the full pytest and server suites, four-profile differential
comparison, saved replay and relevant artifact/fixture audits. Compare against a baseline;
never prune reproducers just to improve agreement. Retry transient runner failures separately.
Finish with `cd ../scripts && uv run verify.py` using the scripts environment
`UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/firnmelt-scripts`.

Keep heavy validation runs bounded and avoid overlapping them. Do not kill validation process
trees: prior forced termination exhausted macOS multiprocessing semaphores. Before another
long round, `python -c "import multiprocessing as m; m.Lock()"` checks that pools can start.
Update this file after each milestone; move superseded snapshots and run history to the archive.
