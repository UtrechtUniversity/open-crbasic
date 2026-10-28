# opencrbasic progress

Current compiler state and handoff, updated **2026-10-28 after development round 57** (round-51
fuzz triage and M3 report/configuration, round 52; include names, round 53; two false rejects, two false
accepts and three first-error families, round 54; PipeLine placement data and four false accepts/rejects,
round 55; four fuzz false accepts and a UINT1 warning, round 56; the M1 gate scope and its last five
relevant false accepts, round 57). Rounds 49-56 are on `main`; use Git to check publication
state and inspect `git status` before editing.

Goal, priorities and the v1.0 roadmap: [PLAN.md](PLAN.md#remaining-work-strategy). The human
decision of 2026-10-24 makes verdict safety, execution mode and the first error the priorities;
later-diagnostic order and cascades no longer block a release. Previous measurements, round logs
and superseded handoffs are in git history: `git show 2612abad:opencrbasic/docs/progress-history.md`. Language-server work has
its own [plan](LSP_PLAN.md) and [progress ledger](LSP_PROGRESS.md).

## Current status

The CR6, CR1000X, CR300 and CR350 profiles are implemented. Reference crashes and runner
failures are not compiler verdicts. Exact diagnostic agreement means equal exit codes and
normalized ordered stdout, not byte parity.

Latest observed validation, on macOS Apple Silicon with Wine 11.17 and Python 3.15:

| Check | Latest result |
|---|---|
| `pytest -q -n 4 -m 'not server'` | 59,451 passed, 311 skipped, 564 strict xfail (round 57, `-n 6`) |
| `pytest -q -m server` | 163 passed (round 57) |
| Round-24–31 fixture audits (15 fixtures, cached oracle) | 0 differing, 0 without verdict |
| Saved fuzz replay, `fuzz_replay.py --recorded --baseline tests/replay_baseline.json --gate no-regressions` (the CI command; round 57) | 3,338 inputs: 2,591 exact (round 56: 2,585); CR6/CR1000X: 21 first-error misses, 25 false accepts, none in the relevant area; regression gate passes; `tests/replay_baseline.json` not refreshed. Earlier rounds: see git history (`git show 2612abad:opencrbasic/PROGRESS.md`) |
| GEUS corpus, `difftest.py --group geus --logger cr1000x` (round 57) | 66/66 exact (round 32: 64 exact, two false rejects); no GEUS `.CR6` programs |
| Fresh fuzz, seeds 449 (CR6) / 450 (CR1000X) (round 56, `--relevant`, 500 mutants each) | CR6 487 exact, 10 message differences, 1 warning-only difference; CR1000X 482 exact, 8 message differences, 5 false accepts (all fixed), 1 warning-only difference. Seeds 447/448 (round 55): 2 false accepts, fixed. Raw output in `scratch/r55/`, `scratch/r56/` (git-ignored) |
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

Four-profile diagnostic comparison, rerun in round 57 and unchanged from round 28
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

Repository verification passed in round 57 both with the reference compiler and with
`CR6COMP=opencrbasic`:

```text
BUILD  Pyrefly 491 files pass | host tests 148 pass | CRBasic 20/20 compiled, 6 warning(s) | repository pass
```

Expected warnings are owned by [repository build status](../STATUS.md#build). Compilation and host tests do
not establish logger runtime behavior. No hardware tests were run.

Rounds 23–31 did not rerun full corpus TDF/table-list comparisons. CDM binary images (`-b`) are
[explicitly excluded from parity](PLAN.md#explicit-exclusions-from-parity). Distribution policy is
in [legal and provenance notes](docs/legal.md).

## Latest changes and evidence

**Round 57** (M1 under the new gate scope; coordinator, Codex `gpt-6.1-sol` decompilation research in
`scratch/r57/codex-alloc.log`). All five relevant false accepts fixed, plus the families they belong to:
- **Task-memory overflow of long delays** (`55821bbfb248`, `ExciteV(Vx1,0,-1)`): the delay generator's
  signed 16-bit loop count; `ExciteV` delays from `1409024010` us and `Delay(0,...)` from `1409024000` us
  (and their 32-bit wraps) repeat `Program too big for task memory` until the message buffer is full.
  The `Delay` interval is unsigned 32-bit (also for the 60-minute limit and scan time; the Double low
  word outside the Long range), `ExciteV`'s delay now costs scan time, and SequentialMode skips the
  option-0 checks (a false reject: `Delay(0,-1,uSec)` there). 14 round-35 `not_modelled` observations
  now match.
- **`SerialOpen`** (`c3d3adda9412`, `9be1e8aba3d8`): a non-numeric format makes the fifth argument the
  CommsMode; a sixth argument must be a constant integer.
- **Bounds** (`e6b6604b4da3`, `f76ae7c58e75`): `PRTCalc` destinations; alias sources of table output
  (`Sample`, `Average`, ...) and alias destinations of `PRTCalc`, `CalFile`, `Move` and the running
  statistics.
- Fixtures `r57_task_memory_delays.json`, `r57_serialopen_format_commsmode.json`,
  `r57_alias_bounds.json` (592 observations, all four profiles; CR6/CR1000X all match except four
  same-verdict `SerialOpen` messages). Details: [docs/checker.md](docs/checker.md#task-memory-overflow-of-long-delays-round-57)
  and the two sections after it. Human proposal recorded as a PLAN check candidate: negative or
  wrapped delays.
- Gates: fixture audit 0 differing; four-profile difftest and GEUS unchanged; CI replay gate passes;
  `verify.py` passes with both compilers. No hardware tests.

**Round 56** (M1, fresh fuzz seeds 449/450; coordinator only). Five CR1000X false accepts fixed:
bridge excitation below 20 mV, `Minimum` data type 0, spatial destination types and Long bounds, and
`Sample(...,UINT1)` of a non-UINT1 variable, `WindVector` reps bounds; plus the CR6/CR1000X UINT1 padding warning. Ten round-36/38
`not_modelled` observations of the UINT1 rule now match. Details: [docs/checker.md](docs/checker.md#round-56-fuzz-rules). Gates as in round 55.
Fresh fuzz seeds 451/452 (end of round 56): CR6 490 exact, 9 message differences; CR1000X 488 exact,
7 message differences, 4 false accepts, 1 warning-only difference. `70e8fae3a5b3` (excitation -1 mV) is
fixed; **open:** `9be1e8aba3d8` (`SerialOpen(ComC7,19200,0,0,150,"fuzz")`, an extra argument: `Parameter
must be a constant integer:` with the whole statement), `e6b6604b4da3` (`PRTCalc(RTD_C,2,...)` destination
bounds), `f76ae7c58e75` (`Sample(&HFF,GeoUnit,String)` String source bounds). Raw output in `scratch/r57/`.

**Round 55** (M1/M2; worker B's round-54 PipeLine work integrated, Codex review, coordinator fixes).
- **CI:** round 54 broke the CI replay gate (`921b96a020e3`: a `Sample` outside a table owned a
  `FieldNames`); fixed in `ce35fbd3`. Local rounds now run the replay exactly as CI does, with
  `--baseline tests/replay_baseline.json` (the gate without it missed this).
- **PipeLine placement data** (worker B, fixture `r54_pipeline_task_instructions.json`, 1,274
  observations, 1,242 matched): `SetDAC`, `ExciteI`, `Resistance`, `VibratingWire`, `EC100`, 24 `CDM_*`
  instructions, a setup group (`CDM_ExciteV`, `CDM_ExciteI`, `CDM_MuxSelect`) and the slow-scan
  `may skip scans` warning ([checker notes](docs/checker.md#task-instructions-in-pipeline-mode)).
- **False reject fixed:** a `Scan` inside `For`/`If`/`Do` in PipeLine Mode no longer makes its task
  instructions conditional (pre-existing since round 49, found by the Codex review).
- **False accepts fixed:** an unknown time unit in `TimeIntoInterval`/`IfTime` (GEUS `64dd660d49e3`);
  `SetSetting("Baudrate(C  OMC1)")` (only the first word of the index counts, `b74fe6cc73d0`); CR6
  `VibratingWire` with a numeric diagnostics argument.
- **Warnings:** no `may skip scans` for `Delay(0,0,...)` or `PortSet(...,1)`.
- Details: [docs/checker.md](docs/checker.md#unknown-time-units-and-settings-port-words-round-55) and the
  following section. Gates: fixture audits 0 differing, difftest and GEUS unchanged, CI replay gate passes,
  `verify.py` passes with both compilers.

**Round 54** (M1/M2; Sonnet workers A and C, Codex research and review, coordinator fixes).
- **False rejects fixed:** a `DataTable` named after a predefined constant (`DataTable(True,...)`)
  made that constant undefined everywhere (found by Codex research on `7d9dbda67c22`); a `FieldNames`
  with no owning output instruction was resolved against any preceding instruction.
- **False accepts fixed:** bridge MeasPEx below 1 (`BrHalf`/`BrHalf3W`/`BrFull`/`BrHalf4W`/`BrFull6W`,
  `Invalid measurements per excitation N`, GEUS fuzz `953f1271f9a9`); an initializer after empty type
  dimensions (`Public A As Float() = {...}`, `628c8b23311c`).
- **First error:** juxtaposed dimension operands (`Dim G(1  8)` -> `Incomplete expression: 1  8.`, also
  in Sub parameters and pointer types, `5d03371b1388`); `FieldNames` errors on the consuming output
  instruction's line with the two follow-up messages (`83c0a809c514`).
- Codex review of the staged diff found two new false accepts (`As Float!(1 8)`, an unclosed
  `Sub S(a(1 8)`), an `OverflowError` on a non-finite dimension and a same-line order flip; all four fixed
  and recorded (`r54_dim_review_controls.json`).
- Fixtures `r54_*.json`: 634 observations, 584 matched; details and gaps in
  [docs/checker.md](docs/checker.md#juxtaposed-dimension-operands-round-54) and the following round-54
  sections. Gates: fixture audits 0 differing, four-profile difftest and GEUS unchanged, replay gate
  passes, `verify.py` passes with both compilers.
- Worker B's PipeLine Mode work was still running at this commit (integrated in round 55).

**Round 53** (M1/M2, fresh relevant fuzz seeds 443/444; one session, coordinator only, Codex
research). The fuzz found no verdict difference by its classifier, but two of its six first-error
misses were a false accept on our side: a non-constant `Include`/`IncludeSection` file name
(`IncludeSection("Functions",Undeclared)`) was accepted; the reference rejects undeclared names and
variables, then reports a NULL file name
([checker notes](docs/checker.md#include-names-that-are-not-constants-round-53)). Fixture
`r53_include_runtime_names.json` (60 observations, 34 matched; CR300/CR350 and string-`Const` names
`not_modelled`); four round-43 `not_modelled` cases of the same rule now match. The other four
misses were open at the end of round 53 (all four fixed in round 54): `7d9dbda67c22` (CR6, `DataTable(True,FinApp_Starts,-1)`: the reference's main-file
error comes before our include-file error), `5d03371b1388` (CR1000X, `Dim GPSstring(1  8)` gives
`Incomplete expression: 1  8.`; probed on CR6/CR1000X: `Public G(8 1)` and `Dim G(2,1  8)` echo only
the bad dimension, `Dim G(x  8)` adds `Variable illegal in Parameter: x  8.`, and nothing else is
reported, where we print `Invalid symbol in expression: G(...)`; the fix belongs in
`parser._parse_vardecl`), `83c0a809c514` (CR1000X, a malformed `FieldNames` is reported on
the line of the instruction it names), `ba8d6f459994` (CR6, a continuation line).

**Round 52** (M2/M3, triage of the round-51 fuzz differences; one session, Sonnet workers A-C,
Codex `gpt-6.1-sol` research and review). Four of the 14 round-51 message differences are now exact.
- **False reject fixed:** CR1000X accepts `SW12(SW12_CSIO,...)` (port 503); its invalid-port message
  is a reference formatting bug (`Invalid port 6642484, must be NULL.` for 0), modelled as profile
  data ([instructions](docs/instructions.md#sw12-invalid-sw12-port-d)). Fixture `r52_sw12_ports.json`.
- **False accept fixed:** constant destinations (`PanelTemp(3,...)`, `Erase(5)`, `AddPrecise(3,1)`)
  inside a one-line `If` were unchecked (`e261e8e17146`); fixture `r52_inline_constant_destination.json`.
- **First error:** a negative `DataInterval` is reported before a later table `Reps`/bounds error, and
  a constant destination anywhere hides every `Reps`/bounds check (`3a4aa2c0910b`); fixture
  `r52_datainterval_first_error.json`.
- **Implementation exception hidden as `Unparsed`:** `Const K=CHR()` crashed the constant folder
  (`cfb47653ff50`); fixture `r52_const_builtin_arity.json`. `crbc` stdout now carries source bytes
  like the message file (a UTF-8 `°` was double-encoded); the in-process replay still counts
  `5a642d9a0c61` because it decodes the two sides differently.
- **M3:** `--report FILE`, `opencrbasic.toml` (`--config`, `--check CODE=SEVERITY`, suppressions with
  reasons), `CompileResult.findings`/`suppressed`; exit 4 for an invalid configuration under
  `--strict` (a round-52 decision) ([checker notes](docs/checker.md#check-configuration-and-report-file-round-52)).
  Codex review findings fixed: duplicate inline `AddPrecise(3,)` diagnostic, cross-drive `relpath`,
  non-string suppression codes, backslash include paths.
- Gates: fixture audit of the r51/r52 fixtures 0 differing; four-profile difftest and GEUS unchanged;
  replay gate passes; `verify.py` passes with both compilers.

**Round 51** (M1, fresh relevant fuzz, seeds 441 CR6 / 442 CR1000X, 500 mutants each, no
shrinking; one session).
- CR6: 492 exact, 6 message differences. CR1000X: 490 exact, 8 message differences, 1 false accept
  (`a66ad5f9beb3`), no false reject, crash, hang, implementation exception or mode difference.
  The false accept minimizes to an undeclared name as 7th `SDI12Recorder` argument inside a
  one-line `If`; `check_const_family_args` ran on top-level statements only. Fixed
  (`_with_inline`); rule in [docs/checker.md](docs/checker.md#pass-1-instruction-arguments-2026-09-27).
  Fixture `tests/fixtures/r51_inline_const_family_args.json` (14 observations). The 14 message
  differences are not triaged. Raw output: `scratch/r51/`.

Rounds 49 and 50 (PipeLine Mode placement of further task instructions, fixture
`tests/fixtures/r49_pipeline_task_instructions.json`, 868 observations) and all earlier round
logs, review fixes and local maintenance queues are in git history
(`git show 2612abad:opencrbasic/PROGRESS.md`). Rules are in
[task instructions in PipeLine Mode](docs/checker.md#task-instructions-in-pipeline-mode).

## Next steps

Follow the [v1.0 milestones](PLAN.md#v10-milestones); M0 comes first.

1. **M0, remaining:** the CI workflow passed on GitHub on 2026-10-25 (run 40184946232, after the
   round-35 TDF fixture refresh); optionally record the parenthesized-assignment runtime on the
   bench CR6 (unknown so far).
2. Human decisions of 2026-10-24, recorded in [PLAN.md](PLAN.md#v10-milestones):
   - CR6 and CR1000X gate v1.0; CR300/CR350 are best effort;
   - the GEUS programs may be compiled as a corpus (`AGENTS.md` updated);
   - the `Status` lesson was corrected after the old observation could not be reproduced
     ([lessons](../knowledge/crbasic/lessons.md#status-names-are-compile-checked));
   - an `--strict` error for aliases named after CRBasic names (`OCB001`, round 33).
3. **M3, remaining:** the language server does not run checks yet (M6: call `checks.analyze` with
   its settings, put the configuration in the cache key); a missing entry file writes no report;
   then the next checks from the PLAN table.
4. **M1, remaining false accepts.** Under the [M1 gate scope](PLAN.md#v10-milestones) (human
   decision 2026-10-28) only relevant-set cases block M1, and none is open after round 57. The 25 left
   on CR6/CR1000X in the replay are not gated (instructions neither FirnMelt nor GEUS uses). Round
   34's: `072c74da6af5` (`FieldClassify("&H0  10a0101")`), `51f39a5f2393` and `7b4b2177d56b`
   (`CDM_FFTFilt`), `870ca9a9562e`/`e8c854e3493d` (I2C/SPI port clamp), `a0b2b0b059b3` (CDM excitation
   clamp), `ec6b11bc3176`/`022d98dd3efc` (`MenuPick`), `4156d89a014d` (`EC100`), `6ac24bd775f1`
   (`CDM_MuxSelect`), `72f95eac103f` (`DNPVariable`), `77636b10567c` (`SDMSW8A`), `7f9efa8af42c`
   (`SDMCD16AC`), `c060e9f1402f` (`TimedControl`), `d12785e05faf` (`TriggerSequence`), `f2f6e5d8b8e6`
   (`SNMPVariable`). Round 35's: `0e23c61229f4` (`CS7500`), `39a03364be83` (`FieldCalStrain`),
   `52138d7bd179` (`CDM_PulsePort`), `577a59ecb08c` (`AVW200` destination), `778ea45e64b7`
   (`CDM_BrFull6W` settling clamp), `9c6367a2c598` (`MenuRecompile`), `ecc509dd06de`/`f61f825392f2`
   (`TableFile`), `fa923ecef6f4` (`MenuPick`). Coverage notices for them wait for M4. The CR300/CR350
   false accepts are best effort. **Next:** fresh `--relevant` seeds until they stop finding relevant
   false accepts (seeds 449-452 found 9, all fixed), then the message-level first-error differences
   (M2). After any `crbasic/` change run `fuzz_replay.py --rerecord-stale` first (`docs/checker.md`
   "First-error families (round 39)" lists the modelled ones).
5. **Open candidates from rounds 44-51:**
   - PipeLine Mode placement is still unprobed for `CDM_BridgeFilt`, `CDM_CalRead`, `CDM_CANOutCfg`,
     `CDM_FactCal`, `CDM_FFTFilt`, `CDM_OffsetRd`, `CDM_VoltFilt`, `CDM_VW300*`, `SDMTrigger`, `TGA`;
     same-line order with the before-`BeginProg` message is not modelled (round 55);
   - round-54 fuzz: 3 first-error misses and 1 earlier first location remain (`scratch/r54/triage.json`; `SetSetting("",...)` and an overflowing `Scan` count match in isolation, so they are ordering effects);
   - 10 round-51 message differences remain (`scratch/r51/`; all later-diagnostic or same-location text except `8f0c2a005513`:
     the reference prints 9 `Parameter will truncate` warnings before the error for an undeclared first
     `TimeIntoInterval` argument in an `If`, the round-38 cascade in `scratch/r38/tii.json`, 9/6/3 by argument;
     Codex research in `scratch/r53/codex-8f0c.log`); PipeLine probing of the instructions above was not started in round 52 (Codex cannot reach the Wine server from its sandbox, so it does research and review only); `VoltSE(...,Undecl)` in a
     one-line `If` prints the `Invalid symbol` line without the earlier `Invalid channel` line;
   - older fuzz findings: `0a9af24e1266` (malformed `Sub` parameter first error), `71107f867ad9`,
`f67f1e2a1258` (unterminated string in a call), `522ddaa3d29c`
     (warning only); numeric routine-parameter declaration diagnostics, quoted-Then condition
     boundary and malformed one-line ElseIf (strict fixture gaps).
6. **M3/M4** can start after M0, in parallel with M1/M2: the check framework, and the CR6 evidence
   inventory from `tools/builder_map.py` and `tools/message_catalog.py`.
7. The old next-step items (Const diagnostics, interval gaps, table metadata, artifact refresh)
   are kept in the round-30 snapshot in `git show 2612abad:opencrbasic/docs/progress-history.md`.
   Re-rank each under the fix, flag or accept rule before working on it.

## Environment setup

Run package tools from `opencrbasic/`, using its external uv environment:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.uv-envs/opencrbasic"
export PYTHONPATH="$PWD/src"
uv sync
```

Python 3.15+ is required. The explicit `PYTHONPATH` also keeps
worktree runs from importing the main checkout through the shared editable installation.

The current macOS Wine setup is documented in [openloggernet/README.md](../openloggernet/README.md#compiling-on-macos).
The oracle uses `~/.wine-cr6`, `WINEDEBUG=-all`, and defaults to disabling GUI drivers through
`WINEDLLOVERRIDES=winemac.drv,winex11.drv,winewayland.drv=d`; explicit overrides are preserved.
For a new prefix, disable crash dialogs once:

```bash
WINEPREFIX="$HOME/.wine-cr6" WINEDEBUG=-all \
  "$HOME/.local/wine/Wine Devel.app/Contents/Resources/wine/bin/wine" \
  reg add 'HKCU\Software\Wine\WineDbg' /v ShowCrashDialog /t REG_DWORD /d 0 /f
```

Runner
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
