# opencrbasic progress

Living state. Update after every milestone. Plan: `PLAN.md`.

## Environment setup (needed to resume)

- Oracle: `apt-get install wine32:i386 wine64 wine` (on Ubuntu 24.04 with the ondrej PPA,
  first `apt-get install --allow-downgrades libgd3=2.3.3-9ubuntu5 libgd3:i386=2.3.3-9ubuntu5`),
  `dpkg --add-architecture i386` first. Run with `WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all`.
  First run takes ~15 s (prefix creation), later runs ~2 s.
- Ghidra 11.4.2 headless (optional, RE only): `/opt/re/ghidra_11.4.2_PUBLIC`, script
  `tools/ghidra/DumpAll.java` writes all decompiled functions to one C file.

## Status by phase

| Phase | State |
|---|---|
| 0 Oracle + corpus | in progress: Wine oracle works; repo programs reproduce `STATUS.md` expected warnings; harness + CHM corpus being built (`tools/oracle.py`, `tools/record_corpus.py`) |
| 1 Binary RE | in progress: CLI decoded (`docs/cli.md`); instruction/function/symbol tables extracted (`tools/extract_tables.py`, `docs/binary.md`); message catalogue (`docs/messages.tsv`); TDF format decoded and round-tripped (`src/opencrbasic/tdf.py`); type codes and pass 1 being documented (`docs/type-codes.md`, `docs/pass1.md`) |
| 2 Front end | done: `lexer.py`, `source.py`, `consteval.py`, `parser.py` written; minimal `checker.py` placeholder (mode.py only) so `python3 -m opencrbasic` runs end to end; `docs/frontend.md` records verified quirks. 825 unit/corpus tests pass (`cd opencrbasic && python3 -m pytest -q`); all 15 `crbasic/*.CR6` entry programs and 196/198 oracle-accepted CHM examples parse with zero `Unparsed` (2 known, documented gaps: a doc typo, an SNMP OID literal). |
| 3 Checker | not started |
| 4 Fuzzing | not started |
| 5 Outputs | not started |
| 6 Simulator | not started |

## Log

- 2026-09-25: Wine oracle installed; all 16 `crbasic/*.CR6` compile under Wine with the
  warnings `STATUS.md` expects. Instruction table found at VA 0x60c320 (16-byte records).

- 2026-09-25: Ghidra decompile of all 10,137 functions; CLI options, compile driver, error
  reporter, and three data tables mapped. `-z` output is the PakBus TDF; `tdf.py` parses and
  re-serialises it byte-identically. Status table fields depend on the program (slow sequences);
  auto-allocated table sizes (`-1`) come from a memory-allocation rule not yet decoded.

- 2026-09-25: branch rebased onto `add-compilers` (CR1000X, CR300, CR350 and other compilers).
  All three stretch targets run under Wine. `extract_tables.py` generalised (symbol record stride
  52 or 64 bytes) and per-logger data extracted to `data/cr1000x|cr300|cr350/`.

- 2026-09-25: Front end (phase 2) written: `lexer.py` (tokens; leading-dot numbers, `&H`/`&B`,
  pointer sigils `!`/`@`), `source.py` (comment/continuation handling, `Include`/`IncludeSection`
  expansion with correct per-file line numbers and `Section` binding), `consteval.py` (constant
  folding with CRBasic's real operator precedence, including a non-textbook `AND`/`OR` rule and
  left-associative `^`, both oracle-verified), `parser.py` (full statement/expression grammar).
  `checker.py` added as the minimal placeholder the plan calls for (`mode.py` only). Verified
  quirks and the oracle evidence for each: `docs/frontend.md`. Test results and open gaps: see the
  phase-2 row above.

## Next steps

1. Phase 3: semantic checker (structure, instruction signatures, table/terminal validation,
   full diagnostics matching `docs/messages.tsv`).
2. Finish CLI option map (`docs/cli.md`) from decompilation.
3. Decode parameter type codes (`docs/type-codes.md`).
4. The two documented front-end gaps (`docs/frontend.md`'s "Known residual gaps") are low value
   and were left for phase 3 or later, not blocking.
