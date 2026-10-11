# opencrbasic plan

Goal: native (Linux/macOS/Windows) replacements for the four reference CRBasic pre-compilers
below, matching their observable behavior exactly. CR6 must remain a drop-in for
`scripts/cr6comp.py`. Future tooling and simulation are deferred until compiler parity.

This document is the stable plan. Live state is in `PROGRESS.md`; reverse-engineering findings
are in `docs/`. If work stops mid-way, resume from `PROGRESS.md` -> "Next steps".

The proposed compiler-backed CR6 editor workstream is planned in [LSP_PLAN.md](LSP_PLAN.md),
with its own handoff ledger in [LSP_PROGRESS.md](LSP_PROGRESS.md). Planning does not change the
compiler-parity priority or start implementation.

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
`compiler/`. The 2026-10-01 continuation requires parity for all four; CR1000X/CR300/CR350 are
no longer stretch work. Granite 6/9/10 and legacy CR1000/CR3000/CR800/CR9000X/CR5000 remain
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

## Phases and exit criteria

| # | Phase | Exit criterion |
|---|---|---|
| 0 | Oracle harness under Wine, corpus collection (repo programs, CHM examples) | reference identities pinned; runner failures excluded from verdicts; saved evidence |
| 1 | Binary RE: CLI options, output files, tables, type codes, error catalog, pipeline | `docs/cli.md`, `docs/binary.md`, `docs/type-codes.md` written; data JSON generated |
| 2 | Lexer, preprocessor, parser | every corpus program parses; unit tests |
| 3 | Semantic checker + diagnostics | repo programs: identical output to oracle; corpus agreement rate reported |
| 4 | Differential fuzzing (mutated corpus programs) | agreement rate tracked in PROGRESS.md; mismatches triaged |
| 5 | Mode selection, warnings, TDF, ProgSignature | identical to oracle on the corpus |
| 6 | Complete parity gate | all four profiles: zero known output/artifact differences, false accepts/rejects, crashes, hangs; coverage limits recorded |

## Remaining-work strategy

Work in small chunks: one observed rule or one harness defect, immediate focused check, then
the broader regression gate. Commit each verified chunk with a single-line message, as requested
on 2026-10-02. Do not batch unrelated semantic fixes or rewrite the parser/checker
while diagnostic ordering is still being reconstructed.

1. Make measurements trustworthy first: distinguish oracle infrastructure failures and logger
   compiler failures; check exit codes alongside output; retain failing cases and expose machine
   readable summaries and nonzero gate exits. Never prune fixed reproducers merely to raise the
   agreement percentage: they are regression evidence.
2. Refresh all four profiles over `crbasic/`, the CHM corpus, and saved reproducers. Group the
   *current* differences by first differing diagnostic, instruction/type-code handler, and pass.
   Rank families by affected cases, shared-profile benefit, and risk. Check accepted boundary
   cases as well as rejected ones before generalising a rule.
3. Prefer one data-driven family handler over many instruction-specific branches when oracle
   evidence establishes shared behavior. Keep exceptions in profile data. Record the emission
   phase, suppression/cutoff, ordering, and CR300/CR350 cascade effects with each rule.
4. Keep the existing two-pass checker orchestration until a measured family requires changing
   it. Introduce shared helpers incrementally; a wholesale pass scheduler or expression rewrite
   would obscure which change improved or regressed parity. Checker exceptions currently get
   swallowed; validation must eventually expose these as implementation failures.
5. Close output coverage separately: stdout, message-file bytes, signature, table list, TDF,
   preprocessing, timing, and CDM image for each profile and supported option combination.
   Repository-program agreement alone cannot establish complete language or artifact parity.
6. After each semantic chunk: focused oracle probes and tests, full pytest, four-profile
   difftest/replay with no false rejects/crashes/hangs or lowered exact agreement; then a fresh
   bounded fuzz seed after the family is stable. Finish with `cd scripts && uv run verify.py`.

Who does what (current human instructions) is in PROGRESS.md "Work method": a coordinating
session integrates and verifies; Claude subagents (Haiku for easy/medium chunks, Sonnet for rule
discovery and reverse engineering) implement in their own worktrees; Codex `gpt-6.1-sol` does
read-only reviews only. Workers get disjoint file ownership and return observed validation.

## Agreement metric

`tools/difftest.py` compiles each corpus file with both compilers and classifies:
exact (equal exit codes and identical normalised output), verdict-match (both pass or both fail, message differs),
mismatch. Report all three in PROGRESS.md; exact-match on the repo's own programs is the first
target because `scripts/verify.py` depends on it. This is a normalized diagnostic metric, not
byte parity: trimming whitespace can conceal differences. Completion additionally requires
byte comparisons for artifacts and a documented stdout transport-normalization rule. Invalid
oracle runs are harness failures, never false accepts. Historical `examples/` are excluded.

## Budget rule

Keep PROGRESS.md accurate after every milestone so a new
session can resume. Delegation follows PROGRESS.md "Work method".
