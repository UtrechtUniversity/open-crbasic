# opencrbasic progress

Current compiler state and handoff, updated **2026-10-24 after development round 31 and the
round-32 v1.0 planning round**. Measurements describe the round-31 implementation. Inspect
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
| `pytest -q -n 4 -m 'not server'` | 48,116 passed, 311 skipped, 144 strict xfail |
| `pytest -q -m server` | 163 passed |
| Round-24–31 fixture audits (15 fixtures, cached oracle) | 0 differing, 0 without verdict |
| Round-31 fixtures (four profiles): measurement spans / settling and overlap / alias cascades / alias collisions / review fixes | 1,014 / 801 / 300 / 514 / 420 reference observations; 960 / 749 / 288 / 512 / 406 match; 54 / 52 / 12 / 2 / 14 strict xfail |
| Earlier alias fixtures (r26–r30) | r26 282/282, r27 152/152, r28 368/368, r29 headers 180/180, r29 Strings 320/320, r29 review 104/104, r30 dimensioned 180/180 match; r30 review 92 match, 8 strict xfail |
| Round-24 destination/settling/FieldNames fixture | 674 observations: 672 match, 2 strict xfail |
| Saved fuzz replay, 3,158 inputs, against round-22 reference results | 2,392 exact; 48 false accepts; 683 message differences; 16 accepted-with-different-output (one execution-mode, 15 warning differences); 19 reference/runner failures |
| Round-31 replay against the round-30 checker | Two outputs changed: one message difference became exact, one changed text but remains a message difference. Reference compilers were not rerun |
| Fresh bounded fuzz, seeds 417–420 (round 28; not rerun since) | 157 exact; one false accept and one diagnostic difference ([details](docs/fuzz-findings.md#round-28-bounded-refresh-2026-10-22)) |
| `tools/timingtest.py` (round 31, by the integration worker) | repository 19/19 on all four profiles; help examples CR6 506/507, CR1000X 505 + one documented mismatch (`covspa_1`), CR300/CR350 505/507 |
| CR6 TDF and table-list corpus comparison (round 22) | Both 210/210 byte-identical; 308 reference rejections, six missing expected artifacts and one reference fault |

Impact classes of the replay differences, the v1.0 measure (`scratch/r32/classify.py`, round-31
replay): 333 same first error with later diagnostics different, 37 same diagnostics in another
order, 220 same first location with a different message, 58 missing the reference's first error,
35 reporting an earlier first location, 15 warning-only and one execution-mode difference.
[PLAN.md](PLAN.md#where-v10-starts-round-30-measurement) has the per-profile table.

Three CR6 probes from 2026-10-24 are additional false accepts outside the replay:
- an unknown table field;
- an unknown `Settings.` name;
- an assignment to an unknown `Status.` field.

They are in `scratch/r32/lint/`.

Four-profile diagnostic comparison, rerun in round 31 and unchanged from round 28
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

Repository verification passed in round 31 both with the reference compiler and with
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

## Next steps

Follow the [v1.0 milestones](PLAN.md#v10-milestones); M0 comes first.

1. **Human decisions needed:**
   - the v1.0 profile scope (all four profiles, or CR6/CR1000X gated with CR300/CR350 best
     effort);
   - confirmation of the `Status` lesson conflict
     ([lessons](../knowledge/crbasic/lessons.md#status-names-are-not-compile-checked) versus the
     2026-10-24 rejections in `scratch/r32/lint/`).
2. Human decision: may the GEUS programs in `examples/Logger Programs/` be compiled as a
   CR1000X differential corpus and as false-positive controls for opencrbasic checks?
   ([PLAN.md](PLAN.md#where-v10-starts-round-30-measurement); `AGENTS.md` currently says not to
   compile `examples/`.)
3. **M0:**
   - promote `scratch/r32/classify.py` into `tools/fuzz_replay.py`/`tools/difftest.py`, with
     independent verdict, mode, first-error, warning and operational dimensions;
   - add gates for new false accepts, mode changes and first-error regressions;
   - add a CI workflow running the Wine-free tests and saved-oracle checks;
   - record the output contract and configuration format in PLAN.md;
   - make the mutation operators more realistic, then run fresh seeds 421–424.
4. **M1:** triage the 48 replay false accepts plus the three probe false accepts into rule
   families before sizing the work. The unknown table field case is the editor-visible one.
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
