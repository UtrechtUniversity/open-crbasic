# opencrbasic plan

Goal: help write reliable CRBasic programs for the CR6, CR1000X, CR300 and CR350. opencrbasic
runs natively on Linux, macOS and Windows and does three jobs:

1. **Verdict:** give the same accept/reject verdict and execution mode as the reference
   pre-compiler, and point at the same first error.
2. **Strictness:** report likely defects the reference compiler accepts, as clearly separate
   opencrbasic diagnostics.
3. **Editor:** serve both through the language server ([LSP_PLAN.md](LSP_PLAN.md)).

CR6 must remain a drop-in for `scripts/cr6comp.py`.

**Human decision, 2026-10-24.** Exact parity with the reference compilers was the first step, and
it built the needed understanding of the language. It is no longer the goal in itself.
Priorities, from most to least important:

1. False accepts and false rejects.
2. Execution mode.
3. The first reported error.
4. Diagnostics beyond the reference compiler, which raise the quality and strictness of the
   output.

Order, wording and cascades of later diagnostics now matter only where they would mislead a
programmer. The [v1.0 roadmap](#remaining-work-strategy) replaces the old Phase 6 parity gate.

This document is the stable plan. Live state is in `PROGRESS.md`; reverse-engineering findings
are in `docs/`. If work stops mid-way, resume from `PROGRESS.md` -> "Next steps". The editor
workstream has its own [plan](LSP_PLAN.md) and [ledger](LSP_PROGRESS.md).

## What CR6Comp.exe is (established)

- A 32-bit MSVC console build (PE timestamp 2025-03-04, version string `CR6.14.2.0`) of the CR6
  operating system's own on-logger compiler. The logger recompiles the `.CR6` source it receives;
  on the PC the exe only checks the program and reports. Evidence: runtime/OS strings in the exe
  (`Cmd_OnLine`, `charger_cmd`, `pc_temp_probe`), `docs/binary.md`.
- Observable contract (from runs under Wine, `docs/cli.md`):
  - stdout `<file> -- Compiled in SequentialMode.` / `PipelineMode.`, or
    `<file> -- Compile Failed!`, a blank line, then `line N: <message>` lines.
  - `Warning: ...` lines after a success.
  - The same text is written to `CR6cmple.xxx` in the working directory.
  - Exit status 0 on success, 1 on failure.
  - Extra command-line options exist (version, TDF, list/preprocessed/CDM outputs); see
    `docs/cli.md`.
- Instruction signatures are table-driven: a 410-entry table `{name, param-type-code list,
  compile fn, runtime fn}`, a builtin-function table and a predefined-symbol table (constants such
  as `mV5000`, `ComC1`, `TypeT`). These are extracted by `tools/extract_tables.py` into
  `src/opencrbasic/data/`.

## Target loggers

Required profiles: CR6 (`CR6Comp.exe` CR6.14.2.0), CR1000X (`cr1000xcomp.exe` CR1000X.8.2.0),
CR300 (`cr300comp.exe` CR300.Std.11.02), CR350 (`cr350comp.exe` CR350.Std.1.07), all in
`compiler/`. The 2026-10-01 continuation made all four required profiles. Under the 2026-10-24
decision all four stay supported, but only CR6 and CR1000X gate v1.0; CR300/CR350 are best effort
([profile scope](#v10-milestones)). Granite 6/9/10 and legacy CR1000/CR3000/CR800/CR9000X/CR5000 remain
outside this plan. All four share the language front end and
the table-driven design; they differ in instruction set, predefined symbols (terminals, COM
ports, ranges), parameter type codes of some instructions, and output conventions (CR300/CR350
print `<file> -- Compiled OK.` instead of a mode). The implementation therefore takes a *logger
profile*: `data/<logger>/{instructions,functions,symbols}.json` extracted from each exe by
`tools/extract_tables.py --exe ... --out ...`, plus a small hand-written profile module
(terminal model, messages that differ). CR6 data stays at `data/` root; relocating working tables
does not improve parity. Differences measured 2026-09-25: CR1000X vs CR6
share 402 instructions and all 134 functions (CR1000X adds `SWVX`, C5-C8, P1/P2, VX1-VX4);
CR300/CR350 share 240 instructions with the CR6, 23-24 of them with different signatures.

## Approach

1. **Oracle first.** Wine runs the real exe (`tools/oracle.py`, cached by content hash). Every
   behaviour we implement is checked differentially against it. Nothing is claimed as matching
   unless the difference test passed.
2. **Tables from the binary, semantics from docs + oracle.** Extract data tables from the exe;
   decode type codes and rules with Ghidra decompilation (`docs/binary.md`) and targeted oracle
   probes; parameter names/optionality from `extracted_chm/`.
3. **Write our own code.** Decompiled code informs behaviour notes in `docs/`; it is never pasted
   into the implementation. See `docs/legal.md`.
4. **Python**, standalone package (`pyproject.toml`, uv), no runtime dependencies, so it can be
   split out of this repository unchanged.

## Architecture

```
source.py      load file, CR/LF handling, line map, Include/IncludeSection expansion
lexer.py       tokens (case-insensitive keywords, numbers incl. &H/&B, strings, comments, _ continuation)
preprocess.py  Const folding, #If/#ElseIf/#Else/#EndIf, ConstTable, conditional compile
parser.py      statements + expressions -> syntax.py nodes (declarations, DataTable, Sub/Function,
               If/Select/Do/For/While, Scan/SubScan/SlowSequence, instruction calls)
checker/symbols.py declared names, types, dimensions, aliases; profiles.py loads predefined symbols
checker/       semantic passes: structure (BeginProg/Scan nesting, declaration placement),
               instruction signatures (data/instructions.json + type codes), tables
               (DataInterval vs Scan, field naming), terminals (port conflicts), mode selection
diagnostics.py CR6Comp-compatible message text and ordering
cli.py         `crbc` command: CR6Comp-compatible stdout, CR6cmple.xxx, exit status
tdf.py         (phase 5) table definition file
timing.py      -t timing estimates
```

## Explicit exclusions from parity

This exclusion stays in force under the v1.0 roadmap.

- **CDM binary image (`-b`).** The CPI Device Module image that `CR6Comp.exe -b <file>` writes
  for programs using `CDM*`, `CPIAddModule2`, `CPISpeed2` or `CPIFileSend` is out of scope. It is
  not required for the Phase 5 or Phase 6 exit criteria, and its absence is not a parity
  difference, a known output difference, or a reason to keep Phase 6 open. No FirnMelt program
  uses CPI/CDM hardware, and `verify.py` and `flash.py` do not pass `-b`.
  - Kept behavior: `-b` still prints the real `No CDM devices used in this program ` message when
    the program uses no CDM instruction, and still validates CDM instruction arguments, port sets
    and channel rules like any other instruction (those checks stay in scope).
  - For a program that does use CDM hardware, `-b` writes an empty file. That is documented and
    accepted; it is not to be treated as a defect or reverse-engineered unless the human reopens
    the decision.
  - Decision: human, 2026-10-12.

## Phases completed before v1.0

| # | Phase | State (details in `PROGRESS.md`) |
|---|---|---|
| 0 | Oracle harness under Wine, corpus collection | done; runner failures are excluded from verdicts |
| 1 | Binary reverse engineering: CLI, tables, type codes, error catalog | done (`docs/cli.md`, `docs/binary.md`, `docs/type-codes.md`) |
| 2 | Lexer, preprocessor, parser | every corpus program parses without an exception; some constructs remain `Unparsed` |
| 3 | Semantic checker and diagnostics | repository and help-example verdicts agree on usable reference results, all four profiles |
| 4 | Differential fuzzing | ongoing; it is the main source of evidence for v1.0 |
| 5 | Mode, warnings, TDF, ProgSignature | CR6 TDF and table list: 210/210 compared artifacts byte-identical (round 22), with 308 reference rejections, six missing expected artifacts and one reference fault; other artifacts partial |

The former Phase 6 exit criterion (zero known differences of any kind on all four profiles) is
retired by the 2026-10-24 decision. It was an unbounded target: message order and cascade text
after the first error made up most of the remaining differences.

## Remaining-work strategy

### Where v1.0 starts (round-30 measurement)

The 3,158 saved fuzz reproducers are the broadest evidence: 2,983 mutate help examples and 175
mutate repository programs. They are saved mismatch reproducers, so they are neither a sample of
real programming mistakes nor of all generated mutants.

Grouped by impact on a programmer (`scratch/r32/classify.py` over the round-30 replay; not yet a
tool). The first error is the first diagnostic with its file, line and message, including
diagnostics without a line number:

| Class | Cases | CR6 | CR1000X | CR300 | CR350 | Impact |
|---|---:|---:|---:|---:|---:|---|
| Exact | 2,391 | 825 | 330 | 607 | 629 | none |
| Same first error, later diagnostics differ | 334 | 66 | 60 | 117 | 91 | low |
| Same first error, same diagnostics in another order | 37 | 7 | 4 | 17 | 9 | low |
| Same first location, different message | 220 | 75 | 59 | 38 | 48 | medium: possibly misleading text |
| We miss the reference's first error | 58 | 26 | 25 | 6 | 1 | high |
| We report an earlier first location | 35 | 5 | 6 | 13 | 11 | high |
| Both accept, warnings differ | 15 | 2 | 8 | 3 | 2 | low to medium |
| Both accept, execution mode differs | 1 | 0 | 1 | 0 | 0 | high: runtime scheduling |
| False accept | 48 | 14 | 17 | 9 | 8 | highest: the logger refuses the program |
| False reject | 0 | 0 | 0 | 0 | 0 | highest (gated) |
| Reference or runner failure | 19 | 6 | 4 | 3 | 6 | no verdict |

Rounds 34-35 fixed the four probe false accepts below and 13 replay false accepts, then fresh
seeds 421-424 added 17 more, 8 of them fixed; `PROGRESS.md` lists the flagged rest. Outside the replay baseline, four 2026-10-24 probes were also false
accepts:
- an unknown table field (`Tbl.NoSuchField(1,1)`, CR6);
- an unknown `Settings.` name (CR6);
- an assignment to an unknown `Status.` field (CR6);
- a `Public X = Status.NotARealField` initializer, which CR6 and CR1000X reject with
  `Variable illegal in Parameter`.

The first three are in `scratch/r32/lint/`, the last in `scratch/r32/status/`.

The repository programs and the help examples have no verdict mismatch on usable reference
results on any profile. The parser accepts every corpus program without an exception, but some
constructs remain `Unparsed` nodes, for example in `samplemaxmin_1.CR6` and `snmpvariable_1.CR6`
(`tests/test_corpus.py`). Unparsed statements escape checks of every channel.

FirnMelt programs use 67 of the 410 CR6 instructions. A name search finds 83 instructions with no
rule beyond signature and parameter types. Only `ClockSet`, `ExitFunction` and `IncludeSection` of
those are used here, and `IncludeSection` is handled in `source.py`. This search does not measure
rule coverage.

**Relevant instruction set.** The GEUS station programs in `examples/Logger Programs/`
(PROMICE, GC-Net, GIOS, SnowFox, WATSON; 42 `.CR1X`/`.CR6` files outside `Archive/`, read
statically on 2026-10-24) cover the same kind of remote weather-station work. Together with the
FirnMelt files they use about 100 instructions and functions
(`scratch/r32/instruction_usage.txt`).

GEUS adds instructions FirnMelt does not use:
- `CardOut`, `WindVector`, `GetRecord`;
- `BrHalf`, `BrHalf4W`, `ExciteV`, `PulseCount`, `CurrentSE`, `PortSet`;
- the `File*`, `FTPClient`, `PPPOpen` and `EmailRelay` functions.

Their legacy `.CR1` (CR1000) and `.CR8` (CR800) programs are outside the four profiles. Of the
relevant set, only `IncludeSection`, `ExitFunction`, `ClockSet`, `MetaData`, `IPNetPower` and
`Network` have no rule beyond their signature. This set defines "relevant" in the decision rule
below and orders M1 triage, M3 check selection and the M4 inventory.

**GEUS corpus (human decision, 2026-10-24).** These programs may be compiled with the reference
compilers and opencrbasic:
- as a corpus of real programs instead of mutants;
- as false-positive controls for opencrbasic checks.

The contact sent every program they had, so some may not be functional or compilable. A reference
rejection is therefore a verdict to match, not a defect in the corpus. The programs stay
historical prior art for FirnMelt's own CRBasic (`AGENTS.md`).

**First GEUS measurement (2026-10-24, `scratch/r32/geus/`).** All 66 `.CR1X` programs under
`examples/Logger Programs/`, including `Archive/`, `Testing/` and `Development/`, were compiled with
`cr1000xcomp.exe` and opencrbasic, with each program's directory supplied as siblings.

The reference accepted all 66. opencrbasic matched 64 exactly and **falsely rejected two**: the
first false rejects seen on any corpus.
- **`Archive/QAS_B_LoggerProgramV2.CR1X`:** a user `Alias D2out(3)=Status` followed by
  `Sample(1,Status,FP2)`; we report `Invalid symbol in expression: Status.`
- **`GIOS_WAT_BR_V4_0.CR1X`:** a one-line `If ... Then (wind_direction = 0)`; we report
  `Invalid, or out of place expression`.

Whether that parenthesized body assigns on the logger is not observed. If it compares and discards
the result, it is a silent no-op and a strong check candidate.

Round 33 fixed both: a parenthesized assignment or instruction is that statement on all four
loggers, and an alias named `Status`, `Public` or `Settings` makes the name a value
([checker notes](docs/checker.md#parenthesized-statements-round-33)). `tools/difftest.py --group geus
--logger cr1000x` now reports 66/66 exact. There are no GEUS `.CR6` programs.

### Three diagnostic channels

Every finding belongs to exactly one channel. Operational failures (timeouts, checker exceptions,
worker crashes) belong to none: they keep their existing separate reporting and are never shown
as a CRBasic diagnostic.

1. **Reference diagnostics.** These reproduce what the reference compiler reports, including its
   warnings and the `-u` unused warnings (`checker/unused.py`, `checker/warnings.py`). Their exit
   status is the reference verdict. Default `crbc` stdout and `CR6cmple.xxx` contain this channel
   only, so they remain a drop-in. Adding checks must not change mode, suppression, message
   buffer, signature or TDF.
2. **opencrbasic checks.** These report likely defects that the reference accepts. Each check has:
   - a stable code (`OCB` plus a number) and a severity;
   - oracle evidence that the reference accepts the exact form, on every profile it applies to;
   - evidence that the form is wrong: the CHM, a `knowledge/*/lessons.md` entry, or a hardware
     observation;
   - accepted controls showing where it does not fire;
   - a documented default-enabled decision.

   Checks never enter `Diagnostics.items`: `CompileResult.ok` and the error filter use that
   collection. They travel in a separate result field.
3. **Coverage notices.** These say that opencrbasic does not model a specific rule that the
   reference applies to a construct in the program. Each notice names that rule, from the
   [evidence inventory](#v10-milestones). A notice is information only and never counts as verdict
   safety.

**Output contract (decided in round 33, tested in `tests/test_checks.py`).** `crbc` default output
is unchanged. With `crbc --strict`:
- checks go to stderr as `<file> line N: <severity> <code>: <message>`; stdout and `CR6cmple.xxx`
  stay byte-identical;
- the exit status keeps 0/1 for the reference verdict and is 3 for "reference accepted, strict
  error";
- a JSON report file (`--report FILE`) and coverage notices are not implemented yet.

`scripts/cr6comp.py` judges success from the output text, so strict findings must never appear in
stdout. `--strict` does not imply `-u`; the language server enables `-u`-equivalent unused warnings
as a separate setting.

**Configuration.** A project file, `opencrbasic.toml` at the workspace root with per-entry and
per-profile overrides, holds:
- the enabled check codes and severities;
- scoped suppressions, each with a reason.

CLI options and LSP settings override the file in a documented precedence. The configuration is
part of the analysis identity, including the LSP cache key. Source comments are never used, because
owned `crbasic/` files allow only pointer comments.

Format (recorded in round 35, implemented with M3; `tomllib`, so still no runtime dependency):

```toml
[checks]                      # severity per code: "error" | "warning" | "info" | "off"
OCB001 = "error"

[profile.cr300.checks]        # per logger profile
OCB001 = "warning"

[entry."crbasic/Main.CR6".checks]   # per entry program, path relative to the file
OCB001 = "off"

[[suppress]]                  # one finding site; `reason` is required
code = "OCB001"
file = "crbasic/OldStation.CR6"     # glob, relative to the file
line = 12                     # optional; without it the whole file
reason = "alias kept for data continuity with the 2025 tables"
```

Precedence, highest first: CLI option, LSP setting, `entry`, `profile`, `[checks]`, the check's
built-in default. An unknown code, an unknown severity or a suppression without `reason` is a
configuration error reported on stderr; it never changes the reference verdict.

### Which gaps to fix and which to flag

Decide per *rule* (an instruction option, argument range or construct), not per instruction name:

- **Fix:**
  - every false accept;
  - the mode difference;
  - first-error differences.

  This applies always to rules exercised by the relevant instruction set (FirnMelt and GEUS
  programs) or the help examples, and otherwise whenever one shared rule clears a family. Fix a same-location message only where the reference
  names a different cause.
- **Flag with a coverage notice:** a rule that needs a deep model and is exercised by neither
  corpus, for example the allocation `Out of memory.` cascades or the 32-bit clamping of non-finite
  arguments. A flagged false accept stays listed by identity in PROGRESS and does not satisfy the
  verdict gate.
- **Accept and document:** later-diagnostic order, cascade and wording differences, as a stated
  normalization in `docs/`. These are not regressions and do not block a release.

### opencrbasic check candidates

The reference behavior below is from 2026-10-24 CR6 probes (`scratch/r32/lint/`); the other
profiles are not probed yet. "Not probed" rows have no reference observation. Each check needs its
exact form probed before it is implemented.

| Candidate | Reference CR6 | Evidence and boundary |
|---|---|---|
| `Alias` named after an instruction, function, system table, keyword or special value (`Alias D(3)=Status`) | compiles, all four (2026-10-24) | **shipped as `OCB001`, error, default on under `--strict`** (human request 2026-10-24); the name shadows the original ([checker notes](docs/checker.md#alias-names-that-are-crbasic-names-round-33)). Predefined CONSTs are reference errors and excluded |
| Constant index past the declared storage (`A(7)` on `A(6)`) | compiles | outside the declaration; the runtime effect is not observed. Must resolve scope, aliases with backing storage (`docs/checker.md` round 30), flattened multidimensional indexing and String character indices |
| Whole array to a `Function` parameter whose body uses more than one element | compiles | only the first element arrives (`functionendfunction.htm`, [lessons](../knowledge/crbasic/lessons.md#arrays-passed-to-helpers)); exclude pointer parameters and scalar intent |
| A String-producing `+` feeding a numeric destination or arithmetic (`L = 2000 + Mid(...)`) | compiles | concatenates ([lessons](../knowledge/crbasic/lessons.md#-with-a-string-operand-concatenates)); deliberate concatenation into a String is legitimate, and left-to-right typing matters |
| Measurement-task `PortSet` (no processing-task option) under a runtime condition | compiles | stays sequencer-controlled ([lessons](../knowledge/crbasic/lessons.md#conditional-digital-output)); `PortSet(...,...,1)` runs in the processing task (`portset.htm`) and is excluded |
| String literal longer than the proven destination capacity | compiles | silent truncation; capacity follows the round-29 slot rule and the `String * 1` case (`dim.htm`) |
| Runtime division by a literal zero | compiles | report the zero divisor without promising a result (`docs/checker.md` records profile-dependent outcomes); compile-time rejections stay in the reference channel |
| `DataInterval` that divides neither an hour nor a day | compiles | information only; respect `Interval=0`, months, offsets ([lessons](../knowledge/crbasic/lessons.md#datainterval-phase), `datainterval.htm`) |
| Unsized `String` parameter | not probed | information; 23-character limit ([lessons](../knowledge/crbasic/lessons.md#default-string-parameter-size)), escalate only with a proven longer argument |
| Derived constant inside `ConstTable` that depends on another editable field | not probed | not recomputed ([lessons](../knowledge/crbasic/lessons.md#derived-constants-in-consttable)) |
| String argument with a proven embedded `0x00` | not probed | truncated ([lessons](../knowledge/crbasic/lessons.md#binary-strings-passed-as-parameters)) |
| Indexed `CHR` writes that overwrite an earlier byte with the terminator | not probed | documented example in `chr.htm` |
| Binary data read through `SerialOpen` formats 0/16, which filter NUL and bytes above 127 | not probed | `serialopen.htm`; needs evidence that binary bytes are expected |
| A `Function` path that returns no value | not probed | returns NAN (`functionendfunction.htm`); exempt side-effect-only functions |
| A non-day-dividing `SlowSequence` interval used for a clock gate | not probed | free-running ([lessons](../knowledge/crbasic/lessons.md#slow-sequence-alignment)); free-running sequences are valid |
| Proven overflow before assignment, or values outside the FP2 range in a table | not probed | `public.htm`, `datatypes.htm`; needs conservative value analysis |

`SerialOpen` without an explicit communication mode is a project convention, not a defect check:
SDM-SIO devices require it omitted (`serialopen.htm`). It belongs in a FirnMelt profile, if
anywhere.

The probes confirm that the reference itself rejects unknown table fields and unknown
`Status`/`Settings` names, so those stay in the reference channel as the false accepts above.
The lesson that said otherwise could not be reproduced and was corrected on 2026-10-24
([`Status` names are compile-checked](../knowledge/crbasic/lessons.md#status-names-are-compile-checked)).

### v1.0 milestones

Round counts are estimates in the one-family-per-worker rounds of 29–31, with Codex research and
review. M1 and M2 are re-estimated after their family triage.

| # | Milestone | Content | Exit criterion | Rounds |
|---|---|---|---|---:|
| M0 | Metric, policy and CI | Separate verdict, mode, first error, warnings, later diagnostics and operational failure as independent dimensions in `fuzz_replay.py`/`difftest.py`, with per-profile denominators. Add machine gates for new false accepts, mode changes and first-error regressions. Run fixtures and saved-oracle tests without Wine in a CI workflow. Decide the output contract, the configuration format, and the GEUS corpus harness. Improve the realism of the existing mutation operators | Impact table produced by a tool and gated; CI runs on every commit; decisions recorded here | 1–2 |
| M1 | Verdict safety | Fix the two GEUS false rejects first. Triage the 48 replay plus four probe false accepts into rule families, fix them under the decision rule, and run fresh realistic seeds | No false reject, crash, hang or implementation exception on any corpus. On the gated profiles, no false accept in repository, help or replay corpora, and every flagged exception listed by identity | 3–6 after triage |
| M2 | Mode and first error | Fix the mode difference and the 93 first-location differences; review the 220 same-location messages for misleading causes | Mode equal on every corpus. First error (file, line and cause) equal on repository and help corpora; per-profile rate on rejected replay cases reported and not lower than the round-30 baseline; every remaining misleading-cause case listed | 2–4 |
| M3 | opencrbasic checks | Framework (codes, severities, configuration, `--strict`, report file, separate result field) and the first checks from the table | Every shipped check meets the evidence-quality bar above, with positive tests, accepted controls and its default decision. Each firing on `crbasic/` programs is fixed or justified. The output contract is tested, including `cr6comp.py`, `--no-message-file` and existing option combinations | 2–3 |
| M4 | Evidence inventory and notices | Generate a CR6 inventory per instruction: builder, helper call sites, direct and reachable diagnostic strings, unresolved calls, linked Python rules and fixtures (`tools/builder_map.py`, `tools/message_catalog.py`). It is an inventory, not a coverage percentage | Generated by a tool with pinned binary hashes; states marked "unknown" or "partially tested" where evidence is missing; notices fire only on an identified missing rule | 1–2 (CR6) |
| M5 | Artifacts | Matrix of stdout, message-file bytes, TDF, table list, signature, preprocessing and timing per profile and option, with expected, missing and empty outputs | CR6 matrix rerun with no regression; each other output marked supported or best effort | 1–2 |
| M6 | Editor | Carry channel, code and evidence link through the analysis result, worker serialization, per-entry composition and publication. Match quick fixes by code rather than text | LSP tests: unsaved includes, several entry contexts, configuration changes clearing findings, stale results, partial syntax suppressing unsound checks, strict errors not disabling formatting | 1–2 |
| M7 | Internal release | Version policy for the package, `__version__`, VSIX and facade API; installed-package check (`tools/lsp_install_check.py`); README on channels, scope and the accepted-difference normalization. Internal use only (`docs/legal.md`) | Tagged v1.0 with revision, configuration, corpus hashes, commands, known failures and exclusions recorded | 1 |

Total: about 12–22 rounds, against an open-ended 20–40+ for full parity.

M0 comes first because it changes what every later round measures and gates. M3 and M4 can then
run in parallel with M1/M2. M6 follows M3.

**Profile scope (human decision, 2026-10-24).** v1.0 gates verdict safety, mode and first error on
CR6 and CR1000X. CR300 and CR350 are best effort: they keep their tests, fixtures and replay
measurements, and a regression there is reported, but it does not block v1.0. FirnMelt uses CR6 and
the bench CR1000X, and the GEUS programs are almost all CR1000X.

### Round method (unchanged in substance)

Work on one observed rule family at a time:

1. Minimize a reproducer.
2. Inspect decompiled behavior; Codex research runs read-only.
3. Probe accepted boundaries and failures on every applicable profile.
4. Implement the smallest change.
5. Preserve the reference observations in a fixture and the owning document.

Never prune reproducers to improve agreement. Prefer one data-driven family handler over many
instruction-specific branches, and keep profile exceptions in profile data. Keep the two-pass
checker orchestration until a measured family requires changing it. Checker exceptions must
surface as implementation failures.

For an opencrbasic check, the evidence comes first: the reference accepting the pattern, the
reason it is wrong, and its false-positive boundary. Then the code. Each round ends with:

1. Focused tests, then the full pytest and server suites.
2. Fixture audits.
3. The four-profile difftest and the saved replay, with the impact table compared to the previous
   round.
4. `cd scripts && uv run verify.py`, also with `CR6COMP=opencrbasic`.

## Agreement metric

`tools/difftest.py` and `tools/fuzz_replay.py` classify every case as one of: exact (equal exit
code and normalized output), verdict match with different messages, false accept, false reject,
mode difference, or reference/runner failure. Runner failures are never verdicts. From M0 on,
message differences are reported in the impact classes of the table above, and those classes,
not the exact count, drive priorities. Artifact parity (TDF, table list, signature) needs byte
comparisons, separately per profile.

## Budget rule

Keep PROGRESS.md accurate after every milestone so a new session can resume. Delegation follows
PROGRESS.md "Work method".
