# opencrbasic plan

Goal: an open-source, native (Linux/macOS/Windows) re-implementation of Campbell Scientific's
`CR6Comp.exe` CRBasic pre-compiler, usable as a drop-in for `scripts/cr6comp.py`, and as the
front end for future CRBasic tooling (linting, host-side simulation, automated tests).

This document is the stable plan. Live state is in `PROGRESS.md`; reverse-engineering findings
are in `docs/`. If work stops mid-way, resume from `PROGRESS.md` -> "Next steps".

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

Primary: CR6 (`CR6Comp.exe`). Stretch (human request 2026-09-25): CR1000X, CR300, CR350, whose
compilers are in `compiler/` on the `add-compilers` branch (`cr1000xcomp.exe` CR1000X.8.2.0,
`cr300comp.exe` CR300.Std.11.02, `cr350comp.exe` CR350.Std.1.07; also Granite 6/9/10 and legacy
CR1000/CR3000/CR800/CR9000X/CR5000 compilers, out of scope). All share the language front end and
the table-driven design; they differ in instruction set, predefined symbols (terminals, COM
ports, ranges), parameter type codes of some instructions, and output conventions (CR300/CR350
print `<file> -- Compiled OK.` instead of a mode). The implementation therefore takes a *logger
profile*: `data/<logger>/{instructions,functions,symbols}.json` extracted from each exe by
`tools/extract_tables.py --exe ... --out ...`, plus a small hand-written profile module
(terminal model, messages that differ). CR6 data currently sits at `data/` root and moves to
`data/cr6/` once the first front end lands. Differences measured 2026-09-25: CR1000X vs CR6
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
parser.py      statements + expressions -> ast.py nodes (declarations, DataTable, Sub/Function,
               If/Select/Do/For/While, Scan/SubScan/SlowSequence, instruction calls)
symbols.py     scopes, predefined symbols (data/constants.json), aliases, units
checker/       semantic passes: structure (BeginProg/Scan nesting, declaration placement),
               instruction signatures (data/instructions.json + type codes), tables
               (DataInterval vs Scan, field naming), terminals (port conflicts), mode selection
diagnostics.py CR6Comp-compatible message text and ordering
cli.py         `crbc` command: CR6Comp-compatible stdout, CR6cmple.xxx, exit status
tdf.py         (phase 5) table definition file
sim/           (phase 6, stretch) host interpreter for automated testing
```

## Phases and exit criteria

| # | Phase | Exit criterion |
|---|---|---|
| 0 | Oracle harness under Wine, corpus collection (repo programs, CHM examples, `examples/`) | `tools/oracle.py` reproduces the verify.py results; corpus + recorded expected outputs committed |
| 1 | Binary RE: CLI options, output files, tables, type codes, error catalog, pipeline | `docs/cli.md`, `docs/binary.md`, `docs/type-codes.md` written; data JSON generated |
| 2 | Lexer, preprocessor, parser | every corpus program parses; unit tests |
| 3 | Semantic checker + diagnostics | repo programs: identical output to oracle; corpus agreement rate reported |
| 4 | Differential fuzzing (mutated corpus programs) | agreement rate tracked in PROGRESS.md; mismatches triaged |
| 5 | Mode selection, warnings, TDF, ProgSignature | identical to oracle on the corpus |
| 6 | Simulator (stretch) | run a tester's scan loop on the host with stubbed measurements |

## Agreement metric

`tools/difftest.py` compiles each corpus file with both compilers and classifies:
exact (identical normalised output), verdict-match (both pass or both fail, message differs),
mismatch. Report all three in PROGRESS.md; exact-match on the repo's own programs is the first
target because `scripts/verify.py` depends on it.

## Budget rule

The session has a fixed token budget. Keep PROGRESS.md accurate after every milestone so a new
session can resume. Delegate boilerplate (CHM extraction, harness scripts, parser plumbing,
tests) to cheaper agents; keep reverse-engineering interpretation and design centralised.
