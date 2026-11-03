# opencrbasic plan

Goal: help write reliable CRBasic programs for the CR6, CR1000X, CR300 and CR350. opencrbasic
runs natively on Linux, macOS and Windows and does three jobs:

1. **Verdict:** give the same accept/reject verdict and execution mode as the reference
   pre-compiler, and point at the same first error.
2. **Strictness:** report likely defects the reference compiler accepts, as clearly separate
   opencrbasic diagnostics.
3. **Editor:** serve both through the language server ([LSP_PLAN.md](LSP_PLAN.md)).

CR6 must remain a drop-in for the `cr6comp` module of the open-loggernet package.

**Human decision, 2026-10-24.** Exact parity with the reference compilers is not a goal in
itself. Priorities, from most to least important:

1. False accepts and false rejects.
2. Execution mode.
3. The first reported error.
4. Diagnostics beyond the reference compiler, which raise the quality and strictness of the
   output.

Order, wording and cascades of later diagnostics matter only where they would mislead a
programmer. Releases are gated by the [v1.0 roadmap](#remaining-work-strategy).

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
`compiler/`. All four are supported; only CR6 and CR1000X gate v1.0, and CR300/CR350 are best effort
([profile scope](#v10-milestones)). Granite 6/9/10 and legacy CR1000/CR3000/CR800/CR9000X/CR5000 remain
outside this plan. All four share the language front end and
the table-driven design; they differ in instruction set, predefined symbols (terminals, COM
ports, ranges), parameter type codes of some instructions, and output conventions (CR300/CR350
print `<file> -- Compiled OK.` instead of a mode). The implementation therefore takes a *logger
profile*: `data/<logger>/{instructions,functions,symbols}.json` extracted from each exe by
`tools/extract_tables.py --exe ... --out ...`, plus a small hand-written profile module
(terminal model, messages that differ). CR6 data is at the `data/` root. Differences measured
2026-09-25: CR1000X vs CR6
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
tdf.py         table definition file
timing.py      -t timing estimates
```

## Explicit exclusions from parity

- **CDM binary image (`-b`).** The CPI Device Module image that `CR6Comp.exe -b <file>` writes
  for programs using `CDM*`, `CPIAddModule2`, `CPISpeed2` or `CPIFileSend` is out of scope. Its
  absence is not a parity difference or a known output difference. No station corpus program
  uses CPI/CDM hardware.
  - Kept behavior: `-b` still prints the real `No CDM devices used in this program ` message when
    the program uses no CDM instruction, and still validates CDM instruction arguments, port sets
    and channel rules like any other instruction (those checks stay in scope).
  - For a program that does use CDM hardware, `-b` writes an empty file. That is documented and
    accepted; it is not to be treated as a defect or reverse-engineered unless the human reopens
    the decision.
  - Decision: human, 2026-10-12.

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

How the replay classes changed since this baseline is in `PROGRESS.md`. Recordings of reproducers
that compile against station corpus siblings carry a sibling digest, so a stale one is reported
instead of measured. Outside the replay baseline, four 2026-10-24 probes were also false accepts:
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

Station programs use 67 of the 410 CR6 instructions. A name search finds 83 instructions with no
rule beyond signature and parameter types. Only `ClockSet`, `ExitFunction` and `IncludeSection` of
those are used here, and `IncludeSection` is handled in `source.py`. This search does not measure
rule coverage.

**Relevant instruction set.** The GEUS station programs in `examples/Logger Programs/`
(PROMICE, GC-Net, GIOS, SnowFox, WATSON; 42 `.CR1X`/`.CR6` files outside `Archive/`, read
statically on 2026-10-24) cover the same kind of remote weather-station work. Together with the
station files they use about 100 instructions and functions
(`scratch/r32/instruction_usage.txt`).

GEUS adds instructions the station does not use:
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
rejection is therefore a verdict to match, not a defect in the corpus.

**GEUS measurement.** All 66 `.CR1X` programs under `examples/Logger Programs/`, including
`Archive/`, `Testing/` and `Development/`, compile with `cr1000xcomp.exe`, each with its directory
supplied as siblings; `tools/difftest.py --group geus --logger cr1000x` reports 66/66 exact.
There are no GEUS `.CR6` programs. A parenthesized assignment or instruction
(`If ... Then (wind_direction = 0)`) is that statement on all four loggers, and an alias named
`Status`, `Public` or `Settings` makes the name a value
([checker notes](docs/checker.md#parenthesized-statements-round-33)). Whether the parenthesized
body assigns on the logger is not observed; if it compares and discards the result, it is a silent
no-op and a check candidate.

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

**Output contract (tested in `tests/test_checks.py`).** `crbc` default output is the reference
channel only. With `crbc --strict`:
- checks go to stderr as `<file> line N: <severity> <code>: <message>`; stdout and `CR6cmple.xxx`
  stay byte-identical;
- the exit status keeps 0/1 for the reference verdict and is 3 for "reference accepted, strict
  error";
- `--report FILE` writes a JSON report (round 52, `docs/checker.md` "Check configuration and report
  file") and also holds the CR6 coverage notices ([inventory](docs/inventory.md)).

open-loggernet's `cr6comp` judges success from the output text, so strict findings must never appear in
stdout. `--strict` does not imply `-u`; the language server enables `-u`-equivalent unused warnings
as a separate setting.

**Configuration.** A project file, `opencrbasic.toml` at the workspace root with per-entry and
per-profile overrides, holds:
- the enabled check codes and severities;
- scoped suppressions, each with a reason.

CLI options and LSP settings override the file in a documented precedence. The configuration is
part of the analysis identity, including the LSP cache key.

Format (read with `tomllib`, so no runtime dependency):

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

  This applies always to rules exercised by the relevant instruction set (station and GEUS
  programs) or the help examples, and otherwise whenever one shared rule clears a family. Fix a same-location message only where the reference
  names a different cause.
- **Flag with a coverage notice:** a rule that needs a deep model and is exercised by neither
  corpus, for example the allocation `Out of memory.` cascades or the 32-bit clamping of non-finite
  arguments. A flagged false accept stays listed by identity in PROGRESS; whether it blocks M1
  depends only on the [M1 gate scope](#v10-milestones).
- **Accept and document:** later-diagnostic order, cascade and wording differences, as a stated
  normalization in `docs/`. These are not regressions and do not block a release.

### opencrbasic check candidates

The reference behavior below is from 2026-10-24 CR6 probes (`scratch/r32/lint/`) unless a row gives
its profiles and a later date. "Not probed" rows have no reference observation. Each check needs its
exact form probed before it is implemented.

| Candidate | Reference CR6 | Evidence and boundary |
|---|---|---|
| `Alias` named after an instruction, function, system table, keyword or special value (`Alias D(3)=Status`) | compiles, all four (2026-10-24) | **shipped as `OCB001`, error, default on under `--strict`** (human request 2026-10-24); the name shadows the original ([checker notes](docs/checker.md#alias-names-that-are-crbasic-names-round-33)). Predefined CONSTs are reference errors and excluded |
| Constant index past the declared storage (`A(7)` on `A(6)`) | compiles | outside the declaration; the runtime effect is not observed. Must resolve scope, aliases with backing storage (`docs/checker.md` round 30), flattened multidimensional indexing and String character indices |
| Whole array to a `Function` parameter whose body uses more than one element | compiles | only the first element arrives (`functionendfunction.htm`, [lessons](https://github.com/UtrechtUniversity/open-crbasic-skills/blob/main/plugins/crbasic/skills/crbasic/references/lessons.md#arrays-passed-to-helpers)); exclude pointer parameters and scalar intent |
| A String-producing `+` feeding a numeric destination (`L = 2000 + Mid(...)`) | compiles, all four (2026-10-30) | **shipped as `OCB002`, warning** ([checker notes](docs/checker.md#opencrbasic-checks-ocb002-ocb007-m3)); only chains mixing numeric and String operands, since GEUS programs join Strings into a number's text on purpose. Feeding arithmetic (`(2000 + Mid(...)) * 2`) is a reference error since round 65 |
| String literal longer than the proven destination capacity | compiles, all four (2026-10-30) | **shipped as `OCB007`, warning**; capacity follows the round-29 slot rule and the `String * 1` case (`dim.htm`); the runtime truncation is not observed |
| Runtime division by a literal zero | compiles, all four (2026-10-30) | **shipped as `OCB004`, warning**; a constant zero divisor including a `Const` or a fold; compile-time rejections stay in the reference channel |
| Negative or wrapped delay: `Delay(1,-5,Sec)`, `Delay(2,-1,Sec)`, `ExciteV(Vx1,0,-10)` | `Delay` forms compile, all four; negative `ExciteV` compiles on CR300/CR350 only, a reference error on CR6/CR1000X (2026-10-30) | **shipped as `OCB003`, warning**, for a constant negative interval (human proposal 2026-10-28; [round 57](docs/checker.md#task-memory-overflow-of-long-delays-round-57)). Not shipped: a `Delay` interval of 2^31 or more. The runtime effect is not observed; delay 0 is legitimate |
| `DataInterval` that divides neither an hour nor a day | compiles, all four (2026-10-30) | **shipped as `OCB006`, info**; respects `Interval=0` and offsets ([lessons](https://github.com/UtrechtUniversity/open-crbasic-skills/blob/main/plugins/crbasic/skills/crbasic/references/lessons.md#datainterval-phase), `datainterval.htm`) |
| Unsized `String` parameter | compiles, all four (2026-10-30) | **shipped as `OCB005`, info**; 23-character limit ([lessons](https://github.com/UtrechtUniversity/open-crbasic-skills/blob/main/plugins/crbasic/skills/crbasic/references/lessons.md#default-string-parameter-size)), escalate only with a proven longer argument |
| Derived constant inside `ConstTable` that depends on another editable field | not probed | not recomputed ([lessons](https://github.com/UtrechtUniversity/open-crbasic-skills/blob/main/plugins/crbasic/skills/crbasic/references/lessons.md#derived-constants-in-consttable)) |
| String argument with a proven embedded `0x00` | not probed | truncated ([lessons](https://github.com/UtrechtUniversity/open-crbasic-skills/blob/main/plugins/crbasic/skills/crbasic/references/lessons.md#binary-strings-passed-as-parameters)) |
| Indexed `CHR` writes that overwrite an earlier byte with the terminator | not probed | documented example in `chr.htm` |
| Binary data read through `SerialOpen` formats 0/16, which filter NUL and bytes above 127 | not probed | `serialopen.htm`; needs evidence that binary bytes are expected |
| A `Function` path that returns no value | not probed | returns NAN (`functionendfunction.htm`); exempt side-effect-only functions |
| A non-day-dividing `SlowSequence` interval used for a clock gate | not probed | free-running ([lessons](https://github.com/UtrechtUniversity/open-crbasic-skills/blob/main/plugins/crbasic/skills/crbasic/references/lessons.md#slow-sequence-alignment)); free-running sequences are valid |
| Proven overflow before assignment, or values outside the FP2 range in a table | not probed | `public.htm`, `datatypes.htm`; needs conservative value analysis |

`SerialOpen` without an explicit communication mode is not a defect check: SDM-SIO devices
require it omitted (`serialopen.htm`).

The probes confirm that the reference itself rejects unknown table fields and unknown
`Status`/`Settings` names, so those stay in the reference channel as the false accepts above.

### v1.0 milestones

| # | Milestone | Content | Exit criterion |
|---|---|---|---|
| M0 | Metric, policy and CI | Separate verdict, mode, first error, warnings, later diagnostics and operational failure as independent dimensions in `fuzz_replay.py`/`difftest.py`, with per-profile denominators. Add machine gates for new false accepts, mode changes and first-error regressions. Run fixtures and saved-oracle tests without Wine in a CI workflow. Decide the output contract, the configuration format, and the GEUS corpus harness. Improve the realism of the existing mutation operators | Impact table produced by a tool and gated; CI runs on every commit; decisions recorded here |
| M1 | Verdict safety | Triage the 48 replay plus four probe false accepts into rule families, fix them under the decision rule, and run fresh realistic seeds | No crash, hang or implementation exception on any corpus. On the gated profiles, no false accept or false reject in the relevant area (see M1 gate scope below) in repository, help, replay or fresh `--relevant` fuzz corpora; every other false accept or reject listed by identity |
| M2 | Mode and first error | Fix the mode difference and the 93 first-location differences; review the 220 same-location messages for misleading causes | Mode equal on every corpus. First error (file, line and cause) equal on repository and help corpora; per-profile rate on rejected replay cases reported and not lower than the round-30 baseline; every remaining misleading-cause case listed |
| M3 | opencrbasic checks | Framework (codes, severities, configuration, `--strict`, report file, separate result field) and the first checks from the table | Every shipped check meets the evidence-quality bar above, with positive tests, accepted controls and its default decision. Each firing on `crbasic/` programs is fixed or justified. The output contract is tested, including `cr6comp`, `--no-message-file` and existing option combinations |
| M4 | Evidence inventory and notices | Generate a CR6 inventory per instruction: builder, helper call sites, direct and reachable diagnostic strings, unresolved calls, linked Python rules and fixtures (`tools/builder_map.py`, `tools/message_catalog.py`). It is an inventory, not a coverage percentage | Generated by a tool with pinned binary hashes; states marked "unknown" or "partially tested" where evidence is missing; notices fire only on an identified missing rule |
| M5 | Artifacts | Matrix of stdout, message-file bytes, TDF, table list, signature, preprocessing and timing per profile and option, with expected, missing and empty outputs | CR6 matrix rerun with no regression; each other output marked supported or best effort |
| M6 | Editor | Carry channel, code and evidence link through the analysis result, worker serialization, per-entry composition and publication. Match quick fixes by code rather than text | LSP tests: unsaved includes, several entry contexts, configuration changes clearing findings, stale results, partial syntax suppressing unsound checks, strict errors not disabling formatting |
| M7 | Release | Version policy for the package, `__version__`, VSIX and facade API; installed-package check (`tools/lsp_install_check.py`); README on channels, scope and the accepted-difference normalization | Tagged v1.0 with revision, configuration, corpus hashes, commands, known failures and exclusions recorded |

**Status (2026-10-30, round 70).** M0 done. **M1 and M2 done**: signed off by the Codex `gpt-6.1-sol`
read-only review of round 68 (M2 complete; `scratch/r68/codex-signoff.log`, local only) after round
69 removed the last M1 blocker, an implementation exception on numbers beyond the float range. The
evidence and remaining listed cases are in `PROGRESS.md` ("Round 69") and the
[first-error review](docs/first-error-review.md). **M3 done (round 70)**: checks
`OCB001`-`OCB007` meet the exit criterion, signed off by a Codex `gpt-6.1-sol` read-only review
(`scratch/m3b/codex-review3.log`, local only); see `PROGRESS.md` "Round 70". **M4 done (round 72)** for CR6: `tools/inventory.py` writes the pinned
[evidence inventory](docs/inventory.md) and the coverage-notice table; see `PROGRESS.md` "Round 72". **M6 done (round 73)**: checks and notices reach the language server with channel, code and evidence link; see `PROGRESS.md` "Round 73". **M5 done (round 74)**: the [artifact matrix](docs/artifacts.md) is gated on CR6 and every other output is marked supported or best effort; see `PROGRESS.md` "Round 74". **M7 done (round 75)**: opencrbasic 1.0.0, tag `opencrbasic-v1.0.0`, [release record](docs/release-1.0.md). The v1.0 roadmap is complete.

**Profile scope (human decision, 2026-10-24).** v1.0 gates verdict safety, mode and first error on
CR6 and CR1000X. CR300 and CR350 are best effort: they keep their tests, fixtures and replay
measurements, and a regression there is reported, but it does not block v1.0. The station corpus uses CR6 and
the bench CR1000X, and the GEUS programs are almost all CR1000X.

**M1 gate scope (human decision, 2026-10-28).** M1 gates false accepts and false rejects only in
the relevant area: statements using an instruction or function of the
[relevant instruction set](#where-v10-starts-round-30-measurement) (`tools/fuzz.py`
`relevant_names`), and language constructs not tied to one instruction. A case on any other
instruction, for example `CDM_*`, `MenuPick` or `TableFile`, does not block M1; it stays listed by
identity in PROGRESS and gets a coverage notice with M4. M1 work therefore centres on fresh
`--relevant` fuzz seeds.

**M2 fix scope (human decision, 2026-10-29).** M2 fixes first-error differences on relevant rules
only where they come from a mistake a programmer could realistically make, the kind
`tools/fuzz.py --relevant` aims at. A difference that appears only in a shrunk, truncated reproducer
or in token-splitting fuzz noise stays listed in the [first-error review](docs/first-error-review.md)
and does not block M2.

### Round method

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

## Agreement metric

`tools/difftest.py` and `tools/fuzz_replay.py` classify every case as one of: exact (equal exit
code and normalized output), verdict match with different messages, false accept, false reject,
mode difference, or reference/runner failure. Runner failures are never verdicts. Message
differences are reported in the impact classes of the table above, and those classes,
not the exact count, drive priorities. Artifact parity (TDF, table list, signature) needs byte
comparisons, separately per profile.

## Budget rule

Keep PROGRESS.md accurate after every milestone so a new session can resume. Delegation follows
PROGRESS.md "Work method".
